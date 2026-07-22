"""极简模板引擎(需求 2)。

支持两种语法,对齐 Cowork bundle 中 m5n() 的语义:
- {{variable}}        变量替换(支持点号路径,如 {{session.name}};缺失变量渲染为空串)
- {{#if cond}}...{{else}}...{{/if}}   条件块(cond 为点号路径;{{else}} 可省略;允许嵌套)

未闭合的 {{#if}} 块、孤立的 {{else}}/{{/if}} 一律抛 ValueError。
只渲染被选中的分支(未选中分支不参与变量替换)。
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\{\{\s*(#if\s+[\w.]+|else|/if|[\w.]+)\s*\}\}")
_FALSE_STRINGS = {"", "0", "false", "no", "off", "none", "null"}


def _lookup(context: dict, path: str):
    cur = context
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_STRINGS
    return bool(value)


def _locate_if(t: str, pos: int) -> tuple[str, str | None, int]:
    """从 if 体起点扫描(感知嵌套 if),返回 (then 原文, else 原文|None, {{/if}} 之后的位置)。"""
    depth = 1
    else_span: tuple[int, int] | None = None
    scan = pos
    while True:
        m = _TOKEN.search(t, scan)
        if not m:
            raise ValueError("模板渲染失败:存在未闭合的 {{#if}} 块")
        tag = m.group(1)
        if tag.startswith("#if"):
            depth += 1
        elif tag == "/if":
            depth -= 1
            if depth == 0:
                if else_span is not None:
                    return t[pos:else_span[0]], t[else_span[1]:m.start()], m.end()
                return t[pos:m.start()], None, m.end()
        elif tag == "else" and depth == 1:
            else_span = (m.start(), m.end())
        scan = m.end()


def render(template: str, context: dict) -> str:
    """渲染模板。context 为变量字典;语法错误抛 ValueError。"""
    out: list[str] = []
    pos = 0
    while True:
        m = _TOKEN.search(template, pos)
        if not m:
            out.append(template[pos:])
            return "".join(out)
        tag = m.group(1)
        if tag in ("else", "/if"):
            raise ValueError(f"模板渲染失败:孤立的 {{{{{tag}}}}}")
        out.append(template[pos:m.start()])
        if tag.startswith("#if"):
            cond = tag[3:].strip()
            then_s, else_s, end = _locate_if(template, m.end())
            branch = then_s if _truthy(_lookup(context, cond)) else else_s
            if branch:
                out.append(render(branch, context))
            pos = end
        else:
            value = _lookup(context, tag)
            out.append("" if value is None else str(value))
            pos = m.end()
