"""Golden 测试集数据模型 + JSONL 读写。gold 跨度记成源文档(抽取文本)的 char 区间,不绑 chunk。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class GoldSpan:
    ingest_record_id: int
    doc_char_start: int
    doc_char_end: int


@dataclass(frozen=True)
class GoldItem:
    id: str
    question: str
    gold_spans: tuple[GoldSpan, ...]
    reference_answer: str
    slices: dict
    provenance: str        # hand | synthetic
    source: str            # own | benchmark:<name> | synthetic-doc
    corpus_version: str


def _item_to_dict(it: GoldItem) -> dict:
    d = asdict(it)
    d["gold_spans"] = [asdict(s) for s in it.gold_spans]
    return d


def _item_from_dict(d: dict) -> GoldItem:
    spans = tuple(GoldSpan(**s) for s in d["gold_spans"])
    return GoldItem(
        id=d["id"], question=d["question"], gold_spans=spans,
        reference_answer=d.get("reference_answer", ""), slices=d.get("slices", {}),
        provenance=d.get("provenance", "hand"), source=d.get("source", "own"),
        corpus_version=d.get("corpus_version", "v1"),
    )


def save_golden(items: list[GoldItem], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(_item_to_dict(it), ensure_ascii=False) + "\n")


def load_golden(path: str | Path) -> list[GoldItem]:
    out: list[GoldItem] = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(_item_from_dict(json.loads(line)))
    return out
