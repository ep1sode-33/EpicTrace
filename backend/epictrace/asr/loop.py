from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from epictrace.asr.config import AsrConfig
from epictrace.asr.hallucination import HallucinationFilter
from epictrace.asr.stream_state import StreamState
from epictrace.asr.types import TranscriptSegment

_MIN_PENDING = 1.0  # 秒:某路未处理音频超过它才值得转一轮
_MIN_FLUSH_TAIL = 0.2  # 秒:flush 时未处理音频超过它才值得转(低于正常 1s 门,专收短尾,FIX 3)


class StreamLoop:
    """单引擎在 mic/device 间逐轮交替(ASR 笔记 §5)。纯逻辑:音源对象由调用方喂(set_sources)。

    流式做法 = **手动切片 + VAD-on + 偏移平移**(见 engine.py 对 §4 WhisperKit 经验的分歧说明):
    每轮选「未处理音频更多」的一路,按 `slice_start_abs = max(cursor, source.base_offset())`
    切出缓冲,交引擎转写(引擎返回 slice-相对时间),再把每段/每词平移 +slice_start_abs 回到
    会话绝对时间,才喂 StreamState。这样长 session 缓冲截断后绝对时间不漂、VAD 始终生效。
    """

    def __init__(self, engine, config: AsrConfig,
                 on_confirmed: Callable[[TranscriptSegment], None],
                 on_partial: Callable[[TranscriptSegment], None]) -> None:
        self._engine = engine
        self._cfg = config
        self._on_confirmed = on_confirmed
        self._on_partial = on_partial
        f = HallucinationFilter(enabled=config.halluc_filter_enabled)
        self._states = {
            "mic": StreamState("mic", f, force_confirm_after=config.force_confirm_after),
            "device": StreamState("device", f, force_confirm_after=config.force_confirm_after),
        }
        self._sources: dict[str, object] = {}

    def set_sources(self, sources: dict[str, object]) -> None:
        """注入当前可用音源({channel: source});source 须有 base_offset/available_seconds/window_from。"""
        self._sources = sources

    def skip_channel_to(self, ch: str, abs_seconds: float) -> None:
        """把某路的确认游标 + 扫描游标都单调推进到 abs_seconds(FIX A 软静音用)。

        软静音某路时,worker 调本方法把该路两游标跳到 available_seconds()——使静音期间攒下的
        音频既不被转写(scanned_end/last_confirmed_end 已越过它),也不在 unmute 后从旧游标回追
        backlog;unmute 后从「现在」继续。未知通道 = no-op。"""
        st = self._states.get(ch)
        if st is None:
            return
        if abs_seconds > st.last_confirmed_end:
            st.last_confirmed_end = abs_seconds
        if abs_seconds > st.scanned_end:
            st.scanned_end = abs_seconds

    def _unprocessed(self, channel: str) -> float:
        """该路未扫描秒数 = 绝对末端 - 已扫描游标(FIX B:按「未扫描量」排序,不用确认游标)。

        用 scanned_end 而非 last_confirmed_end:静音/VAD 空的源被扫过后未扫描量归零,不再霸占
        调度器(否则确认游标停滞 → 该源永远「未处理量最大」→ 另一源被饿死、同段无限重解码)。"""
        src = self._sources.get(channel)
        if src is None:
            return 0.0
        return max(0.0, src.available_seconds() - self._states[channel].scanned_end)

    def _pick_source(self) -> str | None:
        cand = [(self._unprocessed(ch), ch) for ch in self._sources]
        cand = [(u, ch) for u, ch in cand if u >= _MIN_PENDING]
        if not cand:
            return None
        return max(cand)[1]

    def tick(self) -> None:
        ch = self._pick_source()
        if ch is None:
            return
        self._transcribe_channel(ch)

    def _transcribe_channel(self, ch: str) -> None:
        """转写某路自游标起的未处理切片一次,平移回绝对时间,喂 StreamState 并 emit。

        两种切片regime(按「未扫描 backlog = tail - scanned_end」自动选,FIX F):
        - **追赶**(backlog > window_seconds,如长模型加载后冷启动 backlog / 久未被调度的源):
          从 scanned_end/缓冲头向前切一块 ~window_seconds,逐 tick 推进,直到追到距 tail 不足
          window_seconds——绝不直接跳到 tail-window 把开局攒下的 backlog 静默丢掉。每 tick 至多
          一块,不阻塞。
        - **正常滑窗**(backlog ≤ window_seconds):切片起点 = max(确认游标, tail-window, 缓冲头),
          切到 tail。游标推进 + tail-window 上界把回看夹在 ~window 内(长 session 不重转整段)。

        两种情形末尾都推进 scanned_end 到切片末端(含 0 段;FIX B)使调度按未扫描量排序。"""
        src = self._sources[ch]
        st = self._states[ch]
        tail = src.available_seconds()
        window = self._cfg.window_seconds
        base = src.base_offset()
        if tail - st.scanned_end > window:
            # 追赶:从已扫描游标(夹在缓冲头内)向前切一块 ~window;切片末端是块尾而非 tail。
            slice_start_abs = max(st.scanned_end, base)
            slice_end_abs = min(tail, slice_start_abs + window)
        else:
            # 正常有界滑窗(STEP 1):游标 / tail 回看 window / 缓冲头 三者取大,切到 tail。
            slice_start_abs = max(st.last_confirmed_end, tail - window, base)
            slice_end_abs = tail
        pcm = src.window_from(slice_start_abs)
        prefix = st.partial.text if st.partial else ""
        segs = self._engine.transcribe_window(pcm, prefix=prefix, source=ch)
        # 引擎返回 slice-相对时间 → 平移回会话绝对时间后再喂 StreamState。
        segs = [self._shift(s, slice_start_abs) for s in segs]
        # 软强制确认(STEP 1):游标落后 tail 超过 window_seconds 时,本轮强制确认最早 pending
        # 段推进游标,避免未确认窗口无限增长(否则切片会被 base/window 夹住但游标原地不动)。
        force_earliest = (tail - st.last_confirmed_end) > window
        for c in st.ingest(segs, force_confirm_earliest=force_earliest):
            self._on_confirmed(c)
        if st.partial is not None:
            self._on_partial(st.partial)
        # 本轮已扫描到切片末端 → 推进 scanned_end(含 0 段;FIX B),使静音源不再霸占调度。
        st.mark_scanned(slice_end_abs)

    def flush_channel(self, ch: str) -> None:
        """只排空某一路的短尾(FIX D):若该路有 ANY 未处理音频(> _MIN_FLUSH_TAIL≈0.2s,低于
        正常 1s 门)就转一次、平移、ingest;再对该路 StreamState.flush() 强制确认残留 partial 并 emit。

        worker 的 IDLE 检测对「转 IDLE 的那一路」调用本方法——只确认它自己的短尾/partial,绝不
        因一路空闲就强制确认另一路 mid-utterance 的 pending partial(此前 flush() 全路 flush 的副作用)。
        无未处理音频且无 partial 的源是 no-op(故可幂等重复调用)。"""
        if ch not in self._sources:
            return
        if self._unprocessed(ch) > _MIN_FLUSH_TAIL:
            self._transcribe_channel(ch)
        for c in self._states[ch].flush():
            self._on_confirmed(c)

    def flush(self) -> None:
        """排空所有路的短尾(FIX 3,收尾 stop 用):逐路调 flush_channel。"""
        for ch in list(self._sources):
            self.flush_channel(ch)

    @staticmethod
    def _shift(seg: TranscriptSegment, offset: float) -> TranscriptSegment:
        if offset == 0.0:
            return seg
        words = [replace(w, start=w.start + offset, end=w.end + offset) for w in seg.words]
        return replace(seg, start=seg.start + offset, end=seg.end + offset, words=words)
