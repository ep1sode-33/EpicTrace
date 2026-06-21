"""真生产组件装配(懒导入重依赖:FlagEmbedding / Milvus / reranker)。仅 CLI 真跑时调用。"""
from __future__ import annotations


def build_retriever(project_id: int):
    # 懒导入:测试/纯逻辑路径不拉重依赖。
    from epictrace.config import AppConfig
    from epictrace.embedding.bge_m3 import BgeM3Embedder
    from epictrace.retrieval.pipeline import HybridRetriever
    from epictrace.retrieval.rerank import BgeReranker
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore

    embedder = BgeM3Embedder()
    reranker = BgeReranker()
    embedder.warmup()          # 必须在建 Milvus(gRPC)之前 warmup,避免 macOS fork 段错误
    reranker.warmup()
    cfg = AppConfig()
    store = MilvusLiteStore(db_path=cfg.milvus_path, dim=1024)
    return HybridRetriever(embedder, store, reranker)


def build_judge():
    # 判官 = 不同家(Anthropic Messages，经代理),与 DeepSeek 生成器分家。懒导入。
    from scripts.rag_eval.judge_client import AnthropicJudge, load_judge_config

    return AnthropicJudge(load_judge_config())


def build_llm():
    # 生成器 LLM(真 DeepSeek，最终答路）。镜像 api/deps.get_llm:取活动 Profile 直构 OpenAICompatLLM。
    # 此函数仅手动真跑时调用(CLI 测试 monkeypatch 掉)。
    from epictrace.config import AppConfig
    from epictrace.llm.openai_compat import OpenAICompatLLM
    from epictrace.services.settings import SettingsService

    chat = SettingsService(AppConfig()).get_chat_llm()
    if chat is None:
        raise SystemExit("build_llm: 无活动 LLM Profile（先在产品设置里配置 BYOK）")
    return OpenAICompatLLM(base_url=chat.base_url, api_key=chat.api_key, model=chat.model)


def build_chat_model_factory():
    # 复用产品的 chat_model 工厂(真 DeepSeek，agent 工具调用路）。镜像 api/deps.get_chat_model_factory。
    # 返回 ()->ChatOpenAI；同样仅手动真跑时调用。
    from epictrace.agent.chat_model import make_chat_model
    from epictrace.config import AppConfig
    from epictrace.services.settings import SettingsService

    profile = SettingsService(AppConfig()).get_active_profile()
    if profile is None:
        raise SystemExit("build_chat_model_factory: 无活动 LLM Profile（先在产品设置里配置 BYOK）")
    return lambda: make_chat_model(profile)
