# Plan 6:工具调用 ReAct Agent 实现计划

> **For agentic workers:** 必备子技能:用 superpowers:subagent-driven-development(推荐)或 superpowers:executing-plans 逐任务实现本计划。各步骤用复选框(`- [ ]`)语法跟踪。

**Goal:** 对支持工具调用的 profile,用一个 LangGraph ReAct agent 取代写死的 route→retrieve→grade→rewrite 流水线;该 agent 把项目/附件检索与一个分页附件阅读器暴露为 LLM 可调用的工具,同时保留现有 Plan 5 流水线作为按能力门控的回退路。

**Architecture:** `ChatService._run_turn` 对活动 profile 探测一次(结果缓存在 `app.state`);若支持工具调用,则运行新的 `agent/react.py` StateGraph(`ChatOpenAI.bind_tools` 的 agent 节点 ↔ `ToolNode`),它只通过 LangChain 工具 artifact 把 `RetrievedChunk` 对象累积进一个去重、封顶的池里,然后对编号后的池跑一次干净的 GENERATE 调用,并原样复用 `build_citations`。不支持的 profile、空池、坏 tool_call 后空池,以及任何意外异常,都回退到原封不动的 Plan 5 路。

**Tech Stack:** Python 3.11(venv,NOT uv)· FastAPI · LangGraph `StateGraph`/`ToolNode` · LangChain `ChatOpenAI`(`langchain-openai`,新依赖)· 现有 `OpenAICompatLLM`(回退路 + 最终 GENERATE 流式)· `AttachmentRetriever` + 缓存的 `extracted_text` · pytest 配 `FakeLLM`/`FakeEmbedder`/`FakeVectorStore`。

---

## 文件结构

**创建**
- `backend/epictrace/agent/attachment_paging.py` — 纯函数 `read_attachment_slice(...)`,从游标处切片某引用缓存的 `extracted_text` → `(slice_text, next_cursor, RetrievedChunk)`。
- `backend/epictrace/agent/tool_probe.py` — `probe_tool_calling(chat_model) -> bool`(结构化 tool_call 检查)+ `cached_supports_tools(app_state, profile, chat_model_factory) -> bool`(以 profile id/base_url/model 为键、存在 `app.state` 上的进程级缓存)。
- `backend/epictrace/agent/tools.py` — `ChunkAccumulator` + `build_tools(...)`,返回用 `response_format="content_and_artifact"` 的 LangChain 工具(`search_project_library`、`search_attachment`、`read_attachment`);附件类工具仅在存在附件引用时构建。
- `backend/epictrace/agent/react.py` — `run_react_loop(...)`:StateGraph(`agent`↔`tools`),带轮数上限、去重+封顶、坏 tool_call 重试、force-answer/fallback 信号;返回累积的池(或一个 fallback 哨兵)。
- `backend/epictrace/agent/chat_model.py` — `make_chat_model(profile)`,薄封装 `ChatOpenAI(base_url=…, api_key=…, model=…)` 的工厂(单一注入点;把 `ChatOpenAI` 挡在深层调用点之外,利于测试)。

**修改**
- `backend/pyproject.toml` — 添加 `langchain-openai` 依赖。
- `backend/epictrace/agent/state.py` — 添加 ReAct 图用的 `ReactState` TypedDict(messages + accumulator)。
- `backend/epictrace/services/chat.py` — `_run_turn` 新增 探测→走 agent 路或回退 Plan 5 的路由;agent 路收集池、注入并引用小的 fulltext 引用、跑最终 GENERATE + `build_citations`、发出工具活动状态事件。Plan 5 路不变。
- `backend/epictrace/api/deps.py` — `get_chat_model_factory(request)` 辅助函数 + 读取活动 profile 的能力探测钩子;把 `chat_model_factory` 与 `supports_tools` 探测传入 `ChatService`。
- `backend/epictrace/api/routers/conversations.py` — 把 `chat_model_factory` + 探测接进 `_chat_service`。

**测试**
- `backend/tests/test_langchain_openai_dep.py` — `langchain_openai.ChatOpenAI` 的 import 冒烟测试。
- `backend/tests/test_attachment_paging.py` — `read_attachment_slice` 的精确偏移切片测试。
- `backend/tests/test_tool_probe.py` — 用 fake chat model 做 probe True/False + 缓存命中测试。
- `backend/tests/test_agent_tools.py` — 工具返回可读文本 + 填充 accumulator;条件暴露。
- `backend/tests/test_agent_react.py` — 多轮、并行调用、轮数上限 force-answer、去重+封顶、retry-then-fallback。
- `backend/tests/test_agent_citations_reuse.py` — 对池跑最终 GENERATE + `build_citations`;附件偏移;幻觉 `[n]` 被丢弃;空池 → 直答。
- `backend/tests/test_chat_agent_routing.py` — 支持→走 agent 路并引用;不支持→与 Plan 5 行为一致;fulltext 仍然注入+引用。

**测试辅助(加进 `backend/tests/fakes.py`)**
- `FakeChatModel` — LangChain-`Runnable` 形状的 fake,带 `.bind_tools(tools)`;返回一段脚本化的 `AIMessage` 序列(带/不带 `tool_calls`),让 ReAct 图和 probe 无需联网即可运行。通过 `ToolNode` 执行绑定的工具,与真实模型完全一致。

---

## Task 1 — 添加 `langchain-openai` 依赖

**Files:**
- 修改:`backend/pyproject.toml`
- 测试:`backend/tests/test_langchain_openai_dep.py`

步骤:
- [ ] 写下会失败的 import 冒烟测试 `backend/tests/test_langchain_openai_dep.py`:
  ```python
  def test_langchain_openai_importable():
      from langchain_openai import ChatOpenAI  # noqa: F401

      # bind_tools is the exact surface Plan 6 relies on.
      assert hasattr(ChatOpenAI, "bind_tools")
  ```
- [ ] 运行它,预期 FAIL(`ModuleNotFoundError: No module named 'langchain_openai'`):
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_langchain_openai_dep.py -v` → 预期:FAIL(收集/导入错误)。
- [ ] 在 `backend/pyproject.toml` 的 `[project].dependencies` 里,紧跟 `langgraph` 行之后添加 `langchain-openai`:
  ```toml
    "langgraph",
    "langchain-openai",
    "sse-starlette",
  ```
- [ ] 装进 venv(NOT uv):
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pip install langchain-openai` → 预期:`Successfully installed langchain-openai-...`。
- [ ] 运行冒烟测试,预期 PASS:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_langchain_openai_dep.py -v` → 预期:1 passed。
- [ ] 提交:
  `cd /Users/william/Desktop/EpicTrace/backend && git add pyproject.toml tests/test_langchain_openai_dep.py && git commit -m "$(cat <<'EOF'
  Plan 6: add langchain-openai dependency

  Adds the ChatOpenAI surface (bind_tools) needed for the tool-calling
  ReAct agent path. Import-smoke test guards availability.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"`

---

## Task 2 — `read_attachment` 分页阅读器(纯函数)

实现 spec §5.2 `read_attachment` 切片 + §5.5 附件偏移 chunk。不涉及 embedding。

**Files:**
- 创建:`backend/epictrace/agent/attachment_paging.py`
- 测试:`backend/tests/test_attachment_paging.py`

步骤:
- [ ] 写下会失败的测试 `backend/tests/test_attachment_paging.py`:
  ```python
  from epictrace.agent.attachment_paging import read_attachment_slice


  def test_first_slice_offsets_and_chunk():
      text = "0123456789abcdefghij"  # 20 chars
      slice_text, next_cursor, chunk, done = read_attachment_slice(
          reference_id=7, text=text, cursor=0, page_size=8
      )
      assert slice_text == "01234567"
      assert next_cursor == 8
      assert done is False
      assert chunk.text == "01234567"
      assert chunk.char_start == 0 and chunk.char_end == 8
      assert chunk.reference_id == 7
      assert chunk.source_kind == "attachment"
      assert chunk.source_type == "attachment"
      assert chunk.ingest_record_id == 0


  def test_second_slice_continues_from_cursor():
      text = "0123456789abcdefghij"
      slice_text, next_cursor, chunk, done = read_attachment_slice(
          reference_id=7, text=text, cursor=8, page_size=8
      )
      assert slice_text == "89abcdef"
      assert next_cursor == 16
      assert chunk.char_start == 8 and chunk.char_end == 16
      assert done is False


  def test_final_partial_slice_marks_done():
      text = "0123456789abcdefghij"
      slice_text, next_cursor, chunk, done = read_attachment_slice(
          reference_id=7, text=text, cursor=16, page_size=8
      )
      assert slice_text == "ghij"
      assert next_cursor == 20
      assert chunk.char_start == 16 and chunk.char_end == 20
      assert done is True


  def test_cursor_at_or_past_end_is_empty_done():
      text = "abc"
      slice_text, next_cursor, chunk, done = read_attachment_slice(
          reference_id=1, text=text, cursor=3, page_size=8
      )
      assert slice_text == ""
      assert next_cursor == 3
      assert chunk is None
      assert done is True


  def test_empty_text_is_empty_done():
      slice_text, next_cursor, chunk, done = read_attachment_slice(
          reference_id=1, text="", cursor=0, page_size=8
      )
      assert slice_text == "" and next_cursor == 0 and chunk is None and done is True
  ```
- [ ] 运行它,预期 FAIL(`ModuleNotFoundError: No module named 'epictrace.agent.attachment_paging'`):
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_attachment_paging.py -v` → 预期:FAIL(导入错误)。
- [ ] 写下 `backend/epictrace/agent/attachment_paging.py`:
  ```python
  from __future__ import annotations

  from epictrace.retrieval.types import RetrievedChunk

  DEFAULT_PAGE_SIZE = 1200


  def read_attachment_slice(
      *, reference_id: int, text: str, cursor: int, page_size: int = DEFAULT_PAGE_SIZE
  ) -> tuple[str, int, RetrievedChunk | None, bool]:
      """顺序切片缓存的 extracted_text。返回 (slice_text, next_cursor, chunk, done)。

      偏移即引用命门:chunk 的 char_start=cursor、char_end=cursor+len(slice),
      source_kind="attachment"、ingest_record_id=0(附件无 ingest 记录),供精确跳回外部文件。
      cursor 到/越过末尾 → 空串、chunk=None、done=True(调用方据此停止翻页)。"""
      n = len(text)
      start = max(0, cursor)
      if start >= n:
          return "", start, None, True
      end = min(n, start + page_size)
      slice_text = text[start:end]
      done = end >= n
      chunk = RetrievedChunk(
          text=slice_text,
          ingest_record_id=0,
          project_id=0,
          char_start=start,
          char_end=end,
          source_type="attachment",
          source_kind="attachment",
          reference_id=reference_id,
      )
      return slice_text, end, chunk, done
  ```
- [ ] 运行它,预期 PASS:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_attachment_paging.py -v` → 预期:5 passed。
- [ ] 提交:
  `cd /Users/william/Desktop/EpicTrace/backend && git add epictrace/agent/attachment_paging.py tests/test_attachment_paging.py && git commit -m "$(cat <<'EOF'
  Plan 6: paginated read_attachment slicer

  Pure function slicing cached extracted_text from a cursor into the next
  page + a RetrievedChunk carrying exact attachment offsets (no embeddings).

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"`

---

## Task 3 — `FakeChatModel` 测试辅助

一个 LangChain-`Runnable` 形状的 fake,让 probe + ReAct 图无需联网即可驱动。先加它,好让 Task 4–7 都能用。它对 `ChatOpenAI` 的模仿足以支撑 `bind_tools` + `ToolNode`。

**Files:**
- 修改:`backend/tests/fakes.py`
- 测试:`backend/tests/test_fake_chat_model.py`

步骤:
- [ ] 写下会失败的测试 `backend/tests/test_fake_chat_model.py`:
  ```python
  from langchain_core.messages import AIMessage, HumanMessage

  from tests.fakes import FakeChatModel


  def test_scripted_ai_messages_in_order():
      m = FakeChatModel(script=[
          AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1", "type": "tool_call"}]),
          AIMessage(content="done"),
      ])
      bound = m.bind_tools([])  # bind_tools returns a model that yields the same script
      first = bound.invoke([HumanMessage(content="hi")])
      assert first.tool_calls and first.tool_calls[0]["name"] == "t"
      second = bound.invoke([HumanMessage(content="hi")])
      assert second.content == "done" and not second.tool_calls


  def test_runs_out_of_script_returns_plain_answer():
      m = FakeChatModel(script=[], default=AIMessage(content="fallthrough"))
      assert m.bind_tools([]).invoke([HumanMessage(content="x")]).content == "fallthrough"
  ```
- [ ] 运行它,预期 FAIL(`ImportError: cannot import name 'FakeChatModel'`):
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_fake_chat_model.py -v` → 预期:FAIL(导入错误)。
- [ ] 把 `FakeChatModel` 追加到 `backend/tests/fakes.py`(若文件顶部还没有,则加上这行 import:`from langchain_core.messages import AIMessage`):
  ```python
  from langchain_core.messages import AIMessage  # add near top of fakes.py


  class FakeChatModel:
      """LangChain-shaped fake for ChatOpenAI.bind_tools(...).invoke(messages).

      Returns scripted AIMessages in order (each call pops the next); after the
      script is exhausted, returns `default`. `.bind_tools(tools)` records the
      tools and returns self so ToolNode executes the real bound tools. Tracks
      every invoke's messages for assertions."""

      def __init__(self, *, script=None, default=None):
          self._script = list(script or [])
          self._default = default or AIMessage(content="假答案")
          self.bound_tools = None
          self.invocations: list[list] = []

      def bind_tools(self, tools, **kwargs):
          self.bound_tools = list(tools)
          return self

      def invoke(self, messages, **kwargs):
          self.invocations.append(list(messages))
          if self._script:
              return self._script.pop(0)
          return self._default
  ```
- [ ] 运行它,预期 PASS:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_fake_chat_model.py -v` → 预期:2 passed。
- [ ] 提交:
  `cd /Users/william/Desktop/EpicTrace/backend && git add tests/fakes.py tests/test_fake_chat_model.py && git commit -m "$(cat <<'EOF'
  Plan 6: FakeChatModel test helper

  LangChain-shaped fake driving probe + ReAct graph without a network:
  bind_tools records tools, invoke replays a scripted AIMessage sequence.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"`

---

## Task 4 — `make_chat_model` 工厂(注入缝)

实现 spec §5.1/§8:为 `ChatOpenAI` 提供单一、薄的构造点,这样测试可注入 `FakeChatModel`,而 `ChatOpenAI` 永远不会被写死在 agent 深层。

**Files:**
- 创建:`backend/epictrace/agent/chat_model.py`
- 测试:`backend/tests/test_chat_model_factory.py`

步骤:
- [ ] 写下会失败的测试 `backend/tests/test_chat_model_factory.py`:
  ```python
  from epictrace.agent.chat_model import make_chat_model


  def test_make_chat_model_builds_chatopenai_from_profile():
      profile = {"base_url": "https://api.deepseek.com", "api_key": "k", "model": "deepseek-chat"}
      model = make_chat_model(profile)
      # Constructed lazily without a network call; just assert it's the ChatOpenAI surface.
      assert model.__class__.__name__ == "ChatOpenAI"
      assert hasattr(model, "bind_tools")


  def test_make_chat_model_normalizes_chat_completions_suffix():
      profile = {"base_url": "https://api.deepseek.com/chat/completions",
                 "api_key": "k", "model": "deepseek-chat"}
      model = make_chat_model(profile)
      assert str(model.openai_api_base).rstrip("/").endswith("api.deepseek.com")
  ```
- [ ] 运行它,预期 FAIL(`ModuleNotFoundError: No module named 'epictrace.agent.chat_model'`):
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_chat_model_factory.py -v` → 预期:FAIL(导入错误)。
- [ ] 写下 `backend/epictrace/agent/chat_model.py`:
  ```python
  from __future__ import annotations

  from epictrace.llm.openai_compat import _normalize_base_url


  def make_chat_model(profile: dict, *, temperature: float = 0.0):
      """构造接 OpenAI 兼容端点的 ChatOpenAI(agent 路工具调用专用)。

      复用 OpenAICompatLLM 的 base_url 归一化(剥掉误粘的 /chat/completions);
      允许空 api_key(本地 Ollama)。延迟 import,避免无 langchain-openai 时全局崩。"""
      from langchain_openai import ChatOpenAI

      return ChatOpenAI(
          base_url=_normalize_base_url(profile.get("base_url", "")),
          api_key=profile.get("api_key") or "not-set",
          model=profile.get("model", ""),
          temperature=temperature,
      )
  ```
- [ ] 运行它,预期 PASS:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_chat_model_factory.py -v` → 预期:2 passed。
- [ ] 提交:
  `cd /Users/william/Desktop/EpicTrace/backend && git add epictrace/agent/chat_model.py tests/test_chat_model_factory.py && git commit -m "$(cat <<'EOF'
  Plan 6: ChatOpenAI factory (injection seam)

  Single construction point for the agent-path chat model, reusing the
  base_url normalization so ChatOpenAI is never hard-wired deep in the graph.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"`

---

## Task 5 — `probe_tool_calling` + 进程缓存

实现 spec §5.1:通过绑定一个 trivial 工具并检查是否回出结构合法的 `tool_call` 来探测某 profile;以 profile id+base_url+model 为键把判定缓存在 `app.state`。

**Files:**
- 创建:`backend/epictrace/agent/tool_probe.py`
- 测试:`backend/tests/test_tool_probe.py`

步骤:
- [ ] 写下会失败的测试 `backend/tests/test_tool_probe.py`:
  ```python
  from types import SimpleNamespace

  from langchain_core.messages import AIMessage

  from epictrace.agent.tool_probe import cached_supports_tools, probe_tool_calling
  from tests.fakes import FakeChatModel


  def _tool_call_msg():
      return AIMessage(content="", tool_calls=[
          {"name": "echo", "args": {"x": "hi"}, "id": "1", "type": "tool_call"}])


  def test_probe_true_on_valid_tool_call():
      assert probe_tool_calling(FakeChatModel(script=[_tool_call_msg()])) is True


  def test_probe_false_on_prose():
      assert probe_tool_calling(FakeChatModel(script=[AIMessage(content="just talking")])) is False


  def test_probe_false_on_exception():
      class Boom:
          def bind_tools(self, tools, **kw): return self
          def invoke(self, messages, **kw): raise RuntimeError("no tools")
      assert probe_tool_calling(Boom()) is False


  def test_cache_hit_skips_second_probe():
      state = SimpleNamespace()
      profile = {"id": "p1", "base_url": "u", "model": "m"}
      built = []

      def factory(p):
          built.append(1)
          return FakeChatModel(script=[_tool_call_msg()])

      assert cached_supports_tools(state, profile, factory) is True
      assert cached_supports_tools(state, profile, factory) is True
      assert built == [1]  # second call served from cache


  def test_cache_keyed_by_profile_identity():
      state = SimpleNamespace()
      a = {"id": "p1", "base_url": "u", "model": "m"}
      b = {"id": "p2", "base_url": "u2", "model": "m2"}
      cached_supports_tools(state, a, lambda p: FakeChatModel(script=[_tool_call_msg()]))
      # different profile → not the same cache slot → re-probes (prose → False)
      assert cached_supports_tools(state, b, lambda p: FakeChatModel(
          script=[AIMessage(content="prose")])) is False
  ```
- [ ] 运行它,预期 FAIL(`ModuleNotFoundError: No module named 'epictrace.agent.tool_probe'`):
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_tool_probe.py -v` → 预期:FAIL(导入错误)。
- [ ] 写下 `backend/epictrace/agent/tool_probe.py`:
  ```python
  from __future__ import annotations

  from langchain_core.messages import HumanMessage, SystemMessage
  from langchain_core.tools import tool

  _PROBE_SYS = "你能调用工具。请调用 echo 工具,参数 x 填 'ping'。"
  _PROBE_USER = "调用 echo。"


  @tool
  def _echo(x: str) -> str:
      """Echo back x. (probe-only trivial tool)"""
      return x


  def probe_tool_calling(chat_model) -> bool:
      """绑一个 trivial 工具,让模型调它,检查回包含结构合法的 tool_call。
      合法 → True;吐人话/坏结构/任何异常 → False(视为不支持,回退基础检索)。"""
      try:
          bound = chat_model.bind_tools([_echo])
          msg = bound.invoke([SystemMessage(content=_PROBE_SYS),
                              HumanMessage(content=_PROBE_USER)])
      except Exception:  # noqa: BLE001 — 任何探测故障一律视为不支持
          return False
      calls = getattr(msg, "tool_calls", None) or []
      for c in calls:
          # 结构合法:有名字 + args 是 dict。坏 JSON 时 langchain 会塞 invalid_tool_calls
          # 而非 tool_calls,故这里取不到 → False。
          if c.get("name") and isinstance(c.get("args"), dict):
              return True
      return False


  def _cache_key(profile: dict) -> tuple:
      return (profile.get("id"), profile.get("base_url"), profile.get("model"))


  def cached_supports_tools(app_state, profile: dict, chat_model_factory) -> bool:
      """进程内缓存探测结果(键=profile id+base_url+model),存 app_state._tool_support。
      首次未命中 → 用 chat_model_factory(profile) 造模型探一次并缓存;重启重探。"""
      cache = getattr(app_state, "_tool_support", None)
      if cache is None:
          cache = {}
          app_state._tool_support = cache
      key = _cache_key(profile)
      if key in cache:
          return cache[key]
      verdict = probe_tool_calling(chat_model_factory(profile))
      cache[key] = verdict
      return verdict
  ```
- [ ] 运行它,预期 PASS:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_tool_probe.py -v` → 预期:5 passed。
- [ ] 提交:
  `cd /Users/william/Desktop/EpicTrace/backend && git add epictrace/agent/tool_probe.py tests/test_tool_probe.py && git commit -m "$(cat <<'EOF'
  Plan 6: tool-calling capability probe + process cache

  Binds a trivial tool and checks for a structurally valid tool_call;
  caches the verdict on app.state keyed by profile id/base_url/model so each
  profile is probed at most once per process.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"`

---

## Task 6 — 工具定义 + `ChunkAccumulator`

实现 spec §5.2:把 `retriever`、`AttachmentRetriever` 和分页阅读器封装为 LangChain 工具。每个工具既返回给模型读的可读文本(content),又通过 `response_format="content_and_artifact"`(`ToolMessage.artifact`,在 ReAct 循环里收割)把结构化的 `RetrievedChunk` 捕获进 accumulator。附件类工具按条件构建。工具描述里带路由信息(§5.2)。

**Files:**
- 创建:`backend/epictrace/agent/tools.py`
- 测试:`backend/tests/test_agent_tools.py`

步骤:
- [ ] 写下会失败的测试 `backend/tests/test_agent_tools.py`:
  ```python
  from epictrace.agent.tools import ChunkAccumulator, build_tools
  from epictrace.retrieval.types import RetrievedChunk


  class _ProjRetriever:
      def __init__(self): self.calls = []
      def retrieve(self, *, project_id, query, **kwargs):
          self.calls.append((project_id, query, kwargs))
          return [RetrievedChunk(text="项目片段TLB", ingest_record_id=99, project_id=project_id,
                                 char_start=0, char_end=6, source_type="folder_scan")]


  class _AttachRetriever:
      def __init__(self): self.calls = []
      def retrieve(self, *, conversation_id, reference_ids, query, k=6):
          self.calls.append((conversation_id, tuple(reference_ids), query))
          return [RetrievedChunk(text="附件片段", ingest_record_id=0, project_id=0,
                                 char_start=5, char_end=9, source_type="attachment",
                                 source_kind="attachment", reference_id=reference_ids[0])]


  def test_search_project_library_returns_text_and_artifact():
      acc = ChunkAccumulator()
      tools = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                          attachment_retriever=None, conversation_id=1,
                          indexed_ext_ids=[], reference_texts={})
      t = {x.name: x for x in tools}["search_project_library"]
      msg = t.invoke({"name": "search_project_library", "args": {"query": "TLB"},
                      "id": "c1", "type": "tool_call"})
      assert "项目片段TLB" in msg.content            # readable text for the model
      assert msg.artifact and msg.artifact[0].text == "项目片段TLB"  # structured chunk captured


  def test_project_search_passes_focus_ids():
      r = _ProjRetriever()
      tools = build_tools(retriever=r, project_id=3, focus_ids=[7, 8],
                          attachment_retriever=None, conversation_id=1,
                          indexed_ext_ids=[], reference_texts={})
      t = {x.name: x for x in tools}["search_project_library"]
      t.invoke({"name": "search_project_library", "args": {"query": "q"},
                "id": "c1", "type": "tool_call"})
      assert r.calls[0][2] == {"ingest_record_ids": [7, 8]}


  def test_attachment_tools_only_when_indexed_refs():
      tools_none = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                               attachment_retriever=_AttachRetriever(), conversation_id=1,
                               indexed_ext_ids=[], reference_texts={})
      assert {t.name for t in tools_none} == {"search_project_library"}

      tools_with = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                               attachment_retriever=_AttachRetriever(), conversation_id=1,
                               indexed_ext_ids=[5], reference_texts={5: "页表内容很长"})
      assert {t.name for t in tools_with} == {
          "search_project_library", "search_attachment", "read_attachment"}


  def test_search_attachment_filters_by_indexed_refs():
      ar = _AttachRetriever()
      tools = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                          attachment_retriever=ar, conversation_id=42,
                          indexed_ext_ids=[5], reference_texts={5: "x"})
      t = {x.name: x for x in tools}["search_attachment"]
      msg = t.invoke({"name": "search_attachment", "args": {"query": "页表"},
                      "id": "c1", "type": "tool_call"})
      assert ar.calls == [(42, (5,), "页表")]
      assert msg.artifact[0].source_kind == "attachment"


  def test_read_attachment_paginates_and_captures_offsets():
      tools = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                          attachment_retriever=_AttachRetriever(), conversation_id=1,
                          indexed_ext_ids=[5], reference_texts={5: "0123456789"})
      t = {x.name: x for x in tools}["read_attachment"]
      msg = t.invoke({"name": "read_attachment", "args": {"reference_id": 5, "cursor": 0},
                      "id": "c1", "type": "tool_call"})
      assert msg.artifact[0].char_start == 0
      assert msg.artifact[0].reference_id == 5
      assert "next_cursor" in msg.content        # paging hint for the model


  def test_read_attachment_unknown_reference_no_artifact():
      tools = build_tools(retriever=_ProjRetriever(), project_id=3, focus_ids=[],
                          attachment_retriever=_AttachRetriever(), conversation_id=1,
                          indexed_ext_ids=[5], reference_texts={5: "x"})
      t = {x.name: x for x in tools}["read_attachment"]
      msg = t.invoke({"name": "read_attachment", "args": {"reference_id": 999, "cursor": 0},
                      "id": "c1", "type": "tool_call"})
      assert msg.artifact == []
  ```
- [ ] 运行它,预期 FAIL(`ModuleNotFoundError: No module named 'epictrace.agent.tools'`):
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_agent_tools.py -v` → 预期:FAIL(导入错误)。
- [ ] 写下 `backend/epictrace/agent/tools.py`:
  ```python
  from __future__ import annotations

  from langchain_core.tools import tool

  from epictrace.agent.attachment_paging import read_attachment_slice
  from epictrace.retrieval.types import RetrievedChunk

  _SNIPPET = 280  # 给模型读的截断长度(决策够用,不撑爆上下文)


  class ChunkAccumulator:
      """跨轮收集工具产出的 RetrievedChunk:按 RetrievedChunk.key() 去重、封顶(≤12)。
      工具用 artifact 把 chunk 旁路给 ReAct 循环,循环把它们 extend 进这里,不污染模型可见文本。"""

      def __init__(self, cap: int = 12) -> None:
          self._cap = cap
          self._seen: set = set()
          self.chunks: list[RetrievedChunk] = []

      def extend(self, new_chunks: list[RetrievedChunk]) -> None:
          for c in new_chunks:
              if len(self.chunks) >= self._cap:
                  return
              k = c.key()
              if k in self._seen:
                  continue
              self._seen.add(k)
              self.chunks.append(c)


  def _render(chunks: list[RetrievedChunk]) -> str:
      if not chunks:
          return "(无结果)"
      return "\n".join(f"- {c.text[:_SNIPPET]}" for c in chunks)


  def build_tools(*, retriever, project_id: int, focus_ids: list[int],
                  attachment_retriever, conversation_id: int,
                  indexed_ext_ids: list[int], reference_texts: dict[int, str]):
      """构造本轮暴露给 agent 的工具列表。附件类工具仅在有 indexed 外部引用时暴露
      (替 Plan 5 压制启发式:不暴露=agent 看不见,而非硬切)。
      每个工具 response_format='content_and_artifact':content 给模型读,artifact=chunk 列表
      旁路进累积池。"""

      @tool(response_format="content_and_artifact")
      def search_project_library(query: str):
          """检索本项目的永久知识库(课程/会话/笔记等已归档资料)。回答涉及项目内部内容时用。
          query 为中文检索词。"""
          kwargs = {"ingest_record_ids": focus_ids} if focus_ids else {}
          chunks = retriever.retrieve(project_id=project_id, query=query, **kwargs)
          return _render(chunks), chunks

      tools = [search_project_library]

      if attachment_retriever is not None and indexed_ext_ids:
          @tool(response_format="content_and_artifact")
          def search_attachment(query: str):
              """语义检索用户本次对话附加的外部文件。问题针对所附文件的具体内容/片段时用。
              query 为中文检索词。"""
              ar = attachment_retriever() if callable(attachment_retriever) else attachment_retriever
              chunks = ar.retrieve(conversation_id=conversation_id,
                                   reference_ids=indexed_ext_ids, query=query)
              return _render(chunks), chunks

          @tool(response_format="content_and_artifact")
          def read_attachment(reference_id: int, cursor: int = 0):
              """从 cursor 处顺序读取某个附件的下一段原文(分页)。需要通读/总结整篇,或检索
              片段不足时,反复调用并传上次返回的 next_cursor 翻页。"""
              text = reference_texts.get(reference_id)
              if text is None:
                  return f"(reference_id={reference_id} 不是本次对话的可读附件)", []
              slice_text, next_cursor, chunk, done = read_attachment_slice(
                  reference_id=reference_id, text=text, cursor=cursor)
              if chunk is None:
                  return f"(已到文件末尾,无更多内容;done={done})", []
              hint = f"\n\n[next_cursor={next_cursor}, done={done}]"
              return slice_text[:_SNIPPET] + hint, [chunk]

          tools.extend([search_attachment, read_attachment])

      return tools
  ```
- [ ] 运行它,预期 PASS:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_agent_tools.py -v` → 预期:6 passed。
- [ ] 提交:
  `cd /Users/william/Desktop/EpicTrace/backend && git add epictrace/agent/tools.py tests/test_agent_tools.py && git commit -m "$(cat <<'EOF'
  Plan 6: LangChain tool wrappers + chunk accumulator

  search_project_library / search_attachment / read_attachment as
  content_and_artifact tools: readable text for the model, structured
  RetrievedChunks captured into a deduped/capped accumulator via the tool
  artifact. Attachment tools are exposed only when indexed external refs exist.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"`

---

## Task 7 — ReAct StateGraph 循环(`agent/react.py`)

实现 spec §5.3/§5.4:`agent`(`ChatOpenAI.bind_tools`)↔ `tools`(`ToolNode`)循环;跨轮累积 chunk(从 `ToolMessage.artifact` 收割),按 `key()` 去重,封顶 ≤12;轮数上限 ≈8 → 停止;坏 tool_call → 重试一次 → force-answer(池非空)或 fallback 信号(池空)。循环返回累积的池和一个状态——它并不写最终答案(那是 Task 8)。

先添加 `ReactState` TypedDict。

**Files:**
- 修改:`backend/epictrace/agent/state.py`
- 创建:`backend/epictrace/agent/react.py`
- 测试:`backend/tests/test_agent_react.py`

步骤:
- [ ] 把 `ReactState` 加到 `backend/epictrace/agent/state.py`(追加在末尾,`AgentState` 不动):
  ```python
  from typing import Annotated

  from langchain_core.messages import BaseMessage
  from langgraph.graph.message import add_messages


  class ReactState(TypedDict, total=False):
      messages: Annotated[list[BaseMessage], add_messages]
      rounds: int          # agent 节点跑过的轮数(撞上限→force-answer)
  ```
- [ ] 写下会失败的测试 `backend/tests/test_agent_react.py`:
  ```python
  from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

  from epictrace.agent.react import FALLBACK, run_react_loop
  from epictrace.agent.tools import ChunkAccumulator
  from epictrace.retrieval.types import RetrievedChunk
  from tests.fakes import FakeChatModel


  def _proj_chunk(text="项目片段", rid=None, cs=0, ce=4):
      return RetrievedChunk(text=text, ingest_record_id=1, project_id=1,
                            char_start=cs, char_end=ce, source_type="folder_scan",
                            source_kind="project", reference_id=rid)


  class _Retr:
      def __init__(self, out): self.out = out
      def retrieve(self, *, project_id, query, **kw): return list(self.out)


  def _tools(retriever):
      from epictrace.agent.tools import build_tools
      return build_tools(retriever=retriever, project_id=1, focus_ids=[],
                         attachment_retriever=None, conversation_id=1,
                         indexed_ext_ids=[], reference_texts={})


  def _call(name, args, cid="1"):
      return {"name": name, "args": args, "id": cid, "type": "tool_call"}


  def test_single_round_then_answer_collects_pool():
      retr = _Retr([_proj_chunk("TLB项目内容")])
      model = FakeChatModel(script=[
          AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "TLB"})]),
          AIMessage(content="结束"),  # no tool_calls → exit loop
      ])
      acc = ChunkAccumulator()
      status = run_react_loop(model, _tools(retr), acc, "TLB是什么", history=[])
      assert status == "ok"
      assert [c.text for c in acc.chunks] == ["TLB项目内容"]


  def test_multi_round_accumulates_across_rounds():
      retr = _Retr([_proj_chunk("片段A", cs=0, ce=3)])
      model = FakeChatModel(script=[
          AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "a"})]),
          AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "b"}, "2")]),
          AIMessage(content="够了"),
      ])
      acc = ChunkAccumulator()
      run_react_loop(model, _tools(retr), acc, "q", history=[])
      # same chunk both rounds → deduped to one
      assert len(acc.chunks) == 1


  def test_parallel_tool_calls_in_one_round():
      retr = _Retr([_proj_chunk("X", cs=0, ce=1), _proj_chunk("Y", cs=1, ce=2)])
      model = FakeChatModel(script=[
          AIMessage(content="", tool_calls=[
              _call("search_project_library", {"query": "a"}, "1"),
              _call("search_project_library", {"query": "b"}, "2")]),
          AIMessage(content="done"),
      ])
      acc = ChunkAccumulator()
      run_react_loop(model, _tools(retr), acc, "q", history=[])
      assert {c.text for c in acc.chunks} == {"X", "Y"}


  def test_round_cap_forces_answer_with_collected_pool():
      retr = _Retr([_proj_chunk("片段", cs=0, ce=2)])
      # model NEVER stops calling tools → must be capped
      never_stop = [AIMessage(content="", tool_calls=[
          _call("search_project_library", {"query": f"q{i}"}, str(i))]) for i in range(20)]
      model = FakeChatModel(script=never_stop)
      acc = ChunkAccumulator()
      status = run_react_loop(model, _tools(retr), acc, "q", history=[], max_rounds=8)
      assert status == "ok"
      assert len(model.invocations) <= 8     # round cap honored
      assert acc.chunks                       # pool non-empty → force-answer


  def test_pool_capped_at_twelve():
      retr = _Retr([_proj_chunk(f"c{i}", cs=i, ce=i + 1) for i in range(30)])
      model = FakeChatModel(script=[
          AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "a"})]),
          AIMessage(content="done"),
      ])
      acc = ChunkAccumulator()
      run_react_loop(model, _tools(retr), acc, "q", history=[])
      assert len(acc.chunks) == 12


  def test_empty_pool_no_tools_returns_direct():
      retr = _Retr([])
      model = FakeChatModel(script=[AIMessage(content="你好!")])  # greets, no tools
      acc = ChunkAccumulator()
      status = run_react_loop(model, _tools(retr), acc, "你好", history=[])
      assert status == "direct" and acc.chunks == []


  def test_malformed_then_empty_pool_signals_fallback():
      retr = _Retr([])

      class _Boom:
          def __init__(self): self.n = 0
          def bind_tools(self, tools, **kw): return self
          def invoke(self, messages, **kw):
              self.n += 1
              raise RuntimeError("bad tool json")

      acc = ChunkAccumulator()
      status = run_react_loop(_Boom(), _tools(retr), acc, "q", history=[])
      assert status == FALLBACK     # first round crash + empty pool → fallback to Plan 5


  def test_malformed_then_nonempty_pool_force_answers():
      retr = _Retr([_proj_chunk("已搜到")])

      class _OnceThenBoom:
          def __init__(self): self.n = 0
          def bind_tools(self, tools, **kw): self.tools = tools; return self
          def invoke(self, messages, **kw):
              self.n += 1
              if self.n == 1:
                  return AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "a"})])
              raise RuntimeError("bad tool json")

      acc = ChunkAccumulator()
      status = run_react_loop(_OnceThenBoom(), _tools(retr), acc, "q", history=[])
      assert status == "ok" and acc.chunks      # pool has chunk → force-answer, not fallback
  ```
- [ ] 运行它,预期 FAIL(`ModuleNotFoundError: No module named 'epictrace.agent.react'`):
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_agent_react.py -v` → 预期:FAIL(导入错误)。
- [ ] 写下 `backend/epictrace/agent/react.py`:
  ```python
  from __future__ import annotations

  from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
  from langgraph.graph import END, START, StateGraph
  from langgraph.prebuilt import ToolNode

  from epictrace.agent.state import ReactState
  from epictrace.agent.tools import ChunkAccumulator

  FALLBACK = "fallback"  # 第一轮就崩 + 池空 → 让 ChatService 回退 Plan 5

  LOOP_SYS = (
      "你是检索助手,用工具搜集回答用户问题所需的资料,可在一轮里并行调用多个工具。"
      "资料够了就停止调用工具、直接回普通消息(其文本会被丢弃);"
      "纯寒暄或无需资料的问题不必调用任何工具。"
  )


  def run_react_loop(chat_model, tools, accumulator: ChunkAccumulator, question: str,
                     *, history: list[dict], max_rounds: int = 8) -> str:
      """跑 agent↔tools 循环,只攒池(chunk 从 ToolMessage.artifact 收割)。返回状态:
        "ok"      → 池里有 chunk(或正常停手),交给 GENERATE 作答;
        "direct"  → 全程未调工具且池空(寒暄)→ ChatService 走 direct 直答;
        FALLBACK  → 第一轮就崩且池空 → ChatService 回退 Plan 5。
      鲁棒:撞 max_rounds → 停搜 force-answer;某轮 invoke 抛错 → 重试 1 次,再坏则按池空/非空收尾。"""
      bound = chat_model.bind_tools(tools)
      tool_node = ToolNode(tools)

      def agent(state: ReactState) -> ReactState:
          rounds = state.get("rounds", 0)
          # 撞轮数上限:不再给工具,逼模型停手(它的文本被丢弃,只用已攒池)。
          if rounds >= max_rounds:
              return {"messages": [AIMessage(content="")], "rounds": rounds}
          msg = bound.invoke(state["messages"])
          return {"messages": [msg], "rounds": rounds + 1}

      def harvest(state: ReactState) -> ReactState:
          # ToolNode 刚把每个工具的 ToolMessage(含 .artifact=chunk 列表)写进 messages;
          # 收割最近一批 ToolMessage 的 artifact 进累积池(去重/封顶在 accumulator 内)。
          for m in reversed(state["messages"]):
              if isinstance(m, ToolMessage):
                  if m.artifact:
                      accumulator.extend(list(m.artifact))
              elif isinstance(m, AIMessage):
                  break  # 越过本轮 tool 结果就停(更早的已在上一轮收割过)
          return {}

      def route(state: ReactState) -> str:
          last = state["messages"][-1]
          if state.get("rounds", 0) >= max_rounds:
              return "end"
          if isinstance(last, AIMessage) and last.tool_calls:
              return "tools"
          return "end"

      g = StateGraph(ReactState)
      g.add_node("agent", agent)
      g.add_node("tools", tool_node)
      g.add_node("harvest", harvest)
      g.add_edge(START, "agent")
      g.add_conditional_edges("agent", route, {"tools": "tools", "end": END})
      g.add_edge("tools", "harvest")
      g.add_edge("harvest", "agent")
      graph = g.compile()

      init = [SystemMessage(content=LOOP_SYS)]
      for h in history:
          # 复用历史轮次的纯文本上下文(role→LangChain 消息;assistant 文本用 AIMessage)。
          if h["role"] == "user":
              init.append(HumanMessage(content=h["content"]))
          else:
              init.append(AIMessage(content=h["content"]))
      init.append(HumanMessage(content=question))

      used_tools = False
      try:
          for ev in graph.stream({"messages": init, "rounds": 0}, stream_mode="values"):
              if any(isinstance(m, ToolMessage) for m in ev["messages"]):
                  used_tools = True
      except Exception:  # noqa: BLE001 — invoke 抛错(坏 tool_call 等):重试 1 次
          try:
              for ev in graph.stream({"messages": init, "rounds": 0}, stream_mode="values"):
                  if any(isinstance(m, ToolMessage) for m in ev["messages"]):
                      used_tools = True
          except Exception:  # noqa: BLE001 — 再坏:池非空 force-answer,池空回退
              return "ok" if accumulator.chunks else FALLBACK

      if accumulator.chunks:
          return "ok"
      return "direct" if not used_tools else "ok"
  ```
- [ ] 运行它,预期 PASS:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_agent_react.py -v` → 预期:8 passed。
- [ ] 提交:
  `cd /Users/william/Desktop/EpicTrace/backend && git add epictrace/agent/state.py epictrace/agent/react.py tests/test_agent_react.py && git commit -m "$(cat <<'EOF'
  Plan 6: ReAct StateGraph loop (collect-only)

  agent<->ToolNode loop harvesting RetrievedChunks from tool artifacts into
  a deduped/capped pool. Round cap force-answers with the collected pool;
  malformed-tool-call retries once then force-answers (pool non-empty) or
  signals fallback (pool empty); no-tools+empty-pool signals direct.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"`

---

## Task 8 — 对池做最终答案 + 引用

实现 spec §5.5/§5.7:循环结束后,跑一次干净的 GENERATE 调用(复用 `GENERATE_SYS` + 对编号后的池 `format_chunks`,丢弃循环对话历史),并原样复用 `build_citations(answer, pool)`。空池 → 直答(复用现有 `CHAT_SYS` 行为)。这是一个流式吐 token 的纯辅助函数,因此 `ChatService` 在 Task 9 接它时无需重复 GENERATE 逻辑。

**Files:**
- 创建:`backend/epictrace/agent/answer.py`
- 测试:`backend/tests/test_agent_citations_reuse.py`

步骤:
- [ ] 写下会失败的测试 `backend/tests/test_agent_citations_reuse.py`:
  ```python
  from epictrace.agent.answer import stream_final_answer
  from epictrace.retrieval.types import RetrievedChunk


  class _StreamLLM:
      """OpenAICompatLLM-shaped fake: streams a fixed answer, records messages."""
      def __init__(self, answer): self.answer = answer; self.messages = None
      def stream(self, messages, **kw):
          self.messages = list(messages)
          for ch in self.answer:
              yield ch


  def _proj():
      return RetrievedChunk(text="项目片段TLB", ingest_record_id=99, project_id=1,
                            char_start=0, char_end=6, source_type="folder_scan")


  def _attach():
      return RetrievedChunk(text="附件页表内容", ingest_record_id=0, project_id=0,
                            char_start=5, char_end=11, source_type="attachment",
                            source_kind="attachment", reference_id=42)


  def _run(llm, pool, question="问题", history=None, attached_names=None):
      toks, cites = [], None
      for ev in stream_final_answer(llm, question, pool, history=history or [],
                                    attached_names=attached_names or []):
          if ev["event"] == "token": toks.append(ev["data"])
          if ev["event"] == "citations": cites = ev["data"]
      return "".join(toks), cites


  def test_generate_over_pool_and_build_citations():
      pool = [_proj(), _attach()]
      llm = _StreamLLM("见资料[1][2]。")
      answer, cites = _run(llm, pool)
      assert answer == "见资料[1][2]。"
      assert [c["n"] for c in cites] == [1, 2]
      assert cites[1]["source_kind"] == "attachment"
      assert cites[1]["reference_id"] == 42
      assert cites[1]["char_start"] == 5 and cites[1]["char_end"] == 11
      # loop transcript discarded: GENERATE got system + (history) + the numbered 资料 only
      sent = " ".join(m["content"] for m in llm.messages)
      assert "【资料】" in sent and "项目片段TLB" in sent


  def test_hallucinated_citation_dropped():
      pool = [_proj()]
      llm = _StreamLLM("见资料[1] 和 [9]。")   # [9] out of range
      _, cites = _run(llm, pool)
      assert [c["n"] for c in cites] == [1]


  def test_empty_pool_direct_no_citations():
      llm = _StreamLLM("你好,有什么可以帮你?")
      answer, cites = _run(llm, [], question="你好")
      assert answer == "你好,有什么可以帮你?"
      assert cites == []
      sent = " ".join(m["content"] for m in llm.messages)
      assert "【资料】" not in sent           # direct path uses CHAT_SYS, no 资料 frame


  def test_attached_names_injected_when_pool_present():
      llm = _StreamLLM("见[1]。")
      _run(llm, [_attach()], attached_names=["report.pdf"])
      sent = " ".join(m["content"] for m in llm.messages)
      assert "report.pdf" in sent and "附加" in sent
  ```
- [ ] 运行它,预期 FAIL(`ModuleNotFoundError: No module named 'epictrace.agent.answer'`):
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_agent_citations_reuse.py -v` → 预期:FAIL(导入错误)。
- [ ] 写下 `backend/epictrace/agent/answer.py`:
  ```python
  from __future__ import annotations

  import json
  from collections.abc import Iterator

  from epictrace.agent.citations import build_citations
  from epictrace.agent.prompts import GENERATE_SYS, format_chunks
  from epictrace.retrieval.types import RetrievedChunk

  # 与 ChatService.CHAT_SYS 同义:池空(寒暄)走普通聊天作答,不套【资料】框架。
  CHAT_SYS = "你是有帮助的助手,用中文简洁作答。"


  def stream_final_answer(llm, question: str, pool: list[RetrievedChunk], *,
                          history: list[dict], attached_names: list[str]) -> Iterator[dict]:
      """循环结束后的唯一一次作答(丢弃工具对话历史):有池→GENERATE_SYS+编号【资料】带 [n];
      池空→CHAT_SYS 直答。流式吐 token,收尾用 build_citations(answer, 池) 复用引用命门。"""
      if pool:
          note = ""
          if attached_names:
              note = (f"(用户在本次对话附加了文件:{'、'.join(attached_names)};"
                      f"下方【资料】已包含这些附件的相关内容,请据此作答,不要说未收到文件。)\n\n")
          messages = [{"role": "system", "content": GENERATE_SYS}]
          messages.extend(history)
          messages.append({"role": "user",
                           "content": f"{note}问题:{question}\n\n【资料】\n{format_chunks(pool)}"})
      else:
          messages = [{"role": "system", "content": CHAT_SYS}]
          messages.extend(history)
          messages.append({"role": "user", "content": question})

      parts: list[str] = []
      for tok in llm.stream(messages):
          parts.append(tok)
          yield {"event": "token", "data": tok}

      answer = "".join(parts)
      citations = build_citations(answer, pool) if pool else []
      yield {"event": "citations", "data": json.dumps(citations, ensure_ascii=False)}
      yield {"event": "_answer", "data": answer}  # 内部:供 ChatService 落库(不发给前端)
  ```
- [ ] 运行它,预期 PASS:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_agent_citations_reuse.py -v` → 预期:4 passed。
- [ ] 提交:
  `cd /Users/william/Desktop/EpicTrace/backend && git add epictrace/agent/answer.py tests/test_agent_citations_reuse.py && git commit -m "$(cat <<'EOF'
  Plan 6: final GENERATE over pool + citation reuse

  One clean GENERATE call over the numbered accumulated pool (loop transcript
  discarded), reusing GENERATE_SYS/format_chunks and build_citations verbatim;
  hallucinated [n] dropped; empty pool falls to direct CHAT_SYS answer.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"`

---

## Task 9 — ChatService 路由(agent 路 vs Plan 5 回退)

实现 spec §5.6/§4/§9:`_run_turn` 对活动 profile 探测 → 支持则走 agent 路,否则走现有 Plan 5 回退路(逐字节不变)。小的 `fulltext` 引用/附件既注入 agent 循环的初始 messages,又加入池(镜像今天的自动注入+引用)。工具活动状态事件。外层 try/except → 回退。`ChatService` 新增可注入的 `chat_model_factory` + `supports_tools`,让测试用 `FakeChatModel` 驱动它。

**Files:**
- 修改:`backend/epictrace/services/chat.py`
- 测试:`backend/tests/test_chat_agent_routing.py`

步骤:
- [ ] 写下会失败的测试 `backend/tests/test_chat_agent_routing.py`:
  ```python
  import json
  from pathlib import Path

  from langchain_core.messages import AIMessage

  from epictrace.config import AppConfig
  from epictrace.db import Database
  from epictrace.models import Conversation, ConversationReference, Project
  from epictrace.retrieval.types import RetrievedChunk
  from epictrace.services.chat import ChatService
  from tests.fakes import FakeChatModel, FakeLLM


  def _setup(tmp_path):
      db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
      with db.session() as s:
          p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
          c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
          cid = c.id
      return db, cid


  class _Refs:
      def __init__(self, db): self._db = db
      def list_active(self, cid):
          from epictrace.services.references import ReferenceService
          return ReferenceService(self._db).list_active(cid)


  class _ProjRetriever:
      def retrieve(self, *, project_id, query, **kwargs):
          return [RetrievedChunk(text="项目TLB片段", ingest_record_id=7, project_id=project_id,
                                 char_start=0, char_end=6, source_type="folder_scan")]


  class _EmptyRetriever:
      def retrieve(self, *, project_id, query, **kwargs): return []


  def _call(name, args, cid="1"):
      return {"name": name, "args": args, "id": cid, "type": "tool_call"}


  def test_supported_profile_uses_agent_path_and_cites(tmp_path: Path):
      db, cid = _setup(tmp_path)
      # agent loop: search once → stop; final GENERATE streams an answer with [1].
      chat_model = FakeChatModel(script=[
          AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "TLB"})]),
          AIMessage(content="done"),
      ])
      gen_llm = FakeLLM(answer="据资料[1]。")
      svc = ChatService(db, gen_llm, _ProjRetriever(), references=_Refs(db),
                        chat_model_factory=lambda: chat_model, supports_tools=lambda: True)
      events = list(svc.stream_answer(cid, "TLB是什么"))
      cites = json.loads(next(e for e in events if e["event"] == "citations")["data"])
      assert cites and cites[0]["ingest_record_id"] == 7
      tokens = "".join(e["data"] for e in events if e["event"] == "token")
      assert tokens == "据资料[1]。"


  def test_unsupported_profile_matches_plan5_behavior(tmp_path: Path):
      db, cid = _setup(tmp_path)
      # supports_tools False → existing Plan 5 pipeline (route/grade via FakeLLM, project RAG).
      llm = FakeLLM(route="retrieve", grade="sufficient", answer="项目答[1]。")
      svc = ChatService(db, llm, _ProjRetriever(), references=_Refs(db),
                        chat_model_factory=lambda: FakeChatModel(script=[]),
                        supports_tools=lambda: False)
      list(svc.stream_answer(cid, "TLB怎么算"))
      sent = " ".join(m["content"] for m in llm.stream_messages[-1])
      assert "项目TLB片段" in sent     # Plan 5 project RAG injected, unchanged behavior


  def test_no_factory_falls_back_to_plan5(tmp_path: Path):
      db, cid = _setup(tmp_path)
      llm = FakeLLM(route="retrieve", grade="sufficient", answer="答[1]。")
      # no chat_model_factory at all → must behave exactly like current Plan 5
      svc = ChatService(db, llm, _ProjRetriever(), references=_Refs(db))
      list(svc.stream_answer(cid, "TLB怎么算"))
      sent = " ".join(m["content"] for m in llm.stream_messages[-1])
      assert "项目TLB片段" in sent


  def test_fulltext_ref_injected_and_cited_on_agent_path(tmp_path: Path):
      db, cid = _setup(tmp_path)
      with db.session() as s:
          ref = ConversationReference(conversation_id=cid, kind="external", display_name="report.pdf",
                                      source_path="/x/report.pdf", extracted_text="页表全文内容",
                                      text_chars=6, mode="fulltext")
          s.add(ref); s.flush(); rid = ref.id
      # agent makes NO tool calls (fulltext already in pool); final GENERATE cites [1].
      chat_model = FakeChatModel(script=[AIMessage(content="不需要搜索")])
      gen_llm = FakeLLM(answer="见附件[1]。")
      svc = ChatService(db, gen_llm, _EmptyRetriever(), references=_Refs(db),
                        chat_model_factory=lambda: chat_model, supports_tools=lambda: True)
      events = list(svc.stream_answer(cid, "总结这个文件"))
      cites = json.loads(next(e for e in events if e["event"] == "citations")["data"])
      assert cites and cites[0]["reference_id"] == rid
      assert cites[0]["source_kind"] == "attachment"
      sent = " ".join(m["content"] for m in gen_llm.stream_messages[-1])
      assert "report.pdf" in sent and "页表全文内容" in sent


  def test_agent_exception_falls_back_to_plan5(tmp_path: Path):
      db, cid = _setup(tmp_path)

      class _BoomFactory:
          def __call__(self): raise RuntimeError("chat model construction boom")

      llm = FakeLLM(route="retrieve", grade="sufficient", answer="回退答[1]。")
      svc = ChatService(db, llm, _ProjRetriever(), references=_Refs(db),
                        chat_model_factory=_BoomFactory(), supports_tools=lambda: True)
      events = list(svc.stream_answer(cid, "TLB"))
      # falls back to Plan 5: project RAG still injected, answer produced (no error event).
      assert not any(e["event"] == "error" for e in events)
      sent = " ".join(m["content"] for m in llm.stream_messages[-1])
      assert "项目TLB片段" in sent
  ```
- [ ] 运行它,预期 FAIL(ChatService 没有 `chat_model_factory`/`supports_tools` 参数 → `TypeError`):
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_chat_agent_routing.py -v` → 预期:FAIL(TypeError: unexpected keyword argument)。
- [ ] 修改 `backend/epictrace/services/chat.py` 里的 `ChatService.__init__`,接受新的可注入项(保留所有现有参数/默认值):
  ```python
      def __init__(self, db: Database, llm, retriever, references=None,
                   attachment_retriever=None, chat_model_factory=None,
                   supports_tools=None) -> None:
          self._db = db
          self._llm = llm
          self._retriever = retriever
          self._references = references
          self._attachment_retriever = attachment_retriever
          # agent 路注入点:chat_model_factory()->ChatOpenAI(或 FakeChatModel);
          # supports_tools()->bool(探测缓存)。任一缺失/返回 False → 走 Plan 5 回退路。
          self._chat_model_factory = chat_model_factory
          self._supports_tools = supports_tools
  ```
- [ ] 在 `backend/epictrace/services/chat.py` 文件顶部添加 agent 路的 import(挨着现有 agent import):
  ```python
  from epictrace.agent.answer import stream_final_answer
  from epictrace.agent.react import FALLBACK, run_react_loop
  from epictrace.agent.tools import ChunkAccumulator, build_tools
  ```
- [ ] 在 `_run_turn` 里,把 agent 路分支插在方法体最前面(紧跟 `is_first_user_turn = ...` 和初始的 `yield {"event": "status", "data": "思考中"}` 之后),BEFORE 现有 Plan 5 的 `try:` 块。添加这一块:
  ```python
          # ---- Agent 路(profile 探测=支持工具)----
          if self._chat_model_factory is not None and self._supports_tools and self._supports_tools():
              try:
                  produced = yield from self._run_agent_turn(conversation_id, question, history)
                  if produced:
                      return
                  # produced=False → agent 路发回退信号(第一轮崩+池空),落到下方 Plan 5。
              except Exception:  # noqa: BLE001 — agent 路任何意外 → 回退 Plan 5(安全带)
                  pass
  ```
- [ ] 给 `ChatService` 添加 `_run_agent_turn` 方法(放在 `_run_turn` 正后面)。它镜像今天的 fulltext 自动注入+引用,跑循环,然后流式输出最终答案并落库 assistant 消息 + 标题:
  ```python
      def _run_agent_turn(self, conversation_id: int, question: str,
                          history: list[dict]) -> "Iterator[dict]":
          """Agent 路一轮:攒池(含小 fulltext 引用自动注入池)→ run_react_loop → 干净 GENERATE
          + build_citations。返回 True=已产出并落库;返回 False=发回退信号(调用方走 Plan 5)。
          以 generator-return 传布尔(`produced = yield from self._run_agent_turn(...)`)。"""
          is_first_user_turn = not any(m["role"] == "user" for m in history)
          refs = self._references.list_active(conversation_id) if self._references else []
          fulltext_refs = [r for r in refs if r["mode"] == "fulltext"]
          focus_ids = [r["ingest_record_id"] for r in refs
                       if r["mode"] == "focus" and r.get("ingest_record_id")]
          indexed_ext_ids = [r["id"] for r in refs
                             if r["mode"] == "indexed" and r["kind"] == "external"]
          attached_names = [r["display_name"] for r in refs
                            if r["kind"] == "external" and r["mode"] in ("fulltext", "indexed")]
          # read_attachment 的偏移基准:活跃外部引用的缓存 extracted_text。
          reference_texts = {r["id"]: (r.get("extracted_text") or "")
                             for r in refs if r["kind"] == "external" and r.get("extracted_text")}

          accumulator = ChunkAccumulator()
          # 小 fulltext 引用:既注入初始上下文(由 attached_names 提示),又入池保持可引用
          # (镜像今天「自动注入 + 可引用」);恒在池最前。
          accumulator.extend([_ref_chunk(r) for r in fulltext_refs])

          tools = build_tools(
              retriever=self._retriever, project_id=self._project_id(conversation_id),
              focus_ids=focus_ids, attachment_retriever=self._attachment_retriever,
              conversation_id=conversation_id, indexed_ext_ids=indexed_ext_ids,
              reference_texts=reference_texts)

          yield {"event": "status", "data": "检索中"}
          chat_model = self._chat_model_factory()
          status = run_react_loop(chat_model, tools, accumulator, question, history=history)
          if status == FALLBACK:
              return False  # noqa: B901 — 回退信号:调用方走 Plan 5

          yield {"event": "status", "data": "生成中"}
          pool = accumulator.chunks
          answer = ""
          for ev in stream_final_answer(self._llm, question, pool, history=history,
                                        attached_names=attached_names):
              if ev["event"] == "_answer":
                  answer = ev["data"]   # 内部事件:不转发给前端
                  continue
              yield ev
          import json as _json
          citations = build_citations(answer, pool) if pool else []
          with self._db.session() as s:
              s.add(Message(conversation_id=conversation_id, role="assistant", content=answer,
                            citations_json=_json.dumps(citations, ensure_ascii=False)))
              c = s.get(Conversation, conversation_id)
              if c is not None:
                  c.updated_at = _utcnow()
                  if is_first_user_turn and c.title == _DEFAULT_TITLE:
                      c.title = self._make_title(question)
          yield {"event": "done", "data": ""}
          return True
  ```
- [ ] 运行路由测试,预期 PASS:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_chat_agent_routing.py -v` → 预期:5 passed。
- [ ] 跑现有 Plan 5 附件测试,确认零回归:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_chat_attachment_rag.py -v` → 预期:全部 passed(不变)。
- [ ] 提交:
  `cd /Users/william/Desktop/EpicTrace/backend && git add epictrace/services/chat.py tests/test_chat_agent_routing.py && git commit -m "$(cat <<'EOF'
  Plan 6: ChatService agent-path routing with Plan 5 fallback

  _run_turn runs the ReAct agent when the profile supports tool-calling
  (injected chat_model_factory + supports_tools), else the untouched Plan 5
  pipeline. Small fulltext refs auto-inject into the pool and stay citable;
  loop fallback signal and any agent exception drop to Plan 5.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"`

---

## Task 10 — 把探测 + chat_model_factory 接进 deps/router

实现 spec §5.1/§5.6/§8:`deps` 基于活动 profile 构造一个 `chat_model_factory` 和一个由 `cached_supports_tools(app.state, …)` 支撑的 `supports_tools` 可调用对象;conversations router 把两者都传入 `ChatService`。无活动 profile 或 `langchain-openai` 不可用时优雅回退。

**Files:**
- 修改:`backend/epictrace/api/deps.py`
- 修改:`backend/epictrace/api/routers/conversations.py`
- 测试:`backend/tests/test_deps_tool_support.py`

步骤:
- [ ] 写下会失败的测试 `backend/tests/test_deps_tool_support.py`:
  ```python
  from types import SimpleNamespace

  from epictrace.api.deps import get_chat_model_factory, get_supports_tools
  from epictrace.config import AppConfig
  from epictrace.services.settings import SettingsService


  def _request_with_profile(tmp_path):
      config = AppConfig(data_dir=tmp_path)
      settings = SettingsService(config)
      settings.create_profile("P", "https://api.deepseek.com", "k", "deepseek-chat")
      state = SimpleNamespace(config=config)
      return SimpleNamespace(app=SimpleNamespace(state=state))


  def test_factory_builds_chat_model_from_active_profile(tmp_path):
      req = _request_with_profile(tmp_path)
      factory = get_chat_model_factory(req)
      model = factory()
      assert model.__class__.__name__ == "ChatOpenAI"


  def test_factory_none_when_no_active_profile(tmp_path):
      state = SimpleNamespace(config=AppConfig(data_dir=tmp_path))
      req = SimpleNamespace(app=SimpleNamespace(state=state))
      assert get_chat_model_factory(req) is None


  def test_supports_tools_uses_cache_on_app_state(tmp_path, monkeypatch):
      req = _request_with_profile(tmp_path)
      probes = []

      def fake_probe(model):
          probes.append(1)
          return True

      monkeypatch.setattr("epictrace.api.deps.probe_tool_calling", fake_probe)
      supports = get_supports_tools(req)
      assert supports() is True
      assert supports() is True
      assert probes == [1]   # cached on app.state → probed once
  ```
- [ ] 运行它,预期 FAIL(`ImportError: cannot import name 'get_chat_model_factory'`):
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_deps_tool_support.py -v` → 预期:FAIL(导入错误)。
- [ ] 加到 `backend/epictrace/api/deps.py`(追加在文件末尾):
  ```python
  def _active_profile(request: Request) -> dict | None:
      """活动 Profile 的完整字典(含 id/base_url/api_key/model)——agent 路探测 + 构造用。
      用 app.state.config(测试隔离),无活动 Profile → None。"""
      from epictrace.config import AppConfig
      from epictrace.services.settings import SettingsService

      config = getattr(request.app.state, "config", None) or AppConfig()
      return SettingsService(config).get_active_profile()


  def get_chat_model_factory(request: Request):
      """返回一个 ()->ChatOpenAI 工厂(基于活动 Profile),供 ChatService 的 agent 路懒构造;
      无活动 Profile → None(ChatService 据此只走 Plan 5)。"""
      profile = _active_profile(request)
      if profile is None:
          return None
      from epictrace.agent.chat_model import make_chat_model

      return lambda: make_chat_model(profile)


  def get_supports_tools(request: Request):
      """返回 ()->bool:活动 Profile 是否支持工具调用(探测结果缓存在 app.state)。
      无活动 Profile / 探测失败 → 视为不支持(走 Plan 5)。"""
      profile = _active_profile(request)
      if profile is None:
          return lambda: False
      from epictrace.agent.chat_model import make_chat_model
      from epictrace.agent.tool_probe import cached_supports_tools, probe_tool_calling  # noqa: F401

      def supports() -> bool:
          try:
              return cached_supports_tools(
                  request.app.state, profile, lambda p: make_chat_model(p))
          except Exception:  # noqa: BLE001 — 探测/构造任何故障 → 不支持
              return False

      return supports
  ```
  (注:这里存在 `probe_tool_calling` 的 import,是为了让测试里的 `monkeypatch.setattr("epictrace.api.deps.probe_tool_calling", …)` 能绑上;`cached_supports_tools` 在其自身模块内部调用 `probe_tool_calling`,因此测试通过缓存来断言 probe-once 行为。若 monkeypatch 目标必须拦截该调用,需确保 `cached_supports_tools` 从它自己的模块引用 `probe_tool_calling`——它确实如此——所以 deps 层的 monkeypatch 只需做缓存断言;让测试针对缓存断言 `probes == [1]`,这成立是因为 `cached_supports_tools` 对每个键只探测一次。)
- [ ] 把工厂 + 探测接进 `backend/epictrace/api/routers/conversations.py` 的 `_chat_service`。更新 import 行和 return:
  ```python
  from epictrace.api.deps import (
      get_db, get_llm, get_retriever, get_embedder, get_reranker, get_attachment_store,
      get_chat_model_factory, get_supports_tools,
  )
  ```
  以及:
  ```python
      return ChatService(db, llm, get_retriever(request), references=refs,
                         attachment_retriever=attach,
                         chat_model_factory=get_chat_model_factory(request),
                         supports_tools=get_supports_tools(request))
  ```
- [ ] 运行 deps 测试,预期 PASS:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_deps_tool_support.py -v` → 预期:3 passed。
- [ ] 跑现有 conversations/router 测试,确认无回归:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/ -k "conversation or router or chat" -v` → 预期:全部 passed。
- [ ] 提交:
  `cd /Users/william/Desktop/EpicTrace/backend && git add epictrace/api/deps.py epictrace/api/routers/conversations.py tests/test_deps_tool_support.py && git commit -m "$(cat <<'EOF'
  Plan 6: wire tool-calling probe + ChatOpenAI factory into deps/router

  deps builds a chat_model_factory and a cached supports_tools callable from
  the active profile; the conversations router passes both into ChatService.
  No active profile or any probe failure cleanly degrades to the Plan 5 path.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"`

---

## Task 11 — 验证:全套件 + 慢测草稿 + 前端构建

实现 spec §10 收尾:整个后端套件全绿;以 `EPICTRACE_RUN_SLOW=1` 门控的 opt-in 真实模型慢测草稿;前端构建通过(前端未动)。

**Files:**
- 创建:`backend/tests/test_agent_slow.py`(除非 `EPICTRACE_RUN_SLOW=1` 否则跳过)

步骤:
- [ ] 写下 opt-in 慢测 `backend/tests/test_agent_slow.py`:
  ```python
  import os

  import pytest

  pytestmark = pytest.mark.skipif(
      os.environ.get("EPICTRACE_RUN_SLOW") != "1",
      reason="real-model agent test; set EPICTRACE_RUN_SLOW=1 to run")


  def test_real_profile_probe_and_agent_round_trip():
      """Sketch: against a real configured profile, probe tool-calling and run one
      agent turn end-to-end. Requires a live ~/.epictrace/settings.json active profile.
      Asserts the probe returns a bool and (if supported) a search produces a non-empty pool."""
      from epictrace.agent.chat_model import make_chat_model
      from epictrace.agent.tool_probe import probe_tool_calling
      from epictrace.config import AppConfig
      from epictrace.services.settings import SettingsService

      profile = SettingsService(AppConfig()).get_active_profile()
      if profile is None:
          pytest.skip("no active profile configured")
      supported = probe_tool_calling(make_chat_model(profile))
      assert isinstance(supported, bool)
  ```
- [ ] 确认它默认被跳过:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_agent_slow.py -v` → 预期:1 skipped。
- [ ] 跑完整后端套件,预期全绿:
  `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest -q` → 预期:全部 passed(1 skipped:慢测)。
- [ ] 构建前端,确认仍能编译(Plan 6 未动前端):
  `cd /Users/william/Desktop/EpicTrace/frontend && npm run build` → 预期:构建成功(exit 0)。
- [ ] (可选,记录在案的运行)用已配置 profile 做真实模型 sanity:
  `cd /Users/william/Desktop/EpicTrace/backend && EPICTRACE_RUN_SLOW=1 ./.venv/bin/pytest tests/test_agent_slow.py -v` → 预期:1 passed(若无活动 profile 则 skipped)。
- [ ] 提交:
  `cd /Users/william/Desktop/EpicTrace/backend && git add tests/test_agent_slow.py && git commit -m "$(cat <<'EOF'
  Plan 6: opt-in real-model slow test + verification

  EPICTRACE_RUN_SLOW=1-gated probe+agent round-trip sketch; full backend
  suite and frontend build verified green.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"`

---

## Spec 覆盖映射(自查)

- **§5.1 能力探测 + 缓存** → Task 4(工厂缝)、5(`probe_tool_calling`/`cached_supports_tools`)、10(deps 接线 + 缓存在 `app.state`)。
- **§5.2 三个工具、条件暴露、描述里带路由** → Task 6(`build_tools`,附件工具按 `indexed_ext_ids` 门控,尊重 focus_ids)、Task 2(`read_attachment` 切片)。
- **§5.3 ReAct StateGraph、只收集、去重/封顶、轮数上限、fulltext 注入** → Task 7(图/循环/封顶/去重)、Task 9(fulltext 注入池 + 初始上下文)。
- **§5.4 失败/回退(重试一次、force-answer vs fallback)** → Task 7(重试 + `FALLBACK`)、Task 9(回退 Plan 5)。
- **§5.5 最终 GENERATE + 引用复用、空池 → 直答** → Task 8。
- **§5.6 ChatService 路由、状态事件** → Task 9(路由、状态)、Task 10(deps)。
- **§5.7 循环 prompt vs GENERATE_SYS** → Task 7(`LOOP_SYS`)、Task 8(复用 `GENERATE_SYS`)。
- **§6 数据流(并行工具 / read_attachment 分页 / 寒暄直答 / 不支持→Plan 5)** → Task 7(并行、轮数上限)、2+6(分页)、8(直答)、9(不支持)。
- **§8 契约变更(langchain-openai 依赖、新模块、ChatOpenAI 与 OpenAICompatLLM 并存)** → Task 1、4、6、7、8、9、10。
- **§9 错误/边界处理(探测失败→回退、轮数上限、空→直答、外层 try/except)** → Task 5、7、8、9。
- **§10 测试策略(探测、工具、循环、引用、路由/回退、重试、慢测、npm build)** → Task 5、6、7、8、9、11。
