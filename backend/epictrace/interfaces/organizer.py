from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class OrganizationProposal:
    """归类提议(物化 + 入库的输入)。本期为直通形态:整段归一个 Project。
    后续真·归类 Agent 返回更丰富的提议(多 Project / 子文件夹 / 派生文件),execute 侧扩展即可。"""
    project_id: int
    markdown_docs: list[tuple[str, str]] = field(default_factory=list)  # (filename, content)
    screenshot_rel_paths: list[str] = field(default_factory=list)        # 相对 staging_dir


@runtime_checkable
class Organizer(Protocol):
    def propose(self, session, events, hint_project_id: int) -> OrganizationProposal: ...


class PassthroughOrganizer:
    """直通:笔记/剪贴板文本各合成一个 .md,截图列出文件名,全归到 hint_project_id。"""

    def propose(self, session, events, hint_project_id: int) -> OrganizationProposal:
        notes = [e.payload for e in events if e.kind == "note"]
        clips = [e.payload for e in events if e.kind == "clipboard"]
        shots = [e.payload for e in events if e.kind == "screenshot"]
        docs: list[tuple[str, str]] = []
        if notes:
            docs.append(("notes.md", "# 笔记\n\n" + "\n\n".join(notes) + "\n"))
        if clips:
            docs.append(("clipboard.md", "# 剪贴板\n\n" + "\n\n".join(clips) + "\n"))
        return OrganizationProposal(
            project_id=hint_project_id, markdown_docs=docs, screenshot_rel_paths=shots,
        )
