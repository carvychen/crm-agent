"""Live-integration: real Foundry chat completion via AF's FoundryChatClient.

Minimal call (single-turn, 10-token ceiling) to prove:
- Foundry endpoint is reachable
- AzureCliCredential / DefaultAzureCredential chain resolves
- The deployed model name accepts a simple prompt and returns text

Run cost is ≤ 1 inference call.
"""
from __future__ import annotations

import os

import pytest
from agent_framework import Agent, Message, Content
from agent_framework.foundry import FoundryChatClient


def _foundry_credential():
    """Foundry lives in a different tenant from the Dataverse app.

    Local dev: `az login` into Foundry's tenant; AzureCliCredential ignores
    AZURE_TENANT_ID env var and uses the CLI session.
    CI: provide a service principal in Foundry's tenant via
    FOUNDRY_AZURE_{TENANT_ID,CLIENT_ID,CLIENT_SECRET} secrets.
    """
    cid = os.environ.get("FOUNDRY_AZURE_CLIENT_ID")
    csecret = os.environ.get("FOUNDRY_AZURE_CLIENT_SECRET")
    ctenant = os.environ.get("FOUNDRY_AZURE_TENANT_ID")
    if cid and csecret and ctenant:
        from azure.identity import ClientSecretCredential

        return ClientSecretCredential(
            tenant_id=ctenant, client_id=cid, client_secret=csecret
        )
    from azure.identity import AzureCliCredential

    return AzureCliCredential()


async def test_foundry_chat_completes_a_simple_prompt():
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    model = os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini")
    if not endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT not configured in this env")

    client = FoundryChatClient(
        project_endpoint=endpoint,
        model=model,
        credential=_foundry_credential(),
    )
    agent = Agent(
        client=client,
        instructions="You are a test probe. Reply with exactly the word OK.",
    )

    user = Message(role="user", contents=[Content.from_text(text="Ping")])
    response = await agent.run([user])

    text = response.text if hasattr(response, "text") else str(response)
    assert text, "Foundry returned an empty response"
    # We don't hard-assert content equality — model chatter varies — but a
    # one-word reply to "Ping" is reliably short.
    assert len(text) < 200, f"unexpectedly long reply: {text[:200]}..."
