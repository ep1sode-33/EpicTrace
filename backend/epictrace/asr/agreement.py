"""流式词级一致性确认(LocalAgreement / prefix-agreement)。

核心 IP(移植自参考产品的 AgreementStrategies):每轮在滑动窗上转写得到词数组,与上一轮词数组
求**最长公共前缀**(LCP,逐词规整比对);公共前缀去掉末尾 anchor 个词 = 本轮可确认(已稳定)的
词,末尾 anchor 个词留作 anchor(喂回下轮当 prefix 上下文 + 等进一步印证)。这让"稳定下来的词"
尽快确认显示(低延迟)且抗逐词抖动(高准确)——替换原 stream_state 的"段数启发式"(一窗多段就
确认除末段外所有段,从不逐词比对)。

中文:anchor(tc)=1(参考产品 CHINESE_TUNING 实测中文逐字确认最优)。纯逻辑,可单测,与引擎/繁简无关。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from epictrace.asr.types import WordTiming

# 比对规整:剥掉所有非词字符(标点 + 空格;CJK/字母/数字是 \w,保留)。仅用于 LCP 匹配,
# **不改 emit 的原文**——这样「很好,」与「很好」、「 今天」与「今天」视作同词,相邻窗对标点/
# 空格附着的细微差异不再打断公共前缀(否则标点边界处永远确认不下来、或丢标点)。
_NORM_STRIP = re.compile(r"[\W_]+", re.UNICODE)


def _norm(word: str) -> str:
    """词规整(仅比对用):剥非词字符 + 小写。中文逐字比对不受影响;标点/空格差异被吸收。"""
    return _NORM_STRIP.sub("", word).lower()


def longest_common_prefix(prev: list[WordTiming],
                          current: list[WordTiming]) -> list[WordTiming]:
    """两词数组从头逐词规整比对的最长公共前缀(返回 current 侧的前缀切片)。"""
    n = 0
    for a, b in zip(prev, current):
        if _norm(a.word) == _norm(b.word):
            n += 1
        else:
            break
    return current[:n]


@dataclass(frozen=True)
class AgreementResult:
    confirmed: list[WordTiming]  # 本轮确认 emit 的词
    anchor: list[WordTiming]     # 留作下轮 prefix 上下文的词(未确认)
    advanced: bool               # 本轮是否有新确认


def prefix_agreement(prev: list[WordTiming], current: list[WordTiming],
                     anchor_words: int) -> AgreementResult:
    """prefix/LCP 策略(参考产品生产默认):LCP 去掉末尾 anchor_words 个词作 confirmed,
    末尾 anchor_words 个作 anchor。prev 为空(首轮)或公共前缀不足 anchor_words → 不确认。"""
    if not prev:
        return AgreementResult([], [], False)
    lcp = longest_common_prefix(prev, current)
    if len(lcp) < anchor_words:
        return AgreementResult([], [], False)
    cut = len(lcp) - anchor_words
    confirmed = lcp[:cut]
    anchor = lcp[cut:]
    return AgreementResult(confirmed, anchor, bool(confirmed))


@dataclass
class AgreementState:
    """单通道的流式词级确认状态。

    用法:每轮把(已平移到会话绝对时间的)词数组喂 ingest(),它与上轮做 prefix-agreement,返回本轮
    要确认 emit 的词;维护上一轮 hypothesis、已确认末端时间(confirmed_end,供滑窗起点)、anchor
    (下轮 prefix 上下文)、无进展计数(force_confirm_after 轮无进展则强制确认防卡死)。
    """

    anchor_words: int = 1          # 中文 tc=1
    force_confirm_after: int = 4   # 连续 N 轮无进展 → 强制确认(防卡死)
    confirmed_end: float = 0.0     # 已确认词的末端绝对秒(滑窗起点 = 它 - lookback)
    anchor: list[WordTiming] = field(default_factory=list)  # 下轮喂引擎的 prefix 上下文
    _prev: list[WordTiming] = field(default_factory=list)
    _no_advance: int = 0

    def ingest(self, current: list[WordTiming]) -> list[WordTiming]:
        """喂入本轮(绝对时间)词数组,返回本轮要确认 emit 的词,并维护内部状态。"""
        had_prev = bool(self._prev)
        res = prefix_agreement(self._prev, current, self.anchor_words)
        confirmed = list(res.confirmed)
        self.anchor = list(res.anchor)
        if res.advanced:
            self._no_advance = 0
        elif had_prev:
            # 仅在真比对过(上轮有 hypothesis)时才计无进展;首轮(prev 空)不计。
            self._no_advance += 1
            # 强制确认:连续 force_confirm_after 轮无进展(模型反复吐同样不稳定的尾巴)→ 确认
            # current 除末尾 anchor_words 外的词,推进进度,避免永远卡住。
            if self._no_advance >= self.force_confirm_after and len(current) > self.anchor_words:
                cut = len(current) - self.anchor_words
                confirmed = list(current[:cut])
                self.anchor = list(current[cut:])
                self._no_advance = 0
        self._prev = list(current)
        if confirmed:
            self.confirmed_end = confirmed[-1].end
        return confirmed

    def anchor_text(self) -> str:
        """anchor 词拼成的文本(喂引擎当 prefix/initial_prompt 维持上下文)。"""
        return "".join(w.word for w in self.anchor)
