"""A software WebAuthn authenticator for the passkey tests.

It builds real ``attestation: none`` registrations and real ES256 assertions with the
``cryptography`` package, in the JSON shape a browser's ``PublicKeyCredential.toJSON()`` produces,
so the server's verification (RP id hash, origin, challenge, signature, counter) is exercised for
real -- nothing is mocked.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI

from tests.accounts_helpers import PASSWORD, Browser, browser

from webauthn.helpers import base64url_to_bytes, bytes_to_base64url  # isort: skip

LOCAL_ORIGIN = "http://localhost:8700"
FLAG_UP, FLAG_UV, FLAG_BE, FLAG_BS, FLAG_AT = 0x01, 0x04, 0x08, 0x10, 0x40


def b64(data: bytes) -> str:
    return bytes_to_base64url(data)


class SoftwareAuthenticator:
    def __init__(self, *, user_verified: bool = True, backed_up: bool = False) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.aaguid = os.urandom(16)
        self.user_verified = user_verified
        self.backed_up = backed_up
        self.counter = 0
        self.user_handle: bytes | None = None

    @property
    def id(self) -> str:
        return b64(self.credential_id)

    def _flags(self, *, attested: bool) -> int:
        flags = FLAG_UP
        if self.user_verified:
            flags |= FLAG_UV
        if self.backed_up:
            flags |= FLAG_BE | FLAG_BS
        if attested:
            flags |= FLAG_AT
        return flags

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty EC2
                3: -7,  # alg ES256
                -1: 1,  # crv P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    @staticmethod
    def _client_data(kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False},
            separators=(",", ":"),
        ).encode()

    def create(
        self,
        options: dict[str, Any],
        *,
        origin: str = LOCAL_ORIGIN,
        rp_id: str | None = None,
        challenge: str | None = None,
    ) -> dict[str, Any]:
        """``navigator.credentials.create()`` -> RegistrationResponseJSON."""
        rp_id = rp_id or options["rp"]["id"]
        self.user_handle = base64url_to_bytes(options["user"]["id"])
        client_data = self._client_data(
            "webauthn.create", challenge or options["challenge"], origin
        )
        cred = self.credential_id
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([self._flags(attested=True)])
            + struct.pack(">I", self.counter)
            + self.aaguid
            + struct.pack(">H", len(cred))
            + cred
            + self._cose_key()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": self.id,
            "rawId": self.id,
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64(client_data),
                "attestationObject": b64(attestation),
                "transports": ["internal", "hybrid"],
            },
        }

    def get(
        self,
        options: dict[str, Any],
        *,
        origin: str = LOCAL_ORIGIN,
        rp_id: str | None = None,
        counter: int | None = None,
        challenge: str | None = None,
        user_handle: bool = True,
    ) -> dict[str, Any]:
        """``navigator.credentials.get()`` -> AuthenticationResponseJSON. The counter advances by
        one per assertion unless ``counter`` pins it (0 = an authenticator without a counter)."""
        rp_id = rp_id or options["rpId"]
        if counter is None:
            self.counter += 1
        else:
            self.counter = counter
        client_data = self._client_data("webauthn.get", challenge or options["challenge"], origin)
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([self._flags(attested=False)])
            + struct.pack(">I", self.counter)
        )
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": self.id,
            "rawId": self.id,
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64(client_data),
                "authenticatorData": b64(auth_data),
                "signature": b64(signature),
                "userHandle": b64(self.user_handle) if user_handle and self.user_handle else None,
            },
        }


def loopback_setup(app: FastAPI, username: str = "alice", **overrides) -> Browser:
    """First-run setup from the server's own machine (http://localhost:8700): the only kind of
    browser where WebAuthn works without any configured HTTPS origin."""
    b = browser(app, loopback=True)
    body = {
        "username": username,
        "display_name": username.title(),
        "password": PASSWORD,
        **overrides,
    }
    r = b.post("/api/auth/setup", json=body)
    assert r.status_code == 200, r.text
    return b


def loopback_login(app: FastAPI, username: str, password: str = PASSWORD) -> Browser:
    b = browser(app, loopback=True)
    r = b.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200 and r.json()["state"] == "authenticated", r.text
    return b


def register(b: Browser, authenticator: SoftwareAuthenticator, name: str | None = None, **kw):
    """The whole registration ceremony; returns the PasskeyInfo response."""
    r = b.post("/api/auth/passkeys/register/options", json={"name": name} if name else {})
    assert r.status_code == 200, r.text
    opts = r.json()
    credential = authenticator.create(opts["public_key"], **kw)
    r = b.post(
        "/api/auth/passkeys/register/verify",
        json={"attempt_id": opts["attempt_id"], "credential": credential},
    )
    assert r.status_code == 201, r.text
    return r.json()


def login_with(
    b: Browser,
    authenticator: SoftwareAuthenticator,
    *,
    username: str | None = None,
    **kw,
):
    """The whole login ceremony on an anonymous browser; returns the verify response."""
    r = b.post("/api/auth/passkey/login/options", json={"username": username} if username else {})
    assert r.status_code == 200, r.text
    opts = r.json()
    credential = authenticator.get(opts["public_key"], **kw)
    return b.post(
        "/api/auth/passkey/login/verify",
        json={"attempt_id": opts["attempt_id"], "credential": credential},
    )
