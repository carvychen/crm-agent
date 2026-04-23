"""POST /api/chat — OpenAI-compatible SSE entry point for the reference agent."""
from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent.llm_client.base import ChatEvent, Message, ToolCall
from agent.runtime.runtime import AgentRuntime


_BEARER_PREFIX = "Bearer "


def build_chat_route(runtime: AgentRuntime) -> Route:
    """Return a Starlette Route mounting `POST /api/chat` on the given runtime."""

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
        messages = [_message_from_json(m) for m in body.get("messages", [])]

        async def event_stream() -> AsyncIterator[ServerSentEvent]:
            chat_id = f"chatcmpl-{uuid.uuid4().hex}"
            async for event in runtime.chat(messages=messages, user_jwt=user_jwt):
                yield ServerSentEvent(data=_encode_chunk(event, chat_id))
            yield ServerSentEvent(data="[DONE]")

        return EventSourceResponse(event_stream())

    return Route("/api/chat", endpoint=chat, methods=["POST"])


def _message_from_json(payload: dict) -> Message:
    tool_calls = tuple(
        ToolCall(
            id=tc["id"],
            name=tc["function"]["name"],
            arguments=tc["function"].get("arguments", ""),
        )
        for tc in payload.get("tool_calls", []) or []
    )
    return Message(
        role=payload["role"],
        content=payload.get("content"),
        tool_calls=tool_calls,
        tool_call_id=payload.get("tool_call_id"),
    )


def _encode_chunk(event: ChatEvent, chat_id: str) -> str:
    chunk: dict = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "choices": [_choice_from_event(event)],
    }
    if event.type == "done" and event.usage is not None:
        chunk["usage"] = event.usage
    return json.dumps(chunk, ensure_ascii=False)


def _choice_from_event(event: ChatEvent) -> dict:
    if event.type == "delta":
        return {
            "delta": {"content": event.content or ""},
            "index": 0,
            "finish_reason": None,
        }
    if event.type == "tool_call" and event.tool_call is not None:
        return {
            "delta": {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": event.tool_call.id,
                        "type": "function",
                        "function": {
                            "name": event.tool_call.name,
                            "arguments": event.tool_call.arguments,
                        },
                    }
                ]
            },
            "index": 0,
            "finish_reason": None,
        }
    if event.type == "done":
        return {"delta": {}, "index": 0, "finish_reason": "stop"}
    if event.type == "error":
        return {
            "delta": {"content": event.error_message or "error"},
            "index": 0,
            "finish_reason": "error",
        }
    return {"delta": {}, "index": 0, "finish_reason": None}
