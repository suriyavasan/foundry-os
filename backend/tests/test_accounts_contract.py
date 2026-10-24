"""The M7 API surface matches what the web app was built against (web/src/api/types.ts)."""

from __future__ import annotations

EXPECTED = {
    "AuthSession": {"state", "user", "setup_token_required", "two_factor_required", "expires_at"},
    "AuthUser": {"id", "username", "display_name", "email", "two_factor_enabled"},
    "SetupRequest": {"username", "display_name", "password", "email", "setup_token"},
    "LoginRequest": {"username", "password", "totp_code"},
    "PasswordChange": {"current_password", "new_password"},
    "SessionInfo": {
        "id",
        "created_at",
        "last_seen_at",
        "expires_at",
        "ip",
        "user_agent",
        "current",
    },
    "ApiToken": {
        "id",
        "name",
        "scope",
        "prefix",
        "created_at",
        "last_used_at",
        "expires_at",
        "revoked_at",
    },
    "ApiTokenCreate": {"name", "scope"},
    "ApiTokenCreated": {"id", "name", "scope", "prefix", "created_at", "token"},
    "TwoFactorEnroll": {"secret", "otpauth_uri"},
    "TwoFactorVerifyResult": {"enabled", "recovery_codes"},
    "Invitation": {
        "id",
        "company_id",
        "email",
        "role",
        "title",
        "manager_ref",
        "status",
        "expires_at",
        "created_at",
        "accepted_at",
    },
    "InvitationCreate": {"email", "role", "title", "manager_ref"},
    "InvitationCreated": {"delivery", "invite_url", "token", "status"},
    "InvitationAccept": {"username", "display_name", "password"},
    "InvitationAccepted": {"company_id", "company_name", "role", "member_id"},
    "Member": {
        "id",
        "company_id",
        "user_id",
        "username",
        "display_name",
        "email",
        "role",
        "title",
        "manager_ref",
        "status",
        "created_at",
    },
    "MemberUpdate": {"role", "title", "manager_ref"},
    "Notification": {
        "id",
        "company_id",
        "kind",
        "title",
        "body",
        "related_type",
        "related_id",
        "created_at",
        "read_at",
    },
    "NotificationList": {"items", "unread_count"},
    "CompleteTaskRequest": {"comment", "artifact_ids"},
}


def schemas(app) -> dict:
    return app.openapi()["components"]["schemas"]


def test_the_schemas_the_web_contract_checks_exist_with_their_fields(app):
    comps = schemas(app)
    for name, fields in EXPECTED.items():
        assert name in comps, name
        assert fields <= set(comps[name]["properties"]), (
            name,
            fields - set(comps[name]["properties"]),
        )


def test_enums_match_the_ui(app):
    comps = schemas(app)
    assert set(comps["AuthSession"]["properties"]["state"]["enum"]) == {
        "setup_required",
        "unauthenticated",
        "authenticated",
    }
    assert set(comps["Member"]["properties"]["role"]["enum"]) == {
        "owner",
        "admin",
        "member",
        "viewer",
    }
    assert set(comps["Member"]["properties"]["status"]["enum"]) == {"active", "disabled", "removed"}
    assert set(comps["Invitation"]["properties"]["status"]["enum"]) == {
        "pending",
        "accepted",
        "expired",
        "revoked",
    }
    assert set(comps["ApiToken"]["properties"]["scope"]["enum"]) == {"read", "write", "admin"}
    assert set(comps["InvitationCreated"]["properties"]["delivery"]["enum"]) == {"email", "link"}


def test_endpoints_exist_as_the_web_expects(app):
    paths = app.openapi()["paths"]
    expected = {
        ("/api/auth/session", "get"),
        ("/api/auth/setup", "post"),
        ("/api/auth/login", "post"),
        ("/api/auth/logout", "post"),
        ("/api/auth/password", "post"),
        ("/api/auth/sessions", "get"),
        ("/api/auth/sessions", "delete"),
        ("/api/auth/sessions/{session_id}", "delete"),
        ("/api/auth/tokens", "get"),
        ("/api/auth/tokens", "post"),
        ("/api/auth/tokens/{token_id}", "delete"),
        ("/api/auth/2fa/enroll", "post"),
        ("/api/auth/2fa/verify", "post"),
        ("/api/auth/2fa/disable", "post"),
        ("/api/companies/{company_id}/invitations", "get"),
        ("/api/companies/{company_id}/invitations", "post"),
        ("/api/companies/{company_id}/invitations/{invitation_id}", "delete"),
        ("/api/invitations/{token}/accept", "post"),
        ("/api/companies/{company_id}/members", "get"),
        ("/api/companies/{company_id}/members/{member_id}", "patch"),
        ("/api/companies/{company_id}/members/{member_id}", "delete"),
        ("/api/notifications", "get"),
        ("/api/notifications/read", "post"),
        ("/api/companies/{company_id}/tasks/{task_id}/complete", "post"),
        ("/api/companies/{company_id}/approvals/{approval_id}/route", "post"),
    }
    missing = {e for e in expected if e[0] not in paths or e[1] not in paths[e[0]]}
    assert not missing, missing


def test_task_agent_approval_gain_the_org_refs(app):
    comps = schemas(app)
    assert {"assignee_ref", "reviewer_ref"} <= set(comps["Task"]["properties"])
    assert {"assignee_ref", "reviewer_ref"} <= set(comps["TaskCreate"]["properties"])
    assert "manager_ref" in comps["Agent"]["properties"]
    assert "manager_ref" in comps["AgentCreate"]["properties"]
    assert "approver_ref" in comps["Approval"]["properties"]
    me = comps["MeMembership"]["properties"]
    assert "member_id" in me and "member" in me["role"]["enum"]


def test_the_default_response_never_carries_a_secret_field(app):
    comps = schemas(app)
    for name in ("ApiToken", "Member", "AuthUser", "SessionInfo", "Invitation", "Notification"):
        props = set(comps[name]["properties"])
        assert not {p for p in props if "hash" in p or p in ("password", "secret", "token")}, name
