"""Test harness: a local server that serves recorded responses.

No test calls the Veriko API. The tests exercise the SDK against responses
stored in `tests/recordings/`, served by a real HTTP server on `127.0.0.1`.

The harness goes over real HTTP instead of replacing the transport. That
exercises what the SDK runs in production: headers, status codes, binary bodies
and the translation of a urllib `HTTPError`.
"""

from __future__ import annotations

import base64
import contextlib
import json
import socket
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

# A key shaped like the ones the API issues. It belongs to no account.
TEST_API_KEY = "veriko_" + "0123456789abcdef" * 4


def load_recording(name: str) -> dict[str, Any]:
    """Reads a recorded response from `tests/recordings/<name>.json`."""
    with open(RECORDINGS_DIR / (name + ".json"), encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


@dataclass
class RecordedResponse:
    """A response ready to serve."""

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
    """What the SDK sent, as it reached the server."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    # The local port the request came from: requests on one connection share it.
    connection: int = 0

    def json(self) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.body.decode("utf-8"))
        return parsed

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class RecordingServer:
    """Serves the queued responses in order and records what it received."""

    def __init__(self) -> None:
        self.queue: list[RecordedResponse] = []
        self.requests: list[CapturedRequest] = []
        self.sockets: list[Any] = []
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
                b'"detail":"The test server ran out of responses"}],"meta":{}}',
                headers={"Content-Type": "application/json"},
            )
        return self.queue.pop(0)

    def drop_connections(self) -> None:
        """Closes every open connection from the server side."""
        for sock in self.sockets:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()
        self.sockets.clear()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def _make_handler(server: RecordingServer) -> Any:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _handle(self) -> None:
            if self.connection not in server.sockets:
                server.sockets.append(self.connection)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            server.requests.append(
                CapturedRequest(
                    method=self.command,
                    path=self.path,
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=body,
                    connection=int(self.client_address[1]),
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
        do_PATCH = _handle
        do_DELETE = _handle

        def log_message(self, *args: Any) -> None:  # keep pytest output quiet
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
    """The waits the SDK would have made between retries, without making them."""
    return []


@pytest.fixture()
def client(server: RecordingServer, sleeps: list[float]) -> Veriko:
    """A client pointed at the recorded-response server.

    It never sleeps: the waits between retries are appended to `sleeps`, which
    is what the tests assert on.
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
        api_key=TEST_API_KEY,
        timeout=5.0,
        retry=RetryConfig(
            max_retries=max_retries,
            backoff_base_seconds=0.01,
            respect_retry_after=respect_retry_after,
        ),
        user_agent="veriko-sdk-python/test",
        sleep=sleeps.append,
    )
    return Veriko(transport=transport)
