"""Transporte HTTP: una petición, sus reintentos y la traducción de errores.

Usa `urllib` de la biblioteca estándar, de modo que el SDK no añade
dependencias de runtime a quien lo instala.

Qué admite reintento, igual que lo documenta el portal: los `5xx`, el `408` y el
`429`. El resto de los `4xx` no, porque la petición hay que corregirla antes de
repetirla.

https://docs.veriko.mx/es/concepts/rate-limits
"""

from __future__ import annotations

import email.utils
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from . import errors

RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

_ERROR_BY_STATUS: dict[int, Any] = {
    400: errors.InvalidRequestError,
    401: errors.AuthenticationError,
    403: errors.ForbiddenError,
    404: errors.NotFoundError,
    409: errors.ConflictError,
    413: errors.InvalidRequestError,
    422: errors.InvalidRequestError,
}


@dataclass(frozen=True)
class Response:
    """Lo que devolvió la API, ya leído."""

    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        decoded = json.loads(self.body.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise errors.APIError(
                "La respuesta no es un documento JSON:API",
                status=self.status,
                headers=self.headers,
            )
        return decoded


@dataclass(frozen=True)
class MultipartFile:
    """Un archivo adjunto en una petición `multipart/form-data`."""

    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


@dataclass(frozen=True)
class RetryConfig:
    """Cuántas veces reintentar y cuánto esperar entre intentos.

    `max_retries=0` desactiva los reintentos. La espera es exponencial con
    fluctuación aleatoria, salvo cuando la respuesta trae `Retry-After`: ese
    valor gana siempre, porque lo dice el servidor.
    """

    max_retries: int = 2
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 20.0
    respect_retry_after: bool = True

    def sleep_for(self, attempt: int, retry_after: float | None) -> float:
        if self.respect_retry_after and retry_after is not None:
            return max(0.0, min(retry_after, self.backoff_max_seconds * 3))
        window = min(self.backoff_base_seconds * (2**attempt), self.backoff_max_seconds)
        return random.uniform(window / 2, window)


def parse_retry_after(value: str | None) -> float | None:
    """Lee `Retry-After` en sus dos formas: segundos o fecha HTTP."""
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


class Transport:
    """Ejecuta peticiones contra la API y convierte los errores en excepciones."""

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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retry = retry
        self.user_agent = user_agent
        self.accept_language = accept_language
        self._sleep = sleep or time.sleep
        self._opener = opener or urllib.request.build_opener()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        extra_headers: Mapping[str, str] | None = None,
        accept: str = "application/json",
        multipart: Mapping[str, str | MultipartFile] | None = None,
    ) -> Response:
        url = self._build_url(path, query)
        payload: bytes | None = None
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Accept": accept,
            "User-Agent": self.user_agent,
        }
        if self.accept_language:
            headers["Accept-Language"] = self.accept_language
        if json_body is not None:
            payload = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if multipart is not None:
            payload, content_type = _encode_multipart(multipart)
            headers["Content-Type"] = content_type
        if extra_headers:
            for name, value in extra_headers.items():
                if value is not None:
                    headers[name] = value

        attempts = self.retry.max_retries + 1
        last_connection_error: BaseException | None = None

        for attempt in range(attempts):
            is_last = attempt == attempts - 1
            try:
                response = self._send(method, url, payload, headers)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_connection_error = exc
                if is_last:
                    break
                self._sleep(self.retry.sleep_for(attempt, None))
                continue

            if response.status < 400:
                return response

            retry_after = parse_retry_after(response.headers.get("retry-after"))
            if response.status in RETRYABLE_STATUSES and not is_last:
                self._sleep(self.retry.sleep_for(attempt, retry_after))
                continue

            raise self._to_error(response, retry_after)

        raise errors.ConnectionError(
            f"No se pudo contactar a {self.base_url} tras {attempts} intento(s): "
            f"{last_connection_error}",
            attempts=attempts,
        )

    def _build_url(self, path: str, query: Mapping[str, Any] | None) -> str:
        url = self.base_url + "/" + path.lstrip("/")
        if query:
            pairs: list[tuple[str, str]] = []
            for name, value in query.items():
                if value is None:
                    continue
                if isinstance(value, (list, tuple)):
                    pairs.extend((name, str(item)) for item in value)
                elif isinstance(value, bool):
                    # `str(True)` sería `True`; la API sólo entiende `1` y `0`.
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
    ) -> Response:
        request = urllib.request.Request(url, data=payload, method=method.upper())
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with self._opener.open(request, timeout=self.timeout) as raw:
                return Response(
                    status=int(raw.status),
                    headers=_lower_headers(raw.headers.items()),
                    body=raw.read(),
                )
        except urllib.error.HTTPError as exc:
            # Un 4xx o un 5xx llega como excepción, pero es una respuesta con cuerpo.
            body = exc.read()
            return Response(
                status=int(exc.code),
                headers=_lower_headers(exc.headers.items() if exc.headers else []),
                body=body,
            )

    def _to_error(self, response: Response, retry_after: float | None) -> errors.APIError:
        code: str | None = None
        detail: str | None = None
        pointer: str | None = None
        entries: list[dict[str, Any]] = []
        meta: dict[str, Any] = {}
        try:
            body = response.json()
        except (ValueError, errors.APIError):
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

        message = detail or f"La API respondió {response.status} sin cuerpo legible"
        kwargs: dict[str, Any] = {
            "status": response.status,
            "code": code,
            "detail": detail,
            "pointer": pointer,
            "errors": entries,
            "request_id": meta.get("request_id"),
            "meta": meta,
            "headers": response.headers,
        }

        if response.status == 429:
            return errors.RateLimitError(message, retry_after=retry_after, **kwargs)
        cls = _ERROR_BY_STATUS.get(response.status)
        if cls is None:
            cls = errors.ServerError if response.status >= 500 else errors.APIError
        error: errors.APIError = cls(message, **kwargs)
        return error


def _lower_headers(items: Any) -> dict[str, str]:
    return {str(name).lower(): str(value) for name, value in items}


def _encode_multipart(fields: Mapping[str, str | MultipartFile]) -> tuple[bytes, str]:
    """Codifica un cuerpo `multipart/form-data` y devuelve bytes y content-type."""
    boundary = "veriko-" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(("--" + boundary).encode("ascii"))
        if isinstance(value, MultipartFile):
            disposition = (
                'Content-Disposition: form-data; name="'
                + name
                + '"; filename="'
                + value.filename
                + '"'
            )
            chunks.append(disposition.encode("utf-8"))
            chunks.append(("Content-Type: " + value.content_type).encode("ascii"))
            chunks.append(b"")
            chunks.append(value.content)
        else:
            chunks.append(('Content-Disposition: form-data; name="' + name + '"').encode("utf-8"))
            chunks.append(b"")
            chunks.append(str(value).encode("utf-8"))
        chunks.append(b"")
    chunks.append(("--" + boundary + "--").encode("ascii"))
    chunks.append(b"")
    body = b"\r\n".join(chunks)
    return body, "multipart/form-data; boundary=" + boundary


def filename_from_content_disposition(value: str | None, fallback: str) -> str:
    """Saca el nombre de archivo de `Content-Disposition`, o usa el de respaldo."""
    if not value:
        return fallback
    for part in value.split(";"):
        candidate = part.strip()
        if candidate.lower().startswith("filename="):
            name = candidate[len("filename=") :].strip().strip('"')
            if name:
                return name
    return fallback


__all__ = [
    "RETRYABLE_STATUSES",
    "MultipartFile",
    "Response",
    "RetryConfig",
    "Transport",
    "filename_from_content_disposition",
    "parse_retry_after",
]
