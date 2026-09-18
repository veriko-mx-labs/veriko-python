"""Validar una transferencia SPEI y descargar su CEP.

    export VERIKO_API_KEY=veriko_tu_clave_aqui
    python examples/validate.py

Cambia los datos de la transferencia por los tuyos. La fecha es la de envío, en
formato YYYY-MM-DD, y hace falta la clave de rastreo o la referencia numérica.
"""

import sys

from veriko import APIError, RateLimitError, Veriko

client = Veriko()  # lee VERIKO_API_KEY del entorno

try:
    validation = client.validate_transfer(
        fecha="2025-03-15",
        monto=15000.50,
        clave_rastreo="MXBA20250315001234",
        referencia_numerica="1234567",
        emisor="BANCO NACIONAL DE MEXICO",
        receptor="BBVA MEXICO",
        cuenta_beneficiaria="012180004412345678",
        idempotency_key="ejemplo-validate-001",
    )
except RateLimitError as error:
    print("Límite de tasa o cuota agotada. Reintentar en", error.retry_after, "segundos")
    sys.exit(1)
except APIError as error:
    print("La API rechazó la petición:", error.code, "—", error.detail)
    print("request_id:", error.request_id)
    sys.exit(1)

print("Validación:", validation.id)
print("Veredicto: ", validation.status)
print("Banxico:   ", validation.banxico_status)
print("Tardó:     ", validation.processing_time_ms, "ms")

if not validation.has_cep:
    print("Sin comprobante que descargar.")
    sys.exit(0)

cep = client.get_cep(validation.id, format="pdf")
cep.write_to(cep.filename)
print("CEP guardado en", cep.filename, f"({len(cep.content)} bytes)")
