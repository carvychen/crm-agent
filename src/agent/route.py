"""POST /api/chat — OpenAI-compatible SSE wrapper around an AF Agent.

The route translates inbound `{messages, stream}` bodies to AF `Message`
objects, sets the per-request `current_user_jwt` ContextVar so the MCP tool's
header provider can forward the bearer on every call, and streams AF Agent
`AgentResponseUpdate`s back to the client as `chat.completion.chunk` events
terminated by the OpenAI `[DONE]` sentinel.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent.builder import current_user_jwt

try:
    from agent_framework import Content, Message  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover — AF absent when ENABLE_REFERENCE_AGENT=false
    Content = Message = None  # type: ignore


_BEARER_PREFIX = "Bearer "


@runtime_checkable
class AgentLike(Protocol):
    def run(self, messages: Any, *, stream: bool, **kwargs: Any) -> Any: ...


def build_chat_route(agent: AgentLike) -> Route:
    async def chat(request: Request):
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if not auth or not auth.startswith(_BEARER_PREFIX):
            return JSONResponse(
                {
                    "error": "missing_bearer_token",
                    "message": "Authorization: Bearer <user-jwt> required",
                },
                status_code=401,
            )
        user_jwt = auth[len(_BEARER_PREFIX):].strip()

        body = await request.json()
        messages = _to_agent_framework_messages(body.get("messages", []))

        async def stream_events() -> AsyncIterator[ServerSentEvent]:
            chat_id = f"chatcmpl-{uuid.uuid4().hex}"
            ctx_token = current_user_jwt.set(user_jwt)
            try:
                async for update in agent.run(messages, stream=True):
                    text = getattr(update, "text", None)
                    if not text:
                        continue
                    yield ServerSentEvent(
                        data=json.dumps(
                            {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "choices": [
                                    {
                                        "delta": {"content": text},
                                        "index": 0,
                                        "finish_reason": None,
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        )
                    )
                # Terminal chunk mirroring OpenAI's shape (stop finish_reason).
                yield ServerSentEvent(
                    data=json.dumps(
                        {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "choices": [
                                {"delta": {}, "index": 0, "finish_reason": "stop"}
                            ],
                        }
                    )
                )
            finally:
                current_user_jwt.reset(ctx_token)
            yield ServerSentEvent(data="[DONE]")

        return EventSourceResponse(stream_events())

    return Route("/api/chat", endpoint=chat, methods=["POST"])


def _to_agent_framework_messages(payload: list[dict]) -> list:
    """Translate `{role, content}` JSON into AF `Message` objects."""
    if Message is None or Content is None:
        raise RuntimeError(
            "agent_framework is required to handle /api/chat but is not importable; "
            "this code path should be unreachable when ENABLE_REFERENCE_AGENT=false."
        )
    messages = []
    for raw in payload:
        text = raw.get("content")
        contents = [Content.from_text(text=text)] if text is not None else []
        messages.append(Message(role=raw["role"], contents=contents))
    return messages
