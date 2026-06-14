import json
from pathlib import Path

from epictrace.interfaces.media import MediaResult


def _project_conv(client, tmp_path):
    folder = tmp_path / "proj"; folder.mkdir()
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    cid = client.post(f"/api/projects/{pid}/conversations", json={"title": "t"}).json()["id"]
    return pid, cid


def test_add_external_reference_and_list(client, tmp_path: Path):
    _, cid = _project_conv(client, tmp_path)
    f = tmp_path / "note.md"; f.write_text("页表内容", encoding="utf-8")
    r = client.post(f"/api/conversations/{cid}/references",
                    json={"kind": "external", "source_path": str(f)})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "external" and body["mode"] == "fulltext"
    assert "extracted_text" not in body
    listed = client.get(f"/api/conversations/{cid}/references").json()
    assert len(listed) == 1 and listed[0]["display_name"] == "note.md"


def test_detach_reference(client, tmp_path: Path):
    _, cid = _project_conv(client, tmp_path)
    f = tmp_path / "n.md"; f.write_text("内容内容", encoding="utf-8")
    rid = client.post(f"/api/conversations/{cid}/references",
                      json={"kind": "external", "source_path": str(f)}).json()["id"]
    assert client.delete(f"/api/conversations/{cid}/references/{rid}").status_code == 204
    assert client.get(f"/api/conversations/{cid}/references").json() == []


def test_add_reference_bad_file_is_400(client, tmp_path: Path):
    _, cid = _project_conv(client, tmp_path)
    f = tmp_path / "empty.md"; f.write_text("   ", encoding="utf-8")
    r = client.post(f"/api/conversations/{cid}/references",
                    json={"kind": "external", "source_path": str(f)})
    assert r.status_code == 400


def _sse_events(body: str) -> list[tuple[str, str]]:
    """把 SSE 文本切成 (event, data) 列表(够测试用的极简解析)。"""
    events: list[tuple[str, str]] = []
    ev = None; data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            ev = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        elif line == "":
            if ev is not None:
                events.append((ev, "\n".join(data_lines)))
            ev = None; data_lines = []
    return events


def test_stream_attach_emits_status_then_done(client, tmp_path: Path, monkeypatch):
    """SSE 挂附件:假处理器报几次进度后返回 → status* 事件 + done(带创建的引用)。"""
    _, cid = _project_conv(client, tmp_path)
    f = tmp_path / "paper.pdf"; f.write_bytes(b"%PDF")  # 文件存在即可,文本由假处理器给

    class _Proc:
        def supports(self, _p):
            return True

        def process(self, _p, *, progress_cb=None):
            for msg in ("解析中 12/29", "解析中 29/29", "处理页面"):
                progress_cb(msg)
            return MediaResult(text="页表把虚拟地址映射到物理地址", metadata={})

    monkeypatch.setattr("epictrace.services.references.get_processor",
                        lambda p, config: _Proc())

    with client.stream("POST", f"/api/conversations/{cid}/references/stream",
                       json={"kind": "external", "source_path": str(f)}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())
    events = _sse_events(body)
    statuses = [d for e, d in events if e == "status"]
    assert statuses == ["解析中 12/29", "解析中 29/29", "处理页面"]
    done = [d for e, d in events if e == "done"]
    assert len(done) == 1
    ref = json.loads(done[0])
    assert ref["kind"] == "external" and ref["display_name"] == "paper.pdf"
    assert ref["mode"] == "fulltext"
    assert "extracted_text" not in ref  # done 用 ReferenceOut 形状(与非流式一致)
    # block-until-ready:done 之后引用确已落库、可列出。
    listed = client.get(f"/api/conversations/{cid}/references").json()
    assert len(listed) == 1 and listed[0]["id"] == ref["id"]


def test_stream_attach_emits_error_on_extraction_failure(client, tmp_path: Path, monkeypatch):
    """提取失败(processor.process 抛错)→ SSE error 事件,且不落引用。"""
    _, cid = _project_conv(client, tmp_path)
    f = tmp_path / "broken.pdf"; f.write_bytes(b"%PDF")

    from epictrace.media.errors import ExtractionFailed

    class _Proc:
        def supports(self, _p):
            return True

        def process(self, _p, *, progress_cb=None):
            progress_cb("解析中 1/29")
            raise ExtractionFailed("mineru exited 2: boom")

    monkeypatch.setattr("epictrace.services.references.get_processor",
                        lambda p, config: _Proc())

    with client.stream("POST", f"/api/conversations/{cid}/references/stream",
                       json={"kind": "external", "source_path": str(f)}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())
    events = _sse_events(body)
    assert ("status", "解析中 1/29") in events
    errors = [d for e, d in events if e == "error"]
    assert len(errors) == 1 and "boom" in errors[0]
    assert [e for e, _ in events if e == "done"] == []
    assert client.get(f"/api/conversations/{cid}/references").json() == []


def test_stream_attach_rejects_non_external(client, tmp_path: Path):
    _, cid = _project_conv(client, tmp_path)
    r = client.post(f"/api/conversations/{cid}/references/stream",
                    json={"kind": "internal", "ingest_record_id": 1})
    assert r.status_code == 400


def test_stream_attach_unknown_conversation_404(client):
    r = client.post("/api/conversations/999999/references/stream",
                    json={"kind": "external", "source_path": "/nope.pdf"})
    assert r.status_code == 404
