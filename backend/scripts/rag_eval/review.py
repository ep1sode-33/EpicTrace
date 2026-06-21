"""人工精修:逐题 accept(a)/reject(r)/quit(q)。prompt_fn injectable(CLI 用 input,测试脚本化)。"""
from __future__ import annotations

from pathlib import Path

from scripts.rag_eval.golden import GoldItem, save_golden


def review_candidates(candidates: list[GoldItem], *, prompt_fn, out_path: str | Path) -> list[GoldItem]:
    kept: list[GoldItem] = []
    for it in candidates:
        choice = (prompt_fn(it) or "").strip().lower()
        if choice == "q":
            break
        if choice == "a":
            kept.append(it)
        # 其它(含 "r")= 跳过
    save_golden(kept, out_path)
    return kept


def stdin_prompt(it: GoldItem) -> str:
    print(f"\n[{it.id}] {it.question}\n  参考: {it.reference_answer}\n  slices: {it.slices}")
    return input("  accept(a)/reject(r)/quit(q)? ")
