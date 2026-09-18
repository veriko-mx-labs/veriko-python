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
"""

import os

from flask import Flask, request

from veriko import SignatureVerificationError, parse_webhook

app = Flask(__name__)

SECRET = os.environ["VERIKO_WEBHOOK_SECRET"]

# Las entregas se repiten: un reintento trae el mismo Delivery-Id. En producción
# esto vive en Redis o en una tabla, no en memoria.
entregas_vistas = set()


@app.post("/hooks/veriko")
def recibir():
    delivery_id = request.headers.get("X-Veriko-Delivery-Id")

    try:
        evento = parse_webhook(
            request.get_data(),  # el cuerpo crudo, sin interpretar
            request.headers.get("X-Webhook-Signature"),
            SECRET,
        )
    except SignatureVerificationError:
        # Firma que no cuadra: el cuerpo no se procesa.
        return "", 400

    if delivery_id in entregas_vistas:
        return "", 200  # ya la procesamos; el reintento se acusa y se ignora
    entregas_vistas.add(delivery_id)

    if evento.event == "validation.completed" and evento.validation is not None:
        validation = evento.validation
        print(f"[{evento.event}] {validation.id} → {validation.status}")
        if validation.has_cep:
            print("  comprobante disponible en", validation.links.get("cep_pdf"))

    elif evento.event == "validation.retry.resolved" and evento.validation is not None:
        estado = evento.validation.retry_state
        intentos = estado.attempts_completed if estado else 0
        print(f"[{evento.event}] resuelto tras {intentos} reintento(s)")

    elif evento.event == "validation.retry.exhausted":
        print(f"[{evento.event}] se agotaron los reintentos sin veredicto firme")

    return "", 200
