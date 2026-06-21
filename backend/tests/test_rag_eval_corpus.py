# backend/tests/test_rag_eval_corpus.py
import hashlib

from scripts.rag_eval.corpus import CorpusEntry, build_corpus, load_manifest


def test_build_copies_out_and_hashes_without_touching_src(tmp_path):
    src_dir = tmp_path / "orig"
    src_dir.mkdir()
    f = src_dir / "lecture.txt"
    f.write_text("缓存命中率 = 命中 / 总访问", encoding="utf-8")
    src_mtime = f.stat().st_mtime

    dest = tmp_path / "eval-data"
    rows = build_corpus(
        [CorpusEntry(src=f, slices={"domain": "study-cs", "doc_type": "txt", "lang": "zh"})],
        dest=dest, corpus_version="v1",
    )
    # 原件未被改动(内容 + mtime)。
    assert f.read_text(encoding="utf-8") == "缓存命中率 = 命中 / 总访问"
    assert f.stat().st_mtime == src_mtime
    # 拷贝 + sha256 正确。
    assert len(rows) == 1
    want = hashlib.sha256(f.read_bytes()).hexdigest()
    assert rows[0].sha256 == want
    assert (dest / rows[0].rel_path).read_bytes() == f.read_bytes()
    # manifest 可回读且等价。
    assert load_manifest(dest / "manifest.jsonl") == rows


def test_manifest_rel_paths_unique_for_same_basename(tmp_path):
    a = tmp_path / "a" / "notes.md"; a.parent.mkdir(parents=True); a.write_text("AAA")
    b = tmp_path / "b" / "notes.md"; b.parent.mkdir(parents=True); b.write_text("BBB")
    rows = build_corpus(
        [CorpusEntry(src=a, slices={}), CorpusEntry(src=b, slices={})],
        dest=tmp_path / "out", corpus_version="v1",
    )
    assert rows[0].rel_path != rows[1].rel_path   # 同名不互相覆盖
