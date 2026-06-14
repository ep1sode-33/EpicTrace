from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    d = Path.home() / ".epictrace"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass(frozen=True)
class LLMRoleConfig:
    """按角色的 LLM 配置(本计划未使用,先立结构;OpenAI-compatible)。"""
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-chat"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path = field(default_factory=_default_data_dir)
    # 预留:agent / chat / caption 各自端点+key+模型(后续 Plan 用)
    agent_llm: LLMRoleConfig = field(default_factory=LLMRoleConfig)
    chat_llm: LLMRoleConfig = field(default_factory=LLMRoleConfig)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "epictrace.db"

    @property
    def sqlalchemy_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def milvus_path(self) -> str:
        return str(self.data_dir / "epictrace_vectors.db")

    @property
    def attachment_milvus_path(self) -> str:
        # 会话级临时附件向量,单独一个 milvus-lite 文件 —— milvus-lite 对每个 db 文件持独占锁,
        # 不能让项目库与附件库共用一个文件(两个 MilvusClient 会抢锁)。临时、可弃,与永久库分开。
        return str(self.data_dir / "epictrace_attachment_vectors.db")

    @property
    def mineru_venv_dir(self) -> Path:
        return self.data_dir / ".MinerU-venv"

    @property
    def provenance_dir(self) -> Path:
        return self.data_dir / "provenance"
