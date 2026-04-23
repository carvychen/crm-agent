"""Tests for individual preflight checks (src/preflight/checks.py)."""
from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx


# --- DNS reachability -------------------------------------------------------


async def test_dns_check_passes_when_every_host_resolves(monkeypatch):
    from preflight.checks import DnsReachabilityCheck

    resolved: list[str] = []

    def _fake_resolve(host: str) -> str:
        resolved.append(host)
        return "203.0.113.5"  # RFC 5737 TEST-NET-3; never really resolves

    check = DnsReachabilityCheck(
        hosts=["login.example.invalid", "orgtest.crm.example.invalid"],
        resolver=_fake_resolve,
    )
    result = await check.run()

    assert result.status == "pass"
    assert resolved == [
        "login.example.invalid",
        "orgtest.crm.example.invalid",
    ]


async def test_dns_check_fails_when_any_host_unresolvable():
    from preflight.checks import DnsReachabilityCheck

    def _fake_resolve(host: str) -> str:
        if host == "broken":
            raise OSError(f"Name or service not known: {host}")
        return "1.2.3.4"

    check = DnsReachabilityCheck(
        hosts=["ok.example", "broken"],
        resolver=_fake_resolve,
    )
    result = await check.run()

    assert result.status == "fail"
    # Remediation must name the specific host that failed so the operator
    # knows WHICH firewall / Private DNS zone entry to fix — not just "DNS".
    assert "broken" in result.detail
    assert "DNS" in result.remediation or "firewall" in result.remediation.lower()


async def test_dns_check_reports_each_unresolvable_host():
    """Partial failure: one host OK, one host broken — remediation lists the
    broken one so the operator doesn't chase the wrong problem."""
    from preflight.checks import DnsReachabilityCheck

    def _fake_resolve(host: str) -> str:
        if "fail" in host:
            raise OSError("resolver says no")
        return "1.2.3.4"

    check = DnsReachabilityCheck(
        hosts=["ok.host", "fail.host"],
        resolver=_fake_resolve,
    )
    result = await check.run()

    assert result.status == "fail"
    assert "fail.host" in result.detail
    assert "ok.host" not in result.remediation


# --- Token acquisition ------------------------------------------------------


async def test_token_check_passes_when_client_credentials_returns_access_token(
    monkeypatch,
):
    from preflight.checks import TokenAcquisitionCheck

    fake_access_token = "secret-jwt-value-DO-NOT-LEAK-42"
    with respx.mock() as router:
        router.post(
            "https://login.example/22222222-2222-2222-2222-222222222222/oauth2/v2.0/token"
        ).mock(
            return_value=httpx.Response(
                200, json={"access_token": fake_access_token, "expires_in": 3600}
            )
        )

        async with httpx.AsyncClient() as http:
            check = TokenAcquisitionCheck(
                authority="https://login.example",
                tenant_id="22222222-2222-2222-2222-222222222222",
                client_id="11111111-1111-1111-1111-111111111111",
                client_secret="s3cret",
                dataverse_url="https://orgtest.crm.example",
                http=http,
            )
            result = await check.run()

    assert result.status == "pass"
    # The actual access token must never appear in the output — log shipping
    # could send preflight output somewhere with broader access than the token.
    assert fake_access_token not in result.detail
    assert fake_access_token not in result.remediation


async def test_token_check_fails_with_remediation_on_401():
    from preflight.checks import TokenAcquisitionCheck

    with respx.mock() as router:
        router.post(
            "https://login.example/22222222-2222-2222-2222-222222222222/oauth2/v2.0/token"
        ).mock(
            return_value=httpx.Response(
                401,
                json={
                    "error": "invalid_client",
                    "error_description": "AADSTS7000215: Invalid client secret provided.",
                },
            )
        )

        async with httpx.AsyncClient() as http:
            check = TokenAcquisitionCheck(
                authority="https://login.example",
                tenant_id="22222222-2222-2222-2222-222222222222",
                client_id="11111111-1111-1111-1111-111111111111",
                client_secret="wrong",
                dataverse_url="https://orgtest.crm.example",
                http=http,
            )
            result = await check.run()

    assert result.status == "fail"
    # Entra's own error text is surfaced so the admin knows what Entra saw.
    assert "AADSTS7000215" in result.detail
    # Remediation names the env vars the operator needs to fix.
    assert "AZURE_CLIENT_SECRET" in result.remediation


# --- Dataverse WhoAmI -------------------------------------------------------


async def test_whoami_check_passes_when_dataverse_returns_user_id():
    from preflight.checks import WhoAmICheck

    with respx.mock() as router:
        router.get("https://orgtest.crm.example/api/data/v9.2/WhoAmI").mock(
            return_value=httpx.Response(
                200,
                json={
                    "UserId": "33333333-4444-5555-6666-777777777777",
                    "BusinessUnitId": "88888888-aaaa-bbbb-cccc-888888888888",
                    "OrganizationId": "99999999-0000-1111-2222-999999999999",
                },
            )
        )

        async with httpx.AsyncClient() as http:
            check = WhoAmICheck(
                dataverse_url="https://orgtest.crm.example",
                token="dv-tok",
                http=http,
            )
            result = await check.run()

    assert result.status == "pass"
    # The returned UserId echoes in detail so the operator can confirm it's
    # the application user they expected — not some other SP that happens to
    # work by accident.
    assert "33333333-4444-5555-6666-777777777777" in result.detail


async def test_foundry_check_skips_when_agent_disabled():
    from preflight.checks import FoundryReachabilityCheck

    check = FoundryReachabilityCheck(
        agent_enabled=False,
        project_endpoint=None,
        model=None,
        credential_factory=None,
    )
    result = await check.run()

    assert result.status == "skip"
    assert "ENABLE_REFERENCE_AGENT" in result.detail


async def test_foundry_check_fails_when_no_endpoint_but_agent_enabled():
    from preflight.checks import FoundryReachabilityCheck

    check = FoundryReachabilityCheck(
        agent_enabled=True,
        project_endpoint=None,
        model="gpt-4o-mini",
        credential_factory=lambda: None,
    )
    result = await check.run()

    assert result.status == "fail"
    assert "FOUNDRY_PROJECT_ENDPOINT" in result.remediation


async def test_foundry_check_passes_when_probe_returns_output(monkeypatch):
    """The check runs a tiny AF agent.run('ping') against Foundry and
    asserts the agent returns a non-empty text response."""
    from preflight.checks import FoundryReachabilityCheck

    class _FakeAgent:
        async def run(self, messages):
            class _Resp:
                text = "OK"

            return _Resp()

    def _agent_factory(project_endpoint, model, credential):
        return _FakeAgent()

    check = FoundryReachabilityCheck(
        agent_enabled=True,
        project_endpoint="https://proj.services.ai.example",
        model="gpt-4o-mini",
        credential_factory=lambda: "credential-stub",
        agent_factory=_agent_factory,
    )
    result = await check.run()

    assert result.status == "pass"
    assert "Foundry" in result.detail or "reply" in result.detail.lower()


async def test_foundry_check_fails_when_probe_raises():
    from preflight.checks import FoundryReachabilityCheck

    class _BrokenAgent:
        async def run(self, messages):
            raise RuntimeError("401 Unauthorized from Foundry")

    def _agent_factory(project_endpoint, model, credential):
        return _BrokenAgent()

    check = FoundryReachabilityCheck(
        agent_enabled=True,
        project_endpoint="https://proj.services.ai.example",
        model="gpt-4o-mini",
        credential_factory=lambda: "credential-stub",
        agent_factory=_agent_factory,
    )
    result = await check.run()

    assert result.status == "fail"
    assert "401" in result.detail
    # Remediation names the likely causes (wrong tenant, missing RBAC, bad
    # deployment name) without prescribing just one — Foundry has several
    # failure modes and the operator needs the list.
    assert "Foundry" in result.remediation


async def test_whoami_check_fails_with_remediation_on_403():
    from preflight.checks import WhoAmICheck

    with respx.mock() as router:
        router.get("https://orgtest.crm.example/api/data/v9.2/WhoAmI").mock(
            return_value=httpx.Response(403, text="Forbidden")
        )

        async with httpx.AsyncClient() as http:
            check = WhoAmICheck(
                dataverse_url="https://orgtest.crm.example",
                token="dv-tok",
                http=http,
            )
            result = await check.run()

    assert result.status == "fail"
    # 403 means the app user isn't set up in Dataverse — the operator must
    # go to D365 admin and add the application user with a security role.
    assert (
        "application user" in result.remediation.lower()
        or "security role" in result.remediation.lower()
    )
