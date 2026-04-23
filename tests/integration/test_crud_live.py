"""Live CRUD round-trip: create → get → update → delete against real Dataverse.

Every test writes under a `CRM-Agent-Test-<uuid4>` topic name and guarantees
cleanup in `finally:` regardless of assertion outcome. We bind to the first
available account in the dev environment — the conftest already asserts that
at least one account exists (the demo env ships with "Fourth Coffee (sample)").
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from auth import build_auth
from config import get_config
from dataverse_client import OpportunityClient


async def _first_account_id(http: httpx.AsyncClient, token: str, base_url: str) -> str:
    """Pick any account to bind new opportunities to; ordering is irrelevant."""
    resp = await http.get(
        f"{base_url}/api/data/v9.2/accounts",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"$select": "accountid,name", "$top": "1"},
    )
    resp.raise_for_status()
    rows = resp.json().get("value", [])
    if not rows:
        pytest.skip("dev Dataverse has no accounts — cannot bind a test opportunity")
    return rows[0]["accountid"]


async def test_opportunity_create_get_update_delete_round_trip():
    """Full CRUD pipeline against the live demo Dataverse.

    Stages:
      1. Create with CRM-Agent-Test-<uuid> topic, bound to the first account.
      2. Assert get_opportunity returns the same record with the formatted shape.
      3. Update probability and verify the patch landed via a follow-up get.
      4. Delete and verify the subsequent get returns 404.
    The test guarantees cleanup in a teardown try/finally; the record is
    deleted even if an intermediate assertion fails.
    """
    config = get_config()
    topic = f"CRM-Agent-Test-{uuid.uuid4().hex[:12]}"
    new_id: str | None = None

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        auth = build_auth(config, http=http, mi_token_provider=lambda: "")
        token = await auth.get_dataverse_token(user_jwt="unused-in-app-only")
        client = OpportunityClient(config.dataverse_url, http=http)

        try:
            account_id = await _first_account_id(http, token, config.dataverse_url)

            # --- Create --------------------------------------------------
            new_id = await client.create_opportunity(
                token=token,
                name=topic,
                customer_id=account_id,
                customer_type="account",
                estimated_value=55000.0,
                estimated_close_date="2026-12-31",
                probability=50,
                rating=2,  # Warm
            )
            assert new_id, "create_opportunity must return a GUID"

            # --- Get -----------------------------------------------------
            fetched = await client.get_opportunity(token=token, opportunity_id=new_id)
            assert fetched["id"].lower() == new_id.lower()
            assert fetched["topic"] == topic
            assert fetched["probability"] == 50
            assert fetched["rating"] == "Warm"

            # --- Update --------------------------------------------------
            await client.update_opportunity(
                token=token,
                opportunity_id=new_id,
                probability=85,
                rating=1,  # Hot
            )
            refreshed = await client.get_opportunity(
                token=token, opportunity_id=new_id
            )
            assert refreshed["probability"] == 85
            assert refreshed["rating"] == "Hot"
            # Fields we didn't patch must be preserved.
            assert refreshed["topic"] == topic

            # --- Delete (exercised by code path; primary cleanup is below) -
            await client.delete_opportunity(token=token, opportunity_id=new_id)
            new_id = None  # mark as already cleaned

            # After delete the record must be gone.
            with pytest.raises(httpx.HTTPStatusError) as excinfo:
                await client.get_opportunity(
                    token=token, opportunity_id=fetched["id"]
                )
            assert excinfo.value.response.status_code == 404
        finally:
            # Teardown: if the test failed mid-way, still remove the test
            # record so we don't leak CRM-Agent-Test-* junk into the demo env.
            if new_id is not None:
                try:
                    await client.delete_opportunity(
                        token=token, opportunity_id=new_id
                    )
                except Exception:  # pragma: no cover — best-effort cleanup
                    pass
