"""The `client.beneficiaries` family: the allowlist and its bulk import."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import RecordingServer
from veriko import ConfigurationError, InvalidRequestError, Veriko

IMPORT_ID = "42"


# ── The allowlist ───────────────────────────────────────────────────────────


def test_register_a_beneficiary(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiary-created")

    beneficiary = client.beneficiaries.create(
        account_number="012180004412345678", label="Proveedor ABC"
    )

    assert server.requests[0].method == "POST"
    assert server.requests[0].json() == {
        "account_number": "012180004412345678",
        "label": "Proveedor ABC",
    }
    assert beneficiary.id == "50"
    assert beneficiary.account_type == "clabe"
    assert beneficiary.bank_name == "BBVA MEXICO"
    assert beneficiary.is_archived is False
    assert beneficiary.reactivated is False


def test_registering_an_archived_account_reactivates_it(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("beneficiary-reactivated")

    beneficiary = client.beneficiaries.create(account_number="012180004412345678")

    assert beneficiary.reactivated is True
    assert beneficiary.id == "42"
    assert beneficiary.is_archived is False


def test_registering_without_an_account_does_not_reach_the_api(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.beneficiaries.create(account_number="")

    assert raised.value.code == "account_number_required"
    assert server.requests == []


def test_list_returns_the_unpaginated_list(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-list")

    beneficiaries = client.beneficiaries.list()

    assert len(beneficiaries) == 2
    assert [b.account_type for b in beneficiaries] == ["clabe", "phone"]
    assert beneficiaries[0].is_archived is False
    assert beneficiaries[1].is_archived is True


def test_list_filters_by_archived(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-list")

    client.beneficiaries.list(with_archived="1")

    assert "with_archived=1" in server.requests[0].path


def test_update_a_beneficiary(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiary-updated")

    beneficiary = client.beneficiaries.update("51", bank_code="40021", label="Cuenta DiMo HSBC")

    assert server.requests[0].method == "PUT"
    assert server.requests[0].path == "/v1/beneficiaries/51"
    assert server.requests[0].json() == {"bank_code": "40021", "label": "Cuenta DiMo HSBC"}
    assert beneficiary.bank_code == "40021"


def test_an_empty_update_does_not_reach_the_api(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.beneficiaries.update("51")

    assert raised.value.code == "no_valid_fields"
    assert server.requests == []


def test_archive_a_beneficiary(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("no-content")

    client.beneficiaries.delete("42")

    assert server.requests[0].method == "DELETE"
    assert server.requests[0].path == "/v1/beneficiaries/42"


# ── Comprobaciones previas ──────────────────────────────────────────────────


def test_look_up_an_account_in_the_list(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiary-lookup")

    result = client.beneficiaries.lookup("012180004412345678")

    assert server.requests[0].path == "/v1/beneficiaries/lookup?account=012180004412345678"
    assert result.account_type == "clabe"
    assert result.bank_code == "40012"
    assert result.label == "Proveedor ABC"


def test_validate_the_structure_of_an_account(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("account-validation")

    result = client.beneficiaries.validate_account("012180004412345678", type="clabe")

    assert server.requests[0].path == (
        "/v1/beneficiaries/validate-account?account=012180004412345678&type=clabe"
    )
    assert result.account_type == "clabe"
    assert result.checksum_valid is True
    assert result.bank == {"banxico_code": "40012", "name": "BBVA MEXICO"}
    assert result.card is None


# ── Export and template ─────────────────────────────────────────────────────


def test_export_the_beneficiaries(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-export-csv")

    export = client.beneficiaries.export(format="csv", with_archived="0")

    assert "format=csv" in server.requests[0].path
    assert "with_archived=0" in server.requests[0].path
    assert export.filename == "cuentas-2026-05-16.csv"
    assert export.content.startswith(b"Alias,Cuenta,Tipo")


def test_download_the_import_template(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-template")

    template = client.beneficiaries.import_template(format="csv")

    assert server.requests[0].path == "/v1/beneficiaries/imports/template?format=csv"
    assert template.filename == "beneficiarios_plantilla.csv"
    assert template.content.startswith(b"Alias,Cuenta,Tipo")


def test_a_template_with_a_format_outside_the_list(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(ConfigurationError):
        client.beneficiaries.import_template(format="pdf")

    assert server.requests == []


# ── The import cycle ────────────────────────────────────────────────────────


def test_start_an_import_from_bytes(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-created")

    job = client.beneficiaries.import_start(
        b"Alias,Cuenta\nProveedor,012180004412345678\n", parse_mode="template"
    )

    request = server.requests[0]
    assert request.path == "/v1/beneficiaries/imports"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="parse_mode"' in request.body
    assert b"template" in request.body
    assert b'name="file"; filename="beneficiaries.csv"' in request.body
    assert b"012180004412345678" in request.body
    assert job.id == IMPORT_ID
    assert job.status == "pending"


def test_start_an_import_from_a_path(
    client: Veriko, server: RecordingServer, tmp_path: Path
) -> None:
    server.enqueue_recording("beneficiaries-import-created")
    file = tmp_path / "beneficiaries.csv"
    file.write_text("Alias,Cuenta\nProveedor,012180004412345678\n", encoding="utf-8")

    client.beneficiaries.import_start(file)

    body = server.requests[0].body
    assert b'filename="beneficiaries.csv"' in body
    assert b"012180004412345678" in body


def test_read_the_status_of_an_import(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-status")

    job = client.beneficiaries.import_status(IMPORT_ID)

    assert server.requests[0].path == "/v1/beneficiaries/imports/" + IMPORT_ID
    assert job.status == "preview_ready"
    assert job.total_rows == 150
    assert job.is_preview_ready is True
    assert job.is_settled is True
    assert job.is_terminal is False


def test_preview_the_extracted_rows(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-preview")

    page = client.beneficiaries.import_preview(IMPORT_ID, per_page=25)

    assert len(page) == 2
    assert page.total == 150
    assert page.total_pages == 6
    assert page[0].status == "valid"
    assert page[0].parsed_bank_name == "BBVA MEXICO"
    assert page[1].status == "correctable"
    assert page[1].error_codes == ["phone_bank_code_missing"]
    # The job snapshot is available in the page meta.
    assert page.meta["job"]["attributes"]["status"] == "preview_ready"


def test_preview_filters_by_bucket(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-preview")

    client.beneficiaries.import_preview(IMPORT_ID, buckets=["valid", "correctable"])

    route = server.requests[0].path
    # The server reads an array as `buckets[]=a&buckets[]=b`.
    assert "buckets%5B%5D=valid" in route
    assert "buckets%5B%5D=correctable" in route
    assert "buckets=" not in route.replace("buckets%5B%5D=", "")


def test_correct_a_row_of_the_preview(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-row-updated")

    fila = client.beneficiaries.import_edit_row(
        IMPORT_ID, "102", parsed_account_type="phone", parsed_bank_code="40012"
    )

    assert server.requests[0].method == "PATCH"
    assert server.requests[0].path == "/v1/beneficiaries/imports/" + IMPORT_ID + "/rows/102"
    assert server.requests[0].json() == {
        "parsed_account_type": "phone",
        "parsed_bank_code": "40012",
    }
    assert fila.status == "valid"
    assert fila.parsed_bank_code == "40012"


def test_correcting_a_row_without_fields_does_not_reach_the_api(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.beneficiaries.import_edit_row(IMPORT_ID, "102")

    assert raised.value.code == "no_valid_fields"
    assert server.requests == []


def test_remove_a_row_from_the_preview(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("no-content")

    client.beneficiaries.import_remove_row(IMPORT_ID, "102")

    assert server.requests[0].method == "DELETE"
    assert server.requests[0].path == "/v1/beneficiaries/imports/" + IMPORT_ID + "/rows/102"


def test_cancel_an_unconfirmed_import(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("no-content")

    client.beneficiaries.import_cancel(IMPORT_ID)

    assert server.requests[0].method == "DELETE"
    assert server.requests[0].path == "/v1/beneficiaries/imports/" + IMPORT_ID


def test_an_unusable_identifier_cancels_nothing(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(ConfigurationError):
        client.beneficiaries.import_cancel("42/commit")

    assert server.requests == []


def test_confirm_the_import(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-committed")

    job = client.beneficiaries.import_commit(IMPORT_ID)

    assert server.requests[0].method == "POST"
    assert server.requests[0].path == "/v1/beneficiaries/imports/" + IMPORT_ID + "/commit"
    assert job.status == "committing"


def test_wait_until_the_import_is_ready(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-parsing")
    server.enqueue_recording("beneficiaries-import-status")
    waits: list[float] = []

    job = client.beneficiaries.import_wait(IMPORT_ID, poll_interval=2.0, sleep=waits.append)

    assert job.status == "preview_ready"
    assert waits == [2.0]
    # The status endpoint exposes no ETag: `If-None-Match` is not sent.
    assert server.requests[0].header("if-none-match") is None
    assert server.requests[1].header("if-none-match") is None
