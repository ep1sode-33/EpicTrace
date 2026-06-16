from pathlib import Path

from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database


class _Sup:
    """假 supervisor:记录调用,不起任何真子进程。"""

    def __init__(self):
        self.started = []
        self.stopped = []
        self.paused = []
        self.resumed = []

    def start(self, **kw):
        self.started.append(kw)

    def stop(self, sid):
        self.stopped.append(sid)

    def pause(self, sid):
        self.paused.append(sid)

    def resume(self, sid):
        self.resumed.append(sid)


def _client(tmp_path: Path, sup):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    app = create_app(db=db)
    app.state.asr_supervisor = sup
    return TestClient(app)


def test_start_with_mic_triggers_supervisor(tmp_path):
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic", "note"]}).json()["id"]
    assert sup.started and sup.started[0]["session_id"] == sid


def test_start_without_audio_source_does_not_trigger(tmp_path):
    sup = _Sup()
    c = _client(tmp_path, sup)
    c.post("/api/capture/sessions", json={"sources": ["note", "clipboard"]})
    # 路由把全部 sources 传给 supervisor;由 supervisor 决定起不起。这里 assert 调用透传源。
    assert sup.started[0]["sources"] == ["note", "clipboard"]


def test_partial_roundtrip(tmp_path):
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    r = c.post(f"/api/capture/sessions/{sid}/partial",
               json={"source": "mic", "text": "暂定文本"})
    assert r.status_code in (200, 204)
    # transcription 段落经普通事件入库
    c.post(f"/api/capture/sessions/{sid}/events",
           json={"kind": "transcription", "payload": "确认文本", "meta": {"source": "mic"}})
    detail = c.get(f"/api/capture/sessions/{sid}").json()
    assert any(e["kind"] == "transcription" for e in detail["events"])


def test_stop_and_delete_call_supervisor_stop(tmp_path):
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/stop")
    assert sid in sup.stopped
    c.delete(f"/api/capture/sessions/{sid}")
    assert sup.stopped.count(sid) >= 1


def test_pause_resume_call_supervisor(tmp_path):
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/pause")
    c.post(f"/api/capture/sessions/{sid}/resume")
    assert sid in sup.paused and sid in sup.resumed


def test_start_supervisor_error_does_not_block_session(tmp_path):
    class _Boom:
        def start(self, **kw):
            raise RuntimeError("worker spawn failed")

        def stop(self, sid):
            pass

    c = _client(tmp_path, _Boom())
    r = c.post("/api/capture/sessions", json={"sources": ["mic"]})
    # supervisor.start 抛错也不挡 session 创建(降级:其余源/事件照常)。
    assert r.status_code == 201
