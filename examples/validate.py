"""Validate a SPEI transfer and download its CEP.

    export VERIKO_API_KEY=veriko_your_key_here
    python examples/validate.py

The transfer data goes in the constants below. The date is the sending date, as
YYYY-MM-DD, and the operation requires the tracking key or the numeric reference.
"""

import sys

from veriko import APIError, RateLimitError, Veriko

client = Veriko()  # reads VERIKO_API_KEY from the environment

try:
    validation = client.validate_transfer(
        send_date="2025-03-15",
        amount=15000.50,
        tracking_key="MXBA20250315001234",
        numeric_reference="1234567",
        sender_bank="BANCO NACIONAL DE MEXICO",
        receiver_bank="BBVA MEXICO",
        beneficiary_account="012180004412345678",
        idempotency_key="example-validate-001",
    )
except RateLimitError as error:
    print("Rate limit or quota exhausted. Retry in", error.retry_after, "seconds")
    sys.exit(1)
except APIError as error:
    print("The API rejected the request:", error.code, "-", error.detail)
    print("request_id:", error.request_id)
    sys.exit(1)

print("Validation:", validation.id)
print("Verdict:   ", validation.status)
print("Banxico:   ", validation.banxico_status)
print("Took:      ", validation.processing_time_ms, "ms")

if not validation.has_cep:
    print("No receipt to download.")
    sys.exit(0)

cep = client.get_cep(validation.id, format="pdf")
cep.write_to(cep.filename)
print("CEP saved to", cep.filename, f"({len(cep.content)} bytes)")
