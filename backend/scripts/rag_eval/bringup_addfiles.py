"""Bring-up(手动跑):把 eval-data manifest 里尚未入库的新语料文件**增量**加入 project 2 并索引。
只动新记录(已有记录 indexed=True 被 index_project 跳过),保住已生成的 golden。BGE warmup 在建
Milvus(gRPC)之前(macOS fork 段错误)。
用法: ./.venv/bin/python -m scripts.rag_eval.bringup_addfiles
"""
from __future__ import annotations

from pathlib import Path


def add_and_index(eval_data_dir: str = "eval-data", project_id: int = 2) -> dict:
    from sqlalchemy import select

    from epictrace.config import AppConfig
    from epictrace.db import Database
    from epictrace.embedding.bge_m3 import BgeM3Embedder
    from epictrace.models import IngestRecord
    from epictrace.services.index import IndexService
    from epictrace.services.ingest import IngestService
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore
    from scripts.rag_eval.corpus import load_manifest

    cfg = AppConfig()
    db = Database(cfg)
    corpus_names = {m.rel_path for m in load_manifest(Path(eval_data_dir) / "manifest.jsonl")}
    with db.session() as s:
        existing = {Path(r.stored_path).name for r in s.execute(
            select(IngestRecord).where(IngestRecord.project_id == project_id)).scalars()}

    ing = IngestService(db)
    added = []
    for f in sorted(Path(eval_data_dir).glob("*")):
        if not f.is_file() or f.name not in corpus_names or f.name in existing:
            continue  # 只入库 manifest 里的真语料,且跳过已入库的(原 10)
        try:
            rec = ing.ingest_file(project_id, str(f), ingest_method="rag_eval", description="")
            added.append((rec.id, f.name))
            print(f"  ingested rid={rec.id} {f.name}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  skip {f.name}: {e}", flush=True)

    # 索引:index_project 只取 indexed=False 且有 processor 的记录(即新增的)。
    svc = IndexService(db, BgeM3Embedder(), lambda: MilvusLiteStore(db_path=cfg.milvus_path))
    job = svc.index_project(project_id)
    svc.run_in_background(job).join()
    return {"added": added}


if __name__ == "__main__":
    out = add_and_index()
    print(f"[addfiles] 新增入库 {len(out['added'])} 个文件,已索引新记录。")
