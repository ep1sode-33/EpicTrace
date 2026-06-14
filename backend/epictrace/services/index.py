from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.indexing.chunker import chunk_text
from epictrace.interfaces.embedding import EmbeddingProvider
from epictrace.interfaces.vector_store import VectorStore
from epictrace.media import get_processor
from epictrace.models import IngestRecord


@dataclass
class IndexJob:
    project_id: int
    total: int = 0
    done: int = 0
    status: str = "running"          # running | done | error
    errors: list[str] = field(default_factory=list)
    # 后台线程逐文件更新 done/errors,API 轮询读取;单用户本地用锁足够。
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


class IndexService:
    def __init__(self, db: Database, embedder: EmbeddingProvider, vector_store) -> None:
        # vector_store 可以是 VectorStore 实例,或返回它的可调用(getter)。
        # 用 getter 时,Milvus(gRPC)的构造会被推迟到 _run 里、在 warmup 之后,
        # 避免 'gRPC 激活后再加载模型' 段错误(macOS)。
        self._db = db
        self._embedder = embedder
        self._vector_store = vector_store

    def _resolve_store(self) -> VectorStore:
        vs = self._vector_store
        return vs() if callable(vs) else vs

    def index_project(self, project_id: int) -> IndexJob:
        """构建一个 running 的 IndexJob(算好 total),不在此处跑活。

        调用方拿到 job 后用 run_in_background / _run 让 per-file 工作在后台推进,
        从而 API 能立刻返回 running 状态、再由 status 轮询读取实时进度。
        """
        # 取该项目待索引、且有可用 processor 的文件
        with self._db.session() as s:
            recs = list(
                s.execute(
                    select(IngestRecord).where(
                        IngestRecord.project_id == project_id,
                        IngestRecord.indexed.is_(False),
                    )
                ).scalars()
            )
            targets = [(r.id, r.stored_path) for r in recs if get_processor(Path(r.stored_path), self._db.config) is not None]

        job = IndexJob(project_id=project_id, total=len(targets))
        job._targets = targets  # type: ignore[attr-defined]  # 交给 _run 消费
        return job

    def reindex_project(self, project_id: int) -> IndexJob:
        """用当前提取引擎重建该项目的索引:已索引文件用旧引擎提取过,常规 index_project
        只碰 indexed=False 的记录、永远不会重提它们 —— 重建先把派生索引清空、把记录翻回
        待索引,再走 index_project 的同一条后台流水线。

        步骤:(a) 清该项目全部向量;(b) 重置全部 IngestRecord.indexed=False;
        (c) 尽力删该项目记录的 provenance sidecar(派生缓存,可由重跑 MinerU 重建,删失败不阻断);
        (d) 返回与 index_project 同形的 running 的 IndexJob,交给 run_in_background 推进。
        """
        # (a) 清向量。向量是可重建的派生索引;这里整项目清,后台再逐文件 upsert 回去。
        self._resolve_store().delete_by_project(project_id)

        # (b) 把该项目所有记录翻回待索引,使 index_project 重新纳入它们。
        with self._db.session() as s:
            recs = list(
                s.execute(
                    select(IngestRecord).where(IngestRecord.project_id == project_id)
                ).scalars()
            )
            rec_ids = [r.id for r in recs]
            for r in recs:
                r.indexed = False

        # (c) 尽力删 provenance sidecar(派生/可选缓存);失败仅记日志,不阻断重建。
        try:
            prov_dir = Path(self._db.config.provenance_dir)
            for rid in rec_ids:
                sidecar = prov_dir / f"ingest-{rid}.json"
                if sidecar.exists():
                    sidecar.unlink()
        except Exception as e:  # noqa: BLE001 — sidecar 是派生缓存,删失败不影响重建
            import logging

            logging.getLogger("epictrace").warning(
                "重建项目 %s 时清理 provenance sidecar 失败(不阻断): %s", project_id, e
            )

        # (d) 复用 index_project 的同一条流水线(同样的 IndexJob 形状 + 后台推进 + status 轮询)。
        return self.index_project(project_id)

    def run_in_background(self, job: IndexJob) -> threading.Thread:
        """在守护线程里跑 _run(job),立刻返回线程对象;job 会被原地更新。"""
        t = threading.Thread(target=self._run, args=(job,), daemon=True)
        t.start()
        return t

    def _run(self, job: IndexJob) -> None:
        targets = getattr(job, "_targets", [])
        # 关键顺序:先加载模型(warmup),再构造/使用 Milvus(gRPC)。
        # 反过来(gRPC 已激活后再 fork 加载模型)会在 macOS 上段错误。
        self._embedder.warmup()
        store = self._resolve_store()
        for rec_id, path_str in targets:
            try:
                path = Path(path_str)
                proc = get_processor(path, self._db.config)
                text = proc.process(path).text
                chunks = chunk_text(text)
                # 幂等:提取成功后、入库前无条件清旧块,
                # 这样「现在提取为空」的文件也能清掉历史向量。
                store.delete_by_record(rec_id)
                if chunks:
                    vectors = self._embedder.embed([c.text for c in chunks])
                    store.upsert([
                        {
                            "vector": vec, "text": c.text,
                            "ingest_record_id": rec_id, "project_id": job.project_id,
                            "char_start": c.char_start, "char_end": c.char_end,
                            "source_type": "folder_scan",
                            "embed_model_id": self._embedder.model_id,
                        }
                        for c, vec in zip(chunks, vectors)
                    ])
                # 标记已索引
                with self._db.session() as s:
                    r = s.get(IngestRecord, rec_id)
                    if r is not None:
                        r.indexed = True
                with job._lock:
                    job.done += 1
            except Exception as e:  # 单文件失败:记录并继续
                with job._lock:
                    job.errors.append(f"{path_str}: {e}")
        with job._lock:
            job.status = "done"
