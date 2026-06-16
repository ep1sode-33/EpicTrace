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
        """近重复判定(STEP 5 收紧)。两类命中:

        1. **近乎完全相等**:与最近任一段签名相等 → 同一 hypothesis 被再吐一遍,判重。
           短段绝不再因「子串包含」误判——「测试测试」与「测试测试测试」是不同真实话语。
        2. **退化循环**:仅当 recent 末尾连续 >=2 段与本段成子串关系(加本段共 >=3 次)时,
           才用子串/loop 抑制(文档记的「连续 >=3 次相同 hypothesis」退化生长循环)。
        """
        sig = self.signature(text)
        if not sig:
            return True
        # 1. 近乎完全相等(任一最近段)。
        for r in recent:
            if sig == self.signature(r):
                return True
        # 2. 退化循环:从 recent 末尾起数连续「与本段成子串关系」的段;>=2 段(共 >=3)才抑制。
        run = 0
        for r in reversed(recent):
            rsig = self.signature(r)
            if rsig and (sig in rsig or rsig in sig):
                run += 1
            else:
                break
        return run >= 2
