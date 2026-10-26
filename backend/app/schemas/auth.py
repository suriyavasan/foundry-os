"""M7 auth schemas. Names match the web contract (web/src/api/contract.check.ts)."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, field_validator

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
    # Passkeys: how many the signed-in person has (0 when nobody is signed in) and whether the
    # origin this request came from can use WebAuthn at all (localhost or a configured https
    # origin).
    passkeys_count: int = 0
    passkey_supported_origin: bool = False
    # Why passkeys are unavailable here, when known: "ephemeral_origin" is a quick-tunnel address
    # (it changes on every restart, so a passkey bound to it would break). None otherwise.
    passkey_unsupported_reason: Literal["ephemeral_origin"] | None = None


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


# ------------------------------------------------------------------ passkeys (WebAuthn)

MAX_WEBAUTHN_JSON = 64 * 1024


def _bounded_json(value: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(value, separators=(",", ":"))) > MAX_WEBAUTHN_JSON:
        raise ValueError("credential is too large")
    return value


class PasskeyInfo(ApiModel):
    id: str
    name: str
    created_at: Timestamp
    last_used_at: Timestamp | None
    transports: list[str]
    backed_up: bool


class PasskeyRegistrationOptionsRequest(RequestModel):
    name: str | None = Field(default=None, max_length=80)


class PasskeyRegistrationOptions(ApiModel):
    """``public_key`` is the WebAuthn ``PublicKeyCredentialCreationOptions`` JSON (base64url
    fields), as ``PublicKeyCredential.parseCreationOptionsFromJSON`` expects it."""

    attempt_id: str
    public_key: dict[str, Any]


class PasskeyRegistrationVerify(RequestModel):
    attempt_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=80)
    credential: dict[str, Any]  # the browser's RegistrationResponseJSON

    check_size = field_validator("credential")(_bounded_json)


class PasskeyRename(RequestModel):
    name: str = Field(min_length=1, max_length=80)


class PasskeyDelete(RequestModel):
    password: str | None = Field(default=None, max_length=256)


class PasskeyLoginOptionsRequest(RequestModel):
    username: str | None = Field(default=None, max_length=64)


class PasskeyLoginOptions(ApiModel):
    """``public_key`` is the ``PublicKeyCredentialRequestOptions`` JSON for
    ``navigator.credentials.get()``; ``allowCredentials`` is empty without a username
    (discoverable credentials / conditional UI)."""

    attempt_id: str
    public_key: dict[str, Any]


class PasskeyLoginVerify(RequestModel):
    attempt_id: str = Field(min_length=1, max_length=64)
    credential: dict[str, Any]  # the browser's AuthenticationResponseJSON

    check_size = field_validator("credential")(_bounded_json)
