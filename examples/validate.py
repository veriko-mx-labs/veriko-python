"""Validar una transferencia SPEI y descargar su CEP.

    export VERIKO_API_KEY=veriko_tu_clave_aqui
    python examples/validate.py

Los datos de la transferencia van en las constantes de abajo. La fecha es la de
envío, en formato YYYY-MM-DD, y la operación exige la clave de rastreo o la
referencia numérica.
"""

import sys

from veriko import APIError, RateLimitError, Veriko

client = Veriko()  # lee VERIKO_API_KEY del entorno

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
    print("Límite de uso o cuota agotada. Reintenta en", error.retry_after, "segundos")
    sys.exit(1)
except APIError as error:
    print("La API rechazó la petición:", error.code, "-", error.detail)
    print("request_id:", error.request_id)
    sys.exit(1)

print("Validación: ", validation.id)
print("Veredicto:  ", validation.status)
print("Banxico:    ", validation.banxico_status)
print("Duración:   ", validation.processing_time_ms, "ms")

if not validation.has_cep:
    print("No hay comprobante para descargar.")
    sys.exit(0)

cep = client.get_cep(validation.id, format="pdf")
cep.write_to(cep.filename)
print("CEP guardado en", cep.filename, f"({len(cep.content)} bytes)")
