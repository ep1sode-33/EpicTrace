import json
from pathlib import Path

from sqlalchemy import select

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Message, Project
from epictrace.retrieval.types import RetrievedChunk
from epictrace.services.chat import ChatService
from tests.fakes import FakeLLM, RaisingLLM


class _Retriever:
    def retrieve(self, *, project_id, query, k=6):
        return [RetrievedChunk(text="页表映射地址", ingest_record_id=1, project_id=project_id,
                               char_start=0, char_end=6, source_type="folder_scan")]


class _RaisingRetriever:
    def retrieve(self, *, project_id, query, k=6):
        raise RuntimeError("retriever boom")


class _RecordingRetriever:
    """记录是否被调用,用于断言 direct 路由完全不检索。"""

    def __init__(self):
        self.calls = []

    def retrieve(self, *, project_id, query, k=6):
        self.calls.append(query)
        return [RetrievedChunk(text="页表映射地址", ingest_record_id=1, project_id=project_id,
                               char_start=0, char_end=6, source_type="folder_scan")]


def _setup(tmp_path, title="t"):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path / "P")); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title=title); s.add(c); s.flush()
        cid = c.id
    return db, cid


def _roles(db, cid):
    with db.session() as s:
        return [m.role for m in s.execute(
            select(Message).where(Message.conversation_id == cid).order_by(Message.id)
        ).scalars()]


def test_stream_emits_events_and_persists(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(grade="sufficient", answer="地址映射靠页表[1]。"), _Retriever())
    events = list(svc.stream_answer(cid, "页表是什么"))
    kinds = [e["event"] for e in events]
    assert "status" in kinds and "token" in kinds and "citations" in kinds and kinds[-1] == "done"
    answer = "".join(e["data"] for e in events if e["event"] == "token")
    assert "页表" in answer
    cite_evt = next(e for e in events if e["event"] == "citations")
    assert json.loads(cite_evt["data"])[0]["ingest_record_id"] == 1
    # 落库:user + assistant 两条
    with db.session() as s:
        msgs = list(s.execute(select(Message).where(Message.conversation_id == cid)).scalars())
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[1].citations_json and "ingest_record_id" in msgs[1].citations_json


def test_second_turn_includes_prior_turn_in_llm_messages(tmp_path: Path):
    db, cid = _setup(tmp_path)
    llm = FakeLLM(grade="sufficient", answer="第二轮答案[1]。")
    svc = ChatService(db, llm, _Retriever())
    list(svc.stream_answer(cid, "第一轮问题"))     # 第 1 轮:落 user+assistant
    llm.stream_messages.clear()                     # 只看第 2 轮的 stream 输入
    list(svc.stream_answer(cid, "第二轮问题"))     # 第 2 轮
    sent = llm.stream_messages[-1]
    contents = [m["content"] for m in sent]
    # 第 2 轮的 stream 消息里应含第 1 轮的内容(user 问 + assistant 答),且本轮问题在最后。
    assert any("第一轮问题" in c for c in contents)
    assert any("第二轮答案" in c for c in contents)
    assert "第二轮问题" in sent[-1]["content"]
    assert sent[0]["role"] == "system"               # 系统提示仍在最前


def test_first_turn_default_title_set_by_llm_and_updated_at(tmp_path: Path):
    db, cid = _setup(tmp_path, title="新对话")
    svc = ChatService(db, FakeLLM(grade="sufficient", title="页表与分页", answer="答[1]。"), _Retriever())
    with db.session() as s:
        before = s.get(Conversation, cid).updated_at
    list(svc.stream_answer(cid, "操作系统的页表是如何工作的请详细说明一下谢谢" * 2))
    with db.session() as s:
        c = s.get(Conversation, cid)
        assert c.title == "页表与分页"           # 由 LLM 起的标题(非问题首段)
        assert c.updated_at >= before


def test_title_quotes_stripped(tmp_path: Path):
    db, cid = _setup(tmp_path, title="新对话")
    svc = ChatService(db, FakeLLM(grade="sufficient", title="“带引号的标题”", answer="答[1]。"), _Retriever())
    list(svc.stream_answer(cid, "随便问"))
    with db.session() as s:
        assert s.get(Conversation, cid).title == "带引号的标题"


def test_title_falls_back_to_question_when_llm_title_empty(tmp_path: Path):
    db, cid = _setup(tmp_path, title="新对话")
    svc = ChatService(db, FakeLLM(grade="sufficient", title="   ", answer="答[1]。"), _Retriever())
    list(svc.stream_answer(cid, "操作系统的页表是如何工作的呢" * 3))
    with db.session() as s:
        c = s.get(Conversation, cid)
        assert c.title.startswith("操作系统的页表") and len(c.title) <= 30


def test_nondefault_title_preserved_and_no_title_call(tmp_path: Path):
    db, cid = _setup(tmp_path, title="我的自定义标题")
    svc = ChatService(db, FakeLLM(grade="sufficient", title="不该被用", answer="答[1]。"), _Retriever())
    list(svc.stream_answer(cid, "随便问点什么"))
    with db.session() as s:
        assert s.get(Conversation, cid).title == "我的自定义标题"


def test_direct_route_plain_chat_no_citations_and_no_retrieval(tmp_path: Path):
    # route="direct"(打招呼/常识)→ 不检索、不引用,流式产出普通聊天答案。
    db, cid = _setup(tmp_path)
    retr = _RecordingRetriever()
    svc = ChatService(db, FakeLLM(route="direct", answer="你好,有什么可以帮你?"), retr)
    events = list(svc.stream_answer(cid, "你好"))
    assert retr.calls == []                                   # 完全没检索
    answer = "".join(e["data"] for e in events if e["event"] == "token")
    assert answer == "你好,有什么可以帮你?"
    cite_evt = next(e for e in events if e["event"] == "citations")
    assert json.loads(cite_evt["data"]) == []                 # 直答无引用
    assert [e["event"] for e in events][-1] == "done"
    with db.session() as s:
        m = list(s.execute(select(Message).where(Message.conversation_id == cid)).scalars())
        assert [x.role for x in m] == ["user", "assistant"]
        assert m[1].citations_json == "[]"


def test_llm_error_yields_error_event_and_no_assistant_message(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, RaisingLLM(), _Retriever())
    events = list(svc.stream_answer(cid, "问题"))
    kinds = [e["event"] for e in events]
    assert "error" in kinds and "done" not in kinds
    # user 消息可保留,但绝不落半截 assistant 消息。
    assert _roles(db, cid) == ["user"]


def test_retriever_error_yields_error_event_and_no_assistant_message(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(grade="sufficient"), _RaisingRetriever())
    events = list(svc.stream_answer(cid, "问题"))
    assert [e["event"] for e in events][-1] == "error"
    assert _roles(db, cid) == ["user"]


def _contents(db, cid):
    with db.session() as s:
        return [(m.role, m.content) for m in s.execute(
            select(Message).where(Message.conversation_id == cid).order_by(Message.id)
        ).scalars()]


def test_regenerate_replaces_last_assistant_without_duplicating_user(tmp_path: Path):
    # 跑一轮 → user+assistant;regenerate → 删旧 assistant、产新 assistant,数量仍是 2,user 不重复。
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(grade="sufficient", answer="第一次答案[1]。"), _Retriever())
    list(svc.stream_answer(cid, "页表是什么"))
    assert _roles(db, cid) == ["user", "assistant"]

    svc2 = ChatService(db, FakeLLM(grade="sufficient", answer="重生成答案[1]。"), _Retriever())
    events = list(svc2.stream_regenerate(cid))
    assert [e["event"] for e in events][-1] == "done"
    contents = _contents(db, cid)
    # 仍是 user + assistant 两条,user 只有一条(未被复制),assistant 是新答案。
    assert [r for r, _ in contents] == ["user", "assistant"]
    assert contents[0] == ("user", "页表是什么")
    assert "重生成答案" in contents[1][1]


def test_regenerate_after_error_only_turn_produces_assistant(tmp_path: Path):
    # 先制造「只有 user、无 assistant」(LLM 报错)的失败轮次。
    db, cid = _setup(tmp_path)
    failed = ChatService(db, RaisingLLM(), _Retriever())
    list(failed.stream_answer(cid, "页表是什么"))
    assert _roles(db, cid) == ["user"]

    # regenerate 用正常 LLM:对同一个 user 消息重新生成,产出 assistant;user 不重复。
    svc = ChatService(db, FakeLLM(grade="sufficient", answer="补上的答案[1]。"), _Retriever())
    events = list(svc.stream_regenerate(cid))
    assert [e["event"] for e in events][-1] == "done"
    contents = _contents(db, cid)
    assert [r for r, _ in contents] == ["user", "assistant"]
    assert contents[0] == ("user", "页表是什么")
    assert "补上的答案" in contents[1][1]


def test_regenerate_uses_prior_turns_as_history_not_new_user(tmp_path: Path):
    # 两轮后 regenerate:重生成第二轮的答案;LLM stream 输入应含第一轮历史,且不新增 user 消息。
    db, cid = _setup(tmp_path)
    llm = FakeLLM(grade="sufficient", answer="第二轮答案[1]。")
    svc = ChatService(db, llm, _Retriever())
    list(svc.stream_answer(cid, "第一轮问题"))
    list(svc.stream_answer(cid, "第二轮问题"))
    assert _roles(db, cid) == ["user", "assistant", "user", "assistant"]

    llm.stream_messages.clear()
    list(svc.stream_regenerate(cid))
    # 重生成后仍是 user/assistant/user/assistant 四条(删旧 assistant、补新 assistant)。
    assert _roles(db, cid) == ["user", "assistant", "user", "assistant"]
    sent = llm.stream_messages[-1]
    contents = [m["content"] for m in sent]
    assert any("第一轮问题" in c for c in contents)   # 历史在场
    assert "第二轮问题" in sent[-1]["content"]          # 重生成的是第二轮问题


def test_regenerate_with_no_user_message_yields_error(tmp_path: Path):
    # 空会话(无任何 user 消息)→ regenerate 发 error,不落消息。
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(grade="sufficient"), _Retriever())
    events = list(svc.stream_regenerate(cid))
    assert [e["event"] for e in events][-1] == "error"
    assert _roles(db, cid) == []


def _ids_by_role(db, cid, role):
    with db.session() as s:
        return [m.id for m in s.execute(
            select(Message).where(Message.conversation_id == cid, Message.role == role).order_by(Message.id)
        ).scalars()]


def test_edit_updates_user_content_deletes_after_and_regenerates(tmp_path: Path):
    # 一轮后编辑该 user 消息:内容被改、其后的 assistant 被删、产出新 assistant;无重复 user。
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(grade="sufficient", answer="第一次答案[1]。"), _Retriever())
    list(svc.stream_answer(cid, "页表是什么"))
    [user_id] = _ids_by_role(db, cid, "user")

    svc2 = ChatService(db, FakeLLM(grade="sufficient", answer="编辑后的答案[1]。"), _Retriever())
    events = list(svc2.stream_edit(cid, user_id, "改成新问题"))
    assert [e["event"] for e in events][-1] == "done"
    contents = _contents(db, cid)
    assert [r for r, _ in contents] == ["user", "assistant"]
    assert contents[0] == ("user", "改成新问题")        # 内容已替换
    assert "编辑后的答案" in contents[1][1]
    # user 消息仍是同一条(id 未变),没有新增重复 user。
    assert _ids_by_role(db, cid, "user") == [user_id]


def test_edit_middle_user_deletes_everything_after_and_keeps_prior_history(tmp_path: Path):
    # 两轮后编辑第一轮的 user 消息:第二轮整轮被删,只剩 第一轮user + 新assistant;
    # 第一轮之前无历史,故 stream 输入里不应含第二轮内容。
    db, cid = _setup(tmp_path)
    llm = FakeLLM(grade="sufficient", answer="第一轮答案[1]。")
    svc = ChatService(db, llm, _Retriever())
    list(svc.stream_answer(cid, "第一轮问题"))
    list(svc.stream_answer(cid, "第二轮问题"))
    assert _roles(db, cid) == ["user", "assistant", "user", "assistant"]
    first_user_id = _ids_by_role(db, cid, "user")[0]

    llm2 = FakeLLM(grade="sufficient", answer="编辑后的第一轮[1]。")
    svc2 = ChatService(db, llm2, _Retriever())
    list(svc2.stream_edit(cid, first_user_id, "编辑后的第一轮问题"))
    contents = _contents(db, cid)
    assert [r for r, _ in contents] == ["user", "assistant"]
    assert contents[0] == ("user", "编辑后的第一轮问题")
    assert "编辑后的第一轮" in contents[1][1]
    sent = llm2.stream_messages[-1]
    joined = " ".join(m["content"] for m in sent)
    # 编辑第一条 → 它之前无历史:不应带入第二轮问题/答案。
    assert "第二轮问题" not in joined and "第二轮答案" not in joined
    assert "编辑后的第一轮问题" in sent[-1]["content"]


def test_edit_preserves_history_before_edited_message(tmp_path: Path):
    # 三条消息(user1/assistant1/user2)后编辑 user2:user1+assistant1 作历史传入。
    db, cid = _setup(tmp_path)
    llm = FakeLLM(grade="sufficient", answer="第二轮答案[1]。")
    svc = ChatService(db, llm, _Retriever())
    list(svc.stream_answer(cid, "第一轮问题"))
    list(svc.stream_answer(cid, "第二轮问题"))
    second_user_id = _ids_by_role(db, cid, "user")[1]

    llm2 = FakeLLM(grade="sufficient", answer="编辑后的第二轮[1]。")
    svc2 = ChatService(db, llm2, _Retriever())
    list(svc2.stream_edit(cid, second_user_id, "编辑后的第二轮问题"))
    assert _roles(db, cid) == ["user", "assistant", "user", "assistant"]
    sent = llm2.stream_messages[-1]
    joined = " ".join(m["content"] for m in sent)
    assert "第一轮问题" in joined                       # 编辑点之前的历史在场
    assert "编辑后的第二轮问题" in sent[-1]["content"]
    assert _contents(db, cid)[2] == ("user", "编辑后的第二轮问题")


def test_edit_unknown_message_yields_error_and_no_change(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(grade="sufficient", answer="答[1]。"), _Retriever())
    list(svc.stream_answer(cid, "页表是什么"))
    before = _contents(db, cid)
    events = list(svc.stream_edit(cid, 999999, "新内容"))
    assert [e["event"] for e in events][-1] == "error"
    assert _contents(db, cid) == before               # 未改动任何消息


def test_edit_assistant_message_yields_error(tmp_path: Path):
    # 目标必须是 user 消息;指向 assistant → error,不改动。
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(grade="sufficient", answer="答[1]。"), _Retriever())
    list(svc.stream_answer(cid, "页表是什么"))
    [assistant_id] = _ids_by_role(db, cid, "assistant")
    before = _contents(db, cid)
    events = list(svc.stream_edit(cid, assistant_id, "试图编辑助手消息"))
    assert [e["event"] for e in events][-1] == "error"
    assert _contents(db, cid) == before
