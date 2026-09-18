"""Arnés de pruebas: un servidor local que sirve respuestas grabadas.

Ninguna prueba llama a la API de Veriko. Lo que se prueba es el SDK contra
respuestas guardadas en `tests/recordings/`, servidas por un servidor HTTP de
verdad en `127.0.0.1`.

Es deliberado que pase por HTTP real en lugar de sustituir el transporte: así se
ejercita lo que el SDK hace de verdad —cabeceras, códigos de estado, cuerpos
binarios, la traducción de un `HTTPError` de urllib— y no una imitación.
"""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from veriko import RetryConfig, Veriko
from veriko._http import Transport

RECORDINGS_DIR = Path(__file__).parent / "recordings"


def load_recording(name: str) -> dict[str, Any]:
    """Lee una respuesta grabada de `tests/recordings/<name>.json`."""
    with open(RECORDINGS_DIR / (name + ".json"), encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


@dataclass
class RecordedResponse:
    """Una respuesta lista para servir."""

    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_recording(cls, name: str) -> RecordedResponse:
        recording = load_recording(name)
        headers = dict(recording.get("headers") or {})
        if "json" in recording:
            body = json.dumps(recording["json"], ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json; charset=utf-8")
        elif "body_base64" in recording:
            body = base64.b64decode(recording["body_base64"])
        else:
            body = str(recording.get("body", "")).encode("utf-8")
        return cls(status=int(recording["status"]), body=body, headers=headers)


@dataclass
class CapturedRequest:
    """Lo que el SDK envió, tal como llegó al servidor."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.body.decode("utf-8"))
        return parsed

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class RecordingServer:
    """Sirve, en orden, las respuestas encoladas; guarda lo que recibió."""

    def __init__(self) -> None:
        self.queue: list[RecordedResponse] = []
        self.requests: list[CapturedRequest] = []
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_port}/v1"

    def enqueue(self, response: RecordedResponse) -> RecordingServer:
        self.queue.append(response)
        return self

    def enqueue_recording(self, name: str, times: int = 1) -> RecordingServer:
        for _ in range(times):
            self.enqueue(RecordedResponse.from_recording(name))
        return self

    def next_response(self) -> RecordedResponse:
        if not self.queue:
            return RecordedResponse(
                status=500,
                body=b'{"errors":[{"status":"500","code":"no_recording_left",'
                b'"detail":"El servidor de pruebas se quedo sin respuestas"}],"meta":{}}',
                headers={"Content-Type": "application/json"},
            )
        return self.queue.pop(0)

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def _make_handler(server: RecordingServer) -> Any:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            server.requests.append(
                CapturedRequest(
                    method=self.command,
                    path=self.path,
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=body,
                )
            )
            recorded = server.next_response()
            self.send_response(recorded.status)
            for name, value in recorded.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(recorded.body)))
            self.end_headers()
            self.wfile.write(recorded.body)

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle

        def log_message(self, *args: Any) -> None:  # silencio en la salida de pytest
            return

    return Handler


@pytest.fixture()
def server() -> Iterator[RecordingServer]:
    recording_server = RecordingServer()
    try:
        yield recording_server
    finally:
        recording_server.close()


@pytest.fixture()
def sleeps() -> list[float]:
    """Las esperas que el SDK habría hecho entre reintentos, sin hacerlas."""
    return []


@pytest.fixture()
def client(server: RecordingServer, sleeps: list[float]) -> Veriko:
    """Un cliente apuntado al servidor de respuestas grabadas.

    No duerme de verdad: las esperas entre reintentos se anotan en `sleeps`, que
    es lo que las pruebas comprueban.
    """
    return make_client(server, sleeps)


def make_client(
    server: RecordingServer,
    sleeps: list[float],
    *,
    max_retries: int = 2,
    respect_retry_after: bool = True,
) -> Veriko:
    transport = Transport(
        base_url=server.base_url,
        api_key="veriko_prueba",
        timeout=5.0,
        retry=RetryConfig(
            max_retries=max_retries,
            backoff_base_seconds=0.01,
            respect_retry_after=respect_retry_after,
        ),
        user_agent="veriko-python/prueba",
        sleep=sleeps.append,
    )
    return Veriko(transport=transport)
