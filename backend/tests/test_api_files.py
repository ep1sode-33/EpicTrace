from pathlib import Path


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
