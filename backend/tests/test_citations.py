from epictrace.agent.citations import build_citations
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
