"""带 filter 的 dense 检索深度回归:filter 必须在向量索引层生效(真 filtered KNN),
不能是"全局候选 ~beam 内后过滤"。

背景(准度侦察实锤的 bug):milvus-lite 的 HNSW 检索先做全局 beam 遍历(ef 不可调,
候选 ~一两百条封顶),再拿 filter 当 mask 圈结果 —— project 过滤等价于"全局 topN ∩ 项目",
项目的有效 dense 深度只剩个位~几十条,且随其他项目语料增长静默劣化(池级召回失败)。

测试配方:project 1 用 1200 条紧贴 query 的向量霸占全局 top,project 2 放 60 条远离
query 的向量(含一条项目内最优的 GOLD)。索引建成后(等 .idx 落盘),按 project 2 过滤
查 k=30:修复前返回 0 条;修复后必须返回满 30 条且 GOLD 排第一。
"""
from __future__ import annotations

import random
import time
from pathlib import Path

import pytest

from epictrace.vectorstore.milvus_lite import _ATTACHMENT_SCALARS, MilvusLiteStore

DIM = 32
QUERY = [1.0] + [0.0] * (DIM - 1)
_ORTHO = [0.0, 1.0] + [0.0] * (DIM - 2)
GOLD_VEC = [0.5, 0.866] + [0.0] * (DIM - 2)   # 与 query 余弦 0.5:项目内第一、全局排不上号


def _noisy(base: list[float], eps: float, rng: random.Random) -> list[float]:
    return [b + rng.uniform(-eps, eps) for b in base]


def _rec(vec: list[float], rid: int, pid: int, text: str) -> dict:
    return {"vector": vec, "text": text, "ingest_record_id": rid, "project_id": pid,
            "char_start": 0, "char_end": len(text), "source_type": "folder_scan",
            "embed_model_id": "fake", "capture_session_id": 0, "ts": ""}


def _wait_index_built(db_path: str, timeout: float = 30.0) -> None:
    """等后台索引构建落盘(.idx 文件出现)。

    没建成索引时 milvus-lite 走 memtable/brute-force,filter 天然正确 —— 截断 bug 只在
    索引建成后出现,所以必须等到 .idx 才算进入被测状态。文件布局是 milvus-lite 内部细节,
    但没有公开的"索引已建成"API;布局变了这里会超时报错,宁可响也不静默测错状态。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if list(Path(db_path).glob("**/indexes/*.idx")):
            time.sleep(0.2)   # 落盘与查询侧挂载之间留一拍
            return
        time.sleep(0.2)
    raise AssertionError(f"{timeout}s 内未见 .idx 落盘:索引未建成,测试前置状态不成立")


@pytest.fixture(scope="module")
def adversarial_store(tmp_path_factory) -> MilvusLiteStore:
    """两个 project 的对抗数据 + 已建成的向量索引(模块内共享,只读)。"""
    db = str(tmp_path_factory.mktemp("filtered_depth") / "v.db")
    store = MilvusLiteStore(db_path=db, dim=DIM)
    rng = random.Random(7)
    rows = [_rec(_noisy(QUERY, 0.01, rng), 1, 1, f"p1-{i}") for i in range(1200)]
    rows += [_rec(_noisy(_ORTHO, 0.05, rng), 2, 2, f"p2-{i}") for i in range(59)]
    rows.append(_rec(GOLD_VEC, 3, 2, "GOLD"))
    for i in range(0, len(rows), 200):
        store.upsert(rows[i:i + 200])
    store._client.flush("chunks")   # 封 segment,触发后台建索引(白盒:只在测试里用)
    _wait_index_built(db)
    yield store
    store.close()


def test_project_filter_gets_full_depth_not_global_truncation(adversarial_store):
    """project 过滤查 k=30:必须返回项目内真 top-30(满 30 条、GOLD 第一)。

    修复前:HNSW 全局 beam 里全是 project 1,后过滤剩 0 条 —— dense_n 形同虚设。"""
    hits = adversarial_store.query(QUERY, filter={"project_id": 2}, k=30)
    assert len(hits) == 30
    assert hits[0]["text"] == "GOLD"


def test_focus_filter_ingest_record_ids_gets_full_depth(adversarial_store):
    """聚焦过滤(project + ingest_record_id IN,Plan 4 机制)同一条路,同样不许被全局候选截断。"""
    hits = adversarial_store.query(
        QUERY, filter={"project_id": 2, "ingest_record_id": [3]}, k=5)
    assert [h["text"] for h in hits] == ["GOLD"]


def test_attachment_filter_gets_full_depth(tmp_path):
    """attachment collection(Plan 5)同引擎同病:conversation/reference 过滤也要真 filtered KNN。"""
    db = str(tmp_path / "a.db")
    store = MilvusLiteStore(db_path=db, dim=DIM, collection="attachment_chunks",
                            scalars=_ATTACHMENT_SCALARS)
    rng = random.Random(11)

    def arec(vec: list[float], cid: int, rid: int, text: str) -> dict:
        return {"vector": vec, "text": text, "conversation_id": cid, "reference_id": rid,
                "char_start": 0, "char_end": len(text), "source_type": "attachment",
                "embed_model_id": "fake"}

    rows = [arec(_noisy(QUERY, 0.01, rng), 1, 10, f"c1-{i}") for i in range(1200)]
    rows += [arec(_noisy(_ORTHO, 0.05, rng), 2, 20, f"c2-{i}") for i in range(59)]
    rows.append(arec(GOLD_VEC, 2, 20, "GOLD"))
    for i in range(0, len(rows), 200):
        store.upsert(rows[i:i + 200])
    store._client.flush("attachment_chunks")
    _wait_index_built(db)

    hits = store.query(QUERY, filter={"conversation_id": 2, "reference_id": [20]}, k=30)
    assert len(hits) == 30
    assert hits[0]["text"] == "GOLD"
    store.close()


# 旧版建库方式(HNSW 索引)在子进程里跑:构建 HNSW 会走 faiss,faiss 的 libomp 与 pytest
# 进程内已加载的另一份 libomp 相撞会段错误(真机踩过);子进程 + 已知 env 组合彻底隔离。
_LEGACY_BUILD_SCRIPT = """
import random, sys, time
from pathlib import Path
from pymilvus import DataType, MilvusClient
from epictrace.vectorstore.milvus_lite import _SCALARS

db, dim = sys.argv[1], int(sys.argv[2])
client = MilvusClient(db)
schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
schema.add_field("id", DataType.INT64, is_primary=True)
schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
for name, (dtype, kw) in _SCALARS.items():
    schema.add_field(name, dtype, **kw)
ip = client.prepare_index_params()
ip.add_index(field_name="vector", index_type="HNSW", metric_type="COSINE",
             params={"M": 16, "efConstruction": 200})
client.create_collection("chunks", schema=schema, index_params=ip)
client.load_collection("chunks")

query = [1.0] + [0.0] * (dim - 1)
ortho = [0.0, 1.0] + [0.0] * (dim - 2)
gold = [0.5, 0.866] + [0.0] * (dim - 2)
rng = random.Random(3)
def noisy(base, eps):
    return [b + rng.uniform(-eps, eps) for b in base]
def rec(vec, rid, pid, text):
    return {"vector": vec, "text": text, "ingest_record_id": rid, "project_id": pid,
            "char_start": 0, "char_end": len(text), "source_type": "folder_scan",
            "embed_model_id": "fake", "capture_session_id": 0, "ts": ""}
rows = [rec(noisy(query, 0.01), 1, 1, f"p1-{i}") for i in range(1200)]
rows += [rec(noisy(ortho, 0.05), 2, 2, f"p2-{i}") for i in range(59)]
rows.append(rec(gold, 3, 2, "GOLD"))
for i in range(0, len(rows), 200):
    client.insert("chunks", rows[i:i + 200])
client.flush("chunks")
deadline = time.time() + 30
while time.time() < deadline:
    if list(Path(db).glob("**/indexes/*.idx")):
        break
    time.sleep(0.2)
else:
    raise SystemExit("legacy HNSW index not built in 30s")
client.close()
"""


def _build_legacy_hnsw_collection(db: str) -> None:
    import os
    import subprocess
    import sys

    env = {**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE", "OMP_NUM_THREADS": "1"}
    subprocess.run([sys.executable, "-c", _LEGACY_BUILD_SCRIPT, db, str(DIM)],
                   check=True, env=env, cwd=str(Path(__file__).resolve().parents[1]),
                   timeout=120)


def test_reopen_legacy_hnsw_collection_heals_index_to_flat(tmp_path, caplog):
    """存量库(HNSW 索引)重开 → 索引就地换成 FLAT:数据一行不丢、无需重新 embedding,
    过滤检索立刻恢复真实深度。这是老用户库的迁移路径(schema 自愈只看字段集,管不到索引类型)。"""
    db = str(tmp_path / "v.db")
    _build_legacy_hnsw_collection(db)

    with caplog.at_level("WARNING", logger="epictrace"):
        store = MilvusLiteStore(db_path=db, dim=DIM)
    assert any("FLAT" in r.message for r in caplog.records)   # 自愈换索引要留痕
    names = store._client.list_indexes("chunks")
    assert [store._client.describe_index("chunks", n)["index_type"] for n in names] == ["FLAT"]
    hits = store.query(QUERY, filter={"project_id": 2}, k=30)
    assert len(hits) == 30
    assert hits[0]["text"] == "GOLD"
    assert len(store.list_by_project(1)) == 1200   # 向量是派生物没错,但这次连派生物都不用重算
    store.close()
