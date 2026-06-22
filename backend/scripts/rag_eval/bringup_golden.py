"""Bring-up(手动跑,无单测):从已索引的 eval Project 合成候选 golden。

流程:list_by_project 取 chunk → 按 manifest 给每个 chunk 打 slice 标签 → synth_item 让
**Claude(off-family,非被测 DeepSeek)**出题+参考答案+支撑句 → 支撑句在 chunk 内的局部偏移
**按 chunk.char_start 平移成文档偏移**(对齐 chunk_hits 的命中判定)→ 写候选 golden.jsonl。

用法:
    cd backend
    ./.venv/bin/python -m scripts.rag_eval.bringup_golden --project-id 2 \
        --eval-data eval-data --out /tmp/golden_candidates.jsonl [--limit N]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import IngestRecord
from epictrace.vectorstore.milvus_lite import MilvusLiteStore
from scripts.rag_eval.corpus import load_manifest
from scripts.rag_eval.golden import GoldItem, GoldSpan, save_golden
from scripts.rag_eval.synth import synth_item
from scripts.rag_eval.wiring import build_judge


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bringup_golden")
    p.add_argument("--project-id", type=int, required=True)
    p.add_argument("--eval-data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--limit", type=int, default=0, help="只处理前 N 个 chunk(0=全部),试水用")
    ns = p.parse_args(argv if argv is not None else sys.argv[1:])

    cfg = AppConfig()
    # manifest:rel_path(=入库后文件名)→ slices
    manifest = load_manifest(Path(ns.eval_data) / "manifest.jsonl")
    slices_by_name = {m.rel_path: m.slices for m in manifest}
    # ingest_record_id → 文件名(入库时把 eval-data/<rel_path> 拷进项目目录,basename 不变)
    db = Database(cfg)
    with db.session() as s:
        recs = s.execute(select(IngestRecord).where(
            IngestRecord.project_id == ns.project_id)).scalars().all()
        name_by_rid = {r.id: Path(r.stored_path).name for r in recs}

    store = MilvusLiteStore(db_path=cfg.milvus_path)
    gen = build_judge()  # Claude Opus,off-family,不让被测 DeepSeek 自己出题
    chunks = store.list_by_project(ns.project_id)
    if ns.limit:
        chunks = chunks[:ns.limit]

    cands: list[GoldItem] = []
    for i, ch in enumerate(chunks):
        rid = ch["ingest_record_id"]
        sl = dict(slices_by_name.get(name_by_rid.get(rid, ""), {}))
        sl["q_type"] = "single_hop"
        item = synth_item(gen, item_id=f"g{i:04d}", ingest_record_id=rid,
                          doc_text=ch["text"], chunk_text=ch["text"], slices=sl,
                          corpus_version="v1")
        if item is None:
            print(f"  skip chunk {i} (rid {rid}): 被过滤/未定位", file=sys.stderr)
            continue
        # 局部偏移 → 文档偏移(平移 chunk.char_start)
        shift = int(ch["char_start"])
        sp = item.gold_spans[0]
        cands.append(GoldItem(
            id=item.id, question=item.question,
            gold_spans=(GoldSpan(rid, sp.doc_char_start + shift, sp.doc_char_end + shift),),
            reference_answer=item.reference_answer, slices=item.slices,
            provenance=item.provenance, source=item.source, corpus_version=item.corpus_version))
        print(f"  ok chunk {i} (rid {rid}, {sl.get('lang')}/{sl.get('doc_type')}): "
              f"{item.question[:48]}", file=sys.stderr)

    save_golden(cands, ns.out)
    print(f"[bringup] {len(cands)}/{len(chunks)} 候选 → {ns.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
