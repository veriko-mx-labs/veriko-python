"""Rotate a webhook secret without dropping deliveries.

    export VERIKO_API_KEY=veriko_your_key_here
    python examples/rotate_webhook_secret.py <webhook id> <current secret>

By default the previous secret keeps signing for 24 hours, in `X-Webhook-Signature-Old`.
Verify with both secrets during that window, then drop the old one.
"""

import sys

from veriko import Veriko, verify_delivery

client = Veriko()
webhook_id, current_secret = sys.argv[1], sys.argv[2]

endpoint = client.webhooks.regenerate_secret(webhook_id)
print("New secret delivered once. The old one is valid until", endpoint.previous_secret_expires_at)


def handle(headers: dict[str, str], body: bytes) -> bool:
    """Accept a delivery signed with either secret."""
    return verify_delivery(headers, body, [endpoint.secret or "", current_secret])
