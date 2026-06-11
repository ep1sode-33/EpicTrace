import time


def _poll_until_done(client, pid, timeout=10.0):
    """轮询 status,直到不再 running(FakeEmbedder 很快)。返回最终 body。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/projects/{pid}/index/status").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError(f"index job did not finish within {timeout}s: last={body}")


def test_index_endpoint_indexes_pending(index_client, tmp_path):
    folder = tmp_path / "P"
    pid = index_client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    (folder / "note.md").write_text("page table " * 200, encoding="utf-8")
    index_client.post(f"/api/projects/{pid}/scan")

    # POST 立刻返回 running(后台线程推进),不等待完成。
    resp = index_client.post(f"/api/projects/{pid}/index")
    assert resp.status_code == 200
    started = resp.json()
    assert started["total"] == 1 and started["status"] == "running"

    # 轮询 status 直到完成,再断言。
    body = _poll_until_done(index_client, pid)
    assert body["total"] == 1 and body["done"] == 1 and body["status"] == "done"

    files = index_client.get(f"/api/files?project_id={pid}").json()
    assert all(f["indexed"] for f in files)


def test_index_status_unknown_project_404(index_client):
    assert index_client.post("/api/projects/99999/index").status_code == 404


def test_index_status_endpoint_unknown_project_404(index_client):
    # Fix 4:status 对不存在的项目也应 404,而非返回 idle。
    assert index_client.get("/api/projects/99999/index/status").status_code == 404
