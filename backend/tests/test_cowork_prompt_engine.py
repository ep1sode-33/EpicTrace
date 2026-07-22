"""模板引擎单测(需求 2):变量替换 / 条件块 / 嵌套 / 错误检测。"""

import pytest

from epictrace.cowork.prompts.engine import render


def test_variable_substitution():
    assert render("你好 {{name}}", {"name": "世界"}) == "你好 世界"


def test_dotted_path():
    assert render("{{session.name}}", {"session": {"name": "s1"}}) == "s1"


def test_missing_variable_renders_empty():
    assert render("[{{nope}}]", {}) == "[]"


def test_if_truthy_and_falsy():
    t = "{{#if tools}}有工具{{/if}}"
    assert render(t, {"tools": ["a"]}) == "有工具"
    assert render(t, {"tools": []}) == ""


def test_if_else():
    t = "{{#if ok}}是{{else}}否{{/if}}"
    assert render(t, {"ok": True}) == "是"
    assert render(t, {"ok": False}) == "否"


def test_false_strings_are_falsy():
    t = "{{#if v}}T{{else}}F{{/if}}"
    for v in ("", "0", "false", "no", "off", " none "):
        assert render(t, {"v": v}) == "F", v
    assert render(t, {"v": "1"}) == "T"


def test_nested_if():
    t = "{{#if a}}A{{#if b}}B{{/if}}{{/if}}"
    assert render(t, {"a": 1, "b": 1}) == "AB"
    assert render(t, {"a": 1, "b": 0}) == "A"
    assert render(t, {"a": 0, "b": 1}) == ""


def test_unselected_branch_not_rendered_but_parsed():
    # else 分支里的变量不应出现在条件为真的结果里
    t = "{{#if a}}好{{else}}{{broken.var}}{{/if}}"
    assert render(t, {"a": True}) == "好"


def test_unclosed_if_raises():
    with pytest.raises(ValueError, match="未闭合"):
        render("{{#if a}}忘了关", {"a": 1})


def test_stray_else_and_endif_raise():
    with pytest.raises(ValueError, match="孤立"):
        render("{{else}}", {})
    with pytest.raises(ValueError, match="孤立"):
        render("{{/if}}", {})


def test_variables_around_blocks_preserved():
    t = "头 {{#if a}}中{{/if}} 尾"
    assert render(t, {"a": 1}) == "头 中 尾"
