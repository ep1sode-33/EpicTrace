from epictrace.cowork.citations import build_citations
from epictrace.retrieval.types import RetrievedChunk


def _c(rid, text):
    return RetrievedChunk(text=text, ingest_record_id=rid, project_id=7,
                          char_start=10, char_end=10 + len(text), source_type="folder_scan")


def test_build_citations_keeps_only_referenced_numbers():
    chunks = [_c(1, "页表把虚拟地址映射到物理地址"), _c(2, "无关"), _c(3, "缺页中断触发换页")]
    answer = "页表负责地址映射[1]。缺页时会换页[3]。"
    cites = build_citations(answer, chunks)
    ns = {c["n"] for c in cites}
    assert ns == {1, 3}  # 只保留答案里实际出现的 [n],丢弃 [2]
    c1 = next(c for c in cites if c["n"] == 1)
    assert c1["ingest_record_id"] == 1 and c1["char_start"] == 10 and "页表" in c1["snippet"]


def test_build_citations_ignores_out_of_range_numbers():
    cites = build_citations("乱标[9]", [_c(1, "x")])
    assert cites == []


def test_build_citations_passes_through_capture_session_and_ts():
    # 带会话溯源的 chunk → citation 透传两键,供前端跳回会话时刻
    chunk = RetrievedChunk(text="会话里说过页表", ingest_record_id=1, project_id=7,
                           char_start=0, char_end=7, source_type="folder_scan",
                           capture_session_id=12, ts="2026-07-11T03:00:00")
    out = build_citations("引用[1]", [chunk])
    assert out[0]["capture_session_id"] == 12
    assert out[0]["ts"] == "2026-07-11T03:00:00"


def test_build_citations_capture_fields_default_none():
    # 普通 chunk(未带会话字段)→ 两键恒出现,值为 None(JSON null)
    out = build_citations("引用[1]", [_c(1, "普通片段")])
    assert "capture_session_id" in out[0] and out[0]["capture_session_id"] is None
    assert "ts" in out[0] and out[0]["ts"] is None
