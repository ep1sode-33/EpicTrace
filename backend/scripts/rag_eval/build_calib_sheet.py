"""#140 judge 校准:从 v64 run 抽样 + faithfulness 构造负例,生成 William 的打标表(md)+
隐藏 key(判官分 / 构造负例 ground-truth)。William 独立标(不看判官分)→ 再算 kappa(judge vs 人工)。

round1(默认):correctness + citation + faithfulness(16:8 正 8 构造负)。
round2(--round2):只重出 citation + faithfulness 正例,且**不截断**(round1 截断 context/引用片段,
让 William 看到的信息比判官少 → faithfulness/citation 假分歧;round2 给全文修正)。
用法: ./.venv/bin/python -m scripts.rag_eval.build_calib_sheet [--round2]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

R = Path("scripts/rag_eval/runs")
_FAITH_POS = ["g0011", "g0014", "g0016", "g0026", "g0028", "g0029", "g0030", "g0034"]


def _load():
    gen = {g["id"]: g for g in json.loads((R / "gen_dump_v64.json").read_text(encoding="utf-8"))}
    jud = {r["id"]: r["judge"] for r in json.loads((R / "judge_scores_v64.json").read_text(encoding="utf-8")) if r["judge"]}
    negs = json.loads((R / "faith_negatives.json").read_text(encoding="utf-8")) if (R / "faith_negatives.json").is_file() else []
    return gen, jud, negs


def _citation_section(gen, jud, key, out):
    cf_neg = [i for i in gen if jud.get(i, {}).get("citation_faithfulness") not in (None, 1.0)][:6]
    cf_pos = [i for i in gen if jud.get(i, {}).get("citation_faithfulness") == 1.0 and gen[i]["cited_texts"]][:5]
    out.append("## citation_faithfulness(看**被引片段**是否真支撑答案中引用它的论述;有不支撑的在 [ ] 打 x)— 全文未截断\n")
    for k, i in enumerate(cf_neg + cf_pos, 1):
        g = gen[i]
        blocks = "\n".join(f"  [{n+1}] {t}" for n, t in enumerate(g["cited_texts"]))   # 全文
        out.append(f"### CF{k}\n**答案**: {g['answer']}\n**被引片段**:\n{blocks}\n- [ ] 我判:**有引用不支撑**\n")
        key.append({"tag": f"CF{k}", "id": i, "metric": "citation_faithfulness", "judge": jud[i]["citation_faithfulness"]})


def build_round2():
    """只重出 citation(11)+ faithfulness 正例(8),全文不截断 → calib_sheet2.md / calib_key2.json。
    faithfulness 负例(8)沿用 round1 的人工标(截断免疫)。"""
    gen, jud, _ = _load()
    key, out = [], ["# 判官校准打标表 round2(全文未截断;请独立判,先别看判官分)\n"]
    _citation_section(gen, jud, key, out)
    out.append("\n## faithfulness 正例(判**答案**每句是否都能由**上下文**支撑;有无据捏造在 [ ] 打 x)— 全文 context\n")
    for k, i in enumerate(_FAITH_POS, 1):
        g = gen[i]
        out.append(f"### FP{k}\n**问题**: {g['question']}\n**答案**: {g['answer']}\n**上下文(全文)**:\n{g['context']}\n\n- [ ] 我判:**不忠实(有无据声明)**\n")
        key.append({"tag": f"FP{k}", "id": i, "metric": "faithfulness", "judge": jud[i]["faithfulness"], "gt_unfaithful": False})
    # 把 round1 的 8 个 faith 负例(William 已正确判 8/8,截断免疫)烤进 key2(预填 human,round2 不必重判)
    from scripts.rag_eval.calib_score import parse_sheet
    r1h = parse_sheet("eval-data/calib_sheet.md")
    for k1 in json.loads((R / "calib_key.json").read_text(encoding="utf-8")):
        if k1["metric"] == "faithfulness" and k1.get("gt_unfaithful"):
            key.append({"tag": k1["tag"] + "_r1", "id": k1["id"], "metric": "faithfulness",
                        "judge": k1["judge"], "gt_unfaithful": True, "human": r1h.get(k1["tag"])})
    Path("eval-data/calib_sheet2.md").write_text("\n".join(out), encoding="utf-8")
    (R / "calib_key2.json").write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[calib round2] → eval-data/calib_sheet2.md  ({len([k for k in key if k['metric']=='citation_faithfulness'])} cite + {len(_FAITH_POS)} faith正例,全文未截断)")


def build():
    """round1(保留):correctness + citation + faithfulness(8 正 + 8 构造负)。"""
    gen, jud, negs = _load()
    key, out = [], ["# 判官校准打标表（请独立判，先别看判官分）\n"]
    by = sorted(gen, key=lambda i: jud.get(i, {}).get("answer_correctness", 0))
    low = [i for i in by if jud.get(i, {}).get("answer_correctness", 1) < 0.5][:5]
    mid = [i for i in by if 0.5 <= jud.get(i, {}).get("answer_correctness", 1) < 1.0][:5]
    hi = [i for i in by if jud.get(i, {}).get("answer_correctness") == 1.0][:5]
    out.append("## answer_correctness(对照参考答案,判模型答案是否**正确/达标**;不达标在 [ ] 打 x)\n")
    for k, i in enumerate(low + mid + hi, 1):
        g = gen[i]
        out.append(f"### C{k}\n**问题**: {g['question']}\n**参考答案**: {g['reference_answer']}\n**模型答案**: {g['answer']}\n- [ ] 我判:**错/不达标**\n")
        key.append({"tag": f"C{k}", "id": i, "metric": "answer_correctness", "judge": jud[i]["answer_correctness"]})
    _citation_section(gen, jud, key, out)
    pos = [{"id": i, "answer": gen[i]["answer"], "context": gen[i]["context"], "question": gen[i]["question"], "gt": False} for i in [n["id"] for n in negs]]
    neg = [{"id": n["id"] + "_neg", "answer": n["modified_answer"], "context": gen[n["id"]]["context"], "question": gen[n["id"]]["question"], "gt": True, "judge": n.get("judge_faith")} for n in negs]
    items = [x for pair in zip(pos, neg) for x in pair]
    out.append("\n## faithfulness(判答案每句是否都能由上下文支撑;不忠实在 [ ] 打 x)\n")
    for k, it in enumerate(items, 1):
        out.append(f"### F{k}\n**问题**: {it['question']}\n**答案**: {it['answer']}\n**上下文(截断)**: {it['context'][:700]}\n- [ ] 我判:**不忠实(有无据声明)**\n")
        jf = it.get("judge") if it["gt"] else jud.get(it["id"], {}).get("faithfulness")
        key.append({"tag": f"F{k}", "id": it["id"], "metric": "faithfulness", "judge": jf, "gt_unfaithful": it["gt"]})
    Path("eval-data/calib_sheet.md").write_text("\n".join(out), encoding="utf-8")
    (R / "calib_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[calib] → eval-data/calib_sheet.md ({len(key)} 项)")


if __name__ == "__main__":
    build_round2() if "--round2" in sys.argv else build()
