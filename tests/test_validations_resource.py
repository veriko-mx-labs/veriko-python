"""La familia `client.validations`."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import RecordingServer, make_client
from veriko import (
    AccountConflict,
    APIError,
    ConfigurationError,
    DuplicateOf,
    InvalidRequestError,
    PurgePreparation,
    PurgeResult,
    RateLimitError,
    RecheckResult,
    RetryPolicy,
    ServerError,
    ValidationSummary,
    Veriko,
)

VALIDATION_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"


# ── Validation by image ─────────────────────────────────────────────────────


def test_validate_an_image_from_bytes(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-ocr")
    image = b"\x89PNG\r\n\x1a\n test image"

    validation = client.validations.validate_ocr(
        image=image, beneficiary_account="012180004412345678"
    )

    assert validation.validation_type == "ocr"
    assert validation.status == "valid"
    # The image travels as base64: the SDK encodes it, the caller does not.
    body = server.requests[0].json()
    assert base64.b64decode(body["image"]) == image
    assert body["beneficiary_account"] == "012180004412345678"


def test_validate_an_image_from_a_path(
    client: Veriko, server: RecordingServer, tmp_path: Path
) -> None:
    server.enqueue_recording("validate-ocr")
    file = tmp_path / "comprobante.png"
    file.write_bytes(b"receipt content")

    client.validations.validate_ocr(image=file)

    assert base64.b64decode(server.requests[0].json()["image"]) == b"receipt content"


def test_validate_an_image_by_url(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-ocr")

    client.validations.validate_ocr(image_url="https://ejemplo.mx/comprobante.png")

    body = server.requests[0].json()
    assert body == {"image_url": "https://ejemplo.mx/comprobante.png"}


def test_without_an_image_or_url_the_api_is_not_called(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.validations.validate_ocr()

    assert raised.value.code == "image_required"
    assert server.requests == []


def test_a_path_that_does_not_exist_stops_before_the_api(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(ConfigurationError):
        client.validations.validate_ocr(image="no-existe-este-archivo.png")

    assert server.requests == []


# ── Async mode ──────────────────────────────────────────────────────────────


def test_enqueue_returns_the_acknowledgement_without_a_verdict(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-queued")

    queued = client.validations.enqueue(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    assert server.requests[0].path == "/v1/validate?async=1"
    assert queued.id == VALIDATION_ID
    assert queued.status == "queued"
    assert queued.etag == 'W/"0-queued"'
    assert queued.next_poll_after_seconds == 3


def test_enqueue_an_image(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-queued")

    client.validations.enqueue_ocr(image=b"png")

    assert server.requests[0].path == "/v1/validate-ocr?async=1"


def test_poll_until_the_verdict(client: Veriko, server: RecordingServer) -> None:
    # It first stays in the non-terminal state, then resolves.
    server.enqueue_recording("validation-retrying")
    server.enqueue_recording("validate-valid")
    waits: list[float] = []

    validation = client.validations.wait_for(VALIDATION_ID, poll_interval=2.0, sleep=waits.append)

    assert validation.status == "valid"
    assert waits == [2.0]


def test_polling_sends_the_etag_of_the_previous_response(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-retrying")  # trae ETag
    server.enqueue_recording("validate-valid")

    client.validations.wait_for(VALIDATION_ID, poll_interval=0.0, sleep=lambda _: None)

    assert server.requests[0].header("if-none-match") is None
    assert server.requests[1].header("if-none-match") == 'W/"3-not_found"'


def test_polling_gives_up_when_time_runs_out(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-retrying", times=4)

    with pytest.raises(TimeoutError):
        client.validations.wait_for(VALIDATION_ID, timeout=0.0, sleep=lambda _: None)


# ── Listar y recorrer ───────────────────────────────────────────────────────


def test_list_returns_a_page_with_its_counters(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-page1")

    page = client.validations.list(per_page=2, status="valid")

    assert len(page) == 2
    assert page.page == 1
    assert page.total == 3
    assert page.total_pages == 2
    assert page.has_next is True
    assert [v.status for v in page] == ["valid", "not_found"]
    # A listing carries no receipt links: the fields are the spec ones.
    assert page[0].tracking_key == "MXBA20250315001234"
    assert page[0].amount == 15000.5
    assert page[0].bank_name == "BBVA MEXICO"
    assert page[0].retry_state is not None
    assert page[0].retry_state.enabled is False
    assert page[1].beneficiary_label is None
    assert "per_page=2" in server.requests[0].path
    assert "status=valid" in server.requests[0].path


def test_iterating_requests_the_next_page_on_its_own(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validations-page1")
    server.enqueue_recording("validations-page2")

    all_ = list(client.validations.iter(per_page=2))

    assert len(all_) == 3
    assert len(server.requests) == 2
    assert "page=2" in server.requests[1].path


def test_iterating_stops_at_the_last_page(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-page2")  # already the last one

    all_ = list(client.validations.iter(page=2, per_page=2))

    assert len(all_) == 1
    assert len(server.requests) == 1


def test_the_date_filter_travels_as_from(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-page1")

    client.validations.list(from_="2025-03-01", to="2025-03-31")

    # `from` is a reserved word in Python; the argument is `from_`.
    assert "from=2025-03-01" in server.requests[0].path
    assert "to=2025-03-31" in server.requests[0].path


def test_account_statistics(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-stats")

    stats = client.validations.stats(from_="2025-03-01")

    assert stats["total"] == 47
    assert stats["valid"] == 35
    assert stats["by_type"] == {"direct": 30, "ocr": 17}
    assert "from=2025-03-01" in server.requests[0].path


# ── Reintentos ──────────────────────────────────────────────────────────────


def test_list_the_retry_attempts(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("retry-attempts")

    attempts = client.validations.retry_attempts(VALIDATION_ID)

    assert [i.attempt_number for i in attempts] == [1, 2]
    assert attempts[1].new_status == "valid"
    assert attempts[1].dispatched_at == "2025-03-17T08:20:00Z"
    assert attempts[0].proxy_pool_member == "proxy-01"


def test_change_the_retry_policy(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("retry-policy-updated")

    state = client.validations.set_retry_policy(
        VALIDATION_ID,
        RetryPolicy(max_retries=3, interval_seconds=600, outcomes=["not_found"]),
    )

    assert server.requests[0].method == "PUT"
    # The spec wraps the policy in `retry_policy`.
    assert server.requests[0].json() == {
        "retry_policy": {
            "enabled": True,
            "max_retries": 3,
            "interval_seconds": 600,
            "outcomes": ["not_found"],
        }
    }
    # The response is the cycle state, not the full validation.
    assert state.terminal_state == "pending"
    assert state.max_retries == 3
    assert state.outcomes == ["not_found"]


def test_cancel_the_pending_retries(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("retries-cancelled")

    state = client.validations.cancel_retries(VALIDATION_ID)

    assert state.terminal_state == "cancelled"
    assert state.cancelled_at == "2025-03-15T15:00:00Z"
    assert state.enabled is False


# ── Archivos ────────────────────────────────────────────────────────────────


def test_download_the_receipt_image(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-image")

    image = client.validations.image(VALIDATION_ID)

    assert image.content.startswith(b"\x89PNG")
    assert image.content_type == "image/png"
    assert image.filename == "comprobante-" + VALIDATION_ID + ".png"
    assert len(image) == len(image.content)


def test_download_the_receipt_as_pdf(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-image-pdf")

    receipt = client.validations.image(VALIDATION_ID)

    assert receipt.content.startswith(b"%PDF-")
    assert receipt.content_type == "application/pdf"
    assert receipt.filename == "comprobante-" + VALIDATION_ID + ".pdf"


def test_export_the_history_as_csv(client: Veriko, server: RecordingServer, tmp_path: Path) -> None:
    server.enqueue_recording("validations-export-csv")

    export = client.validations.export(format="csv", status="valid")

    assert "format=csv" in server.requests[0].path
    assert "status=valid" in server.requests[0].path
    assert export.filename == "validaciones-2025-03.csv"
    assert export.content.startswith(b"id,fecha,monto")
    target = export.write_to(str(tmp_path / export.filename))
    assert Path(target).read_bytes() == export.content


def test_an_export_format_outside_the_list(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(ConfigurationError):
        client.validations.export(format="pdf")

    assert server.requests == []


# ── Retirar y enviar ────────────────────────────────────────────────────────


def test_remove_a_validation_from_the_history(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("no-content")

    client.validations.delete(VALIDATION_ID)

    assert server.requests[0].method == "DELETE"
    assert server.requests[0].path == "/v1/validations/" + VALIDATION_ID


def test_send_the_receipt_to_telegram(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("telegram-accepted")

    receipt = client.validations.send_cep_to_telegram(VALIDATION_ID)

    assert receipt["queued"] is True
    assert server.requests[0].path.endswith("/cep/send-telegram")


def test_the_body_of_a_request_without_filters_carries_no_nulls(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validations-page1")

    client.validations.list()

    # Without filters the path stays clean: what is not passed is not sent.
    assert server.requests[0].path == "/v1/validations"


def test_the_root_shortcut_and_the_family_do_the_same(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid", times=2)

    from_root = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )
    from_family = client.validations.validate(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    assert from_root.id == from_family.id
    assert json.loads(server.requests[0].body) == json.loads(server.requests[1].body)


# ── What the spec requires ──────────────────────────────────────────────────


def _query(server: RecordingServer, index: int) -> dict[str, list[str]]:
    return parse_qs(urlsplit(server.requests[index].path).query)


def test_boolean_filters_travel_as_one_and_zero(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-page1", times=2)

    client.validations.list(
        playground=True, with_deleted=True, status=["valid", "not_found"], batch_id=42
    )
    client.validations.list(playground=False, with_deleted=False)

    first = _query(server, 0)
    second = _query(server, 1)
    assert first["playground"] == ["1"]
    assert first["with_deleted"] == ["1"]
    assert first["status"] == ["valid,not_found"]
    assert first["batch_id"] == ["42"]
    assert "True" not in server.requests[0].path
    # `playground` only accepts `1`: `False` means no filter.
    assert "playground" not in second
    assert second["with_deleted"] == ["0"]


def test_export_and_statistics_filters_use_the_same_values(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validations-export-csv")
    server.enqueue_recording("validations-stats")

    client.validations.export(with_deleted=True, from_="2025-01-01", limit=100)
    client.validations.stats(playground=True, with_deleted=False)

    export = _query(server, 0)
    assert export["with_deleted"] == ["1"]
    assert export["from"] == ["2025-01-01"]
    assert export["limit"] == ["100"]
    stats = _query(server, 1)
    assert stats["playground"] == ["1"]
    assert stats["with_deleted"] == ["0"]


def test_several_banks_travel_comma_separated(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-page1")
    server.enqueue_recording("validations-stats")

    client.validations.list(banks=["40012", "40014"], bank="40072")
    client.validations.stats(banks="40012,40014")

    listing = _query(server, 0)
    assert listing["banks"] == ["40012,40014"]
    assert listing["bank"] == ["40072"]
    assert _query(server, 1)["banks"] == ["40012,40014"]


def test_an_empty_list_has_no_next_page(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-empty")

    page = client.validations.list()

    assert len(page) == 0
    assert page.total == 0
    assert page.has_next is False


def test_get_leaves_the_etag_on_the_validation(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-retrying")

    validation = client.validations.get(VALIDATION_ID)

    assert validation.etag == 'W/"3-not_found"'
    assert server.requests[0].header("if-none-match") is None


def test_a_conditional_read_without_changes_is_raised_as_304(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("not-modified")

    with pytest.raises(APIError) as raised:
        client.validations.get(VALIDATION_ID, if_none_match='W/"3-not_found"')

    assert raised.value.status == 304
    assert server.requests[0].header("if-none-match") == 'W/"3-not_found"'


def test_polling_goes_through_a_304(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-queued-status")
    server.enqueue_recording("not-modified")
    server.enqueue_recording("validate-valid")

    validation = client.validations.wait_for(VALIDATION_ID, poll_interval=0.0, sleep=lambda _: None)

    assert validation.status == "valid"
    assert len(server.requests) == 3
    assert server.requests[1].header("if-none-match") == 'W/"0-queued"'
    assert server.requests[2].header("if-none-match") == 'W/"0-queued"'


def test_poll_from_the_queue_until_the_verdict(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-queued")
    server.enqueue_recording("validation-queued-status")
    server.enqueue_recording("validate-valid")
    waits: list[float] = []

    queued = client.validations.enqueue(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )
    validation = client.validations.wait_for(queued.id, poll_interval=2.0, sleep=waits.append)

    assert validation.status == "valid"
    assert waits == [2.0]


def test_a_not_found_without_retries_is_a_firm_verdict(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-not-found")

    validation = client.validations.wait_for(VALIDATION_ID, sleep=lambda _: None)

    assert validation.status == "not_found"
    assert len(server.requests) == 1


def test_export_the_history_as_xlsx(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-export-xlsx")

    export = client.validations.export(format="xlsx")

    assert "spreadsheetml.sheet" in (server.requests[0].header("accept") or "")
    assert export.filename == "veriko_validaciones_2025-03-15.xlsx"
    assert export.content.startswith(b"PK")


# ── Own reference, duplicate and account conflict ───────────────────────────


def test_validating_an_image_carries_client_ref(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-ocr-conflict")

    validation = client.validations.validate_ocr(image=b"png", client_ref="orden-4812")

    assert server.requests[0].json()["client_ref"] == "orden-4812"
    assert validation.client_ref == "orden-4812"


def test_enqueue_carries_client_ref_on_both_paths(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-queued", times=2)

    client.validations.enqueue(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
        client_ref="orden-4812",
    )
    client.validations.enqueue_ocr(image=b"png", client_ref="orden-4812")

    assert server.requests[0].path == "/v1/validate?async=1"
    assert server.requests[0].json()["client_ref"] == "orden-4812"
    assert server.requests[1].path == "/v1/validate-ocr?async=1"
    assert server.requests[1].json()["client_ref"] == "orden-4812"


def test_the_validation_exposes_the_duplicate_and_the_account_conflict(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-ocr-conflict")

    validation = client.validations.validate_ocr(image=b"png", beneficiary_account="5678")

    assert validation.duplicate_of == DuplicateOf(
        id="3fa85f64-5717-4562-b3fc-2c963f66afa6", created_at="2025-04-09T09:15:00Z"
    )
    assert validation.account_conflict == AccountConflict(sent_last4="5678", read_last4="9012")
    # The conflict does not change the verdict.
    assert validation.status == "not_found"


def test_client_ref_filters_the_listing_the_export_and_the_statistics(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validations-page1")
    server.enqueue_recording("validations-export-csv")
    server.enqueue_recording("validations-stats")

    client.validations.list(client_ref="orden 4812")
    client.validations.export(client_ref="orden 4812")
    client.validations.stats(client_ref="orden 4812")

    for index in range(3):
        assert _query(server, index)["client_ref"] == ["orden 4812"]


def test_a_listing_carries_the_client_ref_of_each_validation() -> None:
    summary = ValidationSummary.from_item(
        {"id": VALIDATION_ID, "attributes": {"status": "valid", "client_ref": "orden-4812"}}
    )

    assert summary.client_ref == "orden-4812"
    assert ValidationSummary.from_item({"id": VALIDATION_ID, "attributes": {}}).client_ref is None


# ── Varias cuentas candidatas ───────────────────────────────────────────────


def test_enqueue_with_candidates_carries_them_on_both_paths(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-queued", times=2)
    candidates = ["012180004412345678", "002010077777777771"]

    client.validations.enqueue(
        send_date="2025-03-15",
        amount=15000.50,
        tracking_key="MXBA20250315001234",
        candidate_accounts=candidates,
    )
    client.validations.enqueue_ocr(image=b"png", candidate_accounts=candidates)

    assert server.requests[0].path == "/v1/validate?async=1"
    assert server.requests[0].json()["candidate_accounts"] == candidates
    assert "beneficiary_account" not in server.requests[0].json()
    assert server.requests[1].path == "/v1/validate-ocr?async=1"
    assert server.requests[1].json()["candidate_accounts"] == candidates


def test_validating_an_image_with_candidates_does_not_send_the_account(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-ocr")

    client.validations.validate_ocr(
        image_url="https://ejemplo.mx/comprobante.png",
        candidate_accounts=("012180004412345678", "002010077777777771"),
    )

    assert server.requests[0].json() == {
        "image_url": "https://ejemplo.mx/comprobante.png",
        "candidate_accounts": ["012180004412345678", "002010077777777771"],
    }


# ── Keeping the receipt or not ──────────────────────────────────────────────


def test_retain_image_false_travels_and_the_validation_says_the_file_is_not_kept(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-ocr-not-retained")

    validation = client.validations.validate_ocr(image=b"png", retain_image=False)

    assert server.requests[0].json()["retain_image"] is False
    assert validation.image_retained is False
    assert "image_path" not in validation.attributes


def test_without_retain_image_it_is_not_sent_and_the_validation_reports_nothing(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-ocr")

    validation = client.validations.validate_ocr(image=b"png")

    assert "retain_image" not in server.requests[0].json()
    assert validation.image_retained is None
    assert validation.purged_at is None
    assert validation.is_purged is False


def test_retain_image_true_also_travels_on_the_async_path(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-queued")

    client.validations.enqueue_ocr(image=b"png", retain_image=True)

    assert server.requests[0].json()["retain_image"] is True


def test_corrected_banks_travel_in_the_ocr_body(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-queued")

    client.validations.enqueue_ocr(image=b"png", sender_bank="40012", receiver_bank="40002")

    body = server.requests[0].json()
    assert body["sender_bank"] == "40012"
    assert body["receiver_bank"] == "40002"


def test_without_corrected_banks_the_ocr_body_does_not_carry_them(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-ocr")

    client.validations.validate_ocr(image=b"png")

    body = server.requests[0].json()
    assert "sender_bank" not in body
    assert "receiver_bank" not in body


# ── Reviewing the payment status ────────────────────────────────────────────


def test_recheck_a_validation_that_becomes_returned(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-recheck-returned")

    result = client.validations.recheck(VALIDATION_ID)

    assert isinstance(result, RecheckResult)
    assert server.requests[0].method == "POST"
    assert server.requests[0].path == "/v1/validations/" + VALIDATION_ID + "/recheck"
    assert server.requests[0].body == b""
    assert result.changed is True
    assert result.previous_status == "valid"
    assert result.checked_at == "2026-10-02T09:15:44Z"
    assert result.validation.id == VALIDATION_ID
    assert result.validation.status == "returned"
    assert result.validation.is_terminal is True
    assert result.validation.has_cep is True


def test_recheck_a_validation_that_stays_valid(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-recheck-unchanged")

    result = client.validations.recheck(VALIDATION_ID)

    assert result.changed is False
    assert result.previous_status == "valid"
    assert result.validation.status == "valid"


def test_the_recheck_leaves_the_payment_status_on_the_validation(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-recheck-returned")
    server.enqueue_recording("validate-valid")

    returned = client.validations.recheck(VALIDATION_ID).validation
    unchecked = client.get_validation(VALIDATION_ID)

    assert returned.payment_status is not None
    assert returned.payment_status.code == "devuelto"
    assert returned.payment_status.label == "Devuelto"
    assert returned.payment_status.reversed is True
    assert returned.payment_status.settled is False
    assert returned.payment_status.checked_at == "2026-10-02T09:15:44Z"
    assert unchecked.payment_status is None


def test_a_recheck_too_early_is_not_retried_on_its_own(
    server: RecordingServer, sleeps: list[float]
) -> None:
    server.enqueue_recording("validate-429")
    client = make_client(server, sleeps)

    with pytest.raises(RateLimitError) as raised:
        client.validations.recheck(VALIDATION_ID)

    assert raised.value.retry_after == 7
    assert len(server.requests) == 1
    assert sleeps == []


def test_a_recheck_with_banxico_down_is_not_retried_on_its_own(
    server: RecordingServer, sleeps: list[float]
) -> None:
    server.enqueue_recording("validate-503")
    client = make_client(server, sleeps)

    with pytest.raises(ServerError) as raised:
        client.validations.recheck(VALIDATION_ID)

    assert raised.value.status == 503
    assert len(server.requests) == 1


def test_a_purged_validation_answers_410(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-purged-410")

    with pytest.raises(APIError) as raised:
        client.validations.recheck(VALIDATION_ID)

    assert raised.value.status == 410
    assert raised.value.code == "validation_purged"


# ── Borrado definitivo ──────────────────────────────────────────────────────


def test_prepare_the_purge_returns_the_token_and_what_would_be_deleted(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-purge-prepare")

    preparation = client.validations.prepare_purge(VALIDATION_ID)

    assert isinstance(preparation, PurgePreparation)
    assert server.requests[0].method == "POST"
    assert server.requests[0].path == "/v1/validations/" + VALIDATION_ID + "/purge/prepare"
    assert preparation.id == VALIDATION_ID
    assert preparation.confirmation_token == "eyJhZG1pbl9pZCI6Ii4uLiJ9.q1w2e3r4t5y6u7i8o9p0"
    assert preparation.expires_in == 120
    assert preparation.irreversible is True
    assert preparation.will_delete["image"] is True
    assert preparation.will_delete["stored_data"][0] == "request_data"
    assert "purged_at" in preparation.will_keep
    assert preparation.cancels_pending_retries is False
    assert preparation.refunds_quota is False
    assert preparation.same_image_validation_ids == []


def test_execute_the_purge_sends_the_token_and_returns_what_was_deleted(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-purge-executed")

    result = client.validations.execute_purge(
        VALIDATION_ID, confirmation_token="eyJhZG1pbl9pZCI6Ii4uLiJ9.q1w2e3r4t5y6u7i8o9p0"
    )

    assert isinstance(result, PurgeResult)
    assert server.requests[0].method == "POST"
    assert server.requests[0].path == "/v1/validations/" + VALIDATION_ID + "/purge/execute"
    assert server.requests[0].json() == {
        "confirmation_token": "eyJhZG1pbl9pZCI6Ii4uLiJ9.q1w2e3r4t5y6u7i8o9p0"
    }
    assert result.id == VALIDATION_ID
    assert result.purged_at == "2026-10-02T09:30:00Z"
    assert result.file_removal == "complete"
    assert result.deleted["image"] is True
    assert result.deleted["webhook_deliveries"] == 1


def test_executing_the_purge_without_a_token_does_not_call_the_api(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.validations.execute_purge(VALIDATION_ID, confirmation_token="")

    assert raised.value.code == "confirmation_token_missing"
    assert server.requests == []


def test_executing_the_purge_is_not_retried_on_its_own(
    server: RecordingServer, sleeps: list[float]
) -> None:
    server.enqueue_recording("validate-503")
    server.enqueue_recording("validation-purge-executed")
    client = make_client(server, sleeps)

    with pytest.raises(ServerError):
        client.validations.execute_purge(VALIDATION_ID, confirmation_token="token")

    assert len(server.requests) == 1
    assert sleeps == []


def test_a_purged_validation_is_left_as_a_tombstone(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-purged-tombstone")

    validation = client.get_validation(VALIDATION_ID)

    assert validation.is_purged is True
    assert validation.purged_at == "2026-10-02T09:30:00Z"
    assert validation.status == "valid"
    assert validation.attributes["normalized_data"] == {"amount": 15000.5}
    assert validation.request_data == {}
    assert validation.has_cep is False
