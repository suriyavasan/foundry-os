"""SKILL.md parsing: YAML frontmatter -> ``SkillMeta`` (tolerant of Hermes' variations)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_FRONTMATTER_RE = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
MAX_TAGS = 30


class SkillFormatError(ValueError):
    """The SKILL.md is missing or malformed."""


@dataclass
class SkillMeta:
    name: str
    description: str
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    version: str | None = None
    author: str | None = None
    license: str | None = None
    requires_toolsets: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)


def normalize_tag(tag: object) -> str | None:
    text = re.sub(r"[\s_]+", "-", str(tag).strip().lower())
    text = re.sub(r"[^a-z0-9.+-]", "", text).strip("-")
    return text[:40] or None


def normalize_tags(values: object) -> list[str]:
    if isinstance(values, str):
        values = [v for v in re.split(r"[,\s]+", values) if v]
    if not isinstance(values, (list, tuple, set)):
        return []
    out: list[str] = []
    for value in values:
        tag = normalize_tag(value)
        if tag and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillFormatError("SKILL.md must start with a YAML frontmatter block (---)")
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillFormatError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillFormatError("SKILL.md frontmatter must be a mapping")
    return data, text[match.end() :]


def _str(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] or None


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:50]


def _env_names(fm: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in fm.get("required_environment_variables") or []:
        name = item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    prereq = fm.get("prerequisites")
    if isinstance(prereq, dict):
        names.extend(_string_list(prereq.get("env_vars")))
    return list(dict.fromkeys(n for n in names if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", n)))


def parse_skill_md(
    text: str, *, fallback_name: str, fallback_category: str | None = None
) -> SkillMeta:
    fm, _body = split_frontmatter(text)
    meta_block = fm.get("metadata")
    hermes = meta_block.get("hermes") if isinstance(meta_block, dict) else None
    hermes = hermes if isinstance(hermes, dict) else {}
    name = _str(fm.get("name"), 120) or fallback_name
    description = _str(fm.get("description"), 1000)
    if not description:
        raise SkillFormatError("SKILL.md frontmatter needs a description")
    tags = normalize_tags(hermes.get("tags") or fm.get("tags") or [])
    return SkillMeta(
        name=name,
        description=description,
        category=_str(hermes.get("category") or fm.get("category"), 80) or fallback_category,
        tags=tags,
        version=_str(fm.get("version"), 40),
        author=_str(fm.get("author"), 200),
        license=_str(fm.get("license"), 80),
        requires_toolsets=_string_list(hermes.get("requires_toolsets")),
        required_env=_env_names(fm),
        platforms=_string_list(fm.get("platforms")),
        frontmatter=fm,
    )


def check_slug(slug: str) -> str:
    if not SLUG_RE.match(slug):
        raise SkillFormatError(
            "skill slug must be lowercase letters, digits, '.', '_' or '-' (max 64 characters)"
        )
    return slug
