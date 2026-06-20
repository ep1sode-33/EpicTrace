"""单声道词级流式确认(移植自参考产品 TranscriptionService.transcribeStream 的核心算法)。

替换 stream_state.StreamState 的「段数启发式」:那套一窗多段就确认除末段外所有段、从不逐词比对,
弱音/CJK 下逐字时间戳每轮抖动 → 几乎不收敛(参考产品 CHINESE_TUNING 实测 baseline 17 分钟只确认
17 字)。本模块改用**词级 prefix-agreement + 锚定滑窗**:

- 两个游标(对齐参考产品生产代码,不是 bench 模拟器):
  - `last_agreed_seconds`:驱动滑窗起点(= 它 − slice_padding)+ 调度门基准;每轮推进到 `anchor.first.start`。
  - `last_confirmed_end`:已确认文本末端(统计/兜底,不驱动窗口)。
- 每轮把(已平移到会话绝对时间的)词数组喂 `ingest`:与上轮 hypothesis 求最长公共前缀(LCP),
  LCP 去掉末尾 `anchor_words`(中文 tc=1)= 本轮确认,末尾 anchor 留作下轮 prefix 上下文(喂引擎
  `initial_prompt`)+ 等进一步印证。
- confirmed 非空就 emit(对齐生产:靠 dedup 防重,不像 bench 额外 gate didAdvance);
- 连续 `force_confirm_after` 轮无进展 → 强制确认当前 hypothesis 除末 anchor 外的词(防弱音卡死);
- 强制确认仍吐不出文本(全幻觉/重复)且缓冲落后够多 → stall-seek 推进 `last_agreed_seconds`,
  避免对同一段无限重解码(对齐生产 seek 路径,务实简化)。

过滤(幻觉/段内循环/去重)复用 HallucinationFilter,与引擎/繁简无关。调度相关的 `scanned_end`
(供 StreamLoop 双声道公平调度,见 loop.py)也维护在这里。
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace

from epictrace.asr.agreement import longest_common_prefix
from epictrace.asr.hallucination import HallucinationFilter
from epictrace.asr.types import TranscriptSegment, WordTiming


def _meaningful(word: WordTiming) -> bool:
    """词是否含实义内容:有至少一个字母/数字/CJK 字符(纯标点/空白 = 否)。
    Python str.isalnum() 对 CJK 字符返回 True,故中文逐字词照样算实义。"""
    return any(c.isalnum() for c in word.word)


class WordChannel:
    """单源(mic/device)的词级流式确认状态 + 确认纪律。"""

    def __init__(self, source: str, filter: HallucinationFilter, *,
                 anchor_words: int = 1, force_confirm_after: int = 4,
                 slice_padding: float = 2.0, max_slice: float = 15.0,
                 stall_seek: float = 0.8, recent_max: int = 5,
                 min_buffered_for_seek: float = 2.5) -> None:
        self._source = source
        self._filter = filter
        self._tc = max(1, anchor_words)
        self._force_after = force_confirm_after
        self._stall_seek = stall_seek
        self._min_buffered_for_seek = min_buffered_for_seek
        self._max_slice = max_slice
        self._slice_padding = slice_padding
        # 自适应 seek 步长上界 = 一个滑窗内能跳过的最大量(滑窗长 − 回看,不会越过未看音频)。
        self._max_seek_step = max(stall_seek, max_slice - slice_padding)
        # 窗口锚 + 确认文本末端(双游标,见模块 docstring)。
        self.last_agreed_seconds: float = 0.0
        self.last_confirmed_end: float = 0.0
        # 调度游标(供 loop 按「未扫描量」公平排序;每轮推到切片末端,含 0 段)。
        self.scanned_end: float = 0.0
        self.last_agreed_words: list[WordTiming] = []  # anchor → 下轮喂引擎 initial_prompt
        self._prev_words: list[WordTiming] = []        # 上轮 hypothesis(绝对时间)
        self._consecutive_no_advance = 0
        self._recent: deque[tuple[str, float, float]] = deque(maxlen=recent_max)
        # 上轮过滤到 >= last_agreed 的 current_words(供 flush 收尾确认残尾)。
        self._last_current: list[WordTiming] = []
        self.partial: TranscriptSegment | None = None  # 实时未确认 hypothesis 预览

    # --- 供 loop 喂引擎用 ---

    def anchor_text(self) -> str:
        """anchor 词拼成的文本(喂引擎当 initial_prompt 维持上下文)。"""
        return "".join(w.word for w in self.last_agreed_words)

    def clamp_to_base(self, base: float) -> None:
        """缓冲头滚过了已确认位(那段音频已丢)→ 把游标跳到缓冲头,清掉引用旧时间的 anchor/prev。"""
        if base > self.last_agreed_seconds:
            self.last_agreed_seconds = base
            self.last_agreed_words = []
            self._prev_words = []
            self._last_current = []

    def mark_scanned(self, end: float) -> None:
        """记录本轮转写已扫描到的会话绝对秒(单调);供 loop 调度按未扫描量排序(静音源不霸占)。"""
        if end > self.scanned_end:
            self.scanned_end = end

    def skip_to(self, abs_seconds: float) -> None:
        """把两游标单调跳到 abs_seconds(软静音用:静音区间既不重转也不卡调度)。"""
        if abs_seconds > self.last_agreed_seconds:
            self.last_agreed_seconds = abs_seconds
            self.last_agreed_words = []
            self._prev_words = []
            self._last_current = []
        if abs_seconds > self.scanned_end:
            self.scanned_end = abs_seconds

    # --- 核心:每轮喂入绝对时间词数组 ---

    def ingest(self, absolute_words: list[WordTiming], *,
               buffer_end: float) -> list[TranscriptSegment]:
        """喂入本轮(已平移到会话绝对时间的)词数组,返回本轮确认 emit 的段。"""
        out: list[TranscriptSegment] = []
        current = [w for w in absolute_words
                   if w.start >= self.last_agreed_seconds and _meaningful(w)]
        self._last_current = current

        if not current:
            # 无实义词(静音/VAD 空/全幻觉)→ 计无进展,够久就 seek 推进游标(防 re-loop)。
            # 注意:不覆盖 _prev_words(对齐生产 TranscriptionService:无词 tick 在 prevAbsoluteWords
            # 赋值前 return,保留上一个有效 hypothesis)——否则语音恢复那轮的 LCP 会拿空/垃圾比,削弱收敛。
            self._consecutive_no_advance += 1
            self._maybe_seek(buffer_end, [])
            self.partial = None
            return out

        prev = [w for w in self._prev_words
                if w.start >= self.last_agreed_seconds and _meaningful(w)]

        if prev:
            lcp = longest_common_prefix(prev, current)
            if len(lcp) >= self._tc:
                new_confirmed = lcp[:len(lcp) - self._tc]
                anchor = lcp[len(lcp) - self._tc:]
                prev_agreed = self.last_agreed_seconds
                self.last_agreed_words = list(anchor)
                self.last_agreed_seconds = anchor[0].start
                if self.last_agreed_seconds > prev_agreed + 0.1:
                    self._consecutive_no_advance = 0
                else:
                    self._consecutive_no_advance += 1
                if new_confirmed:
                    self._emit(new_confirmed, out)
            else:
                self._consecutive_no_advance += 1

        # 强制确认:连续无进展够多轮 → 确认当前 hypothesis 除末 anchor 外的词(弱音防卡死)。
        if self._consecutive_no_advance >= self._force_after:
            accepted = False
            if len(current) > self._tc:
                fc = current[:len(current) - self._tc]
                anchor = current[len(current) - self._tc:]
                accepted = self._emit(fc, out)
                if accepted:
                    self.last_agreed_words = list(anchor)
                    self.last_agreed_seconds = anchor[0].start
                    self._prev_words = []
                    self._consecutive_no_advance = 0
            if not accepted:
                # 连强制确认都吐不出文本(全幻觉/重复)→ seek 跳过整个当前 hypothesis,避免无限重解码同一段。
                self._maybe_seek(buffer_end, current)

        self._prev_words = absolute_words
        self._update_partial(current)
        return out

    def _emit(self, words: list[WordTiming], out: list[TranscriptSegment]) -> bool:
        """词组拼文本 → 过滤门(幻觉/段内循环/时间感知去重)→ 通过则 emit confirmed 并推 confirmed 末端。
        返回是否被接受(用于 force-confirm 路径判定是否还需 seek)。"""
        if not words:
            return False
        text = "".join(w.word for w in words)
        start, end = words[0].start, words[-1].end
        if self._filter.is_hallucination(text) or self._filter.is_intra_segment_loop(text):
            return False
        if self._filter.is_duplicate(text, start, end, list(self._recent)):
            return False
        self._recent.append((text, start, end))
        out.append(TranscriptSegment(
            text=self._filter.clean(text), start=start, end=end,
            source=self._source, words=list(words), confirmed=True))
        if end > self.last_confirmed_end:
            self.last_confirmed_end = end
        return True

    def _maybe_seek(self, buffer_end: float, current: list[WordTiming]) -> None:
        """stall 恢复:无进展够多轮且缓冲落后够多 → 把 last_agreed 推进,清 anchor/prev、复位计数,
        避免对同一段无限重解码。三档目标(对齐生产 TranscriptionService 的 seek 路径):

        - 有 current hypothesis(确认不出,全 dup/幻觉)→ 跳过整个 hypothesis(>= current[-1].end−0.05),
          否则只挪一小步会反复重解码同一片(生产 candidateFromWords 主导项)。
        - 无词且落后超一个滑窗(lag > maxSlice+padding)→ 直接快进到近 tail(buffer_end−maxSeekStep),
          否则长静音 backlog 只能龟速 0.8s/次 追(生产 lag 分支)。
        - 否则保守推进一个自适应步长。
        全档都夹在 buffer_end−0.1 内(不越过未到音频)。"""
        if self._consecutive_no_advance < self._force_after:
            return
        lag = buffer_end - self.last_agreed_seconds
        if lag < self._min_buffered_for_seek:
            return
        over = self._consecutive_no_advance - self._force_after + 1
        step = min(max(self._stall_seek * over, self._stall_seek), self._max_seek_step)
        conservative = self.last_agreed_seconds + step
        if current:
            target = max(current[-1].end - 0.05, conservative)
        elif lag > self._max_slice + self._slice_padding:
            target = max(conservative, buffer_end - self._max_seek_step)
        else:
            target = conservative
        target = min(target, max(self.last_agreed_seconds, buffer_end - 0.1))
        if target > self.last_agreed_seconds + 0.05:
            self.last_agreed_seconds = target
            self.last_agreed_words = []
            self._prev_words = []
            self._consecutive_no_advance = 0
            self.partial = None

    def _update_partial(self, current: list[WordTiming]) -> None:
        """实时未确认 hypothesis 预览 = 当前 >= last_agreed 的词拼文本(含 anchor 与其后)。
        空 / 幻觉不发(避免预览闪烁垃圾)。"""
        tail = [w for w in current if w.start >= self.last_agreed_seconds]
        text = "".join(w.word for w in tail).strip()
        if not text or self._filter.is_hallucination(text):
            self.partial = None
            return
        self.partial = TranscriptSegment(
            text=text, start=tail[0].start, end=tail[-1].end,
            source=self._source, words=list(tail), confirmed=False)

    def flush(self) -> list[TranscriptSegment]:
        """收尾/IDLE:把残留未确认尾巴(上轮 current 中 >= last_agreed 的词)走过滤门强制确认 emit。
        幂等:emit 后清空,二次 flush 不重复。"""
        out: list[TranscriptSegment] = []
        tail = [w for w in self._last_current if w.start >= self.last_agreed_seconds
                and _meaningful(w)]
        if tail and self._emit(tail, out):
            self.last_agreed_seconds = max(self.last_agreed_seconds, tail[-1].end)
            self.last_agreed_words = []
        self._last_current = []
        self.partial = None
        return out

    @staticmethod
    def shift_words(words: list[WordTiming], offset: float) -> list[WordTiming]:
        """把 slice-相对词时间平移回会话绝对时间(loop 切片后调用)。"""
        if offset == 0.0:
            return words
        return [replace(w, start=w.start + offset, end=w.end + offset) for w in words]
