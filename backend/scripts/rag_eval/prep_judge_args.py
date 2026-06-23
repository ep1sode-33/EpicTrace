"""把 gen_dump 拆成每题小文件(供判官子代理 Read)+ 产出极小的 workflow args
({dir, items:[{id,q_type,n_cited}]})。context 等大字段只落每题文件,不进 args / 主上下文。
用法: ./.venv/bin/python -m scripts.rag_eval.prep_judge_args
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def prep(gen_path: str = "scripts/rag_eval/runs/gen_dump_v65.json",
         items_dir: str = "scripts/rag_eval/runs/gen_items",
         args_out: str = "scripts/rag_eval/runs/judge_args.json") -> str:
    gen = json.loads(Path(gen_path).read_text(encoding="utf-8"))
    d = Path(items_dir)
    d.mkdir(parents=True, exist_ok=True)
    items = []
    for g in gen:
        (d / f"{g['id']}.json").write_text(json.dumps({
            "question": g["question"], "reference_answer": g["reference_answer"],
            "answer": g["answer"], "context": g["context"], "cited_texts": g["cited_texts"],
        }, ensure_ascii=False), encoding="utf-8")
        items.append({"id": g["id"], "q_type": g["slices"].get("q_type"),
                      "n_cited": len(g["cited_texts"])})
    args = {"dir": str(d.resolve()), "items": items}
    Path(args_out).write_text(json.dumps(args, ensure_ascii=False), encoding="utf-8")
    print(f"[prep] {len(items)} 题文件 → {d.resolve()}; args({len(json.dumps(args))}B) → {args_out}")
    return args_out


if __name__ == "__main__":
    prep()
