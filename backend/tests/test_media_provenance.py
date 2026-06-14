import json
from pathlib import Path

from epictrace.media.provenance import write_provenance


def test_writes_sidecar_to_expected_path(tmp_path: Path):
    content = [{"type": "text", "text": "hi", "page_idx": 0}]
    out = write_provenance(tmp_path, "ingest", 42, content)
    assert out == tmp_path / "provenance" / "ingest-42.json"
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8")) == content


def test_reference_kind_path(tmp_path: Path):
    out = write_provenance(tmp_path, "reference", 7, [])
    assert out == tmp_path / "provenance" / "reference-7.json"
    assert out.exists()
