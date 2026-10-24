"""Free-subdomain registries that take a registration file by pull request, and free-domain
providers with a dashboard.

Pluggable: a registry is data (repo, naming rules, how its file looks) plus two renderers. The one
concrete registry is **is-a.dev** (https://github.com/is-a-dev/register), taken from its own
documentation (docs.is-a.dev: ``domains/<name>.json`` with ``owner.username`` and ``records``). The
rules are the registry's, not ours, and they change: the preview always carries the warnings below
and the link to the rules. Review is by humans; nothing here promises acceptance.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


@dataclass(frozen=True)
class RegistryDef:
    id: str
    name: str
    suffix: str
    repo: str
    rules_url: str | None
    eligibility: tuple[str, ...]
    review: str  # "human" | "automatic"
    name_min_length: int
    name_max_length: int
    name_pattern: str
    warnings: tuple[str, ...] = ()
    reserved: frozenset[str] = field(default_factory=frozenset)
    file_dir: str = "domains"

    def label_problem(self, label: str) -> str | None:
        if not (self.name_min_length <= len(label) <= self.name_max_length):
            return f"The name must be {self.name_min_length}-{self.name_max_length} characters long"
        if not re.fullmatch(self.name_pattern, label):
            return "Use lowercase letters, digits and single dashes (not at the start or end)"
        if label in self.reserved:
            return "That name is reserved"
        if self.suffix in label:
            return f"The name must not contain {self.suffix}"
        return None

    def fqdn(self, label: str) -> str:
        return f"{label}.{self.suffix}"

    def file_path(self, label: str) -> str:
        return f"{self.file_dir}/{label}.json"

    def render_file(self, github_login: str, target: str) -> str:
        """The registration file, byte-for-byte what will be committed."""
        body = {"owner": {"username": github_login}, "records": {"CNAME": target}}
        return json.dumps(body, indent=2) + "\n"

    def pr_title(self, label: str) -> str:
        return f"Register {self.fqdn(label)}"

    def pr_body(self, label: str, github_login: str, target: str, site_url: str | None) -> str:
        lines = [
            f"Registers `{self.fqdn(label)}` for @{github_login}.",
            "",
            f"- Record: `CNAME {target}`",
        ]
        if site_url:
            lines.append(f"- Website: {site_url}")
        lines += [
            "",
            "Opened from the owner's own GitHub account after the owner reviewed this exact "
            "content in Foundry AI OS.",
        ]
        return "\n".join(lines) + "\n"


IS_A_DEV = RegistryDef(
    id="is-a.dev",
    name="is-a.dev",
    suffix="is-a.dev",
    repo="is-a-dev/register",
    rules_url="https://docs.is-a.dev",
    eligibility=(
        "Free subdomain of is-a.dev, registered by pull request to is-a-dev/register.",
        "The site must not be blank: a personal portfolio/blog or a non-commercial software "
        "project (as of the registry's quick start; commercial use is against its terms).",
        "Requests are reviewed by humans. The pull request template asks for a website preview "
        "link and a screenshot.",
        "The registry's README says not to use AI to generate the request. Review the file "
        "yourself and submit it as your own.",
    ),
    review="human",
    name_min_length=1,
    name_max_length=63,
    name_pattern=r"[a-z0-9]+(-[a-z0-9]+)*",
    warnings=(
        "is-a.dev is for personal and non-commercial projects. A company site may be rejected "
        "or removed.",
        "A human reviews every request and may ask for changes; merging is not guaranteed.",
        "The pull request is published under your GitHub account and accepts the registry's terms.",
        "Add the website preview link and screenshot the registry asks for to the pull request "
        "after it opens.",
    ),
    reserved=frozenset({"www", "mail", "ns1", "ns2", "docs", "api", "admin", "register"}),
)

REGISTRIES: dict[str, RegistryDef] = {IS_A_DEV.id: IS_A_DEV}


def get_registry(registry_id: str) -> RegistryDef | None:
    return REGISTRIES.get(registry_id)


def valid_hostname(value: str) -> bool:
    return bool(HOSTNAME.match(value.strip().lower()))


@dataclass(frozen=True)
class FreeProviderDef:
    id: str
    name: str
    dashboard_url: str
    suffixes: tuple[str, ...]
    eligibility: tuple[str, ...]
    steps: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "dashboard_url": self.dashboard_url,
            "suffixes": list(self.suffixes),
            "eligibility": list(self.eligibility),
            "steps": list(self.steps),
        }


DIGITALPLAT = FreeProviderDef(
    id="digitalplat",
    name="DigitalPlat FreeDomain",
    dashboard_url="https://dash.domain.digitalplat.org/",
    suffixes=("dpdns.org", "us.kg", "qzz.io", "xx.kg", "qd.je"),
    eligibility=(
        "Free names under the suffixes listed; you register in the provider's dashboard.",
        "The provider decides who qualifies and can change or remove names; read its terms "
        "(commercial use is not documented in what Foundry AI OS could verify).",
    ),
    steps=(
        "Open the dashboard and create your own account (Foundry AI OS never signs up for you).",
        "Search for the name you want and register it. Complete any email or identity "
        "verification the provider asks for.",
        "Set the domain's nameservers to the ones Cloudflare shows after you add the domain as "
        "a zone to your Cloudflare account (or add the DNS records below at the provider).",
        "Come back here and press Verify so Foundry AI OS can check DNS and wire the tunnel.",
    ),
)
FREE_PROVIDERS: dict[str, FreeProviderDef] = {DIGITALPLAT.id: DIGITALPLAT}
