# EpicTrace × Cowork 改造实施计划(分 Phase)

> 需求文档:`/Users/william/Desktop/cowork/docs/epictrace-cowork-redesign.md`(做什么)。
> 本文档是执行计划(怎么做 + 进度追踪)。Steps 用 checkbox(`- [ ]`)追踪。

**Goal:** 把 EpicTrace 的单一 ReAct agent 升级为 Cowork 式多 agent 工作台:完整 agent 循环、分节 system prompt、工具注册表、子 agent 派发、沙箱、skill 系统、权限模型、远程 embedding、多 session 类型、Cowork UI。

**约定(全程):**
- 后端测试 `cd backend && .venv/bin/pytest tests/<file> -q`;全量回归 `cd backend && .venv/bin/pytest tests/ -q`。
- 前端检查 `cd frontend && npx tsc --noEmit && npx eslint src/`;构建 `npx vite build`。
- 简体中文注释/文档;标识符/路径英文。commit 信息中文短主题 `feat(scope): …`。
- TDD:每个功能先写失败测试;工具执行失败一律错误文本回传 LLM,不中断循环。
- 与现有 `epictrace.agent`(检索问答)并行,互不影响;`asr/ retrieval/ indexing/ media/ vectorstore/` 不动。
- **不主动 commit / merge**,William 明说之前代码停在工作区。

---

## Phase 1:Agent 循环 + 工具注册表 + Prompt 分节 + Session 类型 ✅(已完成)

需求 1/2/3(部分)/9。

- [x] `cowork/loop.py` 多轮循环(max_turns / turn_timeout / tool_use ↔ end_turn)
- [x] `cowork/tools/registry.py` ToolRegistry(ToolDef:name/description/parameters/permission/sandbox/always_allow_suppressed;OpenAI schema 输出;失败回传)
- [x] `cowork/tools/builtin_fs.py`(list_projects/list_files/read_file/search_text/delete_file)+ `builtin_retrieval.py`(search_vector/search_hybrid/get_timestamp_citation)
- [x] `cowork/prompts/` 分节模板系统(engine `{{var}}`/`{{#if}}` + sections + assemble 按 session 类型组合)
- [x] `cowork/sessions.py` 五种 session 类型(agent/dispatch_child/scheduled/chat/radar)+ `cowork/service.py` SSE 编排
- [x] `cowork/llm_client.py` complete_fn 抽象(测试注入假件,生产 OpenAI 兼容)

## Phase 2:权限模型 + 审批 + Cowork UI ✅(已完成)

需求 7/10。

- [x] `cowork/permissions.py` 四级权限 + admin_policy.json + 通配符最严格优先 + always-allow 抑制列表
- [x] `cowork/approvals.py` 挂起-恢复通道;`/cowork/approvals` API;`approval_request/resolved` SSE 事件
- [x] 前端 `CoworkView.tsx`:会话列表(1.5s 轮询状态)、对话区、审批弹窗(Allow Once/Session/Deny,抑制工具无 Session 选项)
- [x] 修复:`pending()` 过滤已决策项;权限设置非法值 400;SSE e2e 测试(TestClient 不增量投递 sync SSE → 消费线程 + 主线程轮询决策)

## Phase 3:子 Agent 派发 ✅(已完成,2026-07-21)

需求 4。

- [x] `cowork/agents.py` YAML 定义加载(捆绑 `agent_defs/` + 用户 `~/.epictrace/agents/`,同名覆盖;白名单永远排除派发工具)
- [x] `cowork/dispatch.py` Dispatcher:start 建 dispatch_child 后台并行跑,wait 阻塞收集回传;失败隔离;消息/日志按 session 隔离
- [x] `cowork/tools/builtin_dispatch.py` start_task(ask + 抑制总是允许)/ wait_task(allow)
- [x] 主 agent prompt dispatch 节渲染可用子 agent;`/cowork/agents` + `/sessions/{id}/progress` API
- [x] 前端会话列表子 agent 嵌套 + 父行「子任务 N/M」进度
- [x] 内置 `file-worker.yaml`;13 个新测试;全量 881 passed

---

## Phase 4:Skill 系统(需求 6)✅(已完成,2026-07-21)

**Goal:** `.skill` 包(ZIP:SKILL.md + scripts/ + schemas/)可加载,SKILL.md 注入 system prompt;捆绑 + 用户双来源。

**Files:**
- Create: `backend/epictrace/cowork/skills.py` — SkillDef + 加载(.skill ZIP 或裸目录;捆绑 `cowork/skills_bundle/` + 用户 `~/.epictrace/skills/`)
- Create: `backend/epictrace/cowork/skills_bundle/pdf-reading|docx|pptx/SKILL.md` 首批捆绑 skill(指导文本,命令指向 Phase 6 的 extract_* 工具)
- Modify: `cowork/service.py` + `cowork/dispatch.py` — SectionContext.skills 注入(主 agent 全量;子 agent 按 AgentDef.skills 白名单)
- Modify: `cowork/agents.py` — AgentDef 增加 `skills` 字段
- Test: `backend/tests/test_cowork_skills.py`(8)+ API 层 2 个

- [x] Task 1: SkillDef/loader——SKILL.md frontmatter(name/description)+ body;.skill ZIP 与裸目录都认;用户目录覆盖同名捆绑;坏包跳过并告警
- [x] Task 2: prompt 注入——主 agent 全量注入;dispatch_child 按 AgentDef.skills 白名单注入
- [x] Task 3: 首批捆绑 skill:pdf-reading / docx / pptx
- [x] Task 4: 验收 7 测试(.skill 放用户目录 → 加载可见)+ API `/cowork/skills` 列表;全量 891 passed

## Phase 5:沙箱执行(需求 5)✅(已完成,2026-07-21)

**Goal:** bash / Python 脚本在隔离环境执行:临时目录、资源限制、可配置网络策略;`rm -rf /` 类命令不伤主机(验收 5)。

**Files:**
- Create: `backend/epictrace/cowork/sandbox.py` — macOS `sandbox-exec`(seatbelt:deny default + 读全系统 + 只写临时目录)+ 临时目录 cwd/HOME/TMPDIR + ulimit CPU + wall-clock 超时;网络 none/unrestricted 两档(seatbelt deny network* 含 localhost);非 macOS 降级并告警
- Create: `backend/epictrace/cowork/tools/builtin_shell.py` — `run_bash` / `run_python`(sandbox=required,permission=ask)
- Modify: `services/settings.py` + `api/routers/settings.py` — `/api/settings/sandbox` 配置段(memory_mb/cpu_sec/network)
- Test: `backend/tests/test_cowork_sandbox.py`(14 个)

- [x] Task 1: sandbox 核心:临时目录 cwd、stdout/stderr/exit code 回传、CPU ulimit、超时 kill
- [x] Task 2: 网络档位:none(seatbelt deny network*,实测含 localhost)/ unrestricted
      (macOS 无 unshare;sandbox-exec 是原生方案。已知限制:macOS 内核不强制 RLIMIT_AS,内存上限尽力而为)
- [x] Task 3: run_bash / run_python 注册进 registry;seatbelt 按 resolve 后真实路径判定(/var→/private/var 坑)
- [x] Task 4: 验收 5 测试通过:沙箱内 rm 主机目录被拒,主机文件无恙;写沙箱内正常

## Phase 6:剩余内置工具(需求 3 收尾)✅(已完成,2026-07-21)

**Goal:** 补齐工具清单,让验收 1(「处理这三个 PDF 并写摘要」全自动拆解)端到端可跑。

**Files:**
- Create: `cowork/tools/builtin_extract.py` — extract_pdf/extract_docx/extract_pptx(走 `get_processor` 尊重用户引擎设置)+ transcribe_audio(新增 `python -m epictrace.asr.transcribe_file` 子进程,ASR 就绪门,未就绪返回引导文本不触发 3GB 下载)
- Create: `cowork/tools/builtin_projects.py` — create_project/add_file_to_project/rebuild_index(复用 ProjectService/IngestService/IndexService,经 app.state.index_lock/index_jobs,与 projects 路由同路径)
- Create: `cowork/tools/builtin_ask.py` — ask_user(approvals 通道扩 kind="question",自由文本回答)
- Modify: `builtin_dispatch.py` — process_document/process_batch(内部转 start_task 并行派发 file-worker)
- Test: `backend/tests/test_cowork_tools_phase6.py`(15 个)

- [x] Task 1: extract_* 三件套(超长截断引导分段;扫描件给明确提示)
- [x] Task 2: transcribe_audio —— 独立子进程入口 `asr/transcribe_file.py`(文件进 JSON 出,不绑 capture session)
- [x] Task 3: create_project / add_file_to_project / rebuild_index(全部 permission=ask)
- [x] Task 4: ask_user —— approvals kind=question,前端弹窗文本输入形态(不回答/提交回答)
- [x] Task 5: process_document / process_batch —— 转 start_task;全部派发类工具禁止「总是允许」
- [ ] Task 6: 验收 1 端到端(真模型手测):「处理这三个 PDF 并写一份中文摘要」——待 William 真机验证

## Phase 7:远程 Embedding Provider(需求 8)✅(已完成,2026-07-21)

**Goal:** OpenAI 兼容 /v1/embeddings 远程 provider,与本地 BGE-M3 在设置中切换(验收 8)。

**Files:**
- Create: `backend/epictrace/embedding/openai_compat.py` — OpenAICompatEmbedder(batching 64、dimensions 可选、按 index 排序对齐)
- Modify: `services/settings.py` + `api/routers/settings.py` — embedding 配置段 + `/api/settings/embedding`
- Modify: `api/deps.py` — get_embedder 按设置选路(签名比对自动重建);get_vector_store 维度随设置(≠1024 时 Milvus schema 自愈重建)
- Modify: 前端 SettingsView 新增 Embedding 设置区(provider 切换 + 远程表单,api_key 留空保留)
- Test: `backend/tests/test_embedding_remote.py`(8 个,假 OpenAI client)

- [x] Task 1: OpenAICompatEmbedder 实现接口契约(批大小 64;按 index 排序防兼容端点乱序)
- [x] Task 2: 设置持久化 + 校验;维度变化经 Milvus schema 自愈重建索引(自动翻回 indexed=False)
- [x] Task 3: 验收 8 测试:选路/请求形状/批量/设置 API 全覆盖(真端点手测待 William)

---

## 验收总表(做完对应 Phase 后勾选)

- [x] 验收 2:日志可见完整组装 system prompt,各 section 按预期组合(Phase 1 起,log.debug)
- [x] 验收 3:新增工具只需 registry 注册一次,前后端自动可见(`/cowork/tools`)
- [x] 验收 4:子 agent 日志与主 agent 隔离(消息按 session 落库,UI 分会话展示)
- [x] 验收 6:删除文件工具弹确认框且无「总是允许」选项(ALWAYS_ALLOW_SUPPRESSED)
- [ ] 验收 1:「处理这三个 PDF 并写一份中文摘要」自动拆子 agent 并行 → 汇总(Phase 6 工具链已备,待真模型手测)
- [x] 验收 5:沙箱内破坏性命令不影响主机(Phase 5)
- [x] 验收 7:.skill 放入指定目录,重启(重载)后 agent 自动加载(Phase 4)
- [x] 验收 8:切远程 embedding 后向量检索正常(Phase 7;假件/选路已测,真端点手测待 William)

> 收尾备注:全量回归中 cowork 沙箱测试曾 flaky——主进程激活 Milvus(gRPC)后 fork
> 子进程,gRPC atfork 噪声("FD from fork parent still in poll list")污染子进程 stderr。
> env 补丁拦不住(GRPC_ENABLE_FORK_SUPPORT 只在 gRPC 初始化时读),`sandbox.py` 改为
> 回传前按 gRPC 日志行前缀过滤;transcribe 子进程同步加了 env 防护。
> 最终全量:**927 passed, 7 skipped**。
