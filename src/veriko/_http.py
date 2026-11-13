"""HTTP transport: one request, its retries and the translation of errors.

It uses `http.client` from the standard library, so the SDK adds no runtime
dependencies. Connections are kept alive and reused between requests, and a redirect is
not followed: it would carry `Authorization` to another host.

What gets retried follows the documented rules. A `429` is always safe to repeat
because the API rejected it before running anything, except `quota_exceeded`,
which renews with the next plan cycle. A network failure, a `408` and a `5xx`
leave the outcome unknown, so the SDK repeats them only for requests that are safe
to repeat: `GET`, `PUT` and `DELETE`, and a `POST` that carries an
`Idempotency-Key`. A `409` with `idempotency_key_in_progress` waits `Retry-After`
and repeats the request with the same key, within a bounded budget.

https://docs.veriko.mx/en/concepts/rate-limits
https://docs.veriko.mx/en/concepts/idempotency
"""

from __future__ import annotations

import contextvars
import decimal
import email.utils
import gzip
import http.client
import json
import logging
import random
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import monotonic as _monotonic
from typing import Any, Callable

from . import errors

logger = logging.getLogger("veriko")

RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

# Methods that can be repeated without a key: repeating them leaves the same state.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

# The shape of an API key: `veriko_` and 64 lowercase hexadecimal characters.
API_KEY_PATTERN = re.compile(r"veriko_[0-9a-f]{64}")

# The shape of an `Idempotency-Key`, as the API accepts it.
IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,255}")

# The languages the API translates `detail` into.
SUPPORTED_LANGUAGES = frozenset({"es", "en"})

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

_ERROR_BY_STATUS: dict[int, Any] = {
    304: errors.NotModifiedError,
    400: errors.InvalidRequestError,
    401: errors.AuthenticationError,
    403: errors.ForbiddenError,
    404: errors.NotFoundError,
    409: errors.ConflictError,
    410: errors.GoneError,
    413: errors.PayloadTooLargeError,
    422: errors.InvalidRequestError,
}


@dataclass(frozen=True)
class RateLimitInfo:
    """The `X-RateLimit-*` headers of a response.

    `reset` is the Unix timestamp, in seconds, when the window renews.
    """

    limit: int | None = None
    remaining: int | None = None
    reset: int | None = None


@dataclass(frozen=True)
class ResponseInfo:
    """What a response said besides its body.

    Every model the SDK returns carries one in `response_info`, so a caller can
    read the rate-limit headroom, see that an answer was replayed from the
    idempotency cache, or spot the field-name deprecation without a new call.

    Attributes:
        status: HTTP status.
        headers: response headers, with lowercase names.
        request_id: `meta.request_id` of the body, or the `X-Request-Id` header.
        rate_limit: the `X-RateLimit-*` headers, when present.
        deprecation: the `Deprecation` header, when the operation announces one.
        sunset: the `Sunset` header, the date the deprecated names stop working.
        idempotent_replayed: `True` when the answer came from the idempotency
            cache, `False` when it is fresh, `None` when the request had no key.
        etag: the `ETag` header.
        location: the `Location` header.
        retry_after: the `Retry-After` header, in seconds.
        attempts: how many requests the call took, counting retries.
    """

    status: int
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    request_id: str | None = None
    rate_limit: RateLimitInfo | None = None
    deprecation: str | None = None
    sunset: str | None = None
    idempotent_replayed: bool | None = None
    etag: str | None = None
    location: str | None = None
    retry_after: float | None = None
    attempts: int = 1


@dataclass(frozen=True)
class Response:
    """What the API returned, already read."""

    status: int
    headers: dict[str, str]
    body: bytes
    attempts: int = 1

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            decoded = json.loads(self.body.decode("utf-8"))
        except ValueError as exc:
            raise errors.APIError(
                "The response is not valid JSON",
                status=self.status,
                headers=self.headers,
            ) from exc
        if not isinstance(decoded, dict):
            raise errors.APIError(
                "The response is not a JSON:API document",
                status=self.status,
                headers=self.headers,
            )
        return decoded

    @property
    def info(self) -> ResponseInfo:
        return response_info(self)


def _int_header(headers: Mapping[str, str], name: str) -> int | None:
    try:
        return int(headers[name])
    except (KeyError, ValueError):
        return None


def response_info(response: Response) -> ResponseInfo:
    """Reads the metadata headers of a response into a `ResponseInfo`."""
    headers = response.headers
    rate_limit: RateLimitInfo | None = None
    if any(name in headers for name in ("x-ratelimit-limit", "x-ratelimit-remaining")):
        rate_limit = RateLimitInfo(
            limit=_int_header(headers, "x-ratelimit-limit"),
            remaining=_int_header(headers, "x-ratelimit-remaining"),
            reset=_int_header(headers, "x-ratelimit-reset"),
        )
    replayed = headers.get("idempotent-replayed")
    request_id = headers.get("x-request-id")
    if request_id is None and response.body[:1] == b"{":
        try:
            meta = response.json().get("meta")
        except errors.APIError:
            meta = None
        if isinstance(meta, dict) and meta.get("request_id") is not None:
            request_id = str(meta["request_id"])
    return ResponseInfo(
        status=response.status,
        headers=response.headers,
        request_id=request_id,
        rate_limit=rate_limit,
        deprecation=headers.get("deprecation"),
        sunset=headers.get("sunset"),
        idempotent_replayed=None if replayed is None else replayed.strip().lower() == "true",
        etag=headers.get("etag"),
        location=headers.get("location"),
        retry_after=parse_retry_after(headers.get("retry-after")),
        attempts=response.attempts,
    )


@dataclass(frozen=True)
class MultipartFile:
    """A file attached to a `multipart/form-data` request."""

    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


@dataclass(frozen=True)
class RetryConfig:
    """How many times to retry and how long to wait between attempts.

    `max_retries=0` turns retries off. The wait is exponential with random jitter,
    except when the response carries `Retry-After`: that value wins, because the
    server says it. When `Retry-After` is longer than `max_retry_after_seconds`,
    the SDK does not sleep: it raises the error, with `retry_after` set, so the
    caller decides. `idempotency_wait_seconds` is the total time the SDK waits for
    a request with the same `Idempotency-Key` that is still running.
    """

    max_retries: int = 2
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 20.0
    respect_retry_after: bool = True
    max_retry_after_seconds: float = 60.0
    idempotency_wait_seconds: float = 30.0

    def sleep_for(self, attempt: int, retry_after: float | None) -> float:
        if self.respect_retry_after and retry_after is not None:
            return max(0.0, retry_after)
        window = min(self.backoff_base_seconds * (2**attempt), self.backoff_max_seconds)
        return random.uniform(window / 2, window)

    def exceeds_wait(self, retry_after: float | None) -> bool:
        """`True` when `Retry-After` asks for more than the SDK is willing to sleep."""
        return (
            self.respect_retry_after
            and retry_after is not None
            and retry_after > self.max_retry_after_seconds
        )


def _first_code(response: Response) -> str | None:
    """The `code` of the first entry of an error response."""
    try:
        body = response.json()
    except errors.APIError:
        return None
    entries = body.get("errors")
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        return None
    code = entries[0].get("code")
    return code if isinstance(code, str) else None


def parse_retry_after(value: str | None) -> float | None:
    """Reads `Retry-After` in both forms: seconds or an HTTP date."""
    if not value:
        return None
    stripped = value.strip()
    try:
        return max(0.0, float(stripped))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return None
    return max(0.0, parsed.timestamp() - time.time())


def check_idempotency_key(key: str) -> str:
    """Returns `key` when the API accepts it, and raises otherwise."""
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise errors.InvalidRequestError(
            "An Idempotency-Key is 1 to 255 characters: letters, digits, `_` and `-`",
            status=400,
            code="invalid_idempotency_key",
            local=True,
        )
    return key


def check_accept_language(value: str | None) -> str | None:
    """Returns `value` when its primary language is `es` or `en`, and raises otherwise.

    The API serves any other language in Spanish, so a typo would go unnoticed. A tag
    with a region (`en-US`) or a list with weights (`en-US,en;q=0.9`) is accepted when
    it starts with one of the two.
    """
    if not value:
        return None
    first = value.split(",")[0].split(";")[0].strip().lower()
    if first.split("-")[0] not in SUPPORTED_LANGUAGES:
        raise errors.ConfigurationError(
            "accept_language is 'es' or 'en'; the API serves any other in Spanish. Got "
            + repr(value)
        )
    return value


def _check_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https":
        return base_url
    if parsed.scheme == "http" and (host in _LOCAL_HOSTS or host.endswith(".localhost")):
        return base_url
    raise errors.ConfigurationError(
        "base_url has to use https; plain http is accepted only for localhost, got "
        + repr(base_url)
    )


def _never_reached_server(exc: BaseException) -> bool:
    """`True` for a failure before any byte of the request left, so repeating is safe."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror))


def _is_tls_failure(exc: BaseException) -> bool:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, ssl.SSLError)


def _is_timeout(exc: BaseException) -> bool:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, (TimeoutError, socket.timeout))


_deprecation_warned = False

# How many idle connections to keep per host, and for how long.
_MAX_IDLE_CONNECTIONS = 4
_IDLE_SECONDS = 30.0

_STALE_CONNECTION_ERRORS = (
    http.client.RemoteDisconnected,
    http.client.CannotSendRequest,
    http.client.ResponseNotReady,
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
)


class _ConnectionPool:
    """Keeps connections to the API open between requests (HTTP keep-alive).

    A connection goes back to the pool after its response is read in full, unless the
    server asked to close it. A request that fails on a connection that had been idle,
    the way a server that closed it in the meantime makes it fail, is sent again on a
    new one, but only when repeating it is safe. It uses `http.client`, so the SDK
    adds no dependency.
    """

    def __init__(self, ssl_context: ssl.SSLContext | None = None) -> None:
        self._ssl_context = ssl_context
        self._lock = threading.Lock()
        self._idle: dict[tuple[str, str], list[tuple[http.client.HTTPConnection, float]]] = {}

    def _open(self, scheme: str, netloc: str, timeout: float) -> http.client.HTTPConnection:
        if scheme == "https":
            return http.client.HTTPSConnection(netloc, timeout=timeout, context=self._ssl_context)
        return http.client.HTTPConnection(netloc, timeout=timeout)

    def _take(self, key: tuple[str, str]) -> http.client.HTTPConnection | None:
        now = _monotonic()
        with self._lock:
            bucket = self._idle.get(key, [])
            while bucket:
                connection, since = bucket.pop()
                if now - since <= _IDLE_SECONDS:
                    return connection
                connection.close()
        return None

    def _give_back(self, key: tuple[str, str], connection: http.client.HTTPConnection) -> None:
        with self._lock:
            bucket = self._idle.setdefault(key, [])
            if len(bucket) < _MAX_IDLE_CONNECTIONS:
                bucket.append((connection, _monotonic()))
                return
        connection.close()

    def close(self) -> None:
        """Closes every idle connection."""
        with self._lock:
            idle, self._idle = self._idle, {}
        for bucket in idle.values():
            for connection, _ in bucket:
                connection.close()

    def request(
        self,
        method: str,
        url: str,
        payload: bytes | None,
        headers: Mapping[str, str],
        timeout: float,
        *,
        repeatable: bool,
    ) -> Response:
        parts = urllib.parse.urlsplit(url)
        target = parts.path + ("?" + parts.query if parts.query else "")
        key = (parts.scheme, parts.netloc)
        while True:
            connection = self._take(key)
            reused = connection is not None
            if connection is None:
                connection = self._open(parts.scheme, parts.netloc, timeout)
            connection.timeout = timeout
            if connection.sock is not None:
                connection.sock.settimeout(timeout)
            try:
                connection.request(method, target, body=payload, headers=dict(headers))
                raw = connection.getresponse()
                body = raw.read()
            except _STALE_CONNECTION_ERRORS:
                connection.close()
                if reused and repeatable:
                    continue
                raise
            except BaseException:
                connection.close()
                raise
            lowered = _lower_headers(raw.getheaders())
            if raw.will_close:
                connection.close()
            else:
                self._give_back(key, connection)
            return Response(
                status=int(raw.status), headers=lowered, body=_decode_body(body, lowered)
            )


def _warn_deprecation(response: Response) -> None:
    """Warns once per process when a response carries a `Deprecation` header."""
    global _deprecation_warned
    if _deprecation_warned or "deprecation" not in response.headers:
        return
    _deprecation_warned = True
    sunset = response.headers.get("sunset")
    warnings.warn(
        "The API announced a deprecation"
        + (" with sunset " + sunset if sunset else "")
        + ". Read `response_info.deprecation` on the result for the details.",
        DeprecationWarning,
        stacklevel=4,
    )


_LAST_RESPONSE: contextvars.ContextVar[Response | None] = contextvars.ContextVar(
    "veriko_last_response", default=None
)


class Transport:
    """Runs requests against the API and turns errors into exceptions."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float,
        retry: RetryConfig,
        user_agent: str,
        accept_language: str | None = None,
        sleep: Callable[[float], None] | None = None,
        opener: urllib.request.OpenerDirector | None = None,
        headers: Mapping[str, str] | None = None,
        validate_api_key: bool = True,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.base_url = _check_base_url(base_url).rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retry = retry
        self.user_agent = user_agent
        self.accept_language = check_accept_language(accept_language)
        self.headers: dict[str, str] = dict(headers or {})
        self.validate_api_key = validate_api_key
        self._sleep = sleep or time.sleep
        # Without an `opener`, requests go through a pool of kept-alive connections.
        self._opener = opener
        self._pool: _ConnectionPool | None = None if opener else _ConnectionPool(ssl_context)

    def close(self) -> None:
        """Closes the connections the transport keeps open. It reconnects on next use."""
        if self._pool is not None:
            self._pool.close()

    def with_options(
        self,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        retry: RetryConfig | None = None,
        headers: Mapping[str, str] | None = None,
        accept_language: str | None = None,
    ) -> Transport:
        """A copy that shares the connection settings and overrides some of them."""
        config = retry or self.retry
        if max_retries is not None:
            config = RetryConfig(
                max_retries=max_retries,
                backoff_base_seconds=config.backoff_base_seconds,
                backoff_max_seconds=config.backoff_max_seconds,
                respect_retry_after=config.respect_retry_after,
                max_retry_after_seconds=config.max_retry_after_seconds,
                idempotency_wait_seconds=config.idempotency_wait_seconds,
            )
        clone = Transport(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout if timeout is None else timeout,
            retry=config,
            user_agent=self.user_agent,
            accept_language=accept_language or self.accept_language,
            sleep=self._sleep,
            opener=self._opener,
            headers={**self.headers, **(headers or {})},
            validate_api_key=self.validate_api_key,
        )
        if self._pool is not None:
            clone._pool = self._pool
        return clone

    def request(
        self,
        method: str,
        path: str,
        **options: Any,
    ) -> Response:
        """Sends one request, retries included, and returns the final response.

        The response is also remembered for the running call, so the resource
        methods can attach its metadata to what they return.
        """
        response = self._request(method, path, **options)
        _LAST_RESPONSE.set(response)
        _warn_deprecation(response)
        return response

    def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        json_body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        extra_headers: Mapping[str, str] | None = None,
        accept: str = "application/json",
        multipart: Mapping[str, str | MultipartFile] | None = None,
        retry: bool = True,
    ) -> Response:
        method = method.upper()
        url = self._build_url(path, query)
        payload: bytes | None = None
        headers = {
            "Accept": accept,
            "Accept-Encoding": "gzip",
            "User-Agent": self.user_agent,
        }
        if authenticated:
            if not self.api_key:
                raise errors.ConfigurationError(
                    "The API key is missing. Pass it in api_key or set VERIKO_API_KEY."
                )
            if self.validate_api_key and not API_KEY_PATTERN.fullmatch(self.api_key):
                raise errors.ConfigurationError(
                    "The API key does not have the shape of a Veriko key. "
                    "Copy the whole key from the dashboard."
                )
            headers["Authorization"] = "Bearer " + self.api_key
        if self.accept_language:
            headers["Accept-Language"] = self.accept_language
        if json_body is not None:
            payload = json.dumps(json_body, ensure_ascii=False, default=_json_default).encode(
                "utf-8"
            )
            headers["Content-Type"] = "application/json; charset=utf-8"
        if multipart is not None:
            payload, content_type = _encode_multipart(multipart)
            headers["Content-Type"] = content_type
        for source in (self.headers, extra_headers or {}):
            for name, value in source.items():
                if value is not None:
                    headers[name] = value

        keyed = any(name.lower() == "idempotency-key" for name in headers)
        repeatable = method in IDEMPOTENT_METHODS or keyed
        retries_left = self.retry.max_retries if retry else 0
        in_progress_waited = 0.0
        sends = 0

        while True:
            sends += 1
            try:
                response = self._send(method, url, payload, headers, repeatable)
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                safe = repeatable or _never_reached_server(exc)
                if retries_left > 0 and safe and not _is_tls_failure(exc):
                    retries_left -= 1
                    self._sleep(self.retry.sleep_for(sends - 1, None))
                    continue
                raise errors.ConnectionError(
                    f"Could not reach {self.base_url} after {sends} attempt(s): {exc}",
                    attempts=sends,
                    cause=exc,
                    timed_out=_is_timeout(exc),
                ) from exc

            response = Response(
                status=response.status,
                headers=response.headers,
                body=response.body,
                attempts=sends,
            )
            logger.debug("%s %s -> %s", method, url, response.status)
            if response.status < 300 or response.status == 304:
                return response

            if method == "DELETE" and sends > 1 and response.status == 404:
                # The first attempt deleted it and its answer was lost.
                return Response(status=204, headers=response.headers, body=b"", attempts=sends)

            retry_after = parse_retry_after(response.headers.get("retry-after"))
            code = _first_code(response)

            if response.status == 429 and code == "quota_exceeded":
                raise self._to_error(response, retry_after)

            if response.status == 409 and code == "idempotency_key_in_progress" and keyed:
                wait = retry_after if retry_after is not None else 1.0
                if in_progress_waited + wait <= self.retry.idempotency_wait_seconds:
                    in_progress_waited += wait
                    self._sleep(wait)
                    continue
                raise self._to_error(response, retry_after)

            retryable = response.status == 429 or (
                response.status in RETRYABLE_STATUSES and repeatable
            )
            if retryable and retries_left > 0 and not self.retry.exceeds_wait(retry_after):
                retries_left -= 1
                self._sleep(self.retry.sleep_for(sends - 1, retry_after))
                continue

            raise self._to_error(response, retry_after)

    def _build_url(self, path: str, query: Mapping[str, Any] | None) -> str:
        url = self.base_url + "/" + path.lstrip("/")
        if query:
            pairs: list[tuple[str, str]] = []
            for name, value in query.items():
                if value is None:
                    continue
                if isinstance(value, (list, tuple)):
                    # The server reads an array as `name[]=a&name[]=b`.
                    key = name if name.endswith("[]") else name + "[]"
                    pairs.extend((key, str(item)) for item in value)
                elif isinstance(value, bool):
                    # `str(True)` would be `True`; the API only understands `1` and `0`.
                    pairs.append((name, "1" if value else "0"))
                else:
                    pairs.append((name, str(value)))
            if pairs:
                url = url + "?" + urllib.parse.urlencode(pairs)
        return url

    def _send(
        self,
        method: str,
        url: str,
        payload: bytes | None,
        headers: Mapping[str, str],
        repeatable: bool = False,
    ) -> Response:
        if self._pool is not None:
            return self._pool.request(
                method.upper(), url, payload, headers, self.timeout, repeatable=repeatable
            )
        assert self._opener is not None
        request = urllib.request.Request(url, data=payload, method=method.upper())
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with self._opener.open(request, timeout=self.timeout) as raw:
                lowered = _lower_headers(raw.headers.items())
                return Response(
                    status=int(raw.status),
                    headers=lowered,
                    body=_decode_body(raw.read(), lowered),
                )
        except urllib.error.HTTPError as exc:
            # A 4xx or 5xx arrives as an exception, but it is a response with a body.
            lowered = _lower_headers(exc.headers.items() if exc.headers else [])
            return Response(
                status=int(exc.code),
                headers=lowered,
                body=_decode_body(exc.read(), lowered),
            )

    def _to_error(self, response: Response, retry_after: float | None) -> errors.APIError:
        code: str | None = None
        detail: str | None = None
        pointer: str | None = None
        entries: list[dict[str, Any]] = []
        meta: dict[str, Any] = {}
        try:
            body = response.json()
        except errors.APIError:
            body = {}
        raw_entries = body.get("errors")
        if isinstance(raw_entries, list):
            entries = [entry for entry in raw_entries if isinstance(entry, dict)]
        if entries:
            first = entries[0]
            code = first.get("code")
            detail = first.get("detail")
            source = first.get("source")
            if isinstance(source, dict):
                pointer = source.get("pointer")
        raw_meta = body.get("meta")
        if isinstance(raw_meta, dict):
            meta = raw_meta

        message = detail or f"The API answered {response.status} without a readable body"
        kwargs: dict[str, Any] = {
            "status": response.status,
            "code": code,
            "detail": detail,
            "pointer": pointer,
            "errors": entries,
            "request_id": meta.get("request_id") or response.headers.get("x-request-id"),
            "meta": meta,
            "headers": response.headers,
        }

        if response.status == 429:
            if code == "quota_exceeded":
                return errors.QuotaExceededError(message, retry_after=retry_after, **kwargs)
            return errors.RateLimitError(message, retry_after=retry_after, **kwargs)
        if response.status == 409 and code == "idempotency_key_in_progress":
            return errors.IdempotencyKeyInProgressError(message, retry_after=retry_after, **kwargs)
        cls = _ERROR_BY_STATUS.get(response.status)
        if cls is None:
            cls = errors.ServerError if response.status >= 500 else errors.APIError
        error: errors.APIError = cls(message, **kwargs)
        return error


def _json_default(value: Any) -> Any:
    """Serializes `Decimal` as a JSON number; other types are an error.

    Amounts stay far below 2^53, so the float round trip keeps every cent.
    """
    if isinstance(value, decimal.Decimal) and value.is_finite():
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _decode_body(body: bytes, headers: Mapping[str, str]) -> bytes:
    encoding = headers.get("content-encoding", "").strip().lower()
    if encoding in ("gzip", "x-gzip") and body:
        try:
            return gzip.decompress(body)
        except (OSError, EOFError, zlib.error):
            return body
    return body


def _lower_headers(items: Any) -> dict[str, str]:
    return {str(name).lower(): str(value) for name, value in items}


def _quote_param(value: str) -> str:
    """Escapes a `multipart/form-data` parameter value (RFC 7578)."""
    return value.replace("\\", "\\\\").replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")


def _encode_multipart(fields: Mapping[str, str | MultipartFile]) -> tuple[bytes, str]:
    """Encodes a `multipart/form-data` body and returns bytes and content type."""
    boundary = "veriko-" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(("--" + boundary).encode("ascii"))
        if isinstance(value, MultipartFile):
            disposition = (
                'Content-Disposition: form-data; name="'
                + _quote_param(name)
                + '"; filename="'
                + _quote_param(value.filename.encode("ascii", "replace").decode("ascii"))
                + '"'
            )
            if not value.filename.isascii():
                disposition += "; filename*=UTF-8''" + urllib.parse.quote(value.filename, safe="")
            chunks.append(disposition.encode("ascii"))
            chunks.append(("Content-Type: " + value.content_type).encode("ascii"))
            chunks.append(b"")
            chunks.append(value.content)
        else:
            chunks.append(
                ('Content-Disposition: form-data; name="' + _quote_param(name) + '"').encode(
                    "utf-8"
                )
            )
            chunks.append(b"")
            chunks.append(str(value).encode("utf-8"))
        chunks.append(b"")
    chunks.append(("--" + boundary + "--").encode("ascii"))
    chunks.append(b"")
    body = b"\r\n".join(chunks)
    return body, "multipart/form-data; boundary=" + boundary


def filename_from_content_disposition(value: str | None, fallback: str) -> str:
    """Reads the file name from `Content-Disposition`, or uses the fallback."""
    if not value:
        return fallback
    extended: str | None = None
    plain: str | None = None
    for part in value.split(";"):
        candidate = part.strip()
        lowered = candidate.lower()
        if lowered.startswith("filename*="):
            raw = candidate[len("filename*=") :].strip()
            _, _, encoded = raw.partition("''")
            extended = urllib.parse.unquote(encoded or raw).strip('"')
        elif lowered.startswith("filename="):
            plain = candidate[len("filename=") :].strip().strip('"')
    return extended or plain or fallback


__all__ = [
    "IDEMPOTENCY_KEY_PATTERN",
    "IDEMPOTENT_METHODS",
    "RETRYABLE_STATUSES",
    "MultipartFile",
    "RateLimitInfo",
    "Response",
    "ResponseInfo",
    "RetryConfig",
    "Transport",
    "check_idempotency_key",
    "filename_from_content_disposition",
    "parse_retry_after",
    "response_info",
]
