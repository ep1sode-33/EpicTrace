from pathlib import Path


def test_ingest_unknown_project_returns_404(client, tmp_path):
    src = tmp_path / "note.txt"
    src.write_text("hello", encoding="utf-8")
    resp = client.post(
        "/api/files/ingest",
        json={
            "project_id": 99999,
            "source_path": str(src),
            "ingest_method": "file_direct",
            "description": "",
        },
    )
    assert resp.status_code == 404


def test_ingest_empty_source_path_returns_422(client, tmp_path):
    folder = str(tmp_path / "P2")
    pid = client.post("/api/projects", json={"title": "P2", "folder_path": folder}).json()["id"]
    resp = client.post(
        "/api/files/ingest",
        json={
            "project_id": pid,
            "source_path": "",
            "ingest_method": "file_direct",
            "description": "",
        },
    )
    assert resp.status_code == 422


def test_ingest_file_and_list(client, tmp_path):
    folder = str(tmp_path / "P")
    pid = client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]

    src = tmp_path / "note.md"
    src.write_text("# vm\nvirtual memory", encoding="utf-8")

    resp = client.post(
        "/api/files/ingest",
        json={
            "project_id": pid,
            "source_path": str(src),
            "ingest_method": "file_direct",
            "description": "lecture",
        },
    )
    assert resp.status_code == 201
    rec = resp.json()
    assert rec["original_filename"] == "note.md"
    assert Path(rec["stored_path"]).exists()

    listed = client.get(f"/api/files?project_id={pid}").json()
    assert len(listed) == 1
    assert listed[0]["description"] == "lecture"


def _make_project(client, tmp_path, name="RX"):
    folder = str(tmp_path / name)
    return client.post("/api/projects", json={"title": name, "folder_path": folder}).json()["id"]


def test_ingest_engine_not_ready_returns_409(client, tmp_path, monkeypatch):
    from epictrace.media.errors import ExtractionEngineNotReady

    pid = _make_project(client, tmp_path, "ENR")
    src = tmp_path / "paper.pdf"
    src.write_bytes(b"%PDF-1.4 fake")

    class _Proc:
        def process(self, _path):
            raise ExtractionEngineNotReady("请先在设置中安装高质量提取引擎")

    monkeypatch.setattr("epictrace.services.ingest.get_processor", lambda path, config: _Proc())

    resp = client.post(
        "/api/files/ingest",
        json={
            "project_id": pid,
            "source_path": str(src),
            "ingest_method": "file_direct",
            "description": "",
        },
    )
    assert resp.status_code == 409
    assert "高质量提取引擎" in resp.json()["detail"]


def test_ingest_extraction_failed_returns_400(client, tmp_path, monkeypatch):
    from epictrace.media.errors import ExtractionFailed

    pid = _make_project(client, tmp_path, "EF")
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"PK fake")

    class _Proc:
        def process(self, _path):
            raise ExtractionFailed("mineru exited 2: boom")

    monkeypatch.setattr("epictrace.services.ingest.get_processor", lambda path, config: _Proc())

    resp = client.post(
        "/api/files/ingest",
        json={
            "project_id": pid,
            "source_path": str(src),
            "ingest_method": "file_direct",
            "description": "",
        },
    )
    assert resp.status_code == 400
    assert "mineru" in resp.json()["detail"]
