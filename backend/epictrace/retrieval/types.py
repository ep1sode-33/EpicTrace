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
    capture_session_id: int | None = None  # 会话溯源:来自采集会话的 chunk 才有
    ts: str | None = None                  # 会话内时刻(naive-UTC ISO),供前端跳回

    @classmethod
    def from_row(cls, row: dict, score: float = 0.0) -> "RetrievedChunk":
        return cls(
            text=row["text"], ingest_record_id=row["ingest_record_id"], project_id=row["project_id"],
            char_start=row["char_start"], char_end=row["char_end"],
            source_type=row.get("source_type", "folder_scan"), score=score,
            # 哨兵 0 / "" 归一为 None;附件行缺这两键 → get 返回 None(同样安全)
            capture_session_id=row.get("capture_session_id") or None,
            ts=row.get("ts") or None,
        )

    def key(self) -> tuple:
        # 含 reference_id:附件 chunk 的 ingest_record_id 恒为 0,不同引用的同偏移块(尤其每文件
        # 首块都 char_start=0)否则会在 RRF 去重时撞键、互相吞掉。项目 chunk 的 reference_id 为 None,
        # 行为不变。
        return (self.ingest_record_id, self.reference_id, self.char_start, self.char_end)
