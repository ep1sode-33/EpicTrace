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


from scripts.rag_eval.metrics_generation import score_answer_correctness, score_refusal_correctness


def test_correctness_f1():
    j = _FakeJudge({"answer_claims_supported": [True, True, False],     # P = 2/3
                    "reference_claims_covered": [True, False]})          # R = 1/2
    p, r = 2 / 3, 1 / 2
    assert math.isclose(score_answer_correctness(j, question="q", answer="a", reference="ref"),
                        2 * p * r / (p + r), rel_tol=1e-9)
    assert math.isnan(score_answer_correctness(_FakeJudge(None), question="q", answer="a", reference="r"))


def test_refusal():
    assert score_refusal_correctness(_FakeJudge({"is_refusal": True}), question="q", answer="没有提到") == 1.0
    assert score_refusal_correctness(_FakeJudge({"is_refusal": False}), question="q", answer="是 X") == 0.0
    assert math.isnan(score_refusal_correctness(_FakeJudge(None), question="q", answer="a"))
