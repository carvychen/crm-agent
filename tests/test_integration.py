"""End-to-end integration: user JWT → HTTP → MCP → OBO → mocked Dataverse.

Asserts the full tracer-bullet path: an MCP client speaking Streamable HTTP to
the ASGI app receives list_opportunities results, with the server transparently
performing the OBO exchange. Nothing in this test imports MCP tool functions
directly — the MCP client drives the server over HTTP the same way any external
MCP client would (ADR 0004, US 34).
"""
from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from config import CloudConfig


DATAVERSE_URL = "https://orgtest.crm.dynamics.com"


def _global_config() -> CloudConfig:
    return CloudConfig(
        authority="https://login.microsoftonline.com",
        dataverse_url=DATAVERSE_URL,
        fic_audience="api://AzureADTokenExchange",
        aad_app_client_id="11111111-1111-1111-1111-111111111111",
        aad_app_tenant_id="22222222-2222-2222-2222-222222222222",
    )


def _backend_mock_handler(token_calls: list[dict], dv_calls: list[dict]):
    """MockTransport handler for the server-side httpx (token + Dataverse)."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/v2.0/token"):
            token_calls.append({"body": request.content.decode()})
            return httpx.Response(
                200,
                json={"access_token": "dv-token", "expires_in": 3600},
            )
        if "/api/data/v9.2/opportunities" in url:
            dv_calls.append(
                {
                    "method": request.method,
                    "url": url,
                    "auth": request.headers.get("Authorization"),
                    "params": dict(request.url.params),
                }
            )
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "opportunityid": "aaaa1111-bbbb-cccc-dddd-eeee22223333",
                            "name": "Enterprise Deal",
                            "estimatedclosedate": "2026-06-30",
                            "estimatedvalue": 80000.0,
                            "_customerid_value": "acc-1",
                            "_customerid_value@OData.Community.Display.V1.FormattedValue": "Fourth Coffee",
                            "closeprobability": 80,
                            "opportunityratingcode": 1,
                            "opportunityratingcode@OData.Community.Display.V1.FormattedValue": "Hot",
                        }
                    ]
                },
            )
        return httpx.Response(404, text=f"unexpected backend URL: {url}")

    return handler


async def test_end_to_end_list_opportunities_over_http():
    import json as stdjson

    from asgi import create_asgi_app
    from auth import DataverseAuth
    from dataverse_client import OpportunityClient
    from mcp_server import ServerDeps

    token_calls: list[dict] = []
    dv_calls: list[dict] = []
    backend_transport = httpx.MockTransport(_backend_mock_handler(token_calls, dv_calls))

    async with httpx.AsyncClient(transport=backend_transport) as backend_http:
        config = _global_config()
        deps = ServerDeps(
            auth=DataverseAuth(
                config,
                http=backend_http,
                mi_token_provider=lambda: "mi-fic-assertion",
            ),
            client=OpportunityClient(DATAVERSE_URL, http=backend_http),
        )

        asgi_app = create_asgi_app(deps)
        # Drive the ASGI app via httpx.ASGITransport + asgi-lifespan so the MCP
        # traffic flows through real ASGI request/response + lifespan machinery
        # (not an in-process import shortcut).
        user_jwt = "user-jwt-carol"
        async with LifespanManager(asgi_app), httpx.AsyncClient(
            transport=ASGITransport(app=asgi_app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {user_jwt}"},
            follow_redirects=True,
            timeout=30.0,
        ) as client_http, streamable_http_client(
            "http://testserver/mcp", http_client=client_http
        ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    tools = await session.list_tools()
                    tool_names = [t.name for t in tools.tools]
                    assert "list_opportunities" in tool_names

                    result = await session.call_tool(
                        "list_opportunities", {"top": 10}
                    )

    # End-to-end behavior assertions.
    assert result.isError is False
    payload = stdjson.loads(result.content[0].text)
    assert payload == [
        {
            "id": "aaaa1111-bbbb-cccc-dddd-eeee22223333",
            "topic": "Enterprise Deal",
            "potential_customer": "Fourth Coffee",
            "est_close_date": "2026-06-30",
            "est_revenue": 80000.0,
            "contact": "",
            "account": "",
            "probability": 80,
            "rating": "Hot",
        }
    ]

    # OBO happened against the token endpoint, once per unique user.
    assert len(token_calls) == 1
    assert f"assertion=user-jwt-carol" in token_calls[0]["body"]

    # Dataverse saw the exchanged token (not the user's inbound JWT).
    assert len(dv_calls) == 1
    assert dv_calls[0]["auth"] == "Bearer dv-token"
    assert dv_calls[0]["params"]["$top"] == "10"
