"""Verifying the signature of a webhook.

The bodies are those in `tests/recordings/webhook-*.json`, served here as raw
bytes: that is what the API signs and what must be verified without
reserializing.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from conftest import load_recording
from veriko import (
    SIGNATURE_HEADER,
    SignatureVerificationError,
    compute_signature,
    parse_webhook,
    signature_from_headers,
    verify_webhook,
)

# 64 hex characters, the real shape of the secret per the contract. The signature
# is computed with it, so the concrete value does not matter.
SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def body(name: str) -> bytes:
    """The delivery body, in the exact bytes that would travel."""
    return json.dumps(load_recording(name)["json"], ensure_ascii=False).encode("utf-8")


def sign(payload: bytes, secret: str = SECRET) -> str:
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return "sha256=" + signature


def test_a_correct_signature_is_accepted() -> None:
    payload = body("webhook-validation-completed")

    assert verify_webhook(payload, sign(payload), SECRET) is True


def test_the_signature_is_accepted_with_and_without_the_algorithm_prefix() -> None:
    payload = body("webhook-validation-completed")
    con_prefijo = sign(payload)
    sin_prefijo = con_prefijo[len("sha256=") :]

    assert verify_webhook(payload, con_prefijo, SECRET) is True
    assert verify_webhook(payload, sin_prefijo, SECRET) is True
    assert verify_webhook(payload, sin_prefijo.upper(), SECRET) is True


def test_an_altered_body_invalidates_the_signature() -> None:
    payload = body("webhook-validation-completed")
    signature = sign(payload)
    tampered = payload.replace(b'"valid"', b'"not_found"')

    assert tampered != payload
    assert verify_webhook(tampered, signature, SECRET) is False


def test_serialising_the_json_again_breaks_the_signature() -> None:
    # The most common integration mistake: reading the body into a dict and
    # writing it back. The bytes change even though the document is the same.
    payload = body("webhook-validation-completed")
    signature = sign(payload)
    reserialized = json.dumps(json.loads(payload), indent=2).encode("utf-8")

    assert verify_webhook(reserialized, signature, SECRET) is False


def test_another_secret_invalidates_the_signature() -> None:
    payload = body("webhook-validation-completed")

    assert verify_webhook(payload, sign(payload), "other_secret") is False


def test_without_a_signature_or_a_secret_it_is_not_accepted() -> None:
    payload = body("webhook-validation-completed")

    assert verify_webhook(payload, None, SECRET) is False
    assert verify_webhook(payload, "", SECRET) is False
    assert verify_webhook(payload, sign(payload), "") is False


def test_the_body_accepts_text_as_well_as_bytes() -> None:
    payload = body("webhook-validation-completed")

    assert verify_webhook(payload.decode("utf-8"), sign(payload), SECRET) is True


def test_compute_signature_returns_the_hex_without_a_prefix() -> None:
    payload = b'{"event":"test"}'

    signature = compute_signature(payload, SECRET)

    assert len(signature) == 64
    assert signature == hmac.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()


def test_the_header_is_found_however_the_framework_spells_it() -> None:
    assert signature_from_headers({SIGNATURE_HEADER: "sha256=abc"}) == "sha256=abc"
    assert signature_from_headers({"x-webhook-signature": "sha256=abc"}) == "sha256=abc"
    assert signature_from_headers({"HTTP_X_WEBHOOK_SIGNATURE": "sha256=abc"}) == "sha256=abc"
    assert signature_from_headers({"x-otra-cosa": "sha256=abc"}) is None


def test_parse_webhook_returns_the_parsed_event() -> None:
    payload = body("webhook-validation-completed")

    event = parse_webhook(payload, sign(payload), SECRET)

    assert event.event == "validation.completed"
    assert event.timestamp == "2025-03-15T14:22:11Z"
    assert event.validation is not None
    assert event.validation.status == "valid"
    assert event.validation.has_cep is True
    assert event.validation.id == "f47ac10b-58cc-4372-a567-0e02b2c3d479"


def test_parse_webhook_reads_the_reason_of_an_unconfirmed_validation() -> None:
    payload = body("webhook-validation-completed-not-found")

    event = parse_webhook(payload, sign(payload), SECRET)

    assert event.validation is not None
    assert event.validation.status == "not_found"
    assert event.validation.error_code == "not_found_amount"
    assert event.validation.error_message == (
        "Banxico found the operation with a different amount."
    )


def test_parse_webhook_without_a_reason_leaves_the_fields_none() -> None:
    payload = body("webhook-validation-completed")

    event = parse_webhook(payload, sign(payload), SECRET)

    assert event.validation is not None
    assert event.validation.error_code is None
    assert event.validation.error_message is None


def test_parse_webhook_reads_the_retry_cycle() -> None:
    payload = body("webhook-retry-resolved")

    event = parse_webhook(payload, sign(payload), SECRET)

    assert event.event == "validation.retry.resolved"
    assert event.validation is not None
    assert event.validation.retry_state is not None
    assert event.validation.retry_state.attempts_completed == 3
    assert event.validation.retry_state.terminal_state == "resolved"


def test_parse_webhook_without_banxico_confirmed_leaves_the_field_none() -> None:
    payload = body("webhook-validation-completed")

    event = parse_webhook(payload, sign(payload), SECRET)

    assert event.banxico_confirmed is None


def test_parse_webhook_reads_banxico_confirmed() -> None:
    payload = body("webhook-validation-completed-banxico-confirmed")

    event = parse_webhook(payload, sign(payload), SECRET)

    assert event.banxico_confirmed is not None
    assert event.banxico_confirmed.amount == 1000.50
    assert event.banxico_confirmed.operationDate == "21-07-2026"
    assert event.banxico_confirmed.processingTime == "13:45:02"
    assert event.banxico_confirmed.trackingKey == "MBAN01002607211345ABCDEF"
    assert event.banxico_confirmed.senderBank == "BBVA"
    assert event.banxico_confirmed.receiverBank == "AZTECA"
    assert event.banxico_confirmed.beneficiaryAccount == "••••5678"
    assert event.validation is not None
    assert event.validation.id == "3fa85f64-5717-4562-b3fc-2c963f66afa6"


def test_parse_webhook_reads_client_ref() -> None:
    payload = body("webhook-validation-completed-client-ref")

    event = parse_webhook(payload, sign(payload), SECRET)

    assert event.validation is not None
    assert event.validation.client_ref == "orden-4812"


def test_parse_webhook_without_client_ref_leaves_the_field_none() -> None:
    payload = body("webhook-validation-completed")

    event = parse_webhook(payload, sign(payload), SECRET)

    assert event.validation is not None
    assert event.validation.client_ref is None


def test_parse_webhook_does_not_parse_a_body_with_an_invalid_signature() -> None:
    payload = body("webhook-validation-completed")

    with pytest.raises(SignatureVerificationError):
        parse_webhook(payload, "sha256=0000", SECRET)


def test_parse_webhook_reads_validation_returned() -> None:
    payload = body("webhook-validation-returned")

    event = parse_webhook(payload, sign(payload), SECRET)

    assert event.event == "validation.returned"
    assert event.timestamp == "2026-10-02T09:15:44Z"
    assert event.validation is not None
    assert event.validation.status == "returned"
    assert event.validation.banxico_status == "returned"
    assert event.validation.client_ref == "orden-4812"
    assert event.validation.has_cep is True
    assert event.payment_status is not None
    assert event.payment_status.code == "devuelto"
    assert event.payment_status.label == "Devuelto"
    assert event.payment_status.settled is False
    assert event.payment_status.reversed is True
    assert event.payment_status.checked_at == "2026-10-02T09:15:44Z"


def test_parse_webhook_without_payment_status_leaves_the_field_none() -> None:
    payload = body("webhook-validation-completed")

    event = parse_webhook(payload, sign(payload), SECRET)

    assert event.payment_status is None
