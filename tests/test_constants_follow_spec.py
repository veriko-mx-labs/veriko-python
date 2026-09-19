"""Las constantes que espejan datos del spec siguen siendo iguales a él."""

from __future__ import annotations

from typing import Any

from spec_support import load_spec, operations, schema_properties
from veriko.models import RETRYABLE_OUTCOMES, TERMINAL_STATUSES
from veriko.pagination import DEFAULT_PER_PAGE
from veriko.resources import CEP_FORMATS, EXPORT_FORMATS


def operation(operation_id: str) -> dict[str, Any]:
    for _, _, candidate in operations():
        if candidate["operationId"] == operation_id:
            return candidate
    raise AssertionError(f"el spec no declara {operation_id}")


def parameter_schema(operation_id: str, name: str) -> dict[str, Any]:
    for parameter in operation(operation_id).get("parameters", []):
        if parameter.get("name") == name:
            schema: dict[str, Any] = parameter["schema"]
            return schema
    raise AssertionError(f"{operation_id} no declara el parámetro {name}")


def test_los_estados_de_validacion_son_los_nueve_del_spec() -> None:
    validation = schema_properties(load_spec()["components"]["schemas"]["Validation"])
    attributes = schema_properties(validation["attributes"])
    sdk_statuses = {"queued", "processing", *TERMINAL_STATUSES}

    assert sdk_statuses == set(attributes["status"]["enum"])


def test_los_resultados_reintentables_son_el_enum_del_spec() -> None:
    policy = schema_properties(load_spec()["components"]["schemas"]["RetryPolicy"])

    assert set(RETRYABLE_OUTCOMES) == set(policy["outcomes"]["items"]["enum"])


def test_los_formatos_del_cep_son_el_enum_del_spec() -> None:
    assert set(CEP_FORMATS) == set(parameter_schema("downloadCep", "format")["enum"])


def test_los_formatos_de_exportacion_son_los_enum_del_spec() -> None:
    operation_ids = (
        "exportValidations",
        "exportWebhookDeliveries",
        "exportAllDeliveries",
        "exportBeneficiaries",
        "exportApiUsage",
    )
    for operation_id in operation_ids:
        assert set(EXPORT_FORMATS) == set(parameter_schema(operation_id, "format")["enum"])


def test_el_tamano_de_pagina_predeterminado_es_el_del_spec() -> None:
    schema_default = parameter_schema("getBeneficiaryImportPreview", "per_page")["default"]

    assert schema_default == DEFAULT_PER_PAGE
