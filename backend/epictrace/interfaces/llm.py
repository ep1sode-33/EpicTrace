from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """OpenAI-compatible 抽象。实现留给后续 Plan(DeepSeek 等)。"""

    @abstractmethod
    def complete(self, messages: list[dict], **kwargs) -> str: ...
