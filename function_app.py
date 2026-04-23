"""Azure Functions entry point (Python v2 programming model).

Bootstraps the MCP-server ASGI app with real dependencies and, unless
`ENABLE_REFERENCE_AGENT=false`, also mounts the reference agent at /api/chat.
Authentication (Azure Easy Auth) is configured at the Function App level by
the Bicep in Slice 9 (#11); this layer just forwards the inbound
`Authorization: Bearer <user-jwt>` header to OBO and to the agent route.

Per ADR 0002 the MCP SDK is self-hosted on an HTTP trigger, not the preview
Functions MCP extension; per ADR 0004 the reference agent talks to the same
HTTP endpoint any external MCP client would, even when co-located.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import AsyncIterator

# Azure Functions runs this file from the repo root. Expose `src/` on sys.path
# so module imports match the layout used in tests.
_SRC = Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import azure.functions as func  # noqa: E402
import httpx  # noqa: E402
from azure.identity import DefaultAzureCredential  # noqa: E402

from agent.llm_client.base import LLMClient  # noqa: E402
from agent.llm_client.foundry import FoundryLLMClient  # noqa: E402
from agent.mcp_client import StreamableHttpMCPClient  # noqa: E402
from agent.prompts.loader import PromptLoader  # noqa: E402
from agent.runtime.runtime import AgentRuntime  # noqa: E402
from asgi import create_asgi_app  # noqa: E402
from auth import DataverseAuth  # noqa: E402
from config import get_config  # noqa: E402
from dataverse_client import OpportunityClient  # noqa: E402
from mcp_server import ServerDeps  # noqa: E402


_FOUNDRY_SCOPE = "https://cognitiveservices.azure.com/.default"
_SUPPORTED_PROVIDERS = ("foundry",)


def _build_mcp_server_deps() -> ServerDeps:
    config = get_config()
    http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    credential = DefaultAzureCredential()
    fic_scope = f"{config.fic_audience}/.default"

    def _mi_token() -> str:
        return credential.get_token(fic_scope).token

    return ServerDeps(
        auth=DataverseAuth(config, http=http, mi_token_provider=_mi_token),
        client=OpportunityClient(config.dataverse_url, http=http),
    )


def _build_llm_client() -> LLMClient:
    provider = os.environ.get("LLM_PROVIDER", "foundry").strip().lower()
    if provider != "foundry":
        raise EnvironmentError(
            f"LLM_PROVIDER={provider!r} not supported in this build. "
            f"Expected one of: {_SUPPORTED_PROVIDERS}. "
            "Additional providers (azure-openai-global, azure-openai-cn, custom) "
            "land in Slice 6 (#8)."
        )

    endpoint = _require_env("FOUNDRY_PROJECT_ENDPOINT")
    deployment = os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini")
    credential = DefaultAzureCredential()

    return FoundryLLMClient(
        endpoint=endpoint,
        deployment=deployment,
        token_provider=lambda: credential.get_token(_FOUNDRY_SCOPE).token,
        http=httpx.AsyncClient(timeout=httpx.Timeout(60.0)),
    )


def _build_agent_runtime() -> AgentRuntime:
    prompts_dir = Path(__file__).parent / "src" / "agent" / "prompts"
    mcp_url = os.environ.get("MCP_SERVER_URL")
    if not mcp_url:
        raise EnvironmentError(
            "MCP_SERVER_URL is required when ENABLE_REFERENCE_AGENT=true; "
            "set it to the Function App's own /mcp endpoint."
        )

    @contextlib.asynccontextmanager
    async def http_factory(user_jwt: str) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"Authorization": f"Bearer {user_jwt}"},
        ) as client:
            yield client

    return AgentRuntime(
        llm=_build_llm_client(),
        mcp=StreamableHttpMCPClient(
            mcp_url=mcp_url,
            http_client_factory=http_factory,
        ),
        prompts=PromptLoader(prompts_dir=prompts_dir),
    )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def _agent_enabled() -> bool:
    return os.environ.get("ENABLE_REFERENCE_AGENT", "true").strip().lower() != "false"


_agent = _build_agent_runtime() if _agent_enabled() else None
_asgi_app = create_asgi_app(_build_mcp_server_deps(), agent=_agent)

app = func.AsgiFunctionApp(
    app=_asgi_app,
    http_auth_level=func.AuthLevel.ANONYMOUS,
)
