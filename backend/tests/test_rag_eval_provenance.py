import re

from scripts.rag_eval.provenance import (
    code_fingerprint, dataset_fingerprint, file_fingerprint, git_sha, run_hash,
)


def test_file_fingerprint_deterministic_and_sensitive(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("hello")
    b = tmp_path / "b.txt"; b.write_text("world")
    fp1 = file_fingerprint([a, b])
    assert fp1 == file_fingerprint([b, a])                     # 顺序无关(内部 sort)
    assert re.fullmatch(r"[0-9a-f]{12}", fp1)
    a.write_text("HELLO")
    assert file_fingerprint([a, b]) != fp1                     # 内容变 → 指纹变
    assert file_fingerprint([a]) != file_fingerprint([a, b])   # 文件集变 → 指纹变


def test_dataset_fingerprint(tmp_path):
    g = tmp_path / "golden.jsonl"; g.write_text('{"id":"g1"}\n')
    assert re.fullmatch(r"[0-9a-f]{12}", dataset_fingerprint(g))
    assert dataset_fingerprint(tmp_path / "missing.jsonl") == "none"


def test_run_hash_combines():
    h1 = run_hash("cfg", "code", "data")
    assert h1 == run_hash("cfg", "code", "data")
    assert h1 != run_hash("cfg", "code2", "data")              # 代码变 → run_hash 变(核心修复)
    assert h1 != run_hash("cfg2", "code", "data")


def test_code_fingerprint_and_git_sha_smoke():
    assert re.fullmatch(r"[0-9a-f]{12}", code_fingerprint())   # 真读 epictrace 源文件
    assert isinstance(git_sha(), str) and git_sha()            # 非空 str(或 "unknown")


def test_write_run_uses_run_hash_and_writes_provenance(tmp_path):
    import json

    from scripts.rag_eval.runner import write_run
    result = {"config_hash": "cfg123", "n": 1, "by_slice": {}, "overall": {"m": 1.0},
              "per_question": [{"id": "g1", "slices": {}, "metrics": {"m": 1.0}}]}
    meta = {"run_hash": "rh999abc12", "config": {"k": 6}, "code_fingerprint": "cf",
            "dataset_fingerprint": "df", "git_sha": "abc1234", "judge_model": "claude-opus-4-8"}
    out = write_run(result, tmp_path / "runs", meta=meta)
    assert out.name.startswith("rh999abc12-")                  # 目录前缀用 run_hash(非 config_hash)
    rj = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert rj["judge_model"] == "claude-opus-4-8" and rj["git_sha"] == "abc1234"
    # 不给 meta → 退回 config_hash 前缀(back-compat)
    assert write_run(result, tmp_path / "runs2").name.startswith("cfg123-")
