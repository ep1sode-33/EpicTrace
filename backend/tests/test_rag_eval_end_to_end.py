"""端到端串起 Task 1-8(注入假检索器),证明 golden → run → report 全链路通。"""
from collections import namedtuple

from scripts.rag_eval.config import EvalConfig
from scripts.rag_eval.golden import GoldItem, GoldSpan
from scripts.rag_eval.report import format_report
from scripts.rag_eval.runner import run_retrieve, write_run

C = namedtuple("C", "ingest_record_id char_start char_end")


class _FakeRetriever:
    def retrieve(self, *, project_id, query, k, dense_n, fuse_m):
        # q1 命中其 gold(record 1),q2 不命中。
        return {"q1": [C(1, 0, 5)], "q2": [C(9, 0, 1)]}.get(query, [])[:k]


def test_golden_to_run_to_report(tmp_path):
    golden = [
        GoldItem("g1", "q1", (GoldSpan(1, 0, 10),), "", {"lang": "zh", "q_type": "single_hop"}, "hand", "own", "v1"),
        GoldItem("g2", "q2", (GoldSpan(2, 0, 10),), "", {"lang": "en", "q_type": "single_hop"}, "hand", "own", "v1"),
    ]
    res = run_retrieve(golden, _FakeRetriever(), project_id=1, config=EvalConfig(k=6, k_values=(1, 5)))
    assert res["overall"]["recall_any@5"] == 0.5
    out = write_run(res, tmp_path / "runs")
    report = format_report({k: res[k] for k in ("config_hash", "n", "by_slice", "overall")},
                           metrics=["recall_any@5", "mrr"])
    assert "lang=zh" in report and "lang=en" in report
    assert out.is_dir()
