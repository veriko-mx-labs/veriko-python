"""Validate several transfers concurrently with the asynchronous client.

export VERIKO_API_KEY=veriko_your_key_here
python examples/async_client.py
"""

import asyncio

from veriko import AsyncVeriko


async def main() -> None:
    async with AsyncVeriko() as client:
        validations = await asyncio.gather(
            client.validate_transfer(
                send_date="2025-03-15",
                amount=15000.50,
                tracking_key="MXBA20250315001234",
                beneficiary_account="012180004412345678",
            ),
            client.with_options(timeout=10).validations.list(per_page=5),
        )
        print([getattr(item, "status", None) for item in validations])


asyncio.run(main())
