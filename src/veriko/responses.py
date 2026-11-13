"""Typed results of the operations that return the attributes of a resource.

Each class is a dictionary with the fields of its response schema as attributes.
`usage.history()["months"]` and `usage.history().months` read the same value; the
object compares equal to the dictionary and serializes to the same JSON. `id`,
`type`, `links`, `meta` and `raw` hold the parts of the response outside the
attributes, and `response_info` the headers.

A field the response does not carry reads as `None`. The names of the fields are the
ones of the spec; `from_` stands for `from`.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ._base import ApiObject, NestedFields

# States of a CEP export. The last three are terminal: the export no longer changes.
CEP_EXPORT_STATUSES = ("queued", "building", "ready", "failed", "expired")
CEP_EXPORT_TERMINAL_STATUSES = ("ready", "failed", "expired")

# Why a CEP export ends in `failed`.
CEP_EXPORT_ERROR_CODES = ("range_too_large", "no_ceps", "storage_full", "build_failed")


# ── Validations and catalog ────────────────────────────────────────────────


class ValidationStats(ApiObject):
    """Totals of the validations of the account (`GET /validations/stats`)."""

    total: int
    valid: int
    not_found: int
    cep_unavailable: int
    returned: int
    error: int
    pending: int
    deleted: int
    other: int
    by_type: dict[str, int]
    by_status: dict[str, int]


class TelegramQueued(ApiObject):
    """Acknowledgement of the receipt sent to Telegram (`queued` is `True`)."""

    queued: bool


class BinLookup(ApiObject):
    """The issuing bank of a card (`GET /public/bin-lookup/{bin}`)."""

    bin: str
    bank_name: str
    banxico_code: str | None
    card_brand: str | None
    card_type: str | None
    card_level: str | None
    country_iso: str


class BanxicoStatus(ApiObject):
    """Status of the Banxico query service (`GET /status/banxico`).

    `status` is `operational`, `degraded`, `down` or `unknown`.
    """

    status: str
    status_label: str
    message: str
    last_verified_at: str | None
    estimated_recovery_at: str | None
    check_interval_seconds: int


class BanxicoTimeseries(ApiObject):
    """Health time series of the Banxico service (`GET /status/banxico/timeseries`)."""

    metric: str
    window: str
    unit: str
    bucket_size_minutes: int
    points: list[dict[str, Any]]


# ── Usage ──────────────────────────────────────────────────────────────────


class UsageHistory(ApiObject):
    """Monthly consumption, most recent first (`GET /usage/history`)."""

    months: int
    history: list[dict[str, Any]]


class UsageBreakdown(ApiObject):
    """Consumption by type of counted operation (`GET /usage/breakdown`)."""

    period: str
    operations: list[dict[str, Any]]


class UsageLimits(ApiObject):
    """The rate limits in force, by context (`GET /usage/limits`)."""

    rate_limits: dict[str, Any]
    notes: list[str]


class UsageHeatmap(ApiObject):
    """Validations grouped by day and hour (`GET /usage/heatmap`)."""

    days: int
    max_count: int
    buckets: list[dict[str, Any]]


class PlanQuota(ApiObject):
    """The plan quota at a point in time."""

    plan_slug: str
    plan_name: str
    limit: int
    used: int
    remaining: int
    resets_at: str


class ApiUsage(ApiObject):
    """API usage metrics of the account (`GET /api/usage`)."""

    _content: ClassVar[str] = "data"

    requests_today: int
    requests_month: int
    quota: PlanQuota
    recent_requests: list[dict[str, Any]]
    banxico_status: str
    usage_history: list[dict[str, Any]]

    _nested: ClassVar[NestedFields] = {"quota": (PlanQuota, False)}


# ── Account, dashboard and billing ─────────────────────────────────────────


class UserProfile(ApiObject):
    """Profile of the authenticated account (`GET /users/me`).

    `id` is the identifier of the account.
    """

    email: str
    name: str
    status: str
    timezone: str
    language: str | None
    email_verified_at: str | None
    created_at: str
    api_key: dict[str, Any] | None
    subscription: dict[str, Any] | None
    two_factor: dict[str, Any]
    permissions: list[str]
    platform_access: bool
    roles: list[dict[str, Any]]
    notifications: dict[str, Any]
    legal_accepted: bool


class SavedRetryPolicy(ApiObject):
    """The default retry policy of the account (`/users/me/retry-policy`)."""

    enabled: bool
    max_retries: int
    interval_seconds: int
    outcomes: list[str]


class DashboardSummary(ApiObject):
    """Dashboard summary of the account (`GET /summary`).

    `relationships` carries the latest validations, as the response sends them.
    """

    total_validations: int
    monthly_validations: int
    pending_month: int
    valid_validations: int
    avg_ticket: float
    success_rate_percent: float
    beneficiaries_count: int
    onboarding_milestones_completed: int

    @property
    def relationships(self) -> dict[str, Any]:
        data = self.raw.get("data")
        relationships = data.get("relationships") if isinstance(data, dict) else None
        return relationships if isinstance(relationships, dict) else {}


class BillingSubscription(ApiObject):
    """The active subscription of the account (`GET /billing/subscription`)."""

    plan_slug: str
    plan_name: str
    billing_model: str
    source: str
    status: str
    current_period_start: str
    current_period_end: str
    currency: str
    billing_interval: str
    overage_enabled: bool
    included_validations: int | None
    monthly_validation_limit: int
    beneficiaries_max: int
    bulk_validations_max: int
    effective_limit: int
    is_stripe: bool
    stripe_subscription_id: str | None
    grant_reason: str | None
    cancel_at_period_end: bool
    cancel_at: str | None
    canceled_at: str | None
    trial_start: str | None
    trial_end: str | None


# ── Plans ──────────────────────────────────────────────────────────────────


class PlanPrice(ApiObject):
    """A price of a public plan."""

    currency: str
    billing_interval: str
    kind: str
    unit_amount: int | None
    tax_behavior: str


class PlanFeature(ApiObject):
    """A line of the feature list of a public plan."""

    text_es: str
    text_en: str
    icon: str
    sort_order: int
    is_active: bool


class PublicPlan(ApiObject):
    """A plan of the public catalog (`GET /plans/public`).

    The dictionary is the resource, with `type`, `id` and `attributes`; the fields
    below read from `attributes`.
    """

    _content: ClassVar[str] = "data"
    _reads: ClassVar[str] = "attributes"

    slug: str
    name: str
    billing_model: str
    monthly_validation_limit: int
    included_validations: int | None
    trial_days: int | None
    is_default: bool
    sort_order: int
    prices: list[PlanPrice]
    features: list[PlanFeature]

    _nested: ClassVar[NestedFields] = {
        "prices": (PlanPrice, True),
        "features": (PlanFeature, True),
    }


class PlanComparison(ApiObject):
    """The comparison table of the public plans (`GET /plans/public/comparison`).

    The dictionary is the resource, with `type` and `attributes`.
    """

    _content: ClassVar[str] = "data"
    _reads: ClassVar[str] = "attributes"

    plans: list[dict[str, Any]]
    rows: list[dict[str, Any]]


# ── Insights ───────────────────────────────────────────────────────────────


class InsightsOverview(ApiObject):
    """Totals and success rate of the account (`GET /insights/overview`)."""

    total: int
    today: int
    yesterday: int
    success_rate: float
    avg_latency_ms: int
    by_banxico_7d: dict[str, Any]
    scope: str


class InsightsTrends(ApiObject):
    """A time series of volume or latency (`GET /insights/trends`)."""

    range: str
    metric: str
    scope: str
    series: list[dict[str, Any]]


class InsightsTopBanks(ApiObject):
    """The banks with the most volume or errors (`GET /insights/top-banks`)."""

    metric: str
    scope: str
    banks: list[dict[str, Any]]


class InsightsTopBeneficiaries(ApiObject):
    """The most validated beneficiaries (`GET /insights/top-beneficiaries`)."""

    beneficiaries: list[dict[str, Any]]


# ── Finance ────────────────────────────────────────────────────────────────


class FinanceTopBank(ApiObject):
    """A bank in the ranking of a financial summary."""

    bank_code: str
    bank_name: str
    count: int
    valid: int
    verified_volume: float


class FinanceSummary(ApiObject):
    """The financial summary of a month (`GET /finance/summary`)."""

    period: dict[str, Any]
    counts: dict[str, Any]
    amounts: dict[str, Any]
    success_rate: float
    avg_processing_ms: int
    daily_breakdown: list[dict[str, Any]]
    top_counterparties: list[dict[str, Any]]
    top_banks_receiver: list[FinanceTopBank]
    top_banks_sender: list[FinanceTopBank]
    verdict_distribution: dict[str, Any]
    comparison_prev_month: dict[str, Any]
    statement_folio: str

    _nested: ClassVar[NestedFields] = {
        "top_banks_receiver": (FinanceTopBank, True),
        "top_banks_sender": (FinanceTopBank, True),
    }


class FinancePreview(ApiObject):
    """The rows of a financial report as JSON, with `format="preview"`.

    `report` is `monthly`, `counterparties`, `by-bank` or `accounting`.
    """

    report: str
    month: str
    headers: list[str]
    rows: list[list[str]]


class CepExport(ApiObject):
    """A CEP export built in the background (`/finance/ceps/exports`).

    The dictionary is the resource: `id`, `type`, `attributes` and `links`. The
    fields below read from `attributes`. `status` is `queued`, `building`, `ready`,
    `failed` or `expired`; the last three are terminal. With `ready`,
    `download_path` carries the path of the ZIP and `error_code` is `None`; with
    `failed`, `error_code` tells why (`CEP_EXPORT_ERROR_CODES`).
    """

    _content: ClassVar[str] = "data"
    _reads: ClassVar[str] = "attributes"
    _keys: ClassVar[dict[str, str]] = {"from_": "from"}

    status: str
    from_: str
    to: str
    item_count: int | None
    size_bytes: int | None
    error_code: str | None
    created_at: str
    completed_at: str | None
    expires_at: str | None

    @property
    def download_path(self) -> str | None:
        """`links.download`: where the ZIP of a `ready` export is."""
        links = self.get("links")
        value = links.get("download") if isinstance(links, dict) else None
        return value if isinstance(value, str) else None

    @property
    def is_terminal(self) -> bool:
        """`True` when the export reached `ready`, `failed` or `expired`."""
        return self.status in CEP_EXPORT_TERMINAL_STATUSES

    @property
    def is_ready(self) -> bool:
        """`True` when the ZIP can be downloaded."""
        return self.status == "ready"


__all__ = [
    "CEP_EXPORT_ERROR_CODES",
    "CEP_EXPORT_STATUSES",
    "CEP_EXPORT_TERMINAL_STATUSES",
    "ApiUsage",
    "BanxicoStatus",
    "BanxicoTimeseries",
    "BillingSubscription",
    "BinLookup",
    "CepExport",
    "DashboardSummary",
    "FinancePreview",
    "FinanceSummary",
    "FinanceTopBank",
    "InsightsOverview",
    "InsightsTopBanks",
    "InsightsTopBeneficiaries",
    "InsightsTrends",
    "PlanComparison",
    "PlanFeature",
    "PlanPrice",
    "PlanQuota",
    "PublicPlan",
    "SavedRetryPolicy",
    "TelegramQueued",
    "UsageBreakdown",
    "UsageHeatmap",
    "UsageHistory",
    "UsageLimits",
    "UserProfile",
    "ValidationStats",
]
