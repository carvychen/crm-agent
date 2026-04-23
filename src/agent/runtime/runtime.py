"""AgentRuntime — LLM + MCP tool-calling orchestration loop.

Accepts a user turn, runs the LLM, routes tool calls to the MCP server over
HTTP (ADR 0004 — behind the `MCPClient` abstraction), and yields streaming
ChatEvents back to the caller. The incoming user JWT is forwarded verbatim
to the MCP client so OBO runs under the real end-user identity.
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Protocol

from agent.llm_client.base import ChatEvent, LLMClient, Message, ToolCall
from agent.prompts.loader import PromptLoader


class MCPClient(Protocol):
    """Minimal contract the runtime needs from an MCP transport."""

    async def call_tool(
        self, name: str, arguments: dict, *, user_jwt: str
    ) -> str: ...


class AgentRuntime:
    def __init__(
        self,
        *,
        llm: LLMClient,
        mcp: MCPClient,
        prompts: PromptLoader,
    ) -> None:
        self._llm = llm
        self._mcp = mcp
        self._prompts = prompts

    async def chat(
        self,
        *,
        messages: list[Message],
        user_jwt: str,
    ) -> AsyncIterator[ChatEvent]:
        history: list[Message] = [
            Message(role="system", content=self._prompts.render()),
            *messages,
        ]

        while True:
            tool_calls: list[ToolCall] = []
            last_done: ChatEvent | None = None

            async for event in self._llm.chat_completion(messages=history, tools=None):
                if event.type == "tool_call" and event.tool_call is not None:
                    tool_calls.append(event.tool_call)
                    yield event
                elif event.type == "done":
                    last_done = event  # defer: only the final done reaches the caller
                else:
                    yield event

            if not tool_calls:
                if last_done is not None:
                    yield last_done
                return

            # Record the assistant's tool-call turn, then execute each call and
            # append its result so the next LLM round can summarise.
            history.append(
                Message(role="assistant", content=None, tool_calls=tuple(tool_calls))
            )
            for tc in tool_calls:
                try:
                    arguments = json.loads(tc.arguments) if tc.arguments else {}
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"LLM produced invalid JSON arguments for {tc.name}: {exc}"
                    ) from exc
                result = await self._mcp.call_tool(
                    tc.name, arguments, user_jwt=user_jwt
                )
                history.append(
                    Message(role="tool", content=result, tool_call_id=tc.id)
                )
