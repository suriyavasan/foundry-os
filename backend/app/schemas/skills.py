from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp

SkillStatusLiteral = Literal["active", "quarantined", "archived"]
SkillSourceKindLiteral = Literal[
    "hermes_bundled", "hermes_optional", "github", "company_authored", "uploaded"
]
SkillTrustLiteral = Literal["builtin", "trusted", "community"]
ScanVerdictLiteral = Literal["safe", "caution", "dangerous", "unscanned"]
ActorTypeLiteral = Literal["human", "agent", "system"]


class Skill(ApiModel):
    id: str
    company_id: str
    slug: str
    name: str
    description: str
    category: str | None
    tags: list[str]
    version: str | None
    author: str | None
    license: str | None
    source_kind: SkillSourceKindLiteral
    source_ref: str
    blob_sha256: str
    trust: SkillTrustLiteral
    scan_verdict: ScanVerdictLiteral
    status: SkillStatusLiteral
    requires_toolsets: list[str]
    required_env: list[str]  # variable NAMES only
    created_by_type: ActorTypeLiteral
    created_by_id: str | None
    created_at: Timestamp
    updated_at: Timestamp


class SkillFileInfo(ApiModel):
    path: str
    size_bytes: int


class SkillAssignedAgent(ApiModel):
    id: str
    name: str
    role_title: str
    status: str


class SkillDetail(Skill):
    scan_report: dict[str, Any]
    frontmatter: dict[str, Any]
    files: list[SkillFileInfo]
    assigned_agents: list[SkillAssignedAgent]


class SkillFileContent(ApiModel):
    path: str
    content: str
    truncated: bool


class SkillInstall(RequestModel):
    source_ref: str = Field(min_length=1, max_length=400)
    slug: str | None = Field(default=None, min_length=1, max_length=64)


class SkillAuthorFile(RequestModel):
    path: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=500_000)


class SkillAuthor(RequestModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    body_markdown: str = Field(min_length=1, max_length=200_000)
    files: list[SkillAuthorFile] = Field(default_factory=list, max_length=99)

    @field_validator("body_markdown")
    @classmethod
    def _no_frontmatter(cls, v: str) -> str:
        if v.lstrip().startswith("---"):
            raise ValueError("body_markdown must not include frontmatter; it is generated")
        return v


class SkillNote(RequestModel):
    note: str | None = Field(default=None, max_length=1000)


class SkillSeedAccepted(ApiModel):
    job_id: str | None
    status: str  # queued | already_queued


class SkillSeedResult(ApiModel):
    installed: list[str]
    updated: list[str]
    unchanged: list[str]
    quarantined: list[str]
    missing: list[str]
    agents_equipped: list[str]


class SkillInstallResult(ApiModel):
    skill: Skill
    outcome: Literal["installed", "updated", "unchanged"]
    quarantined: bool
    scan: dict[str, Any]


class SkillRefs(RequestModel):
    skill_ids: list[str] = Field(default_factory=list, max_length=50)
    slugs: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _one(self):
        if not self.skill_ids and not self.slugs:
            raise ValueError("provide skill_ids or slugs")
        return self


class AgentSkill(ApiModel):
    skill: Skill
    assigned_by_type: ActorTypeLiteral
    assigned_by_id: str | None
    assigned_at: Timestamp


class CatalogItem(ApiModel):
    slug: str
    name: str
    description: str
    tags: list[str]
    license: str | None
    source: Literal["library", "hermes", "github"]
    source_kind: str | None
    source_ref: str
    item_type: Literal["skill", "repository"] = "skill"
    installed: bool
    skill_id: str | None = None
    status: str | None = None
    stars: int | None = None
