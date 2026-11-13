"""Verifying the timestamped signature of a webhook.

The header is `X-Webhook-Signature-Timestamped: t=<seconds>,v1=<hex>`, and `v1`
is the HMAC-SHA256 of `<t>.<body>` with the endpoint secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from conftest import load_recording
from veriko import (
    DEFAULT_TOLERANCE_SECONDS,
    TIMESTAMPED_SIGNATURE_HEADER,
    parse_webhook,
    timestamped_signature_from_headers,
    verify_webhook,
    verify_webhook_timestamped,
)

SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
NOW = 1_700_000_000


def body(name: str) -> bytes:
    return json.dumps(load_recording(name)["json"], ensure_ascii=False).encode("utf-8")


def v1(payload: bytes, t: int, secret: str = SECRET) -> str:
    signed = str(t).encode("ascii") + b"." + payload
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def signed_header(payload: bytes, t: int = NOW, secret: str = SECRET) -> str:
    return f"t={t},v1={v1(payload, t, secret)}"


PAYLOAD = body("webhook-validation-completed")


def test_the_fixed_signature_vector() -> None:
    # The HMAC-SHA256 of `1700000000.{"event":"test"}` with the test secret.
    header = "t=1700000000,v1=9b5ddb2ae0c5514e340f612c3424deb50a1e17b12767ae8550d6388096c92e10"

    assert verify_webhook_timestamped(b'{"event":"test"}', header, SECRET, now=NOW) is True


def test_a_correct_signature_inside_the_window_is_accepted() -> None:
    assert verify_webhook_timestamped(PAYLOAD, signed_header(PAYLOAD), SECRET, now=NOW) is True


def test_the_body_accepts_text_as_well_as_bytes() -> None:
    text = PAYLOAD.decode("utf-8")

    assert verify_webhook_timestamped(text, signed_header(PAYLOAD), SECRET, now=NOW) is True


def test_the_window_is_measured_toward_the_past_and_the_future() -> None:
    header = signed_header(PAYLOAD)

    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=NOW + 299) is True
    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=NOW - 299) is True


def test_the_window_edge_is_accepted_and_one_second_more_is_rejected() -> None:
    header = signed_header(PAYLOAD)

    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=NOW + 300) is True
    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=NOW + 301) is False
    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=NOW - 301) is False


def test_an_old_delivery_that_is_repeated_is_rejected() -> None:
    header = signed_header(PAYLOAD)

    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=NOW + 3600) is False


def test_the_window_is_adjusted_with_tolerance() -> None:
    header = signed_header(PAYLOAD)

    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, tolerance=60, now=NOW + 61) is False
    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, tolerance=60, now=NOW + 60) is True
    assert (
        verify_webhook_timestamped(PAYLOAD, header, SECRET, tolerance=3600, now=NOW + 3600) is True
    )


def test_the_default_window_is_five_minutes() -> None:
    assert DEFAULT_TOLERANCE_SECONDS == 300


def test_without_now_it_uses_the_system_clock() -> None:
    now = int(time.time())

    assert verify_webhook_timestamped(PAYLOAD, signed_header(PAYLOAD, now), SECRET) is True
    assert verify_webhook_timestamped(PAYLOAD, signed_header(PAYLOAD, now - 3600), SECRET) is False


def test_an_invalid_current_time_or_window_accepts_nothing() -> None:
    header = signed_header(PAYLOAD)

    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=float("nan")) is False
    assert (
        verify_webhook_timestamped(PAYLOAD, header, SECRET, tolerance=float("nan"), now=NOW)
        is False
    )


def test_an_altered_body_invalidates_the_signature() -> None:
    header = signed_header(PAYLOAD)
    tampered = PAYLOAD.replace(b'"valid"', b'"not_found"')

    assert tampered != PAYLOAD
    assert verify_webhook_timestamped(tampered, header, SECRET, now=NOW) is False


def test_serialising_the_json_again_breaks_the_signature() -> None:
    reserialized = json.dumps(json.loads(PAYLOAD), indent=2).encode("utf-8")

    assert (
        verify_webhook_timestamped(reserialized, signed_header(PAYLOAD), SECRET, now=NOW) is False
    )


def test_another_secret_invalidates_the_signature() -> None:
    header = signed_header(PAYLOAD)

    assert verify_webhook_timestamped(PAYLOAD, header, "other_secret", now=NOW) is False


def test_changing_t_without_signing_again_invalidates_the_signature() -> None:
    # Someone replaying an old delivery cannot give it a new `t`: `t` is signed.
    signature = v1(PAYLOAD, NOW)
    renewed = f"t={NOW + 3000},v1={signature}"

    assert verify_webhook_timestamped(PAYLOAD, renewed, SECRET, now=NOW + 3000) is False


def test_the_body_only_signature_does_not_work_as_a_timestamped_signature() -> None:
    single = hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha256).hexdigest()

    assert verify_webhook_timestamped(PAYLOAD, f"t={NOW},v1={single}", SECRET, now=NOW) is False


def test_with_several_v1_one_match_is_enough() -> None:
    good = v1(PAYLOAD, NOW)
    bad = "0" * 64

    assert verify_webhook_timestamped(PAYLOAD, f"t={NOW},v1={bad},v1={good}", SECRET, now=NOW)
    assert verify_webhook_timestamped(PAYLOAD, f"t={NOW},v1={good},v1={bad}", SECRET, now=NOW)
    assert not verify_webhook_timestamped(
        PAYLOAD, f"t={NOW},v1={bad},v1={bad[::-1]}", SECRET, now=NOW
    )


def test_it_ignores_unknown_keys_and_spaces() -> None:
    signature = v1(PAYLOAD, NOW)

    assert verify_webhook_timestamped(
        PAYLOAD, f" v0=abc , t={NOW} , v1={signature} , v2=otra", SECRET, now=NOW
    )
    assert verify_webhook_timestamped(PAYLOAD, f"v1={signature},t={NOW}", SECRET, now=NOW)


def test_an_uppercase_signature_is_accepted() -> None:
    header = f"t={NOW},v1={v1(PAYLOAD, NOW).upper()}"

    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=NOW) is True


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "   ",
        "basura",
        "t=,v1=",
        f"t={NOW}",
        "v1=" + "a" * 64,
        f"t=ayer,v1={'a' * 64}",
        f"t=-{NOW},v1={'a' * 64}",
        f"t={NOW}.5,v1={'a' * 64}",
        f"t=1e9,v1={'a' * 64}",
        f"t=\u0661\u0667\u0660\u0660\u0660\u0660\u0660\u0660\u0660\u0660,v1={'a' * 64}",
        f"t={'9' * 40},v1={'a' * 64}",
        f"t={NOW},t={NOW},v1={'a' * 64}",
        f"t={NOW},v1=",
        f"t={NOW},v1=áéíóú",
        "sha256=" + "a" * 64,
    ],
    ids=lambda value: repr(value)[:40],
)
def test_a_malformed_header_is_rejected_without_raising(header: str | None) -> None:
    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=NOW) is False


def test_without_a_secret_it_is_not_accepted() -> None:
    assert (
        verify_webhook_timestamped(PAYLOAD, signed_header(PAYLOAD, secret=""), "", now=NOW) is False
    )


def test_both_signatures_coexist_and_the_original_one_does_not_change() -> None:
    old = "sha256=" + hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha256).hexdigest()

    assert verify_webhook(PAYLOAD, old, SECRET) is True
    assert verify_webhook_timestamped(PAYLOAD, signed_header(PAYLOAD), SECRET, now=NOW) is True
    # The plain signature does not cover the time, and the new one is not mistaken for it.
    assert verify_webhook(PAYLOAD, signed_header(PAYLOAD), SECRET) is False
    assert verify_webhook_timestamped(PAYLOAD, old, SECRET, now=NOW) is False


def test_verify_before_parsing_the_event() -> None:
    old = "sha256=" + hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha256).hexdigest()

    assert verify_webhook_timestamped(PAYLOAD, signed_header(PAYLOAD), SECRET, now=NOW)
    event = parse_webhook(PAYLOAD, old, SECRET)

    assert event.event == "validation.completed"


def test_the_header_is_found_however_the_framework_spells_it() -> None:
    value = "t=1,v1=abc"

    assert timestamped_signature_from_headers({TIMESTAMPED_SIGNATURE_HEADER: value}) == value
    assert timestamped_signature_from_headers({"x-webhook-signature-timestamped": value}) == value
    assert (
        timestamped_signature_from_headers({"HTTP_X_WEBHOOK_SIGNATURE_TIMESTAMPED": value}) == value
    )
    # The plain signature is not mistaken for the new one.
    assert timestamped_signature_from_headers({"X-Webhook-Signature": "sha256=abc"}) is None


def test_the_header_name() -> None:
    assert TIMESTAMPED_SIGNATURE_HEADER == "X-Webhook-Signature-Timestamped"
