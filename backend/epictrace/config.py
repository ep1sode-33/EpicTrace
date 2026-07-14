from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    # 打包版启动器/干净账户测试可用 EPICTRACE_DATA_DIR 重定向;默认 ~/.epictrace,与 dev 共用
    # (设计决策 1:数据与模型零迁移)。
    override = os.environ.get("EPICTRACE_DATA_DIR")
    d = Path(override).expanduser() if override else Path.home() / ".epictrace"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _env_path(name: str) -> Path | None:
    v = os.environ.get(name)
    return Path(v).expanduser() if v else None


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
    # 高质量提取(MinerU):模型源 + 子进程超时(秒) + 解析力度(effort)。
    # effort 默认 "medium":比 "high" 快得多,只丢图表分析,文本问答足够;high 对交互式太慢。
    model_source: str = "modelscope"
    extraction_timeout: int = 600
    extraction_effort: str = "medium"
    # 打包(.app)注入通道:启动器设 EPICTRACE_* 环境变量 → 这里读入。dev 形态三者皆空:
    # 前端 dist 走仓库相对路径回退,uv 走 PATH,系统内录 helper 走 swiftc 懒编译。
    frontend_dist: Path | None = field(default_factory=lambda: _env_path("EPICTRACE_FRONTEND_DIST"))
    uv_bin: str | None = field(default_factory=lambda: os.environ.get("EPICTRACE_UV_BIN") or None)
    packaged: bool = field(default_factory=lambda: os.environ.get("EPICTRACE_PACKAGED") == "1")

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
    def asr_model_dir(self) -> Path:
        # faster-whisper 权重缓存(WhisperModel download_root):放 data_dir 下的固定子目录,
        # 与 HF 全局缓存解耦,便于随 app 数据目录迁移/清理。AsrProvisioner 默认用 HF hub 缓存,
        # 子进程显式传此目录给 WhisperModel(download_root=...)统一落盘。
        return self.data_dir / ".asr-models"

    @property
    def provenance_dir(self) -> Path:
        return self.data_dir / "provenance"
