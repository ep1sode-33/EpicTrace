from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """实现留给后续 Plan(BGE-M3 本地等)。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def model_id(self) -> str: ...

    def warmup(self) -> None:
        """可选:在首次 embed 前预加载重资源(如模型)。默认 no-op。

        关键用途:在创建 Milvus(gRPC)客户端之前先加载模型,避免
        'gRPC 激活后再 fork 加载模型' 在 macOS 上段错误。"""
        return None
