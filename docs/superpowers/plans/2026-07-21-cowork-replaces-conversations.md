# 迁移计划:用 Cowork 栈替换「项目与对话」(RAG + 引用链下沉,旧栈删除)

> 用户决策:不保留旧对话栈(ChatService + agent/),「项目与对话」tab 名称与产品形态不变,
> 但底层全部换成 cowork 的 AgentLoop + 分节 prompt + 工具注册表 + 权限模型。
> 姊妹文档:`2026-07-21-cowork-redesign-phases.md`(Phase 1-7,cowork 能力建设)。

## 设计总览

**核心思路:一个 agent 引擎,两个前端视图。**
- `AgentSession.project_id`(可空 FK):绑项目的 session 出现在「项目与对话」的项目树下;无绑定的出现在「Cowork」tab(`?project_id=` / `?free_only=true` 过滤)。
- 引用链下沉:检索工具结果在 **turn 级 chunk 池全局递增编号** → LLM 答案的 [n] 全 turn 唯一 → `build_citations` 映射 → `citations` SSE 事件 + `citations_json` 落库,与旧管线同一契约。
- 检索质量靠 prompt 纪律(workflow 节:先取证、不足改写重试 ≤2、资料不含答案自然拒答不带引用),不移植旧 graph 的 route/grade/rewrite。
- 附件/references 体系(ReferenceService / AttachmentStore / AttachmentRetriever)与 agent 编排无关,整体保留,绑定键 conversation_id → session_id(向量元数据键同步换 session_id;旧向量成孤儿,extracted_text 仍在,可重建,影响可控)。
- tool_probe **不搬**:cowork 循环对不支持 tool-calling 的端点天然降级为无工具回答(有意取舍,不做 409 阻断)。
- 流式 token:旧聊天逐字流,cowork 一次性返回(AgentLoop 工具语义需要完整响应;真流式化是后续可选增强)。

---

## Phase A:引用链下沉 ✅(2026-07-21)

- [x] A1 turn 级 pool + 全局编号(registry/loop 增 `exec_ctx`/`wants_ctx` 透传)
- [x] A2 `citations` 事件 + `AgentMessage.citations_json` 落库/回放(+ db.py ALTER)
- [x] A3 前端 AssistantMarkdown + SourceViewer 接入 CoworkView
- [x] A4 子 agent pool 隔离
- Test:`test_cowork_citations.py`(5)

## Phase B:消息操作 + 自动标题 ✅(2026-07-21)

- [x] B1 `stream_edit`/`stream_regenerate`/`_delete_messages_after`/首轮自动标题(`session_renamed` 事件)+ 两个 SSE 端点
- [x] B2 前端行内编辑 + hover 编辑/重生成(api.ts `_coworkStream` 公共化)
- Test:`test_cowork_edit_regenerate.py`(6)

## Phase C:附件 / references 绑 session ✅(2026-07-21)

- [x] C1 新 `Reference` 模型(session_id FK agent_sessions.id);服务/路由/检索换绑;`AgentSession.project_id`(+ db.py ALTER)
- [x] C2 `builtin_attachments.py`(search_attachment / read_attachment 分页 + attachment_manifest 注入 prompt 新 attachments 节)
- [x] C3 前端引用侧栏(ReferencePanel)+ pickFiles 挂附件;api.ts references 指向 cowork 路径
- Test:`test_cowork_attachments.py`(10)

## Phase D:project_id + 数据迁移 + 删旧栈(进行中)

- [x] D1 模型与过滤:`AgentSession.project_id`、`SessionManager.list(project_id/free_only)`、session 创建/改名端点
- [x] D2 数据迁移 `db_migrate.py`:conversations→agent_sessions、messages→agent_messages(带 citations_json)、conversation_references→references;迁移前 db 文件复制 `.premigrate.bak`;幂等;附件旧向量不迁(见上)
- [x] D3 删旧栈:`agent/` 整目录、`services/chat.py`、`routers/conversations.py`、Conversation/Message/ConversationReference 模型、langgraph/langchain-openai 依赖、26 个旧测试文件;`scripts/rag_eval` 生成段同步清退。补:cowork 删会话清理附件向量(对齐旧语义,无引用不走重路径)。**后端全量 805 passed / 6 skipped**
- [x] D4 前端「项目与对话」切 cowork sessions:`components/CoworkConversation.tsx` 共享组件(CoworkView/ProjectsConversationView 共用);树内会话=project 绑定 session;库内文件内部引用 + 原生拖放挂附件已移植;旧 API/组件(MessageList/Composer/ActivityTimeline/ThinkingBlock + conversations 系 api.ts 函数)已清理;`session_renamed` 接线同步树标题。**迁移已在真实库执行:22 会话 + 51 消息,备份 .premigrate.bak;后端 807 passed / 6 skipped;前端 tsc/eslint/build 全过;截图冒烟(项目会话引用链 [n] 可点)通过**

## Phase E:prompt 检索纪律强化 ✅(2026-07-21)

- [x] E1 workflow 节:检索纪律(改写重试 ≤2 / 无据拒答)+ 引用规范([n] 只用出现过的编号,闲聊不引用)

## Phase F:能力并入「项目与对话」,撤掉 Cowork tab ✅(2026-07-21)

> 用户澄清:不设独立 Cowork 页,Cowork 能力全部塞进「项目与对话」并适配。

- [x] F1 删 Cowork tab(TopBar/App/CoworkView.tsx);自由会话概念从前端移除(后端 `free_only` 过滤保留无害)
- [x] F2 会话头权限模式切换(手动确认/自动执行;切 skip_all 弹 Cowork「Bypass Permissions」式警示;PATCH /cowork/sessions/{sid} 支持 permission_mode;`follow_a_plan` 行为等同 ask 不暴露)
- [x] F3 项目树嵌套子 agent(缩进 └ 行 + 父行「子任务 N/M」);dispatch_child 继承父会话 project_id
- [x] F4 设置页 Agent 区(默认权限模式/轮数/超时/自定义指令/沙箱内存/CPU/网络)
- 测试:809 passed / 6 skipped;前端 tsc/eslint/build + 截图冒烟(tab 三个/切换器/Agent 区)全过

## 验收

1. 「项目与对话」发问 → cowork 循环 → 答案带 [n],点击跳来源/时间戳
2. 编辑/重生成/自动标题/挂附件/引用面板全部可用(与旧体验对齐)
3. `agent/`、`services/chat.py`、`routers/conversations.py` 不存在;全量回归绿
4. 旧对话数据迁移后可见、引用保留;db 有 .bak
5. ~~「Cowork」tab 仅自由会话~~(Phase F 已撤 tab);项目会话仅在「项目与对话」对应项目下;子 agent 嵌套于父会话
