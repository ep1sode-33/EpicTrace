"""F2/F3:get_vector_store 尊重 app.state.config.milvus_path,并给 store 传自愈回调。

真 MilvusLiteStore 构造较重(且这里只关心「用哪个 db_path」与「回调是否装配」),
故把 MilvusLiteStore 换成轻量 spy,断言路径解析与回调装配,不起真库/真模型。
"""
from types import SimpleNamespace

import epictrace.api.deps as deps
import epictrace.vectorstore.milvus_lite as ml


class _FakeWarmable:
    """占位 embedder/reranker:只需有 warmup()(get_embedder/get_reranker 会调)。"""

    def warmup(self) -> None:
        return None


def _install_spy_store(monkeypatch) -> dict:
    """把 MilvusLiteStore 换成记录构造参数的 spy;返回捕获字典。"""
    captured: dict = {}

    class _SpyStore:
        def __init__(self, db_path, dim=1024, on_schema_heal=None, **kw):
            captured["db_path"] = db_path
            captured["on_schema_heal"] = on_schema_heal

    monkeypatch.setattr(ml, "MilvusLiteStore", _SpyStore)
    return captured


def test_get_vector_store_uses_state_config_milvus_path(tmp_path, monkeypatch):
    """注入了 app.state.config → store 落在 config.milvus_path,不新建 AppConfig()。"""
    captured = _install_spy_store(monkeypatch)
    cfg = SimpleNamespace(milvus_path=str(tmp_path / "custom.db"))
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        embedder=_FakeWarmable(), reranker=_FakeWarmable(), vector_store=None, config=cfg)))

    deps.get_vector_store(req)
    assert captured["db_path"] == str(tmp_path / "custom.db")


def test_get_vector_store_falls_back_to_appconfig_when_no_state_config(tmp_path, monkeypatch):
    """state 无 config 属性(如 smoke 测试的 SimpleNamespace)→ 回退 AppConfig().milvus_path。"""
    from epictrace.config import AppConfig

    captured = _install_spy_store(monkeypatch)
    monkeypatch.setattr(AppConfig, "milvus_path", property(lambda self: str(tmp_path / "fb.db")))
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        embedder=_FakeWarmable(), reranker=_FakeWarmable(), vector_store=None)))

    deps.get_vector_store(req)
    assert captured["db_path"] == str(tmp_path / "fb.db")


def test_get_vector_store_passes_schema_heal_callback(tmp_path, monkeypatch):
    """F3:get_vector_store 给 store 传入自愈回调,调用它把 IngestRecord.indexed 全翻 False。"""
    from epictrace.config import AppConfig
    from epictrace.db import Database
    from epictrace.models import IngestRecord, Project

    captured = _install_spy_store(monkeypatch)
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        proj = Project(title="P", folder_path=str(tmp_path)); s.add(proj); s.flush()
        s.add(IngestRecord(project_id=proj.id, original_filename="a.md",
                           stored_path=str(tmp_path / "a.md"), content_hash="h",
                           size_bytes=1, mtime=0.0, ingest_method="file_direct",
                           description="", indexed=True))
        s.flush()

    cfg = SimpleNamespace(milvus_path=str(tmp_path / "v.db"))
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        embedder=_FakeWarmable(), reranker=_FakeWarmable(), vector_store=None,
        config=cfg, db=db)))

    deps.get_vector_store(req)
    cb = captured["on_schema_heal"]
    assert cb is not None
    cb()  # 模拟自愈 drop 后触发
    from epictrace.services.ingest import IngestService
    assert all(not r.indexed for r in IngestService(db).list_for_project(proj.id))
