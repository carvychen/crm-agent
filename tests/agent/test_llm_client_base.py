"""Tests for src/agent/llm_client/base.py — LLMClient ABC contract."""
from __future__ import annotations

from typing import AsyncIterator

import pytest


def test_llm_client_cannot_be_instantiated_without_chat_completion():
    """LLMClient is abstract; subclasses must implement chat_completion."""
    from agent.llm_client.base import ChatEvent, LLMClient, Message

    class MissingOverride(LLMClient):
        pass

    with pytest.raises(TypeError):
        MissingOverride()  # missing abstract method

    class Concrete(LLMClient):
        async def chat_completion(
            self,
            messages: list[Message],
            tools: list[dict] | None = None,
        ) -> AsyncIterator[ChatEvent]:
            yield ChatEvent(type="delta", content="hello")

    concrete = Concrete()  # OK once implemented
    assert isinstance(concrete, LLMClient)
