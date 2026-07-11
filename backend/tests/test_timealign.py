"""timealign 时间锚模块:marker 格式化/解析 round-trip、偏移→时间戳映射、边界与伪 marker。"""
from datetime import datetime, timedelta, timezone

from epictrace.indexing.timealign import (
    format_marker,
    parse_time_anchors,
    ts_for_offset,
)


def test_format_marker_basic():
    ts = datetime(2026, 7, 11, 18, 32, 5)
    assert format_marker(ts, "麦克风") == "## [2026-07-11 18:32:05] 麦克风"


def test_format_marker_truncates_subsecond():
    ts = datetime(2026, 7, 11, 18, 32, 5, 999999)   # 亚秒必须被截断
    assert format_marker(ts, "系统声音") == "## [2026-07-11 18:32:05] 系统声音"


def test_format_marker_tz_aware_normalized_to_utc():
    # 东八区 07-12 02:00 == UTC 07-11 18:00;归一到 naive-UTC 后去时区
    tz8 = timezone(timedelta(hours=8))
    ts = datetime(2026, 7, 12, 2, 0, 0, tzinfo=tz8)
    assert format_marker(ts, "麦克风") == "## [2026-07-11 18:00:00] 麦克风"


def test_format_parse_round_trip():
    ts = datetime(2026, 7, 11, 18, 32, 5)
    marker = format_marker(ts, "麦克风")
    assert parse_time_anchors(marker) == [(0, "2026-07-11T18:32:05")]


def test_parse_multiple_anchors_ascending():
    text = (
        format_marker(datetime(2026, 1, 1, 0, 0, 0), "麦克风") + "\n"
        + "内容一\n\n"
        + format_marker(datetime(2026, 1, 1, 0, 5, 0), "系统声音") + "\n"
        + "内容二\n"
    )
    anchors = parse_time_anchors(text)
    assert len(anchors) == 2
    assert anchors[0] == (0, "2026-01-01T00:00:00")
    assert anchors[1][1] == "2026-01-01T00:05:00"
    assert anchors[0][0] < anchors[1][0]                       # 偏移升序
    assert text[anchors[1][0]:].startswith("## [2026-01-01 00:05:00]")


def test_parse_offset_is_codepoint_index():
    # 前置含 CJK(UTF-8 多字节):offset 必须是码点数,不是字节数
    prefix = "前言\n\n"                                          # 4 个码点
    text = prefix + format_marker(datetime(2026, 7, 11, 0, 0, 0), "麦克风")
    anchors = parse_time_anchors(text)
    assert anchors[0][0] == len(prefix)
    assert text[anchors[0][0]:].startswith("## [")


def test_parse_skips_invalid_date_pseudo_marker():
    text = "## [2026-13-11 18:32:05] 麦克风\n真正内容\n"           # 月 13 非法
    assert parse_time_anchors(text) == []


def test_parse_skips_invalid_time_pseudo_marker():
    text = "## [2026-07-11 25:00:00] 系统声音\n"                  # 时 25 非法
    assert parse_time_anchors(text) == []


def test_parse_mixes_valid_and_invalid():
    text = (
        "## [2026-13-01 00:00:00] 伪\n"                          # 非法,跳过
        + format_marker(datetime(2026, 7, 11, 9, 0, 0), "麦克风") + "\n"
    )
    anchors = parse_time_anchors(text)
    assert anchors == [(text.index("## [2026-07-11"), "2026-07-11T09:00:00")]


def test_ts_for_offset_between_and_after_anchors():
    anchors = [(0, "2026-01-01T00:00:00"), (100, "2026-01-01T00:05:00")]
    assert ts_for_offset(anchors, 50) == "2026-01-01T00:00:00"   # 落在首锚区间
    assert ts_for_offset(anchors, 100) == "2026-01-01T00:05:00"  # 正好次锚起点
    assert ts_for_offset(anchors, 150) == "2026-01-01T00:05:00"  # 末锚之后


def test_ts_for_offset_before_first_anchor_returns_first():
    anchors = [(10, "2026-01-01T00:00:00"), (100, "2026-01-01T00:05:00")]
    assert ts_for_offset(anchors, 0) == "2026-01-01T00:00:00"    # 首锚之前 → 归首锚
    assert ts_for_offset(anchors, 5) == "2026-01-01T00:00:00"


def test_ts_for_offset_empty_anchors_returns_none():
    assert ts_for_offset([], 0) is None
    assert ts_for_offset([], 999) is None
