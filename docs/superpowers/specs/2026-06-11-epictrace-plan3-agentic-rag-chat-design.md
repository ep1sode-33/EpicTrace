# EpicTrace Plan 3:Agentic RAG + 带引用对话 设计

- **日期**:2026-06-11
- **范围**:接通「项目与对话」的对话框。问一个问题 → 在该项目的 Milvus chunks 上做**混合检索 + 重排** → **LangGraph** 智能体环(检索→反思→必要时改写重检索→生成)→ **任意 OpenAI-Compatible LLM 流式作答,答案带来源引用** → 点引用进**内置来源查看器**高亮跳回原文 → 多轮对话 + 持久化。
- **前置**:Plan 2(已 merge):Milvus `chunks`(text + char_start/char_end + ingest_record_id + project_id + source_type)、`BgeM3Embedder`(warmup-before-Milvus 纪律,见 [[macos-embedding-milvus-fork-order]])、`VectorStore.query`、`LLMProvider` 接口(`complete(messages, **kwargs)->str`,无实现)、`config.LLMRoleConfig`(base_url/api_key/model,OpenAI-compat,key 空)、对话页已是 chat-first 空壳(禁用输入)。
- **约定**:不出现前身代号;Python 3.11 venv;git 身份 `ep1sode-33`;**桌面 APP 原生思路**(正经设置面板、原生 Finder 揭示、本地优先持久化,不靠 env/手改配置文件)。

---

## 1. 关键决策(已敲定)

1. **自建检索 + LangGraph**(不上 LlamaIndex):在 Plan 2 的 Milvus/chunks 上自建检索,引用命门完全可控;LangGraph 跑 ReAct + 反思环。
2. **任意 OpenAI-Compatible LLM(BYOK)**:新建 `OpenAICompatLLM`(通用,不绑定具体厂商),只认 `base_url + api_key + model`(DeepSeek/OpenAI/Ollama/vLLM/硅基流动…通吃)。流式。
3. **混合检索**:稠密(BGE-M3 + Milvus)+ 稀疏(jieba + BM25)→ **RRF 融合** → **BGE-reranker-v2** cross-encoder 重排 → top-k。
4. **引用 = 命门**:答案 `[n]` ↔ chunk 的 `{record_id, char_start, char_end, 文件名, 片段}`;点击进**内置来源查看器**(按需重提取该文件文本、高亮 char 区间)+「在 Finder 中显示」。
5. **流式 SSE**;**对话持久化**(SQLite)。
6. **设置面板**:`GET/PUT /api/settings` + 前端齿轮面板,存 `~/.epictrace/settings.json`;AppConfig 启动加载。
7. **warmup 纪律扩展**:reranker 也是 torch 模型,加载会 fork → 必须和 embedder 一样在 Milvus(gRPC)之前 warmup(并入 `get_vector_store`)。

---

## 2. 文件结构

```
backend/epictrace/
  llm/__init__.py
  llm/openai_compat.py          # OpenAICompatLLM(落地 LLMProvider;complete + stream)
  retrieval/__init__.py
  retrieval/dense.py            # 稠密:embed query → VectorStore.query
  retrieval/sparse.py           # 稀疏:jieba + BM25(语料=项目 chunks)
  retrieval/fuse.py             # RRF 融合
  retrieval/rerank.py           # BgeReranker(FlagReranker, cross-encoder, 懒加载+warmup)
  retrieval/pipeline.py         # HybridRetriever:dense+sparse→RRF→rerank→top-k
  agent/__init__.py
  agent/graph.py                # LangGraph:retrieve→grade→(rewrite→retrieve)*→generate
  agent/state.py                # AgentState dataclass/TypedDict
  agent/prompts.py              # 检索-grade / 改写 / 带引用生成 / 反思 提示词
  agent/citations.py            # 把答案 [n] 与 chunk 元数据对齐
  services/chat.py              # ChatService:跑 graph、流式产出事件、落库
  services/source.py            # SourceService:按 record_id 重提取文本(给查看器)
  services/settings.py          # 读写 ~/.epictrace/settings.json,注入 AppConfig
  models.py                     # + Conversation, Message
  schemas.py                    # + 会话/消息/引用/设置 DTO
  interfaces/vector_store.py    # + list_by_project(取项目全部 chunk,供 BM25 语料)
  vectorstore/milvus_lite.py    # 实现 list_by_project
  api/deps.py                   # get_vector_store:warmup embedder + reranker 后再起 Milvus
  api/routers/conversations.py  # 会话 CRUD + 发消息(SSE)
  api/routers/source.py         # GET /source/{record_id}
  api/routers/settings.py       # GET/PUT /settings
frontend/src/
  lib/api.ts                    # + 会话/消息(SSE)/source/settings
  views/ProjectsConversationView.tsx  # 接通对话:流式答案 + 引用 chip + 会话历史
  components/Composer.tsx        # 输入框(启用)
  components/MessageList.tsx     # 消息流 + 引用 chip + 步骤状态
  components/SourceViewer.tsx    # 来源查看器(提取文本 + 高亮 char 区间 + Finder)
  components/SettingsModal.tsx   # 对话 LLM 设置(任意 OpenAI-Compatible)
  components/ConversationList.tsx# 侧栏会话历史
```

---

## 3. 检索流水线(`retrieval/`)

`HybridRetriever.retrieve(project_id, query, k=6) -> list[RetrievedChunk]`:
- `RetrievedChunk = {chunk_id, text, ingest_record_id, project_id, char_start, char_end, source_type, score}`。
- **稠密**(`dense.py`):`get_embedder().embed([query])` → `VectorStore.query(vec, filter={project_id}, k=topN)`(topN≈30)。
- **稀疏**(`sparse.py`):`VectorStore.list_by_project(project_id)` 取该项目全部 chunk 文本 → `jieba.lcut` 分词 → `rank_bm25.BM25Okapi` 内存建库 → 对 query 打分,取 topN。(本地中等规模够用;chunk 极多再持久化——本期内存版。)
- **融合**(`fuse.py`):RRF `score = Σ 1/(k0 + rank_i)`(k0=60),合并稠密/稀疏两路排名 → 候选 topM(≈20)。
- **重排**(`rerank.py`):`BgeReranker`(`FlagReranker("BAAI/bge-reranker-v2-m3")`,懒加载,有 `warmup()`)对 `(query, chunk.text)` 对打分 → 取最终 top-k(默认 6)。
- 接口缝:`Retriever` 用具体类即可;`BgeReranker` 落地一个轻量 `Reranker` 协议(便于测试替身)。

**新增** `VectorStore.list_by_project(project_id) -> list[dict]`:milvus `query(filter="project_id == X", output_fields=[...], limit=大)`,取 chunk 全字段;`MilvusLiteStore` 实现,接口加抽象方法,`FakeVectorStore` 补实现。

---

## 4. LangGraph 智能体环(`agent/`)

**State**(`agent/state.py`):`{project_id, question, history: list[Message], query: str, chunks: list[RetrievedChunk], iterations: int, answer: str, citations: list, grade: str}`。

**图**(`agent/graph.py`):
```
START → retrieve → grade ─┬─(不足且 iterations<2)→ rewrite_query → retrieve
                          └─(足够 或 到上限)──────→ generate → END
```
- `retrieve`:`HybridRetriever.retrieve(project_id, state.query)` → `state.chunks`。
- `grade`(反思-检索充分性):LLM 判断"这些 chunk 是否足以回答问题"→ `sufficient` / `insufficient`。
- `rewrite_query`:LLM 根据问题 + 已有 chunk 的缺口,改写出更好的检索 query;`iterations += 1`。
- `generate`:LLM 用 top-k chunk 生成**带 `[n]` 引用**的答案(**流式**);`citations` 对齐(见 §5)。
- **有界**:最多 2 次额外检索(≤3 次检索),避免死循环/烧 token。
- **轻量扎根校验**(可选,落地时定):generate 后用规则/小 LLM 校验答案里每个 `[n]` 都落在给出的 chunk 集合内,丢弃悬空引用。

> 流式只发生在 `generate`(唯一一次生成),`retrieve/grade/rewrite` 期间向前端发**状态事件**(`检索中`/`评估检索`/`改写检索`)。这样反思环不产生"先生成又丢弃"的浪费,流式干净。

---

## 5. 引用格式(`agent/citations.py`)

- `generate` 提示词:把 top-k chunk 编号 `[1..k]` 连原文喂给 LLM,要求"凡用到某来源就在句末标 `[n]`,只允许引用给定编号"。
- 产出 `citations: list[{n, ingest_record_id, char_start, char_end, filename, snippet}]`,只保留答案里实际出现的 `[n]`。
- 持久化进 `Message.citations_json`;前端把答案文本里的 `[n]` 渲染成可点 chip → 打开来源查看器(§6)。

---

## 6. 来源查看器(`services/source.py` + 前端 `SourceViewer`)

- `GET /api/source/{ingest_record_id}` → `SourceService`:查 `IngestRecord` 拿 `stored_path` → `get_processor(path).process(path).text` **按需重提取**(确定性,偏移与索引时一致)→ 返回 `{filename, path, text}`。
- 前端 `SourceViewer`(模态/抽屉):渲染 `text`,滚动并**高亮 `char_start..char_end`**;顶部「在 Finder 中显示」→ 走 pywebview js_api 原生揭示(新增 `reveal_in_finder(path)`)。
- text/md/code:提取文本≈原文,高亮精确。pdf/docx/pptx:高亮落在**提取文本**上(非原始版式),统一体验。

---

## 7. 对话持久化(`models.py`)

- `Conversation`:`id, project_id(FK), title, created_at, updated_at`。`title` 首条消息自动生成(或截取问题)。
- `Message`:`id, conversation_id(FK), role(user|assistant), content, citations_json(nullable), created_at`。
- 级联:删项目 → 删其 conversations/messages(已有项目删除流程里带上)。
- 侧栏按项目列会话(对应当前「项目与对话」侧栏的 chat 列表占位)。

---

## 8. 设置(`services/settings.py` + `SettingsModal`)

- `~/.epictrace/settings.json`:`{chat_llm: {base_url, api_key, model}}`(预留 agent_llm/embedding 等)。
- `AppConfig` 启动时读它,覆盖默认 `LLMRoleConfig`;`SettingsService` 提供读写。
- `GET /api/settings`(api_key 回传时打码/只回是否已设)、`PUT /api/settings`(写文件 + 热更新 app.state)。
- 前端齿轮 → `SettingsModal`:填 base_url / api_key / model,任意 OpenAI-Compatible;给几个常见预设(DeepSeek / OpenAI / 本地 Ollama)做占位提示。
- **未配置 key 时**:对话输入禁用并提示"先在设置里配置对话模型",而非报错。

---

## 9. API

- `POST /api/projects/{id}/conversations` → 建会话;`GET …/conversations` 列表;`DELETE …/conversations/{cid}`。
- `GET /api/conversations/{cid}/messages` → 历史。
- `POST /api/conversations/{cid}/messages`(**SSE**):body=用户问题 → 落库 user message → 跑 graph → 流式发事件:
  - `event: status`(`检索中`/`评估检索`/`改写检索`/`生成中`)
  - `event: token`(答案增量)
  - `event: citations`(末尾,完整 citations[])
  - `event: done`(assistant message 落库完成,返回 message_id)
- `GET /api/source/{ingest_record_id}` → 来源文本(§6)。
- `GET/PUT /api/settings` → 设置(§8)。

---

## 10. 前端接线

- `ProjectsConversationView`:对话区接通——选/建会话 → `Composer` 启用(无 key 则禁用+引导设置)→ 发消息走 SSE(`fetch` + ReadableStream 解析 `event:`)→ `MessageList` 渲染:状态行(`检索中…`)、流式答案、`[n]` 渲成引用 chip。
- `ConversationList`:侧栏按项目列会话历史(替换「No chats」占位),建/选/删。
- 点引用 chip → `SourceViewer`(`GET /source` + 高亮 + Finder)。
- 齿轮 → `SettingsModal`。
- 全程 impeccable 打磨,保持 chat-first 冷静风;桌面原生(原生揭示、面板、本地持久化)。

---

## 11. warmup 纪律扩展(段错误防复发)

- `BgeReranker.warmup()` 同 `BgeM3Embedder.warmup()`(加载模型,不碰 gRPC)。
- `api/deps.get_vector_store`:构造 Milvus 前,**embedder 和 reranker 都 warmup**(锁内,先模型后 gRPC)。任何首次用到 Milvus 的路径(索引/删除/对话检索)都经此 → 全局 model-before-gRPC。
- 真模型回归测试覆盖"检索路径"(reranker+embedder+Milvus 同进程)不段错误。

---

## 12. 测试策略(TDD)

- **假替身测编排**(快、不碰 torch/网络):
  - `FakeLLM`(可编排返回固定答案/grade,记录收到的 messages)。
  - `FakeEmbedder`(已有)、`FakeReranker`(按子串命中打分)、`FakeVectorStore`(已有 + `list_by_project`)。
  - 测:RRF 融合、citations 对齐(只留实际出现的 `[n]`、丢悬空)、LangGraph 图的分支(足够→generate;不足→rewrite→retrieve;到上限→generate)、ChatService 事件序列、会话/消息落库、SourceService 重提取、settings 读写、API(SSE 事件序列、未配置 key 的禁用提示、未知会话 404)。
- **真模型 slow 冒烟**(`EPICTRACE_RUN_SLOW=1`,默认跳):BGE-reranker 真打分维度/顺序合理;检索路径(embedder+reranker+Milvus)**不段错误**;一条端到端(真 LLM 需 key,标记可跳)。
- **质量归评估**:检索召回/答案质量靠小评估集 / 手测 / 后续 Langfuse,不在单测内。

---

## 13. 明确延后(本期不做)

- **Langfuse** 提示词管理/评测(后续单独接)。
- 图片/音频内容入库(Plan 2 已延后);RAG 只检索现有文本 chunk。
- 跨项目检索、对话内"工具调用"扩展(联网/计算)、答案重生成的复杂 UI。
- session/timestamp/audio 引用(等采集 Plan 4 的内容进 Milvus 后,引用器复用同一套 char→位置对齐)。
- BM25 持久化索引、检索结果缓存。
