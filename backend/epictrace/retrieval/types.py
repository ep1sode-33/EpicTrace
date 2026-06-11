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

    @classmethod
    def from_row(cls, row: dict, score: float = 0.0) -> "RetrievedChunk":
        return cls(
            text=row["text"], ingest_record_id=row["ingest_record_id"], project_id=row["project_id"],
            char_start=row["char_start"], char_end=row["char_end"],
            source_type=row.get("source_type", "folder_scan"), score=score,
        )

    def key(self) -> tuple:
        return (self.ingest_record_id, self.char_start, self.char_end)
