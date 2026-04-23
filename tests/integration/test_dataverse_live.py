"""Live-integration test: real Entra token exchange + real Dataverse list.

First checkpoint of the new testing discipline (ADR 0007): proves the code
actually talks to Azure. If this passes, subsequent live tests expand coverage
from here; if it fails, every downstream live test is blocked and we debug
the creds / networking / endpoint setup first.
"""
from __future__ import annotations

import httpx
import pytest

from auth import build_auth
from config import get_config
from dataverse_client import OpportunityClient


async def test_list_opportunities_against_live_dataverse():
    """Real app-only token from Entra; real list call against dev Dataverse.

    Assertions focus on the contract that the agent / MCP layer relies on —
    not on the specific data in the demo environment, which may vary.
    """
    config = get_config()

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        auth = build_auth(
            config,
            http=http,
            mi_token_provider=lambda: "",  # not used on client-secret path
        )
        token = await auth.get_dataverse_token(user_jwt="ignored-under-app-only")

        assert token, "Entra returned an empty access_token"
        # Access tokens issued by Entra are signed JWTs starting with the
        # header segment `eyJ`. This cheap shape assertion catches the most
        # common regression: a non-token string being returned.
        assert token.startswith("eyJ"), "expected a JWT from Entra"

        client = OpportunityClient(config.dataverse_url, http=http)
        rows = await client.list_opportunities(token=token, top=3)

    # Shape assertions — the demo env may have 0 rows at any given moment;
    # we validate the contract irrespective of data volume.
    assert isinstance(rows, list)
    assert len(rows) <= 3, "$top was not honoured"
    if rows:
        first = rows[0]
        for key in ("id", "topic", "est_revenue", "probability", "rating"):
            assert key in first, f"public shape missing {key!r}: {first}"
