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

        def process(self, _p, *, progress_cb=None, cancel=None):
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

        def process(self, _p, *, progress_cb=None, cancel=None):
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


def test_stream_attach_client_disconnect_cancels_worker(client, tmp_path: Path, monkeypatch):
    """FIX 2:客户端断开 → SSE 生成器置 cancel 事件并收尾;worker(add_external)据此
    停掉(不再空跑/不再无界堆积)。这里:
      - monkeypatch Request.is_disconnected 直接返回 True(模拟断开);
      - monkeypatch add_external 为一个循环检查 cancel 的 worker —— 被取消即退出,
        并 set 一个测试可观测的 stopped 事件。
    断言:生成器结束(请求返回),worker 因 cancel 退出(stopped 在限期内置位,不挂)。
    用假件 + 事件,完全确定,无真实 mineru/无不确定 sleep 竞争。"""
    import threading

    from starlette.requests import Request

    from epictrace.services.references import ReferenceService

    _, cid = _project_conv(client, tmp_path)
    f = tmp_path / "paper.pdf"; f.write_bytes(b"%PDF")

    stopped = threading.Event()
    saw_cancel = threading.Event()

    def fake_add_external(self, conversation_id, path, context_window,
                          progress_cb=None, cancel=None):
        # 模拟长耗时提取:循环到被取消;每圈报一次进度。
        progress_cb and progress_cb("解析中 1/29")
        for _ in range(1000):
            if cancel is not None and cancel.is_set():
                saw_cancel.set()
                stopped.set()
                from epictrace.media.errors import ExtractionFailed
                raise ExtractionFailed("cancelled")
            cancel.wait(timeout=0.01)
        stopped.set()
        return {"id": 1}

    async def always_disconnected(self):
        return True

    monkeypatch.setattr(ReferenceService, "add_external", fake_add_external)
    monkeypatch.setattr(Request, "is_disconnected", always_disconnected)

    with client.stream("POST", f"/api/conversations/{cid}/references/stream",
                       json={"kind": "external", "source_path": str(f)}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())  # 消费到流结束(不挂)

    # 生成器在检测到断开后应及时结束(不会无限等待);worker 因 cancel 退出。
    assert stopped.wait(timeout=5), "worker 未在断开后停止(可能仍在空跑)"
    assert saw_cancel.is_set()
    # 断开后不应发出 done 事件。
    assert [e for e, _ in _sse_events(body) if e == "done"] == []


def test_stream_attach_downloads_models_then_extracts(client, tmp_path: Path, monkeypatch):
    """无模型(installed_no_models)发文件:先经 status 进度报「下载模型」,下完转 ready,再提取出 done。"""
    _, cid = _project_conv(client, tmp_path)
    f = tmp_path / "paper.pdf"; f.write_bytes(b"%PDF")

    class _Prov:
        def __init__(self):
            self._ready = False
        @property
        def state(self):
            return "ready" if self._ready else "installed_no_models"
        def is_ready(self):
            return self._ready
        def download_models(self, *, model_source="modelscope", progress_cb=None):
            if progress_cb:
                progress_cb("正在下载模型(约数 GB,首次较久)…")
            self._ready = True

    prov = _Prov()
    client.app.state.provisioner = prov

    class _Proc:
        def supports(self, _p):
            return True
        def process(self, _p, *, progress_cb=None, cancel=None):
            progress_cb and progress_cb("解析中 1/1")
            return MediaResult(text="页表把虚拟地址映射到物理地址", metadata={})

    monkeypatch.setattr("epictrace.services.references.get_processor",
                        lambda p, config: _Proc())

    with client.stream("POST", f"/api/conversations/{cid}/references/stream",
                       json={"kind": "external", "source_path": str(f)}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())
    events = _sse_events(body)
    statuses = [d for e, d in events if e == "status"]
    # 先有下载模型的进度,再有提取进度。
    assert any("下载模型" in s for s in statuses)
    assert "解析中 1/1" in statuses
    assert prov.is_ready() is True  # 下载已发生
    done = [d for e, d in events if e == "done"]
    assert len(done) == 1
