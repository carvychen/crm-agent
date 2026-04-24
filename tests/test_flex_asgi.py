"""Pin the AsgiFunctionApp route-template workaround.

The azure-functions Python SDK registers the ASGI HTTP trigger with
``route="/{*route}"`` (leading slash). On Flex Consumption (ASP.NET Core 8
host) this causes JobHost startup to fail with RoutePatternException — the
template prefix-concatenation produces ``<prefix>//{*route}`` which routing
validation rejects.

``src.flex_asgi.FlexAsgiFunctionApp`` drops the leading slash. If a future
SDK release fixes the bug upstream, this test will still pass; if a future
SDK release *keeps* the bug and our subclass silently breaks (e.g. because
``_add_http_app`` changes shape), this test fails loudly.
"""
from __future__ import annotations

import azure.functions as func


async def _noop_asgi(scope, receive, send):
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def _registered_routes(app: func.FunctionApp) -> list[str]:
    routes: list[str] = []
    for fn in app.get_functions():
        for binding in fn.get_bindings():
            route = getattr(binding, "route", None)
            if route:
                routes.append(route)
    return routes


def test_flex_asgi_drops_leading_slash():
    from flex_asgi import FlexAsgiFunctionApp

    app = FlexAsgiFunctionApp(app=_noop_asgi, http_auth_level=func.AuthLevel.ANONYMOUS)

    routes = _registered_routes(app)
    assert routes, "FlexAsgiFunctionApp should register at least one HTTP route"
    for route in routes:
        assert not route.startswith("/"), (
            f"route {route!r} starts with '/' — combined with Functions routePrefix "
            "this produces '<prefix>//{*route}' which fails Flex JobHost startup"
        )
        assert route == "{*route}", f"unexpected route template: {route!r}"


def test_upstream_sdk_still_has_the_bug():
    """Guardrail. Delete this test and src/flex_asgi.py together once the SDK
    removes the leading slash. Today (azure-functions 1.24.0 / 2.1.0) the
    upstream default is still '/{*route}'."""
    upstream = func.AsgiFunctionApp(
        app=_noop_asgi, http_auth_level=func.AuthLevel.ANONYMOUS
    )
    routes = _registered_routes(upstream)
    assert any(r.startswith("/") for r in routes), (
        "azure-functions SDK appears to have fixed the leading-slash bug. "
        "FlexAsgiFunctionApp workaround is no longer needed — delete it."
    )
