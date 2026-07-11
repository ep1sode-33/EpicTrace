from datetime import datetime, timezone

from epictrace.interfaces.organizer import OrganizationProposal, PassthroughOrganizer
from epictrace.models import CaptureEvent, CaptureSession


def _evt(kind, payload, sec):
    return CaptureEvent(kind=kind, payload=payload,
                        ts=datetime(2026, 6, 15, 12, 0, sec, tzinfo=timezone.utc), meta={})


def test_passthrough_groups_text_into_markdown_and_lists_screenshots():
    sess = CaptureSession(id=7, title="S", status="staged",
                          staging_dir="/tmp/s/7", sources=["note", "screenshot"])
    events = [
        _evt("note", "想法一", 1),
        _evt("clipboard", "复制的链接", 2),
        _evt("note", "想法二", 3),
        _evt("screenshot", "shot-1.png", 4),
        _evt("pause", "", 5),  # 控制事件不进物化
    ]
    proposal = PassthroughOrganizer().propose(sess, events, hint_project_id=3)
    assert isinstance(proposal, OrganizationProposal)
    assert proposal.project_id == 3
    names = {name for name, _ in proposal.markdown_docs}
    assert names == {"notes.md", "clipboard.md"}
    notes = dict(proposal.markdown_docs)["notes.md"]
    assert "想法一" in notes and "想法二" in notes
    assert proposal.screenshot_rel_paths == ["shot-1.png"]


def test_passthrough_empty_session_yields_no_docs():
    sess = CaptureSession(id=8, title="S", status="staged", staging_dir="/tmp/s/8", sources=[])
    proposal = PassthroughOrganizer().propose(sess, [], hint_project_id=1)
    assert proposal.markdown_docs == []
    assert proposal.screenshot_rel_paths == []


# --- transcript.md 物化(镜像前端 frontend/src/lib/transcript.ts 的段落分组) ---

def _trans(payload, sec, source="mic"):
    """构造一条 transcription 事件(sec 为分钟内秒偏移;tz-aware,需归一 naive-UTC)。"""
    return CaptureEvent(kind="transcription", payload=payload,
                        ts=datetime(2026, 6, 15, 12, 0, sec, tzinfo=timezone.utc),
                        meta={"source": source})


def _staged_session(started=datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)):
    return CaptureSession(id=9, title="会议", status="staged",
                          staging_dir="/tmp/s/9", sources=["mic"], started_at=started)


def _transcript_of(proposal):
    return dict(proposal.markdown_docs).get("transcript.md")


def test_transcription_materializes_transcript_with_marker_and_body():
    events = [_trans("大家好", 1), _trans("欢迎参加", 2)]
    md = _transcript_of(PassthroughOrganizer().propose(_staged_session(), events, 2))
    assert md is not None
    assert "# 会话转录:会议" in md
    # 段首事件 ts 的内嵌 marker(用 timealign.format_marker 生成)
    assert "## [2026-06-15 12:00:01] 麦克风" in md
    # 引言行带会话开始时刻(UTC)
    assert "> 会话开始:2026-06-15 12:00:00(UTC)" in md
    # 同段 CJK 相邻片段直连
    assert "大家好欢迎参加" in md


def test_transcription_source_switch_breaks_paragraph():
    events = [_trans("你好", 1, source="mic"), _trans("世界", 2, source="device")]
    md = _transcript_of(PassthroughOrganizer().propose(_staged_session(), events, 2))
    assert "## [2026-06-15 12:00:01] 麦克风" in md
    assert "## [2026-06-15 12:00:02] 系统声音" in md
    # 两段独立,不被合并直连
    assert "你好世界" not in md


def test_transcription_large_gap_breaks_paragraph():
    # sec 1 与 sec 40 相隔 39s > 30s → 断段(即使同来源)
    events = [_trans("前半段", 1), _trans("后半段", 40)]
    md = _transcript_of(PassthroughOrganizer().propose(_staged_session(), events, 2))
    assert "## [2026-06-15 12:00:01] 麦克风" in md
    assert "## [2026-06-15 12:00:40] 麦克风" in md
    assert "前半段后半段" not in md


def test_transcription_gap_measured_against_previous_event_not_segment_start():
    # 段首 1s、中段 20s、末段 45s:每两条相邻间隔均 ≤30s(20-1=19,45-20=25),
    # 但与段首相隔 44s。前端按「与上一条」比较 → 应合成一段(不断段)。
    events = [_trans("甲", 1), _trans("乙", 20), _trans("丙", 45)]
    md = _transcript_of(PassthroughOrganizer().propose(_staged_session(), events, 2))
    # 只有一个 marker(段首),整段合并
    assert md.count("## [") == 1
    assert "甲乙丙" in md


def test_transcription_non_transcription_event_breaks_paragraph():
    # 同来源、间隔小,但中间插入 note → 断段
    events = [_trans("你好", 1), _evt("note", "打断", 2), _trans("世界", 3)]
    md = _transcript_of(PassthroughOrganizer().propose(_staged_session(), events, 2))
    assert "## [2026-06-15 12:00:01] 麦克风" in md
    assert "## [2026-06-15 12:00:03] 麦克风" in md
    assert "你好世界" not in md


def test_transcription_cjk_segments_join_directly():
    events = [_trans("你好", 1), _trans("世界", 2)]
    md = _transcript_of(PassthroughOrganizer().propose(_staged_session(), events, 2))
    assert "你好世界" in md


def test_transcription_ascii_segments_join_with_space():
    events = [_trans("hello", 1), _trans("world", 2)]
    md = _transcript_of(PassthroughOrganizer().propose(_staged_session(), events, 2))
    assert "hello world" in md
    assert "helloworld" not in md


def test_transcription_empty_payload_skipped_within_paragraph():
    # 空片段跳过,不影响同段其余片段直连;不额外产生 marker
    events = [_trans("前", 1), _trans("   ", 2), _trans("后", 3)]
    md = _transcript_of(PassthroughOrganizer().propose(_staged_session(), events, 2))
    assert "前后" in md
    assert md.count("## [") == 1


def test_transcription_all_empty_yields_no_transcript_doc():
    events = [_trans("", 1), _trans("   ", 2)]
    proposal = PassthroughOrganizer().propose(_staged_session(), events, 2)
    assert "transcript.md" not in dict(proposal.markdown_docs)


def test_transcription_omits_intro_line_when_started_at_none():
    events = [_trans("内容", 1)]
    md = _transcript_of(PassthroughOrganizer().propose(_staged_session(started=None), events, 2))
    assert md is not None
    assert "# 会话转录:会议" in md
    assert "> 会话开始" not in md


def test_transcript_coexists_with_notes_and_clipboard():
    # transcript.md 与既有 notes/clipboard 并存,互不干扰
    sess = _staged_session()
    events = [_evt("note", "想法", 1), _evt("clipboard", "链接", 2), _trans("说了句话", 3)]
    proposal = PassthroughOrganizer().propose(sess, events, 2)
    names = {name for name, _ in proposal.markdown_docs}
    assert names == {"notes.md", "clipboard.md", "transcript.md"}
