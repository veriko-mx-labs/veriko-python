"""The 75 M2M operations the SDK covers, against the public spec.

Each case calls a method with all its optional arguments and checks that what
reached the server exists in the spec: the method and path, the query
parameters, the headers and the body fields. A body without the `retry_policy`
wrapper, or a filter with another name, fails here.

Every operation must also be machine-to-machine: it accepts `ApiKeyAuth` or is
public with `security: []`. An operation that does not meet that cannot enter
this spec or the SDK.
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
IMPORT = "42"
ROW = "17"

PURGE_TOKEN = "eyJhZG1pbl9pZCI6Ii4uLiJ9.q1w2e3r4t5y6u7i8o9p0"

CANDIDATES = ["012180004412345678", "002010077777777771"]

FILTERS: dict[str, Any] = {
    "status": ["valid", "not_found"],
    "type": "ocr",
    "from_": "2025-01-01",
    "to": "2025-03-31",
    "search": "scotiabank",
    "playground": True,
    "with_deleted": True,
    "batch_id": 42,
    "bank": "012",
    "banks": ["40012", "40014", "40072"],
    "amount_min": 1000.5,
    "amount_max": 50000,
    "client_ref": "orden-4812",
    "retry_state": "pending",
}

POLICY = {
    "enabled": True,
    "max_retries": 3,
    "interval_seconds": 600,
    "outcomes": ["not_found", "cep_unavailable"],
}


@dataclass(frozen=True)
class Case:
    operation: str
    recording: str
    call: Callable[[Veriko], object]
    # The case passes every query parameter / body field of the spec.
    all_parameters: bool = False
    whole_body: bool = False
    # Body fields the case omits because they exclude another one it does pass.
    without_fields: frozenset[str] = frozenset()


CASES = [
    # ── Validaciones ────────────────────────────────────────────────────────
    Case(
        "validateDirect",
        "validate-valid",
        lambda c: c.validations.validate(
            send_date="2025-03-15",
            amount=15000.5,
            tracking_key="MXBA20250315001234",
            numeric_reference="1234567",
            sender_bank="BANCO NACIONAL DE MEXICO",
            receiver_bank="BBVA MEXICO",
            beneficiary_account="012180004412345678",
            recipient_is_bank=1,
            retry_policy=POLICY,
            client_ref="orden-4812",
            idempotency_key="pedido-1",
        ),
        whole_body=True,
        without_fields=frozenset({"candidate_accounts"}),
    ),
    Case(
        "validateDirect",
        "validate-candidates",
        lambda c: c.validations.validate(
            send_date="2025-03-15",
            amount=15000.5,
            tracking_key="MXBA20250315001234",
            numeric_reference="1234567",
            sender_bank="BANCO NACIONAL DE MEXICO",
            receiver_bank="BBVA MEXICO",
            candidate_accounts=CANDIDATES,
            recipient_is_bank=1,
            retry_policy=POLICY,
            client_ref="orden-4812",
            idempotency_key="pedido-1",
        ),
        whole_body=True,
        without_fields=frozenset({"beneficiary_account"}),
    ),
    Case(
        "validateDirect",
        "validate-queued",
        lambda c: c.validations.enqueue(
            send_date="2025-03-15",
            amount=15000.5,
            tracking_key="MXBA20250315001234",
            numeric_reference="1234567",
            sender_bank="BANCO NACIONAL DE MEXICO",
            receiver_bank="BBVA MEXICO",
            beneficiary_account="012180004412345678",
            recipient_is_bank=1,
            retry_policy=POLICY,
            client_ref="orden-4812",
            idempotency_key="pedido-1",
        ),
        all_parameters=True,
        whole_body=True,
        without_fields=frozenset({"candidate_accounts"}),
    ),
    Case(
        "validateOcr",
        "validate-ocr",
        lambda c: c.validations.validate_ocr(
            image=b"imagen",
            image_url="https://ejemplo.mx/comprobante.png",
            beneficiary_account="012180004412345678",
            retry_policy=POLICY,
            client_ref="orden-4812",
            retain_image=False,
            sender_bank="40012",
            receiver_bank="40002",
            recipient_is_bank=True,
            idempotency_key="pedido-2",
        ),
        whole_body=True,
        without_fields=frozenset({"candidate_accounts"}),
    ),
    Case(
        "validateOcr",
        "validate-ocr",
        lambda c: c.validations.validate_ocr(
            image=b"imagen",
            image_url="https://ejemplo.mx/comprobante.png",
            candidate_accounts=CANDIDATES,
            retry_policy=POLICY,
            client_ref="orden-4812",
            retain_image=True,
            sender_bank="40012",
            receiver_bank="40002",
            recipient_is_bank=True,
            idempotency_key="pedido-2",
        ),
        whole_body=True,
        without_fields=frozenset({"beneficiary_account"}),
    ),
    Case(
        "validateOcr",
        "validate-queued",
        lambda c: c.validations.enqueue_ocr(
            image=b"imagen",
            image_url="https://ejemplo.mx/comprobante.png",
            beneficiary_account="012180004412345678",
            retry_policy=POLICY,
            client_ref="orden-4812",
            retain_image=False,
            sender_bank="40012",
            receiver_bank="40002",
            recipient_is_bank=True,
            idempotency_key="pedido-2",
        ),
        all_parameters=True,
        whole_body=True,
        without_fields=frozenset({"candidate_accounts"}),
    ),
    Case(
        "listValidations",
        "validations-page1",
        lambda c: c.validations.list(page=2, per_page=5, **FILTERS),
        all_parameters=True,
    ),
    Case(
        "validationStats",
        "validations-stats",
        lambda c: c.validations.stats(**FILTERS),
        all_parameters=True,
    ),
    Case(
        "exportValidations",
        "validations-export-csv",
        lambda c: c.validations.export(format="xlsx", limit=500, **FILTERS),
        all_parameters=True,
    ),
    Case(
        "getValidation",
        "validate-valid",
        lambda c: c.validations.get(ID, if_none_match='W/"3-not_found"'),
    ),
    Case(
        "downloadCep",
        "cep-pdf",
        lambda c: c.validations.cep(ID, format="pdf"),
        all_parameters=True,
    ),
    Case("getValidationImage", "validation-image", lambda c: c.validations.image(ID)),
    Case(
        "listValidationRetryAttempts", "retry-attempts", lambda c: c.validations.retry_attempts(ID)
    ),
    Case(
        "updateValidationRetryPolicy",
        "retry-policy-updated",
        lambda c: c.validations.set_retry_policy(ID, POLICY, idempotency_key="pedido-3"),
        whole_body=True,
    ),
    Case(
        "cancelValidationRetries",
        "retries-cancelled",
        lambda c: c.validations.cancel_retries(ID, idempotency_key="pedido-4"),
    ),
    Case("deleteValidation", "no-content", lambda c: c.validations.delete(ID)),
    Case(
        "sendCepToTelegram", "telegram-accepted", lambda c: c.validations.send_cep_to_telegram(ID)
    ),
    Case("recheckValidation", "validation-recheck-returned", lambda c: c.validations.recheck(ID)),
    Case(
        "prepareValidationPurge",
        "validation-purge-prepare",
        lambda c: c.validations.prepare_purge(ID),
    ),
    Case(
        "executeValidationPurge",
        "validation-purge-executed",
        lambda c: c.validations.execute_purge(ID, confirmation_token=PURGE_TOKEN),
        whole_body=True,
    ),
    # ── Webhooks ────────────────────────────────────────────────────────────
    Case(
        "createWebhook",
        "webhook-created",
        lambda c: c.webhooks.create(
            url="https://miapp.example.com/hooks/pagos",
            events=["validation.completed"],
            description="Cash payments",
        ),
        whole_body=True,
    ),
    Case("listWebhooks", "webhooks-list", lambda c: c.webhooks.list()),
    Case(
        "updateWebhook",
        "webhook-updated",
        lambda c: c.webhooks.update(
            WEBHOOK,
            url="https://miapp.example.com/hooks/nuevo",
            events=["validation.completed", "validation.failed"],
            description=None,
            status="active",
        ),
        whole_body=True,
    ),
    Case("deleteWebhook", "no-content", lambda c: c.webhooks.delete(WEBHOOK)),
    Case("sendWebhookTest", "webhook-test-ok", lambda c: c.webhooks.test(WEBHOOK)),
    Case(
        "regenerateWebhookSecret",
        "webhook-secret-rotated",
        lambda c: c.webhooks.regenerate_secret(WEBHOOK),
    ),
    Case(
        "listWebhookDeliveries",
        "webhook-deliveries",
        lambda c: c.webhooks.deliveries(WEBHOOK, page=2, per_page=10),
        all_parameters=True,
    ),
    Case(
        "listAllDeliveries",
        "webhook-deliveries",
        lambda c: c.webhooks.deliveries(
            WEBHOOK,
            page=2,
            per_page=10,
            status=["retrying", "failed"],
            event_type="validation.completed",
            response_status=["5xx", "none"],
            validation_id=ID,
            q="erp.example.com",
            from_="2026-10-01",
            to="2026-10-15T23:59:59Z",
            min_attempt=2,
            min_response_time_ms=3000,
        ),
        all_parameters=True,
    ),
    Case(
        "listAllDeliveries",
        "webhook-deliveries",
        lambda c: c.webhooks.deliveries(status="failed"),
    ),
    Case(
        "exportWebhookDeliveries",
        "deliveries-export-csv",
        lambda c: c.webhooks.export_deliveries(WEBHOOK, format="csv", limit=100),
        all_parameters=True,
    ),
    Case(
        "exportAllDeliveries",
        "deliveries-export-csv",
        lambda c: c.webhooks.export_deliveries(
            [WEBHOOK, WEBHOOK],
            format="xlsx",
            limit=100,
            status="failed",
            event_type=["validation.failed", "validation.error"],
            response_status=404,
            validation_id=ID,
            q="erp.example.com",
            from_="2026-10-01",
            to="2026-10-15",
            min_attempt=2,
            min_response_time_ms=3000,
        ),
        all_parameters=True,
    ),
    # ── Catalog and Banxico status ──────────────────────────────────────────
    Case("listBanks", "banks", lambda c: c.catalog.banks(if_none_match='"a1b2c3d4e5f6"')),
    Case("lookupBin", "bin-lookup", lambda c: c.catalog.bin_lookup("455632")),
    Case(
        "getSpeiCalendar",
        "spei-calendar",
        lambda c: c.catalog.spei_calendar(2026, if_none_match='"a1b2c3d4e5f6"'),
    ),
    Case("banxicoPublicStatus", "banxico-status", lambda c: c.catalog.banxico_status()),
    Case(
        "banxicoPublicTimeseries",
        "banxico-timeseries",
        lambda c: c.catalog.banxico_timeseries(metric="verdict", window="7d"),
        all_parameters=True,
    ),
    # ── Beneficiarios ───────────────────────────────────────────────────────
    Case(
        "createBeneficiary",
        "beneficiary-created",
        lambda c: c.beneficiaries.create(
            account_number="5512345678", bank_code="40012", label="Proveedor X"
        ),
        whole_body=True,
    ),
    Case(
        "listBeneficiaries",
        "beneficiaries-list",
        lambda c: c.beneficiaries.list(with_archived="0"),
        all_parameters=True,
    ),
    Case(
        "updateBeneficiary",
        "beneficiary-updated",
        lambda c: c.beneficiaries.update(
            "12", label="Proveedor Y", account_number="012180004412345678", bank_code="40012"
        ),
        whole_body=True,
    ),
    Case("deleteBeneficiary", "no-content", lambda c: c.beneficiaries.delete("12")),
    Case(
        "lookupBeneficiaryAccount",
        "beneficiary-lookup",
        lambda c: c.beneficiaries.lookup("012180004412345678"),
        all_parameters=True,
    ),
    Case(
        "validateAccount",
        "account-validation",
        lambda c: c.beneficiaries.validate_account("012180004412345678", type="clabe"),
        all_parameters=True,
    ),
    Case(
        "exportBeneficiaries",
        "beneficiaries-export-csv",
        lambda c: c.beneficiaries.export(format="xlsx", with_archived="1", limit=10),
        all_parameters=True,
    ),
    Case(
        "downloadBeneficiaryImportTemplate",
        "beneficiaries-import-template",
        lambda c: c.beneficiaries.import_template(format="xlsx"),
        all_parameters=True,
    ),
    Case(
        "createBeneficiaryImport",
        "beneficiaries-import-created",
        lambda c: c.beneficiaries.import_start(
            b"cuenta,alias\n012180004412345678,Proveedor X\n",
            parse_mode="free",
            filename="lista.csv",
        ),
        whole_body=True,
    ),
    Case(
        "getBeneficiaryImport",
        "beneficiaries-import-status",
        lambda c: c.beneficiaries.import_status(IMPORT),
    ),
    Case(
        "cancelBeneficiaryImport",
        "no-content",
        lambda c: c.beneficiaries.import_cancel(IMPORT),
    ),
    Case(
        "getBeneficiaryImportPreview",
        "beneficiaries-import-preview",
        lambda c: c.beneficiaries.import_preview(IMPORT, page=2, per_page=5, buckets=["valid"]),
        all_parameters=True,
    ),
    Case(
        "patchBeneficiaryImportRow",
        "beneficiaries-import-row-updated",
        lambda c: c.beneficiaries.import_edit_row(
            IMPORT,
            ROW,
            parsed_account="012180004412345678",
            parsed_label="Proveedor X",
            parsed_account_type="clabe",
            parsed_bank_code="40012",
            parsed_bank_name="BBVA MEXICO",
        ),
        whole_body=True,
    ),
    Case(
        "deleteBeneficiaryImportRow",
        "no-content",
        lambda c: c.beneficiaries.import_remove_row(IMPORT, ROW),
    ),
    Case(
        "commitBeneficiaryImport",
        "beneficiaries-import-committed",
        lambda c: c.beneficiaries.import_commit(IMPORT),
    ),
    # ── Consumo ─────────────────────────────────────────────────────────────
    Case("getUsageSummary", "usage-summary", lambda c: c.usage.summary()),
    Case(
        "getUsageHistory",
        "usage-history",
        lambda c: c.usage.history(months=3),
        all_parameters=True,
    ),
    Case(
        "getUsageBreakdown",
        "usage-breakdown",
        lambda c: c.usage.breakdown(period="current"),
        all_parameters=True,
    ),
    Case("getUsageLimits", "usage-limits", lambda c: c.usage.limits()),
    Case(
        "getUsageHeatmap",
        "usage-heatmap",
        lambda c: c.usage.heatmap(days=30),
        all_parameters=True,
    ),
    Case("getApiUsage", "api-usage", lambda c: c.usage.api_usage()),
    Case(
        "exportApiUsage",
        "api-usage-export-csv",
        lambda c: c.usage.export(format="xlsx", from_="2025-01-01", to="2025-03-31", limit=100),
        all_parameters=True,
    ),
    # ── Account, dashboard, plans, insights, finance and billing ───────────
    Case("myProfile", "account-profile", lambda c: c.account.my_profile()),
    Case(
        "getMyRetryPolicy",
        "account-retry-policy",
        lambda c: c.account.get_my_retry_policy(),
    ),
    Case(
        "updateMyRetryPolicy",
        "account-retry-policy-updated",
        lambda c: c.account.update_my_retry_policy(POLICY, idempotency_key="pedido-5"),
        whole_body=True,
    ),
    Case(
        "getDashboardSummary",
        "dashboard-summary",
        lambda c: c.dashboard.get_summary(limit=5),
        all_parameters=True,
    ),
    Case("listPublicPlans", "public-plans", lambda c: c.plans.list_public()),
    Case(
        "getPublicPlanComparison",
        "public-plan-comparison",
        lambda c: c.plans.get_public_plan_comparison(),
    ),
    Case("getUserInsightsOverview", "insights-overview", lambda c: c.insights.get_overview()),
    Case(
        "getUserInsightsTrends",
        "insights-trends",
        lambda c: c.insights.get_trends(range="30d", metric="latency"),
        all_parameters=True,
    ),
    Case(
        "getUserInsightsTopBanks",
        "insights-top-banks",
        lambda c: c.insights.get_top_banks(metric="errors", limit=10),
        all_parameters=True,
    ),
    Case(
        "getUserInsightsTopBeneficiaries",
        "insights-top-beneficiaries",
        lambda c: c.insights.get_top_beneficiaries(limit=10),
        all_parameters=True,
    ),
    Case(
        "getFinanceSummary",
        "finance-summary",
        lambda c: c.finance.get_summary(month="2026-04", user_id=ID),
        all_parameters=True,
    ),
    Case(
        "getFinanceStatement",
        "finance-statement",
        lambda c: c.finance.get_statement(month="2026-04", format="pdf", user_id=ID),
        all_parameters=True,
    ),
    Case(
        "getFinanceMonthly",
        "finance-monthly",
        lambda c: c.finance.get_monthly(month="2026-04", format="preview", user_id=ID, limit=100),
        all_parameters=True,
    ),
    Case(
        "getFinanceCounterparties",
        "finance-counterparties",
        lambda c: c.finance.get_counterparties(
            month="2026-04", format="preview", user_id=ID, limit=100
        ),
        all_parameters=True,
    ),
    Case(
        "getFinanceByBank",
        "finance-by-bank",
        lambda c: c.finance.get_by_bank(month="2026-04", format="preview", user_id=ID, limit=100),
        all_parameters=True,
    ),
    Case(
        "getFinanceAccounting",
        "finance-accounting",
        lambda c: c.finance.get_accounting(
            month="2026-04", format="preview", user_id=ID, limit=100, decimal="dot"
        ),
        all_parameters=True,
    ),
    Case(
        "getFinanceCeps",
        "finance-ceps",
        lambda c: c.finance.get_ceps(from_="2026-04-01", to="2026-04-30", user_id=ID),
        all_parameters=True,
    ),
    Case(
        "createFinanceCepExport",
        "finance-cep-export-queued",
        lambda c: c.finance.create_cep_export(
            from_="2026-04-01", to="2026-04-30", user_id=ID, idempotency_key="pedido-6"
        ),
        whole_body=True,
    ),
    Case(
        "listFinanceCepExports",
        "finance-cep-exports",
        lambda c: c.finance.list_cep_exports(),
    ),
    Case(
        "getFinanceCepExport",
        "finance-cep-export-ready",
        lambda c: c.finance.get_cep_export(ID),
    ),
    Case(
        "downloadFinanceCepExport",
        "finance-ceps",
        lambda c: c.finance.download_cep_export(ID),
    ),
    Case(
        "billingGetSubscription",
        "billing-subscription",
        lambda c: c.billing.get_subscription(),
    ),
]


IMPLEMENTED_OPERATIONS = {
    "validateDirect",
    "validateOcr",
    "listValidations",
    "getValidation",
    "downloadCep",
    "getValidationImage",
    "validationStats",
    "exportValidations",
    "listValidationRetryAttempts",
    "updateValidationRetryPolicy",
    "cancelValidationRetries",
    "deleteValidation",
    "sendCepToTelegram",
    "recheckValidation",
    "prepareValidationPurge",
    "executeValidationPurge",
    "createWebhook",
    "listWebhooks",
    "updateWebhook",
    "deleteWebhook",
    "sendWebhookTest",
    "regenerateWebhookSecret",
    "listWebhookDeliveries",
    "listAllDeliveries",
    "exportWebhookDeliveries",
    "exportAllDeliveries",
    "listBanks",
    "lookupBin",
    "getSpeiCalendar",
    "banxicoPublicStatus",
    "banxicoPublicTimeseries",
    "createBeneficiary",
    "listBeneficiaries",
    "updateBeneficiary",
    "deleteBeneficiary",
    "lookupBeneficiaryAccount",
    "validateAccount",
    "exportBeneficiaries",
    "downloadBeneficiaryImportTemplate",
    "createBeneficiaryImport",
    "getBeneficiaryImport",
    "cancelBeneficiaryImport",
    "getBeneficiaryImportPreview",
    "patchBeneficiaryImportRow",
    "deleteBeneficiaryImportRow",
    "commitBeneficiaryImport",
    "getUsageSummary",
    "getUsageHistory",
    "getUsageBreakdown",
    "getUsageLimits",
    "getUsageHeatmap",
    "getApiUsage",
    "exportApiUsage",
    "myProfile",
    "getMyRetryPolicy",
    "updateMyRetryPolicy",
    "getDashboardSummary",
    "listPublicPlans",
    "getPublicPlanComparison",
    "getUserInsightsOverview",
    "getUserInsightsTrends",
    "getUserInsightsTopBanks",
    "getUserInsightsTopBeneficiaries",
    "getFinanceSummary",
    "getFinanceStatement",
    "getFinanceMonthly",
    "getFinanceCounterparties",
    "getFinanceByBank",
    "getFinanceAccounting",
    "getFinanceCeps",
    "createFinanceCepExport",
    "listFinanceCepExports",
    "getFinanceCepExport",
    "downloadFinanceCepExport",
    "billingGetSubscription",
}

# Must stay empty: the test below prevents turning it into a permanent allowlist.
KNOWN_GAPS: set[str] = set()


def m2m_operation_ids() -> set[str]:
    return {
        operation["operationId"] for _, _, operation in operations() if accepts_api_key(operation)
    }


def coverage_errors(contract: set[str], implemented: set[str], known_gaps: set[str]) -> list[str]:
    errors: list[str] = []
    missing = sorted(contract - implemented - known_gaps)
    obsolete = sorted(implemented - contract)
    implemented_gaps = sorted(known_gaps & implemented)
    stale_gaps = sorted(known_gaps - contract)

    if missing:
        errors.append("Missing M2M methods: " + ", ".join(missing))
    if obsolete:
        errors.append("Methods outside the M2M contract: " + ", ".join(obsolete))
    if implemented_gaps:
        errors.append("known_gaps already has a method: " + ", ".join(implemented_gaps))
    if stale_gaps:
        errors.append("known_gaps no longer exists in the contract: " + ", ".join(stale_gaps))
    return errors


@pytest.mark.parametrize(
    "case", CASES, ids=[f"{c.operation}-{c.recording}-{n}" for n, c in enumerate(CASES)]
)
def test_what_the_sdk_sends_exists_in_the_spec(
    case: Case, client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording(case.recording)

    case.call(client)

    sent = server.requests[0]
    url = urlsplit(sent.path)
    template, operation = find_operation(sent.method, re.sub(r"^/v1", "", url.path))

    assert operation["operationId"] == case.operation, f"{sent.method} {url.path}"
    assert accepts_api_key(operation), f"{template} does not accept the API key"

    query = sorted(name.removesuffix("[]") for name in parse_qs(url.query))
    query_names = declared(template, operation, "query")
    for name in query:
        assert name in query_names, f"{template}: the spec does not declare the parameter {name}"
    if case.all_parameters:
        assert query == sorted(query_names)

    headers = {name.lower() for name in declared(template, operation, "header")}
    for name in ("idempotency-key", "if-none-match"):
        if name in sent.headers:
            assert name in headers, f"{template}: the spec does not declare the header {name}"

    if sent.body:
        kind = sent.headers["content-type"].split(";")[0].strip()
        properties = request_body_properties(operation, kind)
        if kind == "application/json":
            fields = sorted(json.loads(sent.body))
        else:
            fields = sorted({name.decode() for name in re.findall(rb'; name="([^"]+)"', sent.body)})
        for field in fields:
            assert field in properties, f"{template}: the spec does not declare the field {field}"
        if case.whole_body:
            assert fields == sorted(set(properties) - case.without_fields)


def test_covers_exactly_the_75_operations_of_the_contract_without_partial_families() -> None:
    covered = {case.operation for case in CASES}

    assert len(IMPLEMENTED_OPERATIONS) == 75
    assert covered == IMPLEMENTED_OPERATIONS
    assert coverage_errors(m2m_operation_ids(), IMPLEMENTED_OPERATIONS, KNOWN_GAPS) == []


def test_every_operation_of_the_spec_accepts_the_api_key() -> None:
    sin_clave = {
        operation["operationId"]
        for _, _, operation in operations()
        if not accepts_api_key(operation)
    }

    assert sin_clave == set(), "Operations that do not accept the API key: " + ", ".join(
        sorted(sin_clave)
    )
    assert sin_clave & IMPLEMENTED_OPERATIONS == set()


def test_requires_removing_temporary_gaps_before_release() -> None:
    assert not KNOWN_GAPS, "known_gaps must be empty before releasing the SDK"


def test_fails_closed_if_an_m2m_operation_is_added_removed_or_hidden() -> None:
    contract = m2m_operation_ids()
    removed = "myProfile"
    future = "futureM2mOperation"

    assert coverage_errors(contract | {future}, IMPLEMENTED_OPERATIONS, set()) == [
        "Missing M2M methods: " + future
    ]
    assert coverage_errors(contract - {removed}, IMPLEMENTED_OPERATIONS, set()) == [
        "Methods outside the M2M contract: " + removed
    ]
    assert coverage_errors(contract, IMPLEMENTED_OPERATIONS, {removed}) == [
        "known_gaps already has a method: " + removed
    ]
    assert coverage_errors(contract, IMPLEMENTED_OPERATIONS, {future}) == [
        "known_gaps no longer exists in the contract: " + future
    ]


def test_public_plans_work_without_an_api_key_and_send_no_authorization(
    server: RecordingServer,
) -> None:
    server.enqueue_recording("public-plans")
    client = Veriko(api_key="", base_url=server.base_url, max_retries=0)

    client.plans.list_public()

    assert server.requests[0].header("authorization") is None


def test_financial_reports_keep_csv_as_the_default_format(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("deliveries-export-csv", times=4)

    client.finance.get_monthly(month="2026-04")
    client.finance.get_counterparties(month="2026-04")
    client.finance.get_by_bank(month="2026-04")
    client.finance.get_accounting(month="2026-04")

    expected_paths = (
        "/v1/finance/monthly",
        "/v1/finance/counterparties",
        "/v1/finance/by-bank",
        "/v1/finance/accounting",
    )
    assert len(server.requests) == len(expected_paths)
    for request, expected_path in zip(server.requests, expected_paths):
        url = urlsplit(request.path)
        assert url.path == expected_path
        assert parse_qs(url.query)["month"] == ["2026-04"]
        assert parse_qs(url.query)["format"] == ["csv"]
