"""MCP Server wiring — registers the CRM tools against a self-hosted MCP Server.

Per ADR 0002 we use the official MCP SDK low-level `Server` (not the Functions
MCP extension). The HTTP+SSE mount lands in the Functions-route module; this
module only describes the tool contract and the dependency wiring used both by
unit tests (direct handler invocation) and by the HTTP transport at runtime.

Per-request user identity flows in via the `current_user_jwt` ContextVar. The
HTTP entrypoint MUST set it from the inbound `Authorization: Bearer …` header
before dispatching the MCP request; tests set it directly.
"""
from __future__ import annotations

import contextvars
import json
from dataclasses import dataclass
from typing import Any, Protocol

from mcp import types
from mcp.server.lowlevel import Server


class _AuthLike(Protocol):
    async def get_dataverse_token(self, user_jwt: str) -> str: ...


class _ClientLike(Protocol):
    async def list_opportunities(
        self,
        *,
        token: str,
        filter: str | None = ...,
        top: int | None = ...,
        orderby: str | None = ...,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class ServerDeps:
    auth: _AuthLike
    client: _ClientLike


current_user_jwt: contextvars.ContextVar[str] = contextvars.ContextVar("current_user_jwt")


_LIST_OPPORTUNITIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "filter": {
            "type": "string",
            "description": "OData $filter expression, e.g. \"opportunityratingcode eq 1\".",
        },
        "top": {
            "type": "integer",
            "minimum": 1,
            "description": "Maximum number of records to return.",
        },
        "orderby": {
            "type": "string",
            "description": "OData $orderby, e.g. \"estimatedvalue desc\".",
        },
    },
    "additionalProperties": False,
}


def build_server(deps: ServerDeps) -> Server:
    """Create an MCP `Server` with CRM tools registered."""
    srv = Server("crm-mcp")

    @srv.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="list_opportunities",
                description=(
                    "List Dynamics 365 opportunities the calling user may see. "
                    "Dataverse row-level security applies automatically."
                ),
                inputSchema=_LIST_OPPORTUNITIES_SCHEMA,
            ),
        ]

    @srv.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None):
        args = arguments or {}
        if name != "list_opportunities":
            raise ValueError(f"Unknown tool: {name}")
        user_jwt = current_user_jwt.get()
        token = await deps.auth.get_dataverse_token(user_jwt)
        rows = await deps.client.list_opportunities(
            token=token,
            filter=args.get("filter"),
            top=args.get("top"),
            orderby=args.get("orderby"),
        )
        return [types.TextContent(type="text", text=json.dumps(rows, ensure_ascii=False))]

    return srv
