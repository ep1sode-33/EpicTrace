from __future__ import annotations

from collections import deque

from epictrace.asr.hallucination import HallucinationFilter
from epictrace.asr.types import TranscriptSegment


class StreamState:
    """单源(mic/device)流式确认。确认纪律(ASR 笔记 §3.6/§4):
    - 一窗多段 → 除最后一段外确认并 emit,最后一段作 partial;
    - 幻觉段(HallucinationFilter)/最近 N 重复 → 不 emit;
    - **绝不拿阈值丢段**;无进展 force_confirm_after 轮 → 强制确认当前 partial(防弱音卡死)。"""

    def __init__(self, source: str, filter: HallucinationFilter, *,
                 force_confirm_after: int = 4, recent_max: int = 5) -> None:
        self._source = source
        self._filter = filter
        self._force_after = force_confirm_after
        self.last_confirmed_end: float = 0.0
        # scanned_end:转写已 SCAN 到的会话绝对秒数(FIX B)。区别于 last_confirmed_end(确认游标):
        # 即便某轮 0 段(静音/VAD 空)也推进 scanned_end,使调度按「未扫描量」排序——静音源被扫过
        # 后不再霸占调度器、不重复重解码同一段;另一源不被饿死。不变式:scanned_end >= last_confirmed_end。
        self.scanned_end: float = 0.0
        self.partial: TranscriptSegment | None = None
        # 最近确认段的 (text, start, end):供 FIX C 的时间/重叠感知去重(同段被重叠窗口重转才判重,
        # 真实重复语音在错开时间出现不判重)。
        self._recent: deque[tuple[str, float, float]] = deque(maxlen=recent_max)
        self._rounds_no_progress = 0

    def _gate(self, seg: TranscriptSegment) -> str:
        """过滤门判定(STEP 5):返回 "accept" / "halluc" / "dedup",供 _confirm 区分游标策略。
        幻觉先判(空文本/近静音串也归幻觉),再判去重(FIX C 时间/重叠感知)。"""
        if self._filter.is_hallucination(seg.text):
            return "halluc"
        if self._filter.is_duplicate(seg.text, seg.start, seg.end, list(self._recent)):
            return "dedup"
        return "accept"

    def _confirm(self, seg: TranscriptSegment, out: list[TranscriptSegment],
                 *, force_advance: bool = False) -> None:
        # 通过门 → emit(confirmed 落库)并推游标;不通过仅按类型决定是否推游标(不写垃圾)。
        # stall 恢复 / 软强制确认也走这里 —— 故幻觉/重复永不会作为 confirmed 进存储(FIX E)。
        gate = self._gate(seg)
        if gate == "accept":
            self._recent.append((seg.text, seg.start, seg.end))
            out.append(TranscriptSegment(
                text=self._filter.clean(seg.text), start=seg.start, end=seg.end,
                source=self._source, words=seg.words, confirmed=True))
            self._advance_confirmed(seg.end)
        elif gate == "halluc":
            # 真幻觉/近静音:推游标,避免对同一段反复重转(re-loop)。
            self._advance_confirmed(seg.end)
        elif force_advance:
            # gate == "dedup" 且本轮是软强制确认(窗口已落后 tail 超 window_seconds):
            # 必须推游标,否则有界滑窗下未确认窗口会无限增长(STEP 1 兜底压倒 STEP 5 的保留)。
            self._advance_confirmed(seg.end)
        # gate == "dedup" 且非强制:真实语音被去重门压住——**不推游标**,以便该段音频可被
        # 重新解码,不因一次误压而永久丢失(有界滑窗 + 软强制确认兜底,不会无限循环;STEP 5)。

    def _advance_confirmed(self, end: float) -> None:
        """推进确认游标,并维持不变式 scanned_end >= last_confirmed_end(FIX B)。"""
        self.last_confirmed_end = max(self.last_confirmed_end, end)
        if self.scanned_end < self.last_confirmed_end:
            self.scanned_end = self.last_confirmed_end

    def mark_scanned(self, tail: float) -> None:
        """记录转写已扫描到的会话绝对秒数(FIX B)。每次 _transcribe_channel 后调用,即便 0 段:
        把 scanned_end 单调推到本轮切片末端,使调度按「未扫描量」排序——静音/VAD 空的源被扫过后
        不再霸占调度器,另一源不被饿死,同段也不无限重解码。绝不回退(单调)。"""
        if tail > self.scanned_end:
            self.scanned_end = tail

    def ingest(self, segments: list[TranscriptSegment], *,
               force_confirm_earliest: bool = False) -> list[TranscriptSegment]:
        out: list[TranscriptSegment] = []
        if not segments:
            return out
        if len(segments) > 1:
            # 首段 force_advance:仅当本轮软强制确认(窗口落后超 window_seconds)时——确保即便
            # 首段被去重压住,游标也推进,避免未确认窗口无限增长(STEP 1 兜底);正常情形下
            # 去重拒不推游标(STEP 5,可重转)。其余非末段照常确认。
            for i, seg in enumerate(segments[:-1]):
                self._confirm(seg, out, force_advance=(force_confirm_earliest and i == 0))
            self.partial = segments[-1]
            self._rounds_no_progress = 0
        else:
            # 只有一段:作 partial;连续 N 轮没新确认后再来一轮 → 强制确认它(弱音防卡死)。
            # 强制确认仍跑过滤门:幻觉/重复只推游标不 emit,真实文本才落库(FIX E)。
            # 软强制(STEP 1):游标落后 tail 超过 window_seconds 时,即便未到 N 轮也立刻强制
            # 确认它推进游标——否则有界滑窗会让切片头被 tail-window 夹住而游标原地不动,未确认
            # 窗口无限增长。同样跑过滤门;去重拒在软强制下也推游标(force_advance)。
            if force_confirm_earliest or self._rounds_no_progress + 1 > self._force_after:
                self._confirm(segments[0], out, force_advance=force_confirm_earliest)
                self.partial = None
                self._rounds_no_progress = 0
            else:
                self.partial = segments[0]
                self._rounds_no_progress += 1
        if out:
            self._rounds_no_progress = 0
        return out

    def flush(self) -> list[TranscriptSegment]:
        """收尾/IDLE 时强制确认当前 partial(FIX 3:防短尾被丢)。

        若有 partial:走 _confirm(同 force 路径——幻觉/重复只推游标不 emit,真实文本才落库),
        然后清 partial、复位无进展计数;返回本次 emit 的段。无 partial → 空列表(幂等)。"""
        out: list[TranscriptSegment] = []
        if self.partial is not None:
            self._confirm(self.partial, out)
            self.partial = None
            self._rounds_no_progress = 0
        return out
