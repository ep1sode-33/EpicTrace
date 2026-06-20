from __future__ import annotations

import re

# 近静音/标注幻觉精确串(中英),小写比对;移植自 ASR 笔记 §3.1。
# 注:上游引擎已 t2s 转简(text_normalize),故中文串保持简体即可命中真机的「謝謝大家」类繁体输出。
_EXACT = {
    # 中文「视频结尾/感谢」类静音幻觉(整段恰为其一才命中,误伤有限)。
    "谢谢观看", "谢谢大家", "谢谢收看", "谢谢", "谢谢你", "感谢观看", "感谢收看", "感谢大家",
    "请观看", "请点赞", "请关注", "我们下期再见", "下期再见", "下集再见",
    # 英文 YouTube 式结尾 + 填充词。
    "thank you for watching", "thanks for watching", "thank you", "thanks",
    "please subscribe", "subscribe", "like and subscribe", "bye", "goodbye",
    "you", "okay", "ok", "hmm", "um", "uh", "yeah", "right",
    # 标注/静音占位串。
    "[silence]", "(silence)", "[blank_audio]", "[no speech]", "[inaudible]", "(inaudible)",
    "[music]", "(music)", "[applause]", "(applause)", "[laughter]", "(laughter)",
    "(indistinct)", "(murmuring)",
}
# 出现在更长文本里的中文幻觉子串(§3.2);对 clean+lower 后的文本比对(见 is_hallucination)。
_SUBSTRINGS = (
    "请不吝点赞", "点赞订阅", "订阅转发", "打赏支持", "请订阅", "请点赞", "请转发",
    "一键三连", "转发打赏", "字幕由", "字幕提供", "明镜与点点栏目",
    # 静音/水印类幻觉(真机实测:无声/弱音输入时 Whisper 脑补的平台水印),整段或子串命中即滤。
    "优优独播剧场", "yoyo television series exclusive", "中文字幕志愿者", "字幕志愿者",
)
_LEADING_PUNCT = re.compile(r"^[\W_]+", re.UNICODE)
# 退化生长循环判定的时间紧邻阈值(秒,FIX C):loop 段时间连续递增/重叠,间隔远小于此;
# 真实重复语音(隔几秒再说同一句)间隔超此 → run 断,不被误抑制。
_LOOP_GAP = 1.5
# 段内立即重复(is_intra_segment_loop)的最短重复单元长度(字符)。子串短于它一律放行,
# 避免误杀真实重复语音(「测试测试」「你好你好」「哈哈哈哈」)——只抓 >=4 字的长串多遍拼接。
_MIN_LOOP_UNIT = 4


def _max_run(seq: list) -> int:
    """序列中相邻相等元素的最长连续长度(空序列 = 0)。"""
    if not seq:
        return 0
    best = run = 1
    for i in range(1, len(seq)):
        run = run + 1 if seq[i] == seq[i - 1] else 1
        if run > best:
            best = run
    return best


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
        # raw = 仅去首尾空白 + lower(保留括号),供 [music]/(silence) 类标注串命中;
        # t = 再剥前导标点(clean),供普通近静音串命中。两者都比对 _EXACT。
        raw = text.strip().lower()
        t = self.clean(text).lower()
        if not t:
            return True
        if raw in self._exact or t in self._exact:
            return True
        if t.startswith("(speaking") and t.endswith(")"):
            return True
        # 子串命中:统一对 clean+lower 后的 t 比对(此前误用未规整的原始 text,与 _EXACT 不一致)。
        for sub in _SUBSTRINGS:
            if sub in t:
                return True
        # 段内立即重复(整段恰为某长串多遍无间隔拼接)→ 解码 loop 幻觉。
        if self.is_intra_segment_loop(text):
            return True
        # 退化复读环(低多样性 / 超长同元连串,如「是是是…×40」「unden×9」)——弱音尾巴常见,
        # is_intra_segment_loop 只抓 >=4 字整除拼接,抓不到单字/超长连串,这里补。
        if self.is_repetition_loop(text):
            return True
        return False

    def is_repetition_loop(self, text: str) -> bool:
        """退化复读环检测(移植自参考产品 isRepetitionLoop,按 CJK 逐字 + 空白分词双路):

        - 空白分词后 >=8 个 token:唯一率 < 0.4(绝大多数 token 相同)或连续相同 >=4 → 环
          (抓「是 是 是 …」「thank you thank you …」这类分词后高重复)。
        - 去空白字符串里同一字符连续 >=8 次 → 环(抓「是是是…×N」无空格单字连串)。

        保守:短重复(「对对对」「哈哈哈哈」「测试测试测试测试」)长度/连串不足阈值 → 放行,
        不误杀真实重复语音。"""
        toks = [t.lower() for t in self.clean(text).split()]
        if len(toks) >= 8:
            if len(set(toks)) / len(toks) < 0.4:
                return True
            if _max_run(toks) >= 4:
                return True
        s = self.signature(text)
        if len(s) >= 8 and _max_run(list(s)) >= 8:
            return True
        # 任意子串无间隔平铺 >=5 遍(如「unden×9」):k>=5 几乎只见于解码环;k<=4 留给真实重复
        # 语音(「测试测试测试测试」k=4)。取最小周期判定。
        n = len(s)
        if n >= 10:
            for p in range(1, n // 5 + 1):
                if n % p == 0 and s[:p] * (n // p) == s:
                    return (n // p) >= 5
        return False

    def is_intra_segment_loop(self, text: str) -> bool:
        """段内立即重复检测:整段签名恰为某子串无间隔拼接 2~4 遍,且**最小**重复单元 >=_MIN_LOOP_UNIT
        字 → 判幻觉(如「我会去看你我会去看你」「这次没有这次没有」)。

        关键:取**最小周期**而非任意周期——「测试测试测试测试」表面可分成「测试测试」×2,但其最小周期
        是「测试」(2 字 <4)→ 放行(真实重复语音)。同理放行「测试测试测试」「你好你好你好今天天气很好」
        「哈哈哈哈」「好的好的」。跨段重复/重叠重转由 is_duplicate 处理;本方法只看单段文本内部。
        按 codepoint 比对,与繁简无关(上游已转简)。"""
        s = self.signature(text)
        n = len(s)
        if n < _MIN_LOOP_UNIT * 2:  # 长度不足两个最短单元 → 不可能是 >=4 字的多遍拼接
            return False
        # 最小周期 p:最小的使 s == s[:p] * (n//p) 的整除周期。
        for p in range(1, n // 2 + 1):
            if n % p == 0 and s[:p] * (n // p) == s:
                k = n // p
                return p >= _MIN_LOOP_UNIT and 2 <= k <= 4
        return False

    def is_low_quality(self, text: str, avg_logprob: float,
                       compression_ratio: float) -> bool:
        """段级质量过滤(移植参考产品 isLowQualitySegment):解码退化段判低质丢弃——
        复读环 / 极低 avg_logprob / (低 logprob + 极短) / (高 compression_ratio + 够长)。
        长度按**字符数**(CJK 无词间空格,token 计恒为 1 不可用)。关闭过滤器则一律放行。"""
        if not self._enabled:
            return False
        if self.is_repetition_loop(text):
            return True
        n = len(self.signature(text))  # 去标点/空白后的字符数
        if avg_logprob < -2.0:
            return True
        if avg_logprob < -1.4 and n <= 3:
            return True
        if compression_ratio > 3.0 and n >= 6:
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
