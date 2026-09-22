"""Las familias `client.webhooks` y `client.catalog`."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import RecordingServer
from veriko import APIError, ConfigurationError, InvalidRequestError, Veriko

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
    assert endpoint.secret == "a0b1c2d3e4f5061728394a5b6c7d8e9f00112233445566778899aabbccddeeff"
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

    assert endpoint.secret == "ffeeddccbbaa99887766554433221100f9e8d7c6b5a4938271605f4e3d2c1b0a"
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

    assert len(bancos) == 3
    assert bancos[0].code == "40012"
    assert bancos[0].name == "BBVA MEXICO"
    assert bancos[0].aliases == ["bancomer", "bbva bancomer"]
    assert bancos[1].aliases == []
    # El catálogo trae su ETag para la lectura condicional siguiente.
    assert bancos.etag == '"a1b2c3d4e5f6"'


def test_el_catalogo_admite_etag(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("banks")

    client.catalog.banks(if_none_match='W/"banks-2026-09-18"')

    assert server.requests[0].header("if-none-match") == 'W/"banks-2026-09-18"'


def test_banco_emisor_de_una_tarjeta(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("bin-lookup")

    resultado = client.catalog.bin_lookup("455632")

    assert server.requests[0].path == "/v1/public/bin-lookup/455632"
    assert resultado["bank_name"] == "BBVA MEXICO"
    assert resultado["banxico_code"] == "40012"
    assert resultado["card_brand"] == "VISA"


def test_estado_del_servicio_de_banxico(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("banxico-status")

    estado = client.catalog.banxico_status()

    assert estado["status"] == "operational"
    assert estado["status_label"] == "Operativo"
    assert estado["last_verified_at"] == "2026-04-11T15:30:00Z"


def test_serie_temporal_del_servicio(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("banxico-timeseries")

    serie = client.catalog.banxico_timeseries(metric="probe_latency", window="24h")

    assert "metric=probe_latency" in server.requests[0].path
    assert "window=24h" in server.requests[0].path
    assert serie["unit"] == "ms"
    assert len(serie["points"]) == 2
    assert serie["points"][0] == {"ts": "2026-04-11T14:00:00Z", "value": 1523}


# ── Lo que el spec pide ─────────────────────────────────────────────────────


def _consulta(server: RecordingServer, indice: int) -> dict[str, list[str]]:
    return parse_qs(urlsplit(server.requests[indice].path).query)


def test_la_etiqueta_viaja_como_description(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-created")

    client.webhooks.create(
        url="https://miapp.example.com/hooks/pagos",
        events=["validation.completed"],
        description="Pagos de contado",
    )

    assert server.requests[0].json()["description"] == "Pagos de contado"


def test_borrar_la_etiqueta_con_none(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-updated")

    client.webhooks.update(WEBHOOK_ID, description=None)

    assert server.requests[0].json() == {"description": None}


def test_vaciar_los_eventos_no_llega_a_la_api(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.webhooks.update(WEBHOOK_ID, events=[])

    assert raised.value.code == "events_required"
    assert server.requests == []


def test_un_endpoint_con_filtro_consulta_el_listado_global(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("webhook-deliveries")

    client.webhooks.deliveries(WEBHOOK_ID, status="failed")

    # La ruta por endpoint no admite `status`: el filtro se perdería en silencio.
    assert server.requests[0].path.startswith("/v1/webhooks/deliveries?")
    consulta = _consulta(server, 0)
    assert consulta["endpoint_id"] == [WEBHOOK_ID]
    assert consulta["status"] == ["failed"]


def test_recorrer_las_entregas_en_dos_paginas(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-deliveries-page1")
    server.enqueue_recording("webhook-deliveries-page2")

    todas = list(client.webhooks.iter_deliveries(WEBHOOK_ID, per_page=2))

    assert [entrega.id for entrega in todas] == ["1041", "1042", "1043"]
    assert len(server.requests) == 2
    assert _consulta(server, 1)["page"] == ["2"]
    assert server.requests[1].path.startswith("/v1/webhooks/" + WEBHOOK_ID + "/deliveries?")


def test_las_entregas_traen_los_campos_del_spec(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-deliveries")

    pagina = client.webhooks.deliveries(WEBHOOK_ID)

    reintento = pagina[1]
    assert reintento.attempt == 1
    assert reintento.endpoint_id == WEBHOOK_ID
    assert reintento.next_retry_at == "2025-03-15T14:30:12Z"
    assert reintento.validation_id == "f47ac10b-58cc-4372-a567-0e02b2c3d479"


def test_exportar_las_entregas_de_todos_con_filtros(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("deliveries-export-csv")

    client.webhooks.export_deliveries(status="failed", limit=50)

    assert server.requests[0].path.startswith("/v1/webhooks/deliveries/export?")
    consulta = _consulta(server, 0)
    assert consulta["status"] == ["failed"]
    assert consulta["limit"] == ["50"]
    assert "endpoint_id" not in consulta


def test_los_endpoints_traen_los_campos_del_spec(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhooks-list")

    endpoints = client.webhooks.list()

    assert endpoints[0].description == "Pagos de contado"
    assert endpoints[0].secret_hint == "...b6c7"
    assert endpoints[1].last_delivery_at == "2025-03-15T14:25:12Z"
    assert endpoints[1].updated_at == "2025-02-01T12:00:00Z"


def test_un_catalogo_sin_cambios_se_lanza_como_304(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("not-modified")

    with pytest.raises(APIError) as raised:
        client.catalog.banks(if_none_match='"a1b2c3d4e5f6"')

    assert raised.value.status == 304
