"""Queue validations, register the webhook that collects them, and poll.

    export VERIKO_API_KEY=veriko_your_key_here
    python examples/async_and_webhooks.py

This is the path for volume: the API accepts each request at once and the verdict
arrives later, by webhook or by polling.
"""

import sys

from veriko import APIError, Veriko

client = Veriko()

# 1. The endpoint that will receive the verdicts. The secret travels only once.
endpoint = client.webhooks.create(
    url="https://myapp.example.com/hooks/veriko",
    events=["validation.completed", "validation.retry.resolved"],
)
print("Endpoint", endpoint.id, "registered")
print("Signing secret:", endpoint.secret, "- store it now")

# 2. A test event confirms the receiver is reachable before real traffic goes out.
test = client.webhooks.test(endpoint.id)
if not test.delivered:
    print("The receiver did not accept the test event:", test.error)
    sys.exit(1)
print("Test delivered in", test.response_time_ms, "ms")

# 3. The transfers to validate. The idempotency key comes from the order number,
#    not from a random value: a resend then does not duplicate the charge.
#    `client_ref` ties the validation to your own order.
transfers = [
    {
        "send_date": "2025-03-15",
        "amount": 15000.50,
        "tracking_key": "MXBA20250315001234",
        "beneficiary_account": "012180004412345678",
        "client_ref": "order-4f3a2b1c",
        "idempotency_key": "order-4f3a2b1c",
    },
    {
        "send_date": "2025-03-15",
        "amount": 2300.00,
        "tracking_key": "MXBA20250315005678",
        "beneficiary_account": "012180004412345678",
        "client_ref": "order-7c2d9e0a",
        "idempotency_key": "order-7c2d9e0a",
    },
]

queued_validations = []
for transfer in transfers:
    try:
        queued_validations.append(client.validations.enqueue(**transfer))
    except APIError as error:
        print("Rejected:", error.code, "-", error.detail)

print("Queued", len(queued_validations), "validations")

# 4. With the webhook registered, the verdict arrives on its own. Polling is for a
#    process that needs the result before it continues.
for queued in queued_validations:
    validation = client.validations.wait_for(queued.id, timeout=120)
    print(validation.id, "->", validation.status)
    if validation.has_cep:
        cep = client.validations.cep(validation.id, format="pdf")
        cep.write_to(cep.filename)
        print("  receipt at", cep.filename)
