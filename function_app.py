"""Azure Functions entry point (Python v2 programming model).

Bootstraps the MCP-server ASGI app with real dependencies and exposes it under
`/mcp/*`. Authentication (Azure Easy Auth) is configured at the Function App
level by the Bicep in Slice 9 (#11); this layer just forwards the inbound
`Authorization: Bearer <user-jwt>` header to the OBO exchange.

Per ADR 0002 the MCP SDK is self-hosted on an HTTP trigger, not the preview
Functions MCP extension; per ADR 0004 the reference agent will later talk to
this same endpoint over HTTP even though they share a Function App.
"""
from __future__ import annotations

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


def _build_deps():
    config = get_config()

    # One long-lived httpx client for every outbound call (token endpoint and
    # Dataverse). Cold-start cost amortises across the Function host lifetime.
    http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    # The Managed Identity federates into the AAD app via a FIC; the MI token
    # carries the `fic_audience` and serves as the OAuth client_assertion
    # during OBO. MSAL caches the token inside DefaultAzureCredential, so this
    # synchronous call is near-instantaneous after the first fetch.
    credential = DefaultAzureCredential()
    fic_scope = f"{config.fic_audience}/.default"

    def _mi_token() -> str:
        return credential.get_token(fic_scope).token

    return ServerDeps(
        auth=DataverseAuth(config, http=http, mi_token_provider=_mi_token),
        client=OpportunityClient(config.dataverse_url, http=http),
    )


_asgi_app = create_asgi_app(_build_deps())

app = func.AsgiFunctionApp(
    app=_asgi_app,
    http_auth_level=func.AuthLevel.ANONYMOUS,
)
