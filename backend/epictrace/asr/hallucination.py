from __future__ import annotations

import re

# 近静音/标注幻觉精确串(中英),小写比对;移植自 ASR 笔记 §3.1
_EXACT = {
    "谢谢观看", "谢谢大家", "谢谢收看", "感谢观看", "感谢收看", "感谢大家",
    "thank you for watching", "thanks for watching", "bye", "goodbye",
    "you", "okay", "ok", "hmm", "um", "uh", "yeah", "right",
    "[silence]", "(silence)", "[blank_audio]", "[no speech]", "[inaudible]", "(inaudible)",
}
# 出现在更长文本里的中文幻觉子串(§3.2)
_SUBSTRINGS = (
    "请不吝点赞", "点赞订阅", "订阅转发", "打赏支持", "请订阅", "请点赞", "请转发",
    "字幕由", "字幕提供", "明镜与点点栏目",
)
_LEADING_PUNCT = re.compile(r"^[\W_]+", re.UNICODE)


class HallucinationFilter:
    """引擎无关的文本层幻觉过滤(§3)。阈值不当过滤器——这里才是真过滤。可配置开关/扩展。"""

    def __init__(self, *, enabled: bool = True, extra_exact: set[str] | None = None) -> None:
        self._enabled = enabled
        self._exact = {s.lower() for s in _EXACT} | {s.lower() for s in (extra_exact or set())}

    def clean(self, text: str) -> str:
        return _LEADING_PUNCT.sub("", text).strip()

    def is_hallucination(self, text: str) -> bool:
        if not self._enabled:
            return False
        t = self.clean(text).lower()
        if not t:
            return True
        if t in self._exact:
            return True
        if t.startswith("(speaking") and t.endswith(")"):
            return True
        for sub in _SUBSTRINGS:
            if sub in text:
                return True
        return False

    def signature(self, text: str) -> str:
        # 归一化为去空白/标点的字符序列,供「连续 N 次相同 hypothesis」比对
        return re.sub(r"\s+", "", self.clean(text)).lower()

    def is_duplicate(self, text: str, recent: list[str]) -> bool:
        sig = self.signature(text)
        if not sig:
            return True
        for r in recent:
            rsig = self.signature(r)
            if sig == rsig or (len(sig) >= 4 and (sig in rsig or rsig in sig)):
                return True
        return False
