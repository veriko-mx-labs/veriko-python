"""Build a ZIP of CEPs in the background and download it.

export VERIKO_API_KEY=veriko_your_key_here
python examples/cep_export.py 2026-01-01 2026-01-31
"""

import sys

from veriko import Veriko

client = Veriko()

export = client.finance.create_cep_export(from_=sys.argv[1], to=sys.argv[2])
export = client.finance.wait_for_cep_export(export["id"])

if export["attributes"]["status"] == "ready":
    document = client.finance.download_cep_export(export["id"])
    document.write_to(document.filename)
    print("Saved", document.filename)
else:
    print("Export ended as", export["attributes"]["status"])
