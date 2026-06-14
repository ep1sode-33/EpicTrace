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
