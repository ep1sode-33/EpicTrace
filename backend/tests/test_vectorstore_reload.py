"""回归:对"已存在的 collection"新建 MilvusLiteStore 也要能 search/query。

否则报 'Collection chunks is in state released; call load()' —— app 重启后第一次对话
检索必中(load_collection 之前只在新建分支里调)。

另:schema 自愈 —— 旧字段集(v1,缺 capture_session_id/ts)的 collection 重开时
须自动 drop 重建;字段集一致(如 attachment)则原样保留。
"""
from pathlib import Path

from epictrace.vectorstore.milvus_lite import _ATTACHMENT_SCALARS, _SCALARS, MilvusLiteStore

DIM = 1024

# v1 schema(升级前建的库):从当前 _SCALARS 去掉 v2 新增的两个字段,保持其余同步。
_OLD_SCALARS = {k: v for k, v in _SCALARS.items() if k not in ("capture_session_id", "ts")}


def _rec(rid: int, text: str) -> dict:
    return {
        "vector": [0.1] * DIM, "text": text, "ingest_record_id": rid, "project_id": 7,
        "char_start": 0, "char_end": len(text), "source_type": "folder_scan",
        "embed_model_id": "fake", "capture_session_id": 0, "ts": "",
    }


def _old_rec(rid: int, text: str) -> dict:
    r = _rec(rid, text)
    del r["capture_session_id"], r["ts"]
    return r


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


def test_reopen_v1_collection_drops_and_rebuilds(tmp_path: Path, caplog):
    """schema 自愈:v1 字段集(缺 capture_session_id/ts)的旧库重开 → drop + warning +
    按新 schema 重建;旧行清空(派生索引,可由重索引恢复),新格式行可写可查。"""
    db = str(tmp_path / "v.db")
    s1 = MilvusLiteStore(db_path=db, dim=DIM, scalars=_OLD_SCALARS)
    s1.upsert([_old_rec(1, "旧格式行")])
    s1.close()

    with caplog.at_level("WARNING", logger="epictrace"):
        s2 = MilvusLiteStore(db_path=db, dim=DIM)  # 默认新 _SCALARS → 字段集不一致
    assert any("重建" in r.message for r in caplog.records)
    assert s2.list_by_project(7) == []             # 旧行已随 drop 清空
    s2.upsert([_rec(2, "新格式行")])
    rows = s2.list_by_project(7)
    assert len(rows) == 1
    assert rows[0]["capture_session_id"] == 0 and rows[0]["ts"] == ""
    s2.close()


def test_reopen_attachment_collection_schema_unchanged_keeps_rows(tmp_path: Path):
    """attachment collection 的 scalars 未变:自愈比对一致 → 不 drop,数据原样保留。"""
    db = str(tmp_path / "v.db")
    arec = {"vector": [0.1] * DIM, "text": "附件块", "conversation_id": 1, "reference_id": 10,
            "char_start": 0, "char_end": 3, "source_type": "attachment", "embed_model_id": "fake"}
    s1 = MilvusLiteStore(db_path=db, dim=DIM, collection="attachment_chunks",
                         scalars=_ATTACHMENT_SCALARS)
    s1.upsert([arec])
    s1.close()

    s2 = MilvusLiteStore(db_path=db, dim=DIM, collection="attachment_chunks",
                         scalars=_ATTACHMENT_SCALARS)
    rows = s2.list_by({"conversation_id": 1})
    assert len(rows) == 1 and rows[0]["reference_id"] == 10
    s2.close()
