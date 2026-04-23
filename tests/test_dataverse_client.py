"""Tests for src/dataverse_client.py — Opportunity OData client."""
from __future__ import annotations

import json

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


async def test_get_opportunity_returns_formatted_single_record():
    """GET /opportunities({id}) returns the public-shape dict for one record."""
    from dataverse_client import OpportunityClient

    opp_id = "aaaa1111-bbbb-cccc-dddd-eeee22223333"
    raw = {
        "opportunityid": opp_id,
        "name": "Enterprise Deal",
        "estimatedclosedate": "2026-06-30",
        "estimatedvalue": 80000.0,
        "_customerid_value": "acc-1",
        "_customerid_value@OData.Community.Display.V1.FormattedValue": "Fourth Coffee",
        "closeprobability": 80,
        "opportunityratingcode": 1,
        "opportunityratingcode@OData.Community.Display.V1.FormattedValue": "Hot",
    }

    with respx.mock() as router:
        route = router.get(f"{OPPS_URL}({opp_id})").mock(
            return_value=httpx.Response(200, json=raw)
        )

        async with httpx.AsyncClient() as http:
            client = OpportunityClient(DATAVERSE_URL, http=http)
            result = await client.get_opportunity(token="dv-token", opportunity_id=opp_id)

    assert route.called
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer dv-token"
    # $select is applied even on single-record GET so the shape is stable
    # regardless of whatever server-side default projection Dataverse uses.
    query = dict(req.url.params)
    assert "opportunityid" in query["$select"]

    assert result["id"] == opp_id
    assert result["topic"] == "Enterprise Deal"
    assert result["rating"] == "Hot"
    assert result["potential_customer"] == "Fourth Coffee"


async def test_create_opportunity_with_account_customer_uses_account_binding():
    """customer_type='account' emits customerid_account@odata.bind, not contact."""
    from dataverse_client import OpportunityClient

    new_id = "11111111-aaaa-bbbb-cccc-111111111111"
    with respx.mock() as router:
        route = router.post(OPPS_URL).mock(
            return_value=httpx.Response(
                204,
                headers={
                    "OData-EntityId": f"{OPPS_URL}({new_id})",
                },
            )
        )

        async with httpx.AsyncClient() as http:
            client = OpportunityClient(DATAVERSE_URL, http=http)
            returned_id = await client.create_opportunity(
                token="dv-token",
                name="Enterprise Deal",
                customer_id="22222222-bbbb-bbbb-bbbb-222222222222",
                customer_type="account",
                estimated_value=80000.0,
                estimated_close_date="2026-06-30",
                probability=80,
                rating=1,
            )

    assert returned_id == new_id
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "Enterprise Deal"
    # Polymorphic customerid: account path must use the account bind field.
    assert body["customerid_account@odata.bind"] == "/accounts(22222222-bbbb-bbbb-bbbb-222222222222)"
    assert "customerid_contact@odata.bind" not in body
    assert body["estimatedvalue"] == 80000.0
    assert body["estimatedclosedate"] == "2026-06-30"
    assert body["closeprobability"] == 80
    assert body["opportunityratingcode"] == 1


async def test_create_opportunity_with_contact_customer_uses_contact_binding():
    """customer_type='contact' emits customerid_contact@odata.bind, not account."""
    from dataverse_client import OpportunityClient

    new_id = "33333333-cccc-dddd-eeee-333333333333"
    with respx.mock() as router:
        route = router.post(OPPS_URL).mock(
            return_value=httpx.Response(
                204,
                headers={"OData-EntityId": f"{OPPS_URL}({new_id})"},
            )
        )

        async with httpx.AsyncClient() as http:
            client = OpportunityClient(DATAVERSE_URL, http=http)
            returned_id = await client.create_opportunity(
                token="dv-token",
                name="Consulting Engagement",
                customer_id="44444444-eeee-ffff-0000-444444444444",
                customer_type="contact",
            )

    assert returned_id == new_id
    body = json.loads(route.calls[0].request.content)
    assert body["customerid_contact@odata.bind"] == "/contacts(44444444-eeee-ffff-0000-444444444444)"
    assert "customerid_account@odata.bind" not in body


async def test_create_opportunity_rejects_unknown_customer_type():
    from dataverse_client import OpportunityClient

    async with httpx.AsyncClient() as http:
        client = OpportunityClient(DATAVERSE_URL, http=http)
        with pytest.raises(ValueError) as excinfo:
            await client.create_opportunity(
                token="dv-token",
                name="x",
                customer_id="55555555-aaaa-bbbb-cccc-555555555555",
                customer_type="partner",  # not a Dataverse polymorphic target
            )
    assert "partner" in str(excinfo.value)
    assert "account" in str(excinfo.value)
    assert "contact" in str(excinfo.value)


async def test_update_opportunity_patches_only_supplied_fields():
    """PATCH only ships keys the caller set — never clobbers unspecified fields."""
    from dataverse_client import OpportunityClient

    opp_id = "aaaa1111-bbbb-cccc-dddd-eeee22223333"
    with respx.mock() as router:
        route = router.patch(f"{OPPS_URL}({opp_id})").mock(
            return_value=httpx.Response(204)
        )

        async with httpx.AsyncClient() as http:
            client = OpportunityClient(DATAVERSE_URL, http=http)
            await client.update_opportunity(
                token="dv-token",
                opportunity_id=opp_id,
                probability=75,
            )

    assert route.called
    body = json.loads(route.calls[0].request.content)
    # Only the explicitly-supplied field is in the payload.
    assert body == {"closeprobability": 75}


async def test_update_opportunity_accepts_multiple_fields():
    from dataverse_client import OpportunityClient

    opp_id = "aaaa1111-bbbb-cccc-dddd-eeee22223333"
    with respx.mock() as router:
        route = router.patch(f"{OPPS_URL}({opp_id})").mock(return_value=httpx.Response(204))

        async with httpx.AsyncClient() as http:
            client = OpportunityClient(DATAVERSE_URL, http=http)
            await client.update_opportunity(
                token="dv-token",
                opportunity_id=opp_id,
                name="Renegotiated Deal",
                estimated_value=120000.0,
                estimated_close_date="2026-09-15",
                rating=2,
            )

    body = json.loads(route.calls[0].request.content)
    assert body == {
        "name": "Renegotiated Deal",
        "estimatedvalue": 120000.0,
        "estimatedclosedate": "2026-09-15",
        "opportunityratingcode": 2,
    }


async def test_delete_opportunity_sends_delete_to_record_url():
    from dataverse_client import OpportunityClient

    opp_id = "aaaa1111-bbbb-cccc-dddd-eeee22223333"
    with respx.mock() as router:
        route = router.delete(f"{OPPS_URL}({opp_id})").mock(
            return_value=httpx.Response(204)
        )

        async with httpx.AsyncClient() as http:
            client = OpportunityClient(DATAVERSE_URL, http=http)
            await client.delete_opportunity(token="dv-token", opportunity_id=opp_id)

    assert route.called
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer dv-token"


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
