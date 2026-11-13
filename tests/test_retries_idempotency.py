"""Retries and idempotency: what is repeated, how long to wait and with which key."""

from __future__ import annotations

import pytest

from conftest import RecordingServer, make_client
from veriko import InvalidRequestError, RateLimitError, Veriko
from veriko._http import parse_retry_after


def _validate(client: Veriko) -> None:
    client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )


# ── What is retried ─────────────────────────────────────────────────────────


def test_a_503_is_retried_and_the_second_response_wins(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-503")
    server.enqueue_recording("validate-valid")

    validation = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    assert validation.status == "valid"
    assert len(server.requests) == 2


def test_a_429_is_retried_waiting_what_retry_after_says(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps)
    server.enqueue_recording("validate-429")
    server.enqueue_recording("validate-valid")

    validation = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    assert validation.status == "valid"
    # `Retry-After: 7` wins over the client's own backoff.
    assert sleeps == [7.0]


def test_a_429_for_exhausted_quota_is_not_retried(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps)
    server.enqueue_recording("validate-429-quota")

    with pytest.raises(RateLimitError) as raised:
        _validate(client)

    # The plan quota does not refill in seconds: a single request, no wait.
    assert raised.value.code == "quota_exceeded"
    assert len(server.requests) == 1
    assert sleeps == []


def test_a_422_is_not_retried(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-422")

    with pytest.raises(InvalidRequestError):
        _validate(client)

    # A single request: a 4xx must be fixed, not repeated.
    assert len(server.requests) == 1


def test_without_retries_the_first_429_is_raised(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps, max_retries=0)
    server.enqueue_recording("validate-429")

    with pytest.raises(RateLimitError):
        _validate(client)

    assert len(server.requests) == 1
    assert sleeps == []


def test_own_backoff_is_used_when_there_is_no_retry_after(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps)
    server.enqueue_recording("validate-503", times=2)
    server.enqueue_recording("validate-valid")

    _validate(client)

    assert len(sleeps) == 2
    assert all(wait > 0 for wait in sleeps)
    # Exponential: the second wait is longer than the first even with jitter.
    assert sleeps[1] > sleeps[0] / 2


# ── Idempotencia ────────────────────────────────────────────────────────────


def test_the_integrator_idempotency_key_travels_as_is(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
        idempotency_key="pedido-4f3a2b1c",
    )

    assert server.requests[0].header("idempotency-key") == "pedido-4f3a2b1c"


def test_without_a_key_the_sdk_sets_one_and_repeats_it_on_retries(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps)
    server.enqueue_recording("validate-503")
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    keys = [request.header("idempotency-key") for request in server.requests]
    assert len(keys) == 2
    assert keys[0] is not None
    # The same key on the retry: that is what prevents a duplicate validation.
    assert keys[0] == keys[1]
    assert keys[0].startswith("veriko-python-")


def test_two_different_calls_do_not_share_a_key(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-valid", times=2)

    _validate(client)
    _validate(client)

    first = server.requests[0].header("idempotency-key")
    second = server.requests[1].header("idempotency-key")
    assert first != second


def test_the_generated_key_meets_the_api_pattern(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-valid")

    _validate(client)

    key = server.requests[0].header("idempotency-key")
    assert key is not None
    assert 1 <= len(key) <= 255
    assert all(character.isalnum() or character in "-_" for character in key)


# ── Reading `Retry-After` ───────────────────────────────────────────────────


def test_retry_after_in_seconds() -> None:
    assert parse_retry_after("12") == 12.0


def test_retry_after_as_an_http_date() -> None:
    wait = parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT")
    assert wait is not None
    assert wait > 0


def test_retry_after_missing_or_unreadable() -> None:
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("pronto") is None
