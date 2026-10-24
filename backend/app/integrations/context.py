"""The injectable environment of every integration: HTTP, the tunnel supervisor, DNS/TLS probes,
subprocess runner. ``app.state.integrations`` holds one; tests replace the parts they fake."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.core.crypto import CredentialCipher
from app.integrations.base import IntegrationHttp
from app.integrations.dns import DnsResolver, DohResolver, TlsCheck, check_tls
from app.integrations.tunnel import TunnelSupervisor


@dataclass
class IntegrationContext:
    settings: Settings
    http: IntegrationHttp
    cipher: CredentialCipher
    supervisor: TunnelSupervisor
    resolver: Callable[[], DnsResolver] | None = None
    tls_check: TlsCheck = check_tls
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    sleep: Callable[[float], None] = time.sleep
    emails: Any = None  # services.mail_dispatch.EmailDispatcher
    # cache of the last install plan (metadata only), see services.reachability
    install_plan: dict[str, Any] | None = field(default=None)

    def dns_resolver(self) -> DnsResolver:
        if self.resolver is not None:
            return self.resolver()
        return DohResolver(self.http.client(timeout=10))
