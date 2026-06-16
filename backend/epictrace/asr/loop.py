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

    def _unprocessed(self, channel: str) -> float:
        """该路未处理秒数 = 绝对末端 - 已确认游标(FIX C:按未处理量排序,不用原始缓冲长度)。"""
        src = self._sources.get(channel)
        if src is None:
            return 0.0
        return max(0.0, src.available_seconds() - self._states[channel].last_confirmed_end)

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
        """转写某路自游标起的未处理切片一次,平移回绝对时间,喂 StreamState 并 emit。"""
        src = self._sources[ch]
        st = self._states[ch]
        # 切片起点:游标与缓冲实际起点取大(游标落后于截断点时从缓冲头切)。
        slice_start_abs = max(st.last_confirmed_end, src.base_offset())
        pcm = src.window_from(slice_start_abs)
        prefix = st.partial.text if st.partial else ""
        segs = self._engine.transcribe_window(pcm, prefix=prefix, source=ch)
        # 引擎返回 slice-相对时间 → 平移回会话绝对时间后再喂 StreamState。
        segs = [self._shift(s, slice_start_abs) for s in segs]
        for c in st.ingest(segs):
            self._on_confirmed(c)
        if st.partial is not None:
            self._on_partial(st.partial)

    def flush(self) -> None:
        """排空短尾(FIX 3):正常 tick 只处理 ≥1s 未处理音频,短促一句话+停顿(尾段 <1s)永不被转。

        收尾(stop)或某路转 IDLE 时调用:逐路若有 ANY 未处理音频(> _MIN_FLUSH_TAIL≈0.2s,
        低于正常 1s 门)就转一次、平移、ingest;再对每路 StreamState.flush() 强制确认残留 partial
        并 emit。无未处理音频且无 partial 的源是 no-op(故可幂等重复调用)。"""
        for ch in self._sources:
            if self._unprocessed(ch) > _MIN_FLUSH_TAIL:
                self._transcribe_channel(ch)
            for c in self._states[ch].flush():
                self._on_confirmed(c)

    @staticmethod
    def _shift(seg: TranscriptSegment, offset: float) -> TranscriptSegment:
        if offset == 0.0:
            return seg
        words = [replace(w, start=w.start + offset, end=w.end + offset) for w in seg.words]
        return replace(seg, start=seg.start + offset, end=seg.end + offset, words=words)
