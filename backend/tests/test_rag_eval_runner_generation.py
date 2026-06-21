"""端到端注入:假 chat_model + 假 llm + 假 judge + 假 retriever,证明 ②③ 全链路通,不碰真模型。"""
from collections import namedtuple

from scripts.rag_eval.config import EvalConfig
from scripts.rag_eval.golden import GoldItem, GoldSpan
from scripts.rag_eval.runner_generation import run_generation

C = namedtuple("C", "text ingest_record_id project_id char_start char_end source_type score source_kind reference_id")


def _chunk(rid, a, b, text="t"):
    return C(text, rid, 1, a, b, "folder_scan", 1.0, "project", None)


class _FakeJudge:
    def judge_json(self, system, user):
        return {"claims": [{"text": "c", "supported": True}], "relevancy": 1.0,
                "answer_claims_supported": [True], "reference_claims_covered": [True],
                "is_refusal": True, "citations": [{"supported": True}]}


def test_run_generation_smoke(tmp_path, monkeypatch):
    import scripts.rag_eval.runner_generation as rg

    # 桩掉真 agent 原语:run_react_loop 往 accumulator 塞一个命中 gold 的 chunk;
    # stream_final_answer 吐一个带 [1] 引用的答案。
    def fake_loop(chat_model, tools, accumulator, question, **kw):
        accumulator.chunks.append(_chunk(1, 0, 50))
        return "ok"

    def fake_stream(llm, question, pool, **kw):
        yield {"event": "token", "data": "答案 [1]"}
        yield {"event": "_answer", "data": "答案 [1]"}

    monkeypatch.setattr(rg, "run_react_loop", fake_loop)
    monkeypatch.setattr(rg, "stream_final_answer", fake_stream)
    monkeypatch.setattr(rg, "build_tools", lambda **k: [])

    class _Acc:
        def __init__(self): self.chunks = []
    monkeypatch.setattr(rg, "ChunkAccumulator", _Acc)

    golden = [GoldItem("g1", "q1", (GoldSpan(1, 0, 50),), "ref",
                       {"lang": "zh", "q_type": "single_hop"}, "synthetic", "own", "v1")]
    res = run_generation(golden, build_chat_model=lambda: object(), llm=object(),
                         retriever=object(), judge=_FakeJudge(), cache=None,
                         project_id=1, config=EvalConfig(k=6, k_values=(5,)))
    m = res["per_question"][0]["metrics"]
    assert m["agent_recall_any@5"] == 1.0       # pool 命中 gold(②)
    assert m["citation_accuracy"] == 1.0        # [1] 指向命中 chunk
    assert m["faithfulness"] == 1.0             # judge
    assert "refusal_correctness" not in m       # single_hop 不算 refusal
    assert res["overall"]["faithfulness"] == 1.0
