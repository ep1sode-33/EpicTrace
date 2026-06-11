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


def test_base_url_strips_trailing_chat_completions():
    # 用户把整段端点(含 /chat/completions)粘进来也不该双拼(SDK 会自己加 /chat/completions)。
    llm = OpenAICompatLLM(base_url="https://gw.example.com/v1/chat/completions/", api_key="k", model="m")
    assert "chat/completions" not in str(llm._client.base_url)
    assert "/v1" in str(llm._client.base_url)
    # 粘"根"也照常工作。
    llm2 = OpenAICompatLLM(base_url="https://gw.example.com/v1", api_key="k", model="m")
    assert "/v1" in str(llm2._client.base_url)
