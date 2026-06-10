# EpicTrace

端到端的**本地优先 AI session memory / knowledge workspace**:用户主动开 session 后采集(麦克风/系统声音/截图/剪贴板/笔记/文件,全带时间戳)→ 暂存 → 多媒体处理 → 可选切割 → Agent 归类进**用户控制的本地 Project 文件夹** → 建 RAG 索引 → 通过对话框做**带来源引用(可跳回原始时刻)**的问答与复用。

## 动手前必读
- `docs/superpowers/specs/2026-06-09-epictrace-product-requirements.md` — 产品需求(完整流程、MVP 边界、痛点/强点)
- `docs/superpowers/specs/2026-06-09-epictrace-tech-stack-architecture-design.md` — 技术栈与架构决策
- `docs/reference/asr-streaming-tuning-notes.md` — ASR 流式转写调参/幻觉过滤/macOS 系统音频经验(实现采集层时参考)

## 技术栈一览
- **桌面外壳**:纯 Python 外壳(pywebview / pyloid)起步 → 以后迁 Tauri(前端代码不变)
- **前端**:React + shadcn/ui + Tailwind
- **后端**:Python + FastAPI(本地服务)
- **LLM**:DeepSeek(OpenAI-compatible),**按角色分开配置**(agent / chat / caption 各自端点+key+模型)
- **ASR**:faster-whisper(参数+滤波调优,不做微调)
- **Embedding**:BGE-M3 本地(FlagEmbedding,1024 维);Qwen embedding API 备选
- **Rerank**:BGE-reranker-v2(cross-encoder)
- **向量库**:Milvus Lite(藏在 `VectorStore` 接口后;规模/全文不够时升级 server Milvus 或加 LanceDB)
- **RAG / Agent**:LlamaIndex(检索+引用原语)+ LangGraph(ReAct + Reflection 循环)
- **Prompt 管理/评测**:Langfuse
- **中文分词**:jieba(关键词/混合检索通道)

## 核心原则(贯穿全项目)
- **本地优先 + opt-in**:主动开 session 才记录,绝非常驻监控。
- **Agent 提议、用户确认、系统提交**:采集/切割/归类三处都守。
- **Project = 用户控制的真实本地文件夹**;硬盘结构贴近用户理解,不锁黑盒。
- **时间戳是一等公民**;chunk 必须保住 `字符偏移→时间戳→音频位置` 的对齐(引用回跳的根)。
- **事实来源(文件+transcript+时间线) vs 派生索引(向量,可重建)** 要分清。

## 五个抽象接口("接口缝",换件不返工)
`LLMProvider`(OpenAI-compat)· `EmbeddingProvider` · `VectorStore`(默认 MilvusLiteStore)· `Segmenter`(默认恒等切割=整段 1 段)· `MediaProcessor`(pdf/docx/image→caption/ppt,统一输出 `{文本,元数据,来源引用}`)

## 约定
- **文档、代码、提交信息中不引用任何前身原型的产品代号。**
- 明确不做(MVP):AIGC 检测、文件系统实时监听/自动同步、类 Git 版本追踪、团队协作、云同步、常驻后台监控。

---
*代码骨架建立后,在此补充构建/测试/运行命令与目录结构(届时可跑 `/init` 扩写)。*
