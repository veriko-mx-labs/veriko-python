"""Receptor de webhooks con Flask, con la firma verificada.

    pip install flask
    export VERIKO_WEBHOOK_SECRET=el_secreto_del_endpoint
    flask --app examples/flask_webhook.py run --port 5000

El secreto lo devuelve `POST /v1/webhooks` al registrar el endpoint, una sola
vez. Si se pierde, se rota con `POST /v1/webhooks/{id}/regenerate-secret`.

Dos detalles del receptor:

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
entregas_vistas = set()

# El monto de cada pedido, tal como lo espera esta app. En producción sale de
# la base de datos de pedidos, no de un diccionario en memoria.
pedidos = {"f47ac10b-58cc-4372-a567-0e02b2c3d479": 15000.50}


@app.post("/hooks/veriko")
def recibir():
    delivery_id = request.headers.get("X-Veriko-Delivery-Id")

    cuerpo = request.get_data()  # el cuerpo crudo, sin interpretar

    if not verify_webhook_timestamped(
        cuerpo, timestamped_signature_from_headers(request.headers), SECRET
    ):
        # Firma que no cuadra, o entrega fuera de la ventana: no se procesa.
        return "", 400

    try:
        evento = parse_webhook(cuerpo, request.headers.get("X-Webhook-Signature"), SECRET)
    except SignatureVerificationError:
        # Firma que no cuadra: el cuerpo no se procesa.
        return "", 400

    if delivery_id in entregas_vistas:
        return "", 200  # ya la procesamos; el reintento se acusa y se ignora
    entregas_vistas.add(delivery_id)

    if evento.event == "validation.completed" and evento.validation is not None:
        validation = evento.validation
        print(f"[{evento.event}] {validation.id} → {validation.status}")
        if validation.client_ref:
            print("  pedido:", validation.client_ref)
        if validation.has_cep:
            print("  comprobante disponible en", validation.links.get("cep_pdf"))

        confirmado = evento.banxico_confirmed
        if confirmado is not None:
            # Banxico confirmó el pago: este monto, no el de una imagen de
            # comprobante, es el que hay que comparar contra el pedido antes de
            # liberar la mercancía. `confirmado.beneficiaryAccount` sirve igual
            # para comparar la cuenta cuando el pedido la registra.
            monto_esperado = pedidos.get(validation.id)
            if confirmado.amount == monto_esperado:
                print("  monto confirmado por Banxico:", confirmado.amount)
            else:
                print("  monto confirmado no coincide con el pedido:", confirmado.amount)

    elif evento.event == "validation.returned" and evento.validation is not None:
        # La validación había salido `valid` y Banxico reportó la devolución después.
        # Suscribe el endpoint a este evento además de a `validation.completed`.
        estado_pago = evento.payment_status
        codigo = estado_pago.code if estado_pago is not None else "desconocido"
        print(f"[{evento.event}] {evento.validation.id} → {evento.validation.status} ({codigo})")
        if evento.validation.client_ref:
            print("  pedido a revisar:", evento.validation.client_ref)

    elif evento.event == "validation.retry.resolved" and evento.validation is not None:
        estado = evento.validation.retry_state
        intentos = estado.attempts_completed if estado else 0
        print(f"[{evento.event}] resuelto tras {intentos} reintento(s)")

    elif evento.event == "validation.retry.exhausted":
        print(f"[{evento.event}] se agotaron los reintentos sin veredicto firme")

    return "", 200
