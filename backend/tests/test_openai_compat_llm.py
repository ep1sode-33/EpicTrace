from epictrace.llm.openai_compat import OpenAICompatLLM


class _FakeChoice:
    def __init__(self, content): self.message = type("M", (), {"content": content})


class _FakeResp:
    def __init__(self, content): self.choices = [_FakeChoice(content)]


def test_complete_sends_messages_and_returns_content(monkeypatch):
    captured = {}
    llm = OpenAICompatLLM(base_url="http://x", api_key="k", model="m")

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResp("hello")

    monkeypatch.setattr(llm._client.chat.completions, "create", fake_create)
    out = llm.complete([{"role": "user", "content": "hi"}])
    assert out == "hello"
    assert captured["model"] == "m"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["stream"] is False


def test_stream_yields_token_deltas(monkeypatch):
    llm = OpenAICompatLLM(base_url="http://x", api_key="k", model="m")

    def fake_create(**kwargs):
        assert kwargs["stream"] is True
        for piece in ["he", "llo"]:
            delta = type("D", (), {"content": piece})
            yield type("C", (), {"choices": [type("Ch", (), {"delta": delta})]})

    monkeypatch.setattr(llm._client.chat.completions, "create", fake_create)
    assert "".join(llm.stream([{"role": "user", "content": "hi"}])) == "hello"
