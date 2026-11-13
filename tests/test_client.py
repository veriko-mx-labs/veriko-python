"""Client construction: key, API root and the absence of a network."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import TEST_API_KEY, RecordingServer
from veriko import (
    DEFAULT_BASE_URL,
    ConfigurationError,
    ConnectionError,
    RetryConfig,
    Veriko,
    __version__,
    build_user_agent,
)
from veriko._http import Transport


def test_the_public_version_matches_the_package() -> None:
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    project = pyproject.split("[project]", 1)[1].split("\n[", 1)[0]

    assert f'version = "{__version__}"' in project


def test_without_an_api_key_the_client_builds_for_public_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VERIKO_API_KEY", raising=False)

    client = Veriko()

    assert client.base_url == DEFAULT_BASE_URL


def test_without_an_api_key_an_authenticated_operation_is_rejected_before_the_network(
    monkeypatch: pytest.MonkeyPatch, server: RecordingServer
) -> None:
    monkeypatch.delenv("VERIKO_API_KEY", raising=False)
    client = Veriko(api_key="", base_url=server.base_url)

    with pytest.raises(ConfigurationError):
        client.validate_transfer(
            send_date="2025-03-15",
            amount=15000.50,
            beneficiary_account="012180004412345678",
            tracking_key="MXBA20250315001234",
        )

    assert server.requests == []


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("0123456789abcdef" * 4, id="no-prefix"),
        pytest.param("otra_" + "0123456789abcdef" * 4, id="other-prefix"),
        pytest.param("veriko_x", id="short"),
        pytest.param(TEST_API_KEY + "0", id="long"),
        pytest.param("veriko_" + "0123456789ABCDEF" * 4, id="uppercase"),
        pytest.param(" " + TEST_API_KEY + "\n", id="whitespace"),
    ],
)
def test_a_malformed_key_rejects_the_operation_before_the_network(
    server: RecordingServer, key: str
) -> None:
    client = Veriko(api_key=key, base_url=server.base_url, max_retries=0)

    with pytest.raises(ConfigurationError) as raised:
        client.validate_transfer(
            send_date="2025-03-15",
            amount=15000.50,
            beneficiary_account="012180004412345678",
            tracking_key="MXBA20250315001234",
        )

    assert key.strip() not in str(raised.value)
    assert server.requests == []


def test_a_malformed_key_from_the_environment_does_not_reach_the_network_either(
    monkeypatch: pytest.MonkeyPatch, server: RecordingServer
) -> None:
    monkeypatch.setenv("VERIKO_API_KEY", "veriko_del_entorno")
    client = Veriko(base_url=server.base_url)

    with pytest.raises(ConfigurationError):
        client.validate_transfer(
            send_date="2025-03-15",
            amount=15000.50,
            beneficiary_account="012180004412345678",
            tracking_key="MXBA20250315001234",
        )

    assert server.requests == []


def test_a_malformed_key_does_not_block_public_operations(
    server: RecordingServer,
) -> None:
    server.enqueue_recording("public-plans")
    client = Veriko(api_key="veriko_x", base_url=server.base_url, max_retries=0)

    client.plans.list_public()

    assert len(server.requests) == 1
    assert server.requests[0].header("authorization") is None


def test_the_key_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERIKO_API_KEY", TEST_API_KEY)

    client = Veriko()

    assert client.base_url == DEFAULT_BASE_URL


def test_the_api_root_can_point_somewhere_else() -> None:
    client = Veriko(api_key=TEST_API_KEY, base_url="http://127.0.0.1:9999/v1/")

    assert client.base_url == "http://127.0.0.1:9999/v1"


def test_the_request_identifies_itself_with_the_sdk_user_agent(
    server: RecordingServer,
) -> None:
    client = Veriko(api_key=TEST_API_KEY, base_url=server.base_url)
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    agent = server.requests[0].header("user-agent")
    assert agent is not None
    assert re.fullmatch(
        r"veriko-sdk-python/" + re.escape(__version__) + r" \([a-z]+ \d+\.\d+\.\d+\S*; \S+ \S+\)",
        agent,
    )


def test_the_user_agent_carries_the_prefix_first_and_the_suffix_last(
    server: RecordingServer,
) -> None:
    client = Veriko(
        api_key=TEST_API_KEY,
        base_url=server.base_url,
        user_agent_prefix="mi-producto/1.2",
        user_agent_suffix="integracion-x",
    )
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    agent = server.requests[0].header("user-agent")
    assert agent is not None
    assert agent.startswith("mi-producto/1.2 veriko-sdk-python/" + __version__ + " (")
    assert agent.endswith(") integracion-x")
    assert agent == build_user_agent("mi-producto/1.2", "integracion-x")


def test_build_user_agent_omits_empty_values() -> None:
    base = build_user_agent()

    assert base.startswith("veriko-sdk-python/" + __version__ + " (")
    assert build_user_agent("", None) == base
    assert build_user_agent(None, "   ") == base
    assert build_user_agent("a", "b") == "a " + base + " b"


def test_the_message_language_is_negotiated_by_header(
    server: RecordingServer,
) -> None:
    client = Veriko(api_key=TEST_API_KEY, base_url=server.base_url, accept_language="en")
    server.enqueue_recording("validate-valid")

    client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        beneficiary_account="012180004412345678",
        tracking_key="MXBA20250315001234",
    )

    assert server.requests[0].header("accept-language") == "en"


def test_without_a_server_on_the_other_side_a_connection_error_is_raised(
    sleeps: list[float],
) -> None:
    # Closed port: there is no response to translate, only exhausted attempts.
    transport = Transport(
        base_url="http://127.0.0.1:9/v1",
        api_key=TEST_API_KEY,
        timeout=1.0,
        retry=RetryConfig(max_retries=1, backoff_base_seconds=0.01),
        user_agent="veriko-sdk-python/test",
        sleep=sleeps.append,
    )
    client = Veriko(transport=transport)

    with pytest.raises(ConnectionError) as raised:
        client.validate_transfer(
            send_date="2025-03-15",
            amount=15000.50,
            beneficiary_account="012180004412345678",
            tracking_key="MXBA20250315001234",
        )

    assert raised.value.attempts == 2
    assert len(sleeps) == 1
