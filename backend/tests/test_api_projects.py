def test_create_and_list_projects(client, tmp_path):
    folder = str(tmp_path / "CS 2506")
    resp = client.post("/api/projects", json={"title": "CS 2506", "folder_path": folder})
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "CS 2506"
    assert body["id"] > 0

    listed = client.get("/api/projects").json()
    assert len(listed) == 1
    assert listed[0]["folder_path"] == folder
