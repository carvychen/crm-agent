"""End-to-end integration: POST /api/chat → AgentRuntime → HTTP MCP → mocked Dataverse.

Drives the full reference-agent path that ADR 0004 mandates (HTTP between
agent and MCP, never in-process). The LLM is scripted in Python so the test
is deterministic, but every other hop is real code:

- AgentRuntime invokes `StreamableHttpMCPClient.call_tool`
- which opens an MCP Streamable HTTP session through an ASGI HTTP transport
- which is served by the real `mcp_server` module
- which performs OBO against a mocked token endpoint
- and calls `dataverse_client.list_opportunities` against a mocked Dataverse.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport

from agent.llm_client.base import ChatEvent, LLMClient, Message, ToolCall
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


def _backend_handler(token_calls: list[dict], dv_calls: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/v2.0/token"):
            token_calls.append({"body": request.content.decode()})
            return httpx.Response(
                200, json={"access_token": "dv-token", "expires_in": 3600}
            )
        if "/api/data/v9.2/opportunities" in url:
            dv_calls.append(
                {
                    "auth": request.headers.get("Authorization"),
                    "params": dict(request.url.params),
                }
            )
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "opportunityid": "opp-1",
                            "name": "Enterprise Deal",
                            "_customerid_value@OData.Community.Display.V1.FormattedValue": "Fourth Coffee",
                            "opportunityratingcode": 1,
                            "opportunityratingcode@OData.Community.Display.V1.FormattedValue": "Hot",
                        }
                    ]
                },
            )
        return httpx.Response(404, text=f"unexpected: {url}")

    return handler


class _ScriptedLLM(LLMClient):
    """LLM that replays two turns: tool_call, then summary."""

    def __init__(self) -> None:
        self._turns = [
            [
                ChatEvent(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="call_1",
                        name="list_opportunities",
                        arguments=json.dumps({"top": 5}),
                    ),
                ),
                ChatEvent(type="done", usage={"prompt_tokens": 20, "completion_tokens": 4}),
            ],
            [
                ChatEvent(type="delta", content="你有 1 条商机："),
                ChatEvent(type="delta", content="Enterprise Deal。"),
                ChatEvent(type="done", usage={"prompt_tokens": 60, "completion_tokens": 10}),
            ],
        ]
        self.calls: list[dict] = []

    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        self.calls.append({"messages": list(messages)})
        turn = self._turns.pop(0) if self._turns else []
        for event in turn:
            yield event


def _write_prompts(tmp_path: Path) -> Path:
    (tmp_path / "system.zh.md").write_text(
        "你是一个 CRM 助手。今天是 {current_date}。", encoding="utf-8"
    )
    return tmp_path


async def test_post_api_chat_end_to_end_over_http(tmp_path: Path):
    from agent.mcp_client import StreamableHttpMCPClient
    from agent.prompts.loader import PromptLoader
    from agent.runtime.runtime import AgentRuntime
    from asgi import create_asgi_app
    from auth import DataverseAuth
    from dataverse_client import OpportunityClient
    from mcp_server import ServerDeps

    token_calls: list[dict] = []
    dv_calls: list[dict] = []
    backend_transport = httpx.MockTransport(_backend_handler(token_calls, dv_calls))

    # Override loader to skip {current_date} substitution requirement; we're
    # not asserting date content in this test.
    prompts_dir = tmp_path
    (tmp_path / "system.zh.md").write_text(
        "你是一个 CRM 助手。", encoding="utf-8"
    )
    prompts = PromptLoader(prompts_dir=prompts_dir)

    llm = _ScriptedLLM()

    async with httpx.AsyncClient(transport=backend_transport) as backend_http:
        deps = ServerDeps(
            auth=DataverseAuth(
                _global_config(),
                http=backend_http,
                mi_token_provider=lambda: "mi-fic-assertion",
            ),
            client=OpportunityClient(DATAVERSE_URL, http=backend_http),
        )

        # The MCPClient must speak HTTP — even in-test we use ASGITransport, so
        # an in-process shortcut is impossible (US 34). We build the ASGI app
        # first, THEN point the MCP client at it via a factory that preconfigures
        # the user's bearer token per call.
        agent_runtime_holder: dict = {}

        @contextlib.asynccontextmanager
        async def mcp_http_factory(user_jwt: str):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=agent_runtime_holder["app"]),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {user_jwt}"},
                follow_redirects=True,
                timeout=30.0,
            ) as c:
                yield c

        mcp_client = StreamableHttpMCPClient(
            mcp_url="http://testserver/mcp",
            http_client_factory=mcp_http_factory,
        )

        runtime = AgentRuntime(llm=llm, mcp=mcp_client, prompts=prompts)

        app = create_asgi_app(deps=deps, agent=runtime)
        agent_runtime_holder["app"] = app

        async with LifespanManager(app), httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/chat",
                json={
                    "messages": [{"role": "user", "content": "列出我的商机"}],
                    "stream": True,
                },
                headers={"Authorization": "Bearer user-jwt-alice"},
            )

    # 1. The chat endpoint returned a streaming SSE response.
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    # 2. The response carries the LLM's summary deltas and a [DONE] terminator.
    chunks = [
        json.loads(line[len("data: "):])
        for line in response.content.decode().splitlines()
        if line.startswith("data: ") and not line.endswith("[DONE]")
    ]
    assert any(
        c["choices"][0]["delta"].get("content") == "你有 1 条商机："
        for c in chunks
    )
    assert any(
        c["choices"][0]["delta"].get("content") == "Enterprise Deal。"
        for c in chunks
    )
    assert response.content.decode().rstrip().endswith("[DONE]")

    # 3. The LLM ran twice — once → tool call, once → summary.
    assert len(llm.calls) == 2

    # 4. The MCP tool call triggered OBO: the user's JWT was exchanged for a
    #    Dataverse-scoped token.
    assert len(token_calls) == 1
    assert "assertion=user-jwt-alice" in token_calls[0]["body"]

    # 5. Dataverse saw the EXCHANGED token, not the user's inbound JWT
    #    (proving OBO ran inside the MCP server, not bypassed by the agent).
    assert len(dv_calls) == 1
    assert dv_calls[0]["auth"] == "Bearer dv-token"
    assert dv_calls[0]["params"]["$top"] == "5"
