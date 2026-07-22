"""ToolRegistry 单测(需求 3):注册/schema 输出/执行与错误回传。"""

import json

import pytest

from epictrace.cowork.tools.registry import ToolDef, ToolRegistry


def _echo(text: str) -> str:
    return f"echo:{text}"


def _tool(name="echo", **kw):
    return ToolDef(
        name=name,
        description="回声工具",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=_echo,
        **kw,
    )


def test_register_and_get():
    r = ToolRegistry()
    r.register(_tool())
    assert r.get("echo").description == "回声工具"
    assert r.get("missing") is None
    assert [t.name for t in r.list()] == ["echo"]


def test_duplicate_register_raises():
    r = ToolRegistry()
    r.register(_tool())
    with pytest.raises(ValueError, match="duplicate"):
        r.register(_tool())


def test_invalid_permission_or_sandbox_rejected():
    with pytest.raises(ValueError):
        _tool(permission="yolo")
    with pytest.raises(ValueError):
        _tool(sandbox="maybe")


def test_openai_schemas_shape():
    r = ToolRegistry()
    r.register(_tool())
    schemas = r.openai_schemas()
    assert schemas == [{
        "type": "function",
        "function": {
            "name": "echo",
            "description": "回声工具",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }]


def test_openai_schemas_whitelist():
    r = ToolRegistry()
    r.register(_tool("a"))
    r.register(_tool("b"))
    assert [s["function"]["name"] for s in r.openai_schemas(["b"])] == ["b"]
    # 白名单里的未知名被忽略而不是报错
    assert [s["function"]["name"] for s in r.openai_schemas(["b", "zzz"])] == ["b"]


def test_execute_ok():
    r = ToolRegistry()
    r.register(_tool())
    assert r.execute("echo", json.dumps({"text": "hi"})) == "echo:hi"


def test_execute_unknown_tool_returns_error():
    assert "unknown tool" in ToolRegistry().execute("nope", "{}")


def test_execute_bad_json_returns_error():
    r = ToolRegistry()
    r.register(_tool())
    assert "invalid JSON" in r.execute("echo", "{not json")
    assert "must be a JSON object" in r.execute("echo", "[1,2]")


def test_execute_bad_args_returns_error():
    r = ToolRegistry()
    r.register(_tool())
    assert "bad arguments" in r.execute("echo", json.dumps({"wrong": 1}))


def test_execute_handler_exception_returns_error_not_raise():
    def boom(**_):
        raise RuntimeError("炸了")

    r = ToolRegistry()
    r.register(ToolDef(name="boom", description="d", parameters={}, handler=boom))
    out = r.execute("boom", "{}")
    assert "RuntimeError" in out and "炸了" in out


def test_execute_non_string_result_serialized():
    r = ToolRegistry()
    r.register(ToolDef(name="d", description="d", parameters={}, handler=lambda: {"a": 1}))
    assert json.loads(r.execute("d", "{}")) == {"a": 1}
