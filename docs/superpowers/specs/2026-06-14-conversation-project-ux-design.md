# EpicTrace 对话/项目管理 UX 设计(自动标题修复 + 重命名对话/项目)

> Plan 7 真机手测中撞到的几个对话/项目管理缺口,**独立于提取功能**。这是"批次 A";批次 B(「提取设置」:引擎选择器 + 模型预下载 + effort 开关)随后单独走自己的 spec→plan→实现。
> 动手前必读:`backend/epictrace/services/chat.py`(`_make_title` / `TITLE_SYS` / 两个落库调用点)、`backend/epictrace/api/routers/{conversations,projects}.py`、`backend/epictrace/schemas.py`、前端 `frontend/src/components/ProjectSidebar.tsx` + `frontend/src/views/ProjectsConversationView.tsx` + `frontend/src/lib/api.ts`。

## 1. 背景与目标
- **自动标题坏**:`ChatService._make_title(question)` 只把**首句问题**喂给标题 LLM,且 `TITLE_SYS`("给这段对话起一个不超过 12 字的简短中文标题,只回标题本身。")太弱 → 模型有时**"回答"而非"起名"**。典型:首句是关于论文的提问、而标题这次调用里又没有论文上下文 → 标题变成 "您之前并未提供任何论文"。
- **不能改名**:对话、项目目前只有创建 + 删除,**没有改名接口**;坏标题 / 想改的项目名都改不了。

**目标**:① 标题改用"**问题 + 首轮答案**"生成(治本,首轮答案落库时 `answer` 已在手边);② 对话、项目支持**行内改名**。

## 2. MVP 边界
**做**:`_make_title` 用 Q+A + `TITLE_SYS` 收紧;`PATCH` 改名接口(对话 / 项目);前端**行内改名**(kebab「重命名」+ 双击标题进入编辑,Enter 存 / Esc 取消)。
**不做**:重命名项目时移动磁盘文件夹(**只改显示名**);回填已存在对话的旧标题(只影响之后新建的);批量改名;批次 B 的提取设置。

## 3. 组件

### 3.1 标题生成(后端 `chat.py`)
`_make_title(question, answer)`:`messages = [{system: TITLE_SYS_收紧}, {user: "问题:{question}\n回答:{answer[:500]}"}]`。收紧后的 `TITLE_SYS`:明确"你是标题生成器,为下面这段问答起一个不超过 12 字的中文标题,**只输出标题本身,不要回答问题、不要解释**"。两个落库调用点(`_run_turn` 的 Plan 5 回退路 + `_run_agent_turn` 的 agent 路)在设标题时传入已生成的 `answer`。失败/空 → 回退 `question[:_TITLE_MAX]`(现有行为)。无表结构改动。

### 3.2 重命名对话(后端)
`PATCH /api/conversations/{id}` body `{title}`:`title` 去首尾空白 → 非空校验(空 → 400)+ 限长(复用 `_TITLE_MAX`/合理上限,超长截断或 400)→ 更新 `Conversation.title` → 返回更新后的 `ConversationOut`;未知 id → 404。

### 3.3 重命名项目(后端)
`PATCH /api/projects/{id}` body `{title}`:同样 trim + 非空 + 限长 → 更新 `Project.title`(**绝不动 `folder_path`**——纯改显示名,不移动/重命名磁盘文件夹)→ 返回 `ProjectOut`;未知 id → 404。

### 3.4 前端行内改名
- `api.ts`:`renameConversation(id, title)` / `renameProject(id, title)`(PATCH,复用 `j<T>`)。
- **对话**:`ProjectSidebar` 对话项的三点菜单加「重命名」(与「删除对话」并列)→ 该项标题就地变受控 `<input>`(autofocus、Enter 提交、Esc 取消、失焦按既有交互习惯处理);**双击对话标题**也进入编辑。乐观更新 + 失败回滚到原标题。
- **项目**:项目三点菜单(「删除项目」「重建索引」旁)加「重命名」→ 项目名就地编辑;**双击项目名**进入编辑。同样乐观 + 回滚。
- 编辑态:Enter/失焦提交;空或与原值相同 → 不发请求、直接退出编辑;Esc 丢弃。

## 4. 错误处理与边界
- 空标题:前端不提交 + 退出编辑(后端 400 兜底)。
- 改名接口失败(非 404):前端回滚到原标题 + 轻量提示;不阻塞。
- 限长:超长前端截断或后端钳制(取一个上限,如 60 字)。
- 项目改名只影响显示名;`folder_path` 与磁盘不变。

## 5. 测试策略
- 后端 TDD:`_make_title` 用 Q+A(`FakeLLM` 断言发给标题模型的 messages 同时含问题与答案、且首轮两条路都传了 answer);`PATCH` 对话改名(更新 title / trim / 空→400 / 404);`PATCH` 项目改名(更新 title / **folder_path 不变** / 404)。
- 前端:`npm run build`(无 FE 单测)。

## 6. 明确不做(重申)
移动/重命名磁盘文件夹、回填旧对话标题、批量改名、提取设置(批次 B)。
