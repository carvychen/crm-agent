"""
Dynamics 365 CRM Opportunity skill for agent-framework.

Defines a code-based Skill with scripts that wrap OpportunityClient CRUD operations.
The agent can call these scripts to list, get, create, update, and delete opportunities.

api-doc: https://learn.microsoft.com/zh-cn/power-apps/developer/data-platform/webapi/create-entity-web-api
"""

import json
import os
from textwrap import dedent
from typing import Any

import requests
from agent_framework import Skill, SkillResource

from dataverse_client import build_client_from_env, OpportunityClient

# Formatted-value annotation key
FV = "OData.Community.Display.V1.FormattedValue"


def _format_opp(opp: dict) -> dict:
    """Convert a raw Dataverse opportunity record to a human-friendly dict."""
    return {
        "id": opp.get("opportunityid", ""),
        "topic": opp.get("name", ""),
        "potential_customer": opp.get(f"_customerid_value@{FV}") or opp.get("_customerid_value", ""),
        "est_close_date": opp.get("estimatedclosedate", ""),
        "est_revenue": opp.get("estimatedvalue"),
        "contact": opp.get(f"_parentcontactid_value@{FV}") or opp.get("_parentcontactid_value", ""),
        "account": opp.get(f"_parentaccountid_value@{FV}") or opp.get("_parentaccountid_value", ""),
        "probability": opp.get("closeprobability"),
        "rating": opp.get(f"opportunityratingcode@{FV}") or OpportunityClient.RATING.get(opp.get("opportunityratingcode"), ""),
    }


# ── Skill definition ─────────────────────────────────────────────────────────

crm_opportunity_skill = Skill(
    name="crm-opportunity",
    description="Manage Dynamics 365 CRM opportunities: list, get, create, update, delete",
    content=dedent("""\
        Use this skill when the user asks about CRM opportunities (deals, sales pipeline, revenue).

        Available scripts:
        - search_accounts: Search for accounts by name. Returns account GUIDs.
        - search_contacts: Search for contacts by name. Returns contact GUIDs.
        - list_opportunities: Query opportunities with optional filters, sorting, and limits.
        - get_opportunity: Get a single opportunity by its GUID.
        - create_opportunity: Create a new opportunity (requires name and account_id).
        - update_opportunity: Update fields on an existing opportunity.
        - delete_opportunity: Delete an opportunity by GUID.

        IMPORTANT WORKFLOW — when the user provides an account/contact NAME (not a GUID):
        1. Use search_accounts or search_contacts to find the GUID first.
        2. If exactly one result, use that GUID automatically.
        3. If multiple results, ask the user to pick one.
        4. If no results, inform the user and stop.
        Do NOT ask the user for a GUID if you can look it up by name.

        Field reference (from the CRM list view):
        - Topic (name): The opportunity title
        - Potential Customer: The account or contact this opportunity belongs to
        - Est. Close Date (estimatedclosedate): Expected close date, format YYYY-MM-DD
        - Est. Revenue (estimatedvalue): Expected deal value
        - Contact: Related contact person
        - Account: Related company/account
        - Probability (closeprobability): Win probability 0–100
        - Rating (opportunityratingcode): 1=Hot, 2=Warm, 3=Cold

        When creating, the Potential Customer field is required. Use account_id to bind
        to an account, or contact_id to bind to a contact.

        Review the field-reference resource for OData filter examples.
    """),
    resources=[
        SkillResource(
            name="field-reference",
            content=dedent("""\
                # Opportunity Field Reference

                ## Writable fields
                | Parameter            | API Field                          | Type    |
                |----------------------|------------------------------------|---------|
                | name                 | name                               | string  |
                | account_id           | customerid_account@odata.bind      | GUID    |
                | contact_id           | customerid_contact@odata.bind      | GUID    |
                | estimatedclosedate   | estimatedclosedate                 | date    |
                | estimatedvalue       | estimatedvalue                     | float   |
                | closeprobability     | closeprobability                   | int     |
                | opportunityratingcode| opportunityratingcode              | int     |

                ## Rating values
                1 = Hot, 2 = Warm, 3 = Cold

                ## OData $filter examples
                - All opportunities: (no filter)
                - Revenue > 50000: "estimatedvalue gt 50000"
                - Hot rating: "opportunityratingcode eq 1"
                - Name search: "contains(name, 'keyword')"
                - Closing before date: "estimatedclosedate lt 2026-12-31"
                - Combined: "estimatedvalue gt 20000 and opportunityratingcode eq 1"
            """),
        ),
    ],
)


# ── Scripts ───────────────────────────────────────────────────────────────────

@crm_opportunity_skill.script(
    name="search_accounts",
    description="Search for accounts by name. Required: name (search keyword). Returns list of {id, name}.",
)
def search_accounts(**kwargs: Any) -> str:
    client = build_client_from_env()
    name = kwargs["name"]
    response = requests.get(
        client._url("accounts"),
        headers=client._get_headers(),
        params={
            "$select": "accountid,name",
            "$filter": f"contains(name, '{name}')",
            "$top": 10,
        },
    )
    client._raise_for_status(response)
    accounts = response.json().get("value", [])
    return json.dumps(
        [{"id": a["accountid"], "name": a["name"]} for a in accounts],
        ensure_ascii=False,
    )


@crm_opportunity_skill.script(
    name="search_contacts",
    description="Search for contacts by name. Required: name (search keyword). Returns list of {id, fullname}.",
)
def search_contacts(**kwargs: Any) -> str:
    client = build_client_from_env()
    name = kwargs["name"]
    response = requests.get(
        client._url("contacts"),
        headers=client._get_headers(),
        params={
            "$select": "contactid,fullname",
            "$filter": f"contains(fullname, '{name}')",
            "$top": 10,
        },
    )
    client._raise_for_status(response)
    contacts = response.json().get("value", [])
    return json.dumps(
        [{"id": c["contactid"], "name": c["fullname"]} for c in contacts],
        ensure_ascii=False,
    )


@crm_opportunity_skill.script(
    name="list_opportunities",
    description="List opportunities. Optional: filter (OData $filter), order_by, top (max records).",
)
def list_opportunities(**kwargs: Any) -> str:
    client = build_client_from_env()
    opps = client.list(
        filter_expr=kwargs.get("filter"),
        order_by=kwargs.get("order_by"),
        top=int(kwargs["top"]) if kwargs.get("top") else None,
    )
    return json.dumps([_format_opp(o) for o in opps], ensure_ascii=False, default=str)


@crm_opportunity_skill.script(
    name="get_opportunity",
    description="Get a single opportunity by its GUID. Required: opportunity_id.",
)
def get_opportunity(**kwargs: Any) -> str:
    client = build_client_from_env()
    opp = client.get(kwargs["opportunity_id"])
    return json.dumps(_format_opp(opp), ensure_ascii=False, default=str)


@crm_opportunity_skill.script(
    name="create_opportunity",
    description=(
        "Create an opportunity. Required: name, account_id (GUID). "
        "Optional: estimatedvalue, estimatedclosedate, closeprobability, opportunityratingcode."
    ),
)
def create_opportunity(**kwargs: Any) -> str:
    client = build_client_from_env()

    data: dict[str, Any] = {"name": kwargs["name"]}

    # Potential Customer — account or contact
    if kwargs.get("account_id"):
        data["customerid_account@odata.bind"] = f"/accounts({kwargs['account_id']})"
        data["parentaccountid@odata.bind"] = f"/accounts({kwargs['account_id']})"
    elif kwargs.get("contact_id"):
        data["customerid_contact@odata.bind"] = f"/contacts({kwargs['contact_id']})"
        data["parentcontactid@odata.bind"] = f"/contacts({kwargs['contact_id']})"

    for field in ("estimatedvalue", "closeprobability", "opportunityratingcode"):
        if kwargs.get(field) is not None:
            data[field] = float(kwargs[field]) if field == "estimatedvalue" else int(kwargs[field])

    if kwargs.get("estimatedclosedate"):
        data["estimatedclosedate"] = kwargs["estimatedclosedate"]

    if kwargs.get("parentcontactid"):
        data["parentcontactid@odata.bind"] = f"/contacts({kwargs['parentcontactid']})"

    new_id = client.create(data)
    opp = client.get(new_id)
    return json.dumps({"created": _format_opp(opp)}, ensure_ascii=False, default=str)


@crm_opportunity_skill.script(
    name="update_opportunity",
    description=(
        "Update an opportunity. Required: opportunity_id. "
        "Optional: name, estimatedvalue, estimatedclosedate, closeprobability, opportunityratingcode."
    ),
)
def update_opportunity(**kwargs: Any) -> str:
    client = build_client_from_env()
    opportunity_id = kwargs["opportunity_id"]

    data: dict[str, Any] = {}
    if kwargs.get("name"):
        data["name"] = kwargs["name"]
    if kwargs.get("estimatedvalue") is not None:
        data["estimatedvalue"] = float(kwargs["estimatedvalue"])
    if kwargs.get("estimatedclosedate"):
        data["estimatedclosedate"] = kwargs["estimatedclosedate"]
    if kwargs.get("closeprobability") is not None:
        data["closeprobability"] = int(kwargs["closeprobability"])
    if kwargs.get("opportunityratingcode") is not None:
        data["opportunityratingcode"] = int(kwargs["opportunityratingcode"])

    client.update(opportunity_id, data)
    opp = client.get(opportunity_id)
    return json.dumps({"updated": _format_opp(opp)}, ensure_ascii=False, default=str)


@crm_opportunity_skill.script(
    name="delete_opportunity",
    description="Delete an opportunity. Required: opportunity_id (GUID).",
)
def delete_opportunity(**kwargs: Any) -> str:
    client = build_client_from_env()
    opportunity_id = kwargs["opportunity_id"]
    client.delete(opportunity_id)
    return json.dumps({"deleted": opportunity_id})
