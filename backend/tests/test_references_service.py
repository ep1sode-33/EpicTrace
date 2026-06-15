from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, IngestRecord, Project
from epictrace.services.references import ReferenceService

BIG_WIN = 1_000_000     # 预算极大 → 一定 fulltext
TINY_WIN = 10           # 预算极小 → 外部 deferred / 内部 focus


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid, pid = c.id, p.id
    return db, cid, pid


def _write(tmp_path, name, text):
    f = tmp_path / name; f.write_text(text, encoding="utf-8"); return str(f)


def test_add_external_small_is_fulltext_and_caches_text(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "note.md", "页表把虚拟地址映射到物理地址")
    svc = ReferenceService(db)
    ref = svc.add_external(cid, path, context_window=BIG_WIN)
    assert ref["kind"] == "external" and ref["mode"] == "fulltext"
    assert ref["display_name"] == "note.md"
    active = svc.list_active(cid)
    assert len(active) == 1 and active[0]["extracted_text"].startswith("页表")


def test_add_external_too_big_is_deferred(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "big.md", "字" * 500)
    ref = ReferenceService(db).add_external(cid, path, context_window=TINY_WIN)
    assert ref["mode"] == "deferred"


def test_add_external_rejects_empty_and_unsupported(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    import pytest
    empty = _write(tmp_path, "empty.md", "   ")
    with pytest.raises(ValueError):
        ReferenceService(db).add_external(cid, empty, context_window=BIG_WIN)
    weird = _write(tmp_path, "x.unknownext", "data")
    with pytest.raises(ValueError):
        ReferenceService(db).add_external(cid, weird, context_window=BIG_WIN)


def test_add_internal_small_fulltext_large_focus(tmp_path: Path):
    db, cid, pid = _setup(tmp_path)
    body = "短内容" * 8   # 24 字 → 12 token,超过 TINY_WIN 的 5 token 预算,但远低于 BIG_WIN
    small = _write(tmp_path, "small.md", body)
    with db.session() as s:
        rec = IngestRecord(project_id=pid, original_filename="small.md", stored_path=small,
                           content_hash="h", size_bytes=len(body.encode()), mtime=0.0,
                           ingest_method="folder_scan", extracted_text=body, indexed=True)
        s.add(rec); s.flush(); rid = rec.id
    svc = ReferenceService(db)
    ref = svc.add_internal(cid, rid, context_window=BIG_WIN)
    assert ref["kind"] == "internal" and ref["mode"] == "fulltext" and ref["ingest_record_id"] == rid
    ref2 = svc.add_internal(cid, rid, context_window=TINY_WIN)
    assert ref2["mode"] == "focus"


def test_detach_drops_from_active(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "n.md", "内容内容")
    svc = ReferenceService(db)
    ref = svc.add_external(cid, path, context_window=BIG_WIN)
    svc.detach(cid, ref["id"])
    assert svc.list_active(cid) == []


def test_cumulative_budget_defers_second_file(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    svc = ReferenceService(db)
    # 窗口 = 1000 → 全文预算 500 token ≈ 1000 字符。第一个文件吃掉 ~450 token。
    a = _write(tmp_path, "a.md", "字" * 900)
    b = _write(tmp_path, "b.md", "字" * 400)
    ra = svc.add_external(cid, a, context_window=1000)
    rb = svc.add_external(cid, b, context_window=1000)
    assert ra["mode"] == "fulltext"
    assert rb["mode"] == "deferred"      # a 已占预算,b 累加后超 → deferred


def test_add_external_missing_file_raises_valueerror(tmp_path: Path):
    import pytest
    db, cid, _ = _setup(tmp_path)
    with pytest.raises(ValueError):
        ReferenceService(db).add_external(cid, str(tmp_path / "nope.md"), context_window=1_000_000)


def test_add_internal_rejects_cross_project(tmp_path: Path):
    import pytest
    db, cid, pid = _setup(tmp_path)
    # 另建一个项目并在其下建 ingest record
    from epictrace.models import IngestRecord, Project
    other = _write(tmp_path, "other.md", "别的项目的文件")
    with db.session() as s:
        p2 = Project(title="P2", folder_path=str(tmp_path / "p2")); s.add(p2); s.flush()
        rec = IngestRecord(project_id=p2.id, original_filename="other.md", stored_path=other,
                           content_hash="h", size_bytes=1, mtime=0.0, ingest_method="folder_scan",
                           extracted_text="x", indexed=True)
        s.add(rec); s.flush(); rid = rec.id
    with pytest.raises(ValueError):
        ReferenceService(db).add_internal(cid, rid, context_window=1_000_000)


def test_detach_wrong_conversation_is_noop(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    svc = ReferenceService(db)
    ref = svc.add_external(cid, _write(tmp_path, "n.md", "内容内容"), context_window=1_000_000)
    svc.detach(999999, ref["id"])               # 不是该引用的会话 → 不动
    assert len(svc.list_active(cid)) == 1


def test_add_external_forwards_progress_cb_to_processor(tmp_path: Path, monkeypatch):
    """add_external 把 progress_cb 透传给 processor.process —— SSE 端点据此把进度流给前端。"""
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "paper.pdf", "x")  # 文本由假处理器给

    from epictrace.interfaces.media import MediaResult

    class _PdfProc:
        def supports(self, _path):
            return True

        def process(self, _path, *, progress_cb=None, cancel=None):
            if progress_cb is not None:
                progress_cb("解析中 12/29")
                progress_cb("解析中 29/29")
            return MediaResult(text="页表把虚拟地址映射到物理地址", metadata={})

    monkeypatch.setattr("epictrace.services.references.get_processor",
                        lambda p, config: _PdfProc())
    seen: list[str] = []
    svc = ReferenceService(db)
    ref = svc.add_external(cid, path, context_window=1_000_000, progress_cb=seen.append)
    assert ref["mode"] == "fulltext"
    assert seen == ["解析中 12/29", "解析中 29/29"]


def test_add_external_forwards_cancel_to_processor(tmp_path: Path, monkeypatch):
    """FIX 2:add_external 把 cancel 事件透传给 processor.process —— SSE 端点据此在
    客户端断开时停掉 mineru。"""
    import threading

    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "paper.pdf", "x")
    ev = threading.Event()
    captured = {}

    from epictrace.interfaces.media import MediaResult

    class _PdfProc:
        def supports(self, _path):
            return True

        def process(self, _path, *, progress_cb=None, cancel=None):
            captured["cancel"] = cancel
            return MediaResult(text="页表把虚拟地址映射到物理地址", metadata={})

    monkeypatch.setattr("epictrace.services.references.get_processor",
                        lambda p, config: _PdfProc())
    ReferenceService(db).add_external(cid, path, context_window=1_000_000, cancel=ev)
    assert captured["cancel"] is ev


def test_add_external_text_file_skips_model_ensure(tmp_path: Path):
    """FIX 2:挂 .txt/.md(→ TextMediaProcessor)即使 provisioner 为 installed_no_models,
    也不该触发(几 GB 的)模型下载——只有 MinerU 处理的富文档才需要模型。"""
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "note.md", "页表把虚拟地址映射到物理地址")

    class _Prov:
        state = "installed_no_models"
        def is_ready(self):  # noqa: D401
            return False
        def ensure_models_ready(self, **kw):
            raise AssertionError("text/code files must not trigger model download")
        def download_models(self, **kw):
            raise AssertionError("text/code files must not trigger model download")

    svc = ReferenceService(db, provisioner=_Prov())
    ref = svc.add_external(cid, path, context_window=BIG_WIN)
    assert ref["mode"] == "fulltext"


def test_add_external_mineru_file_blocks_until_models_ready(tmp_path: Path, monkeypatch):
    """FIX 1+2:挂 .pdf(→ MinerU)且 installed_no_models → 提取前必须 ensure_models_ready
    阻塞到就绪;ensure_models_ready 返回前提取不得发生。"""
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "paper.pdf", "x")

    from epictrace.interfaces.media import MediaResult
    from epictrace.media.mineru import MinerUMediaProcessor

    order: list[str] = []

    class _Prov:
        state = "installed_no_models"
        def is_ready(self):
            return False
        def ensure_models_ready(self, *, model_source="modelscope", progress_cb=None):
            order.append("ensure")

    prov = _Prov()
    real_proc = MinerUMediaProcessor(prov, model_source="modelscope", timeout=1)

    def _fake_process(self, p, *, progress_cb=None, cancel=None):
        order.append("extract")
        return MediaResult(text="页表把虚拟地址映射到物理地址", metadata={})

    monkeypatch.setattr(MinerUMediaProcessor, "process", _fake_process)
    monkeypatch.setattr("epictrace.services.references.get_processor",
                        lambda p, config: real_proc)

    svc = ReferenceService(db, provisioner=prov)
    svc.add_external(cid, path, context_window=BIG_WIN)
    assert order == ["ensure", "extract"]  # 先确保模型就绪,再提取


def test_add_external_surfaces_model_ensure_failure(tmp_path: Path, monkeypatch):
    """FIX 1:ensure_models_ready 抛(下载失败/超时)→ add_external 不得静默提取,
    应把失败转成 ValueError(由路由映射)。"""
    import pytest

    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "paper.pdf", "x")

    from epictrace.interfaces.media import MediaResult
    from epictrace.media.mineru import MinerUMediaProcessor

    class _Prov:
        state = "downloading_models"
        def is_ready(self):
            return False
        def ensure_models_ready(self, **kw):
            raise RuntimeError("model download failed: net down")

    prov = _Prov()
    real_proc = MinerUMediaProcessor(prov, model_source="modelscope", timeout=1)

    def _fake_process(self, p, *, progress_cb=None, cancel=None):
        raise AssertionError("must not extract when models not ready")

    monkeypatch.setattr(MinerUMediaProcessor, "process", _fake_process)
    monkeypatch.setattr("epictrace.services.references.get_processor",
                        lambda p, config: real_proc)

    with pytest.raises(ValueError):
        ReferenceService(db, provisioner=prov).add_external(cid, path, context_window=BIG_WIN)


def test_add_external_succeeds_even_if_provenance_write_fails(tmp_path: Path, monkeypatch):
    """provenance(content_list sidecar)派生/可选:DB 行已 commit 后写失败不得向上传播。"""
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "paper.pdf", "x")  # 内容由假处理器给,文件存在即可

    from epictrace.interfaces.media import MediaResult

    class _PdfProc:
        def supports(self, _path):
            return True

        def process(self, _path, *, progress_cb=None, cancel=None):
            return MediaResult(text="页表把虚拟地址映射到物理地址", metadata={
                "backend": "mineru-hybrid",
                "content_list": [{"type": "text", "text": "hi", "page_idx": 0}],
                "pages": 1})

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("epictrace.services.references.get_processor", lambda p, config: _PdfProc())
    monkeypatch.setattr("epictrace.services.references.write_provenance", _boom)

    svc = ReferenceService(db)
    ref = svc.add_external(cid, path, context_window=1_000_000)  # 不应抛
    assert ref["extracted_text"].startswith("页表")
    active = svc.list_active(cid)
    assert len(active) == 1 and active[0]["id"] == ref["id"]
    sidecar = Path(tmp_path) / "provenance" / f"reference-{ref['id']}.json"
    assert not sidecar.exists()
