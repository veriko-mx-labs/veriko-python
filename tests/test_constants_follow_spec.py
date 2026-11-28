"""The constants that mirror spec data stay equal to it."""

from __future__ import annotations

import re
from typing import Any

from spec_support import _resolve, load_spec, operations, schema_properties
from veriko.models import RETRYABLE_OUTCOMES, TERMINAL_STATUSES
from veriko.pagination import DEFAULT_PER_PAGE
from veriko.resources import CEP_FORMATS, EXPORT_FORMATS


def operation(operation_id: str) -> dict[str, Any]:
    for _, _, candidate in operations():
        if candidate["operationId"] == operation_id:
            return candidate
    raise AssertionError(f"the spec does not declare {operation_id}")


def parameter_schema(operation_id: str, name: str) -> dict[str, Any]:
    for parameter in operation(operation_id).get("parameters", []):
        if parameter.get("name") == name:
            schema: dict[str, Any] = parameter["schema"]
            return schema
    raise AssertionError(f"{operation_id} does not declare the parameter {name}")


def test_validation_statuses_and_their_terminals_follow_the_spec() -> None:
    validation = schema_properties(load_spec()["components"]["schemas"]["Validation"])
    attributes = schema_properties(validation["attributes"])
    status_schema = attributes["status"]
    sdk_statuses = {"queued", "processing", *TERMINAL_STATUSES}

    assert sdk_statuses == set(status_schema["enum"])
    assert set(TERMINAL_STATUSES) == set(status_schema["x-terminal-values"])


def test_retryable_outcomes_are_the_spec_enum() -> None:
    policy = schema_properties(load_spec()["components"]["schemas"]["RetryPolicy"])

    assert set(RETRYABLE_OUTCOMES) == set(policy["outcomes"]["items"]["enum"])


def test_cep_formats_are_the_spec_enum() -> None:
    assert set(CEP_FORMATS) == set(parameter_schema("downloadCep", "format")["enum"])


def test_export_formats_are_the_spec_enums() -> None:
    operation_ids = (
        "exportValidations",
        "exportWebhookDeliveries",
        "exportAllDeliveries",
        "exportBeneficiaries",
        "exportApiUsage",
    )
    for operation_id in operation_ids:
        assert set(EXPORT_FORMATS) == set(parameter_schema(operation_id, "format")["enum"])


def test_the_default_page_size_is_the_spec_one() -> None:
    schema_default = parameter_schema("getBeneficiaryImportPreview", "per_page")["default"]

    assert schema_default == DEFAULT_PER_PAGE


# What the SDK does with each `429` code the public API declares: it waits and repeats
# `rate_limit_exceeded` and `export_limit_reached`, and raises at once on `quota_exceeded`
# and `recheck_rate_limited`. A new code in the spec needs a decision here.
KNOWN_429_CODES = {
    "rate_limit_exceeded",
    "quota_exceeded",
    "recheck_rate_limited",
    "export_limit_reached",
}


def _codes_in(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "code" and isinstance(value, str):
                found.add(value)
            else:
                _codes_in(value, found)
    elif isinstance(node, list):
        for item in node:
            _codes_in(item, found)


def declared_429_codes() -> set[str]:
    found: set[str] = set()
    for _, _, candidate in operations():
        response = candidate.get("responses", {}).get("429")
        if response is None:
            continue
        resolved = _resolve(response, "responses")
        _codes_in(resolved, found)
        found.update(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", resolved.get("description", "")))
    return found


def test_every_429_code_of_the_spec_has_a_decision_in_the_sdk() -> None:
    assert declared_429_codes() == KNOWN_429_CODES
