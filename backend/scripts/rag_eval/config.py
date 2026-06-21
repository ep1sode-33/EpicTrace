"""一次 run 的所有旋钮。config_hash 用于归档 run;chunker_hash 用于索引快照(改切块才重建索引)。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EvalConfig:
    k: int = 6                      # rerank 后最终 top_k
    dense_n: int = 30               # dense/sparse 各自召回数
    fuse_m: int = 20                # RRF 融合后保留数
    rrf_k0: int = 60                # RRF 常数(记录用;HybridRetriever 当前内部固定 60)
    sparse_enabled: bool = True
    chunker_target: int = 1800
    chunker_overlap: int = 200
    k_values: tuple[int, ...] = (1, 3, 5, 6)   # @k 指标要算哪些 k
    label: str = ""                 # 人读标签,不进 hash

    def _hash(self, payload: dict) -> str:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def config_hash(self) -> str:
        d = asdict(self)
        d.pop("label", None)
        d["k_values"] = list(self.k_values)
        return self._hash(d)

    def chunker_hash(self) -> str:
        return self._hash({"t": self.chunker_target, "o": self.chunker_overlap})
