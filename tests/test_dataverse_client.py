"""Tests for src/dataverse_client.py — Opportunity OData client."""
from __future__ import annotations

import httpx
import pytest
import respx


DATAVERSE_URL = "https://orgtest.crm.dynamics.com"
OPPS_URL = f"{DATAVERSE_URL}/api/data/v9.2/opportunities"


async def test_list_opportunities_sends_default_select_and_auth_header():
    """Default call hits /opportunities with the documented $select and bearer token."""
    from dataverse_client import OpportunityClient

    with respx.mock() as router:
        route = router.get(OPPS_URL).mock(
            return_value=httpx.Response(200, json={"value": []})
        )

        async with httpx.AsyncClient() as http:
            client = OpportunityClient(DATAVERSE_URL, http=http)
            result = await client.list_opportunities(token="dv-token-xyz")

    assert result == []
    assert route.called
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer dv-token-xyz"
    assert req.headers["Accept"] == "application/json"
    assert "OData.Community.Display.V1.FormattedValue" in req.headers["Prefer"]
    query = dict(req.url.params)
    select = query["$select"].split(",")
    # Default select covers the CRM list-view columns.
    for field in (
        "opportunityid",
        "name",
        "estimatedclosedate",
        "estimatedvalue",
        "_customerid_value",
        "closeprobability",
        "opportunityratingcode",
    ):
        assert field in select, f"expected default $select to include {field}"


async def test_list_opportunities_maps_formatted_values_to_public_shape():
    """Raw Dataverse record (with FormattedValue annotations) → human-friendly dict."""
    from dataverse_client import OpportunityClient

    fv = "OData.Community.Display.V1.FormattedValue"
    raw = {
        "opportunityid": "11111111-aaaa-aaaa-aaaa-111111111111",
        "name": "Enterprise Deal",
        "estimatedclosedate": "2026-06-30",
        "estimatedvalue": 80000.0,
        "_customerid_value": "22222222-bbbb-bbbb-bbbb-222222222222",
        f"_customerid_value@{fv}": "Fourth Coffee",
        "_parentaccountid_value": "22222222-bbbb-bbbb-bbbb-222222222222",
        f"_parentaccountid_value@{fv}": "Fourth Coffee",
        "_parentcontactid_value": None,
        "closeprobability": 80,
        "opportunityratingcode": 1,
        f"opportunityratingcode@{fv}": "Hot",
    }

    with respx.mock() as router:
        router.get(OPPS_URL).mock(
            return_value=httpx.Response(200, json={"value": [raw]})
        )

        async with httpx.AsyncClient() as http:
            client = OpportunityClient(DATAVERSE_URL, http=http)
            result = await client.list_opportunities(token="dv-token")

    assert result == [
        {
            "id": "11111111-aaaa-aaaa-aaaa-111111111111",
            "topic": "Enterprise Deal",
            "potential_customer": "Fourth Coffee",
            "est_close_date": "2026-06-30",
            "est_revenue": 80000.0,
            "contact": "",
            "account": "Fourth Coffee",
            "probability": 80,
            "rating": "Hot",
        }
    ]


async def test_list_opportunities_passes_filter_top_orderby():
    """Caller-supplied filter/top/orderby must land on the OData request as-is."""
    from dataverse_client import OpportunityClient

    with respx.mock() as router:
        route = router.get(OPPS_URL).mock(
            return_value=httpx.Response(200, json={"value": []})
        )

        async with httpx.AsyncClient() as http:
            client = OpportunityClient(DATAVERSE_URL, http=http)
            await client.list_opportunities(
                token="dv-token",
                filter="opportunityratingcode eq 1",
                top=25,
                orderby="estimatedvalue desc",
            )

    query = dict(route.calls[0].request.url.params)
    assert query["$filter"] == "opportunityratingcode eq 1"
    assert query["$top"] == "25"
    assert query["$orderby"] == "estimatedvalue desc"
