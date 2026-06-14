# Batch A:对话/项目 UX 实现计划

> **For agentic workers:** 必需子技能:使用 superpowers:subagent-driven-development(推荐)或 superpowers:executing-plans 来逐任务实现本计划。各步骤用复选框(`- [ ]`)语法跟踪。

**Goal:** 修复自动生成的对话标题(用「问题 + 首轮答案」配合收紧后的标题 prompt),并为对话与项目都加上行内重命名(PATCH 端点 + 侧边栏行内编辑),且绝不触碰项目的 `folder_path`/磁盘。

**Architecture:** 后端新增两个 `PATCH` 路由 —— `PATCH /api/conversations/{id}` 在路由 session 里直接改写 `Conversation.title`(沿用已有的 `create_conversation`/`delete_conversation` 模式),`PATCH /api/projects/{id}` 走一个新的 `ProjectService.rename()` 方法(对齐 `ProjectService.create`/`delete`)。`ChatService._make_title` 改为接收已算好的 `answer`,在两处标题调用点都把 `问题:…\n回答:…` 喂给收紧后的标题 prompt。前端新增 `api.renameConversation`/`renameProject`(经由 `j<T>` 发 PATCH),并把 `onRenameConversation`/`onRenameProject` 回调贯穿 `ProjectsConversationView → ProjectSidebar → ProjectNode/ChatRow`,在那里标题变成一个受控的行内 `<input>`(kebab 菜单「重命名」+ 双击进入;Enter/失焦提交,Esc 取消;空或未变 = 不发请求;乐观更新 + 回滚)。

**Tech Stack:** 后端 Python 3.11 + FastAPI + SQLAlchemy(venv 位于 `backend/.venv`);pytest 配合 `FakeLLM` / `TestClient` / `Database(AppConfig(data_dir=tmp_path))`。前端 React + TypeScript + Tailwind + shadcn/ui(Vite,`npm run build`)。

---

## File Structure

**已修改(后端):**
- `backend/epictrace/services/chat.py` —— 把 `_make_title(self, question)` → `_make_title(self, question, answer)`;收紧 `TITLE_SYS`;更新两处标题调用点以传入 `answer`。
- `backend/epictrace/schemas.py` —— 新增 `RenameIn` 请求 schema(`title: str`)。
- `backend/epictrace/api/routers/conversations.py` —— 新增 `PATCH /conversations/{cid}`(重命名,直接改写 session)。
- `backend/epictrace/services/projects.py` —— 新增 `ProjectService.rename(project_id, title)`。
- `backend/epictrace/api/routers/projects.py` —— 新增 `PATCH /projects/{project_id}`(经 `ProjectService` 重命名)。

**已修改(测试辅助):**
- `backend/tests/fakes.py` —— 扩展 `FakeLLM` 以记录 `complete()` 的消息(新增 `complete_messages` 列表),使标题 prompt 断言能检查发送了什么。

**测试(后端,新建):**
- `backend/tests/test_chat_title.py` —— `_make_title` 在 Plan-5 路径与 agent 路径上都用 Q+A;收紧后的 prompt;回退不变。
- `backend/tests/test_api_rename.py` —— 对话 + 项目的 `PATCH` 重命名(更新、trim、空→400、maxlen 钳制、404、`folder_path` 不变)。
- `backend/tests/test_projects_service.py` —— 新增 `rename` 单元测试(已有文件,追加)。

**已修改(前端):**
- `frontend/src/lib/api.ts` —— 新增 `renameConversation(id, title)` 与 `renameProject(id, title)`。
- `frontend/src/components/ProjectSidebar.tsx` —— 给两个 kebab 菜单都加上「重命名」;在 `ProjectNode`(项目)与 `ChatRow`(对话)里行内编辑标题;双击标题进入编辑。
- `frontend/src/views/ProjectsConversationView.tsx` —— 新增 `handleRenameConversation`/`handleRenameProject`(乐观更新 + 回滚)并把它们贯穿到 `ProjectSidebar`。

---

## 约束与约定(开工前读一遍)

- 后端命令**从 `/Users/william/Desktop/EpicTrace/backend` 运行**,用 `./.venv/bin/pytest`(Python 3.11)。前端命令**从 `/Users/william/Desktop/EpicTrace/frontend` 运行**,用 `npm run build`。
- `chat.py` 里已存在 `_TITLE_MAX = 30` —— PATCH 路由的 maxlen 钳制直接复用它(不新增常量;`30` 字就是「合理上限」)。
- 项目重命名**只改显示标题** —— 重命名路径绝不能读取、设置、移动或创建 `folder_path`/磁盘。测试断言 `folder_path` 不变。
- Git 身份已配置好。提交用 `git add <paths> && git commit -m "<subject>" -m "<body>"`;body 必须以下面这行结尾:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- 任何地方都不要写任何前身原型代号。

### 已对齐的既有模式(读过代码后确认)

- **对话没有 service** —— `create_conversation`/`delete_conversation` 直接改写 session(`with db.session() as s: ... s.add/delete`)。所以对话重命名也在路由里直接改写 session。404 约定:`raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")`。
- **项目确实有 `ProjectService`** —— `create_project`/`delete_project` 走 `ProjectService(db).create/delete(...)`。所以项目重命名新增 `ProjectService.rename(...)` 并由路由调用。404 约定:`_ensure_project(db, project_id)` 抛 `HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")`,随后 service 改写。
- **`FakeLLM`** 按 system prompt 关键词路由:标题调用由 `"标题" in sys` 匹配(因此收紧后的 `TITLE_SYS` 仍须含子串 `标题`)。`complete()` 目前**不**记录消息 —— 只有 `stream()` 记录进 `stream_messages`。Task 1 新增 `complete_messages` 列表,让 Q+A 断言能检查标题调用的消息。
- **前端 kebab 接线**:对话 kebab 在 `ChatRow`(目前只有「删除」);项目 kebab 在 `ProjectNode`(目前「重建索引」+「删除项目」)。行内编辑状态对每行本地(`useState`)。重命名回调贯穿 `ProjectsConversationView → ProjectSidebar → ProjectNode → ChatChildren → ChatRow`。乐观更新要改的状态缓存:`projects`(项目重命名)与 `conversationsByProject[projectId]`(对话重命名)。
- **Fetch 辅助**:`j<T>(r)` 在非 2xx 时抛错;`api` 方法返回 `fetch(...).then(j<T>)`。新的 PATCH 方法照搬 `updateProfile`(PUT 带 JSON body + `j<T>`)。

---

## Task 1 —— 用「问题 + 答案」自动生成标题

把 `_make_title(self, question)` → `_make_title(self, question, answer)`,收紧 `TITLE_SYS`,把标题的 user 消息构造成 `问题:{question}\n回答:{answer[:500]}`,并更新两处标题调用点(`_run_turn` 约第 188 行、`_run_agent_turn` 约第 259 行)以传入已算好的 `answer`。回退(`question[:20]`,钳到 `[:_TITLE_MAX]`)不变。

**Files:**
- 测试:`backend/tests/test_chat_title.py`(新建)
- 辅助:`backend/tests/fakes.py`(扩展 `FakeLLM`)
- 实现:`backend/epictrace/services/chat.py`

- [ ] 扩展 `FakeLLM` 以记录 `complete()` 的消息。在 `backend/tests/fakes.py` 中,编辑 `FakeLLM.__init__` 加上记录器,并编辑 `FakeLLM.complete` 向其追加。

  在 `__init__` 中,在 `self.stream_messages: list[list[dict]] = []   # ...` 这行之后,加上:
  ```python
        self.complete_messages: list[list[dict]] = []  # 记录每次 complete 收到的完整 message 列表(供标题断言)
  ```

  把 `complete` 方法:
  ```python
      def complete(self, messages, **kwargs):
          return self._route(messages)
  ```
  替换为:
  ```python
      def complete(self, messages, **kwargs):
          self.complete_messages.append(list(messages))
          return self._route(messages)
  ```

- [ ] 写下会失败的测试文件 `backend/tests/test_chat_title.py`,完整内容如下。
  ```python
  from pathlib import Path

  from langchain_core.messages import AIMessage
  from sqlalchemy import select

  from epictrace.config import AppConfig
  from epictrace.db import Database
  from epictrace.models import Conversation, Message, Project
  from epictrace.retrieval.types import RetrievedChunk
  from epictrace.services.chat import ChatService
  from tests.fakes import FakeChatModel, FakeLLM


  class _Retriever:
      def retrieve(self, *, project_id, query, **kwargs):
          return [RetrievedChunk(text="页表映射地址", ingest_record_id=1, project_id=project_id,
                                 char_start=0, char_end=6, source_type="folder_scan")]


  class _Refs:
      def __init__(self, db): self._db = db
      def list_active(self, cid):
          from epictrace.services.references import ReferenceService
          return ReferenceService(self._db).list_active(cid)


  def _setup(tmp_path, title="新对话"):
      db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
      with db.session() as s:
          p = Project(title="P", folder_path=str(tmp_path / "P")); s.add(p); s.flush()
          c = Conversation(project_id=p.id, title=title); s.add(c); s.flush()
          cid = c.id
      return db, cid


  def _title_call_messages(llm):
      """从 FakeLLM 记录的 complete 调用里挑出标题调用(system 含『标题』)。"""
      for msgs in llm.complete_messages:
          if "标题" in msgs[0]["content"]:
              return msgs
      raise AssertionError("没有发生标题生成调用")


  def test_plan5_title_uses_question_and_answer(tmp_path: Path):
      # Plan 5 回退路(无 chat_model_factory):标题调用的 user 消息须同时含问题与(截断的)答案。
      db, cid = _setup(tmp_path)
      llm = FakeLLM(grade="sufficient", title="页表与分页", answer="页表把虚拟地址映射到物理帧[1]。")
      svc = ChatService(db, llm, _Retriever())
      list(svc.stream_answer(cid, "操作系统的页表是如何工作的"))
      sent = _title_call_messages(llm)
      user_content = sent[-1]["content"]
      assert "操作系统的页表是如何工作的" in user_content   # 问题在场
      assert "页表把虚拟地址映射到物理帧" in user_content     # 首轮答案也在场
      with db.session() as s:
          assert s.get(Conversation, cid).title == "页表与分页"


  def test_agent_path_title_uses_question_and_answer(tmp_path: Path):
      # Agent 路(supports_tools=True):标题调用同样须同时含问题与答案。
      db, cid = _setup(tmp_path)
      chat_model = FakeChatModel(script=[
          AIMessage(content="", tool_calls=[
              {"name": "search_project_library", "args": {"query": "页表"},
               "id": "1", "type": "tool_call"}]),
          AIMessage(content="done"),
      ])
      llm = FakeLLM(title="页表问答", answer="页表是地址映射结构[1]。")
      svc = ChatService(db, llm, _Retriever(), references=_Refs(db),
                        chat_model_factory=lambda: chat_model, supports_tools=lambda: True)
      list(svc.stream_answer(cid, "什么是页表"))
      sent = _title_call_messages(llm)
      user_content = sent[-1]["content"]
      assert "什么是页表" in user_content
      assert "页表是地址映射结构" in user_content
      with db.session() as s:
          assert s.get(Conversation, cid).title == "页表问答"


  def test_title_answer_is_truncated_to_500_chars(tmp_path: Path):
      # 答案很长时,喂给标题模型的回答须截断到 500 字(避免标题调用吃满上下文)。
      db, cid = _setup(tmp_path)
      long_answer = "页" * 800 + "[1]。"
      llm = FakeLLM(grade="sufficient", title="长答案标题", answer=long_answer)
      svc = ChatService(db, llm, _Retriever())
      list(svc.stream_answer(cid, "问题"))
      user_content = _title_call_messages(llm)[-1]["content"]
      # 回答片段最多 500 个『页』;若未截断会有 800 个。
      assert user_content.count("页") <= 500


  def test_title_system_prompt_is_title_only(tmp_path: Path):
      # 收紧后的 TITLE_SYS 须明确『只输出标题』而非作答(防 LLM『回答』而非『起名』)。
      from epictrace.services.chat import TITLE_SYS
      assert "标题" in TITLE_SYS
      assert "只输出标题" in TITLE_SYS


  def test_title_falls_back_to_question_when_empty(tmp_path: Path):
      # LLM 标题为空白 → 回退到问题首段,clamp 到 _TITLE_MAX。
      db, cid = _setup(tmp_path)
      llm = FakeLLM(grade="sufficient", title="   ", answer="答案[1]。")
      svc = ChatService(db, llm, _Retriever())
      list(svc.stream_answer(cid, "操作系统的页表是如何工作的呢" * 3))
      with db.session() as s:
          c = s.get(Conversation, cid)
          assert c.title.startswith("操作系统的页表") and len(c.title) <= 30
  ```

- [ ] 运行新测试,预期 FAIL(签名不匹配:`_make_title` 仍只收一个参数,且 prompt 断言失败)。
  ```
  ./.venv/bin/pytest -q tests/test_chat_title.py
  ```
  预期:失败 —— 例如 `TypeError: _make_title() missing 1 required positional argument: 'answer'` 和/或 `只输出标题` / 答案在消息中的断言出现 `AssertionError`。

- [ ] 收紧 `backend/epictrace/services/chat.py` 中的 `TITLE_SYS`。把:
  ```python
  TITLE_SYS = "给这段对话起一个不超过 12 字的简短中文标题,只回标题本身。"
  ```
  替换为:
  ```python
  TITLE_SYS = (
      "你是对话标题生成器。为下面这段问答起一个不超过 12 字的简短中文标题,"
      "概括它们在聊什么。只输出标题本身,不要回答问题、不要加引号、不要解释。"
  )
  ```

- [ ] 改 `backend/epictrace/services/chat.py` 中 `_make_title` 的签名与方法体。把:
  ```python
      def _make_title(self, question: str) -> str:
          """首轮自动命名:一次廉价 LLM 调用产出简短标题;失败/为空 → 回退到问题首段。"""
          fallback = question[:20]
          try:
              title = self._llm.complete([
                  {"role": "system", "content": TITLE_SYS},
                  {"role": "user", "content": question},
              ]).strip().strip("\"'“”‘’ ")
          except Exception:  # noqa: BLE001 — 标题失败不该影响主回答
              return fallback
          return (title or fallback)[:_TITLE_MAX]
  ```
  替换为:
  ```python
      def _make_title(self, question: str, answer: str) -> str:
          """首轮自动命名:用『问题 + 首轮答案』做一次廉价 LLM 调用产出简短标题;
          失败/为空 → 回退到问题首段。答案截断到 500 字,避免标题调用吃满上下文。"""
          fallback = question[:20]
          try:
              title = self._llm.complete([
                  {"role": "system", "content": TITLE_SYS},
                  {"role": "user", "content": f"问题:{question}\n回答:{answer[:500]}"},
              ]).strip().strip("\"'“”‘’ ")
          except Exception:  # noqa: BLE001 — 标题失败不该影响主回答
              return fallback
          return (title or fallback)[:_TITLE_MAX]
  ```

- [ ] 更新 `_run_turn`(约第 189 行)里的 Plan-5 标题调用点。把:
  ```python
                  if is_first_user_turn and c.title == _DEFAULT_TITLE:
                      c.title = self._make_title(question)
  ```
  替换为:
  ```python
                  if is_first_user_turn and c.title == _DEFAULT_TITLE:
                      c.title = self._make_title(question, answer)
  ```
  (`answer` 在此处的作用域内 —— 在 `_run_turn` 前面的 `answer = "".join(parts)` 已设置。)

- [ ] 更新 `_run_agent_turn`(约第 259 行)里 agent 路径的标题调用点。把:
  ```python
                  if is_first_user_turn and c.title == _DEFAULT_TITLE:
                      c.title = self._make_title(question)
  ```
  替换为:
  ```python
                  if is_first_user_turn and c.title == _DEFAULT_TITLE:
                      c.title = self._make_title(question, answer)
  ```
  (`answer` 在此处的作用域内 —— 在标题块之前的 `try` 里从 `_answer` 内部事件设置。)

- [ ] 运行新测试,预期 PASS。
  ```
  ./.venv/bin/pytest -q tests/test_chat_title.py
  ```
  预期:`5 passed`。

- [ ] 运行已有的 chat 测试套件以确认没有回归(旧的 `_make_title(question)` 调用签名是内部的;`test_chat_service.py` 里已有的标题测试仍端到端地驱动标题,必须保持绿)。
  ```
  ./.venv/bin/pytest -q tests/test_chat_service.py tests/test_chat_agent_routing.py
  ```
  预期:全部通过(无失败、无错误)。

- [ ] 提交。
  ```
  git add backend/epictrace/services/chat.py backend/tests/fakes.py backend/tests/test_chat_title.py
  git commit -m "Auto-title conversations from question + first answer" -m "$(cat <<'EOF'
  _make_title now takes the already-computed answer and feeds 问题/回答 to a
  tightened title-only prompt at both the Plan-5 and agent title call sites.
  FakeLLM records complete() messages so the Q+A is assertable.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 2 —— PATCH 重命名对话

新增 `PATCH /api/conversations/{cid}`,带 `{title}` body。去首尾空白;trim 后为空 → 400;钳到 `_TITLE_MAX`(30)字;更新 `Conversation.title`;返回 `ConversationOut`;未知 id → 404。直接改写 session(对齐 `create_conversation`/`delete_conversation`)。

**Files:**
- Schema:`backend/epictrace/schemas.py`
- 测试:`backend/tests/test_api_rename.py`(新建)
- 实现:`backend/epictrace/api/routers/conversations.py`

- [ ] 新增 `RenameIn` schema。在 `backend/epictrace/schemas.py` 中,`ConversationCreate` 类之后(约第 58 行),加上:
  ```python
  class RenameIn(BaseModel):
      title: str
  ```
  (不加 `Field(min_length=...)` —— 空/纯空白的校验放在路由里,这样它能先 trim 再返回干净的 400。)

- [ ] 写下会失败的测试文件 `backend/tests/test_api_rename.py`,完整内容如下(覆盖 Task 2 的对话用例;Task 3 会向同一文件追加项目用例)。
  ```python
  from pathlib import Path

  import pytest
  from fastapi.testclient import TestClient

  from epictrace.api.app import create_app
  from epictrace.config import AppConfig
  from epictrace.db import Database


  @pytest.fixture()
  def client(tmp_path: Path) -> TestClient:
      db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
      app = create_app(db=db)
      return TestClient(app)


  def _project(client: TestClient, tmp_path: Path) -> int:
      folder = str(tmp_path / "P")
      return client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]


  def _conversation(client: TestClient, pid: int) -> int:
      return client.post(f"/api/projects/{pid}/conversations", json={"title": "旧标题"}).json()["id"]


  # ---- conversation rename ----

  def test_rename_conversation_updates_title(client, tmp_path):
      pid = _project(client, tmp_path)
      cid = _conversation(client, pid)
      resp = client.patch(f"/api/conversations/{cid}", json={"title": "新标题"})
      assert resp.status_code == 200
      assert resp.json()["title"] == "新标题"
      assert resp.json()["id"] == cid
      # 重新拉列表确认已落库。
      listed = client.get(f"/api/projects/{pid}/conversations").json()
      assert listed[0]["title"] == "新标题"


  def test_rename_conversation_trims_whitespace(client, tmp_path):
      pid = _project(client, tmp_path)
      cid = _conversation(client, pid)
      resp = client.patch(f"/api/conversations/{cid}", json={"title": "  去空白  "})
      assert resp.status_code == 200
      assert resp.json()["title"] == "去空白"


  def test_rename_conversation_empty_is_400(client, tmp_path):
      pid = _project(client, tmp_path)
      cid = _conversation(client, pid)
      assert client.patch(f"/api/conversations/{cid}", json={"title": "   "}).status_code == 400
      # 标题未被改坏。
      assert client.get(f"/api/projects/{pid}/conversations").json()[0]["title"] == "旧标题"


  def test_rename_conversation_clamps_maxlen(client, tmp_path):
      pid = _project(client, tmp_path)
      cid = _conversation(client, pid)
      resp = client.patch(f"/api/conversations/{cid}", json={"title": "标" * 100})
      assert resp.status_code == 200
      assert len(resp.json()["title"]) == 30


  def test_rename_unknown_conversation_404(client):
      assert client.patch("/api/conversations/999999", json={"title": "x"}).status_code == 404
  ```

- [ ] 运行,预期 FAIL(还没有 PATCH 路由 → 405 Method Not Allowed,所以 `== 200`/`== 400`/`== 404` 断言失败)。
  ```
  ./.venv/bin/pytest -q tests/test_api_rename.py
  ```
  预期:对话测试失败(`assert 405 == 200` 等)。

- [ ] 把重命名路由加到 `backend/epictrace/api/routers/conversations.py`。先把 `RenameIn` 加进 schema 导入 —— 把:
  ```python
  from epictrace.schemas import ConversationCreate, ConversationOut, MessageCreate, MessageOut
  ```
  替换为:
  ```python
  from epictrace.schemas import (
      ConversationCreate, ConversationOut, MessageCreate, MessageOut, RenameIn,
  )
  ```
  然后把路由加在 `delete_conversation` 之后(`list_messages` 之前):
  ```python
  @router.patch("/conversations/{cid}", response_model=ConversationOut)
  def rename_conversation(cid: int, payload: RenameIn, db: Database = Depends(get_db)):
      # 仅改显示标题:去首尾空白 → 非空校验 → 钳到 _TITLE_MAX。
      from epictrace.services.chat import _TITLE_MAX

      title = payload.title.strip()
      if not title:
          raise HTTPException(status.HTTP_400_BAD_REQUEST, "title must not be empty")
      with db.session() as s:
          c = s.get(Conversation, cid)
          if c is None:
              raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
          c.title = title[:_TITLE_MAX]
          s.flush(); s.refresh(c)
          return ConversationOut.model_validate(c)
  ```

- [ ] 运行,预期对话测试 PASS。
  ```
  ./.venv/bin/pytest -q tests/test_api_rename.py -k conversation
  ```
  预期:`5 passed`(5 个对话测试)。

- [ ] 提交。
  ```
  git add backend/epictrace/schemas.py backend/epictrace/api/routers/conversations.py backend/tests/test_api_rename.py
  git commit -m "Add PATCH rename for conversations" -m "$(cat <<'EOF'
  PATCH /api/conversations/{id} trims + rejects empty (400), clamps to
  _TITLE_MAX, updates the title, returns ConversationOut; 404 for unknown id.
  Mutates the session directly, matching create/delete_conversation.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 3 —— PATCH 重命名项目

新增 `ProjectService.rename(project_id, title)` 与 `PATCH /api/projects/{project_id}`,带同样的 trim + 非空(400)+ 钳长校验。只更新 `Project.title` —— **绝不**触碰 `folder_path`/磁盘。返回 `ProjectOut`;未知 id → 404。

**Files:**
- Service:`backend/epictrace/services/projects.py`
- Service 测试:`backend/tests/test_projects_service.py`(追加)
- API 测试:`backend/tests/test_api_rename.py`(追加)
- 实现:`backend/epictrace/api/routers/projects.py`

- [ ] 向 `backend/tests/test_projects_service.py` 追加 service 级别的会失败测试(在 `test_delete_unknown_returns_none` 之后):
  ```python
  def test_rename_updates_title_and_keeps_folder(tmp_path: Path):
      db = _db(tmp_path)
      folder = tmp_path / "CS 2506"
      svc = ProjectService(db)
      proj = svc.create(title="CS 2506", folder_path=str(folder))

      renamed = svc.rename(proj.id, "操作系统 2506")
      assert renamed is not None
      assert renamed.title == "操作系统 2506"
      assert renamed.folder_path == str(folder)   # 磁盘路径不变
      assert folder.exists()                       # 不移动/重命名文件夹
      assert [p.title for p in svc.list()] == ["操作系统 2506"]


  def test_rename_unknown_returns_none(tmp_path: Path):
      assert ProjectService(_db(tmp_path)).rename(99999, "x") is None
  ```

- [ ] 向 `backend/tests/test_api_rename.py` 追加 API 级别的会失败测试(在 `test_rename_unknown_conversation_404` 之后):
  ```python
  # ---- project rename ----

  def test_rename_project_updates_title(client, tmp_path):
      folder = str(tmp_path / "P")
      pid = client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]
      resp = client.patch(f"/api/projects/{pid}", json={"title": "重命名后的项目"})
      assert resp.status_code == 200
      assert resp.json()["title"] == "重命名后的项目"
      assert resp.json()["folder_path"] == folder   # 路径不变


  def test_rename_project_keeps_folder_path_unchanged(client, tmp_path):
      folder = str(tmp_path / "P")
      pid = client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]
      client.patch(f"/api/projects/{pid}", json={"title": "新名字"})
      listed = client.get("/api/projects").json()
      assert listed[0]["title"] == "新名字"
      assert listed[0]["folder_path"] == folder      # 磁盘路径一字未改
      assert Path(folder).exists()                    # 文件夹仍在原处


  def test_rename_project_trims_and_rejects_empty(client, tmp_path):
      folder = str(tmp_path / "P")
      pid = client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]
      assert client.patch(f"/api/projects/{pid}", json={"title": "  整理  "}).json()["title"] == "整理"
      assert client.patch(f"/api/projects/{pid}", json={"title": "   "}).status_code == 400


  def test_rename_project_clamps_maxlen(client, tmp_path):
      folder = str(tmp_path / "P")
      pid = client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]
      resp = client.patch(f"/api/projects/{pid}", json={"title": "项" * 100})
      assert resp.status_code == 200
      assert len(resp.json()["title"]) == 30


  def test_rename_unknown_project_404(client):
      assert client.patch("/api/projects/999999", json={"title": "x"}).status_code == 404
  ```

- [ ] 运行,预期 FAIL(没有 `rename` 方法 → `AttributeError`;没有 PATCH 路由 → 405)。
  ```
  ./.venv/bin/pytest -q tests/test_projects_service.py tests/test_api_rename.py
  ```
  预期:失败 —— `AttributeError: 'ProjectService' object has no attribute 'rename'` 与 `assert 405 == 200`。

- [ ] 把 `rename` 加进 `backend/epictrace/services/projects.py` 的 `ProjectService`(在 `list` 方法之后、`delete` 之前):
  ```python
      def rename(self, project_id: int, title: str) -> Project | None:
          """仅改显示标题(绝不动 folder_path / 磁盘文件夹)。项目不存在则返回 None。
          标题的 trim / 非空 / 钳长由调用方(路由)负责;本方法只写 title。"""
          with self._db.session() as s:
              proj = s.get(Project, project_id)
              if proj is None:
                  return None
              proj.title = title
              s.flush()
              s.refresh(proj)
              s.expunge(proj)
              return proj
  ```

- [ ] 把 PATCH 路由加到 `backend/epictrace/api/routers/projects.py`。先把 `RenameIn` 加进 schema 导入 —— 把:
  ```python
  from epictrace.schemas import IndexStatusOut, ProjectCreate, ProjectOut, ScanResultOut
  ```
  替换为:
  ```python
  from epictrace.schemas import IndexStatusOut, ProjectCreate, ProjectOut, RenameIn, ScanResultOut
  ```
  然后把路由加在 `list_projects` 之后(`delete_project` 之前):
  ```python
  @router.patch("/{project_id}", response_model=ProjectOut)
  def rename_project(project_id: int, payload: RenameIn, db: Database = Depends(get_db)) -> ProjectOut:
      # 仅改显示标题:去首尾空白 → 非空校验 → 钳到 _TITLE_MAX;绝不触碰 folder_path / 磁盘。
      from epictrace.services.chat import _TITLE_MAX

      title = payload.title.strip()
      if not title:
          raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="title must not be empty")
      proj = ProjectService(db).rename(project_id, title[:_TITLE_MAX])
      if proj is None:
          raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
      return ProjectOut.model_validate(proj)
  ```

- [ ] 运行,预期 PASS。
  ```
  ./.venv/bin/pytest -q tests/test_projects_service.py tests/test_api_rename.py
  ```
  预期:全部通过(2 个新 service 测试 + 所有对话与项目重命名测试)。

- [ ] 提交。
  ```
  git add backend/epictrace/services/projects.py backend/epictrace/api/routers/projects.py backend/tests/test_projects_service.py backend/tests/test_api_rename.py
  git commit -m "Add PATCH rename for projects (title only, folder unchanged)" -m "$(cat <<'EOF'
  ProjectService.rename writes Project.title and never touches folder_path or
  disk. PATCH /api/projects/{id} trims + rejects empty (400), clamps to
  _TITLE_MAX, returns ProjectOut; 404 for unknown id. Tests assert folder_path
  is unchanged and the folder stays in place.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 4 —— 前端 API + 行内重命名

新增 `api.renameConversation`/`renameProject`;给两个 kebab 菜单都加上「重命名」;行内编辑标题(受控 input、autofocus、Enter 提交、Esc 取消、失焦提交;空或未变 → 不发请求);双击标题进入编辑;乐观更新 + 失败回滚。

**Files:**
- `frontend/src/lib/api.ts`
- `frontend/src/components/ProjectSidebar.tsx`
- `frontend/src/views/ProjectsConversationView.tsx`

> 前端没有单元测试;正确性由 Task 5 的 `npm run build`(类型检查)验证。每一步都是一处聚焦编辑;在 API + view + 组件编辑后各 build 一次。

- [ ] 把两个 PATCH 辅助加进 `frontend/src/lib/api.ts`。插在 `deleteConversation` 方法之后(约第 96 行其闭合 `,` 之后),照搬 `updateProfile`:
  ```typescript
    renameConversation: (cid: number, title: string) =>
      fetch(`${BASE}/api/conversations/${cid}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      }).then(j<Conversation>),
    renameProject: (id: number, title: string) =>
      fetch(`${BASE}/api/projects/${id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      }).then(j<Project>),
  ```

- [ ] 在 `frontend/src/views/ProjectsConversationView.tsx` 里新增重命名处理函数并把它们贯穿下去。

  首先,在 `handleReindexProject` 之后(约第 270 行其闭合 `);` 之后)加上 `handleRenameProject`。它乐观更新 `projects`(以及 `selected`),调用 API,失败时回滚:
  ```tsx
    // 重命名项目(仅显示名,后端不动磁盘):乐观更新列表 + 选中项,失败回滚到原标题。
    const handleRenameProject = useCallback(
      async (project: Project, title: string) => {
        const next = title.trim();
        if (!next || next === project.title) return;
        const prevTitle = project.title;
        setProjects((prev) =>
          prev.map((p) => (p.id === project.id ? { ...p, title: next } : p)),
        );
        setSelected((cur) => (cur && cur.id === project.id ? { ...cur, title: next } : cur));
        try {
          await api.renameProject(project.id, next);
        } catch {
          // 失败回滚到原标题(列表 + 选中项)。
          setProjects((prev) =>
            prev.map((p) => (p.id === project.id ? { ...p, title: prevTitle } : p)),
          );
          setSelected((cur) =>
            cur && cur.id === project.id ? { ...cur, title: prevTitle } : cur,
          );
        }
      },
      [],
    );

    // 重命名对话:乐观更新该项目的对话缓存,失败回滚到原标题。
    const handleRenameConversation = useCallback(
      async (conversation: Conversation, title: string) => {
        const next = title.trim();
        if (!next || next === conversation.title) return;
        const prevTitle = conversation.title;
        const patch = (t: string) =>
          setConversationsByProject((prev) => {
            const list = prev[conversation.project_id];
            if (!list) return prev;
            return {
              ...prev,
              [conversation.project_id]: list.map((c) =>
                c.id === conversation.id ? { ...c, title: t } : c,
              ),
            };
          });
        patch(next);
        try {
          await api.renameConversation(conversation.id, next);
        } catch {
          patch(prevTitle);
        }
      },
      [],
    );
  ```

  然后把两者都接进 `<ProjectSidebar .../>`(约第 315-331 行的 props 块里),加在 `onReindexProject={handleReindexProject}` 之后:
  ```tsx
          onRenameProject={handleRenameProject}
          onRenameConversation={handleRenameConversation}
  ```

- [ ] 在 `frontend/src/components/ProjectSidebar.tsx` 里把新 props 穿过 `ProjectSidebar` 与 `ProjectNode`。

  (a) 加进 `ProjectSidebar` 解构出的 props(在约第 47 行 `onReindexProject,` 之后):
  ```tsx
    onRenameProject,
    onRenameConversation,
  ```
  (b) 加进 `ProjectSidebar` 的 prop 类型(在约第 73 行 `onReindexProject` 类型之后):
  ```tsx
    /** 重命名项目(仅显示名):行内编辑提交时调用。 */
    onRenameProject: (project: Project, title: string) => void;
    /** 重命名对话:行内编辑提交时调用。 */
    onRenameConversation: (conversation: Conversation, title: string) => void;
  ```
  (c) 把它们传进每个 `<ProjectNode .../>`(在约第 123 行 `onReindexProject={onReindexProject}` 之后):
  ```tsx
                  onRenameProject={onRenameProject}
                  onRenameConversation={onRenameConversation}
  ```
  (d) 加进 `ProjectNode` 解构出的参数(在约第 172 行 `onReindexProject,` 之后)及其 prop 类型(在约第 187 行 `onReindexProject` 类型之后):
  ```tsx
    onRenameProject,
    onRenameConversation,
  ```
  ```tsx
    onRenameProject: (project: Project, title: string) => void;
    onRenameConversation: (conversation: Conversation, title: string) => void;
  ```
  (e) 把 `onRenameConversation` 传进 `<ChatChildren .../>`(在约第 298 行 `onDeleteConversation={onDeleteConversation}` 之后):
  ```tsx
              onRenameConversation={onRenameConversation}
  ```

- [ ] 把一个可复用的行内编辑 input 组件加进 `frontend/src/components/ProjectSidebar.tsx`。插在文件顶部附近(imports 之后、`ProjectSidebar` 函数之前)。它是一个受控、挂载即聚焦的 input,Enter/失焦提交,Esc 取消;由父级决定值是否变化:
  ```tsx
  /**
   * 行内重命名输入框:受控、挂载即聚焦并全选;Enter / 失焦提交,Esc 取消。
   * 是否真正发请求(空 / 未变 → 不发)由父级回调决定;本组件只负责编辑交互。
   */
  function InlineRename({
    initial,
    onSubmit,
    onCancel,
    className,
  }: {
    initial: string;
    onSubmit: (value: string) => void;
    onCancel: () => void;
    className?: string;
  }) {
    const [value, setValue] = useState(initial);
    // 用 ref 防止「失焦提交」与「Enter/Esc 已结束编辑」重复触发。
    const doneRef = useRef(false);
    const submit = () => {
      if (doneRef.current) return;
      doneRef.current = true;
      onSubmit(value);
    };
    const cancel = () => {
      if (doneRef.current) return;
      doneRef.current = true;
      onCancel();
    };
    return (
      <input
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onFocus={(e) => e.currentTarget.select()}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            submit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            cancel();
          }
        }}
        onBlur={submit}
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "min-w-0 flex-1 rounded-md border border-ring/60 bg-background px-1.5 py-0.5 text-sm text-foreground outline-none",
          className,
        )}
      />
    );
  }
  ```
  同时更新文件顶部的 React 导入(它目前只导入 `useState`)—— 把:
  ```tsx
  import { useState } from "react";
  ```
  替换为:
  ```tsx
  import { useRef, useState } from "react";
  ```

- [ ] 给 `ProjectNode` 的项目行加上行内重命名(`frontend/src/components/ProjectSidebar.tsx`)。

  (a) 在已有的 `menuOpen` 状态旁加上编辑状态(约第 190 行):
  ```tsx
    const [editing, setEditing] = useState(false);
  ```
  (b) 替换项目标题的渲染。当前主点击按钮是:
  ```tsx
          <button
            type="button"
            aria-current={selected ? "true" : undefined}
            onClick={() => onSelectProject(project)}
            className="flex min-w-0 flex-1 items-center gap-2 py-2 pr-16 text-left outline-none"
          >
            <FolderClosed
              className={cn(
                "size-4 shrink-0 transition-colors",
                selected
                  ? "text-foreground"
                  : "text-muted-foreground group-hover/row:text-foreground",
              )}
              strokeWidth={selected ? 2.25 : 2}
            />
            <span
              className={cn(
                "truncate font-medium",
                selected && "text-foreground",
              )}
            >
              {project.title}
            </span>
          </button>
  ```
  把它替换为(`editing` 时渲染行内 input 而非点击按钮;双击标题进入编辑):
  ```tsx
          {editing ? (
            <div className="flex min-w-0 flex-1 items-center gap-2 py-2 pr-16">
              <FolderClosed className="size-4 shrink-0 text-foreground" strokeWidth={2.25} />
              <InlineRename
                initial={project.title}
                onSubmit={(v) => {
                  setEditing(false);
                  onRenameProject(project, v);
                }}
                onCancel={() => setEditing(false)}
              />
            </div>
          ) : (
            <button
              type="button"
              aria-current={selected ? "true" : undefined}
              onClick={() => onSelectProject(project)}
              onDoubleClick={() => setEditing(true)}
              className="flex min-w-0 flex-1 items-center gap-2 py-2 pr-16 text-left outline-none"
            >
              <FolderClosed
                className={cn(
                  "size-4 shrink-0 transition-colors",
                  selected
                    ? "text-foreground"
                    : "text-muted-foreground group-hover/row:text-foreground",
                )}
                strokeWidth={selected ? 2.25 : 2}
              />
              <span
                className={cn(
                  "truncate font-medium",
                  selected && "text-foreground",
                )}
              >
                {project.title}
              </span>
            </button>
          )}
  ```
  (c) 给项目 kebab 加上「重命名」。当前 `DropdownMenuContent` 是:
  ```tsx
              <DropdownMenuContent align="end" sideOffset={4}>
                <DropdownMenuItem onSelect={() => onReindexProject(project)}>
                  <RefreshCw />
                  重建索引
                </DropdownMenuItem>
                <DropdownMenuItem
                  variant="destructive"
                  onSelect={() => onDeleteProject(project)}
                >
                  <Trash2 />
                  删除项目
                </DropdownMenuItem>
              </DropdownMenuContent>
  ```
  把它替换为(在「重建索引」上方加一个「重命名」项):
  ```tsx
              <DropdownMenuContent align="end" sideOffset={4}>
                <DropdownMenuItem onSelect={() => setEditing(true)}>
                  <Pencil />
                  重命名
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => onReindexProject(project)}>
                  <RefreshCw />
                  重建索引
                </DropdownMenuItem>
                <DropdownMenuItem
                  variant="destructive"
                  onSelect={() => onDeleteProject(project)}
                >
                  <Trash2 />
                  删除项目
                </DropdownMenuItem>
              </DropdownMenuContent>
  ```
  (d) 把 `Pencil` 图标加进 lucide 导入。把:
  ```tsx
  import {
    ChevronRight,
    FolderClosed,
    MoreHorizontal,
    PenLine,
    Plus,
    RefreshCw,
    Trash2,
  } from "lucide-react";
  ```
  替换为:
  ```tsx
  import {
    ChevronRight,
    FolderClosed,
    MoreHorizontal,
    Pencil,
    PenLine,
    Plus,
    RefreshCw,
    Trash2,
  } from "lucide-react";
  ```

- [ ] 给 `ChatRow` 的对话行加上行内重命名,并把 `onRenameConversation` 经 `ChatChildren` 传过去(`frontend/src/components/ProjectSidebar.tsx`)。

  (a) `ChatChildren` —— 加进它解构出的参数(在约第 324 行 `onDeleteConversation,` 之后)及其 prop 类型(在约第 324 行 `onDeleteConversation` 类型之后):
  ```tsx
    onRenameConversation,
  ```
  ```tsx
    onRenameConversation: (conversation: Conversation, title: string) => void;
  ```
  然后把它传进每个 `<ChatRow .../>`(在约第 364 行 `onDelete={onDeleteConversation}` 之后):
  ```tsx
              onRename={onRenameConversation}
  ```
  (b) `ChatRow` —— 加进它解构出的参数(在约第 381 行 `onDelete,` 之后)及 prop 类型(在约第 382 行 `onDelete` 类型之后):
  ```tsx
    onRename,
  ```
  ```tsx
    onRename: (conversation: Conversation, title: string) => void;
  ```
  在已有的 `menuOpen` 旁加上编辑状态(约第 383 行):
  ```tsx
    const [editing, setEditing] = useState(false);
  ```
  替换对话标题按钮:
  ```tsx
          <button
            type="button"
            aria-current={active ? "true" : undefined}
            onClick={() => onSelect(conversation)}
            className="flex min-w-0 flex-1 items-center px-2.5 py-1.5 pr-8 text-left outline-none"
          >
            <span className="truncate">{conversation.title}</span>
          </button>
  ```
  替换为(editing 时显示行内 input;双击进入编辑):
  ```tsx
          {editing ? (
            <div className="flex min-w-0 flex-1 items-center px-2.5 py-1.5 pr-8">
              <InlineRename
                initial={conversation.title}
                onSubmit={(v) => {
                  setEditing(false);
                  onRename(conversation, v);
                }}
                onCancel={() => setEditing(false)}
              />
            </div>
          ) : (
            <button
              type="button"
              aria-current={active ? "true" : undefined}
              onClick={() => onSelect(conversation)}
              onDoubleClick={() => setEditing(true)}
              className="flex min-w-0 flex-1 items-center px-2.5 py-1.5 pr-8 text-left outline-none"
            >
              <span className="truncate">{conversation.title}</span>
            </button>
          )}
  ```
  (c) 给对话 kebab 加上「重命名」。把:
  ```tsx
            <DropdownMenuContent align="end" sideOffset={4}>
              <DropdownMenuItem
                variant="destructive"
                onSelect={() => onDelete(conversation)}
              >
                <Trash2 />
                删除
              </DropdownMenuItem>
            </DropdownMenuContent>
  ```
  替换为:
  ```tsx
            <DropdownMenuContent align="end" sideOffset={4}>
              <DropdownMenuItem onSelect={() => setEditing(true)}>
                <Pencil />
                重命名
              </DropdownMenuItem>
              <DropdownMenuItem
                variant="destructive"
                onSelect={() => onDelete(conversation)}
              >
                <Trash2 />
                删除
              </DropdownMenuItem>
            </DropdownMenuContent>
  ```

- [ ] 构建以对前端接线做类型检查。
  ```
  npm run build
  ```
  (从 `/Users/william/Desktop/EpicTrace/frontend` 运行。)预期:退出码 0,无 TypeScript 错误。如果它报告未使用的导入或 prop 类型不匹配,修复那个具体符号并重跑。

- [ ] 提交。
  ```
  git add frontend/src/lib/api.ts frontend/src/components/ProjectSidebar.tsx frontend/src/views/ProjectsConversationView.tsx
  git commit -m "Add inline rename for conversations and projects in sidebar" -m "$(cat <<'EOF'
  api.renameConversation/renameProject (PATCH). Sidebar kebab "重命名" + double-
  click title enters a controlled inline input (autofocus, Enter/blur submit,
  Esc cancel; empty-or-unchanged skips the request). Optimistic update with
  rollback on failure for both projects and conversations.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 5 —— 验证

后端全套测试绿;前端 build 干净。

**Files:** 无(仅验证)。

- [ ] 从 `/Users/william/Desktop/EpicTrace/backend` 运行后端全套测试。
  ```
  ./.venv/bin/pytest -q
  ```
  预期:所有测试通过(无失败、无错误)。注意:某些 `*_slow` / `*_smoke` 测试会下载真实模型 —— 若 runner 一直在跳过它们,保持同样的跳过行为;不要把某个无关的既有 skip/xfail 当成失败计入。新文件(`test_chat_title.py`、`test_api_rename.py`)与已编辑文件(`test_chat_service.py`、`test_chat_agent_routing.py`、`test_projects_service.py`)都必须是绿的。

- [ ] 从 `/Users/william/Desktop/EpicTrace/frontend` 运行前端 build。
  ```
  npm run build
  ```
  预期:退出码 0。

- [ ] 对照 spec(`docs/superpowers/specs/2026-06-14-conversation-project-ux-design.md`)做最终自检:
  - 3.1 标题用 Q+A 配合收紧后的 `TITLE_SYS`,两处调用点都传 `answer`,回退不变 —— Task 1。✔
  - 3.2 `PATCH /api/conversations/{id}` trim + 非空(400)+ maxlen + 更新 + 404 —— Task 2。✔
  - 3.3 `PATCH /api/projects/{id}` 同样校验 + 更新标题,`folder_path` 不变 + 404 —— Task 3。✔
  - 3.4 `api.renameConversation`/`renameProject`;两个菜单上的 kebab「重命名」;行内受控 input(autofocus、Enter/失焦提交、Esc 取消;空或未变 → 不发请求);双击标题进入编辑;乐观 + 回滚 —— Task 4。✔
  - 「不做」:不移动/重命名文件夹、不回填旧标题、不批量重命名、不做 Batch B —— 都没加。✔
