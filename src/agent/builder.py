"""Reference agent builder — composes an `agent_framework.Agent` from its parts.

ADR 0005 (amended) selects Microsoft Agent Framework as the orchestration
runtime. The abstraction for "swap the LLM" is AF's own
`SupportsChatGetResponse`; this module dispatches on `LLM_PROVIDER` to the
matching concrete chat client, leaving the runtime / tool-loop / approval
code identical across providers.

Supported providers (Slice 6):
- `foundry`              — `agent_framework.foundry.FoundryChatClient` (default)
- `azure-openai-global`  — `agent_framework_openai.OpenAIChatClient` against Azure OpenAI on Global
- `azure-openai-cn`      — same class against Azure OpenAI on 21Vianet
- `custom`               — dotted-path factory in `CUSTOM_LLM_CLIENT_FACTORY`

Invariant 1 stays intact: the MCP server is consumed through AF's own
`MCPStreamableHTTPTool`, which speaks the standard protocol regardless of
which chat client is picked.
"""
from __future__ import annotations

import importlib
import os
from contextvars import ContextVar
from datetime import date
from typing import Any

import httpx
from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient

from agent.prompts.loader import PromptLoader


# Set per-request by `/api/chat` before invoking the Agent. Read at tool-call
# time by the request-event hook on the MCP tool's httpx client so the MCP
# server sees the inbound user JWT and OBO preserves end-user identity.
current_user_jwt: ContextVar[str] = ContextVar("current_user_jwt")


_SUPPORTED_PROVIDERS = (
    "foundry",
    "azure-openai-global",
    "azure-openai-cn",
    "custom",
)


class UnsupportedLLMProviderError(ValueError):
    """Raised when `llm_provider` is set to a value this build does not support."""


def bearer_header_provider(_kwargs: dict[str, Any]) -> dict[str, str]:
    """Return the Authorization header for the current in-flight request."""
    return {"Authorization": f"Bearer {current_user_jwt.get()}"}


async def _bearer_request_hook(request: httpx.Request) -> None:
    """httpx request-event hook that attaches Authorization on every outbound
    MCP request. We cannot rely on AF's header_provider alone because it only
    fires inside call_tool — the MCP `initialize` handshake goes out before
    that and is rejected with 401 without this hook."""
    try:
        request.headers["Authorization"] = f"Bearer {current_user_jwt.get()}"
    except LookupError:
        return


def build_agent(
    *,
    project_endpoint: str,
    model: str,
    mcp_url: str,
    prompts: PromptLoader,
    credential: Any,
    llm_provider: str = "foundry",
    azure_openai_endpoint: str | None = None,
    azure_openai_api_version: str = "2024-10-21",
    current_date: str | None = None,
    mcp_http_client: httpx.AsyncClient | None = None,
) -> Agent:
    """Compose a ready-to-run reference agent for the configured LLM provider.

    The Agent owns its chat client + its MCP tool connection; it is intended
    to live for the Function App host's lifetime.
    """
    client = _build_chat_client(
        llm_provider=llm_provider,
        project_endpoint=project_endpoint,
        model=model,
        azure_openai_endpoint=azure_openai_endpoint,
        azure_openai_api_version=azure_openai_api_version,
        credential=credential,
    )

    if mcp_http_client is None:
        mcp_http_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=300.0),
        )
    mcp_http_client.event_hooks.setdefault("request", []).append(_bearer_request_hook)

    mcp_tool = MCPStreamableHTTPTool(
        name="crm-mcp",
        url=mcp_url,
        http_client=mcp_http_client,
        load_prompts=False,
        approval_mode={"always_require_approval": ["delete_opportunity"]},
    )

    rendered_date = current_date or date.today().isoformat()
    instructions = prompts.render(
        current_date=rendered_date,
        provider=llm_provider,
    )

    return Agent(
        client=client,
        instructions=instructions,
        tools=[mcp_tool],
    )


def _build_chat_client(
    *,
    llm_provider: str,
    project_endpoint: str,
    model: str,
    azure_openai_endpoint: str | None,
    azure_openai_api_version: str,
    credential: Any,
) -> Any:
    if llm_provider == "foundry":
        return FoundryChatClient(
            project_endpoint=project_endpoint,
            model=model,
            credential=credential,
        )
    if llm_provider in ("azure-openai-global", "azure-openai-cn"):
        # Lazy import — keeps agent_framework_openai out of the startup path
        # when the operator isn't using Azure OpenAI.
        from agent_framework_openai import OpenAIChatClient

        endpoint = azure_openai_endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            raise EnvironmentError(
                f"LLM_PROVIDER={llm_provider!r} requires azure_openai_endpoint "
                "parameter or AZURE_OPENAI_ENDPOINT environment variable "
                "(e.g. https://<resource>.openai.azure.com for Global, "
                "https://<resource>.openai.azure.cn for China)."
            )
        return OpenAIChatClient(
            azure_endpoint=endpoint,
            model=model,
            api_version=azure_openai_api_version,
            credential=credential,
        )
    if llm_provider == "custom":
        dotted = os.environ.get("CUSTOM_LLM_CLIENT_FACTORY")
        if not dotted:
            raise EnvironmentError(
                "LLM_PROVIDER=custom requires CUSTOM_LLM_CLIENT_FACTORY env "
                "variable set to 'module.path:callable'. The callable must "
                "take no arguments and return an object implementing AF's "
                "SupportsChatGetResponse protocol."
            )
        module_path, _, attr_name = dotted.partition(":")
        if not attr_name:
            raise EnvironmentError(
                f"CUSTOM_LLM_CLIENT_FACTORY={dotted!r} is malformed — "
                "expected 'module.path:callable_name'."
            )
        module = importlib.import_module(module_path)
        factory = getattr(module, attr_name)
        return factory()
    raise UnsupportedLLMProviderError(
        f"llm_provider={llm_provider!r} is not supported. "
        f"Expected one of: {_SUPPORTED_PROVIDERS}."
    )
