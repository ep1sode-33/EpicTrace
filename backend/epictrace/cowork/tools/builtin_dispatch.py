"""派发工具(需求 4):start_task 创建子 agent,wait_task 收集结果。

这两个工具只在主 agent session 注册(见 service.py);子 agent 的工具白名单
在 agents.AgentDef.allowed_tool_names 中永久排除它们(防无限嵌套派发)。
"""

from __future__ import annotations

from epictrace.cowork.dispatch import DEFAULT_WAIT_TIMEOUT, CompleteFactory, Dispatcher
from epictrace.cowork.tools.registry import ToolDef, ToolRegistry


def build_dispatch_tools(
    dispatcher: Dispatcher,
    *,
    parent_session_id: int,
    registry: ToolRegistry,
    complete_factory: CompleteFactory,
) -> list[ToolDef]:
    agent_names = sorted(dispatcher.agent_defs)
    agent_enum = "、".join(agent_names) or "(无可用子 agent)"

    def start_task(agent: str, task: str, context: str = "") -> str:
        return dispatcher.start(
            parent_session_id=parent_session_id,
            agent_name=agent,
            task=task,
            context=context,
            registry=registry,
            complete_factory=complete_factory,
        )

    def wait_task(task_ids: list[int] | None = None,
                  timeout_sec: float = DEFAULT_WAIT_TIMEOUT) -> str:
        return dispatcher.wait(
            parent_session_id=parent_session_id,
            task_ids=task_ids,
            timeout=timeout_sec,
        )

    def _pick_worker() -> str | None:
        """优先 file-worker,否则取第一个可用定义;没有则 None。"""
        if "file-worker" in dispatcher.agent_defs:
            return "file-worker"
        return next(iter(sorted(dispatcher.agent_defs)), None)

    def process_document(project_id: int, path: str, instructions: str = "") -> str:
        worker = _pick_worker()
        if worker is None:
            return "Error: 没有可用的子 agent 定义"
        task = (f"处理项目 {project_id} 中的文件「{path}」。"
                + (instructions.strip() or "通读全文并给出中文要点摘要。"))
        return dispatcher.start(
            parent_session_id=parent_session_id, agent_name=worker, task=task,
            context=f"目标文件在项目 {project_id} 内,相对路径:{path}",
            registry=registry, complete_factory=complete_factory,
        )

    def process_batch(project_id: int, paths: list[str], instructions: str = "") -> str:
        worker = _pick_worker()
        if worker is None:
            return "Error: 没有可用的子 agent 定义"
        if not paths:
            return "Error: paths 不能为空"
        results = [process_document(project_id, p, instructions) for p in paths[:20]]
        note = "\n".join(f"- {p}:{r.split('。')[0]}。" for p, r in zip(paths[:20], results))
        return (f"已并行派发 {len(results)} 个子任务(每个文件一个子 agent):\n{note}\n"
                "全部派发完毕,用 wait_task 收集结果后汇总。")

    return [
        ToolDef(
            name="start_task",
            description=(
                "派发一个子 agent 在后台并行执行独立子任务。子 agent 看不到本次对话,"
                "task 必须自包含。可用子 agent:" + agent_enum
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent": {"type": "string",
                              "description": f"子 agent 名称,可选:{agent_enum}"},
                    "task": {"type": "string",
                             "description": "自包含的任务描述:目标、涉及的项目/文件、期望产出"},
                    "context": {"type": "string",
                                "description": "可选的背景信息(主对话中已知的关键事实)"},
                },
                "required": ["agent", "task"],
            },
            handler=start_task,
            permission="ask",
            # 需求 7:派发类工具禁止「总是允许」(在 ALWAYS_ALLOW_SUPPRESSED 中)
            always_allow_suppressed=True,
        ),
        ToolDef(
            name="wait_task",
            description=(
                "等待子 agent 任务完成并取回结果。不带参数等待当前派发的全部任务;"
                "超时未完成的任务会标注,可稍后再次调用收取。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_ids": {"type": "array", "items": {"type": "integer"},
                                 "description": "可选,只等指定任务 ID"},
                    "timeout_sec": {"type": "number",
                                    "description": f"最长等待秒数,默认 {DEFAULT_WAIT_TIMEOUT:.0f}"},
                },
            },
            handler=wait_task,
            permission="allow",
        ),
        ToolDef(
            name="process_document",
            description=(
                "处理单个文档(自动派发子 agent 在后台执行,等价于预置好任务的 start_task)。"
                "适合「读这份 PDF/DOCX 并总结」类指令;批量文件请用 process_batch。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "path": {"type": "string", "description": "项目内相对路径"},
                    "instructions": {"type": "string",
                                     "description": "可选处理要求;默认「通读并中文要点摘要」"},
                },
                "required": ["project_id", "path"],
            },
            handler=process_document,
            permission="ask",
            always_allow_suppressed=True,  # 派发类同 start_task(需求 7)
        ),
        ToolDef(
            name="process_batch",
            description=(
                "批量处理多个文档:每个文件并行派发一个子 agent,全部后台执行。"
                "派发后用 wait_task 收集各文件结果再汇总。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "paths": {"type": "array", "items": {"type": "string"},
                              "description": "项目内相对路径列表(最多 20 个)"},
                    "instructions": {"type": "string",
                                     "description": "可选处理要求,应用到每个文件"},
                },
                "required": ["project_id", "paths"],
            },
            handler=process_batch,
            permission="ask",
            always_allow_suppressed=True,  # 派发类同 start_task(需求 7)
        ),
    ]
