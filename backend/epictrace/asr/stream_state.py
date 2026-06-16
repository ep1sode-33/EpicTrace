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
        self.partial: TranscriptSegment | None = None
        self._recent: deque[str] = deque(maxlen=recent_max)
        self._rounds_no_progress = 0

    def _accept(self, seg: TranscriptSegment) -> bool:
        if self._filter.is_hallucination(seg.text):
            return False
        if self._filter.is_duplicate(seg.text, list(self._recent)):
            return False
        return True

    def _confirm(self, seg: TranscriptSegment, out: list[TranscriptSegment]) -> None:
        # 只在通过幻觉/去重门时才 emit(confirmed 落库);不通过仅推游标(不丢音、不写垃圾)。
        # stall 恢复的强制确认也走这里 —— 故幻觉永不会作为 confirmed 进存储(FIX E)。
        if self._accept(seg):
            self._recent.append(seg.text)
            out.append(TranscriptSegment(
                text=self._filter.clean(seg.text), start=seg.start, end=seg.end,
                source=self._source, words=seg.words, confirmed=True))
        self.last_confirmed_end = max(self.last_confirmed_end, seg.end)

    def ingest(self, segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
        out: list[TranscriptSegment] = []
        if not segments:
            return out
        if len(segments) > 1:
            for seg in segments[:-1]:
                self._confirm(seg, out)
            self.partial = segments[-1]
            self._rounds_no_progress = 0
        else:
            # 只有一段:作 partial;连续 N 轮没新确认后再来一轮 → 强制确认它(弱音防卡死)。
            # 强制确认仍跑过滤门:幻觉/重复只推游标不 emit,真实文本才落库(FIX E)。
            self.partial = segments[0]
            self._rounds_no_progress += 1
            if self._rounds_no_progress > self._force_after:
                self._confirm(segments[0], out)
                self.partial = None
                self._rounds_no_progress = 0
        if out:
            self._rounds_no_progress = 0
        return out
