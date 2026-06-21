from collections import namedtuple

from scripts.rag_eval.config import EvalConfig
from scripts.rag_eval.golden import GoldItem, GoldSpan
from scripts.rag_eval.runner import run_retrieve, write_run

C = namedtuple("C", "ingest_record_id char_start char_end")


class _FakeRetriever:
    """据问题返回固定排序结果(不碰真模型/Milvus)。"""
    def __init__(self, mapping):
        self._m = mapping

    def retrieve(self, *, project_id, query, k, dense_n, fuse_m):
        return self._m.get(query, [])[:k]


def _golden():
    return [
        GoldItem("g1", "q-hit-top1", (GoldSpan(1, 0, 10),), "", {"q_type": "single_hop"}, "hand", "own", "v1"),
        GoldItem("g2", "q-miss", (GoldSpan(5, 0, 10),), "", {"q_type": "single_hop"}, "hand", "own", "v1"),
    ]


def test_run_retrieve_aggregates(tmp_path):
    retr = _FakeRetriever({
        "q-hit-top1": [C(1, 0, 5), C(9, 0, 1)],
        "q-miss": [C(8, 0, 1), C(7, 0, 1)],
    })
    cfg = EvalConfig(k=6, k_values=(1, 3))
    res = run_retrieve(_golden(), retr, project_id=42, config=cfg)
    assert res["n"] == 2
    assert res["config_hash"] == cfg.config_hash()
    # overall recall_any@1 = (1 命中 + 0) / 2 = 0.5
    assert res["overall"]["recall_any@1"] == 0.5
    # 分片存在 single_hop。
    assert "q_type=single_hop" in res["by_slice"]

    path = write_run(res, tmp_path / "runs")
    assert (path / "summary.json").is_file()
    assert (path / "per_question.jsonl").is_file()
    assert (path / "config.json").is_file()
