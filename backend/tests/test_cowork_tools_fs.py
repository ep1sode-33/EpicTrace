"""文件系统内置工具单测:项目隔离 / 路径越界防护 / 搜索。"""

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Project
from epictrace.cowork.tools.builtin_fs import build_fs_tools


@pytest.fixture()
def tools(tmp_path):
    proj_dir = tmp_path / "proj"
    (proj_dir / "docs").mkdir(parents=True)
    (proj_dir / "docs" / "a.md").write_text("# 标题\nalpha beta\n", encoding="utf-8")
    (proj_dir / "b.txt").write_text("gamma\nalpha again\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = Database(AppConfig(data_dir=data_dir))
    db.create_all()
    with db.session() as s:
        s.add(Project(title="测试项目", folder_path=str(proj_dir)))
    return {t.name: t for t in build_fs_tools(db)}


def test_list_projects(tools):
    out = tools["list_projects"].handler()
    assert "测试项目" in out and "id=1" in out


def test_list_files(tools):
    out = tools["list_files"].handler(project_id=1)
    assert "docs/a.md" in out and "b.txt" in out


def test_list_files_missing_project(tools):
    assert "not found" in tools["list_files"].handler(project_id=99)


def test_read_file(tools):
    out = tools["read_file"].handler(project_id=1, path="docs/a.md")
    assert "alpha beta" in out


def test_read_file_pagination(tools):
    out = tools["read_file"].handler(project_id=1, path="docs/a.md", offset=2, limit=5)
    assert "标题" in out  # offset 2 起 5 字符:「标题\nal」区间内
    assert "全文" in out


def test_read_file_blocks_path_escape(tools):
    assert "not found" in tools["read_file"].handler(project_id=1, path="../../etc/passwd")
    assert "not found" in tools["read_file"].handler(project_id=1, path="/etc/passwd")


def test_search_text(tools):
    out = tools["search_text"].handler(project_id=1, pattern="alpha")
    assert "a.md" in out and "b.txt" in out
    # 正则语法:字符类匹配
    out_re = tools["search_text"].handler(project_id=1, pattern="alp[ha]{2}")
    assert "a.md" in out_re


def test_search_text_invalid_regex_falls_back_to_literal(tools):
    # 无效正则不报错,回退为字面量搜索:「alp[ha」不是任何行的字面内容 → 未找到
    assert "未找到" in tools["search_text"].handler(project_id=1, pattern="alp[ha")
    # 含正则特殊字符的字面内容能命中
    assert "标题" in tools["search_text"].handler(project_id=1, pattern="# 标题")


def test_search_text_no_match(tools):
    assert "未找到" in tools["search_text"].handler(project_id=1, pattern="zzzzz")
