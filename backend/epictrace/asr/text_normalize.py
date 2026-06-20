"""中文转写文本规整:繁体→简体(OpenCC t2s)。

large-v3 + language="zh" 常吐繁体(真机:「這四個飄誤應該對麼」),既难读又让 HallucinationFilter
的简体精确表失配(「謝謝大家」匹配不上简体「谢谢大家」)。在引擎产文本处统一过一遍 t2s,既根治
繁体可读性,又是幻觉过滤的前置(filter 看到的已是简体)。

t2s 只规整简繁字形、不改语义/英文/数字,且基本是逐字映射 → 逐 word 独立转换时 word 数不变,
词级时间戳(start/end)原样保留,「字符偏移→时间戳→音频位置」对齐不破。

OpenCC 未安装 / 导入失败 / 运行期异常一律恒等降级(返回原文 + 仅告警一次),绝不让转写崩。
"""
from __future__ import annotations

import logging

_log = logging.getLogger("epictrace.asr")


class ChineseSimplifier:
    """繁体→简体规整器(OpenCC t2s 单例封装)。导入/构建失败则恒等降级。

    单例:构建一次复用(OpenCC 词典加载有成本,绝不每段新建)。convert 对空串/降级态/运行期
    异常都原样返回输入。
    """

    def __init__(self) -> None:
        self._cc = None
        try:
            from opencc import OpenCC

            self._cc = OpenCC("t2s")
        except Exception as e:  # noqa: BLE001 — 缺 opencc/词典加载失败都降级,不拦转写
            _log.warning("ChineseSimplifier disabled (opencc unavailable): %s", e)

    def convert(self, text: str) -> str:
        if not text or self._cc is None:
            return text
        try:
            return self._cc.convert(text)
        except Exception as e:  # noqa: BLE001 — 运行期异常恒等降级
            _log.warning("ChineseSimplifier convert failed, passthrough: %s", e)
            return text
