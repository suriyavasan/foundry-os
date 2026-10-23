"""Built-in role templates: YAML files in ``app/roles/builtin`` validated into ``RoleTemplateSpec``.

The same validator checks company-custom templates (HR/humans create those at runtime), so a
template can never carry a field the built-ins could not.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AgentKind, RoleFamily

ROLES_DIR = Path(__file__).resolve().parent
BUILTIN_DIR = ROLES_DIR / "builtin"
SEED_SKILLS_DIR = ROLES_DIR / "skills"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,39}$")

# Toolsets an employee may ever be given (mirrors the runtime launcher's allowlist).
ALLOWED_TOOLSETS = frozenset(
    {"company", "file", "web", "search", "skills", "todo", "vision", "terminal", "code_execution"}
)
# Toolsets that execute code on the host/sandbox. Only built-in templates and humans may grant them:
# an agent-authored template must not be a path to more power than its author has.
PRIVILEGED_TOOLSETS = frozenset({"terminal", "code_execution"})
# Families an agent can never hire into (founder/HR seats are human decisions).
RESERVED_FAMILIES = frozenset({RoleFamily.FOUNDER.value, RoleFamily.HR.value})


class TemplateError(ValueError):
    """A role template file is invalid."""


class RoleTemplateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    slug: str
    family: RoleFamily
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    default_kind: AgentKind = AgentKind.SPECIALIST
    default_department: str | None = Field(default=None, max_length=80)
    instructions: str = Field(default="", max_length=20_000)
    default_toolsets: tuple[str, ...] = ()
    default_skill_slugs: tuple[str, ...] = ()
    required_skill_tags: tuple[str, ...] = ()

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError("slug must be kebab-case (a-z, 0-9, '-'), at most 64 characters")
        return v

    @field_validator("default_toolsets")
    @classmethod
    def _toolsets(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(v) - ALLOWED_TOOLSETS)
        if unknown:
            raise ValueError(f"unknown toolsets: {', '.join(unknown)}")
        if len(set(v)) != len(v):
            raise ValueError("duplicate toolsets")
        return v

    @field_validator("default_skill_slugs")
    @classmethod
    def _skills(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        bad = [s for s in v if not SLUG_RE.match(s)]
        if bad:
            raise ValueError(f"invalid skill slugs: {', '.join(bad)}")
        if len(set(v)) != len(v) or len(v) > 40:
            raise ValueError("skill slugs must be unique (at most 40)")
        return v

    @field_validator("required_skill_tags")
    @classmethod
    def _tags(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        bad = [t for t in v if not TAG_RE.match(t)]
        if bad:
            raise ValueError(f"tags must be lowercase words: {', '.join(bad)}")
        return v

    def privileged_toolsets(self) -> list[str]:
        return sorted(set(self.default_toolsets) & PRIVILEGED_TOOLSETS)


def parse_template(raw: dict) -> RoleTemplateSpec:
    try:
        return RoleTemplateSpec.model_validate(raw)
    except ValueError as exc:
        raise TemplateError(str(exc)) from exc


def load_template_file(path: Path) -> RoleTemplateSpec:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TemplateError(f"{path.name}: cannot read template: {exc}") from exc
    if not isinstance(raw, dict):
        raise TemplateError(f"{path.name}: template must be a mapping")
    try:
        spec = parse_template(raw)
    except TemplateError as exc:
        raise TemplateError(f"{path.name}: {exc}") from exc
    if path.stem != spec.slug:
        raise TemplateError(f"{path.name}: file name must equal the slug {spec.slug!r}")
    return spec


@lru_cache(maxsize=1)
def builtin_templates() -> dict[str, RoleTemplateSpec]:
    """All built-in templates keyed by slug (validated once per process)."""
    found: dict[str, RoleTemplateSpec] = {}
    for path in sorted(BUILTIN_DIR.glob("*.yaml")):
        spec = load_template_file(path)
        if spec.slug in found:
            raise TemplateError(f"duplicate template slug {spec.slug!r}")
        found[spec.slug] = spec
    if not found:
        raise TemplateError(f"no role templates found in {BUILTIN_DIR}")
    return found


def get_builtin_template(slug: str) -> RoleTemplateSpec | None:
    return builtin_templates().get(slug)


def seed_skill_dir(slug: str) -> Path | None:
    """Directory of a company-authored seed skill shipped with Company OS, if it exists."""
    path = SEED_SKILLS_DIR / slug
    return path if (path / "SKILL.md").is_file() else None


def company_seed_slugs() -> list[str]:
    return sorted(p.name for p in SEED_SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file())


def seed_skill_slugs() -> list[str]:
    """Every skill slug referenced by a built-in template (the library seeded per company)."""
    seen: dict[str, None] = {}
    for spec in builtin_templates().values():
        for slug in spec.default_skill_slugs:
            seen.setdefault(slug)
    return list(seen)
