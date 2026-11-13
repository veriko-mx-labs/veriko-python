"""The Spanish names from 0.7 keep working as deprecated."""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from conftest import RecordingServer
from veriko import Veriko
from veriko._compat import REQUEST_ALIASES
from veriko.models import ValidationSummary

FIELDS: dict[str, Any] = {
    "fecha": "2025-03-15",
    "monto": 15000.5,
    "clave_rastreo": "MXBA20250315001234",
    "referencia_numerica": "1234567",
    "emisor": "BANCO NACIONAL DE MEXICO",
    "receptor": "BBVA MEXICO",
    "cuenta_beneficiaria": "012180004412345678",
    "receptor_participante": 1,
}


def test_spanish_kwargs_warn_and_send_the_english_name(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid")

    with pytest.warns(DeprecationWarning) as caught:
        client.validate_transfer(**FIELDS)

    texts = [str(warning.message) for warning in caught]
    for old, new in [(k, REQUEST_ALIASES[k]) for k in FIELDS]:
        assert any("`" + old + "`" in t and "`" + new + "`" in t for t in texts)
    # The warning points at the calling code, not at the SDK internals.
    assert all(warning.filename == __file__ for warning in caught)
    assert server.requests[0].json() == {
        "send_date": "2025-03-15",
        "amount": 15000.5,
        "beneficiary_account": "012180004412345678",
        "tracking_key": "MXBA20250315001234",
        "numeric_reference": "1234567",
        "sender_bank": "BANCO NACIONAL DE MEXICO",
        "receiver_bank": "BBVA MEXICO",
        "recipient_is_bank": 1,
    }


def test_english_names_emit_no_warnings(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("validate-valid")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        client.validate_transfer(
            send_date="2025-03-15",
            amount=1,
            beneficiary_account="012180004412345678",
            tracking_key="MXBA20250315001234",
        )


def test_the_resource_path_and_enqueueing_also_accept_the_spanish_names(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-valid")
    server.enqueue_recording("validate-valid")

    with pytest.warns(DeprecationWarning):
        client.validations.validate(**FIELDS)
    with pytest.warns(DeprecationWarning) as caught:
        client.validations.enqueue(**FIELDS)

    assert all(warning.filename == __file__ for warning in caught)
    assert server.requests[1].json()["amount"] == 15000.5


def test_validate_ocr_accepts_the_banks_and_accounts_in_spanish(
    client: Veriko, server: RecordingServer
) -> None:
    server.enqueue_recording("validate-ocr")

    with pytest.warns(DeprecationWarning):
        client.validations.validate_ocr(
            image=b"png",
            cuenta_beneficiaria="012180004412345678",
            banco_emisor="40012",
            banco_receptor="40002",
        )

    body = server.requests[0].json()
    assert body["beneficiary_account"] == "012180004412345678"
    assert body["sender_bank"] == "40012"
    assert body["receiver_bank"] == "40002"
    assert not set(body) & set(REQUEST_ALIASES)


@pytest.mark.parametrize(("old", "new"), sorted(REQUEST_ALIASES.items()))
def test_both_names_of_the_same_field_raise_type_error(
    client: Veriko, server: RecordingServer, old: str, new: str
) -> None:
    base: dict[str, Any] = {
        "send_date": "2025-03-15",
        "amount": 1,
        "tracking_key": "MXBA20250315001234",
        "beneficiary_account": "012180004412345678",
    }
    base[new] = "x"

    with pytest.raises(TypeError, match=new), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client.validate_transfer(**{**base, old: "y"})

    assert server.requests == []


def test_an_unknown_name_is_still_a_type_error(client: Veriko) -> None:
    with pytest.raises(TypeError, match="unexpected keyword"):
        client.validate_transfer(amount=1, invented=1)


def test_the_amount_is_required_under_either_name(client: Veriko) -> None:
    with pytest.raises(TypeError, match="amount"):
        client.validate_transfer(
            tracking_key="MXBA20250315001234", beneficiary_account="012180004412345678"
        )


def test_the_spanish_attribute_of_a_model_gives_the_same_with_a_warning() -> None:
    summary = ValidationSummary.from_item(
        {"id": "1", "attributes": {"status": "valid", "numeric_reference": "1234567"}}
    )
    assert summary.numeric_reference == "1234567"

    with pytest.warns(DeprecationWarning, match="numeric_reference") as caught:
        assert summary.referencia_numerica == "1234567"
    assert caught[0].filename == __file__


def test_a_model_reads_the_spanish_name_if_the_english_one_is_missing() -> None:
    summary = ValidationSummary.from_item(
        {"id": "1", "attributes": {"status": "valid", "referencia_numerica": "7654321"}}
    )

    assert summary.numeric_reference == "7654321"
