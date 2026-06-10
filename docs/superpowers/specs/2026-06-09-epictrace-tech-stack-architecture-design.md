# EpicTrace 技术栈与架构设计

- **日期**:2026-06-09
- **范围**:本文档只决定 **"用什么技术、怎么搭"**(技术栈 + 架构 + 关键接口)。产品需求(采集→暂存→切割→归类→入库→索引→问答的完整流程)是**输入/已知**,不在此重述。
- **状态**:已与作者逐项确认,待最终复核 → 进入实现计划。

---

## 1. 目标与优先级

这是一个**个人项目**,目标是"整狠活"+ 练值钱的技能,而非精益上市。优先级排序:

1. **(首要)市场对口**:技术栈要对齐中国大陆"AI 应用/大模型应用开发"岗位的简历关键词与真实能力。
2. **做出一个能跑、能 demo 的本地优先产品**("有真实产品落地"本身就是简历价值)。
3. **跨平台就绪**:目前只做 macOS,但选型要让 Windows/Linux 以后可达。

一个关键判断:目标岗位(算法/后端)**对前端零要求** → 简历价值集中在 **Python AI 后端**;前端/桌面外壳"够把产品 demo 起来"即可,不过度投入。

---

## 2. 关键决策总览

| 维度 | 决策 | 一句话理由 |
|---|---|---|
| 桌面外壳 | **A1**:先纯 Python 外壳(pywebview / pyloid),规划好以后迁 Tauri | 学习面最小、最快跑通闭环;外壳非简历卖点 |
| 前端框架 | **B1**:React + shadcn/ui + Tailwind | Claude 桌面端观感;时间线可视化生态最深;AI 辅助最强 |
| 后端 | **Python + FastAPI**(本地服务) | 简历价值核心;JD 明写 "FastAPI 封装标准接口" |
| LLM | **全 DeepSeek(V4 Pro 为主)**,OpenAI-compatible;**按角色分开配置** | 单一供应商够用、最强简历词;按角色配可省钱+灵活 |
| ASR | **faster-whisper**,做参数+滤波调优(**不做微调**) | Python 原生、市场词;调参足以复刻"大讲座场景优化" |
| Embedding | **BGE-M3 本地**(FlagEmbedding,1024 维);Qwen embedding API 备选 | 中英混检强、开源免费、本地优先;自带稀疏向量供混合检索 |
| Rerank | **BGE-reranker-v2**(cross-encoder) | JD 明写 "混合检索 → Rerank";真提升质量 |
| 向量库 | **单引擎 Milvus Lite**,保留 `VectorStore` 接口 | 简历词 + 嵌入式;接口保留以便测试/升级/换件 |
| RAG/Agent | **LlamaIndex**(检索+引用原语)+ **LangGraph**(agentic 循环) | JD 明写 "LangChain/LangGraph 实战 + ReAct/Reflection" |
| Prompt 管理/评测 | **Langfuse**(追踪 + 版本 + 评测闭环) | JD 第一责任就是 "Prompt 系统工程 + 评测闭环" |
| 中文分词 | **jieba**(关键词检索通道) | JD 加分项;混合检索中文 BM25 需要 |

---

## 3. 架构分层

```
┌─────────────────────────────────────────────────────────┐
│ ① 前端 / UI 外壳                                          │
│   React + shadcn/ui  ·  跑在 pywebview/pyloid 外壳里        │
│   (以后整体迁移到 Tauri,前端代码不变)                      │
├─────────────────────────────────────────────────────────┤
│ ② 后端大脑(Python + FastAPI,本地服务)                    │
│   三套 Agent:切割 / 整理归类 / Agentic RAG                 │
│   暂存区状态机 · 引用回跳 · Prompt 评测层(Langfuse)        │
├─────────────────────────────────────────────────────────┤
│ ③ 重计算(现成件)                                          │
│   ASR=faster-whisper · LLM=DeepSeek · Embedding=BGE-M3     │
│   Rerank=BGE-reranker-v2 · 图片描述=云端多模态              │
├─────────────────────────────────────────────────────────┤
│ ④ 存储                                                     │
│   真实 Project 文件夹(事实来源) + Milvus Lite(派生索引)   │
│   + 关系型元数据(session 时间线/入库记录/文件 hash 等)     │
├─────────────────────────────────────────────────────────┤
│ ⊕ OS 采集层(全 Python)                                    │
│   麦克风 / 系统声音 / 全局截图快捷键 / 剪贴板监听(带时间戳) │
└─────────────────────────────────────────────────────────┘
```

---

## 4. 五个抽象接口("接口缝")

整套设计围绕这五个边界,保证以后能换件不返工:

| 接口 | 默认实现 | 作用 / 换件场景 |
|---|---|---|
| `LLMProvider` | DeepSeek(OpenAI-compat) | 换供应商/本地 LLM 只改配置;**按角色多实例**(见 §7) |
| `EmbeddingProvider` | BGE-M3 本地 | 本地模型 ↔ 云 API 一条路;换模型需重嵌 |
| `VectorStore` | `MilvusLiteStore` | 测试用假 store;以后加 LanceDB / 升级 server Milvus |
| `Segmenter` | **恒等切割**(整段=1 段) | 下游只吃 `Segment[]`;以后上 LLM 切割零改动 |
| `MediaProcessor` | 按类型分(pdf/docx/image→caption/ppt) | 统一吐 `{文本, 元数据, 来源引用}` |

---

## 5. RAG / Agentic RAG 链路

### 5.1 索引链路(入库时)
```
文档解析(MediaProcessor) → 切分(保住时间戳/偏移对齐)
  → Embedding(BGE-M3 稠密+稀疏) → 写入 Milvus Lite(向量+原文+元数据)
```

### 5.2 查询链路(Agentic RAG,用 LangGraph 实装为 ReAct + Reflection)
```
用户问题
  ↓
[LLM 决策] 怎么搜:语义 / 关键词 / 时间窗 / 按 session / 按 project   ← ReAct:思考→选工具
  ↓
混合检索(向量 + 关键词/jieba) → top-50 候选
  ↓
Rerank(BGE-reranker-v2,廉价专才,不用 LLM) → top-5/8
  ↓
[LLM 反思/judge] 够答吗?跑题没?缺什么?                          ← Reflection:自检纠偏
  ├─ 够 → 带来源引用回答
  ├─ 不够/跑题 → 改写 query / 换搜法 / 扩上下文 → 回到检索(再一轮)
  └─ 多轮仍无 → 明确说"找不到",不编造
  ↓
(限制检索轮数 + 上下文预算,防死循环)
```

**分工铁律**:Rerank 用专门的 cross-encoder 模型(快/便宜);LLM 只干"推理 + 决策"(贵/会思考)。两者在不同层,不混用。

---

## 6. 引用数据模型(命门)

引用"跳回原始时刻"**不是单独系统,就是向量库那张宽表的元数据列**。每个 chunk 一条记录:

| 字段 | 用途 |
|---|---|
| `vector`(1024 维) | 语义搜索 |
| `sparse` / 关键词 | 关键词/混合检索 |
| `text` | 原文,给 LLM / 显示 |
| `project_id` / `session_id` | 按 Project / session 过滤与回溯 |
| `source_type` | transcript / 截图caption / 剪贴板 / 笔记 / 文件 |
| `start_time` / `end_time` | 时间窗过滤 + **跳回那一刻** |
| `char_offset` / `audio_pos` | **引用精确定位** |
| `embed_model_id` | 防止跨模型向量混用 |
| `file_path` / `created_at` / `ingest_method` | 来源与入库记录 |

**硬约束**:从第一天起,切分时就保住 `字符偏移 → 时间戳 → 音频位置` 的对齐(faster-whisper 本来就出带时间戳的段,别在 chunking 时丢掉)。事后补极痛。

**事实来源 vs 派生索引**:
```
真实 Project 文件夹 + transcript + 时间线  =  事实来源(source of truth,资产)
Milvus Lite 里的向量 + 索引               =  派生索引(可删了重嵌重建)
```

---

## 7. LLM 配置:按角色分开

`LLMProvider` 做成"按角色多实例",每个角色独立配 `base_url` + `api_key` + `model`:

- `agent_llm` — 强推理(DeepSeek V4 Pro / reasoner),跑 Agentic RAG。
- `chat_llm` — 便宜档,跑日常对话/摘要。
- `caption_llm` — 多模态,跑图片描述。
- `embedding` / `rerank` — 同理可独立配置端点。

好处:不同任务用不同模型/供应商/key,既灵活又省钱。

**对话页附件功能**(类 ChatGPT/Claude):支持传文件/图片。**设计上分两类**——本次对话的**临时附件**(用完即弃) vs 真正**入库进 Project**(走归类流程)。两者不可混。

---

## 8. 向量库:单 Milvus Lite + 保留接口

- **实现只留 `MilvusLiteStore` 一个**(嵌入式单 `.db` 文件,pymilvus API = 服务器版 Milvus,真简历词 + 真迁移路径)。
- **`VectorStore` 接口保留**(零成本):服务测试(假 store)、不锁死(以后加 LanceDB 或升级 server Milvus 即"换实现")、上层不缠 pymilvus。

**两条已知前提(知情接受)**:
1. **规模天花板**:Milvus Lite 官方"仅小规模";个人用量大概率没事,撞墙则升级 server Milvus(改连接地址)或补 LanceDB 实现,**可恢复**。
2. **混合检索细节**:Milvus Lite `hybrid_search` 走稠密+稀疏融合,BGE-M3 直接出稀疏向量 → **混合检索可用**。但"真正的 BM25 + jieba 中文全文"在 Lite 不一定齐全;真需要则**另挂 SQLite FTS5(配 jieba)**做关键词通道,或用 BGE-M3 稀疏顶上。**实现时验证。**

---

## 9. 贯穿原则

- **本地优先 + opt-in**:用户主动开 session 才记录;原始数据/transcript/索引尽量留本地。
- **隐私**:对话/Agentic LLM 用云端(DeepSeek),换质量;基本放弃硬隐私承诺,但 `LLMProvider` 抽象保留"以后接本地 LLM"的口子。
- **三个暂存区**(采集后 / 整理归类前 / embedding):用户自控节奏,耗电耗性能的步骤由用户触发。
- **Agent 提议、用户确认、系统提交**:采集/切割/归类三处都守。
- **文件对账(MVP 轻量)**:打开 Project 时按 `size+mtime` 廉价检查,变了重算 hash;丢失则在 Project 内**按 hash 找移动过的文件**提示一键 relink;非阻塞状态徽章,不做自动魔法。

---

## 10. 简历关键词收获

Python · FastAPI · RAG / Agentic RAG / 混合检索 / Rerank · LangChain/LangGraph(ReAct/Reflection/工具编排/自检纠偏) · LlamaIndex · **Milvus / pymilvus** · BGE-M3 / BGE-reranker / sentence-transformers / FlagEmbedding · DeepSeek / Qwen · Prompt 工程 + 评测体系(Langfuse) · jieba · 向量数据库 · React(附带)

---

## 11. 明确不做(Non-goals)

- **AIGC 检测**(困惑度/风格/词汇多样性):与 EpicTrace 不自然契合,**唯一缺口**;真要吃这个细分,单独做个小检测 demo,**不为它扭曲产品**。
- **文件系统实时监听 / 自动改名移动同步 / 类 Git 版本追踪 / 团队协作 / 云同步 / 常驻后台监控**(沿用原始 MVP 边界)。
- **复用已有 Swift/WhisperKit 采集代码**:已决定不复用,跨平台 → 采集层在新栈(Python)重写。早期 Swift 原型的调参/幻觉过滤/系统音频经验见 `docs/reference/asr-streaming-tuning-notes.md`(其中 macOS Core Audio 知识可直接迁移)。
- **本文不重述完整产品功能 spec**:那是另一份(或多份)需求/实现文档。

---

## 12. 已知风险 / 实现时需验证

| 项 | 风险 / 待验证 |
|---|---|
| 系统声音采集 | 每平台原生且最难:mac=ScreenCaptureKit/Core Audio taps(需屏幕录制权限)、Win=WASAPI loopback、Linux=PipeWire。先用 Python 库(soundcard 等)验证可行性。**macOS 的 Core Audio tap 经验与坑见 `docs/reference/asr-streaming-tuning-notes.md` §5(直接可迁移)** |
| 桌面打包 | 真正的痛点:PyInstaller spec 手写、端口冲突、mac 公证需**付费 Apple 开发者号**。A1 起步可推迟,迁 Tauri 时集中处理 |
| Milvus Lite 混合/全文 | 见 §8 前提 2,实现时确认 jieba/BM25 通道方案 |
| DeepSeek V4 Pro SKU | 实现前在官方控制台确认确切型号名与 RMB 价格 |
| embedding 换模型 | 任何时候换 embedding 模型 = 全量重嵌;`embed_model_id` 必须入库 |

---

## 13. 迁移路径(都已留口子)

- **外壳**:pywebview/pyloid → Tauri(前端 React 代码不变)。
- **向量库**:Milvus Lite → 服务器版 Milvus(改连接地址)或加 LanceDB(换 `VectorStore` 实现)。
- **切割**:恒等切割 → LLM 切割(换 `Segmenter` 实现)。
- **LLM**:云 DeepSeek → 本地 LLM(加 `LLMProvider` 实现)。
