from pathlib import Path


def test_scan_endpoint_registers_and_lists(client, tmp_path):
    folder = tmp_path / "P"
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    (folder / "note.md").write_text("hello", encoding="utf-8")

    resp = client.post(f"/api/projects/{pid}/scan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == 1

    files = client.get(f"/api/files?project_id={pid}").json()
    assert len(files) == 1
    assert files[0]["indexed"] is False
    assert files[0]["ingest_method"] == "folder_scan"


def test_scan_unknown_project_404(client):
    assert client.post("/api/projects/99999/scan").status_code == 404
