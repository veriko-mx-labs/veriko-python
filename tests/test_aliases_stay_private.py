"""The names from earlier versions keep working and stay out of everything public."""

from __future__ import annotations

import inspect
import re
import warnings
from pathlib import Path
from typing import Any

import pytest

import veriko
from conftest import RecordingServer
from veriko import AsyncVeriko, Veriko
from veriko._compat import REQUEST_ALIASES, RESPONSE_ALIASES
from veriko.models import ValidationSummary

ROOT = Path(__file__).resolve().parent.parent
NAMES = sorted({*REQUEST_ALIASES, *RESPONSE_ALIASES})


def _mentions(text: str) -> list[str]:
    """The alias names that appear in `text` as whole identifiers."""
    return [
        name for name in NAMES if re.search(r"(?<![A-Za-z0-9_])" + name + r"(?![A-Za-z0-9_])", text)
    ]


def _public_callables() -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    clients: list[Any] = [Veriko, AsyncVeriko]
    for client in clients:
        found.append((client.__name__, client))
        for attribute, value in vars(client).items():
            if not attribute.startswith("_") and inspect.isfunction(value):
                found.append((client.__name__ + "." + attribute, value))
    for module in (veriko.resources, veriko._async_resources):
        for class_name, family in vars(module).items():
            if class_name.startswith("_") or not inspect.isclass(family):
                continue
            if family.__module__ != module.__name__:
                continue
            for attribute, value in vars(family).items():
                if not attribute.startswith("_") and inspect.isfunction(value):
                    found.append((class_name + "." + attribute, value))
    return found


def test_no_public_signature_or_annotation_names_an_alias() -> None:
    callables = _public_callables()
    assert len(callables) > 100
    for label, function in callables:
        signature = str(inspect.signature(function))
        assert not _mentions(signature), f"{label}: {_mentions(signature)}"


def test_no_public_docstring_names_an_alias() -> None:
    for label, function in _public_callables():
        assert not _mentions(inspect.getdoc(function) or ""), label
    for name in veriko.__all__:
        documented = inspect.getdoc(getattr(veriko, name)) or ""
        assert not _mentions(documented), name


def test_the_package_exports_no_alias_and_no_alias_map() -> None:
    assert not [name for name in veriko.__all__ if _mentions(name)]
    assert not {"REQUEST_ALIASES", "RESPONSE_ALIASES"} & set(veriko.__all__)
    assert not hasattr(veriko, "REQUEST_ALIASES")


def test_no_model_field_is_an_alias() -> None:
    import dataclasses

    from veriko import models, responses

    for module in (models, responses):
        for name, value in vars(module).items():
            if inspect.isclass(value) and value.__module__ == module.__name__:
                fields = (
                    [f.name for f in dataclasses.fields(value)]
                    if dataclasses.is_dataclass(value)
                    else sorted(getattr(value, "_fields", ()))
                )
                assert not _mentions(" ".join(fields)), name


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "CHANGELOG.md",
        *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "examples").glob("*")),
    ],
)
def test_documents_and_examples_name_no_alias(path: str) -> None:
    file = ROOT / path
    if file.is_dir() or file.suffix not in {".md", ".py"}:
        pytest.skip("not a text document")
    assert not _mentions(file.read_text(encoding="utf-8")), path


# ── They still work ────────────────────────────────────────────────────────

VALUES = {
    "send_date": "2025-03-15",
    "amount": 12.5,
    "tracking_key": "MXBA20250315001234",
    "numeric_reference": "1234567",
    "sender_bank": "40012",
    "receiver_bank": "40002",
    "beneficiary_account": "012180004412345678",
    "recipient_is_bank": True,
}


@pytest.mark.parametrize(("old", "new"), sorted(REQUEST_ALIASES.items()))
def test_each_alias_is_sent_as_the_current_field_and_warns(
    client: Veriko, server: RecordingServer, old: str, new: str
) -> None:
    server.enqueue_recording("validate-valid")
    fields: dict[str, Any] = {
        k: v for k, v in VALUES.items() if k in ("amount", "tracking_key", "beneficiary_account")
    }
    if new in fields:
        fields.pop(new)
    value = (
        ["012180004412345678", "012180004412345679"]
        if new == "candidate_accounts"
        else VALUES.get(new)
    )
    if new == "candidate_accounts":
        fields.pop("beneficiary_account", None)
    fields[old] = value

    with pytest.warns(DeprecationWarning, match=new):
        client.validations.validate(**fields)

    body = server.requests[0].json()
    assert body[new] == value
    assert old not in body


def test_a_response_alias_reads_the_current_attribute_and_warns() -> None:
    summary = ValidationSummary.from_item(
        {"id": "1", "attributes": {"status": "valid", "numeric_reference": "1234567"}}
    )
    (old, new), *_ = RESPONSE_ALIASES.items()

    with pytest.warns(DeprecationWarning, match=new):
        assert getattr(summary, old) == "1234567"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert summary.numeric_reference == "1234567"
