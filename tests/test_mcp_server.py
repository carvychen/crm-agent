"""Tests for src/mcp_server.py — MCP Server registration + routing."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from mcp import types


async def _list_tools(srv):
    handler = srv.request_handlers[types.ListToolsRequest]
    result = await handler(types.ListToolsRequest(method="tools/list", params=None))
    return result.root.tools


async def _call_tool(srv, name: str, arguments: dict | None):
    handler = srv.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = await handler(req)
    return result.root


class _FakeAuth:
    def __init__(self, token: str = "dv-token") -> None:
        self.token = token
        self.calls: list[str] = []

    async def get_dataverse_token(self, user_jwt: str) -> str:
        self.calls.append(user_jwt)
        return self.token


class _FakeClient:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or [{"id": "opp-1", "topic": "Enterprise Deal"}]
        self.list_calls: list[dict[str, Any]] = []

    async def list_opportunities(self, **kwargs):
        self.list_calls.append(kwargs)
        return self.rows


async def test_list_tools_returns_list_opportunities_with_self_describing_schema():
    """The MCP server advertises list_opportunities with a full JSON schema (US 12)."""
    from mcp_server import ServerDeps, build_server

    deps = ServerDeps(auth=_FakeAuth(), client=_FakeClient())
    srv = build_server(deps)

    tools = await _list_tools(srv)

    names = [t.name for t in tools]
    assert "list_opportunities" in names

    tool = next(t for t in tools if t.name == "list_opportunities")
    assert tool.description  # non-empty description is part of the contract
    schema = tool.inputSchema
    assert schema["type"] == "object"
    # Every documented argument appears in the schema.
    for arg in ("filter", "top", "orderby"):
        assert arg in schema["properties"], f"schema missing {arg}"
    # Types are documented (no untyped any-objects).
    assert schema["properties"]["filter"]["type"] == "string"
    assert schema["properties"]["top"]["type"] == "integer"
    assert schema["properties"]["orderby"]["type"] == "string"


async def test_call_tool_forwards_user_jwt_to_auth_and_invokes_client():
    """call_tool reads the current user JWT, exchanges it, and passes args to the client."""
    from mcp_server import ServerDeps, build_server, current_user_jwt

    auth = _FakeAuth(token="dv-token-for-user")
    client = _FakeClient(rows=[{"id": "opp-42", "topic": "Enterprise"}])
    srv = build_server(ServerDeps(auth=auth, client=client))

    token = current_user_jwt.set("user-jwt-xyz")
    try:
        result = await _call_tool(
            srv,
            "list_opportunities",
            {"filter": "opportunityratingcode eq 1", "top": 5},
        )
    finally:
        current_user_jwt.reset(token)

    # 1. Auth saw the current user's JWT.
    assert auth.calls == ["user-jwt-xyz"]
    # 2. Client was invoked with the exchanged token + forwarded args.
    assert len(client.list_calls) == 1
    call = client.list_calls[0]
    assert call["token"] == "dv-token-for-user"
    assert call["filter"] == "opportunityratingcode eq 1"
    assert call["top"] == 5
    assert call["orderby"] is None
    # 3. Tool response carries the rows as JSON text content.
    contents = result.content
    assert len(contents) == 1
    payload = json.loads(contents[0].text)
    assert payload == [{"id": "opp-42", "topic": "Enterprise"}]


async def test_call_tool_rejects_unknown_name():
    """A tool name this server does not expose raises a clear error."""
    from mcp_server import ServerDeps, build_server, current_user_jwt

    srv = build_server(ServerDeps(auth=_FakeAuth(), client=_FakeClient()))

    token = current_user_jwt.set("user-jwt")
    try:
        result = await _call_tool(srv, "drop_database", {})
    finally:
        current_user_jwt.reset(token)
    # The SDK wraps raised exceptions into a tool-error result.
    assert result.isError is True
    assert "drop_database" in result.content[0].text
