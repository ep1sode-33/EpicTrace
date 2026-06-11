"""回归:对"已存在的 collection"新建 MilvusLiteStore 也要能 search/query。

否则报 'Collection chunks is in state released; call load()' —— app 重启后第一次对话
检索必中(load_collection 之前只在新建分支里调)。
"""
from pathlib import Path

from epictrace.vectorstore.milvus_lite import MilvusLiteStore

DIM = 1024


def _rec(rid: int, text: str) -> dict:
    return {
        "vector": [0.1] * DIM, "text": text, "ingest_record_id": rid, "project_id": 7,
        "char_start": 0, "char_end": len(text), "source_type": "folder_scan",
        "embed_model_id": "fake",
    }


def test_reopened_store_loads_existing_collection(tmp_path: Path):
    db = str(tmp_path / "v.db")
    s1 = MilvusLiteStore(db_path=db, dim=DIM)
    s1.upsert([_rec(1, "虚拟内存与页表")])
    s1.close()  # 释放独占锁,模拟 app 关闭

    s2 = MilvusLiteStore(db_path=db, dim=DIM)  # collection 已存在;__init__ 必须自动 load
    hits = s2.query([0.1] * DIM, filter={"project_id": 7}, k=3)  # 修复前这里报 'released'
    assert len(hits) == 1 and hits[0]["text"] == "虚拟内存与页表"
    rows = s2.list_by_project(7)  # BM25 语料的 query 路径也要能用
    assert len(rows) == 1
    s2.close()
