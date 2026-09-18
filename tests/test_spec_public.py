"""Candado: `spec/openapi.yaml` es la versión pública, no el bundle interno.

El 2026-09-18, el primer endpoint que publicó el spec sirvió el bundle crudo, con
las 264 rutas `/admin/*` y sus schemas. La copia de este repositorio sale de
https://docs.veriko.mx/openapi.yaml, que ya está filtrada por visibilidad.

Estas pruebas fallan si alguien la sustituye por el bundle interno.
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

    for marca in ("x-visibility:", "x-permission:", "x-admin-notes:"):
        assert marca not in texto, f"el spec conserva {marca}"


def test_declara_los_dos_esquemas_de_autenticacion() -> None:
    esquemas = load_spec()["components"]["securitySchemes"]

    assert esquemas["ApiKeyAuth"]["in"] == "header"
    assert esquemas["CookieAuth"]["in"] == "cookie"
