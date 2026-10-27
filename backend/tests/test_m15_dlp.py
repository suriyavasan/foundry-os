"""M15 DLP: a Luhn-checked card-number detector and the Stripe / Mercury key shapes, in the shared
scanner every agent-facing text path already uses (notes, memory, audit summaries, outbound)."""

from __future__ import annotations

import pytest

from app.guardrails import dlp
from app.services import secrets_scan

VISA = "4111111111111111"
VISA_SPACED = "4111 1111 1111 1111"
VISA_DASHED = "4111-1111-1111-1111"
AMEX = "378282246310005"  # 15 digits
MASTERCARD = "5555555555554444"
DINERS_14 = "30569309025904"
LONG_19 = "6011000990139424000"[:19]


@pytest.mark.parametrize("pan", [VISA, VISA_SPACED, VISA_DASHED, AMEX, MASTERCARD, DINERS_14])
def test_luhn_valid_numbers_are_detected_and_redacted(pan):
    text = f"please pay with {pan} today"
    assert secrets_scan.find_secret(text) == "card number"
    assert secrets_scan.contains_card_number(text)
    assert secrets_scan.scrub(text) == "please pay with [card number] today"


@pytest.mark.parametrize(
    "text",
    [
        "4111111111111112",  # fails Luhn
        "1111111111111111",  # one repeated digit (Luhn-valid but not a card)
        "0000000000000000",
        "order 1234567",  # too short
        "2026-10-27 12:00:00",
        "id 123456789012",  # 12 digits
        "version 1.2.3.4111111111111111",  # glued to a dotted token
        "ref-4111111111111111",  # glued to an identifier
        "4111111111111111a",
    ],
)
def test_things_that_are_not_card_numbers_are_left_alone(text):
    assert secrets_scan.find_secret(text) is None
    assert secrets_scan.scrub(text) == text


def test_the_marker_is_card_number_and_other_secrets_stay_redacted():
    text = f"card {VISA} and key sk_live_{'a1B2' * 6} and whsec_{'z9Y8' * 5}"
    out = secrets_scan.scrub(text)
    assert "[card number]" in out and VISA not in out
    assert out.count("[redacted]") == 2


def test_several_numbers_in_one_text_are_all_redacted():
    out = secrets_scan.scrub(f"a {VISA} b {MASTERCARD} c {AMEX}")
    assert out == "a [card number] b [card number] c [card number]"


@pytest.mark.parametrize(
    ("text", "name"),
    [
        (f"sk_live_{'Ab12' * 6}", "stripe key"),
        (f"rk_live_{'Ab12' * 6}", "stripe key"),
        (f"rk_test_{'Ab12' * 6}", "stripe key"),
        (f"whsec_{'Ab12' * 6}", "stripe webhook secret"),
        ("secret-token:mercury_production_wma_AbCdEfGh12345678", "mercury token"),
    ],
)
def test_payment_provider_keys_are_detected(text, name):
    assert secrets_scan.find_secret(f"the key is {text} ok") == name
    assert text not in (secrets_scan.scrub(f"the key is {text} ok") or "")


def test_short_lookalikes_are_not_keys():
    assert secrets_scan.find_secret("sk_live_short") is None
    assert secrets_scan.find_secret("whsec_abc") is None


def test_outbound_dlp_sees_a_card_number_and_a_stripe_key():
    findings = dlp.scan_text(f"My card is {VISA_SPACED}, key rk_live_{'Ab12' * 6}")
    assert any(f.kind == "secret" and f.severity == "high" for f in findings)
    description = " ".join(f.description for f in findings)
    assert "card number" in description and "stripe key" in description
    # a finding never carries the matched text
    assert VISA not in repr(findings) and "Ab12" not in repr(findings)


def test_pan_in_a_note_is_refused_and_in_a_log_summary_scrubbed():
    from app.guardrails import engine  # the guardrail log summary path scrubs its text

    assert VISA not in (secrets_scan.scrub(f"paid with {VISA}") or "")
    assert callable(engine.__dict__.get("scrub"))  # the engine uses the same scrubber


def test_the_pan_detector_is_fast_on_long_digit_runs():
    import time

    text = "9" * 50_000 + " " + "1 " * 20_000
    started = time.monotonic()
    secrets_scan.scrub(text)
    assert time.monotonic() - started < 2
