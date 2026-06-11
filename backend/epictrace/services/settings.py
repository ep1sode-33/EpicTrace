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

    def update_chat_llm(self, *, base_url: str, api_key: str, model: str) -> None:
        data = self._read()
        data["chat_llm"] = {"base_url": base_url, "api_key": api_key, "model": model}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def public_view(self) -> dict:
        c = self.get_chat_llm()
        return {"chat_llm": {"base_url": c.base_url, "model": c.model, "api_key_set": bool(c.api_key)}}
