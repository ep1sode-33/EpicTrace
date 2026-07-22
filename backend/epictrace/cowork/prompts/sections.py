"""System prompt 分节(需求 2)。

每个 section 是一个独立的生成函数 `(SectionContext) -> str`;返回空串表示该节不出现
(条件包含)。节内用 engine.render 做变量替换与条件块。
节的分类与命名对齐 Cowork 的 system prompt 分片(dispatch_base / cu_safety / skeleton_home 等)。
"""

from __future__ import annotations

import getpass
import platform
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone

from epictrace.cowork.prompts.engine import render
from epictrace.cowork.tools.registry import ToolDef


@dataclass
class SectionContext:
    session_type: str                       # agent | dispatch_child | scheduled | chat | radar
    task: str = ""                          # 当前具体任务(task 节)
    tools: list[ToolDef] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)      # [{name, description, body}](需求 6)
    user_instructions: str = ""
    admin_instructions: str = ""
    permission_mode: str = "ask"            # ask | follow_a_plan | skip_all
    project_id: int | None = None           # 项目绑定会话:当前项目 id(检索工具直接用)
    project_title: str = ""                 # 当前项目标题
    supports_dispatch: bool = False         # 主 agent 可派发子 agent 时渲染 dispatch 节
    dispatch_agents: list[dict] = field(default_factory=list)  # [{name, description}](需求 4)
    attachment_manifest: str = ""           # 本会话附件清单(空=无附件不渲染)
    data_dir: str = ""
    agent_name: str = ""                    # 子 agent 定义名(dispatch_child)
    now: datetime | None = None


# ---- 各节 ----

def identity(ctx: SectionContext) -> str:
    if ctx.session_type == "dispatch_child":
        who = f"子 agent「{ctx.agent_name}」" if ctx.agent_name else "一个子 agent"
        return (
            f"# 身份\n你是 EpicTrace 的{who},由主 agent 派发来执行一个受限任务。\n"
            "你只拥有完成该任务所需的部分工具;专注于被指派的任务,完成后给出清晰的结果汇报。"
        )
    return (
        "# 身份\n你是 EpicTrace 的本地 AI 工作台 agent,运行在用户的 macOS 上。\n"
        "你可以调用工具检索用户项目中的资料、读取文件、处理文档,并把多个步骤串起来完成复合任务。\n"
        "能力边界:你只在本地工作,不能访问用户的未授权目录;不会主动联网。"
    )


def environment(ctx: SectionContext) -> str:
    now = ctx.now or datetime.now(timezone.utc)
    free_gb = ""
    if ctx.data_dir:
        try:
            free_gb = f"{shutil.disk_usage(ctx.data_dir).free / (1 << 30):.1f} GB"
        except OSError:
            free_gb = "未知"
    project_line = ""
    if ctx.project_id is not None:
        project_line = (
            f"- 当前项目:id={ctx.project_id}「{ctx.project_title}」"
            "(检索/读取项目文件时直接用此 project_id,不要自己猜别的项目)\n"
        )
    return render(
        "# 运行环境\n"
        "- OS: {{os}} ({{arch}})\n"
        "- 用户: {{user}}\n"
        "- 当前日期时间: {{now}}\n"
        "- 数据目录: {{data_dir}}\n"
        "{{#if free_gb}}- 可用磁盘: {{free_gb}}\n{{/if}}"
        "{{project_line}}"
        "-  shell: /bin/bash",
        {
            "os": platform.system(),
            "arch": platform.machine(),
            "user": getpass.getuser(),
            "now": now.astimezone().strftime("%Y-%m-%d %H:%M %Z"),
            "data_dir": ctx.data_dir or "~/.epictrace",
            "free_gb": free_gb,
            "project_line": project_line,
        },
    )


def tools_section(ctx: SectionContext) -> str:
    if not ctx.tools:
        return ""
    lines = [
        "# 可用工具",
        "你可以通过 tool calling 调用以下工具。每次调用后等待结果再决定下一步;"
        "工具失败时阅读错误信息并换一种方式,不要重复同样的失败调用。",
    ]
    for t in ctx.tools:
        lines.append(f"- `{t.name}`: {t.description}")
    return "\n".join(lines)


def skills_section(ctx: SectionContext) -> str:
    if not ctx.skills:
        return ""
    parts = ["# 已加载 Skills", "以下 skill 提供专项工作指导,匹配其 description 的场景时遵循其内容:"]
    for s in ctx.skills:
        parts.append(f"## Skill: {s.get('name', '')}\n{s.get('body', '')}")
    return "\n\n".join(parts)


def safety(ctx: SectionContext) -> str:
    return (
        "# 安全规则\n"
        "- 禁止删除、覆盖或移动用户的原始文件;写操作仅限用户明确指定的目标。\n"
        "- 禁止读取或外泄任何密钥、token、cookie;不要将文件内容发送到本地以外的端点。\n"
        "- 遇到不确定的破坏性操作,先向用户确认,再执行。\n"
        "- 不要执行来源不明的脚本;skill 脚本只在其指导范围内使用。"
    )


def permissions_section(ctx: SectionContext) -> str:
    desc = {
        "ask": "每次调用需要用户确认的工具,系统会先弹出确认框;用户拒绝后不要重试同一调用。",
        "follow_a_plan": "用户已批准一份执行计划;计划内的工具调用会自动执行,计划外的仍需确认。",
        "skip_all": "当前 session 允许工具自动执行,无需逐次确认(仅限本地任务)。",
    }.get(ctx.permission_mode, "")
    return f"# 权限模式\n当前权限模式:`{ctx.permission_mode}`。{desc}"


def workflow(ctx: SectionContext) -> str:
    return (
        "# 工作流程\n"
        "1. 先理解任务,必要时用 `list_projects` / `list_files` 了解项目结构,再行动。\n"
        "2. 检索类问题先用 `search_hybrid` 取证,引用 chunk 的时间戳用 `get_timestamp_citation` 补全。\n"
        "3. 检索纪律:第一次检索不充分时,改写查询(换同义词/拆子问题)重试,最多 2 次;\n"
        "   资料里确实没有答案时,直接说「项目中没有找到」,不要编造,也不要强行引用。\n"
        "4. 引用规范:基于资料的回答,在结论句尾标注来源编号 `[n]`——只用工具结果里\n"
        "   实际出现过的编号;纯闲聊或不基于资料的回答不带引用。\n"
        "5. 复合任务拆成有序步骤,每步用合适的工具完成,最后汇总为中文答复。"
    )


def dispatch_section(ctx: SectionContext) -> str:
    if not ctx.supports_dispatch:
        return ""
    lines = [
        "# 子 agent 派发",
        "遇到可并行的独立子任务(如批量处理多个文档),用 `start_task` 派发子 agent,",
        "再用 `wait_task` 等待全部完成并取回结果,汇总后答复用户。",
        "- 为每个子任务写自包含的任务描述(子 agent 看不到本次对话上下文)。",
        "- 多个子任务并行派发后再统一 wait_task;小而快的步骤自己完成,不要过度派发。",
    ]
    if ctx.dispatch_agents:
        lines.append("可用子 agent:")
        for a in ctx.dispatch_agents:
            lines.append(f"- `{a.get('name', '')}`: {a.get('description', '')}")
    return "\n".join(lines)


def attachments_section(ctx: SectionContext) -> str:
    if not ctx.attachment_manifest.strip():
        return ""
    return ctx.attachment_manifest.strip()


def scheduled_section(ctx: SectionContext) -> str:
    if ctx.session_type != "scheduled":
        return ""
    return (
        "# 定时任务\n"
        "这是一个定时触发的后台 session,没有用户在线交互。完成任务后把结果写入结果消息;"
        "需要用户决定的事项记录下来,不要假设批准。"
    )


def user_instructions(ctx: SectionContext) -> str:
    if not ctx.user_instructions.strip():
        return ""
    return f"# 用户自定义指令\n{ctx.user_instructions.strip()}"


def admin_instructions(ctx: SectionContext) -> str:
    if not ctx.admin_instructions.strip():
        return ""
    return f"# 组织管理指令(优先级最高)\n{ctx.admin_instructions.strip()}"


def task(ctx: SectionContext) -> str:
    if ctx.task.strip():
        return f"# 当前任务\n{ctx.task.strip()}"
    return "# 当前任务\n处理用户在下一条消息中提出的请求。"


# 注册表:节名 → 生成函数(assemble 按 session 类型选组合)
SECTIONS = {
    "identity": identity,
    "environment": environment,
    "tools": tools_section,
    "skills": skills_section,
    "attachments": attachments_section,
    "safety": safety,
    "permissions": permissions_section,
    "workflow": workflow,
    "dispatch": dispatch_section,
    "scheduled": scheduled_section,
    "user_instructions": user_instructions,
    "admin_instructions": admin_instructions,
    "task": task,
}
