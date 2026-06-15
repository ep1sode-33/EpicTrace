# EpicTrace 采集骨架设计(Plan 8:会话 + 事件 + 暂存 + 时间线 + 归类闭环)

> Plan 8。EpicTrace「主动开 session 才记录」的源头。本期落地**采集骨架**:会话生命周期、带时间戳的事件模型、采集后暂存区、图形时间线、三个轻量采集源(笔记/剪贴板/截图)、以及**归类闭环**(Organizer 接口缝 + 手动指派到 Project → 入库 → 索引)。**ASR(mic)与系统内录各自延后成独立 plan**,本期只留 `AudioSource` / `Transcriber` 接口缝。
>
> 动手前必读:`docs/superpowers/specs/2026-06-09-epictrace-product-requirements.md`(§4-13 采集→暂存→处理→归类→入库流程)、`docs/reference/asr-streaming-tuning-notes.md`(ASR 与 macOS 系统音频经验,**留给后续 ASR plan**)、`backend/epictrace/services/ingest.py`(`IngestService.ingest_file`)、`backend/epictrace/services/index.py`(索引管线)、`backend/epictrace/models.py`(`IngestRecord` 已含 `ingest_method`)、`backend/epictrace/media/__init__.py`(`get_processor`)、`shell/run.py`(pywebview `js_api` + 原生 drop 的现成模式)、`frontend/src/views/CaptureView.tsx`(当前为占位)。

## 1. 背景与目标

产品需求把采集流程定死(§4-13):用户**主动**开 session → 按需开关多种带时间戳的输入源 → session 结束进**采集后暂存区**(不强制立即整理)→ 多媒体处理 / 可选切割 / Agent 归类 / 用户复核 → 入库进**用户控制的本地 Project 文件夹** → embedding 暂存 → 建索引 → 带引用问答。

当前 `CaptureView.tsx` 是占位页,后端无任何 session/event 概念。本期目标:把**采集 → 暂存 → 时间线 → 归类入库**这条纵向链路用骨架打通,且把最贵/最脆的两块(mic ASR、macOS 系统内录)用**接口缝**隔开延后,使骨架完全可测、可独立合并。

## 2. MVP 边界(本期)

**做**:
- 会话生命周期:开始 / 结束 session;源开关;同一时刻**单一活动 session**;所有事件带绝对时间戳。
- 数据模型:`capture_sessions` + `capture_events` 两张新表;`IngestRecord` 加 `source_session_id`(可选回溯)。
- 三个轻量采集源:**笔记**(前端输入)、**剪贴板**(shell 轮询 `NSPasteboard.changeCount`,session 期间 + 去重)、**截图**(shell `js_api` + **全局快捷键** + 应用内按钮)。
- 采集后暂存区:列出 raw sessions;**图形时间线 v1**(事件按 `ts` 排列 + 时间刻度;可缩放轨道留作后续打磨)。
- 接口缝:`AudioSource`、`Transcriber`、`Organizer`(延续「5 接口」家族)。
- 归类闭环 v1:`Organizer` 恒等/直通默认 + **手动指派整段 session 到一个 Project** → 物化进 Project 文件夹 → 复用 `IngestService` + 索引管线。

**不做(本期延后)**:
- **mic 实时 ASR**(faster-whisper 流式 + §3 幻觉过滤 + 段落确认)→ 下一 plan,本期「声音」源开关 disabled 占位。
- **系统内录**(macOS Core Audio process tap;前身 `SystemAudioCapture.swift` 可移植)→ 再下一 plan。
- 截图/拖入图片的 **caption/OCR**(按 decision-log D8,独立 vision 路径)→ 图片本期作为文件落进 Project,文本提取等其处理器 plan。
- 真·归类 Agent、切割 Agent、切割/归类复核 UI、embedding 暂存区交互、文件移动/修改自动同步(产品 §17 明确非 MVP)。

## 3. 进程模型

**后端(FastAPI)= 数据权威**:session / event / staging 文件 / `Organizer` / 入库索引。
**shell(`run.py`)= 浏览器拿不到的原生能力**,经 `js_api` 暴露,采集到的事件经 HTTP 回 POST 给后端(本机 `127.0.0.1:8765`):
- 截图:`js_api capture_screenshot(region?)` → PyObjC/Quartz 抓屏 → 存图到 staging → POST event。
- 全局快捷键:session 活动期间在 shell 注册系统级热键(NSEvent 全局监听 / CGEventTap,需辅助功能权限)→ 触发截图。
- 剪贴板监听:session 活动期间起一个定时器轮询 `NSPasteboard.generalPasteboard().changeCount`,变化则读文本(复用现有 NSPasteboard 访问)→ 去重 → POST event。

**否决「全塞后端」**(Python 直接 `mss` 截图 + PyObjC 监听):全局热键需 GUI 进程的 run loop + 权限,shell 才是自然落点;截图/剪贴板的系统权限挂在 shell app 身份下更干净;开发态(浏览器无 `window.pywebview`)对这些原生源静默降级(同现有 picker 的 dev 回退)。

后端 ↔ shell 现有约定:shell 已持有 `window`,可 `evaluate_js` 推事件给前端;前端经 `js_api` 调原生 + 经 `fetch` 调后端 API。本期新增的原生回调路径:shell 抓到事件 → 直接 POST 后端 `/api/capture/sessions/{id}/events`(不绕前端),以免前端不在采集页时丢事件。

## 4. 数据模型

### `capture_sessions`
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | PK | |
| `title` | str | 自动「会话 @{开始时间}」,可改名(复用批次 A 的 rename 思路) |
| `status` | str | `recording` / `staged` / `organized` |
| `started_at` | datetime | |
| `ended_at` | datetime \| null | |
| `staging_dir` | str | `<data_dir>/sessions/<id>/`(未归类前 app 管,非用户可见 Project) |
| `sources` | json | 本次开启了哪些源(`note`/`clipboard`/`screenshot`) |

### `capture_events`
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | PK | |
| `session_id` | FK → capture_sessions | |
| `kind` | str | `note` / `clipboard` / `screenshot` / `audio`(预留)… |
| `ts` | datetime | **绝对时间戳——一等公民**,时间线/回溯/未来引用回跳的根 |
| `payload` | Text | 文本事件存文本;文件事件存相对 staging_dir 的路径 |
| `meta` | json | 源相关元数据(截图分辨率、剪贴板来源 app 等) |

### `IngestRecord`(扩展)
加 `source_session_id: int | null`(FK,nullable):session 入库的记录回指来源 session(产品 §13 要求记录入库方式;`ingest_method` 已存在且已含 `session` 取值)。

## 5. 接口缝(`backend/epictrace/interfaces/`)

延续 `LLMProvider / EmbeddingProvider / VectorStore / Segmenter / MediaProcessor`:

- **`AudioSource`**:采音抽象(start/stop → 产出音频帧或落盘文件 + 时间戳)。本期只定义 + 文档化,无实现;「声音」源开关 disabled 占位。
- **`Transcriber`**:ASR 抽象(音频 → 带词级时间戳的 transcript 段)。本期只定义 + 恒等/空默认。mic ASR plan 落地 faster-whisper 实现。
- **`Organizer`**:`propose(session) -> OrganizationProposal`。归类提议抽象。本期默认实现 = 直通(见 §7)。真·归类 Agent 后续接入,**call site 不变**。

## 6. 采集源(本期三个轻量源)

| 源 | 机制 | 落点 | 降级 |
|---|---|---|---|
| **笔记** | 前端输入框 → `POST …/events {kind:note, payload:文本}` | 文本入 event | 纯前端,无原生依赖 |
| **剪贴板** | shell 轮询 `changeCount`(session 期间)→ 变化读文本 → 去重(同上条不重复)→ POST | 文本入 event | 无 pywebview(dev)→ 该源不可用,前端标灰 |
| **截图** | shell `js_api capture_screenshot` + 全局热键 → 抓屏存图到 `staging_dir` → `POST {kind:screenshot, payload:相对路径, meta:{w,h}}` | 图片文件 + event | 无屏幕录制权限 → 引导授权;被拒标灰 + 提示,不崩 |
| **声音** | 仅 `AudioSource`/`Transcriber` 接口缝 + disabled 开关 | — | 下一 plan |

## 7. 数据流 / 生命周期

```
开 session(选源,后端建 capture_session + staging_dir,shell 起对应原生监听)
  → 事件实时累积(后端 SSE / 轮询给前端 live feed)
  → 停 session(status=staged,shell 停所有原生监听)
  → 采集后暂存区(列出 raw sessions)
  → 图形时间线(事件按 ts 排列 + 时间刻度)
  → [Organizer 插入点] 手动指派到某 Project
  → 物化:notes/clipboard 各合成 .md 写入 staging_dir;截图已是文件
       → 逐个 IngestService.ingest_file(project_id, 文件, ingest_method="session",
         description, source_session_id=session.id)
       → 复用切块/嵌入/索引管线建立索引
  → status=organized
```

**本期只有文本事件(笔记 + 剪贴板)真进索引**;截图作为图片文件落进 Project 文件夹(`get_processor` 对图片暂无文本处理器 → `extracted_text=""`),等图像 caption/OCR plan 落地后**重建索引自动纳入**(复用现有「重建索引」)。音频本期不产生。

## 8. 归类 hook(闭环 v1)

`Organizer` 默认实现 `PassthroughOrganizer`:不调 LLM,产出「整段 session 归到用户手选的一个 Project」的 `OrganizationProposal`。前端在暂存区给一个「指派到 Project」入口(选已有 Project)。提交即执行 §7 的物化 + 入库 + 索引。

真·归类 Agent(进哪个 Project / 是否新建 / 子文件夹 / 派生文件 / 复核)是后续 plan:届时新增一个 `Organizer` 实现替换默认,**§7 的 call site 与物化/入库逻辑不变**。

## 9. API(`/api/capture/...`)

- `POST /api/capture/sessions {sources}` → 建并开始 session(已有活动 session 则 409)。
- `POST /api/capture/sessions/{id}/stop` → 结束(status=staged)。
- `GET /api/capture/sessions` / `GET /api/capture/sessions/{id}` → 列表 / 详情(含事件)。
- `POST /api/capture/sessions/{id}/events` → 追加事件(笔记来自前端;截图/剪贴板来自 shell)。
- `GET /api/capture/sessions/{id}/events/stream` → SSE live feed(复用现有 `consumeSSE`)。
- `PATCH /api/capture/sessions/{id}` → 改名。
- `DELETE /api/capture/sessions/{id}` → 删 session + staging 文件。
- `POST /api/capture/sessions/{id}/organize {project_id}` → 手动指派闭环(物化 + ingest + index;已 organized 则 409)。
- shell `js_api` 新增:`capture_screenshot(region?) -> 路径`、`start_capture_monitors(session_id, sources)`、`stop_capture_monitors()`(注册/注销全局热键 + 剪贴板轮询)。

## 10. 权限与降级(macOS)

- 截图需**屏幕录制**权限、全局热键需**辅助功能**权限:首次触发引导授权;被拒 → 该源标灰 + 提示,不崩、不静默丢。
- 剪贴板轮询**仅 session 活动期间**,停止即止;只读不写。
- **opt-in**:不开 session 不采集;源默认**全关**,显式开。
- 开发态(浏览器无 `window.pywebview`):笔记可用;剪贴板/截图原生源标灰提示「需在桌面 app 内」。

## 11. 错误处理与边界

- 已有活动 session 再开 → 409(单一活动 session)。
- shell POST event 失败(后端未就绪/网络)→ shell 侧重试一次 + 记日志,不崩采集。
- 截图存盘失败 / 抓屏返回空 → 该次截图失败提示,session 继续。
- organize 时 Project 不存在 / 已 organized → 4xx。
- 删 session → 连带删 staging 文件(已 organized 的、已复制进 Project 的文件**不删**——那是用户的库内文件)。

## 12. 测试策略

- **后端 TDD(全 fake)**:`FakeClock` 注入时间戳;假 shell-capture(直接 POST 构造事件);假 `Organizer`/复用现有 fake embedder+store。覆盖:session 生命周期(开/停/单一活动 409)、event 追加与 `ts` 排序、staging 落盘与删除、`PassthroughOrganizer` 物化 → `ingest_file(ingest_method="session", source_session_id=...)` → 索引(断言文本事件入索引、截图文件落库 `extracted_text=""`)、改名、organize 幂等/409。
- **shell 原生三件**(截图 / 全局热键 / 剪贴板监听):薄封装 + **手测**(无自动化,同 `read_clipboard_files` 的既有处理);异常一律降级为空 + 日志。
- **前端**:`npm run build`。

## 13. 明确不做(重申)

mic ASR、系统内录、图片 caption/OCR、真·归类 Agent、切割流程及复核 UI、embedding 暂存区交互、文件移动/修改自动同步、类 Git 版本追踪、团队/云同步、常驻后台监控。
