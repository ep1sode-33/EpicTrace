from pathlib import Path


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
