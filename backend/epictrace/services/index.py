from __future__ import annotations

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


class IndexService:
    def __init__(self, db: Database, embedder: EmbeddingProvider, vector_store: VectorStore) -> None:
        self._db = db
        self._embedder = embedder
        self._store = vector_store

    def index_project(self, project_id: int) -> IndexJob:
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
            targets = [(r.id, r.stored_path) for r in recs if get_processor(Path(r.stored_path)) is not None]

        job = IndexJob(project_id=project_id, total=len(targets))
        for rec_id, path_str in targets:
            try:
                path = Path(path_str)
                proc = get_processor(path)
                text = proc.process(path).text
                chunks = chunk_text(text)
                if chunks:
                    vectors = self._embedder.embed([c.text for c in chunks])
                    self._store.delete_by_record(rec_id)  # 幂等:重索引先清旧块
                    self._store.upsert([
                        {
                            "vector": vec, "text": c.text,
                            "ingest_record_id": rec_id, "project_id": project_id,
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
                job.done += 1
            except Exception as e:  # 单文件失败:记录并继续
                job.errors.append(f"{path_str}: {e}")
        job.status = "done"
        return job
