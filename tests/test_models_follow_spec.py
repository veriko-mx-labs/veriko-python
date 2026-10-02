"""Los campos tipados de cada modelo existen en el spec público.

Un modelo que lee `attributes.get("short_name")` cuando el spec dice `aliases`
devuelve `None` sin avisar. Aquí cada campo del modelo, salvo los que el SDK
añade (`raw`, `attributes`, `etag`...), tiene que ser una propiedad que el spec
declara para ese recurso.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from spec_support import load_spec, schema_properties
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
    UsageSummary,
    Validation,
    ValidationSummary,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookTestResult,
)

PROPIOS = {"raw", "attributes"}

# (modelo, schema del spec, dónde están sus campos, campos que añade el SDK)
MODELOS: list[tuple[type[Any], str, str, set[str]]] = [
    (Validation, "Validation", "attributes", PROPIOS | {"id", "links", "etag", "meta"}),
    (RetryState, "RetryStateFull", "plano", PROPIOS),
    (ValidationSummary, "PublicValidationListItem", "attributes", PROPIOS | {"id"}),
    (RetryAttempt, "RetryAttempt", "plano", PROPIOS),
    (WebhookEndpoint, "WebhookEndpoint", "attributes", PROPIOS | {"id"}),
    (WebhookDelivery, "WebhookDelivery", "attributes", PROPIOS | {"id"}),
    (WebhookTestResult, "SendWebhookTestAttributes", "plano", PROPIOS),
    (Bank, "BankResource", "attributes", PROPIOS),
    (Beneficiary, "Beneficiary", "attributes", PROPIOS | {"id"}),
    (BeneficiaryLookup, "BeneficiaryLookupResource", "attributes", PROPIOS),
    (BeneficiaryImportJob, "BeneficiaryImportJob", "attributes", PROPIOS | {"id"}),
    (BeneficiaryImportRow, "BeneficiaryImportRow", "attributes", PROPIOS | {"id"}),
    (UsageSummary, "UsageSummary", "plano", PROPIOS),
]


@pytest.mark.parametrize(
    ("modelo", "esquema", "donde", "propios"),
    MODELOS,
    ids=[modelo.__name__ for modelo, *_ in MODELOS],
)
def test_los_campos_tipados_existen_en_el_spec(
    modelo: type[Any], esquema: str, donde: str, propios: set[str]
) -> None:
    propiedades = schema_properties(load_spec()["components"]["schemas"][esquema])
    if donde == "attributes":
        propiedades = schema_properties(propiedades["attributes"])

    campos = {campo.name for campo in dataclasses.fields(modelo)} - propios

    inventados = sorted(campos - set(propiedades))
    assert not inventados, f"{modelo.__name__} lee campos que el spec no declara: {inventados}"


def _atributos(esquema: str) -> dict[str, Any]:
    """Los atributos de un recurso `data.attributes` de una respuesta del spec."""
    esquemas = load_spec()["components"]["schemas"]
    data = schema_properties(esquemas[esquema])["data"]
    atributos: dict[str, Any] = schema_properties(schema_properties(data)["attributes"])
    return atributos


def _atributo_de_validacion(nombre: str) -> dict[str, Any]:
    esquemas = load_spec()["components"]["schemas"]
    atributos = schema_properties(schema_properties(esquemas["Validation"])["attributes"])
    return schema_properties(atributos[nombre])


ANIDADOS: list[tuple[type[Any], Any, set[str]]] = [
    (DuplicateOf, lambda: _atributo_de_validacion("duplicate_of"), set()),
    (AccountConflict, lambda: _atributo_de_validacion("account_conflict"), set()),
    (CandidateMatch, lambda: _atributo_de_validacion("candidate_match"), set()),
    (
        RecheckResult,
        lambda: schema_properties(load_spec()["components"]["schemas"]["ValidationRecheckMeta"]),
        PROPIOS | {"validation"},
    ),
    (
        PurgePreparation,
        lambda: _atributos("ValidationPurgePrepareResponse"),
        PROPIOS | {"id"},
    ),
    (PurgeResult, lambda: _atributos("ValidationPurgeExecuteResponse"), PROPIOS | {"id"}),
]


@pytest.mark.parametrize(
    ("modelo", "propiedades", "propios"),
    ANIDADOS,
    ids=[modelo.__name__ for modelo, *_ in ANIDADOS],
)
def test_los_tipos_anidados_existen_en_el_spec(
    modelo: type[Any], propiedades: Any, propios: set[str]
) -> None:
    declaradas = set(propiedades())
    campos = {campo.name for campo in dataclasses.fields(modelo)} - propios

    inventados = sorted(campos - declaradas)
    assert not inventados, f"{modelo.__name__} lee campos que el spec no declara: {inventados}"
    assert campos == declaradas
