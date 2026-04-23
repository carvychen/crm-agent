"""Tests for POST /api/chat — reference-agent SSE entry point."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport

from agent.llm_client.base import ChatEvent, Message


def _parse_sse(body: bytes) -> list[dict | str]:
    """Yield each `data:` payload from an SSE body, JSON-decoded unless [DONE]."""
    out: list[dict | str] = []
    for line in body.decode().splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            out.append("[DONE]")
        else:
            out.append(json.loads(payload))
    return out


class _FakeRuntime:
    """Runtime double: replays a canned sequence of ChatEvents per /api/chat call."""

    def __init__(self, events: list[ChatEvent]) -> None:
        self._events = events
        self.calls: list[dict] = []

    async def chat(
        self, *, messages: list[Message], user_jwt: str
    ) -> AsyncIterator[ChatEvent]:
        self.calls.append({"messages": list(messages), "user_jwt": user_jwt})
        for event in self._events:
            yield event


async def test_chat_route_streams_openai_compatible_sse(tmp_path: Path):
    """POST /api/chat → SSE chunks (deltas + usage) → [DONE] sentinel."""
    from agent.prompts.loader import PromptLoader  # noqa: F401
    from asgi import create_asgi_app
    from mcp_server import ServerDeps

    # mcp_server deps are unused by /api/chat but required by create_asgi_app's
    # shape (the MCP endpoint and the agent route share the same app).
    class _NullAuth:
        async def get_dataverse_token(self, user_jwt: str) -> str:
            raise AssertionError("/api/chat path must not touch OBO directly")

    class _NullClient:
        async def list_opportunities(self, **_):
            raise AssertionError("/api/chat path must not touch Dataverse directly")

    runtime = _FakeRuntime(
        [
            ChatEvent(type="delta", content="你好，"),
            ChatEvent(type="delta", content="我是 CRM 助手。"),
            ChatEvent(
                type="done",
                usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            ),
        ]
    )

    app = create_asgi_app(
        deps=ServerDeps(auth=_NullAuth(), client=_NullClient()),
        agent=runtime,
    )

    async with LifespanManager(app), httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "你好"}], "stream": True},
            headers={"Authorization": "Bearer user-jwt-bob"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.content)
    assert events[-1] == "[DONE]"  # OpenAI-compatible terminator

    # Non-terminator events follow OpenAI chat.completion.chunk shape.
    chunks = [e for e in events if e != "[DONE]"]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    # Text deltas carry `choices[].delta.content`.
    delta_contents = [
        c["choices"][0]["delta"]["content"]
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    ]
    assert delta_contents == ["你好，", "我是 CRM 助手。"]
    # Usage is included on the terminal chunk only.
    usage_chunks = [c for c in chunks if "usage" in c and c["usage"] is not None]
    assert len(usage_chunks) == 1
    assert usage_chunks[0]["usage"]["total_tokens"] == 14

    # The route forwarded the user JWT and the posted messages to the runtime.
    assert len(runtime.calls) == 1
    assert runtime.calls[0]["user_jwt"] == "user-jwt-bob"
    assert runtime.calls[0]["messages"][0].role == "user"
    assert runtime.calls[0]["messages"][0].content == "你好"


async def test_chat_route_is_absent_when_reference_agent_disabled(tmp_path: Path):
    """When agent=None (ENABLE_REFERENCE_AGENT=false), POST /api/chat returns 404."""
    from asgi import create_asgi_app
    from mcp_server import ServerDeps

    class _NullAuth:
        async def get_dataverse_token(self, user_jwt: str) -> str: ...

    class _NullClient:
        async def list_opportunities(self, **_): ...

    app = create_asgi_app(
        deps=ServerDeps(auth=_NullAuth(), client=_NullClient()),
        agent=None,
    )

    async with LifespanManager(app), httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/chat",
            json={"messages": []},
            headers={"Authorization": "Bearer user-jwt"},
        )

    assert response.status_code == 404
