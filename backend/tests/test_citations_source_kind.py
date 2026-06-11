from epictrace.agent.citations import build_citations
from epictrace.retrieval.types import RetrievedChunk


def test_chunk_defaults_source_kind_project():
    c = RetrievedChunk(text="x", ingest_record_id=1, project_id=1,
                       char_start=0, char_end=1, source_type="folder_scan")
    assert c.source_kind == "project" and c.reference_id is None


def test_build_citations_includes_source_kind_and_reference_id():
    chunks = [
        RetrievedChunk(text="项目片段", ingest_record_id=7, project_id=1,
                       char_start=0, char_end=4, source_type="folder_scan"),
        RetrievedChunk(text="附件全文", ingest_record_id=0, project_id=0,
                       char_start=0, char_end=4, source_type="attachment",
                       source_kind="attachment", reference_id=42),
    ]
    out = build_citations("用了[1]和[2]", chunks)
    assert out[0]["source_kind"] == "project" and out[0]["reference_id"] is None
    assert out[1]["source_kind"] == "attachment" and out[1]["reference_id"] == 42
