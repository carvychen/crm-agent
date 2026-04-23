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

_GET_OPPORTUNITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "opportunity_id": {
            "type": "string",
            "description": "GUID of the opportunity to fetch.",
        }
    },
    "required": ["opportunity_id"],
    "additionalProperties": False,
}

_RATING_VALUES = [1, 2, 3]  # 1=Hot, 2=Warm, 3=Cold (Dataverse opportunityratingcode)
_CUSTOMER_TYPES = ["account", "contact"]

_CREATE_OPPORTUNITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Topic / name of the opportunity.",
        },
        "customer_id": {
            "type": "string",
            "description": "GUID of the Account or Contact the opportunity is for.",
        },
        "customer_type": {
            "type": "string",
            "enum": _CUSTOMER_TYPES,
            "description": "Which kind of record `customer_id` points at.",
        },
        "estimated_value": {
            "type": "number",
            "description": "Estimated revenue.",
        },
        "estimated_close_date": {
            "type": "string",
            "description": "Estimated close date, YYYY-MM-DD.",
        },
        "probability": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "Win probability, 0-100.",
        },
        "rating": {
            "type": "integer",
            "enum": _RATING_VALUES,
            "description": "1=Hot, 2=Warm, 3=Cold.",
        },
    },
    "required": ["name", "customer_id", "customer_type"],
    "additionalProperties": False,
}

_UPDATE_OPPORTUNITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "opportunity_id": {
            "type": "string",
            "description": "GUID of the opportunity to update.",
        },
        "name": {"type": "string"},
        "estimated_value": {"type": "number"},
        "estimated_close_date": {"type": "string"},
        "probability": {"type": "integer", "minimum": 0, "maximum": 100},
        "rating": {"type": "integer", "enum": _RATING_VALUES},
    },
    "required": ["opportunity_id"],
    "additionalProperties": False,
}

_DELETE_OPPORTUNITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "opportunity_id": {
            "type": "string",
            "description": "GUID of the opportunity to delete (destructive, irreversible).",
        }
    },
    "required": ["opportunity_id"],
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
            types.Tool(
                name="get_opportunity",
                description="Fetch a single opportunity by its GUID.",
                inputSchema=_GET_OPPORTUNITY_SCHEMA,
            ),
            types.Tool(
                name="create_opportunity",
                description=(
                    "Create a new Dynamics 365 opportunity. `customer_type` picks "
                    "the polymorphic customerid binding (account or contact). "
                    "Rating is the Dataverse enum: 1=Hot, 2=Warm, 3=Cold."
                ),
                inputSchema=_CREATE_OPPORTUNITY_SCHEMA,
            ),
            types.Tool(
                name="update_opportunity",
                description=(
                    "Patch an existing opportunity. Only fields supplied in the "
                    "call are modified; omitted fields are left untouched on the server."
                ),
                inputSchema=_UPDATE_OPPORTUNITY_SCHEMA,
            ),
            types.Tool(
                name="delete_opportunity",
                description=(
                    "Permanently delete an opportunity. Destructive and "
                    "irreversible — the reference agent gates this behind a "
                    "user confirmation (ADR 0005, Slice 3)."
                ),
                inputSchema=_DELETE_OPPORTUNITY_SCHEMA,
            ),
        ]

    @srv.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None):
        args = arguments or {}
        user_jwt = current_user_jwt.get()
        token = await deps.auth.get_dataverse_token(user_jwt)

        if name == "list_opportunities":
            rows = await deps.client.list_opportunities(
                token=token,
                filter=args.get("filter"),
                top=args.get("top"),
                orderby=args.get("orderby"),
            )
            return _json_text(rows)

        if name == "get_opportunity":
            row = await deps.client.get_opportunity(
                token=token,
                opportunity_id=_required(args, "opportunity_id"),
            )
            return _json_text(row)

        if name == "create_opportunity":
            _validate_rating(args.get("rating"))
            _validate_customer_type(args.get("customer_type"))
            new_id = await deps.client.create_opportunity(
                token=token,
                name=_required(args, "name"),
                customer_id=_required(args, "customer_id"),
                customer_type=_required(args, "customer_type"),
                estimated_value=args.get("estimated_value"),
                estimated_close_date=args.get("estimated_close_date"),
                probability=args.get("probability"),
                rating=args.get("rating"),
            )
            return _json_text({"opportunity_id": new_id})

        if name == "update_opportunity":
            _validate_rating(args.get("rating"))
            await deps.client.update_opportunity(
                token=token,
                opportunity_id=_required(args, "opportunity_id"),
                name=args.get("name"),
                estimated_value=args.get("estimated_value"),
                estimated_close_date=args.get("estimated_close_date"),
                probability=args.get("probability"),
                rating=args.get("rating"),
            )
            return _json_text({"updated": True})

        if name == "delete_opportunity":
            await deps.client.delete_opportunity(
                token=token,
                opportunity_id=_required(args, "opportunity_id"),
            )
            return _json_text({"deleted": True})

        raise ValueError(f"Unknown tool: {name}")

    return srv


def _required(args: dict[str, Any], key: str) -> Any:
    if key not in args or args[key] is None:
        raise ValueError(f"Required argument {key!r} is missing.")
    return args[key]


def _validate_rating(rating: int | None) -> None:
    if rating is not None and rating not in _RATING_VALUES:
        raise ValueError(
            f"rating={rating!r} is invalid. Expected one of: {_RATING_VALUES} "
            "(1=Hot, 2=Warm, 3=Cold)."
        )


def _validate_customer_type(customer_type: str | None) -> None:
    if customer_type is not None and customer_type not in _CUSTOMER_TYPES:
        raise ValueError(
            f"customer_type={customer_type!r} is invalid. "
            f"Expected one of: {_CUSTOMER_TYPES}."
        )


def _json_text(payload: Any) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
