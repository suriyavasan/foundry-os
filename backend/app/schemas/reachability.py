"""M8 schemas: reachability, tunnel, domains, hosted UI, e-mail identity, connected accounts.

Names and shapes follow the web contract (``web/src/api/types.ts`` "Milestone 8"); a few fields are
added (marked) and none is removed. No response carries a secret.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, RootModel

from app.schemas.common import ApiModel, RequestModel, Timestamp

# ------------------------------------------------------------------ device reachability

ReachabilityModeLiteral = Literal["local", "lan", "public"]
TunnelProviderKindLiteral = Literal["cloudflare_quick", "cloudflare_named", "tailscale_funnel"]
TunnelStateLiteral = Literal["stopped", "starting", "running", "error"]
StatusLiteral = Literal["unknown", "pending", "ok", "error"]


class LanAddress(ApiModel):
    address: str
    family: Literal["ipv4", "ipv6"]
    interface: str | None
    url: str


class LanStatus(ApiModel):
    # True when the server process is actually bound to all interfaces.
    enabled: bool
    mdns_name: str | None
    mdns_url: str | None
    addresses: list[LanAddress]
    # Added: the owner's persisted preference, the address the process is bound to, and whether
    # the two differ (the server must be restarted -- e.g. `foundryos up` -- to rebind).
    requested: bool = False
    bind_host: str = "127.0.0.1"
    restart_required: bool = False


class TunnelBinary(ApiModel):
    name: str
    found: bool
    version: str | None
    path: str | None
    install_instructions: list[str]
    download_offered: bool
    download_url: str | None
    download_sha256: str | None
    download_size_bytes: int | None


class TunnelProviderOption(ApiModel):
    kind: TunnelProviderKindLiteral
    requires_account: str | None
    available: bool
    unavailable_reason: str | None


class TunnelStatus(ApiModel):
    provider: TunnelProviderKindLiteral | None
    state: TunnelStateLiteral
    url: str | None
    hostname: str | None
    last_health_check_at: Timestamp | None
    healthy: bool | None
    restarts: int
    started_at: Timestamp | None
    error: str | None
    binary: TunnelBinary | None
    providers: list[TunnelProviderOption]
    # Added: a quick tunnel gets a new URL on every start; the last URL seen and when it appeared
    # (kept across restarts, so a stopped tunnel still shows what the address was).
    ephemeral: bool = False
    last_url: str | None = None
    url_changed_at: Timestamp | None = None


class TunnelConfig(RequestModel):
    provider: TunnelProviderKindLiteral
    hostname: str | None = Field(default=None, max_length=253)


class TunnelInstallRequest(RequestModel):
    confirm: bool = False
    # What the owner was shown; when given it must equal the published checksum.
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


class TunnelInstallPlan(ApiModel):
    """Added: what a confirmed install would download (metadata lookup only; nothing downloaded)."""

    version: str
    asset: str
    url: str
    sha256: str
    size_bytes: int | None = None
    checksum_sources: list[str] = []


class TunnelLogs(ApiModel):
    """Added: the last output lines of the tunnel process (secrets masked)."""

    lines: list[str]


class LanRequest(RequestModel):
    enabled: bool


class DomainSummary(ApiModel):
    domain: str | None
    dns_status: StatusLiteral
    tls_status: StatusLiteral


class ReachabilityStatus(ApiModel):
    mode: ReachabilityModeLiteral
    device_name: str
    hostname: str
    auth_mode: str
    local_urls: list[str]
    lan: LanStatus
    tunnel: TunnelStatus
    active_url: str | None
    public_exposure_blocked_reason: str | None
    public_exposure_blocked_code: str | None = None
    domain: DomainSummary | None = None
    # Added: advice that does not block (e.g. "two_factor_recommended").
    public_exposure_advice: str | None = None
    two_factor_enabled: bool = False


# ------------------------------------------------------------------ domains

DomainPathLiteral = Literal["github_registry", "free_provider", "paid_registrar"]
DomainStatusLiteral = Literal[
    "none", "awaiting_approval", "pr_open", "awaiting_founder", "pending_dns", "active", "error"
]
DomainRegistrationStatusLiteral = Literal[
    "awaiting_approval",
    "submitted",
    "pr_open",
    "merged",
    "awaiting_founder",
    "purchased",
    "rejected",
    "failed",
    "cancelled",
]


class DnsRecord(ApiModel):
    type: Literal["A", "AAAA", "CNAME", "TXT", "NS", "MX"]
    name: str
    value: str
    ttl: int | None = None
    status: Literal["ok", "missing", "mismatch", "pending"] | None = None


class DomainRegistration(ApiModel):
    id: str
    company_id: str
    path: DomainPathLiteral
    domain: str
    registry_id: str | None
    provider_id: str | None
    registrar: str | None
    status: DomainRegistrationStatusLiteral
    approval_id: str | None
    pr_url: str | None
    price_micros: int | None
    error: str | None
    created_at: Timestamp
    updated_at: Timestamp
    # Added: how the amount was obtained ("estimated" = the registrar's quote, "verified" = the
    # amount the registrar reports it charged) and the ceiling the approval is bound to.
    cost_kind: Literal["verified", "estimated", "unknown"] | None = None
    max_price_micros: int | None = None


class DomainPlan(ApiModel):
    domain: str | None
    path: DomainPathLiteral | None
    status: DomainStatusLiteral
    dns_status: StatusLiteral
    tls_status: StatusLiteral
    dns_records: list[DnsRecord]
    steps: list[str]
    registration: DomainRegistration | None
    last_checked_at: Timestamp | None
    error: str | None


class DomainRegistry(ApiModel):
    id: str
    name: str
    suffix: str
    repo: str
    rules_url: str | None
    eligibility: list[str]
    review: Literal["human", "automatic"]
    name_min_length: int
    name_max_length: int
    name_pattern: str
    warnings: list[str] = []  # added


class FreeDomainProvider(ApiModel):
    id: str
    name: str
    dashboard_url: str
    suffixes: list[str]
    eligibility: list[str]
    steps: list[str]


class DomainAvailability(ApiModel):
    domain: str
    available: bool
    registrar: str
    price_micros: int | None
    renewal_price_micros: int | None
    cost_kind: Literal["verified", "estimated", "unknown"] | None = None


class DomainRegistrationPreviewRequest(RequestModel):
    registry_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=63)
    # Added: where the CNAME points. Default: the hosted UI's Vercel target or a named tunnel.
    target: str | None = Field(default=None, max_length=253)


class DomainRegistrationPreview(ApiModel):
    fqdn: str
    repo: str
    branch: str | None
    file_path: str
    file_content: str
    pr_title: str
    pr_body: str
    publishes_as: str | None
    eligible: bool
    ineligible_reason: str | None
    warnings: list[str]
    target: str | None = None  # added: the CNAME target in the file


class DomainRegistrationCreateRegistry(RequestModel):
    path: Literal["github_registry"]
    registry_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=63)
    target: str | None = Field(default=None, max_length=253)


class DomainRegistrationCreateFree(RequestModel):
    path: Literal["free_provider"]
    provider_id: str = Field(min_length=1, max_length=40)
    domain: str = Field(min_length=3, max_length=253)


class DomainRegistrationCreatePaid(RequestModel):
    path: Literal["paid_registrar"]
    registrar: str = Field(min_length=1, max_length=40)
    domain: str = Field(min_length=3, max_length=253)
    years: int = Field(ge=1, le=10)
    # The most the approval allows the purchase to cost. None = the current quote.
    max_price_micros: int | None = Field(default=None, ge=0)


class DomainRegistrationCreate(
    RootModel[
        Annotated[
            DomainRegistrationCreateRegistry
            | DomainRegistrationCreateFree
            | DomainRegistrationCreatePaid,
            Field(discriminator="path"),
        ]
    ]
):
    pass


class DnsApplyRequest(RequestModel):
    """Added: owner-triggered DNS through the connected Cloudflare account."""

    changes: list[dict] = Field(min_length=1, max_length=20)


class DnsPlanStep(ApiModel):
    op: Literal["create", "update", "noop"]
    type: str
    name: str
    value: str
    ttl: int | None = None
    previous: str | None = None


class DnsPlanResult(ApiModel):
    zone: str
    provider: str
    steps: list[DnsPlanStep]
    applied: bool = False


# ------------------------------------------------------------------ hosted UI

HostedUiStatusLiteral = Literal["none", "deploying", "ready", "error"]


class HostedUiDeployment(ApiModel):
    status: HostedUiStatusLiteral
    provider: Literal["vercel"]
    project_name: str | None
    url: str | None
    proxy_target: str | None
    last_deployed_at: Timestamp | None
    error: str | None
    approval_id: str | None = None


# ------------------------------------------------------------------ e-mail identity

EmailProviderKindLiteral = Literal["agentmail", "cloudflare_resend", "gmail", "outlook"]


class EmailIdentity(ApiModel):
    provider: EmailProviderKindLiteral | None
    status: Literal["not_connected", "connected", "error"]
    domain: str | None
    error: str | None
    ai_disclosure_footer: bool


class EmailIdentityUpdate(RequestModel):
    provider: EmailProviderKindLiteral | None = None
    ai_disclosure_footer: bool | None = None


class Mailbox(ApiModel):
    id: str
    company_id: str
    address: str
    display_name: str | None
    owner_agent_id: str | None
    provider: str
    status: Literal["active", "pending", "error", "disabled"]
    inbound_count: int
    outbound_count: int
    last_inbound_at: Timestamp | None
    last_outbound_at: Timestamp | None
    created_at: Timestamp
    error: str | None = None  # added


class MailboxCreate(RequestModel):
    local_part: str = Field(min_length=1, max_length=64)
    owner_agent_id: str
    display_name: str | None = Field(default=None, max_length=120)


# ------------------------------------------------------------------ connected accounts


class ConnectedAccount(ApiModel):
    id: str
    company_id: str
    provider: str
    label: str
    credential_id: str | None
    scopes: list[str]
    status: Literal["connected", "invalid", "revoked"]
    last_checked_at: Timestamp | None
    metadata: dict[str, str]
    created_at: Timestamp
    last_error: str | None = None  # added


class ConnectedAccountCreate(RequestModel):
    provider: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=120)
    # The main secret, stored first through POST /credentials. Providers with no secret
    # (Tailscale) ignore it.
    credential_id: str | None = None
    extra_credential_ids: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)


class ProviderCheck(ApiModel):
    ok: bool
    message: str | None
    checked_at: Timestamp | None
    account_label: str | None
    scopes: list[str]


class OAuthStartRequest(RequestModel):
    provider: str


class OAuthStart(ApiModel):
    authorize_url: str


class ProviderInfo(ApiModel):
    """Added: what can be connected, which fields it needs and what it enables."""

    id: str
    label: str
    category: str
    needs_secret: bool
    secret_fields: list[str]
    optional_secret_fields: list[str]
    metadata_fields: list[str]
    required_metadata: list[str]
    agent_actions: list[str]
    oauth: bool
    help: str


# ------------------------------------------------------------------ checklist


class ChecklistStep(ApiModel):
    id: str
    title: str
    description: str
    after: str
    status: Literal["done", "todo", "attention"]
    optional: bool
    providers: list[str]


class Checklist(ApiModel):
    """Added: the Founder's one-time steps, computed by the server."""

    steps: list[ChecklistStep]
    done: int
    total: int
