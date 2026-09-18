"""Cada estado HTTP tiene su excepción, y el `code` es lo que se lee."""

from __future__ import annotations

import pytest

from conftest import RecordingServer
from veriko import (
    AuthenticationError,
    ConflictError,
    InvalidRequestError,
    RateLimitError,
    Veriko,
)


def _validate(client: Veriko) -> None:
    client.validate_transfer(fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234")


def test_422_expone_todas_las_entradas_no_solo_la_primera(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-422")

    with pytest.raises(InvalidRequestError) as raised:
        _validate(client)

    error = raised.value
    assert error.status == 422
    assert error.code == "required"
    assert error.pointer == "/data/attributes/fecha"
    assert len(error.errors) == 2
    assert [entry["code"] for entry in error.errors] == ["required", "invalid_clabe_checksum"]
    assert error.request_id == "a1b2c3d4e5f6"


def test_401_es_error_de_autenticacion(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-401")

    with pytest.raises(AuthenticationError) as raised:
        _validate(client)

    assert raised.value.status == 401
    assert raised.value.code == "unauthorized"


def test_409_de_idempotencia_en_curso(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-409")

    with pytest.raises(ConflictError) as raised:
        _validate(client)

    assert raised.value.code == "idempotency_key_in_progress"


def test_429_agotado_expone_los_segundos_de_espera(client: Veriko, server: RecordingServer) -> None:
    # Tres respuestas 429: el cliente reintenta dos veces y agota los intentos.
    server.enqueue_recording("validate-429", times=3)

    with pytest.raises(RateLimitError) as raised:
        _validate(client)

    assert raised.value.status == 429
    assert raised.value.code == "rate_limit_exceeded"
    assert raised.value.retry_after == 7.0
    assert raised.value.headers["x-ratelimit-remaining"] == "0"


def test_el_mensaje_del_error_lleva_el_codigo_y_el_request_id(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-401")

    with pytest.raises(AuthenticationError) as raised:
        _validate(client)

    texto = str(raised.value)
    assert "HTTP 401" in texto
    assert "unauthorized" in texto
    assert "request_id=a8b9c0d1e2f3" in texto
