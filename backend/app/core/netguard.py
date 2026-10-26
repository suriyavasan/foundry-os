"""SSRF guard for URLs an owner/admin types in (model-provider base URLs, ``detect --base-url``).

The control plane makes HTTP requests on behalf of a human-supplied address. Without a guard an
owner (or anyone who stole an admin session) could make the server call the cloud metadata
service (``169.254.169.254``), an internal admin panel or another machine's loopback. Two layers:

* :func:`check_url` -- scheme, no credentials, then the host is **resolved** and *every* address
  is classified. Never allowed, whatever the caller says: link-local (169.254.0.0/16 and
  fe80::/10, which includes the cloud metadata address), the well-known metadata addresses,
  unspecified (0.0.0.0, ::), multicast, and every other range that is not globally routable
  (reserved, documentation, benchmarking, site-local ...). Loopback needs ``allow_loopback``;
  private ranges (RFC 1918, CGNAT/Tailscale 100.64/10, IPv6 ULA fc00::/7) need
  ``allow_private``. Decimal/hex/octal host spellings (``http://2130706433``) are resolved by
  the system resolver like any other name, so they are judged by what they resolve to.
* :class:`GuardedTransport` -- an ``httpx`` transport that repeats the check **for every
  request** and connects to the address it just validated (the URL's host is replaced by the
  IP; ``Host`` and TLS SNI / certificate verification keep the original name). A name that
  answers with a public address when it is saved and with ``169.254.169.254`` when it is used
  (DNS rebinding) is therefore refused, and there is no second lookup between check and
  connect for an attacker to win.

Policy per provider kind lives in :func:`provider_policy`: local-model kinds may point at this
machine; private-network targets need the owner's explicit opt-in; cloud kinds must resolve to
public addresses. Callers never follow redirects (a redirect would be a new, unchecked target).
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.core.errors import ValidationFailed

ALLOWED_SCHEMES = ("http", "https")
LOCAL_MODEL_KINDS = frozenset({"ollama", "openai_compatible"})

# Always refused, even when private/loopback targets are allowed.
METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(a)
    for a in (
        "169.254.169.254",  # AWS / GCP / Azure / DigitalOcean / OpenStack
        "169.254.170.2",  # AWS ECS task metadata
        "fd00:ec2::254",  # AWS IPv6 metadata (inside ULA space, so it needs its own entry)
        "100.100.100.200",  # Alibaba Cloud (inside CGNAT space)
        "192.0.0.192",  # Oracle Cloud (legacy)
    )
)
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",  # CGNAT; Tailscale hands these out
        "fc00::/7",  # IPv6 unique local
    )
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class UnsafeUrl(ValidationFailed):
    """The URL is not acceptable (422). ``details`` follows the validation-error shape and adds
    ``reason`` (machine readable) and ``can_allow`` (true when the owner's opt-in would permit
    it)."""

    code = "unsafe_url"
    default_message = "That URL is not allowed"

    def __init__(
        self, message: str, *, reason: str, field: str = "base_url", can_allow: bool = False
    ) -> None:
        super().__init__(
            message,
            details=[
                {
                    "loc": ["body", field],
                    "msg": message,
                    "reason": reason,
                    "can_allow": can_allow,
                }
            ],
        )
        self.reason = reason


class BlockedDestination(httpx.ConnectError):
    """A request was refused by the guard before any connection was made."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Policy:
    allow_loopback: bool = False
    allow_private: bool = False


PUBLIC_ONLY = Policy()
LOCAL_MODEL = Policy(allow_loopback=True)


def provider_policy(kind: str, allow_private_network: bool = False) -> Policy:
    """Cloud kinds must resolve to public addresses; ``ollama`` / ``openai_compatible`` may use
    loopback, and private networks only with the owner's explicit opt-in."""
    if kind in LOCAL_MODEL_KINDS:
        return Policy(allow_loopback=True, allow_private=allow_private_network)
    return PUBLIC_ONLY


@dataclass(frozen=True)
class CheckedUrl:
    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]  # validated, resolver order; empty only when resolution was optional


# ------------------------------------------------------------------ classification


def classify_ip(ip: IpAddress | str) -> str:
    """``public`` | ``loopback`` | ``private`` | ``link_local`` | ``metadata`` | ``unspecified`` |
    ``multicast`` | ``reserved``. IPv4-mapped, NAT64 and 6to4 addresses are judged by the IPv4
    address they carry, so ``::ffff:169.254.169.254`` is link-local too."""
    addr = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    if isinstance(addr, ipaddress.IPv6Address):
        embedded = _embedded_v4(addr)
        if embedded is not None:
            inner = classify_ip(embedded)
            if inner != "public":
                return inner
    if addr in METADATA_ADDRESSES:
        return "metadata"
    if addr.is_unspecified:
        return "unspecified"
    if addr.is_multicast:
        return "multicast"
    if addr.is_link_local:
        return "link_local"
    if addr.is_loopback:
        return "loopback"
    if any(addr in net for net in PRIVATE_NETWORKS):
        return "private"
    if not addr.is_global:
        return "reserved"
    return "public"


def _embedded_v4(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    if addr in _NAT64:
        return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    if addr in _SIX_TO_FOUR:
        return ipaddress.IPv4Address((int(addr) >> 80) & 0xFFFFFFFF)
    return None


# ------------------------------------------------------------------ resolution


def system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        ip = str(info[4][0]).split("%", 1)[0]  # drop an IPv6 scope id
        if ip not in seen:
            seen.append(ip)
    return seen


# Tests replace this; production code always goes through ``resolve``.
_resolver: Callable[[str, int], list[str]] = system_resolver


def resolve(host: str, port: int) -> list[str]:
    return _resolver(host, port)


# ------------------------------------------------------------------ the check


def _refusal(cls: str, host: str, ip: str, policy: Policy, field: str) -> UnsafeUrl:
    shown = host if host == ip else f"{host} ({ip})"
    if cls in ("link_local", "metadata"):
        return UnsafeUrl(
            f"{shown} is a link-local or cloud-metadata address; it can never be used",
            reason=cls,
            field=field,
        )
    if cls == "loopback":
        return UnsafeUrl(
            f"{shown} is this machine (loopback); this provider kind must use a public address",
            reason="loopback",
            field=field,
        )
    if cls == "private":
        return UnsafeUrl(
            f"{shown} is on a private network. If this is your own server, turn on "
            "'allow private network' for this provider; otherwise use a public address",
            reason="private_network",
            field=field,
            can_allow=True,
        )
    return UnsafeUrl(f"{shown} is a {cls} address and cannot be used", reason=cls, field=field)


def judge(cls: str, policy: Policy) -> bool:
    """True when an address of this class may be contacted under ``policy``."""
    if cls == "public":
        return True
    if cls == "loopback":
        return policy.allow_loopback
    if cls == "private":
        return policy.allow_private
    return False


def check_url(
    url: str,
    allow_loopback: bool = False,
    allow_private: bool = False,
    *,
    require_resolution: bool = True,
    field: str = "base_url",
) -> CheckedUrl:
    """Validate ``url`` for a server-side request or raise :class:`UnsafeUrl` (422).

    ``require_resolution=False`` (used when a URL is merely *saved*) tolerates a name that does not
    resolve yet; the per-request check in :class:`GuardedTransport` always requires it."""
    policy = Policy(allow_loopback=allow_loopback, allow_private=allow_private)
    try:
        parts = urlsplit(url.strip())
        port = parts.port
        host = parts.hostname
        username, password = parts.username, parts.password
    except ValueError as exc:
        raise UnsafeUrl("not a valid URL", reason="invalid", field=field) from exc
    if parts.scheme not in ALLOWED_SCHEMES or not host:
        raise UnsafeUrl("must be an http(s) URL", reason="scheme", field=field)
    if username or password:
        raise UnsafeUrl(
            "must not contain credentials; use the credential store",
            reason="credentials",
            field=field,
        )
    host = host.rstrip(".")
    port = port or (443 if parts.scheme == "https" else 80)
    try:
        literal = [str(ipaddress.ip_address(host.split("%", 1)[0]))]
    except ValueError:
        literal = []
    addresses = literal
    if not literal:
        try:
            addresses = resolve(host, port)
        except (OSError, UnicodeError) as exc:
            if require_resolution:
                raise UnsafeUrl(
                    f"{host} could not be resolved to an address",
                    reason="unresolvable",
                    field=field,
                ) from exc
            addresses = []
        if require_resolution and not addresses:
            raise UnsafeUrl(
                f"{host} could not be resolved to an address", reason="unresolvable", field=field
            )
    for ip in addresses:
        cls = classify_ip(ip)
        if not judge(cls, policy):
            raise _refusal(cls, host, ip, policy, field)
    return CheckedUrl(url.strip(), parts.scheme, host, port, tuple(addresses))


# ------------------------------------------------------------------ the pinned transport


class GuardedTransport(httpx.BaseTransport):
    """Checks every request's destination and connects to the validated IP (no rebinding window)."""

    def __init__(
        self,
        policy: Policy,
        *,
        inner: httpx.BaseTransport | None = None,
        retries: int = 0,
    ) -> None:
        self.policy = policy
        self._inner = inner or httpx.HTTPTransport(retries=retries)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        original_host = request.url.host
        try:
            checked = check_url(
                str(request.url),
                allow_loopback=self.policy.allow_loopback,
                allow_private=self.policy.allow_private,
            )
        except UnsafeUrl as exc:
            raise BlockedDestination(exc.message, exc.reason) from exc
        # IPv4 first: a service bound to 127.0.0.1 only must not be missed because "localhost"
        # also lists ::1.
        ordered = sorted(checked.addresses, key=lambda a: ipaddress.ip_address(a).version)
        is_literal = _is_ip(original_host)
        last: httpx.ConnectError | httpx.ConnectTimeout | None = None
        for ip in ordered:
            request.url = request.url.copy_with(host=ip)
            if not is_literal:
                request.extensions = {**request.extensions, "sni_hostname": original_host}
            try:
                return self._inner.handle_request(request)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last = exc
        assert last is not None
        raise last

    def close(self) -> None:
        self._inner.close()


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
