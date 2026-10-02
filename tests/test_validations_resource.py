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


# ── Validación por imagen ───────────────────────────────────────────────────


def test_validar_una_imagen_desde_bytes(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-ocr")
    imagen = b"\x89PNG\r\n\x1a\n imagen de prueba"

    validation = client.validations.validate_ocr(
        image=imagen, cuenta_beneficiaria="012180004412345678"
    )

    assert validation.validation_type == "ocr"
    assert validation.status == "valid"
    # La imagen viaja en base64: el SDK la codifica, quien llama no.
    cuerpo = server.requests[0].json()
    assert base64.b64decode(cuerpo["image"]) == imagen
    assert cuerpo["cuenta_beneficiaria"] == "012180004412345678"


def test_validar_una_imagen_desde_una_ruta(
    client: Veriko, server: RecordingServer, tmp_path: Path
) -> None:
    server.enqueue_recording("validate-ocr")
    archivo = tmp_path / "comprobante.png"
    archivo.write_bytes(b"contenido del comprobante")

    client.validations.validate_ocr(image=archivo)

    assert base64.b64decode(server.requests[0].json()["image"]) == b"contenido del comprobante"


def test_validar_una_imagen_por_url(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-ocr")

    client.validations.validate_ocr(image_url="https://ejemplo.mx/comprobante.png")

    cuerpo = server.requests[0].json()
    assert cuerpo == {"image_url": "https://ejemplo.mx/comprobante.png"}


def test_sin_imagen_ni_url_no_se_llama_a_la_api(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.validations.validate_ocr()

    assert raised.value.code == "image_required"
    assert server.requests == []


def test_una_ruta_que_no_existe_se_detiene_antes_de_la_api(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(ConfigurationError):
        client.validations.validate_ocr(image="no-existe-este-archivo.png")

    assert server.requests == []


# ── Modo asíncrono ──────────────────────────────────────────────────────────


def test_encolar_devuelve_el_acuse_sin_veredicto(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-queued")

    queued = client.validations.enqueue(
        fecha="2025-03-15",
        monto=15000.50,
        cuenta_beneficiaria="012180004412345678",
        clave_rastreo="MXBA20250315001234",
    )

    assert server.requests[0].path == "/v1/validate?async=1"
    assert queued.id == VALIDATION_ID
    assert queued.status == "queued"
    assert queued.etag == 'W/"0-queued"'
    assert queued.next_poll_after_seconds == 3


def test_encolar_una_imagen(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-queued")

    client.validations.enqueue_ocr(image=b"png")

    assert server.requests[0].path == "/v1/validate-ocr?async=1"


def test_sondear_hasta_el_veredicto(client: Veriko, server: RecordingServer) -> None:
    # Primero sigue en el estado no terminal, después resuelve.
    server.enqueue_recording("validation-retrying")
    server.enqueue_recording("validate-valid")
    esperas: list[float] = []

    validation = client.validations.wait_for(VALIDATION_ID, poll_interval=2.0, sleep=esperas.append)

    assert validation.status == "valid"
    assert esperas == [2.0]


def test_el_sondeo_manda_el_etag_de_la_respuesta_anterior(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-retrying")  # trae ETag
    server.enqueue_recording("validate-valid")

    client.validations.wait_for(VALIDATION_ID, poll_interval=0.0, sleep=lambda _: None)

    assert server.requests[0].header("if-none-match") is None
    assert server.requests[1].header("if-none-match") == 'W/"3-not_found"'


def test_el_sondeo_se_rinde_al_agotar_el_tiempo(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-retrying", times=4)

    with pytest.raises(TimeoutError):
        client.validations.wait_for(VALIDATION_ID, timeout=0.0, sleep=lambda _: None)


# ── Listar y recorrer ───────────────────────────────────────────────────────


def test_listar_devuelve_una_pagina_con_sus_contadores(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validations-page1")

    pagina = client.validations.list(per_page=2, status="valid")

    assert len(pagina) == 2
    assert pagina.page == 1
    assert pagina.total == 3
    assert pagina.total_pages == 2
    assert pagina.has_next is True
    assert [v.status for v in pagina] == ["valid", "not_found"]
    # Un listado no trae enlaces al comprobante: los campos son los del spec.
    assert pagina[0].tracking_key == "MXBA20250315001234"
    assert pagina[0].amount == 15000.5
    assert pagina[0].bank_name == "BBVA MEXICO"
    assert pagina[0].retry_state is not None
    assert pagina[0].retry_state.enabled is False
    assert pagina[1].beneficiary_label is None
    assert "per_page=2" in server.requests[0].path
    assert "status=valid" in server.requests[0].path


def test_recorrer_pide_la_pagina_siguiente_sola(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-page1")
    server.enqueue_recording("validations-page2")

    todas = list(client.validations.iter(per_page=2))

    assert len(todas) == 3
    assert len(server.requests) == 2
    assert "page=2" in server.requests[1].path


def test_recorrer_se_detiene_en_la_ultima_pagina(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-page2")  # ya es la última

    todas = list(client.validations.iter(page=2, per_page=2))

    assert len(todas) == 1
    assert len(server.requests) == 1


def test_el_filtro_de_fecha_viaja_como_from(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-page1")

    client.validations.list(from_="2025-03-01", to="2025-03-31")

    # `from` es palabra reservada en Python; el argumento es `from_`.
    assert "from=2025-03-01" in server.requests[0].path
    assert "to=2025-03-31" in server.requests[0].path


def test_estadisticas_de_la_cuenta(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-stats")

    stats = client.validations.stats(from_="2025-03-01")

    assert stats["total"] == 47
    assert stats["valid"] == 35
    assert stats["by_type"] == {"direct": 30, "ocr": 17}
    assert "from=2025-03-01" in server.requests[0].path


# ── Reintentos ──────────────────────────────────────────────────────────────


def test_listar_los_intentos_de_reintento(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("retry-attempts")

    intentos = client.validations.retry_attempts(VALIDATION_ID)

    assert [i.attempt_number for i in intentos] == [1, 2]
    assert intentos[1].new_status == "valid"
    assert intentos[1].dispatched_at == "2025-03-17T08:20:00Z"
    assert intentos[0].proxy_pool_member == "proxy-01"


def test_cambiar_la_politica_de_reintentos(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("retry-policy-updated")

    estado = client.validations.set_retry_policy(
        VALIDATION_ID,
        RetryPolicy(max_retries=3, interval_seconds=600, outcomes=["not_found"]),
    )

    assert server.requests[0].method == "PUT"
    # El spec envuelve la política en `retry_policy`.
    assert server.requests[0].json() == {
        "retry_policy": {
            "enabled": True,
            "max_retries": 3,
            "interval_seconds": 600,
            "outcomes": ["not_found"],
        }
    }
    # La respuesta es el estado del ciclo, no la validación completa.
    assert estado.terminal_state == "pending"
    assert estado.max_retries == 3
    assert estado.outcomes == ["not_found"]


def test_cancelar_los_reintentos_pendientes(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("retries-cancelled")

    estado = client.validations.cancel_retries(VALIDATION_ID)

    assert estado.terminal_state == "cancelled"
    assert estado.cancelled_at == "2025-03-15T15:00:00Z"
    assert estado.enabled is False


# ── Archivos ────────────────────────────────────────────────────────────────


def test_descargar_la_imagen_del_comprobante(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-image")

    imagen = client.validations.image(VALIDATION_ID)

    assert imagen.content.startswith(b"\x89PNG")
    assert imagen.content_type == "image/png"
    assert imagen.filename == "comprobante-" + VALIDATION_ID + ".png"
    assert len(imagen) == len(imagen.content)


def test_descargar_el_comprobante_en_pdf(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-image-pdf")

    comprobante = client.validations.image(VALIDATION_ID)

    assert comprobante.content.startswith(b"%PDF-")
    assert comprobante.content_type == "application/pdf"
    assert comprobante.filename == "comprobante-" + VALIDATION_ID + ".pdf"


def test_exportar_el_historial_en_csv(
    client: Veriko, server: RecordingServer, tmp_path: Path
) -> None:
    server.enqueue_recording("validations-export-csv")

    export = client.validations.export(format="csv", status="valid")

    assert "format=csv" in server.requests[0].path
    assert "status=valid" in server.requests[0].path
    assert export.filename == "validaciones-2025-03.csv"
    assert export.content.startswith(b"id,fecha,monto")
    destino = export.write_to(str(tmp_path / export.filename))
    assert Path(destino).read_bytes() == export.content


def test_un_formato_de_exportacion_fuera_de_la_lista(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(ConfigurationError):
        client.validations.export(format="pdf")

    assert server.requests == []


# ── Retirar y enviar ────────────────────────────────────────────────────────


def test_retirar_una_validacion_del_historial(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("no-content")

    client.validations.delete(VALIDATION_ID)

    assert server.requests[0].method == "DELETE"
    assert server.requests[0].path == "/v1/validations/" + VALIDATION_ID


def test_enviar_el_comprobante_a_telegram(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("telegram-accepted")

    acuse = client.validations.send_cep_to_telegram(VALIDATION_ID)

    assert acuse["queued"] is True
    assert server.requests[0].path.endswith("/cep/send-telegram")


def test_el_cuerpo_de_una_peticion_sin_filtros_no_lleva_nulos(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validations-page1")

    client.validations.list()

    # Sin filtros, la ruta va limpia: lo que no se pasa no viaja.
    assert server.requests[0].path == "/v1/validations"


def test_el_atajo_de_la_raiz_y_la_familia_hacen_lo_mismo(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid", times=2)

    desde_raiz = client.validate_transfer(
        fecha="2025-03-15",
        monto=15000.50,
        cuenta_beneficiaria="012180004412345678",
        clave_rastreo="MXBA20250315001234",
    )
    desde_familia = client.validations.validate(
        fecha="2025-03-15",
        monto=15000.50,
        cuenta_beneficiaria="012180004412345678",
        clave_rastreo="MXBA20250315001234",
    )

    assert desde_raiz.id == desde_familia.id
    assert json.loads(server.requests[0].body) == json.loads(server.requests[1].body)


# ── Lo que el spec pide ─────────────────────────────────────────────────────


def _consulta(server: RecordingServer, indice: int) -> dict[str, list[str]]:
    return parse_qs(urlsplit(server.requests[indice].path).query)


def test_los_filtros_booleanos_viajan_como_uno_y_cero(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validations-page1", times=2)

    client.validations.list(
        playground=True, with_deleted=True, status=["valid", "not_found"], batch_id=42
    )
    client.validations.list(playground=False, with_deleted=False)

    primera = _consulta(server, 0)
    segunda = _consulta(server, 1)
    assert primera["playground"] == ["1"]
    assert primera["with_deleted"] == ["1"]
    assert primera["status"] == ["valid,not_found"]
    assert primera["batch_id"] == ["42"]
    assert "True" not in server.requests[0].path
    # `playground` sólo admite `1`: `False` equivale a no filtrar.
    assert "playground" not in segunda
    assert segunda["with_deleted"] == ["0"]


def test_los_filtros_de_la_exportacion_y_las_estadisticas_usan_los_mismos_valores(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validations-export-csv")
    server.enqueue_recording("validations-stats")

    client.validations.export(with_deleted=True, from_="2025-01-01", limit=100)
    client.validations.stats(playground=True, with_deleted=False)

    exportacion = _consulta(server, 0)
    assert exportacion["with_deleted"] == ["1"]
    assert exportacion["from"] == ["2025-01-01"]
    assert exportacion["limit"] == ["100"]
    estadisticas = _consulta(server, 1)
    assert estadisticas["playground"] == ["1"]
    assert estadisticas["with_deleted"] == ["0"]


def test_una_lista_vacia_no_tiene_pagina_siguiente(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-empty")

    pagina = client.validations.list()

    assert len(pagina) == 0
    assert pagina.total == 0
    assert pagina.has_next is False


def test_get_deja_el_etag_en_la_validacion(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-retrying")

    validation = client.validations.get(VALIDATION_ID)

    assert validation.etag == 'W/"3-not_found"'
    assert server.requests[0].header("if-none-match") is None


def test_una_lectura_condicional_sin_cambios_se_lanza_como_304(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("not-modified")

    with pytest.raises(APIError) as raised:
        client.validations.get(VALIDATION_ID, if_none_match='W/"3-not_found"')

    assert raised.value.status == 304
    assert server.requests[0].header("if-none-match") == 'W/"3-not_found"'


def test_el_sondeo_atraviesa_un_304(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-queued-status")
    server.enqueue_recording("not-modified")
    server.enqueue_recording("validate-valid")

    validation = client.validations.wait_for(VALIDATION_ID, poll_interval=0.0, sleep=lambda _: None)

    assert validation.status == "valid"
    assert len(server.requests) == 3
    assert server.requests[1].header("if-none-match") == 'W/"0-queued"'
    assert server.requests[2].header("if-none-match") == 'W/"0-queued"'


def test_sondear_desde_la_cola_hasta_el_veredicto(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-queued")
    server.enqueue_recording("validation-queued-status")
    server.enqueue_recording("validate-valid")
    esperas: list[float] = []

    queued = client.validations.enqueue(
        fecha="2025-03-15",
        monto=15000.50,
        cuenta_beneficiaria="012180004412345678",
        clave_rastreo="MXBA20250315001234",
    )
    validation = client.validations.wait_for(queued.id, poll_interval=2.0, sleep=esperas.append)

    assert validation.status == "valid"
    assert esperas == [2.0]


def test_un_not_found_sin_reintentos_es_un_veredicto_firme(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-not-found")

    validation = client.validations.wait_for(VALIDATION_ID, sleep=lambda _: None)

    assert validation.status == "not_found"
    assert len(server.requests) == 1


def test_exportar_el_historial_en_xlsx(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validations-export-xlsx")

    export = client.validations.export(format="xlsx")

    assert "spreadsheetml.sheet" in (server.requests[0].header("accept") or "")
    assert export.filename == "veriko_validaciones_2025-03-15.xlsx"
    assert export.content.startswith(b"PK")


# ── Referencia propia, duplicado y conflicto de cuenta ──────────────────────


def test_validar_una_imagen_lleva_client_ref(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-ocr-conflict")

    validation = client.validations.validate_ocr(image=b"png", client_ref="orden-4812")

    assert server.requests[0].json()["client_ref"] == "orden-4812"
    assert validation.client_ref == "orden-4812"


def test_encolar_lleva_client_ref_en_los_dos_caminos(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-queued", times=2)

    client.validations.enqueue(
        fecha="2025-03-15",
        monto=15000.50,
        cuenta_beneficiaria="012180004412345678",
        clave_rastreo="MXBA20250315001234",
        client_ref="orden-4812",
    )
    client.validations.enqueue_ocr(image=b"png", client_ref="orden-4812")

    assert server.requests[0].path == "/v1/validate?async=1"
    assert server.requests[0].json()["client_ref"] == "orden-4812"
    assert server.requests[1].path == "/v1/validate-ocr?async=1"
    assert server.requests[1].json()["client_ref"] == "orden-4812"


def test_la_validacion_expone_el_duplicado_y_el_conflicto_de_cuenta(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-ocr-conflict")

    validation = client.validations.validate_ocr(image=b"png", cuenta_beneficiaria="5678")

    assert validation.duplicate_of == DuplicateOf(
        id="3fa85f64-5717-4562-b3fc-2c963f66afa6", created_at="2025-04-09T09:15:00Z"
    )
    assert validation.account_conflict == AccountConflict(sent_last4="5678", read_last4="9012")
    # El conflicto no cambia el veredicto.
    assert validation.status == "not_found"


def test_client_ref_filtra_el_listado_la_exportacion_y_las_estadisticas(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validations-page1")
    server.enqueue_recording("validations-export-csv")
    server.enqueue_recording("validations-stats")

    client.validations.list(client_ref="orden 4812")
    client.validations.export(client_ref="orden 4812")
    client.validations.stats(client_ref="orden 4812")

    for indice in range(3):
        assert _consulta(server, indice)["client_ref"] == ["orden 4812"]


def test_un_listado_trae_el_client_ref_de_cada_validacion() -> None:
    resumen = ValidationSummary.from_item(
        {"id": VALIDATION_ID, "attributes": {"status": "valid", "client_ref": "orden-4812"}}
    )

    assert resumen.client_ref == "orden-4812"
    assert ValidationSummary.from_item({"id": VALIDATION_ID, "attributes": {}}).client_ref is None


# ── Varias cuentas candidatas ───────────────────────────────────────────────


def test_encolar_con_candidatas_las_lleva_en_los_dos_caminos(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-queued", times=2)
    candidatas = ["012180004412345678", "002010077777777771"]

    client.validations.enqueue(
        fecha="2025-03-15",
        monto=15000.50,
        clave_rastreo="MXBA20250315001234",
        cuentas_candidatas=candidatas,
    )
    client.validations.enqueue_ocr(image=b"png", cuentas_candidatas=candidatas)

    assert server.requests[0].path == "/v1/validate?async=1"
    assert server.requests[0].json()["cuentas_candidatas"] == candidatas
    assert "cuenta_beneficiaria" not in server.requests[0].json()
    assert server.requests[1].path == "/v1/validate-ocr?async=1"
    assert server.requests[1].json()["cuentas_candidatas"] == candidatas


def test_validar_una_imagen_con_candidatas_no_envia_la_cuenta(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-ocr")

    client.validations.validate_ocr(
        image_url="https://ejemplo.mx/comprobante.png",
        cuentas_candidatas=("012180004412345678", "002010077777777771"),
    )

    assert server.requests[0].json() == {
        "image_url": "https://ejemplo.mx/comprobante.png",
        "cuentas_candidatas": ["012180004412345678", "002010077777777771"],
    }


# ── Conservar el comprobante o no ───────────────────────────────────────────


def test_retain_image_false_viaja_y_la_validacion_dice_que_no_conserva_el_archivo(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-ocr-not-retained")

    validation = client.validations.validate_ocr(image=b"png", retain_image=False)

    assert server.requests[0].json()["retain_image"] is False
    assert validation.image_retained is False
    assert "image_path" not in validation.attributes


def test_sin_retain_image_no_viaja_y_la_validacion_no_informa_nada(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-ocr")

    validation = client.validations.validate_ocr(image=b"png")

    assert "retain_image" not in server.requests[0].json()
    assert validation.image_retained is None
    assert validation.purged_at is None
    assert validation.is_purged is False


def test_retain_image_true_tambien_viaja_en_el_camino_asincrono(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-queued")

    client.validations.enqueue_ocr(image=b"png", retain_image=True)

    assert server.requests[0].json()["retain_image"] is True


# ── Revisar el estado de pago ───────────────────────────────────────────────


def test_revisar_una_validacion_que_pasa_a_returned(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-recheck-returned")

    resultado = client.validations.recheck(VALIDATION_ID)

    assert isinstance(resultado, RecheckResult)
    assert server.requests[0].method == "POST"
    assert server.requests[0].path == "/v1/validations/" + VALIDATION_ID + "/recheck"
    assert server.requests[0].body == b""
    assert resultado.changed is True
    assert resultado.previous_status == "valid"
    assert resultado.checked_at == "2026-10-02T09:15:44Z"
    assert resultado.validation.id == VALIDATION_ID
    assert resultado.validation.status == "returned"
    assert resultado.validation.is_terminal is True
    assert resultado.validation.has_cep is True


def test_revisar_una_validacion_que_sigue_valid(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-recheck-unchanged")

    resultado = client.validations.recheck(VALIDATION_ID)

    assert resultado.changed is False
    assert resultado.previous_status == "valid"
    assert resultado.validation.status == "valid"


def test_la_revision_deja_el_estado_del_pago_en_la_validacion(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-recheck-returned")
    server.enqueue_recording("validate-valid")

    devuelta = client.validations.recheck(VALIDATION_ID).validation
    sin_revisar = client.get_validation(VALIDATION_ID)

    assert devuelta.payment_status is not None
    assert devuelta.payment_status.code == "devuelto"
    assert devuelta.payment_status.label == "Devuelto"
    assert devuelta.payment_status.reversed is True
    assert devuelta.payment_status.settled is False
    assert devuelta.payment_status.checked_at == "2026-10-02T09:15:44Z"
    assert sin_revisar.payment_status is None


def test_una_revision_antes_de_tiempo_no_se_reintenta_sola(
    server: RecordingServer, sleeps: list[float]
) -> None:
    server.enqueue_recording("validate-429")
    client = make_client(server, sleeps)

    with pytest.raises(RateLimitError) as raised:
        client.validations.recheck(VALIDATION_ID)

    assert raised.value.retry_after == 7
    assert len(server.requests) == 1
    assert sleeps == []


def test_una_revision_con_banxico_caido_no_se_reintenta_sola(
    server: RecordingServer, sleeps: list[float]
) -> None:
    server.enqueue_recording("validate-503")
    client = make_client(server, sleeps)

    with pytest.raises(ServerError) as raised:
        client.validations.recheck(VALIDATION_ID)

    assert raised.value.status == 503
    assert len(server.requests) == 1


def test_una_validacion_purgada_responde_410(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-purged-410")

    with pytest.raises(APIError) as raised:
        client.validations.recheck(VALIDATION_ID)

    assert raised.value.status == 410
    assert raised.value.code == "validation_purged"


# ── Borrado definitivo ──────────────────────────────────────────────────────


def test_preparar_el_borrado_devuelve_el_token_y_lo_que_se_borraria(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-purge-prepare")

    preparacion = client.validations.prepare_purge(VALIDATION_ID)

    assert isinstance(preparacion, PurgePreparation)
    assert server.requests[0].method == "POST"
    assert server.requests[0].path == "/v1/validations/" + VALIDATION_ID + "/purge/prepare"
    assert preparacion.id == VALIDATION_ID
    assert preparacion.confirmation_token == "eyJhZG1pbl9pZCI6Ii4uLiJ9.q1w2e3r4t5y6u7i8o9p0"
    assert preparacion.expires_in == 120
    assert preparacion.irreversible is True
    assert preparacion.will_delete["image"] is True
    assert preparacion.will_delete["stored_data"][0] == "request_data"
    assert "purged_at" in preparacion.will_keep
    assert preparacion.cancels_pending_retries is False
    assert preparacion.refunds_quota is False
    assert preparacion.same_image_validation_ids == []


def test_ejecutar_el_borrado_envia_el_token_y_devuelve_lo_borrado(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-purge-executed")

    resultado = client.validations.execute_purge(
        VALIDATION_ID, confirmation_token="eyJhZG1pbl9pZCI6Ii4uLiJ9.q1w2e3r4t5y6u7i8o9p0"
    )

    assert isinstance(resultado, PurgeResult)
    assert server.requests[0].method == "POST"
    assert server.requests[0].path == "/v1/validations/" + VALIDATION_ID + "/purge/execute"
    assert server.requests[0].json() == {
        "confirmation_token": "eyJhZG1pbl9pZCI6Ii4uLiJ9.q1w2e3r4t5y6u7i8o9p0"
    }
    assert resultado.id == VALIDATION_ID
    assert resultado.purged_at == "2026-10-02T09:30:00Z"
    assert resultado.file_removal == "complete"
    assert resultado.deleted["image"] is True
    assert resultado.deleted["webhook_deliveries"] == 1


def test_ejecutar_el_borrado_sin_token_no_llama_a_la_api(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.validations.execute_purge(VALIDATION_ID, confirmation_token="")

    assert raised.value.code == "confirmation_token_missing"
    assert server.requests == []


def test_ejecutar_el_borrado_no_se_reintenta_solo(
    server: RecordingServer, sleeps: list[float]
) -> None:
    server.enqueue_recording("validate-503")
    server.enqueue_recording("validation-purge-executed")
    client = make_client(server, sleeps)

    with pytest.raises(ServerError):
        client.validations.execute_purge(VALIDATION_ID, confirmation_token="token")

    assert len(server.requests) == 1
    assert sleeps == []


def test_una_validacion_purgada_queda_como_una_lapida(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validation-purged-tombstone")

    validation = client.get_validation(VALIDATION_ID)

    assert validation.is_purged is True
    assert validation.purged_at == "2026-10-02T09:30:00Z"
    assert validation.status == "valid"
    assert validation.attributes["normalized_data"] == {"monto": 15000.5}
    assert validation.request_data == {}
    assert validation.has_cep is False
