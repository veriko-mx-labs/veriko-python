"""Exportaciones de CEP en segundo plano: lo que devuelve cada método."""

from __future__ import annotations

import json

from conftest import RecordingServer
from veriko import Veriko

ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"


def test_create_cep_export_envia_el_rango_y_devuelve_la_exportacion_en_cola(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("finance-cep-export-queued")

    export = client.finance.create_cep_export(
        from_="2026-04-01", to="2026-04-30", idempotency_key="pedido-7"
    )

    enviada = server.requests[0]
    assert enviada.method == "POST"
    assert enviada.path == "/v1/finance/ceps/exports"
    assert json.loads(enviada.body) == {"from": "2026-04-01", "to": "2026-04-30"}
    assert enviada.header("idempotency-key") == "pedido-7"
    assert export["id"] == ID
    assert export["attributes"]["status"] == "queued"
    assert export["links"]["self"] == "/v1/finance/ceps/exports/" + ID


def test_get_cep_export_devuelve_el_enlace_de_descarga_cuando_esta_lista(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("finance-cep-export-ready")

    export = client.finance.get_cep_export(ID)

    assert server.requests[0].path == "/v1/finance/ceps/exports/" + ID
    assert export["attributes"]["status"] == "ready"
    assert export["attributes"]["item_count"] == 42
    assert export["links"]["download"] == "/v1/finance/ceps/exports/" + ID + "/download"


def test_list_cep_exports_devuelve_los_recursos(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("finance-cep-exports")

    exports = client.finance.list_cep_exports()

    assert server.requests[0].path == "/v1/finance/ceps/exports"
    assert [export["id"] for export in exports] == [ID]


def test_download_cep_export_devuelve_el_zip(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("finance-ceps")

    documento = client.finance.download_cep_export(ID)

    assert server.requests[0].path == "/v1/finance/ceps/exports/" + ID + "/download"
    assert documento.content_type == "application/zip"
    assert documento.filename == "finance-ceps.zip"
    assert documento.content.startswith(b"PK")
