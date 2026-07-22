from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import dataclass

from epictrace.asr.config import AsrConfig
from epictrace.config import AppConfig
from epictrace.media.mineru_provisioner import MinerUProvisioner

# 进程内、按 settings.json 路径共享的锁:SettingsService 每请求新建实例,实例级锁无法
# 串行化并发请求。这里按绝对路径维护一把锁(_path_locks),由 _registry_lock 守护其建立,
# 使「同一个 settings.json」的所有读-改-写跨实例串行(本地单用户,进程内锁足够)。
_registry_lock = threading.Lock()
_path_locks: dict[str, threading.Lock] = {}


def _lock_for(path: str) -> threading.Lock:
    with _registry_lock:
        lock = _path_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _path_locks[path] = lock
        return lock


@dataclass
class ChatLLMSettings:
    """活动 Profile 的对话 LLM 取值(供 get_llm 构造 OpenAICompatLLM)。"""
    base_url: str
    api_key: str
    model: str
    context_window: int = 32768


def _short_id() -> str:
    """短随机 token 作为 Profile id(本地运行时,非工作流脚本——secrets 可用)。"""
    return secrets.token_hex(4)


def _clamp_int(value, default: int, lo: int, hi: int) -> int:
    """读时归一:非 int 或越界 → 回退默认/钳位,保证旧设置缺新字段也不崩。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(v, hi))


def _validate_int_range(name: str, value, lo: int, hi: int) -> None:
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid {name}: {value}")
    if not (lo <= v <= hi):
        raise ValueError(f"{name} out of range [{lo},{hi}]: {v}")


_VALID_EFFORT = {"high", "medium"}
_VALID_MODEL_SOURCE = {"modelscope", "huggingface", "local"}
# v2:可选 pypdf(简单文字处理,默认、开箱即用、免安装)或 mineru(OCR/VLM,质量高)。
_VALID_ENGINE = {"pypdf", "mineru"}
# 默认引擎:pypdf。免安装、免下模型,文本类 PDF/DOCX/PPTX 直接可用。
_DEFAULT_ENGINE = "pypdf"

# Cowork agent 循环的可调参数(settings.json 顶层 "agent" 键)
_AGENT_DEFAULTS = {"max_turns": 50, "turn_timeout_sec": 120, "user_instructions": ""}
_MAX_TURNS_RANGE = (1, 200)
_TURN_TIMEOUT_RANGE = (10, 3600)

_SANDBOX_DEFAULTS = {"memory_mb": 512, "cpu_sec": 60, "network": "none"}
_SANDBOX_MEMORY_RANGE = (64, 8192)
_SANDBOX_CPU_RANGE = (5, 600)
_SANDBOX_NETWORK_MODES = {"none", "unrestricted"}

_EMBEDDING_DEFAULTS = {
    "provider": "local", "base_url": "", "api_key": "", "model": "", "dimensions": 1024,
}
_EMBEDDING_PROVIDERS = {"local", "remote"}
_EMBEDDING_DIM_RANGE = (64, 8192)

# Cowork 权限模型(settings.json 顶层 "permissions" 键)
_VALID_PERMISSION_MODES = {"ask", "follow_a_plan", "skip_all"}
_VALID_TOOL_OVERRIDE = {"ask", "ask-session", "allow"}


class SettingsService:
    """读写 ~/.epictrace/settings.json。本地单用户,明文存盘(桌面 APP)。

    数据形状:
        { "profiles": [ {"id","name","base_url","api_key","model"} ],
          "active_profile_id": "<id|null>" }

    多个命名 Profile + 一个活动 Profile(目前用于 chat;以后 chat/agent/caption 可各选其一)。
    """

    def __init__(self, config: AppConfig) -> None:
        self._path = config.data_dir / "settings.json"
        self._config = config
        # 按路径共享的锁:串行化对同一 settings.json 的读-改-写(跨请求/跨实例)。
        self._lock = _lock_for(str(self._path.resolve()))

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
        """读取并就地迁移旧形状,返回规范化设置。

        归一 profiles/active_profile_id,但保留其余顶层键(如 extraction),
        使 profile 变更经 _write 回写时不会把 extraction 等键吞掉。
        """
        data = self._read_raw()
        profiles = data.get("profiles")
        if not isinstance(profiles, list):
            # 旧形状迁移:{"chat_llm": {...}} → 单个名为「默认」的活动 Profile。
            old = data.get("chat_llm")
            if isinstance(old, dict):
                pid = _short_id()
                migrated = dict(data)
                migrated.pop("chat_llm", None)
                migrated["profiles"] = [
                    {
                        "id": pid,
                        "name": "默认",
                        "base_url": old.get("base_url", ""),
                        "api_key": old.get("api_key", ""),
                        "model": old.get("model", ""),
                    }
                ]
                migrated["active_profile_id"] = pid
                # 关键:立刻落盘固定 id。否则每次 _load 都生成新随机 id,前端拿到的 id 与
                # 下次请求迁移出的 id 对不上 → update/delete/set_active 全部静默 no-op
                # (表现为"保存不下去、删不掉、名称改不动")。
                self._write(migrated)
                return migrated
            normalized = dict(data)
            normalized["profiles"] = []
            normalized["active_profile_id"] = None
            return normalized
        active = data.get("active_profile_id")
        ids = {p.get("id") for p in profiles if isinstance(p, dict)}
        if active not in ids:
            active = None
        normalized = dict(data)
        normalized["profiles"] = profiles
        normalized["active_profile_id"] = active
        return normalized

    def _write(self, data: dict) -> None:
        # 原子写:先写同目录临时文件,再 os.replace 覆盖目标(同卷上是原子 rename)。
        # 这样写到一半崩溃也只会留下临时文件,settings.json 永远是上一份完整内容,不被截断。
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        # 唯一临时名(同进程并发写时不撞)。失败时尽力清理。
        tmp = self._path.with_name(f".{self._path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())  # 落盘后再 rename,确保替换的是完整内容
            os.replace(tmp, self._path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

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

    def get_extraction_settings(self) -> dict:
        """{engine, effort, model_source}。无持久化 → 回退 AppConfig 默认。"""
        data = self._read_raw()
        ext = data.get("extraction")
        if not isinstance(ext, dict):
            ext = {}
        return {
            "engine": ext.get("engine", _DEFAULT_ENGINE),
            "effort": ext.get("effort", self._config.extraction_effort),
            "model_source": ext.get("model_source", self._config.model_source),
        }

    def set_extraction_settings(
        self, *, engine: str, effort: str, model_source: str
    ) -> dict:
        """校验后持久化 extraction 对象,返回更新后的设置。非法值 → ValueError。"""
        if engine not in _VALID_ENGINE:
            raise ValueError(f"invalid engine: {engine}")
        if effort not in _VALID_EFFORT:
            raise ValueError(f"invalid effort: {effort}")
        if model_source not in _VALID_MODEL_SOURCE:
            raise ValueError(f"invalid model_source: {model_source}")
        # 只改 extraction;profiles/active_profile_id 等其余键原样保留(读 raw,不经 _load 归一)。
        # 锁内做读-改-写,避免与 profile 写并发交错丢更新。
        with self._lock:
            data = self._read_raw()
            data["extraction"] = {
                "engine": engine, "effort": effort, "model_source": model_source,
            }
            self._write(data)
        return self.get_extraction_settings()

    def get_asr_settings(self) -> dict:
        """ASR 可调配置(model/language/vad/阈值…)。无持久化 → AsrConfig 默认。

        持久化的 asr 对象与默认合并(经 AsrConfig.from_dict 只取识别的键、补齐缺省),
        旧设置缺新字段也不崩。

        FIX G:读时迁移非法 model——已下架的 model(如 distil-large-v3)仍残留在 settings.json
        里时,矫正为默认 large-v3 并落盘固定(否则非法值跨重启存活,worker 拿到会就绪检测失败)。
        """
        data = self._read_raw()
        asr = data.get("asr")
        if not isinstance(asr, dict):
            asr = {}
        cfg = AsrConfig.from_dict(asr).to_dict()
        if cfg["model"] not in AsrConfig._VALID_MODELS:
            # 矫正为默认,并尽力把迁移落盘(失败不致命,下次读仍会再矫正)。
            cfg["model"] = AsrConfig().model
            try:
                with self._lock:
                    raw = self._read_raw()
                    cur = raw.get("asr")
                    if not isinstance(cur, dict):
                        cur = {}
                    cur["model"] = cfg["model"]
                    raw["asr"] = AsrConfig.from_dict(cur).to_dict()
                    self._write(raw)
            except OSError:
                pass
        return cfg

    def set_asr_settings(self, d: dict) -> dict:
        """校验后把部分键合并进现有 asr 设置并持久化,返回更新后的设置。非法值 → ValueError。

        - 校验 model ∈ AsrConfig._VALID_MODELS。
        - 校验 compute_type ∈ AsrConfig._VALID_COMPUTE_TYPES(未知值会让 WhisperModel 加载崩溃,FIX H)。
        - 校验 window_seconds ∈ [5, 120](<=0 破坏切片,过大无意义,FIX H)。
        - 部分 dict 合并:仅覆盖给出的键,其余保留现状。
        - 只改 asr;profiles/extraction 等其余顶层键原样保留(读 raw,不经 _load 归一)。
        锁内做读-改-写,避免与 profile/extraction 写并发交错丢更新。
        """
        d = d or {}
        if "model" in d and d["model"] not in AsrConfig._VALID_MODELS:
            raise ValueError(f"invalid model: {d['model']}")
        if "compute_type" in d and d["compute_type"] not in AsrConfig._VALID_COMPUTE_TYPES:
            raise ValueError(f"invalid compute_type: {d['compute_type']}")
        if "window_seconds" in d:
            try:
                ws = float(d["window_seconds"])
            except (TypeError, ValueError):
                raise ValueError(f"invalid window_seconds: {d['window_seconds']}")
            if not (AsrConfig._WINDOW_SECONDS_MIN <= ws <= AsrConfig._WINDOW_SECONDS_MAX):
                raise ValueError(f"window_seconds out of range [5,120]: {ws}")
        with self._lock:
            data = self._read_raw()
            current = data.get("asr")
            if not isinstance(current, dict):
                current = {}
            # 现状(补默认)叠加传入部分键 → 再经 from_dict 规范化为完整 dict。
            merged = {**AsrConfig.from_dict(current).to_dict(), **d}
            data["asr"] = AsrConfig.from_dict(merged).to_dict()
            self._write(data)
        return self.get_asr_settings()

    def get_agent_settings(self) -> dict:
        """Cowork agent 循环参数(max_turns/turn_timeout_sec/user_instructions),缺省回退默认。"""
        data = self._read_raw()
        agent = data.get("agent")
        if not isinstance(agent, dict):
            agent = {}
        return {
            "max_turns": _clamp_int(agent.get("max_turns"), _AGENT_DEFAULTS["max_turns"],
                                    *_MAX_TURNS_RANGE),
            "turn_timeout_sec": _clamp_int(agent.get("turn_timeout_sec"),
                                           _AGENT_DEFAULTS["turn_timeout_sec"],
                                           *_TURN_TIMEOUT_RANGE),
            "user_instructions": str(agent.get("user_instructions", "") or ""),
        }

    def set_agent_settings(self, d: dict) -> dict:
        """校验后部分合并 agent 设置并持久化(值为 None 的键保留原值)。非法值 → ValueError。"""
        d = d or {}
        if d.get("max_turns") is not None:
            _validate_int_range("max_turns", d["max_turns"], *_MAX_TURNS_RANGE)
        if d.get("turn_timeout_sec") is not None:
            _validate_int_range("turn_timeout_sec", d["turn_timeout_sec"], *_TURN_TIMEOUT_RANGE)
        if d.get("user_instructions") is not None and not isinstance(d["user_instructions"], str):
            raise ValueError("invalid user_instructions: must be a string")
        with self._lock:
            data = self._read_raw()
            merged = self.get_agent_settings()
            for k in ("max_turns", "turn_timeout_sec", "user_instructions"):
                if d.get(k) is not None:
                    merged[k] = d[k]
            data["agent"] = merged
            self._write(data)
        return self.get_agent_settings()

    def get_sandbox_settings(self) -> dict:
        """沙箱参数(需求 5):memory_mb/cpu_sec/network(none|unrestricted),缺省回退默认。"""
        data = self._read_raw()
        sb = data.get("sandbox")
        if not isinstance(sb, dict):
            sb = {}
        network = sb.get("network")
        if network not in _SANDBOX_NETWORK_MODES:
            network = _SANDBOX_DEFAULTS["network"]
        return {
            "memory_mb": _clamp_int(sb.get("memory_mb"), _SANDBOX_DEFAULTS["memory_mb"],
                                    *_SANDBOX_MEMORY_RANGE),
            "cpu_sec": _clamp_int(sb.get("cpu_sec"), _SANDBOX_DEFAULTS["cpu_sec"],
                                  *_SANDBOX_CPU_RANGE),
            "network": network,
        }

    def set_sandbox_settings(self, d: dict) -> dict:
        """校验后部分合并沙箱设置并持久化。非法值 → ValueError。"""
        d = d or {}
        if d.get("memory_mb") is not None:
            _validate_int_range("memory_mb", d["memory_mb"], *_SANDBOX_MEMORY_RANGE)
        if d.get("cpu_sec") is not None:
            _validate_int_range("cpu_sec", d["cpu_sec"], *_SANDBOX_CPU_RANGE)
        if d.get("network") is not None and d["network"] not in _SANDBOX_NETWORK_MODES:
            raise ValueError(f"invalid network: {d['network']}")
        with self._lock:
            data = self._read_raw()
            merged = self.get_sandbox_settings()
            for k in ("memory_mb", "cpu_sec", "network"):
                if d.get(k) is not None:
                    merged[k] = d[k]
            data["sandbox"] = merged
            self._write(data)
        return self.get_sandbox_settings()

    def get_embedding_settings(self) -> dict:
        """Embedding provider 设置(需求 8):local(BGE-M3)/ remote(OpenAI 兼容端点)。
        非法持久化值读时归一;api_key 原样保存(与 LLM Profile 同文件同保护级)。"""
        data = self._read_raw()
        emb = data.get("embedding")
        if not isinstance(emb, dict):
            emb = {}
        provider = emb.get("provider")
        if provider not in _EMBEDDING_PROVIDERS:
            provider = _EMBEDDING_DEFAULTS["provider"]
        dimensions = _clamp_int(emb.get("dimensions"),
                                _EMBEDDING_DEFAULTS["dimensions"], *_EMBEDDING_DIM_RANGE)
        if provider == "local":
            # 本地 BGE-M3 恒为 1024 维:remote 配置过的维度不能带回来(codex review R3)
            dimensions = 1024
        return {
            "provider": provider,
            "base_url": str(emb.get("base_url", "") or ""),
            "api_key": str(emb.get("api_key", "") or ""),
            "model": str(emb.get("model", "") or ""),
            "dimensions": dimensions,
        }

    def set_embedding_settings(self, d: dict) -> dict:
        """校验后部分合并 embedding 设置并持久化。非法值 → ValueError。"""
        d = d or {}
        if d.get("provider") is not None and d["provider"] not in _EMBEDDING_PROVIDERS:
            raise ValueError(f"invalid provider: {d['provider']}")
        if d.get("dimensions") is not None:
            _validate_int_range("dimensions", d["dimensions"], *_EMBEDDING_DIM_RANGE)
        for k in ("base_url", "api_key", "model"):
            if d.get(k) is not None and not isinstance(d[k], str):
                raise ValueError(f"invalid {k}: must be a string")
        with self._lock:
            data = self._read_raw()
            merged = self.get_embedding_settings()
            for k in ("provider", "base_url", "api_key", "model", "dimensions"):
                if d.get(k) is not None:
                    merged[k] = d[k]
            data["embedding"] = merged
            self._write(data)
        return self.get_embedding_settings()

    def get_permission_settings(self) -> dict:
        """权限设置 {mode, tool_overrides}。非法持久化值读时归一,不崩。"""
        data = self._read_raw()
        perm = data.get("permissions")
        if not isinstance(perm, dict):
            perm = {}
        mode = perm.get("mode")
        if mode not in _VALID_PERMISSION_MODES:
            mode = "ask"
        overrides = perm.get("tool_overrides")
        if not isinstance(overrides, dict):
            overrides = {}
        overrides = {str(k): v for k, v in overrides.items() if v in _VALID_TOOL_OVERRIDE}
        return {"mode": mode, "tool_overrides": overrides}

    def set_permission_settings(self, d: dict) -> dict:
        """校验后部分合并权限设置并持久化。非法值 → ValueError。"""
        d = d or {}
        if d.get("mode") is not None and d["mode"] not in _VALID_PERMISSION_MODES:
            raise ValueError(f"invalid mode: {d['mode']}")
        if d.get("tool_overrides") is not None:
            ov = d["tool_overrides"]
            if not isinstance(ov, dict):
                raise ValueError("invalid tool_overrides: must be an object")
            for k, v in ov.items():
                if v not in _VALID_TOOL_OVERRIDE:
                    raise ValueError(f"invalid tool override for {k!r}: {v}")
        with self._lock:
            data = self._read_raw()
            merged = self.get_permission_settings()
            if d.get("mode") is not None:
                merged["mode"] = d["mode"]
            if d.get("tool_overrides") is not None:
                merged["tool_overrides"] = dict(d["tool_overrides"])
            data["permissions"] = merged
            self._write(data)
        return self.get_permission_settings()

    def extraction_status(self) -> dict:
        """高质量提取引擎(MinerU)的 provisioning 状态。"""
        prov = MinerUProvisioner(
            self._config.mineru_venv_dir, uv_bin=getattr(self._config, "uv_bin", None)
        )
        return {
            "state": prov.state,
            "ready": prov.is_ready(),
            "error": prov.last_error,
            "failed_stage": prov.failed_stage,
        }

    def get_chat_llm(self) -> ChatLLMSettings | None:
        """活动 Profile 的 base_url/api_key/model;无活动 Profile 时返回 None。"""
        p = self.get_active_profile()
        if p is None:
            return None
        return ChatLLMSettings(
            base_url=p.get("base_url", ""),
            api_key=p.get("api_key", ""),
            model=p.get("model", ""),
            context_window=int(p.get("context_window", 32768)),
        )

    # ---- 变更 ----
    def create_profile(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        context_window: int = 32768,
    ) -> str:
        """新建 Profile,返回其 id。首个 Profile 自动成为活动。"""
        with self._lock:
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
                    "context_window": context_window,
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
        context_window: int | None = None,
    ) -> None:
        """更新 Profile 的字段。任一参数为 None → 保留原值;
        尤其 api_key=None 保留既有密钥(前端只回传打码视图,不该清空真 key)。"""
        with self._lock:
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
                    if context_window is not None:
                        p["context_window"] = context_window
                    self._write(data)
                    return
        # 未找到:静默不改(幂等,避免对已删除 id 抛错)

    def delete_profile(self, profile_id: str) -> None:
        """删除 Profile。若删的是活动 Profile,活动改指剩余的第一个,无则 None。"""
        with self._lock:
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
        with self._lock:
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
                    "context_window": int(p.get("context_window", 32768)),
                    "api_key_set": bool(p.get("api_key")),
                }
                for p in data["profiles"]
            ],
        }
