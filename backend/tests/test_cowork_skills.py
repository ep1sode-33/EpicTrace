"""Skill 系统测试(需求 6):加载(裸目录/.skill ZIP/覆盖/坏包跳过)+ prompt 注入 + API。"""

import zipfile

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.cowork.agents import AgentDef
from epictrace.cowork.approvals import ApprovalManager
from epictrace.cowork.dispatch import Dispatcher
from epictrace.cowork.llm_client import LLMResponse
from epictrace.cowork.sessions import SessionManager
from epictrace.cowork.skills import load_skills, parse_skill_md
from epictrace.cowork.tools.registry import ToolDef, ToolRegistry
from tests.fakes import FakeCoworkComplete


def _write_skill_md(path, name, description="测试 skill", body="# 指导\n照做。"):
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8")


# ---- 加载 ----

def test_bundled_skills_loaded(tmp_path):
    skills = load_skills(AppConfig(data_dir=tmp_path))
    assert {"pdf-reading", "docx", "pptx"} <= set(skills)
    assert all(s.source == "bundled" for s in skills.values())
    assert skills["pdf-reading"].body  # frontmatter 被剥离,只剩正文


def test_user_dir_bare_dir_form(tmp_path):
    d = tmp_path / "skills" / "my-skill"
    d.mkdir(parents=True)
    _write_skill_md(d / "SKILL.md", "my-skill")
    skills = load_skills(AppConfig(data_dir=tmp_path))
    assert skills["my-skill"].source == "user"


def test_user_dir_zip_form(tmp_path):
    """验收 7:Markdown 打包为 .skill 放进用户目录即可被加载。"""
    d = tmp_path / "skills"
    d.mkdir()
    with zipfile.ZipFile(d / "zipped.skill", "w") as z:
        z.writestr("SKILL.md", "---\nname: zipped\ndescription: zip 包\n---\n\n# 正文\n步骤。\n")
        z.writestr("scripts/helper.py", "print('hi')\n")
    skills = load_skills(AppConfig(data_dir=tmp_path))
    assert skills["zipped"].description == "zip 包"
    assert "步骤" in skills["zipped"].body


def test_zip_with_top_level_dir(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    with zipfile.ZipFile(d / "nested.skill", "w") as z:
        z.writestr("nested/SKILL.md", "---\nname: nested\ndescription: n\n---\n\n正文。\n")
    assert "nested" in load_skills(AppConfig(data_dir=tmp_path))


def test_user_overrides_bundled(tmp_path):
    d = tmp_path / "skills" / "docx"
    d.mkdir(parents=True)
    _write_skill_md(d / "SKILL.md", "docx", description="用户覆盖版")
    skills = load_skills(AppConfig(data_dir=tmp_path))
    assert skills["docx"].description == "用户覆盖版"
    assert skills["docx"].source == "user"


def test_bad_skill_skipped(tmp_path):
    d = tmp_path / "skills"
    bad = d / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("没有 frontmatter 的正文", encoding="utf-8")
    (d / "junk.skill").write_text("这不是 zip", encoding="utf-8")
    skills = load_skills(AppConfig(data_dir=tmp_path))
    assert "bad" not in skills and "junk" not in skills
    assert "pdf-reading" in skills  # 坏包不影响其它加载


def test_parse_requires_name_and_body():
    with pytest.raises(ValueError):
        parse_skill_md("---\ndescription: 无名\n---\n\n正文\n", origin="t", source="user")
    with pytest.raises(ValueError):
        parse_skill_md("---\nname: x\n---\n\n\n", origin="t", source="user")


# ---- prompt 注入 ----

def test_child_prompt_skills_whitelist(tmp_path):
    """dispatch_child 按 AgentDef.skills 注入;未列出的 skill 不进 prompt(需求 6)。"""
    config = AppConfig(data_dir=tmp_path)
    db = Database(config)
    db.create_all()
    sessions = SessionManager(db)
    skills = load_skills(config)
    defs = {"worker": AgentDef(name="worker", description="", tools=("echo_tool",),
                               skills=("pdf-reading",))}
    disp = Dispatcher(db=db, sessions=sessions, agent_defs=defs,
                      approvals=ApprovalManager(), config=config, skills=skills)
    registry = ToolRegistry()
    registry.register(ToolDef(name="echo_tool", description="e", parameters={},
                              handler=lambda: "ok", permission="allow"))
    parent = sessions.create(type="agent")

    captured = {}

    def spy_complete(messages, tools):
        captured["system"] = messages[0]["content"]
        return LLMResponse(content="done")

    disp.start(parent_session_id=parent.id, agent_name="worker", task="读个 PDF",
               registry=registry, complete_factory=lambda model="": spy_complete)
    disp.wait(parent_session_id=parent.id, timeout=10)

    assert "# 已加载 Skills" in captured["system"]
    assert "pdf-reading" in captured["system"]
    assert "pptx" not in captured["system"]  # 白名单外不注入
