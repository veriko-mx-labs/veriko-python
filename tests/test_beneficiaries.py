"""La familia `client.beneficiaries`: la lista blanca y su importación masiva."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import RecordingServer
from veriko import ConfigurationError, InvalidRequestError, Veriko

IMPORT_ID = "42"


# ── La lista blanca ─────────────────────────────────────────────────────────


def test_registrar_un_beneficiario(client: Veriko, server: RecordingServer) -> None:
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


def test_registrar_sin_cuenta_no_llega_a_la_api(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.beneficiaries.create(account_number="")

    assert raised.value.code == "account_number_required"
    assert server.requests == []


def test_listar_devuelve_la_lista_sin_paginar(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-list")

    beneficiaries = client.beneficiaries.list()

    assert len(beneficiaries) == 2
    assert [b.account_type for b in beneficiaries] == ["clabe", "phone"]
    assert beneficiaries[0].is_archived is False
    assert beneficiaries[1].is_archived is True


def test_listar_filtra_por_archivados(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-list")

    client.beneficiaries.list(with_archived="1")

    assert "with_archived=1" in server.requests[0].path


def test_cambiar_un_beneficiario(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiary-updated")

    beneficiary = client.beneficiaries.update("51", bank_code="40021", label="Cuenta DiMo HSBC")

    assert server.requests[0].method == "PUT"
    assert server.requests[0].path == "/v1/beneficiaries/51"
    assert server.requests[0].json() == {"bank_code": "40021", "label": "Cuenta DiMo HSBC"}
    assert beneficiary.bank_code == "40021"


def test_un_cambio_vacio_no_llega_a_la_api(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.beneficiaries.update("51")

    assert raised.value.code == "no_valid_fields"
    assert server.requests == []


def test_archivar_un_beneficiario(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("no-content")

    client.beneficiaries.delete("42")

    assert server.requests[0].method == "DELETE"
    assert server.requests[0].path == "/v1/beneficiaries/42"


# ── Comprobaciones previas ──────────────────────────────────────────────────


def test_validar_la_estructura_de_una_cuenta(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("account-validation")

    resultado = client.beneficiaries.validate_account("012180004412345678")

    assert (
        server.requests[0].path == "/v1/beneficiaries/validate-account?account=012180004412345678"
    )
    assert resultado.account_type == "clabe"
    assert resultado.checksum_valid is True
    assert resultado.is_complete is True
    assert resultado.bank["name"] == "BBVA MEXICO"


def test_resolver_una_cuenta_de_la_lista(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiary-lookup")

    resultado = client.beneficiaries.lookup("012180004412345678")

    assert server.requests[0].path == "/v1/beneficiaries/lookup?account=012180004412345678"
    assert resultado.account_type == "clabe"
    assert resultado.bank_code == "40012"
    assert resultado.label == "Proveedor ABC"


# ── Exportación y plantilla ─────────────────────────────────────────────────


def test_exportar_los_beneficiarios(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-export-csv")

    export = client.beneficiaries.export(format="csv", with_archived="0")

    assert "format=csv" in server.requests[0].path
    assert "with_archived=0" in server.requests[0].path
    assert export.filename == "cuentas-2026-05-16.csv"
    assert export.content.startswith(b"Alias,Cuenta,Tipo")


def test_descargar_la_plantilla_de_importacion(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-template")

    plantilla = client.beneficiaries.import_template(format="csv")

    assert server.requests[0].path == "/v1/beneficiaries/imports/template?format=csv"
    assert plantilla.filename == "beneficiarios_plantilla.csv"
    assert plantilla.content.startswith(b"Alias,Cuenta,Tipo")


def test_una_plantilla_con_formato_fuera_de_la_lista(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(ConfigurationError):
        client.beneficiaries.import_template(format="pdf")

    assert server.requests == []


# ── El ciclo de importación ─────────────────────────────────────────────────


def test_iniciar_una_importacion_desde_bytes(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-created")

    trabajo = client.beneficiaries.import_start(
        b"Alias,Cuenta\nProveedor,012180004412345678\n", parse_mode="template"
    )

    peticion = server.requests[0]
    assert peticion.path == "/v1/beneficiaries/imports"
    assert peticion.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="parse_mode"' in peticion.body
    assert b"template" in peticion.body
    assert b'name="file"; filename="beneficiarios.csv"' in peticion.body
    assert b"012180004412345678" in peticion.body
    assert trabajo.id == IMPORT_ID
    assert trabajo.status == "pending"


def test_iniciar_una_importacion_desde_una_ruta(
    client: Veriko, server: RecordingServer, tmp_path: Path
) -> None:
    server.enqueue_recording("beneficiaries-import-created")
    archivo = tmp_path / "beneficiarios.csv"
    archivo.write_text("Alias,Cuenta\nProveedor,012180004412345678\n", encoding="utf-8")

    client.beneficiaries.import_start(archivo)

    cuerpo = server.requests[0].body
    assert b'filename="beneficiarios.csv"' in cuerpo
    assert b"012180004412345678" in cuerpo


def test_consultar_el_estado_de_una_importacion(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-status")

    trabajo = client.beneficiaries.import_status(IMPORT_ID)

    assert server.requests[0].path == "/v1/beneficiaries/imports/" + IMPORT_ID
    assert trabajo.status == "preview_ready"
    assert trabajo.total_rows == 150
    assert trabajo.is_preview_ready is True
    assert trabajo.is_settled is True
    assert trabajo.is_terminal is False


def test_previsualizar_las_filas_extraidas(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-preview")

    pagina = client.beneficiaries.import_preview(IMPORT_ID, per_page=25)

    assert len(pagina) == 2
    assert pagina.total == 150
    assert pagina.total_pages == 6
    assert pagina[0].status == "valid"
    assert pagina[0].parsed_bank_name == "BBVA MEXICO"
    assert pagina[1].status == "correctable"
    assert pagina[1].error_codes == ["phone_bank_code_missing"]
    # La instantánea del trabajo queda accesible en el meta de la página.
    assert pagina.meta["job"]["attributes"]["status"] == "preview_ready"


def test_previsualizar_filtra_por_bucket(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-preview")

    client.beneficiaries.import_preview(IMPORT_ID, buckets=["valid", "correctable"])

    ruta = server.requests[0].path
    assert "buckets=valid" in ruta
    assert "buckets=correctable" in ruta


def test_corregir_una_fila_de_la_vista_previa(client: Veriko, server: RecordingServer) -> None:
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


def test_corregir_una_fila_sin_campos_no_llega_a_la_api(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.beneficiaries.import_edit_row(IMPORT_ID, "102")

    assert raised.value.code == "no_valid_fields"
    assert server.requests == []


def test_quitar_una_fila_de_la_vista_previa(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("no-content")

    client.beneficiaries.import_remove_row(IMPORT_ID, "102")

    assert server.requests[0].method == "DELETE"
    assert server.requests[0].path == "/v1/beneficiaries/imports/" + IMPORT_ID + "/rows/102"


def test_confirmar_la_importacion(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-committed")

    trabajo = client.beneficiaries.import_commit(IMPORT_ID)

    assert server.requests[0].method == "POST"
    assert server.requests[0].path == "/v1/beneficiaries/imports/" + IMPORT_ID + "/commit"
    assert trabajo.status == "committing"


def test_esperar_a_que_la_importacion_quede_lista(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("beneficiaries-import-parsing")
    server.enqueue_recording("beneficiaries-import-status")
    esperas: list[float] = []

    trabajo = client.beneficiaries.import_wait(IMPORT_ID, poll_interval=2.0, sleep=esperas.append)

    assert trabajo.status == "preview_ready"
    assert esperas == [2.0]
    # El endpoint de estado no expone ETag: no se manda If-None-Match.
    assert server.requests[0].header("if-none-match") is None
    assert server.requests[1].header("if-none-match") is None
