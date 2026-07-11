from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig


def test_health_ok(tmp_path):
    # 传 tmp 隔离的 config(仍覆盖 db=None 的默认构造分支):create_app 现在会跑
    # 向量 schema 版本重置,不能让测试写真实 ~/.epictrace(标记文件 + 全库 indexed 翻转)。
    client = TestClient(create_app(config=AppConfig(data_dir=tmp_path)))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
