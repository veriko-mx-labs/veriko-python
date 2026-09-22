"""Cada respuesta grabada coincide con el schema de la operación que representa."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft7Validator

from spec_support import load_spec, operations

RECORDINGS = Path(__file__).parent / "recordings"

OPERATION_RECORDINGS = {
    "myProfile": ("account-profile",),
    "getDashboardSummary": ("dashboard-summary",),
    "getMyRetryPolicy": ("account-retry-policy",),
    "updateMyRetryPolicy": ("account-retry-policy-updated",),
    "listPublicPlans": ("public-plans",),
    "getPublicPlanComparison": ("public-plan-comparison",),
    "getUserInsightsOverview": ("insights-overview",),
    "getUserInsightsTrends": ("insights-trends",),
    "getUserInsightsTopBanks": ("insights-top-banks",),
    "getUserInsightsTopBeneficiaries": ("insights-top-beneficiaries",),
    "getFinanceSummary": ("finance-summary",),
    "getFinanceStatement": ("finance-statement",),
    "getFinanceMonthly": ("finance-monthly",),
    "getFinanceCounterparties": ("finance-counterparties",),
    "getFinanceByBank": ("finance-by-bank",),
    "getFinanceAccounting": ("finance-accounting",),
    "getFinanceCeps": ("finance-ceps",),
    "billingGetSubscription": ("billing-subscription",),
    "exportApiUsage": ("api-usage-export-csv",),
    "getApiUsage": ("api-usage",),
    "listBanks": ("banks",),
    "banxicoPublicStatus": ("banxico-status",),
    "banxicoPublicTimeseries": ("banxico-timeseries",),
    "exportBeneficiaries": ("beneficiaries-export-csv",),
    "commitBeneficiaryImport": ("beneficiaries-import-committed",),
    "createBeneficiaryImport": ("beneficiaries-import-created",),
    "getBeneficiaryImport": ("beneficiaries-import-parsing", "beneficiaries-import-status"),
    "getBeneficiaryImportPreview": ("beneficiaries-import-preview",),
    "patchBeneficiaryImportRow": ("beneficiaries-import-row-updated",),
    "downloadBeneficiaryImportTemplate": ("beneficiaries-import-template",),
    "listBeneficiaries": ("beneficiaries-list",),
    "createBeneficiary": ("beneficiary-created",),
    "lookupBeneficiaryAccount": ("beneficiary-lookup",),
    "updateBeneficiary": ("beneficiary-updated",),
    "lookupBin": ("bin-lookup",),
    "downloadCep": ("cep-404", "cep-pdf", "cep-xml"),
    "exportAllDeliveries": ("deliveries-export-csv",),
    "deleteValidation": ("no-content",),
    "getValidation": ("not-modified", "validation-queued-status", "validation-retrying"),
    "cancelValidationRetries": ("retries-cancelled",),
    "listValidationRetryAttempts": ("retry-attempts",),
    "updateValidationRetryPolicy": ("retry-policy-updated",),
    "sendCepToTelegram": ("telegram-accepted",),
    "getUsageBreakdown": ("usage-breakdown",),
    "getUsageHeatmap": ("usage-heatmap",),
    "getUsageHistory": ("usage-history",),
    "getUsageLimits": ("usage-limits",),
    "getUsageSummary": ("usage-summary",),
    "validateDirect": (
        "validate-401",
        "validate-409",
        "validate-422",
        "validate-429",
        "validate-503",
        "validate-not-found",
        "validate-queued",
        "validate-returned",
        "validate-valid",
    ),
    "validateOcr": ("validate-ocr",),
    "getValidationImage": ("validation-image",),
    "listValidations": ("validations-empty", "validations-page1", "validations-page2"),
    "exportValidations": ("validations-export-csv", "validations-export-xlsx"),
    "validationStats": ("validations-stats",),
    "createWebhook": ("webhook-created",),
    "listAllDeliveries": (
        "webhook-deliveries",
        "webhook-deliveries-page1",
        "webhook-deliveries-page2",
    ),
    "regenerateWebhookSecret": ("webhook-secret-rotated",),
    "sendWebhookTest": ("webhook-test-failed", "webhook-test-ok"),
    "updateWebhook": ("webhook-updated",),
    "listWebhooks": ("webhooks-list",),
}

# Son cuerpos entrantes para probar la firma y el parser, no respuestas de una operación.
NON_RESPONSE_RECORDINGS = {"webhook-retry-resolved", "webhook-validation-completed"}


def operation_by_id(operation_id: str) -> dict[str, Any]:
    for _, _, operation in operations():
        if operation["operationId"] == operation_id:
            return operation
    raise AssertionError(f"el spec no declara {operation_id}")


def resolve(node: dict[str, Any]) -> dict[str, Any]:
    reference = node.get("$ref")
    if not reference:
        return node
    current: Any = load_spec()
    for part in reference.removeprefix("#/").split("/"):
        current = current[part.replace("~1", "/").replace("~0", "~")]
    return cast(dict[str, Any], current)


def response_schema(operation_id: str, recording: dict[str, Any]) -> dict[str, Any] | None:
    response = resolve(operation_by_id(operation_id)["responses"][str(recording["status"])])
    if "json" not in recording:
        return None
    content_type = next(
        value
        for name, value in recording.get("headers", {}).items()
        if name.lower() == "content-type"
    ).split(";", 1)[0]
    content = response.get("content", {})
    assert content_type in content, f"{operation_id} no declara una respuesta {content_type}"
    return cast(dict[str, Any], content[content_type]["schema"])


def normalize_nullable(node: Any) -> None:
    if isinstance(node, list):
        for item in node:
            normalize_nullable(item)
        return
    if not isinstance(node, dict):
        return
    if node.get("nullable") is True and isinstance(node.get("type"), str):
        node["type"] = [node["type"], "null"]
    for value in node.values():
        normalize_nullable(value)


def validator_for(schema: dict[str, Any]) -> Draft7Validator:
    document = copy.deepcopy(load_spec())
    document["$schema"] = "http://json-schema.org/draft-07/schema#"
    document["x-recording-schema"] = schema
    document["$ref"] = "#/x-recording-schema"
    normalize_nullable(document)
    return Draft7Validator(document)


def applicable_schemas(schema: dict[str, Any], value: Any) -> list[dict[str, Any]]:
    resolved = resolve(schema)
    schemas = [resolved]
    for part in resolved.get("allOf", []):
        schemas.extend(applicable_schemas(part, value))
    for keyword in ("oneOf", "anyOf"):
        for candidate in resolved.get(keyword, []):
            if validator_for(candidate).is_valid(value):
                schemas.extend(applicable_schemas(candidate, value))
    return schemas


def assert_only_declared(
    value: Any, schema: dict[str, Any] | list[dict[str, Any]], path: str = "$"
) -> None:
    sources = schema if isinstance(schema, list) else [schema]
    schemas = [candidate for source in sources for candidate in applicable_schemas(source, value)]
    if isinstance(value, list):
        for index, item in enumerate(value):
            items = [candidate["items"] for candidate in schemas if "items" in candidate]
            if items:
                assert_only_declared(item, items, f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        declared = [
            candidate["properties"][key]
            for candidate in schemas
            if key in candidate.get("properties", {})
        ]
        allows_extra = any(
            candidate.get("additionalProperties") is True
            or isinstance(candidate.get("additionalProperties"), dict)
            for candidate in schemas
        )
        assert declared or allows_extra, f"{path}.{key} no existe en el spec"
        if declared:
            assert_only_declared(item, declared, f"{path}.{key}")


def recording_cases() -> list[tuple[str, str]]:
    return [
        (operation_id, recording)
        for operation_id, recordings in OPERATION_RECORDINGS.items()
        for recording in recordings
    ]


def test_todas_las_grabaciones_tienen_un_contrato() -> None:
    mapped = {recording for _, recording in recording_cases()} | NON_RESPONSE_RECORDINGS
    present = {path.stem for path in RECORDINGS.glob("*.json")}

    assert mapped == present


def test_las_grabaciones_siguen_el_schema_de_su_operacion() -> None:
    for operation_id, name in recording_cases():
        recording: dict[str, Any] = json.loads((RECORDINGS / f"{name}.json").read_text())
        schema = response_schema(operation_id, recording)
        if schema is None:
            continue
        errors = sorted(
            validator_for(schema).iter_errors(recording["json"]),
            key=lambda error: list(error.path),
        )
        assert not errors, f"{name}: {errors[0].json_path}: {errors[0].message}"
        assert_only_declared(recording["json"], schema, name)
