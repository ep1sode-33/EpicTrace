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


# milvus-lite 私有布局依赖,集中一处:引擎把每个 segment 的向量索引持久化成
# <db>/partitions/<partition>/indexes/<segment>.<field>.<type>.idx sidecar 文件。
_IDX_GLOB = "**/indexes/*.idx"


def _sidecar_index_files(db_path: str) -> list[Path]:
    """按引擎私有布局(_IDX_GLOB)找索引 sidecar 文件 —— 白盒探测,只许测试用。

    引擎没有公开 API 能问"索引文件在哪/是否已落盘",而测试要等索引建成、要对 sidecar
    注入故障(截断/删除),只能按布局摸文件。升级 milvus-lite 后本文件若开始超时/报错,
    先查引擎索引文件布局是否变了(另见 docs/decisions/ 的 FLAT 索引决策记录)。"""
    return sorted(Path(db_path).glob(_IDX_GLOB))


def _restart_engine(db_path: str) -> None:
    """模拟 app 重启(与 _sidecar_index_files 同级的引擎内部白盒依赖)。

    MilvusClient.close 只断 gRPC 通道;milvus-lite 3.0 的 in-process 引擎(后台线程
    gRPC server + 内存态 collection)仍挂在 server_manager 上,同进程重开同一路径会
    复用活引擎 —— 那不是重启。显式 release 才是冷启动:重读 manifest、从 sidecar
    重载索引(sidecar 故障注入要测的正是这条加载路径)。"""
    from milvus_lite.server_manager import server_manager_instance
    server_manager_instance.release_server(db_path)


def _wait_index_built(db_path: str, timeout: float = 30.0) -> None:
    """等后台索引构建落盘(.idx 文件出现)。

    没建成索引时 milvus-lite 走 memtable/brute-force,filter 天然正确 —— 截断 bug 只在
    索引建成后出现,所以必须等到 .idx 才算进入被测状态。布局变了这里会超时报错,
    宁可响也不静默测错状态。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _sidecar_index_files(db_path):
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


def test_filter_matching_zero_rows_returns_empty(adversarial_store):
    """filter 命中 0 行:必须返回空列表,绝不能漏放其他 project 的行
    (引擎 valid_indices 为空的早退路径,此前只有源码级保证、没有直测)。"""
    assert adversarial_store.query(QUERY, filter={"project_id": 999}, k=30) == []


def test_attachment_filter_gets_full_depth(tmp_path):
    """attachment collection(Plan 5)同引擎同病:conversation/reference 过滤也要真 filtered KNN。"""
    db = str(tmp_path / "a.db")
    store = MilvusLiteStore(db_path=db, dim=DIM, collection="attachment_chunks",
                            scalars=_ATTACHMENT_SCALARS)
    rng = random.Random(11)

    def arec(vec: list[float], cid: int, rid: int, text: str) -> dict:
        return {"vector": vec, "text": text, "session_id": cid, "reference_id": rid,
                "char_start": 0, "char_end": len(text), "source_type": "attachment",
                "embed_model_id": "fake"}

    rows = [arec(_noisy(QUERY, 0.01, rng), 1, 10, f"c1-{i}") for i in range(1200)]
    rows += [arec(_noisy(_ORTHO, 0.05, rng), 2, 20, f"c2-{i}") for i in range(59)]
    rows.append(arec(GOLD_VEC, 2, 20, "GOLD"))
    for i in range(0, len(rows), 200):
        store.upsert(rows[i:i + 200])
    store._client.flush("attachment_chunks")
    _wait_index_built(db)

    hits = store.query(QUERY, filter={"session_id": 2, "reference_id": [20]}, k=30)
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

db, dim, idx_glob = sys.argv[1], int(sys.argv[2]), sys.argv[3]
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
    if list(Path(db).glob(idx_glob)):
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
    subprocess.run([sys.executable, "-c", _LEGACY_BUILD_SCRIPT, db, str(DIM), _IDX_GLOB],
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


# ── 索引 sidecar 半写窗口(Codex High,故障注入实锤)────────────────────────────
# 引擎 create_index 先把索引 spec 提交进 manifest,load 时才(非原子地,open+np.save,
# 无 tmp+rename)把索引写成 .idx sidecar —— 进程死在写入中途,就留下"manifest 说索引
# 已建成(describe_index: FLAT/Finished)、文件却只有半个"的状态。describe 只读 manifest
# 看不出来;引擎 load 读损坏文件抛 MilvusException 且不清理该文件(orphan 清理只按数据
# 文件 stem 匹配,截断文件 stem 合法、不会被清)。修复前:每次重开都在 load_collection
# 同一处失败,向量库持续不可用;行数据(parquet)完好。


def _build_small_store(db: str) -> None:
    """40 行 project 1 + 10 行 project 2(含 GOLD),索引落盘后冷关(真重启语义)。"""
    store = MilvusLiteStore(db_path=db, dim=DIM)
    rng = random.Random(5)
    rows = [_rec(_noisy(QUERY, 0.01, rng), 1, 1, f"p1-{i}") for i in range(40)]
    rows += [_rec(_noisy(_ORTHO, 0.05, rng), 2, 2, f"p2-{i}") for i in range(9)]
    rows.append(_rec(GOLD_VEC, 3, 2, "GOLD"))
    store.upsert(rows)
    store._client.flush("chunks")
    _wait_index_built(db)
    store.close()
    _restart_engine(db)


def test_reopen_with_truncated_sidecar_self_heals_once_then_noop(tmp_path, caplog):
    """spec 已提交 + sidecar 截断一半(模拟死在索引落盘中途):重开一次即自愈
    (load 失败兜底:drop 索引连带清掉损坏文件 → 重建 → 重新 load),数据一行不丢;
    再重开是干净 no-op(不再走兜底、不再报 warning)。修复前此场景每次重开都抛
    MilvusException('file seems not fully written')、永不自愈。"""
    db = str(tmp_path / "v.db")
    _build_small_store(db)
    files = _sidecar_index_files(db)
    assert files, "前置不成立:索引 sidecar 未落盘"
    for p in files:
        with open(p, "r+b") as f:
            f.truncate(p.stat().st_size // 2)   # 半写:只留前一半字节

    with caplog.at_level("WARNING", logger="epictrace"):
        store = MilvusLiteStore(db_path=db, dim=DIM)   # 修复前:这里就炸
    assert any("load 失败" in r.message for r in caplog.records)   # 自愈要留痕
    hits = store.query(QUERY, filter={"project_id": 2}, k=10)
    assert len(hits) == 10
    assert hits[0]["text"] == "GOLD"
    assert len(store.list_by_project(1)) == 40   # 行数据完好,只重建了派生索引
    store.close()
    _restart_engine(db)

    caplog.clear()
    with caplog.at_level("WARNING", logger="epictrace"):
        store = MilvusLiteStore(db_path=db, dim=DIM)
    assert not [r for r in caplog.records if r.name == "epictrace"]   # 二开 no-op
    assert len(store.query(QUERY, filter={"project_id": 2}, k=10)) == 10
    store.close()


def test_reopen_with_missing_sidecar_rebuilds(tmp_path):
    """spec 已提交 + sidecar 整个缺失(死在 manifest 提交后、文件创建前):重开必须直接
    可用且 sidecar 重新落盘。当前引擎对"文件不存在"本就走重建分支(故障注入实证);
    若未来引擎把它变成硬错误,则由我们的 load 失败兜底接住 —— 两层谁接都行,锁的是
    "重开一次即可用"这个行为。"""
    db = str(tmp_path / "v.db")
    _build_small_store(db)
    files = _sidecar_index_files(db)
    assert files, "前置不成立:索引 sidecar 未落盘"
    for p in files:
        p.unlink()

    store = MilvusLiteStore(db_path=db, dim=DIM)
    hits = store.query(QUERY, filter={"project_id": 2}, k=10)
    assert len(hits) == 10
    assert hits[0]["text"] == "GOLD"
    assert _sidecar_index_files(db), "sidecar 未重建落盘"
    store.close()
