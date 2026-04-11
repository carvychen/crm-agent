"""
Dynamics 365 CRM Opportunity skill for agent-framework.

Defines a code-based Skill with scripts that wrap OpportunityClient CRUD operations.
The agent can call these scripts to list, get, create, update, and delete opportunities.

api-doc: https://learn.microsoft.com/zh-cn/power-apps/developer/data-platform/webapi/create-entity-web-api
"""

import json
from textwrap import dedent
from typing import Any

from agent_framework import Skill, SkillResource

from dataverse_client import get_client, OpportunityClient, safe_script

_fmt = OpportunityClient.format_opportunity


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
        - create_opportunity: Create a new opportunity (requires name and account_id). account_id accepts a GUID or account name.
        - update_opportunity: Update fields on an existing opportunity.
        - delete_opportunity: Delete an opportunity by GUID.

        IMPORTANT WORKFLOW — when the user provides a NAME instead of a GUID:

        For account/contact names:
        1. Use search_accounts or search_contacts to find the GUID first.
        2. If exactly one result, use that GUID automatically.
        3. If multiple results, ask the user to pick one.
        4. If no results, inform the user and stop.
        Do NOT ask the user for a GUID if you can look it up by name.

        For opportunity names (when opportunity_id is needed):
        1. Use list_opportunities with filter "contains(name, '<keyword>')" to find matching opportunities.
        2. If exactly one result, use its id automatically.
        3. If multiple results, ask the user to pick one.
        4. If no results, inform the user and stop.
        Do NOT ask the user for an opportunity GUID — always look it up by name.

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
                | account_id           | customerid_account@odata.bind      | GUID or name |
                | contact_id           | customerid_contact@odata.bind      | GUID or name |
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
@safe_script
def search_accounts(name: str, **kwargs: Any) -> str:
    client = get_client()
    return json.dumps(client.search_accounts(name), ensure_ascii=False)


@crm_opportunity_skill.script(
    name="search_contacts",
    description="Search for contacts by name. Required: name (search keyword). Returns list of {id, fullname}.",
)
@safe_script
def search_contacts(name: str, **kwargs: Any) -> str:
    client = get_client()
    return json.dumps(client.search_contacts(name), ensure_ascii=False)


@crm_opportunity_skill.script(
    name="list_opportunities",
    description="List opportunities. Optional: filter (OData $filter), order_by, top (max records).",
)
@safe_script
def list_opportunities(
    filter: str = "",
    order_by: str = "",
    top: int | None = None,
    **kwargs: Any,
) -> str:
    client = get_client()
    opps = client.list(
        filter_expr=filter or None,
        order_by=order_by or None,
        top=top,
    )
    return json.dumps([_fmt(o) for o in opps], ensure_ascii=False, default=str)


@crm_opportunity_skill.script(
    name="get_opportunity",
    description="Get a single opportunity by its GUID. Required: opportunity_id.",
)
@safe_script
def get_opportunity(opportunity_id: str, **kwargs: Any) -> str:
    client = get_client()
    opp = client.get(opportunity_id)
    return json.dumps(_fmt(opp), ensure_ascii=False, default=str)


@crm_opportunity_skill.script(
    name="create_opportunity",
    description=(
        "Create an opportunity. Required: name, account_id (GUID or account name). "
        "Optional: estimatedvalue, estimatedclosedate, closeprobability, opportunityratingcode."
    ),
)
@safe_script
def create_opportunity(
    name: str = "",
    account_id: str = "",
    contact_id: str = "",
    estimatedvalue: float | None = None,
    estimatedclosedate: str = "",
    closeprobability: int | None = None,
    opportunityratingcode: int | None = None,
    parentcontactid: str = "",
    **kwargs: Any,
) -> str:
    if not name:
        return json.dumps({"error": "Missing required parameter: name", "received_kwargs": list(kwargs.keys())}, ensure_ascii=False)

    client = get_client()
    data: dict[str, Any] = {"name": name}

    # Potential Customer — auto-resolve names to GUIDs
    if account_id:
        resolved = client.resolve_account_id(account_id)
        data["customerid_account@odata.bind"] = f"/accounts({resolved})"
        data["parentaccountid@odata.bind"] = f"/accounts({resolved})"
    elif contact_id:
        resolved = client.resolve_contact_id(contact_id)
        data["customerid_contact@odata.bind"] = f"/contacts({resolved})"
        data["parentcontactid@odata.bind"] = f"/contacts({resolved})"

    if estimatedvalue is not None:
        data["estimatedvalue"] = float(estimatedvalue)
    if closeprobability is not None:
        data["closeprobability"] = int(closeprobability)
    if opportunityratingcode is not None:
        data["opportunityratingcode"] = int(opportunityratingcode)
    if estimatedclosedate:
        data["estimatedclosedate"] = estimatedclosedate
    if parentcontactid:
        resolved = client.resolve_contact_id(parentcontactid)
        data["parentcontactid@odata.bind"] = f"/contacts({resolved})"

    new_id = client.create(data)
    opp = client.get(new_id)
    return json.dumps({"created": _fmt(opp)}, ensure_ascii=False, default=str)


@crm_opportunity_skill.script(
    name="update_opportunity",
    description=(
        "Update an opportunity. Required: opportunity_id. "
        "Optional: name, estimatedvalue, estimatedclosedate, closeprobability, opportunityratingcode."
    ),
)
@safe_script
def update_opportunity(
    opportunity_id: str,
    name: str = "",
    estimatedvalue: float | None = None,
    estimatedclosedate: str = "",
    closeprobability: int | None = None,
    opportunityratingcode: int | None = None,
    **kwargs: Any,
) -> str:
    client = get_client()
    data: dict[str, Any] = {}
    if name:
        data["name"] = name
    if estimatedvalue is not None:
        data["estimatedvalue"] = float(estimatedvalue)
    if estimatedclosedate:
        data["estimatedclosedate"] = estimatedclosedate
    if closeprobability is not None:
        data["closeprobability"] = int(closeprobability)
    if opportunityratingcode is not None:
        data["opportunityratingcode"] = int(opportunityratingcode)

    client.update(opportunity_id, data)
    opp = client.get(opportunity_id)
    return json.dumps({"updated": _fmt(opp)}, ensure_ascii=False, default=str)


@crm_opportunity_skill.script(
    name="delete_opportunity",
    description="Delete an opportunity. Required: opportunity_id (GUID).",
)
@safe_script
def delete_opportunity(opportunity_id: str, **kwargs: Any) -> str:
    client = get_client()
    client.delete(opportunity_id)
    return json.dumps({"deleted": opportunity_id})
