"""Value-level secret detection (services/secrets_scan.py) and the outbound DLP built on it."""

from __future__ import annotations

import pytest

from app.guardrails import dlp
from app.services.secrets_scan import find_secret, scrub

FINE_GRAINED = (
    "github_pat_11ABCDEFG0aBcDeFgHiJkL_mNoPqRsTuVwXyZ0123456789aBcDeFgHiJkLmNoPqRsTuVwXyZ01"
)
CLASSIC = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"


@pytest.mark.parametrize(
    "token,name",
    [(FINE_GRAINED, "github fine-grained token"), (CLASSIC, "github token")],
)
def test_github_tokens_are_found_and_scrubbed(token, name):
    text = f"push failed for {token} on origin"
    assert find_secret(text) == name
    cleaned = scrub(text)
    assert token not in cleaned and "[redacted]" in cleaned
    assert cleaned.startswith("push failed for ") and cleaned.endswith(" on origin")


def test_a_fine_grained_token_inside_a_url_or_quote_is_still_scrubbed():
    for text in (
        f"https://x-access-token:{FINE_GRAINED}@github.com/acme/app.git",
        f'Authorization: "token {FINE_GRAINED}"',
        f"GH_TOKEN={FINE_GRAINED}",
    ):
        assert FINE_GRAINED not in (scrub(text) or ""), text


def test_short_or_unrelated_github_pat_lookalikes_are_not_flagged():
    for text in ("github_pat_short", "see github_pat_ for details", "mygithub_pat_x" + "a" * 30):
        assert find_secret(text) is None or "github" not in find_secret(text), text


def test_outbound_dlp_flags_a_fine_grained_token_as_a_secret_without_echoing_it():
    findings = dlp.scan_text(f"here you go: {FINE_GRAINED}")
    assert [f.kind for f in findings] == ["secret"]
    assert FINE_GRAINED not in repr([f.as_dict() for f in findings])
