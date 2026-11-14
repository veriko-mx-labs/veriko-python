"""Receptor de webhooks con Flask, con la firma verificada.

    pip install flask
    export VERIKO_WEBHOOK_SECRET=el_secreto_del_endpoint
    flask --app examples/flask_webhook.py run --port 5000

El secreto lo devuelve `POST /v1/webhooks` al registrar el endpoint, una sola
vez. Si se pierde, se rota con `POST /v1/webhooks/{id}/regenerate-secret`.

Tres detalles del receptor:

1. Lo que se firma es el cuerpo crudo. `request.get_data()` lo devuelve tal como
   llegó; `request.get_json()` ya lo interpretó, y volver a serializarlo cambia
   los bytes y rompe la firma.
2. La entrega dispone de 10 segundos. Responder `2xx` primero y procesar después
   evita reintentos innecesarios.
3. `X-Webhook-Signature-Timestamped` firma también la hora del intento. Con ella el
   receptor descarta una entrega vieja que alguien vuelva a enviar: la ventana por
   omisión es de 5 minutos contra `t`, no contra el `timestamp` del cuerpo.
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

# Las entregas se repiten: un reintento trae el mismo Delivery-Id. En producción
# esto vive en Redis o en una tabla, no en memoria.
seen_deliveries = set()

# El monto de cada pedido, tal como lo espera esta app. En producción sale de
# la base de datos de pedidos, no de un diccionario en memoria.
orders = {"f47ac10b-58cc-4372-a567-0e02b2c3d479": 15000.50}


@app.post("/hooks/veriko")
def receive():
    delivery_id = request.headers.get("X-Veriko-Delivery-Id")

    body = request.get_data()  # el cuerpo crudo, sin interpretar

    if not verify_webhook_timestamped(
        body, timestamped_signature_from_headers(request.headers), SECRET
    ):
        # Firma que no cuadra, o entrega fuera de la ventana: no se procesa.
        return "", 400

    try:
        event = parse_webhook(body, request.headers.get("X-Webhook-Signature"), SECRET)
    except SignatureVerificationError:
        # Firma que no cuadra: el cuerpo no se procesa.
        return "", 400

    if delivery_id in seen_deliveries:
        return "", 200  # ya se procesó; el reintento se confirma y se ignora
    seen_deliveries.add(delivery_id)

    if event.event == "validation.completed" and event.validation is not None:
        validation = event.validation
        print(f"[{event.event}] {validation.id} -> {validation.status}")
        if validation.client_ref:
            print("  pedido:", validation.client_ref)
        if validation.has_cep:
            print("  comprobante disponible en", validation.links.get("cep_pdf"))

        confirmed = event.banxico_confirmed
        if confirmed is not None:
            # Banxico confirmó el pago: este monto, no el de una imagen de
            # comprobante, es el que hay que comparar contra el pedido antes de
            # liberar la mercancía. `confirmed.beneficiary_account` sirve igual
            # para comparar la cuenta cuando el pedido la registra.
            expected_amount = orders.get(validation.id)
            if confirmed.amount == expected_amount:
                print("  monto confirmado por Banxico:", confirmed.amount)
            else:
                print("  el monto confirmado no coincide con el pedido:", confirmed.amount)

    elif event.event == "validation.returned" and event.validation is not None:
        # La validación había salido `valid` y Banxico reportó la devolución después.
        # Suscribe el endpoint a este evento además de a `validation.completed`.
        payment_status = event.payment_status
        code = payment_status.code if payment_status is not None else "desconocido"
        print(f"[{event.event}] {event.validation.id} -> {event.validation.status} ({code})")
        if event.validation.client_ref:
            print("  pedido por revisar:", event.validation.client_ref)

    elif event.event == "validation.retry.resolved" and event.validation is not None:
        state = event.validation.retry_state
        attempts = state.attempts_completed if state else 0
        print(f"[{event.event}] resuelta tras {attempts} reintento(s)")

    elif event.event == "validation.retry.exhausted":
        print(f"[{event.event}] se agotaron los reintentos sin un veredicto firme")

    return "", 200
