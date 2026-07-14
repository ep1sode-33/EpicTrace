"""前端静态资源挂载:config.frontend_dist 注入优先,缺失时不静默回退到错误路径。"""
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig


def _cfg(tmp_path, dist):
    dd = tmp_path / "dd"
    dd.mkdir(exist_ok=True)
    return AppConfig(data_dir=dd, frontend_dist=dist)


def test_mount_injected_frontend_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>packaged-ok</html>", encoding="utf-8")
    app = create_app(config=_cfg(tmp_path, dist))
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "packaged-ok" in r.text


def test_injected_dist_missing_no_mount(tmp_path):
    # 显式注入但目录不存在:不挂载(404),也不回退 dev 相对路径——打包错误要响铃可见。
    app = create_app(config=_cfg(tmp_path, tmp_path / "nope"))
    assert TestClient(app).get("/").status_code == 404
