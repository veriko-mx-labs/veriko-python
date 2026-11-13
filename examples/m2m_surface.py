"""Operations of the public M2M surface.

Plans are public and do not send the key even if it is configured. For the
account, insights and finance calls, export VERIKO_API_KEY before running.
"""

from __future__ import annotations

import os

from veriko import Veriko

client = Veriko()

plans = client.plans.list_public()
print(plans)

if not os.environ.get("VERIKO_API_KEY"):
    print("Set VERIKO_API_KEY to query account, insights and finance.")
else:
    profile = client.account.my_profile()
    trends = client.insights.get_trends(range="30d", metric="latency")
    statement = client.finance.get_statement(month="2026-04", format="pdf")

    print(profile, trends)
    statement.write_to(statement.filename)
