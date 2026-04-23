"""Azure Functions entry point (Python v2 programming model).

Bootstraps the MCP-server ASGI app and, unless `ENABLE_REFERENCE_AGENT=false`,
also mounts the reference agent (Microsoft Agent Framework) at /api/chat.
Authentication (Azure Easy Auth) is configured at the Function App level by
the Bicep in Slice 9 (#11); this layer just forwards the inbound
`Authorization: Bearer <user-jwt>` header to OBO and into the agent.

Per ADR 0002 the MCP SDK is self-hosted on an HTTP trigger, not the preview
Functions MCP extension. Per ADR 0004 the reference agent talks to the MCP
server over HTTP even when co-located (via AF's `MCPStreamableHTTPTool`).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Azure Functions runs this file from the repo root. Expose `src/` on sys.path
# so module imports match the layout used in tests.
_SRC = Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import azure.functions as func  # noqa: E402
import httpx  # noqa: E402
from azure.identity import DefaultAzureCredential  # noqa: E402

from asgi import create_asgi_app  # noqa: E402
from auth import DataverseAuth  # noqa: E402
from config import get_config  # noqa: E402
from dataverse_client import OpportunityClient  # noqa: E402
from mcp_server import ServerDeps  # noqa: E402


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


def _build_reference_agent():
    """Compose the AF-based reference agent. Delayed-import because the
    heavy agent_framework deps should only load when enabled."""
    from agent.builder import build_agent
    from agent.prompts.loader import PromptLoader

    prompts_dir = Path(__file__).parent / "src" / "agent" / "prompts"
    return build_agent(
        project_endpoint=_require_env("FOUNDRY_PROJECT_ENDPOINT"),
        model=os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini"),
        mcp_url=_require_env("MCP_SERVER_URL"),
        prompts=PromptLoader(prompts_dir=prompts_dir),
        credential=DefaultAzureCredential(),
    )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def _agent_enabled() -> bool:
    return os.environ.get("ENABLE_REFERENCE_AGENT", "true").strip().lower() != "false"


_agent = _build_reference_agent() if _agent_enabled() else None
_asgi_app = create_asgi_app(_build_mcp_server_deps(), agent=_agent)

app = func.AsgiFunctionApp(
    app=_asgi_app,
    http_auth_level=func.AuthLevel.ANONYMOUS,
)
