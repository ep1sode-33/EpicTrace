# EpicTrace Plan 5 设计:大外部附件的会话级临时 RAG

> 接在 Plan 4(对话附件/引用)之后。本期把 Plan 4 里被标 `deferred`(登记但不可用)的**大外部文件**接上:挂载时建一个**会话级、用完即清的临时向量索引**,聊天时走和项目/内部文件一样的 hybrid 检索 → chunk 级精确引用。
> 动手前必读:Plan 4 设计 `docs/superpowers/specs/2026-06-11-epictrace-plan-4-chat-attachments-references-design.md`(复用其 `ReferenceService` / `RetrievedChunk` / 引用回跳 / 检索管线)。
> 取舍见 `docs/decisions/2026-06-11-attachment-phase-plan-and-tech-choices.md`(D14 记录:本期把 D3/D10 的"fork 子 agent 抽取式提炼"改成"临时 RAG")。

## 1. 背景与目标

Plan 4 让小外部文件全文进上下文、内部文件走 `focus` 检索;**大外部文件只标了 `deferred`,目前不可用**。原计划(D3)用 fork 子 agent 读全文蒸馏,重审后(MinerU 之问触发)判定过度设计——我们已有 hybrid 检索 + reranker + 引用回跳,内部 `focus` 本就是"对单文件做 RAG"。

**目标**:大外部文件挂载时切块+embed 进**会话级临时索引**(与项目永久库分开、随附件/对话清理),聊天时检索 top-k 进【资料】,引用精确到 chunk 并跳回原文。比 fork 每轮重读全文便宜得多(embed 一次,之后只检索)。

**与 MinerU(Plan 6)正交**:MinerU 提升提取**质量**;Plan 5 处理**体量/相关性**。以后 MinerU 经 `MediaProcessor` 缝把更干净的文本喂给本期的索引,无缝叠加。

## 2. MVP 边界

**做**
- 外部大文件:`deferred` → **`indexed`**(会话级临时 RAG)。
- 临时 `attachment_chunks` 向量集合(与项目 `chunks` 分开),记录 `conversation_id + reference_id + char 偏移`。
- 挂载时同步切块+embed+upsert;chip「索引中…」→「已索引检索」。
- 聊天检索轮:对活跃外部 `indexed` 引用,在临时集合上 dense+sparse→rerank(按 `conversation_id` + 活跃 `reference_id` 过滤)→ chunks 进【资料】。
- 清理:解挂 → 按 `reference_id` 删;删对话 → 按 `conversation_id` 删。

**不做(本期)**
- 后台异步索引(v1 同步 + 前端 loading;大文件会卡一下——已知取舍)。
- fork 子 agent 跨文档综合(搁置为未来增强)。
- 内部文件改动(维持 Plan 4 的 `focus`)。
- 图片 / 视觉。
- 把外部附件写进项目永久库(**绝不**;临时集合 ≠ 入库)。

## 3. 核心原则锚定
- **临时 ≠ 入库**:`attachment_chunks` 是会话级派生缓存,随附件/对话删除即清;项目永久 `chunks` 不受影响。事实来源(原文件 + 缓存 `extracted_text`) vs 派生索引(可弃)分清。
- **macOS gRPC-fork 段错误**:临时 store 走与项目 store 同一条"先暖 embedder+reranker 再起 Milvus 客户端"的路([[macos-embedding-milvus-fork-order]])。
- **引用是一等公民**:attachment chunk 带 `char_start/char_end`(指向缓存 `extracted_text`)+ `reference_id` → SourceViewer 精确跳回(Plan 4 已支持 attachment 来源解析,本期只是把它从"文件级"升到"chunk 级")。
- **接口缝不返工**:提取仍走 `MediaProcessor`;Plan 6 的 MinerU 插同一条缝后,索引质量自动变好。

## 4. 架构

外部文件的两条路(内部文件维持 Plan 4):

| 大小 | mode | 进上下文方式 | 引用 |
|---|---|---|---|
| 小(≤ context_window 预算) | `fulltext` | 全文进【资料】(Plan 4,不变) | 文件级 |
| 大 | **`indexed`(本期)** | 会话级临时 RAG 检索 top-k | **chunk 级** |

## 5. 组件

### 5.1 临时向量 store
- `MilvusLiteStore` 参数化 collection 名(默认 `chunks`);新增实例用 `attachment_chunks`(同一 milvus-lite db_path)。schema = 现有 `chunks` 字段 + `conversation_id` + `reference_id`。
- `deps.get_attachment_store(request)`:与 `get_vector_store` 同样**先暖 embedder+reranker 再构造**(或复用已暖件),缓存到 `app.state`。
- store 需要一个按 filter 列全量行的方法(给稀疏检索喂语料),按 `conversation_id` + `reference_id IN {…}` 过滤(`query` 的 IN 支持 Plan 4 已加)。

### 5.2 `ReferenceService.add_external` 改造
大文件分支由"登记 deferred"改为:`Chunker` 切块 → `EmbeddingProvider.embed` → upsert 进 `attachment_chunks`(每 chunk 带 `conversation_id`、`reference_id`、`char_start/char_end`、`text`、`embed_model_id`)→ mode = `indexed`。仍缓存整段 `extracted_text`(供 SourceViewer + 偏移基准)。embed/索引失败 → 回退 `deferred` + 前端提示(不阻塞)。`ReferenceService` 因此需要 embedder + attachment store(由路由注入)。

### 5.3 临时检索路径
新增 `retrieval/attachment.py`:`attachment_retrieve(embedder, store, reranker, *, conversation_id, reference_ids, query, k)` = dense(`store.query` filter `{conversation_id, reference_id: [...]}`)+ sparse(对该过滤集的行跑 jieba/BM25)→ `rrf_fuse` → `reranker.rerank` → `list[RetrievedChunk]`(`source_kind="attachment"`、`reference_id` 带上)。复用现有 `rrf_fuse`/`BgeReranker`。

### 5.4 `ChatService` 接入
`_run_turn`:取活跃外部 `indexed` 引用的 `reference_id` 列表。**有活跃 indexed 引用 → 本轮强制检索**(与 Plan 4 的 `focus` 同处理:图的 `after_route` 见到它们就走 retrieve,避免被 direct 路由静默跳过)。检索轮跑 `attachment_retrieve(...)` → 把结果 chunks 接到项目 RAG chunks 之后(全文引用仍在最前)。`build_citations` 照常(已带 `source_kind`/`reference_id`)。无任何引用/聚焦的纯 direct 轮(如"你好")不检索附件。

### 5.5 清理
- `ReferenceService.detach(cid, rid)`:软删引用后,删 `attachment_chunks` 中 `reference_id == rid` 的向量。
- 删对话:删 `attachment_chunks` 中 `conversation_id == cid` 的向量(在对话删除路由里调;`conversation_references` 行本就 FK 级联删)。

## 6. 数据流(挂大外部文件 → 提问 → 引用)
1. 拖入大 PDF → `add_external` 提取→切块→embed→upsert 进 `attachment_chunks`,mode=`indexed`,chip「已索引检索」。
2. 提问(检索轮)→ ChatService 项目 RAG + `attachment_retrieve`(本会话本引用)→ 合并进【资料】→ 流式带 `[n]`。
3. 点 `[n]`(attachment)→ SourceViewer 按 `reference_id` 取缓存全文、高亮 chunk 偏移、可在 Finder 显示。
4. 解挂 / 删对话 → 清该 scratch 向量。

## 7. 数据模型
- `ConversationReference.mode`:外部取值 `fulltext` | `indexed`(`deferred` 退役;仅作 embed 失败的回退态)。其余字段不变(已缓存 `extracted_text`)。
- 新集合 `attachment_chunks`(schema 复用 `chunks` + `conversation_id` + `reference_id`);**不**新增 SQL 表。

## 8. 接口 / 契约变更
- `MilvusLiteStore(__init__)` 增 `collection` 参数(默认 `chunks`);新增按 filter 列全量行的方法(或泛化 `list_by_project`)。
- `deps.get_attachment_store`;`ReferenceService` 构造增 embedder + attachment store(references 路由注入)。
- 新 `retrieval/attachment.py:attachment_retrieve`。
- `ChatService` 增 attachment store/embedder/reranker 依赖(或一个组合检索器),用于检索轮合并。
- 删对话路由增 scratch 清理调用。
- 前端:`mode` 多了 `indexed`(chip 文案「已索引检索」,ReferencePanel 已按 mode 显示);其余无需改(SourceViewer 的 attachment 分支 Plan 4 已有)。

## 9. 错误处理与边界
- embed/索引失败(模型未就绪/超大 OOM)→ 该引用回退 `deferred` + 前端提示"未能索引,稍后重试";不阻塞对话。
- 挂载同步索引慢(首个附件触发 BGE-M3 加载)→ 前端 loading/`索引中…`;v1 接受,future 后台化。
- 空提取文本 → 拒绝(Plan 4 已有)。
- 检索轮无活跃 indexed 引用 → 不查临时集合(零开销)。

## 10. 测试策略
- `ReferenceService.add_external` 大文件:切块+upsert 进 attachment store、mode=`indexed`、缓存 extracted_text(用 `FakeEmbedder` + `FakeVectorStore`)。
- `attachment_retrieve`:按 `conversation_id` + `reference_id` 过滤、rerank、`source_kind=attachment`(Fakes)。
- `ChatService`:检索轮合并 attachment chunks → 引用带 `source_kind=attachment`/`reference_id`/偏移;direct 轮不查附件;无 references 行为同 Plan 4。
- 清理:detach / 删对话 → attachment 向量被删(`FakeVectorStore.deleted_*`)。
- 真模型 slow 测试走 `EPICTRACE_RUN_SLOW=1`;前端 `npm run build`。

## 11. 明确不做(重申)
后台异步索引、fork 跨文档综合、内部文件改动、图片、外部文件入永久库。
