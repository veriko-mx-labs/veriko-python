"""Verificación de la firma de un webhook.

Cada entrega llega firmada con HMAC-SHA256 del cuerpo, usando el secreto que
devolvió `POST /v1/webhooks` al registrar el endpoint. La firma viaja en
hexadecimal, prefijada por el algoritmo:

    X-Webhook-Signature: sha256=<hex>

Lo que se firma es el cuerpo tal como llegó. Interpretar el JSON y volver a
serializarlo cambia los bytes, porque el orden de las claves, los espacios y el
escape de los caracteres no ASCII no se conservan, y la firma deja de cuadrar.
El receptor lee el cuerpo crudo antes de que el framework lo interprete.

https://docs.veriko.mx/es/concepts/webhooks-architecture
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from .errors import SignatureVerificationError
from .models import Validation, WebhookEvent

# Cabecera de la firma.
SIGNATURE_HEADER = "X-Webhook-Signature"

# Otras cabeceras de una entrega.
EVENT_HEADER = "X-Veriko-Event"
DELIVERY_ID_HEADER = "X-Veriko-Delivery-Id"
TIMESTAMP_HEADER = "X-Veriko-Timestamp"

_SIGNATURE_PREFIX = "sha256="


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


def signature_from_headers(headers: Mapping[str, str]) -> str | None:
    """Busca la cabecera de firma en cualquiera de sus grafías.

    Los nombres de cabecera no distinguen mayúsculas, y cada framework las
    entrega a su manera: `HTTP_X_WEBHOOK_SIGNATURE` en WSGI, por ejemplo.
    """
    wanted = {
        SIGNATURE_HEADER.lower(),
        SIGNATURE_HEADER.lower().replace("-", "_"),
        "http_" + SIGNATURE_HEADER.lower().replace("-", "_"),
    }
    for name, value in headers.items():
        if str(name).lower() in wanted:
            return value
    return None


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

    return WebhookEvent(
        event=str(document.get("event") or ""),
        timestamp=document.get("timestamp"),
        validation=validation,
        data=data if isinstance(data, dict) else {},
        raw=document,
    )


__all__ = [
    "DELIVERY_ID_HEADER",
    "EVENT_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "compute_signature",
    "parse_webhook",
    "signature_from_headers",
    "verify_webhook",
]
