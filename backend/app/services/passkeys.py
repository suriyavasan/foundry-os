"""Passkeys (WebAuthn) as a way to sign in, and as strong authentication for the exposure gate.

Design notes:

* The ceremony maths (CBOR, COSE, signatures, RP-id hash, origin) is ``py_webauthn``'s; this module
  owns what that library leaves to the relying party: challenges, RP id / origin policy, rate
  limiting, clone detection, who may add or remove a passkey, and the audit trail.
* Challenges are generated here, stored server-side (``passkey_challenges``), single use (burnt
  before verification, so a failed attempt cannot be retried), valid five minutes, and bound to a
  purpose and, for a registration, to the signed-in user. The client only echoes an ``attempt_id``.
* RP id and expected origins are never taken from the client: they come from the request's Host
  header, which must be loopback (RP id ``localhost``) or match an origin Foundry knows to be its
  own (running tunnel, custom domain, an https ``public_url``, ``COMPANY_OS_PASSKEY_ORIGINS``). The
  origin the browser reports inside ``clientDataJSON`` must then be in that set. A request from any
  other host (for example ``http://192.168.1.20:8700``) gets no WebAuthn at all: browsers refuse
  it there, and so do we.
* A quick tunnel (``*.trycloudflare.com``) is the exception to "a running tunnel": its address
  changes on every restart, which would orphan the passkey while it still counted as strong
  authentication. It gets no WebAuthn (reason ``ephemeral_origin``); a named tunnel does.
* A passkey login is rate limited like a password login (per IP and per credential) and every
  failure is the same generic ``401 invalid_credentials``. ``login_options`` answers a stable decoy
  allow-list for usernames that do not exist (or have no passkey), so the response never says
  whether an account exists.
* Passkeys are human-only: every route sits behind ``account_user`` (agents get 403 and a guardrail
  event) and additionally requires a browser session (not an API token, not the owner token).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json_dict
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core import passwords
from app.core.config import Settings
from app.core.errors import (
    AppError,
    Conflict,
    Forbidden,
    InvalidCredentials,
    NotFound,
    RateLimited,
    ValidationFailed,
)
from app.core.netinfo import is_loopback_address
from app.core.time import utcnow
from app.integrations.tunnel import is_ephemeral_host
from app.models.accounts import AuthSession, Passkey, PasskeyChallenge
from app.models.reachability import CompanyDomain
from app.models.user import User
from app.services.auth import AuthService, LoginOutcome, digest, normalize_username

logger = logging.getLogger("company_os.passkeys")

RP_NAME = "Foundry AI OS"
CHALLENGE_TTL = timedelta(minutes=5)
FRESH_SESSION = timedelta(minutes=10)  # removing a passkey: the session must be this young
MAX_PASSKEYS = 20
MAX_OUTSTANDING_LOGIN_CHALLENGES = 1000
DEFAULT_NAME = "Passkey"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1")
LOOPBACK_RP_ID = "localhost"
KNOWN_TRANSPORTS = frozenset({"usb", "nfc", "ble", "hybrid", "internal", "smart-card", "cable"})
GENERIC_LOGIN_FAILURE = "Passkey sign-in failed"


class PasskeyOriginUnsupported(AppError):
    status_code = 400
    code = "passkey_origin_unsupported"
    default_message = (
        "Passkeys work only over HTTPS or on localhost. Open Foundry AI OS at "
        "http://localhost:<port> on this machine, or through its HTTPS address."
    )


@dataclass(frozen=True)
class RpContext:
    """Relying-party parameters for one request: the RP id and the origins a ceremony may report."""

    rp_id: str
    origins: tuple[str, ...]


# ------------------------------------------------------------------ RP id / origin policy


def _split_host(raw: str) -> tuple[str, str]:
    """(hostname, ":port" or "") from a Host header, lower-cased."""
    raw = raw.strip().lower()
    if raw.startswith("["):  # [::1]:8700
        end = raw.find("]")
        host = raw[: end + 1] if end != -1 else raw
        rest = raw[end + 1 :] if end != -1 else ""
        return host, rest if rest.startswith(":") else ""
    if raw.count(":") == 1:
        host, _, port = raw.partition(":")
        return host, f":{port}"
    return raw, ""


def normalize_origin(value: str) -> str | None:
    """``https://Host:443/path`` -> ``https://host``. None for anything that is not an http(s)
    origin with a host."""
    try:
        parts = urlsplit(value.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host:
        return None
    default = 443 if scheme == "https" else 80
    suffix = f":{port}" if port and port != default else ""
    return f"{scheme}://{host.lower()}{suffix}"


def _origin_host(origin: str) -> str:
    return urlsplit(origin).netloc.lower()


def configured_origins(db: Session, settings: Settings, tunnel_url: str | None = None) -> list[str]:
    """Non-loopback origins this install is known to be served from, for passkeys. https only:
    browsers refuse WebAuthn on any other plain-http origin, so those would never work."""
    raw: list[str] = [*settings.passkey_origins]
    if tunnel_url:
        raw.append(tunnel_url)
    raw.append(settings.public_url)
    for domain in db.scalars(select(CompanyDomain.domain).where(CompanyDomain.domain.is_not(None))):
        if domain:
            raw.append(f"https://{domain}")
    out: list[str] = []
    for value in raw:
        origin = normalize_origin(value)
        if origin is None or not origin.startswith("https://") or origin in out:
            continue
        out.append(origin)
    return out


def ephemeral_origin(
    host_header: str, tunnel_url: str | None = None, tunnel_ephemeral: bool = False
) -> bool:
    """Whether this Host is a quick-tunnel address (its name changes when the tunnel restarts):
    any ``*.trycloudflare.com`` host, or the running tunnel's host when it is a quick tunnel."""
    host, port = _split_host(host_header)
    if not host:
        return False
    if is_ephemeral_host(host):
        return True
    if tunnel_ephemeral and tunnel_url:
        origin = normalize_origin(tunnel_url)
        return bool(origin and _origin_host(origin) == f"{host}{port}")
    return False


def unsupported_reason(
    host_header: str, tunnel_url: str | None = None, tunnel_ephemeral: bool = False
) -> str | None:
    return (
        "ephemeral_origin" if ephemeral_origin(host_header, tunnel_url, tunnel_ephemeral) else None
    )


def resolve_rp(
    db: Session,
    settings: Settings,
    host_header: str,
    tunnel_url: str | None = None,
    tunnel_ephemeral: bool = False,
) -> RpContext | None:
    """The RP context for a request with this Host header, or None when WebAuthn cannot be used
    from there (LAN http, an unknown host name, a quick-tunnel address)."""
    host, port = _split_host(host_header)
    if not host:
        return None
    if ephemeral_origin(host_header, tunnel_url, tunnel_ephemeral):
        return None
    if host in LOOPBACK_HOSTS:
        origins = {f"{scheme}://{host}{port}" for scheme in ("http", "https")}
        for name in LOOPBACK_HOSTS:
            origins.add(f"http://{name}:{settings.port}")
        for extra in settings.cors_origins:  # the Vite dev server (:5173) proxying /api
            origin = normalize_origin(extra)
            if origin and (urlsplit(origin).hostname or "") in LOOPBACK_HOSTS:
                origins.add(origin)
        return RpContext(LOOPBACK_RP_ID, tuple(sorted(origins)))
    if is_loopback_address(host):  # ::1 and friends: no usable RP id
        return None
    for origin in configured_origins(db, settings, tunnel_url):
        if _origin_host(origin) == f"{host}{port}":
            return RpContext(host, (origin,))
    return None


def clean_name(value: str | None) -> str:
    return " ".join((value or "").split())[:80] or DEFAULT_NAME


# ------------------------------------------------------------------ the service


class PasskeyService:
    def __init__(self, auth: AuthService, settings: Settings) -> None:
        self.auth = auth
        self.settings = settings

    # ------------------------------------------------------------ helpers

    def resolve_rp(
        self,
        db: Session,
        host_header: str,
        tunnel_url: str | None = None,
        tunnel_ephemeral: bool = False,
    ) -> RpContext | None:
        return resolve_rp(db, self.settings, host_header, tunnel_url, tunnel_ephemeral)

    @staticmethod
    def require_rp(ctx: RpContext | None) -> RpContext:
        if ctx is None:
            raise PasskeyOriginUnsupported()
        return ctx

    def user_handle(self, user: User) -> bytes:
        """A stable, opaque WebAuthn user handle (no personal data, not the user id itself)."""
        return hmac_digest(self.auth.keys.get(), b"passkey-user:" + user.id.encode())[:32]

    def _decoys(self, name: str) -> list[PublicKeyCredentialDescriptor]:
        """Stable fake credential ids for a username that has no passkey, so the allow-list looks
        like a real user's. Same name -> same ids, forever."""
        key = self.auth.keys.get()
        count = 1 + hmac_digest(key, b"passkey-decoy-count:" + name.encode())[0] % 2
        return [
            PublicKeyCredentialDescriptor(
                id=hmac_digest(key, b"passkey-decoy:%d:" % i + name.encode())[:32]
            )
            for i in range(count)
        ]

    def count_for(self, db: Session, user_id: str) -> int:
        return int(
            db.scalar(select(func.count()).select_from(Passkey).where(Passkey.user_id == user_id))
            or 0
        )

    def list_for(self, db: Session, user_id: str) -> list[Passkey]:
        return list(
            db.scalars(
                select(Passkey)
                .where(Passkey.user_id == user_id)
                .order_by(Passkey.created_at, Passkey.id)
            )
        )

    # ------------------------------------------------------------ challenges

    def _new_challenge(
        self,
        db: Session,
        *,
        purpose: str,
        user_id: str | None,
        rp_id: str,
        name: str | None,
        now: datetime,
    ) -> tuple[PasskeyChallenge, bytes]:
        db.execute(
            delete(PasskeyChallenge).where(PasskeyChallenge.expires_at < now - timedelta(hours=1))
        )
        if purpose == "login":
            outstanding = db.scalar(
                select(func.count())
                .select_from(PasskeyChallenge)
                .where(
                    PasskeyChallenge.purpose == "login",
                    PasskeyChallenge.consumed_at.is_(None),
                    PasskeyChallenge.expires_at > now,
                )
            )
            if (outstanding or 0) >= MAX_OUTSTANDING_LOGIN_CHALLENGES:
                raise RateLimited(
                    "Too many sign-in attempts in progress. Try again shortly.",
                    details={"reason": "too_many_attempts", "retry_after_seconds": 30},
                )
        else:  # a new registration supersedes this person's earlier unfinished one
            db.execute(
                delete(PasskeyChallenge).where(
                    PasskeyChallenge.user_id == user_id,
                    PasskeyChallenge.purpose == purpose,
                    PasskeyChallenge.consumed_at.is_(None),
                )
            )
        raw = secrets.token_bytes(32)
        row = PasskeyChallenge(
            purpose=purpose,
            user_id=user_id,
            challenge=bytes_to_base64url(raw),
            rp_id=rp_id,
            name=name,
            created_at=now,
            expires_at=now + CHALLENGE_TTL,
        )
        db.add(row)
        db.flush()
        return row, raw

    @staticmethod
    def _consume(
        db: Session, attempt_id: str, purpose: str, user_id: str | None, now: datetime
    ) -> tuple[PasskeyChallenge | None, str | None]:
        """Burn the challenge (committed immediately: a failed verification cannot be retried).
        Returns (row, None) or (None, reason) with reason unknown | used | expired."""
        row = db.get(PasskeyChallenge, attempt_id[:64]) if attempt_id else None
        if (
            row is None
            or row.purpose != purpose
            or (purpose == "register" and row.user_id != user_id)
        ):
            return None, "unknown"
        expired = row.expires_at <= now
        claimed = db.execute(
            update(PasskeyChallenge)
            .where(
                PasskeyChallenge.id == row.id,
                PasskeyChallenge.consumed_at.is_(None),
                PasskeyChallenge.expires_at > now,
            )
            .values(consumed_at=now)
        )
        db.commit()
        if claimed.rowcount != 1:
            return None, "expired" if expired else "used"
        db.refresh(row)
        return row, None

    # ------------------------------------------------------------ registration

    def registration_options(
        self,
        db: Session,
        user: User,
        ctx: RpContext | None,
        *,
        name: str | None,
        now: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        now = now or utcnow()
        ctx = self.require_rp(ctx)
        if self.count_for(db, user.id) >= MAX_PASSKEYS:
            raise Conflict(f"At most {MAX_PASSKEYS} passkeys; remove one first")
        row, raw = self._new_challenge(
            db,
            purpose="register",
            user_id=user.id,
            rp_id=ctx.rp_id,
            name=clean_name(name) if name else None,
            now=now,
        )
        options = generate_registration_options(
            rp_id=ctx.rp_id,
            rp_name=RP_NAME,
            user_name=user.username or user.display_name,
            user_display_name=user.display_name,
            user_id=self.user_handle(user),
            challenge=raw,
            exclude_credentials=[
                PublicKeyCredentialDescriptor(
                    id=base64url_to_bytes(p.credential_id),
                    transports=_transports_enum(p.transports),
                )
                for p in self.list_for(db, user.id)
            ],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )
        db.commit()
        return row.id, options_to_json_dict(options)

    def registration_verify(
        self,
        db: Session,
        user: User,
        ctx: RpContext | None,
        *,
        attempt_id: str,
        name: str | None,
        credential: dict[str, Any],
        now: datetime | None = None,
    ) -> Passkey:
        now = now or utcnow()
        challenge, reason = self._consume(db, attempt_id, "register", user.id, now)
        if challenge is None:
            raise ValidationFailed(
                "That passkey request has expired. Start again."
                if reason in ("expired", "used")
                else "Unknown passkey request. Start again.",
                details={"reason": f"challenge_{reason}"},
            )
        if ctx is None or ctx.rp_id != challenge.rp_id:
            raise ValidationFailed(
                "This address cannot be used to add a passkey.",
                details={"reason": "origin_mismatch"},
            )
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge.challenge),
                expected_rp_id=ctx.rp_id,
                expected_origin=list(ctx.origins),
                require_user_presence=True,
            )
        except Exception as exc:  # parse errors and InvalidRegistrationResponse alike
            logger.info("passkey registration rejected: %s", type(exc).__name__)
            raise ValidationFailed(
                "The passkey could not be verified.", details={"reason": "verification_failed"}
            ) from exc
        credential_id = bytes_to_base64url(verified.credential_id)
        if db.scalar(select(Passkey.id).where(Passkey.credential_id == credential_id)):
            raise Conflict("That passkey is already registered")
        if self.count_for(db, user.id) >= MAX_PASSKEYS:
            raise Conflict(f"At most {MAX_PASSKEYS} passkeys; remove one first")
        response = credential.get("response")
        reported = response.get("transports") if isinstance(response, dict) else None
        transports = sorted(
            {t for t in (reported if isinstance(reported, list) else []) if t in KNOWN_TRANSPORTS}
        )
        passkey = Passkey(
            user_id=user.id,
            credential_id=credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            transports=transports,
            aaguid=verified.aaguid,
            name=clean_name(name or challenge.name),
            backed_up=bool(verified.credential_backed_up),
            created_at=now,
        )
        db.add(passkey)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise Conflict("That passkey is already registered") from exc
        self.auth._audit_user(
            db,
            user,
            "auth.passkey_added",
            f"Passkey '{passkey.name}' added",
            # the relying party the passkey is bound to: the exposure gate ignores quick-tunnel ones
            {"passkey_id": passkey.id, "rp_id": ctx.rp_id},
        )
        db.commit()
        return passkey

    # ------------------------------------------------------------ manage

    def _own(self, db: Session, user: User, passkey_id: str) -> Passkey:
        passkey = db.scalar(
            select(Passkey).where(Passkey.id == passkey_id[:64], Passkey.user_id == user.id)
        )
        if passkey is None:
            raise NotFound("Passkey not found")
        return passkey

    def rename(self, db: Session, user: User, passkey_id: str, name: str) -> Passkey:
        passkey = self._own(db, user, passkey_id)
        passkey.name = clean_name(name)
        self.auth._audit_user(
            db,
            user,
            "auth.passkey_renamed",
            f"Passkey renamed to '{passkey.name}'",
            {"passkey_id": passkey.id},
        )
        db.commit()
        return passkey

    def remove(
        self,
        db: Session,
        user: User,
        passkey_id: str,
        *,
        session: AuthSession | None,
        password: str | None,
        now: datetime | None = None,
    ) -> None:
        now = now or utcnow()
        passkey = self._own(db, user, passkey_id)
        if not user.password_hash and self.count_for(db, user.id) <= 1:
            raise Conflict(
                "This is your only way to sign in. Set a password before removing it.",
                details={"reason": "last_sign_in_method"},
            )
        fresh = session is not None and now - session.created_at <= FRESH_SESSION
        if not fresh:
            if not password or not user.password_hash:
                raise Forbidden(
                    "Confirm with your password, or sign in again, to remove a passkey.",
                    details={"reason": "reauthentication_required"},
                )
            keys = [f"pw:{user.id}"]
            self.auth.assert_not_locked(db, keys, now)
            if not passwords.verify_password(self.auth.hasher, user.password_hash, password):
                self.auth.record_failure(db, keys, now)
                raise InvalidCredentials(
                    "The password is not correct", details={"reason": "invalid_credentials"}
                )
            self.auth.clear_failures(db, keys)
        db.delete(passkey)
        self.auth._audit_user(
            db,
            user,
            "auth.passkey_removed",
            f"Passkey '{passkey.name}' removed",
            {"passkey_id": passkey.id},
        )
        db.commit()

    # ------------------------------------------------------------ login

    def login_options(
        self,
        db: Session,
        ctx: RpContext | None,
        *,
        username: str | None,
        now: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        now = now or utcnow()
        ctx = self.require_rp(ctx)
        name = normalize_username(username)[:64] if username else ""
        allow: list[PublicKeyCredentialDescriptor] = []
        if name:
            user = db.scalar(select(User).where(User.username == name))
            real = self.list_for(db, user.id) if user else []
            allow = (
                [
                    PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id))
                    for p in real
                ]
                if real
                else self._decoys(name)
            )
        row, raw = self._new_challenge(
            db, purpose="login", user_id=None, rp_id=ctx.rp_id, name=None, now=now
        )
        options = generate_authentication_options(
            rp_id=ctx.rp_id,
            challenge=raw,
            allow_credentials=allow or None,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        db.commit()
        return row.id, options_to_json_dict(options)

    def login_verify(
        self,
        db: Session,
        ctx: RpContext | None,
        *,
        attempt_id: str,
        credential: dict[str, Any],
        ip: str | None,
        user_agent: str | None,
        now: datetime | None = None,
    ) -> LoginOutcome:
        now = now or utcnow()
        raw_id = credential.get("id")
        credential_id = raw_id if isinstance(raw_id, str) and len(raw_id) <= 1400 else ""
        keys = [f"pk:{digest(credential_id)[:40]}", f"ip:{ip or 'unknown'}"]
        self.auth.assert_not_locked(db, keys, now)

        def fail() -> InvalidCredentials:
            self.auth.record_failure(db, keys, now)
            return InvalidCredentials(
                GENERIC_LOGIN_FAILURE, details={"reason": "invalid_credentials"}
            )

        challenge, _reason = self._consume(db, attempt_id, "login", None, now)
        if challenge is None or ctx is None or ctx.rp_id != challenge.rp_id:
            raise fail()
        passkey = (
            db.scalar(select(Passkey).where(Passkey.credential_id == credential_id))
            if credential_id
            else None
        )
        user = db.get(User, passkey.user_id) if passkey else None
        if passkey is None or user is None or not user.username:
            raise fail()
        response = credential.get("response")
        handle = response.get("userHandle") if isinstance(response, dict) else None
        if handle and handle != bytes_to_base64url(self.user_handle(user)):
            raise fail()
        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge.challenge),
                expected_rp_id=ctx.rp_id,
                expected_origin=list(ctx.origins),
                credential_public_key=passkey.public_key,
                # the counter policy is applied below, after the signature is known to be genuine
                credential_current_sign_count=0,
            )
        except Exception as exc:
            logger.info("passkey login rejected: %s", type(exc).__name__)
            raise fail() from exc
        if passkey.sign_count > 0 and verified.new_sign_count <= passkey.sign_count:
            # A genuine signature with a counter that did not advance: a cloned authenticator.
            self.auth._audit_user(
                db,
                user,
                "auth.passkey_clone_suspected",
                f"Passkey '{passkey.name}' presented a stale signature counter; sign-in refused",
                {"passkey_id": passkey.id},
            )
            raise fail()
        passkey.sign_count = verified.new_sign_count
        passkey.last_used_at = now
        passkey.backed_up = bool(verified.credential_backed_up)
        self.auth.clear_failures(db, [keys[0]])
        session, raw = self.auth.create_session(db, user, ip=ip, user_agent=user_agent, now=now)
        db.commit()
        return LoginOutcome(user, session, raw)


def hmac_digest(key: bytes, message: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha256).digest()


def _transports_enum(values: list[str] | None) -> list[AuthenticatorTransport] | None:
    out = []
    for value in values or []:
        try:
            out.append(AuthenticatorTransport(value))
        except ValueError:
            continue
    return out or None
