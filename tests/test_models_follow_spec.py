"""The typed fields of each model exist in the public spec.

A model that reads `attributes.get("short_name")` when the spec says `aliases`
silently returns `None`. Here every model field, except those the SDK adds
(`raw`, `attributes`, `etag`...), must be a property the spec declares for that
resource.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from spec_support import load_spec, response_properties, schema_named, schema_properties
from veriko import responses
from veriko._base import ApiObject
from veriko.models import (
    AccountConflict,
    Bank,
    Beneficiary,
    BeneficiaryImportJob,
    BeneficiaryImportRow,
    BeneficiaryLookup,
    CandidateMatch,
    DuplicateOf,
    PurgePreparation,
    PurgeResult,
    RecheckResult,
    RetryAttempt,
    RetryState,
    SpeiCalendar,
    UsageSummary,
    Validation,
    ValidationSummary,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookTestResult,
)

OWN = {"raw", "attributes"}

# (model, spec schema, where its fields live, fields the SDK adds)
MODELS: list[tuple[type[Any], str, str, set[str]]] = [
    (Validation, "Validation", "attributes", OWN | {"id", "links", "etag", "meta"}),
    (RetryState, "RetryStateFull", "plano", OWN),
    (ValidationSummary, "PublicValidationListItem", "attributes", OWN | {"id"}),
    (RetryAttempt, "RetryAttempt", "plano", OWN),
    (WebhookEndpoint, "WebhookEndpoint", "attributes", OWN | {"id"}),
    (WebhookDelivery, "WebhookDelivery", "attributes", OWN | {"id"}),
    (WebhookTestResult, "SendWebhookTestAttributes", "plano", OWN),
    (Bank, "BankResource", "attributes", OWN),
    (Beneficiary, "Beneficiary", "attributes", OWN | {"id", "reactivated"}),
    (BeneficiaryLookup, "BeneficiaryLookupResource", "attributes", OWN),
    (BeneficiaryImportJob, "BeneficiaryImportJob", "attributes", OWN | {"id"}),
    (BeneficiaryImportRow, "BeneficiaryImportRow", "attributes", OWN | {"id"}),
    (UsageSummary, "UsageSummary", "plano", OWN),
    (SpeiCalendar, "SpeiCalendarResource", "attributes", OWN | {"covered_years", "etag"}),
]


@pytest.mark.parametrize(
    ("model", "schema", "location", "own"),
    MODELS,
    ids=[model.__name__ for model, *_ in MODELS],
)
def test_typed_fields_exist_in_the_spec(
    model: type[Any], schema: str, location: str, own: set[str]
) -> None:
    properties = schema_properties(load_spec()["components"]["schemas"][schema])
    if location == "attributes":
        properties = schema_properties(properties["attributes"])

    fields = {field.name for field in dataclasses.fields(model)} - own

    invented = sorted(fields - set(properties))
    assert not invented, f"{model.__name__} reads fields the spec does not declare: {invented}"


def _attributes(schema: str) -> dict[str, Any]:
    """The attributes of a `data.attributes` resource in a spec response."""
    schemas = load_spec()["components"]["schemas"]
    data = schema_properties(schemas[schema])["data"]
    attributes_: dict[str, Any] = schema_properties(schema_properties(data)["attributes"])
    return attributes_


def _validation_attribute(name: str) -> dict[str, Any]:
    schemas = load_spec()["components"]["schemas"]
    attributes_ = schema_properties(schema_properties(schemas["Validation"])["attributes"])
    return schema_properties(attributes_[name])


NESTED: list[tuple[type[Any], Any, set[str]]] = [
    (DuplicateOf, lambda: _validation_attribute("duplicate_of"), set()),
    (AccountConflict, lambda: _validation_attribute("account_conflict"), set()),
    (CandidateMatch, lambda: _validation_attribute("candidate_match"), set()),
    (
        RecheckResult,
        lambda: schema_properties(load_spec()["components"]["schemas"]["ValidationRecheckMeta"]),
        OWN | {"validation"},
    ),
    (
        PurgePreparation,
        lambda: _attributes("ValidationPurgePrepareResponse"),
        OWN | {"id"},
    ),
    (PurgeResult, lambda: _attributes("ValidationPurgeExecuteResponse"), OWN | {"id"}),
]


@pytest.mark.parametrize(
    ("model", "properties", "own"),
    NESTED,
    ids=[model.__name__ for model, *_ in NESTED],
)
def test_nested_types_exist_in_the_spec(model: type[Any], properties: Any, own: set[str]) -> None:
    declared_names = set(properties())
    fields = {field.name for field in dataclasses.fields(model)} - own

    invented = sorted(fields - declared_names)
    assert not invented, f"{model.__name__} reads fields the spec does not declare: {invented}"
    assert fields == declared_names


def test_reactivated_comes_from_the_meta_the_spec_declares() -> None:
    response = load_spec()["components"]["schemas"]["CreateBeneficiaryResponse"]
    meta = schema_properties(response)["meta"]

    assert schema_properties(meta)["reactivated"]["type"] == "boolean"


# ── The typed dictionaries of `veriko.responses` ───────────────────────────

# (model, method, path of a request, status, where its fields live in the response)
RESPONSE_MODELS: list[tuple[type[ApiObject], str, str, str, str]] = [
    (responses.ValidationStats, "GET", "/validations/stats", "200", "attributes"),
    (responses.TelegramQueued, "POST", "/validations/x/cep/send-telegram", "202", "attributes"),
    (responses.BinLookup, "GET", "/public/bin-lookup/411111", "200", "attributes"),
    (responses.BanxicoStatus, "GET", "/status/banxico", "200", "attributes"),
    (responses.BanxicoTimeseries, "GET", "/status/banxico/timeseries", "200", "attributes"),
    (responses.UsageHistory, "GET", "/usage/history", "200", "attributes"),
    (responses.UsageBreakdown, "GET", "/usage/breakdown", "200", "attributes"),
    (responses.UsageLimits, "GET", "/usage/limits", "200", "attributes"),
    (responses.UsageHeatmap, "GET", "/usage/heatmap", "200", "attributes"),
    (responses.ApiUsage, "GET", "/api/usage", "200", "data"),
    (responses.UserProfile, "GET", "/users/me", "200", "attributes"),
    (responses.SavedRetryPolicy, "GET", "/users/me/retry-policy", "200", "attributes"),
    (responses.SavedRetryPolicy, "PUT", "/users/me/retry-policy", "200", "attributes"),
    (responses.DashboardSummary, "GET", "/summary", "200", "attributes"),
    (responses.BillingSubscription, "GET", "/billing/subscription", "200", "attributes"),
    (responses.PublicPlan, "GET", "/plans/public", "200", "attributes"),
    (responses.PlanComparison, "GET", "/plans/public/comparison", "200", "attributes"),
    (responses.InsightsOverview, "GET", "/insights/overview", "200", "attributes"),
    (responses.InsightsTrends, "GET", "/insights/trends", "200", "attributes"),
    (responses.InsightsTopBanks, "GET", "/insights/top-banks", "200", "attributes"),
    (responses.InsightsTopBeneficiaries, "GET", "/insights/top-beneficiaries", "200", "attributes"),
    (responses.FinanceSummary, "GET", "/finance/summary", "200", "attributes"),
    (responses.FinancePreview, "GET", "/finance/monthly", "200", "attributes"),
    (responses.CepExport, "POST", "/finance/ceps/exports", "202", "attributes"),
    (responses.CepExport, "GET", "/finance/ceps/exports/x", "200", "attributes"),
    (responses.CepExport, "GET", "/finance/ceps/exports", "200", "attributes"),
]

# Nested models and the schema of `components.schemas` they mirror.
NESTED_MODELS: list[tuple[type[ApiObject], str]] = [
    (responses.PlanQuota, "PlanQuotaState"),
    (responses.PlanPrice, "PlanPriceAttributes"),
    (responses.PlanFeature, "PlanFeature"),
    (responses.FinanceTopBank, "FinanceTopBank"),
]

NOT_FIELDS = {"id", "type", "meta", "links", "raw", "response_info"}


def _field_in_spec(model: type[ApiObject], name: str) -> str:
    return model._keys.get(name, name)


@pytest.mark.parametrize(
    ("model", "method", "pathname", "status", "location"),
    RESPONSE_MODELS,
    ids=[f"{model.__name__}-{method}-{path}" for model, method, path, *_ in RESPONSE_MODELS],
)
def test_response_fields_exist_in_the_spec(
    model: type[ApiObject], method: str, pathname: str, status: str, location: str
) -> None:
    properties = response_properties(method, pathname, status, location)
    declared = {_field_in_spec(model, name) for name in model._fields}

    assert model._fields, f"{model.__name__} declares no fields"
    invented = sorted(declared - set(properties))
    assert not invented, f"{model.__name__} reads fields the spec does not declare: {invented}"


def test_a_cep_export_in_a_spec_response_reads_its_fields_from_the_attributes() -> None:
    properties = response_properties("GET", "/finance/ceps/exports/x", "200", "data")

    assert {"id", "type", "attributes", "links"} <= set(properties)


@pytest.mark.parametrize(
    ("model", "schema"), NESTED_MODELS, ids=[m.__name__ for m, _ in NESTED_MODELS]
)
def test_nested_response_fields_exist_in_the_spec(model: type[ApiObject], schema: str) -> None:
    declared = {_field_in_spec(model, name) for name in model._fields}

    assert declared == set(schema_named(schema))


def test_no_field_hides_a_dictionary_method_or_a_base_attribute() -> None:
    models = {model for model, *_ in RESPONSE_MODELS} | {model for model, _ in NESTED_MODELS}
    for model in models:
        clashes = sorted(model._fields & (set(dir(dict)) | NOT_FIELDS))
        assert not clashes, f"{model.__name__} has fields that clash with the dictionary: {clashes}"
