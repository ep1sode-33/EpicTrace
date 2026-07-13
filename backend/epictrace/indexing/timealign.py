"""时间锚共享模块:transcript.md 段落标题里内嵌的时间 marker 的格式化 / 解析 / 偏移映射。

写端(organizer)物化 transcript.md 时,每段前写一行 marker:
    ## [2026-07-11 18:32:05] 麦克风
读端(index)在切块前解析全文,得到 [(码点偏移, ISO 时间串), ...] 锚表,
再用 ts_for_offset 把任意 chunk 的 char_start 映射回它所属段落的会话时刻(引用回跳的根)。

**为何把时间内嵌进正文,而非旁挂元数据**:chunker 与前端 SourceViewer 都以
*同一个* transcript.md 的 Python 码点偏移为坐标系。marker 作为正文的一部分参与切块,
其 m.start() 与 chunk 的 char_start 天然落在同一坐标轴上,按构造对齐,无需另建
偏移↔时间的旁路映射(那种映射会在物化/切块的任一改动下漂移)。

**naive-UTC 约定(全链绑定)**:marker 里的时间一律是**去时区的 UTC**、截断到秒。
format_marker 收到 tz-aware 输入先转 UTC 再抹掉 tzinfo;parse 出的 ISO 串同样无时区后缀。
上下游(event ts、引用回跳)都按此裸 UTC 语义解读,避免本地时区歧义。
"""
from __future__ import annotations

import bisect
import re
from datetime import datetime, timezone

# marker 行格式:`## [YYYY-MM-DD HH:MM:SS] <label>`。MULTILINE 下逐行匹配,
# 捕获组 1 是 naive-UTC 的日期时间串(空格分隔);`]` 之后的 label 部分可有可无。
_MARKER_RE = re.compile(
    r"^## \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\](?:\s.*)?$",
    re.MULTILINE,
)

# marker 内日期时间格式(空格分隔);format / parse 共用一处,避免两端漂移。
_MARKER_DT_FMT = "%Y-%m-%d %H:%M:%S"


def format_marker(ts: datetime, label: str) -> str:
    """把 (时刻, 声道/来源标签) 渲染成一行 transcript marker。

    ts 若带 tzinfo 先归一到 UTC 再去时区(naive-UTC 约定);一律截断到秒。
    """
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    ts = ts.replace(microsecond=0)
    return f"## [{ts.strftime(_MARKER_DT_FMT)}] {label}"


def parse_time_anchors(text: str) -> list[tuple[int, str]]:
    """从全文解析出时间锚表 `[(码点偏移, ISO 时间串), ...]`,按偏移升序。

    偏移取 m.start()(Python 码点坐标,与 chunker/SourceViewer 同系)。
    时间串转成 ISO("T" 分隔、无时区后缀);用 strptime 校验,日期/时间不合法
    的伪 marker(如月 13)直接跳过,不入表。finditer 天然按偏移升序。
    """
    anchors: list[tuple[int, str]] = []
    for m in _MARKER_RE.finditer(text):
        try:
            dt = datetime.strptime(m.group(1), _MARKER_DT_FMT)
        except ValueError:
            continue  # 伪 marker:数字凑对格式但不是合法日期时间
        anchors.append((m.start(), dt.isoformat()))
    return anchors


def split_at_anchors(text: str, anchors: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """按锚偏移把全文切成段,返回 `[(base_offset, segment), ...]`(base 升序)。

    边界 = `[0, a1, a2, …, len(text))`(a_i = 各锚的码点偏移);每段落在**恰好一个** marker
    区间内。index 侧对每段独立 chunk_text、再把 base_offset 加回段内 chunk 的 char_start/
    char_end(回到全局码点坐标,SourceViewer 高亮的根),从而保证**任何 chunk 不跨 marker**,
    ts_for_offset 因而精确。

    `anchors` 为空 → 返回单段 `[(0, text)]`(index 侧行为与直接 chunk_text(text) 完全一致)。
    仅空白/为空的段跳过(不产 chunk)。
    """
    if not anchors:
        return [(0, text)]
    bounds = [0, *(off for off, _ in anchors), len(text)]
    segments: list[tuple[int, str]] = []
    for base, end in zip(bounds, bounds[1:]):
        seg = text[base:end]
        if seg.strip():
            segments.append((base, seg))
    return segments


def ts_for_offset(anchors: list[tuple[int, str]], char_start: int) -> str | None:
    """给定 chunk 的起始码点偏移,返回它所属段落的 ISO 时间串。

    取 `offset <= char_start` 的最右锚;char_start 落在首锚之前则归到首锚;
    锚表为空返回 None。
    """
    if not anchors:
        return None
    idx = bisect.bisect_right(anchors, char_start, key=lambda a: a[0]) - 1
    if idx < 0:  # 首锚之前(如正文前导语):归到首锚的时刻
        return anchors[0][1]
    return anchors[idx][1]
