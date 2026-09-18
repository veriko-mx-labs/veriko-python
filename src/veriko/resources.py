"""Las operaciones de la API, agrupadas por familia.

Cada familia es un atributo del cliente: `client.validations`, `client.webhooks`
y `client.catalog`. Los tres métodos que estrenó la versión 0.1.0 siguen en la
raíz del cliente, porque son el camino corto del caso de uso principal.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from ._http import Response, Transport, filename_from_content_disposition
from .errors import APIError, ConfigurationError, InvalidRequestError
from .models import (
    Bank,
    CepDocument,
    Document,
    QueuedValidation,
    RetryAttempt,
    RetryPolicy,
    Validation,
    ValidationSummary,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookTestResult,
)
from .pagination import Page, iterate_pages

CEP_FORMATS = ("xml", "pdf")
EXPORT_FORMATS = ("csv", "xlsx")

_EXPORT_CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _path_segment(value: str) -> str:
    """Un identificador que va en la ruta no puede traer barras ni espacios."""
    cleaned = str(value).strip()
    if not cleaned or "/" in cleaned or "?" in cleaned or "#" in cleaned:
        raise ConfigurationError("Identificador inservible: " + repr(value))
    return cleaned


def _clean(params: Mapping[str, Any]) -> dict[str, Any]:
    """Quita los filtros sin valor: lo que no se pasa no viaja."""
    return {clave: valor for clave, valor in params.items() if valor is not None}


def _retry_payload(policy: RetryPolicy | Mapping[str, Any]) -> dict[str, Any]:
    return policy.to_payload() if isinstance(policy, RetryPolicy) else dict(policy)


class _Resource:
    """Base de las familias: todas comparten el transporte del cliente."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport


class Validations(_Resource):
    """Validaciones SPEI: crearlas, seguirlas y descargar lo que producen."""

    # ── Crear ───────────────────────────────────────────────────────────────

    def validate(
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
        """Valida una transferencia SPEI contra el CEP de Banxico.

        `POST /v1/validate`. Devuelve el veredicto en `Validation.status`. Para
        volumen está `enqueue()`, que acepta la petición y deja el veredicto
        para después.
        """
        body = self._direct_body(
            fecha=fecha,
            monto=monto,
            clave_rastreo=clave_rastreo,
            referencia_numerica=referencia_numerica,
            cuenta_beneficiaria=cuenta_beneficiaria,
            emisor=emisor,
            receptor=receptor,
            receptor_participante=receptor_participante,
            retry_policy=retry_policy,
        )
        return self._sync("/validate", body, idempotency_key)

    def validate_ocr(
        self,
        *,
        image: bytes | str | Path | None = None,
        image_url: str | None = None,
        cuenta_beneficiaria: str | None = None,
        retry_policy: RetryPolicy | Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Validation:
        """Valida una transferencia a partir de la imagen del comprobante.

        `POST /v1/validate-ocr`. La imagen se lee del disco cuando `image` es una
        ruta, y se codifica en base64 aquí. Formatos: JPEG, PNG o WebP.

        `image_url` sirve para una imagen ya publicada en HTTPS. Si se envían las
        dos, la API sólo considera `image`.
        """
        body = self._ocr_body(
            image=image,
            image_url=image_url,
            cuenta_beneficiaria=cuenta_beneficiaria,
            retry_policy=retry_policy,
        )
        return self._sync("/validate-ocr", body, idempotency_key)

    @staticmethod
    def _direct_body(
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
        **_ignorados: Any,
    ) -> dict[str, Any]:
        """El cuerpo de una validación por campos, con su comprobación previa."""
        if not clave_rastreo and not referencia_numerica:
            raise InvalidRequestError(
                "Hace falta clave_rastreo o referencia_numerica para buscar la "
                "transferencia en el CEP",
                status=422,
                code="clave_or_ref_required",
            )
        body: dict[str, Any] = {"fecha": fecha, "monto": monto}
        body.update(
            _clean(
                {
                    "clave_rastreo": clave_rastreo,
                    "referencia_numerica": referencia_numerica,
                    "cuenta_beneficiaria": cuenta_beneficiaria,
                    "emisor": emisor,
                    "receptor": receptor,
                    "receptor_participante": receptor_participante,
                }
            )
        )
        if retry_policy is not None:
            body["retry_policy"] = _retry_payload(retry_policy)
        return body

    @staticmethod
    def _ocr_body(
        *,
        image: bytes | str | Path | None = None,
        image_url: str | None = None,
        cuenta_beneficiaria: str | None = None,
        retry_policy: RetryPolicy | Mapping[str, Any] | None = None,
        **_ignorados: Any,
    ) -> dict[str, Any]:
        """El cuerpo de una validación por imagen, con su comprobación previa."""
        if image is None and not image_url:
            raise InvalidRequestError(
                "Hace falta la imagen del comprobante: pasa `image` o `image_url`",
                status=422,
                code="image_required",
            )
        body: dict[str, Any] = {}
        if image is not None:
            body["image"] = _encode_image(image)
        if image_url:
            body["image_url"] = image_url
        if cuenta_beneficiaria:
            body["cuenta_beneficiaria"] = cuenta_beneficiaria
        if retry_policy is not None:
            body["retry_policy"] = _retry_payload(retry_policy)
        return body

    def enqueue(self, **campos: Any) -> QueuedValidation:
        """Encola una validación por campos y devuelve el acuse.

        `POST /v1/validate?async=1`. Acepta los mismos argumentos que
        `validate()`. La API responde `202` con el identificador; el veredicto se
        recoge con `wait_for()`, o llega por webhook.

        https://docs.veriko.mx/es/concepts/async-validations
        """
        return self._queue("/validate", self._direct_body(**campos), campos.get("idempotency_key"))

    def enqueue_ocr(self, **campos: Any) -> QueuedValidation:
        """Encola una validación por imagen y devuelve el acuse.

        `POST /v1/validate-ocr?async=1`. Acepta los mismos argumentos que
        `validate_ocr()`.
        """
        return self._queue("/validate-ocr", self._ocr_body(**campos), campos.get("idempotency_key"))

    def _post(
        self,
        path: str,
        body: Mapping[str, Any],
        idempotency_key: str | None,
        *,
        asincrono: bool,
    ) -> Response:
        from .client import new_idempotency_key

        return self._transport.request(
            "POST",
            path,
            json_body=body,
            query={"async": 1} if asincrono else None,
            extra_headers={"Idempotency-Key": idempotency_key or new_idempotency_key()},
        )

    def _sync(self, path: str, body: Mapping[str, Any], idempotency_key: str | None) -> Validation:
        response = self._post(path, body, idempotency_key, asincrono=False)
        return Validation.from_response(response.json())

    def _queue(
        self, path: str, body: Mapping[str, Any], idempotency_key: str | None
    ) -> QueuedValidation:
        response = self._post(path, body, idempotency_key, asincrono=True)
        document = response.json()
        data = document.get("data") or {}
        meta = document.get("meta") or {}
        return QueuedValidation(
            id=str(data.get("id") or ""),
            status=str((data.get("attributes") or {}).get("status") or "queued"),
            etag=response.headers.get("etag"),
            location=response.headers.get("location"),
            next_poll_after_seconds=meta.get("next_poll_after_seconds"),
            meta=meta,
            raw=document,
        )

    # ── Consultar ───────────────────────────────────────────────────────────

    def get(self, validation_id: str, *, if_none_match: str | None = None) -> Validation:
        """Lee una validación por su identificador.

        `GET /v1/validations/{id}`. Con `if_none_match` la API responde `304`
        cuando nada cambió desde ese `ETag`, y el SDK lo señala levantando
        `APIError` con `status=304`.
        """
        response = self._transport.request(
            "GET",
            "/validations/" + _path_segment(validation_id),
            extra_headers={"If-None-Match": if_none_match} if if_none_match else None,
        )
        validation = Validation.from_response(response.json())
        etag = response.headers.get("etag")
        return replace(validation, etag=etag) if etag else validation

    def list(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
        status: str | None = None,
        type: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        search: str | None = None,
        playground: bool | None = None,
        with_deleted: bool | None = None,
        batch_id: str | None = None,
        bank: str | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        retry_state: str | None = None,
    ) -> Page[ValidationSummary]:
        """Lista las validaciones de la cuenta, con filtros y paginación.

        `GET /v1/validations`. `from_` lleva guion bajo porque `from` es palabra
        reservada de Python; viaja como `from`.
        """
        response = self._transport.request(
            "GET",
            "/validations",
            query=_clean(
                {
                    "page": page,
                    "per_page": per_page,
                    "status": status,
                    "type": type,
                    "from": from_,
                    "to": to,
                    "search": search,
                    "playground": playground,
                    "with_deleted": with_deleted,
                    "batch_id": batch_id,
                    "bank": bank,
                    "amount_min": amount_min,
                    "amount_max": amount_max,
                    "retry_state": retry_state,
                }
            ),
        )
        return Page.from_response(response.json(), ValidationSummary.from_item)

    def iter(self, **filtros: Any) -> Iterator[ValidationSummary]:
        """Recorre todas las validaciones que casan con los filtros.

        Pide cada página cuando la anterior se agota. `max_pages` acota el
        recorrido.
        """
        max_pages = filtros.pop("max_pages", None)
        inicio = int(filtros.pop("page", 1) or 1)
        return iterate_pages(
            lambda numero: self.list(page=numero, **filtros),
            start_page=inicio,
            max_pages=max_pages,
        )

    def stats(self, **filtros: Any) -> dict[str, Any]:
        """Totales de las validaciones de la cuenta, con los mismos filtros.

        `GET /v1/validations/stats`.
        """
        filtros = dict(filtros)
        if "from_" in filtros:
            filtros["from"] = filtros.pop("from_")
        response = self._transport.request("GET", "/validations/stats", query=_clean(filtros))
        data = response.json().get("data")
        return data if isinstance(data, dict) else {}

    def retry_attempts(self, validation_id: str) -> Sequence[RetryAttempt]:
        """Los intentos del ciclo de reintentos de una validación.

        `GET /v1/validations/{id}/retry-attempts`.
        """
        response = self._transport.request(
            "GET", "/validations/" + _path_segment(validation_id) + "/retry-attempts"
        )
        datos = response.json().get("data")
        if not isinstance(datos, list):
            return []
        return [RetryAttempt.from_item(item) for item in datos if isinstance(item, dict)]

    def wait_for(
        self,
        validation_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 5.0,
        sleep: Any = None,
    ) -> Validation:
        """Sondea una validación hasta que su veredicto queda firme.

        Es la pareja de `enqueue()`. Manda `If-None-Match` con el `ETag` de la
        respuesta anterior, así que un sondeo que no encuentra cambios no
        descarga otra vez el mismo cuerpo.

        Espera a `Validation.is_settled`, no a `is_terminal`: una validación con
        reintentos en marcha llega a `not_found` y sigue cambiando después.

        Raises:
            TimeoutError: si se agota `timeout` sin veredicto firme.
        """
        dormir: Any = sleep or time.sleep
        limite = time.monotonic() + timeout
        etag: str | None = None
        ultima: Validation | None = None

        while True:
            try:
                ultima = self.get(validation_id, if_none_match=etag)
                etag = ultima.etag
            except APIError as error:
                if error.status != 304:
                    raise
            if ultima is not None and ultima.is_settled:
                return ultima
            if time.monotonic() >= limite:
                raise TimeoutError(
                    "La validación "
                    + validation_id
                    + " no alcanzó un estado terminal en "
                    + str(timeout)
                    + " s"
                )
            dormir(poll_interval)

    # ── Archivos ────────────────────────────────────────────────────────────

    def cep(self, validation_id: str, *, format: str = "xml") -> CepDocument:
        """Descarga el CEP oficial de una validación en XML o en PDF.

        `GET /v1/validations/{id}/cep`.
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
        return CepDocument(
            validation_id=validation_id,
            content=response.body,
            content_type=response.headers.get("content-type", accept),
            format=format,
            filename=filename_from_content_disposition(
                response.headers.get("content-disposition"),
                "CEP-" + validation_id + "." + format,
            ),
        )

    def image(self, validation_id: str) -> Document:
        """Descarga la imagen del comprobante de una validación por OCR.

        `GET /v1/validations/{id}/image`.
        """
        response = self._transport.request(
            "GET",
            "/validations/" + _path_segment(validation_id) + "/image",
            accept="image/png, image/jpeg, application/json",
        )
        return _document(response, "comprobante-" + validation_id, "image/png")

    def export(self, *, format: str = "csv", **filtros: Any) -> Document:
        """Exporta el historial de validaciones en CSV o en XLSX.

        `GET /v1/validations/export`.
        """
        return _export(
            self._transport,
            "/validations/export",
            format,
            filtros,
            "validaciones",
        )

    # ── Cambiar ─────────────────────────────────────────────────────────────

    def set_retry_policy(
        self,
        validation_id: str,
        policy: RetryPolicy | Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> Validation:
        """Cambia la política de reintentos de una validación concreta.

        `PUT /v1/validations/{id}/retry-policy`.
        """
        response = self._transport.request(
            "PUT",
            "/validations/" + _path_segment(validation_id) + "/retry-policy",
            json_body=_retry_payload(policy),
            extra_headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
        )
        return Validation.from_response(response.json())

    def cancel_retries(
        self, validation_id: str, *, idempotency_key: str | None = None
    ) -> Validation:
        """Detiene el ciclo de reintentos pendientes de una validación.

        `POST /v1/validations/{id}/cancel-retries`.
        """
        response = self._transport.request(
            "POST",
            "/validations/" + _path_segment(validation_id) + "/cancel-retries",
            extra_headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
        )
        return Validation.from_response(response.json())

    def delete(self, validation_id: str) -> None:
        """Retira una validación del historial.

        `DELETE /v1/validations/{id}`. La API responde `204` y no devuelve cuerpo.
        """
        self._transport.request("DELETE", "/validations/" + _path_segment(validation_id))

    def send_cep_to_telegram(self, validation_id: str) -> dict[str, Any]:
        """Envía el comprobante al chat de Telegram vinculado a la cuenta.

        `POST /v1/validations/{id}/cep/send-telegram`. La API acusa con `202`: el
        envío ocurre después.
        """
        response = self._transport.request(
            "POST", "/validations/" + _path_segment(validation_id) + "/cep/send-telegram"
        )
        data = response.json().get("data")
        return data if isinstance(data, dict) else {}


class Webhooks(_Resource):
    """Endpoints de webhook y su historial de entregas."""

    def create(self, *, url: str, events: Sequence[str]) -> WebhookEndpoint:
        """Registra un endpoint y devuelve su secreto de firma.

        `POST /v1/webhooks`. El secreto viaja **una sola vez**, en esta
        respuesta: guárdalo al recibirlo. Si se pierde, se rota con
        `regenerate_secret()`.

        Admite entre 1 y 10 eventos por endpoint.
        """
        if not events:
            raise InvalidRequestError(
                "Un endpoint se suscribe al menos a un evento",
                status=422,
                code="events_required",
            )
        response = self._transport.request(
            "POST", "/webhooks", json_body={"url": url, "events": list(events)}
        )
        return WebhookEndpoint.from_item(response.json().get("data") or {})

    def list(self) -> Sequence[WebhookEndpoint]:
        """Lista los endpoints registrados, sin sus secretos.

        `GET /v1/webhooks`.
        """
        response = self._transport.request("GET", "/webhooks")
        datos = response.json().get("data")
        if not isinstance(datos, list):
            return []
        return [WebhookEndpoint.from_item(item) for item in datos if isinstance(item, dict)]

    def update(
        self,
        webhook_id: str,
        *,
        url: str | None = None,
        events: Sequence[str] | None = None,
        status: str | None = None,
    ) -> WebhookEndpoint:
        """Cambia la URL, los eventos suscritos o el estado de un endpoint.

        `PUT /v1/webhooks/{id}`. Un endpoint que el sistema apagó se reactiva
        con `status="active"`; `auto_disabled` no se puede asignar desde la API.
        """
        body = _clean({"url": url, "events": list(events) if events else None, "status": status})
        if not body:
            raise InvalidRequestError(
                "No hay nada que cambiar: pasa url, events o status",
                status=422,
                code="empty_update",
            )
        response = self._transport.request(
            "PUT", "/webhooks/" + _path_segment(webhook_id), json_body=body
        )
        return WebhookEndpoint.from_item(response.json().get("data") or {})

    def delete(self, webhook_id: str) -> None:
        """Retira un endpoint.

        `DELETE /v1/webhooks/{id}`. La API responde `204`.
        """
        self._transport.request("DELETE", "/webhooks/" + _path_segment(webhook_id))

    def test(self, webhook_id: str) -> WebhookTestResult:
        """Envía un evento de prueba al endpoint.

        `POST /v1/webhooks/{id}/test`. Las entregas de prueba no cuentan para el
        contador de fallos consecutivos.
        """
        response = self._transport.request(
            "POST", "/webhooks/" + _path_segment(webhook_id) + "/test"
        )
        return WebhookTestResult.from_response(response.json())

    def regenerate_secret(self, webhook_id: str) -> WebhookEndpoint:
        """Rota el secreto de firma de un endpoint.

        `POST /v1/webhooks/{id}/regenerate-secret`. El secreto nuevo viene en
        `WebhookEndpoint.secret`, y es la última vez que la API lo entrega.
        """
        response = self._transport.request(
            "POST", "/webhooks/" + _path_segment(webhook_id) + "/regenerate-secret"
        )
        return WebhookEndpoint.from_item(response.json().get("data") or {})

    # ── Entregas ────────────────────────────────────────────────────────────

    def deliveries(
        self,
        webhook_id: str | None = None,
        *,
        page: int | None = None,
        per_page: int | None = None,
        status: str | None = None,
        event_type: str | None = None,
    ) -> Page[WebhookDelivery]:
        """Lista los intentos de entrega.

        Con `webhook_id` consulta los de ese endpoint
        (`GET /v1/webhooks/{id}/deliveries`); sin él, los de todos
        (`GET /v1/webhooks/deliveries`), donde además se puede filtrar por estado
        y por tipo de evento.
        """
        if webhook_id is None:
            path = "/webhooks/deliveries"
            query = _clean(
                {
                    "page": page,
                    "per_page": per_page,
                    "status": status,
                    "event_type": event_type,
                }
            )
        else:
            path = "/webhooks/" + _path_segment(webhook_id) + "/deliveries"
            query = _clean({"page": page, "per_page": per_page})

        response = self._transport.request("GET", path, query=query)
        return Page.from_response(response.json(), WebhookDelivery.from_item)

    def iter_deliveries(
        self, webhook_id: str | None = None, **filtros: Any
    ) -> Iterator[WebhookDelivery]:
        """Recorre todos los intentos de entrega, página a página."""
        max_pages = filtros.pop("max_pages", None)
        inicio = int(filtros.pop("page", 1) or 1)
        return iterate_pages(
            lambda numero: self.deliveries(webhook_id, page=numero, **filtros),
            start_page=inicio,
            max_pages=max_pages,
        )

    def export_deliveries(
        self,
        webhook_id: str | None = None,
        *,
        format: str = "csv",
        **filtros: Any,
    ) -> Document:
        """Exporta el historial de entregas en CSV o en XLSX.

        Con `webhook_id`, las de ese endpoint; sin él, las de todos.
        """
        if webhook_id is None:
            return _export(
                self._transport, "/webhooks/deliveries/export", format, filtros, "entregas"
            )
        return _export(
            self._transport,
            "/webhooks/" + _path_segment(webhook_id) + "/deliveries/export",
            format,
            filtros,
            "entregas-" + webhook_id,
        )


class Catalog(_Resource):
    """Datos abiertos: el catálogo de bancos y el estado del servicio."""

    def banks(self, *, if_none_match: str | None = None) -> Sequence[Bank]:
        """Lista las instituciones participantes en SPEI con su código.

        `GET /v1/public/banks`.
        """
        response = self._transport.request(
            "GET",
            "/public/banks",
            extra_headers={"If-None-Match": if_none_match} if if_none_match else None,
        )
        datos = response.json().get("data")
        if not isinstance(datos, list):
            return []
        return [Bank.from_item(item) for item in datos if isinstance(item, dict)]

    def bin_lookup(self, bin: str) -> dict[str, Any]:
        """Resuelve el banco emisor de una tarjeta por sus primeros dígitos.

        `GET /v1/public/bin-lookup/{bin}`.
        """
        response = self._transport.request("GET", "/public/bin-lookup/" + _path_segment(bin))
        data = response.json().get("data")
        return data if isinstance(data, dict) else {}

    def banxico_status(self) -> dict[str, Any]:
        """Estado del servicio de consulta de Banxico.

        `GET /v1/status/banxico`. Sirve para distinguir un `cep_unavailable`
        propio de la transferencia de una caída del servicio.
        """
        response = self._transport.request("GET", "/status/banxico")
        data = response.json().get("data")
        return data if isinstance(data, dict) else {}

    def banxico_timeseries(
        self, *, metric: str | None = None, window: str | None = None
    ) -> dict[str, Any]:
        """Serie temporal de salud del servicio de Banxico.

        `GET /v1/status/banxico/timeseries`.
        """
        response = self._transport.request(
            "GET",
            "/status/banxico/timeseries",
            query=_clean({"metric": metric, "window": window}),
        )
        data = response.json().get("data")
        return data if isinstance(data, dict) else {}


# ── Ayudantes compartidos ───────────────────────────────────────────────────


def _encode_image(image: bytes | str | Path) -> str:
    """Devuelve la imagen en base64, leyéndola del disco cuando es una ruta."""
    if isinstance(image, bytes):
        crudo = image
    else:
        ruta = Path(image)
        if not ruta.is_file():
            raise ConfigurationError("No existe el archivo de imagen: " + str(image))
        crudo = ruta.read_bytes()
    return base64.b64encode(crudo).decode("ascii")


def _document(response: Response, fallback: str, accept: str) -> Document:
    return Document(
        content=response.body,
        content_type=response.headers.get("content-type", accept),
        filename=filename_from_content_disposition(
            response.headers.get("content-disposition"), fallback
        ),
    )


def _export(
    transport: Transport,
    path: str,
    format: str,
    filtros: Mapping[str, Any],
    fallback: str,
) -> Document:
    if format not in EXPORT_FORMATS:
        raise ConfigurationError(
            "El formato de exportación es 'csv' o 'xlsx'; llegó " + repr(format)
        )
    query = dict(filtros)
    if "from_" in query:
        query["from"] = query.pop("from_")
    query["format"] = format
    accept = _EXPORT_CONTENT_TYPES[format]
    response = transport.request(
        "GET", path, query=_clean(query), accept=accept + ", application/json"
    )
    return _document(response, fallback + "." + format, accept)


__all__ = ["CEP_FORMATS", "EXPORT_FORMATS", "Catalog", "Validations", "Webhooks"]
