"""#140:解析 William 标好的 calib_sheet.md(每项 - [x]=我判有问题)+ 隐藏 calib_key.json(判官分/
构造真值)→ 算每指标 kappa(judge vs 人工)。达标(kappa≥~0.6)才采信该判官指标。
判前别看判官分;标完跑: ./.venv/bin/python -m scripts.rag_eval.calib_score
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.rag_eval.calibration import calibrate


def parse_sheet(md_path: str) -> dict[str, bool]:
    """返回 {tag: 人工判为"有问题"(勾了 [x])}。"""
    txt = Path(md_path).read_text(encoding="utf-8")
    heads = list(re.finditer(r"^### (\w+)\b", txt, re.M))
    out: dict[str, bool] = {}
    for i, m in enumerate(heads):
        seg_end = heads[i + 1].start() if i + 1 < len(heads) else len(txt)
        seg = txt[m.end():seg_end]
        out[m.group(1)] = bool(re.search(r"- \[[xX]\]", seg))
    return out


def _judge_bad(metric: str, judge) -> bool | None:
    """判官分二值化为"有问题":correctness<0.5=错;citation/faith<1.0=有不支撑/不忠实。"""
    if judge is None:
        return None
    if metric == "answer_correctness":
        return judge < 0.5
    return judge < 1.0


def score(sheet: str = "eval-data/calib_sheet.md",
          key: str = "scripts/rag_eval/runs/calib_key.json") -> None:
    human = parse_sheet(sheet)
    keys = json.loads(Path(key).read_text(encoding="utf-8"))
    agg: dict[str, dict] = {}
    for k in keys:
        h = k["human"] if "human" in k else human.get(k["tag"])  # 预填(如 round2 烤入的 round1 负例)优先
        jb = _judge_bad(k["metric"], k["judge"])
        if h is None or jb is None:
            continue
        d = agg.setdefault(k["metric"], {"j": [], "w": [], "gt": []})
        d["j"].append(jb)
        d["w"].append(h)
        if "gt_unfaithful" in k:
            d["gt"].append(k["gt_unfaithful"])
    print("# judge 校准(judge 二值 vs 人工二值,均以「有问题」为正类)")
    for metric, d in agg.items():
        cal = calibrate(d["j"], d["w"])
        verdict = "✅ 采信" if (cal["kappa"] == cal["kappa"] and cal["kappa"] >= 0.6) else "⚠️ 存疑/欠校准"
        print(f"{metric}: kappa={cal['kappa']:.2f} 一致={cal['agreement']:.0%} n={cal['n']}  {verdict}")
        if d["gt"]:
            acc = sum(1 for jb, gt in zip(d["j"], d["gt"]) if jb == gt) / len(d["gt"])
            print(f"   faith 构造负例:judge vs 真值 accuracy={acc:.0%}(n={len(d['gt'])})")


if __name__ == "__main__":
    import sys
    if "--round2" in sys.argv:
        score("eval-data/calib_sheet2.md", "scripts/rag_eval/runs/calib_key2.json")
    else:
        score()
