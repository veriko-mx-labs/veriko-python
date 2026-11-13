"""Validating a transfer: what is sent and what is read back."""

from __future__ import annotations

import json

import pytest

from conftest import TEST_API_KEY, RecordingServer, make_client
from veriko import CandidateMatch, InvalidRequestError, RetryPolicy, Veriko


def test_valid_verdict_carries_a_receipt(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-valid")

    validation = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        tracking_key="MXBA20250315001234",
        numeric_reference="1234567",
        sender_bank="BANCO NACIONAL DE MEXICO",
        receiver_bank="BBVA MEXICO",
        beneficiary_account="012180004412345678",
    )

    assert validation.status == "valid"
    assert validation.banxico_status == "valid"
    assert validation.id == "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    assert validation.has_cep is True
    assert validation.is_terminal is True
    assert validation.processing_time_ms == 1320
    assert validation.request_data["tracking_key"] == "MXBA20250315001234"


def test_not_found_verdict_carries_no_receipt(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-not-found")

    validation = client.validate_transfer(
        send_date="2025-03-16",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250316000001",
    )

    assert validation.status == "not_found"
    assert validation.has_cep is False
    # A `not_found` is terminal for this call, but it does not say the
    # transfer does not exist: the CEP can take time to be published.
    assert validation.is_terminal is True


def test_returned_verdict_still_delivers_the_receipt(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-returned")

    validation = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    assert validation.status == "returned"
    assert validation.has_cep is True


def test_the_request_carries_the_fields_in_the_body(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        tracking_key="MXBA20250315001234",
        beneficiary_account="012180004412345678",
        sender_bank="BANCO NACIONAL DE MEXICO",
    )

    sent = server.requests[0]
    assert sent.method == "POST"
    assert sent.path == "/v1/validate"
    assert sent.header("authorization") == "Bearer " + TEST_API_KEY
    body = sent.json()
    assert body == {
        "send_date": "2025-03-15",
        "amount": 15000.50,
        "tracking_key": "MXBA20250315001234",
        "beneficiary_account": "012180004412345678",
        "sender_bank": "BANCO NACIONAL DE MEXICO",
    }
    # Fields that are not passed do not travel as `null`: they are not sent.
    assert "numeric_reference" not in body
    assert "retry_policy" not in body


def test_the_retry_policy_travels_in_the_body(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-not-found")

    client.validate_transfer(
        send_date="2025-03-16",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250316000001",
        retry_policy=RetryPolicy(
            max_retries=3, interval_seconds=600, outcomes=["not_found", "cep_unavailable"]
        ),
    )

    assert server.requests[0].json()["retry_policy"] == {
        "enabled": True,
        "max_retries": 3,
        "interval_seconds": 600,
        "outcomes": ["not_found", "cep_unavailable"],
    }


def test_without_a_transfer_identifier_the_api_is_not_called(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps)

    with pytest.raises(InvalidRequestError) as raised:
        client.validate_transfer(
            send_date="2025-03-15", amount=100.0, beneficiary_account="012180004412345678"
        )

    assert raised.value.code == "tracking_key_or_reference_required"
    assert server.requests == []


def test_the_body_travels_in_utf8_without_escapes(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
        sender_bank="BANCO DEL BAJÍO",
    )

    raw = server.requests[0].body.decode("utf-8")
    assert "BANCO DEL BAJÍO" in raw
    assert json.loads(raw)["sender_bank"] == "BANCO DEL BAJÍO"


def test_read_a_validation_by_its_id(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-retrying")

    validation = client.get_validation("f47ac10b-58cc-4372-a567-0e02b2c3d479")

    assert server.requests[0].path == "/v1/validations/f47ac10b-58cc-4372-a567-0e02b2c3d479"
    assert validation.retry_state is not None
    assert validation.retry_state.enabled is True
    assert validation.retry_state.attempts_completed == 2
    assert validation.retry_state.next_attempt_at == "2025-03-15T14:42:11Z"


def test_without_a_beneficiary_account_the_api_is_not_called(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps)

    with pytest.raises(InvalidRequestError) as raised:
        client.validate_transfer(
            send_date="2025-03-15",
            amount=100.0,
            tracking_key="MXBA20250315001234",
            beneficiary_account="",
        )

    assert raised.value.code == "account_required"
    assert server.requests == []


def test_without_an_account_or_candidates_the_api_is_not_called(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.validate_transfer(
            send_date="2025-03-15", amount=100.0, tracking_key="MXBA20250315001234"
        )

    assert raised.value.code == "account_required"

    with pytest.raises(InvalidRequestError) as queued:
        client.validations.enqueue(send_date="2025-03-15", amount=100.0, tracking_key="MXBA1")

    assert queued.value.code == "account_required"
    assert server.requests == []


def test_candidate_accounts_travel_instead_of_the_account(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-candidates")

    validation = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        tracking_key="MXBA20250315001234",
        candidate_accounts=["012180004412345678", "002010077777777771"],
    )

    body = server.requests[0].json()
    assert body["candidate_accounts"] == ["012180004412345678", "002010077777777771"]
    assert "beneficiary_account" not in body
    assert validation.candidate_match == CandidateMatch(index=1, account_last4="7771")


def test_without_candidates_the_validation_has_no_candidate_match(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid")

    validation = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    assert "candidate_accounts" not in server.requests[0].json()
    assert validation.candidate_match is None


def test_the_account_and_the_candidates_are_mutually_exclusive(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.validate_transfer(
            send_date="2025-03-15",
            amount=100.0,
            tracking_key="MXBA20250315001234",
            beneficiary_account="012180004412345678",
            candidate_accounts=["012180004412345678", "002010077777777771"],
        )

    assert raised.value.code == "account_and_candidates_mutually_exclusive"

    with pytest.raises(InvalidRequestError) as by_image:
        client.validations.validate_ocr(
            image=b"png",
            beneficiary_account="012180004412345678",
            candidate_accounts=["012180004412345678", "002010077777777771"],
        )

    assert by_image.value.code == "account_and_candidates_mutually_exclusive"
    assert server.requests == []


def test_a_string_is_not_a_list_of_candidates(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.validate_transfer(
            send_date="2025-03-15",
            amount=100.0,
            tracking_key="MXBA20250315001234",
            candidate_accounts="012180004412345678",
        )

    assert raised.value.code == "candidate_accounts_invalid"
    assert server.requests == []


def test_a_list_that_does_not_comply_reaches_the_api_to_be_rejected(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-candidates")

    client.validate_transfer(
        send_date="2025-03-15",
        amount=100.0,
        tracking_key="MXBA20250315001234",
        candidate_accounts=["012180004412345678"],
    )

    assert server.requests[0].json()["candidate_accounts"] == ["012180004412345678"]


def test_client_ref_travels_in_the_body_and_comes_back_in_the_validation(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid-client-ref")

    validation = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
        client_ref="orden-4812",
    )

    assert server.requests[0].json()["client_ref"] == "orden-4812"
    assert validation.client_ref == "orden-4812"


def test_without_client_ref_it_is_not_sent_and_the_field_is_none(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid")

    validation = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    assert "client_ref" not in server.requests[0].json()
    assert validation.client_ref is None
    assert validation.duplicate_of is None
    assert validation.account_conflict is None


def test_an_empty_client_ref_reaches_the_api_to_be_rejected(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
        client_ref="",
    )

    assert server.requests[0].json()["client_ref"] == ""


def test_without_a_date_the_body_does_not_carry_it(client: Veriko, server: RecordingServer) -> None:
    # The date is optional: without it the API tries candidate dates against Banxico.
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        amount=15000.50,
        tracking_key="MXBA20250315001234",
        beneficiary_account="012180004412345678",
    )

    body = server.requests[0].json()
    assert "send_date" not in body
    assert body["amount"] == 15000.50


def test_direct_valid_carries_the_cross_checked_account(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid")

    validation = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        tracking_key="MXBA20250315001234",
        beneficiary_account="012180004412345678",
    )

    assert validation.account_cross_checked is True


def test_without_the_field_the_cross_checked_account_is_none(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-not-found")

    validation = client.validate_transfer(
        send_date="2025-03-16",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250316000001",
    )

    assert validation.account_cross_checked is None


def test_slow_banxico_returns_the_validation_in_processing(
    client: Veriko, server: RecordingServer
) -> None:
    # Without `?async=1`, the API answers `202` when Banxico takes over 45 seconds.
    server.enqueue_recording("validate-sync-deadline")

    validation = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        tracking_key="MXBA20250315001234",
        beneficiary_account="012180004412345678",
    )

    assert validation.status == "processing"
    assert validation.id == "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    assert validation.is_terminal is False
    assert "async=1" not in server.requests[0].path
