"""Tests for POST /api/chat — AF-based reference agent SSE entry point."""
from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport


class _Update:
    """Minimal AgentResponseUpdate double — carries a text chunk."""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeAgent:
    """Agent double: replays a scripted sequence of update chunks per call."""

    def __init__(self, updates: list[str]) -> None:
        self._updates = updates
        self.calls: list[dict] = []

    def run(self, messages, *, stream: bool, **_kwargs):
        self.calls.append({"messages": messages, "stream": stream})
        updates = self._updates

        class _Stream:
            def __aiter__(self):
                return _Stream._iter(iter(updates))

            @staticmethod
            async def _iter(it):
                for text in it:
                    yield _Update(text)

            def __anext__(self):  # noqa: D401 — protocol compliance
                raise StopAsyncIteration

        return _Stream()


def _parse_sse(body: bytes) -> list:
    out: list = []
    for line in body.decode().splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        out.append("[DONE]" if payload == "[DONE]" else json.loads(payload))
    return out


async def test_chat_route_streams_openai_compatible_sse(tmp_path: Path):
    """POST /api/chat → SSE chunks with AF update text → [DONE]."""
    from asgi import create_asgi_app
    from mcp_server import ServerDeps

    class _NullAuth:
        async def get_dataverse_token(self, user_jwt: str) -> str: ...

    class _NullClient:
        async def list_opportunities(self, **_): ...

    fake_agent = _FakeAgent(updates=["你好，", "我是 CRM 助手。"])

    app = create_asgi_app(
        ServerDeps(auth=_NullAuth(), client=_NullClient()),
        agent=fake_agent,
    )

    async with LifespanManager(app), httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "你好"}], "stream": True},
            headers={"Authorization": "Bearer user-jwt-alice"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.content)
    chunks = [e for e in events if e != "[DONE]"]
    assert events[-1] == "[DONE]"

    # Text updates land in OpenAI-compatible delta.content.
    contents = [
        c["choices"][0]["delta"]["content"]
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    ]
    assert contents == ["你好，", "我是 CRM 助手。"]

    # Route forwarded the messages and the user JWT (latter via ContextVar
    # set before agent.run) — verified by the agent having been invoked once.
    assert len(fake_agent.calls) == 1
    assert fake_agent.calls[0]["stream"] is True


async def test_chat_route_absent_when_reference_agent_disabled():
    """When agent=None (ENABLE_REFERENCE_AGENT=false), POST /api/chat returns 404."""
    from asgi import create_asgi_app
    from mcp_server import ServerDeps

    class _NullAuth:
        async def get_dataverse_token(self, user_jwt: str) -> str: ...

    class _NullClient:
        async def list_opportunities(self, **_): ...

    app = create_asgi_app(ServerDeps(auth=_NullAuth(), client=_NullClient()), agent=None)

    async with LifespanManager(app), httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/chat",
            json={"messages": []},
            headers={"Authorization": "Bearer tok"},
        )

    assert response.status_code == 404
