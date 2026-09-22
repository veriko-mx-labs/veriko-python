"""Descargar el CEP: el archivo que emite Banxico, no un enlace a él."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import RecordingServer
from veriko import ConfigurationError, NotFoundError, Veriko

VALIDATION_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"


def test_descargar_el_cep_en_xml(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("cep-xml")

    cep = client.get_cep(VALIDATION_ID)

    assert server.requests[0].path == "/v1/validations/" + VALIDATION_ID + "/cep?format=xml"
    assert cep.format == "xml"
    assert cep.content_type == "application/xml"
    assert cep.filename == "CEP-" + VALIDATION_ID + ".xml"
    assert cep.content.startswith(b"<?xml")
    assert b"SPEI_Tercero" in cep.content


def test_descargar_el_cep_en_pdf(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("cep-pdf")

    cep = client.get_cep(VALIDATION_ID, format="pdf")

    assert server.requests[0].path.endswith("format=pdf")
    assert server.requests[0].header("accept") == "application/pdf, application/json"
    assert cep.format == "pdf"
    assert cep.content.startswith(b"%PDF-")


def test_guardar_el_comprobante_en_disco(
    client: Veriko, server: RecordingServer, tmp_path: Path
) -> None:
    server.enqueue_recording("cep-pdf")

    cep = client.get_cep(VALIDATION_ID, format="pdf")
    destino = cep.write_to(str(tmp_path / cep.filename))

    assert Path(destino).read_bytes() == cep.content


def test_sin_comprobante_la_api_responde_404(client: Veriko, server: RecordingServer) -> None:
    server.enqueue_recording("cep-404")

    with pytest.raises(NotFoundError) as raised:
        client.get_cep(VALIDATION_ID)

    assert raised.value.code == "cep_not_available"


def test_un_formato_fuera_de_la_lista_no_llega_a_la_api(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(ConfigurationError):
        client.get_cep(VALIDATION_ID, format="docx")

    assert server.requests == []


def test_un_identificador_con_barras_no_se_interpola_en_la_ruta(
    client: Veriko, server: RecordingServer
) -> None:
    with pytest.raises(ConfigurationError):
        client.get_cep("../../otra/ruta")

    assert server.requests == []
