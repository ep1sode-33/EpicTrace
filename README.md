# EpicTrace

> 本地优先的 AI **session memory / 知识工作区**。把你主动记录的 session 与文件(音频→转写、截图、剪贴板、笔记、文件)变成**项目级、可检索、可引用、可跳回原始时刻**的本地知识库。

EpicTrace 面向会**主动记录**自己工作的人(上课、开会、线上讲座、项目调研、debug、看视频学习):你开一个 session 采集多模态素材 → 事后处理入库 → 通过**带来源引用**的对话复用。**数据始终在你自己的硬盘上**,绝非常驻监控——只有你主动开 session 才记录。

整个产品是一条三阶段流水线(也是顶栏的三个 tab):

```
采集  ───►  信息处理与入库  ───►  项目与对话
Capture     Process & Ingest      Projects & Chat
```

---

## 核心理念

- **本地优先 + opt-in**:主动开 session 才记录;Project 是你硬盘上**真实的本地文件夹**,结构贴近你的理解,不锁黑盒。
- **Agent 提议、用户确认、系统提交**:采集、切割、归类三处都守——自动归类先"提议",经你确认才落库。
- **时间戳是一等公民**:每段转写都带它被说出来的**真实钟表时刻**,让对话回答能引用「你在某时刻说过 X」并跳回来源(信息回溯)。
- **事实来源 vs 派生索引**:原始文件 + transcript + 时间线是事实来源;向量索引是可重建的派生物——两者分清。

## 功能

**① 采集(Capture)** — 主动开 session,采集:
- **麦克风 + 系统内录**(macOS Core Audio):**48kHz 录音**存为事实来源;停止后用 **mlx-whisper(Apple GPU)整文件一次性转写**成带标点、带时间戳的权威 transcript。
- 笔记、剪贴板、截图,全部带时间戳进时间线。
- 支持中途开/停某个音源、暂停/继续;时间线如实显示「何时开始/停止了哪个源」。

**② 处理与入库(Process & Ingest)** — 暂存区复核 → 指派进某个 Project:
- 多媒体提取(PDF/DOCX/图片 → 文本/caption,经 `MediaProcessor`)。
- 物化进 Project 文件夹 + 入库元数据(hash/大小/mtime/来源/时间/文本)落 SQLite。
- **BGE-M3 本地 embedding**(1024 维)+ **Milvus Lite** 向量索引,保住 `字符偏移→时间戳→音频位置` 的对齐(引用回跳的根)。

**③ 项目与对话(Projects & Chat)** — 对自己的素材提问:
- **混合检索**(向量 + jieba 关键词)+ **BGE-reranker-v2** 重排。
- **LangGraph ReAct + Reflection** Agent 编排,**LlamaIndex** 提供检索与引用原语。
- 回答**带来源引用**,可**跳回原始时刻**。
- LLM 走 **DeepSeek(OpenAI 兼容,BYOK)**,按角色(agent/chat/caption)分开配置端点与模型。

## 技术栈

| 层 | 选型 |
|---|---|
| 桌面外壳 | 纯 Python(pywebview)起步 → 以后迁 Tauri(前端不变) |
| 前端 | React + shadcn/ui + Tailwind(Vite) |
| 后端 | Python 3.11 + FastAPI(本地服务,`127.0.0.1:8765`) |
| LLM | DeepSeek(OpenAI 兼容),按角色分开配置 |
| ASR | mlx-whisper(Apple GPU,large-v3,停录后一次性转写);faster-whisper 为后备 |
| Embedding | BGE-M3 本地(FlagEmbedding,1024 维);Qwen embedding API 备选 |
| Rerank | BGE-reranker-v2(cross-encoder) |
| 向量库 | Milvus Lite(藏在 `VectorStore` 接口后) |
| RAG / Agent | LlamaIndex(检索+引用)+ LangGraph(ReAct + Reflection) |
| 中文分词 | jieba |

**五个抽象接口("接口缝",换件不返工)**:`LLMProvider`(OpenAI 兼容)· `EmbeddingProvider` · `VectorStore`(默认 MilvusLiteStore)· `Segmenter`(默认恒等切割)· `MediaProcessor`(pdf/docx/image → 统一 `{文本, 元数据, 来源引用}`)。

## 目录结构

```
backend/epictrace/
  api/         FastAPI 路由 + 应用工厂(main:app)
  asr/         语音采集 + 转写(音源、worker 子进程、mlx 引擎、一次性重转)
  embedding/   BGE-M3 embedding provider
  indexing/    分块 + 入库 + 索引 job
  retrieval/   混合检索 + rerank
  agent/       LangGraph ReAct + Reflection
  llm/         DeepSeek(OpenAI 兼容)provider
  media/       MediaProcessor(提取/caption)
  vectorstore/ Milvus Lite 后端
  interfaces/  五个抽象接口
  services/    采集/归类/索引/设置等应用服务
frontend/src/views/   采集 / 处理入库 / 项目与对话 / 设置
shell/         pywebview 桌面外壳(run.py)+ macOS 系统内录原生 helper(native/)
docs/          spec / plan / 决策记录
```

## 快速开始

> macOS 优先(系统内录 + Apple GPU 转写依赖 macOS)。后端 Python 固定 **3.11**,虚拟环境在 `backend/.venv`。

**安装依赖**

```bash
cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e .
cd ../frontend && npm install
```

**开发模式**(前后端分跑,热更新)

```bash
# 后端
cd backend && .venv/bin/uvicorn epictrace.main:app --port 8765
# 前端(另开一个终端)
cd frontend && npm run dev      # http://localhost:5173
```

**桌面 app**(pywebview 起壳 + 内嵌后端)

```bash
cd frontend && npm run build
cd ../backend && .venv/bin/python ../shell/run.py
```

> 首次用语音转写需先在「设置 → ASR」下载模型(本地 mlx large-v3)。系统内录需在 *系统设置 → 隐私与安全性 → 屏幕录制* 授权后重启 app。

**测试**

```bash
cd backend && .venv/bin/pytest          # 后端
cd frontend && npm run build            # 前端(tsc + vite)
```

## 现状

采集(会话/事件/暂存/时间线/录制 HUD)、麦克风 + 系统内录 ASR(48kHz 录音 + mlx 一次性转写)、归类入库、BGE-M3 + Milvus Lite 索引、混合检索 + rerank、ReAct Agent 带引用对话——均已打通。

**明确不做(MVP)**:AIGC 检测、文件系统实时监听/自动同步、类 Git 版本追踪、团队协作、云同步、常驻后台监控。

## 文档

- 产品需求:`docs/superpowers/specs/`
- 实现计划:`docs/superpowers/plans/`
- 调参 / 经验:`docs/reference/`
- 架构决策记录:`docs/decisions/`
