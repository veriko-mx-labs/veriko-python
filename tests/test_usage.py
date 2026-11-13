"""The `client.usage` family: consumption, limits and the activity log."""

from __future__ import annotations

import pytest

from conftest import RecordingServer
from veriko import ConfigurationError, UsageSummary, Veriko


def test_the_quota_of_the_current_plan(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-summary")

    summary = client.usage.summary()

    assert server.requests[0].path == "/v1/usage/summary"
    assert summary.plan_slug == "basic"
    assert summary.limit == 1000
    assert summary.used == 420
    assert summary.remaining == 580
    assert summary.used_percent == 42
    assert summary.tone == "ok"


def test_the_trial_quota_is_not_replenished() -> None:
    summary = UsageSummary.from_response(
        {
            "data": {
                "type": "usage_summary",
                "attributes": {
                    "plan_slug": "guest",
                    "limit": 5,
                    "used": 3,
                    "remaining": 2,
                    "quota_kind": "trial",
                    "renews": False,
                },
            }
        }
    )

    assert summary.quota_kind == "trial"
    assert summary.renews is False
    assert summary.remaining == 2


def test_without_quota_kind_the_fields_stay_none(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-summary")

    summary = client.usage.summary()

    assert summary.quota_kind is None
    assert summary.renews is None


def test_the_monthly_history(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-history")

    history = client.usage.history(months=6)

    assert "months=6" in server.requests[0].path
    assert history["months"] == 6
    assert history["history"][0]["period"] == "2026-04"
    assert history["history"][0]["used"] == 420


def test_the_breakdown_by_operation(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-breakdown")

    breakdown = client.usage.breakdown(period="2026-04")

    assert "period=2026-04" in server.requests[0].path
    assert breakdown["period"] == "2026-04"
    assert [op["operation"] for op in breakdown["operations"]] == ["validation", "validation_ocr"]


def test_the_rate_limits(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-limits")

    limits = client.usage.limits()

    assert server.requests[0].path == "/v1/usage/limits"
    assert limits["rate_limits"]["api"]["ip_per_minute"] == 60
    assert isinstance(limits["notes"], list)


def test_the_heatmap(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("usage-heatmap")

    mapping = client.usage.heatmap(days=30)

    assert "days=30" in server.requests[0].path
    assert mapping["days"] == 30
    assert mapping["max_count"] == 48
    assert mapping["buckets"][0]["count"] == 48


def test_the_api_usage_metrics(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("api-usage")

    metrics = client.usage.api_usage()

    assert server.requests[0].path == "/v1/api/usage"
    assert metrics["requests_today"] == 42
    assert metrics["quota"]["remaining"] == 580


def test_export_the_activity_log(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("api-usage-export-csv")

    export = client.usage.export(format="csv", from_="2026-05-01", to="2026-05-17")

    route = server.requests[0].path
    assert "format=csv" in route
    assert "from=2026-05-01" in route
    assert "to=2026-05-17" in route
    assert export.filename == "actividad_api_2026-05-16.csv"
    assert export.content.startswith(b"fecha,hora,metodo")


def test_an_export_format_outside_the_list(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(ConfigurationError):
        client.usage.export(format="pdf")

    assert server.requests == []
