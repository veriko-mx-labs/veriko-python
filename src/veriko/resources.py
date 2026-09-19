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

from ._http import MultipartFile, Response, Transport, filename_from_content_disposition
from .errors import APIError, ConfigurationError, InvalidRequestError
from .models import (
    Bank,
    BankList,
    Beneficiary,
    BeneficiaryImportJob,
    BeneficiaryImportRow,
    BeneficiaryLookup,
    CepDocument,
    Document,
    QueuedValidation,
    RetryAttempt,
    RetryPolicy,
    RetryState,
    UsageSummary,
    Validation,
    ValidationSummary,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookTestResult,
)
from .pagination import Page, iterate_pages

CEP_FORMATS = ("xml", "pdf")
EXPORT_FORMATS = ("csv", "xlsx")
TEMPLATE_FORMATS = ("csv", "xlsx", "xls", "txt", "json")

_EXPORT_CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

_TEMPLATE_CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "txt": "text/plain",
    "json": "application/json",
}

_IMPORT_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}


_IMAGE_ACCEPT = "image/png, image/jpeg, image/webp, application/octet-stream, application/json"

_IMAGE_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}

# Marca un argumento que no se pasó, para distinguirlo de `None`, que borra un valor.
_UNSET: Any = object()


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


def _flag(value: bool | None) -> str | None:
    """`True` viaja como `1` y `False` como `0`; sin valor, el filtro no viaja."""
    if value is None:
        return None
    return "1" if value else "0"


def _filters(filtros: Mapping[str, Any]) -> dict[str, Any]:
    """Los filtros de una consulta de validaciones, con el nombre y el valor que la API espera.

    `from_` viaja como `from`. `playground` sólo admite `1`, así que `False` no
    filtra. `with_deleted` distingue `1` (sólo retiradas) de `0` (sólo activas).
    Varios estados se unen con comas.
    """
    query = dict(filtros)
    if "from_" in query:
        query["from"] = query.pop("from_")
    if "playground" in query:
        query["playground"] = "1" if query["playground"] else None
    if "with_deleted" in query:
        query["with_deleted"] = _flag(query["with_deleted"])
    estados = query.get("status")
    if isinstance(estados, (list, tuple)):
        query["status"] = ",".join(str(estado) for estado in estados)
    return _clean(query)


def _subscribed_events(events: Sequence[str] | None) -> list[str]:
    """Los eventos de un endpoint. Uno como mínimo, y la API admite hasta diez."""
    if not events:
        raise InvalidRequestError(
            "Un endpoint se suscribe al menos a un evento",
            status=422,
            code="events_required",
        )
    return list(events)


def _retry_state(response: Response) -> RetryState:
    """El estado del ciclo de reintentos que devuelven el cambio de política y la cancelación."""
    data = response.json().get("data")
    attributes = data.get("attributes") if isinstance(data, dict) else None
    estado = attributes.get("retry_state") if isinstance(attributes, dict) else None
    return RetryState.from_dict(estado if isinstance(estado, dict) else None)


def _not_modified(what: str, response: Response) -> APIError:
    """El `304` de una lectura condicional, señalado como `APIError` con `status=304`."""
    return APIError(
        what + " no cambió desde el ETag indicado",
        status=304,
        headers=response.headers,
    )


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
        if response.status == 304:
            raise _not_modified("La validación", response)
        validation = Validation.from_response(response.json())
        etag = response.headers.get("etag")
        return replace(validation, etag=etag) if etag else validation

    def list(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
        status: str | Sequence[str] | None = None,
        type: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        search: str | None = None,
        playground: bool | None = None,
        with_deleted: bool | None = None,
        batch_id: int | None = None,
        bank: str | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        retry_state: str | None = None,
    ) -> Page[ValidationSummary]:
        """Lista las validaciones de la cuenta, con filtros y paginación.

        `GET /v1/validations`. `from_` lleva guion bajo porque `from` es palabra
        reservada de Python; viaja como `from`. `status` acepta un estado o una
        lista. `with_deleted=True` devuelve sólo las retiradas y `False` sólo las
        activas. `playground=True` limita a las del banco de pruebas.

        Un listado trae menos campos que `get()`: no incluye los datos enviados,
        el resultado de Banxico ni los enlaces al comprobante.
        """
        response = self._transport.request(
            "GET",
            "/validations",
            query={
                "page": page,
                "per_page": per_page,
                **_filters(
                    {
                        "status": status,
                        "type": type,
                        "from_": from_,
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
            },
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

        `GET /v1/validations/stats`. Devuelve los contadores (`total`, `valid`,
        `not_found`, `by_type`, `by_status`...).
        """
        response = self._transport.request("GET", "/validations/stats", query=_filters(filtros))
        return _data_attributes(response)

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
            accept=_IMAGE_ACCEPT,
        )
        tipo = response.headers.get("content-type", "image/png")
        extension = _IMAGE_EXTENSIONS.get(tipo.split(";")[0].strip(), "")
        return _document(response, "comprobante-" + validation_id + extension, tipo)

    def export(self, *, format: str = "csv", **filtros: Any) -> Document:
        """Exporta el historial de validaciones en CSV o en XLSX.

        `GET /v1/validations/export`.
        """
        return _export(
            self._transport,
            "/validations/export",
            format,
            _filters(filtros),
            "validaciones",
        )

    # ── Cambiar ─────────────────────────────────────────────────────────────

    def set_retry_policy(
        self,
        validation_id: str,
        policy: RetryPolicy | Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> RetryState:
        """Cambia la política de reintentos de una validación concreta.

        `PUT /v1/validations/{id}/retry-policy`. El cuerpo va envuelto en
        `retry_policy`. Devuelve el estado del ciclo de reintentos, no la
        validación completa.
        """
        response = self._transport.request(
            "PUT",
            "/validations/" + _path_segment(validation_id) + "/retry-policy",
            json_body={"retry_policy": _retry_payload(policy)},
            extra_headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
        )
        return _retry_state(response)

    def cancel_retries(
        self, validation_id: str, *, idempotency_key: str | None = None
    ) -> RetryState:
        """Detiene el ciclo de reintentos pendientes de una validación.

        `POST /v1/validations/{id}/cancel-retries`. Devuelve el estado del ciclo,
        con `terminal_state="cancelled"`. Si el ciclo ya no está activo, la API
        responde `422` con `retry_not_active`.
        """
        response = self._transport.request(
            "POST",
            "/validations/" + _path_segment(validation_id) + "/cancel-retries",
            extra_headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
        )
        return _retry_state(response)

    def delete(self, validation_id: str) -> None:
        """Retira una validación del historial.

        `DELETE /v1/validations/{id}`. La API responde `204` y no devuelve cuerpo.
        """
        self._transport.request("DELETE", "/validations/" + _path_segment(validation_id))

    def send_cep_to_telegram(self, validation_id: str) -> dict[str, Any]:
        """Envía el comprobante al chat de Telegram vinculado a la cuenta.

        `POST /v1/validations/{id}/cep/send-telegram`. La API acusa con `202`: el
        envío ocurre después. Devuelve el acuse (`{"queued": True}`).
        """
        response = self._transport.request(
            "POST", "/validations/" + _path_segment(validation_id) + "/cep/send-telegram"
        )
        return _data_attributes(response)


class Webhooks(_Resource):
    """Endpoints de webhook y su historial de entregas."""

    def create(
        self, *, url: str, events: Sequence[str], description: str | None = None
    ) -> WebhookEndpoint:
        """Registra un endpoint y devuelve su secreto de firma.

        `POST /v1/webhooks`. El secreto viaja **una sola vez**, en esta
        respuesta: guárdalo al recibirlo. Si se pierde, se rota con
        `regenerate_secret()`.

        Admite entre 1 y 10 eventos por endpoint. `description` es una etiqueta
        libre para distinguirlo de los demás.
        """
        body = _clean(
            {"url": url, "events": _subscribed_events(events), "description": description}
        )
        response = self._transport.request("POST", "/webhooks", json_body=body)
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
        description: str | None = _UNSET,
    ) -> WebhookEndpoint:
        """Cambia la URL, los eventos, la etiqueta o el estado de un endpoint.

        `PUT /v1/webhooks/{id}`. Un endpoint que el sistema apagó se reactiva
        con `status="active"`; `auto_disabled` no se puede asignar desde la API.
        `description=None` borra la etiqueta, y `events` no puede ir vacío.
        """
        body = _clean({"url": url, "status": status})
        if events is not None:
            body["events"] = _subscribed_events(events)
        if description is not _UNSET:
            body["description"] = description
        if not body:
            raise InvalidRequestError(
                "No hay nada que cambiar: pasa url, events, description o status",
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
        y por tipo de evento. La ruta por endpoint no admite esos filtros, así que
        con `webhook_id` y un filtro la petición va al listado global con
        `endpoint_id`.
        """
        filtrado = status is not None or event_type is not None
        if webhook_id is not None and not filtrado:
            path = "/webhooks/" + _path_segment(webhook_id) + "/deliveries"
            query = _clean({"page": page, "per_page": per_page})
        else:
            path = "/webhooks/deliveries"
            query = _clean(
                {
                    "page": page,
                    "per_page": per_page,
                    "endpoint_id": None if webhook_id is None else _path_segment(webhook_id),
                    "status": status,
                    "event_type": event_type,
                }
            )

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

        Con `webhook_id`, las de ese endpoint; sin él, las de todos. Como en
        `deliveries()`, un filtro (`status`, `event_type`) con `webhook_id` lleva la
        petición al listado global con `endpoint_id`.
        """
        filtrado = filtros.get("status") is not None or filtros.get("event_type") is not None
        if webhook_id is not None and not filtrado:
            return _export(
                self._transport,
                "/webhooks/" + _path_segment(webhook_id) + "/deliveries/export",
                format,
                {"limit": filtros.get("limit")},
                "entregas-" + webhook_id,
            )
        return _export(
            self._transport,
            "/webhooks/deliveries/export",
            format,
            {
                **filtros,
                "endpoint_id": None if webhook_id is None else _path_segment(webhook_id),
            },
            "entregas" if webhook_id is None else "entregas-" + webhook_id,
        )


class Catalog(_Resource):
    """Datos abiertos: el catálogo de bancos y el estado del servicio."""

    def banks(self, *, if_none_match: str | None = None) -> BankList:
        """Lista las instituciones participantes en SPEI con su código.

        `GET /v1/public/banks`. Con `if_none_match` la API responde `304` cuando el
        catálogo no cambió, y el SDK lo señala levantando `APIError` con
        `status=304`. El `ETag` de cada lectura queda en `BankList.etag`.
        """
        response = self._transport.request(
            "GET",
            "/public/banks",
            extra_headers={"If-None-Match": if_none_match} if if_none_match else None,
        )
        if response.status == 304:
            raise _not_modified("El catálogo", response)
        datos = response.json().get("data")
        bancos = (
            [Bank.from_item(item) for item in datos if isinstance(item, dict)]
            if isinstance(datos, list)
            else []
        )
        return BankList(bancos, etag=response.headers.get("etag"))

    def bin_lookup(self, bin: str) -> dict[str, Any]:
        """Resuelve el banco emisor de una tarjeta por sus primeros dígitos.

        `GET /v1/public/bin-lookup/{bin}`. Devuelve `bin`, `bank_name`,
        `banxico_code`, `card_brand`, `card_type`, `card_level` y `country_iso`.
        """
        response = self._transport.request("GET", "/public/bin-lookup/" + _path_segment(bin))
        return _data_attributes(response)

    def banxico_status(self) -> dict[str, Any]:
        """Estado del servicio de consulta de Banxico.

        `GET /v1/status/banxico`. Sirve para distinguir un `cep_unavailable`
        propio de la transferencia de una caída del servicio. Devuelve `status`
        (`operational`, `degraded`, `down` o `unknown`), `status_label`, `message`
        y `last_verified_at`.
        """
        response = self._transport.request("GET", "/status/banxico")
        return _data_attributes(response)

    def banxico_timeseries(
        self, *, metric: str | None = None, window: str | None = None
    ) -> dict[str, Any]:
        """Serie temporal de salud del servicio de Banxico.

        `GET /v1/status/banxico/timeseries`. `metric` es `probe_latency` o
        `verdict`, y `window`, una de `1h`, `8h`, `12h`, `24h` o `7d`. Devuelve
        `unit`, `bucket_size_minutes` y `points` (`ts` y `value`).
        """
        response = self._transport.request(
            "GET",
            "/status/banxico/timeseries",
            query=_clean({"metric": metric, "window": window}),
        )
        return _data_attributes(response)


class Beneficiaries(_Resource):
    """Cuentas beneficiarias guardadas y su importación masiva."""

    # ── La lista blanca ─────────────────────────────────────────────────────

    def create(
        self,
        *,
        account_number: str,
        bank_code: str | None = None,
        label: str | None = None,
    ) -> Beneficiary:
        """Registra una cuenta beneficiaria.

        `POST /beneficiaries`. El tipo se detecta por longitud: CLABE (18
        dígitos), tarjeta (16) o celular DiMo (10). Para un celular, `bank_code`
        es obligatorio; en CLABE y tarjeta se deriva del número. Un alta de una
        cuenta archivada la reactiva.
        """
        if not account_number:
            raise InvalidRequestError(
                "Hace falta account_number para registrar el beneficiario",
                status=422,
                code="account_number_required",
            )
        body = _clean({"account_number": account_number, "bank_code": bank_code, "label": label})
        response = self._transport.request("POST", "/beneficiaries", json_body=body)
        return Beneficiary.from_response(response.json())

    def list(self, *, with_archived: str | None = None) -> Sequence[Beneficiary]:
        """Lista las cuentas beneficiarias guardadas.

        `GET /beneficiaries`. Sin paginar: vuelve la lista completa. El filtro
        `with_archived` vale `0` para sólo activas, `1` para sólo archivadas; sin
        él, las dos.
        """
        response = self._transport.request(
            "GET", "/beneficiaries", query=_clean({"with_archived": with_archived})
        )
        datos = response.json().get("data")
        if not isinstance(datos, list):
            return []
        return [Beneficiary.from_item(item) for item in datos if isinstance(item, dict)]

    def update(
        self,
        beneficiary_id: str,
        *,
        label: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
    ) -> Beneficiary:
        """Cambia la etiqueta, la cuenta o el banco de un beneficiario.

        `PUT /beneficiaries/{id}`. Un `account_number` nuevo vuelve a derivar el
        tipo y el banco; `bank_code` solo se aplica sobre cuentas de tipo
        celular.
        """
        body = _clean({"label": label, "account_number": account_number, "bank_code": bank_code})
        if not body:
            raise InvalidRequestError(
                "No hay nada que cambiar: pasa label, account_number o bank_code",
                status=422,
                code="no_valid_fields",
            )
        response = self._transport.request(
            "PUT", "/beneficiaries/" + _path_segment(beneficiary_id), json_body=body
        )
        return Beneficiary.from_response(response.json())

    def delete(self, beneficiary_id: str) -> None:
        """Archiva un beneficiario.

        `DELETE /beneficiaries/{id}`. El registro no se borra: sale de la lista
        activa y se consulta con `with_archived=1`. Un alta posterior con la
        misma cuenta lo reactiva. La API responde `204`.
        """
        self._transport.request("DELETE", "/beneficiaries/" + _path_segment(beneficiary_id))

    def lookup(self, account: str) -> BeneficiaryLookup:
        """Resuelve una cuenta concreta dentro de la lista propia.

        `GET /beneficiaries/lookup`. Devuelve los datos del banco cuando la
        cuenta está entre las guardadas; si no, la API responde `404`.
        """
        response = self._transport.request(
            "GET", "/beneficiaries/lookup", query={"account": account}
        )
        return BeneficiaryLookup.from_response(response.json())

    def export(
        self,
        *,
        format: str = "csv",
        with_archived: str | None = None,
        limit: int | None = None,
    ) -> Document:
        """Exporta la lista de beneficiarios en CSV o en XLSX.

        `GET /beneficiaries/export`. Los números salen enmascarados salvo la
        CLABE; `limit` solo baja el tope de 100 000 filas.
        """
        return _export(
            self._transport,
            "/beneficiaries/export",
            format,
            {"with_archived": with_archived, "limit": limit},
            "beneficiarios",
        )

    # ── Importación masiva ──────────────────────────────────────────────────

    def import_template(self, *, format: str = "csv") -> Document:
        """Descarga la plantilla para la importación masiva.

        `GET /beneficiaries/imports/template`. Sirve de punto de partida del
        ciclo: descargar, rellenar, subir, revisar y confirmar.
        """
        if format not in TEMPLATE_FORMATS:
            raise ConfigurationError(
                "El formato de plantilla es 'csv', 'xlsx', 'xls', 'txt' o 'json'; llegó "
                + repr(format)
            )
        accept = _TEMPLATE_CONTENT_TYPES[format]
        response = self._transport.request(
            "GET",
            "/beneficiaries/imports/template",
            query={"format": format},
            accept=accept + ", application/json",
        )
        return _document(response, "beneficiarios-plantilla." + format, accept)

    def import_start(
        self,
        file: bytes | str | Path,
        *,
        parse_mode: str = "template",
        filename: str | None = None,
    ) -> BeneficiaryImportJob:
        """Sube un archivo y abre un trabajo de importación.

        `POST /beneficiaries/imports`. `file` puede ser bytes o una ruta, que el
        SDK lee del disco. `parse_mode` es `template` (encabezados canónicos) o
        `free` (formato libre). La respuesta es un `202` con el trabajo en
        estado `pending`; nada se persiste hasta `import_commit()`.
        """
        content, nombre, content_type = _import_file(file, filename)
        response = self._transport.request(
            "POST",
            "/beneficiaries/imports",
            multipart={
                "parse_mode": parse_mode,
                "file": MultipartFile(filename=nombre, content=content, content_type=content_type),
            },
        )
        return BeneficiaryImportJob.from_response(response.json())

    def import_status(self, import_id: str) -> BeneficiaryImportJob:
        """Lee el estado de un trabajo de importación y sus contadores.

        `GET /beneficiaries/imports/{id}`.
        """
        response = self._transport.request(
            "GET", "/beneficiaries/imports/" + _path_segment(import_id)
        )
        return BeneficiaryImportJob.from_response(response.json())

    def import_preview(
        self,
        import_id: str,
        *,
        page: int | None = None,
        per_page: int | None = None,
        buckets: Sequence[str] | None = None,
    ) -> Page[BeneficiaryImportRow]:
        """Lista las filas extraídas de la importación, paginadas.

        `GET /beneficiaries/imports/{id}/preview`. Disponible en `preview_ready`
        o después. `buckets` filtra por grupo (`valid`, `correctable`, `fatal`,
        `duplicate_account`, `duplicate_alias`).
        """
        response = self._transport.request(
            "GET",
            "/beneficiaries/imports/" + _path_segment(import_id) + "/preview",
            query=_clean({"page": page, "per_page": per_page, "buckets": buckets}),
        )
        return Page.from_response(response.json(), BeneficiaryImportRow.from_item)

    def iter_import_preview(self, import_id: str, **filtros: Any) -> Iterator[BeneficiaryImportRow]:
        """Recorre todas las filas de la vista previa, página a página."""
        max_pages = filtros.pop("max_pages", None)
        inicio = int(filtros.pop("page", 1) or 1)
        return iterate_pages(
            lambda numero: self.import_preview(import_id, page=numero, **filtros),
            start_page=inicio,
            max_pages=max_pages,
        )

    def import_edit_row(
        self,
        import_id: str,
        row_id: str,
        *,
        parsed_account: str | None = None,
        parsed_label: str | None = None,
        parsed_account_type: str | None = None,
        parsed_bank_code: str | None = None,
        parsed_bank_name: str | None = None,
    ) -> BeneficiaryImportRow:
        """Corrige una fila de la vista previa antes de confirmar.

        `PATCH /beneficiaries/imports/{id}/rows/{row_id}`. Sólo los campos
        presentes se sobreescriben; la fila se reprocesa y su grupo puede
        cambiar con la corrección.
        """
        body = _clean(
            {
                "parsed_account": parsed_account,
                "parsed_label": parsed_label,
                "parsed_account_type": parsed_account_type,
                "parsed_bank_code": parsed_bank_code,
                "parsed_bank_name": parsed_bank_name,
            }
        )
        if not body:
            raise InvalidRequestError(
                "No hay nada que corregir: pasa alguno de los campos parsed_*",
                status=422,
                code="no_valid_fields",
            )
        response = self._transport.request(
            "PATCH",
            "/beneficiaries/imports/" + _path_segment(import_id) + "/rows/" + _path_segment(row_id),
            json_body=body,
        )
        return BeneficiaryImportRow.from_response(response.json())

    def import_remove_row(self, import_id: str, row_id: str) -> None:
        """Quita una fila de la vista previa.

        `DELETE /beneficiaries/imports/{id}/rows/{row_id}`. La API responde `204`.
        """
        self._transport.request(
            "DELETE",
            "/beneficiaries/imports/" + _path_segment(import_id) + "/rows/" + _path_segment(row_id),
        )

    def import_commit(self, import_id: str) -> BeneficiaryImportJob:
        """Confirma la importación y dispara la persistencia de sus filas.

        `POST /beneficiaries/imports/{id}/commit`. La operación es asíncrona: la
        respuesta es un `202` con el trabajo en `committing`, y el resultado se
        sigue con `import_wait()`.
        """
        response = self._transport.request(
            "POST", "/beneficiaries/imports/" + _path_segment(import_id) + "/commit"
        )
        return BeneficiaryImportJob.from_response(response.json())

    def import_cancel(self, import_id: str) -> None:
        """Cancela una importación que todavía no se confirmó.

        `DELETE /beneficiaries/imports/{id}`. Admite los estados `pending`,
        `parsing` y `preview_ready`; con uno terminal o en `committing`, la API
        responde `404`. Las cuentas que una confirmación ya persistió se archivan
        una a una con `delete()`. La API responde `204`.
        """
        self._transport.request("DELETE", "/beneficiaries/imports/" + _path_segment(import_id))

    def import_wait(
        self,
        import_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
        sleep: Any = None,
    ) -> BeneficiaryImportJob:
        """Sondea una importación hasta que su avance se detiene.

        Espera a `BeneficiaryImportJob.is_settled`: `preview_ready` o un estado
        final. A diferencia de `validations.wait_for()`, aquí no hay `ETag` que
        reutilizar: el endpoint de estado no lo expone, así que cada sondeo
        descarga el cuerpo entero.

        Raises:
            TimeoutError: si se agota `timeout` sin llegar a un estado firme.
        """
        dormir: Any = sleep or time.sleep
        limite = time.monotonic() + timeout
        while True:
            trabajo = self.import_status(import_id)
            if trabajo.is_settled:
                return trabajo
            if time.monotonic() >= limite:
                raise TimeoutError(
                    "La importación "
                    + import_id
                    + " no llegó a preview_ready ni a un estado final en "
                    + str(timeout)
                    + " s"
                )
            dormir(poll_interval)


class Usage(_Resource):
    """Consumo y límites: la cuota del plan y el registro de actividad."""

    def summary(self) -> UsageSummary:
        """Devuelve la cuota de validaciones del plan en curso.

        `GET /usage/summary`. Trae el límite, lo consumido, lo restante y el
        nivel de aviso en `tone`.
        """
        response = self._transport.request("GET", "/usage/summary")
        return UsageSummary.from_response(response.json())

    def history(self, *, months: int | None = None) -> dict[str, Any]:
        """Devuelve el consumo mensual de los últimos `months` meses.

        `GET /usage/history`. De más reciente a más antiguo. El límite que
        acompaña a cada fila es el de hoy, no el que regía aquel mes.
        """
        response = self._transport.request(
            "GET", "/usage/history", query=_clean({"months": months})
        )
        return _data_attributes(response)

    def breakdown(self, *, period: str | None = None) -> dict[str, Any]:
        """Desglosa el consumo por tipo de operación contabilizada.

        `GET /usage/breakdown`. `period` es `current` para el mes en curso o una
        cadena `YYYY-MM`.
        """
        response = self._transport.request(
            "GET", "/usage/breakdown", query=_clean({"period": period})
        )
        return _data_attributes(response)

    def limits(self) -> dict[str, Any]:
        """Devuelve los límites de tasa aplicables, por contexto.

        `GET /usage/limits`. Sólo la configuración vigente, sin contadores en
        vivo; son ajenos a la cuota mensual de `summary()`.
        """
        response = self._transport.request("GET", "/usage/limits")
        return _data_attributes(response)

    def heatmap(self, *, days: int | None = None) -> dict[str, Any]:
        """Devuelve las validaciones agrupadas por día y hora.

        `GET /usage/heatmap`. Cubre los últimos `days` días (máximo 90) y sólo
        trae las celdas con al menos una validación.
        """
        response = self._transport.request("GET", "/usage/heatmap", query=_clean({"days": days}))
        return _data_attributes(response)

    def api_usage(self) -> dict[str, Any]:
        """Devuelve las métricas de uso de la API de la cuenta.

        `GET /api/usage`. Reúne las peticiones de hoy y del mes, la cuota del
        plan, las últimas peticiones y el estado del servicio de Banxico.
        """
        response = self._transport.request("GET", "/api/usage")
        data = response.json().get("data")
        return data if isinstance(data, dict) else {}

    def export(
        self,
        *,
        format: str = "csv",
        from_: str | None = None,
        to: str | None = None,
        limit: int | None = None,
    ) -> Document:
        """Exporta el registro de actividad en CSV o en XLSX.

        `GET /api/usage/export`. `from_` y `to` acotan el rango, las dos
        inclusive; `limit` solo baja el tope de 100 000 filas.
        """
        return _export(
            self._transport,
            "/api/usage/export",
            format,
            {"from_": from_, "to": to, "limit": limit},
            "actividad-api",
        )


class Account(_Resource):
    """Perfil y preferencias de la cuenta autenticada."""

    def my_profile(self) -> dict[str, Any]:
        """`GET /users/me`: devuelve los atributos del perfil."""
        response = self._transport.request("GET", "/users/me")
        return _data_attributes(response)

    def profile(self) -> dict[str, Any]:
        return self.my_profile()

    def get_my_retry_policy(self) -> dict[str, Any]:
        """`GET /users/me/retry-policy`."""
        response = self._transport.request("GET", "/users/me/retry-policy")
        return _data_attributes(response)

    def retry_policy(self) -> dict[str, Any]:
        return self.get_my_retry_policy()

    def update_my_retry_policy(
        self,
        policy: RetryPolicy | Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """`PUT /users/me/retry-policy` con el cuerpo completo de la política."""
        response = self._transport.request(
            "PUT",
            "/users/me/retry-policy",
            json_body={"retry_policy": _retry_payload(policy)},
            extra_headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
        )
        return _data_attributes(response)


class Dashboard(_Resource):
    """Resumen del panel de la cuenta autenticada."""

    def get_summary(self, *, limit: int | None = None) -> dict[str, Any]:
        response = self._transport.request("GET", "/summary", query=_clean({"limit": limit}))
        return _data_attributes(response)

    def summary(self, *, limit: int | None = None) -> dict[str, Any]:
        return self.get_summary(limit=limit)


class Plans(_Resource):
    """Catálogo de planes públicos, sin API key."""

    def list_public(self) -> list[dict[str, Any]]:
        response = self._transport.request("GET", "/plans/public", authenticated=False)
        data = response.json().get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def list(self) -> list[dict[str, Any]]:
        return self.list_public()

    def get_public_plan_comparison(self) -> dict[str, Any]:
        response = self._transport.request("GET", "/plans/public/comparison", authenticated=False)
        data = response.json().get("data")
        return data if isinstance(data, dict) else {}

    def comparison(self) -> dict[str, Any]:
        return self.get_public_plan_comparison()


class Insights(_Resource):
    """Métricas agregadas de la cuenta autenticada."""

    def get_overview(self) -> dict[str, Any]:
        response = self._transport.request("GET", "/insights/overview")
        return _data_attributes(response)

    def overview(self) -> dict[str, Any]:
        return self.get_overview()

    def get_trends(self, *, range: str | None = None, metric: str | None = None) -> dict[str, Any]:
        response = self._transport.request(
            "GET", "/insights/trends", query=_clean({"range": range, "metric": metric})
        )
        return _data_attributes(response)

    def trends(self, **kwargs: Any) -> dict[str, Any]:
        return self.get_trends(**kwargs)

    def get_top_banks(
        self, *, metric: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        response = self._transport.request(
            "GET", "/insights/top-banks", query=_clean({"metric": metric, "limit": limit})
        )
        return _data_attributes(response)

    def top_banks(self, **kwargs: Any) -> dict[str, Any]:
        return self.get_top_banks(**kwargs)

    def get_top_beneficiaries(self, *, limit: int | None = None) -> dict[str, Any]:
        response = self._transport.request(
            "GET", "/insights/top-beneficiaries", query=_clean({"limit": limit})
        )
        return _data_attributes(response)

    def top_beneficiaries(self, **kwargs: Any) -> dict[str, Any]:
        return self.get_top_beneficiaries(**kwargs)


_FINANCE_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "html": "text/html",
    "zip": "application/zip",
}


def _finance_file(
    transport: Transport,
    path: str,
    query: Mapping[str, Any],
    format: str,
    fallback: str,
    *,
    include_format: bool = True,
) -> Document:
    accept = _FINANCE_CONTENT_TYPES.get(format, "application/octet-stream")
    request_query = {**query, "format": format} if include_format else dict(query)
    response = transport.request(
        "GET", path, query=_clean(request_query), accept=accept + ", application/json"
    )
    return _document(response, fallback + "." + format, accept)


class Finance(_Resource):
    """Resúmenes y descargas financieras."""

    def get_summary(self, *, month: str, user_id: str | None = None) -> dict[str, Any]:
        response = self._transport.request(
            "GET", "/finance/summary", query={"month": month, "user_id": user_id}
        )
        return _data_attributes(response)

    def summary(self, **kwargs: Any) -> dict[str, Any]:
        return self.get_summary(**kwargs)

    def get_statement(
        self, *, month: str, format: str = "pdf", user_id: str | None = None
    ) -> Document:
        return _finance_file(
            self._transport,
            "/finance/statement",
            {"month": month, "user_id": user_id},
            format,
            "estado-de-cuenta",
        )

    def statement(self, **kwargs: Any) -> Document:
        return self.get_statement(**kwargs)

    def _preview(
        self,
        path: str,
        *,
        month: str,
        format: str,
        user_id: str | None,
        limit: int | None,
        fallback: str,
        decimal: str | None = None,
    ) -> dict[str, Any] | Document:
        query = {"month": month, "user_id": user_id, "limit": limit}
        if decimal is not None:
            query["decimal"] = decimal
        if format != "preview":
            return _finance_file(self._transport, path, query, format, fallback)
        response = self._transport.request("GET", path, query=_clean({**query, "format": format}))
        return _data_attributes(response)

    def get_monthly(
        self,
        *,
        month: str,
        format: str = "csv",
        user_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any] | Document:
        return self._preview(
            "/finance/monthly",
            month=month,
            format=format,
            user_id=user_id,
            limit=limit,
            fallback="finanzas-mensuales",
        )

    def monthly(self, **kwargs: Any) -> dict[str, Any] | Document:
        return self.get_monthly(**kwargs)

    def get_counterparties(
        self,
        *,
        month: str,
        format: str = "csv",
        user_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any] | Document:
        return self._preview(
            "/finance/counterparties",
            month=month,
            format=format,
            user_id=user_id,
            limit=limit,
            fallback="contrapartes",
        )

    def counterparties(self, **kwargs: Any) -> dict[str, Any] | Document:
        return self.get_counterparties(**kwargs)

    def get_by_bank(
        self,
        *,
        month: str,
        format: str = "csv",
        user_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any] | Document:
        return self._preview(
            "/finance/by-bank",
            month=month,
            format=format,
            user_id=user_id,
            limit=limit,
            fallback="finanzas-por-banco",
        )

    def by_bank(self, **kwargs: Any) -> dict[str, Any] | Document:
        return self.get_by_bank(**kwargs)

    def get_accounting(
        self,
        *,
        month: str,
        decimal: str | None = None,
        format: str = "csv",
        user_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any] | Document:
        return self._preview(
            "/finance/accounting",
            month=month,
            format=format,
            user_id=user_id,
            limit=limit,
            fallback="contabilidad",
            decimal=decimal,
        )

    def accounting(self, **kwargs: Any) -> dict[str, Any] | Document:
        return self.get_accounting(**kwargs)

    def get_ceps(self, *, from_: str, to: str, user_id: str | None = None) -> Document:
        return _finance_file(
            self._transport,
            "/finance/ceps",
            {"from": from_, "to": to, "user_id": user_id},
            "zip",
            "ceps",
            include_format=False,
        )

    def ceps(self, **kwargs: Any) -> Document:
        return self.get_ceps(**kwargs)


class Billing(_Resource):
    """Suscripción activa de la cuenta autenticada."""

    def get_subscription(self) -> dict[str, Any]:
        response = self._transport.request("GET", "/billing/subscription")
        return _data_attributes(response)

    def subscription(self) -> dict[str, Any]:
        return self.get_subscription()


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


def _import_file(file: bytes | str | Path, filename: str | None) -> tuple[bytes, str, str]:
    """Devuelve el contenido de un archivo de importación, su nombre y su tipo."""
    if isinstance(file, bytes):
        contenido = file
        nombre = filename or "beneficiarios.csv"
    else:
        ruta = Path(file)
        if not ruta.is_file():
            raise ConfigurationError("No existe el archivo: " + str(file))
        contenido = ruta.read_bytes()
        nombre = filename or ruta.name
    return contenido, nombre, _content_type_for(nombre)


def _content_type_for(filename: str) -> str:
    """El content-type de un archivo por su extensión, o genérico si no casa."""
    return _IMPORT_CONTENT_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


def _data_attributes(response: Response) -> dict[str, Any]:
    """El objeto `data.attributes` de una respuesta, o vacío si no está."""
    data = response.json().get("data")
    if not isinstance(data, dict):
        return {}
    attributes = data.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


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


__all__ = [
    "CEP_FORMATS",
    "EXPORT_FORMATS",
    "TEMPLATE_FORMATS",
    "Account",
    "Beneficiaries",
    "Billing",
    "Catalog",
    "Dashboard",
    "Finance",
    "Insights",
    "Plans",
    "Usage",
    "Validations",
    "Webhooks",
]
