"""Tests for src/agent/builder.py — the AF Agent factory."""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_framework import Agent, MCPStreamableHTTPTool


def _fake_credential():
    """Minimal credential stand-in — FoundryChatClient accepts AzureTokenProvider."""
    class _Cred:
        def get_token(self, *scopes, **kwargs):
            class _T:
                token = "fake-token"
                expires_on = 99999999999
            return _T()
    return _Cred()


def test_build_agent_wires_foundry_client_mcp_tool_and_prompt(tmp_path: Path):
    """build_agent returns an Agent with MCP tool registered, instructions rendered."""
    (tmp_path / "system.zh.md").write_text(
        "你是 CRM 助手。今天是 {current_date}。", encoding="utf-8"
    )

    from agent.builder import build_agent
    from agent.prompts.loader import PromptLoader

    prompts = PromptLoader(prompts_dir=tmp_path)
    agent = build_agent(
        project_endpoint="https://proj.services.ai.azure.com",
        model="gpt-4o-mini",
        mcp_url="http://localhost:7071/mcp",
        prompts=prompts,
        current_date="2026-04-23",
        credential=_fake_credential(),
    )

    assert isinstance(agent, Agent)

    # Instructions carry the rendered prompt (including the substituted date).
    instructions = agent.default_options["instructions"]
    assert "你是 CRM 助手。" in instructions
    assert "2026-04-23" in instructions

    # The MCP server is registered on the Agent (AF routes it onto the per-
    # invocation tool list). URL matches the configured endpoint.
    assert len(agent.mcp_tools) == 1
    mcp_tool = agent.mcp_tools[0]
    assert isinstance(mcp_tool, MCPStreamableHTTPTool)
    assert mcp_tool.url == "http://localhost:7071/mcp"

    # Destructive Dataverse writes are gated behind user confirmation
    # (Slice 3). `delete_opportunity` is the only one in the current tool set
    # that requires approval; list / get / create / update must not.
    approval = mcp_tool.approval_mode
    assert isinstance(approval, dict)
    assert "delete_opportunity" in approval.get("always_require_approval", [])
    for non_destructive in (
        "list_opportunities",
        "get_opportunity",
        "create_opportunity",
        "update_opportunity",
    ):
        assert non_destructive not in approval.get("always_require_approval", [])


# --- Slice 6: LLM provider dispatch -----------------------------------------


def _prompts(tmp_path: Path):
    (tmp_path / "system.zh.md").write_text(
        "你是 CRM 助手。今天是 {current_date}。", encoding="utf-8"
    )
    from agent.prompts.loader import PromptLoader

    return PromptLoader(prompts_dir=tmp_path)


def test_build_agent_defaults_to_foundry_when_llm_provider_unset(tmp_path: Path):
    from agent.builder import build_agent
    from agent_framework.foundry import FoundryChatClient

    agent = build_agent(
        project_endpoint="https://proj.services.ai.example",
        model="gpt-4o-mini",
        mcp_url="http://localhost:7071/mcp",
        prompts=_prompts(tmp_path),
        credential=_fake_credential(),
        # No llm_provider kwarg — defaults to 'foundry'
    )
    assert isinstance(agent.client, FoundryChatClient)


def test_build_agent_dispatches_to_azure_openai_global(tmp_path: Path):
    from agent.builder import build_agent
    from agent_framework_openai import OpenAIChatClient

    agent = build_agent(
        llm_provider="azure-openai-global",
        project_endpoint="ignored-for-openai",
        model="gpt-4o-mini",
        azure_openai_endpoint="https://myresource.openai.azure.example",
        mcp_url="http://localhost:7071/mcp",
        prompts=_prompts(tmp_path),
        credential=_fake_credential(),
    )
    assert isinstance(agent.client, OpenAIChatClient)


def test_build_agent_dispatches_to_azure_openai_cn(tmp_path: Path):
    from agent.builder import build_agent
    from agent_framework_openai import OpenAIChatClient

    agent = build_agent(
        llm_provider="azure-openai-cn",
        project_endpoint="ignored-for-openai",
        model="gpt-4o-mini",
        azure_openai_endpoint="https://myresource.openai.azure.cn",
        mcp_url="http://localhost:7071/mcp",
        prompts=_prompts(tmp_path),
        credential=_fake_credential(),
    )
    assert isinstance(agent.client, OpenAIChatClient)


def test_build_agent_custom_provider_loads_dotted_path_factory(
    tmp_path: Path, monkeypatch
):
    """CUSTOM_LLM_CLIENT_FACTORY lets a customer inject a provider we never
    shipped support for — the minimum requirement is that the factory returns
    a SupportsChatGetResponse-compatible object."""
    # A test-only module the factory resolves via dotted path.
    import sys
    import types

    mod = types.ModuleType("slice6_custom_fixture")

    class _CannedClient:
        async def get_response(self, messages, *, stream=False, **kwargs):
            raise NotImplementedError  # runtime is not exercised here

    def factory():
        return _CannedClient()

    mod.factory = factory
    sys.modules["slice6_custom_fixture"] = mod
    monkeypatch.setenv(
        "CUSTOM_LLM_CLIENT_FACTORY", "slice6_custom_fixture:factory"
    )

    from agent.builder import build_agent

    agent = build_agent(
        llm_provider="custom",
        project_endpoint="n/a",
        model="n/a",
        mcp_url="http://localhost:7071/mcp",
        prompts=_prompts(tmp_path),
        credential=_fake_credential(),
    )
    assert isinstance(agent.client, _CannedClient)


def test_build_agent_custom_without_env_var_raises(tmp_path: Path, monkeypatch):
    """LLM_PROVIDER=custom without CUSTOM_LLM_CLIENT_FACTORY is a
    misconfiguration the operator should see immediately, not a runtime surprise."""
    monkeypatch.delenv("CUSTOM_LLM_CLIENT_FACTORY", raising=False)

    from agent.builder import build_agent

    with pytest.raises(EnvironmentError) as excinfo:
        build_agent(
            llm_provider="custom",
            project_endpoint="n/a",
            model="n/a",
            mcp_url="http://localhost:7071/mcp",
            prompts=_prompts(tmp_path),
            credential=_fake_credential(),
        )
    assert "CUSTOM_LLM_CLIENT_FACTORY" in str(excinfo.value)


def test_build_agent_unknown_provider_raises_with_full_list(tmp_path: Path):
    from agent.builder import UnsupportedLLMProviderError, build_agent

    with pytest.raises(UnsupportedLLMProviderError) as excinfo:
        build_agent(
            llm_provider="anthropic",
            project_endpoint="n/a",
            model="n/a",
            mcp_url="http://localhost:7071/mcp",
            prompts=_prompts(tmp_path),
            credential=_fake_credential(),
        )
    message = str(excinfo.value)
    assert "anthropic" in message
    for valid in ("foundry", "azure-openai-global", "azure-openai-cn", "custom"):
        assert valid in message


def test_build_agent_forwards_provider_to_prompt_loader(tmp_path: Path):
    """The per-provider override file is appended to instructions when a
    non-foundry provider is selected (Slice 6 + ADR 0006 integration)."""
    (tmp_path / "system.zh.md").write_text("base prompt", encoding="utf-8")
    providers = tmp_path / "providers"
    providers.mkdir()
    (providers / "azure-openai-cn.md").write_text(
        "CN-specific guidance", encoding="utf-8"
    )

    from agent.builder import build_agent
    from agent.prompts.loader import PromptLoader

    agent = build_agent(
        llm_provider="azure-openai-cn",
        project_endpoint="n/a",
        model="gpt-4o-mini",
        azure_openai_endpoint="https://myresource.openai.azure.cn",
        mcp_url="http://localhost:7071/mcp",
        prompts=PromptLoader(prompts_dir=tmp_path),
        credential=_fake_credential(),
    )
    instructions = agent.default_options["instructions"]
    assert "base prompt" in instructions
    assert "CN-specific guidance" in instructions
