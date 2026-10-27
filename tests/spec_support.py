"""Ayudantes de las pruebas que comparan el SDK con el spec público.

El spec de este repositorio (`spec/openapi.yaml`) es copia de
https://docs.veriko.mx/openapi.yaml.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

SPEC_PATH = Path(__file__).resolve().parent.parent / "spec" / "openapi.yaml"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


@lru_cache(maxsize=1)
def load_spec() -> dict[str, Any]:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with open(SPEC_PATH, encoding="utf-8") as handle:
        spec: dict[str, Any] = yaml.load(handle, Loader=loader)
    return spec


def operations() -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Cada operación del spec: su ruta, su método y su definición."""
    for template, item in load_spec()["paths"].items():
        for method in HTTP_METHODS:
            operation = item.get(method)
            if operation is not None:
                yield template, method, operation


def find_operation(method: str, pathname: str) -> tuple[str, dict[str, Any]]:
    """La operación del spec a la que corresponde una petición.

    Las rutas literales (`/validations/stats`) ganan a las que llevan un
    parámetro (`/validations/{id}`).
    """
    candidatas: list[tuple[str, dict[str, Any]]] = []
    for template, item in load_spec()["paths"].items():
        operation = item.get(method.lower())
        if operation is None:
            continue
        patron = re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", template) + "$")
        if patron.match(pathname):
            candidatas.append((template, operation))
    assert candidatas, f"el spec no declara {method} {pathname}"
    candidatas.sort(key=lambda candidata: "{" in candidata[0])
    return candidatas[0]


def _resolve(node: dict[str, Any], section: str) -> dict[str, Any]:
    reference = node.get("$ref")
    if not reference:
        return node
    resolved: dict[str, Any] = load_spec()["components"][section][reference.split("/")[-1]]
    return resolved


def declared(template: str, operation: dict[str, Any], where: str) -> list[str]:
    """Los nombres de los parámetros que la operación declara en `query` o `header`."""
    compartidos = load_spec()["paths"][template].get("parameters", [])
    nombres: list[str] = []
    for parametro in [*compartidos, *operation.get("parameters", [])]:
        resuelto = _resolve(parametro, "parameters")
        if resuelto.get("in") == where:
            nombres.append(resuelto["name"])
    return nombres


def schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    """Las propiedades de un schema, siguiendo `$ref` y sumando las de `allOf`."""
    resuelto = _resolve(schema, "schemas")
    propiedades: dict[str, Any] = {}
    for parte in resuelto.get("allOf", []):
        propiedades.update(schema_properties(parte))
    propiedades.update(resuelto.get("properties", {}))
    return propiedades


def request_body_properties(operation: dict[str, Any], content_type: str) -> dict[str, Any]:
    """Las propiedades del cuerpo que la operación declara para ese tipo de contenido."""
    request_body = _resolve(operation["requestBody"], "requestBodies")
    return schema_properties(request_body["content"][content_type]["schema"])


def security(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Los esquemas de autenticación de la operación."""
    if "security" in operation:
        return list(operation["security"])
    return list(load_spec().get("security", []))


def accepts_api_key(operation: dict[str, Any]) -> bool:
    """`True` si la operación acepta la clave de API o es pública y no pide autenticación."""
    esquemas = security(operation)
    return not esquemas or any("ApiKeyAuth" in alternativa for alternativa in esquemas)
