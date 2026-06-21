import json

from scripts.rag_eval.cli import main


def test_report_subcommand_reads_summary(tmp_path, capsys):
    s = tmp_path / "summary.json"
    s.write_text(json.dumps({"config_hash": "abc", "n": 1,
                             "overall": {"recall_any@5": 0.5, "mrr": 0.5},
                             "by_slice": {}}), encoding="utf-8")
    rc = main(["report", "--summary", str(s)])
    assert rc == 0
    assert "run abc" in capsys.readouterr().out


def test_diff_subcommand(tmp_path, capsys):
    a = tmp_path / "a.json"; b = tmp_path / "b.json"
    a.write_text(json.dumps({"config_hash": "a", "n": 1, "overall": {"mrr": 0.3}, "by_slice": {}}), encoding="utf-8")
    b.write_text(json.dumps({"config_hash": "b", "n": 1, "overall": {"mrr": 0.6}, "by_slice": {}}), encoding="utf-8")
    rc = main(["diff", "--a", str(a), "--b", str(b), "--metrics", "mrr"])
    assert rc == 0
    assert "diff a → b" in capsys.readouterr().out


def test_unknown_subcommand_returns_nonzero(capsys):
    assert main(["bogus"]) == 2
