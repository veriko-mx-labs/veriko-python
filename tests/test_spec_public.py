"""`spec/openapi.yaml` is the copy of the spec at https://docs.veriko.mx/openapi.yaml.

These tests fail if the copy does not meet the contract of that API.
"""

from __future__ import annotations

from spec_support import SPEC_PATH, load_spec


def test_declares_no_admin_route() -> None:
    admin = [route for route in load_spec()["paths"] if route.startswith("/admin")]

    assert admin == []


def test_declares_the_public_routes() -> None:
    routes = load_spec()["paths"]

    assert len(routes) > 50
    assert "/validate" in routes
    assert "/validations/{id}/cep" in routes
    assert "/webhooks" in routes


def test_does_not_keep_the_internal_bundle_extensions() -> None:
    text = SPEC_PATH.read_text(encoding="utf-8")

    for marker in (
        "x-visibility:",
        "x-permission:",
        "x-admin-notes:",
        "x-auth:",
        "x-integration:",
    ):
        assert marker not in text, f"the spec keeps {marker}"


def test_declares_only_the_m2m_api_key_scheme() -> None:
    schemes = load_spec()["components"]["securitySchemes"]

    assert schemes["ApiKeyAuth"]["in"] == "header"
    assert set(schemes) == {"ApiKeyAuth"}
    assert "CookieAuth" not in schemes
