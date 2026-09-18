"""SDK oficial de Veriko para Python.

Veriko valida transferencias SPEI mexicanas contra el CEP (Comprobante
Electrónico de Pago) que emite el Banco de México, y devuelve un veredicto.

    from veriko import Veriko

    client = Veriko()  # lee VERIKO_API_KEY del entorno

    validation = client.validate_transfer(
        fecha="2025-03-15",
        monto=15000.50,
        clave_rastreo="MXBA20250315001234",
        cuenta_beneficiaria="012180004412345678",
    )

    if validation.status == "valid":
        cep = client.get_cep(validation.id, format="pdf")
        cep.write_to(cep.filename)

Documentación de la API: https://docs.veriko.mx
"""

from __future__ import annotations

__version__ = "0.1.0"

from ._http import RetryConfig
from .client import (
    API_KEY_ENV_VAR,
    BASE_URL_ENV_VAR,
    CEP_FORMATS,
    DEFAULT_BASE_URL,
    Veriko,
)
from .errors import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    ConflictError,
    ConnectionError,
    ForbiddenError,
    InvalidRequestError,
    NotFoundError,
    RateLimitError,
    ServerError,
    SignatureVerificationError,
    VerikoError,
)
from .models import (
    RETRYABLE_OUTCOMES,
    TERMINAL_STATUSES,
    CepDocument,
    QueuedValidation,
    RetryPolicy,
    RetryState,
    Validation,
    WebhookEvent,
)
from .webhooks import (
    DELIVERY_ID_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    compute_signature,
    parse_webhook,
    signature_from_headers,
    verify_webhook,
)

__all__ = [
    "API_KEY_ENV_VAR",
    "BASE_URL_ENV_VAR",
    "CEP_FORMATS",
    "DEFAULT_BASE_URL",
    "DELIVERY_ID_HEADER",
    "EVENT_HEADER",
    "RETRYABLE_OUTCOMES",
    "SIGNATURE_HEADER",
    "TERMINAL_STATUSES",
    "TIMESTAMP_HEADER",
    "APIError",
    "AuthenticationError",
    "CepDocument",
    "ConfigurationError",
    "ConflictError",
    "ConnectionError",
    "ForbiddenError",
    "InvalidRequestError",
    "NotFoundError",
    "QueuedValidation",
    "RateLimitError",
    "RetryConfig",
    "RetryPolicy",
    "RetryState",
    "ServerError",
    "SignatureVerificationError",
    "Validation",
    "Veriko",
    "VerikoError",
    "WebhookEvent",
    "__version__",
    "compute_signature",
    "parse_webhook",
    "signature_from_headers",
    "verify_webhook",
]
