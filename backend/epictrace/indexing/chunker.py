from __future__ import annotations

from dataclasses import dataclass

# target/overlap 是"英文等效字符数":英文 ~4 字符/token,1800 字 ≈ 450-512 token。
# 但中文 ~1.3 字/token,同样 1800 字是 2-3 倍 token、块过大、检索粒度/引用跨度粗。
# chunk_text 按文本实际语言组成把字符目标**缩放到统一 token 预算**(见 _chars_per_token),
# 不引 tokenizer 依赖;纯英文缩放比≈1、行为不变。
DEFAULT_TARGET = 1800   # 英文 ~450-512 token
DEFAULT_OVERLAP = 200
_REF_CPT = 4.0          # 英文基线:每 token ~4 字符(缩放参照)

# 优先级从高到低的断句点(在窗口尾部就近找一个,避免把句子切碎)。
_BOUNDARIES = ["\n\n", "\n", "。", "! ", "? ", ". ", "!", "?", ";", ";"]


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF      # CJK 统一表意
            or 0x3400 <= o <= 0x4DBF   # 扩展 A
            or 0xF900 <= o <= 0xFAFF   # 兼容表意
            or 0x3000 <= o <= 0x303F   # CJK 标点
            or 0xFF00 <= o <= 0xFFEF)  # 全角符号


def _chars_per_token(text: str) -> float:
    """估每 token 字符数(不引 tokenizer):CJK 约 1.3 字/token,其余(拉丁/数字/空白)约 4。
    按文本组成加权 → 纯英文≈4、纯中文≈1.3、混合居中。"""
    if not text:
        return _REF_CPT
    cjk = sum(1 for ch in text if _is_cjk(ch))
    est_tokens = cjk / 1.3 + (len(text) - cjk) / _REF_CPT
    return len(text) / est_tokens if est_tokens > 0 else _REF_CPT


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
    if not text.strip():  # 空 / 仅空白:无可索引内容
        return []
    # 按文本语言把字符目标缩放到统一 token 预算:中文块不再是英文的 2-3 倍大。
    scale = _chars_per_token(text) / _REF_CPT
    overlap = max(round(overlap * scale), 30)
    target = max(round(target * scale), 3 * overlap)
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
