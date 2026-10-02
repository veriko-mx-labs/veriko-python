"""Tipos del dominio: lo que la API devuelve, con nombres de la API.

Los campos conservan el nombre que viaja en el JSON (`clave_rastreo`,
`cuenta_beneficiaria`, `banxico_status`).

Cada modelo guarda el documento original en `raw`. Un campo que la API añada
después queda accesible ahí sin esperar a una versión nueva del SDK.
"""

from __future__ import annotations

from collections.abc import Iterable
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

# Estados finales de una importación de beneficiarios.
IMPORT_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


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
    max_retries: int | None = None
    interval_seconds: int | None = None
    outcomes: list[str] | None = None
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
            max_retries=data.get("max_retries"),
            interval_seconds=data.get("interval_seconds"),
            outcomes=list(data["outcomes"]) if isinstance(data.get("outcomes"), list) else None,
            raw=data,
        )


@dataclass(frozen=True)
class DuplicateOf:
    """La validación previa que ya había confirmado esta misma transferencia.

    Sólo aparece cuando existe una validación `valid`, no retirada, de la misma
    cuenta y con la misma clave de rastreo. No cambia el veredicto: la
    validación se consulta y se cobra igual.
    """

    id: str
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DuplicateOf:
        return cls(id=str(data.get("id") or ""), created_at=data.get("created_at"))


@dataclass(frozen=True)
class AccountConflict:
    """El choque entre la cuenta enviada y la que muestra la imagen del comprobante.

    Sólo aparece en validaciones por OCR. Prevalece la cuenta enviada, que es la
    que se consulta en Banxico, y el veredicto no cambia. Cada valor trae los
    últimos 4 dígitos de su cuenta.
    """

    sent_last4: str
    read_last4: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccountConflict:
        return cls(
            sent_last4=str(data.get("sent_last4") or ""),
            read_last4=str(data.get("read_last4") or ""),
        )


@dataclass(frozen=True)
class CandidateMatch:
    """Cuál de las `cuentas_candidatas` enviadas coincidió con la transferencia.

    `index` es la posición, desde 0, de la cuenta ganadora en la lista que se
    envió, y `account_last4` son sus últimos 4 dígitos. La cuenta completa va en
    `normalized_data.cuenta_beneficiaria` de la validación. No cambia el veredicto.
    """

    index: int
    account_last4: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateMatch:
        return cls(
            index=int(data.get("index") or 0),
            account_last4=str(data.get("account_last4") or ""),
        )


@dataclass(frozen=True)
class PaymentStatus:
    """El estado oficial del pago, tal como lo dio Banxico la última vez que se le preguntó.

    `code` vale `liquidado`, `en_proceso`, `cancelado`, `rechazado`,
    `en_proceso_devolucion`, `devuelto` o `desconocido`. Con `devuelto` o
    `en_proceso_devolucion` la validación pasa a `returned`. `checked_at` es el
    instante de la consulta, en ISO 8601 UTC.
    """

    code: str
    label: str | None = None
    settled: bool | None = None
    reversed: bool | None = None
    checked_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaymentStatus:
        return cls(
            code=str(data.get("code") or ""),
            label=data.get("label"),
            settled=data.get("settled"),
            reversed=data.get("reversed"),
            checked_at=data.get("checked_at"),
            raw=data,
        )


@dataclass(frozen=True)
class Validation:
    """Una validación SPEI: el veredicto y todo lo que lo acompaña.

    `client_ref` devuelve la referencia propia que se envió al validar, cuando se
    envió. `duplicate_of`, `account_conflict` y `candidate_match` son `None` salvo
    que la API los informe. `image_retained` indica si la plataforma conserva el
    archivo del comprobante, y `purged_at` marca una validación borrada
    definitivamente, que queda como una lápida.

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
    client_ref: str | None = None
    duplicate_of: DuplicateOf | None = None
    account_conflict: AccountConflict | None = None
    candidate_match: CandidateMatch | None = None
    image_retained: bool | None = None
    purged_at: str | None = None
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

    @property
    def is_purged(self) -> bool:
        """`True` cuando la validación se borró definitivamente y quedó como lápida."""
        return self.purged_at is not None

    @property
    def payment_status(self) -> PaymentStatus | None:
        """El estado del pago que Banxico dio en la última revisión, si existe.

        Se lee de `banxico_result._payment_status`. Su ausencia significa que no se
        pudo saber, nunca que el pago esté liquidado.
        """
        result = self.attributes.get("banxico_result")
        status = result.get("_payment_status") if isinstance(result, dict) else None
        return PaymentStatus.from_dict(status) if isinstance(status, dict) else None

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> Validation:
        data = body.get("data") or {}
        attributes: dict[str, Any] = data.get("attributes") or {}
        retry_state = attributes.get("retry_state")
        duplicate_of = attributes.get("duplicate_of")
        account_conflict = attributes.get("account_conflict")
        candidate_match = attributes.get("candidate_match")
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
            client_ref=attributes.get("client_ref"),
            duplicate_of=(
                DuplicateOf.from_dict(duplicate_of) if isinstance(duplicate_of, dict) else None
            ),
            account_conflict=(
                AccountConflict.from_dict(account_conflict)
                if isinstance(account_conflict, dict)
                else None
            ),
            candidate_match=(
                CandidateMatch.from_dict(candidate_match)
                if isinstance(candidate_match, dict)
                else None
            ),
            image_retained=attributes.get("image_retained"),
            purged_at=attributes.get("purged_at"),
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
class RecheckResult:
    """El resultado de volver a consultar el estado de pago de una validación.

    `validation` es la validación con el estado vigente. `checked_at` es el
    instante de la consulta a Banxico, y vale `None` cuando no se consultó nada
    porque la validación ya estaba en `returned`. `changed` es `True` cuando esta
    consulta la pasó de `valid` a `returned`. `previous_status` es el veredicto de
    Banxico antes de la consulta: `valid` o `returned`.

    https://docs.veriko.mx/es/concepts/cep-concept
    """

    validation: Validation
    checked_at: str | None
    changed: bool
    previous_status: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> RecheckResult:
        meta = body.get("meta")
        recheck = meta.get("recheck") if isinstance(meta, dict) else None
        info: dict[str, Any] = recheck if isinstance(recheck, dict) else {}
        return cls(
            validation=Validation.from_response(body),
            checked_at=info.get("checked_at"),
            changed=bool(info.get("changed", False)),
            previous_status=str(info.get("previous_status") or ""),
            raw=body,
        )


@dataclass(frozen=True)
class PurgePreparation:
    """Lo que borraría el borrado definitivo de una validación, y el token que lo confirma.

    Todavía no ha cambiado nada. `id` es el de la validación. `confirmation_token`
    es de un solo uso, está atado a la cuenta y a esa validación, y caduca en
    `expires_in` segundos. `will_delete` describe lo que se borraría y `will_keep`
    lista los campos que conserva la lápida. `irreversible` vale `True` siempre.

    https://docs.veriko.mx/es/how-to/purge-a-validation
    """

    id: str
    confirmation_token: str
    expires_in: int
    irreversible: bool
    will_delete: dict[str, Any]
    will_keep: list[str]
    cancels_pending_retries: bool
    refunds_quota: bool
    same_image_validation_ids: list[str]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> PurgePreparation:
        data = body.get("data") or {}
        attributes: dict[str, Any] = data.get("attributes") or {}
        return cls(
            id=str(data.get("id") or ""),
            confirmation_token=str(attributes.get("confirmation_token") or ""),
            expires_in=int(attributes.get("expires_in") or 0),
            irreversible=bool(attributes.get("irreversible", True)),
            will_delete=attributes.get("will_delete") or {},
            will_keep=list(attributes.get("will_keep") or []),
            cancels_pending_retries=bool(attributes.get("cancels_pending_retries", False)),
            refunds_quota=bool(attributes.get("refunds_quota", False)),
            same_image_validation_ids=list(attributes.get("same_image_validation_ids") or []),
            raw=body,
        )


@dataclass(frozen=True)
class PurgeResult:
    """El resultado del borrado definitivo de una validación.

    `id` es el de la validación, que queda como una lápida. `purged_at` es el
    instante del borrado, en ISO 8601 UTC. `file_removal` vale `complete` cuando
    los archivos ya no existen, y `pending` cuando alguno no se pudo borrar en ese
    momento y el barrido diario lo termina. `deleted` cuenta lo que se borró.

    https://docs.veriko.mx/es/how-to/purge-a-validation
    """

    id: str
    purged_at: str
    file_removal: str
    deleted: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> PurgeResult:
        data = body.get("data") or {}
        attributes: dict[str, Any] = data.get("attributes") or {}
        return cls(
            id=str(data.get("id") or ""),
            purged_at=str(attributes.get("purged_at") or ""),
            file_removal=str(attributes.get("file_removal") or ""),
            deleted=attributes.get("deleted") or {},
            raw=body,
        )


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
class BanxicoConfirmed:
    """Lo que Banxico confirmó de la operación, en una entrega de webhook.

    Sólo viaja cuando Banxico confirmó el pago: `banxico_status` es `valid`, o
    `returned` con el comprobante ya descargado. Compararlo contra el pedido
    antes de darlo por pagado evita el caso donde una imagen de comprobante
    muestra un monto distinto del que Banxico confirmó.

    `beneficiaryAccount` llega enmascarada, con sólo los últimos 4 dígitos.

    https://docs.veriko.mx/es/concepts/webhooks-architecture
    """

    amount: float | None = None
    operationDate: str | None = None
    processingTime: str | None = None
    trackingKey: str | None = None
    senderBank: str | None = None
    receiverBank: str | None = None
    beneficiaryAccount: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BanxicoConfirmed:
        return cls(
            amount=data.get("amount"),
            operationDate=data.get("operationDate"),
            processingTime=data.get("processingTime"),
            trackingKey=data.get("trackingKey"),
            senderBank=data.get("senderBank"),
            receiverBank=data.get("receiverBank"),
            beneficiaryAccount=data.get("beneficiaryAccount"),
            raw=data,
        )


@dataclass(frozen=True)
class WebhookEvent:
    """Una entrega de webhook ya verificada.

    `payment_status` sólo viaja en `validation.returned`: es el estado del pago que
    delató la devolución. En el resto de los eventos vale `None`.

    https://docs.veriko.mx/es/concepts/webhooks-architecture
    """

    event: str
    timestamp: str | None
    validation: Validation | None
    banxico_confirmed: BanxicoConfirmed | None = None
    payment_status: PaymentStatus | None = None
    data: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ValidationSummary:
    """Una validación en un listado: los campos que la API devuelve en colección.

    Es más estrecha que `Validation`: un listado no trae los datos enviados, el
    resultado de Banxico ni los enlaces al comprobante. Por eso no tiene
    `has_cep`. `Veriko.validations.get(id)` devuelve la completa.
    """

    id: str
    status: str
    banxico_status: str | None = None
    validation_type: str | None = None
    bank_name: str | None = None
    beneficiary_label: str | None = None
    amount: float | None = None
    tracking_key: str | None = None
    referencia_numerica: str | None = None
    beneficiary_account: str | None = None
    is_playground: bool = False
    created_at: str | None = None
    deleted_at: str | None = None
    retry_state: RetryState | None = None
    client_ref: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> ValidationSummary:
        attributes: dict[str, Any] = item.get("attributes") or {}
        retry_state = attributes.get("retry_state")
        return cls(
            id=str(item.get("id") or ""),
            status=str(attributes.get("status") or ""),
            banxico_status=attributes.get("banxico_status"),
            validation_type=attributes.get("validation_type"),
            bank_name=attributes.get("bank_name"),
            beneficiary_label=attributes.get("beneficiary_label"),
            amount=attributes.get("amount"),
            tracking_key=attributes.get("tracking_key"),
            referencia_numerica=attributes.get("referencia_numerica"),
            beneficiary_account=attributes.get("beneficiary_account"),
            is_playground=bool(attributes.get("is_playground", False)),
            created_at=attributes.get("created_at"),
            deleted_at=attributes.get("deleted_at"),
            retry_state=RetryState.from_dict(retry_state) if retry_state is not None else None,
            client_ref=attributes.get("client_ref"),
            attributes=attributes,
            raw=item,
        )


@dataclass(frozen=True)
class RetryAttempt:
    """Un intento del ciclo de reintentos automáticos de una validación."""

    attempt_number: int
    dispatched_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    prev_banxico_status: str | None = None
    new_banxico_status: str | None = None
    prev_status: str | None = None
    new_status: str | None = None
    processing_time_ms: int | None = None
    error_code: str | None = None
    proxy_pool_member: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> RetryAttempt:
        return cls(
            attempt_number=int(item.get("attempt_number") or 0),
            dispatched_at=item.get("dispatched_at"),
            started_at=item.get("started_at"),
            finished_at=item.get("finished_at"),
            prev_banxico_status=item.get("prev_banxico_status"),
            new_banxico_status=item.get("new_banxico_status"),
            prev_status=item.get("prev_status"),
            new_status=item.get("new_status"),
            processing_time_ms=item.get("processing_time_ms"),
            error_code=item.get("error_code"),
            proxy_pool_member=item.get("proxy_pool_member"),
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
    description: str | None = None
    status: str | None = None
    secret: str | None = None
    secret_hint: str | None = None
    consecutive_failures: int = 0
    last_delivery_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
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
            description=attributes.get("description"),
            status=attributes.get("status"),
            secret=attributes.get("secret"),
            secret_hint=attributes.get("secret_hint"),
            consecutive_failures=int(attributes.get("consecutive_failures") or 0),
            last_delivery_at=attributes.get("last_delivery_at"),
            created_at=attributes.get("created_at"),
            updated_at=attributes.get("updated_at"),
            attributes=attributes,
            raw=item,
        )


@dataclass(frozen=True)
class WebhookDelivery:
    """Un intento de entrega de un webhook, con lo que respondió el receptor."""

    id: str
    endpoint_id: str | None = None
    endpoint_url: str | None = None
    event_type: str | None = None
    validation_id: str | None = None
    status: str | None = None
    attempt: int | None = None
    response_status: int | None = None
    response_body: str | None = None
    response_time_ms: int | None = None
    next_retry_at: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> WebhookDelivery:
        attributes: dict[str, Any] = item.get("attributes") or {}
        return cls(
            id=str(item.get("id") or ""),
            endpoint_id=attributes.get("endpoint_id"),
            endpoint_url=attributes.get("endpoint_url"),
            event_type=attributes.get("event_type"),
            validation_id=attributes.get("validation_id"),
            status=attributes.get("status"),
            attempt=attributes.get("attempt"),
            response_status=attributes.get("response_status"),
            response_body=attributes.get("response_body"),
            response_time_ms=attributes.get("response_time_ms"),
            next_retry_at=attributes.get("next_retry_at"),
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
    aliases: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Bank:
        attributes: dict[str, Any] = item.get("attributes") or {}
        aliases = attributes.get("aliases")
        return cls(
            code=str(attributes.get("code") or item.get("id") or "") or None,
            name=attributes.get("name"),
            aliases=list(aliases) if isinstance(aliases, list) else [],
            raw=item,
        )


class BankList(list[Bank]):
    """El catálogo de bancos, con el `ETag` de la respuesta.

    Es una lista de `Bank`. `etag` se pasa como `if_none_match` en la lectura
    siguiente, y vale `None` cuando la API no lo manda.
    """

    def __init__(self, banks: Iterable[Bank] = (), *, etag: str | None = None) -> None:
        super().__init__(banks)
        self.etag = etag


@dataclass(frozen=True)
class Beneficiary:
    """Una cuenta beneficiaria guardada: CLABE, tarjeta o celular DiMo.

    https://docs.veriko.mx/es/concepts/beneficiaries
    """

    id: str
    account_number: str | None = None
    account_type: str | None = None
    bank_code: str | None = None
    bank_name: str | None = None
    label: str | None = None
    status: str | None = None
    created_at: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_archived(self) -> bool:
        """`True` cuando el beneficiario fue archivado y no figura en la lista activa."""
        return self.status == "inactive"

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Beneficiary:
        attributes: dict[str, Any] = item.get("attributes") or {}
        return cls(
            id=str(item.get("id") or ""),
            account_number=attributes.get("account_number"),
            account_type=attributes.get("account_type"),
            bank_code=attributes.get("bank_code"),
            bank_name=attributes.get("bank_name"),
            label=attributes.get("label"),
            status=attributes.get("status"),
            created_at=attributes.get("created_at"),
            attributes=attributes,
            raw=item,
        )

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> Beneficiary:
        data = body.get("data")
        return cls.from_item(data if isinstance(data, dict) else {})


@dataclass(frozen=True)
class BeneficiaryLookup:
    """La cuenta resuelta por `beneficiaries.lookup()` dentro de la lista propia."""

    account_number: str | None = None
    account_type: str | None = None
    bank_code: str | None = None
    bank_name: str | None = None
    label: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> BeneficiaryLookup:
        data = body.get("data") or {}
        attributes: dict[str, Any] = data.get("attributes") or {}
        return cls(
            account_number=attributes.get("account_number"),
            account_type=attributes.get("account_type"),
            bank_code=attributes.get("bank_code"),
            bank_name=attributes.get("bank_name"),
            label=attributes.get("label"),
            attributes=attributes,
            raw=body,
        )


@dataclass(frozen=True)
class BeneficiaryImportJob:
    """Un trabajo de importación masiva de beneficiarios y su avance.

    Ciclo de vida: `pending` → `parsing` → `preview_ready` → `committing` →
    `completed` (o `failed` / `cancelled`).

    https://docs.veriko.mx/es/concepts/bulk-imports
    """

    id: str
    status: str | None = None
    file_format: str | None = None
    parse_mode: str | None = None
    total_rows: int | None = None
    valid_count: int | None = None
    correctable_count: int | None = None
    fatal_count: int | None = None
    duplicate_count: int | None = None
    committed_count: int | None = None
    skipped_count: int | None = None
    llm_invoked: bool = False
    error_code: str | None = None
    error_summary: str | None = None
    created_at: str | None = None
    parsed_at: str | None = None
    committed_at: str | None = None
    completed_at: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_terminal(self) -> bool:
        """`True` cuando el trabajo terminó, con éxito o no."""
        return self.status in IMPORT_TERMINAL_STATUSES

    @property
    def is_preview_ready(self) -> bool:
        """`True` cuando las filas ya están listas para revisar y corregir."""
        return self.status == "preview_ready"

    @property
    def is_settled(self) -> bool:
        """`True` cuando el trabajo ya no avanza por sí solo.

        Cubre `preview_ready`, donde el avance se detiene a esperar la revisión
        del usuario, y los estados finales. Es lo que espera `import_wait()`.
        """
        return self.is_preview_ready or self.is_terminal

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> BeneficiaryImportJob:
        data = body.get("data") or {}
        attributes: dict[str, Any] = data.get("attributes") or {}
        return cls(
            id=str(data.get("id") or ""),
            status=attributes.get("status"),
            file_format=attributes.get("file_format"),
            parse_mode=attributes.get("parse_mode"),
            total_rows=attributes.get("total_rows"),
            valid_count=attributes.get("valid_count"),
            correctable_count=attributes.get("correctable_count"),
            fatal_count=attributes.get("fatal_count"),
            duplicate_count=attributes.get("duplicate_count"),
            committed_count=attributes.get("committed_count"),
            skipped_count=attributes.get("skipped_count"),
            llm_invoked=bool(attributes.get("llm_invoked", False)),
            error_code=attributes.get("error_code"),
            error_summary=attributes.get("error_summary"),
            created_at=attributes.get("created_at"),
            parsed_at=attributes.get("parsed_at"),
            committed_at=attributes.get("committed_at"),
            completed_at=attributes.get("completed_at"),
            attributes=attributes,
            raw=body,
        )


@dataclass(frozen=True)
class BeneficiaryImportRow:
    """Una fila extraída de un archivo de importación, con su grupo y sus correcciones."""

    id: str
    row_index: int | None = None
    status: str | None = None
    parsed_account: str | None = None
    parsed_account_type: str | None = None
    parsed_bank_code: str | None = None
    parsed_bank_name: str | None = None
    parsed_label: str | None = None
    error_codes: list[str] = field(default_factory=list)
    corrections_applied: dict[str, Any] = field(default_factory=dict)
    user_overrides: dict[str, Any] = field(default_factory=dict)
    raw_preview: dict[str, Any] = field(default_factory=dict)
    created_beneficiary_id: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> BeneficiaryImportRow:
        attributes: dict[str, Any] = item.get("attributes") or {}
        errores = attributes.get("error_codes")
        correcciones = attributes.get("corrections_applied")
        sobreescrituras = attributes.get("user_overrides")
        previa = attributes.get("raw_preview")
        return cls(
            id=str(item.get("id") or ""),
            row_index=attributes.get("row_index"),
            status=attributes.get("status"),
            parsed_account=attributes.get("parsed_account"),
            parsed_account_type=attributes.get("parsed_account_type"),
            parsed_bank_code=attributes.get("parsed_bank_code"),
            parsed_bank_name=attributes.get("parsed_bank_name"),
            parsed_label=attributes.get("parsed_label"),
            error_codes=list(errores) if isinstance(errores, list) else [],
            corrections_applied=correcciones if isinstance(correcciones, dict) else {},
            user_overrides=sobreescrituras if isinstance(sobreescrituras, dict) else {},
            raw_preview=previa if isinstance(previa, dict) else {},
            created_beneficiary_id=attributes.get("created_beneficiary_id"),
            attributes=attributes,
            raw=item,
        )

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> BeneficiaryImportRow:
        data = body.get("data")
        return cls.from_item(data if isinstance(data, dict) else {})


@dataclass(frozen=True)
class UsageSummary:
    """La cuota de validaciones del plan en curso, con el nivel de aviso.

    `tone` resume el consumo: `ok` por debajo del 70 %, `warn` entre el 70 % y el
    89 %, y `danger` a partir del 90 % o con la cuota agotada.

    `quota_kind` dice de dónde sale la cuota: `cycle` para el ciclo de la
    suscripción y `trial` para las validaciones de prueba de una cuenta que aún no
    activa el plan gratuito. Con `trial`, `renews` es `False`: la cuota no se
    repone y `resets_at` marca el fin del periodo técnico, no más unidades.
    """

    plan_slug: str | None = None
    plan_name: str | None = None
    limit: int | None = None
    used: int | None = None
    remaining: int | None = None
    used_percent: float | None = None
    tone: str | None = None
    resets_at: str | None = None
    next_reset_at: str | None = None
    quota_kind: str | None = None
    renews: bool | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> UsageSummary:
        data = body.get("data") or {}
        attributes: dict[str, Any] = data.get("attributes") or {}
        return cls(
            plan_slug=attributes.get("plan_slug"),
            plan_name=attributes.get("plan_name"),
            limit=attributes.get("limit"),
            used=attributes.get("used"),
            remaining=attributes.get("remaining"),
            used_percent=attributes.get("used_percent"),
            tone=attributes.get("tone"),
            resets_at=attributes.get("resets_at"),
            next_reset_at=attributes.get("next_reset_at"),
            quota_kind=attributes.get("quota_kind"),
            renews=attributes.get("renews"),
            attributes=attributes,
            raw=body,
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
    "IMPORT_TERMINAL_STATUSES",
    "RETRYABLE_OUTCOMES",
    "TERMINAL_STATUSES",
    "Bank",
    "BankList",
    "BanxicoConfirmed",
    "Beneficiary",
    "BeneficiaryImportJob",
    "BeneficiaryImportRow",
    "BeneficiaryLookup",
    "CepDocument",
    "Document",
    "QueuedValidation",
    "RetryAttempt",
    "RetryPolicy",
    "RetryState",
    "UsageSummary",
    "Validation",
    "ValidationStatus",
    "ValidationSummary",
    "WebhookDelivery",
    "WebhookEndpoint",
    "WebhookEvent",
    "WebhookTestResult",
]
