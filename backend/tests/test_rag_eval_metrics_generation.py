import math

from scripts.rag_eval.metrics_generation import score_answer_relevancy, score_faithfulness


class _FakeJudge:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def judge_json(self, system, user):
        self.calls.append((system, user))
        return self._reply


def test_faithfulness_fraction():
    j = _FakeJudge({"claims": [{"text": "a", "supported": True},
                               {"text": "b", "supported": False},
                               {"text": "c", "supported": True}]})
    assert math.isclose(score_faithfulness(j, answer="...", context="..."), 2 / 3, rel_tol=1e-9)
    assert "上下文" in j.calls[0][1] or "context" in j.calls[0][1].lower()


def test_faithfulness_nan_paths():
    assert math.isnan(score_faithfulness(_FakeJudge(None), answer="x", context="y"))
    assert math.isnan(score_faithfulness(_FakeJudge({"claims": []}), answer="x", context="y"))


def test_relevancy_clamped():
    assert score_answer_relevancy(_FakeJudge({"relevancy": 0.9}), question="q", answer="a") == 0.9
    assert score_answer_relevancy(_FakeJudge({"relevancy": 1.5}), question="q", answer="a") == 1.0
    assert math.isnan(score_answer_relevancy(_FakeJudge(None), question="q", answer="a"))
