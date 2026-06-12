from epictrace.agent.chat_model import make_chat_model


def test_make_chat_model_builds_chatopenai_from_profile():
    profile = {"base_url": "https://api.deepseek.com", "api_key": "k", "model": "deepseek-chat"}
    model = make_chat_model(profile)
    # Constructed lazily without a network call; just assert it's the ChatOpenAI surface.
    assert model.__class__.__name__ == "ChatOpenAI"
    assert hasattr(model, "bind_tools")


def test_make_chat_model_normalizes_chat_completions_suffix():
    profile = {"base_url": "https://api.deepseek.com/chat/completions",
               "api_key": "k", "model": "deepseek-chat"}
    model = make_chat_model(profile)
    assert str(model.openai_api_base).rstrip("/").endswith("api.deepseek.com")
