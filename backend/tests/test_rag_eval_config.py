from scripts.rag_eval.config import EvalConfig


def test_hash_stable_and_excludes_label():
    a = EvalConfig(k=6, label="baseline")
    b = EvalConfig(k=6, label="run-2")
    assert a.config_hash() == b.config_hash()        # label 不影响 hash
    assert len(a.config_hash()) == 12


def test_hash_changes_with_knob():
    assert EvalConfig(k=6).config_hash() != EvalConfig(k=10).config_hash()


def test_chunker_hash_only_depends_on_chunker():
    assert EvalConfig(k=6).chunker_hash() == EvalConfig(k=10).chunker_hash()
    assert EvalConfig(chunker_target=900).chunker_hash() != EvalConfig().chunker_hash()
