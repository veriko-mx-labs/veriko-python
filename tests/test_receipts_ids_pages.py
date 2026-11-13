"""Receipt checks, identifier formats, page sizes, typed filters and downloaded headers."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import RecordedResponse, RecordingServer
from spec_support import find_operation, load_spec, schema_properties
from veriko import (
    CEP_EXPORT_ERROR_CODES,
    CEP_EXPORT_STATUSES,
    CEP_EXPORT_TERMINAL_STATUSES,
    WEBHOOK_EVENTS,
    WEBHOOK_TEST_EVENT,
    ConfigurationError,
    InvalidRequestError,
    PayloadTooLargeError,
    Veriko,
    WebhookEvent,
)
from veriko.pagination import DELIVERIES_PAGE, IMPORT_PREVIEW_PAGE, VALIDATIONS_PAGE

UUID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
EMPTY = b'{"data":[],"meta":{}}'


def _query(server: RecordingServer, index: int = 0) -> dict[str, list[str]]:
    return parse_qs(urlsplit(server.requests[index].path).query)


# ── Receipts ───────────────────────────────────────────────────────────────


def test_a_receipt_over_12_mb_is_rejected_before_any_request(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(PayloadTooLargeError) as raised:
        client.validations.validate_ocr(image=b"x" * (12 * 1024 * 1024 + 1))

    assert raised.value.local
    assert raised.value.code == "body_too_large"
    assert server.requests == []


def test_a_receipt_file_over_12_mb_is_rejected_without_reading_it(
    client: Veriko, server: RecordingServer, tmp_path: Path
) -> None:
    big = tmp_path / "big.png"
    with open(big, "wb") as handle:
        handle.truncate(12 * 1024 * 1024 + 1)

    with pytest.raises(PayloadTooLargeError):
        client.validations.enqueue_ocr(image=big)

    assert server.requests == []


def test_an_image_url_has_to_be_https(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.validations.validate_ocr(image_url="http://example.com/receipt.png")

    assert raised.value.code == "invalid_url_scheme"
    assert raised.value.local
    assert server.requests == []


def test_a_pdf_of_more_than_three_pages_is_rejected(
    client: Veriko, server: RecordingServer
) -> None:
    pdf = b"%PDF-1.4\n" + b"<< /Type /Page >>\n" * 4 + b"<< /Type /Pages >>\n"

    with pytest.raises(InvalidRequestError) as raised:
        client.validations.validate_ocr(image=pdf)

    assert raised.value.code == "pdf_too_many_pages"
    assert server.requests == []


def test_a_pdf_of_three_pages_and_a_data_uri_go_through(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-ocr")
    server.enqueue_recording("validate-ocr")
    pdf = b"%PDF-1.4\n" + b"<< /Type /Page >>\n" * 3 + b"<< /Type /Pages >>\n"

    client.validations.validate_ocr(image=pdf)
    client.validations.validate_ocr(image="data:image/png;base64,iVBORw0KGgo=")

    assert server.requests[0].json()["image"]
    assert server.requests[1].json()["image"] == "data:image/png;base64,iVBORw0KGgo="


# ── Identifiers ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["val-1", "42", "../x", UUID + "/cep", "", " "])
def test_a_validation_id_has_to_be_a_uuid(
    client: Veriko, server: RecordingServer, value: str
) -> None:
    with pytest.raises(ConfigurationError):
        client.validations.get(value)

    assert server.requests == []


@pytest.mark.parametrize("value", [UUID, "42", "abc", "4 2", "-1"])
def test_a_beneficiary_or_import_id_has_to_be_an_integer(
    client: Veriko, server: RecordingServer, value: str
) -> None:
    server.enqueue(RecordedResponse(204, b""))

    if value in (UUID, "abc", "4 2", "-1"):
        with pytest.raises(ConfigurationError):
            client.beneficiaries.delete(value)
        assert server.requests == []
    else:
        client.beneficiaries.delete(value)
        assert server.requests[0].path == "/v1/beneficiaries/42"


def test_integer_ids_are_accepted_as_integers(client: Veriko, server: RecordingServer) -> None:
    server.enqueue(RecordedResponse(204, b""))

    client.beneficiaries.import_cancel(42)

    assert server.requests[0].path == "/v1/beneficiaries/imports/42"


def test_the_bin_and_the_year_follow_their_spec_format(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(ConfigurationError):
        client.catalog.bin_lookup("12345")
    with pytest.raises(ConfigurationError):
        client.catalog.spei_calendar(99)

    assert server.requests == []


def test_the_path_formats_are_the_ones_of_the_spec() -> None:
    for method, path, parameter, kind in [
        ("GET", "/validations/x", "id", ("string", "uuid")),
        ("GET", "/webhooks/x/deliveries", "id", ("string", "uuid")),
        ("GET", "/finance/ceps/exports/x", "id", ("string", "uuid")),
        ("DELETE", "/beneficiaries/1", "id", ("integer", None)),
        ("GET", "/beneficiaries/imports/1", "id", ("integer", None)),
        ("DELETE", "/beneficiaries/imports/1/rows/2", "row_id", ("integer", None)),
    ]:
        template, operation = find_operation(method, path)
        shared = load_spec()["paths"][template].get("parameters", [])
        for entry in [*shared, *operation.get("parameters", [])]:
            if entry.get("name") == parameter:
                assert (entry["schema"]["type"], entry["schema"].get("format")) == kind, path


# ── Page sizes and typed filters ───────────────────────────────────────────


def _parameter(path: str, name: str) -> dict[str, object]:
    template, operation = find_operation("GET", path)
    found: dict[str, object] = next(
        entry["schema"] for entry in operation["parameters"] if entry.get("name") == name
    )
    assert template
    return found


def test_the_page_sizes_are_the_ones_of_the_spec() -> None:
    for path, size in [
        ("/validations", VALIDATIONS_PAGE),
        ("/webhooks/deliveries", DELIVERIES_PAGE),
        ("/beneficiaries/imports/1/preview", IMPORT_PREVIEW_PAGE),
    ]:
        schema = _parameter(path, "per_page")
        assert (schema["default"], schema["maximum"]) == (size.default, size.maximum), path


def test_a_page_without_counters_reports_the_default_of_its_endpoint(
    client: Veriko, server: RecordingServer
) -> None:
    for _ in range(3):
        server.enqueue(RecordedResponse(200, EMPTY, {"Content-Type": "application/json"}))

    assert client.validations.list().per_page == 10
    assert client.webhooks.deliveries().per_page == 50
    assert client.beneficiaries.import_preview(7).per_page == 25


def test_iterators_ask_for_the_largest_page_unless_told_otherwise(
    client: Veriko, server: RecordingServer
) -> None:
    for _ in range(3):
        server.enqueue(RecordedResponse(200, EMPTY, {"Content-Type": "application/json"}))

    list(client.validations.iter())
    list(client.webhooks.iter_deliveries())
    list(client.beneficiaries.iter_import_preview(7, per_page=30))

    assert _query(server, 0)["per_page"] == ["50"]
    assert _query(server, 1)["per_page"] == ["100"]
    assert _query(server, 2)["per_page"] == ["30"]


def test_next_page_repeats_the_filters(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-page1")
    server.enqueue_recording("validations-page2")

    first = client.validations.list(per_page=2, type="ocr", from_="2026-04-01")
    second = first.next_page()

    assert second is not None
    assert _query(server, 1)["page"] == ["2"]
    assert _query(server, 1)["type"] == ["ocr"]
    assert _query(server, 1)["from"] == ["2026-04-01"]
    assert second.page == 2


def test_the_filters_of_stats_export_and_iterators_are_checked(client: Veriko) -> None:
    with pytest.raises(TypeError):
        client.validations.stats(typo="x")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        client.validations.export(typo="x")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        client.validations.iter(typo="x")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        client.webhooks.export_deliveries(typo="x")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        client.beneficiaries.iter_import_preview(7, typo="x")  # type: ignore[call-arg]


def test_stats_and_export_send_the_typed_filters(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-stats")
    server.enqueue_recording("validations-export-csv")

    client.validations.stats(type="ocr", retry_state="pending", with_deleted=False)
    client.validations.export(type="direct", limit=10, banks=["40012", "40014"])

    assert _query(server, 0) == {"type": ["ocr"], "retry_state": ["pending"], "with_deleted": ["0"]}
    assert _query(server, 1)["limit"] == ["10"]
    assert _query(server, 1)["banks"] == ["40012,40014"]


# ── Constants that mirror the spec ─────────────────────────────────────────


def test_webhook_events_are_the_spec_enum() -> None:
    schemas = load_spec()["components"]["schemas"]
    subscribed = schema_properties(schemas["CreateWebhookRequest"])["events"]["items"]["enum"]
    delivered = schema_properties(schema_properties(schemas["WebhookDelivery"])["attributes"])[
        "event_type"
    ]["enum"]

    assert list(WEBHOOK_EVENTS) == subscribed
    assert [*WEBHOOK_EVENTS, WEBHOOK_TEST_EVENT] == delivered


def test_the_cep_export_constants_are_the_spec_enums() -> None:
    schemas = load_spec()["components"]["schemas"]
    attributes = schema_properties(schemas["CepArchiveAttributes"])

    assert list(CEP_EXPORT_STATUSES) == attributes["status"]["enum"]
    assert set(CEP_EXPORT_TERMINAL_STATUSES) == set(attributes["status"]["x-terminal-values"])
    assert set(CEP_EXPORT_ERROR_CODES) <= set(attributes["error_code"]["enum"])


def test_an_import_status_has_no_etag_to_poll_with() -> None:
    _, operation = find_operation("GET", "/beneficiaries/imports/1")
    headers = operation["responses"]["200"].get("headers", {})
    parameters = [entry.get("name") for entry in operation.get("parameters", [])]

    assert "ETag" not in headers
    assert "If-None-Match" not in parameters


def test_a_webhook_event_tells_its_kind() -> None:
    def event(name: str) -> WebhookEvent:
        return WebhookEvent(event=name, timestamp=None, validation=None)

    assert event("validation.completed").is_validation_event
    assert event("billing.payment_failed").is_billing_event
    assert event("test").is_test
    assert not event("test").is_validation_event


# ── Downloads and language ─────────────────────────────────────────────────


def test_a_finance_download_keeps_its_headers(client: Veriko, server: RecordingServer) -> None:
    server.enqueue(
        RecordedResponse(
            200,
            b"%PDF-1.4",
            {
                "Content-Type": "application/pdf",
                "X-Finance-Folio": "VK-2026-04-0001",
                "X-Finance-Items": "12",
            },
        )
    )

    document = client.finance.statement(month="2026-04")

    assert document.headers["x-finance-folio"] == document.finance_folio == "VK-2026-04-0001"
    assert document.finance_items == 12
    assert b"".join(document.iter_bytes(3)) == b"%PDF-1.4"


def test_the_language_has_to_be_es_or_en() -> None:
    from conftest import TEST_API_KEY

    Veriko(api_key=TEST_API_KEY, accept_language="en-US,en;q=0.9")
    with pytest.raises(ConfigurationError):
        Veriko(api_key=TEST_API_KEY, accept_language="fr")
    with pytest.raises(ConfigurationError):
        Veriko(api_key=TEST_API_KEY).with_options(accept_language="de")
