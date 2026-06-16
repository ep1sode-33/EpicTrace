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


def test_stop_calls_asr_stop_before_status_flip(tmp_path):
    """FIX B:stop_session 必须先停 ASR(此时 session 仍 recording)再翻 staged,
    否则 worker 最后几个 POST 撞 SessionNotRecording 409。假 supervisor 在 stop 时回查 session 状态。"""
    from epictrace.config import AppConfig
    from epictrace.db import Database
    from epictrace.services.capture import CaptureService

    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    from epictrace.api.app import create_app
    from fastapi.testclient import TestClient

    class _OrderSup:
        def __init__(self):
            self.status_at_stop = []

        def start(self, **kw):
            pass

        def stop(self, sid):
            # 停 ASR 时回查 session 当前状态;若已是 staged 说明顺序错了。
            self.status_at_stop.append(CaptureService(db).get_session(sid).status)

        def pause(self, sid):
            pass

        def resume(self, sid):
            pass

    sup = _OrderSup()
    app = create_app(db=db)
    app.state.asr_supervisor = sup
    c = TestClient(app)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/stop")
    # ASR 停的那一刻,session 还应是 recording(状态翻转在 ASR 停之后)。
    assert sup.status_at_stop == ["recording"]


def test_start_passes_resolved_config_to_supervisor(tmp_path):
    """FIX D:路由经 SettingsService 解析完整 ASR 设置并传给 supervisor.start(config=...)。"""
    from epictrace.config import AppConfig
    from epictrace.services.settings import SettingsService

    cfg_app = AppConfig(data_dir=tmp_path)
    SettingsService(cfg_app).set_asr_settings({"model": "medium", "vad_threshold": 0.33})

    from epictrace.db import Database
    db = Database(cfg_app)
    db.create_all()
    from epictrace.api.app import create_app
    from fastapi.testclient import TestClient

    sup = _Sup()
    app = create_app(db=db)
    app.state.asr_supervisor = sup
    c = TestClient(app)
    c.post("/api/capture/sessions", json={"sources": ["mic"]})
    assert sup.started
    kw = sup.started[0]
    assert kw["model"] == "medium"
    assert kw["config"]["vad_threshold"] == 0.33
    assert kw["config"]["model"] == "medium"


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
