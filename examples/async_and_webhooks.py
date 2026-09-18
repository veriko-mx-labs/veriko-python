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
    url="https://miapp.example.com/hooks/veriko",
    events=["validation.completed", "validation.retry.resolved"],
)
print("Endpoint", endpoint.id, "registrado")
print("Secreto de firma:", endpoint.secret, "— guárdalo ahora")

# 2. Un evento de prueba confirma que el receptor es alcanzable antes de mandar
#    tráfico de verdad.
prueba = client.webhooks.test(endpoint.id)
if not prueba.delivered:
    print("El receptor no aceptó el evento de prueba:", prueba.error)
    sys.exit(1)
print("Prueba entregada en", prueba.response_time_ms, "ms")

# 3. Las transferencias por validar. La clave de idempotencia sale del número de
#    pedido, no de un aleatorio: así un reenvío no duplica el cargo.
transferencias = [
    {
        "fecha": "2025-03-15",
        "monto": 15000.50,
        "clave_rastreo": "MXBA20250315001234",
        "idempotency_key": "pedido-4f3a2b1c",
    },
    {
        "fecha": "2025-03-15",
        "monto": 2300.00,
        "clave_rastreo": "MXBA20250315005678",
        "idempotency_key": "pedido-7c2d9e0a",
    },
]

encoladas = []
for transferencia in transferencias:
    try:
        encoladas.append(client.validations.enqueue(**transferencia))
    except APIError as error:
        print("Rechazada:", error.code, "—", error.detail)

print("Encoladas", len(encoladas), "validaciones")

# 4. Con el webhook registrado, el veredicto llega solo. Sondear sirve para un
#    proceso que necesita el resultado antes de continuar.
for queued in encoladas:
    validation = client.validations.wait_for(queued.id, timeout=120)
    print(validation.id, "→", validation.status)
    if validation.has_cep:
        cep = client.validations.cep(validation.id, format="pdf")
        cep.write_to(cep.filename)
        print("  comprobante en", cep.filename)
