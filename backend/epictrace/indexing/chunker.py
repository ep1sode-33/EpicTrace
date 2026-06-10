from __future__ import annotations

from dataclasses import dataclass

# 不引入 tokenizer 依赖:用字符数近似 token(~4 字符/token)。
DEFAULT_TARGET = 1800   # ~约 450-512 token
DEFAULT_OVERLAP = 200

# 优先级从高到低的断句点(在窗口尾部就近找一个,避免把句子切碎)。
_BOUNDARIES = ["\n\n", "\n", "。", "! ", "? ", ". ", "!", "?", ";", ";"]


@dataclass(frozen=True)
class Chunk:
    text: str
    char_start: int
    char_end: int


def _find_break(window: str, min_end: int) -> int | None:
    """在 window 内、位置 >= min_end 处,返回某个边界'之后'的索引;找不到返回 None。"""
    best = None
    for b in _BOUNDARIES:
        idx = window.rfind(b)
        if idx != -1 and idx + len(b) >= min_end:
            best = max(best or 0, idx + len(b))
    return best


def chunk_text(
    text: str, target: int = DEFAULT_TARGET, overlap: int = DEFAULT_OVERLAP
) -> list[Chunk]:
    if not text:
        return []
    n = len(text)
    chunks: list[Chunk] = []
    start = 0
    while start < n:
        end = min(start + target, n)
        if end < n:
            window = text[start:end]
            brk = _find_break(window, min_end=overlap)  # 至少要比 overlap 大,避免碎块
            if brk is not None:
                end = start + brk
        chunks.append(Chunk(text=text[start:end], char_start=start, char_end=end))
        if end >= n:
            break
        start = max(end - overlap, start + 1)  # 带重叠前进,保证 start 严格递增
    return chunks
