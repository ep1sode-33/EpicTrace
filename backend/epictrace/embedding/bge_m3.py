from __future__ import annotations

import threading


class BgeM3Embedder:
    """进程内 BGE-M3(落地 EmbeddingProvider 契约)。懒加载:首次 embed 时下载/加载模型。"""

    _MODEL_ID = "bge-m3"
    _DIM = 1024

    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from FlagEmbedding import BGEM3FlagModel
                    self._model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure()
        out = model.encode(texts, return_dense=True, return_sparse=False, return_colbert_vecs=False)
        dense = out["dense_vecs"]
        return [list(map(float, v)) for v in dense]

    @property
    def model_id(self) -> str:
        return self._MODEL_ID
