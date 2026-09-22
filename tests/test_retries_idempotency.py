"""Reintentos e idempotencia: qué se repite, cuánto se espera y con qué clave."""

from __future__ import annotations

import pytest

from conftest import RecordingServer, make_client
from veriko import InvalidRequestError, RateLimitError, Veriko
from veriko._http import parse_retry_after


def _validate(client: Veriko) -> None:
    client.validate_transfer(fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234")


# ── Qué se reintenta ────────────────────────────────────────────────────────


def test_un_503_se_reintenta_y_la_segunda_respuesta_gana(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-503")
    server.enqueue_recording("validate-valid")

    validation = client.validate_transfer(
        fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234"
    )

    assert validation.status == "valid"
    assert len(server.requests) == 2


def test_un_429_se_reintenta_esperando_lo_que_dice_retry_after(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps)
    server.enqueue_recording("validate-429")
    server.enqueue_recording("validate-valid")

    validation = client.validate_transfer(
        fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234"
    )

    assert validation.status == "valid"
    # `Retry-After: 7` gana sobre el backoff propio del cliente.
    assert sleeps == [7.0]


def test_un_422_no_se_reintenta(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-422")

    with pytest.raises(InvalidRequestError):
        _validate(client)

    # Una sola petición: la de un 4xx hay que corregirla, no repetirla.
    assert len(server.requests) == 1


def test_sin_reintentos_el_primer_429_se_lanza(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps, max_retries=0)
    server.enqueue_recording("validate-429")

    with pytest.raises(RateLimitError):
        _validate(client)

    assert len(server.requests) == 1
    assert sleeps == []


def test_el_backoff_propio_se_usa_cuando_no_hay_retry_after(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps)
    server.enqueue_recording("validate-503", times=2)
    server.enqueue_recording("validate-valid")

    _validate(client)

    assert len(sleeps) == 2
    assert all(espera > 0 for espera in sleeps)
    # Exponencial: la segunda espera es mayor que la primera aun con fluctuación.
    assert sleeps[1] > sleeps[0] / 2


# ── Idempotencia ────────────────────────────────────────────────────────────


def test_la_clave_de_idempotencia_del_integrador_viaja_tal_cual(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        fecha="2025-03-15",
        monto=15000.50,
        clave_rastreo="MXBA20250315001234",
        idempotency_key="pedido-4f3a2b1c",
    )

    assert server.requests[0].header("idempotency-key") == "pedido-4f3a2b1c"


def test_sin_clave_el_sdk_pone_una_y_la_repite_en_los_reintentos(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps)
    server.enqueue_recording("validate-503")
    server.enqueue_recording("validate-valid")

    client.validate_transfer(fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234")

    claves = [request.header("idempotency-key") for request in server.requests]
    assert len(claves) == 2
    assert claves[0] is not None
    # La misma clave en el reintento: es lo que evita la validación duplicada.
    assert claves[0] == claves[1]
    assert claves[0].startswith("veriko-python-")


def test_dos_llamadas_distintas_no_comparten_clave(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-valid", times=2)

    _validate(client)
    _validate(client)

    primera = server.requests[0].header("idempotency-key")
    segunda = server.requests[1].header("idempotency-key")
    assert primera != segunda


def test_la_clave_generada_cumple_el_patron_de_la_api(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid")

    _validate(client)

    clave = server.requests[0].header("idempotency-key")
    assert clave is not None
    assert 1 <= len(clave) <= 255
    assert all(caracter.isalnum() or caracter in "-_" for caracter in clave)


# ── Lectura de `Retry-After` ────────────────────────────────────────────────


def test_retry_after_en_segundos() -> None:
    assert parse_retry_after("12") == 12.0


def test_retry_after_como_fecha_http() -> None:
    espera = parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT")
    assert espera is not None
    assert espera > 0


def test_retry_after_ausente_o_ilegible() -> None:
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("pronto") is None
