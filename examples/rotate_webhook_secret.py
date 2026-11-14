"""Rota el secreto de un webhook sin perder entregas.

    export VERIKO_API_KEY=veriko_tu_clave_aqui
    python examples/rotate_webhook_secret.py <id del webhook> <secreto actual>

Por omisión, el secreto anterior sigue firmando durante 24 horas, en
`X-Webhook-Signature-Old`. Verifica con los dos secretos durante esa ventana y
después descarta el anterior.
"""

import sys

from veriko import Veriko, verify_delivery

client = Veriko()
webhook_id, current_secret = sys.argv[1], sys.argv[2]

endpoint = client.webhooks.regenerate_secret(webhook_id)
print(
    "El secreto nuevo se entrega una sola vez. El anterior vale hasta",
    endpoint.previous_secret_expires_at,
)


def handle(headers: dict[str, str], body: bytes) -> bool:
    """Acepta una entrega firmada con cualquiera de los dos secretos."""
    return verify_delivery(headers, body, [endpoint.secret or "", current_secret])
