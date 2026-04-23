"""Live-integration: MCP server end-to-end against real Dataverse.

Drives the same ASGI app `function_app.py` runs, but via `httpx.ASGITransport`
so the test stays in-process. Everything *below* the ASGI layer is real: live
Entra token, live Dataverse HTTP, live OpenAI-compatible responses parsed by
the real MCP Python SDK client. This proves US 12 (`list_tools` self-describing
schema) and US 34 (no in-process shortcut) simultaneously.
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def test_mcp_server_list_tools_and_list_opportunities_live():
    """Boots the real ASGI app, speaks MCP to it, verifies the opportunities
    tool exists and a call returns real Dataverse data."""
    from asgi import create_asgi_app
    from auth import build_auth
    from config import get_config
    from dataverse_client import OpportunityClient
    from mcp_server import ServerDeps

    config = get_config()
    http_backend = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    deps = ServerDeps(
        auth=build_auth(config, http=http_backend, mi_token_provider=lambda: ""),
        client=OpportunityClient(config.dataverse_url, http=http_backend),
    )
    app = create_asgi_app(deps=deps)

    try:
        async with LifespanManager(app), httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer ignored-under-app-only"},
            follow_redirects=True,
            timeout=30.0,
        ) as client_http, streamable_http_client(
            "http://testserver/mcp", http_client=client_http
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools_response = await session.list_tools()
                tool_names = [t.name for t in tools_response.tools]
                assert "list_opportunities" in tool_names

                result = await session.call_tool(
                    "list_opportunities", {"top": 3}
                )
    finally:
        await http_backend.aclose()

    assert result.isError is False, f"tool call failed: {result.content}"
    payload = json.loads(result.content[0].text)
    assert isinstance(payload, list)
    assert len(payload) <= 3
    if payload:
        first = payload[0]
        for key in ("id", "topic", "probability", "rating"):
            assert key in first
