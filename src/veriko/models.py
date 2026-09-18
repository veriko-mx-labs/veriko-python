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
class AccountValidation:
    """Resultado de `beneficiaries.validate_account()`: la estructura de una cuenta.

    Un número mal formado no es un error de la petición, sino un resultado que se
    lee en `checksum_valid` o en `account_type`.
    """

    input: str | None = None
    length: int | None = None
    is_numeric: bool = False
    account_type: str | None = None
    is_complete: bool = False
    checksum_valid: bool = False
    computed_control_digit: str | None = None
    auto_completed: str | None = None
    bank: dict[str, Any] = field(default_factory=dict)
    card: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> AccountValidation:
        data = body.get("data") or {}
        attributes: dict[str, Any] = data.get("attributes") or {}
        bank = attributes.get("bank")
        card = attributes.get("card")
        return cls(
            input=attributes.get("input"),
            length=attributes.get("length"),
            is_numeric=bool(attributes.get("is_numeric", False)),
            account_type=attributes.get("account_type"),
            is_complete=bool(attributes.get("is_complete", False)),
            checksum_valid=bool(attributes.get("checksum_valid", False)),
            computed_control_digit=attributes.get("computed_control_digit"),
            auto_completed=attributes.get("auto_completed"),
            bank=bank if isinstance(bank, dict) else {},
            card=card if isinstance(card, dict) else {},
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
    "AccountValidation",
    "Bank",
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
