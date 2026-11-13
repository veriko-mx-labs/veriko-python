"""Transport hardening: retries, idempotency keys, errors, metadata and per-call options."""

from __future__ import annotations

import json
import warnings
from decimal import Decimal

import pytest

from conftest import RecordedResponse, RecordingServer, make_client
from veriko import (
    APIError,
    AsyncVeriko,
    GoneError,
    InvalidRequestError,
    NotModifiedError,
    PayloadTooLargeError,
    QuotaExceededError,
    RateLimitError,
    ServerError,
    Veriko,
    VerikoTimeoutError,
)
from veriko._http import Transport

VALIDATION_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
WEBHOOK_ID = "a1b2c3d4-0000-4000-8000-000000000001"


def _json(status: int, body: object, **headers: str) -> RecordedResponse:
    return RecordedResponse(
        status=status,
        body=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
    )


def _error(status: int, code: str, **headers: str) -> RecordedResponse:
    return _json(
        status,
        {"errors": [{"status": str(status), "code": code, "detail": code}], "meta": {}},
        **headers,
    )


# ── Retries and keys ────────────────────────────────────────────────────────


def test_a_post_without_a_key_is_not_retried_on_5xx(
    client: Veriko, server: RecordingServer, sleeps: list[float]
) -> None:
    server.enqueue(_error(503, "service_unavailable"))
    server.enqueue_recording("webhook-created")

    with pytest.raises(ServerError):
        client.webhooks.create(url="https://example.com/hook", events=["validation.completed"])

    assert len(server.requests) == 1
    assert sleeps == []


def test_a_keyed_post_is_retried_with_the_same_key(client: Veriko, server: RecordingServer) -> None:
    server.enqueue(_error(503, "service_unavailable"))
    server.enqueue_recording("retries-cancelled")

    client.validations.cancel_retries(VALIDATION_ID)

    keys = {request.header("idempotency-key") for request in server.requests}
    assert len(server.requests) == 2
    assert len(keys) == 1
    assert None not in keys


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.validations.set_retry_policy(VALIDATION_ID, {"max_retries": 1}),
        lambda c: c.validations.cancel_retries(VALIDATION_ID),
        lambda c: c.account.update_my_retry_policy({"max_retries": 1}),
        lambda c: c.finance.create_cep_export(from_="2026-01-01", to="2026-01-31"),
    ],
    ids=["set_retry_policy", "cancel_retries", "update_my_retry_policy", "create_cep_export"],
)
def test_every_operation_with_an_idempotency_header_sends_one(
    client: Veriko, server: RecordingServer, call: object
) -> None:
    server.enqueue(_json(200, {"data": {"id": "1", "type": "x", "attributes": {}}, "meta": {}}))

    call(client)  # type: ignore[operator]

    assert server.requests[0].header("idempotency-key")


def test_a_malformed_idempotency_key_is_rejected_before_the_network(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.validations.cancel_retries(VALIDATION_ID, idempotency_key="not valid!")

    assert raised.value.code == "invalid_idempotency_key"
    assert raised.value.local is True
    assert server.requests == []


def test_a_retry_after_above_the_cap_raises_without_sleeping(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps)
    server.enqueue(_error(429, "rate_limit_exceeded", **{"Retry-After": "3600"}))

    with pytest.raises(RateLimitError) as raised:
        client.catalog.banks()

    assert raised.value.retry_after == 3600.0
    assert sleeps == []
    assert len(server.requests) == 1


def test_quota_exceeded_is_its_own_class_and_not_retried(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-429-quota")

    with pytest.raises(QuotaExceededError) as raised:
        client.catalog.banks()

    assert isinstance(raised.value, RateLimitError)
    assert len(server.requests) == 1


def test_a_delete_that_answers_404_after_a_retry_counts_as_done(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue(_error(503, "service_unavailable"))
    server.enqueue(_error(404, "not_found"))

    client.validations.delete(VALIDATION_ID)
    assert len(server.requests) == 2


# ── Errors ──────────────────────────────────────────────────────────────────


def test_410_and_413_have_their_own_classes(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-purged-410")
    with pytest.raises(GoneError) as gone:
        client.validations.get(VALIDATION_ID)
    assert gone.value.code == "validation_purged"

    server.enqueue(_error(413, "payload_too_large"))
    with pytest.raises(PayloadTooLargeError) as large:
        client.validations.get(VALIDATION_ID)
    assert isinstance(large.value, InvalidRequestError)


def test_the_error_exposes_every_entry_hint_and_docs_url(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue(
        _json(
            422,
            {
                "errors": [
                    {"status": "422", "code": "validation_error", "detail": "Invalid"},
                    {
                        "status": "422",
                        "code": "invalid_date",
                        "detail": "Bad date",
                        "hint": "Use YYYY-MM-DD",
                        "docs_url": "https://docs.veriko.mx/en/concepts/errors",
                        "source": {"pointer": "/send_date"},
                    },
                ],
                "meta": {"request_id": "abc123"},
            },
        )
    )

    with pytest.raises(InvalidRequestError) as raised:
        client.validate_transfer(
            amount=1, beneficiary_account="012180004412345678", tracking_key="K"
        )

    error = raised.value
    assert error.codes == ["validation_error", "invalid_date"]
    assert error.has_code("invalid_date")
    assert error.field_errors[0].hint == "Use YYYY-MM-DD"
    assert error.field_errors[0].pointer == "/send_date"
    assert error.request_id == "abc123"
    assert error.local is False


def test_local_checks_are_marked_and_use_api_codes(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.validate_transfer(amount=1, tracking_key="K")

    assert raised.value.code == "account_required"
    assert raised.value.local is True
    assert server.requests == []


def test_a_304_raises_not_modified_with_the_etag(client: Veriko, server: RecordingServer) -> None:
    server.enqueue(RecordedResponse(status=304, body=b"", headers={"ETag": '"v1"'}))

    with pytest.raises(NotModifiedError) as raised:
        client.validations.get(VALIDATION_ID, if_none_match='"v1"')

    assert isinstance(raised.value, APIError)
    assert raised.value.status == 304
    assert raised.value.etag == '"v1"'


# ── Response metadata ───────────────────────────────────────────────────────


def test_results_carry_the_response_metadata(client: Veriko, server: RecordingServer) -> None:
    server.enqueue(
        RecordedResponse(
            status=200,
            body=RecordedResponse.from_recording("validate-valid").body,
            headers={
                "Content-Type": "application/json",
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "99",
                "X-RateLimit-Reset": "1760000000",
                "Idempotent-Replayed": "true",
                "ETag": '"abc"',
            },
        )
    )

    validation = client.validate_transfer(
        amount=Decimal("15000.50"), beneficiary_account="012180004412345678", tracking_key="K"
    )

    info = validation.response_info
    assert info is not None
    assert info.rate_limit is not None
    assert (info.rate_limit.limit, info.rate_limit.remaining, info.rate_limit.reset) == (
        100,
        99,
        1760000000,
    )
    assert info.idempotent_replayed is True
    assert info.etag == '"abc"'
    assert json.loads(server.requests[0].body)["amount"] == 15000.5


def test_lists_and_dicts_keep_their_type_and_carry_the_metadata(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("webhooks-list")
    server.enqueue_recording("usage-limits")

    endpoints = client.webhooks.list()
    limits = client.usage.limits()

    assert isinstance(endpoints, list)
    assert endpoints.response_info is not None  # type: ignore[attr-defined]
    assert isinstance(limits, dict)
    assert limits.response_info is not None
    assert limits.response_info.status == 200


def test_a_deprecation_header_warns_once(client: Veriko, server: RecordingServer) -> None:
    import veriko._http as http

    http._deprecation_warned = False
    for _ in range(2):
        server.enqueue(
            RecordedResponse(
                status=200,
                body=b'{"data":[],"meta":{}}',
                headers={
                    "Content-Type": "application/json",
                    "Deprecation": "true",
                    "Sunset": "Mon, 01 Mar 2027 00:00:00 GMT",
                },
            )
        )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.webhooks.list()
        client.webhooks.list()

    assert len([w for w in caught if issubclass(w.category, DeprecationWarning)]) == 1


# ── Per-call options ────────────────────────────────────────────────────────


def test_with_options_overrides_timeout_headers_and_keeps_the_original(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("webhooks-list")
    custom = client.with_options(timeout=3.0, headers={"X-Trace": "t1"})

    custom.webhooks.list()

    assert server.requests[0].header("x-trace") == "t1"
    assert custom._transport.timeout == 3.0
    assert client._transport.timeout != 3.0


# ── Waiting ─────────────────────────────────────────────────────────────────


def test_wait_for_honors_next_poll_after_seconds_and_times_out_with_the_last_value(
    client: Veriko, server: RecordingServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = json.loads(RecordedResponse.from_recording("validation-queued-status").body)
    body.setdefault("meta", {})["next_poll_after_seconds"] = 7
    server.enqueue(_json(200, body))
    server.enqueue(_json(200, body))
    waits: list[float] = []
    ticks = iter([0.0, 1.0, 11.0])
    monkeypatch.setattr("veriko.resources.time.monotonic", lambda: next(ticks))

    with pytest.raises(VerikoTimeoutError) as raised:
        client.validations.wait_for(VALIDATION_ID, timeout=10.0, sleep=waits.append)

    assert waits == [7.0]
    assert raised.value.resource_id == VALIDATION_ID
    assert isinstance(raised.value, TimeoutError)
    assert raised.value.last is not None


def test_wait_for_cep_export_returns_on_a_terminal_status(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("finance-cep-export-queued")
    server.enqueue_recording("finance-cep-export-ready")
    waits: list[float] = []

    export = client.finance.wait_for_cep_export(
        "f47ac10b-58cc-4372-a567-0e02b2c3d479", poll_interval=1.5, sleep=waits.append
    )

    assert export["attributes"]["status"] == "ready"
    assert export.status == "ready"
    assert export.is_terminal
    assert waits == [1.5]


# ── Requests ────────────────────────────────────────────────────────────────


def test_enqueue_rejects_unknown_arguments(client: Veriko) -> None:
    with pytest.raises(TypeError):
        client.validations.enqueue(amount=1, beneficiary_account="x", tracking_key="K", bogus=1)
    with pytest.raises(TypeError):
        client.validations.enqueue_ocr(image=b"x", bogus=1)


def test_regenerate_secret_sends_immediate_only_when_asked(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("webhook-secret-rotated")
    server.enqueue_recording("webhook-secret-rotated")

    client.webhooks.regenerate_secret(WEBHOOK_ID)
    client.webhooks.regenerate_secret(WEBHOOK_ID, immediate=True)

    assert server.requests[0].body == b""
    assert json.loads(server.requests[1].body) == {"immediate": True}


def test_the_ocr_request_carries_recipient_is_bank(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-ocr")

    client.validations.validate_ocr(image=b"x", recipient_is_bank=True)

    assert json.loads(server.requests[0].body)["recipient_is_bank"] is True


def test_a_json_import_file_has_its_content_type(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-created")

    client.beneficiaries.import_start(b"[]", filename="rows.json")

    assert b"Content-Type: application/json" in server.requests[0].body


# ── Async client ────────────────────────────────────────────────────────────


def test_the_async_client_mirrors_the_sync_one(
    server: RecordingServer, sleeps: list[float]
) -> None:
    import asyncio

    server.enqueue_recording("validations-page1")
    server.enqueue_recording("validations-page2")
    sync = make_client(server, sleeps)
    client = AsyncVeriko.__new__(AsyncVeriko)
    client._attach(sync)

    async def run() -> list[str]:
        ids = []
        async for item in client.validations.iter():
            ids.append(item.id)
        return ids

    ids = asyncio.run(run())

    assert ids
    assert hasattr(client.finance, "wait_for_cep_export")


def test_the_generated_async_file_is_up_to_date() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "generate_async.py"
    spec = importlib.util.spec_from_file_location("generate_async", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main.__module__  # the module loads
    import sys

    argv = sys.argv
    sys.argv = ["generate_async.py", "--check"]
    try:
        assert module.main() == 0
    finally:
        sys.argv = argv


def test_transport_is_exported() -> None:
    assert Transport.__name__ == "Transport"


# ── Models ──────────────────────────────────────────────────────────────────


def _validation(status: str, retry_state: dict[str, object] | None, **extra: object) -> object:
    from veriko import Validation

    attributes: dict[str, object] = {"status": status, **extra}
    if retry_state is not None:
        attributes["retry_state"] = retry_state
    return Validation.from_response({"data": {"id": "1", "attributes": attributes}})


def test_a_retry_state_without_a_cycle_does_not_keep_the_wait_open() -> None:
    no_cycle = _validation("not_found", {"enabled": True, "terminal_state": None})
    live = _validation("not_found", {"enabled": True, "terminal_state": "pending"})
    waiting = _validation(
        "not_found",
        {"enabled": True, "terminal_state": None, "next_attempt_at": "2026-01-01T00:00:00Z"},
    )
    done = _validation("valid", {"enabled": True, "terminal_state": "pending"})

    assert no_cycle.is_settled is True  # type: ignore[attr-defined]
    assert live.is_settled is False  # type: ignore[attr-defined]
    assert waiting.is_settled is False  # type: ignore[attr-defined]
    assert done.is_settled is True  # type: ignore[attr-defined]


def test_the_validation_exposes_the_ocr_normalization_and_banxico_fields() -> None:
    validation = _validation(
        "processing",
        None,
        ocr_result={"amount": "10.00"},
        ocr_confidence=0.93,
        normalized_data={"amount": 10},
        normalization_warnings=["w"],
        banxico_result={"found": True},
        expires_at="2026-01-01T00:00:00Z",
    )

    assert validation.ocr_result == {"amount": "10.00"}  # type: ignore[attr-defined]
    assert validation.ocr_confidence == 0.93  # type: ignore[attr-defined]
    assert validation.normalized_data == {"amount": 10}  # type: ignore[attr-defined]
    assert validation.banxico_result == {"found": True}  # type: ignore[attr-defined]
    assert validation.is_pending is True  # type: ignore[attr-defined]


def test_the_queued_acknowledgement_keeps_its_extra_fields(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-queued")

    queued = client.validations.enqueue(
        amount=1, beneficiary_account="012180004412345678", tracking_key="K"
    )

    assert queued.id
    assert queued.status == "queued"
    assert queued.response_info is not None


# ── Connection reuse ───────────────────────────────────────────────────────


def test_requests_reuse_one_connection(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-limits", times=3)

    for _ in range(3):
        client.usage.limits()

    assert len({request.connection for request in server.requests}) == 1


def test_close_drops_the_connections_and_the_client_reconnects(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("usage-limits", times=2)

    client.usage.limits()
    client.close()
    client.usage.limits()

    assert server.requests[0].connection != server.requests[1].connection


def test_a_response_that_closes_the_connection_is_not_reused(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue(RecordedResponse(200, b'{"data":{}}', {"Connection": "close"}))
    server.enqueue_recording("usage-limits")

    client.usage.limits()
    client.usage.limits()

    assert server.requests[0].connection != server.requests[1].connection


def test_a_connection_the_server_dropped_is_replaced_for_a_repeatable_request(
    server: RecordingServer, sleeps: list[float]
) -> None:
    from conftest import make_client

    client = make_client(server, sleeps)
    server.enqueue_recording("usage-limits", times=2)
    client.usage.limits()
    # The server closes the idle connection behind the client's back.
    server.drop_connections()

    client.usage.limits()

    assert server.requests[0].connection != server.requests[1].connection
    assert sleeps == []


def test_the_client_is_a_context_manager(server: RecordingServer, sleeps: list[float]) -> None:
    from conftest import make_client

    server.enqueue_recording("usage-limits")
    with make_client(server, sleeps) as client:
        client.usage.limits()
