from __future__ import annotations

import math

# 保守的中英混合估算:宁可高估 token 数(少塞文件)。约 2 字符/token。
CHARS_PER_TOKEN = 2.0
# 全文注入最多占模型上下文窗口的一半,余下留给系统提示 / 历史 / 项目 RAG / 答案头寸。
FULLTEXT_FRACTION = 0.5


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def fulltext_budget(context_window: int) -> int:
    return max(0, int(context_window * FULLTEXT_FRACTION))


def fits_fulltext(text: str, context_window: int, used_tokens: int = 0) -> bool:
    return used_tokens + estimate_tokens(text) <= fulltext_budget(context_window)
