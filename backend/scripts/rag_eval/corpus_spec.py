"""读本地(gitignored)corpus_spec.json:[{glob, slices, source}] → CorpusEntry 列表(展开 glob)。"""
from __future__ import annotations

import json
from glob import glob
from pathlib import Path

from scripts.rag_eval.corpus import CorpusEntry


def load_entries(spec_path: str | Path) -> list[CorpusEntry]:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    entries: list[CorpusEntry] = []
    for grp in spec:
        for hit in sorted(glob(grp["glob"], recursive=True)):
            p = Path(hit)
            if p.is_file():
                entries.append(CorpusEntry(src=p, slices=grp.get("slices", {}),
                                           source=grp.get("source", "own")))
    return entries
