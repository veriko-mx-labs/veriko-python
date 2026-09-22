"""Verificación de la firma de un webhook.

Los cuerpos son los de `tests/recordings/webhook-*.json`, servidos aquí como
bytes crudos: es lo que firma la API y lo que hay que verificar sin reserializar.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from conftest import load_recording
from veriko import (
    SIGNATURE_HEADER,
    SignatureVerificationError,
    compute_signature,
    parse_webhook,
    signature_from_headers,
    verify_webhook,
)

# 64 hex, la forma real del secreto según el contrato. La firma se calcula con él,
# así que el valor concreto da igual.
SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def cuerpo(name: str) -> bytes:
    """El cuerpo de la entrega, en los bytes exactos que viajarían."""
    return json.dumps(load_recording(name)["json"], ensure_ascii=False).encode("utf-8")


def firmar(payload: bytes, secret: str = SECRET) -> str:
    firma = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return "sha256=" + firma


def test_una_firma_correcta_se_acepta() -> None:
    payload = cuerpo("webhook-validation-completed")

    assert verify_webhook(payload, firmar(payload), SECRET) is True


def test_la_firma_se_acepta_con_y_sin_el_prefijo_del_algoritmo() -> None:
    payload = cuerpo("webhook-validation-completed")
    con_prefijo = firmar(payload)
    sin_prefijo = con_prefijo[len("sha256=") :]

    assert verify_webhook(payload, con_prefijo, SECRET) is True
    assert verify_webhook(payload, sin_prefijo, SECRET) is True
    assert verify_webhook(payload, sin_prefijo.upper(), SECRET) is True


def test_un_cuerpo_alterado_invalida_la_firma() -> None:
    payload = cuerpo("webhook-validation-completed")
    firma = firmar(payload)
    alterado = payload.replace(b'"valid"', b'"not_found"')

    assert alterado != payload
    assert verify_webhook(alterado, firma, SECRET) is False


def test_reserializar_el_json_rompe_la_firma() -> None:
    # El error más común al integrar: leer el cuerpo a un diccionario y volverlo
    # a escribir. Los bytes cambian, aunque el documento sea el mismo.
    payload = cuerpo("webhook-validation-completed")
    firma = firmar(payload)
    reserializado = json.dumps(json.loads(payload), indent=2).encode("utf-8")

    assert verify_webhook(reserializado, firma, SECRET) is False


def test_otro_secreto_invalida_la_firma() -> None:
    payload = cuerpo("webhook-validation-completed")

    assert verify_webhook(payload, firmar(payload), "otro_secreto") is False


def test_sin_firma_o_sin_secreto_no_se_acepta() -> None:
    payload = cuerpo("webhook-validation-completed")

    assert verify_webhook(payload, None, SECRET) is False
    assert verify_webhook(payload, "", SECRET) is False
    assert verify_webhook(payload, firmar(payload), "") is False


def test_el_cuerpo_admite_texto_ademas_de_bytes() -> None:
    payload = cuerpo("webhook-validation-completed")

    assert verify_webhook(payload.decode("utf-8"), firmar(payload), SECRET) is True


def test_compute_signature_devuelve_el_hex_sin_prefijo() -> None:
    payload = b'{"event":"test"}'

    firma = compute_signature(payload, SECRET)

    assert len(firma) == 64
    assert firma == hmac.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()


def test_la_cabecera_se_encuentra_sin_importar_como_la_escriba_el_framework() -> None:
    assert signature_from_headers({SIGNATURE_HEADER: "sha256=abc"}) == "sha256=abc"
    assert signature_from_headers({"x-webhook-signature": "sha256=abc"}) == "sha256=abc"
    assert signature_from_headers({"HTTP_X_WEBHOOK_SIGNATURE": "sha256=abc"}) == "sha256=abc"
    assert signature_from_headers({"x-otra-cosa": "sha256=abc"}) is None


def test_parse_webhook_devuelve_el_evento_interpretado() -> None:
    payload = cuerpo("webhook-validation-completed")

    evento = parse_webhook(payload, firmar(payload), SECRET)

    assert evento.event == "validation.completed"
    assert evento.timestamp == "2025-03-15T14:22:11Z"
    assert evento.validation is not None
    assert evento.validation.status == "valid"
    assert evento.validation.has_cep is True
    assert evento.validation.id == "f47ac10b-58cc-4372-a567-0e02b2c3d479"


def test_parse_webhook_lee_el_ciclo_de_reintentos() -> None:
    payload = cuerpo("webhook-retry-resolved")

    evento = parse_webhook(payload, firmar(payload), SECRET)

    assert evento.event == "validation.retry.resolved"
    assert evento.validation is not None
    assert evento.validation.retry_state is not None
    assert evento.validation.retry_state.attempts_completed == 3
    assert evento.validation.retry_state.terminal_state == "resolved"


def test_parse_webhook_no_interpreta_un_cuerpo_con_firma_invalida() -> None:
    payload = cuerpo("webhook-validation-completed")

    with pytest.raises(SignatureVerificationError):
        parse_webhook(payload, "sha256=0000", SECRET)
