from pathlib import Path

from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database


class _Sup:
    """假 supervisor:记录调用,不起任何真子进程。is_running 据 start/stop 跟踪在跑集。"""

    def __init__(self):
        self.started = []
        self.stopped = []
        self.paused = []
        self.resumed = []
        self.retranscribed = []
        self._running: set[int] = set()

    def retranscribe(self, sid, staging, *, config=None, model=None):
        self.retranscribed.append({"sid": sid, "staging": staging, "config": config})

    def start(self, **kw):
        self.started.append(kw)
        self._running.add(kw["session_id"])

    def stop(self, sid):
        self.stopped.append(sid)
        self._running.discard(sid)

    def is_running(self, sid):
        return sid in self._running

    def pause(self, sid):
        self.paused.append(sid)

    def resume(self, sid):
        self.resumed.append(sid)


class _ReadyProvisioner:
    """假 ASR provisioner:默认就绪(供不测门控的用例,不被空 tmp 缓存挡掉)。"""

    def is_ready(self, model):
        return True


def _client(tmp_path: Path, sup):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    app = create_app(db=db)
    app.state.asr_supervisor = sup
    app.state.asr_provisioner = _ReadyProvisioner()  # 默认就绪,门控不挡(FIX 1)
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
    app.state.asr_provisioner = _ReadyProvisioner()
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
    app.state.asr_provisioner = _ReadyProvisioner()
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


class _FakeProvisioner:
    """假 ASR provisioner:固定 is_ready 返回值,记录被问的 model。"""

    def __init__(self, ready: bool):
        self._ready = ready
        self.asked = []

    def is_ready(self, model):
        self.asked.append(model)
        return self._ready


def _client_with_prov(tmp_path, sup, prov):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    app = create_app(db=db)
    app.state.asr_supervisor = sup
    app.state.asr_provisioner = prov
    return TestClient(app)


def test_audio_session_blocked_when_model_not_ready(tmp_path):
    """选了 mic 但模型未就绪 → 409,session 不建,supervisor.start 不调。门经 provisioner.is_ready
    (= 注入的假件可控,与设置页同一实例;真件 MlxOneshotProvisioner 检 mlx 完整 v3)。"""
    sup = _Sup()
    prov = _FakeProvisioner(ready=False)
    c = _client_with_prov(tmp_path, sup, prov)
    r = c.post("/api/capture/sessions", json={"sources": ["mic", "note"]})
    assert r.status_code == 409
    assert sup.started == []                       # supervisor 没被拉起
    assert c.get("/api/capture/sessions").json() == []  # session 没被创建
    assert prov.asked                              # 确实查了就绪态


def test_system_audio_session_blocked_when_model_not_ready(tmp_path):
    """system_audio 同样受门控(经 provisioner)。"""
    sup = _Sup()
    prov = _FakeProvisioner(ready=False)
    c = _client_with_prov(tmp_path, sup, prov)
    r = c.post("/api/capture/sessions", json={"sources": ["system_audio"]})
    assert r.status_code == 409
    assert sup.started == []


def test_audio_session_allowed_when_model_ready(tmp_path):
    """FIX 1:模型就绪 → 201 + supervisor.start 被调。"""
    sup = _Sup()
    prov = _FakeProvisioner(ready=True)
    c = _client_with_prov(tmp_path, sup, prov)
    r = c.post("/api/capture/sessions", json={"sources": ["mic"]})
    assert r.status_code == 201
    assert sup.started and sup.started[0]["session_id"] == r.json()["id"]


def test_non_audio_session_not_gated_when_model_absent(tmp_path):
    """FIX 1:note/clipboard 等非音频源不受门控:模型缺也 201。"""
    sup = _Sup()
    prov = _FakeProvisioner(ready=False)
    c = _client_with_prov(tmp_path, sup, prov)
    r = c.post("/api/capture/sessions", json={"sources": ["note", "clipboard"]})
    assert r.status_code == 201
    assert prov.asked == []                        # 非音频源根本不查就绪态


class _FakeProc:
    """假 worker 句柄:poll() 返回 None = 在跑(供真 AsrSupervisor.is_running)。"""

    def terminate(self): ...
    def kill(self): ...
    def wait(self, timeout=None):
        return 0
    def poll(self):
        return None


def _client_real_sup(tmp_path: Path, sup):
    """注入**真** AsrSupervisor(spawn 假件)+ 就绪 provisioner,用于测懒启动 / is_running 逻辑。"""
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    app = create_app(db=db)
    app.state.asr_supervisor = sup
    app.state.asr_provisioner = _ReadyProvisioner()
    return TestClient(app)


def test_asr_enabled_initialized_from_audio_sources(tmp_path):
    """起 session 时「期望开启集」= 选中的音频源(非音频源不计)。"""
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions",
                 json={"sources": ["mic", "note"]}).json()["id"]
    assert c.get(f"/api/capture/sessions/{sid}/asr-source").json() == {"enabled": ["mic"]}


def test_asr_source_enable_then_disable(tmp_path):
    """启停某路:GET 反映期望开启集;关闭移除;空集不残留;重复启用幂等。"""
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    # 中途加开 system_audio(开始没勾)→ 204 + GET 反映。
    r = c.post(f"/api/capture/sessions/{sid}/asr-source",
               json={"source": "system_audio", "enabled": True})
    assert r.status_code == 204
    assert c.get(f"/api/capture/sessions/{sid}/asr-source").json() == {
        "enabled": ["mic", "system_audio"]}
    # 重复启用同源:幂等。
    c.post(f"/api/capture/sessions/{sid}/asr-source",
           json={"source": "system_audio", "enabled": True})
    assert c.get(f"/api/capture/sessions/{sid}/asr-source").json() == {
        "enabled": ["mic", "system_audio"]}
    # 关闭 mic → 移除。
    c.post(f"/api/capture/sessions/{sid}/asr-source", json={"source": "mic", "enabled": False})
    assert c.get(f"/api/capture/sessions/{sid}/asr-source").json() == {"enabled": ["system_audio"]}


def test_asr_source_enable_blocked_when_model_not_ready(tmp_path):
    """启用音源需模型就绪(同 start_session 门控,经 provisioner):未就绪 → 409,期望集不变。"""
    sup = _Sup()
    prov = _FakeProvisioner(ready=False)
    c = _client_with_prov(tmp_path, sup, prov)
    # 起无音频源的 session(不受门控)。
    sid = c.post("/api/capture/sessions", json={"sources": ["note"]}).json()["id"]
    r = c.post(f"/api/capture/sessions/{sid}/asr-source", json={"source": "mic", "enabled": True})
    assert r.status_code == 409
    assert c.get(f"/api/capture/sessions/{sid}/asr-source").json() == {"enabled": []}


def test_asr_source_unknown_source_400(tmp_path):
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    r = c.post(f"/api/capture/sessions/{sid}/asr-source", json={"source": "bogus", "enabled": True})
    assert r.status_code == 400


def test_asr_source_cleared_on_stop(tmp_path):
    """停止 session 清掉其期望开启集(下次同 id 不串状态)。"""
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/stop")
    assert c.get(f"/api/capture/sessions/{sid}/asr-source").json() == {"enabled": []}


def test_asr_source_enable_lazy_starts_worker_when_not_running(tmp_path):
    """中途开音源:worker 没在跑(起 session 时没勾音频源)→ 懒启动 worker(带当前期望集)。"""
    from epictrace.asr.supervisor import AsrSupervisor

    spawned = []
    sup = AsrSupervisor(spawn=lambda argv: spawned.append(argv) or _FakeProc())
    c = _client_real_sup(tmp_path, sup)
    # 起无音频源的 session → 不起 worker。
    sid = c.post("/api/capture/sessions", json={"sources": ["note"]}).json()["id"]
    assert spawned == [] and sup.is_running(sid) is False
    # 中途开 mic → 懒启动 worker。
    r = c.post(f"/api/capture/sessions/{sid}/asr-source", json={"source": "mic", "enabled": True})
    assert r.status_code == 204
    assert len(spawned) == 1 and "mic" in spawned[0]
    assert sup.is_running(sid) is True


def test_asr_source_enable_no_restart_when_worker_running(tmp_path):
    """中途加开第二路:worker 已在跑 → 不重启(worker 自行轮询 reconcile 出新源)。"""
    from epictrace.asr.supervisor import AsrSupervisor

    spawned = []
    sup = AsrSupervisor(spawn=lambda argv: spawned.append(argv) or _FakeProc())
    c = _client_real_sup(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    assert len(spawned) == 1                      # 起 session 时已起 worker
    c.post(f"/api/capture/sessions/{sid}/asr-source",
           json={"source": "system_audio", "enabled": True})
    assert len(spawned) == 1                      # 没有第二次 spawn(worker 自行 reconcile)
    assert c.get(f"/api/capture/sessions/{sid}/asr-source").json() == {
        "enabled": ["mic", "system_audio"]}


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


# ---- C:停止时整文件重转 → 替换转录事件 ----


def test_replace_transcript_replaces_only_transcription_events(tmp_path):
    """POST /transcript:删旧 transcription 事件 + 插权威段(meta.authoritative);其它事件(note)不动。"""
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    # 录制中先落几条流式转录 + 一条 note。
    c.post(f"/api/capture/sessions/{sid}/events",
           json={"kind": "transcription", "payload": "流式错字", "meta": {"source": "mic"}})
    c.post(f"/api/capture/sessions/{sid}/events", json={"kind": "note", "payload": "我的笔记"})
    c.post(f"/api/capture/sessions/{sid}/stop")
    # 权威重转回写。
    r = c.post(f"/api/capture/sessions/{sid}/transcript", json={"segments": [
        {"source": "mic", "text": "权威转录第一段", "start": 0.0, "end": 2.0, "audio_offset": 0.0,
         "words": [{"w": "权威", "s": 0.0, "e": 0.5}], "wav": "audio-mic-1.wav"},
        {"source": "mic", "text": "权威转录第二段", "start": 2.0, "end": 4.0, "audio_offset": 2.0},
    ]})
    assert r.status_code == 204
    events = c.get(f"/api/capture/sessions/{sid}").json()["events"]
    trans = [e for e in events if e["kind"] == "transcription"]
    notes = [e for e in events if e["kind"] == "note"]
    assert [e["payload"] for e in trans] == ["权威转录第一段", "权威转录第二段"]  # 流式被替换
    assert all(e["meta"].get("authoritative") for e in trans)
    assert notes and notes[0]["payload"] == "我的笔记"                          # note 不动


def test_replace_transcript_clears_retranscribing_flag(tmp_path):
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/stop")
    # 手动置重转标志(模拟 stop 已起重转),再回写 /transcript 应清除它。
    c.app.state.asr_retranscribing.add(sid)
    assert c.get(f"/api/capture/sessions/{sid}").json()["retranscribing"] is True
    c.post(f"/api/capture/sessions/{sid}/transcript", json={"segments": []})
    assert c.get(f"/api/capture/sessions/{sid}").json()["retranscribing"] is False


def test_stop_spawns_retranscribe_when_audio_present(tmp_path):
    """有 audio-*.wav → stop 触发 retranscribe + 置 retranscribing 标志。"""
    from pathlib import Path

    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    staging = c.get(f"/api/capture/sessions/{sid}").json()["staging_dir"]
    Path(staging, "audio-mic-1.wav").write_bytes(b"")  # 占位:_start_retranscribe 只 glob 存在性
    c.post(f"/api/capture/sessions/{sid}/stop")
    assert sup.retranscribed and sup.retranscribed[0]["sid"] == sid
    assert c.get(f"/api/capture/sessions/{sid}").json()["retranscribing"] is True


def test_stop_no_retranscribe_without_audio(tmp_path):
    """无 audio wav → stop 不触发 retranscribe(不白起 mlx)。"""
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["note"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/stop")
    assert sup.retranscribed == []
    assert c.get(f"/api/capture/sessions/{sid}").json()["retranscribing"] is False


def test_organize_blocked_while_retranscribing(tmp_path):
    """单遍架构:权威转录(停录后一次性)还在跑时入库会落空转录 → organize 返回 409。"""
    (tmp_path / "proj").mkdir()
    sup = _Sup()
    c = _client(tmp_path, sup)
    pid = c.post("/api/projects", json={"title": "P", "folder_path": str(tmp_path / "proj")}).json()["id"]
    sid = c.post("/api/capture/sessions", json={"sources": ["mic"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/stop")
    c.app.state.asr_retranscribing.add(sid)
    r = c.post(f"/api/capture/sessions/{sid}/organize", json={"project_id": pid})
    assert r.status_code == 409 and "权威转录" in r.json()["detail"]


def test_retranscribe_watcher_clears_flag_when_child_exits_without_writing(tmp_path):
    """看护:重转子进程退出但没回写 /transcript(失败)→ 清 asr_retranscribing,避免 organize 被永久挡死。"""
    import time as _t
    import types

    from epictrace.api.routers.capture import _watch_retranscribe
    c = _client(tmp_path, _Sup())

    class _Proc:
        def wait(self):
            return 1  # 立即退出,模拟失败(未 POST /transcript)

    sid = 999
    c.app.state.asr_retranscribing.add(sid)
    req = types.SimpleNamespace(app=types.SimpleNamespace(state=c.app.state))
    _watch_retranscribe(req, sid, _Proc())
    for _ in range(100):
        if sid not in c.app.state.asr_retranscribing:
            break
        _t.sleep(0.02)
    assert sid not in c.app.state.asr_retranscribing


def test_source_events_recorded_on_start_toggle_stop(tmp_path):
    """时间线:开局/中途开关/停录都记录音源 start/stop 事件(kind=source,meta={source,action})。"""
    sup = _Sup()
    c = _client(tmp_path, sup)
    sid = c.post("/api/capture/sessions", json={"sources": ["mic", "system_audio"]}).json()["id"]
    # 开局:两个音源各一条 start,按 sources 顺序(事件按 ts 升序,无重复)。
    evs = c.get(f"/api/capture/sessions/{sid}").json()["events"]
    start = [(e["meta"]["source"], e["meta"]["action"]) for e in evs if e["kind"] == "source"]
    assert start == [("mic", "start"), ("system_audio", "start")]
    # 中途关 system_audio → 一条 stop。
    c.post(f"/api/capture/sessions/{sid}/asr-source", json={"source": "system_audio", "enabled": False})
    # 停录 → 仍活跃的 mic 记 stop。
    c.post(f"/api/capture/sessions/{sid}/stop")
    evs = c.get(f"/api/capture/sessions/{sid}").json()["events"]
    src = [(e["meta"]["source"], e["meta"]["action"]) for e in evs if e["kind"] == "source"]
    # 断言**精确有序全序列**(顺序 + 无重复):开局两条 start → 中途关 → 停录关。
    assert src == [
        ("mic", "start"),
        ("system_audio", "start"),
        ("system_audio", "stop"),   # 中途关
        ("mic", "stop"),            # 停录时关
    ]
