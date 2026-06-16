from pathlib import Path

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.vectorstore.milvus_lite import MilvusLiteStore
from fastapi.testclient import TestClient
from tests.fakes import FakeEmbedder


def _client(tmp_path: Path) -> TestClient:
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    return TestClient(create_app(db=db, embedder=FakeEmbedder(), vector_store=store))


def test_session_lifecycle_and_events(tmp_path: Path):
    c = _client(tmp_path)
    r = c.post("/api/capture/sessions", json={"sources": ["note"]})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["status"] == "recording"

    # 单一活动 session
    assert c.post("/api/capture/sessions", json={"sources": ["note"]}).status_code == 409

    assert c.post(f"/api/capture/sessions/{sid}/events",
                  json={"kind": "note", "payload": "hi"}).status_code == 201
    c.post(f"/api/capture/sessions/{sid}/pause")
    c.post(f"/api/capture/sessions/{sid}/resume")
    assert c.post(f"/api/capture/sessions/{sid}/stop").json()["status"] == "staged"

    detail = c.get(f"/api/capture/sessions/{sid}").json()
    kinds = [e["kind"] for e in detail["events"]]
    assert kinds == ["note", "pause", "resume"]


def test_rename_and_delete(tmp_path: Path):
    c = _client(tmp_path)
    sid = c.post("/api/capture/sessions", json={"sources": ["note"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/stop")
    assert c.patch(f"/api/capture/sessions/{sid}", json={"title": "新名"}).json()["title"] == "新名"
    assert c.delete(f"/api/capture/sessions/{sid}").status_code == 200
    assert c.get(f"/api/capture/sessions/{sid}").status_code == 404


def test_get_running_session_does_not_500(tmp_path: Path):
    """FIX 8:对一个仍在 recording 的 session 取详情,elapsed 计算不能因 tz 不匹配 500。"""
    c = _client(tmp_path)
    sid = c.post("/api/capture/sessions", json={"sources": ["note"]}).json()["id"]
    r = c.get(f"/api/capture/sessions/{sid}")
    assert r.status_code == 200, r.text
    assert r.json()["elapsed_seconds"] >= 0.0


def test_organize_ingests_and_starts_index_job(tmp_path: Path):
    c = _client(tmp_path)
    proj = c.post("/api/projects", json={"title": "P", "folder_path": str(tmp_path / "P")}).json()
    sid = c.post("/api/capture/sessions", json={"sources": ["note"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/events", json={"kind": "note", "payload": "virtual memory"})
    c.post(f"/api/capture/sessions/{sid}/stop")

    r = c.post(f"/api/capture/sessions/{sid}/organize", json={"project_id": proj["id"]})
    assert r.status_code == 200
    assert r.json()["project_id"] == proj["id"]      # 返回 IndexStatusOut(后台 job)
    # session 已 organized;项目里出现 1 条 session 入库记录
    assert c.get(f"/api/capture/sessions/{sid}").json()["status"] == "organized"
    files = c.get(f"/api/files?project_id={proj['id']}").json()
    assert any(f["ingest_method"] == "session" for f in files)

    # 再 organize → 409
    assert c.post(f"/api/capture/sessions/{sid}/organize",
                  json={"project_id": proj["id"]}).status_code == 409


def test_organize_unknown_project_404(tmp_path: Path):
    """FIX 7:对不存在的 project_id 归类 → 404(而非 500)。"""
    c = _client(tmp_path)
    sid = c.post("/api/capture/sessions", json={"sources": ["note"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/events", json={"kind": "note", "payload": "x"})
    c.post(f"/api/capture/sessions/{sid}/stop")
    r = c.post(f"/api/capture/sessions/{sid}/organize", json={"project_id": 99999})
    assert r.status_code == 404, r.text


def test_organize_recording_session_409(tmp_path: Path):
    """FIX 5/7:录制中的 session 归类 → 409(SessionNotStaged 映射)。"""
    c = _client(tmp_path)
    proj = c.post("/api/projects", json={"title": "P", "folder_path": str(tmp_path / "P")}).json()
    sid = c.post("/api/capture/sessions", json={"sources": ["note"]}).json()["id"]
    # 不 stop → 仍 recording
    r = c.post(f"/api/capture/sessions/{sid}/organize", json={"project_id": proj["id"]})
    assert r.status_code == 409, r.text


def test_organize_while_job_running_409(tmp_path: Path):
    """FIX 3:该项目已有 running 的索引 job 时再归类 → 409,且不起第二个 job。"""
    from epictrace.services.index import IndexJob

    c = _client(tmp_path)
    proj = c.post("/api/projects", json={"title": "P", "folder_path": str(tmp_path / "P")}).json()
    pid = proj["id"]
    sid = c.post("/api/capture/sessions", json={"sources": ["note"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/events", json={"kind": "note", "payload": "x"})
    c.post(f"/api/capture/sessions/{sid}/stop")

    running = IndexJob(project_id=pid, total=1, done=0, status="running")
    c.app.state.index_jobs[pid] = running

    r = c.post(f"/api/capture/sessions/{sid}/organize", json={"project_id": pid})
    assert r.status_code == 409, r.text
    # 没有启动第二个 job:仍是我们塞进去的同一个对象。
    assert c.app.state.index_jobs[pid] is running
    # session 没被标记 organized(归类未发生)。
    assert c.get(f"/api/capture/sessions/{sid}").json()["status"] == "staged"
