"""ASGI entry point for the MCP server over Streamable HTTP.

Mounts the MCP `Server` behind a Starlette app. On every request, the
`Authorization: Bearer <user-jwt>` header is extracted and placed in the
`current_user_jwt` ContextVar so the MCP tool handlers can read it during the
OBO exchange. This is the code path exercised equally by the reference agent
and any external MCP client (ADR 0004).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from typing import Any

from starlette.applications import Starlette
from starlette.routing import Mount

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from mcp_server import ServerDeps, build_server, current_user_jwt


_BEARER_PREFIX = b"Bearer "


def _extract_user_jwt_from_scope(scope: dict) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization" and value.startswith(_BEARER_PREFIX):
            token = value[len(_BEARER_PREFIX):].strip()
            return token.decode("ascii") if token else None
    return None


async def _send_unauthorized(send) -> None:
    body = b'{"error":"missing_bearer_token","message":"Authorization: Bearer <user-jwt> required"}'
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [(b"content-type", b"application/json")],
    })
    await send({"type": "http.response.body", "body": body})


def create_asgi_app(
    deps: ServerDeps,
    *,
    agent: Any = None,
) -> Starlette:
    """Build an ASGI app exposing MCP over Streamable HTTP, and optionally the
    reference agent's `/api/chat` SSE endpoint.

    The returned Starlette app manages `StreamableHTTPSessionManager.run()`
    via the ASGI lifespan protocol, so it can be mounted directly in
    uvicorn / Azure Functions (`AsgiFunctionApp`) or driven by an
    `asgi-lifespan` test harness.

    When `agent` is None, the `/api/chat` route is omitted — this is the
    behaviour selected by `ENABLE_REFERENCE_AGENT=false`.
    """
    mcp_server = build_server(deps)
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        stateless=True,
        json_response=True,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    async def mcp_asgi(scope, receive, send):
        if scope["type"] != "http":
            raise RuntimeError(f"MCP endpoint only handles HTTP (got {scope['type']})")
        user_jwt = _extract_user_jwt_from_scope(scope)
        if user_jwt is None:
            await _send_unauthorized(send)
            return
        ctx = current_user_jwt.set(user_jwt)
        try:
            await session_manager.handle_request(scope, receive, send)
        finally:
            current_user_jwt.reset(ctx)

    routes: list = [Mount("/mcp", app=mcp_asgi)]
    if agent is not None:
        from agent.route import build_chat_route

        routes.append(build_chat_route(agent))

    return Starlette(lifespan=lifespan, routes=routes)
