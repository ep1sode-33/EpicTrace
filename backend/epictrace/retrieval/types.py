from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievedChunk:
    text: str
    ingest_record_id: int
    project_id: int
    char_start: int
    char_end: int
    source_type: str
    score: float = 0.0
    source_kind: str = "project"          # project | attachment
    reference_id: int | None = None

    @classmethod
    def from_row(cls, row: dict, score: float = 0.0) -> "RetrievedChunk":
        return cls(
            text=row["text"], ingest_record_id=row["ingest_record_id"], project_id=row["project_id"],
            char_start=row["char_start"], char_end=row["char_end"],
            source_type=row.get("source_type", "folder_scan"), score=score,
        )

    def key(self) -> tuple:
        # 含 reference_id:附件 chunk 的 ingest_record_id 恒为 0,不同引用的同偏移块(尤其每文件
        # 首块都 char_start=0)否则会在 RRF 去重时撞键、互相吞掉。项目 chunk 的 reference_id 为 None,
        # 行为不变。
        return (self.ingest_record_id, self.reference_id, self.char_start, self.char_end)
