"""Webhook receiver with Flask, with the signature verified.

    pip install flask
    export VERIKO_WEBHOOK_SECRET=the_endpoint_secret
    flask --app examples/flask_webhook.py run --port 5000

`POST /v1/webhooks` returns the secret when the endpoint is registered, only once.
If it is lost, it is rotated with `POST /v1/webhooks/{id}/regenerate-secret`.

Three details of the receiver:

1. What is signed is the raw body. `request.get_data()` returns it as it arrived;
   `request.get_json()` has already parsed it, and serialising it again changes the
   bytes and breaks the signature.
2. The delivery has 10 seconds. Answering `2xx` first and processing afterwards
   avoids unnecessary retries.
3. `X-Webhook-Signature-Timestamped` also signs the time of the attempt. With it the
   receiver discards an old delivery that someone sends again: the default window
   is 5 minutes against `t`, not against the `timestamp` of the body.
"""

import os

from flask import Flask, request

from veriko import (
    SignatureVerificationError,
    parse_webhook,
    timestamped_signature_from_headers,
    verify_webhook_timestamped,
)

app = Flask(__name__)

SECRET = os.environ["VERIKO_WEBHOOK_SECRET"]

# Deliveries repeat: a retry carries the same Delivery-Id. In production this
# lives in Redis or a table, not in memory.
seen_deliveries = set()

# The amount of each order, as this app expects it. In production it comes from
# the orders database, not from an in-memory dictionary.
orders = {"f47ac10b-58cc-4372-a567-0e02b2c3d479": 15000.50}


@app.post("/hooks/veriko")
def receive():
    delivery_id = request.headers.get("X-Veriko-Delivery-Id")

    body = request.get_data()  # the raw body, not parsed

    if not verify_webhook_timestamped(
        body, timestamped_signature_from_headers(request.headers), SECRET
    ):
        # A signature that does not match, or a delivery outside the window: not processed.
        return "", 400

    try:
        event = parse_webhook(body, request.headers.get("X-Webhook-Signature"), SECRET)
    except SignatureVerificationError:
        # A signature that does not match: the body is not processed.
        return "", 400

    if delivery_id in seen_deliveries:
        return "", 200  # already processed; the retry is acknowledged and ignored
    seen_deliveries.add(delivery_id)

    if event.event == "validation.completed" and event.validation is not None:
        validation = event.validation
        print(f"[{event.event}] {validation.id} -> {validation.status}")
        if validation.client_ref:
            print("  order:", validation.client_ref)
        if validation.has_cep:
            print("  receipt available at", validation.links.get("cep_pdf"))

        confirmed = event.banxico_confirmed
        if confirmed is not None:
            # Banxico confirmed the payment: this amount, not the one on a receipt
            # image, is the one to compare with the order before releasing the
            # goods. `confirmed.beneficiaryAccount` works the same to compare the
            # account when the order records it.
            expected_amount = orders.get(validation.id)
            if confirmed.amount == expected_amount:
                print("  amount confirmed by Banxico:", confirmed.amount)
            else:
                print("  confirmed amount does not match the order:", confirmed.amount)

    elif event.event == "validation.returned" and event.validation is not None:
        # The validation was `valid` and Banxico reported the return afterwards.
        # Subscribe the endpoint to this event as well as to `validation.completed`.
        payment_status = event.payment_status
        code = payment_status.code if payment_status is not None else "desconocido"
        print(f"[{event.event}] {event.validation.id} -> {event.validation.status} ({code})")
        if event.validation.client_ref:
            print("  order to review:", event.validation.client_ref)

    elif event.event == "validation.retry.resolved" and event.validation is not None:
        state = event.validation.retry_state
        attempts = state.attempts_completed if state else 0
        print(f"[{event.event}] resolved after {attempts} retry(ies)")

    elif event.event == "validation.retry.exhausted":
        print(f"[{event.event}] retries exhausted without a firm verdict")

    return "", 200
