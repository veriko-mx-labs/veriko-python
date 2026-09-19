"""Operaciones que se incorporaron a la superficie M2M pública.

Los planes son públicos y no envían la clave aunque esté configurada. Para las
llamadas de cuenta, insights y finanzas, exporta VERIKO_API_KEY antes de correr.
"""

from __future__ import annotations

import os

from veriko import Veriko

client = Veriko()

plans = client.plans.list_public()
print(plans)

if not os.environ.get("VERIKO_API_KEY"):
    print("Configura VERIKO_API_KEY para consultar cuenta, insights y finanzas.")
else:
    profile = client.account.my_profile()
    trends = client.insights.get_trends(range="30d", metric="latency")
    statement = client.finance.get_statement(month="2026-04", format="pdf")

    print(profile, trends)
    statement.write_to(statement.filename)
