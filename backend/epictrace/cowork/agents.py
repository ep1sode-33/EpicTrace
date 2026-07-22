"""子 agent 定义(需求 4):YAML 描述名称/描述/工具白名单/模型/权限/轮数上限。

加载来源(后者覆盖同名前者):
- 捆绑定义:`epictrace/cowork/agent_defs/*.yaml`(随应用分发)
- 用户定义:`~/.epictrace/agents/*.yaml`

定义示例:
    name: pdf-processor
    description: Extracts text, tables, images from PDF files
    tools: [extract_pdf_text, search_vector]
    disallowed_tools: [delete_file]
    skills: [pdf-reading]      # 注入的 skill 指导(SKILL.md 正文进 system prompt)
    model: ""                # 空 = 继承主 agent 模型;可填更便宜的模型名
    permission_mode: skip_all
    max_turns: 15
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from epictrace.config import AppConfig
from epictrace.cowork.sessions import PERMISSION_MODES

log = logging.getLogger("epictrace.cowork")

DEFAULT_CHILD_MAX_TURNS = 15

# 子 agent 永远不允许再派发(防无限嵌套)或向用户提问(无人值守),无论白名单怎么写
DISPATCH_TOOL_NAMES = frozenset({
    "start_task", "wait_task", "process_document", "process_batch", "ask_user",
})


@dataclass(frozen=True)
class AgentDef:
    name: str
    description: str
    tools: tuple[str, ...] = ()            # 工具白名单;空 = 全部(减 disallowed)
    disallowed_tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()           # 注入的 skill 名白名单;空 = 不注入
    model: str = ""                        # 空 = 继承主 agent 模型
    permission_mode: str = "skip_all"      # 子 agent 默认无人值守
    max_turns: int = DEFAULT_CHILD_MAX_TURNS

    def allowed_tool_names(self, all_names: list[str]) -> list[str]:
        """按注册表实际工具求有效白名单:白名单(或全部) − disallowed − 派发工具。"""
        base = [n for n in (self.tools or tuple(all_names)) if n in all_names]
        banned = set(self.disallowed_tools) | DISPATCH_TOOL_NAMES
        return [n for n in base if n not in banned]


def _parse_def(data: dict, source: Path) -> AgentDef:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError(f"{source.name}: 缺少 name")
    mode = str(data.get("permission_mode") or "skip_all")
    if mode not in PERMISSION_MODES:
        raise ValueError(f"{source.name}: invalid permission_mode: {mode}")
    max_turns = data.get("max_turns", DEFAULT_CHILD_MAX_TURNS)
    if not isinstance(max_turns, int) or max_turns < 1:
        raise ValueError(f"{source.name}: invalid max_turns: {max_turns!r}")
    return AgentDef(
        name=name,
        description=str(data.get("description") or "").strip(),
        tools=tuple(str(t) for t in (data.get("tools") or [])),
        disallowed_tools=tuple(str(t) for t in (data.get("disallowed_tools") or [])),
        skills=tuple(str(s) for s in (data.get("skills") or [])),
        model=str(data.get("model") or "").strip(),
        permission_mode=mode,
        max_turns=max_turns,
    )


def _load_dir(path: Path, into: dict[str, AgentDef]) -> None:
    if not path.is_dir():
        return
    for f in sorted(path.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"{f.name}: 顶层必须是 mapping")
            d = _parse_def(data, f)
            into[d.name] = d
        except (yaml.YAMLError, ValueError, OSError) as e:
            log.warning("子 agent 定义 %s 加载失败,跳过: %s", f, e)


def load_agent_defs(config: AppConfig, extra_dir: Path | None = None) -> dict[str, AgentDef]:
    """加载全部子 agent 定义:捆绑目录 → 用户目录(~/.epictrace/agents)→ extra_dir(测试)。"""
    defs: dict[str, AgentDef] = {}
    _load_dir(Path(__file__).parent / "agent_defs", into=defs)
    _load_dir(config.data_dir / "agents", into=defs)
    if extra_dir is not None:
        _load_dir(extra_dir, into=defs)
    return defs
