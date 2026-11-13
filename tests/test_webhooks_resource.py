"""The `client.webhooks` and `client.catalog` families."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import RecordingServer
from veriko import APIError, ConfigurationError, InvalidRequestError, NotFoundError, Veriko

WEBHOOK_ID = "9f8e7d6c-5b4a-3210-fedc-ba9876543210"


# ── Endpoints ───────────────────────────────────────────────────────────────


def test_registering_an_endpoint_returns_the_secret(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("webhook-created")

    endpoint = client.webhooks.create(
        url="https://miapp.example.com/hooks/pagos", events=["validation.completed"]
    )

    assert server.requests[0].method == "POST"
    assert server.requests[0].json() == {
        "url": "https://miapp.example.com/hooks/pagos",
        "events": ["validation.completed"],
    }
    # The secret travels once, on registration.
    assert endpoint.secret == "a0b1c2d3e4f5061728394a5b6c7d8e9f00112233445566778899aabbccddeeff"
    assert endpoint.id == WEBHOOK_ID
    assert endpoint.status == "active"


def test_registering_without_events_does_not_reach_the_api(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.webhooks.create(url="https://miapp.example.com/hooks", events=[])

    assert raised.value.code == "events_required"
    assert server.requests == []


def test_listing_endpoints_carries_no_secrets(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhooks-list")

    endpoints = client.webhooks.list()

    assert len(endpoints) == 2
    assert all(endpoint.secret is None for endpoint in endpoints)
    assert endpoints[0].is_auto_disabled is False
    assert endpoints[1].is_auto_disabled is True
    assert endpoints[1].consecutive_failures == 3


def test_change_the_subscribed_events(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-updated")

    endpoint = client.webhooks.update(
        WEBHOOK_ID, events=["validation.completed", "validation_import.completed"]
    )

    assert server.requests[0].method == "PUT"
    assert server.requests[0].json() == {
        "events": ["validation.completed", "validation_import.completed"]
    }
    assert len(endpoint.events) == 2


def test_reactivate_a_turned_off_endpoint(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-updated")

    client.webhooks.update(WEBHOOK_ID, status="active")

    assert server.requests[0].json() == {"status": "active"}


def test_an_empty_update_does_not_reach_the_api(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.webhooks.update(WEBHOOK_ID)

    assert raised.value.code == "no_valid_fields"
    assert server.requests == []


def test_remove_an_endpoint(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("no-content")

    client.webhooks.delete(WEBHOOK_ID)

    assert server.requests[0].method == "DELETE"
    assert server.requests[0].path == "/v1/webhooks/" + WEBHOOK_ID


def test_test_event_delivered(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-test-ok")

    result = client.webhooks.test(WEBHOOK_ID)

    assert result.delivered is True
    assert result.http_status == 200
    assert result.error is None


def test_failed_test_event_explains_the_reason(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-test-failed")

    result = client.webhooks.test(WEBHOOK_ID)

    assert result.delivered is False
    assert result.error is not None
    assert "timeout" in result.error


def test_rotating_the_secret_returns_the_new_one(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-secret-rotated")

    endpoint = client.webhooks.regenerate_secret(WEBHOOK_ID)

    assert endpoint.secret == "ffeeddccbbaa99887766554433221100f9e8d7c6b5a4938271605f4e3d2c1b0a"
    assert server.requests[0].path.endswith("/regenerate-secret")


# ── Entregas ────────────────────────────────────────────────────────────────


def test_deliveries_of_one_endpoint(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-deliveries")

    page = client.webhooks.deliveries(WEBHOOK_ID, per_page=2)

    assert server.requests[0].path.startswith("/v1/webhooks/" + WEBHOOK_ID + "/deliveries")
    assert len(page) == 2
    assert page[0].status == "success"
    assert page[0].response_status == 200
    assert page[1].status == "retrying"
    assert page[1].error_message == "Service Unavailable"


def test_deliveries_of_all_endpoints(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-deliveries")

    client.webhooks.deliveries(status="retrying", event_type="validation.completed")

    route = server.requests[0].path
    assert route.startswith("/v1/webhooks/deliveries")
    assert "status=retrying" in route
    assert "event_type=validation.completed" in route


def test_iterate_the_deliveries(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-deliveries")

    all_ = list(client.webhooks.iter_deliveries(WEBHOOK_ID))

    # A single page: there is no second request.
    assert len(all_) == 2
    assert len(server.requests) == 1


def test_export_the_deliveries(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("deliveries-export-csv")

    export = client.webhooks.export_deliveries(WEBHOOK_ID, format="csv")

    assert export.filename == "entregas-2025-03.csv"
    assert export.content.startswith(b"id,event_type,status")


def test_export_with_a_format_outside_the_list(client: Veriko, server: RecordingServer) -> None:
    with pytest.raises(ConfigurationError):
        client.webhooks.export_deliveries(format="pdf")

    assert server.requests == []


# ── Catalogs ────────────────────────────────────────────────────────────────


def test_bank_catalog(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("banks")

    banks = client.catalog.banks()

    assert len(banks) == 3
    assert banks[0].code == "40012"
    assert banks[0].name == "BBVA MEXICO"
    assert banks[0].aliases == ["bancomer", "bbva bancomer"]
    assert banks[1].aliases == []
    # The catalog carries its ETag for the next conditional read.
    assert banks.etag == '"a1b2c3d4e5f6"'


def test_the_catalog_supports_etag(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("banks")

    client.catalog.banks(if_none_match='W/"banks-2026-09-18"')

    assert server.requests[0].header("if-none-match") == 'W/"banks-2026-09-18"'


def test_issuing_bank_of_a_card(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("bin-lookup")

    result = client.catalog.bin_lookup("455632")

    assert server.requests[0].path == "/v1/public/bin-lookup/455632"
    assert result["bank_name"] == "BBVA MEXICO"
    assert result["banxico_code"] == "40012"
    assert result["card_brand"] == "VISA"


def test_spei_calendar_of_a_year(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("spei-calendar")

    calendar = client.catalog.spei_calendar(2026)

    assert server.requests[0].path == "/v1/public/spei-calendar/2026"
    assert calendar.year == 2026
    assert calendar.non_business_days[0] == "2026-01-01"
    assert len(calendar.non_business_days) == 10
    assert calendar.source["issuer"] == "CNBV"
    assert calendar.source["published_on"] == "2025-12-10"
    assert calendar.covered_years == [2024, 2025, 2026]
    assert calendar.etag == '"a1b2c3d4e5f6"'


def test_the_spei_calendar_supports_etag(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("not-modified")

    with pytest.raises(APIError) as raised:
        client.catalog.spei_calendar(2026, if_none_match='"a1b2c3d4e5f6"')

    assert server.requests[0].header("if-none-match") == '"a1b2c3d4e5f6"'
    assert raised.value.status == 304


def test_a_year_without_a_spei_calendar_is_404(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("spei-calendar-404")

    with pytest.raises(NotFoundError) as raised:
        client.catalog.spei_calendar(2030)

    assert raised.value.code == "spei_calendar_year_not_covered"


def test_banxico_service_status(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("banxico-status")

    state = client.catalog.banxico_status()

    assert state["status"] == "operational"
    assert state["status_label"] == "Operativo"
    assert state["last_verified_at"] == "2026-04-11T15:30:00Z"


def test_service_time_series(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("banxico-timeseries")

    series = client.catalog.banxico_timeseries(metric="probe_latency", window="24h")

    assert "metric=probe_latency" in server.requests[0].path
    assert "window=24h" in server.requests[0].path
    assert series["unit"] == "ms"
    assert len(series["points"]) == 2
    assert series["points"][0] == {"ts": "2026-04-11T14:00:00Z", "value": 1523}


# ── What the spec requires ──────────────────────────────────────────────────


def _query(server: RecordingServer, index: int) -> dict[str, list[str]]:
    return parse_qs(urlsplit(server.requests[index].path).query)


def test_the_label_travels_as_description(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-created")

    client.webhooks.create(
        url="https://miapp.example.com/hooks/pagos",
        events=["validation.completed"],
        description="Cash payments",
    )

    assert server.requests[0].json()["description"] == "Cash payments"


def test_clear_the_label_with_none(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-updated")

    client.webhooks.update(WEBHOOK_ID, description=None)

    assert server.requests[0].json() == {"description": None}


def test_emptying_the_events_does_not_reach_the_api(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        client.webhooks.update(WEBHOOK_ID, events=[])

    assert raised.value.code == "events_required"
    assert server.requests == []


def test_an_endpoint_with_a_filter_queries_the_global_listing(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("webhook-deliveries")

    client.webhooks.deliveries(WEBHOOK_ID, status="failed")

    # The per-endpoint route does not accept `status`: the filter would be silently lost.
    assert server.requests[0].path.startswith("/v1/webhooks/deliveries?")
    query = _query(server, 0)
    assert query["endpoint_id"] == [WEBHOOK_ID]
    assert query["status"] == ["failed"]


def test_multi_value_filters_travel_comma_separated(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("webhook-deliveries")

    client.webhooks.deliveries(
        status=["failed", "retrying"],
        event_type=["validation.completed", "validation.returned"],
        response_status=["5xx", 404, "none"],
        from_="2026-10-01",
        min_attempt=2,
    )

    query = _query(server, 0)
    assert query["status"] == ["failed,retrying"]
    assert query["event_type"] == ["validation.completed,validation.returned"]
    assert query["response_status"] == ["5xx,404,none"]
    assert query["from"] == ["2026-10-01"]
    assert query["min_attempt"] == ["2"]
    assert "from_" not in query


def test_several_endpoints_query_the_global_listing(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("webhook-deliveries")
    other = "7c9e6679-7425-40de-944b-e07fc1f90ae7"

    client.webhooks.deliveries([WEBHOOK_ID, other])

    assert server.requests[0].path.startswith("/v1/webhooks/deliveries?")
    assert _query(server, 0)["endpoint_id"] == [WEBHOOK_ID + "," + other]


def test_an_endpoint_with_a_new_filter_also_goes_to_the_global_listing(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("webhook-deliveries")

    client.webhooks.deliveries(WEBHOOK_ID, min_response_time_ms=3000)

    assert server.requests[0].path.startswith("/v1/webhooks/deliveries?")
    query = _query(server, 0)
    assert query["endpoint_id"] == [WEBHOOK_ID]
    assert query["min_response_time_ms"] == ["3000"]


def test_iterate_the_deliveries_over_two_pages(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-deliveries-page1")
    server.enqueue_recording("webhook-deliveries-page2")

    all_ = list(client.webhooks.iter_deliveries(WEBHOOK_ID, per_page=2))

    assert [delivery.id for delivery in all_] == ["1041", "1042", "1043"]
    assert len(server.requests) == 2
    assert _query(server, 1)["page"] == ["2"]
    assert server.requests[1].path.startswith("/v1/webhooks/" + WEBHOOK_ID + "/deliveries?")


def test_deliveries_carry_the_spec_fields(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhook-deliveries")

    page = client.webhooks.deliveries(WEBHOOK_ID)

    retry = page[1]
    assert retry.attempt == 1
    assert retry.endpoint_id == WEBHOOK_ID
    assert retry.next_retry_at == "2025-03-15T14:30:12Z"
    assert retry.validation_id == "f47ac10b-58cc-4372-a567-0e02b2c3d479"


def test_export_the_deliveries_of_all_with_filters(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("deliveries-export-csv")

    client.webhooks.export_deliveries(status="failed", limit=50)

    assert server.requests[0].path.startswith("/v1/webhooks/deliveries/export?")
    query = _query(server, 0)
    assert query["status"] == ["failed"]
    assert query["limit"] == ["50"]
    assert "endpoint_id" not in query


def test_export_the_deliveries_of_one_endpoint_with_a_new_filter(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("deliveries-export-csv")

    client.webhooks.export_deliveries(WEBHOOK_ID, q="erp.example.com", to="2026-10-15", limit=20)

    assert server.requests[0].path.startswith("/v1/webhooks/deliveries/export?")
    query = _query(server, 0)
    assert query["endpoint_id"] == [WEBHOOK_ID]
    assert query["q"] == ["erp.example.com"]
    assert query["to"] == ["2026-10-15"]
    assert query["limit"] == ["20"]


def test_export_the_deliveries_of_one_endpoint_without_filters_uses_its_route(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("deliveries-export-csv")

    client.webhooks.export_deliveries(WEBHOOK_ID, limit=20)

    assert server.requests[0].path.startswith("/v1/webhooks/" + WEBHOOK_ID + "/deliveries/export?")
    assert _query(server, 0)["limit"] == ["20"]


def test_endpoints_carry_the_spec_fields(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("webhooks-list")

    endpoints = client.webhooks.list()

    assert endpoints[0].description == "Cash payments"
    assert endpoints[0].secret_hint == "...b6c7"
    assert endpoints[1].last_delivery_at == "2025-03-15T14:25:12Z"
    assert endpoints[1].updated_at == "2025-02-01T12:00:00Z"


def test_a_catalog_without_changes_is_raised_as_304(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("not-modified")

    with pytest.raises(APIError) as raised:
        client.catalog.banks(if_none_match='"a1b2c3d4e5f6"')

    assert raised.value.status == 304
