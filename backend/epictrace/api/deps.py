from __future__ import annotations

import threading

from fastapi import Request

from epictrace.db import Database

# 串行化 vector store 的首次构造:避免并发两次构造抢 milvus-lite 的独占文件锁。
_vector_store_lock = threading.Lock()


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_embedder(request: Request):
    """延迟构造 embedder:仅在索引/检索路由首次用到时才起真件。

    provider 由 embedding 设置决定(需求 8):local = BGE-M3;remote = OpenAI 兼容端点。
    设置变更后经「签名比对」自动重建(app.state.embedder_key),无需重启。"""
    from epictrace.services.settings import SettingsService

    config = getattr(request.app.state, "config", None)
    # smoke 测试的 SimpleNamespace 无 data_dir → 读不了 settings,按 local 处理
    cfg = (SettingsService(config).get_embedding_settings()
           if config is not None and hasattr(config, "data_dir") else None)
    key = (cfg["provider"], cfg["base_url"], cfg["api_key"], cfg["model"], cfg["dimensions"]) if cfg else ("local",)
    # 注入的测试假件没有签名轨迹(tracked=None)→ 原样返回,不参与选路/重建
    # (曾把注入的 FakeEmbedder 误判为过期,CI 上真去下载 BGE-M3 卡死索引测试)
    tracked = getattr(request.app.state, "embedder_key", None)
    if request.app.state.embedder is not None and (tracked is None or tracked == key):
        return request.app.state.embedder
    if cfg is not None and cfg["provider"] == "remote" and cfg["base_url"] and cfg["model"]:
        from epictrace.embedding.openai_compat import OpenAICompatEmbedder

        embedder = OpenAICompatEmbedder(
            base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"],
            dimensions=cfg["dimensions"])
    else:
        from epictrace.embedding.bge_m3 import BgeM3Embedder

        embedder = BgeM3Embedder()
    request.app.state.embedder = embedder
    request.app.state.embedder_key = key
    return embedder


def get_reranker(request: Request):
    """延迟构造默认 reranker(BGE-reranker-v2)。同 get_embedder 模式:首次用到才起真件,
    且与 embedder 一样必须在任何 Milvus/gRPC 之前 warmup(见 macos-embedding-milvus-fork-order)。"""
    reranker = getattr(request.app.state, "reranker", None)
    if reranker is None:
        from epictrace.retrieval.rerank import BgeReranker

        reranker = BgeReranker()
        request.app.state.reranker = reranker
    return reranker


def get_vector_store(request: Request):
    """延迟构造默认 vector store(Milvus Lite),并保证"模型先加载、再起 gRPC"。

    macOS 上:milvus-lite 的 gRPC 客户端激活后,再 fork 加载 BGE-M3 / reranker 模型会段错误。
    所有首次用到 Milvus 的路径(索引 / 删除 / RAG 查询)都经过这里,所以在构造 Milvus
    之前先 warmup embedding 与 reranker 模型(此时进程内还没有任何 gRPC),全局保证顺序安全。
    用锁串行化,避免并发两次构造抢 milvus-lite 的独占文件锁。"""
    store = request.app.state.vector_store
    # 签名 = embedding 空间完整身份(provider/base_url/model/dimensions;codex review R3),
    # 持久化在 <milvus>.embedsig.json(进程重启后仍可比对)。
    from epictrace.services.settings import SettingsService

    config0 = getattr(request.app.state, "config", None)
    sig = _embedding_sig(config0)
    cur_dim = sig[3]
    tracked = getattr(request.app.state, "vector_store_sig", None)
    if store is not None and (tracked is None or tracked == sig):
        return store
    with _vector_store_lock:
        store = request.app.state.vector_store
        tracked = getattr(request.app.state, "vector_store_sig", None)
        stale = store is not None and tracked is not None and tracked != sig
        if store is None and config0 is not None:
            # 重启后:内存签名丢失,与持久化签名比对;不一致则旧向量整体作废
            stale = not _sig_file_matches(getattr(config0, "milvus_path", None), sig)
        if stale:
            config0b = getattr(request.app.state, "config", None)
            _drop_store_file(store, getattr(config0b, "milvus_path", None))
            store = request.app.state.vector_store = None
            # 向量已清空:全部记录翻回待索引(否则零目标、检索静默变空)
            _make_reset_indexed_on_heal(request)()
        if store is None:
            get_embedder(request).warmup()  # 先加载 embedding 模型(此时无 gRPC)
            get_reranker(request).warmup()  # 再加载 reranker 模型(仍无 gRPC)
            from epictrace.config import AppConfig
            from epictrace.vectorstore.milvus_lite import MilvusLiteStore

            # 用注入的 app.state.config(测试为 tmp data_dir),同 get_attachment_store 的模式;
            # 无注入才回退新建 AppConfig()(smoke 测试的 SimpleNamespace 无 config 属性,靠 getattr 回退)。
            config = getattr(request.app.state, "config", None) or AppConfig()
            store = MilvusLiteStore(db_path=config.milvus_path, dim=cur_dim,
                                    on_schema_heal=_make_reset_indexed_on_heal(request))
            request.app.state.vector_store = store
            request.app.state.vector_store_sig = sig
            _write_sig_file(config.milvus_path, sig)
    return store


def _embedding_sig(config) -> tuple:
    """embedding 空间身份(provider, base_url, model, dimensions);无配置 → 本地默认。"""
    from epictrace.services.settings import SettingsService

    if config is None or not hasattr(config, "data_dir"):
        return ("local", "", "", 1024)
    cfg = SettingsService(config).get_embedding_settings()
    return (cfg["provider"], cfg["base_url"], cfg["model"], cfg["dimensions"])


def _sig_path(db_path: str | None):
    from pathlib import Path

    return Path(str(db_path) + ".embedsig.json") if db_path else None


def _sig_file_matches(db_path: str | None, sig: tuple) -> bool:
    """持久化签名与当前一致?无签名文件(旧库/新库)→ 视为一致(旧库历史上就是本地模型)。"""
    import json

    p = _sig_path(db_path)
    if p is None or not p.exists():
        return True
    try:
        return tuple(json.loads(p.read_text(encoding="utf-8"))) == sig
    except (json.JSONDecodeError, OSError, TypeError):
        return True  # 签名文件损坏:不因此误删用户索引


def _write_sig_file(db_path: str | None, sig: tuple) -> None:
    import json
    import logging

    p = _sig_path(db_path)
    if p is None:
        return
    try:
        p.write_text(json.dumps(list(sig), ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logging.getLogger("epictrace").warning("写 embedding 签名文件失败: %s", e)


def _drop_store_file(store, db_path: str | None) -> None:
    """签名变化后废弃一个 Milvus Lite store:尽力 close,再删库文件(派生数据,可重建)。"""
    import logging
    from pathlib import Path

    try:
        close = getattr(store, "close", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001
        pass
    if db_path:
        try:
            Path(db_path).unlink(missing_ok=True)
        except OSError as e:
            logging.getLogger("epictrace").warning("删除 milvus 库文件失败(%s): %s", db_path, e)


def _make_reset_indexed_on_heal(request: Request):
    """构造 MilvusLiteStore 的自愈回调:当 chunks collection 因 schema 不一致被 drop 重建后,
    把**全部** IngestRecord.indexed 翻回 False —— 否则向量被清空而记录仍 indexed=True,
    常规索引零目标、检索静默变空(F3)。参照 services.index.reset_index_if_schema_upgraded 的
    update 写法,直接写一条 update,避免 import 循环。db 缺失(如 smoke 测试的 SimpleNamespace)→ no-op。"""
    def _reset() -> None:
        db = getattr(request.app.state, "db", None)
        if db is None:
            return
        from sqlalchemy import update

        from epictrace.models import IngestRecord

        with db.session() as s:
            s.execute(update(IngestRecord).values(indexed=False))

    return _reset


def get_attachment_store(request: Request):
    """会话级临时附件向量库(attachment_chunks)。**单独一个 milvus-lite 文件**——milvus-lite
    对每个 db 文件持独占锁,不能和项目库共用一个文件(否则两个 MilvusClient 抢锁)。
    与 get_vector_store 同样保证"先暖 embedder+reranker 再起 Milvus"(macOS fork 段错误)。
    签名(embedding 空间身份)持久化在 <db>.embedsig.json;失效时重建并把 indexed 引用
    翻回 deferred(codex review R2/R3)。"""
    config0 = getattr(request.app.state, "config", None)
    sig = _embedding_sig(config0)
    cur_dim = sig[3]
    store = getattr(request.app.state, "attachment_store", None)
    tracked = getattr(request.app.state, "attachment_store_sig", None)
    if store is not None and (tracked is None or tracked == sig):
        return store
    with _vector_store_lock:
        store = request.app.state.attachment_store
        tracked = getattr(request.app.state, "attachment_store_sig", None)
        stale = store is not None and tracked is not None and tracked != sig
        if store is None and config0 is not None:
            stale = not _sig_file_matches(getattr(config0, "attachment_milvus_path", None), sig)
        if stale:
            config0b = getattr(request.app.state, "config", None)
            _drop_store_file(store, getattr(config0b, "attachment_milvus_path", None))
            store = request.app.state.attachment_store = None
            # 向量已空但 Reference.mode 仍标 indexed(codex review R3):翻回 deferred(可重建态)
            _reset_indexed_attachments(request)
        if store is None:
            get_embedder(request).warmup()
            get_reranker(request).warmup()
            from epictrace.config import AppConfig
            from epictrace.vectorstore.milvus_lite import MilvusLiteStore, _ATTACHMENT_SCALARS

            config = getattr(request.app.state, "config", None) or AppConfig()
            store = MilvusLiteStore(db_path=config.attachment_milvus_path, dim=cur_dim,
                                    collection="attachment_chunks", scalars=_ATTACHMENT_SCALARS)
            request.app.state.attachment_store = store
            request.app.state.attachment_store_sig = sig
            _write_sig_file(config.attachment_milvus_path, sig)
    return store


def _reset_indexed_attachments(request: Request) -> None:
    """附件向量库被废弃后,把 references.mode='indexed' 翻回 'deferred'(可重建态)。"""
    import logging

    db = getattr(request.app.state, "db", None)
    if db is None:
        return
    from sqlalchemy import update

    from epictrace.models import Reference

    try:
        with db.session() as s:
            s.execute(update(Reference).where(Reference.mode == "indexed")
                      .values(mode="deferred"))
    except Exception as e:  # noqa: BLE001
        logging.getLogger("epictrace").warning("附件引用重分类失败: %s", e)


def get_llm(request: Request):
    """对话 LLM:优先用注入的 app.state.llm;否则按 SettingsService 判断是否「已配置」——
    存在一个活动 Profile(is_configured)就用其 base_url/key/model 构造 OpenAICompatLLM 并缓存,
    **允许空 api_key**(本地 Ollama 等无 key 端点),仅在「无活动 Profile」时返回 None(由路由 409)。
    用 app.state.config(create_app 注入,测试为 tmp data_dir)而非新建 AppConfig(),保证隔离。"""
    llm = getattr(request.app.state, "llm", None)
    if llm is not None:
        return llm
    from epictrace.config import AppConfig
    from epictrace.services.settings import SettingsService

    config = getattr(request.app.state, "config", None) or AppConfig()
    settings = SettingsService(config)
    chat = settings.get_chat_llm()
    if chat is None:
        return None
    from epictrace.llm.openai_compat import OpenAICompatLLM

    llm = OpenAICompatLLM(base_url=chat.base_url, api_key=chat.api_key, model=chat.model)
    request.app.state.llm = llm
    return llm


def get_provisioner(request: Request):
    """高质量提取 provisioner(MinerU)。优先用注入的 app.state.provisioner(测试假件);
    否则按 app.state.config.mineru_venv_dir 懒构造并缓存。"""
    prov = getattr(request.app.state, "provisioner", None)
    if prov is not None:
        return prov
    from epictrace.config import AppConfig
    from epictrace.media.mineru_provisioner import MinerUProvisioner

    config = getattr(request.app.state, "config", None) or AppConfig()
    prov = MinerUProvisioner(config.mineru_venv_dir, uv_bin=getattr(config, "uv_bin", None))
    request.app.state.provisioner = prov
    return prov


def get_asr_provisioner(request: Request):
    """ASR 模型 provisioner。架构转单遍 mlx 后 = mlx 完整 large-v3 的就绪检测/下载
    (MlxOneshotProvisioner,落 HF 默认缓存)。优先用注入的 app.state.asr_provisioner(测试假件)。"""
    prov = getattr(request.app.state, "asr_provisioner", None)
    if prov is not None:
        return prov
    from epictrace.asr.provisioner import MlxOneshotProvisioner

    prov = MlxOneshotProvisioner()
    request.app.state.asr_provisioner = prov
    return prov


def get_retriever(request: Request):
    """混合检索器:dense + sparse → RRF → rerank。优先用注入的 app.state.retriever;
    否则复用延迟构造的 embedder / store / reranker。"""
    retriever = getattr(request.app.state, "retriever", None)
    if retriever is not None:
        return retriever
    from epictrace.retrieval.pipeline import HybridRetriever

    return HybridRetriever(
        get_embedder(request),
        get_vector_store(request),
        get_reranker(request),
    )
