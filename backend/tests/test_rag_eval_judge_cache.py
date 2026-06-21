from scripts.rag_eval.judge_cache import JudgeCache, cache_key


def test_cache_key_stable_and_sensitive():
    a = cache_key("faithfulness", "g1", "ans", "ctx", "claude-opus-4-8")
    b = cache_key("faithfulness", "g1", "ans", "ctx", "claude-opus-4-8")
    assert a == b
    assert a != cache_key("faithfulness", "g1", "ans2", "ctx", "claude-opus-4-8")


def test_put_get_persists(tmp_path):
    p = tmp_path / "judge_cache.jsonl"
    c = JudgeCache(p)
    assert c.get("k1") is None
    c.put("k1", {"score": 0.8})
    assert c.get("k1") == {"score": 0.8}
    # 新实例从磁盘恢复。
    assert JudgeCache(p).get("k1") == {"score": 0.8}
