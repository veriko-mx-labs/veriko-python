"""Tipos del dominio: lo que la API devuelve, con nombres de la API.

Los campos conservan el nombre que viaja en el JSON (`clave_rastreo`,
`cuenta_beneficiaria`, `banxico_status`).

Cada modelo guarda el documento original en `raw`. Un campo que la API añada
después queda accesible ahí sin esperar a una versión nueva del SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Estados del ciclo de vida de una validación. Los tres primeros no son
# terminales; el resto sí. https://docs.veriko.mx/es/concepts/validation-flow
ValidationStatus = str

# Veredictos que admiten reintento automático.
RETRYABLE_OUTCOMES = ("not_found", "cep_unavailable", "error")

# Estados en los que una validación ya no va a cambiar por sí sola.
TERMINAL_STATUSES = (
    "valid",
    "not_found",
    "cep_unavailable",
    "invalid",
    "returned",
    "failed",
    "error",
)


@dataclass(frozen=True)
class RetryPolicy:
    """Política de reintentos automáticos de una validación.

    `interval_seconds` admite de 300 a 86 400 (5 minutos a 24 horas) y
    `max_retries` tiene un techo que depende del plan.

    https://docs.veriko.mx/es/concepts/retry-policy
    """

    enabled: bool = True
    max_retries: int | None = None
    interval_seconds: int | None = None
    outcomes: list[str] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"enabled": self.enabled}
        if self.max_retries is not None:
            payload["max_retries"] = self.max_retries
        if self.interval_seconds is not None:
            payload["interval_seconds"] = self.interval_seconds
        if self.outcomes is not None:
            payload["outcomes"] = list(self.outcomes)
        return payload


@dataclass(frozen=True)
class RetryState:
    """Estado del ciclo de reintentos, tal como lo informa la API."""

    enabled: bool = False
    attempts_completed: int = 0
    next_attempt_at: str | None = None
    resolved_at: str | None = None
    exhausted_at: str | None = None
    cancelled_at: str | None = None
    terminal_state: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RetryState:
        data = data or {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            attempts_completed=int(data.get("attempts_completed") or 0),
            next_attempt_at=data.get("next_attempt_at"),
            resolved_at=data.get("resolved_at"),
            exhausted_at=data.get("exhausted_at"),
            cancelled_at=data.get("cancelled_at"),
            terminal_state=data.get("terminal_state"),
            raw=data,
        )


@dataclass(frozen=True)
class Validation:
    """Una validación SPEI: el veredicto y todo lo que lo acompaña.

    https://docs.veriko.mx/es/concepts/validation-flow
    """

    id: str
    status: str
    banxico_status: str | None = None
    validation_type: str | None = None
    is_playground: bool = False
    processing_time_ms: int | None = None
    created_at: str | None = None
    completed_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    request_data: dict[str, Any] = field(default_factory=dict)
    retry_state: RetryState | None = None
    links: dict[str, Any] = field(default_factory=dict)
    etag: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    meta: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_terminal(self) -> bool:
        """`True` cuando el estado ya no va a cambiar sin una acción externa."""
        return self.status in TERMINAL_STATUSES

    @property
    def is_settled(self) -> bool:
        """`True` cuando el veredicto ya no va a cambiar, ni por reintentos.

        `is_terminal` mira sólo el estado. Un `not_found` es terminal para la
        consulta que lo produjo, pero si la validación tiene un ciclo de
        reintentos en marcha la API va a volver a preguntar a Banxico, y el
        veredicto puede cambiar. Ésta es la condición que hay que esperar.
        """
        if self.status not in TERMINAL_STATUSES:
            return False
        estado = self.retry_state
        ciclo_vivo = (
            estado is not None and estado.enabled and estado.terminal_state in (None, "pending")
        )
        return not ciclo_vivo

    @property
    def has_cep(self) -> bool:
        """`True` cuando hay comprobante que descargar.

        Se deriva de `links.cep_xml`, que es lo que publica el servidor. Un
        `returned` nacido de `cep_unavailable` no lo trae.
        """
        return bool(self.links.get("cep_xml"))

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> Validation:
        data = body.get("data") or {}
        attributes: dict[str, Any] = data.get("attributes") or {}
        retry_state = attributes.get("retry_state")
        return cls(
            id=str(data.get("id") or ""),
            status=str(attributes.get("status") or ""),
            banxico_status=attributes.get("banxico_status"),
            validation_type=attributes.get("validation_type"),
            is_playground=bool(attributes.get("is_playground", False)),
            processing_time_ms=attributes.get("processing_time_ms"),
            created_at=attributes.get("created_at"),
            completed_at=attributes.get("completed_at"),
            error_code=attributes.get("error_code"),
            error_message=attributes.get("error_message"),
            request_data=attributes.get("request_data") or {},
            retry_state=RetryState.from_dict(retry_state) if retry_state is not None else None,
            links=data.get("links") or {},
            attributes=attributes,
            meta=body.get("meta") or {},
            raw=body,
        )


@dataclass(frozen=True)
class QueuedValidation:
    """Respuesta de una validación asíncrona (`async_=True`): sólo el acuse.

    El veredicto se recoge sondeando `get_validation(id)` hasta un estado
    terminal, no antes de `next_poll_after_seconds`.

    https://docs.veriko.mx/es/concepts/async-validations
    """

    id: str
    status: str
    etag: str | None = None
    location: str | None = None
    next_poll_after_seconds: int | None = None
    meta: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class CepDocument:
    """El CEP de Banxico descargado: el archivo, no un enlace a él."""

    validation_id: str
    content: bytes
    content_type: str
    format: str
    filename: str

    def write_to(self, path: str) -> str:
        """Guarda el comprobante en `path` y devuelve la ruta escrita."""
        with open(path, "wb") as handle:
            handle.write(self.content)
        return path


@dataclass(frozen=True)
class WebhookEvent:
    """Una entrega de webhook ya verificada.

    https://docs.veriko.mx/es/concepts/webhooks-architecture
    """

    event: str
    timestamp: str | None
    validation: Validation | None
    data: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ValidationSummary:
    """Una validación en un listado: los campos que la API devuelve en colección.

    Es más estrecha que `Validation`: un listado no trae ni `request_data` ni el
    resultado de Banxico. `Veriko.validations.get(id)` devuelve la completa.
    """

    id: str
    status: str
    banxico_status: str | None = None
    validation_type: str | None = None
    created_at: str | None = None
    completed_at: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    links: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def has_cep(self) -> bool:
        return bool(self.links.get("cep_xml"))

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> ValidationSummary:
        attributes: dict[str, Any] = item.get("attributes") or {}
        return cls(
            id=str(item.get("id") or ""),
            status=str(attributes.get("status") or ""),
            banxico_status=attributes.get("banxico_status"),
            validation_type=attributes.get("validation_type"),
            created_at=attributes.get("created_at"),
            completed_at=attributes.get("completed_at"),
            attributes=attributes,
            links=item.get("links") or {},
            raw=item,
        )


@dataclass(frozen=True)
class RetryAttempt:
    """Un intento del ciclo de reintentos automáticos de una validación."""

    attempt: int
    status: str | None = None
    attempted_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> RetryAttempt:
        attributes: dict[str, Any] = item.get("attributes") or item
        return cls(
            attempt=int(attributes.get("attempt") or attributes.get("attempt_number") or 0),
            status=attributes.get("status") or attributes.get("outcome"),
            attempted_at=attributes.get("attempted_at") or attributes.get("created_at"),
            raw=item,
        )


@dataclass(frozen=True)
class WebhookEndpoint:
    """Un endpoint de webhook registrado.

    `secret` sólo viene con valor al registrarlo y al rotar el secreto; en los
    listados es `None`, porque la API no lo vuelve a entregar.

    https://docs.veriko.mx/es/concepts/webhooks-architecture
    """

    id: str
    url: str
    events: list[str] = field(default_factory=list)
    status: str | None = None
    secret: str | None = None
    consecutive_failures: int = 0
    created_at: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_auto_disabled(self) -> bool:
        """`True` cuando el sistema lo apagó tras tres fallos consecutivos."""
        return self.status == "auto_disabled"

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> WebhookEndpoint:
        attributes: dict[str, Any] = item.get("attributes") or {}
        eventos = attributes.get("events")
        return cls(
            id=str(item.get("id") or ""),
            url=str(attributes.get("url") or ""),
            events=list(eventos) if isinstance(eventos, list) else [],
            status=attributes.get("status"),
            secret=attributes.get("secret"),
            consecutive_failures=int(attributes.get("consecutive_failures") or 0),
            created_at=attributes.get("created_at"),
            attributes=attributes,
            raw=item,
        )


@dataclass(frozen=True)
class WebhookDelivery:
    """Un intento de entrega de un webhook, con lo que respondió el receptor."""

    id: str
    event_type: str | None = None
    status: str | None = None
    response_status: int | None = None
    response_time_ms: int | None = None
    error_message: str | None = None
    created_at: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> WebhookDelivery:
        attributes: dict[str, Any] = item.get("attributes") or {}
        return cls(
            id=str(item.get("id") or ""),
            event_type=attributes.get("event_type") or attributes.get("event"),
            status=attributes.get("status"),
            response_status=attributes.get("response_status"),
            response_time_ms=attributes.get("response_time_ms"),
            error_message=attributes.get("error_message"),
            created_at=attributes.get("created_at"),
            attributes=attributes,
            raw=item,
        )


@dataclass(frozen=True)
class WebhookTestResult:
    """Resultado del evento de prueba de un endpoint.

    Las entregas de prueba no cuentan para el contador de fallos consecutivos
    que desactiva un endpoint.
    """

    delivered: bool
    http_status: int | None = None
    response_time_ms: int | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> WebhookTestResult:
        data = body.get("data") or {}
        attributes: dict[str, Any] = data.get("attributes") or {}
        return cls(
            delivered=bool(attributes.get("delivered")),
            http_status=attributes.get("http_status"),
            response_time_ms=attributes.get("response_time_ms"),
            error=attributes.get("error"),
            raw=body,
        )


@dataclass(frozen=True)
class Bank:
    """Una institución del catálogo SPEI."""

    code: str | None = None
    name: str | None = None
    short_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Bank:
        attributes: dict[str, Any] = item.get("attributes") or item
        return cls(
            code=str(attributes.get("code") or item.get("id") or "") or None,
            name=attributes.get("name"),
            short_name=attributes.get("short_name") or attributes.get("alias"),
            raw=item,
        )


@dataclass(frozen=True)
class Document:
    """Un archivo que devuelve la API: el CEP, una imagen o una exportación."""

    content: bytes
    content_type: str
    filename: str

    def write_to(self, path: str) -> str:
        """Guarda el archivo en `path` y devuelve la ruta escrita."""
        with open(path, "wb") as handle:
            handle.write(self.content)
        return path

    def __len__(self) -> int:
        return len(self.content)


__all__ = [
    "RETRYABLE_OUTCOMES",
    "TERMINAL_STATUSES",
    "Bank",
    "CepDocument",
    "Document",
    "QueuedValidation",
    "RetryAttempt",
    "RetryPolicy",
    "RetryState",
    "Validation",
    "ValidationStatus",
    "ValidationSummary",
    "WebhookDelivery",
    "WebhookEndpoint",
    "WebhookEvent",
    "WebhookTestResult",
]
