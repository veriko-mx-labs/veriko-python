"""The operations of the API, grouped by family.

Each family is an attribute of the client: `client.validations`, `client.webhooks`,
`client.catalog` and the rest. The three most used methods stay at the root of the
client, because they are the short path of the main use case.
"""

from __future__ import annotations

import base64
import functools
import inspect
import re
import time
import urllib.parse
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from ._base import ApiDict, ApiList, attach
from ._compat import merge_legacy_kwargs
from ._http import (
    _LAST_RESPONSE,
    MultipartFile,
    Response,
    Transport,
    check_idempotency_key,
    filename_from_content_disposition,
    parse_retry_after,
)
from .errors import (
    APIError,
    ConfigurationError,
    InvalidRequestError,
    NotModifiedError,
    PayloadTooLargeError,
    VerikoTimeoutError,
)
from .models import (
    AccountValidation,
    Bank,
    BankList,
    Beneficiary,
    BeneficiaryImportJob,
    BeneficiaryImportRow,
    BeneficiaryLookup,
    CepDocument,
    Document,
    PurgePreparation,
    PurgeResult,
    QueuedValidation,
    RecheckResult,
    RetryAttempt,
    RetryPolicy,
    RetryState,
    RetryStateFilter,
    SpeiCalendar,
    UsageSummary,
    Validation,
    ValidationSummary,
    ValidationType,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookTestResult,
)
from .pagination import (
    DELIVERIES_PAGE,
    IMPORT_PREVIEW_PAGE,
    VALIDATIONS_PAGE,
    Page,
    iterate_pages,
)
from .responses import (
    ApiUsage,
    BanxicoStatus,
    BanxicoTimeseries,
    BillingSubscription,
    BinLookup,
    CepExport,
    DashboardSummary,
    FinancePreview,
    FinanceSummary,
    InsightsOverview,
    InsightsTopBanks,
    InsightsTopBeneficiaries,
    InsightsTrends,
    PlanComparison,
    PublicPlan,
    SavedRetryPolicy,
    TelegramQueued,
    UsageBreakdown,
    UsageHeatmap,
    UsageHistory,
    UsageLimits,
    UserProfile,
    ValidationStats,
)

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
    ".json": "application/json",
}


_IMAGE_ACCEPT = (
    "image/png, image/jpeg, image/webp, application/pdf, application/octet-stream, application/json"
)

_IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}

# Marks an argument that was not passed, to tell it from `None`, which clears a value.
_UNSET: Any = object()


_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# The format each kind of identifier has in the spec.
_SEGMENT_FORMATS = {
    "uuid": _UUID,
    "int": re.compile(r"[0-9]{1,18}"),
    "bin": re.compile(r"[0-9]{6,16}"),
    "year": re.compile(r"[1-9][0-9]{3}"),
}


def _path_segment(value: str | int, kind: str = "any") -> str:
    """An identifier that goes in the path, checked against the format the spec gives it.

    `kind` is `uuid` for validations, endpoints and CEP exports, `int` for beneficiaries
    and imports, `bin` and `year` for the catalog. Anything else is rejected before a
    request goes out, and the segment is always percent-encoded.
    """
    cleaned = str(value).strip()
    pattern = _SEGMENT_FORMATS.get(kind)
    if not cleaned or (pattern is not None and not pattern.fullmatch(cleaned)):
        raise ConfigurationError("Unusable " + kind + " identifier: " + repr(value))
    if pattern is None and any(mark in cleaned for mark in "/?#\\"):
        raise ConfigurationError("Unusable identifier: " + repr(value))
    return urllib.parse.quote(cleaned, safe="")


def _idempotency_header(key: str | None) -> dict[str, str]:
    """The `Idempotency-Key` header: the caller's key, checked, or a fresh one.

    The six operations that accept the header always send one, so a retry after a
    lost response never repeats the effect.
    """
    from .client import new_idempotency_key

    return {"Idempotency-Key": check_idempotency_key(key) if key else new_idempotency_key()}


def _clean(params: Mapping[str, Any]) -> dict[str, Any]:
    """Drops filters without a value: what is not passed is not sent."""
    return {key: value for key, value in params.items() if value is not None}


def _retry_payload(policy: RetryPolicy | Mapping[str, Any]) -> dict[str, Any]:
    return policy.to_payload() if isinstance(policy, RetryPolicy) else dict(policy)


def _account_fields(
    beneficiary_account: str | None, candidate_accounts: Sequence[str] | None
) -> dict[str, Any]:
    """The account of the request: a single one or a list of candidates, never both.

    How many candidates the platform accepts is decided by the API, which answers
    `422` with `candidate_accounts_invalid` when the list does not comply.
    """
    if candidate_accounts is None:
        return {"beneficiary_account": beneficiary_account} if beneficiary_account else {}
    if beneficiary_account:
        raise InvalidRequestError(
            "Send beneficiary_account or candidate_accounts, not both",
            status=422,
            code="account_and_candidates_mutually_exclusive",
            local=True,
        )
    if isinstance(candidate_accounts, (str, bytes)):
        raise InvalidRequestError(
            "candidate_accounts goes as a list of accounts",
            status=422,
            code="candidate_accounts_invalid",
            local=True,
        )
    return {"candidate_accounts": list(candidate_accounts)}


def _flag(value: bool | None) -> str | None:
    """`True` goes as `1` and `False` as `0`; without a value, the filter is not sent."""
    if value is None:
        return None
    return "1" if value else "0"


def _filters(filters: Mapping[str, Any]) -> dict[str, Any]:
    """The filters of a validations query, with the name and value the API expects.

    `from_` goes as `from`. `playground` only accepts `1`, so `False` does not
    filter. `with_deleted` tells `1` (only removed) from `0` (only active).
    Several statuses, or several bank keys in `banks`, are joined with commas.
    """
    query = dict(filters)
    if "from_" in query:
        query["from"] = query.pop("from_")
    if "playground" in query:
        query["playground"] = "1" if query["playground"] else None
    if "with_deleted" in query:
        query["with_deleted"] = _flag(query["with_deleted"])
    for name in ("status", "banks"):
        query[name] = _comma_list(query.get(name))
    return _clean(query)


def _comma_list(value: Any) -> Any:
    """A list goes as a single comma-separated value; a lone value goes as is."""
    if isinstance(value, (list, tuple)):
        return ",".join(str(element) for element in value)
    return value


def _delivery_filters(filters: Mapping[str, Any]) -> dict[str, Any]:
    """The filters of a deliveries query, with the name and value the API expects.

    `from_` goes as `from`. `status`, `event_type` and `response_status` accept
    several values, which are joined with commas.
    """
    query = dict(filters)
    if "from_" in query:
        query["from"] = query.pop("from_")
    for name in ("status", "event_type", "response_status"):
        query[name] = _comma_list(query.get(name))
    return _clean(query)


def _endpoint_ids(webhook_id: str | Sequence[str] | None) -> str | None:
    """One or several endpoints for the `endpoint_id` filter, comma-separated."""
    if webhook_id is None:
        return None
    if isinstance(webhook_id, str):
        return _path_segment(webhook_id, "uuid")
    return ",".join(_path_segment(identifier, "uuid") for identifier in webhook_id)


def _subscribed_events(events: Sequence[str] | None) -> list[str]:
    """The events of an endpoint. At least one, and the API accepts up to ten."""
    if not events:
        raise InvalidRequestError(
            "An endpoint subscribes to at least one event",
            status=422,
            code="events_required",
            local=True,
        )
    return list(events)


def _retry_state(response: Response) -> RetryState:
    """The retry cycle state that the policy change and the cancellation return."""
    data = response.json().get("data")
    attributes = data.get("attributes") if isinstance(data, dict) else None
    state = attributes.get("retry_state") if isinstance(attributes, dict) else None
    return RetryState.from_dict(state if isinstance(state, dict) else None)


def _poll_pause(hint: Any, result: Any, default: float, *, header: str | None = None) -> float:
    """How long to wait before the next poll: the API's hint, or `default`."""
    if isinstance(hint, (int, float)) and not isinstance(hint, bool) and hint > 0:
        return float(hint)
    info = getattr(result, "response_info", None)
    retry_after = getattr(info, "retry_after", None)
    if retry_after is None and header:
        retry_after = parse_retry_after(header)
    if retry_after is not None and retry_after > 0:
        return float(retry_after)
    return default


def _not_modified(what: str, response: Response) -> APIError:
    """The `304` of a conditional read, raised as `NotModifiedError`."""
    return NotModifiedError(
        what + " has not changed since the ETag given",
        status=304,
        headers=response.headers,
        etag=response.headers.get("etag"),
    )


def _with_response_info(method: Callable[..., Any]) -> Callable[..., Any]:
    """Attaches the metadata of the last response to what a public method returns.

    A `list` becomes an `ApiList` and a `dict` an `ApiDict`, both subclasses that
    behave as before; models and other results get `response_info` set in place.
    """

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        previous = _LAST_RESPONSE.get()
        _LAST_RESPONSE.set(None)
        try:
            result = method(*args, **kwargs)
        finally:
            response = _LAST_RESPONSE.get()
            _LAST_RESPONSE.set(response or previous)
        if response is None or result is None:
            return result
        if type(result) is list:
            result = ApiList(result)
        elif type(result) is dict:
            result = ApiDict(result)
        return attach(result, response.info)

    return wrapper


class _Resource:
    """Base of the families: all of them share the transport of the client.

    Every public method of a family returns its result with `response_info`: the
    rate-limit headers, `Idempotent-Replayed`, `Deprecation`, `Sunset`, `ETag` and
    the request identifier of the response that produced it.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for name, member in list(vars(cls).items()):
            if name.startswith("_") or not inspect.isfunction(member):
                continue
            if inspect.isgeneratorfunction(member):
                continue
            setattr(cls, name, _with_response_info(member))

    def __init__(self, transport: Transport) -> None:
        self._transport = transport


class Validations(_Resource):
    """SPEI validations: create them, follow them and download what they produce.

    A validation purged with `execute_purge()` answers `410` with
    `validation_purged` on the CEP, the receipt, the retries and the later recheck.
    """

    # ── Create ──────────────────────────────────────────────────────────────

    def validate(
        self,
        *,
        send_date: str | None = None,
        amount: float | int | str | Decimal | None = None,
        beneficiary_account: str | None = None,
        candidate_accounts: Sequence[str] | None = None,
        tracking_key: str | None = None,
        numeric_reference: str | None = None,
        sender_bank: str | None = None,
        receiver_bank: str | None = None,
        recipient_is_bank: bool | int | None = None,
        retry_policy: RetryPolicy | Mapping[str, Any] | None = None,
        client_ref: str | None = None,
        idempotency_key: str | None = None,
        **legacy: Any,
    ) -> Validation:
        """Validates a SPEI transfer against the Banxico CEP.

        `POST /v1/validate`. Returns the verdict in `Validation.status`. For volume,
        `enqueue()` accepts the request and leaves the verdict for later.

        The account goes in `beneficiary_account`, or in `candidate_accounts` when
        it is not known which one it was: at least 2 accounts, up to the maximum the
        service sets, in a single validation and with a single quota unit. Send one of
        the two, not both. The
        API does not look the account up among the saved beneficiaries. The account
        that matched is left in `Validation.candidate_match`.

        `send_date` is the sending date, as `YYYY-MM-DD`, and is optional: if it is
        missing or unusable, the API tries candidate dates against Banxico and
        leaves the date it found the payment with in the result. If it has no data
        to guess it, it rejects with `422` (`preflight_failed`, `send_date_required`).

        `client_ref` is your own reference, 1 to 64 characters, which comes back in
        `Validation.client_ref` and in the webhooks, and which `list()` accepts as an
        exact filter.

        `amount` is required, greater than zero, with up to two decimals and up to 13
        integer digits.

        If the Banxico query takes more than 45 seconds, the API answers `202` and
        the validation continues in the background: the returned `Validation` has
        `status="processing"` and the verdict is collected with `get()`.

        Field names from earlier versions keep working for existing integrations and
        emit a `DeprecationWarning`; they are not part of the typed API.
        """
        body = self._direct_body(
            send_date=send_date,
            amount=amount,
            beneficiary_account=beneficiary_account,
            candidate_accounts=candidate_accounts,
            tracking_key=tracking_key,
            numeric_reference=numeric_reference,
            sender_bank=sender_bank,
            receiver_bank=receiver_bank,
            recipient_is_bank=recipient_is_bank,
            retry_policy=retry_policy,
            client_ref=client_ref,
            **legacy,
        )
        return self._sync("/validate", body, idempotency_key)

    def validate_ocr(
        self,
        *,
        image: bytes | str | Path | None = None,
        image_url: str | None = None,
        beneficiary_account: str | None = None,
        candidate_accounts: Sequence[str] | None = None,
        retry_policy: RetryPolicy | Mapping[str, Any] | None = None,
        client_ref: str | None = None,
        retain_image: bool | None = None,
        sender_bank: str | None = None,
        receiver_bank: str | None = None,
        recipient_is_bank: bool | int | None = None,
        idempotency_key: str | None = None,
        **legacy: Any,
    ) -> Validation:
        """Validates a transfer from the receipt, as an image or a PDF.

        `POST /v1/validate-ocr`. The file is read from disk when `image` is a path,
        and encoded in base64 here. Formats: JPEG, PNG, WebP or a PDF of 1 to 3
        pages, up to 12 MB. A `str` is a path unless it starts with `data:`, which
        goes as a data URI. A file over 12 MB, an `image_url` that is not `https` and
        an uncompressed PDF of more than 3 pages raise `InvalidRequestError` before
        any request (`local=True`).

        `image_url` is for a receipt already published over HTTPS. If both are sent,
        the API only considers `image`.

        `candidate_accounts` replaces `beneficiary_account` when the image does not
        show the account and it is not known which one it was: at least 2 accounts, up
        to the maximum the service sets, with a single quota unit. Send one of the
        two, not both.

        `retain_image` is `True` by default: the platform keeps the file and
        `image()` serves it. With `False` it deletes it as soon as the validation
        reaches a terminal state where it no longer needs it, and
        `Validation.image_retained` is `False`.

        `client_ref` is your own reference, 1 to 64 characters, which comes back in
        `Validation.client_ref` and in the webhooks.

        `sender_bank` and `receiver_bank` correct the bank read from the receipt:
        the 4 or 5 digit SPEI key (`"40012"`) or the name of the bank replaces the one
        read before querying Banxico. A bank that cannot be resolved answers `422`.

        Like `validate()`, it answers `202` in `processing` when the Banxico query
        takes more than 45 seconds; the verdict is collected with `get()`.

        Field names from earlier versions keep working for existing integrations and
        emit a `DeprecationWarning`; they are not part of the typed API.
        """
        body = self._ocr_body(
            image=image,
            image_url=image_url,
            beneficiary_account=beneficiary_account,
            candidate_accounts=candidate_accounts,
            retry_policy=retry_policy,
            client_ref=client_ref,
            retain_image=retain_image,
            sender_bank=sender_bank,
            receiver_bank=receiver_bank,
            recipient_is_bank=recipient_is_bank,
            **legacy,
        )
        return self._sync("/validate-ocr", body, idempotency_key)

    @staticmethod
    def _direct_body(
        *,
        send_date: str | None = None,
        amount: float | int | str | Decimal | None = None,
        beneficiary_account: str | None = None,
        candidate_accounts: Sequence[str] | None = None,
        tracking_key: str | None = None,
        numeric_reference: str | None = None,
        sender_bank: str | None = None,
        receiver_bank: str | None = None,
        recipient_is_bank: bool | int | None = None,
        retry_policy: RetryPolicy | Mapping[str, Any] | None = None,
        client_ref: str | None = None,
        idempotency_key: str | None = None,
        **legacy: Any,
    ) -> dict[str, Any]:
        """The body of a validation by fields, with its prior checks."""
        fields = merge_legacy_kwargs(
            {
                "send_date": send_date,
                "amount": amount,
                "beneficiary_account": beneficiary_account,
                "candidate_accounts": candidate_accounts,
                "tracking_key": tracking_key,
                "numeric_reference": numeric_reference,
                "sender_bank": sender_bank,
                "receiver_bank": receiver_bank,
                "recipient_is_bank": recipient_is_bank,
            },
            legacy,
            stacklevel=4,
        )
        if fields["amount"] is None:
            raise TypeError("missing required keyword-only argument: 'amount'")
        if not fields["tracking_key"] and not fields["numeric_reference"]:
            raise InvalidRequestError(
                "tracking_key or numeric_reference is required to look the transfer up in the CEP",
                status=422,
                code="tracking_key_or_reference_required",
                local=True,
            )
        if fields["candidate_accounts"] is None and not fields["beneficiary_account"]:
            raise InvalidRequestError(
                "beneficiary_account is required, or candidate_accounts when it is not known "
                "which account it was: the API does not look it up among the saved beneficiaries",
                status=422,
                code="account_required",
                local=True,
            )
        body: dict[str, Any] = {"amount": fields["amount"]}
        if fields["send_date"] is not None:
            body["send_date"] = fields["send_date"]
        body.update(_account_fields(fields["beneficiary_account"], fields["candidate_accounts"]))
        body.update(
            _clean(
                {
                    "tracking_key": fields["tracking_key"],
                    "numeric_reference": fields["numeric_reference"],
                    "sender_bank": fields["sender_bank"],
                    "receiver_bank": fields["receiver_bank"],
                    "recipient_is_bank": fields["recipient_is_bank"],
                    "client_ref": client_ref,
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
        beneficiary_account: str | None = None,
        candidate_accounts: Sequence[str] | None = None,
        retry_policy: RetryPolicy | Mapping[str, Any] | None = None,
        client_ref: str | None = None,
        retain_image: bool | None = None,
        sender_bank: str | None = None,
        receiver_bank: str | None = None,
        recipient_is_bank: bool | int | None = None,
        idempotency_key: str | None = None,
        **legacy: Any,
    ) -> dict[str, Any]:
        """The body of an OCR validation, with its prior checks."""
        fields = merge_legacy_kwargs(
            {
                "beneficiary_account": beneficiary_account,
                "candidate_accounts": candidate_accounts,
                "sender_bank": sender_bank,
                "receiver_bank": receiver_bank,
                "recipient_is_bank": recipient_is_bank,
            },
            legacy,
            stacklevel=4,
        )
        if image is None and not image_url:
            raise InvalidRequestError(
                "The receipt image is required: pass `image` or `image_url`",
                status=422,
                code="image_required",
                local=True,
            )
        body: dict[str, Any] = {}
        if image is not None:
            body["image"] = _encode_image(image)
        if image_url:
            _check_image_url(image_url)
            body["image_url"] = image_url
        body.update(
            _account_fields(fields["beneficiary_account"] or None, fields["candidate_accounts"])
        )
        if client_ref is not None:
            body["client_ref"] = client_ref
        if retain_image is not None:
            body["retain_image"] = retain_image
        if fields["sender_bank"] is not None:
            body["sender_bank"] = fields["sender_bank"]
        if fields["receiver_bank"] is not None:
            body["receiver_bank"] = fields["receiver_bank"]
        if fields["recipient_is_bank"] is not None:
            body["recipient_is_bank"] = fields["recipient_is_bank"]
        if retry_policy is not None:
            body["retry_policy"] = _retry_payload(retry_policy)
        return body

    def enqueue(self, **fields: Any) -> QueuedValidation:
        """Queues a validation by fields and returns the acknowledgement.

        `POST /v1/validate?async=1`. Accepts the same arguments as `validate()`. The
        API answers `202` with the identifier; the verdict is collected with
        `wait_for()`, or arrives by webhook.

        https://docs.veriko.mx/en/concepts/async-validations
        """
        return self._queue("/validate", self._direct_body(**fields), fields.get("idempotency_key"))

    def enqueue_ocr(self, **fields: Any) -> QueuedValidation:
        """Queues an OCR validation and returns the acknowledgement.

        `POST /v1/validate-ocr?async=1`. Accepts the same arguments as
        `validate_ocr()`.
        """
        return self._queue("/validate-ocr", self._ocr_body(**fields), fields.get("idempotency_key"))

    def _post(
        self,
        path: str,
        body: Mapping[str, Any],
        idempotency_key: str | None,
        *,
        is_async: bool,
    ) -> Response:
        return self._transport.request(
            "POST",
            path,
            json_body=body,
            query={"async": 1} if is_async else None,
            extra_headers=_idempotency_header(idempotency_key),
        )

    def _sync(self, path: str, body: Mapping[str, Any], idempotency_key: str | None) -> Validation:
        response = self._post(path, body, idempotency_key, is_async=False)
        return Validation.from_response(response.json())

    def _queue(
        self, path: str, body: Mapping[str, Any], idempotency_key: str | None
    ) -> QueuedValidation:
        response = self._post(path, body, idempotency_key, is_async=True)
        document = response.json()
        data = document.get("data") or {}
        meta = document.get("meta") or {}
        attributes = data.get("attributes") or {}
        return QueuedValidation(
            id=str(data.get("id") or attributes.get("validation_id") or ""),
            status=str(attributes.get("status") or "queued"),
            etag=response.headers.get("etag"),
            location=response.headers.get("location"),
            next_poll_after_seconds=meta.get("next_poll_after_seconds"),
            enqueued_at=attributes.get("enqueued_at"),
            expires_at=attributes.get("expires_at"),
            etag_version=attributes.get("etag_version"),
            client_ref=attributes.get("client_ref"),
            retry_state=(
                RetryState.from_dict(attributes["retry_state"])
                if isinstance(attributes.get("retry_state"), dict)
                else None
            ),
            meta=meta,
            raw=document,
        )

    # ── Read ────────────────────────────────────────────────────────────────

    def get(self, validation_id: str, *, if_none_match: str | None = None) -> Validation:
        """Reads a validation by its identifier.

        `GET /v1/validations/{id}`. With `if_none_match` the API answers `304` when
        nothing changed since that `ETag`, and the SDK signals it by raising
        `NotModifiedError`, an `APIError` with `status=304`.
        """
        response = self._transport.request(
            "GET",
            "/validations/" + _path_segment(validation_id, "uuid"),
            extra_headers={"If-None-Match": if_none_match} if if_none_match else None,
        )
        if response.status == 304:
            raise _not_modified("The validation", response)
        validation = Validation.from_response(response.json())
        etag = response.headers.get("etag")
        return replace(validation, etag=etag) if etag else validation

    def list(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
        status: str | Sequence[str] | None = None,
        type: ValidationType | None = None,
        from_: str | None = None,
        to: str | None = None,
        search: str | None = None,
        playground: bool | None = None,
        with_deleted: bool | None = None,
        batch_id: int | None = None,
        bank: str | None = None,
        banks: str | Sequence[str] | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        client_ref: str | None = None,
        retry_state: RetryStateFilter | None = None,
    ) -> Page[ValidationSummary]:
        """Lists the validations of the account, with filters and pagination.

        `GET /v1/validations`. `from_` has a trailing underscore because `from` is a
        reserved word in Python; it goes as `from`. `status` accepts one status or a
        list. `with_deleted=True` returns only the removed ones and `False` only the
        active ones. `playground=True` limits to the test-bank ones. `client_ref`
        filters by exact match with the reference sent when validating.

        `banks` accepts up to 50 SPEI keys, as a list or comma-separated, and returns
        the validations whose sender or receiver bank is any of them. It combines
        with `bank`: with both, both are met.

        A listing carries fewer fields than `get()`: it does not include the data
        sent, the Banxico result or the receipt links.
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
                        "banks": banks,
                        "amount_min": amount_min,
                        "amount_max": amount_max,
                        "client_ref": client_ref,
                        "retry_state": retry_state,
                    }
                ),
            },
        )
        return Page.from_response(
            response.json(),
            ValidationSummary.from_item,
            default_per_page=VALIDATIONS_PAGE.default,
            fetch=lambda number: self.list(
                page=number,
                per_page=per_page,
                status=status,
                type=type,
                from_=from_,
                to=to,
                search=search,
                playground=playground,
                with_deleted=with_deleted,
                batch_id=batch_id,
                bank=bank,
                banks=banks,
                amount_min=amount_min,
                amount_max=amount_max,
                client_ref=client_ref,
                retry_state=retry_state,
            ),
        )

    def iter(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
        max_pages: int | None = None,
        status: str | Sequence[str] | None = None,
        type: ValidationType | None = None,
        from_: str | None = None,
        to: str | None = None,
        search: str | None = None,
        playground: bool | None = None,
        with_deleted: bool | None = None,
        batch_id: int | None = None,
        bank: str | None = None,
        banks: str | Sequence[str] | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        client_ref: str | None = None,
        retry_state: RetryStateFilter | None = None,
    ) -> Iterator[ValidationSummary]:
        """Walks every validation that matches the filters, with the arguments of `list()`.

        It requests each page when the previous one runs out, and asks for the most the
        endpoint gives per page (50) unless `per_page` says otherwise. `page` is the
        page to start at and `max_pages` bounds the walk.
        """
        return iterate_pages(
            lambda number: self.list(
                page=number,
                per_page=per_page or VALIDATIONS_PAGE.maximum,
                status=status,
                type=type,
                from_=from_,
                to=to,
                search=search,
                playground=playground,
                with_deleted=with_deleted,
                batch_id=batch_id,
                bank=bank,
                banks=banks,
                amount_min=amount_min,
                amount_max=amount_max,
                client_ref=client_ref,
                retry_state=retry_state,
            ),
            start_page=page or 1,
            max_pages=max_pages,
        )

    def stats(
        self,
        *,
        status: str | Sequence[str] | None = None,
        type: ValidationType | None = None,
        from_: str | None = None,
        to: str | None = None,
        search: str | None = None,
        playground: bool | None = None,
        with_deleted: bool | None = None,
        batch_id: int | None = None,
        bank: str | None = None,
        banks: str | Sequence[str] | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        client_ref: str | None = None,
        retry_state: RetryStateFilter | None = None,
    ) -> ValidationStats:
        """Totals of the validations of the account, with the filters of `list()`.

        `GET /v1/validations/stats`. Returns the counters (`total`, `valid`,
        `not_found`, `by_type`, `by_status`...).
        """
        query = _filters(
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
                "banks": banks,
                "amount_min": amount_min,
                "amount_max": amount_max,
                "client_ref": client_ref,
                "retry_state": retry_state,
            }
        )
        response = self._transport.request("GET", "/validations/stats", query=query)
        return ValidationStats.from_response(response.json())

    def retry_attempts(self, validation_id: str) -> Sequence[RetryAttempt]:
        """The attempts of the retry cycle of a validation.

        `GET /v1/validations/{id}/retry-attempts`.
        """
        response = self._transport.request(
            "GET", "/validations/" + _path_segment(validation_id, "uuid") + "/retry-attempts"
        )
        data = response.json().get("data")
        if not isinstance(data, list):
            return []
        return [RetryAttempt.from_item(item) for item in data if isinstance(item, dict)]

    def wait_for(
        self,
        validation_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 5.0,
        sleep: Any = None,
    ) -> Validation:
        """Polls a validation until its verdict is firm.

        It is the counterpart of `enqueue()`. It sends `If-None-Match` with the
        `ETag` of the previous response, so a poll that finds no changes does not
        download the same body again. Between polls it waits what the API asks:
        `meta.next_poll_after_seconds` or `Retry-After`, and `poll_interval` when
        the response says nothing. The wait never goes past `timeout`.

        It waits for `Validation.is_settled`, not `is_terminal`: a validation with
        retries under way reaches `not_found` and keeps changing afterwards.

        Raises:
            VerikoTimeoutError: if `timeout` runs out without a firm verdict. It is
                also a `TimeoutError`, and `last` holds the last validation read.
        """
        sleeper: Any = sleep or time.sleep
        deadline = time.monotonic() + timeout
        etag: str | None = None
        last: Validation | None = None
        pause = poll_interval

        while True:
            try:
                last = self.get(validation_id, if_none_match=etag)
                etag = last.etag
                pause = _poll_pause(last.meta.get("next_poll_after_seconds"), last, poll_interval)
            except NotModifiedError as error:
                pause = _poll_pause(
                    None, None, poll_interval, header=error.headers.get("retry-after")
                )
            if last is not None and last.is_settled:
                return last
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VerikoTimeoutError(
                    "The validation " + validation_id + " did not settle in " + str(timeout) + " s",
                    resource_id=validation_id,
                    timeout=timeout,
                    last=last,
                )
            sleeper(min(pause, remaining))

    # ── Files ───────────────────────────────────────────────────────────────

    def cep(self, validation_id: str, *, format: str = "xml") -> CepDocument:
        """Downloads the official CEP of a validation as XML or PDF.

        `GET /v1/validations/{id}/cep`.
        """
        if format not in CEP_FORMATS:
            raise ConfigurationError("The CEP format is 'xml' or 'pdf'; got " + repr(format))
        accept = "application/xml" if format == "xml" else "application/pdf"
        response = self._transport.request(
            "GET",
            "/validations/" + _path_segment(validation_id, "uuid") + "/cep",
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
            headers=response.headers,
        )

    def image(self, validation_id: str) -> Document:
        """Downloads the receipt of an OCR validation: an image or a PDF.

        `GET /v1/validations/{id}/image`. Answers `410` with `image_not_retained`
        when the validation was created with `retain_image=False`.
        """
        response = self._transport.request(
            "GET",
            "/validations/" + _path_segment(validation_id, "uuid") + "/image",
            accept=_IMAGE_ACCEPT,
        )
        content_type = response.headers.get("content-type", "image/png")
        extension = _IMAGE_EXTENSIONS.get(content_type.split(";")[0].strip(), "")
        return _document(response, "receipt-" + validation_id + extension, content_type)

    def export(
        self,
        *,
        format: str = "csv",
        limit: int | None = None,
        status: str | Sequence[str] | None = None,
        type: ValidationType | None = None,
        from_: str | None = None,
        to: str | None = None,
        search: str | None = None,
        playground: bool | None = None,
        with_deleted: bool | None = None,
        batch_id: int | None = None,
        bank: str | None = None,
        banks: str | Sequence[str] | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        client_ref: str | None = None,
        retry_state: RetryStateFilter | None = None,
    ) -> Document:
        """Exports the validation history as CSV or XLSX, with the filters of `list()`.

        `GET /v1/validations/export`. `limit` lowers the cap of 100,000 rows.
        """
        query = _filters(
            {
                "limit": limit,
                "status": status,
                "type": type,
                "from_": from_,
                "to": to,
                "search": search,
                "playground": playground,
                "with_deleted": with_deleted,
                "batch_id": batch_id,
                "bank": bank,
                "banks": banks,
                "amount_min": amount_min,
                "amount_max": amount_max,
                "client_ref": client_ref,
                "retry_state": retry_state,
            }
        )
        return _export(self._transport, "/validations/export", format, query, "validations")

    # ── Change ──────────────────────────────────────────────────────────────

    def set_retry_policy(
        self,
        validation_id: str,
        policy: RetryPolicy | Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> RetryState:
        """Changes the retry policy of a specific validation.

        `PUT /v1/validations/{id}/retry-policy`. The body is wrapped in
        `retry_policy`. Returns the state of the retry cycle, not the whole
        validation.
        """
        response = self._transport.request(
            "PUT",
            "/validations/" + _path_segment(validation_id, "uuid") + "/retry-policy",
            json_body={"retry_policy": _retry_payload(policy)},
            extra_headers=_idempotency_header(idempotency_key),
        )
        return _retry_state(response)

    def cancel_retries(
        self, validation_id: str, *, idempotency_key: str | None = None
    ) -> RetryState:
        """Stops the pending retry cycle of a validation.

        `POST /v1/validations/{id}/cancel-retries`. Returns the cycle state, with
        `terminal_state="cancelled"`. If the cycle is no longer active, the API
        answers `422` with `retry_not_active`.
        """
        response = self._transport.request(
            "POST",
            "/validations/" + _path_segment(validation_id, "uuid") + "/cancel-retries",
            extra_headers=_idempotency_header(idempotency_key),
        )
        return _retry_state(response)

    def delete(self, validation_id: str) -> None:
        """Removes a validation from the history.

        `DELETE /v1/validations/{id}`. The API answers `204` and returns no body.
        """
        self._transport.request("DELETE", "/validations/" + _path_segment(validation_id, "uuid"))

    def send_cep_to_telegram(self, validation_id: str) -> TelegramQueued:
        """Sends the receipt to the Telegram chat linked to the account.

        `POST /v1/validations/{id}/cep/send-telegram`. The API acknowledges with
        `202`: the delivery happens later. Returns the acknowledgement
        (`{"queued": True}`).
        """
        response = self._transport.request(
            "POST", "/validations/" + _path_segment(validation_id, "uuid") + "/cep/send-telegram"
        )
        return TelegramQueued.from_response(response.json())

    # ── Recheck and purge ───────────────────────────────────────────────────

    def recheck(self, validation_id: str) -> RecheckResult:
        """Queries Banxico again for the payment status of a `valid` validation.

        `POST /v1/validations/{id}/recheck`. It does not consume quota. A `valid` is
        the verdict of the moment of the query: Banxico can report the return hours
        or days later. If it does, the validation becomes `returned`, keeps its CEP
        and the API emits the `validation.returned` webhook.

        A `valid` validation created at most 72 hours ago is queried. A `returned`
        one answers its current state without querying Banxico, with `checked_at`
        as `None`. Any other, or a `valid` outside the window, answers `422` with
        `recheck_not_eligible` or `recheck_window_expired`.

        Each validation accepts one query every 10 minutes: before that it answers
        `429` with `recheck_rate_limited`, and `RateLimitError.retry_after` carries
        the seconds left. If Banxico does not deliver a readable status it answers
        `503` with `recheck_unavailable`, and the validation keeps its interval. A
        purged validation answers `410` with `validation_purged`. The SDK does not
        retry this call on its own: each of those responses already carries its wait.

        https://docs.veriko.mx/en/concepts/cep-concept
        """
        response = self._transport.request(
            "POST",
            "/validations/" + _path_segment(validation_id, "uuid") + "/recheck",
            retry=False,
        )
        return RecheckResult.from_response(response.json())

    def prepare_purge(self, validation_id: str) -> PurgePreparation:
        """Describes what the permanent deletion of a validation would erase.

        `POST /v1/validations/{id}/purge/prepare`. It changes nothing: it returns
        what would be deleted, what the tombstone would keep and a single-use
        `confirmation_token`, which expires in `expires_in` seconds and which
        `execute_purge()` requires.

        It only accepts your own validation in a terminal state. A validation in
        progress answers `409` with `purge_validation_in_progress`, and an already
        purged one, `410` with `validation_purged`.

        https://docs.veriko.mx/en/how-to/purge-a-validation
        """
        response = self._transport.request(
            "POST", "/validations/" + _path_segment(validation_id, "uuid") + "/purge/prepare"
        )
        return PurgePreparation.from_response(response.json())

    def execute_purge(self, validation_id: str, *, confirmation_token: str) -> PurgeResult:
        """Permanently deletes the content of a validation.

        `POST /v1/validations/{id}/purge/execute`. It is the second step: the
        `confirmation_token` is the one `prepare_purge()` returned for this same
        validation. **The deletion is irreversible** and does not return quota. The
        validation is left as a tombstone: it keeps the verdict, the dates and the
        amount, and carries `purged_at`.

        A missing, malformed, expired token, or one from another validation,
        answers `422`; an already used one, `409` with
        `confirmation_token_already_used`. If the response does not arrive, `get()`
        tells whether the deletion completed: a purged validation carries
        `purged_at`. The SDK does not retry this call on its own.

        https://docs.veriko.mx/en/how-to/purge-a-validation
        """
        if not confirmation_token:
            raise InvalidRequestError(
                "confirmation_token is required: prepare_purge() returns it",
                status=422,
                code="confirmation_token_missing",
                local=True,
            )
        response = self._transport.request(
            "POST",
            "/validations/" + _path_segment(validation_id, "uuid") + "/purge/execute",
            json_body={"confirmation_token": confirmation_token},
            retry=False,
        )
        return PurgeResult.from_response(response.json())


class Webhooks(_Resource):
    """Webhook endpoints and their delivery history."""

    def create(
        self, *, url: str, events: Sequence[str], description: str | None = None
    ) -> WebhookEndpoint:
        """Registers an endpoint and returns its signing secret.

        `POST /v1/webhooks`. The secret travels **only once**, in this response:
        store it when you receive it. If it is lost, rotate it with
        `regenerate_secret()`.

        It accepts 1 to 10 events per endpoint. `description` is a free label to
        tell it apart from the others.
        """
        body = _clean(
            {"url": url, "events": _subscribed_events(events), "description": description}
        )
        response = self._transport.request("POST", "/webhooks", json_body=body)
        return WebhookEndpoint.from_item(response.json().get("data") or {})

    def list(self) -> Sequence[WebhookEndpoint]:
        """Lists the registered endpoints, without their secrets.

        `GET /v1/webhooks`.
        """
        response = self._transport.request("GET", "/webhooks")
        data = response.json().get("data")
        if not isinstance(data, list):
            return []
        return [WebhookEndpoint.from_item(item) for item in data if isinstance(item, dict)]

    def update(
        self,
        webhook_id: str,
        *,
        url: str | None = None,
        events: Sequence[str] | None = None,
        status: str | None = None,
        description: str | None = _UNSET,
    ) -> WebhookEndpoint:
        """Changes the URL, the events, the label or the status of an endpoint.

        `PUT /v1/webhooks/{id}`. An endpoint the system turned off is reactivated
        with `status="active"`; `auto_disabled` cannot be assigned from the API.
        `description=None` clears the label, and `events` cannot be empty.
        """
        body = _clean({"url": url, "status": status})
        if events is not None:
            body["events"] = _subscribed_events(events)
        if description is not _UNSET:
            body["description"] = description
        if not body:
            raise InvalidRequestError(
                "Nothing to change: pass url, events, description or status",
                status=422,
                code="no_valid_fields",
                local=True,
            )
        response = self._transport.request(
            "PUT", "/webhooks/" + _path_segment(webhook_id, "uuid"), json_body=body
        )
        return WebhookEndpoint.from_item(response.json().get("data") or {})

    def delete(self, webhook_id: str) -> None:
        """Removes an endpoint.

        `DELETE /v1/webhooks/{id}`. The API answers `204`.
        """
        self._transport.request("DELETE", "/webhooks/" + _path_segment(webhook_id, "uuid"))

    def test(self, webhook_id: str) -> WebhookTestResult:
        """Sends a test event to the endpoint.

        `POST /v1/webhooks/{id}/test`. Test deliveries do not count toward the
        consecutive failure counter.
        """
        response = self._transport.request(
            "POST", "/webhooks/" + _path_segment(webhook_id, "uuid") + "/test"
        )
        return WebhookTestResult.from_response(response.json())

    def regenerate_secret(self, webhook_id: str, *, immediate: bool = False) -> WebhookEndpoint:
        """Rotates the signing secret of an endpoint.

        `POST /v1/webhooks/{id}/regenerate-secret`. The new secret comes in
        `WebhookEndpoint.secret`, and it is the last time the API delivers it.
        By default the previous secret keeps signing deliveries for 24 hours, in
        `X-Webhook-Signature-Old`, until `previous_secret_expires_at`; with
        `immediate=True` it stops at once.
        """
        response = self._transport.request(
            "POST",
            "/webhooks/" + _path_segment(webhook_id, "uuid") + "/regenerate-secret",
            json_body={"immediate": True} if immediate else None,
        )
        return WebhookEndpoint.from_item(response.json().get("data") or {})

    # ── Deliveries ─────────────────────────────────────────────────────────

    def deliveries(
        self,
        webhook_id: str | Sequence[str] | None = None,
        *,
        page: int | None = None,
        per_page: int | None = None,
        status: str | Sequence[str] | None = None,
        event_type: str | Sequence[str] | None = None,
        response_status: str | int | Sequence[str | int] | None = None,
        validation_id: str | None = None,
        q: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        min_attempt: int | None = None,
        min_response_time_ms: int | None = None,
    ) -> Page[WebhookDelivery]:
        """Lists the delivery attempts.

        With `webhook_id` it queries those of that endpoint
        (`GET /v1/webhooks/{id}/deliveries`); without it, those of all of them
        (`GET /v1/webhooks/deliveries`), where it can also filter. The per-endpoint
        route accepts no filters, so with `webhook_id` and a filter the request goes
        to the global listing with `endpoint_id`. `webhook_id` also accepts a list of
        up to 20 endpoints, which always goes to the global listing.

        `status`, `event_type` and `response_status` accept one value or a list.
        `response_status` accepts an exact code (`404`), a family (`"5xx"`) or
        `"none"` for deliveries without a response. `from_` and `to` bound the
        creation date (`from_` goes as `from`); `min_attempt=2` leaves only the
        retries and `min_response_time_ms` the slow responses. `q` searches the
        identifier, the endpoint, the event, the validation and the HTTP code.
        """
        filters = _delivery_filters(
            {
                "status": status,
                "event_type": event_type,
                "response_status": response_status,
                "validation_id": validation_id,
                "q": q,
                "from_": from_,
                "to": to,
                "min_attempt": min_attempt,
                "min_response_time_ms": min_response_time_ms,
            }
        )
        if isinstance(webhook_id, str) and not filters:
            path = "/webhooks/" + _path_segment(webhook_id, "uuid") + "/deliveries"
            query = _clean({"page": page, "per_page": per_page})
        else:
            path = "/webhooks/deliveries"
            query = _clean(
                {
                    "page": page,
                    "per_page": per_page,
                    "endpoint_id": _endpoint_ids(webhook_id),
                    **filters,
                }
            )

        response = self._transport.request("GET", path, query=query)
        return Page.from_response(
            response.json(),
            WebhookDelivery.from_item,
            default_per_page=DELIVERIES_PAGE.default,
            fetch=lambda number: self.deliveries(
                webhook_id,
                page=number,
                per_page=per_page,
                status=status,
                event_type=event_type,
                response_status=response_status,
                validation_id=validation_id,
                q=q,
                from_=from_,
                to=to,
                min_attempt=min_attempt,
                min_response_time_ms=min_response_time_ms,
            ),
        )

    def iter_deliveries(
        self,
        webhook_id: str | Sequence[str] | None = None,
        *,
        page: int | None = None,
        per_page: int | None = None,
        max_pages: int | None = None,
        status: str | Sequence[str] | None = None,
        event_type: str | Sequence[str] | None = None,
        response_status: str | int | Sequence[str | int] | None = None,
        validation_id: str | None = None,
        q: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        min_attempt: int | None = None,
        min_response_time_ms: int | None = None,
    ) -> Iterator[WebhookDelivery]:
        """Walks every delivery attempt, with the arguments of `deliveries()`.

        It asks for the most the endpoint gives per page (100) unless `per_page` says
        otherwise. `page` is the page to start at and `max_pages` bounds the walk.
        """
        return iterate_pages(
            lambda number: self.deliveries(
                webhook_id,
                page=number,
                per_page=per_page or DELIVERIES_PAGE.maximum,
                status=status,
                event_type=event_type,
                response_status=response_status,
                validation_id=validation_id,
                q=q,
                from_=from_,
                to=to,
                min_attempt=min_attempt,
                min_response_time_ms=min_response_time_ms,
            ),
            start_page=page or 1,
            max_pages=max_pages,
        )

    def export_deliveries(
        self,
        webhook_id: str | Sequence[str] | None = None,
        *,
        format: str = "csv",
        limit: int | None = None,
        status: str | Sequence[str] | None = None,
        event_type: str | Sequence[str] | None = None,
        response_status: str | int | Sequence[str | int] | None = None,
        validation_id: str | None = None,
        q: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        min_attempt: int | None = None,
        min_response_time_ms: int | None = None,
    ) -> Document:
        """Exports the delivery history as CSV or XLSX.

        With `webhook_id`, those of that endpoint; without it, those of all of them.
        It accepts the filters of `deliveries()` and `limit`. As there, a filter with
        `webhook_id`, or a list of endpoints, takes the request to the global listing
        with `endpoint_id`.
        """
        query = _delivery_filters(
            {
                "status": status,
                "event_type": event_type,
                "response_status": response_status,
                "validation_id": validation_id,
                "q": q,
                "from_": from_,
                "to": to,
                "min_attempt": min_attempt,
                "min_response_time_ms": min_response_time_ms,
            }
        )
        if isinstance(webhook_id, str) and not query:
            return _export(
                self._transport,
                "/webhooks/" + _path_segment(webhook_id, "uuid") + "/deliveries/export",
                format,
                {"limit": limit},
                "deliveries-" + webhook_id,
            )
        return _export(
            self._transport,
            "/webhooks/deliveries/export",
            format,
            {**query, "limit": limit, "endpoint_id": _endpoint_ids(webhook_id)},
            "deliveries-" + webhook_id if isinstance(webhook_id, str) else "deliveries",
        )


class Catalog(_Resource):
    """Open data: the bank catalog, the SPEI calendar and the service status."""

    def banks(self, *, if_none_match: str | None = None) -> BankList:
        """Lists the institutions participating in SPEI with their code.

        `GET /v1/public/banks`. With `if_none_match` the API answers `304` when the
        catalog did not change, and the SDK signals it by raising `APIError` with
        `status=304`. The `ETag` of each read is left in `BankList.etag`.
        """
        response = self._transport.request(
            "GET",
            "/public/banks",
            extra_headers={"If-None-Match": if_none_match} if if_none_match else None,
        )
        if response.status == 304:
            raise _not_modified("The catalog", response)
        data = response.json().get("data")
        banks = (
            [Bank.from_item(item) for item in data if isinstance(item, dict)]
            if isinstance(data, list)
            else []
        )
        return BankList(banks, etag=response.headers.get("etag"))

    def bin_lookup(self, bin: str) -> BinLookup:
        """Resolves the issuing bank of a card from its first digits.

        `GET /v1/public/bin-lookup/{bin}`. Returns `bin`, `bank_name`,
        `banxico_code`, `card_brand`, `card_type`, `card_level` and `country_iso`.
        """
        response = self._transport.request("GET", "/public/bin-lookup/" + _path_segment(bin, "bin"))
        return BinLookup.from_response(response.json())

    def spei_calendar(self, year: int, *, if_none_match: str | None = None) -> SpeiCalendar:
        """SPEI non-business-day calendar of a year, with the provision it comes from.

        `GET /v1/public/spei-calendar/{year}`. It does not consume quota. A year
        without a calendar raises `NotFoundError` with
        `spei_calendar_year_not_covered`. With `if_none_match` the API answers `304`
        when the calendar did not change, and the SDK signals it by raising
        `NotModifiedError`, an `APIError` with `status=304`. The `ETag` of each read is left in
        `SpeiCalendar.etag`.
        """
        response = self._transport.request(
            "GET",
            "/public/spei-calendar/" + _path_segment(year, "year"),
            extra_headers={"If-None-Match": if_none_match} if if_none_match else None,
        )
        if response.status == 304:
            raise _not_modified("The calendar", response)
        return SpeiCalendar.from_response(response.json(), etag=response.headers.get("etag"))

    def banxico_status(self) -> BanxicoStatus:
        """Status of the Banxico query service.

        `GET /v1/status/banxico`. It tells a `cep_unavailable` specific to the
        transfer from an outage of the service. Returns `status` (`operational`,
        `degraded`, `down` or `unknown`), `status_label`, `message` and
        `last_verified_at`.
        """
        response = self._transport.request("GET", "/status/banxico")
        return BanxicoStatus.from_response(response.json())

    def banxico_timeseries(
        self, *, metric: str | None = None, window: str | None = None
    ) -> BanxicoTimeseries:
        """Health time series of the Banxico service.

        `GET /v1/status/banxico/timeseries`. `metric` is `probe_latency` or
        `verdict`, and `window` one of `1h`, `8h`, `12h`, `24h` or `7d`. Returns
        `unit`, `bucket_size_minutes` and `points` (`ts` and `value`).
        """
        response = self._transport.request(
            "GET",
            "/status/banxico/timeseries",
            query=_clean({"metric": metric, "window": window}),
        )
        return BanxicoTimeseries.from_response(response.json())


class Beneficiaries(_Resource):
    """Saved beneficiary accounts and their bulk import."""

    # ── The allow list ──────────────────────────────────────────────────────

    def create(
        self,
        *,
        account_number: str,
        bank_code: str | None = None,
        label: str | None = None,
    ) -> Beneficiary:
        """Registers a beneficiary account.

        `POST /beneficiaries`. The type is detected by length: CLABE (18 digits),
        card (16) or DiMo mobile number (10). For a mobile number, `bank_code` is
        required; for CLABE and card it is derived from the number. Registering an
        archived account reactivates it: the API answers `200` instead of `201` and
        `Beneficiary.reactivated` is `True`. Without `label`, the reactivated
        account keeps its alias. An active account with the same number raises
        `InvalidRequestError` with `beneficiary_already_registered`.
        """
        if not account_number:
            raise InvalidRequestError(
                "account_number is required to register the beneficiary",
                status=422,
                code="account_number_required",
                local=True,
            )
        body = _clean({"account_number": account_number, "bank_code": bank_code, "label": label})
        response = self._transport.request("POST", "/beneficiaries", json_body=body)
        return Beneficiary.from_response(response.json())

    def list(self, *, with_archived: str | None = None) -> Sequence[Beneficiary]:
        """Lists the saved beneficiary accounts.

        `GET /beneficiaries`. Not paginated: the whole list comes back. The
        `with_archived` filter is `0` for active only, `1` for archived only; without
        it, both.
        """
        response = self._transport.request(
            "GET", "/beneficiaries", query=_clean({"with_archived": with_archived})
        )
        data = response.json().get("data")
        if not isinstance(data, list):
            return []
        return [Beneficiary.from_item(item) for item in data if isinstance(item, dict)]

    def update(
        self,
        beneficiary_id: str | int,
        *,
        label: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
    ) -> Beneficiary:
        """Changes the label, the account or the bank of a beneficiary.

        `PUT /beneficiaries/{id}`. A new `account_number` derives the type and the
        bank again; `bank_code` only applies to mobile-number accounts.
        """
        body = _clean({"label": label, "account_number": account_number, "bank_code": bank_code})
        if not body:
            raise InvalidRequestError(
                "Nothing to change: pass label, account_number or bank_code",
                status=422,
                code="no_valid_fields",
                local=True,
            )
        response = self._transport.request(
            "PUT", "/beneficiaries/" + _path_segment(beneficiary_id, "int"), json_body=body
        )
        return Beneficiary.from_response(response.json())

    def delete(self, beneficiary_id: str | int) -> None:
        """Archives a beneficiary.

        `DELETE /beneficiaries/{id}`. The record is not deleted: it leaves the
        active list and is read with `with_archived=1`. A later registration with the
        same account reactivates it. The API answers `204`.
        """
        self._transport.request("DELETE", "/beneficiaries/" + _path_segment(beneficiary_id, "int"))

    def lookup(self, account: str) -> BeneficiaryLookup:
        """Resolves a specific account within your own list.

        `GET /beneficiaries/lookup`. Returns the bank data when the account is among
        the saved ones; otherwise the API answers `404`.
        """
        response = self._transport.request(
            "GET", "/beneficiaries/lookup", query={"account": account}
        )
        return BeneficiaryLookup.from_response(response.json())

    def validate_account(self, account: str, *, type: str | None = None) -> AccountValidation:
        """Checks the structure of an account number: CLABE, card or mobile number.

        `GET /beneficiaries/validate-account`. `account` is 1 to 19 digits, complete
        or partial. The type is detected by length, and the CLABE control digit or the
        card Luhn check is verified. `type` forces the reading: `"clabe"` or `"bin"`.
        A malformed number does not raise an error: it shows in `checksum_valid` and
        in `account_type`. It does not consume quota.
        """
        response = self._transport.request(
            "GET",
            "/beneficiaries/validate-account",
            query=_clean({"account": account, "type": type}),
        )
        return AccountValidation.from_response(response.json())

    def export(
        self,
        *,
        format: str = "csv",
        with_archived: str | None = None,
        limit: int | None = None,
    ) -> Document:
        """Exports the beneficiary list as CSV or XLSX.

        `GET /beneficiaries/export`. Numbers come out masked except the CLABE;
        `limit` only lowers the cap of 100,000 rows.
        """
        return _export(
            self._transport,
            "/beneficiaries/export",
            format,
            {"with_archived": with_archived, "limit": limit},
            "beneficiaries",
        )

    # ── Bulk import ─────────────────────────────────────────────────────────

    def import_template(self, *, format: str = "csv") -> Document:
        """Downloads the template for the bulk import.

        `GET /beneficiaries/imports/template`. It is the starting point of the cycle:
        download, fill in, upload, review and confirm.
        """
        if format not in TEMPLATE_FORMATS:
            raise ConfigurationError(
                "The template format is 'csv', 'xlsx', 'xls', 'txt' or 'json'; got " + repr(format)
            )
        accept = _TEMPLATE_CONTENT_TYPES[format]
        response = self._transport.request(
            "GET",
            "/beneficiaries/imports/template",
            query={"format": format},
            accept=accept + ", application/json",
        )
        return _document(response, "beneficiaries-template." + format, accept)

    def import_start(
        self,
        file: bytes | str | Path,
        *,
        parse_mode: str = "template",
        filename: str | None = None,
    ) -> BeneficiaryImportJob:
        """Uploads a file and opens an import job.

        `POST /beneficiaries/imports`. `file` can be bytes or a path, which the SDK
        reads from disk. `parse_mode` is `template` (canonical headers) or `free`
        (free format). The response is a `202` with the job in `pending` status;
        nothing is persisted until `import_commit()`.
        """
        content, name, content_type = _import_file(file, filename)
        response = self._transport.request(
            "POST",
            "/beneficiaries/imports",
            multipart={
                "parse_mode": parse_mode,
                "file": MultipartFile(filename=name, content=content, content_type=content_type),
            },
        )
        return BeneficiaryImportJob.from_response(response.json())

    def import_status(self, import_id: str | int) -> BeneficiaryImportJob:
        """Reads the status of an import job and its counters.

        `GET /beneficiaries/imports/{id}`.
        """
        response = self._transport.request(
            "GET", "/beneficiaries/imports/" + _path_segment(import_id, "int")
        )
        return BeneficiaryImportJob.from_response(response.json())

    def import_preview(
        self,
        import_id: str | int,
        *,
        page: int | None = None,
        per_page: int | None = None,
        buckets: Sequence[str] | None = None,
    ) -> Page[BeneficiaryImportRow]:
        """Lists the rows extracted from the import, paginated.

        `GET /beneficiaries/imports/{id}/preview`. Available in `preview_ready` or
        later. `buckets` filters by group (`valid`, `correctable`, `fatal`,
        `duplicate_account`, `duplicate_alias`).
        """
        response = self._transport.request(
            "GET",
            "/beneficiaries/imports/" + _path_segment(import_id, "int") + "/preview",
            query=_clean({"page": page, "per_page": per_page, "buckets": buckets}),
        )
        return Page.from_response(
            response.json(),
            BeneficiaryImportRow.from_item,
            default_per_page=IMPORT_PREVIEW_PAGE.default,
            fetch=lambda number: self.import_preview(
                import_id, page=number, per_page=per_page, buckets=buckets
            ),
        )

    def iter_import_preview(
        self,
        import_id: str | int,
        *,
        page: int | None = None,
        per_page: int | None = None,
        max_pages: int | None = None,
        buckets: Sequence[str] | None = None,
    ) -> Iterator[BeneficiaryImportRow]:
        """Walks every row of the preview, with the arguments of `import_preview()`.

        It asks for the most the endpoint gives per page (100) unless `per_page` says
        otherwise. `page` is the page to start at and `max_pages` bounds the walk.
        """
        return iterate_pages(
            lambda number: self.import_preview(
                import_id,
                page=number,
                per_page=per_page or IMPORT_PREVIEW_PAGE.maximum,
                buckets=buckets,
            ),
            start_page=page or 1,
            max_pages=max_pages,
        )

    def import_edit_row(
        self,
        import_id: str | int,
        row_id: str | int,
        *,
        parsed_account: str | None = None,
        parsed_label: str | None = None,
        parsed_account_type: str | None = None,
        parsed_bank_code: str | None = None,
        parsed_bank_name: str | None = None,
    ) -> BeneficiaryImportRow:
        """Corrects a row of the preview before confirming.

        `PATCH /beneficiaries/imports/{id}/rows/{row_id}`. Only the fields present
        are overwritten; the row is reprocessed and its group can change with the
        correction.
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
                "Nothing to correct: pass one of the parsed_* fields",
                status=422,
                code="no_valid_fields",
                local=True,
            )
        response = self._transport.request(
            "PATCH",
            "/beneficiaries/imports/"
            + _path_segment(import_id, "int")
            + "/rows/"
            + _path_segment(row_id, "int"),
            json_body=body,
        )
        return BeneficiaryImportRow.from_response(response.json())

    def import_remove_row(self, import_id: str | int, row_id: str | int) -> None:
        """Removes a row from the preview.

        `DELETE /beneficiaries/imports/{id}/rows/{row_id}`. The API answers `204`.
        """
        self._transport.request(
            "DELETE",
            "/beneficiaries/imports/"
            + _path_segment(import_id, "int")
            + "/rows/"
            + _path_segment(row_id, "int"),
        )

    def import_commit(self, import_id: str | int) -> BeneficiaryImportJob:
        """Confirms the import and triggers the persistence of its rows.

        `POST /beneficiaries/imports/{id}/commit`. The operation is asynchronous: the
        response is a `202` with the job in `committing`, and the result is followed
        with `import_wait()`.
        """
        response = self._transport.request(
            "POST", "/beneficiaries/imports/" + _path_segment(import_id, "int") + "/commit"
        )
        return BeneficiaryImportJob.from_response(response.json())

    def import_cancel(self, import_id: str | int) -> None:
        """Cancels an import that has not been confirmed yet.

        `DELETE /beneficiaries/imports/{id}`. It accepts the `pending`, `parsing` and
        `preview_ready` statuses; with a terminal one, or `committing`, the API
        answers `404`. Accounts that a confirmation already persisted are archived one
        by one with `delete()`. The API answers `204`.
        """
        self._transport.request(
            "DELETE", "/beneficiaries/imports/" + _path_segment(import_id, "int")
        )

    def import_wait(
        self,
        import_id: str | int,
        *,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
        sleep: Any = None,
    ) -> BeneficiaryImportJob:
        """Polls an import until its progress stops.

        It waits for `BeneficiaryImportJob.is_settled`: `preview_ready` or a final
        status. Unlike `validations.wait_for()`, there is no `ETag` to reuse here: the
        status endpoint does not expose one, so each poll downloads the whole body.
        A `Retry-After` on a poll replaces `poll_interval` for that wait.

        Raises:
            VerikoTimeoutError: if `timeout` runs out without reaching a firm status.
        """
        sleeper: Any = sleep or time.sleep
        deadline = time.monotonic() + timeout
        while True:
            job = self.import_status(import_id)
            if job.is_settled:
                return job
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VerikoTimeoutError(
                    "The import " + str(import_id) + " did not settle in " + str(timeout) + " s",
                    resource_id=str(import_id),
                    timeout=timeout,
                    last=job,
                )
            sleeper(min(_poll_pause(None, job, poll_interval), remaining))


class Usage(_Resource):
    """Consumption and limits: the plan quota and the activity log."""

    def summary(self) -> UsageSummary:
        """Returns the validation quota of the current plan.

        `GET /usage/summary`. Carries the limit, what was consumed, what remains and
        the warning level in `tone`.
        """
        response = self._transport.request("GET", "/usage/summary")
        return UsageSummary.from_response(response.json())

    def history(self, *, months: int | None = None) -> UsageHistory:
        """Returns the monthly consumption of the last `months` months.

        `GET /usage/history`. Most recent first. The limit that goes with each row is
        today's, not the one in force that month.
        """
        response = self._transport.request(
            "GET", "/usage/history", query=_clean({"months": months})
        )
        return UsageHistory.from_response(response.json())

    def breakdown(self, *, period: str | None = None) -> UsageBreakdown:
        """Breaks the consumption down by type of counted operation.

        `GET /usage/breakdown`. `period` is `current` for the current month or a
        `YYYY-MM` string.
        """
        response = self._transport.request(
            "GET", "/usage/breakdown", query=_clean({"period": period})
        )
        return UsageBreakdown.from_response(response.json())

    def limits(self) -> UsageLimits:
        """Returns the applicable rate limits, by context.

        `GET /usage/limits`. Only the configuration in force, without live counters;
        they are separate from the monthly quota of `summary()`.
        """
        response = self._transport.request("GET", "/usage/limits")
        return UsageLimits.from_response(response.json())

    def heatmap(self, *, days: int | None = None) -> UsageHeatmap:
        """Returns the validations grouped by day and hour.

        `GET /usage/heatmap`. Covers the last `days` days (maximum 90) and only
        carries the cells with at least one validation.
        """
        response = self._transport.request("GET", "/usage/heatmap", query=_clean({"days": days}))
        return UsageHeatmap.from_response(response.json())

    def api_usage(self) -> ApiUsage:
        """Returns the API usage metrics of the account.

        `GET /api/usage`. Gathers today's and the month's requests, the plan quota,
        the latest requests and the status of the Banxico service.
        """
        response = self._transport.request("GET", "/api/usage")
        return ApiUsage.from_response(response.json())

    def export(
        self,
        *,
        format: str = "csv",
        from_: str | None = None,
        to: str | None = None,
        limit: int | None = None,
    ) -> Document:
        """Exports the activity log as CSV or XLSX.

        `GET /api/usage/export`. `from_` and `to` bound the range, both inclusive;
        `limit` only lowers the cap of 100,000 rows.
        """
        return _export(
            self._transport,
            "/api/usage/export",
            format,
            {"from_": from_, "to": to, "limit": limit},
            "api-activity",
        )


class Account(_Resource):
    """Profile and preferences of the authenticated account."""

    def my_profile(self) -> UserProfile:
        """`GET /users/me`: returns the profile attributes."""
        response = self._transport.request("GET", "/users/me")
        return UserProfile.from_response(response.json())

    def profile(self) -> UserProfile:
        return self.my_profile()

    def get_my_retry_policy(self) -> SavedRetryPolicy:
        """`GET /users/me/retry-policy`."""
        response = self._transport.request("GET", "/users/me/retry-policy")
        return SavedRetryPolicy.from_response(response.json())

    def retry_policy(self) -> SavedRetryPolicy:
        return self.get_my_retry_policy()

    def update_my_retry_policy(
        self,
        policy: RetryPolicy | Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> SavedRetryPolicy:
        """`PUT /users/me/retry-policy` with the full body of the policy."""
        response = self._transport.request(
            "PUT",
            "/users/me/retry-policy",
            json_body={"retry_policy": _retry_payload(policy)},
            extra_headers=_idempotency_header(idempotency_key),
        )
        return SavedRetryPolicy.from_response(response.json())


class Dashboard(_Resource):
    """Dashboard summary of the authenticated account."""

    def get_summary(self, *, limit: int | None = None) -> DashboardSummary:
        response = self._transport.request("GET", "/summary", query=_clean({"limit": limit}))
        return DashboardSummary.from_response(response.json())

    def summary(self, *, limit: int | None = None) -> DashboardSummary:
        return self.get_summary(limit=limit)


class Plans(_Resource):
    """Catalog of public plans, without an API key."""

    def list_public(self) -> ApiList[PublicPlan]:
        response = self._transport.request("GET", "/plans/public", authenticated=False)
        return PublicPlan.list_from_response(response.json())

    def list(self) -> ApiList[PublicPlan]:
        return self.list_public()

    def get_public_plan_comparison(self) -> PlanComparison:
        response = self._transport.request("GET", "/plans/public/comparison", authenticated=False)
        return PlanComparison.from_response(response.json())

    def comparison(self) -> PlanComparison:
        return self.get_public_plan_comparison()


class Insights(_Resource):
    """Aggregated metrics of the authenticated account."""

    def get_overview(self) -> InsightsOverview:
        response = self._transport.request("GET", "/insights/overview")
        return InsightsOverview.from_response(response.json())

    def overview(self) -> InsightsOverview:
        return self.get_overview()

    def get_trends(self, *, range: str | None = None, metric: str | None = None) -> InsightsTrends:
        response = self._transport.request(
            "GET", "/insights/trends", query=_clean({"range": range, "metric": metric})
        )
        return InsightsTrends.from_response(response.json())

    def trends(self, **kwargs: Any) -> InsightsTrends:
        return self.get_trends(**kwargs)

    def get_top_banks(
        self, *, metric: str | None = None, limit: int | None = None
    ) -> InsightsTopBanks:
        response = self._transport.request(
            "GET", "/insights/top-banks", query=_clean({"metric": metric, "limit": limit})
        )
        return InsightsTopBanks.from_response(response.json())

    def top_banks(self, **kwargs: Any) -> InsightsTopBanks:
        return self.get_top_banks(**kwargs)

    def get_top_beneficiaries(self, *, limit: int | None = None) -> InsightsTopBeneficiaries:
        response = self._transport.request(
            "GET", "/insights/top-beneficiaries", query=_clean({"limit": limit})
        )
        return InsightsTopBeneficiaries.from_response(response.json())

    def top_beneficiaries(self, **kwargs: Any) -> InsightsTopBeneficiaries:
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
    """Financial summaries and downloads."""

    def get_summary(self, *, month: str, user_id: str | None = None) -> FinanceSummary:
        response = self._transport.request(
            "GET", "/finance/summary", query={"month": month, "user_id": user_id}
        )
        return FinanceSummary.from_response(response.json())

    def summary(self, **kwargs: Any) -> FinanceSummary:
        return self.get_summary(**kwargs)

    def get_statement(
        self, *, month: str, format: str = "pdf", user_id: str | None = None
    ) -> Document:
        return _finance_file(
            self._transport,
            "/finance/statement",
            {"month": month, "user_id": user_id},
            format,
            "statement",
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
    ) -> FinancePreview | Document:
        query = {"month": month, "user_id": user_id, "limit": limit}
        if decimal is not None:
            query["decimal"] = decimal
        if format != "preview":
            return _finance_file(self._transport, path, query, format, fallback)
        response = self._transport.request("GET", path, query=_clean({**query, "format": format}))
        return FinancePreview.from_response(response.json())

    def get_monthly(
        self,
        *,
        month: str,
        format: str = "csv",
        user_id: str | None = None,
        limit: int | None = None,
    ) -> FinancePreview | Document:
        return self._preview(
            "/finance/monthly",
            month=month,
            format=format,
            user_id=user_id,
            limit=limit,
            fallback="monthly-finance",
        )

    def monthly(self, **kwargs: Any) -> FinancePreview | Document:
        return self.get_monthly(**kwargs)

    def get_counterparties(
        self,
        *,
        month: str,
        format: str = "csv",
        user_id: str | None = None,
        limit: int | None = None,
    ) -> FinancePreview | Document:
        return self._preview(
            "/finance/counterparties",
            month=month,
            format=format,
            user_id=user_id,
            limit=limit,
            fallback="counterparties",
        )

    def counterparties(self, **kwargs: Any) -> FinancePreview | Document:
        return self.get_counterparties(**kwargs)

    def get_by_bank(
        self,
        *,
        month: str,
        format: str = "csv",
        user_id: str | None = None,
        limit: int | None = None,
    ) -> FinancePreview | Document:
        return self._preview(
            "/finance/by-bank",
            month=month,
            format=format,
            user_id=user_id,
            limit=limit,
            fallback="finance-by-bank",
        )

    def by_bank(self, **kwargs: Any) -> FinancePreview | Document:
        return self.get_by_bank(**kwargs)

    def get_accounting(
        self,
        *,
        month: str,
        decimal: str | None = None,
        format: str = "csv",
        user_id: str | None = None,
        limit: int | None = None,
    ) -> FinancePreview | Document:
        return self._preview(
            "/finance/accounting",
            month=month,
            format=format,
            user_id=user_id,
            limit=limit,
            fallback="accounting",
            decimal=decimal,
        )

    def accounting(self, **kwargs: Any) -> FinancePreview | Document:
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

    def create_cep_export(
        self,
        *,
        from_: str,
        to: str,
        user_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> CepExport:
        """Requests the CEP ZIP of a range to be built in the background.

        `POST /v1/finance/ceps/exports`. Returns the export with `status` as
        `queued`, its `id` and `links.self`. If an export of the same range is
        already under way, it returns that one.
        """
        response = self._transport.request(
            "POST",
            "/finance/ceps/exports",
            json_body=_clean({"from": from_, "to": to, "user_id": user_id}),
            extra_headers=_idempotency_header(idempotency_key),
        )
        return CepExport.from_response(response.json())

    def list_cep_exports(self) -> ApiList[CepExport]:
        """The recent CEP exports, most recent first.

        `GET /v1/finance/ceps/exports`.
        """
        response = self._transport.request("GET", "/finance/ceps/exports")
        return CepExport.list_from_response(response.json())

    def get_cep_export(self, export_id: str) -> CepExport:
        """The status of a CEP export.

        `GET /v1/finance/ceps/exports/{id}`. `status` is terminal at `ready`,
        `failed` or `expired`; with `ready`, `download_path` carries the path of the
        file.
        """
        response = self._transport.request(
            "GET", "/finance/ceps/exports/" + _path_segment(export_id, "uuid")
        )
        return CepExport.from_response(response.json())

    def wait_for_cep_export(
        self,
        export_id: str,
        *,
        timeout: float = 600.0,
        poll_interval: float = 3.0,
        sleep: Any = None,
    ) -> CepExport:
        """Polls a CEP export until it is `ready`, `failed` or `expired`.

        `GET /v1/finance/ceps/exports/{id}` on repeat. A `Retry-After` on a poll
        replaces `poll_interval` for that wait. Returns the last export; check
        `status` for the outcome, and call `download_cep_export()` when it is
        `ready`.

        Raises:
            VerikoTimeoutError: if `timeout` runs out while the export is still
                `queued` or `processing`.
        """
        sleeper: Any = sleep or time.sleep
        deadline = time.monotonic() + timeout
        while True:
            export = self.get_cep_export(export_id)
            if export.is_terminal:
                return export
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VerikoTimeoutError(
                    "The export " + export_id + " did not finish in " + str(timeout) + " s",
                    resource_id=export_id,
                    timeout=timeout,
                    last=export,
                )
            sleeper(min(_poll_pause(None, export, poll_interval), remaining))

    def download_cep_export(self, export_id: str) -> Document:
        """The ZIP of a CEP export in `ready` status.

        `GET /v1/finance/ceps/exports/{id}/download`.
        """
        return _finance_file(
            self._transport,
            "/finance/ceps/exports/" + _path_segment(export_id, "uuid") + "/download",
            {},
            "zip",
            "ceps",
            include_format=False,
        )


class Billing(_Resource):
    """Active subscription of the authenticated account."""

    def get_subscription(self) -> BillingSubscription:
        response = self._transport.request("GET", "/billing/subscription")
        return BillingSubscription.from_response(response.json())

    def subscription(self) -> BillingSubscription:
        return self.get_subscription()


# ── Shared helpers ──────────────────────────────────────────────────────────


# What the API accepts as a receipt: a file of at most 12 MB, and a PDF of 1 to 3 pages.
MAX_RECEIPT_BYTES = 12 * 1024 * 1024
MAX_RECEIPT_PDF_PAGES = 3

_PDF_PAGE = re.compile(rb"/Type\s*/Page(?![A-Za-z])")


def _check_receipt_size(size: int) -> None:
    if size > MAX_RECEIPT_BYTES:
        raise PayloadTooLargeError(
            "The receipt weighs " + str(size) + " bytes; the maximum is 12 MB",
            status=413,
            code="body_too_large",
            local=True,
        )


def _check_receipt(raw: bytes) -> None:
    """Rejects, before sending, a receipt the API would refuse for its size or pages.

    The page count reads the page objects of an uncompressed PDF; a PDF that keeps them
    in compressed object streams is left to the API.
    """
    _check_receipt_size(len(raw))
    if raw.startswith(b"%PDF-") and len(_PDF_PAGE.findall(raw)) > MAX_RECEIPT_PDF_PAGES:
        raise InvalidRequestError(
            "The PDF has more than " + str(MAX_RECEIPT_PDF_PAGES) + " pages",
            status=422,
            code="pdf_too_many_pages",
            local=True,
        )


def _check_image_url(image_url: str) -> None:
    """The API fetches `image_url` over HTTPS only."""
    if urllib.parse.urlsplit(image_url).scheme.lower() != "https":
        raise InvalidRequestError(
            "image_url has to be an https address",
            status=422,
            code="invalid_url_scheme",
            local=True,
        )


def _encode_image(image: bytes | str | Path) -> str:
    """Returns the receipt in base64, reading it from disk when it is a path.

    A `str` is a path, except when it starts with `data:`: that is a data URI, which
    the API accepts as is. Bytes are the file itself.
    """
    if isinstance(image, str) and image.startswith("data:"):
        _, _, payload = image.partition(",")
        _check_receipt_size(len(payload) * 3 // 4)
        return image
    if isinstance(image, bytes):
        raw = image
    else:
        path = Path(image)
        if not path.is_file():
            raise ConfigurationError("The image file does not exist: " + str(image))
        _check_receipt_size(path.stat().st_size)
        raw = path.read_bytes()
    _check_receipt(raw)
    return base64.b64encode(raw).decode("ascii")


def _import_file(file: bytes | str | Path, filename: str | None) -> tuple[bytes, str, str]:
    """Returns the content of an import file, its name and its type."""
    if isinstance(file, bytes):
        content = file
        name = filename or "beneficiaries.csv"
    else:
        path = Path(file)
        if not path.is_file():
            raise ConfigurationError("The file does not exist: " + str(file))
        content = path.read_bytes()
        name = filename or path.name
    return content, name, _content_type_for(name)


def _content_type_for(filename: str) -> str:
    """The content type of a file by its extension, or a generic one if none matches."""
    return _IMPORT_CONTENT_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


def _document(response: Response, fallback: str, accept: str) -> Document:
    return Document(
        content=response.body,
        content_type=response.headers.get("content-type", accept),
        filename=filename_from_content_disposition(
            response.headers.get("content-disposition"), fallback
        ),
        headers=response.headers,
    )


def _export(
    transport: Transport,
    path: str,
    format: str,
    filters: Mapping[str, Any],
    fallback: str,
) -> Document:
    if format not in EXPORT_FORMATS:
        raise ConfigurationError("The export format is 'csv' or 'xlsx'; got " + repr(format))
    query = dict(filters)
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
