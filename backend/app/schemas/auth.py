"""M7 auth schemas. Names match the web contract (web/src/api/contract.check.ts)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel, RequestModel, Timestamp

AuthStateLiteral = Literal["setup_required", "unauthenticated", "authenticated"]
TokenScopeLiteral = Literal["read", "write", "admin"]


class AuthUser(ApiModel):
    id: str
    username: str
    display_name: str
    email: str | None
    two_factor_enabled: bool = False


class AuthSession(ApiModel):
    """``GET /auth/session`` (public) and the answer of login / setup."""

    state: AuthStateLiteral
    user: AuthUser | None
    # setup_required only: the request is not from loopback, so the printed setup token is needed.
    setup_token_required: bool = False
    # login answered "password ok, now the authenticator code" (state stays unauthenticated).
    two_factor_required: bool = False
    expires_at: Timestamp | None = None


class SetupRequest(RequestModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)
    email: str | None = Field(default=None, max_length=320)
    setup_token: str | None = Field(default=None, max_length=256)


class LoginRequest(RequestModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, max_length=32)


class PasswordChange(RequestModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class SessionInfo(ApiModel):
    id: str
    created_at: Timestamp
    last_seen_at: Timestamp | None
    expires_at: Timestamp
    ip: str | None
    user_agent: str | None
    current: bool


class ApiToken(ApiModel):
    id: str
    name: str
    scope: TokenScopeLiteral
    prefix: str | None = None  # first characters, to recognise the token. Never the secret.
    created_at: Timestamp
    last_used_at: Timestamp | None
    expires_at: Timestamp | None = None
    revoked_at: Timestamp | None = None


class ApiTokenCreate(RequestModel):
    name: str = Field(min_length=1, max_length=80)
    scope: TokenScopeLiteral


class ApiTokenCreated(ApiToken):
    """The only response that ever carries the secret."""

    token: str


class TwoFactorEnroll(ApiModel):
    secret: str
    otpauth_uri: str


class TwoFactorVerify(RequestModel):
    code: str = Field(min_length=1, max_length=32)


class TwoFactorDisable(RequestModel):
    code: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class TwoFactorVerifyResult(ApiModel):
    enabled: bool
    recovery_codes: list[str] | None = None  # shown once, when this call enabled 2FA
