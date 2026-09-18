"""Excepciones del SDK.

La API devuelve los errores en un arreglo `errors`, y el campo estable de cada
entrada es `code` — no `detail`, que se traduce según `Accept-Language` y puede
reformularse entre versiones. Por eso todas las excepciones exponen `code` y la
lista completa de `errors`: ramificar sobre el texto es el error más silencioso
al integrar esta API.

https://docs.veriko.mx/es/concepts/errors
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class VerikoError(Exception):
    """Raíz de todo lo que lanza el SDK."""


class ConfigurationError(VerikoError):
    """Falta la clave de API, o un argumento del cliente es inservible."""


class ConnectionError(VerikoError):
    """No hubo respuesta: DNS, TLS, socket o tiempo agotado.

    Se lanza cuando los reintentos configurados ya se agotaron.
    """

    def __init__(self, message: str, *, attempts: int = 1) -> None:
        super().__init__(message)
        self.attempts = attempts


class APIError(VerikoError):
    """La API respondió, y la respuesta fue un error.

    Attributes:
        status: estado HTTP.
        code: `errors[0].code`, el contrato estable sobre el que ramificar.
        detail: `errors[0].detail`, texto legible y traducido.
        pointer: `errors[0].source.pointer` cuando el rechazo señala un campo.
        errors: el arreglo completo. Un `422` trae una entrada por campo
            inválido; leer sólo la primera oculta el resto.
        request_id: `meta.request_id`, el dato con el que se investiga un caso.
        meta: el objeto `meta` completo de la respuesta.
        headers: cabeceras de la respuesta, con el nombre en minúsculas.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str | None = None,
        detail: str | None = None,
        pointer: str | None = None,
        errors: Sequence[dict[str, Any]] | None = None,
        request_id: str | None = None,
        meta: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.detail = detail
        self.pointer = pointer
        self.errors: list[dict[str, Any]] = list(errors or [])
        self.request_id = request_id
        self.meta: dict[str, Any] = meta or {}
        self.headers: dict[str, str] = headers or {}

    def __str__(self) -> str:
        head = f"HTTP {self.status}"
        if self.code:
            head = f"{head} {self.code}"
        suffix = f" (request_id={self.request_id})" if self.request_id else ""
        return f"{head}: {super().__str__()}{suffix}"


class AuthenticationError(APIError):
    """`401`. Falta la clave de API o ya no es válida."""


class ForbiddenError(APIError):
    """`403`. Autenticado, pero sin permiso para esa operación."""


class NotFoundError(APIError):
    """`404`. El recurso no existe, o el CEP no está disponible (`cep_not_available`)."""


class ConflictError(APIError):
    """`409`. Conflicto de estado; con `Idempotency-Key`, la petición anterior sigue en curso."""


class InvalidRequestError(APIError):
    """`400`, `413` y `422`. La petición hay que corregirla antes de repetirla."""


class RateLimitError(APIError):
    """`429`. Límite de tasa superado, o cuota del plan agotada.

    `retry_after` son los segundos que la respuesta pide esperar, cuando los
    trae: es el único dato necesario para reintentar sin adivinar.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServerError(APIError):
    """`5xx`. Admite reintento; el SDK ya lo hizo si quedaban intentos."""


class SignatureVerificationError(VerikoError):
    """La firma de un webhook no cuadra con el cuerpo recibido."""


__all__ = [
    "APIError",
    "AuthenticationError",
    "ConfigurationError",
    "ConflictError",
    "ConnectionError",
    "ForbiddenError",
    "InvalidRequestError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "SignatureVerificationError",
    "VerikoError",
]
