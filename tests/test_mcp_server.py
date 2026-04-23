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
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        single: dict[str, Any] | None = None,
        new_id: str = "new-opp-id",
    ) -> None:
        self.rows = rows or [{"id": "opp-1", "topic": "Enterprise Deal"}]
        self.single = single or {"id": "opp-1", "topic": "Enterprise Deal"}
        self.new_id = new_id
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    async def list_opportunities(self, **kwargs):
        self.list_calls.append(kwargs)
        return self.rows

    async def get_opportunity(self, **kwargs):
        self.get_calls.append(kwargs)
        return self.single

    async def create_opportunity(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.new_id

    async def update_opportunity(self, **kwargs):
        self.update_calls.append(kwargs)

    async def delete_opportunity(self, **kwargs):
        self.delete_calls.append(kwargs)


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


async def test_list_tools_exposes_full_opportunity_crud_set():
    """After Slice 3 the MCP server advertises list/get/create/update/delete."""
    from mcp_server import ServerDeps, build_server

    deps = ServerDeps(auth=_FakeAuth(), client=_FakeClient())
    srv = build_server(deps)

    tools = await _list_tools(srv)
    names = {t.name for t in tools}

    assert {
        "list_opportunities",
        "get_opportunity",
        "create_opportunity",
        "update_opportunity",
        "delete_opportunity",
    } <= names


async def test_get_opportunity_tool_routes_user_jwt_and_id():
    from mcp_server import ServerDeps, build_server, current_user_jwt

    auth = _FakeAuth(token="dv-for-user")
    client = _FakeClient(single={"id": "opp-42", "topic": "Test"})
    srv = build_server(ServerDeps(auth=auth, client=client))

    ctx_token = current_user_jwt.set("user-jwt-alice")
    try:
        result = await _call_tool(srv, "get_opportunity", {"opportunity_id": "opp-42"})
    finally:
        current_user_jwt.reset(ctx_token)

    assert result.isError is False
    assert auth.calls == ["user-jwt-alice"]
    assert len(client.get_calls) == 1
    assert client.get_calls[0]["token"] == "dv-for-user"
    assert client.get_calls[0]["opportunity_id"] == "opp-42"
    payload = json.loads(result.content[0].text)
    assert payload == {"id": "opp-42", "topic": "Test"}


async def test_create_opportunity_tool_forwards_all_supported_fields():
    from mcp_server import ServerDeps, build_server, current_user_jwt

    client = _FakeClient(new_id="opp-new")
    srv = build_server(ServerDeps(auth=_FakeAuth(), client=client))

    args = {
        "name": "Enterprise Deal",
        "customer_id": "22222222-bbbb-bbbb-bbbb-222222222222",
        "customer_type": "account",
        "estimated_value": 80000.0,
        "estimated_close_date": "2026-06-30",
        "probability": 80,
        "rating": 1,
    }
    ctx_token = current_user_jwt.set("user-jwt-bob")
    try:
        result = await _call_tool(srv, "create_opportunity", args)
    finally:
        current_user_jwt.reset(ctx_token)

    assert result.isError is False
    call = client.create_calls[0]
    for key in (
        "name",
        "customer_id",
        "customer_type",
        "estimated_value",
        "estimated_close_date",
        "probability",
        "rating",
    ):
        assert call[key] == args[key], f"forward failed for {key}"
    # The tool result is the new GUID as JSON string; LLM uses it for follow-ups.
    payload = json.loads(result.content[0].text)
    assert payload == {"opportunity_id": "opp-new"}


async def test_create_opportunity_tool_rejects_invalid_rating():
    """Rating must be 1/2/3 (Hot/Warm/Cold) — enum validation at the tool layer."""
    from mcp_server import ServerDeps, build_server, current_user_jwt

    srv = build_server(ServerDeps(auth=_FakeAuth(), client=_FakeClient()))

    ctx_token = current_user_jwt.set("user-jwt")
    try:
        result = await _call_tool(
            srv,
            "create_opportunity",
            {
                "name": "x",
                "customer_id": "22222222-bbbb-bbbb-bbbb-222222222222",
                "customer_type": "account",
                "rating": 7,  # invalid
            },
        )
    finally:
        current_user_jwt.reset(ctx_token)

    assert result.isError is True
    # The MCP SDK's schema-layer validator catches rating=7 before our handler
    # even runs (enum=[1,2,3]); our own _validate_rating is the fallback for
    # callers that bypass `list_tools` schemas. Either layer fires as long as
    # the error surfaces the offending value.
    text = result.content[0].text.lower()
    assert "7" in text or "rating" in text


async def test_update_opportunity_tool_passes_through_partial_fields():
    from mcp_server import ServerDeps, build_server, current_user_jwt

    client = _FakeClient()
    srv = build_server(ServerDeps(auth=_FakeAuth(), client=client))

    ctx_token = current_user_jwt.set("user-jwt")
    try:
        result = await _call_tool(
            srv,
            "update_opportunity",
            {"opportunity_id": "opp-1", "probability": 90},
        )
    finally:
        current_user_jwt.reset(ctx_token)

    assert result.isError is False
    call = client.update_calls[0]
    assert call["opportunity_id"] == "opp-1"
    assert call["probability"] == 90
    # Fields the caller didn't set must not be forwarded.
    for absent in ("name", "estimated_value", "estimated_close_date", "rating"):
        assert absent not in call or call[absent] is None


async def test_delete_opportunity_tool_routes_id_and_user_jwt():
    from mcp_server import ServerDeps, build_server, current_user_jwt

    client = _FakeClient()
    auth = _FakeAuth()
    srv = build_server(ServerDeps(auth=auth, client=client))

    ctx_token = current_user_jwt.set("user-jwt-carol")
    try:
        result = await _call_tool(srv, "delete_opportunity", {"opportunity_id": "opp-1"})
    finally:
        current_user_jwt.reset(ctx_token)

    assert result.isError is False
    assert auth.calls == ["user-jwt-carol"]
    assert client.delete_calls[0]["opportunity_id"] == "opp-1"


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
