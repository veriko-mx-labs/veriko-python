"""Construcción del cliente: clave, raíz de la API y ausencia de red."""

from __future__ import annotations

import pytest

from conftest import RecordingServer
from veriko import (
    DEFAULT_BASE_URL,
    ConfigurationError,
    ConnectionError,
    RetryConfig,
    Veriko,
    __version__,
)
from veriko._http import Transport


def test_sin_clave_de_api_falla_al_construir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERIKO_API_KEY", raising=False)

    with pytest.raises(ConfigurationError) as raised:
        Veriko()

    assert "VERIKO_API_KEY" in str(raised.value)


def test_la_clave_se_lee_del_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERIKO_API_KEY", "veriko_del_entorno")

    client = Veriko()

    assert client.base_url == DEFAULT_BASE_URL


def test_la_raiz_de_la_api_se_puede_apuntar_a_otro_sitio() -> None:
    client = Veriko(api_key="veriko_x", base_url="http://127.0.0.1:9999/v1/")

    assert client.base_url == "http://127.0.0.1:9999/v1"


def test_la_peticion_se_identifica_con_el_user_agent_del_sdk(
    server: RecordingServer,
) -> None:
    client = Veriko(api_key="veriko_x", base_url=server.base_url)
    server.enqueue_recording("validate-valid")

    client.validate_transfer(fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234")

    agente = server.requests[0].header("user-agent")
    assert agente is not None
    assert agente.startswith("veriko-python/" + __version__)


def test_el_idioma_de_los_mensajes_se_negocia_por_cabecera(
    server: RecordingServer,
) -> None:
    client = Veriko(api_key="veriko_x", base_url=server.base_url, accept_language="en")
    server.enqueue_recording("validate-valid")

    client.validate_transfer(fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234")

    assert server.requests[0].header("accept-language") == "en"


def test_sin_servidor_al_otro_lado_se_lanza_un_error_de_conexion(
    sleeps: list[float],
) -> None:
    # Puerto cerrado: no hay respuesta que traducir, sólo intentos agotados.
    transport = Transport(
        base_url="http://127.0.0.1:9/v1",
        api_key="veriko_x",
        timeout=1.0,
        retry=RetryConfig(max_retries=1, backoff_base_seconds=0.01),
        user_agent="veriko-python/prueba",
        sleep=sleeps.append,
    )
    client = Veriko(transport=transport)

    with pytest.raises(ConnectionError) as raised:
        client.validate_transfer(
            fecha="2025-03-15", monto=15000.50, clave_rastreo="MXBA20250315001234"
        )

    assert raised.value.attempts == 2
    assert len(sleeps) == 1
