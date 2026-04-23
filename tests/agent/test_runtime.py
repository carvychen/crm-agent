"""Tests for src/agent/runtime/runtime.py — LLM + tool-call orchestration."""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

from agent.llm_client.base import ChatEvent, LLMClient, Message, ToolCall


def _write_prompts(tmp_path: Path, system: str = "你是 CRM 助手。") -> Path:
    """Create a minimal prompts directory under tmp_path and return it."""
    (tmp_path / "system.zh.md").write_text(system, encoding="utf-8")
    return tmp_path


class _ScriptedLLM(LLMClient):
    """LLM stub that replays a canned sequence of ChatEvents per call."""

    def __init__(self, *turns: list[ChatEvent]) -> None:
        self._turns = list(turns)
        self.calls: list[dict] = []

    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        self.calls.append({"messages": list(messages), "tools": tools})
        events = self._turns.pop(0) if self._turns else []
        for event in events:
            yield event


async def test_chat_yields_assistant_deltas_then_done_when_no_tool_calls(tmp_path: Path):
    """A single-turn message with no tool calls streams deltas then signals done."""
    from agent.prompts.loader import PromptLoader
    from agent.runtime.runtime import AgentRuntime

    prompts = PromptLoader(prompts_dir=_write_prompts(tmp_path))
    llm = _ScriptedLLM(
        [
            ChatEvent(type="delta", content="你好，"),
            ChatEvent(type="delta", content="我是 CRM 助手。"),
            ChatEvent(type="done", usage={"prompt_tokens": 10, "completion_tokens": 5}),
        ]
    )

    # No tool calls in this scenario, so MCP is never invoked.
    runtime = AgentRuntime(llm=llm, mcp=_FakeMCP(results={}), prompts=prompts)

    events = []
    async for event in runtime.chat(
        messages=[Message(role="user", content="你好")],
        user_jwt="user-jwt",
    ):
        events.append(event)

    # Assistant deltas flow through unchanged, followed by a terminal done event.
    assert [e.type for e in events] == ["delta", "delta", "done"]
    assert events[0].content == "你好，"
    assert events[1].content == "我是 CRM 助手。"
    assert events[2].usage == {"prompt_tokens": 10, "completion_tokens": 5}

    # The runtime injected the system prompt as the first message.
    assert len(llm.calls) == 1
    sent = llm.calls[0]["messages"]
    assert sent[0].role == "system"
    assert "CRM 助手" in sent[0].content
    assert sent[-1].role == "user"
    assert sent[-1].content == "你好"


class _FakeMCP:
    """In-memory MCP client double: replays canned tool results."""

    def __init__(self, results: dict[str, str]) -> None:
        self._results = results
        self.calls: list[dict] = []

    async def call_tool(self, name: str, arguments: dict, *, user_jwt: str) -> str:
        self.calls.append({"name": name, "arguments": arguments, "user_jwt": user_jwt})
        if name not in self._results:
            raise KeyError(f"unexpected tool call: {name}")
        return self._results[name]


async def test_chat_runs_tool_call_loop_against_mcp(tmp_path: Path):
    """LLM requests a tool → runtime calls MCP → result is fed back to the LLM."""
    from agent.prompts.loader import PromptLoader
    from agent.runtime.runtime import AgentRuntime

    prompts = PromptLoader(prompts_dir=_write_prompts(tmp_path))

    # Turn 1: LLM asks for a tool call.
    turn1 = [
        ChatEvent(
            type="tool_call",
            tool_call=ToolCall(
                id="call_1",
                name="list_opportunities",
                arguments='{"top": 5}',
            ),
        ),
        ChatEvent(type="done", usage={"prompt_tokens": 10, "completion_tokens": 2}),
    ]
    # Turn 2: after the tool result is available, LLM summarises.
    turn2 = [
        ChatEvent(type="delta", content="你有 "),
        ChatEvent(type="delta", content="3 条商机。"),
        ChatEvent(type="done", usage={"prompt_tokens": 50, "completion_tokens": 8}),
    ]
    llm = _ScriptedLLM(turn1, turn2)

    mcp = _FakeMCP(
        results={
            "list_opportunities": '[{"id":"opp-1","topic":"Deal A"},'
            '{"id":"opp-2","topic":"Deal B"},'
            '{"id":"opp-3","topic":"Deal C"}]'
        }
    )

    runtime = AgentRuntime(llm=llm, mcp=mcp, prompts=prompts)

    events = []
    async for event in runtime.chat(
        messages=[Message(role="user", content="列出我的商机")],
        user_jwt="user-jwt-alice",
    ):
        events.append(event)

    # The caller sees the tool_call event, then the assistant's summary deltas.
    assert [e.type for e in events] == ["tool_call", "delta", "delta", "done"]
    assert events[0].tool_call.name == "list_opportunities"
    assert events[1].content == "你有 "
    assert events[2].content == "3 条商机。"

    # The user's JWT was forwarded to MCP so OBO / RLS apply.
    assert len(mcp.calls) == 1
    assert mcp.calls[0]["name"] == "list_opportunities"
    assert mcp.calls[0]["arguments"] == {"top": 5}
    assert mcp.calls[0]["user_jwt"] == "user-jwt-alice"

    # Second LLM call includes the assistant tool-call message and the tool result.
    assert len(llm.calls) == 2
    second_call_messages = llm.calls[1]["messages"]
    roles = [m.role for m in second_call_messages]
    assert "assistant" in roles, "LLM must see its own tool_call turn"
    assert roles[-1] == "tool"
    assert "Deal A" in second_call_messages[-1].content
    assert second_call_messages[-1].tool_call_id == "call_1"
