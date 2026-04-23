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
    assert isinstance(agent.mcp_tools[0], MCPStreamableHTTPTool)
    assert agent.mcp_tools[0].url == "http://localhost:7071/mcp"
