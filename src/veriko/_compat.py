"""Field names from earlier versions: accepted and translated to the current names.

This private module is the only place that lists them. They keep working for existing
integrations, with a `DeprecationWarning` that names the replacement, and they are not
part of the typed API: no signature, annotation, model field, document or example
mentions them.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import Any

# Request arguments: Spanish name -> canonical English name.
REQUEST_ALIASES: dict[str, str] = {
    "fecha": "send_date",
    "monto": "amount",
    "clave_rastreo": "tracking_key",
    "referencia_numerica": "numeric_reference",
    "emisor": "sender_bank",
    "receptor": "receiver_bank",
    "banco_emisor": "sender_bank",
    "banco_receptor": "receiver_bank",
    "cuenta_beneficiaria": "beneficiary_account",
    "cuentas_candidatas": "candidate_accounts",
    "receptor_participante": "recipient_is_bank",
}


# Response attributes: earlier name -> current name.
RESPONSE_ALIASES: dict[str, str] = {"referencia_numerica": "numeric_reference"}


def warn_deprecated_name(old: str, new: str, *, stacklevel: int) -> None:
    """Warns that `old` is deprecated and names its replacement."""
    warnings.warn(
        "`" + old + "` is deprecated and will be removed in a future version; use `" + new + "`.",
        DeprecationWarning,
        stacklevel=stacklevel + 1,
    )


def merge_legacy_kwargs(
    values: Mapping[str, Any],
    legacy: Mapping[str, Any],
    *,
    stacklevel: int = 2,
) -> dict[str, Any]:
    """Merges the English arguments with the ones that arrived under a Spanish name.

    `values` carries the arguments under their English name; `None` means it was
    not passed. `legacy` carries what was left in `**kwargs`. Each Spanish name is
    translated, with a warning, to its English name. If the same field arrives in
    both languages, or an unknown name arrives, `TypeError` is raised.

    `stacklevel` counts from the public method that calls this function: with `2`,
    the warning points at the code that called that method.
    """
    merged = dict(values)
    seen: dict[str, str] = {}
    for old, value in legacy.items():
        new = REQUEST_ALIASES.get(old)
        if new is None:
            raise TypeError("got an unexpected keyword argument '" + old + "'")
        if merged.get(new) is not None or new in seen:
            other = seen.get(new, new)
            raise TypeError(
                "`" + other + "` and `" + old + "` are the same field: pass only `" + new + "`"
            )
        warn_deprecated_name(old, new, stacklevel=stacklevel + 1)
        seen[new] = old
        merged[new] = value
    return merged
