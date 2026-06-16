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
