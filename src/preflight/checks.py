"""Individual preflight checks: DNS, token acquisition, WhoAmI, Foundry.

Each check is a concrete `Check`. Checks take their dependencies by
constructor so tests can inject mocks without monkey-patching.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

import httpx

from preflight.core import CheckResult

Resolver = Callable[[str], str]


def _host_of(url: str) -> str:
    return urlparse(url).hostname or url


# --- DNS reachability -------------------------------------------------------


@dataclass
class DnsReachabilityCheck:
    """Resolve every host in the given list.

    A host that fails to resolve points the operator at the concrete name
    to unblock (Private DNS zone, firewall egress rule, or split-horizon
    resolver). The check is synchronous; DNS calls are fast and a blocked
    port 53 manifests as a timeout rather than a hang.
    """

    hosts: list[str]
    resolver: Resolver = socket.gethostbyname
    name: str = "dns-reachability"

    async def run(self) -> CheckResult:
        failures: list[str] = []
        for host in self.hosts:
            try:
                self.resolver(host)
            except OSError as exc:
                failures.append(f"{host} ({exc})")
        if not failures:
            return CheckResult(
                name=self.name,
                status="pass",
                detail=f"resolved {len(self.hosts)} host(s): "
                + ", ".join(self.hosts),
            )
        return CheckResult(
            name=self.name,
            status="fail",
            detail="unresolvable: " + "; ".join(failures),
            remediation=(
                "Confirm DNS resolution for the listed hosts from the Function "
                "App's VNet. For Azure China (21Vianet), Private DNS zones "
                "differ from Global — check the Private Endpoint + Private DNS "
                "zone link. For Azure Global dev, a corporate firewall or VPN "
                "split-tunnel is the usual cause."
            ),
        )


# --- Token acquisition ------------------------------------------------------


@dataclass
class TokenAcquisitionCheck:
    """Exchange client credentials for a Dataverse-scoped access token.

    Covers the dev path (`AUTH_MODE=app_only_secret`). An OBO-specific check
    would additionally verify the FIC assertion; that lives in a separate
    check type and only runs when `AUTH_MODE=obo` (not yet wired because the
    author's tenant has no WIF configured — Slice 8 ships the check shape
    with Dataverse-token coverage and leaves the OBO variant as a follow-up
    once WIF is provisioned at UAT).
    """

    authority: str
    tenant_id: str
    client_id: str
    client_secret: str
    dataverse_url: str
    http: httpx.AsyncClient
    name: str = "token-acquisition"

    async def run(self) -> CheckResult:
        try:
            response = await self.http.post(
                f"{self.authority}/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": f"{self.dataverse_url}/.default",
                },
            )
        except httpx.RequestError as exc:
            return CheckResult(
                name=self.name,
                status="fail",
                detail=f"network error reaching Entra: {exc}",
                remediation=(
                    "Verify outbound HTTPS to the authority host. In Azure "
                    "China, the authority is login.partner.microsoftonline.cn "
                    "(not .com)."
                ),
            )

        if response.status_code == 200 and response.json().get("access_token"):
            # Never echo the access token — logs might be shipped to places
            # with wider access than the token grants.
            return CheckResult(
                name=self.name,
                status="pass",
                detail="Entra issued a Dataverse-scoped access token",
            )
        body = response.text
        return CheckResult(
            name=self.name,
            status="fail",
            detail=f"Entra returned {response.status_code}: {body}",
            remediation=(
                "Check the AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / "
                "AZURE_TENANT_ID values. Entra's `error_description` "
                "(e.g. AADSTS-prefixed code) above names the root cause; "
                "Microsoft Learn's 'AADSTS error codes' page decodes each."
            ),
        )


# --- Dataverse WhoAmI -------------------------------------------------------


@dataclass
class WhoAmICheck:
    """Call Dataverse's WhoAmI with the provided token.

    A 200 with a `UserId` means:
      - the token is accepted by Dataverse,
      - the Dataverse application user exists,
      - and it has at least the baseline security role needed for reads.

    A 403 or 404 means the Dataverse application user isn't set up correctly
    — the remediation points the admin at D365 Admin Center.
    """

    dataverse_url: str
    token: str
    http: httpx.AsyncClient
    name: str = "dataverse-whoami"

    async def run(self) -> CheckResult:
        if not self.token:
            return CheckResult(
                name=self.name,
                status="fail",
                detail="no Dataverse token available (prior check should have failed)",
                remediation="Investigate the token-acquisition check failure first.",
            )
        try:
            response = await self.http.get(
                f"{self.dataverse_url}/api/data/v9.2/WhoAmI",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
            )
        except httpx.RequestError as exc:
            return CheckResult(
                name=self.name,
                status="fail",
                detail=f"network error reaching Dataverse: {exc}",
                remediation=(
                    "Check DATAVERSE_URL points at the right host, and that "
                    "the Function App's egress can reach it. China tenants "
                    "use *.crm.dynamics.cn hostnames."
                ),
            )

        if response.status_code == 200:
            body = response.json()
            user_id = body.get("UserId", "(missing UserId)")
            return CheckResult(
                name=self.name,
                status="pass",
                detail=f"Dataverse accepted the token as UserId={user_id}",
            )
        return CheckResult(
            name=self.name,
            status="fail",
            detail=f"Dataverse returned {response.status_code}: {response.text}",
            remediation=(
                "Ensure the AAD application user is created in the Dataverse "
                "environment (D365 Admin Center → Environments → Settings → "
                "Users + permissions → Application users → New) and assigned "
                "a security role that grants Delegate privilege. Without "
                "that, even a valid Entra token returns 403 at the Dataverse "
                "boundary."
            ),
        )


# --- Foundry reachability ---------------------------------------------------


@dataclass
class FoundryReachabilityCheck:
    """Probe the Foundry chat completions endpoint with a trivial 1-token call.

    Skipped cleanly when `ENABLE_REFERENCE_AGENT=false` — MCP-only deployments
    don't need a working LLM. When enabled but misconfigured, the remediation
    names the three most common failure modes (wrong tenant for the SP, no
    Cognitive Services User role assignment, wrong deployment name) so the
    operator can triage without reading our code.
    """

    agent_enabled: bool
    project_endpoint: str | None
    model: str | None
    credential_factory: Callable[[], object] | None
    agent_factory: Callable[[str, str, object], object] | None = None
    name: str = "foundry-reachability"

    async def run(self) -> CheckResult:
        if not self.agent_enabled:
            return CheckResult(
                name=self.name,
                status="skip",
                detail="ENABLE_REFERENCE_AGENT=false — MCP-only deployment",
            )
        if not self.project_endpoint:
            return CheckResult(
                name=self.name,
                status="fail",
                detail="FOUNDRY_PROJECT_ENDPOINT is not set",
                remediation=(
                    "Set FOUNDRY_PROJECT_ENDPOINT to the Foundry project's "
                    "base URL (e.g. https://<name>.services.ai.azure.com/"
                    "api/projects/<project>) in the Function App config."
                ),
            )

        credential = (self.credential_factory or (lambda: None))()
        agent = (self.agent_factory or _default_foundry_agent_factory)(
            self.project_endpoint, self.model or "gpt-4o-mini", credential
        )

        try:
            # AF's Agent.run accepts a plain string or a Message list; the
            # simple string form is enough for a reachability probe.
            response = await agent.run("ping")
        except Exception as exc:  # noqa: BLE001 — any failure is a fail result
            return CheckResult(
                name=self.name,
                status="fail",
                detail=f"Foundry probe raised {type(exc).__name__}: {exc}",
                remediation=(
                    "Foundry probes commonly fail for three reasons: "
                    "(a) the credential authenticates in the wrong tenant — "
                    "Foundry and Dataverse may live in different tenants, "
                    "and `az login` / the SP must target Foundry's tenant; "
                    "(b) the identity lacks `Cognitive Services User` on "
                    "the Foundry project; (c) FOUNDRY_MODEL doesn't match a "
                    "real deployment name in the project."
                ),
            )

        text = getattr(response, "text", "") or ""
        if not text:
            return CheckResult(
                name=self.name,
                status="fail",
                detail="Foundry accepted the call but returned empty text",
                remediation=(
                    "The model responded but with no content; check the "
                    "deployment's content filters and system prompt length."
                ),
            )
        return CheckResult(
            name=self.name,
            status="pass",
            detail=f"Foundry returned a reply ({len(text)} chars)",
        )


def _default_foundry_agent_factory(
    project_endpoint: str, model: str, credential: object
) -> object:  # pragma: no cover — exercised by live test, not unit
    """Late import so `agent_framework` stays a soft dep of the preflight."""
    from agent_framework import Agent
    from agent_framework.foundry import FoundryChatClient

    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=credential,
    )
    return Agent(
        client=client,
        instructions="You are a reachability probe. Reply briefly.",
    )
