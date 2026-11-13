"""Exceptions the SDK raises.

The API returns errors in an `errors` array. The stable field of each entry is
`code`; `detail` is translated according to `Accept-Language` and can change
wording between versions. Every API exception exposes `code`, the first entry,
and the whole list in `entries`, with `hint`, `docs_url` and `source` for each
one.

https://docs.veriko.mx/en/concepts/errors
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


class VerikoError(Exception):
    """Root of everything the SDK raises."""


class ConfigurationError(VerikoError):
    """The API key is missing, or an argument of the client cannot be used."""


class ConnectionError(VerikoError):
    """No response arrived: DNS, TLS, socket or timeout.

    The SDK raises it when the configured retries are exhausted. `cause` is the
    last underlying exception, also chained as `__cause__`. `timed_out` is `True`
    when that exception was a timeout. `VerikoConnectionError` is the same class
    under a name that does not shadow the built-in.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int = 1,
        cause: BaseException | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.cause = cause
        self.timed_out = timed_out


VerikoConnectionError = ConnectionError


class VerikoTimeoutError(VerikoError, TimeoutError):
    """A wait helper ran out of time before the resource reached a final state.

    It is also a built-in `TimeoutError`, so `except TimeoutError` keeps working.
    `resource_id` is the validation, import or export being awaited, `timeout` is
    the budget in seconds, and `last` is the last value read, when there was one.
    """

    def __init__(
        self,
        message: str,
        *,
        resource_id: str | None = None,
        timeout: float | None = None,
        last: Any = None,
    ) -> None:
        super().__init__(message)
        self.resource_id = resource_id
        self.timeout = timeout
        self.last = last


@dataclass(frozen=True)
class ErrorEntry:
    """One entry of the `errors` array of an error response.

    `source` carries `pointer` when the rejection names a field; `pointer` is a
    shortcut to it. `meta` holds the extra data some codes add, such as
    `validation_id` or `retry_after`.
    """

    status: str | None = None
    code: str | None = None
    detail: str | None = None
    hint: str | None = None
    docs_url: str | None = None
    source: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def pointer(self) -> str | None:
        value = self.source.get("pointer")
        return value if isinstance(value, str) else None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ErrorEntry:
        source = data.get("source")
        meta = data.get("meta")
        status = data.get("status")
        return cls(
            status=str(status) if status is not None else None,
            code=data.get("code"),
            detail=data.get("detail"),
            hint=data.get("hint"),
            docs_url=data.get("docs_url"),
            source=source if isinstance(source, dict) else {},
            meta=meta if isinstance(meta, dict) else {},
            raw=data,
        )


class APIError(VerikoError):
    """The API answered, and the answer was an error.

    Attributes:
        status: HTTP status.
        code: `errors[0].code`, the stable contract to branch on. A `422` starts
            with a `validation_error` summary; `codes` and `field_errors` carry
            the specific entries after it.
        detail: `errors[0].detail`, readable and translated text.
        pointer: `errors[0].source.pointer` when the rejection names a field.
        hint: `errors[0].hint`, a suggested fix when the API offers one.
        docs_url: `errors[0].docs_url`, the page that explains the code.
        errors: the whole array as the API sent it, as dictionaries.
        entries: the same array as `ErrorEntry` objects.
        request_id: `meta.request_id`, the value to quote when asking for support.
        meta: the whole `meta` object of the response.
        headers: response headers, with lowercase names.
        local: `True` when the SDK raised the error before sending the request.
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
        local: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.detail = detail
        self.pointer = pointer
        self.errors: list[dict[str, Any]] = list(errors or [])
        self.entries: list[ErrorEntry] = [ErrorEntry.from_dict(item) for item in self.errors]
        self.request_id = request_id
        self.meta: dict[str, Any] = meta or {}
        self.headers: dict[str, str] = headers or {}
        self.local = local

    @property
    def hint(self) -> str | None:
        return self.entries[0].hint if self.entries else None

    @property
    def docs_url(self) -> str | None:
        return self.entries[0].docs_url if self.entries else None

    @property
    def validation_id(self) -> str | None:
        """`meta.validation_id`: the validation created before the request failed."""
        value = self.meta.get("validation_id")
        if value is None:
            for entry in self.entries:
                value = entry.meta.get("validation_id")
                if value is not None:
                    break
        return str(value) if value is not None else None

    @property
    def codes(self) -> list[str]:
        """Every `code` in the response, in order."""
        return [entry.code for entry in self.entries if entry.code]

    @property
    def field_errors(self) -> list[ErrorEntry]:
        """The entries that point at a field, without the leading summary."""
        return [entry for entry in self.entries if entry.pointer]

    def has_code(self, code: str) -> bool:
        """`True` when any entry of the response carries `code`."""
        return code == self.code or code in self.codes

    def __str__(self) -> str:
        head = f"HTTP {self.status}"
        if self.code:
            head = f"{head} {self.code}"
        suffix = f" (request_id={self.request_id})" if self.request_id else ""
        return f"{head}: {super().__str__()}{suffix}"


class AuthenticationError(APIError):
    """`401`. The API key is missing or no longer valid."""


class ForbiddenError(APIError):
    """`403`. Authenticated, but not allowed to run that operation."""


class NotFoundError(APIError):
    """`404`. The resource does not exist, or the CEP is unavailable (`cep_not_available`)."""


class NotModifiedError(APIError):
    """`304`. A conditional read found nothing new since the `ETag` sent.

    It is an `APIError` with `status=304`, so `except APIError` and checks on
    `status` keep working; catch this class to handle the cache hit on its own.
    `etag` is the `ETag` the response repeated, when it carried one.
    """

    def __init__(self, message: str, *, etag: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.etag = etag


class ConflictError(APIError):
    """`409`. State conflict, such as `purge_validation_in_progress`."""


class IdempotencyKeyInProgressError(ConflictError):
    """`409` with `idempotency_key_in_progress`: the first request with that key is still running.

    The SDK waits `Retry-After` and repeats the request with the same key; this
    error reaches the caller only when its wait budget ran out. Repeating the
    call later, with the same key, returns the original result.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class GoneError(APIError):
    """`410`. The content is gone for good: `validation_purged` or `image_not_retained`."""


class InvalidRequestError(APIError):
    """`400` and `422`. The request has to be corrected before it is repeated."""


class PayloadTooLargeError(InvalidRequestError):
    """`413`. The body is over the limit: 17 MB for the whole request, 12 MB for a receipt file."""


class RateLimitError(APIError):
    """`429`. Rate limit exceeded, or plan quota exhausted.

    `retry_after` is the number of seconds the response asks to wait, when it
    carries one. `limit`, `remaining` and `reset` read the `X-RateLimit-*`
    headers; `reset` is a Unix timestamp in seconds.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after

    def _header_int(self, name: str) -> int | None:
        try:
            return int(self.headers[name])
        except (KeyError, ValueError):
            return None

    @property
    def limit(self) -> int | None:
        return self._header_int("x-ratelimit-limit")

    @property
    def remaining(self) -> int | None:
        return self._header_int("x-ratelimit-remaining")

    @property
    def reset(self) -> int | None:
        return self._header_int("x-ratelimit-reset")


class QuotaExceededError(RateLimitError):
    """`429` with `quota_exceeded`: the plan quota is spent.

    It renews on the next cycle, so the SDK does not retry it. Change the plan or
    wait for `retry_after` seconds.
    """


class ServerError(APIError):
    """`5xx`. It can be retried; the SDK already did if attempts were left."""


class SignatureVerificationError(VerikoError):
    """The signature of a webhook does not match the body received."""


__all__ = [
    "APIError",
    "AuthenticationError",
    "ConfigurationError",
    "ConflictError",
    "ConnectionError",
    "ErrorEntry",
    "ForbiddenError",
    "GoneError",
    "IdempotencyKeyInProgressError",
    "InvalidRequestError",
    "NotFoundError",
    "NotModifiedError",
    "PayloadTooLargeError",
    "QuotaExceededError",
    "RateLimitError",
    "ServerError",
    "SignatureVerificationError",
    "VerikoConnectionError",
    "VerikoError",
    "VerikoTimeoutError",
]
