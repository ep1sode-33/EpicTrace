# EpicTrace Plan 6 设计:工具调用 ReAct Agent(agentic RAG via tools)

> 把对话从"写死的 route→retrieve→grade→rewrite 流水线"升级为 **tool-calling ReAct agent**:检索做成 LLM 可调用的工具,agent 自己决定调哪个、调几次。替掉 Plan 3 的硬 route 节点 + Plan 5 的"有外部附件就压制项目 RAG"启发式——二者都变成"工具描述 + 条件暴露"涌现出来的行为。对齐 CLAUDE.md 写明的 "LangGraph(ReAct + Reflection)" 愿景。
> 动手前必读:Plan 3 的检索/引用管线(`agent/graph.py`、`agent/prompts.py`、`agent/citations.py`、`OpenAICompatLLM`、`build_citations`)、Plan 5 设计 `docs/superpowers/specs/2026-06-12-epictrace-plan-5-ephemeral-attachment-rag-design.md`(`AttachmentRetriever`、附件临时库、`RetrievedChunk` 复用)。
> 取舍见 `docs/decisions/2026-06-11-attachment-phase-plan-and-tech-choices.md`(本期记 D16+:方案 B 合体、探测缓存 gate、三工具含分页通读、引用复用 build_citations)。

## 1. 背景与目标

Plan 3 的对话路由是写死的:LLM 只能在 `route`(retrieve/direct)里二选一,顺序固定。Plan 4/5 为接附件,加了"本轮有外部附件 → 压制全局项目 RAG"的硬启发式。实测暴露两个问题:① 路由僵硬,模型无法"既查项目库又查附件";② 压制启发式粗暴(要么全压、要么不压),用户明确要的是"**全都要,agent 自己决定调什么工具**"、像 GPT 网页版那样干净的文件问答。

**目标**:把三处检索封装成 LLM 可调用工具,agent 在一个 ReAct 循环里自主决定调用组合与次数;模型"觉得不够再换 query 搜一次"本身就是**反思**,于是 agent 路**砍掉显式 grade/rewrite**。

**BYOK 现实**:工具调用支持度随模型差异很大(DeepSeek ~81%、Qwen ~96%、本地 Ollama 经 `/v1` 最差,常吐坏 JSON / >3 工具就乱)。因此**按 profile 做能力探测 + 缓存**,不支持就**回退到现有 Plan 5 流水线**(现有管线不删,降级当兜底)。

## 2. MVP 边界

**做**
- 三个工具:`search_project_library`、`search_attachment`(仅会话有附件时暴露)、`read_attachment`(分页通读,仅附件存在时暴露)。
- 自建 LangGraph `StateGraph`:工具循环节点用 LangChain `ChatOpenAI(base_url=…).bind_tools(...)` + LangGraph `ToolNode`;循环只负责**攒 chunk 进累积池**。
- 循环结束 → **复用现有 GENERATE 节点 + `build_citations`** 写带 `[n]` 的答案(引用命门零改动)。
- 按 profile 能力探测(发极小 tool_call 测试)+ 进程内缓存,决定走 agent 路还是回退路。
- 鲁棒性:循环轮数上限(≈8,撞到→用已搜 chunk force-answer)、工具调用坏了重试 1 次(再坏→回退/force-answer)、外层 try/except 安全带。

**不做(本期)**
- `read_full_attachment` 一次性吐全文(用**分页** `read_attachment` 替代)。
- 大附件整篇 map-reduce 摘要(分页通读是 best-effort;超大文档撞轮数上限即覆盖不全——已知边界)。
- 把 profile 能力持久化进 DB(v1 进程内缓存,重启再探一次即可)。
- 前端大改(对话仍是同一条 SSE + 同一套引用/SourceViewer;探测徽标可选)。
- 图片/视觉工具;把附件写进项目永久库(**绝不**,沿用 Plan 5)。

## 3. 核心原则锚定
- **引用是一等公民 / 命门复用**:agent 路最终答案由**独立 GENERATE** 写,不是循环里模型自写 → Pattern A 的"稳定 id→偏移表"塌缩进现有 `[n]→chunk→偏移` 机制,`build_citations` 字节不动地复用,幻觉 `[n]` 照旧丢弃。
- **本地优先 + BYOK 不一刀切**:能力探测 + 回退,保证本地小模型用户拿到稳的基础检索而非"半坏的 agent"。
- **临时 ≠ 入库**:`search_attachment`/`read_attachment` 只读 Plan 5 的会话级临时库 + 缓存 `extracted_text`,随附件/对话清理,项目永久库不受影响。
- **macOS gRPC-fork 段错误**:所有 store/embedder 走既有"先暖 embedder+reranker 再起 Milvus 客户端"路径([[macos-embedding-milvus-fork-order]]),不新增 fork 风险点。
- **接口缝不返工**:工具是对现有 `retriever`/`AttachmentRetriever`/`extracted_text` 的薄封装;Plan 7 的 MinerU 插进 `MediaProcessor` 后,工具检索质量自动变好。

## 4. 架构:两条路 + 探测 gate

```
提问 → ChatService 选路
        │
        ├─[profile 探测=支持工具]→ Agent 路(本期新增)
        │     StateGraph: 循环{ ChatOpenAI.bind_tools → ToolNode } → 攒池 → GENERATE → build_citations
        │     (撞 8 轮 / 工具坏 → force-answer 用已搜池;第一轮就崩 → 回退路)
        │
        └─[profile 探测=不支持]→ 回退路 = 现有 Plan 5 流水线(route→grade→rewrite + 附件合并 + 压制启发式),原样不动
```

## 5. 组件

### 5.1 能力探测(`deps` 旁)
- `probe_tool_calling(profile) -> bool`:用该 profile 构造 `ChatOpenAI(base_url, api_key, model)`,`bind_tools([trivial echo 工具])`,系统让它调一下,检查回包含**结构合法的 tool_call**。合法 → True。异常/吐人话/坏 JSON → False。
- 结果缓存到 `app.state`,按 profile id(+ base_url + model)键。重启重探(一次调用,可接到设置页「测试连接」顺手显示"✓ 支持工具调用 / ✗ 将用基础检索",前端徽标可选)。
- 代价:某 profile 首条消息多一次极小探测往返(一次性)。

### 5.2 工具集(薄封装,路由写进描述,只描述领域、不硬性互斥)
- `search_project_library(query)`:dense+sparse→RRF→rerank 查项目永久库(复用现有 `retriever`);若本轮有 `focus` 引用(大内部文件)→ 自动 `ingest_record_id IN {…}` 圈范围。**替 Plan 3 route + Plan 4 focus**。
- `search_attachment(query)`:复用 Plan 5 `AttachmentRetriever.retrieve(conversation_id, reference_ids, query)`,按本会话活跃 `indexed` 外部引用过滤。**仅会话有此类附件时暴露**(替 Plan 5 压制启发式:不暴露=agent 看不到,而非硬切)。
- `read_attachment(reference_id, cursor)`:对缓存 `extracted_text` 顺序切片返回下一段 + 下一 cursor(无需 embedding)。供"覆盖整篇/总结/检索片段不足"用,反复调用翻页。**仅附件存在时暴露**。
- 每次工具返回**两份**:给模型读的**截断文本**(供决策);带偏移的 `RetrievedChunk` 对象进**累积池**(经 LangChain tool artifact / graph state 捕获,不污染模型可见文本)。`read_attachment` 切片也封成 chunk(`char_start=cursor`、`char_end=cursor+len`、`reference_id`、`source_kind="attachment"`)。

### 5.3 Agent StateGraph
- 节点:`agent`(`ChatOpenAI.bind_tools` 决定工具/停手)→ 条件边 →`tools`(`ToolNode` 执行,结果与 chunk 回灌)→ 回 `agent`,循环。模型**不再调工具**(回普通消息)→ 退出循环。
- **循环只攒池**:跨轮累积 chunk,用现有 `RetrievedChunk.key()`(含 `reference_id`)去重,封顶(≤12 段)。
- 初始消息:沿用 Plan 5 "点名本会话附加的文件(…不要说未收到文件)";**小全文(`fulltext`)引用/附件直接注入初始上下文**(让 agent 知道有它、避免无谓搜索),且其 chunk 一并进累积池(保持可引用),镜像今天"自动注入 + 可引用"行为。
- 轮数上限 ≈8:撞到 → 停搜,拿已攒池 force-answer。

### 5.4 失败/回退处理
- 某轮 tool_call 结构坏 → 重试 1 次。再坏:**已攒到 chunk → force-answer**;**第一轮就崩、池空 → 回退 Plan 5 流水线**。
- 外层 try/except 兜任何其它意外 → 回退 Plan 5。

### 5.5 最终 GENERATE + 引用(复用,零新代码)
- 循环结束 → **干净的新 GENERATE 调用**:`GENERATE_SYS` + 用户问题 + 累积池编号 【资料】[1..k](**丢弃循环对话历史**;答案靠证据=chunk,GENERATE 全拿到)。
- `build_citations(answer, 池)` 照旧产出引用(`source_kind`/`reference_id`/偏移),幻觉 `[n]` 丢弃。**因为用户只看 GENERATE 输出 → "别报工具名"自动满足**。
- **池空(寒暄轮)→ 走现有 direct 直接作答**,不硬套"据资料"框架。

### 5.6 ChatService 接入
- `_run_turn`:取 profile → `probe_tool_calling`(查缓存)。支持 → 跑 agent 路;不支持 → 现有 Plan 5 路径原样。流式 status 事件复用现有通道,反映工具活动("检索项目库…""读取附件…")。

### 5.7 提示词
- **循环提示(管搜集)**:"你是检索助手,用工具搜集回答所需资料,可一次并行调多个工具;资料够了就停止调用工具;纯寒暄/无需资料的问题不必调工具。"(其文本输出丢弃)
- **GENERATE_SYS(管作答)**:复用现有,从 【资料】 写带 `[n]` 答案,不提工具。

## 6. 数据流
1. **挂大 PDF + 提问**:ChatService 探测=支持 → agent 路。模型一轮并行 `search_attachment('TLB')` + `search_project_library('虚拟内存')` → 两份 chunk 进池,文本回灌 → 模型觉得够 → 停手 → GENERATE 编号写答 `[1][2]` → `build_citations` → SSE。点 `[1]`(attachment)按 `reference_id` + 偏移精确跳回。
2. **"总结这个文件"**:模型反复 `read_attachment(rid, cursor)` 翻页 → 切片进池 → 够了或撞 8 轮 → GENERATE 总结(撞上限=覆盖不全,best-effort)。
3. **寒暄"你好"**:模型不调工具 → 池空 → direct 直接答。
4. **本地模型(探测=不支持)**:走 Plan 5 流水线,行为同今天。

## 7. 数据模型
- **无新 SQL 表**。profile 能力 = 进程内 `app.state` 缓存(可选 future 持久化)。
- 复用 Plan 5 `attachment_chunks` 临时集合 + `ConversationReference.extracted_text` 缓存(`read_attachment` 的偏移基准)。

## 8. 接口/契约变更
- **新依赖** `langchain-openai`(`ChatOpenAI`,接 OpenAI 兼容端点);`langgraph` 的 `ToolNode`/预制件(已在依赖内)。
- `OpenAICompatLLM` **不变**(回退路 + 最终 GENERATE 流式仍用它);agent 路新增 `ChatOpenAI` 层 → 同一 profile 构造两个模型对象(可接受的小重复)。
- 新增 `agent/tools.py`(三工具封装 + artifact 捕获)、`agent/react.py`(StateGraph + 循环/上限/重试/force-answer)、`deps.probe_tool_calling` + 缓存。
- `ChatService._run_turn` 增选路;`AttachmentRetriever` 复用;新增 `read_attachment` 分页读(切 `extracted_text`)。
- 前端:基本不动(同 SSE + 引用 + SourceViewer);探测徽标可选小增。

## 9. 错误处理与边界
- 探测失败/超时 → 视为不支持 → 回退。
- 轮数上限 → force-answer(已搜池);工具坏 → 重试 1 → force-answer 或回退(见 5.4)。
- 池空 → direct。
- 大附件整篇总结 = best-effort(分页 + 上限),明确告知是已知边界。
- 探测给首条消息加一次往返延迟(一次性)。

## 10. 测试策略(后端 TDD,Fakes 为主)
- **探测**:`FakeLLM` 会/不会吐合法 tool_call → `probe_tool_calling` 返回 True/False + 缓存命中。
- **工具封装**:`search_*`/`read_attachment` 返回 chunk + 落池;`read_attachment` 切片偏移正确;条件暴露(无附件时不含附件工具)。
- **agent 循环**:多轮、并行调用、撞 8 轮 force-answer、跨轮去重封顶。
- **引用**:`build_citations` 复用 → `[n]`→偏移;attachment/分页切片精确偏移;幻觉 `[n]` 丢弃。
- **选路/回退**:探测=不支持 → 走现有流水线,行为同 Plan 5(复用 Plan 5 既有断言)。
- **重试**:坏 tool_call → 重试 → 池非空 force-answer / 池空回退。
- 真模型 slow 测试走 `EPICTRACE_RUN_SLOW=1`;前端 `npm run build`。

## 11. 明确不做(重申)
`read_full_attachment` 一次性全文、map-reduce 整篇摘要、profile 能力持久化进 DB、前端大改、图片/视觉工具、附件入永久库。
