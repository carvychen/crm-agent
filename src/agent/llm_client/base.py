"""LLMClient abstract interface + the narrow message/event shape every provider speaks.

Per ADR 0005 the abstraction covers only chat completion with tool calling.
Provider-specific quirks (token limit handling, tool-call JSON dialects, rate-limit
headers) stay inside each concrete subclass and MUST NOT leak into runtime code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

Role = Literal["system", "user", "assistant", "tool"]
EventType = Literal["delta", "tool_call", "done", "error"]


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation the LLM requested."""

    id: str
    name: str
    arguments: str  # JSON-encoded arguments


@dataclass(frozen=True)
class Message:
    """A turn in the conversation passed to the LLM."""

    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    tool_call_id: str | None = None  # populated when role == "tool"


@dataclass(frozen=True)
class ChatEvent:
    """A single event in the streaming chat response."""

    type: EventType
    content: str | None = None           # assistant text delta
    tool_call: ToolCall | None = None    # assembled tool call (after streaming completes)
    usage: dict | None = None            # token usage, populated with `done`
    error_message: str | None = None     # populated with `error`


class LLMClient(ABC):
    """Minimal chat+tool-call interface; providers hide their own quirks."""

    @abstractmethod
    def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """Yield ChatEvents as they arrive from the underlying provider."""
        raise NotImplementedError  # pragma: no cover — abstract
