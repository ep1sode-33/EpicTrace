import json

from scripts.rag_eval import cli


def test_run_subcommand_routes(tmp_path, monkeypatch):
    golden = tmp_path / "g.jsonl"
    golden.write_text(json.dumps({"id": "g1", "question": "q", "gold_spans": [],
                                  "reference_answer": "", "slices": {}, "provenance": "hand",
                                  "source": "own", "corpus_version": "v1"}) + "\n", encoding="utf-8")
    called = {}
    # 桩掉重组件装配 + 真跑,只验证路由 + 产物落盘。
    monkeypatch.setattr(cli, "_RUNS", tmp_path / "runs")
    import scripts.rag_eval.wiring as wiring
    monkeypatch.setattr(wiring, "build_retriever", lambda pid: object())
    monkeypatch.setattr(wiring, "build_judge", lambda: object())
    monkeypatch.setattr(wiring, "build_chat_model_factory", lambda: (lambda: object()))
    monkeypatch.setattr(wiring, "build_llm", lambda: object())

    def fake_run_generation(golden_items, **kw):
        called["n"] = len(golden_items)
        return {"config_hash": "abc", "n": len(golden_items), "per_question": [],
                "by_slice": {}, "overall": {"faithfulness": 1.0}}
    monkeypatch.setattr(cli, "run_generation", fake_run_generation)

    rc = cli.main(["run", "--golden", str(golden), "--project-id", "1"])
    assert rc == 0 and called["n"] == 1
