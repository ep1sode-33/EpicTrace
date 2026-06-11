from __future__ import annotations

import json
from dataclasses import dataclass

from epictrace.config import AppConfig


@dataclass
class ChatLLMSettings:
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-chat"


class SettingsService:
    """读写 ~/.epictrace/settings.json。本地单用户,明文存盘(桌面 APP)。"""

    def __init__(self, config: AppConfig) -> None:
        self._path = config.data_dir / "settings.json"

    def _read(self) -> dict:
        if self._path.exists():
            return json.loads(self._path.read_text(encoding="utf-8"))
        return {}

    def get_chat_llm(self) -> ChatLLMSettings:
        c = self._read().get("chat_llm", {})
        return ChatLLMSettings(
            base_url=c.get("base_url", "https://api.deepseek.com"),
            api_key=c.get("api_key", ""),
            model=c.get("model", "deepseek-chat"),
        )

    def update_chat_llm(self, *, base_url: str, model: str, api_key: str | None = None) -> None:
        """更新对话 LLM 设置。api_key=None → 保留已存的 key(前端只回传打码视图,不该清空真 key);
        仅当显式传入非 None 值时才写入/替换(允许传空串以清空本地无 key 端点)。"""
        data = self._read()
        existing = data.get("chat_llm", {})
        key = existing.get("api_key", "") if api_key is None else api_key
        data["chat_llm"] = {"base_url": base_url, "api_key": key, "model": model}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_configured(self) -> bool:
        """用户是否至少保存过一次对话模型设置(settings.json 存在且含 chat_llm 项)。
        作为"是否可用"的信号:与"有没有 api_key"解耦——本地无 key 端点(Ollama 等)也算已配置。"""
        return self._path.exists() and "chat_llm" in self._read()

    def public_view(self) -> dict:
        c = self.get_chat_llm()
        return {
            "configured": self.is_configured(),
            "chat_llm": {"base_url": c.base_url, "model": c.model, "api_key_set": bool(c.api_key)},
        }
