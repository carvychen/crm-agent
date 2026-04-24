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
from auth import build_auth  # noqa: E402
from config import get_config  # noqa: E402
from dataverse_client import OpportunityClient  # noqa: E402
from flex_asgi import FlexAsgiFunctionApp  # noqa: E402
from mcp_server import ServerDeps  # noqa: E402


_PRODUCTION_ENV_MARKER = "AZURE_FUNCTIONS_ENVIRONMENT"


def _assert_prod_uses_obo() -> None:
    """Refuse to boot a production Function App under AUTH_MODE=app_only_secret.

    ADR 0007 permits the client-secret path for dev / CI integration only.
    Production must use OBO+WIF (ADR 0001). The Azure Functions runtime sets
    AZURE_FUNCTIONS_ENVIRONMENT=Production by default in deployed slots.
    """
    env = os.environ.get(_PRODUCTION_ENV_MARKER, "").strip().lower()
    mode = os.environ.get("AUTH_MODE", "obo").strip().lower()
    if env == "production" and mode == "app_only_secret":
        raise RuntimeError(
            "AUTH_MODE=app_only_secret is forbidden in production "
            f"({_PRODUCTION_ENV_MARKER}=Production). Set AUTH_MODE=obo and "
            "configure WIF + Managed Identity per ADR 0001."
        )


def _runtime_credential() -> DefaultAzureCredential:
    """Build a `DefaultAzureCredential` that picks the right Managed Identity.

    On a Function App with only a User-Assigned Managed Identity (no system MI),
    DefaultAzureCredential with no args falls back to the absent system identity
    and fails with *"ManagedIdentityCredential: ... Unable to load the proper
    Managed Identity"*. Passing the UAMI's client ID explicitly tells the SDK
    which identity to target. `MANAGED_IDENTITY_CLIENT_ID` is published by
    `infra/main.bicep` from the MI module's `clientId` output, so this stays in
    lock-step with whatever MI the Bicep deploys.

    Returns a plain `DefaultAzureCredential()` when the env var is absent — that
    keeps local dev (az login, no MI) and pytest with DefaultAzureCredential
    mocks working unchanged.
    """
    mi_client_id = os.environ.get("MANAGED_IDENTITY_CLIENT_ID")
    if mi_client_id:
        return DefaultAzureCredential(managed_identity_client_id=mi_client_id)
    return DefaultAzureCredential()


def _build_mcp_server_deps() -> ServerDeps:
    config = get_config()
    http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    credential = _runtime_credential()
    fic_scope = f"{config.fic_audience}/.default"

    def _mi_token() -> str:
        return credential.get_token(fic_scope).token

    return ServerDeps(
        auth=build_auth(config, http=http, mi_token_provider=_mi_token),
        client=OpportunityClient(config.dataverse_url, http=http),
    )


def _build_reference_agent():
    """Compose the AF-based reference agent. Delayed-import because the
    heavy agent_framework deps should only load when enabled."""
    from agent.builder import build_agent
    from agent.prompts.loader import PromptLoader

    prompts_dir = Path(__file__).parent / "src" / "agent" / "prompts"
    llm_provider = os.environ.get("LLM_PROVIDER", "foundry").strip().lower()
    return build_agent(
        llm_provider=llm_provider,
        project_endpoint=os.environ.get("FOUNDRY_PROJECT_ENDPOINT", ""),
        model=os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini"),
        azure_openai_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        azure_openai_api_version=os.environ.get(
            "AZURE_OPENAI_API_VERSION", "2024-10-21"
        ),
        mcp_url=_require_env("MCP_SERVER_URL"),
        prompts=PromptLoader(prompts_dir=prompts_dir),
        credential=_runtime_credential(),
    )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def _agent_enabled() -> bool:
    return os.environ.get("ENABLE_REFERENCE_AGENT", "true").strip().lower() != "false"


_assert_prod_uses_obo()
_agent = _build_reference_agent() if _agent_enabled() else None
_asgi_app = create_asgi_app(_build_mcp_server_deps(), agent=_agent)

# FlexAsgiFunctionApp — not func.AsgiFunctionApp — works around a leading-slash
# bug in the SDK's registered route template. See src/flex_asgi.py.
app = FlexAsgiFunctionApp(
    app=_asgi_app,
    http_auth_level=func.AuthLevel.ANONYMOUS,
)
