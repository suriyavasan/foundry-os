"""Accounts and authentication (M7): setup, login, sessions, CSRF, API tokens, 2FA, passwords.

Design notes (docs/specs/m7 section 2, architecture section 7):

* Passwords are Argon2id (``core.passwords``); a hash weaker than the current settings is upgraded
  at the next successful login.
* Session cookies and API tokens carry 256-bit random secrets; only their SHA-256 is stored, so a
  database leak yields nothing replayable. A session slides (14 days idle) up to 30 days.
* CSRF is a *signed* double submit: ``cos_csrf`` = HMAC(server key, session hash); the header must
  equal both the cookie and the value derived from the session, so a cookie planted from a sibling
  origin does not help an attacker. Bearer authentication has no CSRF surface and is exempt.
* Login is throttled per username and per IP with exponential backoff; unknown usernames are
  tracked and verified against a dummy hash so neither the response nor its timing says whether an
  account exists.
* Agents never reach any of this: every entry point here is called from human-only routes.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

import pyotp
from argon2 import PasswordHasher
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import passwords
from app.core.config import Settings
from app.core.crypto import CredentialCipher
from app.core.errors import (
    Conflict,
    InvalidCredentials,
    InvalidSetupToken,
    NotFound,
    RateLimited,
    SetupComplete,
    SetupTokenRequired,
    ValidationFailed,
)
from app.core.keys import SecretKeyProvider
from app.core.owner_token import OwnerTokenProvider
from app.core.principal import Principal, PrincipalType
from app.core.time import utcnow
from app.models.accounts import ApiToken, AuthSession, LoginThrottle
from app.models.company import CompanyMember
from app.models.enums import MemberStatus, TokenScope
from app.models.user import User
from app.services import audit

SESSION_COOKIE = "cos_session"
CSRF_COOKIE = "cos_csrf"
CSRF_HEADER = "x-csrf-token"
API_TOKEN_PREFIX = "cos_pat_"
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,255}\.[^\s@]{2,63}$")
TOUCH_INTERVAL = timedelta(
    seconds=60
)  # last_seen / sliding-expiry writes are at most this frequent
THROTTLE_RESET_AFTER = timedelta(hours=1)
IP_FREE_FACTOR = 3  # per-IP allowance = factor x the per-username one (shared addresses, NAT)
RECOVERY_CODE_COUNT = 10
MAX_ACTIVE_TOKENS = 25
TOTP_INTERVAL = 30


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def normalize_username(value: str) -> str:
    return value.strip().lower()


def validate_username(value: str) -> str:
    username = normalize_username(value)
    if not USERNAME_RE.match(username):
        raise ValidationFailed(
            "Username: 3-32 characters of lowercase letters, digits, dots, dashes, underscores",
            details=[{"loc": ["body", "username"], "msg": "invalid username"}],
        )
    return username


def normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    email = value.strip().lower()
    if not email:
        return None
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise ValidationFailed(
            "Enter a valid email address",
            details=[{"loc": ["body", "email"], "msg": "invalid email address"}],
        )
    return email


@dataclass(frozen=True)
class LoginOutcome:
    user: User
    session: AuthSession | None  # None while the second factor is still owed
    session_token: str | None
    two_factor_required: bool = False


class AuthService:
    """Stateless apart from the shared hasher; one instance per app (``app.state.auth``)."""

    def __init__(
        self,
        settings: Settings,
        keys: SecretKeyProvider,
        cipher: CredentialCipher,
        owner_token: OwnerTokenProvider,
    ) -> None:
        self.settings = settings
        self.keys = keys
        self.cipher = cipher
        self.owner_token = owner_token
        self.hasher: PasswordHasher = passwords.build_hasher(settings)
        self._dummy: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers

    def _dummy_hash(self) -> str:
        with self._lock:
            if self._dummy is None:
                self._dummy = passwords.hash_password(self.hasher, secrets.token_urlsafe(16))
            return self._dummy

    def hash_password(self, password: str) -> str:
        return passwords.hash_password(self.hasher, password)

    def check_new_password(self, password: str, user: User | None = None, **personal: str | None):
        verdict = passwords.check_password_strength(
            password,
            personal=(
                personal.get("username") or (user.username if user else None),
                personal.get("display_name") or (user.display_name if user else None),
                personal.get("email") or (user.email if user else None),
            ),
        )
        if not verdict.ok:
            raise ValidationFailed(
                verdict.message or "Password is too weak",
                details=[{"loc": ["body", "password"], "msg": verdict.message or "too weak"}],
            )

    def principal_for(self, user: User) -> Principal:
        return Principal(type=PrincipalType.HUMAN, id=user.id, label=user.display_name)

    def _audit_user(
        self, db: Session, user: User, action: str, summary: str, data: dict | None = None
    ) -> None:
        """Account events have no company of their own: they go to the activity feed of each company
        the person belongs to. (No secrets: only what happened.)"""
        company_ids = db.scalars(
            select(CompanyMember.company_id).where(
                CompanyMember.user_id == user.id, CompanyMember.status == MemberStatus.ACTIVE
            )
        )
        for company_id in list(company_ids):
            audit.record(
                db,
                company_id=company_id,
                principal=self.principal_for(user),
                action=action,
                target_type="user",
                target_id=user.id,
                summary=summary,
                data=data,
            )

    # ------------------------------------------------------------------ setup state

    def local_owner(self, db: Session) -> User | None:
        return db.scalar(
            select(User).where(User.is_local_owner.is_(True)).order_by(User.created_at, User.id)
        )

    def setup_pending(self, db: Session) -> bool:
        """True while the placeholder owner has no credentials: the install is still unclaimed."""
        owner = self.local_owner(db)
        if owner is None:
            return not db.scalar(select(User.id).where(User.password_hash.is_not(None)).limit(1))
        return owner.password_hash is None

    # ------------------------------------------------------------------ throttle

    def _free_for(self, key: str) -> int:
        free = self.settings.login_free_attempts
        return free * IP_FREE_FACTOR if key.startswith(("ip:", "setup:")) else free

    def assert_not_locked(self, db: Session, keys: list[str], now: datetime) -> None:
        wait = 0.0
        for key in keys:
            row = db.get(LoginThrottle, key)
            if row is not None and row.locked_until is not None and row.locked_until > now:
                wait = max(wait, (row.locked_until - now).total_seconds())
        if wait > 0:
            raise RateLimited(
                "Too many sign-in attempts. Try again later.",
                details={
                    "reason": "too_many_attempts",
                    "retry_after_seconds": max(1, int(wait + 0.5)),
                },
            )

    def record_failure(self, db: Session, keys: list[str], now: datetime) -> None:
        # keep the table small: unknown usernames are tracked too, so old rows are dropped
        db.execute(
            delete(LoginThrottle).where(
                LoginThrottle.last_failure_at < now - 24 * THROTTLE_RESET_AFTER,
                LoginThrottle.key.notin_(keys),
            )
        )
        for key in keys:
            row = db.get(LoginThrottle, key)
            if row is None:
                row = LoginThrottle(key=key, failures=0)
                db.add(row)
            if row.last_failure_at is not None and now - row.last_failure_at > THROTTLE_RESET_AFTER:
                row.failures = 0
            row.failures += 1
            row.last_failure_at = now
            over = row.failures - self._free_for(key)
            if over > 0:
                seconds = min(self.settings.login_max_lockout_seconds, 2 ** min(over, 20))
                row.locked_until = now + timedelta(seconds=seconds)
        db.commit()

    def clear_failures(self, db: Session, keys: list[str]) -> None:
        db.execute(delete(LoginThrottle).where(LoginThrottle.key.in_(keys)))

    # ------------------------------------------------------------------ sessions

    def create_session(
        self, db: Session, user: User, *, ip: str | None, user_agent: str | None, now: datetime
    ) -> tuple[AuthSession, str]:
        raw = secrets.token_urlsafe(32)  # 256 bits
        session = AuthSession(
            token_hash=digest(raw),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=self.settings.session_idle_days),
            absolute_expires_at=now + timedelta(days=self.settings.session_absolute_days),
            ip=(ip or None) and ip[:64],
            user_agent=(user_agent or None) and user_agent[:300],
        )
        db.add(session)
        db.flush()
        return session, raw

    def resolve_session(
        self, db: Session, raw: str | None, now: datetime, *, touch: bool = True
    ) -> tuple[AuthSession, User] | None:
        if not raw or len(raw) > 128:
            return None
        session = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(raw)))
        if (
            session is None
            or session.revoked_at is not None
            or now >= session.expires_at
            or now >= session.absolute_expires_at
        ):
            return None
        user = db.get(User, session.user_id)
        if user is None:
            return None
        if touch and (session.last_seen_at is None or now - session.last_seen_at >= TOUCH_INTERVAL):
            session.last_seen_at = now
            session.expires_at = min(
                now + timedelta(days=self.settings.session_idle_days), session.absolute_expires_at
            )
            db.commit()
        return session, user

    def revoke_session(self, db: Session, session: AuthSession, now: datetime) -> None:
        if session.revoked_at is None:
            session.revoked_at = now

    def revoke_user_sessions(
        self, db: Session, user_id: str, now: datetime, *, except_id: str | None = None
    ) -> int:
        stmt = (
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        if except_id:
            stmt = stmt.where(AuthSession.id != except_id)
        return int(db.execute(stmt).rowcount or 0)

    def list_sessions(self, db: Session, user_id: str, now: datetime) -> list[AuthSession]:
        return list(
            db.scalars(
                select(AuthSession)
                .where(
                    AuthSession.user_id == user_id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > now,
                    AuthSession.absolute_expires_at > now,
                )
                .order_by(AuthSession.created_at.desc(), AuthSession.id.desc())
            )
        )

    def revoke_one(self, db: Session, user: User, session_id: str, now: datetime) -> None:
        session = db.scalar(
            select(AuthSession).where(AuthSession.id == session_id, AuthSession.user_id == user.id)
        )
        if session is None or session.revoked_at is not None:
            raise NotFound("Session not found")
        session.revoked_at = now
        self._audit_user(db, user, "auth.session_revoked", "A session was revoked")
        db.commit()

    def revoke_everywhere(self, db: Session, user: User, now: datetime) -> int:
        count = self.revoke_user_sessions(db, user.id, now)
        self._audit_user(
            db, user, "auth.sessions_revoked", "Signed out everywhere", {"count": count}
        )
        db.commit()
        return count

    # ------------------------------------------------------------------ CSRF

    def csrf_token(self, session: AuthSession) -> str:
        return hmac.new(
            self.keys.get(), b"csrf:" + session.token_hash.encode(), hashlib.sha256
        ).hexdigest()

    def csrf_ok(self, session: AuthSession, header: str | None, cookie: str | None) -> bool:
        if not header or not cookie:
            return False
        expected = self.csrf_token(session)
        return hmac.compare_digest(header, cookie) and hmac.compare_digest(header, expected)

    # ------------------------------------------------------------------ setup

    def setup(
        self,
        db: Session,
        *,
        username: str,
        display_name: str,
        password: str,
        email: str | None,
        setup_token: str | None,
        loopback: bool,
        owner_credential: bool,
        ip: str | None,
        user_agent: str | None,
        now: datetime | None = None,
    ) -> tuple[User, AuthSession, str]:
        """First run: claim the placeholder owner (keeping its memberships and company) or, on a
        database without one, create the first user. Needs the setup token unless the request is
        from loopback or already carries the owner credential (which *is* that token)."""
        now = now or utcnow()
        if not self.setup_pending(db):
            raise SetupComplete()
        throttle_keys = [f"setup:{ip or 'unknown'}"]
        if not (loopback or owner_credential):
            self.assert_not_locked(db, throttle_keys, now)
            if not setup_token:
                raise SetupTokenRequired()
            if not self.owner_token.verify(setup_token):
                self.record_failure(db, throttle_keys, now)
                raise InvalidSetupToken()
        username = validate_username(username)
        email = normalize_email(email)
        display_name = display_name.strip()
        if not display_name:
            raise ValidationFailed(
                "Enter a display name",
                details=[{"loc": ["body", "display_name"], "msg": "required"}],
            )
        self.check_new_password(password, username=username, display_name=display_name, email=email)

        hashed = self.hash_password(password)
        user = self.local_owner(db)
        if user is None:
            user = User(display_name=display_name, is_local_owner=True)
            db.add(user)
            db.flush()
        self._check_identity_free(db, username, email, exclude_user_id=user.id)
        # Atomic claim: if a concurrent setup won the race this updates nothing and we stop.
        claimed = db.execute(
            update(User)
            .where(User.id == user.id, User.password_hash.is_(None))
            .values(password_hash=hashed)
        )
        if claimed.rowcount != 1:
            db.rollback()
            raise SetupComplete()
        db.refresh(user)
        user.username = username
        user.display_name = display_name
        user.email = email
        user.password_changed_at = now
        user.updated_at = now
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise Conflict("That username or email is already in use") from exc
        session, raw = self.create_session(db, user, ip=ip, user_agent=user_agent, now=now)
        self._audit_user(db, user, "account.setup", f"Account {username} was set up")
        db.commit()
        return user, session, raw

    def _check_identity_free(
        self, db: Session, username: str, email: str | None, *, exclude_user_id: str | None
    ) -> None:
        taken = db.scalar(
            select(User.id).where(User.username == username, User.id != (exclude_user_id or ""))
        )
        if taken:
            raise Conflict(
                "That username is already taken",
                details=[{"loc": ["body", "username"], "msg": "already taken"}],
            )
        if email:
            used = db.scalar(
                select(User.id).where(User.email == email, User.id != (exclude_user_id or ""))
            )
            if used:
                raise Conflict(
                    "That email address is already in use",
                    details=[{"loc": ["body", "email"], "msg": "already in use"}],
                )

    def create_user(
        self,
        db: Session,
        *,
        username: str,
        display_name: str,
        password: str,
        email: str | None,
        now: datetime,
    ) -> User:
        """A new account (invitation accept). No commit."""
        username = validate_username(username)
        display_name = display_name.strip()
        if not display_name:
            raise ValidationFailed(
                "Enter a display name",
                details=[{"loc": ["body", "display_name"], "msg": "required"}],
            )
        self.check_new_password(password, username=username, display_name=display_name, email=email)
        self._check_identity_free(db, username, email, exclude_user_id=None)
        user = User(
            username=username,
            display_name=display_name,
            email=email,
            password_hash=self.hash_password(password),
            password_changed_at=now,
        )
        db.add(user)
        db.flush()
        return user

    # ------------------------------------------------------------------ login

    def login(
        self,
        db: Session,
        *,
        username: str,
        password: str,
        totp_code: str | None,
        ip: str | None,
        user_agent: str | None,
        now: datetime | None = None,
    ) -> LoginOutcome:
        now = now or utcnow()
        name = normalize_username(username)[:64]
        keys = [f"user:{name}", f"ip:{ip or 'unknown'}"]
        self.assert_not_locked(db, keys, now)
        user = db.scalar(select(User).where(User.username == name)) if name else None
        # Always run one Argon2 verification so timing does not reveal whether the user exists.
        stored = user.password_hash if user and user.password_hash else self._dummy_hash()
        password_ok = passwords.verify_password(self.hasher, stored, password)
        if user is None or not user.password_hash or not password_ok:
            self.record_failure(db, keys, now)
            raise InvalidCredentials(details={"reason": "invalid_credentials"})
        if user.totp_enabled:
            if not totp_code:
                return LoginOutcome(user, None, None, two_factor_required=True)
            if not self._consume_second_factor(db, user, totp_code, now):
                self.record_failure(db, keys, now)
                raise InvalidCredentials(
                    "That code was not accepted", details={"reason": "invalid_code"}
                )
        self.clear_failures(db, [keys[0]])
        if passwords.needs_rehash(self.hasher, user.password_hash):
            user.password_hash = self.hash_password(password)
            user.password_changed_at = user.password_changed_at or now
        session, raw = self.create_session(db, user, ip=ip, user_agent=user_agent, now=now)
        db.commit()
        return LoginOutcome(user, session, raw)

    # ------------------------------------------------------------------ password

    def change_password(
        self,
        db: Session,
        user: User,
        *,
        current_password: str,
        new_password: str,
        keep_session_id: str | None,
        now: datetime | None = None,
    ) -> int:
        now = now or utcnow()
        keys = [f"pw:{user.id}"]
        self.assert_not_locked(db, keys, now)
        if not passwords.verify_password(self.hasher, user.password_hash, current_password):
            self.record_failure(db, keys, now)
            raise InvalidCredentials(
                "The current password is not correct", details={"reason": "invalid_credentials"}
            )
        self.check_new_password(new_password, user)
        if passwords.verify_password(self.hasher, user.password_hash, new_password):
            raise ValidationFailed(
                "Choose a password you have not used before",
                details=[{"loc": ["body", "new_password"], "msg": "same as the current password"}],
            )
        self.clear_failures(db, keys)
        user.password_hash = self.hash_password(new_password)
        user.password_changed_at = now
        user.updated_at = now
        # A password change signs every other device out.
        revoked = self.revoke_user_sessions(db, user.id, now, except_id=keep_session_id)
        self._audit_user(
            db, user, "auth.password_changed", "Password changed", {"sessions_revoked": revoked}
        )
        db.commit()
        return revoked

    # ------------------------------------------------------------------ TOTP 2FA

    def _totp(self, user: User) -> pyotp.TOTP | None:
        if not user.totp_secret_enc:
            return None
        return pyotp.TOTP(self.cipher.decrypt(user.totp_secret_enc), interval=TOTP_INTERVAL)

    def _match_totp(self, user: User, code: str, now: datetime) -> int | None:
        """The accepted time-step for ``code`` (now +-1 step), or None. A step is usable once."""
        totp = self._totp(user)
        digits = re.sub(r"\s+", "", code)
        if totp is None or not re.fullmatch(r"\d{6}", digits):
            return None
        current = int(now.timestamp()) // TOTP_INTERVAL
        for step in (current, current - 1, current + 1):
            expected = totp.at(step * TOTP_INTERVAL)
            if hmac.compare_digest(expected, digits):
                if user.totp_last_step is not None and step <= user.totp_last_step:
                    return None  # replay
                return step
        return None

    @staticmethod
    def _recovery_digest(code: str) -> str:
        return digest(re.sub(r"[\s-]+", "", code).lower())

    def _consume_second_factor(self, db: Session, user: User, code: str, now: datetime) -> bool:
        step = self._match_totp(user, code, now)
        if step is not None:
            user.totp_last_step = step
            return True
        candidate = self._recovery_digest(code)
        for stored in list(user.recovery_code_hashes or []):
            if hmac.compare_digest(stored, candidate):
                user.recovery_code_hashes = [h for h in user.recovery_code_hashes if h != stored]
                self._audit_user(
                    db, user, "auth.recovery_code_used", "A 2FA recovery code was used"
                )
                return True
        return False

    def twofa_enroll(self, db: Session, user: User) -> tuple[str, str]:
        if user.totp_enabled:
            raise Conflict("Two-factor authentication is already enabled")
        secret = pyotp.random_base32()
        user.totp_secret_enc = self.cipher.encrypt(secret)
        user.totp_last_step = None
        db.commit()
        uri = pyotp.TOTP(secret, interval=TOTP_INTERVAL).provisioning_uri(
            name=user.username or user.display_name, issuer_name="Company OS"
        )
        return secret, uri

    def twofa_verify(
        self, db: Session, user: User, code: str, now: datetime | None = None
    ) -> list[str] | None:
        """Confirm enrolment. Returns the recovery codes (shown once) when this enabled 2FA."""
        now = now or utcnow()
        keys = [f"2fa:{user.id}"]
        self.assert_not_locked(db, keys, now)
        if not user.totp_secret_enc:
            raise Conflict("Start enrolment first (POST /auth/2fa/enroll)")
        step = self._match_totp(user, code, now)
        if step is None:
            self.record_failure(db, keys, now)
            raise InvalidCredentials(
                "That code was not accepted", details={"reason": "invalid_code"}
            )
        self.clear_failures(db, keys)
        if user.totp_enabled:
            user.totp_last_step = step
            db.commit()
            return None
        codes = [
            "-".join((c[:5], c[5:]))
            for c in (secrets.token_hex(5) for _ in range(RECOVERY_CODE_COUNT))
        ]
        user.recovery_code_hashes = [self._recovery_digest(c) for c in codes]
        user.totp_enabled = True
        user.totp_last_step = step
        self._audit_user(db, user, "auth.2fa_enabled", "Two-factor authentication enabled")
        db.commit()
        return codes

    def twofa_disable(
        self, db: Session, user: User, *, code: str, password: str, now: datetime | None = None
    ) -> None:
        now = now or utcnow()
        keys = [f"2fa:{user.id}"]
        self.assert_not_locked(db, keys, now)
        if not user.totp_enabled:
            raise Conflict("Two-factor authentication is not enabled")
        if not passwords.verify_password(self.hasher, user.password_hash, password):
            self.record_failure(db, keys, now)
            raise InvalidCredentials(
                "The password is not correct", details={"reason": "invalid_credentials"}
            )
        if not self._consume_second_factor(db, user, code, now):
            self.record_failure(db, keys, now)
            raise InvalidCredentials(
                "That code was not accepted", details={"reason": "invalid_code"}
            )
        self.clear_failures(db, keys)
        user.totp_enabled = False
        user.totp_secret_enc = None
        user.totp_last_step = None
        user.recovery_code_hashes = []
        self._audit_user(db, user, "auth.2fa_disabled", "Two-factor authentication disabled")
        db.commit()

    # ------------------------------------------------------------------ API tokens

    def create_token(
        self, db: Session, user: User, *, name: str, scope: str, now: datetime | None = None
    ) -> tuple[ApiToken, str]:
        now = now or utcnow()
        name = name.strip()
        if not name:
            raise ValidationFailed(
                "Give the token a name",
                details=[{"loc": ["body", "name"], "msg": "required"}],
            )
        active = db.scalars(
            select(ApiToken.id).where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
        ).all()
        if len(active) >= MAX_ACTIVE_TOKENS:
            raise Conflict(f"At most {MAX_ACTIVE_TOKENS} active tokens; revoke one first")
        raw = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
        token = ApiToken(
            user_id=user.id,
            name=name[:80],
            scope=TokenScope(scope).value,
            prefix=raw[:12],
            token_hash=digest(raw),
            created_at=now,
        )
        db.add(token)
        db.flush()
        self._audit_user(
            db,
            user,
            "auth.token_created",
            f"API token '{token.name}' created ({token.scope})",
            {"token_id": token.id, "scope": token.scope},
        )
        db.commit()
        return token, raw

    def list_tokens(self, db: Session, user_id: str) -> list[ApiToken]:
        return list(
            db.scalars(
                select(ApiToken)
                .where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
                .order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
            )
        )

    def revoke_token(self, db: Session, user: User, token_id: str, now: datetime) -> None:
        token = db.scalar(
            select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id)
        )
        if token is None or token.revoked_at is not None:
            raise NotFound("Token not found")
        token.revoked_at = now
        self._audit_user(
            db,
            user,
            "auth.token_revoked",
            f"API token '{token.name}' revoked",
            {"token_id": token.id},
        )
        db.commit()

    def resolve_token(self, db: Session, raw: str, now: datetime) -> tuple[ApiToken, User] | None:
        if not raw.startswith(API_TOKEN_PREFIX) or len(raw) > 128:
            return None
        token = db.scalar(select(ApiToken).where(ApiToken.token_hash == digest(raw)))
        if (
            token is None
            or token.revoked_at is not None
            or (token.expires_at is not None and now >= token.expires_at)
        ):
            return None
        user = db.get(User, token.user_id)
        if user is None:
            return None
        if token.last_used_at is None or now - token.last_used_at >= TOUCH_INTERVAL:
            token.last_used_at = now
            db.commit()
        return token, user

    def revoke_all_credentials(self, db: Session, user_id: str, now: datetime) -> None:
        """Sessions and API tokens (a person who left every company)."""
        self.revoke_user_sessions(db, user_id, now)
        db.execute(
            update(ApiToken)
            .where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
