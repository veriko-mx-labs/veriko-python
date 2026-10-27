"""Verificación de la firma con marca de tiempo de un webhook.

La cabecera es `X-Webhook-Signature-Timestamped: t=<segundos>,v1=<hex>`, y `v1` es
el HMAC-SHA256 de `<t>.<cuerpo>` con el secreto del endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from conftest import load_recording
from veriko import (
    DEFAULT_TOLERANCE_SECONDS,
    TIMESTAMPED_SIGNATURE_HEADER,
    parse_webhook,
    timestamped_signature_from_headers,
    verify_webhook,
    verify_webhook_timestamped,
)

SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
AHORA = 1_700_000_000


def cuerpo(name: str) -> bytes:
    return json.dumps(load_recording(name)["json"], ensure_ascii=False).encode("utf-8")


def v1(payload: bytes, t: int, secret: str = SECRET) -> str:
    firmado = str(t).encode("ascii") + b"." + payload
    return hmac.new(secret.encode("utf-8"), firmado, hashlib.sha256).hexdigest()


def cabecera(payload: bytes, t: int = AHORA, secret: str = SECRET) -> str:
    return f"t={t},v1={v1(payload, t, secret)}"


PAYLOAD = cuerpo("webhook-validation-completed")


def test_el_vector_fijo_de_la_firma() -> None:
    # El HMAC-SHA256 de `1700000000.{"event":"test"}` con el secreto de las pruebas.
    header = "t=1700000000,v1=9b5ddb2ae0c5514e340f612c3424deb50a1e17b12767ae8550d6388096c92e10"

    assert verify_webhook_timestamped(b'{"event":"test"}', header, SECRET, now=AHORA) is True


def test_una_firma_correcta_dentro_de_la_ventana_se_acepta() -> None:
    assert verify_webhook_timestamped(PAYLOAD, cabecera(PAYLOAD), SECRET, now=AHORA) is True


def test_el_cuerpo_admite_texto_ademas_de_bytes() -> None:
    texto = PAYLOAD.decode("utf-8")

    assert verify_webhook_timestamped(texto, cabecera(PAYLOAD), SECRET, now=AHORA) is True


def test_la_ventana_se_mide_hacia_el_pasado_y_hacia_el_futuro() -> None:
    header = cabecera(PAYLOAD)

    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=AHORA + 299) is True
    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=AHORA - 299) is True


def test_el_borde_de_la_ventana_se_acepta_y_un_segundo_mas_se_rechaza() -> None:
    header = cabecera(PAYLOAD)

    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=AHORA + 300) is True
    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=AHORA + 301) is False
    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=AHORA - 301) is False


def test_una_entrega_vieja_que_se_repite_se_rechaza() -> None:
    header = cabecera(PAYLOAD)

    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=AHORA + 3600) is False


def test_la_ventana_se_ajusta_con_tolerance() -> None:
    header = cabecera(PAYLOAD)

    assert (
        verify_webhook_timestamped(PAYLOAD, header, SECRET, tolerance=60, now=AHORA + 61) is False
    )
    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, tolerance=60, now=AHORA + 60) is True
    assert (
        verify_webhook_timestamped(PAYLOAD, header, SECRET, tolerance=3600, now=AHORA + 3600)
        is True
    )


def test_la_ventana_por_omision_son_cinco_minutos() -> None:
    assert DEFAULT_TOLERANCE_SECONDS == 300


def test_sin_now_usa_el_reloj_del_sistema() -> None:
    ahora = int(time.time())

    assert verify_webhook_timestamped(PAYLOAD, cabecera(PAYLOAD, ahora), SECRET) is True
    assert verify_webhook_timestamped(PAYLOAD, cabecera(PAYLOAD, ahora - 3600), SECRET) is False


def test_una_hora_actual_o_una_ventana_invalidas_no_aceptan_nada() -> None:
    header = cabecera(PAYLOAD)

    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=float("nan")) is False
    assert (
        verify_webhook_timestamped(PAYLOAD, header, SECRET, tolerance=float("nan"), now=AHORA)
        is False
    )


def test_un_cuerpo_alterado_invalida_la_firma() -> None:
    header = cabecera(PAYLOAD)
    alterado = PAYLOAD.replace(b'"valid"', b'"not_found"')

    assert alterado != PAYLOAD
    assert verify_webhook_timestamped(alterado, header, SECRET, now=AHORA) is False


def test_reserializar_el_json_rompe_la_firma() -> None:
    reserializado = json.dumps(json.loads(PAYLOAD), indent=2).encode("utf-8")

    assert verify_webhook_timestamped(reserializado, cabecera(PAYLOAD), SECRET, now=AHORA) is False


def test_otro_secreto_invalida_la_firma() -> None:
    header = cabecera(PAYLOAD)

    assert verify_webhook_timestamped(PAYLOAD, header, "otro_secreto", now=AHORA) is False


def test_cambiar_t_sin_volver_a_firmar_invalida_la_firma() -> None:
    # Quien repite una entrega vieja no puede ponerle una `t` nueva: `t` está firmada.
    firma = v1(PAYLOAD, AHORA)
    renovada = f"t={AHORA + 3000},v1={firma}"

    assert verify_webhook_timestamped(PAYLOAD, renovada, SECRET, now=AHORA + 3000) is False


def test_la_firma_del_cuerpo_solo_no_sirve_como_firma_con_marca_de_tiempo() -> None:
    sola = hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha256).hexdigest()

    assert verify_webhook_timestamped(PAYLOAD, f"t={AHORA},v1={sola}", SECRET, now=AHORA) is False


def test_con_varias_v1_basta_con_que_una_cuadre() -> None:
    buena = v1(PAYLOAD, AHORA)
    mala = "0" * 64

    assert verify_webhook_timestamped(PAYLOAD, f"t={AHORA},v1={mala},v1={buena}", SECRET, now=AHORA)
    assert verify_webhook_timestamped(PAYLOAD, f"t={AHORA},v1={buena},v1={mala}", SECRET, now=AHORA)
    assert not verify_webhook_timestamped(
        PAYLOAD, f"t={AHORA},v1={mala},v1={mala[::-1]}", SECRET, now=AHORA
    )


def test_ignora_las_claves_que_no_conoce_y_los_espacios() -> None:
    firma = v1(PAYLOAD, AHORA)

    assert verify_webhook_timestamped(
        PAYLOAD, f" v0=abc , t={AHORA} , v1={firma} , v2=otra", SECRET, now=AHORA
    )
    assert verify_webhook_timestamped(PAYLOAD, f"v1={firma},t={AHORA}", SECRET, now=AHORA)


def test_la_firma_en_mayusculas_se_acepta() -> None:
    header = f"t={AHORA},v1={v1(PAYLOAD, AHORA).upper()}"

    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=AHORA) is True


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "   ",
        "basura",
        "t=,v1=",
        f"t={AHORA}",
        "v1=" + "a" * 64,
        f"t=ayer,v1={'a' * 64}",
        f"t=-{AHORA},v1={'a' * 64}",
        f"t={AHORA}.5,v1={'a' * 64}",
        f"t=1e9,v1={'a' * 64}",
        f"t=\u0661\u0667\u0660\u0660\u0660\u0660\u0660\u0660\u0660\u0660,v1={'a' * 64}",
        f"t={'9' * 40},v1={'a' * 64}",
        f"t={AHORA},t={AHORA},v1={'a' * 64}",
        f"t={AHORA},v1=",
        f"t={AHORA},v1=áéíóú",
        "sha256=" + "a" * 64,
    ],
    ids=lambda valor: repr(valor)[:40],
)
def test_una_cabecera_mal_formada_se_rechaza_sin_lanzar(header: str | None) -> None:
    assert verify_webhook_timestamped(PAYLOAD, header, SECRET, now=AHORA) is False


def test_sin_secreto_no_se_acepta() -> None:
    assert verify_webhook_timestamped(PAYLOAD, cabecera(PAYLOAD, secret=""), "", now=AHORA) is False


def test_las_dos_firmas_conviven_y_la_de_siempre_no_cambia() -> None:
    antigua = "sha256=" + hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha256).hexdigest()

    assert verify_webhook(PAYLOAD, antigua, SECRET) is True
    assert verify_webhook_timestamped(PAYLOAD, cabecera(PAYLOAD), SECRET, now=AHORA) is True
    # La firma de siempre no cubre la hora, y la nueva no se confunde con ella.
    assert verify_webhook(PAYLOAD, cabecera(PAYLOAD), SECRET) is False
    assert verify_webhook_timestamped(PAYLOAD, antigua, SECRET, now=AHORA) is False


def test_verificar_antes_de_interpretar_el_evento() -> None:
    antigua = "sha256=" + hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha256).hexdigest()

    assert verify_webhook_timestamped(PAYLOAD, cabecera(PAYLOAD), SECRET, now=AHORA)
    evento = parse_webhook(PAYLOAD, antigua, SECRET)

    assert evento.event == "validation.completed"


def test_la_cabecera_se_encuentra_sin_importar_como_la_escriba_el_framework() -> None:
    valor = "t=1,v1=abc"

    assert timestamped_signature_from_headers({TIMESTAMPED_SIGNATURE_HEADER: valor}) == valor
    assert timestamped_signature_from_headers({"x-webhook-signature-timestamped": valor}) == valor
    assert (
        timestamped_signature_from_headers({"HTTP_X_WEBHOOK_SIGNATURE_TIMESTAMPED": valor}) == valor
    )
    # La firma de siempre no se confunde con la nueva.
    assert timestamped_signature_from_headers({"X-Webhook-Signature": "sha256=abc"}) is None


def test_el_nombre_de_la_cabecera() -> None:
    assert TIMESTAMPED_SIGNATURE_HEADER == "X-Webhook-Signature-Timestamped"
