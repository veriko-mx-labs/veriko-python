"""`spec/openapi.yaml` es la copia del spec de https://docs.veriko.mx/openapi.yaml.

Estas pruebas fallan si la copia no cumple el contrato de esa API.
"""

from __future__ import annotations

from spec_support import SPEC_PATH, load_spec


def test_no_declara_ninguna_ruta_de_administracion() -> None:
    admin = [ruta for ruta in load_spec()["paths"] if ruta.startswith("/admin")]

    assert admin == []


def test_declara_las_rutas_publicas() -> None:
    rutas = load_spec()["paths"]

    assert len(rutas) > 50
    assert "/validate" in rutas
    assert "/validations/{id}/cep" in rutas
    assert "/webhooks" in rutas


def test_no_conserva_las_extensiones_internas_del_bundle() -> None:
    texto = SPEC_PATH.read_text(encoding="utf-8")

    for marca in (
        "x-visibility:",
        "x-permission:",
        "x-admin-notes:",
        "x-auth:",
        "x-integration:",
    ):
        assert marca not in texto, f"el spec conserva {marca}"


def test_declara_solo_el_esquema_m2m_de_api_key() -> None:
    esquemas = load_spec()["components"]["securitySchemes"]

    assert esquemas["ApiKeyAuth"]["in"] == "header"
    assert set(esquemas) == {"ApiKeyAuth"}
    assert "CookieAuth" not in esquemas
