"""Tests for the agent's MCP integration surface.

The end-to-end path (AF tool → real MCP server over HTTP → OBO → Dataverse) is
covered by Slice 1's `tests/test_integration.py` (MCP protocol end-to-end) and
will be re-verified against a real tenant by Slice 8's pre-flight script
(#10). Here we assert just the seam that Slice 2 adds: the header provider
bound to `current_user_jwt` produces per-request Authorization headers that
isolate concurrent callers.
"""
from __future__ import annotations

import asyncio

import pytest


def test_header_provider_reads_current_user_jwt_contextvar():
    """bearer_header_provider returns the JWT set in the ContextVar as Bearer."""
    from agent.builder import bearer_header_provider, current_user_jwt

    token = current_user_jwt.set("user-jwt-carol")
    try:
        headers = bearer_header_provider({"tool_name": "list_opportunities"})
    finally:
        current_user_jwt.reset(token)

    assert headers == {"Authorization": "Bearer user-jwt-carol"}


def test_header_provider_errors_when_contextvar_unset():
    """A header-provider call outside a request context fails loudly — no silent
    bleed to a prior user's token."""
    from agent.builder import bearer_header_provider

    # current_user_jwt has no default; get() must raise.
    with pytest.raises(LookupError):
        bearer_header_provider({})


async def test_contextvar_isolated_between_concurrent_tasks():
    """ContextVar values do not bleed across concurrent /api/chat handlers."""
    from agent.builder import bearer_header_provider, current_user_jwt

    seen: dict[str, str] = {}

    async def handler(user_jwt: str, tag: str) -> None:
        token = current_user_jwt.set(user_jwt)
        try:
            await asyncio.sleep(0.01)  # interleave with the other task
            headers = bearer_header_provider({})
            seen[tag] = headers["Authorization"]
        finally:
            current_user_jwt.reset(token)

    await asyncio.gather(
        handler("user-jwt-alice", "a"),
        handler("user-jwt-bob", "b"),
    )

    assert seen == {
        "a": "Bearer user-jwt-alice",
        "b": "Bearer user-jwt-bob",
    }
