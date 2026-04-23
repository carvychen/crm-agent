"""Live-integration: the full reference-agent stack end-to-end.

`POST /api/chat` (real HTTP) → AF `Agent.run(stream=True)` → real Foundry
→ AF decides to call `list_opportunities` → AF's `MCPStreamableHTTPTool`
opens a real MCP Streamable HTTP session against the same Function App's
`/mcp` endpoint → real `src/mcp_server.py` → real Entra token exchange
(client-secret in dev mode, ADR 0007) → real Dataverse → rows stream back.

The ASGI app is served by an in-process uvicorn bound to a free local port;
every HTTP request therefore owns its own socket and task lifecycle, which
avoids the `cancel scope` task-ownership errors we hit when naively reusing
`httpx.ASGITransport` across AF's internal anyio task groups.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def _running_server(app, port: int):
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        # Wait until uvicorn has bound the port and completed startup.
        for _ in range(50):
            if getattr(server, "started", False):
                break
            await asyncio.sleep(0.05)
        else:  # pragma: no cover — uvicorn usually starts in <1s
            raise RuntimeError("uvicorn failed to start within 2.5s")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
        except asyncio.CancelledError:
            pass


async def test_api_chat_end_to_end_live():
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT not configured")

    from agent.builder import build_agent
    from agent.prompts.loader import PromptLoader
    from asgi import create_asgi_app
    from auth import build_auth
    from config import get_config
    from dataverse_client import OpportunityClient
    from mcp_server import ServerDeps

    def _foundry_credential():
        cid = os.environ.get("FOUNDRY_AZURE_CLIENT_ID")
        csecret = os.environ.get("FOUNDRY_AZURE_CLIENT_SECRET")
        ctenant = os.environ.get("FOUNDRY_AZURE_TENANT_ID")
        if cid and csecret and ctenant:
            from azure.identity import ClientSecretCredential

            return ClientSecretCredential(
                tenant_id=ctenant, client_id=cid, client_secret=csecret
            )
        from azure.identity import AzureCliCredential

        return AzureCliCredential()

    port = _free_port()
    config = get_config()
    backend_http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    prompts_dir = Path(__file__).resolve().parents[2] / "src" / "agent" / "prompts"

    deps = ServerDeps(
        auth=build_auth(config, http=backend_http, mi_token_provider=lambda: ""),
        client=OpportunityClient(config.dataverse_url, http=backend_http),
    )
    agent = build_agent(
        project_endpoint=endpoint,
        model=os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini"),
        mcp_url=f"http://127.0.0.1:{port}/mcp",
        prompts=PromptLoader(prompts_dir=prompts_dir),
        credential=_foundry_credential(),
    )
    app = create_asgi_app(deps=deps, agent=agent)

    try:
        async with _running_server(app, port) as base_url:
            async with httpx.AsyncClient(
                base_url=base_url, timeout=httpx.Timeout(120.0)
            ) as client:
                response = await client.post(
                    "/api/chat",
                    json={
                        "messages": [
                            {
                                "role": "user",
                                "content": "列出我的前 3 条商机",
                            }
                        ],
                        "stream": True,
                    },
                    headers={"Authorization": "Bearer ignored-under-app-only"},
                )

                assert response.status_code == 200
                assert response.headers["content-type"].startswith(
                    "text/event-stream"
                )
                body = response.content.decode()

        # Close AF's MCP session so its anyio task group tears down cleanly
        # before the event loop ends.
        try:
            await agent.mcp_tools[0].close()
        except RuntimeError as exc:
            if "cancel scope" not in str(exc).lower():
                raise
    finally:
        await backend_http.aclose()

    assert body.rstrip().endswith("[DONE]"), f"stream did not terminate: {body[-200:]}"

    chunks = [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ") and not line.endswith("[DONE]")
    ]
    assert chunks, "expected at least one streamed chunk"
    assert all(c.get("object") == "chat.completion.chunk" for c in chunks)

    contents = [
        c["choices"][0]["delta"].get("content")
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    ]
    assert contents, f"no text streamed from the agent: {body[:500]}"
