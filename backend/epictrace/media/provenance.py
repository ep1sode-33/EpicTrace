from __future__ import annotations

import json
from pathlib import Path


def write_provenance(data_dir: Path, kind: str, item_id: int, content_list: list) -> Path:
    """落 content_list sidecar 到 <data_dir>/provenance/<kind>-<id>.json。

    派生缓存(可由重跑 MinerU 重建),不进核心 SQL 事实表。
    kind 为 'ingest' 或 'reference'。
    """
    out_dir = Path(data_dir) / "provenance"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{kind}-{item_id}.json"
    out_path.write_text(
        json.dumps(content_list, ensure_ascii=False), encoding="utf-8"
    )
    return out_path
