"""Skill 系统(需求 6)。

Skill 是给 LLM 的专项工作指导包:核心是 `SKILL.md`(YAML frontmatter 声明
name/description + Markdown 正文),可附 scripts/ schemas/ 等资源。
两种打包形态都认:
- 裸目录:`skills/pdf-reading/SKILL.md`
- `.skill` 文件:ZIP 归档,根目录(或唯一顶层目录)内含 SKILL.md

加载来源(后者覆盖同名前者):
- 捆绑:`epictrace/cowork/skills_bundle/`
- 用户:`~/.epictrace/skills/`(验收 7:放一个 .skill 进去,重载后自动出现)

加载后注入 agent 的 system prompt(skills 节);description 是 LLM 判断何时
遵循该 skill 的依据。主 agent 注入全部;子 agent 按 AgentDef.skills 白名单。
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from epictrace.config import AppConfig

log = logging.getLogger("epictrace.cowork")


@dataclass(frozen=True)
class SkillDef:
    name: str
    description: str
    body: str            # SKILL.md 的 Markdown 正文(去掉 frontmatter)
    source: str = "user"  # bundled | user

    def as_prompt_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "body": self.body}


def parse_skill_md(text: str, *, origin: str, source: str) -> SkillDef:
    """解析 SKILL.md:`---` 围起的 YAML frontmatter(name/description)+ Markdown 正文。"""
    parts = text.split("---", 2)
    if len(parts) < 3 or parts[0].strip():
        raise ValueError(f"{origin}: 缺少 YAML frontmatter(--- 围起的 name/description)")
    meta = yaml.safe_load(parts[1])
    if not isinstance(meta, dict):
        raise ValueError(f"{origin}: frontmatter 必须是 mapping")
    name = str(meta.get("name") or "").strip()
    if not name:
        raise ValueError(f"{origin}: frontmatter 缺少 name")
    body = parts[2].strip()
    if not body:
        raise ValueError(f"{origin}: 正文为空")
    return SkillDef(
        name=name,
        description=str(meta.get("description") or "").strip(),
        body=body,
        source=source,
    )


def _read_zip_skill(path: Path) -> str:
    """从 .skill(ZIP)中取 SKILL.md 文本:优先根目录,否则唯一顶层目录下的。"""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        root = [n for n in names if n == "SKILL.md"]
        nested = [n for n in names if n.endswith("/SKILL.md") and n.count("/") == 1]
        pick = (root or nested or [None])[0]
        if pick is None:
            raise ValueError(f"{path.name}: 归档内找不到 SKILL.md")
        return z.read(pick).decode("utf-8")


def _load_dir(path: Path, *, source: str, into: dict[str, SkillDef]) -> None:
    if not path.is_dir():
        return
    # 裸目录形态:<dir>/<skill-name>/SKILL.md
    for d in sorted(p for p in path.iterdir() if p.is_dir()):
        md = d / "SKILL.md"
        if not md.is_file():
            continue
        try:
            sk = parse_skill_md(md.read_text(encoding="utf-8"), origin=str(md), source=source)
            into[sk.name] = sk
        except (yaml.YAMLError, ValueError, OSError) as e:
            log.warning("skill %s 加载失败,跳过: %s", md, e)
    # .skill(ZIP)形态
    for f in sorted(path.glob("*.skill")):
        try:
            sk = parse_skill_md(_read_zip_skill(f), origin=str(f), source=source)
            into[sk.name] = sk
        except (zipfile.BadZipFile, yaml.YAMLError, ValueError, OSError, UnicodeDecodeError) as e:
            log.warning("skill %s 加载失败,跳过: %s", f, e)


def load_skills(config: AppConfig, extra_dir: Path | None = None) -> dict[str, SkillDef]:
    """加载全部 skill:捆绑目录 → 用户目录(~/.epictrace/skills)→ extra_dir(测试)。"""
    skills: dict[str, SkillDef] = {}
    _load_dir(Path(__file__).parent / "skills_bundle", source="bundled", into=skills)
    _load_dir(config.data_dir / "skills", source="user", into=skills)
    if extra_dir is not None:
        _load_dir(extra_dir, source="user", into=skills)
    return skills
