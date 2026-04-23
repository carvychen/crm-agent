"""StreamableHttpMCPClient — the concrete `MCPClient` the reference agent uses.

Opens a fresh Streamable HTTP session per tool call so that a per-user JWT can
be attached without risking header bleed between concurrent callers. Session
lifetime is bounded by a single tool call; request pooling is a future
optimisation, not a correctness requirement.
"""
from __future__ import annotations

import json
from typing import AsyncContextManager, Callable

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


HttpClientFactory = Callable[[str], AsyncContextManager[httpx.AsyncClient]]


class StreamableHttpMCPClient:
    """MCP client over Streamable HTTP, scoped per tool call for per-user isolation."""

    def __init__(self, *, mcp_url: str, http_client_factory: HttpClientFactory) -> None:
        self._url = mcp_url
        self._factory = http_client_factory

    async def call_tool(self, name: str, arguments: dict, *, user_jwt: str) -> str:
        async with self._factory(user_jwt) as http_client:
            async with streamable_http_client(
                self._url, http_client=http_client
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name=name, arguments=arguments)

        if result.isError:
            text = result.content[0].text if result.content else "tool error"
            raise RuntimeError(f"MCP tool {name!r} failed: {text}")

        # Walking-skeleton contract: tools return a single TextContent whose
        # `.text` is the payload (JSON-encoded structured data or plain text).
        if not result.content:
            return ""
        return result.content[0].text
