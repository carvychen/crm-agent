"""Tests for src/agent/llm_client/foundry.py — Azure AI Foundry provider."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from agent.llm_client.base import Message


FOUNDRY_ENDPOINT = "https://proj.services.ai.azure.com"
DEPLOYMENT = "gpt-4o-mini"
COMPLETIONS_URL = f"{FOUNDRY_ENDPOINT}/openai/deployments/{DEPLOYMENT}/chat/completions"


def _sse(lines: list[str]) -> bytes:
    """Encode a list of SSE `data: ...` lines with proper framing."""
    return "".join(f"data: {line}\n\n" for line in lines).encode()


async def test_foundry_client_posts_openai_compatible_body_and_parses_sse_deltas():
    """The Foundry provider serialises messages, posts with Bearer auth, and
    translates each SSE chunk into a ChatEvent."""
    from agent.llm_client.foundry import FoundryLLMClient

    stream_body = _sse(
        [
            json.dumps(
                {
                    "choices": [
                        {"delta": {"content": "你好，"}, "index": 0, "finish_reason": None}
                    ]
                }
            ),
            json.dumps(
                {
                    "choices": [
                        {
                            "delta": {"content": "我是 CRM 助手。"},
                            "index": 0,
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "choices": [
                        {"delta": {}, "index": 0, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 5,
                        "total_tokens": 17,
                    },
                }
            ),
            "[DONE]",
        ]
    )

    with respx.mock() as router:
        route = router.post(COMPLETIONS_URL).mock(
            return_value=httpx.Response(
                200,
                content=stream_body,
                headers={"Content-Type": "text/event-stream"},
            )
        )

        async with httpx.AsyncClient() as http:
            client = FoundryLLMClient(
                endpoint=FOUNDRY_ENDPOINT,
                deployment=DEPLOYMENT,
                token_provider=lambda: "foundry-token",
                http=http,
            )

            events = []
            async for event in client.chat_completion(
                messages=[
                    Message(role="system", content="你是 CRM 助手。"),
                    Message(role="user", content="你好"),
                ],
                tools=[{"type": "function", "function": {"name": "list_opportunities"}}],
            ):
                events.append(event)

    # Request shape: OpenAI-compatible chat completions body + Bearer token.
    assert route.called
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer foundry-token"
    body = json.loads(req.content)
    assert body["model"] == DEPLOYMENT
    assert body["stream"] is True
    assert body["messages"] == [
        {"role": "system", "content": "你是 CRM 助手。"},
        {"role": "user", "content": "你好"},
    ]
    assert body["tools"] == [{"type": "function", "function": {"name": "list_opportunities"}}]

    # Response shape: deltas + done with usage.
    assert [e.type for e in events] == ["delta", "delta", "done"]
    assert events[0].content == "你好，"
    assert events[1].content == "我是 CRM 助手。"
    assert events[2].usage == {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
    }
