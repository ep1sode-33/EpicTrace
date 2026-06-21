"""judge 结果磁盘缓存:相同 (metric, qid, answer, context, model) 不重复付费。只缓存成功裁决。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def cache_key(metric: str, question_id: str, answer: str, context: str, judge_model: str) -> str:
    h = hashlib.sha256()
    for part in (metric, question_id, answer, context, judge_model):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class JudgeCache:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem: dict[str, dict] = {}
        if self._path.is_file():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._mem[rec["k"]] = rec["v"]

    def get(self, key: str) -> dict | None:
        return self._mem.get(key)

    def put(self, key: str, value: dict) -> None:
        self._mem[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"k": key, "v": value}, ensure_ascii=False) + "\n")
