from __future__ import annotations

from epictrace.retrieval.types import RetrievedChunk

DEFAULT_PAGE_SIZE = 1200


def read_attachment_slice(
    *, reference_id: int, text: str, cursor: int, page_size: int = DEFAULT_PAGE_SIZE
) -> tuple[str, int, RetrievedChunk | None, bool]:
    """顺序切片缓存的 extracted_text。返回 (slice_text, next_cursor, chunk, done)。

    偏移即引用命门:chunk 的 char_start=cursor、char_end=cursor+len(slice),
    source_kind="attachment"、ingest_record_id=0(附件无 ingest 记录),供精确跳回外部文件。
    cursor 到/越过末尾 → 空串、chunk=None、done=True(调用方据此停止翻页)。"""
    n = len(text)
    start = max(0, cursor)
    if start >= n:
        return "", start, None, True
    end = min(n, start + page_size)
    slice_text = text[start:end]
    done = end >= n
    chunk = RetrievedChunk(
        text=slice_text,
        ingest_record_id=0,
        project_id=0,
        char_start=start,
        char_end=end,
        source_type="attachment",
        source_kind="attachment",
        reference_id=reference_id,
    )
    return slice_text, end, chunk, done
