def test_langchain_openai_importable():
    from langchain_openai import ChatOpenAI  # noqa: F401

    # bind_tools is the exact surface Plan 6 relies on.
    assert hasattr(ChatOpenAI, "bind_tools")
