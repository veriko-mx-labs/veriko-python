"""Background CEP exports: what each method returns."""

from __future__ import annotations

import json

from conftest import RecordingServer
from veriko import Veriko

ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"


def test_create_cep_export_sends_the_range_and_returns_the_queued_export(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("finance-cep-export-queued")

    export = client.finance.create_cep_export(
        from_="2026-04-01", to="2026-04-30", idempotency_key="pedido-7"
    )

    sent = server.requests[0]
    assert sent.method == "POST"
    assert sent.path == "/v1/finance/ceps/exports"
    assert json.loads(sent.body) == {"from": "2026-04-01", "to": "2026-04-30"}
    assert sent.header("idempotency-key") == "pedido-7"
    assert export["id"] == ID
    assert export["attributes"]["status"] == "queued"
    assert export["links"]["self"] == "/v1/finance/ceps/exports/" + ID
    assert export.id == ID
    assert export.status == "queued"
    assert export.from_ == "2026-04-01"
    assert not export.is_terminal


def test_get_cep_export_returns_the_download_link_when_ready(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("finance-cep-export-ready")

    export = client.finance.get_cep_export(ID)

    assert server.requests[0].path == "/v1/finance/ceps/exports/" + ID
    assert export["attributes"]["status"] == "ready"
    assert export["attributes"]["item_count"] == 42
    assert export["links"]["download"] == "/v1/finance/ceps/exports/" + ID + "/download"
    assert export.is_ready
    assert export.item_count == 42
    assert export.download_path == "/v1/finance/ceps/exports/" + ID + "/download"


def test_list_cep_exports_returns_the_resources(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("finance-cep-exports")

    exports = client.finance.list_cep_exports()

    assert server.requests[0].path == "/v1/finance/ceps/exports"
    assert [export["id"] for export in exports] == [ID]


def test_download_cep_export_returns_the_zip(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("finance-ceps")

    document = client.finance.download_cep_export(ID)

    assert server.requests[0].path == "/v1/finance/ceps/exports/" + ID + "/download"
    assert document.content_type == "application/zip"
    assert document.filename == "finance-ceps.zip"
    assert document.content.startswith(b"PK")
