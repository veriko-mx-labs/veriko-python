"""Validar una transferencia: lo que se envía y lo que se lee de vuelta."""

from __future__ import annotations

import json

import pytest

from conftest import RecordingServer, make_client
from veriko import InvalidRequestError, RetryPolicy, Veriko


def test_veredicto_valid_trae_comprobante(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-valid")

    validation = client.validate_transfer(
        fecha="2025-03-15",
        monto=15000.50,
        clave_rastreo="MXBA20250315001234",
        referencia_numerica="1234567",
        emisor="BANCO NACIONAL DE MEXICO",
        receptor="BBVA MEXICO",
        cuenta_beneficiaria="012180004412345678",
    )

    assert validation.status == "valid"
    assert validation.banxico_status == "valid"
    assert validation.id == "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    assert validation.has_cep is True
    assert validation.is_terminal is True
    assert validation.processing_time_ms == 1320
    assert validation.request_data["clave_rastreo"] == "MXBA20250315001234"


def test_veredicto_not_found_no_trae_comprobante(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-not-found")

    validation = client.validate_transfer(
        fecha="2025-03-16", monto=15000.50, clave_rastreo="MXBA20250316000001"
    )

    assert validation.status == "not_found"
    assert validation.has_cep is False
    # Un `not_found` es terminal para esta llamada, pero no dice que la
    # transferencia no exista: el CEP puede tardar en publicarse.
    assert validation.is_terminal is True


def test_veredicto_returned_sigue_entregando_el_comprobante(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-returned")

    validation = client.validate_transfer(
        fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234"
    )

    assert validation.status == "returned"
    assert validation.has_cep is True


def test_la_peticion_lleva_los_campos_en_el_cuerpo(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        fecha="2025-03-15",
        monto=15000.50,
        clave_rastreo="MXBA20250315001234",
        cuenta_beneficiaria="012180004412345678",
        emisor="BANCO NACIONAL DE MEXICO",
    )

    sent = server.requests[0]
    assert sent.method == "POST"
    assert sent.path == "/v1/validate"
    assert sent.header("authorization") == "Bearer veriko_prueba"
    body = sent.json()
    assert body == {
        "fecha": "2025-03-15",
        "monto": 15000.50,
        "clave_rastreo": "MXBA20250315001234",
        "cuenta_beneficiaria": "012180004412345678",
        "emisor": "BANCO NACIONAL DE MEXICO",
    }
    # Los campos que no se pasan no viajan como `null`: no se envían.
    assert "referencia_numerica" not in body
    assert "retry_policy" not in body


def test_la_politica_de_reintentos_viaja_en_el_cuerpo(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-not-found")

    client.validate_transfer(
        fecha="2025-03-16",
        monto=15000.50,
        clave_rastreo="MXBA20250316000001",
        retry_policy=RetryPolicy(
            max_retries=3, interval_seconds=600, outcomes=["not_found", "cep_unavailable"]
        ),
    )

    assert server.requests[0].json()["retry_policy"] == {
        "enabled": True,
        "max_retries": 3,
        "interval_seconds": 600,
        "outcomes": ["not_found", "cep_unavailable"],
    }


def test_sin_identificador_de_transferencia_no_se_llama_a_la_api(
    server: RecordingServer, sleeps: list[float]
) -> None:
    client = make_client(server, sleeps)

    with pytest.raises(InvalidRequestError) as raised:
        client.validate_transfer(fecha="2025-03-15", monto=100.0)

    assert raised.value.code == "clave_or_ref_required"
    assert server.requests == []


def test_el_cuerpo_viaja_en_utf8_sin_escapes(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        fecha="2025-03-15",
        monto=15000.50,
        clave_rastreo="MXBA20250315001234",
        emisor="BANCO DEL BAJÍO",
    )

    raw = server.requests[0].body.decode("utf-8")
    assert "BANCO DEL BAJÍO" in raw
    assert json.loads(raw)["emisor"] == "BANCO DEL BAJÍO"


def test_leer_una_validacion_por_su_id(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validation-retrying")

    validation = client.get_validation("f47ac10b-58cc-4372-a567-0e02b2c3d479")

    assert server.requests[0].path == "/v1/validations/f47ac10b-58cc-4372-a567-0e02b2c3d479"
    assert validation.retry_state is not None
    assert validation.retry_state.enabled is True
    assert validation.retry_state.attempts_completed == 2
    assert validation.retry_state.next_attempt_at == "2025-03-15T14:42:11Z"
