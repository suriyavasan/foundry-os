"""HMAC-signed agent run tokens: ``cos_run.<payload>.<signature>``.

The payload is ``{run_id, agent_id, company_id, exp}``. Validity also requires the run to
be ``starting``/``running`` (checked in ``services/agent_auth``), so a leaked token dies
with its run.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass

TOKEN_PREFIX = "cos_run."


@dataclass(frozen=True)
class RunTokenClaims:
    run_id: str
    agent_id: str
    company_id: str
    exp: int


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(key: bytes, payload_b64: str) -> str:
    return _b64(hmac.new(key, payload_b64.encode(), hashlib.sha256).digest())


def mint_run_token(key: bytes, claims: RunTokenClaims) -> str:
    body = json.dumps(
        {
            "run_id": claims.run_id,
            "agent_id": claims.agent_id,
            "company_id": claims.company_id,
            "exp": claims.exp,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    payload = _b64(body.encode())
    return f"{TOKEN_PREFIX}{payload}.{_sign(key, payload)}"


def verify_run_token(key: bytes, token: str, *, now_epoch: int) -> RunTokenClaims | None:
    """Return the claims if the signature is valid and the token is unexpired, else None."""
    if not token.startswith(TOKEN_PREFIX):
        return None
    try:
        payload, signature = token[len(TOKEN_PREFIX) :].split(".", 1)
        if not hmac.compare_digest(signature, _sign(key, payload)):
            return None
        data = json.loads(_unb64(payload))
        claims = RunTokenClaims(
            run_id=str(data["run_id"]),
            agent_id=str(data["agent_id"]),
            company_id=str(data["company_id"]),
            exp=int(data["exp"]),
        )
    except Exception:
        return None
    if claims.exp <= now_epoch:
        return None
    return claims
