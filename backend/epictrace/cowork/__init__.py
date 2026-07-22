"""EpicTrace × Cowork:本地多 agent 工作台引擎(唯一对话栈;旧 epictrace.agent 检索问答
流水线已随旧对话栈一并删除)。本包提供:
- tools/:统一工具注册表(内置工具 + 后续 MCP 扩展)
- prompts/:分节 system prompt 模板系统
- sessions.py:多类型 agent session 生命周期管理
- loop.py:多轮 agent 循环(思考 → 工具调用 → 观察 → 继续)
- service.py:面向 API 层的编排(SSE 事件流)
"""
