"""The provider catalog: what each connectable account is, which agent actions it enables, how to
build its client from stored secrets and how to test the connection.

Secret fields: the first (``main``) is the account's ``credential_id``; the others are
``extra_credential_ids`` keyed by field name. Non-secret fields live in the account metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.integrations import tailscale as ts
from app.integrations.agentmail import AgentMailClient
from app.integrations.base import IntegrationHttp, ProviderAuthError, ProviderError
from app.integrations.cards.mercury import MercuryProvider
from app.integrations.cards.stripe_issuing import StripeIssuingProvider
from app.integrations.cloudflare import CloudflareClient
from app.integrations.github import GithubClient
from app.integrations.namecheap import NamecheapClient
from app.integrations.porkbun import PorkbunClient
from app.integrations.resend import ResendClient
from app.integrations.vercel import VercelClient


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    category: str  # dns | hosting | code | email | tunnel | registrar | oauth | cards
    secrets: tuple[str, ...] = ("api_token",)  # [0] is main
    optional_secrets: tuple[str, ...] = ()
    metadata: tuple[str, ...] = ()
    required_metadata: tuple[str, ...] = ()
    actions: frozenset[str] = frozenset()
    needs_secret: bool = True
    oauth: bool = False
    help: str = ""


SPECS: dict[str, ProviderSpec] = {
    s.id: s
    for s in (
        ProviderSpec(
            "cloudflare",
            "Cloudflare",
            "dns",
            optional_secrets=("webhook_secret",),
            metadata=("account_id", "email_worker", "forward_to"),
            actions=frozenset(
                {"dns.read", "dns.change", "tunnel.status", "tunnel.start", "tunnel.stop"}
            ),
            help="API token with Zone:Read, DNS:Edit, Cloudflare Tunnel:Edit (and Email Routing "
            "Rules:Edit for mailboxes).",
        ),
        ProviderSpec(
            "vercel",
            "Vercel",
            "hosting",
            metadata=("team_id",),
            actions=frozenset({"hosted_ui.deploy", "hosted_ui.status"}),
            help="A Vercel access token (Account Settings > Tokens).",
        ),
        ProviderSpec(
            "github",
            "GitHub",
            "code",
            actions=frozenset({"github.read", "domain.preview", "domain.register_pr"}),
            help="A personal access token that may create forks and pull requests "
            "(fine-grained: Contents + Pull requests write on forks; classic: public_repo).",
        ),
        ProviderSpec(
            "agentmail",
            "AgentMail",
            "email",
            optional_secrets=("webhook_secret",),
            metadata=("domain",),
            actions=frozenset({"mailbox.create", "email.send", "email.read"}),
            help="An AgentMail API key. Add the webhook signing secret to receive mail.",
        ),
        ProviderSpec(
            "resend",
            "Resend",
            "email",
            optional_secrets=("webhook_secret",),
            actions=frozenset({"mailbox.create", "email.send", "email.read"}),
            help="A Resend API key (full access to manage domains; sending access can only send).",
        ),
        ProviderSpec(
            "tailscale",
            "Tailscale",
            "tunnel",
            needs_secret=False,
            actions=frozenset(),
            help="Uses the Tailscale already signed in on this device; no secret is stored.",
        ),
        ProviderSpec(
            "porkbun",
            "Porkbun",
            "registrar",
            secrets=("api_key", "secret_api_key"),
            actions=frozenset({"domain.search", "domain.purchase"}),
            help="Porkbun API key and secret key; enable API access on the domain.",
        ),
        ProviderSpec(
            "namecheap",
            "Namecheap",
            "registrar",
            secrets=("api_key",),
            metadata=("api_user", "username", "client_ip"),
            required_metadata=("api_user", "username", "client_ip"),
            actions=frozenset({"domain.search", "domain.purchase"}),
            help="Namecheap API key, ApiUser, UserName and the whitelisted client IP. "
            "Purchases also need registrant_<Field> metadata.",
        ),
        # M15 agent cards. The control plane makes every call; agents never get these keys
        # (``actions`` is empty: no agent can use them directly). The card adapters are
        # implemented against the providers' documented APIs and fixture-tested, not verified live.
        ProviderSpec(
            "stripe_issuing",
            "Stripe Issuing",
            "cards",
            secrets=("api_key",),
            optional_secrets=("webhook_secret",),
            metadata=("cardholder_id",),
            help="A Stripe RESTRICTED key: write Issuing Cards and Issuing Cardholders; read "
            "Issuing Authorizations, Issuing Transactions and Balance; nothing else. A sandbox "
            "key (rk_test_...) is a safe first test.",
        ),
        ProviderSpec(
            "mercury",
            "Mercury",
            "cards",
            secrets=("api_token",),
            metadata=("account_id",),
            help="A Mercury API token with read/write access. Mercury may require your server's "
            "public IP on an allowlist for write tokens.",
        ),
        ProviderSpec("gmail", "Gmail", "oauth", needs_secret=False, oauth=True),
        ProviderSpec("outlook", "Outlook", "oauth", needs_secret=False, oauth=True),
    )
}


def spec_for(provider: str) -> ProviderSpec | None:
    return SPECS.get(provider)


@dataclass
class CheckResult:
    ok: bool
    message: str | None = None
    account_label: str | None = None
    scopes: tuple[str, ...] = ()
    metadata: dict[str, str] | None = None


def build_client(
    provider: str,
    secrets: dict[str, str],
    meta: dict[str, str],
    http: IntegrationHttp,
    settings: Settings,
    *,
    timeout: float = 20.0,
):
    """An API client for the account; the caller closes ``client.http`` when done."""
    client = http.client(timeout=timeout)
    main = secrets.get("api_token") or secrets.get("api_key") or ""
    if provider == "cloudflare":
        return CloudflareClient(client, main)
    if provider == "vercel":
        return VercelClient(client, main, meta.get("team_id") or None)
    if provider == "github":
        return GithubClient(client, main, base_url=settings.github_api_url)
    if provider == "agentmail":
        return AgentMailClient(client, main)
    if provider == "resend":
        return ResendClient(client, main)
    if provider == "porkbun":
        return PorkbunClient(client, main, secrets.get("secret_api_key", ""))
    if provider == "namecheap":
        return NamecheapClient(
            client,
            main,
            api_user=meta.get("api_user", ""),
            username=meta.get("username", ""),
            client_ip=meta.get("client_ip", ""),
        )
    if provider == "stripe_issuing":
        return StripeIssuingProvider(
            client,
            main,
            cardholder_id=meta.get("cardholder_id") or None,
            webhook_secret=secrets.get("webhook_secret") or None,
        )
    if provider == "mercury":
        return MercuryProvider(client, main, account_id=meta.get("account_id") or None)
    client.close()
    raise ProviderError(f"No client for provider {provider!r}")


def check_connection(provider: str, client: Any, meta: dict[str, str]) -> CheckResult:
    """Never raises for provider failures: they become ``ok=False`` with the provider's message
    (secrets masked by the client)."""
    try:
        return _TESTS[provider](client, meta)
    except ProviderError as exc:
        return CheckResult(False, exc.message)


def _t_cloudflare(c: CloudflareClient, meta: dict[str, str]) -> CheckResult:
    info = c.verify_token()
    if info.get("status") not in (None, "active"):
        return CheckResult(False, f"The token is {info.get('status')}")
    scopes = ["token:active"]
    out_meta: dict[str, str] = {}
    label = None
    try:
        accounts = c.accounts()
        if accounts:
            chosen = next(
                (a for a in accounts if a.get("id") == meta.get("account_id")), accounts[0]
            )
            label = chosen.get("name")
            out_meta["account_id"] = str(chosen.get("id"))
            out_meta["account_name"] = str(chosen.get("name"))
            scopes.append("account:read")
    except ProviderAuthError:
        pass
    try:
        c.zones()
        scopes.append("zone:read")
    except ProviderAuthError:
        pass
    account_id = meta.get("account_id") or out_meta.get("account_id")
    if account_id:
        try:
            c.tunnel_list(account_id)
            scopes.append("tunnel:read")
        except ProviderError:
            pass
    return CheckResult(True, "Connected to Cloudflare", label, tuple(scopes), out_meta)


def _t_vercel(c: VercelClient, meta: dict[str, str]) -> CheckResult:
    user = c.user()
    label = user.get("username") or user.get("name") or user.get("email")
    out = {"username": str(user.get("username"))} if user.get("username") else {}
    return CheckResult(True, "Connected to Vercel", label, ("account",), out)


def _t_github(c: GithubClient, meta: dict[str, str]) -> CheckResult:
    user = c.identity()
    login = str(user.get("login") or "")
    return CheckResult(
        True,
        "Connected to GitHub",
        login or None,
        tuple(c.scopes),
        {"login": login} if login else {},
    )


def _t_agentmail(c: AgentMailClient, meta: dict[str, str]) -> CheckResult:
    c.inboxes(1)
    return CheckResult(True, "Connected to AgentMail", None, ("inboxes",))


def _t_resend(c: ResendClient, meta: dict[str, str]) -> CheckResult:
    try:
        domains = c.domains()
    except ProviderAuthError as exc:
        if "restricted" in exc.message.lower():  # a sending-only key cannot list domains
            return CheckResult(True, "Connected (sending access only)", None, ("send",))
        raise
    return CheckResult(
        True, "Connected to Resend", None, ("domains", "send"), {"domains": str(len(domains))}
    )


def _t_porkbun(c: PorkbunClient, meta: dict[str, str]) -> CheckResult:
    c.ping()
    return CheckResult(True, "Connected to Porkbun", None, ("domains",))


def _t_namecheap(c: NamecheapClient, meta: dict[str, str]) -> CheckResult:
    info = c.ping()
    return CheckResult(True, "Connected to Namecheap", info.get("account"), ("domains",))


def _t_cards(c: StripeIssuingProvider | MercuryProvider, meta: dict[str, str]) -> CheckResult:
    """Authenticated, Issuing / Cards enabled, balance readable. Creates no card."""
    info = c.check()
    mode = str(info.get("mode", "live"))
    scopes = ["auth"]
    if info.get("issuing_enabled"):
        scopes.append("cards")
    if info.get("balance_readable"):
        scopes.append("balance")
    if not info.get("ok"):
        return CheckResult(
            False,
            str(info.get("message") or "Card issuing is not enabled for this credential"),
            None,
            tuple(scopes),
        )
    label = f"{c.provider} ({mode})"
    return CheckResult(True, f"Connected to {c.provider} ({mode} mode)", label, tuple(scopes))


_TESTS = {
    "cloudflare": _t_cloudflare,
    "vercel": _t_vercel,
    "github": _t_github,
    "agentmail": _t_agentmail,
    "resend": _t_resend,
    "porkbun": _t_porkbun,
    "namecheap": _t_namecheap,
    "stripe_issuing": _t_cards,
    "mercury": _t_cards,
}


def check_tailscale(binary: str | None, run) -> CheckResult:
    info = ts.status(binary, run)
    if not info["installed"] or info["error"]:
        return CheckResult(False, info["error"])
    meta = {"dns_name": info["dns_name"] or "", "tailnet": info["tailnet"] or ""}
    return CheckResult(
        True,
        "Tailscale is running" + (" and can serve Funnel" if info["funnel_capable"] else ""),
        info["dns_name"],
        ("funnel",) if info["funnel_capable"] else (),
        {k: v for k, v in meta.items() if v},
    )
