from pathlib import Path


def test_index_endpoint_indexes_pending(index_client, tmp_path):
    folder = tmp_path / "P"
    pid = index_client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    (folder / "note.md").write_text("page table " * 200, encoding="utf-8")
    index_client.post(f"/api/projects/{pid}/scan")

    resp = index_client.post(f"/api/projects/{pid}/index")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1 and body["done"] == 1 and body["status"] == "done"

    files = index_client.get(f"/api/files?project_id={pid}").json()
    assert all(f["indexed"] for f in files)


def test_index_status_unknown_project_404(index_client):
    assert index_client.post("/api/projects/99999/index").status_code == 404
