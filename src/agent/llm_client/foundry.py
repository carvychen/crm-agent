"""FoundryLLMClient — Azure AI Foundry provider for the LLMClient ABC.

Talks to Foundry's OpenAI-compatible chat completions endpoint using streaming
SSE. Per ADR 0005, provider-specific quirks (tool-call JSON dialects, usage
header shapes) stay inside this module — the runtime above only sees
normalised `ChatEvent` values.
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Callable

import httpx
from httpx_sse import aconnect_sse

from agent.llm_client.base import ChatEvent, LLMClient, Message, ToolCall


TokenProvider = Callable[[], str]


class FoundryLLMClient(LLMClient):
    def __init__(
        self,
        *,
        endpoint: str,
        deployment: str,
        token_provider: TokenProvider,
        http: httpx.AsyncClient,
        api_version: str = "2024-10-21",
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._deployment = deployment
        self._token_provider = token_provider
        self._http = http
        self._api_version = api_version

    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        body: dict = {
            "model": self._deployment,
            "messages": [_message_to_openai(m) for m in messages],
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        url = f"{self._endpoint}/openai/deployments/{self._deployment}/chat/completions"

        async with aconnect_sse(
            self._http,
            "POST",
            url,
            params={"api-version": self._api_version},
            headers={
                "Authorization": f"Bearer {self._token_provider()}",
                "Content-Type": "application/json",
            },
            json=body,
        ) as event_source:
            # Tool-call JSON dialects are assembled across multiple chunks:
            # OpenAI streams `{id, function.name, function.arguments}` piecewise.
            tool_call_buffers: dict[int, dict] = {}
            last_usage: dict | None = None

            async for sse in event_source.aiter_sse():
                if sse.data == "[DONE]":
                    break
                chunk = json.loads(sse.data)
                if (usage := chunk.get("usage")) is not None:
                    last_usage = usage
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {}) or {}
                    if (content := delta.get("content")) is not None:
                        yield ChatEvent(type="delta", content=content)
                    for tc in delta.get("tool_calls", []) or []:
                        _merge_tool_call(tool_call_buffers, tc)
                    if choice.get("finish_reason") == "tool_calls":
                        for buf in tool_call_buffers.values():
                            yield ChatEvent(
                                type="tool_call",
                                tool_call=ToolCall(
                                    id=buf.get("id", ""),
                                    name=buf.get("name", ""),
                                    arguments=buf.get("arguments", ""),
                                ),
                            )
                        tool_call_buffers = {}

            yield ChatEvent(type="done", usage=last_usage)


def _message_to_openai(message: Message) -> dict:
    """Serialise a Message into the OpenAI chat completions wire format."""
    body: dict = {"role": message.role}
    if message.content is not None:
        body["content"] = message.content
    if message.tool_calls:
        body["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in message.tool_calls
        ]
    if message.tool_call_id is not None:
        body["tool_call_id"] = message.tool_call_id
    return body


def _merge_tool_call(buffers: dict[int, dict], delta: dict) -> None:
    idx = delta.get("index", 0)
    buf = buffers.setdefault(idx, {})
    if (tc_id := delta.get("id")) is not None:
        buf["id"] = tc_id
    function = delta.get("function") or {}
    if (name := function.get("name")) is not None:
        buf["name"] = buf.get("name", "") + name
    if (args := function.get("arguments")) is not None:
        buf["arguments"] = buf.get("arguments", "") + args
