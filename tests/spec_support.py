"""Helpers for the tests that compare the SDK with the public spec.

The spec in this repository (`spec/openapi.yaml`) is a copy of
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
    """Every operation in the spec: its path, its method and its definition."""
    for template, item in load_spec()["paths"].items():
        for method in HTTP_METHODS:
            operation = item.get(method)
            if operation is not None:
                yield template, method, operation


def find_operation(method: str, pathname: str) -> tuple[str, dict[str, Any]]:
    """The spec operation a request corresponds to.

    Literal paths (`/validations/stats`) win over those that carry a
    parameter (`/validations/{id}`).
    """
    candidates: list[tuple[str, dict[str, Any]]] = []
    for template, item in load_spec()["paths"].items():
        operation = item.get(method.lower())
        if operation is None:
            continue
        pattern = re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", template) + "$")
        if pattern.match(pathname):
            candidates.append((template, operation))
    assert candidates, f"the spec does not declare {method} {pathname}"
    candidates.sort(key=lambda candidate: "{" in candidate[0])
    return candidates[0]


def _resolve(node: dict[str, Any], section: str) -> dict[str, Any]:
    reference = node.get("$ref")
    if not reference:
        return node
    resolved: dict[str, Any] = load_spec()["components"][section][reference.split("/")[-1]]
    return resolved


def declared(template: str, operation: dict[str, Any], where: str) -> list[str]:
    """The names of the parameters the operation declares in `query` or `header`."""
    shared = load_spec()["paths"][template].get("parameters", [])
    names: list[str] = []
    for parameter in [*shared, *operation.get("parameters", [])]:
        resolved = _resolve(parameter, "parameters")
        if resolved.get("in") == where:
            names.append(resolved["name"])
    return names


def schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    """The properties of a schema, following `$ref` and merging those of `allOf`."""
    resolved = _resolve(schema, "schemas")
    properties: dict[str, Any] = {}
    for part in resolved.get("allOf", []):
        properties.update(schema_properties(part))
    properties.update(resolved.get("properties", {}))
    return properties


def response_properties(
    method: str, pathname: str, status: str, location: str = "attributes"
) -> dict[str, Any]:
    """The properties of the resource a JSON response carries in `data`.

    `location` is `attributes` for the properties of `data.attributes`, or `data` for
    those of `data` itself. When `data` is a list, they are those of its items.
    """
    _, operation = find_operation(method, pathname)
    response = _resolve(operation["responses"][status], "responses")
    schema = response["content"]["application/json"]["schema"]
    data = schema_properties(schema)["data"]
    resolved = _resolve(data, "schemas")
    if resolved.get("type") == "array":
        data = resolved["items"]
    properties = schema_properties(data)
    return schema_properties(properties["attributes"]) if location == "attributes" else properties


def schema_named(name: str) -> dict[str, Any]:
    """The properties of a schema of `components.schemas`."""
    return schema_properties(load_spec()["components"]["schemas"][name])


def request_body_properties(operation: dict[str, Any], content_type: str) -> dict[str, Any]:
    """The body properties the operation declares for that content type."""
    request_body = _resolve(operation["requestBody"], "requestBodies")
    return schema_properties(request_body["content"][content_type]["schema"])


def security(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """The authentication schemes of the operation."""
    if "security" in operation:
        return list(operation["security"])
    return list(load_spec().get("security", []))


def accepts_api_key(operation: dict[str, Any]) -> bool:
    """`True` if the operation accepts the API key or is public and needs no authentication."""
    schemes = security(operation)
    return not schemes or any("ApiKeyAuth" in alternative for alternative in schemes)
