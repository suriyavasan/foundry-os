"""Pure helpers for ``foundryos setup`` (the terminal version of the web onboarding).

Nothing here talks to the network or the terminal, so the rules are unit-tested on their own
(``tests/test_cli_onboarding_units.py``): the provider presets (mirrors ``web/src/lib/aiSetup.ts``), the
shape of a provider test result, the model picker's filter, timezone detection, the ``--secrets-stdin``
format, the idea rule, and the options object the wizard runs on.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.cli.client import CliError

# ------------------------------------------------------------------ steps and flags

STEPS = ("service", "account", "2fa", "company", "ai", "work", "public-url")
WORK_MODES = ("api", "hybrid")
CLI_IDS = ("claude_code", "codex", "opencode", "antigravity")
# --cli-auth value -> what the wizard does ("provider" = OpenCode on the company's model provider).
CLI_AUTH_FLAGS = {"own-login": "own_login", "api-key": "api_key", "provider": "provider"}
PUBLIC_URL_CHOICES = ("quick", "named", "tailscale", "off")
IDEA_MAX = 280

# What --secrets-stdin may carry (key=value lines). Anything else is refused, not ignored.
SECRET_KEYS = ("password", "api_key", "cli_api_key", "setup_token")

# The readiness checks, in the order the server reports them (web/src/lib/aiSetup.ts CHECK_ORDER).
CHECK_ORDER = ("reachable", "auth", "tool_calling", "context", "model_exists")
CHECK_LABEL = {
    "reachable": "Reachable",
    "auth": "Authenticated",
    "tool_calling": "Tool calling",
    "context": "Context window",
    "model_exists": "Model available",
}

BLOCKING_REASON_TEXT = {
    "no_tested_provider": "Connect an AI provider and run its test. Nothing can run without a working one.",
    "provider_test_failed": "The last test of your AI provider did not pass. Fix the connection and test it again.",
    "no_default_model": "Choose the model your AI Co-founder should use to manage the company.",
}


@dataclass
class WizardOptions:
    """Everything ``foundryos setup`` can be told up front. Every field has a default so other
    commands can run the wizard with ``WizardOptions()``. Secrets never live in an option: they come
    from ``secrets`` (parsed from ``--secrets-stdin``) or a hidden prompt."""

    only: str | None = None
    non_interactive: bool = False
    username: str | None = None
    display_name: str | None = None
    email: str | None = None
    company_name: str | None = None
    idea: str | None = None
    timezone: str | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    allow_private_network: bool = False
    work_mode: str | None = None
    cli: str | None = None
    cli_auth: str | None = None
    install_cli: bool = False
    service: bool | None = None
    linger: bool | None = None
    two_fa: bool | None = None
    public_url: str | None = None
    hostname: str | None = None
    secrets_stdin: bool = False
    secrets: dict[str, str] = field(default_factory=dict, repr=False)

    def hybrid_flags_without_hybrid(self) -> bool:
        return self.work_mode == "api" and bool(self.cli or self.cli_auth or self.install_cli)


def usage(message: str) -> CliError:
    return CliError(message, code="usage")


def validate_choice(flag: str, value: str | None, allowed: tuple[str, ...]) -> None:
    if value is not None and value not in allowed:
        raise usage(f"{flag} must be one of: {', '.join(allowed)} (got {value!r}).")


def validate_options(opts: WizardOptions) -> None:
    """Fail early (exit 2) on a flag value that can never work."""
    validate_choice("--only", opts.only, STEPS)
    validate_choice("--work-mode", opts.work_mode, WORK_MODES)
    validate_choice("--cli", opts.cli, CLI_IDS)
    validate_choice("--cli-auth", opts.cli_auth, tuple(CLI_AUTH_FLAGS))
    validate_choice("--public-url", opts.public_url, PUBLIC_URL_CHOICES)
    validate_choice("--provider", opts.provider, tuple(PRESET_BY_FLAG))
    if opts.public_url == "named" and not opts.hostname:
        raise usage("--public-url named needs --hostname (the domain you own on Cloudflare).")
    if opts.cli_auth == "provider" and opts.cli not in (None, "opencode"):
        raise usage("--cli-auth provider only works with --cli opencode.")
    if opts.cli_auth == "own-login" and opts.cli == "antigravity":
        raise usage("Antigravity CLI has no own-login mode; use --cli-auth api-key.")
    if opts.idea is not None:
        idea_error(opts.idea)
    if opts.timezone is not None:
        timezone_error(opts.timezone)
    if opts.base_url is not None:
        err = base_url_error(opts.base_url)
        if err:
            raise usage(f"--base-url: {err}")
    if opts.hybrid_flags_without_hybrid():
        raise usage("--cli / --cli-auth / --install-cli need --work-mode hybrid.")


# ------------------------------------------------------------------ secrets on stdin


def parse_secrets(text: str) -> dict[str, str]:
    """``--secrets-stdin``: one ``key=value`` per line, split on the FIRST ``=`` (a password may contain
    ``=``). Blank lines and ``#`` comments are skipped. Unknown or repeated keys and empty values are
    refused; the message names the key, never a value."""
    out: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep:
            raise usage(f"--secrets-stdin line {number}: expected key=value.")
        if key not in SECRET_KEYS:
            raise usage(
                f"--secrets-stdin line {number}: unknown key {key!r} (allowed: {', '.join(SECRET_KEYS)})."
            )
        if key in out:
            raise usage(f"--secrets-stdin: {key!r} appears twice.")
        if value == "":
            raise usage(f"--secrets-stdin line {number}: {key!r} has an empty value.")
        out[key] = value
    return out


def redact(text: str, *secrets: str | None) -> str:
    """Never let a message echo a secret back (the web's ``redactSecret``)."""
    for secret in secrets:
        if secret and len(secret.strip()) >= 4:
            text = text.replace(secret.strip(), "[redacted]")
    return text


# ------------------------------------------------------------------ input rules


def idea_error(idea: str) -> str | None:
    """The idea is one line of at most 280 characters (the web's IDEA_MAX). Blank is fine (optional)."""
    text = idea.strip()
    if "\n" in text or "\r" in text:
        raise usage("The idea must be one line.")
    if len(text) > IDEA_MAX:
        raise usage(
            f"Keep the idea to one line of at most {IDEA_MAX} characters ({len(text)} given)."
        )
    return None


def timezone_error(name: str) -> str | None:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise usage(
            f"Unknown timezone {name!r}. Use an IANA name such as Europe/Berlin or UTC."
        ) from None
    return None


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return bool(name)


def detect_timezone(
    environ: Mapping[str, str] | None = None,
    timezone_file: Path = Path("/etc/timezone"),
    localtime_link: Path = Path("/etc/localtime"),
) -> str:
    """The machine's IANA timezone: ``TZ``, ``/etc/timezone``, the ``/etc/localtime`` link, else UTC."""
    env = os.environ if environ is None else environ
    candidates: list[str] = []
    tz = (env.get("TZ") or "").strip().lstrip(":")
    if tz:
        candidates.append(tz)
    with contextlib.suppress(OSError):
        candidates.append(timezone_file.read_text(encoding="utf-8").strip())
    try:
        target = os.readlink(localtime_link)
        if "zoneinfo/" in target:
            candidates.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in candidates:
        if name and is_valid_timezone(name):
            return name
    return "UTC"


def is_loopback_host(host: str | None) -> bool:
    h = (host or "").strip("[]").lower()
    return h in ("localhost", "::1", "0.0.0.0") or h.startswith("127.")


def is_loopback_url(url: str) -> bool:
    try:
        return is_loopback_host(urlparse(url).hostname)
    except ValueError:
        return False


def url_port(url: str, default: int = 8700) -> int:
    try:
        return urlparse(url).port or default
    except ValueError:
        return default


def base_url_error(url: str) -> str | None:
    """None when fine. The web's rules: http(s) only, no credentials inside the URL."""
    text = url.strip()
    try:
        parsed = urlparse(text)
    except ValueError:
        return "Enter a full URL like http://127.0.0.1:11434"
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "Use an http:// or https:// URL, for example http://127.0.0.1:11434"
    if parsed.username or parsed.password:
        return "Do not put credentials in the URL; the API key has its own prompt"
    return None


def api_key_error(value: str) -> str | None:
    if not value.strip():
        return "An API key is required for this provider"
    if re.search(r"\s", value.strip()):
        return "An API key has no spaces or line breaks; check what you pasted"
    return None


# ------------------------------------------------------------------ provider presets


@dataclass(frozen=True)
class ProviderPreset:
    id: str  # the web card id (also the credential name prefix)
    flag: str  # the --provider value
    kind: str  # the API's provider kind
    label: str
    blurb: str
    needs_base_url: bool
    needs_key: bool  # the key is required
    takes_key: bool  # a key may be given (openai-compatible: optional)
    key_label: str
    model_suggestions: tuple[str, ...]
    base_url_hint: str


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        "openrouter", "openrouter", "openrouter", "OpenRouter",
        "One key, hundreds of models from every lab.",
        False, True, True, "OpenRouter API key",
        ("anthropic/claude-sonnet-4.5", "openai/gpt-4.1"), "",
    ),
    ProviderPreset(
        "anthropic", "anthropic", "anthropic", "Anthropic",
        "Claude models, straight from Anthropic.",
        False, True, True, "Anthropic API key",
        ("claude-sonnet-4-5",), "",
    ),
    ProviderPreset(
        "openai", "openai", "openai", "OpenAI",
        "GPT models, straight from OpenAI.",
        False, True, True, "OpenAI API key",
        ("gpt-4.1",), "",
    ),
    ProviderPreset(
        "openai_compatible", "openai-compatible", "openai_compatible", "OpenAI-compatible",
        "Any compatible API: Groq, Together, DeepSeek, a gateway or your own server.",
        True, False, True, "API key (if the server needs one)",
        (), "https://host.example/v1",
    ),
)  # fmt: skip
OLLAMA_PRESET = ProviderPreset(
    "ollama", "ollama", "ollama", "Local Ollama",
    "Runs on this computer: free and private.",
    True, False, False, "", (), "http://127.0.0.1:11434",
)  # fmt: skip
ALL_PRESETS: tuple[ProviderPreset, ...] = (*PROVIDER_PRESETS, OLLAMA_PRESET)
PRESET_BY_FLAG = {p.flag: p for p in ALL_PRESETS}


def credential_name(preset: ProviderPreset) -> str:
    """Named like the web: ``<card id>-key``."""
    return f"{preset.id}-key"


def derive_is_local(kind: str, base_url: str | None) -> bool:
    """Whether calls run on this machine (no provider charge): derived, never typed in."""
    return kind == "ollama" or (kind == "openai_compatible" and is_loopback_url(base_url or ""))


def provider_body(
    preset: ProviderPreset,
    *,
    base_url: str | None,
    model: str,
    credential_id: str | None,
    allow_private_network: bool = False,
) -> dict[str, Any]:
    """The ``POST /model-providers`` body. It references the credential by id; there is no secret in it."""
    url = (base_url or "").strip() if preset.needs_base_url else ""
    body: dict[str, Any] = {
        "name": preset.label,
        "kind": preset.kind,
        "default_model": model.strip(),
        "is_local": derive_is_local(preset.kind, url),
        "enabled": True,
    }
    if url:
        body["base_url"] = url
    if credential_id:
        body["credential_id"] = credential_id
    if allow_private_network and preset.needs_base_url:
        body["allow_private_network"] = True
    return body


# ------------------------------------------------------------------ provider test results


def provider_test_passed(test: Mapping[str, Any] | None) -> bool:
    """Ready only if the server says ok and every reported check passed (the web's ``testPassed``)."""
    if not test or test.get("ok") is not True:
        return False
    return all(c.get("ok") is True for c in test.get("checks") or [])


def first_failure(test: Mapping[str, Any] | None) -> dict[str, Any] | None:
    for check in (test or {}).get("checks") or []:
        if check.get("ok") is not True:
            return check
    return None


def check_label(name: str) -> str:
    return CHECK_LABEL.get(name, name.replace("_", " ").replace("-", " ").capitalize())


def check_lines(test: Mapping[str, Any] | None) -> list[str]:
    """One line per readiness check: ``[ok] Reachable``, ``[FAIL] Authenticated: <server's detail>``,
    ``[--] Model available (not checked)``."""
    reported = {c.get("name"): c for c in (test or {}).get("checks") or []}
    lines: list[str] = []
    for name in (*CHECK_ORDER, *[n for n in reported if n not in CHECK_ORDER]):
        check = reported.get(name)
        label = check_label(str(name))
        if check is None:
            lines.append(f"[--] {label} (not checked)")
        elif check.get("ok") is True:
            lines.append(f"[ok] {label}" + (f": {check['detail']}" if check.get("detail") else ""))
        else:
            lines.append(
                f"[FAIL] {label}" + (f": {check['detail']}" if check.get("detail") else "")
            )
    return lines


def failure_summary(test: Mapping[str, Any] | None) -> str:
    """The one line of "what to fix": the first failing check with the server's detail."""
    check = first_failure(test)
    if check is None:
        return "The test did not pass."
    detail = f": {check['detail']}" if check.get("detail") else ""
    return f"{check_label(str(check.get('name')))} failed{detail}"


# ------------------------------------------------------------------ model picker


@dataclass(frozen=True)
class ModelOption:
    id: str
    context_length: int | None = None


def normalize_models(raw: Any) -> list[ModelOption]:
    """``GET /model-providers/{id}/models`` -> options (``items`` of objects or strings, or a bare list)."""
    items = raw.get("items", []) if isinstance(raw, dict) else raw
    out: list[ModelOption] = []
    seen: set[str] = set()
    for m in items or []:
        if isinstance(m, str):
            mid, ctx = m, None
        elif isinstance(m, dict):
            mid = m.get("id") or m.get("name") or m.get("model")
            ctx = m.get("context_length") or m.get("context_window")
        else:
            continue
        if not isinstance(mid, str) or not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(ModelOption(mid, ctx if isinstance(ctx, int) else None))
    return out


def filter_models(models: list[ModelOption], query: str, limit: int = 50) -> list[ModelOption]:
    """Case-insensitive substring match; prefix matches first; empty query = everything."""
    q = query.strip().lower()
    if not q:
        return models[:limit]
    starts = [m for m in models if m.id.lower().startswith(q)]
    contains = [m for m in models if q in m.id.lower() and not m.id.lower().startswith(q)]
    return [*starts, *contains][:limit]


# ------------------------------------------------------------------ 2FA


def qr_text(uri: str) -> str | None:
    """The otpauth URI as a terminal QR (segno is a dependency); None when it cannot be drawn."""
    try:
        import segno

        buf = io.StringIO()
        segno.make(uri).terminal(out=buf, compact=True)
        return buf.getvalue()
    except Exception:  # a missing QR must never stop the setup: the secret is printed as well
        return None


# ------------------------------------------------------------------ the first note


def seed_idea(client, company_id: str, idea: str) -> str:
    """Make the one-line idea the first Founder's note (the web's ``seedCompanyIdea``), once.

    Returns ``note`` (created), ``exists`` (an identical note is already there: a rerun), ``skipped``
    (no idea) or ``waiting`` (the server refuses notes until the AI works: 409 ``ai_setup_required``)."""
    text = idea.strip()
    if not text:
        return "skipped"
    base = f"/companies/{company_id}/notes"
    for note in client.paged(base, max_items=400):
        if (note.get("body") or "").strip() == text:
            return "exists"
    try:
        client.post(base, {"body": text})
    except CliError as exc:
        if exc.code == "ai_setup_required":
            return "waiting"
        raise
    return "note"
