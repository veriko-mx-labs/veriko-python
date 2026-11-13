"""Domain types: what the API returns, under the names of the API.

Fields keep the name that travels in the JSON (`tracking_key`,
`beneficiary_account`, `banxico_status`). A name from an earlier version still reads,
through a fallback that emits a `DeprecationWarning`.

Each model keeps the original document in `raw`. A field the API adds later is
reachable there without waiting for a new SDK version.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from ._base import ResponseCarrier
from ._compat import RESPONSE_ALIASES, warn_deprecated_name


def _as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _as_list(value: Any) -> list[Any] | None:
    return value if isinstance(value, list) else None


def _legacy_key(current: str) -> str:
    """The key an earlier API version used for `current` in responses."""
    return next(old for old, new in RESPONSE_ALIASES.items() if new == current)


def _first(data: dict[str, Any], *names: str) -> Any:
    """The first value present among `names`: the English name, then the deprecated one."""
    for name in names:
        if data.get(name) is not None:
            return data[name]
    return None


# Lifecycle states of a validation. The first three are not terminal; the rest are.
# https://docs.veriko.mx/en/concepts/validation-flow
ValidationStatus = str

# Verdicts that allow an automatic retry.
# States before any verdict: the validation is accepted and still being worked on.
PENDING_STATUSES = ("queued", "processing")

RETRYABLE_OUTCOMES = ("not_found", "cep_unavailable", "error")

# States in which a validation no longer changes on its own.
TERMINAL_STATUSES = (
    "valid",
    "not_found",
    "cep_unavailable",
    "invalid",
    "returned",
    "failed",
    "error",
)

# Event types an endpoint subscribes to (`events` of `webhooks.create()`). A delivery
# can also carry the `test` event, which `webhooks.test()` sends and nobody subscribes to.
WEBHOOK_EVENTS = (
    "validation.completed",
    "validation.failed",
    "validation.error",
    "validation.retry.scheduled",
    "validation.retry.resolved",
    "validation.retry.exhausted",
    "validation.returned",
    "billing.payment_succeeded",
    "billing.payment_failed",
    "billing.trial_will_end",
    "billing.subscription_canceled",
    "billing.invoice_upcoming",
)
WEBHOOK_TEST_EVENT = "test"
WebhookEventName = Literal[
    "validation.completed",
    "validation.failed",
    "validation.error",
    "validation.retry.scheduled",
    "validation.retry.resolved",
    "validation.retry.exhausted",
    "validation.returned",
    "billing.payment_succeeded",
    "billing.payment_failed",
    "billing.trial_will_end",
    "billing.subscription_canceled",
    "billing.invoice_upcoming",
]

# Values of the `type` and `retry_state` filters of the validation listings.
ValidationType = Literal["direct", "ocr"]
RetryStateFilter = Literal["pending", "resolved", "exhausted", "cancelled"]

# Final states of a beneficiary import.
IMPORT_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


@dataclass(frozen=True)
class RetryPolicy(ResponseCarrier):
    """Automatic retry policy of a validation.

    `interval_seconds` accepts 300 to 86,400 (5 minutes to 24 hours) and
    `max_retries` has a ceiling that depends on the plan.

    https://docs.veriko.mx/en/concepts/retry-policy
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
class RetryState(ResponseCarrier):
    """State of the retry cycle, as the API reports it."""

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
class DuplicateOf(ResponseCarrier):
    """The earlier validation that had already confirmed this same transfer.

    It only appears when there is a `valid`, non-removed validation for the same
    account with the same tracking key. It does not change the verdict: the
    validation is queried and charged all the same.
    """

    id: str
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DuplicateOf:
        return cls(id=str(data.get("id") or ""), created_at=data.get("created_at"))


@dataclass(frozen=True)
class AccountConflict(ResponseCarrier):
    """The clash between the account sent and the one the receipt image shows.

    It only appears on OCR validations. The account sent prevails, since it is the
    one queried at Banxico, and the verdict does not change. Each value carries the
    last 4 digits of its account.
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
class CandidateMatch(ResponseCarrier):
    """Which of the `candidate_accounts` sent matched the transfer.

    `index` is the position, from 0, of the winning account in the list that was
    sent, and `account_last4` are its last 4 digits. The full account is in the
    validation's `normalized_data.beneficiary_account`. It does not change the
    verdict.
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
class PaymentStatus(ResponseCarrier):
    """The official payment status, as Banxico gave it the last time it was asked.

    `code` is `liquidado`, `en_proceso`, `cancelado`, `rechazado`,
    `en_proceso_devolucion`, `devuelto` or `desconocido`. With `devuelto` or
    `en_proceso_devolucion` the validation becomes `returned`. `checked_at` is the
    instant of the query, in ISO 8601 UTC.
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
class Validation(ResponseCarrier):
    """A SPEI validation: the verdict and everything that goes with it.

    `client_ref` returns your own reference sent when validating, if one was sent.
    `duplicate_of`, `account_conflict` and `candidate_match` are `None` unless the
    API reports them. `account_cross_checked` is `True` when the account queried at
    Banxico belongs to the client (the one sent, one of the candidates or that of
    an active beneficiary). With `False`, a `valid` confirms the transfer exists,
    but not that it was paid to the expected account: compare the account of the
    Banxico result before releasing the payment. It is `None` when the API does not
    report it. `image_retained` says whether the platform keeps the receipt file,
    and `purged_at` marks a permanently deleted validation, which is left as a
    tombstone.

    https://docs.veriko.mx/en/concepts/validation-flow
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
    account_cross_checked: bool | None = None
    image_retained: bool | None = None
    purged_at: str | None = None
    ocr_result: dict[str, Any] | None = None
    ocr_confidence: float | None = None
    normalized_data: dict[str, Any] | None = None
    normalization_warnings: list[Any] | None = None
    banxico_result: dict[str, Any] | None = None
    enqueued_at: str | None = None
    processing_started_at: str | None = None
    expires_at: str | None = None
    etag_version: str | None = None
    image_path: str | None = None
    is_masked: bool | None = None
    batch_id: int | None = None
    batch_position: int | None = None
    links: dict[str, Any] = field(default_factory=dict)
    etag: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    meta: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_terminal(self) -> bool:
        """`True` when the state no longer changes without an external action."""
        return self.status in TERMINAL_STATUSES

    @property
    def is_settled(self) -> bool:
        """`True` when the verdict no longer changes, not even through retries.

        `is_terminal` only looks at the state. A `not_found` is terminal for the
        query that produced it, but if the validation has a retry cycle under way the
        API will ask Banxico again, and the verdict can change. This is the
        condition to wait for.
        """
        if self.status not in TERMINAL_STATUSES:
            return False
        state = self.retry_state
        live_cycle = (
            state is not None
            and state.enabled
            and self.status in RETRYABLE_OUTCOMES
            and state.terminal_state in (None, "pending")
            and (state.terminal_state == "pending" or state.next_attempt_at is not None)
        )
        return not live_cycle

    @property
    def is_pending(self) -> bool:
        """`True` while the validation is queued or processing, before any verdict."""
        return self.status in PENDING_STATUSES

    @property
    def has_cep(self) -> bool:
        """`True` when there is a receipt to download.

        It is derived from `links.cep_xml`, which is what the server publishes. A
        `returned` born from `cep_unavailable` does not carry it.
        """
        return bool(self.links.get("cep_xml"))

    @property
    def is_purged(self) -> bool:
        """`True` when the validation was permanently deleted and left as a tombstone."""
        return self.purged_at is not None

    @property
    def payment_status(self) -> PaymentStatus | None:
        """The payment status Banxico gave at the last recheck, if there is one.

        It is read from `banxico_result._payment_status`. Its absence means it could
        not be known, never that the payment is settled.
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
        cross_checked = attributes.get("account_cross_checked")
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
            account_cross_checked=(cross_checked if isinstance(cross_checked, bool) else None),
            image_retained=attributes.get("image_retained"),
            purged_at=attributes.get("purged_at"),
            ocr_result=_as_dict(attributes.get("ocr_result")),
            ocr_confidence=attributes.get("ocr_confidence"),
            normalized_data=_as_dict(attributes.get("normalized_data")),
            normalization_warnings=_as_list(attributes.get("normalization_warnings")),
            banxico_result=_as_dict(attributes.get("banxico_result")),
            enqueued_at=attributes.get("enqueued_at"),
            processing_started_at=attributes.get("processing_started_at"),
            expires_at=attributes.get("expires_at"),
            etag_version=attributes.get("etag_version"),
            image_path=attributes.get("image_path"),
            is_masked=attributes.get("is_masked"),
            batch_id=attributes.get("batch_id"),
            batch_position=attributes.get("batch_position"),
            links=data.get("links") or {},
            attributes=attributes,
            meta=body.get("meta") or {},
            raw=body,
        )


@dataclass(frozen=True)
class QueuedValidation(ResponseCarrier):
    """Response of an asynchronous validation (`async_=True`): only the acknowledgement.

    The verdict is collected by polling `get_validation(id)` until a terminal state,
    not before `next_poll_after_seconds`.

    https://docs.veriko.mx/en/concepts/async-validations
    """

    id: str
    status: str
    etag: str | None = None
    location: str | None = None
    next_poll_after_seconds: int | None = None
    enqueued_at: str | None = None
    expires_at: str | None = None
    etag_version: str | None = None
    client_ref: str | None = None
    retry_state: RetryState | None = None
    meta: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class RecheckResult(ResponseCarrier):
    """The result of querying the payment status of a validation again.

    `validation` is the validation with the current state. `checked_at` is the
    instant of the Banxico query, and is `None` when nothing was queried because
    the validation was already `returned`. `changed` is `True` when this query moved
    it from `valid` to `returned`. `previous_status` is the Banxico verdict before
    the query: `valid` or `returned`.

    https://docs.veriko.mx/en/concepts/cep-concept
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
class PurgePreparation(ResponseCarrier):
    """What the permanent deletion of a validation would erase, and the token that confirms it.

    Nothing has changed yet. `id` is the validation's. `confirmation_token` is
    single-use, tied to the account and to that validation, and expires in
    `expires_in` seconds. `will_delete` describes what would be erased and
    `will_keep` lists the fields the tombstone keeps. `irreversible` is always
    `True`.

    https://docs.veriko.mx/en/how-to/purge-a-validation
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
class PurgeResult(ResponseCarrier):
    """The result of the permanent deletion of a validation.

    `id` is the validation's, which is left as a tombstone. `purged_at` is the
    instant of the deletion, in ISO 8601 UTC. `file_removal` is `complete` when the
    files no longer exist, and `pending` when one could not be deleted at that
    moment and the daily sweep finishes it. `deleted` counts what was erased.

    https://docs.veriko.mx/en/how-to/purge-a-validation
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
class CepDocument(ResponseCarrier):
    """The downloaded Banxico CEP: the file, not a link to it."""

    validation_id: str
    content: bytes
    content_type: str
    format: str
    filename: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)

    def write_to(self, path: str) -> str:
        """Saves the receipt at `path` and returns the path written."""
        with open(path, "wb") as handle:
            handle.write(self.content)
        return path

    def iter_bytes(self, chunk_size: int = 65536) -> Iterator[bytes]:
        """The content in chunks of `chunk_size` bytes."""
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]


@dataclass(frozen=True)
class BanxicoConfirmed(ResponseCarrier):
    """What Banxico confirmed about the operation, in a webhook delivery.

    It only travels when Banxico confirmed the payment: `banxico_status` is
    `valid`, or `returned` with the receipt already downloaded. Comparing it with
    the order before treating it as paid avoids the case where a receipt image shows
    an amount different from the one Banxico confirmed.

    `beneficiaryAccount` arrives masked, with only the last 4 digits.

    https://docs.veriko.mx/en/concepts/webhooks-architecture
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
class WebhookEvent(ResponseCarrier):
    """A webhook delivery that has already been verified.

    `payment_status` only travels in `validation.returned`: it is the payment status
    that gave the return away. In the other events it is `None`. `delivery_id` and
    `attempt_timestamp` come from the delivery headers when `parse_webhook()` receives
    them.

    https://docs.veriko.mx/en/concepts/webhooks-architecture
    """

    event: str
    timestamp: str | None
    validation: Validation | None
    banxico_confirmed: BanxicoConfirmed | None = None
    payment_status: PaymentStatus | None = None
    data: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    delivery_id: str | None = None
    attempt_timestamp: int | None = None

    @property
    def is_validation_event(self) -> bool:
        """`True` for the `validation.*` events, whose `validation` is filled in."""
        return self.event.startswith("validation.")

    @property
    def is_billing_event(self) -> bool:
        """`True` for the `billing.*` events, whose payload is in `data`."""
        return self.event.startswith("billing.")

    @property
    def is_test(self) -> bool:
        """`True` for the delivery that `webhooks.test()` sends."""
        return self.event == WEBHOOK_TEST_EVENT


@dataclass(frozen=True)
class ValidationSummary(ResponseCarrier):
    """A validation in a listing: the fields the API returns in a collection.

    It is narrower than `Validation`: a listing does not carry the data sent, the
    Banxico result or the receipt links. That is why it has no `has_cep`.
    `Veriko.validations.get(id)` returns the full one.
    """

    id: str
    status: str
    banxico_status: str | None = None
    validation_type: str | None = None
    bank_name: str | None = None
    beneficiary_label: str | None = None
    amount: float | None = None
    tracking_key: str | None = None
    numeric_reference: str | None = None
    beneficiary_account: str | None = None
    bank_code: str | None = None
    is_playground: bool = False
    created_at: str | None = None
    deleted_at: str | None = None
    purged_at: str | None = None
    retry_state: RetryState | None = None
    client_ref: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def __getattr__(self, name: str) -> Any:
        # Attribute names from earlier versions: read the current one, with a warning.
        current = RESPONSE_ALIASES.get(name)
        if current is None:
            raise AttributeError(name)
        warn_deprecated_name(name, current, stacklevel=2)
        return getattr(self, current)

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
            numeric_reference=_first(
                attributes, "numeric_reference", _legacy_key("numeric_reference")
            ),
            beneficiary_account=attributes.get("beneficiary_account"),
            bank_code=attributes.get("bank_code"),
            is_playground=bool(attributes.get("is_playground", False)),
            created_at=attributes.get("created_at"),
            deleted_at=attributes.get("deleted_at"),
            purged_at=attributes.get("purged_at"),
            retry_state=RetryState.from_dict(retry_state) if retry_state is not None else None,
            client_ref=attributes.get("client_ref"),
            attributes=attributes,
            raw=item,
        )


@dataclass(frozen=True)
class RetryAttempt(ResponseCarrier):
    """An attempt of the automatic retry cycle of a validation."""

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
class WebhookEndpoint(ResponseCarrier):
    """A registered webhook endpoint.

    `secret` only carries a value when it is registered and when the secret is
    rotated; in listings it is `None`, because the API does not deliver it again.

    https://docs.veriko.mx/en/concepts/webhooks-architecture
    """

    id: str
    url: str
    events: list[str] = field(default_factory=list)
    description: str | None = None
    status: str | None = None
    secret: str | None = None
    secret_hint: str | None = None
    previous_secret_expires_at: str | None = None
    consecutive_failures: int = 0
    last_delivery_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_auto_disabled(self) -> bool:
        """`True` when the system turned it off after three consecutive failures."""
        return self.status == "auto_disabled"

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> WebhookEndpoint:
        attributes: dict[str, Any] = item.get("attributes") or {}
        events = attributes.get("events")
        return cls(
            id=str(item.get("id") or ""),
            url=str(attributes.get("url") or ""),
            events=list(events) if isinstance(events, list) else [],
            description=attributes.get("description"),
            status=attributes.get("status"),
            secret=attributes.get("secret"),
            secret_hint=attributes.get("secret_hint"),
            previous_secret_expires_at=attributes.get("previous_secret_expires_at"),
            consecutive_failures=int(attributes.get("consecutive_failures") or 0),
            last_delivery_at=attributes.get("last_delivery_at"),
            created_at=attributes.get("created_at"),
            updated_at=attributes.get("updated_at"),
            attributes=attributes,
            raw=item,
        )


@dataclass(frozen=True)
class WebhookDelivery(ResponseCarrier):
    """A webhook delivery attempt, with what the receiver answered."""

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
class WebhookTestResult(ResponseCarrier):
    """Result of the test event of an endpoint.

    Test deliveries do not count toward the consecutive failure counter that turns
    an endpoint off.
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
class Bank(ResponseCarrier):
    """An institution of the SPEI catalog."""

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
    """The bank catalog, with the `ETag` of the response.

    It is a list of `Bank`. `etag` is passed as `if_none_match` on the next read,
    and is `None` when the API does not send it.
    """

    def __init__(self, banks: Iterable[Bank] = (), *, etag: str | None = None) -> None:
        super().__init__(banks)
        self.etag = etag


@dataclass(frozen=True)
class SpeiCalendar(ResponseCarrier):
    """The SPEI non-business-day calendar of a year, with the provision it comes from.

    `non_business_days` only carries the non-business days from Monday to Friday, as
    `YYYY-MM-DD`; Saturdays and Sundays are always non-business. `source` is the
    official provision (`issuer`, `published_in`, `published_on` and `title`).
    `covered_years` lists the years that have a calendar, and `etag` is passed as
    `if_none_match` on the next read.
    """

    year: int | None = None
    non_business_days: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    covered_years: list[int] = field(default_factory=list)
    etag: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, body: dict[str, Any], *, etag: str | None = None) -> SpeiCalendar:
        data = body.get("data")
        item: dict[str, Any] = data if isinstance(data, dict) else {}
        attributes: dict[str, Any] = item.get("attributes") or {}
        meta = body.get("meta")
        covered = meta.get("covered_years") if isinstance(meta, dict) else None
        days = attributes.get("non_business_days")
        source = attributes.get("source")
        year = attributes.get("year")
        return cls(
            year=year if isinstance(year, int) else None,
            non_business_days=[str(day) for day in days] if isinstance(days, list) else [],
            source=dict(source) if isinstance(source, dict) else {},
            covered_years=[int(y) for y in covered] if isinstance(covered, list) else [],
            etag=etag,
            raw=item,
        )


@dataclass(frozen=True)
class Beneficiary(ResponseCarrier):
    """A saved beneficiary account: CLABE, card or DiMo mobile number.

    `reactivated` is `True` only in the response of `beneficiaries.create()` when
    the registration reactivated an archived account with the same number; on a
    new registration and on the other reads it is `False`.

    https://docs.veriko.mx/en/concepts/beneficiaries
    """

    id: str
    account_number: str | None = None
    account_type: str | None = None
    bank_code: str | None = None
    bank_name: str | None = None
    label: str | None = None
    status: str | None = None
    created_at: str | None = None
    reactivated: bool = False
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_archived(self) -> bool:
        """`True` when the beneficiary was archived and is not in the active list."""
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
        beneficiary = cls.from_item(data if isinstance(data, dict) else {})
        meta = body.get("meta")
        if isinstance(meta, dict) and meta.get("reactivated") is True:
            return replace(beneficiary, reactivated=True)
        return beneficiary


@dataclass(frozen=True)
class BeneficiaryLookup(ResponseCarrier):
    """The account resolved by `beneficiaries.lookup()` within your own list."""

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
class AccountValidation(ResponseCarrier):
    """The result of `beneficiaries.validate_account()`: the structure of an account.

    A malformed number is not a request error: it shows as `checksum_valid=False`
    or `account_type="unknown"`. `bank` and `card` are dictionaries (`banxico_code`
    and `name`; `bin`, `brand`, `type`, `level` and `country_iso`) or `None`. The
    query does not consume quota.
    """

    input: str
    length: int
    is_numeric: bool
    account_type: str
    is_complete: bool
    checksum_valid: bool
    computed_control_digit: str | None = None
    auto_completed: str | None = None
    bank: dict[str, Any] | None = None
    card: dict[str, Any] | None = None
    attributes: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> AccountValidation:
        data = body.get("data") or {}
        attributes: dict[str, Any] = data.get("attributes") or {}
        bank = attributes.get("bank")
        card = attributes.get("card")
        return cls(
            input=str(attributes.get("input") or ""),
            length=int(attributes.get("length") or 0),
            is_numeric=bool(attributes.get("is_numeric", False)),
            account_type=str(attributes.get("account_type") or ""),
            is_complete=bool(attributes.get("is_complete", False)),
            checksum_valid=bool(attributes.get("checksum_valid", False)),
            computed_control_digit=attributes.get("computed_control_digit"),
            auto_completed=attributes.get("auto_completed"),
            bank=bank if isinstance(bank, dict) else None,
            card=card if isinstance(card, dict) else None,
            attributes=attributes,
            raw=body,
        )


@dataclass(frozen=True)
class BeneficiaryImportJob(ResponseCarrier):
    """A bulk beneficiary import job and its progress.

    Lifecycle: `pending` → `parsing` → `preview_ready` → `committing` →
    `completed` (or `failed` / `cancelled`).

    https://docs.veriko.mx/en/concepts/bulk-imports
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
        """`True` when the job finished, successfully or not."""
        return self.status in IMPORT_TERMINAL_STATUSES

    @property
    def is_preview_ready(self) -> bool:
        """`True` when the rows are ready to review and correct."""
        return self.status == "preview_ready"

    @property
    def is_settled(self) -> bool:
        """`True` when the job no longer advances on its own.

        It covers `preview_ready`, where progress stops to wait for the user's
        review, and the final states. It is what `import_wait()` waits for.
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
class BeneficiaryImportRow(ResponseCarrier):
    """A row extracted from an import file, with its group and its corrections."""

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
        errors = attributes.get("error_codes")
        corrections = attributes.get("corrections_applied")
        overrides = attributes.get("user_overrides")
        preview = attributes.get("raw_preview")
        return cls(
            id=str(item.get("id") or ""),
            row_index=attributes.get("row_index"),
            status=attributes.get("status"),
            parsed_account=attributes.get("parsed_account"),
            parsed_account_type=attributes.get("parsed_account_type"),
            parsed_bank_code=attributes.get("parsed_bank_code"),
            parsed_bank_name=attributes.get("parsed_bank_name"),
            parsed_label=attributes.get("parsed_label"),
            error_codes=list(errors) if isinstance(errors, list) else [],
            corrections_applied=corrections if isinstance(corrections, dict) else {},
            user_overrides=overrides if isinstance(overrides, dict) else {},
            raw_preview=preview if isinstance(preview, dict) else {},
            created_beneficiary_id=attributes.get("created_beneficiary_id"),
            attributes=attributes,
            raw=item,
        )

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> BeneficiaryImportRow:
        data = body.get("data")
        return cls.from_item(data if isinstance(data, dict) else {})


@dataclass(frozen=True)
class UsageSummary(ResponseCarrier):
    """The validation quota of the current plan, with the warning level.

    `tone` summarizes consumption: `ok` below 70 %, `warn` between 70 % and 89 %,
    and `danger` from 90 % or with the quota exhausted.

    `quota_kind` says where the quota comes from: `cycle` for the subscription
    cycle and `trial` for the trial validations of an account that has not yet
    activated the free plan. With `trial`, `renews` is `False`: the quota is not
    replenished and `resets_at` marks the end of the technical period, not more
    units.
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
class Document(ResponseCarrier):
    """A file the API returns: the CEP, an image or an export.

    `headers` are the response headers, with lowercase names. A finance download
    carries `x-finance-folio` (the folio of a statement) and `x-finance-items` (how
    many rows or files it holds); `finance_folio` and `finance_items` read them.
    The whole body is in memory: `iter_bytes()` only splits it.
    """

    content: bytes
    content_type: str
    filename: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)

    def write_to(self, path: str) -> str:
        """Saves the file at `path` and returns the path written."""
        with open(path, "wb") as handle:
            handle.write(self.content)
        return path

    def iter_bytes(self, chunk_size: int = 65536) -> Iterator[bytes]:
        """The content in chunks of `chunk_size` bytes."""
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    @property
    def finance_folio(self) -> str | None:
        """`X-Finance-Folio`: the folio of a financial statement."""
        return self.headers.get("x-finance-folio")

    @property
    def finance_items(self) -> int | None:
        """`X-Finance-Items`: how many items the financial download holds."""
        value = self.headers.get("x-finance-items")
        return int(value) if value is not None and value.isdigit() else None

    def __len__(self) -> int:
        return len(self.content)


__all__ = [
    "IMPORT_TERMINAL_STATUSES",
    "RETRYABLE_OUTCOMES",
    "TERMINAL_STATUSES",
    "WEBHOOK_EVENTS",
    "WEBHOOK_TEST_EVENT",
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
    "RetryStateFilter",
    "UsageSummary",
    "Validation",
    "ValidationStatus",
    "ValidationSummary",
    "ValidationType",
    "WebhookDelivery",
    "WebhookEndpoint",
    "WebhookEvent",
    "WebhookEventName",
    "WebhookTestResult",
]
