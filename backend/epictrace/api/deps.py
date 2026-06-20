from __future__ import annotations

import threading

from fastapi import Request

from epictrace.db import Database

# 串行化 vector store 的首次构造:避免并发两次构造抢 milvus-lite 的独占文件锁。
_vector_store_lock = threading.Lock()


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_embedder(request: Request):
    """延迟构造默认 embedder:仅在索引路由首次用到时才起真件(BGE-M3)。"""
    embedder = request.app.state.embedder
    if embedder is None:
        from epictrace.embedding.bge_m3 import BgeM3Embedder

        embedder = BgeM3Embedder()
        request.app.state.embedder = embedder
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
    if store is not None:
        return store
    with _vector_store_lock:
        store = request.app.state.vector_store
        if store is None:
            get_embedder(request).warmup()  # 先加载 embedding 模型(此时无 gRPC)
            get_reranker(request).warmup()  # 再加载 reranker 模型(仍无 gRPC)
            from epictrace.config import AppConfig
            from epictrace.vectorstore.milvus_lite import MilvusLiteStore

            store = MilvusLiteStore(db_path=AppConfig().milvus_path, dim=1024)
            request.app.state.vector_store = store
    return store


def get_attachment_store(request: Request):
    """会话级临时附件向量库(attachment_chunks)。**单独一个 milvus-lite 文件**——milvus-lite
    对每个 db 文件持独占锁,不能和项目库共用一个文件(否则两个 MilvusClient 抢锁)。
    与 get_vector_store 同样保证"先暖 embedder+reranker 再起 Milvus"(macOS fork 段错误)。"""
    store = getattr(request.app.state, "attachment_store", None)
    if store is not None:
        return store
    with _vector_store_lock:
        store = request.app.state.attachment_store
        if store is None:
            get_embedder(request).warmup()
            get_reranker(request).warmup()
            from epictrace.config import AppConfig
            from epictrace.vectorstore.milvus_lite import MilvusLiteStore, _ATTACHMENT_SCALARS

            config = getattr(request.app.state, "config", None) or AppConfig()
            store = MilvusLiteStore(db_path=config.attachment_milvus_path, dim=1024,
                                    collection="attachment_chunks", scalars=_ATTACHMENT_SCALARS)
            request.app.state.attachment_store = store
    return store


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
    prov = MinerUProvisioner(config.mineru_venv_dir)
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


def _active_profile(request: Request) -> dict | None:
    """活动 Profile 的完整字典(含 id/base_url/api_key/model)——agent 路探测 + 构造用。
    用 app.state.config(测试隔离),无活动 Profile → None。"""
    from epictrace.config import AppConfig
    from epictrace.services.settings import SettingsService

    config = getattr(request.app.state, "config", None) or AppConfig()
    return SettingsService(config).get_active_profile()


def get_chat_model_factory(request: Request):
    """返回一个 ()->ChatOpenAI 工厂(基于活动 Profile),供 ChatService 的 agent 路懒构造;
    无活动 Profile → None(ChatService 据此只走 Plan 5)。"""
    profile = _active_profile(request)
    if profile is None:
        return None
    from epictrace.agent.chat_model import make_chat_model

    return lambda: make_chat_model(profile)


def get_supports_tools(request: Request):
    """返回 ()->bool:活动 Profile 是否支持工具调用(探测结果缓存在 app.state)。
    无活动 Profile / 探测失败 → 视为不支持(走 Plan 5)。"""
    profile = _active_profile(request)
    if profile is None:
        return lambda: False
    from epictrace.agent.chat_model import make_chat_model
    from epictrace.agent.tool_probe import cached_supports_tools

    def supports() -> bool:
        try:
            return cached_supports_tools(
                request.app.state, profile, lambda p: make_chat_model(p))
        except Exception:  # noqa: BLE001 — 探测/构造任何故障 → 不支持
            return False

    return supports
