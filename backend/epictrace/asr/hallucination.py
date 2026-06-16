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
# 退化生长循环判定的时间紧邻阈值(秒,FIX C):loop 段时间连续递增/重叠,间隔远小于此;
# 真实重复语音(隔几秒再说同一句)间隔超此 → run 断,不被误抑制。
_LOOP_GAP = 1.5


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

    def is_duplicate(self, text: str, start: float, end: float,
                     recent: list[tuple[str, float, float]]) -> bool:
        """近重复判定(FIX C:时间/重叠感知)。recent 是最近确认段的 (text, start, end)。两类命中:

        1. **重叠重转**:与某最近段签名相等 **且** 时间区间重叠 → 同一段音频被重叠窗口重转
           (有界滑窗下相邻 tick 的切片必然重叠)→ 判重。短段绝不再因「子串包含」误判——
           「测试测试」与「测试测试测试」是不同真实话语。
           关键反例:同一文本但时间明显错开(真实重复语音,如「测试」说三遍)→ **不判重**,
           照常 emit + 推进游标,绝不被卡住或丢弃。
        2. **退化循环**:仅当 recent 末尾连续 >=2 段与本段成子串关系(加本段共 >=3 次)时,
           才用子串/loop 抑制(文档记的「连续 >=3 次相同 hypothesis」退化生长循环)。这类是真
           幻觉,不依赖时间重叠(loop 段时间常连续递增)。
        """
        sig = self.signature(text)
        if not sig:
            return True
        # 1. 重叠重转:签名相等 **且** 时间区间相交(同一段音频被相邻重叠窗口重复解码)。
        for r_text, r_start, r_end in recent:
            if sig == self.signature(r_text) and self._overlaps(start, end, r_start, r_end):
                return True
        # 2. 退化循环:从 recent 末尾起数连续「与本段成子串关系 **且** 时间紧邻(无明显间隔)」
        #    的段;>=2 段(共 >=3)才抑制。退化生长循环段时间连续递增/重叠(引擎在重叠窗里反复
        #    吐越来越长的同前缀);而真实重复语音(同文本隔一段时间再说)段间有明显间隔,run 会断,
        #    不会被误抑制(FIX C)。
        run = 0
        prev_start = start  # 从候选段往回走,要求与上一段紧邻(gap < _LOOP_GAP)
        for r_text, r_start, r_end in reversed(recent):
            rsig = self.signature(r_text)
            contiguous = (prev_start - r_end) < _LOOP_GAP
            if rsig and contiguous and (sig in rsig or rsig in sig):
                run += 1
                prev_start = r_start
            else:
                break
        return run >= 2

    @staticmethod
    def _overlaps(s1: float, e1: float, s2: float, e2: float) -> bool:
        """两个 [start, end] 时间区间是否相交(端点接触不算重叠:相邻段不误判重)。"""
        return s1 < e2 and s2 < e1
