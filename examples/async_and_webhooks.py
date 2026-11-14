"""Encolar validaciones, registrar el webhook que las recoge, y sondear.

    export VERIKO_API_KEY=veriko_tu_clave_aqui
    python examples/async_and_webhooks.py

Es el camino para volumen: la API acepta cada petición al instante y el veredicto
llega después, por webhook o por sondeo.
"""

import sys

from veriko import APIError, Veriko

client = Veriko()

# 1. El endpoint que va a recibir los veredictos. El secreto viaja una sola vez.
endpoint = client.webhooks.create(
    url="https://myapp.example.com/hooks/veriko",
    events=["validation.completed", "validation.retry.resolved"],
)
print("Endpoint", endpoint.id, "registrado")
print("Secreto de firma:", endpoint.secret, "- guárdalo ahora")

# 2. Un evento de prueba confirma que el receptor responde antes de mandar tráfico real.
test = client.webhooks.test(endpoint.id)
if not test.delivered:
    print("El receptor no aceptó el evento de prueba:", test.error)
    sys.exit(1)
print("Prueba entregada en", test.response_time_ms, "ms")

# 3. Las transferencias por validar. La clave de idempotencia sale del número de
#    pedido, no de un aleatorio: así un reenvío no duplica el cargo. `client_ref`
#    relaciona la validación con un pedido propio.
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
        print("Rechazada:", error.code, "-", error.detail)

print("En cola:", len(queued_validations), "validaciones")

# 4. Con el webhook registrado, el veredicto llega solo. Sondear sirve para un
#    proceso que necesita el resultado antes de continuar.
for queued in queued_validations:
    validation = client.validations.wait_for(queued.id, timeout=120)
    print(validation.id, "->", validation.status)
    if validation.has_cep:
        cep = client.validations.cep(validation.id, format="pdf")
        cep.write_to(cep.filename)
        print("  comprobante en", cep.filename)
