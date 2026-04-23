"""Tests for src/auth.py — OBO-over-WIF Dataverse token exchange."""
from __future__ import annotations

import urllib.parse
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from config import CloudConfig


def _global_config() -> CloudConfig:
    return CloudConfig(
        authority="https://login.microsoftonline.com",
        dataverse_url="https://orgtest.crm.dynamics.com",
        fic_audience="api://AzureADTokenExchange",
        aad_app_client_id="11111111-1111-1111-1111-111111111111",
        aad_app_tenant_id="22222222-2222-2222-2222-222222222222",
    )


async def test_get_dataverse_token_performs_obo_request_with_correct_payload():
    """OBO exchange POSTs the right form fields and returns the Dataverse access token."""
    from auth import DataverseAuth

    config = _global_config()

    with respx.mock() as router:
        route = router.post(
            "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222/oauth2/v2.0/token"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "dataverse-token", "expires_in": 3600},
            )
        )

        async with httpx.AsyncClient() as http:
            auth = DataverseAuth(
                config,
                http=http,
                mi_token_provider=lambda: "mi-token-for-fic",
                clock=lambda: datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            )
            token = await auth.get_dataverse_token("user-jwt-abc")

    assert token == "dataverse-token"
    assert route.called
    form = dict(urllib.parse.parse_qsl(route.calls[0].request.content.decode()))
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assert form["client_id"] == config.aad_app_client_id
    assert form["client_assertion_type"] == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    assert form["client_assertion"] == "mi-token-for-fic"
    assert form["assertion"] == "user-jwt-abc"
    assert form["scope"] == "https://orgtest.crm.dynamics.com/.default"
    assert form["requested_token_use"] == "on_behalf_of"


async def test_get_dataverse_token_caches_within_ttl():
    """Second call for the same user within TTL must be served from cache (no re-exchange)."""
    from auth import DataverseAuth

    config = _global_config()

    with respx.mock() as router:
        route = router.post(
            "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222/oauth2/v2.0/token"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "dataverse-token", "expires_in": 3600},
            )
        )

        clock_now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
        async with httpx.AsyncClient() as http:
            auth = DataverseAuth(
                config,
                http=http,
                mi_token_provider=lambda: "mi-token",
                clock=lambda: clock_now,
            )
            token1 = await auth.get_dataverse_token("user-jwt-abc")
            token2 = await auth.get_dataverse_token("user-jwt-abc")

    assert token1 == token2 == "dataverse-token"
    assert route.call_count == 1  # second call served from cache


async def test_get_dataverse_token_refreshes_after_expiry():
    """Once the cached token is past its expiry, the next call re-exchanges."""
    from auth import DataverseAuth

    config = _global_config()

    with respx.mock() as router:
        route = router.post(
            "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222/oauth2/v2.0/token"
        ).mock(
            side_effect=[
                httpx.Response(200, json={"access_token": "first", "expires_in": 60}),
                httpx.Response(200, json={"access_token": "second", "expires_in": 60}),
            ]
        )

        t = {"now": datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)}
        async with httpx.AsyncClient() as http:
            auth = DataverseAuth(
                config,
                http=http,
                mi_token_provider=lambda: "mi-token",
                clock=lambda: t["now"],
            )
            first = await auth.get_dataverse_token("user-jwt-abc")
            # Advance clock well past the effective TTL (60s - 60s skew = immediate refresh
            # required; bump by 5 minutes to be unambiguous).
            t["now"] = t["now"] + timedelta(minutes=5)
            second = await auth.get_dataverse_token("user-jwt-abc")

    assert first == "first"
    assert second == "second"
    assert route.call_count == 2


async def test_client_secret_auth_uses_client_credentials_flow_and_ignores_user_jwt():
    """AUTH_MODE=app_only_secret path (ADR 0007) runs client_credentials and
    returns an app-only Dataverse token; it ignores any inbound user JWT."""
    from auth import ClientSecretDataverseAuth

    config = _global_config()

    with respx.mock() as router:
        route = router.post(
            "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222/oauth2/v2.0/token"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "service-account-dv-token", "expires_in": 3600},
            )
        )

        async with httpx.AsyncClient() as http:
            auth = ClientSecretDataverseAuth(
                config,
                http=http,
                client_secret="s3cr3t",
            )
            # The method signature is kept for interface parity; the user_jwt is
            # accepted but deliberately ignored in this auth mode.
            token = await auth.get_dataverse_token(user_jwt="any-value")

    assert token == "service-account-dv-token"
    assert route.called
    form = dict(urllib.parse.parse_qsl(route.calls[0].request.content.decode()))
    assert form["grant_type"] == "client_credentials"
    assert form["client_id"] == config.aad_app_client_id
    assert form["client_secret"] == "s3cr3t"
    assert form["scope"] == "https://orgtest.crm.dynamics.com/.default"
    # No OBO-specific fields should be sent on this path.
    assert "assertion" not in form
    assert "requested_token_use" not in form


def test_build_auth_dispatches_by_auth_mode(monkeypatch):
    """build_auth reads AUTH_MODE and returns the right implementation."""
    from auth import (
        ClientSecretDataverseAuth,
        DataverseAuth,
        UnsupportedAuthModeError,
        build_auth,
    )

    config = _global_config()

    async def _fake_mi() -> str:
        return "mi"

    monkeypatch.setenv("AUTH_MODE", "obo")
    import httpx as _httpx

    http = _httpx.AsyncClient()
    obo = build_auth(config, http=http, mi_token_provider=lambda: "mi")
    assert isinstance(obo, DataverseAuth)

    monkeypatch.setenv("AUTH_MODE", "app_only_secret")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "s3cr3t")
    sec = build_auth(config, http=http, mi_token_provider=lambda: "mi")
    assert isinstance(sec, ClientSecretDataverseAuth)

    monkeypatch.setenv("AUTH_MODE", "unknown")
    with pytest.raises(UnsupportedAuthModeError) as excinfo:
        build_auth(config, http=http, mi_token_provider=lambda: "mi")
    assert "unknown" in str(excinfo.value)
    assert "obo" in str(excinfo.value)
    assert "app_only_secret" in str(excinfo.value)
