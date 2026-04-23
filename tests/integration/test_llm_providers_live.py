"""Live-integration: LLM provider dispatch end-to-end.

`foundry` is covered by the existing `test_agent_live.py`. This file focuses
on the dispatcher paths Slice 6 introduces. Each provider's coverage matches
what a customer can realistically verify:

- `custom` — we install a test fixture that returns a canned
  SupportsChatGetResponse; exercises dotted-path import + AF acceptance of
  non-AF chat clients. Runs on every PR (no Azure dep).
- `azure-openai-global` / `azure-openai-cn` — only exercised if
  AZURE_OPENAI_ENDPOINT is configured; otherwise skipped with a clear
  reason. Most customer installs have Foundry OR Azure OpenAI, not both,
  so this gated path is realistic.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import httpx
import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_custom_provider_live_dispatch(monkeypatch):
    """End-to-end test of the custom dotted-path provider: install a fake
    factory in sys.modules, build the agent, confirm it uses our instance."""
    # Build a minimal SupportsChatGetResponse implementation for the test.
    module = types.ModuleType("slice6_live_fixture")

    class _ProbeClient:
        probe_ran = False

        async def get_response(self, messages, *, stream=False, **kwargs):
            _ProbeClient.probe_ran = True
            raise NotImplementedError  # we're not exercising the runtime loop here

    def factory() -> _ProbeClient:
        return _ProbeClient()

    module.factory = factory
    module.ProbeClient = _ProbeClient  # expose for assertion
    sys.modules["slice6_live_fixture"] = module

    monkeypatch.setenv("CUSTOM_LLM_CLIENT_FACTORY", "slice6_live_fixture:factory")

    from agent.builder import build_agent
    from agent.prompts.loader import PromptLoader

    prompts_dir = _REPO_ROOT / "src" / "agent" / "prompts"
    agent = build_agent(
        llm_provider="custom",
        project_endpoint="n/a",
        model="n/a",
        mcp_url="http://localhost:7071/mcp",
        prompts=PromptLoader(prompts_dir=prompts_dir),
        credential=None,
    )
    assert isinstance(agent.client, _ProbeClient)


async def test_azure_openai_global_live_chat_completion():
    """Real call against Azure OpenAI on Global. Skipped when
    AZURE_OPENAI_ENDPOINT is not configured — the author's dev tenant
    primarily uses Foundry, so this is the realistic gate."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    model = os.environ.get("AZURE_OPENAI_MODEL")
    if not endpoint or not model:
        pytest.skip(
            "AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_MODEL not configured — "
            "skipping live Azure-OpenAI-Global probe. Set both in .env to "
            "exercise this path."
        )

    from agent_framework import Agent, Content, Message
    from agent_framework_openai import OpenAIChatClient
    from azure.identity import DefaultAzureCredential

    client = OpenAIChatClient(
        azure_endpoint=endpoint,
        model=model,
        credential=DefaultAzureCredential(),
    )
    agent = Agent(client=client, instructions="Reply with OK.")
    user = Message(role="user", contents=[Content.from_text(text="Ping")])
    response = await agent.run([user])
    text = getattr(response, "text", "") or ""
    assert text, "Azure OpenAI returned an empty response"
