"""rag-eval CLI:index / build-corpus / retrieve / report / diff / run / gen-golden / review-golden。
手动跑,不进 CI。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.rag_eval.config import EvalConfig
from scripts.rag_eval.golden import load_golden
from scripts.rag_eval.report import GEN_CORE, diff_runs, format_report
from scripts.rag_eval.runner import run_retrieve, write_run


def __getattr__(name: str):
    # 懒导入 run_generation:它顶部拉 epictrace.agent.*(经 langchain_core 牵出 transformers 等重库)。
    # 模块级懒解析让 `import cli` 保持轻量(纯路由/报表路径不拉重依赖);CLI 测试照常
    # monkeypatch.setattr(cli, "run_generation", fake)——patch 写进模块 __dict__ 后即遮蔽本 __getattr__。
    if name == "run_generation":
        from scripts.rag_eval.runner_generation import run_generation
        return run_generation
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

_RUNS = Path(__file__).parent / "runs"


def _load_summary(p: str) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _build_meta(cfg, golden_path: str, *, judge_model=None, gen_model=None) -> dict:
    """全量溯源:run_hash(config+code+data)+ 全 config + 代码/数据指纹 + git SHA + 模型 id + seed。"""
    from dataclasses import asdict

    from scripts.rag_eval import provenance
    ch = cfg.config_hash()
    code_fp = provenance.code_fingerprint()
    ds_fp = provenance.dataset_fingerprint(golden_path)
    meta = {"run_hash": provenance.run_hash(ch, code_fp, ds_fp), "config_hash": ch,
            "config": asdict(cfg), "code_fingerprint": code_fp, "dataset_fingerprint": ds_fp,
            "git_sha": provenance.git_sha(), "label": cfg.label, "seed": 0}
    if judge_model:
        meta["judge_model"] = judge_model
    if gen_model:
        meta["gen_model"] = gen_model
    return meta


def _active_model_name():
    """被测生成器(DeepSeek)的活动 profile 模型名;读不到 → None(不挡归档)。"""
    try:
        from epictrace.config import AppConfig
        from epictrace.services.settings import SettingsService
        return (SettingsService(AppConfig()).get_active_profile() or {}).get("model")
    except Exception:  # noqa: BLE001
        return None


def _cmd_report(ns) -> int:
    print(format_report(_load_summary(ns.summary), metrics=ns.metrics))
    return 0


def _cmd_diff(ns) -> int:
    print(diff_runs(_load_summary(ns.a), _load_summary(ns.b), metrics=ns.metrics))
    return 0


def _load_per_q(run_dir: str) -> list[dict]:
    p = Path(run_dir) / "per_question.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _cmd_report_ci(ns) -> int:
    from scripts.rag_eval.report import format_report_ci
    print(format_report_ci(_load_per_q(ns.run), metrics=ns.metrics))
    return 0


def _cmd_diff_paired(ns) -> int:
    from scripts.rag_eval.report import diff_runs_paired
    print(diff_runs_paired(_load_per_q(ns.a), _load_per_q(ns.b), metrics=ns.metrics))
    return 0


def _cmd_agg(ns) -> int:
    from scripts.rag_eval.report import format_multirun
    sums = [_load_summary(str(Path(d) / "summary.json")) for d in ns.runs]
    print(format_multirun(sums, metrics=ns.metrics))
    return 0


def _cmd_retrieve(ns) -> int:
    from scripts.rag_eval.wiring import build_retriever
    golden = load_golden(ns.golden)
    cfg = EvalConfig(k=ns.k, dense_n=ns.dense_n, fuse_m=ns.fuse_m, label=ns.label or "")
    retr = build_retriever(ns.project_id)
    res = run_retrieve(golden, retr, project_id=ns.project_id, config=cfg)
    out = write_run(res, _RUNS, meta=_build_meta(cfg, ns.golden))
    print(format_report({k: res[k] for k in ("config_hash", "n", "by_slice", "overall")}))
    print(f"\n[rag-eval] run written to {out}", file=sys.stderr)
    return 0


def _cmd_run(ns) -> int:
    # 测量点 ②③ 外循环:载 golden → 装配 retriever+judge+chat_model+llm+cache → 跑生成 → 落盘 + 报表。
    # 重组件装配走 wiring(懒导入真件);CLI 测试 monkeypatch 掉 wiring.* 与 cli.run_generation。
    from scripts.rag_eval import wiring
    from scripts.rag_eval.judge_cache import JudgeCache
    # 经模块属性取 run_generation:未 patch 时走本模块 __getattr__ 懒导入;CLI 测试 patch 后取假件。
    _run_generation = getattr(sys.modules[__name__], "run_generation")
    golden = load_golden(ns.golden)
    cfg = EvalConfig(k=ns.k, dense_n=ns.dense_n, fuse_m=ns.fuse_m, label=ns.label or "")
    cache = JudgeCache(_RUNS / "judge_cache.jsonl")
    judge = wiring.build_judge()
    res = _run_generation(golden, build_chat_model=wiring.build_chat_model_factory(),
                         llm=wiring.build_llm(), retriever=wiring.build_retriever(ns.project_id),
                         judge=judge, cache=cache, project_id=ns.project_id, config=cfg)
    judge_model = getattr(getattr(judge, "_cfg", None), "model", None)
    out = write_run(res, _RUNS, meta=_build_meta(cfg, ns.golden, judge_model=judge_model,
                                                 gen_model=_active_model_name()))
    print(format_report({k: res[k] for k in ("config_hash", "n", "by_slice", "overall")}, metrics=GEN_CORE))
    print(f"\n[rag-eval] run written to {out}", file=sys.stderr)
    return 0


def _cmd_gen_golden(ns) -> int:
    # 采样→synth_item 的编排见 plan 手动 bring-up:需按真实抽取文本(doc_text per ingest_record_id)接线,
    # 与 indexing.py 一样打通真 store。本任务先延迟到手动期,给出清晰退出信息。
    raise SystemExit(
        "gen-golden: 见 plan 手动 bring-up——本命令组织 采样+synth_item;按真实抽取文本接线")


def _cmd_review_golden(ns) -> int:
    # 人工精修:逐题 accept/reject/quit,culled 集落盘到 --out。
    from scripts.rag_eval.golden import load_golden as _lg
    from scripts.rag_eval.review import review_candidates, stdin_prompt
    kept = review_candidates(_lg(ns.candidates), prompt_fn=stdin_prompt, out_path=ns.out)
    print(f"[rag-eval] kept {len(kept)} items → {ns.out}", file=sys.stderr)
    return 0


def _cmd_index(ns) -> int:
    # 真重活:把 eval-data 入库到 eval Project 并建索引。懒导入,手动跑。
    from scripts.rag_eval.indexing import index_eval_corpus  # 见 Task 10 备注
    pid = index_eval_corpus(ns.eval_data, project_name=ns.project_name)
    print(f"[rag-eval] indexed eval corpus into project_id={pid}", file=sys.stderr)
    return 0


def _cmd_build_corpus(ns) -> int:
    from scripts.rag_eval.corpus import build_corpus
    from scripts.rag_eval.corpus_spec import load_entries   # 本地 gitignored spec,见 Task 10 备注
    rows = build_corpus(load_entries(ns.spec), dest=Path(ns.dest), corpus_version=ns.corpus_version)
    print(f"[rag-eval] copied {len(rows)} files into {ns.dest}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rag-eval")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("retrieve"); r.set_defaults(fn=_cmd_retrieve)
    r.add_argument("--golden", required=True); r.add_argument("--project-id", dest="project_id", type=int, required=True)
    r.add_argument("--k", type=int, default=6); r.add_argument("--dense-n", dest="dense_n", type=int, default=30)
    r.add_argument("--fuse-m", dest="fuse_m", type=int, default=20); r.add_argument("--label", default="")

    rep = sub.add_parser("report"); rep.set_defaults(fn=_cmd_report)
    rep.add_argument("--summary", required=True); rep.add_argument("--metrics", nargs="*", default=None)

    d = sub.add_parser("diff"); d.set_defaults(fn=_cmd_diff)
    d.add_argument("--a", required=True); d.add_argument("--b", required=True); d.add_argument("--metrics", nargs="*", default=None)

    rci = sub.add_parser("report-ci"); rci.set_defaults(fn=_cmd_report_ci)
    rci.add_argument("--run", required=True); rci.add_argument("--metrics", nargs="*", default=None)

    dp = sub.add_parser("diff-paired"); dp.set_defaults(fn=_cmd_diff_paired)
    dp.add_argument("--a", required=True); dp.add_argument("--b", required=True); dp.add_argument("--metrics", nargs="*", default=None)

    ag = sub.add_parser("agg"); ag.set_defaults(fn=_cmd_agg)
    ag.add_argument("--runs", nargs="+", required=True); ag.add_argument("--metrics", nargs="*", default=None)

    idx = sub.add_parser("index"); idx.set_defaults(fn=_cmd_index)
    idx.add_argument("--eval-data", dest="eval_data", required=True); idx.add_argument("--project-name", dest="project_name", default="rag-eval")

    bc = sub.add_parser("build-corpus"); bc.set_defaults(fn=_cmd_build_corpus)
    bc.add_argument("--spec", required=True); bc.add_argument("--dest", required=True); bc.add_argument("--corpus-version", dest="corpus_version", default="v1")

    rn = sub.add_parser("run"); rn.set_defaults(fn=_cmd_run)
    rn.add_argument("--golden", required=True); rn.add_argument("--project-id", dest="project_id", type=int, required=True)
    rn.add_argument("--k", type=int, default=6); rn.add_argument("--dense-n", dest="dense_n", type=int, default=30)
    rn.add_argument("--fuse-m", dest="fuse_m", type=int, default=20); rn.add_argument("--label", default="")

    rg = sub.add_parser("review-golden"); rg.set_defaults(fn=_cmd_review_golden)
    rg.add_argument("--candidates", required=True); rg.add_argument("--out", required=True)

    gg = sub.add_parser("gen-golden"); gg.set_defaults(fn=_cmd_gen_golden)
    gg.add_argument("--out", required=True)

    try:
        ns = p.parse_args(argv if argv is not None else sys.argv[1:])
    except SystemExit as e:  # 未知子命令:argparse 直接 exit(2),转成返回码
        return int(e.code) if e.code is not None else 2
    if not getattr(ns, "fn", None):  # 无子命令:打印用法并返回 2
        p.print_usage(sys.stderr)
        return 2
    return ns.fn(ns)


if __name__ == "__main__":
    raise SystemExit(main())
