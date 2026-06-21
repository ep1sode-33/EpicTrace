from scripts.rag_eval.golden import GoldItem, GoldSpan, load_golden
from scripts.rag_eval.review import review_candidates


def _cand(i):
    return GoldItem(f"g{i}", f"q{i}", (GoldSpan(1, 0, 10),), "ref", {"lang": "zh"}, "synthetic", "own", "v1")


def test_accept_reject_then_quit(tmp_path):
    cands = [_cand(1), _cand(2), _cand(3), _cand(4)]
    decisions = iter(["a", "r", "q"])   # accept g1, reject g2, quit before g3
    out = tmp_path / "golden.jsonl"
    kept = review_candidates(cands, prompt_fn=lambda it: next(decisions), out_path=out)
    assert [k.id for k in kept] == ["g1"]
    assert [k.id for k in load_golden(out)] == ["g1"]
