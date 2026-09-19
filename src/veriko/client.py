"""El cliente: la entrada al SDK y las familias de operaciones."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from typing import Any

from ._http import RetryConfig, Transport
from .errors import ConfigurationError
from .models import CepDocument, RetryPolicy, Validation
from .resources import CEP_FORMATS, Beneficiaries, Catalog, Usage, Validations, Webhooks

DEFAULT_BASE_URL = "https://api.veriko.mx/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
API_KEY_ENV_VAR = "VERIKO_API_KEY"
BASE_URL_ENV_VAR = "VERIKO_BASE_URL"


def new_idempotency_key() -> str:
    """Una clave por llamada, estable entre los reintentos de esa misma llamada.

    Reintentar un `POST` sin clave de idempotencia puede duplicar la validación,
    y su cargo, cuando la respuesta se perdió pero la petición llegó. Con clave,
    el reintento devuelve la respuesta original.

    Esta clave no sobrevive al proceso que la generó. La que protege un reenvío
    posterior es la que se pasa en `idempotency_key`.
    """
    return "veriko-python-" + uuid.uuid4().hex


class Veriko:
    """Cliente de la API de Veriko.

    La clave de API se lee del argumento `api_key` o, si no se pasa, de la
    variable de entorno `VERIKO_API_KEY`. Empieza con `veriko_` y se obtiene en
    el panel: https://app.veriko.mx

    Las operaciones se agrupan por familia:

    - `client.validations`: Validar, consultar, reintentar y descargar.
    - `client.webhooks`: Endpoints y su historial de entregas.
    - `client.catalog`: Bancos y estado del servicio de Banxico.
    - `client.beneficiaries`: Cuentas guardadas y su importación masiva.
    - `client.usage`: Cuota, límites y registro de actividad.

    Las tres de uso más frecuente están también en la raíz, como atajo:

        >>> from veriko import Veriko
        >>> client = Veriko()
        >>> validation = client.validate_transfer(
        ...     fecha="2025-03-15",
        ...     monto=15000.50,
        ...     clave_rastreo="MXBA20250315001234",
        ...     cuenta_beneficiaria="012180004412345678",
        ... )
        >>> validation.status
        'valid'

    Args:
        api_key: la clave de API. Por omisión, `VERIKO_API_KEY`.
        base_url: la raíz de la API. Por omisión, `VERIKO_BASE_URL` o producción.
        timeout: segundos de espera por intento.
        max_retries: reintentos automáticos además del primer intento. `0` los
            desactiva. Sólo se reintenta lo que admite reintento: los `5xx`, el
            `408` y el `429`.
        accept_language: `es` o `en`, para el idioma de `error.detail`.
        retry: configuración de reintentos completa, si hace falta afinar el
            backoff. Gana sobre `max_retries`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 2,
        accept_language: str | None = None,
        retry: RetryConfig | None = None,
        user_agent_suffix: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get(API_KEY_ENV_VAR) or ""
        if transport is None and not resolved_key:
            raise ConfigurationError(
                "Falta la clave de API. Pásala como Veriko(api_key=...) o pon "
                + API_KEY_ENV_VAR
                + " en el entorno. Se obtiene en https://app.veriko.mx"
            )
        resolved_base = base_url or os.environ.get(BASE_URL_ENV_VAR) or DEFAULT_BASE_URL

        from . import __version__

        self._transport = transport or Transport(
            base_url=resolved_base,
            api_key=resolved_key,
            timeout=timeout,
            retry=retry or RetryConfig(max_retries=max_retries),
            user_agent=(
                "veriko-python/"
                + __version__
                + " (+https://github.com/veriko-mx-labs/veriko-python)"
                + ((" " + user_agent_suffix) if user_agent_suffix else "")
            ),
            accept_language=accept_language,
        )

        self.validations = Validations(self._transport)
        self.webhooks = Webhooks(self._transport)
        self.catalog = Catalog(self._transport)
        self.beneficiaries = Beneficiaries(self._transport)
        self.usage = Usage(self._transport)

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    # ── Atajos del caso de uso principal ────────────────────────────────────

    def validate_transfer(
        self,
        *,
        fecha: str,
        monto: float | int | str,
        clave_rastreo: str | None = None,
        referencia_numerica: str | None = None,
        cuenta_beneficiaria: str | None = None,
        emisor: str | None = None,
        receptor: str | None = None,
        receptor_participante: int | None = None,
        retry_policy: RetryPolicy | Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Validation:
        """Atajo de `client.validations.validate()`.

        Valida una transferencia SPEI contra el CEP de Banxico. Hace falta
        `clave_rastreo` o `referencia_numerica`; las dos juntas precisan la
        búsqueda. La fecha es la de **envío**, en `YYYY-MM-DD`.

        Cada llamada consume cuota del plan, y se descuenta al aceptar la
        petición.
        """
        return self.validations.validate(
            fecha=fecha,
            monto=monto,
            clave_rastreo=clave_rastreo,
            referencia_numerica=referencia_numerica,
            cuenta_beneficiaria=cuenta_beneficiaria,
            emisor=emisor,
            receptor=receptor,
            receptor_participante=receptor_participante,
            retry_policy=retry_policy,
            idempotency_key=idempotency_key,
        )

    def get_validation(self, validation_id: str, *, if_none_match: str | None = None) -> Validation:
        """Atajo de `client.validations.get()`."""
        return self.validations.get(validation_id, if_none_match=if_none_match)

    def get_cep(self, validation_id: str, *, format: str = "xml") -> CepDocument:
        """Atajo de `client.validations.cep()`.

        Descarga el comprobante oficial: el XML que emitió Banxico, con su sello
        digital y su cadena original, o el PDF equivalente.
        """
        return self.validations.cep(validation_id, format=format)


__all__ = [
    "API_KEY_ENV_VAR",
    "BASE_URL_ENV_VAR",
    "CEP_FORMATS",
    "DEFAULT_BASE_URL",
    "Veriko",
    "new_idempotency_key",
]
