"""La familia `client.validations`: lo que estrena la versión 0.2.0."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from conftest import RecordingServer
from veriko import ConfigurationError, InvalidRequestError, RetryPolicy, Veriko

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
    assert pagina[0].has_cep is True
    assert pagina[1].has_cep is False
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

    assert stats["total"] == 128
    assert stats["valid"] == 97
    assert "from=2025-03-01" in server.requests[0].path


# ── Reintentos ──────────────────────────────────────────────────────────────


def test_listar_los_intentos_de_reintento(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("retry-attempts")

    intentos = client.validations.retry_attempts(VALIDATION_ID)

    assert [i.attempt for i in intentos] == [1, 2]
    assert intentos[1].status == "valid"


def test_cambiar_la_politica_de_reintentos(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("retry-policy-updated")

    validation = client.validations.set_retry_policy(
        VALIDATION_ID,
        RetryPolicy(max_retries=3, interval_seconds=600, outcomes=["not_found"]),
    )

    assert server.requests[0].method == "PUT"
    assert server.requests[0].json() == {
        "enabled": True,
        "max_retries": 3,
        "interval_seconds": 600,
        "outcomes": ["not_found"],
    }
    assert validation.retry_state is not None
    assert validation.retry_state.terminal_state == "pending"


def test_cancelar_los_reintentos_pendientes(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("retries-cancelled")

    validation = client.validations.cancel_retries(VALIDATION_ID)

    assert validation.retry_state is not None
    assert validation.retry_state.terminal_state == "cancelled"
    assert validation.retry_state.cancelled_at == "2025-03-15T15:00:00Z"


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

    assert acuse["attributes"]["queued"] is True
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
