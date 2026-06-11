from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from epictrace.config import AppConfig


@dataclass
class ChatLLMSettings:
    """活动 Profile 的对话 LLM 取值(供 get_llm 构造 OpenAICompatLLM)。"""
    base_url: str
    api_key: str
    model: str


def _short_id() -> str:
    """短随机 token 作为 Profile id(本地运行时,非工作流脚本——secrets 可用)。"""
    return secrets.token_hex(4)


class SettingsService:
    """读写 ~/.epictrace/settings.json。本地单用户,明文存盘(桌面 APP)。

    数据形状:
        { "profiles": [ {"id","name","base_url","api_key","model"} ],
          "active_profile_id": "<id|null>" }

    多个命名 Profile + 一个活动 Profile(目前用于 chat;以后 chat/agent/caption 可各选其一)。
    """

    def __init__(self, config: AppConfig) -> None:
        self._path = config.data_dir / "settings.json"

    # ---- 持久化 ----
    def _read_raw(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # 损坏/不可读:当作空设置,不崩
                return {}
        return {}

    def _load(self) -> dict:
        """读取并就地迁移旧形状,返回规范化的 {profiles, active_profile_id}。"""
        data = self._read_raw()
        profiles = data.get("profiles")
        if not isinstance(profiles, list):
            # 旧形状迁移:{"chat_llm": {...}} → 单个名为「默认」的活动 Profile。
            old = data.get("chat_llm")
            if isinstance(old, dict):
                pid = _short_id()
                migrated = {
                    "profiles": [
                        {
                            "id": pid,
                            "name": "默认",
                            "base_url": old.get("base_url", ""),
                            "api_key": old.get("api_key", ""),
                            "model": old.get("model", ""),
                        }
                    ],
                    "active_profile_id": pid,
                }
                # 关键:立刻落盘固定 id。否则每次 _load 都生成新随机 id,前端拿到的 id 与
                # 下次请求迁移出的 id 对不上 → update/delete/set_active 全部静默 no-op
                # (表现为"保存不下去、删不掉、名称改不动")。
                self._write(migrated)
                return migrated
            return {"profiles": [], "active_profile_id": None}
        active = data.get("active_profile_id")
        ids = {p.get("id") for p in profiles if isinstance(p, dict)}
        if active not in ids:
            active = None
        return {"profiles": profiles, "active_profile_id": active}

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- 查询 ----
    def list_profiles(self) -> list[dict]:
        """内部用:含 api_key 的完整 Profile 列表(顺序即保存顺序)。"""
        return list(self._load()["profiles"])

    def get_active_profile(self) -> dict | None:
        data = self._load()
        active = data["active_profile_id"]
        if active is None:
            return None
        for p in data["profiles"]:
            if p.get("id") == active:
                return p
        return None

    def is_configured(self) -> bool:
        """是否存在一个活动 Profile(== 可用于对话)。"""
        return self.get_active_profile() is not None

    def get_chat_llm(self) -> ChatLLMSettings | None:
        """活动 Profile 的 base_url/api_key/model;无活动 Profile 时返回 None。"""
        p = self.get_active_profile()
        if p is None:
            return None
        return ChatLLMSettings(
            base_url=p.get("base_url", ""),
            api_key=p.get("api_key", ""),
            model=p.get("model", ""),
        )

    # ---- 变更 ----
    def create_profile(self, name: str, base_url: str, api_key: str, model: str) -> str:
        """新建 Profile,返回其 id。首个 Profile 自动成为活动。"""
        data = self._load()
        pid = _short_id()
        existing_ids = {p.get("id") for p in data["profiles"]}
        while pid in existing_ids:
            pid = _short_id()
        data["profiles"].append(
            {
                "id": pid,
                "name": name,
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
            }
        )
        if data["active_profile_id"] is None:
            data["active_profile_id"] = pid
        self._write(data)
        return pid

    def update_profile(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """更新 Profile 的字段。任一参数为 None → 保留原值;
        尤其 api_key=None 保留既有密钥(前端只回传打码视图,不该清空真 key)。"""
        data = self._load()
        for p in data["profiles"]:
            if p.get("id") == profile_id:
                if name is not None:
                    p["name"] = name
                if base_url is not None:
                    p["base_url"] = base_url
                if model is not None:
                    p["model"] = model
                if api_key is not None:
                    p["api_key"] = api_key
                self._write(data)
                return
        # 未找到:静默不改(幂等,避免对已删除 id 抛错)

    def delete_profile(self, profile_id: str) -> None:
        """删除 Profile。若删的是活动 Profile,活动改指剩余的第一个,无则 None。"""
        data = self._load()
        before = len(data["profiles"])
        data["profiles"] = [p for p in data["profiles"] if p.get("id") != profile_id]
        if len(data["profiles"]) == before:
            return  # 无此 id
        if data["active_profile_id"] == profile_id:
            data["active_profile_id"] = (
                data["profiles"][0]["id"] if data["profiles"] else None
            )
        self._write(data)

    def set_active(self, profile_id: str) -> None:
        """设活动 Profile;id 不存在则忽略(不改当前活动)。"""
        data = self._load()
        if any(p.get("id") == profile_id for p in data["profiles"]):
            data["active_profile_id"] = profile_id
            self._write(data)

    # ---- 对外视图 ----
    def public_view(self) -> dict:
        """本地单机:明文回传 api_key(允许前端查看/编辑/复制)。保留 api_key_set 便于显示。"""
        data = self._load()
        return {
            "configured": data["active_profile_id"] is not None,
            "active_profile_id": data["active_profile_id"],
            "profiles": [
                {
                    "id": p.get("id"),
                    "name": p.get("name", ""),
                    "base_url": p.get("base_url", ""),
                    "api_key": p.get("api_key", ""),
                    "model": p.get("model", ""),
                    "api_key_set": bool(p.get("api_key")),
                }
                for p in data["profiles"]
            ],
        }
