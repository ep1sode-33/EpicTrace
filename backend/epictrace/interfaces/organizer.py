from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from epictrace.indexing.timealign import format_marker


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
    """直通:笔记/剪贴板文本各合成一个 .md,转写合成 transcript.md(带内嵌时间 marker),
    截图列出文件名,全归到 hint_project_id。"""

    def propose(self, session, events, hint_project_id: int) -> OrganizationProposal:
        notes = [e.payload for e in events if e.kind == "note"]
        clips = [e.payload for e in events if e.kind == "clipboard"]
        shots = [e.payload for e in events if e.kind == "screenshot"]
        docs: list[tuple[str, str]] = []
        if notes:
            docs.append(("notes.md", "# 笔记\n\n" + "\n\n".join(notes) + "\n"))
        if clips:
            docs.append(("clipboard.md", "# 剪贴板\n\n" + "\n\n".join(clips) + "\n"))
        transcript = _render_transcript(session, events)
        if transcript is not None:
            docs.append(("transcript.md", transcript))
        return OrganizationProposal(
            project_id=hint_project_id, markdown_docs=docs, screenshot_rel_paths=shots,
        )


# ---------------------------------------------------------------------------
# transcript.md 物化:段落分组 + 内嵌时间 marker
#
# 段落分组 / 拼接规则**逐条镜像**前端 frontend/src/lib/transcript.ts 的
# groupTimelineItems / joinSegments(两侧改动须同步)。marker 一律经
# indexing/timealign.format_marker 生成,写读两端共用同一格式。
# ---------------------------------------------------------------------------

# 同一段落内相邻转写的最大时间间隔(秒);超过则断段。镜像前端 PARAGRAPH_GAP_SECS。
PARAGRAPH_GAP_SECS = 30

# CJK 字符(含中日韩标点/假名/全角):判断衔接处是否中文,决定拼接时是否插空格。
# 范围逐一对应前端 transcript.ts 的 CJK 正则(U+3000-303F、U+3040-30FF、U+3400-4DBF、
# U+4E00-9FFF、U+F900-FAFF、U+FF00-FFEF)。
_CJK = re.compile(
    r"[　-〿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]"
)


def _to_naive_utc(ts: datetime) -> datetime:
    """归一到裸 UTC(去时区)。tz-aware 先转 UTC 再抹 tzinfo;naive 视作已是 UTC 原样返回。
    权威转录 ts 本就是 started_at+seg.start 重建的 naive-UTC;测试可能构造 tz-aware。"""
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def _event_source(ev) -> str:
    """取事件来源标识;transcription 用 meta.source,非字符串或缺省视作 mic
    (镜像前端 eventSource 的 `typeof s === "string"` 判定)。"""
    s = (ev.meta or {}).get("source")
    return s if isinstance(s, str) else "mic"


def _join_segments(texts: list[str]) -> str:
    """拼接同段落的相邻转写片段(镜像前端 joinSegments):中文片段直连;仅当衔接处两侧
    都不是 CJK(至少一侧英文/数字)时插一个空格,避免英文单词黏连。空片段跳过。"""
    out = ""
    for t in texts:
        if not t:
            continue
        if out and not _CJK.search(out[-1]) and not _CJK.search(t[0]):
            out += " "
        out += t
    return out


def _group_paragraphs(events) -> list[tuple[datetime, str, str]]:
    """把按 ts 升序的事件合并成转写段落(镜像前端 groupTimelineItems):连续、同
    meta.source、且与**上一条事件**间隔 ≤PARAGRAPH_GAP_SECS 的 transcription 事件合成一段;
    来源切换 / 出现非转写事件 / 间隔过大都断段。返回 [(段首 ts, source, 拼接正文), ...],
    只含拼接后非空的段(整段全空则丢弃)。"""
    paragraphs: list[tuple[datetime, str, str]] = []
    cur: dict | None = None  # {source, texts, start_ts, prev_ts}

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            text = _join_segments(cur["texts"])
            if text:
                paragraphs.append((cur["start_ts"], cur["source"], text))
            cur = None

    for ev in events:
        # 非转写事件断段(镜像前端:passthrough 事件先 flush 再透传)。
        if ev.kind != "transcription":
            flush()
            continue
        src = _event_source(ev)
        ts = _to_naive_utc(ev.ts)
        text = (ev.payload or "").strip()
        # 断段条件:来源变了 / 与上一条事件间隔 >30s(gapSeconds(cur.end_ts, ev.ts))。
        if cur is not None and (
            cur["source"] != src
            or abs((ts - cur["prev_ts"]).total_seconds()) > PARAGRAPH_GAP_SECS
        ):
            flush()
        if cur is None:
            cur = {"source": src, "texts": [], "start_ts": ts, "prev_ts": ts}
        if text:
            cur["texts"].append(text)
        cur["prev_ts"] = ts  # 空片段亦更新,与前端 cur.end_ts = ev.ts 一致
    flush()
    return paragraphs


def _render_transcript(session, events) -> str | None:
    """渲染 transcript.md:头部标题 + 可选会话开始引言 + 每段(内嵌 marker + 正文)。
    无任何非空转写段 → 返回 None(不产 doc,与「无 notes 不产 notes.md」同构)。"""
    paragraphs = _group_paragraphs(events)
    if not paragraphs:
        return None
    lines: list[str] = [f"# 会话转录:{session.title}", ""]
    started = getattr(session, "started_at", None)
    if started is not None:
        started = _to_naive_utc(started)
        lines.append(
            f"> 会话开始:{started.strftime('%Y-%m-%d %H:%M:%S')}(UTC);"
            "下方时间标记为绝对时刻(UTC)。"
        )
        lines.append("")
    for ts, source, text in paragraphs:
        # 声道标签与前端 kindLabel 一致:device→系统声音,否则→麦克风。
        label = "系统声音" if source == "device" else "麦克风"
        lines.append(format_marker(ts, label))
        lines.append("")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
