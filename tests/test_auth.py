"""Tests for src/auth.py — OBO-over-WIF Dataverse token exchange."""
from __future__ import annotations

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
