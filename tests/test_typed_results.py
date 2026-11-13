"""The methods that returned a plain dictionary return typed ones that still behave as one."""

from __future__ import annotations

import json
import pickle
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from conftest import RecordingServer, load_recording
from veriko import ApiList, ApiObject, Veriko, responses

VALIDATION_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"


@dataclass
class Case:
    name: str
    recording: str
    call: Callable[[Veriko], Any]
    model: type[ApiObject]
    # What the dictionary holds: `attributes`, or the whole `data` resource.
    content: str = "attributes"
    # Fields the recording carries that the model leaves to the dictionary (aliases).
    aliases: frozenset[str] = frozenset()


CASES = [
    Case("stats", "validations-stats", lambda c: c.validations.stats(), responses.ValidationStats),
    Case(
        "telegram",
        "telegram-accepted",
        lambda c: c.validations.send_cep_to_telegram(VALIDATION_ID),
        responses.TelegramQueued,
    ),
    Case("bin", "bin-lookup", lambda c: c.catalog.bin_lookup("411111"), responses.BinLookup),
    Case(
        "banxico-status",
        "banxico-status",
        lambda c: c.catalog.banxico_status(),
        responses.BanxicoStatus,
    ),
    Case(
        "banxico-series",
        "banxico-timeseries",
        lambda c: c.catalog.banxico_timeseries(),
        responses.BanxicoTimeseries,
    ),
    Case("usage-history", "usage-history", lambda c: c.usage.history(), responses.UsageHistory),
    Case(
        "usage-breakdown",
        "usage-breakdown",
        lambda c: c.usage.breakdown(),
        responses.UsageBreakdown,
    ),
    Case("usage-limits", "usage-limits", lambda c: c.usage.limits(), responses.UsageLimits),
    Case("usage-heatmap", "usage-heatmap", lambda c: c.usage.heatmap(), responses.UsageHeatmap),
    Case("api-usage", "api-usage", lambda c: c.usage.api_usage(), responses.ApiUsage, "data"),
    Case(
        "profile",
        "account-profile",
        lambda c: c.account.my_profile(),
        responses.UserProfile,
        aliases=frozenset({"id"}),
    ),
    Case(
        "retry-policy",
        "account-retry-policy",
        lambda c: c.account.get_my_retry_policy(),
        responses.SavedRetryPolicy,
    ),
    Case(
        "retry-policy-update",
        "account-retry-policy-updated",
        lambda c: c.account.update_my_retry_policy({"enabled": True}),
        responses.SavedRetryPolicy,
    ),
    Case(
        "dashboard",
        "dashboard-summary",
        lambda c: c.dashboard.get_summary(),
        responses.DashboardSummary,
    ),
    Case(
        "comparison",
        "public-plan-comparison",
        lambda c: c.plans.comparison(),
        responses.PlanComparison,
        "data",
    ),
    Case(
        "overview", "insights-overview", lambda c: c.insights.overview(), responses.InsightsOverview
    ),
    Case("trends", "insights-trends", lambda c: c.insights.trends(), responses.InsightsTrends),
    Case(
        "top-banks",
        "insights-top-banks",
        lambda c: c.insights.top_banks(),
        responses.InsightsTopBanks,
    ),
    Case(
        "top-beneficiaries",
        "insights-top-beneficiaries",
        lambda c: c.insights.top_beneficiaries(),
        responses.InsightsTopBeneficiaries,
    ),
    Case(
        "finance-summary",
        "finance-summary",
        lambda c: c.finance.summary(month="2026-04"),
        responses.FinanceSummary,
    ),
    Case(
        "finance-preview",
        "finance-monthly",
        lambda c: c.finance.monthly(month="2026-04", format="preview"),
        responses.FinancePreview,
    ),
    Case(
        "billing",
        "billing-subscription",
        lambda c: c.billing.subscription(),
        responses.BillingSubscription,
    ),
    Case(
        "cep-export-create",
        "finance-cep-export-queued",
        lambda c: c.finance.create_cep_export(from_="2026-04-01", to="2026-04-30"),
        responses.CepExport,
        "data",
    ),
    Case(
        "cep-export-get",
        "finance-cep-export-ready",
        lambda c: c.finance.get_cep_export(VALIDATION_ID),
        responses.CepExport,
        "data",
    ),
]


def _expected(recording: str, content: str) -> dict[str, Any]:
    data: dict[str, Any] = load_recording(recording)["json"]["data"]
    return data if content == "data" else data["attributes"]


@pytest.fixture(params=CASES, ids=[case.name for case in CASES])
def served(
    request: pytest.FixtureRequest, client: Veriko, server: RecordingServer
) -> tuple[Case, Any, dict[str, Any]]:
    case: Case = request.param
    server.enqueue_recording(case.recording)
    return case, case.call(client), _expected(case.recording, case.content)


def test_the_result_is_the_typed_model_and_still_a_dictionary(
    served: tuple[Case, Any, dict[str, Any]],
) -> None:
    case, result, expected = served

    assert type(result) is case.model
    assert isinstance(result, dict)
    assert result == expected
    assert expected == result
    assert dict(result) == expected
    assert list(result) == list(expected)
    assert len(result) == len(expected)
    assert json.dumps(result, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_dictionary_reads_work_as_before(served: tuple[Case, Any, dict[str, Any]]) -> None:
    _, result, expected = served

    for key, value in expected.items():
        assert key in result
        assert result[key] == value
        assert result.get(key) == value
    assert "not-a-field" not in result
    assert result.get("not-a-field") is None
    assert set(result.keys()) == set(expected)
    assert list(result.items()) == list(expected.items())


def test_declared_fields_read_as_attributes(served: tuple[Case, Any, dict[str, Any]]) -> None:
    case, result, expected = served
    source = expected.get("attributes") if case.model._reads == "attributes" else expected

    assert isinstance(source, dict)
    for name in case.model._fields:
        key = case.model._keys.get(name, name)
        value = getattr(result, name)
        if key not in source or source[key] is None:
            assert value is None
        elif name in case.model._nested:
            many = case.model._nested[name][1]
            converted = [dict(item) for item in value] if many else dict(value)
            assert converted == source[key]
        else:
            assert value == source[key]


def test_the_recording_is_covered_by_the_declared_fields(
    served: tuple[Case, Any, dict[str, Any]],
) -> None:
    case, _, expected = served
    source = expected.get("attributes") if case.model._reads == "attributes" else expected
    declared = {case.model._keys.get(name, name) for name in case.model._fields}

    assert isinstance(source, dict)
    assert set(source) - declared - case.aliases == set()


def test_the_parts_of_the_response_outside_the_dictionary_are_kept(
    served: tuple[Case, Any, dict[str, Any]],
) -> None:
    _, result, _ = served

    assert result.response_info is not None
    assert result.response_info.status in (200, 202)
    assert isinstance(result.meta, dict)
    assert result.raw["data"] is not None


def test_it_survives_a_copy_and_a_pickle(served: tuple[Case, Any, dict[str, Any]]) -> None:
    _, result, expected = served

    clone = pickle.loads(pickle.dumps(result))

    assert type(clone) is type(result)
    assert clone == expected
    assert clone.id == result.id


def test_an_unknown_attribute_is_an_attribute_error(
    served: tuple[Case, Any, dict[str, Any]],
) -> None:
    _, result, _ = served

    with pytest.raises(AttributeError):
        result.not_a_field  # noqa: B018


# ── Details of some models ─────────────────────────────────────────────────


def test_the_profile_reads_its_nested_account_data(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("account-profile")

    profile = client.account.my_profile()

    assert profile.email == profile["email"]
    assert profile.id == "b1e4a2c3-1234-4abc-8def-000000000001"
    assert isinstance(profile.two_factor, dict)


def test_the_public_plans_are_a_list_of_typed_resources(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("public-plans")

    plans = client.plans.list_public()

    assert isinstance(plans, ApiList)
    assert isinstance(plans, list)
    plan = plans[0]
    assert isinstance(plan, responses.PublicPlan)
    assert plan["attributes"]["slug"] == plan.slug == "free"
    assert plan.id == plan["id"] == "free"
    assert all(isinstance(feature, responses.PlanFeature) for feature in plan.features)
    assert plan.features[0].text_en == plan["attributes"]["features"][0]["text_en"]
    assert plans.response_info is not None
    assert isinstance(plans.meta, dict)


def test_the_api_usage_quota_is_a_nested_model(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("api-usage")

    usage = client.usage.api_usage()

    assert isinstance(usage.quota, responses.PlanQuota)
    assert usage.quota.remaining == usage["quota"]["remaining"] == 580
    assert usage.requests_today == 42


def test_the_dashboard_keeps_its_relationships(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("dashboard-summary")

    summary = client.dashboard.summary()

    assert "recent_validations" in summary.relationships
    assert summary.total_validations == 1284


def test_a_cep_export_in_a_list_is_typed(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("finance-cep-exports")

    exports = client.finance.list_cep_exports()

    assert isinstance(exports, ApiList)
    assert all(isinstance(export, responses.CepExport) for export in exports)
    assert exports[0].id == exports[0]["id"] == VALIDATION_ID
    assert exports[0].status == exports[0]["attributes"]["status"]
    assert exports[0].to == exports[0]["attributes"]["to"]


def test_a_cep_export_status_decides_whether_it_is_terminal() -> None:
    def export(status: str) -> responses.CepExport:
        return responses.CepExport({"attributes": {"status": status}})

    assert [export(s).is_terminal for s in responses.CEP_EXPORT_STATUSES] == [
        False,
        False,
        True,
        True,
        True,
    ]
    assert export("ready").is_ready
    assert not export("failed").is_ready
    assert export("ready").download_path is None


def test_a_model_built_by_hand_reads_none_for_what_it_lacks() -> None:
    stats = responses.ValidationStats({"total": 3})

    assert stats.total == 3
    assert getattr(stats, "valid") is None  # noqa: B009
    assert stats.id is None
    assert stats.meta == {}
    assert stats == {"total": 3}
