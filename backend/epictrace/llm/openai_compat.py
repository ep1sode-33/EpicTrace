from __future__ import annotations

from collections.abc import Iterator

from openai import OpenAI

from epictrace.interfaces.llm import LLMProvider


class OpenAICompatLLM(LLMProvider):
    """任意 OpenAI-Compatible 端点(DeepSeek/OpenAI/Ollama/vLLM…)。"""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._model = model
        # 空 key 也允许构造(本地 Ollama 等无需 key);真正调用时才需要有效配置。
        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-set")

    def complete(self, messages: list[dict], **kwargs) -> str:
        resp = self._client.chat.completions.create(
            model=self._model, messages=messages, stream=False, **kwargs
        )
        return resp.choices[0].message.content or ""

    def stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=self._model, messages=messages, stream=True, **kwargs
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                yield delta.content
