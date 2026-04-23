"""Live search: real Dataverse name resolution for Account + Contact.

The demo environment ships with a handful of "… (sample)" accounts and
contacts; we search against those and assert the shape contract rather than
any specific value, because the demo content may evolve.
"""
from __future__ import annotations

import httpx
import pytest

from auth import build_auth
from config import get_config
from dataverse_client import OpportunityClient


async def test_search_accounts_against_live_dataverse_matches_sample_data():
    """`Fourth Coffee (sample)` is a stock Dataverse demo account; we know it
    exists and use `Fourth Coffee` as a reliable substring query."""
    config = get_config()

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        auth = build_auth(config, http=http, mi_token_provider=lambda: "")
        token = await auth.get_dataverse_token(user_jwt="unused-in-app-only")

        client = OpportunityClient(config.dataverse_url, http=http)
        matches = await client.search_accounts(token=token, query="Fourth Coffee")

    assert matches, "expected Dataverse to contain 'Fourth Coffee (sample)' demo data"
    assert all(isinstance(m["id"], str) and m["id"] for m in matches)
    assert all("Fourth Coffee" in m["name"] for m in matches)


async def test_search_accounts_empty_for_gibberish_query():
    """A query that can't possibly match must return an empty list, not
    raise — the LLM relies on [] as the zero-match signal."""
    config = get_config()

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        auth = build_auth(config, http=http, mi_token_provider=lambda: "")
        token = await auth.get_dataverse_token(user_jwt="unused-in-app-only")

        client = OpportunityClient(config.dataverse_url, http=http)
        # UUID-like string is extremely unlikely to appear in any account name.
        matches = await client.search_accounts(
            token=token, query="zzz-no-match-67e8f0a3"
        )

    assert matches == []


async def test_search_contacts_returns_shape_when_contacts_exist():
    """Dataverse demo data includes contacts; we don't assume any specific
    name so we fetch the first character of the alphabet just to get hits."""
    config = get_config()

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        auth = build_auth(config, http=http, mi_token_provider=lambda: "")
        token = await auth.get_dataverse_token(user_jwt="unused-in-app-only")

        client = OpportunityClient(config.dataverse_url, http=http)
        # 'a' is common enough to hit demo contacts; bounded by $top.
        matches = await client.search_contacts(token=token, query="a", top=3)

    # Shape check only; whether the demo env has matching contacts varies.
    assert isinstance(matches, list)
    for m in matches:
        assert "id" in m and m["id"]
        assert "name" in m
