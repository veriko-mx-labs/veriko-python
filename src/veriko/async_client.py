"""The asynchronous client.

`AsyncVeriko` has the same families and methods as `Veriko`, as coroutines. The SDK
has no runtime dependencies, so it does not open its own asynchronous connections:
each call runs the synchronous method in a worker thread (`asyncio.to_thread`),
which keeps the event loop free while the request is in flight.

    async with AsyncVeriko() as client:
        validation = await client.validate_transfer(amount=15000.50, ...)
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any

from ._async_resources import (
    AsyncAccount,
    AsyncBeneficiaries,
    AsyncBilling,
    AsyncCatalog,
    AsyncDashboard,
    AsyncFinance,
    AsyncInsights,
    AsyncPlans,
    AsyncUsage,
    AsyncValidations,
    AsyncWebhooks,
)
from .client import Veriko
from .models import CepDocument, Validation


class AsyncVeriko:
    """Asynchronous client of the Veriko API.

    It takes the same arguments as `Veriko`. Every family is an attribute with the
    same name, and every method is a coroutine; the `iter_*` methods are
    asynchronous iterators.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._attach(Veriko(*args, **kwargs))

    def _attach(self, client: Veriko) -> None:
        self._client = client
        self.validations = AsyncValidations(client.validations)
        self.webhooks = AsyncWebhooks(client.webhooks)
        self.catalog = AsyncCatalog(client.catalog)
        self.beneficiaries = AsyncBeneficiaries(client.beneficiaries)
        self.usage = AsyncUsage(client.usage)
        self.account = AsyncAccount(client.account)
        self.dashboard = AsyncDashboard(client.dashboard)
        self.plans = AsyncPlans(client.plans)
        self.insights = AsyncInsights(client.insights)
        self.finance = AsyncFinance(client.finance)
        self.billing = AsyncBilling(client.billing)

    @classmethod
    def _wrap(cls, client: Veriko) -> AsyncVeriko:
        wrapped = cls.__new__(cls)
        wrapped._attach(client)
        return wrapped

    @property
    def base_url(self) -> str:
        return self._client.base_url

    def with_options(self, **options: Any) -> AsyncVeriko:
        """A client with some settings overridden. See `Veriko.with_options()`."""
        return self._wrap(self._client.with_options(**options))

    async def validate_transfer(self, **fields: Any) -> Validation:
        """Shortcut for `client.validations.validate()`."""
        return await self.validations.validate(**fields)

    async def get_validation(
        self, validation_id: str, *, if_none_match: str | None = None
    ) -> Validation:
        """Shortcut for `client.validations.get()`."""
        return await self.validations.get(validation_id, if_none_match=if_none_match)

    async def get_cep(self, validation_id: str, *, format: str = "xml") -> CepDocument:
        """Shortcut for `client.validations.cep()`."""
        return await self.validations.cep(validation_id, format=format)

    async def aclose(self) -> None:
        """Closes the connections the client keeps open."""
        await asyncio.to_thread(self._client.close)

    async def __aenter__(self) -> AsyncVeriko:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()


__all__ = ["AsyncVeriko"]
