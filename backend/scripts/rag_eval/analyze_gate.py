"""#144 接地闸门 BEFORE/AFTER 分析:negation 题该转为拒答、single_hop 可答题不该被误拒。
BEFORE=v64;AFTER=带闸门的子集重跑。跑: ./.venv/bin/python -m scripts.rag_eval.analyze_gate"""
import json
from pathlib import Path

from epictrace.agent.answer import _REFUSAL
from scripts.rag_eval.golden import load_golden

R = Path("scripts/rag_eval/runs")
_REFUSE_WORDS = ["无法", "没有", "未提及", "未找到", "未涉及", "并未给出", "不包含", "没提及", "未给出"]


def _refused(ans: str) -> bool:
    return ans.strip() == _REFUSAL or any(w in ans for w in _REFUSE_WORDS)


def main() -> None:
    before = {d["id"]: d["answer"] for d in json.loads((R / "gen_dump_v64.json").read_text(encoding="utf-8"))}
    after = {d["id"]: d["answer"] for d in json.loads((R / "gen_dump_gate_after.json").read_text(encoding="utf-8"))}
    g = {i.id: i for i in load_golden("eval-data/golden.jsonl")}
    neg = [i for i in g.values() if (i.slices or {}).get("q_type") == "negation"]
    sh = [iid for iid in after if (g[iid].slices or {}).get("q_type") == "single_hop"]

    print("=== negation(应全拒答)===")
    b_ref = a_ref = 0
    for it in neg:
        b, a = before.get(it.id, ""), after.get(it.id, "")
        if it.id not in after:
            continue
        rb, ra = _refused(b), _refused(a)
        b_ref += rb
        a_ref += ra
        flag = "✅修复" if (ra and not rb) else ("持平" if ra == rb else "⚠️回退")
        print(f"  {it.id}: BEFORE={'拒' if rb else '编'} → AFTER={'拒' if ra else '编'} {flag}  | {a[:50]}")
    n = sum(1 for it in neg if it.id in after)
    print(f"  refusal: BEFORE {b_ref}/{n} ({b_ref/n:.0%}) → AFTER {a_ref}/{n} ({a_ref/n:.0%})")

    print("\n=== single_hop(可答题,不该被误拒)===")
    over = 0
    for iid in sh:
        a = after.get(iid, "")
        is_ref = a.strip() == _REFUSAL
        over += is_ref
        print(f"  {iid}: {'⚠️误拒!' if is_ref else '正常答✓'}  | {a[:50]}")
    print(f"  误拒(over-refusal): {over}/{len(sh)} —— 0 才算闸门没伤可答题")


if __name__ == "__main__":
    main()
