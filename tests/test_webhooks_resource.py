"""Las familias `client.webhooks` y `client.catalog`."""

from __future__ import annotations

import pytest

from conftest import RecordingServer
from veriko import ConfigurationError, InvalidRequestError, Veriko

WEBHOOK_ID = "9f8e7d6c-5b4a-3210-fedc-ba9876543210"


# ── Endpoints ───────────────────────────────────────────────────────────────


def test_registrar_un_endpoint_devuelve_el_secreto(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-created")

    endpoint = client.webhooks.create(
        url="https://miapp.example.com/hooks/pagos", events=["validation.completed"]
    )

    assert server.requests[0].method == "POST"
    assert server.requests[0].json() == {
        "url": "https://miapp.example.com/hooks/pagos",
        "events": ["validation.completed"],
    }
    # El secreto viaja una sola vez, al registrar.
    assert endpoint.secret == "whsec_3f9a1c7e5d2b48a6b0c1d2e3f4a5b6c7"
    assert endpoint.id == WEBHOOK_ID
    assert endpoint.status == "active"


def test_registrar_sin_eventos_no_llega_a_la_api(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.webhooks.create(url="https://miapp.example.com/hooks", events=[])

    assert raised.value.code == "events_required"
    assert server.requests == []


def test_listar_endpoints_no_trae_secretos(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhooks-list")

    endpoints = client.webhooks.list()

    assert len(endpoints) == 2
    assert all(endpoint.secret is None for endpoint in endpoints)
    assert endpoints[0].is_auto_disabled is False
    assert endpoints[1].is_auto_disabled is True
    assert endpoints[1].consecutive_failures == 3


def test_cambiar_los_eventos_suscritos(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-updated")

    endpoint = client.webhooks.update(
        WEBHOOK_ID, events=["validation.completed", "validation_import.completed"]
    )

    assert server.requests[0].method == "PUT"
    assert server.requests[0].json() == {
        "events": ["validation.completed", "validation_import.completed"]
    }
    assert len(endpoint.events) == 2


def test_reactivar_un_endpoint_apagado(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-updated")

    client.webhooks.update(WEBHOOK_ID, status="active")

    assert server.requests[0].json() == {"status": "active"}


def test_un_cambio_vacio_no_llega_a_la_api(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.webhooks.update(WEBHOOK_ID)

    assert raised.value.code == "empty_update"
    assert server.requests == []


def test_retirar_un_endpoint(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("no-content")

    client.webhooks.delete(WEBHOOK_ID)

    assert server.requests[0].method == "DELETE"
    assert server.requests[0].path == "/v1/webhooks/" + WEBHOOK_ID


def test_evento_de_prueba_entregado(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-test-ok")

    resultado = client.webhooks.test(WEBHOOK_ID)

    assert resultado.delivered is True
    assert resultado.http_status == 200
    assert resultado.error is None


def test_evento_de_prueba_fallido_explica_el_motivo(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("webhook-test-failed")

    resultado = client.webhooks.test(WEBHOOK_ID)

    assert resultado.delivered is False
    assert resultado.error is not None
    assert "tiempo límite" in resultado.error


def test_rotar_el_secreto_devuelve_el_nuevo(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-secret-rotated")

    endpoint = client.webhooks.regenerate_secret(WEBHOOK_ID)

    assert endpoint.secret == "whsec_a1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert server.requests[0].path.endswith("/regenerate-secret")


# ── Entregas ────────────────────────────────────────────────────────────────


def test_entregas_de_un_endpoint(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-deliveries")

    pagina = client.webhooks.deliveries(WEBHOOK_ID, per_page=2)

    assert server.requests[0].path.startswith("/v1/webhooks/" + WEBHOOK_ID + "/deliveries")
    assert len(pagina) == 2
    assert pagina[0].status == "success"
    assert pagina[0].response_status == 200
    assert pagina[1].status == "retrying"
    assert pagina[1].error_message == "Service Unavailable"


def test_entregas_de_todos_los_endpoints(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-deliveries")

    client.webhooks.deliveries(status="retrying", event_type="validation.completed")

    ruta = server.requests[0].path
    assert ruta.startswith("/v1/webhooks/deliveries")
    assert "status=retrying" in ruta
    assert "event_type=validation.completed" in ruta


def test_recorrer_las_entregas(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-deliveries")

    todas = list(client.webhooks.iter_deliveries(WEBHOOK_ID))

    # Una sola página: no hay segunda petición.
    assert len(todas) == 2
    assert len(server.requests) == 1


def test_exportar_las_entregas(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("deliveries-export-csv")

    export = client.webhooks.export_deliveries(WEBHOOK_ID, format="csv")

    assert export.filename == "entregas-2025-03.csv"
    assert export.content.startswith(b"id,event_type,status")


def test_exportar_con_un_formato_fuera_de_la_lista(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(ConfigurationError):
        client.webhooks.export_deliveries(format="pdf")

    assert server.requests == []


# ── Catálogos ───────────────────────────────────────────────────────────────


def test_catalogo_de_bancos(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("banks")

    bancos = client.catalog.banks()

    assert len(bancos) == 2
    assert bancos[0].code == "40002"
    assert bancos[1].name == "BBVA MEXICO"


def test_el_catalogo_admite_etag(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("banks")

    client.catalog.banks(if_none_match='W/"banks-2026-09-18"')

    assert server.requests[0].header("if-none-match") == 'W/"banks-2026-09-18"'


def test_banco_emisor_de_una_tarjeta(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("bin-lookup")

    resultado = client.catalog.bin_lookup("455632")

    assert server.requests[0].path == "/v1/public/bin-lookup/455632"
    assert resultado["bank_name"] == "BBVA MEXICO"


def test_estado_del_servicio_de_banxico(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("banxico-status")

    estado = client.catalog.banxico_status()

    assert estado["status"] == "operational"
    assert estado["latency_ms"] == 820


def test_serie_temporal_del_servicio(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("banxico-timeseries")

    serie = client.catalog.banxico_timeseries(metric="latency", window="24h")

    assert "metric=latency" in server.requests[0].path
    assert "window=24h" in server.requests[0].path
    assert len(serie["points"]) == 2
