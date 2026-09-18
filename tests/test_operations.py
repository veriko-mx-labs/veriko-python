"""Las 48 operaciones que cubre el SDK, contra el spec público.

Cada caso llama a un método con todos sus argumentos opcionales y comprueba que lo
que llegó al servidor existe en el spec: el método y la ruta, los parámetros de
consulta, las cabeceras y los campos del cuerpo. Un cuerpo sin el envoltorio
`retry_policy`, o un filtro con otro nombre, fallan aquí.

Además cada operación tiene que ser de máquina a máquina: la que sólo acepta la
cookie de sesión es de la interfaz y no entra en el SDK, en ninguna versión.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import RecordingServer
from spec_support import (
    accepts_api_key,
    declared,
    find_operation,
    operations,
    request_body_properties,
)
from veriko import Veriko

ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
WEBHOOK = "9f8e7d6c-5b4a-3210-fedc-ba9876543210"
IMPORT = "8a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
FILA = "17"

FILTROS: dict[str, Any] = {
    "status": ["valid", "not_found"],
    "type": "ocr",
    "from_": "2025-01-01",
    "to": "2025-03-31",
    "search": "scotiabank",
    "playground": True,
    "with_deleted": True,
    "batch_id": 42,
    "bank": "012",
    "amount_min": 1000.5,
    "amount_max": 50000,
    "retry_state": "pending",
}

POLITICA = {
    "enabled": True,
    "max_retries": 3,
    "interval_seconds": 600,
    "outcomes": ["not_found", "cep_unavailable"],
}


@dataclass(frozen=True)
class Caso:
    operacion: str
    grabacion: str
    llamada: Callable[[Veriko], object]
    # El caso pasa todos los parámetros de consulta / campos del cuerpo del spec.
    todos_los_parametros: bool = False
    todo_el_cuerpo: bool = False


CASOS = [
    # ── Validaciones ────────────────────────────────────────────────────────
    Caso(
        "validateDirect",
        "validate-valid",
        lambda c: c.validations.validate(
            fecha="2025-03-15",
            monto=15000.5,
            clave_rastreo="MXBA20250315001234",
            referencia_numerica="1234567",
            emisor="BANCO NACIONAL DE MEXICO",
            receptor="BBVA MEXICO",
            cuenta_beneficiaria="012180004412345678",
            receptor_participante=1,
            retry_policy=POLITICA,
            idempotency_key="pedido-1",
        ),
        todo_el_cuerpo=True,
    ),
    Caso(
        "validateDirect",
        "validate-queued",
        lambda c: c.validations.enqueue(
            fecha="2025-03-15",
            monto=15000.5,
            clave_rastreo="MXBA20250315001234",
            referencia_numerica="1234567",
            emisor="BANCO NACIONAL DE MEXICO",
            receptor="BBVA MEXICO",
            cuenta_beneficiaria="012180004412345678",
            receptor_participante=1,
            retry_policy=POLITICA,
            idempotency_key="pedido-1",
        ),
        todos_los_parametros=True,
        todo_el_cuerpo=True,
    ),
    Caso(
        "validateOcr",
        "validate-ocr",
        lambda c: c.validations.validate_ocr(
            image=b"imagen",
            image_url="https://ejemplo.mx/comprobante.png",
            cuenta_beneficiaria="012180004412345678",
            retry_policy=POLITICA,
            idempotency_key="pedido-2",
        ),
        todo_el_cuerpo=True,
    ),
    Caso(
        "validateOcr",
        "validate-queued",
        lambda c: c.validations.enqueue_ocr(
            image=b"imagen",
            image_url="https://ejemplo.mx/comprobante.png",
            cuenta_beneficiaria="012180004412345678",
            retry_policy=POLITICA,
            idempotency_key="pedido-2",
        ),
        todos_los_parametros=True,
        todo_el_cuerpo=True,
    ),
    Caso(
        "listValidations",
        "validations-page1",
        lambda c: c.validations.list(page=2, per_page=5, **FILTROS),
        todos_los_parametros=True,
    ),
    Caso(
        "validationStats",
        "validations-stats",
        lambda c: c.validations.stats(**FILTROS),
        todos_los_parametros=True,
    ),
    Caso(
        "exportValidations",
        "validations-export-csv",
        lambda c: c.validations.export(format="xlsx", limit=500, **FILTROS),
        todos_los_parametros=True,
    ),
    Caso(
        "getValidation",
        "validate-valid",
        lambda c: c.validations.get(ID, if_none_match='W/"3-not_found"'),
    ),
    Caso(
        "downloadCep",
        "cep-pdf",
        lambda c: c.validations.cep(ID, format="pdf"),
        todos_los_parametros=True,
    ),
    Caso("getValidationImage", "validation-image", lambda c: c.validations.image(ID)),
    Caso(
        "listValidationRetryAttempts", "retry-attempts", lambda c: c.validations.retry_attempts(ID)
    ),
    Caso(
        "updateValidationRetryPolicy",
        "retry-policy-updated",
        lambda c: c.validations.set_retry_policy(ID, POLITICA, idempotency_key="pedido-3"),
        todo_el_cuerpo=True,
    ),
    Caso(
        "cancelValidationRetries",
        "retries-cancelled",
        lambda c: c.validations.cancel_retries(ID, idempotency_key="pedido-4"),
    ),
    Caso("deleteValidation", "no-content", lambda c: c.validations.delete(ID)),
    Caso(
        "sendCepToTelegram", "telegram-accepted", lambda c: c.validations.send_cep_to_telegram(ID)
    ),
    # ── Webhooks ────────────────────────────────────────────────────────────
    Caso(
        "createWebhook",
        "webhook-created",
        lambda c: c.webhooks.create(
            url="https://miapp.example.com/hooks/pagos",
            events=["validation.completed"],
            description="Pagos de contado",
        ),
        todo_el_cuerpo=True,
    ),
    Caso("listWebhooks", "webhooks-list", lambda c: c.webhooks.list()),
    Caso(
        "updateWebhook",
        "webhook-updated",
        lambda c: c.webhooks.update(
            WEBHOOK,
            url="https://miapp.example.com/hooks/nuevo",
            events=["validation.completed", "validation.failed"],
            description=None,
            status="active",
        ),
        todo_el_cuerpo=True,
    ),
    Caso("deleteWebhook", "no-content", lambda c: c.webhooks.delete(WEBHOOK)),
    Caso("sendWebhookTest", "webhook-test-ok", lambda c: c.webhooks.test(WEBHOOK)),
    Caso(
        "regenerateWebhookSecret",
        "webhook-secret-rotated",
        lambda c: c.webhooks.regenerate_secret(WEBHOOK),
    ),
    Caso(
        "listWebhookDeliveries",
        "webhook-deliveries",
        lambda c: c.webhooks.deliveries(WEBHOOK, page=2, per_page=10),
        todos_los_parametros=True,
    ),
    Caso(
        "listAllDeliveries",
        "webhook-deliveries",
        lambda c: c.webhooks.deliveries(
            WEBHOOK,
            page=2,
            per_page=10,
            status="retrying",
            event_type="validation.completed",
        ),
        todos_los_parametros=True,
    ),
    Caso(
        "listAllDeliveries",
        "webhook-deliveries",
        lambda c: c.webhooks.deliveries(status="failed"),
    ),
    Caso(
        "exportWebhookDeliveries",
        "deliveries-export-csv",
        lambda c: c.webhooks.export_deliveries(WEBHOOK, format="csv", limit=100),
        todos_los_parametros=True,
    ),
    Caso(
        "exportAllDeliveries",
        "deliveries-export-csv",
        lambda c: c.webhooks.export_deliveries(
            WEBHOOK,
            format="xlsx",
            limit=100,
            status="failed",
            event_type="validation.failed",
        ),
        todos_los_parametros=True,
    ),
    # ── Catálogo y estado de Banxico ────────────────────────────────────────
    Caso("listBanks", "banks", lambda c: c.catalog.banks(if_none_match='"a1b2c3d4e5f6"')),
    Caso("lookupBin", "bin-lookup", lambda c: c.catalog.bin_lookup("455632")),
    Caso("banxicoPublicStatus", "banxico-status", lambda c: c.catalog.banxico_status()),
    Caso(
        "banxicoPublicTimeseries",
        "banxico-timeseries",
        lambda c: c.catalog.banxico_timeseries(metric="verdict", window="7d"),
        todos_los_parametros=True,
    ),
    # ── Beneficiarios ───────────────────────────────────────────────────────
    Caso(
        "createBeneficiary",
        "beneficiary-created",
        lambda c: c.beneficiaries.create(
            account_number="5512345678", bank_code="40012", label="Proveedor X"
        ),
        todo_el_cuerpo=True,
    ),
    Caso(
        "listBeneficiaries",
        "beneficiaries-list",
        lambda c: c.beneficiaries.list(with_archived="0"),
        todos_los_parametros=True,
    ),
    Caso(
        "updateBeneficiary",
        "beneficiary-updated",
        lambda c: c.beneficiaries.update(
            "12", label="Proveedor Y", account_number="012180004412345678", bank_code="40012"
        ),
        todo_el_cuerpo=True,
    ),
    Caso("deleteBeneficiary", "no-content", lambda c: c.beneficiaries.delete("12")),
    Caso(
        "validateAccount",
        "account-validation",
        lambda c: c.beneficiaries.validate_account("012180004412345678", type="clabe"),
        todos_los_parametros=True,
    ),
    Caso(
        "lookupBeneficiaryAccount",
        "beneficiary-lookup",
        lambda c: c.beneficiaries.lookup("012180004412345678"),
        todos_los_parametros=True,
    ),
    Caso(
        "exportBeneficiaries",
        "beneficiaries-export-csv",
        lambda c: c.beneficiaries.export(format="xlsx", with_archived="1", limit=10),
        todos_los_parametros=True,
    ),
    Caso(
        "downloadBeneficiaryImportTemplate",
        "beneficiaries-import-template",
        lambda c: c.beneficiaries.import_template(format="xlsx"),
        todos_los_parametros=True,
    ),
    Caso(
        "createBeneficiaryImport",
        "beneficiaries-import-created",
        lambda c: c.beneficiaries.import_start(
            b"cuenta,alias\n012180004412345678,Proveedor X\n",
            parse_mode="free",
            filename="lista.csv",
        ),
        todo_el_cuerpo=True,
    ),
    Caso(
        "getBeneficiaryImport",
        "beneficiaries-import-status",
        lambda c: c.beneficiaries.import_status(IMPORT),
    ),
    Caso(
        "getBeneficiaryImportPreview",
        "beneficiaries-import-preview",
        lambda c: c.beneficiaries.import_preview(IMPORT, page=2, per_page=5, buckets=["valid"]),
        todos_los_parametros=True,
    ),
    Caso(
        "patchBeneficiaryImportRow",
        "beneficiaries-import-row-updated",
        lambda c: c.beneficiaries.import_edit_row(
            IMPORT,
            FILA,
            parsed_account="012180004412345678",
            parsed_label="Proveedor X",
            parsed_account_type="clabe",
            parsed_bank_code="40012",
            parsed_bank_name="BBVA MEXICO",
        ),
        todo_el_cuerpo=True,
    ),
    Caso(
        "deleteBeneficiaryImportRow",
        "no-content",
        lambda c: c.beneficiaries.import_remove_row(IMPORT, FILA),
    ),
    Caso(
        "commitBeneficiaryImport",
        "beneficiaries-import-committed",
        lambda c: c.beneficiaries.import_commit(IMPORT),
    ),
    # ── Consumo ─────────────────────────────────────────────────────────────
    Caso("getUsageSummary", "usage-summary", lambda c: c.usage.summary()),
    Caso(
        "getUsageHistory",
        "usage-history",
        lambda c: c.usage.history(months=3),
        todos_los_parametros=True,
    ),
    Caso(
        "getUsageBreakdown",
        "usage-breakdown",
        lambda c: c.usage.breakdown(period="current"),
        todos_los_parametros=True,
    ),
    Caso("getUsageLimits", "usage-limits", lambda c: c.usage.limits()),
    Caso(
        "getUsageHeatmap",
        "usage-heatmap",
        lambda c: c.usage.heatmap(days=30),
        todos_los_parametros=True,
    ),
    Caso("getApiUsage", "api-usage", lambda c: c.usage.api_usage()),
    Caso(
        "exportApiUsage",
        "api-usage-export-csv",
        lambda c: c.usage.export(format="xlsx", from_="2025-01-01", to="2025-03-31", limit=100),
        todos_los_parametros=True,
    ),
]

# Las operaciones de las etiquetas Beneficiaries y Usage que aceptan la clave de API y
# todavía no tienen método.
SIN_METODO = {"cancelBeneficiaryImport"}


@pytest.mark.parametrize(
    "caso", CASOS, ids=[f"{c.operacion}-{c.grabacion}-{n}" for n, c in enumerate(CASOS)]
)
def test_lo_que_el_sdk_envia_existe_en_el_spec(
    caso: Caso, client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording(caso.grabacion)

    caso.llamada(client)

    enviada = server.requests[0]
    url = urlsplit(enviada.path)
    plantilla, operacion = find_operation(enviada.method, re.sub(r"^/v1", "", url.path))

    assert operacion["operationId"] == caso.operacion, f"{enviada.method} {url.path}"
    assert accepts_api_key(operacion), f"{plantilla} sólo acepta la cookie de sesión"

    consulta = sorted(parse_qs(url.query))
    declarados = declared(plantilla, operacion, "query")
    for nombre in consulta:
        assert nombre in declarados, f"{plantilla}: el spec no declara el parámetro {nombre}"
    if caso.todos_los_parametros:
        assert consulta == sorted(declarados)

    cabeceras = {nombre.lower() for nombre in declared(plantilla, operacion, "header")}
    for nombre in ("idempotency-key", "if-none-match"):
        if nombre in enviada.headers:
            assert nombre in cabeceras, f"{plantilla}: el spec no declara la cabecera {nombre}"

    if enviada.body:
        tipo = enviada.headers["content-type"].split(";")[0].strip()
        propiedades = request_body_properties(operacion, tipo)
        if tipo == "application/json":
            campos = sorted(json.loads(enviada.body))
        else:
            campos = sorted(
                {nombre.decode() for nombre in re.findall(rb'; name="([^"]+)"', enviada.body)}
            )
        for campo in campos:
            assert campo in propiedades, f"{plantilla}: el spec no declara el campo {campo}"
        if caso.todo_el_cuerpo:
            assert campos == sorted(propiedades)


def test_son_las_48_operaciones_de_esta_version() -> None:
    cubiertas = {caso.operacion for caso in CASOS}

    assert len(cubiertas) == 48


def test_las_familias_completas_no_traen_operaciones_sin_metodo() -> None:
    cubiertas = {caso.operacion for caso in CASOS}
    familias = {"Validations", "Webhooks", "Public", "Banxico Status", "Beneficiaries", "Usage"}
    de_maquina = {
        operacion["operationId"]
        for _, _, operacion in operations()
        if familias & set(operacion.get("tags", [])) and accepts_api_key(operacion)
    }

    assert de_maquina - cubiertas == SIN_METODO
    assert cubiertas <= de_maquina


def test_ninguna_operacion_de_sesion_tiene_metodo() -> None:
    cubiertas = {caso.operacion for caso in CASOS}
    solo_cookie = {
        operacion["operationId"]
        for _, _, operacion in operations()
        if not accepts_api_key(operacion)
    }

    assert solo_cookie
    assert cubiertas & solo_cookie == set()
