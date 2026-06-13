from epictrace.agent.tools import ChunkAccumulator, build_tools
from epictrace.retrieval.types import RetrievedChunk


class _ProjRetriever:
    def __init__(self): self.calls = []
    def retrieve(self, *, project_id, query, **kwargs):
        self.calls.append((project_id, query, kwargs))
        return [RetrievedChunk(text="项目片段TLB", ingest_record_id=99, project_id=project_id,
                               char_start=0, char_end=6, source_type="folder_scan")]


class _AttachRetriever:
    def __init__(self): self.calls = []
    def retrieve(self, *, conversation_id, reference_ids, query, k=6):
        self.calls.append((conversation_id, tuple(reference_ids), query))
        return [RetrievedChunk(text="附件片段", ingest_record_id=0, project_id=0,
                               char_start=5, char_end=9, source_type="attachment",
                               source_kind="attachment", reference_id=reference_ids[0])]


def test_search_project_library_returns_text_and_artifact():
    acc = ChunkAccumulator()
    tools = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                        attachment_retriever=None, conversation_id=1,
                        indexed_ext_ids=[], reference_texts={})
    t = {x.name: x for x in tools}["search_project_library"]
    msg = t.invoke({"name": "search_project_library", "args": {"query": "TLB"},
                    "id": "c1", "type": "tool_call"})
    assert "项目片段TLB" in msg.content            # readable text for the model
    assert msg.artifact and msg.artifact[0].text == "项目片段TLB"  # structured chunk captured


def test_project_search_passes_focus_ids():
    r = _ProjRetriever()
    tools = build_tools(retriever=r, project_id=3, focus_ids=[7, 8],
                        attachment_retriever=None, conversation_id=1,
                        indexed_ext_ids=[], reference_texts={})
    t = {x.name: x for x in tools}["search_project_library"]
    t.invoke({"name": "search_project_library", "args": {"query": "q"},
              "id": "c1", "type": "tool_call"})
    assert r.calls[0][2] == {"ingest_record_ids": [7, 8]}


def test_attachment_tools_only_when_indexed_refs():
    tools_none = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                             attachment_retriever=_AttachRetriever(), conversation_id=1,
                             indexed_ext_ids=[], reference_texts={})
    assert {t.name for t in tools_none} == {"search_project_library"}

    tools_with = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                             attachment_retriever=_AttachRetriever(), conversation_id=1,
                             indexed_ext_ids=[5], reference_texts={5: "页表内容很长"})
    assert {t.name for t in tools_with} == {
        "search_project_library", "search_attachment", "read_attachment"}


def test_read_attachment_exposed_for_fulltext_only_no_search():
    """A fulltext ref (NOT indexed) is readable, so read_attachment must be exposed even
    with no indexed refs. search_attachment stays gated on indexed_ext_ids (fulltext docs
    are not embedded → semantic search can't cover them)."""
    tools = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                        attachment_retriever=_AttachRetriever(), conversation_id=1,
                        indexed_ext_ids=[], reference_texts={9: "小文件全文"},
                        fulltext_ids=[9])
    assert {t.name for t in tools} == {"search_project_library", "read_attachment"}


def test_read_attachment_fulltext_returns_whole_doc_in_one_chunk():
    """read_attachment on a fulltext ref returns the ENTIRE extracted_text as one chunk
    (char_start=0, char_end=len, done=True), ignoring cursor — so 'summarize the whole
    file' is one-shot and pages are never needed for small fulltext docs."""
    text = "页表内容" * 500   # longer than the paging window, must NOT be paged
    tools = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                        attachment_retriever=_AttachRetriever(), conversation_id=1,
                        indexed_ext_ids=[], reference_texts={9: text}, fulltext_ids=[9])
    t = {x.name: x for x in tools}["read_attachment"]
    msg = t.invoke({"name": "read_attachment", "args": {"reference_id": 9, "cursor": 0},
                    "id": "c1", "type": "tool_call"})
    chunk = msg.artifact[0]
    assert chunk.char_start == 0 and chunk.char_end == len(text)   # whole-doc offsets
    assert chunk.text == text                                      # full content captured
    assert chunk.source_kind == "attachment" and chunk.reference_id == 9
    assert "done=True" in msg.content                              # one-shot, no more pages


def test_search_attachment_filters_by_indexed_refs():
    ar = _AttachRetriever()
    tools = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                        attachment_retriever=ar, conversation_id=42,
                        indexed_ext_ids=[5], reference_texts={5: "x"})
    t = {x.name: x for x in tools}["search_attachment"]
    msg = t.invoke({"name": "search_attachment", "args": {"query": "页表"},
                    "id": "c1", "type": "tool_call"})
    assert ar.calls == [(42, (5,), "页表")]
    assert msg.artifact[0].source_kind == "attachment"


def test_read_attachment_paginates_and_captures_offsets():
    tools = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                        attachment_retriever=_AttachRetriever(), conversation_id=1,
                        indexed_ext_ids=[5], reference_texts={5: "0123456789"})
    t = {x.name: x for x in tools}["read_attachment"]
    msg = t.invoke({"name": "read_attachment", "args": {"reference_id": 5, "cursor": 0},
                    "id": "c1", "type": "tool_call"})
    assert msg.artifact[0].char_start == 0
    assert msg.artifact[0].reference_id == 5
    assert "next_cursor" in msg.content        # paging hint for the model


def test_read_attachment_unknown_reference_no_artifact():
    tools = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                        attachment_retriever=_AttachRetriever(), conversation_id=1,
                        indexed_ext_ids=[5], reference_texts={5: "x"})
    t = {x.name: x for x in tools}["read_attachment"]
    msg = t.invoke({"name": "read_attachment", "args": {"reference_id": 999, "cursor": 0},
                    "id": "c1", "type": "tool_call"})
    assert msg.artifact == []
