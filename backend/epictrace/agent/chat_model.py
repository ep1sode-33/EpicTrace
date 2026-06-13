from __future__ import annotations

from epictrace.llm.openai_compat import _normalize_base_url


def make_chat_model(profile: dict, *, temperature: float = 0.0):
    """构造接 OpenAI 兼容端点的 ChatOpenAI(agent 路工具调用专用)。

    复用 OpenAICompatLLM 的 base_url 归一化(剥掉误粘的 /chat/completions);
    允许空 api_key(本地 Ollama)。延迟 import,避免无 langchain-openai 时全局崩。"""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=_normalize_base_url(profile.get("base_url", "")),
        api_key=profile.get("api_key") or "not-set",
        model=profile.get("model", ""),
        temperature=temperature,
    )
