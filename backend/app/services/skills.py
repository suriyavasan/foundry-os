"""Skill library: import (Hermes / GitHub / authored), scan policy, assignment, materialization.

Authorization lives here: humans (owner/admin) and the HR agent may install, author and assign;
nobody may assign skills to themselves; only ``active`` skills reach an agent. Every change is
audited and mirrored to the company event stream in the same transaction.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    InvalidTransition,
    LicenseRefused,
    NotFound,
    ScannerUnavailable,
    UpstreamError,
    ValidationFailed,
)
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.enums import (
    AgentStatus,
    JobStatus,
    ScanVerdict,
    SkillSourceKind,
    SkillStatus,
    SkillTrust,
)
from app.models.run import Job
from app.models.skill import AgentSkill, Skill, SkillBlob
from app.roles import loader as roles
from app.schemas.skills import SkillAuthor
from app.services import audit, events, hr_access, jobs
from app.services.common import creator_of
from app.services.pagination import paginate
from app.skills import scan as scanmod
from app.skills.blobstore import (
    BlobStore,
    NormalizedSkill,
    SkillFile,
    SkillRejected,
    hash_dir,
    normalize_dir,
    normalize_files,
)
from app.skills.env import SkillsEnv
from app.skills.frontmatter import (
    SkillFormatError,
    SkillMeta,
    check_slug,
    normalize_tags,
    parse_skill_md,
)
from app.skills.github import GithubError, GithubNotFound
from app.skills.license import detect_license_text, normalize_license
from app.skills.refs import BadSourceRef, GithubRef, HermesRef, parse_source_ref

logger = logging.getLogger("company_os.skills")

COMPANY_SEED_PREFIX = "company_os:roles/skills/"
_LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "LICENCE")


# ---------------------------------------------------------------- results


@dataclass
class ImportResult:
    skill: Skill
    outcome: str  # installed | updated | unchanged
    quarantined: bool
    scan: dict[str, Any]


@dataclass
class SeedResult:
    installed: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    agents_equipped: list[str] = field(default_factory=list)


@dataclass
class MaterializeResult:
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------- helpers


def source_identity(source_ref: str) -> str:
    """Stable identity of where a skill comes from, ignoring the pinned commit."""
    try:
        ref = parse_source_ref(source_ref)
    except BadSourceRef:
        return source_ref
    if isinstance(ref, GithubRef):
        return f"github:{ref.owner.lower()}/{ref.repo.lower()}:{ref.path}"
    return f"hermes:{ref.target}"


def _get_by_slug(db: Session, company_id: str, slug: str) -> Skill | None:
    return db.scalar(select(Skill).where(Skill.company_id == company_id, Skill.slug == slug))


def get_skill(db: Session, ctx: CompanyContext, skill_id: str) -> Skill:
    skill = db.scalar(select(Skill).where(Skill.id == skill_id, Skill.company_id == ctx.company_id))
    if skill is None:
        raise NotFound("Skill not found")
    return skill


def resolve_skill_ref(db: Session, company_id: str, ref: str) -> Skill:
    skill = db.scalar(
        select(Skill).where(Skill.company_id == company_id, or_(Skill.id == ref, Skill.slug == ref))
    )
    if skill is None:
        raise NotFound(f"Skill not found: {ref}")
    return skill


def _blob_row(db: Session, info) -> None:
    if db.get(SkillBlob, info.sha256) is None:
        db.add(
            SkillBlob(sha256=info.sha256, size_bytes=info.size_bytes, file_count=info.file_count)
        )
        db.flush()


def _scan(env: SkillsEnv, blob_path: Path, trust: str, guard_source: str) -> scanmod.ScanReport:
    """Run the guard. Builtin sources degrade to ``unscanned``; everything else fails closed."""
    if not env.scanner.available():
        if trust == SkillTrust.BUILTIN:
            return scanmod.UNSCANNED
        raise ScannerUnavailable(
            "The skill security scanner (runtime Python environment) is not installed; "
            "only built-in skills can be imported until it is"
        )
    try:
        return env.scanner.scan(blob_path, source=guard_source)
    except (scanmod.ScanFailed, scanmod.ScannerUnavailable) as exc:
        if trust == SkillTrust.BUILTIN:
            logger.warning("scan of a built-in skill failed: %s", exc)
            return scanmod.ScanReport(
                verdict=ScanVerdict.UNSCANNED, summary=f"scan failed: {exc}"[:300]
            )
        raise ScannerUnavailable(f"The skill security scan failed: {exc}") from exc


def require_scanner_for(env: SkillsEnv, trust: str) -> None:
    if trust != SkillTrust.BUILTIN and not env.scanner.available():
        raise ScannerUnavailable(
            "The skill security scanner (runtime Python environment) is not installed; "
            "only built-in skills can be imported until it is"
        )


def _mutating_principal_ok(db: Session, ctx: CompanyContext, action: str) -> None:
    hr_access.require_human_or_hr(db, ctx, action)


def _bump(version: str | None) -> str:
    if not version:
        return "1.0.0"
    parts = version.split(".")
    if parts[-1].isdigit():
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)
    return f"{version}.1"


# ---------------------------------------------------------------- import core


def import_skill(
    db: Session,
    company_id: str,
    env: SkillsEnv,
    *,
    normalized: NormalizedSkill,
    meta: SkillMeta,
    slug: str,
    source_kind: str,
    source_ref: str,
    trust: str,
    guard_source: str,
    principal: Principal | None,
    license_spdx: str | None = None,
    keep_archived: bool = False,
    version_override: str | None = None,
) -> ImportResult:
    """Store, scan and register a normalized skill. Does not commit.

    Re-importing the same source updates the row in place (previous blob stays on disk, both
    hashes are in the audit event). A different source under an existing slug is a conflict.
    """
    try:
        check_slug(slug)
    except SkillFormatError as exc:
        raise ValidationFailed(str(exc)) from exc
    existing = _get_by_slug(db, company_id, slug)
    if existing is not None and source_identity(existing.source_ref) != source_identity(source_ref):
        raise Conflict(
            f"The slug {slug!r} is already used by a different skill ({existing.source_ref}); "
            "pass another slug",
            details={"slug": slug, "existing_source_ref": existing.source_ref},
        )
    if existing is not None and keep_archived and existing.status == SkillStatus.ARCHIVED:
        return ImportResult(existing, "unchanged", False, existing.scan_report)

    same_blob = existing is not None and existing.blob_sha256 == normalized.sha256
    rescan_needed = existing is not None and (
        existing.scan_verdict == ScanVerdict.UNSCANNED and env.scanner.available()
    )
    if (
        existing is not None
        and same_blob
        and existing.status in (SkillStatus.ACTIVE, SkillStatus.QUARANTINED)
        and not rescan_needed
    ):
        return ImportResult(
            existing, "unchanged", existing.status == SkillStatus.QUARANTINED, existing.scan_report
        )

    require_scanner_for(env, trust)
    info = env.blobs.put(normalized)
    _blob_row(db, info)
    report = _scan(env, env.blobs.path(info.sha256), trust, guard_source)
    allowed = scanmod.allowed_by_policy(trust, report.verdict)
    status = SkillStatus.ACTIVE if allowed else SkillStatus.QUARANTINED
    scan_json = report.to_json()
    version = version_override or meta.version
    values = {
        "name": meta.name[:120],
        "description": meta.description,
        "category": meta.category,
        "tags": list(meta.tags),
        "version": version,
        "author": meta.author,
        "license": license_spdx if license_spdx is not None else meta.license,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "blob_sha256": info.sha256,
        "trust": trust,
        "scan_verdict": report.verdict,
        "scan_report": scan_json,
        "status": status,
        "requires_toolsets": list(meta.requires_toolsets),
        "required_env": list(meta.required_env),
    }
    ctype, cid = creator_of(principal)
    now = utcnow()
    if existing is None:
        skill = Skill(
            company_id=company_id, slug=slug, created_by_type=ctype, created_by_id=cid, **values
        )
        db.add(skill)
        outcome = "installed"
        previous: dict[str, Any] = {}
    else:
        skill = existing
        previous = {
            "previous_blob_sha256": skill.blob_sha256,
            "previous_version": skill.version,
            "previous_status": skill.status,
        }
        for key, value in values.items():
            setattr(skill, key, value)
        skill.updated_at = now
        outcome = "updated"
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict(f"The slug {slug!r} was taken concurrently; retry") from exc

    data = {
        "slug": slug,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "blob_sha256": info.sha256,
        "trust": trust,
        "scan_verdict": report.verdict,
        "status": status,
        "finding_count": scan_json["finding_count"],
        **previous,
    }
    audit.record(
        db,
        company_id=company_id,
        principal=principal,
        action="skill.installed" if outcome == "installed" else "skill.updated",
        target_type="skill",
        target_id=skill.id,
        summary=f"{'Installed' if outcome == 'installed' else 'Updated'} skill {slug}"
        f" ({source_kind}, {report.verdict})",
        data=data,
    )
    events.emit(
        db,
        company_id,
        "skill.installed" if outcome == "installed" else "skill.updated",
        {"skill_id": skill.id, "slug": slug, "status": status, "source_kind": source_kind},
    )
    if status == SkillStatus.QUARANTINED:
        audit.record(
            db,
            company_id=company_id,
            principal=principal,
            action="skill.quarantined",
            target_type="skill",
            target_id=skill.id,
            summary=f"Quarantined skill {slug}: {report.verdict} verdict from the scanner",
            data={"slug": slug, "scan_verdict": report.verdict, "trust": trust},
        )
        events.emit(db, company_id, "skill.quarantined", {"skill_id": skill.id, "slug": slug})
    return ImportResult(skill, outcome, status == SkillStatus.QUARANTINED, scan_json)


# ---------------------------------------------------------------- sources


def _read_meta(
    normalized: NormalizedSkill, fallback_name: str, category: str | None = None
) -> SkillMeta:
    data = normalized.read("SKILL.md") or b""
    return parse_skill_md(
        data.decode("utf-8", errors="replace"),
        fallback_name=fallback_name,
        fallback_category=category,
    )


def _wrap_rejections(fn):
    try:
        return fn()
    except SkillRejected as exc:
        raise ValidationFailed(str(exc)) from exc
    except SkillFormatError as exc:
        raise ValidationFailed(str(exc)) from exc


def install_hermes(
    db: Session,
    company_id: str,
    env: SkillsEnv,
    target: str,
    *,
    slug: str | None,
    principal: Principal | None,
    keep_archived: bool = False,
) -> ImportResult:
    found = env.hermes.find(target)
    if found is None:
        raise NotFound(f"Hermes skill not found: {target}")

    def build():
        normalized = normalize_dir(found.path)
        return normalized, _read_meta(normalized, found.slug, found.meta.category)

    normalized, meta = _wrap_rejections(build)
    return import_skill(
        db,
        company_id,
        env,
        normalized=normalized,
        meta=meta,
        slug=slug or found.slug,
        source_kind=found.source_kind,
        source_ref=found.source_ref(env.hermes.commit()),
        trust=SkillTrust.BUILTIN,
        guard_source="official",
        principal=principal,
        keep_archived=keep_archived,
    )


def _github_license(meta: SkillMeta, normalized: NormalizedSkill, client, ref: GithubRef) -> str:
    """Skill-level frontmatter license, else a LICENSE file in the skill, else the repo license.
    Anything not on the allowlist (or unknown) is refused."""
    if meta.license:
        spdx = normalize_license(meta.license)
        if spdx is None:
            raise LicenseRefused(
                f"The skill's license ({meta.license!r}) is not on the allowlist",
                details={"license": meta.license},
            )
        return spdx
    for name in _LICENSE_FILES:
        data = normalized.read(name)
        if data is not None:
            spdx = detect_license_text(data.decode("utf-8", errors="replace"))
            if spdx is None:
                raise LicenseRefused(
                    f"The skill's {name} is not an allowed license", details={"file": name}
                )
            return spdx
    try:
        repo_license = client.repo_license(ref.owner, ref.repo)
    except GithubError as exc:
        raise UpstreamError(str(exc)) from exc
    spdx = normalize_license(repo_license)
    if spdx is None:
        raise LicenseRefused(
            "The skill has no license, and the repository's license is "
            + (f"{repo_license!r}, which is not allowed" if repo_license else "unknown"),
            details={"license": repo_license},
        )
    return spdx


def install_github(
    db: Session,
    company_id: str,
    env: SkillsEnv,
    ref: GithubRef,
    *,
    slug: str | None,
    principal: Principal | None,
) -> ImportResult:
    from app.skills.scan import trust_for_github

    require_scanner_for(env, SkillTrust.COMMUNITY)  # fail closed before any network use
    client = env.github()
    try:
        commit = client.resolve_commit(ref.owner, ref.repo, ref.ref)
        download = client.download_skill(ref.owner, ref.repo, commit, ref.path)
    except GithubNotFound as exc:
        raise NotFound(f"GitHub skill not found: {exc}") from exc
    except GithubError as exc:
        raise UpstreamError(str(exc)) from exc
    except SkillRejected as exc:
        raise ValidationFailed(str(exc)) from exc

    fallback = ref.path.rsplit("/", 1)[-1] if ref.path else ref.repo
    normalized, meta = _wrap_rejections(
        lambda: (lambda n: (n, _read_meta(n, fallback)))(normalize_files(download.files))
    )
    spdx = _github_license(meta, normalized, client, ref)
    trust = trust_for_github(ref.owner, ref.repo)
    guard_source = f"{ref.owner}/{ref.repo}" + (f"/{ref.path}" if ref.path else "")
    return import_skill(
        db,
        company_id,
        env,
        normalized=normalized,
        meta=meta,
        slug=slug or fallback.lower(),
        source_kind=SkillSourceKind.GITHUB,
        source_ref=ref.canonical(commit),
        trust=trust,
        guard_source=guard_source,
        principal=principal,
        license_spdx=spdx,
    )


def install_skill(
    db: Session,
    ctx: CompanyContext,
    env: SkillsEnv,
    source_ref: str,
    *,
    slug: str | None = None,
) -> ImportResult:
    _mutating_principal_ok(db, ctx, "install skills")
    try:
        ref = parse_source_ref(source_ref)
    except BadSourceRef as exc:
        raise ValidationFailed(str(exc)) from exc
    if isinstance(ref, HermesRef):
        result = install_hermes(
            db, ctx.company_id, env, ref.target, slug=slug, principal=ctx.principal
        )
    else:
        result = install_github(db, ctx.company_id, env, ref, slug=slug, principal=ctx.principal)
    db.commit()
    return result


def author_skill(
    db: Session, ctx: CompanyContext, env: SkillsEnv, data: SkillAuthor
) -> ImportResult:
    """Company-authored skill (HR or a human). Scanned as ``community``: a flagged skill is
    quarantined, never active."""
    _mutating_principal_ok(db, ctx, "author skills")
    try:
        check_slug(data.slug)
    except SkillFormatError as exc:
        raise ValidationFailed(str(exc)) from exc
    existing = _get_by_slug(db, ctx.company_id, data.slug)
    if existing is not None and existing.source_kind != SkillSourceKind.COMPANY_AUTHORED:
        raise Conflict(f"The slug {data.slug!r} belongs to an installed skill; choose another")
    tags = normalize_tags(data.tags)
    version = _bump(existing.version) if existing is not None else "1.0.0"
    label = ctx.principal.label if ctx.principal.is_agent else "Company OS"
    frontmatter = {
        "name": data.slug,
        "description": " ".join(data.description.split()),
        "version": version,
        "author": label,
        "platforms": ["linux", "macos", "windows"],
        "metadata": {"hermes": {"tags": tags}},
    }
    skill_md = (
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, width=1000)
        + "---\n\n"
        + data.body_markdown.strip()
        + "\n"
    )
    entries = [SkillFile("SKILL.md", skill_md.encode("utf-8"))]
    entries += [SkillFile(f.path, f.content.encode("utf-8")) for f in data.files]

    def build():
        normalized = normalize_files(entries)
        return normalized, _read_meta(normalized, data.slug)

    normalized, meta = _wrap_rejections(build)
    meta.name = data.name
    meta.author = label
    result = import_skill(
        db,
        ctx.company_id,
        env,
        normalized=normalized,
        meta=meta,
        slug=data.slug,
        source_kind=SkillSourceKind.COMPANY_AUTHORED,
        source_ref=f"company:{data.slug}",
        trust=SkillTrust.COMMUNITY,
        guard_source="community",
        principal=ctx.principal,
        license_spdx=None,
        version_override=version,
    )
    db.commit()
    return result


# ---------------------------------------------------------------- seeding


def seed_company_library(
    db: Session, company_id: str, env: SkillsEnv, principal: Principal | None = None
) -> SeedResult:
    """Import every skill referenced by a built-in role template (idempotent) and equip agents
    that were created from a template but have no skills yet. Commits per skill so a failure
    midway keeps what was imported."""
    result = SeedResult()
    for slug in roles.seed_skill_slugs():
        try:
            seed_dir = roles.seed_skill_dir(slug)
            if seed_dir is not None:

                def build(path=seed_dir, name=slug):
                    normalized = normalize_dir(path)
                    return normalized, _read_meta(normalized, name)

                normalized, meta = _wrap_rejections(build)
                imported = import_skill(
                    db,
                    company_id,
                    env,
                    normalized=normalized,
                    meta=meta,
                    slug=slug,
                    source_kind=SkillSourceKind.COMPANY_AUTHORED,
                    source_ref=f"{COMPANY_SEED_PREFIX}{slug}",
                    trust=SkillTrust.BUILTIN,
                    guard_source="official",
                    principal=principal,
                    keep_archived=True,
                )
            else:
                if env.hermes.find(slug) is None:
                    result.missing.append(slug)
                    continue
                imported = install_hermes(
                    db, company_id, env, slug, slug=None, principal=principal, keep_archived=True
                )
            db.commit()
        except (Conflict, ValidationFailed, NotFound) as exc:
            db.rollback()
            logger.warning("seed skill %s skipped: %s", slug, exc.message)
            result.missing.append(slug)
            continue
        bucket = {"installed": result.installed, "updated": result.updated}.get(
            imported.outcome, result.unchanged
        )
        bucket.append(slug)
        if imported.quarantined:
            result.quarantined.append(slug)

    from app.services import role_templates  # local: avoids an import cycle at module load

    for agent in db.scalars(
        select(Agent).where(
            Agent.company_id == company_id,
            Agent.role_template.is_not(None),
            Agent.status.in_([AgentStatus.PROPOSED, AgentStatus.ACTIVE, AgentStatus.PAUSED]),
        )
    ):
        view = role_templates.find_template(db, company_id, agent.role_template or "")
        if view is None or has_any_skill(db, company_id, agent.id):
            continue
        added = assign_slugs_internal(
            db, company_id, agent, list(view.spec.default_skill_slugs), principal=principal
        )
        if added:
            result.agents_equipped.append(agent.id)
    audit.record(
        db,
        company_id=company_id,
        principal=principal,
        action="skill_library.seeded",
        target_type="company",
        target_id=company_id,
        summary=f"Seeded skill library: {len(result.installed)} installed, "
        f"{len(result.updated)} updated, {len(result.missing)} unavailable",
        data={
            "installed": result.installed,
            "updated": result.updated,
            "missing": result.missing,
            "quarantined": result.quarantined,
        },
    )
    events.emit(db, company_id, "skill_library.seeded", {"installed": len(result.installed)})
    db.commit()
    return result


def enqueue_seed(db: Session, ctx: CompanyContext) -> tuple[str | None, str]:
    """Queue the seeding job for the worker (one pending job per company). Owner/admin only."""
    require_human_admin(ctx, "seed the skill library")
    kind = "seed_skill_library"
    pending = db.scalar(
        select(Job).where(
            Job.company_id == ctx.company_id,
            Job.kind == kind,
            Job.status.in_([JobStatus.QUEUED, JobStatus.LEASED]),
        )
    )
    if pending is not None:
        return pending.id, "already_queued"
    job = jobs.enqueue(
        db,
        kind=kind,
        payload={"company_id": ctx.company_id},
        company_id=ctx.company_id,
        dedupe_key=f"{kind}:{ctx.company_id}:{uuid.uuid4().hex[:8]}",
    )
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="skill_library.seed_requested",
        target_type="company",
        target_id=ctx.company_id,
        summary="Requested seeding of the skill library",
        data={"job_id": job.id},
    )
    db.commit()
    return job.id, "queued"


def seed_now(db: Session, ctx: CompanyContext, env: SkillsEnv) -> SeedResult:
    require_human_admin(ctx, "seed the skill library")
    return seed_company_library(db, ctx.company_id, env, ctx.principal)


# ---------------------------------------------------------------- assignment


def has_any_skill(db: Session, company_id: str, agent_id: str) -> bool:
    return (
        db.scalar(
            select(func.count(AgentSkill.id)).where(
                AgentSkill.company_id == company_id, AgentSkill.agent_id == agent_id
            )
        )
        or 0
    ) > 0


def assign_slugs_internal(
    db: Session,
    company_id: str,
    agent: Agent,
    slugs: list[str],
    *,
    principal: Principal | None,
    strict: bool = False,
) -> list[str]:
    """Assign active skills by slug (no authorization, no commit). Unknown/inactive slugs are
    skipped unless ``strict``. Returns the slugs that were newly assigned."""
    skills = {
        s.slug: s
        for s in db.scalars(
            select(Skill).where(Skill.company_id == company_id, Skill.slug.in_(slugs))
        )
    }
    have = set(
        db.scalars(
            select(AgentSkill.skill_id).where(
                AgentSkill.company_id == company_id, AgentSkill.agent_id == agent.id
            )
        )
    )
    atype, aid = creator_of(principal)
    added: list[str] = []
    for slug in slugs:
        skill = skills.get(slug)
        if skill is None or skill.status != SkillStatus.ACTIVE:
            if strict:
                raise NotFound(f"Active skill not found: {slug}")
            continue
        if skill.id in have:
            continue
        db.add(
            AgentSkill(
                company_id=company_id,
                agent_id=agent.id,
                skill_id=skill.id,
                assigned_by_type=atype,
                assigned_by_id=aid,
            )
        )
        have.add(skill.id)
        added.append(slug)
    if added:
        db.flush()
        audit.record(
            db,
            company_id=company_id,
            principal=principal,
            action="agent.skills_changed",
            target_type="agent",
            target_id=agent.id,
            summary=f"Assigned skills to {agent.name}: {', '.join(added)}",
            data={"added": added, "removed": []},
        )
        events.emit(
            db,
            company_id,
            "agent.skills_changed",
            {"agent_id": agent.id, "added": added, "removed": []},
        )
    return added


def _target_agent(db: Session, ctx: CompanyContext, agent_id: str) -> Agent:
    agent = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.company_id == ctx.company_id))
    if agent is None:
        raise NotFound("Agent not found")
    return agent


def _authorize_assignment(db: Session, ctx: CompanyContext, agent_id: str) -> None:
    if ctx.principal.is_agent and ctx.principal.id == agent_id:
        raise Forbidden("Agents cannot assign or remove their own skills")
    _mutating_principal_ok(db, ctx, "assign skills")


def assign_skills(
    db: Session,
    ctx: CompanyContext,
    agent_id: str,
    *,
    skill_ids: list[str],
    slugs: list[str],
) -> list[AgentSkill]:
    _authorize_assignment(db, ctx, agent_id)
    agent = _target_agent(db, ctx, agent_id)
    if agent.status in (AgentStatus.RETIRED, AgentStatus.REJECTED):
        raise Conflict(f"A {agent.status} agent cannot be given skills")
    resolved: list[Skill] = []
    for ref in [*skill_ids, *slugs]:
        skill = resolve_skill_ref(db, ctx.company_id, ref)
        if skill.status != SkillStatus.ACTIVE:
            raise Conflict(
                f"Skill {skill.slug!r} is {skill.status}; only active skills can be assigned",
                details={"slug": skill.slug, "status": skill.status},
            )
        if skill not in resolved:
            resolved.append(skill)
    assign_slugs_internal(
        db, ctx.company_id, agent, [s.slug for s in resolved], principal=ctx.principal, strict=True
    )
    from app.services import hiring  # local: hiring depends on this module

    hiring.resolve_skill_gaps(db, ctx.company_id, principal=ctx.principal)
    db.commit()
    return list_agent_skill_rows(db, ctx.company_id, agent.id)


def unassign_skill(db: Session, ctx: CompanyContext, agent_id: str, skill_ref: str) -> None:
    _authorize_assignment(db, ctx, agent_id)
    agent = _target_agent(db, ctx, agent_id)
    skill = resolve_skill_ref(db, ctx.company_id, skill_ref)
    row = db.scalar(
        select(AgentSkill).where(
            AgentSkill.company_id == ctx.company_id,
            AgentSkill.agent_id == agent.id,
            AgentSkill.skill_id == skill.id,
        )
    )
    if row is None:
        raise NotFound("That skill is not assigned to this agent")
    db.delete(row)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="agent.skills_changed",
        target_type="agent",
        target_id=agent.id,
        summary=f"Removed skill {skill.slug} from {agent.name}",
        data={"added": [], "removed": [skill.slug]},
    )
    events.emit(
        db,
        ctx.company_id,
        "agent.skills_changed",
        {"agent_id": agent.id, "added": [], "removed": [skill.slug]},
    )
    db.commit()


def list_agent_skill_rows(db: Session, company_id: str, agent_id: str) -> list[AgentSkill]:
    rows = db.execute(
        select(AgentSkill, Skill)
        .join(Skill, Skill.id == AgentSkill.skill_id)
        .where(AgentSkill.company_id == company_id, AgentSkill.agent_id == agent_id)
        .order_by(Skill.slug)
    ).all()
    out = []
    for link, skill in rows:
        link.skill = skill  # type: ignore[attr-defined]  (serialization convenience)
        out.append(link)
    return out


def list_agent_skills(db: Session, ctx: CompanyContext, agent_id: str) -> list[AgentSkill]:
    _target_agent(db, ctx, agent_id)
    return list_agent_skill_rows(db, ctx.company_id, agent_id)


def capability_map(
    db: Session, company_id: str, agent_ids: list[str] | None = None
) -> dict[str, tuple[set[str], set[str]]]:
    """agent id -> (slugs, capability tags) from the agent's ACTIVE assigned skills. A skill
    contributes its tags, its slug and its category."""
    stmt = (
        select(AgentSkill.agent_id, Skill.slug, Skill.tags, Skill.category)
        .join(Skill, Skill.id == AgentSkill.skill_id)
        .where(AgentSkill.company_id == company_id, Skill.status == SkillStatus.ACTIVE)
    )
    if agent_ids is not None:
        stmt = stmt.where(AgentSkill.agent_id.in_(agent_ids))
    out: dict[str, tuple[set[str], set[str]]] = {}
    for agent_id, slug, tags, category in db.execute(stmt):
        slugs, caps = out.setdefault(agent_id, (set(), set()))
        slugs.add(slug)
        caps.update(tags or [])
        caps.add(slug)
        if category:
            caps.add(str(category).lower())
    return out


# ---------------------------------------------------------------- owner queries


def list_skills(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    status: str | None = None,
    source_kind: str | None = None,
    tag: str | None = None,
    q: str | None = None,
):
    stmt = select(Skill).where(Skill.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(Skill.status == status)
    if source_kind:
        stmt = stmt.where(Skill.source_kind == source_kind)
    if tag:
        stmt = stmt.where(cast(Skill.tags, String).like(f'%"{tag.strip().lower()}"%'))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Skill.slug).like(like),
                func.lower(Skill.name).like(like),
                func.lower(Skill.description).like(like),
            )
        )
    return paginate(db, stmt, Skill, limit=limit, cursor=cursor, descending=False)


def skill_detail(db: Session, ctx: CompanyContext, env: SkillsEnv, skill_id: str) -> dict[str, Any]:
    skill = get_skill(db, ctx, skill_id)
    files: list[dict[str, Any]] = []
    frontmatter: dict[str, Any] = {}
    if env.blobs.exists(skill.blob_sha256):
        files = env.blobs.list_files(skill.blob_sha256)
        try:
            text, _ = env.blobs.read_text(skill.blob_sha256, "SKILL.md")
            frontmatter = parse_skill_md(text, fallback_name=skill.slug).frontmatter
        except (SkillFormatError, FileNotFoundError, UnicodeDecodeError, SkillRejected):
            frontmatter = {}
    agents = db.execute(
        select(Agent.id, Agent.name, Agent.role_title, Agent.status)
        .join(AgentSkill, AgentSkill.agent_id == Agent.id)
        .where(AgentSkill.company_id == ctx.company_id, AgentSkill.skill_id == skill.id)
        .order_by(Agent.name)
    ).all()
    return {
        "skill": skill,
        "scan_report": skill.scan_report,
        "frontmatter": _json_safe(frontmatter),
        "files": files,
        "assigned_agents": [
            {"id": a, "name": n, "role_title": r, "status": s} for a, n, r, s in agents
        ],
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def read_skill_file(
    db: Session, ctx: CompanyContext, env: SkillsEnv, skill_id: str, path: str
) -> dict[str, Any]:
    skill = get_skill(db, ctx, skill_id)
    if not env.blobs.exists(skill.blob_sha256):
        raise NotFound("Skill content is missing on disk")
    try:
        content, truncated = env.blobs.read_text(skill.blob_sha256, path)
    except SkillRejected as exc:
        raise BadRequest(str(exc)) from exc
    except FileNotFoundError as exc:
        raise NotFound("File not found in this skill") from exc
    except UnicodeDecodeError as exc:
        raise Conflict("That file is not text and cannot be previewed") from exc
    return {"path": path, "content": content, "truncated": truncated}


def archive_skill(db: Session, ctx: CompanyContext, skill_id: str, note: str | None) -> Skill:
    require_human_admin(ctx, "archive skills")
    skill = get_skill(db, ctx, skill_id)
    if skill.status == SkillStatus.ARCHIVED:
        raise InvalidTransition("The skill is already archived", details={"from": skill.status})
    previous = skill.status
    skill.status = SkillStatus.ARCHIVED
    skill.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="skill.archived",
        target_type="skill",
        target_id=skill.id,
        summary=f"Archived skill {skill.slug}",
        data={"from": previous, "note": note},
    )
    events.emit(db, ctx.company_id, "skill.archived", {"skill_id": skill.id, "slug": skill.slug})
    db.commit()
    return skill


def unquarantine_skill(db: Session, ctx: CompanyContext, skill_id: str, note: str | None) -> Skill:
    """Explicit human override of the scanner (audited with the verdict that was overridden)."""
    require_human_admin(ctx, "release quarantined skills")
    skill = get_skill(db, ctx, skill_id)
    if skill.status != SkillStatus.QUARANTINED:
        raise InvalidTransition(
            f"Only quarantined skills can be released (this one is {skill.status})",
            details={"from": skill.status},
        )
    skill.status = SkillStatus.ACTIVE
    skill.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="skill.unquarantined",
        target_type="skill",
        target_id=skill.id,
        summary=f"Released skill {skill.slug} from quarantine (human override of a "
        f"{skill.scan_verdict} verdict)",
        data={
            "scan_verdict": skill.scan_verdict,
            "trust": skill.trust,
            "blob_sha256": skill.blob_sha256,
            "note": note,
        },
    )
    events.emit(db, ctx.company_id, "skill.updated", {"skill_id": skill.id, "slug": skill.slug})
    db.commit()
    return skill


# ---------------------------------------------------------------- catalog


def catalog(
    db: Session,
    ctx: CompanyContext,
    env: SkillsEnv,
    *,
    q: str | None,
    source: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Search the library, the Hermes checkout and (``source=github``) GitHub."""
    if ctx.principal.is_agent:
        hr_access.require_hr_or_founder(db, ctx, "search the skill catalog")
    source = (source or "all").lower()
    if source not in ("all", "library", "hermes", "github"):
        raise ValidationFailed("source must be one of: all, library, hermes, github")
    try:
        offset = int(cursor) if cursor else 0
        if offset < 0:
            raise ValueError
    except ValueError as exc:
        raise BadRequest("Invalid cursor") from exc
    words = [w for w in (q or "").lower().split() if w]

    installed: dict[str, Skill] = {
        s.slug: s for s in db.scalars(select(Skill).where(Skill.company_id == ctx.company_id))
    }
    items: list[dict[str, Any]] = []

    if source in ("all", "library"):
        for s in sorted(installed.values(), key=lambda s: s.slug):
            hay = " ".join([s.slug, s.name, s.description, *s.tags]).lower()
            if all(w in hay for w in words):
                items.append(
                    {
                        "slug": s.slug,
                        "name": s.name,
                        "description": s.description,
                        "tags": list(s.tags),
                        "license": s.license,
                        "source": "library",
                        "source_kind": s.source_kind,
                        "source_ref": s.source_ref,
                        "item_type": "skill",
                        "installed": True,
                        "skill_id": s.id,
                        "status": s.status,
                    }
                )
    if source in ("all", "hermes"):
        commit = env.hermes.commit()
        for h in env.hermes.search(q or ""):
            have = installed.get(h.slug)
            if source == "all" and have is not None and have.source_kind.startswith("hermes"):
                continue  # already listed as a library entry
            items.append(
                {
                    "slug": h.slug,
                    "name": h.meta.name,
                    "description": h.meta.description,
                    "tags": list(h.meta.tags),
                    "license": h.meta.license,
                    "source": "hermes",
                    "source_kind": h.source_kind,
                    "source_ref": h.source_ref(commit),
                    "item_type": "skill",
                    "installed": have is not None and have.source_kind.startswith("hermes"),
                    "skill_id": have.id if have is not None else None,
                    "status": have.status if have is not None else None,
                }
            )
    if source == "github":
        items.extend(_github_catalog(env, q or "", installed))
    page = items[offset : offset + limit]
    next_cursor = str(offset + limit) if offset + limit < len(items) else None
    return page, next_cursor


def _github_catalog(env: SkillsEnv, q: str, installed: dict[str, Skill]) -> list[dict[str, Any]]:
    client = env.github()
    out: list[dict[str, Any]] = []
    installed_refs = {source_identity(s.source_ref): s for s in installed.values()}
    text = q.strip()
    try:
        if text.count("/") == 1 and " " not in text:  # owner/repo: list its skills
            owner, repo = text.split("/")
            commit = client.resolve_commit(owner, repo, None)
            lic = client.repo_license(owner, repo)
            for path in client.list_skill_paths(owner, repo, commit):
                ref = GithubRef(owner, repo, None, path)
                have = installed_refs.get(source_identity(ref.canonical(commit)))
                out.append(
                    {
                        "slug": (path.rsplit("/", 1)[-1] if path else repo).lower(),
                        "name": path.rsplit("/", 1)[-1] if path else repo,
                        "description": f"Skill in {owner}/{repo}",
                        "tags": [],
                        "license": lic,
                        "source": "github",
                        "source_kind": SkillSourceKind.GITHUB.value,
                        "source_ref": ref.canonical(commit),
                        "item_type": "skill",
                        "installed": have is not None,
                        "skill_id": have.id if have else None,
                        "status": have.status if have else None,
                    }
                )
            return out
        for hit in client.search_repositories(text):
            out.append(
                {
                    "slug": hit.full_name,
                    "name": hit.full_name,
                    "description": hit.description or "",
                    "tags": [],
                    "license": hit.license,
                    "source": "github",
                    "source_kind": SkillSourceKind.GITHUB.value,
                    "source_ref": f"github:{hit.full_name}",
                    "item_type": "repository",
                    "installed": False,
                    "stars": hit.stars,
                }
            )
        if client.has_token:
            for code in client.search_skill_files(text):
                ref = GithubRef(*code.repo.split("/", 1), None, code.skill_dir)
                out.append(
                    {
                        "slug": (
                            code.skill_dir.rsplit("/", 1)[-1] or code.repo.split("/")[1]
                        ).lower(),
                        "name": code.skill_dir or code.repo,
                        "description": code.description or "",
                        "tags": [],
                        "license": None,
                        "source": "github",
                        "source_kind": SkillSourceKind.GITHUB.value,
                        "source_ref": f"github:{ref.owner}/{ref.repo}:{ref.path}",
                        "item_type": "skill",
                        "installed": False,
                    }
                )
    except GithubNotFound as exc:
        raise NotFound(f"GitHub: {exc}") from exc
    except GithubError as exc:
        raise UpstreamError(str(exc)) from exc
    return out


# ---------------------------------------------------------------- materialization


def materialize_for_agent(
    db: Session,
    company_id: str,
    agent_id: str,
    hermes_home: Path,
    *,
    blobs: BlobStore | None = None,
) -> MaterializeResult:
    """Make ``<hermes_home>/skills/`` contain EXACTLY the agent's assigned ACTIVE skills.

    Each skill dir is copied from the verified blob into a temp dir and swapped in atomically;
    unchanged dirs are left alone; anything else under ``skills/`` (stale, quarantined,
    archived, unassigned, leftovers) is removed. A blob that is missing or fails its hash
    check is skipped (reported), never copied.
    """
    blobs = blobs or BlobStore(get_settings().skills_blobs_dir)
    skills_dir = Path(hermes_home) / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    rows = db.execute(
        select(Skill)
        .join(AgentSkill, AgentSkill.skill_id == Skill.id)
        .where(
            AgentSkill.company_id == company_id,
            AgentSkill.agent_id == agent_id,
            Skill.company_id == company_id,
            Skill.status == SkillStatus.ACTIVE,
        )
        .order_by(Skill.slug)
    ).scalars()
    result = MaterializeResult()
    wanted: set[str] = set()
    for skill in rows:
        try:
            check_slug(skill.slug)
        except SkillFormatError:  # pragma: no cover - slugs are validated on import
            result.skipped.append({"slug": skill.slug, "reason": "invalid_slug"})
            continue
        if not blobs.exists(skill.blob_sha256):
            result.skipped.append({"slug": skill.slug, "reason": "blob_missing"})
            continue
        if not blobs.verify(skill.blob_sha256):
            result.skipped.append({"slug": skill.slug, "reason": "blob_corrupt"})
            continue
        wanted.add(skill.slug)
        dest = skills_dir / skill.slug
        if hash_dir(dest) == skill.blob_sha256 and not dest.is_symlink():
            result.unchanged.append(skill.slug)
            continue
        tmp = skills_dir / f".tmp-{skill.slug}-{uuid.uuid4().hex[:8]}"
        shutil.copytree(blobs.path(skill.blob_sha256), tmp, symlinks=False)
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        elif dest.exists():
            old = skills_dir / f".old-{skill.slug}-{uuid.uuid4().hex[:8]}"
            os.rename(dest, old)
            os.rename(tmp, dest)
            shutil.rmtree(old, ignore_errors=True)
            result.written.append(skill.slug)
            continue
        os.rename(tmp, dest)
        result.written.append(skill.slug)
    for entry in sorted(skills_dir.iterdir()):
        if entry.name in wanted:
            continue
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        else:
            shutil.rmtree(entry, ignore_errors=True)
        result.removed.append(entry.name)
    return result
