"""Built-in role templates: loading, validation, seed-skill coverage, custom templates."""

from __future__ import annotations

import pytest

from app.core.config import REPO_DIR
from app.roles import loader
from app.skills.frontmatter import normalize_tags, parse_skill_md
from tests.helpers import api

EXPECTED = {
    "founder-ceo": "founder",
    "hr-partner": "hr",
    "software-engineer": "engineering",
    "product-designer": "design",
    "growth-marketer": "marketing",
    "account-executive": "sales",
    "customer-success-manager": "customer_success",
    "research-analyst": "research",
    "qa-engineer": "qa",
    "devops-engineer": "operations",
    "finance-analyst": "finance",
    "project-lead": "generic",
    "marketing-lead": "marketing",
    "engineering-lead": "engineering",
    "market-researcher": "research",  # M16
}
RUNTIME = REPO_DIR / "runtime"


def test_all_builtin_templates_load_and_cover_every_family():
    templates = loader.builtin_templates()
    assert {slug: t.family.value for slug, t in templates.items()} == EXPECTED
    for t in templates.values():
        assert t.instructions.strip() and t.description.strip()
        assert set(t.default_toolsets) <= loader.ALLOWED_TOOLSETS
        assert "company" in t.default_toolsets
        assert t.default_skill_slugs and t.required_skill_tags


def test_company_authored_seed_skills_exist_and_are_wellformed():
    assert loader.company_seed_slugs() == [
        "hiring-playbook",
        "market-radar-playbook",  # M16
        "sales-outreach-playbook",
        "support-triage",
        "writing-company-skills",
    ]
    for slug in loader.company_seed_slugs():
        text = (loader.seed_skill_dir(slug) / "SKILL.md").read_text()
        meta = parse_skill_md(text, fallback_name="?")
        assert meta.name == slug
        assert meta.license == "MIT" and meta.author == "Company OS"
        assert meta.tags and meta.description
        assert len(text.splitlines()) < 150  # concise


@pytest.mark.skipif(not (RUNTIME / "skills").is_dir(), reason="runtime checkout not present")
def test_every_seed_skill_slug_exists_in_the_runtime_checkout():
    from app.skills.hermes import HermesCatalog

    catalog = HermesCatalog(RUNTIME)
    company = set(loader.company_seed_slugs())
    missing = [s for s in loader.seed_skill_slugs() if s not in company and not catalog.find(s)]
    assert missing == []


@pytest.mark.skipif(not (RUNTIME / "skills").is_dir(), reason="runtime checkout not present")
def test_required_tags_are_covered_by_the_default_skills_tags():
    """A template's 'what this role is good at' must be real: some default skill carries each tag."""
    from app.skills.hermes import HermesCatalog

    catalog = HermesCatalog(RUNTIME)
    for t in loader.builtin_templates().values():
        tags: set[str] = set()
        for slug in t.default_skill_slugs:
            seed = loader.seed_skill_dir(slug)
            if seed is not None:
                meta = parse_skill_md((seed / "SKILL.md").read_text(), fallback_name=slug)
            else:
                meta = catalog.find(slug).meta
            tags |= set(meta.tags) | {slug}
        assert set(t.required_skill_tags) <= tags, (t.slug, set(t.required_skill_tags) - tags)


def test_validator_rejects_bad_templates(tmp_path):
    good = {
        "slug": "ok-role",
        "family": "generic",
        "title": "Ok",
        "default_toolsets": ["company"],
    }
    assert loader.parse_template(good).slug == "ok-role"
    for bad in (
        {**good, "slug": "Bad Slug"},
        {**good, "family": "wizard"},
        {**good, "default_toolsets": ["company", "browser"]},
        {**good, "default_skill_slugs": ["a", "a"]},
        {**good, "required_skill_tags": ["Has Space"]},
        {**good, "unknown_field": 1},
        {**good, "default_kind": "emperor"},
    ):
        with pytest.raises(loader.TemplateError):
            loader.parse_template(bad)


def test_file_name_must_match_slug(tmp_path):
    path = tmp_path / "other.yaml"
    path.write_text("slug: ok-role\nfamily: generic\ntitle: Ok\n")
    with pytest.raises(loader.TemplateError, match="file name"):
        loader.load_template_file(path)


def test_tag_normalization():
    assert normalize_tags(["Code Analysis", "CI", "ci", " Pull_Requests "]) == [
        "code-analysis",
        "ci",
        "pull-requests",
    ]


# ------------------------------------------------------------------ API


def test_role_template_listing_and_custom_templates(client, company):
    cid = company["id"]
    items = client.get(api(cid, "/role-templates")).json()["items"]
    assert {i["slug"] for i in items} == set(EXPECTED) and all(i["builtin"] for i in items)

    body = {
        "slug": "data-steward",
        "family": "operations",
        "title": "Data Steward",
        "instructions": "Keep data clean.",
        "default_toolsets": ["company", "file", "terminal"],
    }
    created = client.post(api(cid, "/role-templates"), json=body)
    assert created.status_code == 201, created.text
    assert created.json()["builtin"] is False and created.json()["id"]
    assert client.post(api(cid, "/role-templates"), json=body).status_code == 409
    clash = client.post(api(cid, "/role-templates"), json={**body, "slug": "qa-engineer"})
    assert clash.status_code == 409  # built-in slugs are reserved
    unknown_skill = {**body, "slug": "other", "default_skill_slugs": ["no-such-skill"]}
    assert client.post(api(cid, "/role-templates"), json=unknown_skill).status_code == 422
    assert "data-steward" in {
        i["slug"] for i in client.get(api(cid, "/role-templates")).json()["items"]
    }
