"""检索内置工具(需求 3 工具清单):search_vector / search_hybrid / get_timestamp_citation。

包装现有 HybridRetriever / dense_search(检索模块不动,此处只做 agent 工具适配)。
检索件(embedder/Milvus/reranker)构造昂贵且有 macOS fork 顺序约束,因此工厂接收的是
零参惰性 getter,首次真正调用工具时才解析。
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select

from epictrace.db import Database
from epictrace.models import IngestRecord
from epictrace.retrieval.types import RetrievedChunk
from epictrace.cowork.tools.registry import ToolDef


def _format_chunks(db: Database, chunks: list[RetrievedChunk],
                   pool: list[RetrievedChunk] | None = None) -> str:
    """格式化检索结果。pool 非空时:chunks 追加进 turn 级引用池,编号从池长度继续——
    同一 turn 多次检索编号全局递增不重置,LLM 答案的 [n] 才能唯一映射回 chunk(引用链)。"""
    if not chunks:
        return "未检索到相关内容。"
    offset = 0
    if pool is not None:
        offset = len(pool)
        pool.extend(chunks)
    ids = {c.ingest_record_id for c in chunks}
    with db.session() as s:
        rows = s.execute(
            select(IngestRecord.id, IngestRecord.original_filename)
            .where(IngestRecord.id.in_(ids))
        ).all()
    names = {r[0]: r[1] for r in rows}
    parts: list[str] = []
    for i, c in enumerate(chunks, 1):
        name = names.get(c.ingest_record_id, f"记录#{c.ingest_record_id}")
        ts = f" | 时间 {c.ts}" if c.ts else ""
        parts.append(f"[{offset + i}] {name}{ts}(偏移 {c.char_start}-{c.char_end})\n{c.text}")
    return "\n\n".join(parts)


def build_retrieval_tools(
    db: Database,
    *,
    get_retriever: Callable[[], object],   # () -> HybridRetriever
    get_dense: Callable[[], Callable],     # () -> dense_search(embedder, store, ...) 偏函数
) -> list[ToolDef]:
    def _pool(ctx: dict | None) -> list[RetrievedChunk] | None:
        return ctx.get("chunk_pool") if ctx else None

    def search_vector(ctx: dict | None, project_id: int, query: str, k: int = 6) -> str:
        dense = get_dense()
        chunks = dense(project_id=project_id, query=query, k=max(1, min(int(k), 20)))
        return _format_chunks(db, chunks, _pool(ctx))

    def search_hybrid(ctx: dict | None, project_id: int, query: str, k: int = 6) -> str:
        chunks = get_retriever().retrieve(
            project_id=project_id, query=query, k=max(1, min(int(k), 20)))
        return _format_chunks(db, chunks, _pool(ctx))

    def get_timestamp_citation(ctx: dict | None, project_id: int, query: str, k: int = 8) -> str:
        chunks = get_retriever().retrieve(
            project_id=project_id, query=query, k=max(1, min(int(k), 20)))
        timed = [c for c in chunks if c.capture_session_id and c.ts]
        if not timed:
            return "检索结果中没有带时间戳的片段(相关内容可能不来自采集会话)。"
        return _format_chunks(db, timed, _pool(ctx))

    return [
        ToolDef(
            name="search_vector",
            description="语义相似搜索(BGE-M3 向量通道):按含义找相关片段,适合表述不精确的问题。",
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "query": {"type": "string", "description": "自然语言检索词"},
                    "k": {"type": "integer", "description": "返回条数,默认 6,最大 20"},
                },
                "required": ["project_id", "query"],
            },
            handler=search_vector,
            permission="allow",
            wants_ctx=True,
        ),
        ToolDef(
            name="search_hybrid",
            description="混合搜索(向量+关键词+重排序):检索项目资料的首选通道,返回带来源编号的片段。",
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "query": {"type": "string"},
                    "k": {"type": "integer", "description": "默认 6,最大 20"},
                },
                "required": ["project_id", "query"],
            },
            handler=search_hybrid,
            permission="allow",
            wants_ctx=True,
        ),
        ToolDef(
            name="get_timestamp_citation",
            description="获取带时间戳的引用:检索后只保留来自采集会话、可跳回原始时刻的片段。",
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "query": {"type": "string"},
                    "k": {"type": "integer", "description": "默认 8,最大 20"},
                },
                "required": ["project_id", "query"],
            },
            handler=get_timestamp_citation,
            permission="allow",
            wants_ctx=True,
        ),
    ]
