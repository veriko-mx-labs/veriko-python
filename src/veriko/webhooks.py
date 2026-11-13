"""Verification of the signature of a webhook.

Each delivery arrives signed with the HMAC-SHA256 of the body, using the secret
that `POST /v1/webhooks` returned when the endpoint was registered. The signature
travels in hexadecimal, prefixed by the algorithm:

    X-Webhook-Signature: sha256=<hex>

What is signed is the body as it arrived. Parsing the JSON and serialising it
again changes the bytes, because key order, whitespace and the escaping of
non-ASCII characters are not preserved, and the signature no longer matches. The
receiver reads the raw body before the framework parses it.

Every delivery also carries a second signature that includes the time of the
attempt:

    X-Webhook-Signature-Timestamped: t=<seconds>,v1=<hex>

`v1` is the HMAC-SHA256 of `<t>.<body>`. With it, the receiver can discard an old
delivery that someone sends again.

For 24 hours after `POST /v1/webhooks/{id}/regenerate-secret`, each delivery also
carries `X-Webhook-Signature-Old`, in the same format and with the same `t`,
signed with the previous secret. Pass both secrets to `verify_delivery()` or
`parse_webhook()` to keep verifying without interruption during the rotation.

https://docs.veriko.mx/en/concepts/webhooks-architecture
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Iterable, Mapping
from typing import Any

from .errors import SignatureVerificationError
from .models import (
    BanxicoConfirmed,
    PaymentStatus,
    Validation,
    WebhookEvent,
)

# Header of the signature.
SIGNATURE_HEADER = "X-Webhook-Signature"

# Header of the signature that includes the time of the attempt.
TIMESTAMPED_SIGNATURE_HEADER = "X-Webhook-Signature-Timestamped"

# Header of the signature made with the previous secret, during the 24 hours
# after a rotation.
PREVIOUS_SIGNATURE_HEADER = "X-Webhook-Signature-Old"

# Recommended window between `t` and the receiver's clock, in seconds.
DEFAULT_TOLERANCE_SECONDS = 300

# Other headers of a delivery.
EVENT_HEADER = "X-Veriko-Event"
DELIVERY_ID_HEADER = "X-Veriko-Delivery-Id"
TIMESTAMP_HEADER = "X-Veriko-Timestamp"

_SIGNATURE_PREFIX = "sha256="
_UNIX_SECONDS = re.compile(r"[0-9]{1,15}")


def _secrets(secret: str | bytes | Iterable[str | bytes] | None) -> list[bytes]:
    """One secret or several, as bytes. Empty values are dropped."""
    if secret is None:
        return []
    items: Iterable[str | bytes] = [secret] if isinstance(secret, (str, bytes)) else secret
    return [
        (item.encode("utf-8") if isinstance(item, str) else bytes(item)) for item in items if item
    ]


def _body(payload: bytes | str) -> bytes:
    return payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)


def compute_signature(payload: bytes | str, secret: str | bytes) -> str:
    """Returns the hexadecimal HMAC-SHA256 of the body, without the `sha256=` prefix."""
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    return hmac.new(key, _body(payload), hashlib.sha256).hexdigest()


def _equal(received: str, expected: str) -> bool:
    """Constant-time comparison of two hexadecimal digests, safe for any input.

    It compares bytes, so a header with non-ASCII characters is a mismatch and not
    a `TypeError`.
    """
    return hmac.compare_digest(received.encode("utf-8"), expected.encode("ascii"))


def verify_webhook(
    payload: bytes | str,
    signature: str | None,
    secret: str | bytes | Iterable[str | bytes] | None,
) -> bool:
    """Checks the signature of a delivery. Returns `True` or `False`, never raises.

    Accepts the header value with or without the `sha256=` prefix. The comparison
    takes constant time, so response time leaks nothing. `secret` is one secret or
    several, such as the current and the previous one during a rotation.

    Args:
        payload: the raw body of the request, not serialised again.
        signature: the value of the `X-Webhook-Signature` header.
        secret: the endpoint secret, or an iterable of secrets.
    """
    secrets = _secrets(secret)
    if not signature or not secrets:
        return False
    received = signature.strip()
    if received.lower().startswith(_SIGNATURE_PREFIX):
        received = received[len(_SIGNATURE_PREFIX) :]
    received = received.lower()
    body = _body(payload)
    # Every secret is tried, without stopping at the first match.
    matches = [_equal(received, hmac.new(key, body, hashlib.sha256).hexdigest()) for key in secrets]
    return any(matches)


def _timestamped_parts(header: str) -> tuple[str, list[str]] | None:
    """Splits `t=<seconds>,v1=<hex>` into the time and the `v1` signatures.

    Accepts several `v1` and ignores the keys it does not know. Returns `None`
    when `t` is missing, appears more than once, or is not an integer number of
    seconds.
    """
    times: list[str] = []
    digests: list[str] = []
    for part in header.split(","):
        key, separator, value = part.strip().partition("=")
        if not separator:
            continue
        key, value = key.strip(), value.strip()
        if key == "t":
            times.append(value)
        elif key == "v1":
            digests.append(value.lower())
    if len(times) != 1 or not _UNIX_SECONDS.fullmatch(times[0]):
        return None
    return times[0], digests


def attempt_timestamp(header: str | None) -> int | None:
    """The `t` of a timestamped signature header, in Unix seconds, or `None`."""
    if not header:
        return None
    parts = _timestamped_parts(header)
    return int(parts[0]) if parts else None


def _verify_timestamped(
    body: bytes,
    header: str | None,
    secrets: list[bytes],
    tolerance: float,
    now: float | None,
) -> bool:
    if not header or not secrets:
        return False
    parts = _timestamped_parts(header)
    if parts is None:
        return False
    timestamp, digests = parts
    current = time.time() if now is None else now
    if not abs(current - int(timestamp)) <= tolerance:
        return False
    signed = timestamp.encode("ascii") + b"." + body
    matches = [
        _equal(digest, hmac.new(key, signed, hashlib.sha256).hexdigest())
        for key in secrets
        for digest in digests
    ]
    return any(matches)


def verify_webhook_timestamped(
    payload: bytes | str,
    header: str | None,
    secret: str | bytes | Iterable[str | bytes] | None,
    *,
    tolerance: float = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> bool:
    """Checks the timestamped signature of a delivery. Returns `True` or `False`, never raises.

    Accepts the value of `X-Webhook-Signature-Timestamped`, or of
    `X-Webhook-Signature-Old`: `t=<seconds>,v1=<hex>`. Computes the HMAC-SHA256
    of `<t>.<body>`, compares it in constant time with each `v1`, and rejects the
    delivery when `t` is more than `tolerance` seconds away from the receiver's
    clock.

    The window is measured against `t`, the time of that attempt, and not against
    the `timestamp` field of the body, which is the event time. A retry arrives up
    to about 8.6 hours after the event, with a fresh `t`.

    Args:
        payload: the raw body of the request, not serialised again.
        header: the value of the signature header with the timestamp.
        secret: the endpoint secret, or an iterable of secrets.
        tolerance: the window in seconds. Defaults to 300 (5 minutes).
        now: the current time in Unix seconds. Defaults to the system clock; pass
            it in tests.
    """
    return _verify_timestamped(_body(payload), header, _secrets(secret), tolerance, now)


def _header_value(headers: Mapping[str, str], header: str) -> str | None:
    """The value of a header, in whichever spelling the frameworks use."""
    wanted = {
        header.lower(),
        header.lower().replace("-", "_"),
        "http_" + header.lower().replace("-", "_"),
    }
    for name, value in headers.items():
        if str(name).lower() in wanted:
            return value
    return None


def signature_from_headers(headers: Mapping[str, str]) -> str | None:
    """Finds the signature header in any of its spellings.

    Header names are case-insensitive, and each framework hands them over its own
    way: `HTTP_X_WEBHOOK_SIGNATURE` in WSGI, for example.
    """
    return _header_value(headers, SIGNATURE_HEADER)


def timestamped_signature_from_headers(headers: Mapping[str, str]) -> str | None:
    """Finds the timestamped signature header in any of its spellings."""
    return _header_value(headers, TIMESTAMPED_SIGNATURE_HEADER)


def previous_signature_from_headers(headers: Mapping[str, str]) -> str | None:
    """Finds the `X-Webhook-Signature-Old` header in any of its spellings."""
    return _header_value(headers, PREVIOUS_SIGNATURE_HEADER)


def verify_delivery(
    headers: Mapping[str, str],
    payload: bytes | str,
    secrets: str | bytes | Iterable[str | bytes] | None,
    *,
    tolerance: float = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
    require_timestamp: bool = True,
) -> bool:
    """Checks a whole delivery from its headers. Returns `True` or `False`, never raises.

    It checks `X-Webhook-Signature-Timestamped` and, when present,
    `X-Webhook-Signature-Old`, each against every secret given, and applies the
    `tolerance` window to `t`. With `require_timestamp=False`, a delivery without
    a valid timestamped signature falls back to `X-Webhook-Signature`, which has
    no replay protection.

    Pass the current and the previous secret during the 24 hours after a rotation.

    Args:
        headers: the request headers, in any spelling the frameworks use.
        payload: the raw body of the request, not serialised again.
        secrets: one secret or several.
        tolerance: the window in seconds. Defaults to 300 (5 minutes).
        now: the current time in Unix seconds; pass it in tests.
        require_timestamp: reject deliveries that only carry the plain signature.
    """
    keys = _secrets(secrets)
    body = _body(payload)
    for header in (
        timestamped_signature_from_headers(headers),
        previous_signature_from_headers(headers),
    ):
        if _verify_timestamped(body, header, keys, tolerance, now):
            return True
    if require_timestamp:
        return False
    return verify_webhook(body, signature_from_headers(headers), keys)


def _event_from_document(
    document: dict[str, Any],
    *,
    delivery_id: str | None = None,
    attempt_at: int | None = None,
    event_header: str | None = None,
) -> WebhookEvent:
    data = document.get("data")
    validation: Validation | None = None
    if isinstance(data, dict) and data.get("type") == "validation":
        validation = Validation.from_response(document)

    banxico_confirmed: BanxicoConfirmed | None = None
    payment_status: PaymentStatus | None = None
    if isinstance(data, dict):
        attributes = data.get("attributes")
        confirmed = attributes.get("banxico_confirmed") if isinstance(attributes, dict) else None
        if isinstance(confirmed, dict):
            banxico_confirmed = BanxicoConfirmed.from_dict(confirmed)
        status = attributes.get("payment_status") if isinstance(attributes, dict) else None
        if isinstance(status, dict):
            payment_status = PaymentStatus.from_dict(status)

    return WebhookEvent(
        event=str(document.get("event") or event_header or ""),
        timestamp=document.get("timestamp"),
        validation=validation,
        banxico_confirmed=banxico_confirmed,
        payment_status=payment_status,
        data=data if isinstance(data, dict) else {},
        raw=document,
        delivery_id=delivery_id,
        attempt_timestamp=attempt_at,
    )


def parse_webhook(
    payload: bytes | str,
    signature: str | Mapping[str, str] | None,
    secret: str | bytes | Iterable[str | bytes] | None,
    *,
    tolerance: float = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
    require_timestamp: bool = True,
) -> WebhookEvent:
    """Verifies the signature and returns the parsed event.

    `signature` is either the value of `X-Webhook-Signature`, which checks the
    body only, or the request headers, which checks the timestamped signature, the
    previous-secret signature and the `tolerance` window. Passing the headers is
    the recommended form: it rejects a delivery replayed outside the window.
    With headers, `require_timestamp=False` allows the plain signature as a
    fallback.

    `secret` is one secret or several; pass the current and the previous one
    during the 24 hours after a rotation.

    Raises:
        SignatureVerificationError: when the signature does not match the body or
            falls outside the window. The body is not parsed in that case.
    """
    delivery_id: str | None = None
    attempt_at: int | None = None
    event_header: str | None = None
    if isinstance(signature, Mapping):
        verified = verify_delivery(
            signature,
            payload,
            secret,
            tolerance=tolerance,
            now=now,
            require_timestamp=require_timestamp,
        )
        delivery_id = _header_value(signature, DELIVERY_ID_HEADER)
        event_header = _header_value(signature, EVENT_HEADER)
        attempt_at = attempt_timestamp(timestamped_signature_from_headers(signature))
    else:
        verified = verify_webhook(payload, signature, secret)
    if not verified:
        raise SignatureVerificationError(
            "The delivery signature does not match the body received, or its timestamp is "
            "outside the tolerance window. Check that the body is the raw one, not "
            "serialised again, and that the secret belongs to the endpoint that sends."
        )

    body = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    try:
        parsed: Any = json.loads(body)
    except ValueError as exc:
        raise SignatureVerificationError("The delivery body is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise SignatureVerificationError("The delivery body is not a JSON object")
    return _event_from_document(
        parsed, delivery_id=delivery_id, attempt_at=attempt_at, event_header=event_header
    )


__all__ = [
    "DEFAULT_TOLERANCE_SECONDS",
    "DELIVERY_ID_HEADER",
    "EVENT_HEADER",
    "PREVIOUS_SIGNATURE_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMPED_SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "attempt_timestamp",
    "compute_signature",
    "parse_webhook",
    "previous_signature_from_headers",
    "signature_from_headers",
    "timestamped_signature_from_headers",
    "verify_delivery",
    "verify_webhook",
    "verify_webhook_timestamped",
]
