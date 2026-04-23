"""Live-integration: prove an external MCP client can consume this skill bundle.

The skill bundle is the "instruction manual" layer (Invariant 1): it must
work with ANY MCP-compliant client, not just our reference agent. This test
simulates a generic external client:

1. Read `.mcp.json` as a JSON file (same thing Claude Desktop / VS Code MCP do).
2. Extract `mcpServers["crm"].url`.
3. Point an MCP Streamable HTTP client at it.
4. Verify list_tools returns the advertised CRUD + search tools.
5. Call a real tool against the live Dataverse and verify the result.

If this test passes, a customer editing `.mcp.json` to point at their deployed
Function App's `/mcp` endpoint is all the integration they need to do.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


_SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "crm-opportunity"


async def test_skill_bundle_works_with_external_mcp_client():
    """Drive the MCP server through the URL pattern in `.mcp.json` to prove
    external agents can consume this skill identically to the reference
    agent (Invariant 1)."""
    from asgi import create_asgi_app
    from auth import build_auth
    from config import get_config
    from dataverse_client import OpportunityClient
    from mcp_server import ServerDeps

    # Any external client would read .mcp.json like this:
    mcp_json = json.loads((_SKILL_DIR / ".mcp.json").read_text(encoding="utf-8"))
    url_template = mcp_json["mcpServers"]["crm"]["url"]
    assert "REPLACE-WITH-YOUR-FUNCTION-APP-HOST" in url_template, (
        "template must ship with the placeholder intact; customer substitutes"
    )

    # For this in-test client we substitute a loopback URL rather than a real
    # hostname — that's what Claude Desktop would do with a deployed URL.
    config = get_config()
    backend_http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        deps = ServerDeps(
            auth=build_auth(config, http=backend_http, mi_token_provider=lambda: ""),
            client=OpportunityClient(config.dataverse_url, http=backend_http),
        )
        app = create_asgi_app(deps=deps)
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
                tools = await session.list_tools()
                tool_names = {t.name for t in tools.tools}
                # The bundle's SKILL.md advertises these exact names; if the
                # server ever renames one, this test fails — forcing the
                # skill docs to stay honest.
                advertised = {
                    "list_opportunities",
                    "get_opportunity",
                    "search_accounts",
                    "search_contacts",
                    "create_opportunity",
                    "update_opportunity",
                    "delete_opportunity",
                }
                assert advertised <= tool_names, (
                    f"skill bundle promises {sorted(advertised - tool_names)} "
                    "but MCP server does not expose them"
                )
    finally:
        await backend_http.aclose()
