"""The client: the entry point of the SDK and the operation families."""

from __future__ import annotations

import os
import platform
import sys
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from ._compat import merge_legacy_kwargs
from ._http import RetryConfig, Transport
from .models import CepDocument, RetryPolicy, Validation
from .resources import (
    CEP_FORMATS,
    Account,
    Beneficiaries,
    Billing,
    Catalog,
    Dashboard,
    Finance,
    Insights,
    Plans,
    Usage,
    Validations,
    Webhooks,
)

DEFAULT_BASE_URL = "https://api.veriko.mx/v1"
# Longer than the 45 seconds the API can wait for Banxico before answering 202.
DEFAULT_TIMEOUT_SECONDS = 60.0
API_KEY_ENV_VAR = "VERIKO_API_KEY"
BASE_URL_ENV_VAR = "VERIKO_BASE_URL"


def build_user_agent(prefix: str | None = None, suffix: str | None = None) -> str:
    """The `User-Agent` the SDK sends on every request.

    It has the form `veriko-sdk-python/<version> (<implementation> <Python
    version>; <system> <architecture>)`. `prefix` goes first, for a product that
    wraps the SDK and wants to identify itself (`"my-product/1.2"`), and `suffix`
    goes last. They are separated by a space and empty ones are omitted. It carries
    no data about the machine beyond the runtime, the system and the architecture.
    """
    from . import __version__

    runtime = (
        "("
        + platform.python_implementation().lower()
        + " "
        + platform.python_version()
        + "; "
        + sys.platform
        + " "
        + platform.machine()
        + ")"
    )
    parts = [prefix, "veriko-sdk-python/" + __version__ + " " + runtime, suffix]
    return " ".join(part.strip() for part in parts if part and part.strip())


def new_idempotency_key() -> str:
    """One key per call, stable across the retries of that same call.

    Retrying a `POST` without an idempotency key can duplicate the validation, and
    its charge, when the response was lost but the request arrived. With a key, the
    retry returns the original response.

    This key does not outlive the process that generated it. The one that protects
    a later resend is the one passed in `idempotency_key`.
    """
    return "veriko-python-" + uuid.uuid4().hex


class Veriko:
    """Client of the Veriko API.

    The API key is read from the `api_key` argument or, if it is not passed, from
    the `VERIKO_API_KEY` environment variable. It starts with `veriko_` and is
    obtained in the dashboard: https://app.veriko.mx

    Operations are grouped by family:

    - `client.validations`: validate, query, retry and download.
    - `client.webhooks`: endpoints and their delivery history.
    - `client.catalog`: banks and Banxico service status.
    - `client.beneficiaries`: saved accounts and their bulk import.
    - `client.usage`: quota, limits and activity log.
    - `client.account`: profile and default retry policy.
    - `client.dashboard`: dashboard summary.
    - `client.plans`: catalog and comparison of public plans.
    - `client.insights`: aggregated account metrics.
    - `client.finance`: financial summaries and downloads.
    - `client.billing`: active subscription.

    The three most used are also at the root, as a shortcut:

        >>> from veriko import Veriko
        >>> client = Veriko()
        >>> validation = client.validate_transfer(
        ...     send_date="2025-03-15",
        ...     amount=15000.50,
        ...     tracking_key="MXBA20250315001234",
        ...     beneficiary_account="012180004412345678",
        ... )
        >>> validation.status
        'valid'

    Args:
        api_key: the API key. Defaults to `VERIKO_API_KEY`.
        base_url: the root of the API. Defaults to `VERIKO_BASE_URL` or production.
        timeout: seconds to wait per attempt.
        max_retries: automatic retries besides the first attempt. `0` turns them
            off. Only what can be retried is retried: the `5xx`, the `408` and the
            `429`.
        accept_language: `es` or `en`, for the language of `error.detail`.
        retry: full retry configuration, if the backoff needs tuning. It wins over
            `max_retries`.
        user_agent_prefix: text that goes before the `User-Agent` of the SDK. It is
            for a product that wraps the SDK and wants to identify itself.
        user_agent_suffix: text that goes at the end of the `User-Agent`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 2,
        accept_language: str | None = None,
        retry: RetryConfig | None = None,
        user_agent_prefix: str | None = None,
        user_agent_suffix: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get(API_KEY_ENV_VAR) or ""
        resolved_base = base_url or os.environ.get(BASE_URL_ENV_VAR) or DEFAULT_BASE_URL

        self._transport = transport or Transport(
            base_url=resolved_base,
            api_key=resolved_key,
            timeout=timeout,
            retry=retry or RetryConfig(max_retries=max_retries),
            user_agent=build_user_agent(user_agent_prefix, user_agent_suffix),
            accept_language=accept_language,
        )

        self.validations = Validations(self._transport)
        self.webhooks = Webhooks(self._transport)
        self.catalog = Catalog(self._transport)
        self.beneficiaries = Beneficiaries(self._transport)
        self.usage = Usage(self._transport)
        self.account = Account(self._transport)
        self.dashboard = Dashboard(self._transport)
        self.plans = Plans(self._transport)
        self.insights = Insights(self._transport)
        self.finance = Finance(self._transport)
        self.billing = Billing(self._transport)

    def with_options(
        self,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        headers: Mapping[str, str] | None = None,
        accept_language: str | None = None,
    ) -> Veriko:
        """A client that shares this one's settings with some of them overridden.

        It is the per-call options mechanism: `client.with_options(timeout=5).validations.get(id)`.
        `headers` adds request headers, such as `Idempotency-Key` on a single call.
        The original client is not modified.
        """
        return Veriko(
            transport=self._transport.with_options(
                timeout=timeout,
                max_retries=max_retries,
                headers=headers,
                accept_language=accept_language,
            )
        )

    def close(self) -> None:
        """Closes the connections the client keeps open. It reconnects if used again."""
        self._transport.close()

    def __enter__(self) -> Veriko:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    # ── Shortcuts for the main use case ─────────────────────────────────────

    def validate_transfer(
        self,
        *,
        send_date: str | None = None,
        amount: float | int | str | Decimal | None = None,
        beneficiary_account: str | None = None,
        candidate_accounts: Sequence[str] | None = None,
        tracking_key: str | None = None,
        numeric_reference: str | None = None,
        sender_bank: str | None = None,
        receiver_bank: str | None = None,
        recipient_is_bank: bool | int | None = None,
        retry_policy: RetryPolicy | Mapping[str, Any] | None = None,
        client_ref: str | None = None,
        idempotency_key: str | None = None,
        **legacy: Any,
    ) -> Validation:
        """Shortcut for `client.validations.validate()`.

        Validates a SPEI transfer against the Banxico CEP. `amount` is required;
        `beneficiary_account`, or `candidate_accounts` when it is not known which
        account it was (one of the two, not both), and `tracking_key` or
        `numeric_reference`; the last two together narrow the search. The date is
        the **sending** date, as `YYYY-MM-DD`, goes in `send_date` and is optional:
        without it the API tries candidate dates. `client_ref` is your own
        reference, which comes back in the validation and in the webhooks.

        Field names from earlier versions keep working for existing integrations and
        emit a `DeprecationWarning`; they are not part of the typed API.

        Each call consumes plan quota, which is deducted when the request is
        accepted.
        """
        fields = merge_legacy_kwargs(
            {
                "send_date": send_date,
                "amount": amount,
                "beneficiary_account": beneficiary_account,
                "candidate_accounts": candidate_accounts,
                "tracking_key": tracking_key,
                "numeric_reference": numeric_reference,
                "sender_bank": sender_bank,
                "receiver_bank": receiver_bank,
                "recipient_is_bank": recipient_is_bank,
            },
            legacy,
            stacklevel=2,
        )
        return self.validations.validate(
            retry_policy=retry_policy,
            client_ref=client_ref,
            idempotency_key=idempotency_key,
            **fields,
        )

    def get_validation(self, validation_id: str, *, if_none_match: str | None = None) -> Validation:
        """Shortcut for `client.validations.get()`."""
        return self.validations.get(validation_id, if_none_match=if_none_match)

    def get_cep(self, validation_id: str, *, format: str = "xml") -> CepDocument:
        """Shortcut for `client.validations.cep()`.

        Downloads the official receipt: the XML Banxico issued, with its digital
        seal and its original string, or the equivalent PDF.
        """
        return self.validations.cep(validation_id, format=format)


__all__ = [
    "API_KEY_ENV_VAR",
    "BASE_URL_ENV_VAR",
    "CEP_FORMATS",
    "DEFAULT_BASE_URL",
    "Veriko",
    "build_user_agent",
    "new_idempotency_key",
]
