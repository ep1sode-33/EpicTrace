# EpicTrace Plan 4 设计:现代对话体验 + 对话引用(外部附件 + 内部文件)

> 本期("Plan 4")是 Plan 3 引用式 Agentic RAG 对话之上的下一步。
> 动手前必读:`docs/superpowers/specs/2026-06-09-epictrace-product-requirements.md`、同目录技术栈文档、`docs/superpowers/specs/2026-06-11-epictrace-plan3-agentic-rag-chat-design.md`(本期复用其检索/引用/SSE/ChatService 基建)。
> 决策取舍与备选见 `docs/decisions/2026-06-11-attachment-phase-plan-4nd-tech-choices.md`(本地、不进 git)。

## 1. 背景与目标

Plan 3 让对话能基于**项目永久索引**做带引用的问答。但用户经常想"临时丢一两个文件进来一起聊",或"这轮聚焦项目里某几个文件"。Plan 4 补上这块,并把对话外壳打磨到现代桌面 LLM 的手感。

**一句话目标**:让用户在对话里**引用文件**(外部拖进来的临时文件 + 项目内已索引的文件),引用可跳回原文;**外部附件默认不入库、且 Plan 4 完全不对其做向量化**;对话 UI 具备附件、拖拽/粘贴、流式消抖、空状态等现代手感。

## 2. MVP 边界

**做**
- 「本对话引用」折叠面板,两栏:**外部文件**(从硬盘拖/选)、**内部文件**(从项目挑)。两类都可**解挂**。
- 加文件 → **按当前模型上下文窗口动态分流**:放得进预算 → 全文进上下文;放不下 → 见下。
- **外部大文件**(放不进预算)→ 本期不硬塞,**明确提示、留给 Plan 5**(子 agent 提炼)。
- **内部大文件** → 复用项目**现成向量**,把项目 RAG **聚焦**到 pin 的文件(不新增向量化)。
- 内容**对话级存活**:挂过之后下轮不挂仍可追问。
- 引用 `[n]` 覆盖内部(永久偏移)与外部全文(跳回外部文件),都能跳回原文。
- UI 打磨:附件 chips + 拖拽 + 粘贴;流式 markdown 缓冲(代码块闭合再渲染,消抖);空状态建议提示。

**不做(本期)**
- **外部文件的任何向量化 / scratch 向量索引**(外部只走全文;大外部→ Plan 5)。
- 子 agent 提炼(Plan 5)、MinerU 高质量提取(Plan 6)。
- **图片 / 视觉**(独立 vision/caption 链路,后续;非 Plan 6)。
- 入库(写进项目永久索引)——仅用户明确要求才发生,属"项目入库"能力(后续)。
- 常驻(非折叠)文件面板、多模型切换面板、消息分支树、键盘快捷键(YAGNI)。

## 3. 核心原则锚定

- **本地优先 + opt-in / 不监控**:附件是用户主动挂的临时引用。
- **Agent 提议、用户确认、系统提交**:挂/解挂、入库与否都由用户控。
- **外部附件不入库、不向量化**:外部文件不写进项目永久 `chunks`,Plan 4 也**不**给它建任何向量索引;事实来源 = 原文件 + 缓存的提取文本(可弃)。
- **接口缝不返工**:提取一律走 `MediaProcessor` 接口。本期实现仍是 pypdf/docx/pptx/text;Plan 6 把 MinerU 插到同一条缝后面后,**附件与索引一起升级,Plan 4 不改一行**。
- **时间戳/偏移是一等公民**:引用保住 `字符偏移 → 来源` 对齐(内部走永久索引偏移,外部全文走缓存文本偏移),跳回原文。

## 4. 架构总览:来源路 + 接口缝

每轮回答的【资料】可能来自三条路:

| 路 | 来源 | 进上下文方式 | 引用跳回 | 是否新增向量化 |
|---|---|---|---|---|
| (a) 项目全局 RAG(Plan 3 已有) | 项目永久 `chunks` | `route` 决定 retrieve / direct | 永久 ingest_record | 否(项目索引时已建) |
| (b) 全文注入 | 放得进预算的外部 / 内部文件 | 整段提取文本进【资料】 | 外部→缓存文本偏移;内部→永久偏移 | **否** |
| (c) 内部聚焦检索 | pin 的**大**内部文件 | 项目 RAG 加 `ingest_record_id IN {…}` 过滤 | 永久 ingest_record | 否(复用现成向量) |

外部大文件 = **不在 Plan 4 的任何一条路**里;记录为"待 Plan 5",面板提示。接口缝:(b) 的提取经 `MediaProcessor`;(c) 经现有 `EmbeddingProvider`/`HybridRetriever`(只查询、不新增向量)。

## 5. 组件

### 5.1 size-gate:按上下文窗口动态算预算
- LLM Profile 增 `context_window`(int,设置里可填,保守默认如 32768)。
- **全文预算** = `context_window − 预留`;预留 = 系统提示 + 历史 + 项目 RAG 配额 + 答案头寸(各取保守常数)。
- token 用**字符保守估算**(中英不同系数),不接各模型分词器。
- 判定:一个引用文件的提取文本 + 已注入的全文引用之和 ≤ 预算 → `fulltext`;否则:外部→`deferred`(留给 B)、内部→`focus`(走 (c))。

### 5.2 后端:`ReferenceService`(新)
管理一个对话的引用集合(CRUD + 提取 + size-gate + 解挂)。
- `add_external(conversation_id, path)`:`get_processor(path).process()` 提取并**缓存文本进 DB**;按预算定 `mode`(`fulltext` | `deferred`)。`deferred` 仅登记 + 提示,**不提取进上下文、不建索引**。
- `add_internal(conversation_id, ingest_record_id)`:复用项目已索引文件;按其文本大小定 `mode`(`fulltext`:`SourceService` 风格再提取整段;`focus`:仅记录"聚焦"该 `ingest_record_id`,复用现成向量)。
- `detach(reference_id)`:软删(`detached=True`)。**无 scratch 向量需清理**。
- `list_active(conversation_id)`:未解挂的引用,供 ChatService 组装上下文。

### 5.3 后端:内部聚焦过滤
有 `focus` 内部引用时,项目 RAG 的 dense/sparse 两路检索都加 `ingest_record_id IN {pinned}`(扩展 `VectorStore.query` 支持 IN 表达式)。没 `focus` 内部引用 → 全项目 RAG 照旧。

### 5.4 后端:`ChatService` 改造(组装上下文)
`_run_turn` 在 RAG 图前后插入引用处理:
- 取 `ReferenceService.list_active`。
- **有活跃引用 → 本轮一定带资料**:`fulltext` 引用(外部+内部)整段进【资料】;`focus` 内部引用使项目 RAG 聚焦;`deferred` 外部引用**不进资料**(仅前端提示)。无引用 → Plan 3 的 `route`(direct/retrieve)行为完全不变。
- 三路合成统一 `RetrievedChunk` 列表喂 `GENERATE_SYS`/`format_chunks`。
- **为 Plan 5 留口**:`deferred` 外部引用就是 Plan 5 子 agent 抽取式提炼的输入;接口不变。

### 5.5 引用与来源跳回
- `RetrievedChunk` 与引用 payload 增 `source_kind`(`project` | `attachment`)与可选 `reference_id`。
- 内部 = `project`,走现有 ingest_record → 路径 → 永久偏移高亮(不变)。
- 外部全文 = `attachment`:把整段外部文本作为一个 chunk(`char_start=0..len`,`reference_id` 指向 `conversation_reference`);`SourceService` 按 `reference_id → source_path`(或缓存 `extracted_text`)解析,前端来源查看器**复用现有按码点高亮 + 在 Finder 显示**,仅多一条"附件来源"解析分支。
- **引用粒度的已知取舍**:全文注入的引用(外部全文 / 内部全文)其 `[n]` 是**文件级**(跳回整篇,高亮整段),不如项目 RAG 的 chunk 级精确。这是 Plan 4 的已知限制——**精确到片段的逐字摘录引用由 Plan 5(抽取式子 agent)补齐**。`focus` 内部引用与项目全局 RAG 仍是 chunk 级精确偏移。

### 5.6 前端:引用面板 + Composer
- 折叠条 `本对话引用 (n) ▾`;展开两栏(外部 / 内部);每条:图标 + 名 + **模式 chip**(`全文已载入` / `已索引聚焦` / `待 Plan 5(文件较大)`)+ `×` 解挂。
- 外部入口:拖拽到对话区 + 粘贴 + `+`(`pickFile`,扩展为多选);内部入口:`+ 从项目添加`(项目文件选择器)或 composer `@文件名` 自动补全。
- 现代手感:流式 markdown 缓冲(代码块未闭合不渲染,消抖)、空状态建议提示。
- 流式 SSE / 编辑/重试/复制 / IME 防误发 / Stop / markdown / 引用 chip→查看器 **沿用 Plan 3**。

## 6. 数据流(加文件 → 提问 → 引用)
1. 用户拖入 `报告.pdf`(或 `@` 选内部 `notes.md`)→ `ReferenceService` 提取、缓存、按预算定 mode → 面板出现条目 + 模式 chip(放得下=全文 / 太大=待 B / 内部大=聚焦)。
2. 用户提问 → `POST /conversations/{cid}/messages`(body 不必带附件:引用是会话级,后端 `list_active` 自取)。
3. `ChatService` 组装【资料】(全文引用 + 聚焦/全局 RAG)→ 流式生成带 `[n]` 的回答 → `build_citations`(含 `source_kind`/`reference_id`)。
4. 用户点 `[n]` → 来源查看器:`project` 跳回项目文件、`attachment` 跳回外部文件,均高亮偏移。
5. 解挂 → 该引用退出后续轮次的【资料】(无向量需清理)。

## 7. 数据模型
新表 `conversation_reference`:
- `id` PK;`conversation_id` FK(CASCADE);`kind`(`external`|`internal`);`display_name`
- 外部:`source_path`、`extracted_text`(缓存)、`text_chars`
- 内部:`ingest_record_id` FK
- `mode`(`fulltext`|`focus`|`deferred`);`detached`(bool, 默认 False);`created_at`

`Message` 不变(引用是会话级)。**无 scratch 向量 collection**(本期不向量化外部文件)。
LLM Profile(`SettingsService`)增 `context_window`(int)。

## 8. 接口 / 契约变更
- 新 REST:`POST/GET/DELETE /api/conversations/{cid}/references`(增/列/解挂);`GET /api/projects/{pid}/files`(内部文件选择器,若无现成)。
- `SourceService.get_text` 增 `attachment` 分支(按 reference 解析)。
- `RetrievedChunk` + 引用 JSON 增 `source_kind`、`reference_id`。
- `VectorStore.query` filter 支持 `IN`(内部聚焦)。**不新增** attachment 向量存储。
- `SettingsService` profile 增 `context_window`;设置面板加该字段。
- 前端 `lib/api.ts` 增 references CRUD;`Composer`/`MessageList`/`ProjectsConversationView` 增引用面板与状态。

## 9. 错误处理与边界
- 提取失败(损坏/不支持后缀)→ 面板该条标错误,不阻塞对话;`get_processor` 返回 None → 友好提示。
- 外部文件在提问时已被移动/删除 → 用缓存 `extracted_text`;跳回时提示"原文件已不在原位"。
- 预算边界:多个全文引用累加超预算时,后挂的转 `deferred`/`focus` 并提示;空文本文件拒绝挂。
- `context_window` 未填 → 用保守默认;填得过大导致实际 400 → 把错误透传给用户(Plan 3 的 error 事件)。
- 解挂正在被引用的文件 → 仅影响后续轮次,已生成回答的引用快照不变。
- LLM 未配置 → 沿用 Plan 3 的 409。

## 10. 测试策略
- 单元:size-gate(按 `context_window` 算预算:fits→fulltext / 外部超→deferred / 内部超→focus;多文件累加)、`ReferenceService` 增/列/解挂、内部聚焦过滤(IN)、上下文组装(全文+聚焦+全局)、引用 `source_kind`(内部 project / 外部 attachment)、外部提取缓存(原文件移动后仍可用)。
- 引用偏移正确性:内部永久偏移、外部全文缓存偏移各一条端到端。
- 沿用 `FakeLLM`/`FakeEmbedder`/`FakeReranker`/`FakeVectorStore`;真实模型走 `EPICTRACE_RUN_SLOW=1`。
- 前端 `npm run build` 通过。

## 11. 明确不做(重申)
外部文件向量化 / scratch 索引、子 agent 提炼(B)、MinerU(C)、图片/视觉、入库、常驻面板、多模型面板、消息分支树、键盘快捷键。
