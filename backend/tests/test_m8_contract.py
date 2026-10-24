"""The OpenAPI schema carries the names, enums and paths the web (web/src/api/types.ts, Milestone 8)
expects. The TypeScript check itself lives in web/src/api/contract.check.ts (see the M8 report)."""

from __future__ import annotations


def components(app):
    return app.openapi()["components"]["schemas"]


def test_schema_names_enums_and_paths_match_the_web_contract(app):
    schemas = components(app)
    for name in (
        "ReachabilityStatus", "LanAddress", "LanStatus", "TunnelStatus", "TunnelConfig", "TunnelBinary",
        "TunnelProviderOption", "DomainSummary", "DomainPlan", "DnsRecord", "DomainRegistration",
        "DomainRegistry", "FreeDomainProvider", "DomainAvailability", "DomainRegistrationPreview",
        "DomainRegistrationPreviewRequest", "DomainRegistrationCreate", "HostedUiDeployment",
        "EmailIdentity", "EmailIdentityUpdate", "Mailbox", "MailboxCreate", "ConnectedAccount",
        "ConnectedAccountCreate", "ProviderCheck",
    ):  # fmt: skip
        assert name in schemas, name

    def enum(schema, prop):
        node = schemas[schema]["properties"][prop]
        node = node.get("anyOf", [node])[0]
        return set(node["enum"])

    assert enum("TunnelStatus", "state") == {"stopped", "starting", "running", "error"}
    assert enum("TunnelStatus", "provider") == {
        "cloudflare_quick",
        "cloudflare_named",
        "tailscale_funnel",
    }
    assert enum("ConnectedAccount", "status") == {"connected", "invalid", "revoked"}
    assert enum("Mailbox", "status") == {"active", "pending", "error", "disabled"}
    assert enum("HostedUiDeployment", "status") == {"none", "deploying", "ready", "error"}
    assert enum("ReachabilityStatus", "mode") == {"local", "lan", "public"}
    assert enum("DomainPlan", "status") == {
        "none",
        "awaiting_approval",
        "pr_open",
        "awaiting_founder",
        "pending_dns",
        "active",
        "error",
    }
    assert enum("EmailIdentity", "provider") == {
        "agentmail",
        "cloudflare_resend",
        "gmail",
        "outlook",
    }
    assert enum("EmailIdentity", "status") == {"not_connected", "connected", "error"}
    assert enum("DomainRegistration", "path") == {
        "github_registry",
        "free_provider",
        "paid_registrar",
    }
    assert enum("DomainRegistration", "status") == {"awaiting_approval", "submitted", "pr_open", "merged", "awaiting_founder", "purchased", "rejected", "failed", "cancelled"}  # fmt: skip
    one_of = schemas["DomainRegistrationCreate"]
    assert {s["$ref"].rsplit("/", 1)[-1] for s in one_of["oneOf"]} == {
        "DomainRegistrationCreateRegistry", "DomainRegistrationCreateFree", "DomainRegistrationCreatePaid",
    }  # fmt: skip
    # no response schema has a field that could carry a secret
    for name, schema in schemas.items():
        if name.startswith(
            ("ConnectedAccount", "Mailbox", "EmailIdentity", "HostedUi", "Tunnel", "Reachability")
        ):
            assert not {"token", "secret", "password", "api_key", "value"} & set(
                schema.get("properties", {})
            ), name

    paths = app.openapi()["paths"]
    expected = {
        ("get", "/api/reachability"), ("patch", "/api/reachability/lan"),
        ("post", "/api/reachability/tunnel/start"), ("post", "/api/reachability/tunnel/stop"),
        ("post", "/api/reachability/tunnel/install"),
        ("get", "/api/companies/{company_id}/connected-accounts"),
        ("post", "/api/companies/{company_id}/connected-accounts"),
        ("post", "/api/companies/{company_id}/connected-accounts/{account_id}/test"),
        ("delete", "/api/companies/{company_id}/connected-accounts/{account_id}"),
        ("post", "/api/companies/{company_id}/connected-accounts/oauth/start"),
        ("get", "/api/companies/{company_id}/domain"), ("get", "/api/companies/{company_id}/domain/registries"),
        ("get", "/api/companies/{company_id}/domain/free-providers"), ("get", "/api/companies/{company_id}/domain/search"),
        ("post", "/api/companies/{company_id}/domain/registrations/preview"),
        ("post", "/api/companies/{company_id}/domain/registrations"),
        ("post", "/api/companies/{company_id}/domain/verify"),
        ("get", "/api/companies/{company_id}/hosted-ui"), ("post", "/api/companies/{company_id}/hosted-ui/deploy"),
        ("get", "/api/companies/{company_id}/email"), ("patch", "/api/companies/{company_id}/email"),
        ("get", "/api/companies/{company_id}/mailboxes"), ("post", "/api/companies/{company_id}/mailboxes"),
    }  # fmt: skip
    for method, path in expected:
        assert method in paths.get(path, {}), (method, path)
