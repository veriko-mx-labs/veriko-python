"""Each HTTP status has its exception, and `code` is what callers read."""

from __future__ import annotations

import pytest

from conftest import RecordingServer
from veriko import (
    AuthenticationError,
    ConflictError,
    IdempotencyKeyInProgressError,
    InvalidRequestError,
    RateLimitError,
    Veriko,
)


def _validate(client: Veriko) -> None:
    client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )


def test_422_exposes_all_entries_not_only_the_first(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-422")

    with pytest.raises(InvalidRequestError) as raised:
        _validate(client)

    error = raised.value
    assert error.status == 422
    assert error.code == "required"
    assert error.pointer == "/data/attributes/send_date"
    assert len(error.errors) == 2
    assert [entry["code"] for entry in error.errors] == ["required", "invalid_clabe_checksum"]
    assert error.request_id == "a1b2c3d4e5f6"


def test_401_is_an_authentication_error(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-401")

    with pytest.raises(AuthenticationError) as raised:
        _validate(client)

    assert raised.value.status == 401
    assert raised.value.code == "unauthorized"


def test_409_for_an_idempotency_key_in_progress_waits_then_raises(
    client: Veriko, server: RecordingServer, sleeps: list[float]
) -> None:
    # The budget is 30 s and each wait is 1 s without `Retry-After`: 30 waits, 31 sends.
    server.enqueue_recording("validate-409", times=31)

    with pytest.raises(IdempotencyKeyInProgressError) as raised:
        _validate(client)

    assert isinstance(raised.value, ConflictError)
    assert raised.value.code == "idempotency_key_in_progress"
    assert sleeps == [1.0] * 30
    assert len(server.requests) == 31
    assert len({r.header("idempotency-key") for r in server.requests}) == 1


def test_409_for_an_idempotency_key_in_progress_resolves_when_the_first_call_ends(
    client: Veriko, server: RecordingServer, sleeps: list[float]
) -> None:
    server.enqueue_recording("validate-409", times=2)
    server.enqueue_recording("validate-valid")

    result = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    assert result.status == "valid"
    assert sleeps == [1.0, 1.0]


def test_exhausted_429_exposes_the_seconds_to_wait(client: Veriko, server: RecordingServer) -> None:
    # Three 429 responses: the client retries twice and runs out of attempts.
    server.enqueue_recording("validate-429", times=3)

    with pytest.raises(RateLimitError) as raised:
        _validate(client)

    assert raised.value.status == 429
    assert raised.value.code == "rate_limit_exceeded"
    assert raised.value.retry_after == 7.0
    assert raised.value.headers["x-ratelimit-remaining"] == "0"


def test_the_error_message_carries_the_code_and_the_request_id(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-401")

    with pytest.raises(AuthenticationError) as raised:
        _validate(client)

    text = str(raised.value)
    assert "HTTP 401" in text
    assert "unauthorized" in text
    assert "request_id=a8b9c0d1e2f3" in text
