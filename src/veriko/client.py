"""El cliente: las operaciones de la API que este SDK cubre."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from typing import Any

from ._http import RetryConfig, Transport, filename_from_content_disposition
from .errors import ConfigurationError, InvalidRequestError
from .models import CepDocument, RetryPolicy, Validation

DEFAULT_BASE_URL = "https://api.veriko.mx/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
API_KEY_ENV_VAR = "VERIKO_API_KEY"
BASE_URL_ENV_VAR = "VERIKO_BASE_URL"

CEP_FORMATS = ("xml", "pdf")
_CEP_EXTENSIONS = {"xml": "xml", "pdf": "pdf"}


class Veriko:
    """Cliente de la API de Veriko.

    La clave de API se lee del argumento `api_key` o, si no se pasa, de la
    variable de entorno `VERIKO_API_KEY`. Empieza con `veriko_` y se obtiene en
    el panel: https://app.veriko.mx

    Ejemplo:
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

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    # ── Validar una transferencia ────────────────────────────────────────────

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
        receptor_participante: str | None = None,
        retry_policy: RetryPolicy | Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Validation:
        """Valida una transferencia SPEI contra el CEP de Banxico.

        `POST /v1/validate`. Devuelve el veredicto en `Validation.status`:
        `valid`, `not_found`, `cep_unavailable`, `returned` o `error`.

        Hace falta `clave_rastreo` o `referencia_numerica` — las dos juntas
        precisan más la búsqueda. La fecha es la de **envío** de la
        transferencia, en `YYYY-MM-DD`.

        Un `not_found` inmediato no equivale a una transferencia inexistente: un
        CEP tarda en publicarse. Para eso está `retry_policy`, que deja a la API
        reintentando por su cuenta y avisando por webhook.

        Cada llamada consume cuota del plan, y se descuenta al aceptar la
        petición.

        Args:
            fecha: fecha de la transferencia, `YYYY-MM-DD`.
            monto: importe en pesos, mayor que cero y con hasta dos decimales.
            clave_rastreo: clave de rastreo, 1 a 30 caracteres.
            referencia_numerica: referencia numérica, 1 a 7 dígitos.
            cuenta_beneficiaria: CLABE, tarjeta o celular DiMo del beneficiario.
            emisor: nombre o código SPEI del banco emisor.
            receptor: nombre o código SPEI del banco receptor.
            receptor_participante: clave del participante receptor, si se conoce.
            retry_policy: política de reintentos automáticos de la API.
            idempotency_key: identificador del intento de negocio —el número de
                pedido, de lote o de transacción—. Con él, repetir la petición no
                duplica la validación durante 24 horas. Si no se pasa, el SDK
                genera una por llamada, estable entre sus propios reintentos.

        Raises:
            InvalidRequestError: la petición no es válida (`422`), o falta el
                identificador de la transferencia.
            RateLimitError: límite de tasa o cuota agotada (`429`).
            APIError: cualquier otro error de la API.
        """
        if not clave_rastreo and not referencia_numerica:
            raise InvalidRequestError(
                "Hace falta clave_rastreo o referencia_numerica para buscar la "
                "transferencia en el CEP",
                status=422,
                code="clave_or_ref_required",
            )

        body: dict[str, Any] = {"fecha": fecha, "monto": monto}
        optional = {
            "clave_rastreo": clave_rastreo,
            "referencia_numerica": referencia_numerica,
            "cuenta_beneficiaria": cuenta_beneficiaria,
            "emisor": emisor,
            "receptor": receptor,
            "receptor_participante": receptor_participante,
        }
        for name, value in optional.items():
            if value is not None:
                body[name] = value
        if retry_policy is not None:
            body["retry_policy"] = (
                retry_policy.to_payload()
                if isinstance(retry_policy, RetryPolicy)
                else dict(retry_policy)
            )

        response = self._transport.request(
            "POST",
            "/validate",
            json_body=body,
            extra_headers={"Idempotency-Key": idempotency_key or _new_idempotency_key()},
        )
        return Validation.from_response(response.json())

    # ── Consultar una validación ya hecha ───────────────────────────────────

    def get_validation(self, validation_id: str) -> Validation:
        """Lee una validación por su identificador.

        `GET /v1/validations/{id}`. Es la operación con la que se sigue una
        validación que quedó reintentando: el veredicto final aparece cuando
        `Validation.is_terminal` es `True`.
        """
        response = self._transport.request("GET", "/validations/" + _path_segment(validation_id))
        return Validation.from_response(response.json())

    # ── Obtener el CEP ──────────────────────────────────────────────────────

    def get_cep(self, validation_id: str, *, format: str = "xml") -> CepDocument:
        """Descarga el CEP oficial de una validación.

        `GET /v1/validations/{id}/cep`. Devuelve el archivo, no un enlace: el
        XML que emitió Banxico —con su sello digital y su cadena original— o el
        PDF equivalente.

        Sólo existe cuando la validación tiene comprobante (`Validation.has_cep`).
        Si no, la API responde `404` con `cep_not_available`, y el SDK lo lanza
        como `NotFoundError`.

        Args:
            validation_id: el identificador de la validación.
            format: `xml` (por omisión) o `pdf`.

        Raises:
            ConfigurationError: si `format` no es `xml` ni `pdf`.
            NotFoundError: la validación no existe, o no tiene comprobante.
        """
        if format not in CEP_FORMATS:
            raise ConfigurationError("El formato del CEP es 'xml' o 'pdf'; llegó " + repr(format))

        accept = "application/xml" if format == "xml" else "application/pdf"
        response = self._transport.request(
            "GET",
            "/validations/" + _path_segment(validation_id) + "/cep",
            query={"format": format},
            accept=accept + ", application/json",
        )
        fallback = "CEP-" + validation_id + "." + _CEP_EXTENSIONS[format]
        return CepDocument(
            validation_id=validation_id,
            content=response.body,
            content_type=response.headers.get("content-type", accept),
            format=format,
            filename=filename_from_content_disposition(
                response.headers.get("content-disposition"), fallback
            ),
        )


def _new_idempotency_key() -> str:
    """Una clave por llamada, estable entre los reintentos de esa misma llamada.

    Reintentar un `POST` sin clave de idempotencia puede duplicar la validación
    —y su cargo— cuando la respuesta se perdió pero la petición sí llegó. Con
    clave, el reintento devuelve la respuesta original.

    Una clave derivada del intento de negocio protege más: sobrevive al proceso
    que la generó. Por eso `validate_transfer` acepta `idempotency_key`, y esto
    es sólo el respaldo para quien no la pase.
    """
    return "veriko-python-" + uuid.uuid4().hex


def _path_segment(value: str) -> str:
    """Un identificador que va en la ruta no puede traer barras ni espacios."""
    cleaned = str(value).strip()
    if not cleaned or "/" in cleaned or "?" in cleaned or "#" in cleaned:
        raise ConfigurationError("Identificador de validación inservible: " + repr(value))
    return cleaned


__all__ = ["API_KEY_ENV_VAR", "BASE_URL_ENV_VAR", "CEP_FORMATS", "DEFAULT_BASE_URL", "Veriko"]
