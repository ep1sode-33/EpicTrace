"""附件工具(从旧 agent/tools.py 移植到 cowork 注册表):search_attachment / read_attachment。

按 session 的活跃引用(ReferenceService.list_active)过滤:
- search_attachment:会话级临时集合(mode=indexed 的外部引用)语义检索;
- read_attachment:有缓存全文的引用(fulltext 一次返回;indexed 分页,cursor 翻页)。
检索 chunks 经 exec_ctx["chunk_pool"] 旁路进 turn 级引用池(与项目检索同一引用链)。
"""

from __future__ import annotations

from collections.abc import Callable

from epictrace.db import Database
from epictrace.retrieval.types import RetrievedChunk
from epictrace.services.references import ReferenceService
from epictrace.cowork.tools.registry import ToolDef

DEFAULT_PAGE_SIZE = 1200
_READ_LIMIT = 20000


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


def _append_pool(ctx: dict | None, chunks: list[RetrievedChunk], pool_offset_base: int = 0) -> int:
    """chunks 追加进 turn 级 pool(若有),返回本次起始编号(与项目检索的全局编号一致)。"""
    if not ctx:
        return 1
    pool = ctx.get("chunk_pool")
    if pool is None:
        return 1
    offset = len(pool)
    pool.extend(chunks)
    return offset + 1


def build_attachment_tools(
    db: Database,
    *,
    session_id: int,
    get_attachment_retriever: Callable[[], object | None],
) -> list[ToolDef]:
    def _refs() -> list[dict]:
        return ReferenceService(db).list_active(session_id)

    def _indexed_ext_ids() -> list[int]:
        return [r["id"] for r in _refs()
                if r["kind"] == "external" and r["mode"] == "indexed"]

    def _reference_texts() -> dict[int, str]:
        return {r["id"]: r["extracted_text"] for r in _refs() if r.get("extracted_text")}

    def _fulltext_ids() -> set[int]:
        return {r["id"] for r in _refs() if r["mode"] == "fulltext"}

    def search_attachment(ctx: dict | None, query: str, k: int = 6) -> str:
        ids = _indexed_ext_ids()
        if not ids:
            return "本会话没有已建索引的附件(只有 mode=indexed 的外部引用可语义检索)。"
        ar = get_attachment_retriever()
        if ar is None:
            return "Error: 附件检索不可用(embedder/attachment store 未就绪)"
        chunks = ar.retrieve(session_id=session_id, reference_ids=ids, query=query,
                             k=max(1, min(int(k), 20)))
        if not chunks:
            return "附件中未检索到相关内容。"
        base = _append_pool(ctx, chunks)
        names = {r["id"]: r["display_name"] for r in _refs()}
        return "\n\n".join(
            f"[{base + i}] 附件「{names.get(c.reference_id, c.reference_id)}」"
            f"(偏移 {c.char_start}-{c.char_end})\n{c.text}"
            for i, c in enumerate(chunks)
        )

    def read_attachment(ctx: dict | None, reference_id: int, cursor: int = 0) -> str:
        texts = _reference_texts()
        text = texts.get(reference_id)
        if text is None:
            return f"Error: reference_id={reference_id} 不是本会话的可读附件(无可读全文)。"
        if reference_id in _fulltext_ids():
            chunk = RetrievedChunk(
                text=text, ingest_record_id=0, project_id=0,
                char_start=0, char_end=len(text),
                source_type="attachment", source_kind="attachment",
                reference_id=reference_id)
            base = _append_pool(ctx, [chunk])
            body = text[:_READ_LIMIT]
            note = f"(全文 {len(text)} 字符)" if len(text) <= _READ_LIMIT else \
                f"(全文 {len(text)} 字符,截取前 {_READ_LIMIT})"
            return f"[{base}] 附件全文 {note}:\n{body}\n\n[done=True]"
        slice_text, next_cursor, chunk, done = read_attachment_slice(
            reference_id=reference_id, text=text, cursor=cursor)
        if chunk is None:
            return f"(已到文件末尾,无更多内容;done={done})"
        base = _append_pool(ctx, [chunk])
        return (f"[{base}] 附件第 {cursor}-{next_cursor} 字符:\n{slice_text}"
                f"\n\n[next_cursor={next_cursor}, done={done}]")

    return [
        ToolDef(
            name="search_attachment",
            description=(
                "语义检索用户附加到本会话的外部文件(仅 mode=indexed 的附件可检索)。"
                "问题针对附件具体内容时用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "中文检索词"},
                    "k": {"type": "integer", "description": "默认 6,最大 20"},
                },
                "required": ["query"],
            },
            handler=search_attachment,
            permission="allow",
            wants_ctx=True,
        ),
        ToolDef(
            name="read_attachment",
            description=(
                "读取本会话附件的原文(fulltext 小文件一次返回;大文件按 cursor 分页,"
                "用返回的 next_cursor 翻页)。reference_id 取自「可读附件清单」。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reference_id": {"type": "integer"},
                    "cursor": {"type": "integer", "description": "分页起点,默认 0"},
                },
                "required": ["reference_id"],
            },
            handler=read_attachment,
            permission="allow",
            wants_ctx=True,
        ),
    ]


def attachment_manifest(db: Database, session_id: int) -> str:
    """「可读附件清单」文本,注入 system prompt(空则返回空串,不注入)。
    对齐旧栈的 manifest 注入(chat.py),让 LLM 知道有哪些附件及 reference_id。"""
    refs = ReferenceService(db).list_active(session_id)
    if not refs:
        return ""
    lines = ["# 本会话附件"]
    for r in refs:
        readable = "可读全文" if r.get("extracted_text") else "仅语义检索" if r["mode"] == "indexed" else "仅记录"
        lines.append(f"- id={r['id']} | {r['display_name']} | {r['kind']} | "
                     f"{r['mode']} | {r['text_chars']} 字符 | {readable}")
    lines.append("需要附件内容时:小文件用 read_attachment 读原文;大文件先 search_attachment 定位再 read_attachment 翻页。")
    return "\n".join(lines)
