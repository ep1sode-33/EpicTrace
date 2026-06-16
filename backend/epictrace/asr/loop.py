from __future__ import annotations

from collections.abc import Callable

from epictrace.asr.config import AsrConfig
from epictrace.asr.hallucination import HallucinationFilter
from epictrace.asr.stream_state import StreamState
from epictrace.asr.types import TranscriptSegment

_MIN_PENDING = 1.0  # 秒:某路待处理音频超过它才值得转一轮


class StreamLoop:
    """单引擎在 mic/device 间逐轮交替(ASR 笔记 §5)。纯逻辑:音频与 pending 由调用方喂。"""

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
        self._pending = {"mic": 0.0, "device": 0.0}

    def set_pending(self, *, mic: float, device: float) -> None:
        self._pending = {"mic": mic, "device": device}

    def _pick_source(self) -> str | None:
        cand = [(p, s) for s, p in self._pending.items() if p >= _MIN_PENDING]
        if not cand:
            return None
        return max(cand)[1]

    def tick(self, audio: dict) -> None:
        src = self._pick_source()
        if src is None:
            return
        st = self._states[src]
        prefix = st.partial.text if st.partial else ""
        segs = self._engine.transcribe_window(
            audio[src], clip_start=st.last_confirmed_end, prefix=prefix, source=src)
        for c in st.ingest(segs):
            self._on_confirmed(c)
        if st.partial is not None:
            self._on_partial(st.partial)
