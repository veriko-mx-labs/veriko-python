"""Downloading the CEP: the file Banxico issues, not a link to it."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import RecordingServer
from veriko import ConfigurationError, NotFoundError, Veriko

VALIDATION_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"


def test_download_the_cep_as_xml(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("cep-xml")

    cep = client.get_cep(VALIDATION_ID)

    assert server.requests[0].path == "/v1/validations/" + VALIDATION_ID + "/cep?format=xml"
    assert cep.format == "xml"
    assert cep.content_type == "application/xml"
    assert cep.filename == "CEP-" + VALIDATION_ID + ".xml"
    assert cep.content.startswith(b"<?xml")
    assert b"SPEI_Tercero" in cep.content


def test_download_the_cep_as_pdf(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("cep-pdf")

    cep = client.get_cep(VALIDATION_ID, format="pdf")

    assert server.requests[0].path.endswith("format=pdf")
    assert server.requests[0].header("accept") == "application/pdf, application/json"
    assert cep.format == "pdf"
    assert cep.content.startswith(b"%PDF-")


def test_save_the_receipt_to_disk(client: Veriko, server: RecordingServer, tmp_path: Path) -> None:
    server.enqueue_recording("cep-pdf")

    cep = client.get_cep(VALIDATION_ID, format="pdf")
    target = cep.write_to(str(tmp_path / cep.filename))

    assert Path(target).read_bytes() == cep.content


def test_without_a_receipt_the_api_answers_404(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("cep-404")

    with pytest.raises(NotFoundError) as raised:
        client.get_cep(VALIDATION_ID)

    assert raised.value.code == "cep_not_available"


def test_a_format_outside_the_list_does_not_reach_the_api(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(ConfigurationError):
        client.get_cep(VALIDATION_ID, format="docx")

    assert server.requests == []


def test_an_identifier_with_slashes_is_not_interpolated_into_the_path(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(ConfigurationError):
        client.get_cep("../../otra/ruta")

    assert server.requests == []
