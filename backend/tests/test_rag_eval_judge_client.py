from scripts.rag_eval.judge_client import AnthropicJudge, JudgeConfig, extract_json, load_judge_config


def test_extract_json_strips_fences():
    assert extract_json('```json\n{"supported": true}\n```') == {"supported": True}
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json("not json") is None


def test_load_judge_config_from_keyfile(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_EVAL_JUDGE_KEY", raising=False)
    monkeypatch.delenv("RAG_EVAL_JUDGE_BASE_URL", raising=False)
    kf = tmp_path / "temp_key"
    kf.write_text("KEY=sk-abc123\nBASE_URL=https://api-slb.krill-ai.com\n", encoding="utf-8")
    cfg = load_judge_config(str(kf))
    assert cfg.api_key == "sk-abc123"
    assert cfg.base_url == "https://api-slb.krill-ai.com"
    assert cfg.model == "claude-opus-4-8"


def test_judge_json_parses_messages_response():
    calls = {}

    def fake_transport(url, headers, json_body):
        calls["url"] = url
        calls["headers"] = headers
        return 200, {"content": [{"type": "text", "text": '```json\n{"verdict": "ok"}\n```'}]}

    j = AnthropicJudge(JudgeConfig("https://x", "sk-1", "claude-opus-4-8"), transport=fake_transport)
    out = j.judge_json("你是裁判", "判这个")
    assert out == {"verdict": "ok"}
    assert calls["url"].endswith("/v1/messages")
    assert calls["headers"]["x-api-key"] == "sk-1"
    assert calls["headers"]["anthropic-version"] == "2023-06-01"


def test_judge_json_returns_none_after_retries():
    def boom_transport(url, headers, json_body):
        return 500, {"error": "boom"}

    j = AnthropicJudge(JudgeConfig("https://x", "sk-1", "m"), transport=boom_transport, retries=1)
    assert j.judge_json("s", "u") is None
