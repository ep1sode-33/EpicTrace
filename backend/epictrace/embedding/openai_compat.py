"""远程 Embedding provider(需求 8):OpenAI 兼容 /v1/embeddings。

不限 OpenAI 本身——DeepSeek、硅基流动等任何 OpenAI 兼容端点均可。
实现 EmbeddingProvider 契约,与本地 BGE-M3 在设置中切换(见 services/settings
embedding 段;切换后维度若变,Milvus schema 自愈会重建索引——见 deps.get_vector_store)。
"""

from __future__ import annotations

import logging

from epictrace.interfaces.embedding import EmbeddingProvider

log = logging.getLogger("epictrace.embedding")

# 单次 embeddings 请求的文本批大小(各兼容端点对 batch 上限不一,保守取值)
_BATCH = 64


class OpenAICompatEmbedder(EmbeddingProvider):
    def __init__(self, *, base_url: str, api_key: str, model: str,
                 dimensions: int | None = None, timeout: float = 60.0) -> None:
        if not base_url.strip():
            raise ValueError("remote embedding: base_url 不能为空")
        if not model.strip():
            raise ValueError("remote embedding: model 不能为空")
        self._base_url = base_url.strip()
        self._api_key = api_key
        self._model = model.strip()
        self._dimensions = dimensions
        self._timeout = timeout

    @property
    def model_id(self) -> str:
        return f"openai:{self._model}"

    @property
    def dimensions(self) -> int | None:
        """配置的向量维度(供 vector store 构造);None = 端点默认。"""
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        from epictrace.llm.openai_compat import _normalize_base_url

        client = OpenAI(base_url=_normalize_base_url(self._base_url),
                        api_key=self._api_key or "none", timeout=self._timeout)
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            batch = texts[i: i + _BATCH]
            kwargs: dict = {"model": self._model, "input": batch}
            if self._dimensions:
                kwargs["dimensions"] = self._dimensions
            resp = client.embeddings.create(**kwargs)
            # OpenAI 契约按 index 排序返回;显式按 index 对齐,防兼容端点乱序
            out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        return out
