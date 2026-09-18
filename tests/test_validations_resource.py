"""La familia `client.validations`: lo que estrena la versión 0.2.0."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import RecordingServer
from veriko import (
    APIError,
    ConfigurationError,
    InvalidRequestError,
    RetryPolicy,
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
        fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234"
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
        fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234"
    )
    desde_familia = client.validations.validate(
        fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234"
    )

    assert desde_raiz.id == desde_familia.id
    assert json.loads(server.requests[0].body) == json.loads(server.requests[1].body)


# ── Lo que el spec pide y la versión 0.3.0 hacía distinto ───────────────────


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
        fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234"
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
