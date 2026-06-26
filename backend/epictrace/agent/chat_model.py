from __future__ import annotations

from epictrace.llm.openai_compat import _normalize_base_url

_CLS = None  # 缓存懒构造的子类(避免每次工厂调用重定义)


def _reasoning_chat_openai():
    """懒构造 ChatOpenAI 子类:把 DeepSeek 的 delta.reasoning_content 捞进
    AIMessageChunk.additional_kwargs —— 基类按其文档**明确不提取** reasoning_content,
    并建议「use a provider-specific subclass」。这样 agent 决策时的推理过程也能被流式取出
    (透明化它「在想搜什么」的那段空窗),而 langgraph 编排 / ToolNode / tool_calls 全不变。"""
    global _CLS
    if _CLS is not None:
        return _CLS
    from langchain_openai import ChatOpenAI

    class ReasoningChatOpenAI(ChatOpenAI):
        def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
            gen = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
            if gen is not None:
                try:
                    delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                    rc = delta.get("reasoning_content") or delta.get("reasoning")  # 部分端点用 reasoning
                    if rc:
                        gen.message.additional_kwargs["reasoning_content"] = rc
                except Exception:  # noqa: BLE001 — 提取推理失败绝不影响主流(正文/工具调用)
                    pass
            return gen

    _CLS = ReasoningChatOpenAI
    return _CLS


def make_chat_model(profile: dict, *, temperature: float = 0.0):
    """构造接 OpenAI 兼容端点的 ChatOpenAI(agent 路工具调用专用)。

    用 ReasoningChatOpenAI 子类,使流式可拿到 reasoning_content(透明对话「思考过程」);
    复用 OpenAICompatLLM 的 base_url 归一化(剥掉误粘的 /chat/completions);
    允许空 api_key(本地 Ollama)。延迟 import,避免无 langchain-openai 时全局崩。"""
    cls = _reasoning_chat_openai()
    return cls(
        base_url=_normalize_base_url(profile.get("base_url", "")),
        api_key=profile.get("api_key") or "not-set",
        model=profile.get("model", ""),
        temperature=temperature,
    )
