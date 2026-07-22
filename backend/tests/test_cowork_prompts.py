"""system prompt 分节与组装单测(需求 2 / 验收 2)。"""

from epictrace.cowork.prompts.assemble import assemble_system_prompt
from epictrace.cowork.prompts.sections import SectionContext
from epictrace.cowork.tools.registry import ToolDef


def _tool(name):
    return ToolDef(name=name, description=f"{name} 描述", parameters={}, handler=lambda: "")


def test_agent_layout_contains_core_sections():
    ctx = SectionContext(session_type="agent", tools=[_tool("search_hybrid")], task="整理资料")
    p = assemble_system_prompt(ctx)
    for marker in ("# 身份", "# 运行环境", "# 可用工具", "# 安全规则", "# 权限模式",
                   "# 工作流程", "# 当前任务"):
        assert marker in p, marker
    assert "search_hybrid" in p and "search_hybrid 描述" in p
    assert "整理资料" in p


def test_chat_layout_has_no_tools_or_safety():
    ctx = SectionContext(session_type="chat", tools=[_tool("search_hybrid")])
    p = assemble_system_prompt(ctx)
    assert "# 可用工具" not in p
    assert "# 安全规则" not in p
    assert "# 身份" in p


def test_tools_section_omitted_when_no_tools():
    ctx = SectionContext(session_type="agent", tools=[])
    assert "# 可用工具" not in assemble_system_prompt(ctx)


def test_user_instructions_conditional():
    base = SectionContext(session_type="agent")
    assert "用户自定义指令" not in assemble_system_prompt(base)
    ctx = SectionContext(session_type="agent", user_instructions="  总是用中文回答  ")
    p = assemble_system_prompt(ctx)
    assert "# 用户自定义指令" in p and "总是用中文回答" in p


def test_admin_instructions_conditional_and_label():
    ctx = SectionContext(session_type="agent", admin_instructions="禁止外发数据")
    p = assemble_system_prompt(ctx)
    assert "组织管理指令" in p and "禁止外发数据" in p


def test_dispatch_section_only_when_supported():
    ctx = SectionContext(session_type="agent", supports_dispatch=False)
    assert "# 子 agent 派发" not in assemble_system_prompt(ctx)
    ctx.supports_dispatch = True
    assert "# 子 agent 派发" in assemble_system_prompt(ctx)


def test_dispatch_child_identity_and_task():
    ctx = SectionContext(session_type="dispatch_child", agent_name="pdf-processor",
                         task="提取 a.pdf 文本")
    p = assemble_system_prompt(ctx)
    assert "pdf-processor" in p and "提取 a.pdf 文本" in p
    assert "# 子 agent 派发" not in p  # 子 agent 不能再派发(Phase 3 前恒不渲染)


def test_scheduled_section_only_for_scheduled():
    ctx = SectionContext(session_type="scheduled", task="每晚整理")
    assert "# 定时任务" in assemble_system_prompt(ctx)
    ctx2 = SectionContext(session_type="agent")
    assert "# 定时任务" not in assemble_system_prompt(ctx2)


def test_task_default():
    ctx = SectionContext(session_type="agent")
    assert "下一条消息" in assemble_system_prompt(ctx)


def test_skills_section_injected():
    ctx = SectionContext(session_type="agent",
                         skills=[{"name": "pdf", "body": "PDF 处理指导"}])
    p = assemble_system_prompt(ctx)
    assert "# 已加载 Skills" in p and "PDF 处理指导" in p
