"""Verificación de la firma de un webhook.

Cada entrega llega firmada con HMAC-SHA256 del cuerpo, usando el secreto que
devolvió `POST /v1/webhooks` al registrar el endpoint. La firma viaja en
hexadecimal, prefijada por el algoritmo:

    X-Webhook-Signature: sha256=<hex>

Lo que se firma es el cuerpo tal como llegó. Interpretar el JSON y volver a
serializarlo cambia los bytes, porque el orden de las claves, los espacios y el
escape de los caracteres no ASCII no se conservan, y la firma deja de cuadrar.
El receptor lee el cuerpo crudo antes de que el framework lo interprete.

Cada entrega lleva además una segunda firma que incluye la hora del intento:

    X-Webhook-Signature-Timestamped: t=<segundos>,v1=<hex>

`v1` es el HMAC-SHA256 de `<t>.<cuerpo>`. Con ella el receptor puede descartar
una entrega vieja que alguien vuelva a enviar.

https://docs.veriko.mx/es/concepts/webhooks-architecture
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from typing import Any

from .errors import SignatureVerificationError
from .models import BanxicoConfirmed, PaymentStatus, Validation, WebhookEvent

# Cabecera de la firma.
SIGNATURE_HEADER = "X-Webhook-Signature"

# Cabecera de la firma que incluye la hora del intento.
TIMESTAMPED_SIGNATURE_HEADER = "X-Webhook-Signature-Timestamped"

# Ventana recomendada entre `t` y el reloj del receptor, en segundos.
DEFAULT_TOLERANCE_SECONDS = 300

# Otras cabeceras de una entrega.
EVENT_HEADER = "X-Veriko-Event"
DELIVERY_ID_HEADER = "X-Veriko-Delivery-Id"
TIMESTAMP_HEADER = "X-Veriko-Timestamp"

_SIGNATURE_PREFIX = "sha256="
_UNIX_SECONDS = re.compile(r"[0-9]{1,15}")


def compute_signature(payload: bytes | str, secret: str) -> str:
    """Devuelve el HMAC-SHA256 hexadecimal del cuerpo, sin el prefijo `sha256=`."""
    body = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_webhook(
    payload: bytes | str,
    signature: str | None,
    secret: str,
) -> bool:
    """Comprueba la firma de una entrega. Devuelve `True` o `False`, sin lanzar.

    Acepta el valor de la cabecera con o sin el prefijo `sha256=`. La comparación
    es en tiempo constante, para no filtrar información por el tiempo de
    respuesta.

    Args:
        payload: el cuerpo crudo de la petición, sin reserializar.
        signature: el valor de la cabecera `X-Webhook-Signature`.
        secret: el secreto del endpoint, entregado al registrarlo.
    """
    if not signature or not secret:
        return False
    received = signature.strip()
    if received.lower().startswith(_SIGNATURE_PREFIX):
        received = received[len(_SIGNATURE_PREFIX) :]
    expected = compute_signature(payload, secret)
    return hmac.compare_digest(received.lower(), expected)


def _timestamped_parts(header: str) -> tuple[str, list[str]] | None:
    """Separa `t=<segundos>,v1=<hex>` en la hora y las firmas `v1`.

    Admite varias `v1` y descarta las claves que no conoce. Devuelve `None`
    cuando falta `t`, cuando trae más de una, o cuando no es un entero de
    segundos.
    """
    times: list[str] = []
    digests: list[str] = []
    for part in header.split(","):
        key, separator, value = part.strip().partition("=")
        if not separator:
            continue
        key, value = key.strip(), value.strip()
        if key == "t":
            times.append(value)
        elif key == "v1":
            digests.append(value.lower())
    if len(times) != 1 or not _UNIX_SECONDS.fullmatch(times[0]):
        return None
    return times[0], digests


def verify_webhook_timestamped(
    payload: bytes | str,
    header: str | None,
    secret: str,
    *,
    tolerance: float = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> bool:
    """Comprueba la firma con marca de tiempo de una entrega. Devuelve `True` o `False`, sin lanzar.

    Acepta el valor de la cabecera `X-Webhook-Signature-Timestamped`:
    `t=<segundos>,v1=<hex>`. Calcula el HMAC-SHA256 de `<t>.<cuerpo>`, lo
    compara en tiempo constante con cada `v1` y rechaza la entrega cuando `t` se
    aleja del reloj del receptor más de `tolerance` segundos.

    La ventana se mide contra `t`, la hora de ese intento, y no contra el campo
    `timestamp` del cuerpo, que es la hora del evento. Un reintento llega hasta
    unas 8,6 horas después del evento, pero con una `t` nueva.

    Args:
        payload: el cuerpo crudo de la petición, sin reserializar.
        header: el valor de la cabecera `X-Webhook-Signature-Timestamped`.
        secret: el secreto del endpoint, entregado al registrarlo.
        tolerance: la ventana, en segundos. Por omisión, 300 (5 minutos).
        now: la hora actual en segundos Unix. Por omisión, la del reloj del
            sistema; se pasa en las pruebas.
    """
    if not header or not secret:
        return False
    parts = _timestamped_parts(header)
    if parts is None:
        return False
    timestamp, digests = parts

    current = time.time() if now is None else now
    if not abs(current - int(timestamp)) <= tolerance:
        return False

    body = payload.encode("utf-8") if isinstance(payload, str) else payload
    expected = hmac.new(
        secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()
    # Se comparan como bytes: `compare_digest` rechaza con `TypeError` un `str` que no es ASCII.
    # Se comparan todas, sin cortar en la primera que cuadra.
    matches = [
        hmac.compare_digest(digest.encode("utf-8"), expected.encode("ascii")) for digest in digests
    ]
    return any(matches)


def _header_value(headers: Mapping[str, str], header: str) -> str | None:
    """El valor de una cabecera, en cualquiera de las grafías de los frameworks."""
    wanted = {
        header.lower(),
        header.lower().replace("-", "_"),
        "http_" + header.lower().replace("-", "_"),
    }
    for name, value in headers.items():
        if str(name).lower() in wanted:
            return value
    return None


def signature_from_headers(headers: Mapping[str, str]) -> str | None:
    """Busca la cabecera de firma en cualquiera de sus grafías.

    Los nombres de cabecera no distinguen mayúsculas, y cada framework las
    entrega a su manera: `HTTP_X_WEBHOOK_SIGNATURE` en WSGI, por ejemplo.
    """
    return _header_value(headers, SIGNATURE_HEADER)


def timestamped_signature_from_headers(headers: Mapping[str, str]) -> str | None:
    """Busca la cabecera de la firma con marca de tiempo en cualquiera de sus grafías."""
    return _header_value(headers, TIMESTAMPED_SIGNATURE_HEADER)


def parse_webhook(
    payload: bytes | str,
    signature: str | None,
    secret: str,
) -> WebhookEvent:
    """Verifica la firma y devuelve el evento ya interpretado.

    Raises:
        SignatureVerificationError: si la firma no cuadra con el cuerpo. En ese
            caso el cuerpo no se interpreta.
    """
    if not verify_webhook(payload, signature, secret):
        raise SignatureVerificationError(
            "La firma de la entrega no cuadra con el cuerpo recibido. "
            "Revisa que el cuerpo sea el crudo, sin reserializar, y que el "
            "secreto sea el del endpoint que envía."
        )

    body = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    parsed: Any = json.loads(body)
    if not isinstance(parsed, dict):
        raise SignatureVerificationError("El cuerpo de la entrega no es un objeto JSON")
    document: dict[str, Any] = parsed

    data = document.get("data")
    validation: Validation | None = None
    if isinstance(data, dict) and data.get("type") == "validation":
        validation = Validation.from_response(document)

    banxico_confirmed: BanxicoConfirmed | None = None
    payment_status: PaymentStatus | None = None
    if isinstance(data, dict):
        attributes = data.get("attributes")
        confirmado = attributes.get("banxico_confirmed") if isinstance(attributes, dict) else None
        if isinstance(confirmado, dict):
            banxico_confirmed = BanxicoConfirmed.from_dict(confirmado)
        estado = attributes.get("payment_status") if isinstance(attributes, dict) else None
        if isinstance(estado, dict):
            payment_status = PaymentStatus.from_dict(estado)

    return WebhookEvent(
        event=str(document.get("event") or ""),
        timestamp=document.get("timestamp"),
        validation=validation,
        banxico_confirmed=banxico_confirmed,
        payment_status=payment_status,
        data=data if isinstance(data, dict) else {},
        raw=document,
    )


__all__ = [
    "DEFAULT_TOLERANCE_SECONDS",
    "DELIVERY_ID_HEADER",
    "EVENT_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMPED_SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "compute_signature",
    "parse_webhook",
    "signature_from_headers",
    "timestamped_signature_from_headers",
    "verify_webhook",
    "verify_webhook_timestamped",
]
