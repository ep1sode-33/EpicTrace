"""#140 judge 校准:从 v64 run 抽样 + faithfulness 构造负例,生成 William 的打标表(md)+
隐藏 key(判官分 / 构造负例 ground-truth)。William 独立标(不看判官分)→ 再算 kappa(judge vs 人工)。

correctness/citation 用真题(自然分布够);faithfulness 系统几乎全 1.0 → 8 真正例 + 8 构造负例
(注入无据声明)测 discrimination。
用法: ./.venv/bin/python -m scripts.rag_eval.build_calib_sheet
"""
from __future__ import annotations

import json
from pathlib import Path

R = Path("scripts/rag_eval/runs")


def _load():
    gen = {g["id"]: g for g in json.loads((R / "gen_dump_v64.json").read_text(encoding="utf-8"))}
    jud = {r["id"]: r["judge"] for r in json.loads((R / "judge_scores_v64.json").read_text(encoding="utf-8")) if r["judge"]}
    negs = json.loads((R / "faith_negatives.json").read_text(encoding="utf-8")) if (R / "faith_negatives.json").is_file() else []
    return gen, jud, negs


def build():
    gen, jud, negs = _load()
    key = []          # 隐藏:id/section → judge_score, ground_truth
    out = ["# 判官校准打标表（请独立判，先别看判官分；判完把每节的 x 填进 [ ]）\n"]

    # ---- 第一部分:correctness(分层抽样真题)----
    by = sorted(gen, key=lambda i: jud.get(i, {}).get("answer_correctness", 0))
    low = [i for i in by if jud.get(i, {}).get("answer_correctness", 1) < 0.5][:5]
    mid = [i for i in by if 0.5 <= jud.get(i, {}).get("answer_correctness", 1) < 1.0][:5]
    hi = [i for i in by if jud.get(i, {}).get("answer_correctness") == 1.0][:5]
    corr_ids = low + mid + hi
    out.append("## 第一部分:answer_correctness(对照参考答案,判模型答案是否**正确/达标**;不达标在 [ ] 打 x)\n")
    for k, i in enumerate(corr_ids, 1):
        g = gen[i]
        out.append(f"### C{k}\n**问题**: {g['question']}\n**参考答案**: {g['reference_answer']}\n**模型答案**: {g['answer']}\n- [ ] 我判:**错/不达标**\n")
        key.append({"tag": f"C{k}", "id": i, "metric": "answer_correctness", "judge": jud[i]["answer_correctness"]})

    # ---- 第二部分:citation_faithfulness(真负例 + 真正例)----
    cf_neg = [i for i in gen if jud.get(i, {}).get("citation_faithfulness") not in (None, 1.0)][:6]
    cf_pos = [i for i in gen if jud.get(i, {}).get("citation_faithfulness") == 1.0 and gen[i]["cited_texts"]][:5]
    out.append("\n## 第二部分:citation_faithfulness(看**被引片段**是否真支撑答案中引用它的论述;有不支撑的在 [ ] 打 x)\n")
    for k, i in enumerate(cf_neg + cf_pos, 1):
        g = gen[i]
        blocks = "\n".join(f"  [{n+1}] {t[:300]}" for n, t in enumerate(g["cited_texts"]))
        out.append(f"### CF{k}\n**答案**: {g['answer']}\n**被引片段**:\n{blocks}\n- [ ] 我判:**有引用不支撑**\n")
        key.append({"tag": f"CF{k}", "id": i, "metric": "citation_faithfulness", "judge": jud[i]["citation_faithfulness"]})

    # ---- 第三部分:faithfulness(8 真正例 + 8 构造负例,打乱)----
    pos = [{"id": i, "answer": gen[i]["answer"], "context": gen[i]["context"], "question": gen[i]["question"], "gt_unfaithful": False}
           for i in [n["id"] for n in negs]]   # 真正例 = 负例的源题(原答案)
    neg = [{"id": n["id"] + "_neg", "answer": n["modified_answer"], "context": gen[n["id"]]["context"],
            "question": gen[n["id"]]["question"], "gt_unfaithful": True, "judge": n.get("judge_faith")} for n in negs]
    items = pos + neg
    # 简单确定性打乱(交错),不依赖随机
    items = [x for pair in zip(pos, neg) for x in pair]
    out.append("\n## 第三部分:faithfulness(判**答案**每句是否都能由**上下文**支撑;有无据捏造=不忠实,在 [ ] 打 x)\n")
    for k, it in enumerate(items, 1):
        out.append(f"### F{k}\n**问题**: {it['question']}\n**答案**: {it['answer']}\n**上下文(截断)**: {it['context'][:700]}\n- [ ] 我判:**不忠实(有无据声明)**\n")
        jf = it.get("judge") if it["gt_unfaithful"] else jud.get(it["id"], {}).get("faithfulness")
        key.append({"tag": f"F{k}", "id": it["id"], "metric": "faithfulness", "judge": jf, "gt_unfaithful": it["gt_unfaithful"]})

    Path("eval-data/calib_sheet.md").write_text("\n".join(out), encoding="utf-8")
    (R / "calib_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[calib] 打标表 → eval-data/calib_sheet.md  ({len(corr_ids)} corr + {len(cf_neg+cf_pos)} cite + {len(items)} faith = {len(key)} 项)")
    print(f"[calib] 隐藏 key → {R/'calib_key.json'}")


if __name__ == "__main__":
    build()
