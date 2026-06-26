from __future__ import annotations

from collections.abc import Iterator

from openai import OpenAI

from epictrace.interfaces.llm import LLMProvider


def _normalize_base_url(base_url: str) -> str:
    """OpenAI SDK 会自动在 base_url 后追加 /chat/completions。若用户把整段端点(含
    /chat/completions)粘进来,SDK 会拼成 .../chat/completions/chat/completions → 400。
    这里剥掉末尾的 /chat/completions[/],让用户粘"根"或粘"整段端点"都能用。"""
    u = base_url.strip().rstrip("/")
    suffix = "/chat/completions"
    if u.endswith(suffix):
        u = u[: -len(suffix)]
    return u or base_url


class OpenAICompatLLM(LLMProvider):
    """任意 OpenAI-Compatible 端点(DeepSeek/OpenAI/Ollama/vLLM…)。"""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._model = model
        # 空 key 也允许构造(本地 Ollama 等无需 key);真正调用时才需要有效配置。
        self._client = OpenAI(base_url=_normalize_base_url(base_url), api_key=api_key or "not-set")

    def complete(self, messages: list[dict], **kwargs) -> str:
        resp = self._client.chat.completions.create(
            model=self._model, messages=messages, stream=False, **kwargs
        )
        return resp.choices[0].message.content or ""

    def stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        # 仅正文 token(向后兼容:多数调用方只要答案文本)。
        for ev in self.stream_events(messages, **kwargs):
            if ev["type"] == "content":
                yield ev["text"]

    def stream_events(self, messages: list[dict], **kwargs) -> Iterator[dict]:
        """分离推理与正文:逐块 yield {"type": "reasoning"|"content", "text": str}。
        推理(DeepSeek `reasoning_content` / 部分端点 `reasoning`)给前端做「思考过程」折叠块;
        不返回推理的端点只会有 content,行为与 stream() 一致。"""
        stream = self._client.chat.completions.create(
            model=self._model, messages=messages, stream=True, **kwargs
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if rc:
                yield {"type": "reasoning", "text": rc}
            if getattr(delta, "content", None):
                yield {"type": "content", "text": delta.content}
