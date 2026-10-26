"""Device-level reachability: how this installation can be reached (docs/specs/m8 section 2).

Everything here is installation-wide and owner-only (the account that claimed the install, the same
rule as the emergency stop). Public exposure -- binding to the network or running a tunnel -- is
refused unless ``auth_mode=password`` and the owner account is protected by a password chosen under
the policy (or 2FA): ``exposure_block`` is the single gate and its code/reason are part of the
status the UI polls.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.core.netinfo import is_loopback_bind
from app.core.principal import Principal
from app.core.time import utcnow
from app.integrations import cloudflared, tailscale
from app.integrations.cloudflare import CloudflareClient, apply_dns_plan, plan_dns
from app.integrations.context import IntegrationContext
from app.integrations.registries import valid_hostname
from app.integrations.tunnel import TunnelSpec, is_ephemeral_host
from app.models.accounts import Passkey
from app.models.audit import AuditEvent
from app.models.company import CompanyMember
from app.models.enums import MemberRole, MemberStatus
from app.models.guardrails import SystemState
from app.models.reachability import CompanyDomain, ConnectedAccount
from app.models.user import User
from app.services import audit, connected_accounts, events

logger = logging.getLogger("company_os.reachability")
STATE_KEY = "reachability"
SKIP_INTERFACES = (
    "utun", "awdl", "llw", "bridge", "vmenet", "gif", "stf", "anpi", "ap1", "docker",
    "veth", "br-", "virbr", "vmnet", "tun", "tap",
)  # fmt: skip
TUNNEL_KINDS = ("cloudflare_quick", "cloudflare_named", "tailscale_funnel")
URL_STATE_KEY = "tunnel_url"  # SystemState row: last_url, previous_url, url_changed_at, history
URL_HISTORY = 5
PUBLIC_URL_FILE = "public-url.json"  # in the data dir, NOT in run/ (that is globbed for pid files)


# ------------------------------------------------------------------ owner & persisted state


def require_installation_owner(db: Session, principal: Principal) -> User:
    if not principal.is_human:
        raise Forbidden("Only the installation owner can change how this device is reached")
    user = db.get(User, principal.id)
    if user is None or not user.is_local_owner:
        raise Forbidden("Only the installation owner can see or change how this device is reached")
    return user


def load_state(db: Session) -> dict[str, Any]:
    row = db.get(SystemState, STATE_KEY)
    return dict(row.value or {}) if row else {}


def save_state(db: Session, **patch: Any) -> dict[str, Any]:
    row = db.get(SystemState, STATE_KEY)
    value = dict(row.value or {}) if row else {}
    value.update(patch)
    if row is None:
        db.add(SystemState(key=STATE_KEY, value=value, updated_at=utcnow()))
    else:
        row.value = value
        row.updated_at = utcnow()
    return value


def owner_company_ids(db: Session, user_id: str) -> list[str]:
    return list(
        db.scalars(
            select(CompanyMember.company_id).where(
                CompanyMember.user_id == user_id,
                CompanyMember.status == MemberStatus.ACTIVE,
                CompanyMember.role.in_([MemberRole.OWNER.value, MemberRole.ADMIN.value]),
            )
        )
    )


def audit_device(
    db: Session, principal: Principal, action: str, summary: str, data: dict[str, Any] | None = None
) -> None:
    """Install-wide actions have no company: they go to the owner's companies' activity feeds."""
    for company_id in owner_company_ids(db, principal.id):
        audit.record(
            db,
            company_id=company_id,
            principal=principal,
            action=action,
            target_type="reachability",
            target_id=None,
            summary=summary,
            data=data,
        )
        events.emit(db, company_id, "reachability.updated", {})


# ------------------------------------------------------------------ the exposure gate


def owner_has_strong_auth(db: Session, owner: User) -> bool:
    """Two-factor sign-in or at least one passkey: either makes up for a password that was not set
    under the policy (a passkey is phishing-resistant and needs the device)."""
    if owner.totp_enabled:
        return True
    ids = list(db.scalars(select(Passkey.id).where(Passkey.user_id == owner.id)))
    if not ids:
        return False
    stale = _ephemeral_passkey_ids(db, owner.id)
    return any(pid not in stale for pid in ids)


def _ephemeral_passkey_ids(db: Session, user_id: str) -> set[str]:
    """Passkeys registered on a quick-tunnel origin. They stop working when the URL changes, so
    they are not strong authentication. The origin is read from the ``auth.passkey_added`` audit
    event (registration there is refused now; an event from before this was recorded carries no
    origin and counts as an ordinary passkey)."""
    found: set[str] = set()
    rows = db.scalars(
        select(AuditEvent.data).where(
            AuditEvent.action == "auth.passkey_added", AuditEvent.target_id == user_id
        )
    )
    for data in rows:
        if isinstance(data, dict) and is_ephemeral_host(str(data.get("rp_id") or "")):
            found.add(str(data.get("passkey_id")))
    return found


def exposure_block(db: Session, settings) -> dict[str, str] | None:
    """Why this device may not be exposed beyond localhost, or None when it may. ``code`` is one of
    ``auth_local``, ``no_password``, ``weak_password``."""
    if settings.auth_mode != "password":
        return {
            "code": "auth_local",
            "reason": "Sign-in is in local (development) mode, which trusts this machine only. "
            "Run with password sign-in before exposing this device.",
        }
    owner = db.scalar(
        select(User).where(User.is_local_owner.is_(True)).order_by(User.created_at, User.id)
    )
    if owner is None or not owner.password_hash:
        return {
            "code": "no_password",
            "reason": "The owner account has no password yet. Finish the first-run setup first.",
        }
    if owner.password_changed_at is None and not owner_has_strong_auth(db, owner):
        return {
            "code": "weak_password",
            "reason": "The owner password was not set under the password policy. Change it (or "
            "turn on 2FA or add a passkey) before exposing this device.",
        }
    return None


def require_exposure_allowed(db: Session, settings) -> None:
    block = exposure_block(db, settings)
    if block:
        raise Forbidden(
            block["reason"],
            details={"reason": "public_exposure_blocked", "code": block["code"]},
        )


def owner_has_2fa(db: Session) -> bool:
    owner = db.scalar(select(User).where(User.is_local_owner.is_(True)).limit(1))
    return bool(owner and owner.totp_enabled)


# ------------------------------------------------------------------ LAN detection


def _skip_interface(name: str) -> bool:
    return bool(re.fullmatch(r"lo\d*", name)) or name.startswith(SKIP_INTERFACES)


def _usable(ip: str) -> tuple[bool, str | None]:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False, None
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return False, None
    if isinstance(addr, ipaddress.IPv6Address):
        # globally routable or unique-local only
        return (addr.is_global or addr.is_private), "ipv6"
    return True, "ipv4"


def parse_ifconfig(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    iface = ""
    for line in text.splitlines():
        head = re.match(r"^([A-Za-z0-9_.:-]+?):\s+flags=", line)
        if head:
            iface = head.group(1)
            continue
        m = re.match(r"^\s+inet6?\s+([0-9a-fA-F:.]+)", line)
        if m and iface:
            out.append((iface, m.group(1)))
    return out


def parse_ip_json(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for link in json.loads(text):
        for info in link.get("addr_info", []):
            if info.get("local"):
                out.append((link.get("ifname", ""), info["local"]))
    return out


def detect_addresses(run=subprocess.run, system: str | None = None) -> list[dict[str, str | None]]:
    """Non-loopback, non-link-local addresses with their interface. Best effort and read-only."""
    system = system or platform.system()
    pairs: list[tuple[str, str]] = []
    try:
        if system == "Linux":
            proc = run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=5)
            pairs = parse_ip_json(proc.stdout)
        else:
            proc = run(["ifconfig"], capture_output=True, text=True, timeout=5)
            pairs = parse_ifconfig(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        pairs = []
    found: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for iface, ip in pairs:
        ok, family = _usable(ip)
        if not ok or ip in seen or _skip_interface(iface):
            continue
        seen.add(ip)
        found.append({"address": ip.split("%")[0], "family": family, "interface": iface})
    if not found:  # the interface used for the default route, without a name
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.0.2.1", 9))  # TEST-NET: no packet is sent
                ip = s.getsockname()[0]
            if _usable(ip)[0]:
                found.append({"address": ip, "family": "ipv4", "interface": None})
        except OSError:
            pass
    found.sort(key=lambda a: (a["family"] != "ipv4", a["address"]))
    return found


def _scutil(run, key: str) -> str | None:
    try:
        proc = run(["scutil", "--get", key], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def device_names(run=subprocess.run) -> tuple[str, str, str | None]:
    """(device name, hostname, mDNS name). macOS announces ``<LocalHostName>.local`` through
    Bonjour; elsewhere it is reported only when avahi is installed."""
    hostname = socket.gethostname()
    system = platform.system()
    if system == "Darwin":
        local = _scutil(run, "LocalHostName")
        name = _scutil(run, "ComputerName") or hostname
        return name, hostname, f"{local}.local" if local else None
    short = hostname.split(".")[0]
    mdns = f"{short}.local" if shutil.which("avahi-daemon") else None
    return hostname, hostname, mdns


def _url(address: str, port: int) -> str:
    host = f"[{address}]" if ":" in address else address
    return f"http://{host}:{port}"


# ------------------------------------------------------------------ tunnel facts


def owner_accounts(db: Session, user_id: str, provider: str) -> list[ConnectedAccount]:
    ids = owner_company_ids(db, user_id)
    if not ids:
        return []
    return list(
        db.scalars(
            select(ConnectedAccount)
            .where(
                ConnectedAccount.company_id.in_(ids),
                ConnectedAccount.provider == provider,
                ConnectedAccount.status == "connected",
            )
            .order_by(ConnectedAccount.created_at, ConnectedAccount.id)
        )
    )


def binary_model(ic: IntegrationContext) -> dict[str, Any]:
    info = cloudflared.binary_info(ic.settings)
    asset = cloudflared.platform_asset()
    plan = ic.install_plan or {}
    return {
        "name": "cloudflared",
        "found": info.found,
        "version": info.version,
        "path": info.path,
        "install_instructions": [] if info.found else list(cloudflared.INSTALL_INSTRUCTIONS),
        "download_offered": (not info.found) and asset is not None,
        "download_url": plan.get("url"),
        "download_sha256": plan.get("sha256"),
        "download_size_bytes": plan.get("size_bytes"),
    }


def provider_options(db: Session, ic: IntegrationContext, user_id: str) -> list[dict[str, Any]]:
    found = cloudflared.find_cloudflared(ic.settings) is not None
    cf = bool(owner_accounts(db, user_id, "cloudflare"))
    ts_bin = tailscale.find_tailscale(ic.settings.tailscale_path)
    ts_info = tailscale.status(ts_bin, ic.run) if ts_bin else None
    ts_ok = bool(ts_info and ts_info["running"])
    ts_acc = bool(owner_accounts(db, user_id, "tailscale"))
    return [
        {
            "kind": "cloudflare_quick",
            "requires_account": None,
            "available": found,
            "unavailable_reason": None if found else "cloudflared is not installed",
        },
        {
            "kind": "cloudflare_named",
            "requires_account": "cloudflare",
            "available": found and cf,
            "unavailable_reason": None
            if found and cf
            else ("cloudflared is not installed" if not found else "Connect a Cloudflare account"),
        },
        {
            "kind": "tailscale_funnel",
            "requires_account": "tailscale",
            "available": ts_ok and ts_acc,
            "unavailable_reason": None
            if ts_ok and ts_acc
            else (
                (ts_info or {}).get("error") or "Tailscale is not installed"
                if not ts_ok
                else "Connect your Tailscale account"
            ),
        },
    ]


def tunnel_model(db: Session, ic: IntegrationContext, user_id: str) -> dict[str, Any]:
    status = ic.supervisor.status()
    saved = load_url_state(db)
    provider = status["provider"] or (load_state(db).get("tunnel") or {}).get("provider")
    host = urlsplit(status["url"] or saved.get("last_url") or "").hostname
    changed_at = saved.get("url_changed_at")
    return {
        **status,
        # a quick tunnel's URL is new every time it starts
        "ephemeral": provider == "cloudflare_quick" or is_ephemeral_host(host),
        "last_url": saved.get("last_url"),
        "url_changed_at": datetime.fromisoformat(changed_at) if changed_at else None,
        "binary": binary_model(ic),
        "providers": provider_options(db, ic, user_id),
    }


# ------------------------------------------------------------------ status


def status(db: Session, ic: IntegrationContext, principal: Principal) -> dict[str, Any]:
    user = require_installation_owner(db, principal)
    settings = ic.settings
    state = load_state(db)
    bound = not is_loopback_bind(settings.bind_host)
    requested = bool(state.get("lan_enabled", False))
    device_name, hostname, mdns = device_names(ic.run)
    addresses = detect_addresses(ic.run)
    port = settings.port
    local_urls = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    lan_addresses = [{**a, "url": _url(str(a["address"]), port)} for a in addresses]
    tunnel = tunnel_model(db, ic, user.id)
    public_url = tunnel["url"] if tunnel["state"] == "running" else None
    if public_url:
        mode, active = "public", public_url
    elif bound:
        mode = "lan"
        active = (
            f"http://{mdns}:{port}"
            if mdns
            else (lan_addresses[0]["url"] if lan_addresses else local_urls[0])
        )
    else:
        mode, active = "local", local_urls[0]
    block = exposure_block(db, settings)
    domain_row = None
    for company_id in owner_company_ids(db, user.id):
        domain_row = db.scalar(
            select(CompanyDomain).where(
                CompanyDomain.company_id == company_id, CompanyDomain.domain.is_not(None)
            )
        )
        if domain_row:
            break
    two_fa = bool(user.totp_enabled)
    strong = owner_has_strong_auth(db, user)
    return {
        "mode": mode,
        "device_name": device_name,
        "hostname": hostname,
        "auth_mode": settings.auth_mode,
        "local_urls": local_urls,
        "lan": {
            "enabled": bound,
            "mdns_name": mdns,
            "mdns_url": f"http://{mdns}:{port}" if mdns else None,
            "addresses": lan_addresses,
            "requested": requested,
            "bind_host": settings.bind_host,
            "restart_required": requested != bound,
        },
        "tunnel": tunnel,
        "active_url": active,
        "public_exposure_blocked_reason": block["reason"] if block else None,
        "public_exposure_blocked_code": block["code"] if block else None,
        "public_exposure_advice": "two_factor_recommended" if not block and not strong else None,
        "two_factor_enabled": two_fa,
        "domain": (
            {
                "domain": domain_row.domain,
                "dns_status": domain_row.dns_status,
                "tls_status": domain_row.tls_status,
            }
            if domain_row
            else None
        ),
    }


# ------------------------------------------------------------------ LAN preference


def set_lan(db: Session, ic: IntegrationContext, principal: Principal, enabled: bool) -> None:
    """Persist the bind preference. The listening socket belongs to the launcher: the process must
    be restarted (``python -m app.serve`` / ``foundryos up``) to rebind, which ``restart_required``
    reports. Switching it on needs the exposure gate; switching it off never does."""
    require_installation_owner(db, principal)
    if enabled:
        require_exposure_allowed(db, ic.settings)
    save_state(db, lan_enabled=enabled)
    audit_device(
        db,
        principal,
        "reachability.lan_changed",
        f"Local network access {'requested' if enabled else 'switched off'}",
        {"enabled": enabled},
    )
    db.commit()


def preferred_bind_host(db_state: dict[str, Any]) -> str:
    return "0.0.0.0" if db_state.get("lan_enabled") else "127.0.0.1"  # noqa: S104


# ------------------------------------------------------------------ cloudflared install


def install_plan(db: Session, ic: IntegrationContext, principal: Principal) -> dict[str, Any]:
    require_installation_owner(db, principal)
    plan = cloudflared.plan_install(ic.settings, ic.http).as_dict()
    ic.install_plan = plan
    return plan


def install_binary(
    db: Session,
    ic: IntegrationContext,
    principal: Principal,
    *,
    confirm: bool,
    sha256: str | None,
) -> None:
    require_installation_owner(db, principal)
    path = cloudflared.install(ic.settings, ic.http, confirm=confirm, expected_sha256=sha256)
    version = cloudflared.binary_version(str(path))
    ic.install_plan = None
    audit_device(
        db,
        principal,
        "reachability.cloudflared_installed",
        "Installed cloudflared from the official release (checksum verified)",
        {"version": version, "path": str(path)},
    )
    db.commit()


# ------------------------------------------------------------------ tunnel control


def _named_tunnel_spec(
    db: Session, ic: IntegrationContext, user: User, hostname: str, binary: str
) -> TunnelSpec:
    accounts = owner_accounts(db, user.id, "cloudflare")
    if not accounts:
        raise Conflict("Connect a Cloudflare account first", details={"reason": "no_account"})
    account = accounts[0]
    client = connected_accounts.client_for(db, ic, account)
    assert isinstance(client, CloudflareClient)
    try:
        account_id = (account.meta or {}).get("account_id")
        if not account_id:
            found = client.accounts()
            if len(found) != 1:
                raise ValidationFailed(
                    "The Cloudflare token sees several accounts: set account_id on the connected "
                    "account"
                )
            account_id = str(found[0]["id"])
        zone = client.zone_for(hostname)
        if zone is None:
            raise ValidationFailed(
                f"The connected Cloudflare account has no zone for {hostname}",
                details={"reason": "no_zone"},
            )
        name = "company-os-" + re.sub(r"[^a-z0-9]+", "-", hostname)[:50].strip("-")
        existing = [t for t in client.tunnel_list(account_id, name) if t.get("name") == name]
        tunnel = existing[0] if existing else client.tunnel_create(account_id, name)
        tunnel_id = str(tunnel["id"])
        token = client.tunnel_token(account_id, tunnel_id)
        client.tunnel_configure(account_id, tunnel_id, hostname, ic.settings.local_url)
        records = client.dns_list(zone["id"], name=hostname)
        steps = plan_dns(
            [
                {
                    "type": "CNAME",
                    "name": hostname,
                    "value": f"{tunnel_id}.cfargotunnel.com",
                    "proxied": True,
                }
            ],
            records,
        )
        apply_dns_plan(client, zone["id"], steps)
    finally:
        client.http.close()
    return TunnelSpec(
        provider="cloudflare_named",
        argv=[binary, "tunnel", "--no-autoupdate", "run"],
        env={"TUNNEL_TOKEN": token},
        hostname=hostname,
        known_url=f"https://{hostname}",
        secrets=(token,),
    )


def build_spec(
    db: Session, ic: IntegrationContext, user: User, provider: str, hostname: str | None
) -> TunnelSpec:
    settings = ic.settings
    if provider in ("cloudflare_quick", "cloudflare_named"):
        binary = cloudflared.find_cloudflared(settings)
        if binary is None:
            raise Conflict(
                "cloudflared is not installed",
                details={"reason": "binary_missing", "binary": "cloudflared"},
            )
        if provider == "cloudflare_quick":
            return TunnelSpec(
                provider=provider,
                argv=[binary, "tunnel", "--no-autoupdate", "--url", settings.local_url],
                env={"HOME": str(cloudflared.quick_tunnel_home(settings))},
            )
        host = (hostname or "").strip().lower()
        if not valid_hostname(host):
            raise ValidationFailed(
                "Enter the hostname to serve, e.g. app.example.com",
                details=[{"loc": ["body", "hostname"], "msg": "invalid hostname"}],
            )
        return _named_tunnel_spec(db, ic, user, host, binary)
    if provider == "tailscale_funnel":
        binary = tailscale.find_tailscale(settings.tailscale_path)
        info = tailscale.status(binary, ic.run)
        if binary is None or info["error"]:
            raise Conflict(
                info["error"] or "Tailscale is not installed",
                details={"reason": "tailscale_unavailable"},
            )
        if not owner_accounts(db, user.id, "tailscale"):
            raise Conflict("Connect your Tailscale account first", details={"reason": "no_account"})
        dns = info["dns_name"]
        return TunnelSpec(
            provider=provider,
            argv=[binary, "funnel", str(settings.port)],
            hostname=dns,
            known_url=f"https://{dns}" if dns else None,
        )
    raise ValidationFailed(f"Unknown tunnel provider {provider!r}")


def installation_owner(db: Session) -> User:
    owner = db.scalar(
        select(User).where(User.is_local_owner.is_(True)).order_by(User.created_at, User.id)
    )
    if owner is None:
        raise Conflict("This installation has no owner account yet")
    return owner


def start_tunnel_core(
    db: Session, ic: IntegrationContext, provider: str, hostname: str | None
) -> TunnelSpec:
    """Start the tunnel (authorization is the caller's: the owner, or a consumed approval). The
    exposure gate is enforced here, so no caller can skip it."""
    require_exposure_allowed(db, ic.settings)
    if ic.supervisor.active:
        raise Conflict("The tunnel is already running; stop it first")
    spec = build_spec(db, ic, installation_owner(db), provider, hostname)
    save_state(db, tunnel={"provider": provider, "hostname": hostname, "desired": True})
    db.commit()
    ic.supervisor.start(spec)
    return spec


def stop_tunnel_core(db: Session, ic: IntegrationContext) -> None:
    ic.supervisor.stop()
    state = load_state(db).get("tunnel") or {}
    save_state(db, tunnel={**state, "desired": False})


def start_tunnel(
    db: Session,
    ic: IntegrationContext,
    principal: Principal,
    provider: str,
    hostname: str | None,
) -> None:
    require_installation_owner(db, principal)
    spec = start_tunnel_core(db, ic, provider, hostname)
    audit_device(
        db,
        principal,
        "reachability.tunnel_started",
        f"Started the {provider} tunnel",
        {"provider": provider, "hostname": spec.hostname},
    )
    db.commit()


def stop_tunnel(db: Session, ic: IntegrationContext, principal: Principal) -> None:
    require_installation_owner(db, principal)
    stop_tunnel_core(db, ic)
    audit_device(db, principal, "reachability.tunnel_stopped", "Stopped the tunnel")
    db.commit()


def tunnel_logs(db: Session, ic: IntegrationContext, principal: Principal) -> list[str]:
    require_installation_owner(db, principal)
    return ic.supervisor.logs(200)


def resume_tunnel(session_factory, ic: IntegrationContext) -> bool:
    """On startup: bring back a tunnel that was running when the server stopped (the exposure gate
    and the binary are checked again). Returns whether one was started."""
    if not ic.settings.tunnel_autoresume:
        return False
    with session_factory() as db:
        cfg = load_state(db).get("tunnel") or {}
        if not cfg.get("desired") or cfg.get("provider") not in TUNNEL_KINDS:
            return False
        if exposure_block(db, ic.settings) is not None or ic.supervisor.active:
            return False
        owner = db.scalar(select(User).where(User.is_local_owner.is_(True)).limit(1))
        if owner is None:
            return False
        try:
            spec = build_spec(db, ic, owner, cfg["provider"], cfg.get("hostname"))
        except Exception as exc:
            logger.warning("tunnel not resumed: %s", getattr(exc, "message", type(exc).__name__))
            return False
    ic.supervisor.start(spec)
    return True


def public_url(ic: IntegrationContext) -> str | None:
    st = ic.supervisor.status()
    return st["url"] if st["state"] == "running" else None


def tunnel_is_ephemeral(ic: IntegrationContext) -> bool:
    """Whether the running tunnel's URL changes on every restart (a quick tunnel)."""
    st = ic.supervisor.status()
    return st["state"] == "running" and (
        st["provider"] == "cloudflare_quick"
        or is_ephemeral_host(urlsplit(st["url"] or "").hostname)
    )


# ------------------------------------------------------------------ recording the public URL

_url_lock = threading.Lock()


def load_url_state(db: Session) -> dict[str, Any]:
    row = db.get(SystemState, URL_STATE_KEY)
    return dict(row.value or {}) if row else {}


def _save_url_state(db: Session, value: dict[str, Any]) -> None:
    row = db.get(SystemState, URL_STATE_KEY)
    if row is None:
        db.add(SystemState(key=URL_STATE_KEY, value=value, updated_at=utcnow()))
    else:
        row.value = value
        row.updated_at = utcnow()


def public_url_path(settings) -> Path:
    return settings.resolved_data_dir / PUBLIC_URL_FILE


def _announce(url: str) -> None:
    """INFO ``Public URL: ...``. The API process only shows INFO from uvicorn's own loggers, so when
    ours is not enabled for INFO the line goes there: a headless user finds it in the server log."""
    target = logger if logger.isEnabledFor(logging.INFO) else logging.getLogger("uvicorn.error")
    target.info("Public URL: %s", url)


def _read_public_url_file(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_public_url_file(path: Path, payload: dict[str, Any]) -> None:
    """Atomic (temp file + rename), 0600, next to the database: never half-written, never world
    readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".public-url-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def on_tunnel_change(session_factory, ic: IntegrationContext, status: dict[str, Any]) -> None:
    """The tunnel supervisor's change listener (never raises). A quick-tunnel URL is new on every
    start, so a URL the saved one does not match is remembered (last, previous, when, last five),
    audited for the owner's companies in the same transaction, logged, and written to
    ``<data>/public-url.json`` for a user with no browser. An unchanged URL records nothing; the
    file follows the tunnel (running / starting / stopped / error)."""
    try:
        with _url_lock:
            _record_tunnel_change(session_factory, ic, status)
    except Exception as exc:
        logger.warning("could not record the tunnel state: %s", type(exc).__name__)


def _record_tunnel_change(session_factory, ic: IntegrationContext, status: dict[str, Any]) -> None:
    state = str(status.get("state") or "stopped")
    url = status.get("url") if state == "running" else None
    provider = status.get("provider")
    now = utcnow()
    path = public_url_path(ic.settings)
    with session_factory() as db:
        saved = load_url_state(db)
        new_url = bool(url) and url != saved.get("last_url")
        if new_url:
            stamp = now.isoformat()
            saved = {
                "last_url": url,
                "previous_url": saved.get("last_url"),
                "url_changed_at": stamp,
                "provider": provider,
                "history": [{"url": url, "at": stamp}, *(saved.get("history") or [])][:URL_HISTORY],
            }
            _save_url_state(db, saved)
            owner = db.scalar(
                select(User).where(User.is_local_owner.is_(True)).order_by(User.created_at, User.id)
            )
            for company_id in owner_company_ids(db, owner.id) if owner else []:
                audit.record(
                    db,
                    company_id=company_id,
                    principal=None,
                    action="reachability.tunnel_url_changed",
                    target_type="reachability",
                    target_id=None,
                    summary=f"The public URL is now {url}",
                    data={
                        "url": url,
                        "previous_url": saved["previous_url"],
                        "provider": provider,
                    },
                )
                events.emit(db, company_id, "reachability.updated", {})
            db.commit()
    if new_url:
        _announce(str(url))
    current = _read_public_url_file(path)
    if state == "running" and url:
        payload = {
            "url": url,
            "provider": provider,
            "state": "running",
            "changed_at": saved.get("url_changed_at"),
            "previous_url": saved.get("previous_url"),
        }
    elif current is None and state in ("stopped", "starting"):
        return  # nothing was ever published and nothing is up: there is nothing to retract
    else:
        payload = {
            "url": None,
            "provider": provider or saved.get("provider"),
            "state": state,
            "changed_at": now.isoformat(),
            "previous_url": saved.get("last_url"),
        }
    if (
        current
        and current.get("state") == payload["state"]
        and current.get("url") == payload["url"]
    ):
        return
    try:
        _write_public_url_file(path, payload)
    except OSError as exc:
        logger.warning("could not write %s: %s", path.name, type(exc).__name__)
