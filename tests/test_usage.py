"""La familia `client.usage`: consumo, límites y registro de actividad."""

from __future__ import annotations

import pytest

from conftest import RecordingServer
from veriko import ConfigurationError, Veriko


def test_la_cuota_del_plan_en_curso(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-summary")

    summary = client.usage.summary()

    assert server.requests[0].path == "/v1/usage/summary"
    assert summary.plan_slug == "basic"
    assert summary.limit == 1000
    assert summary.used == 420
    assert summary.remaining == 580
    assert summary.used_percent == 42
    assert summary.tone == "ok"


def test_el_historial_mensual(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-history")

    historial = client.usage.history(months=6)

    assert "months=6" in server.requests[0].path
    assert historial["months"] == 6
    assert historial["history"][0]["period"] == "2026-04"
    assert historial["history"][0]["used"] == 420


def test_el_desglose_por_operacion(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-breakdown")

    desglose = client.usage.breakdown(period="2026-04")

    assert "period=2026-04" in server.requests[0].path
    assert desglose["period"] == "2026-04"
    assert [op["operation"] for op in desglose["operations"]] == ["validation", "validation_ocr"]


def test_los_limites_de_tasa(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-limits")

    limites = client.usage.limits()

    assert server.requests[0].path == "/v1/usage/limits"
    assert limites["rate_limits"]["api"]["ip_per_minute"] == 60
    assert isinstance(limites["notes"], list)


def test_el_mapa_de_calor(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-heatmap")

    mapa = client.usage.heatmap(days=30)

    assert "days=30" in server.requests[0].path
    assert mapa["days"] == 30
    assert mapa["max_count"] == 48
    assert mapa["buckets"][0]["count"] == 48


def test_las_metricas_de_uso_de_la_api(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("api-usage")

    metricas = client.usage.api_usage()

    assert server.requests[0].path == "/v1/api/usage"
    assert metricas["requests_today"] == 42
    assert metricas["quota"]["remaining"] == 580


def test_exportar_el_registro_de_actividad(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("api-usage-export-csv")

    export = client.usage.export(format="csv", from_="2026-05-01", to="2026-05-17")

    ruta = server.requests[0].path
    assert "format=csv" in ruta
    assert "from=2026-05-01" in ruta
    assert "to=2026-05-17" in ruta
    assert export.filename == "actividad_api_2026-05-16.csv"
    assert export.content.startswith(b"fecha,hora,metodo")


def test_un_formato_de_exportacion_fuera_de_la_lista(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(ConfigurationError):
        client.usage.export(format="pdf")

    assert server.requests == []
