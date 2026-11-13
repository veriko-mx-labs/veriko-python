"""Secret rotation: several secrets, `X-Webhook-Signature-Old`, the window and bytes safety."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from veriko import (
    SignatureVerificationError,
    parse_webhook,
    verify_delivery,
    verify_webhook,
    verify_webhook_timestamped,
)

NEW = "a" * 64
OLD = "b" * 64
NOW = 1_700_000_000
BODY = json.dumps({"event": "validation.completed", "timestamp": "2026-01-01T00:00:00Z"}).encode()


def _timestamped(secret: str, t: int = NOW, body: bytes = BODY) -> str:
    digest = hmac.new(secret.encode(), str(t).encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def _plain(secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), BODY, hashlib.sha256).hexdigest()


def test_a_delivery_signed_with_the_previous_secret_verifies_through_the_old_header() -> None:
    headers = {
        "X-Webhook-Signature-Timestamped": _timestamped(NEW),
        "X-Webhook-Signature-Old": _timestamped(OLD),
    }

    assert verify_delivery(headers, BODY, [OLD], now=NOW)
    assert verify_delivery(headers, BODY, [NEW, OLD], now=NOW)
    assert not verify_delivery(headers, BODY, ["c" * 64], now=NOW)


def test_several_secrets_are_accepted_by_the_plain_and_timestamped_verifiers() -> None:
    assert verify_webhook(BODY, _plain(OLD), [NEW, OLD])
    assert verify_webhook_timestamped(BODY, _timestamped(OLD), [NEW, OLD], now=NOW)


def test_the_default_window_is_five_minutes() -> None:
    header = _timestamped(NEW)

    assert verify_webhook_timestamped(BODY, header, NEW, now=NOW + 300)
    assert not verify_webhook_timestamped(BODY, header, NEW, now=NOW + 301)


def test_non_ascii_signatures_and_bodies_do_not_raise() -> None:
    assert verify_webhook(BODY, "sha256=áéíóú", NEW) is False
    assert verify_webhook("cuerpo ñ".encode(), _plain(NEW), NEW) is False
    assert verify_webhook(BODY, None, NEW) is False


def test_parse_webhook_with_headers_rejects_a_replay_outside_the_window() -> None:
    headers = {"X-Webhook-Signature-Timestamped": _timestamped(NEW)}

    event = parse_webhook(BODY, headers, NEW, now=NOW)
    assert event.event == "validation.completed"
    assert event.attempt_timestamp == NOW

    with pytest.raises(SignatureVerificationError):
        parse_webhook(BODY, headers, NEW, now=NOW + 3600)


def test_parse_webhook_reads_the_delivery_id_from_the_headers() -> None:
    headers = {
        "X-Webhook-Signature-Timestamped": _timestamped(NEW),
        "X-Veriko-Delivery-Id": "d-1",
    }

    assert parse_webhook(BODY, headers, NEW, now=NOW).delivery_id == "d-1"


def test_the_plain_signature_alone_needs_require_timestamp_false() -> None:
    headers = {"X-Webhook-Signature": _plain(NEW)}

    assert not verify_delivery(headers, BODY, NEW, now=NOW)
    assert verify_delivery(headers, BODY, NEW, now=NOW, require_timestamp=False)
