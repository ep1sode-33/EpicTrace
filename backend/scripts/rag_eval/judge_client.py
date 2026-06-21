"""Anthropic Messages 判官客户端(claude-opus-4-8,经 krill-ai 代理)。与 DeepSeek 生成器分家。
key 是机密:不打印、不落任何产物。失败回 None(指标记 NaN,不记 0)。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_json(text: str) -> dict | None:
    """剥 markdown 围栏后 json.loads(Opus 即便被要求只出 JSON 也爱加 ```json 围栏)。"""
    if not text:
        return None
    stripped = _FENCE.sub("", text.strip())
    # 仅去首尾围栏行后仍可能含前后噪声:退而求其次截取第一个 { 到最后一个 }。
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        a, b = stripped.find("{"), stripped.rfind("}")
        if a != -1 and b > a:
            try:
                return json.loads(stripped[a:b + 1])
            except json.JSONDecodeError:
                return None
        return None


@dataclass(frozen=True)
class JudgeConfig:
    base_url: str
    api_key: str
    model: str = "claude-opus-4-8"


def load_judge_config(keyfile: str | None = None) -> JudgeConfig:
    base = os.environ.get("RAG_EVAL_JUDGE_BASE_URL", "")
    key = os.environ.get("RAG_EVAL_JUDGE_KEY", "")
    model = os.environ.get("RAG_EVAL_JUDGE_MODEL", "claude-opus-4-8")
    if not (base and key):
        path = Path(keyfile or os.path.expanduser("~/Desktop/temp_key"))
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip().upper(), v.strip()
            if k in ("KEY", "API_KEY") and not key:
                key = v
            elif k == "BASE_URL" and not base:
                base = v
    if not (base and key):
        raise RuntimeError("judge config 缺 BASE_URL / KEY(看 temp_key 或环境变量)")
    return JudgeConfig(base_url=base, api_key=key, model=model)


def _httpx_transport(url, headers, json_body):
    import httpx
    resp = httpx.post(url, headers=headers, json=json_body, timeout=120)
    try:
        return resp.status_code, resp.json()
    except Exception:  # noqa: BLE001
        return resp.status_code, {}


class AnthropicJudge:
    def __init__(self, config: JudgeConfig, *, transport=None, retries: int = 2) -> None:
        self._cfg = config
        self._transport = transport or _httpx_transport
        self._retries = retries

    def judge_json(self, system: str, user: str, *, max_tokens: int = 1024) -> dict | None:
        url = self._cfg.base_url.rstrip("/") + "/v1/messages"
        headers = {"x-api-key": self._cfg.api_key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        body = {"model": self._cfg.model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": user}]}
        for _ in range(self._retries + 1):
            try:
                status, payload = self._transport(url, headers, body)
                if status == 200:
                    blocks = payload.get("content") or []
                    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                    parsed = extract_json(text)
                    if parsed is not None:
                        return parsed
            except Exception:  # noqa: BLE001 — 重试
                pass
        return None
