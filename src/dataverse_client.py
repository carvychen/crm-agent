"""Dataverse Web API client — Opportunity entity.

Unlike the original `skills/crm-opportunity/lib/dataverse_client.py`, this
version does NOT bind to a credential at construction time. The caller passes a
Dataverse-scoped access token per call — produced by `DataverseAuth` via OBO —
so every request runs under the real end-user's identity and Dataverse RLS
applies.
"""
from __future__ import annotations

from typing import Any

import httpx

_API_VERSION = "v9.2"
_FV_ANNOTATION = "OData.Community.Display.V1.FormattedValue"

# Polymorphic `customerid` lookup: Dataverse stores it as a Customer field that
# can point at either an Account OR a Contact. Writes use a type-specific
# @odata.bind field; we surface the type as a public enum.
_CUSTOMER_TYPES = {"account": "accounts", "contact": "contacts"}

_DEFAULT_SELECT_FIELDS = (
    "opportunityid",
    "name",
    "estimatedclosedate",
    "estimatedvalue",
    "_customerid_value",
    "_parentcontactid_value",
    "_parentaccountid_value",
    "closeprobability",
    "opportunityratingcode",
)


class OpportunityClient:
    """Typed client for the `opportunities` entity set."""

    def __init__(self, dataverse_url: str, *, http: httpx.AsyncClient) -> None:
        self._base = dataverse_url.rstrip("/")
        self._api = f"{self._base}/api/data/{_API_VERSION}"
        self._http = http

    async def list_opportunities(
        self,
        *,
        token: str,
        filter: str | None = None,
        top: int | None = None,
        orderby: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"$select": ",".join(_DEFAULT_SELECT_FIELDS)}
        if filter is not None:
            params["$filter"] = filter
        if top is not None:
            params["$top"] = top
        if orderby is not None:
            params["$orderby"] = orderby

        response = await self._http.get(
            f"{self._api}/opportunities",
            headers=_headers(token),
            params=params,
        )
        response.raise_for_status()
        return [_format_opportunity(row) for row in response.json().get("value", [])]

    async def get_opportunity(
        self,
        *,
        token: str,
        opportunity_id: str,
    ) -> dict[str, Any]:
        """Fetch a single opportunity by GUID and return the public shape."""
        response = await self._http.get(
            f"{self._api}/opportunities({opportunity_id})",
            headers=_headers(token),
            params={"$select": ",".join(_DEFAULT_SELECT_FIELDS)},
        )
        response.raise_for_status()
        return _format_opportunity(response.json())

    async def create_opportunity(
        self,
        *,
        token: str,
        name: str,
        customer_id: str,
        customer_type: str,
        estimated_value: float | None = None,
        estimated_close_date: str | None = None,
        probability: int | None = None,
        rating: int | None = None,
    ) -> str:
        """Create an Opportunity and return its newly-minted GUID.

        `customer_type` must be 'account' or 'contact'; emits the correct
        polymorphic `customerid_<type>@odata.bind` field.
        """
        entity_set = _CUSTOMER_TYPES.get(customer_type.lower())
        if entity_set is None:
            raise ValueError(
                f"customer_type={customer_type!r} is not supported. "
                f"Expected one of: {sorted(_CUSTOMER_TYPES)} "
                "(Dataverse's polymorphic Customer field points at accounts or contacts)."
            )

        body: dict[str, Any] = {
            "name": name,
            f"customerid_{customer_type.lower()}@odata.bind": f"/{entity_set}({customer_id})",
        }
        if estimated_value is not None:
            body["estimatedvalue"] = estimated_value
        if estimated_close_date is not None:
            body["estimatedclosedate"] = estimated_close_date
        if probability is not None:
            body["closeprobability"] = probability
        if rating is not None:
            body["opportunityratingcode"] = rating

        response = await self._http.post(
            f"{self._api}/opportunities",
            headers=_headers(token),
            json=body,
        )
        response.raise_for_status()
        # Dataverse returns the new record's URI in OData-EntityId:
        # .../opportunities(<guid>)
        entity_id = response.headers.get("OData-EntityId", "")
        return entity_id.rstrip(")").rsplit("(", 1)[-1]

    async def update_opportunity(
        self,
        *,
        token: str,
        opportunity_id: str,
        name: str | None = None,
        estimated_value: float | None = None,
        estimated_close_date: str | None = None,
        probability: int | None = None,
        rating: int | None = None,
    ) -> None:
        """PATCH an opportunity with only the fields the caller set.

        Dataverse interprets missing fields as "leave untouched"; supplying
        `None` at the Python level therefore never clears a Dataverse field.
        To explicitly clear a field the caller needs a dedicated method; not
        in scope for this slice.
        """
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if estimated_value is not None:
            body["estimatedvalue"] = estimated_value
        if estimated_close_date is not None:
            body["estimatedclosedate"] = estimated_close_date
        if probability is not None:
            body["closeprobability"] = probability
        if rating is not None:
            body["opportunityratingcode"] = rating

        response = await self._http.patch(
            f"{self._api}/opportunities({opportunity_id})",
            headers=_headers(token),
            json=body,
        )
        response.raise_for_status()

    async def delete_opportunity(
        self,
        *,
        token: str,
        opportunity_id: str,
    ) -> None:
        response = await self._http.delete(
            f"{self._api}/opportunities({opportunity_id})",
            headers=_headers(token),
        )
        response.raise_for_status()

    async def search_accounts(
        self,
        *,
        token: str,
        query: str,
        top: int = 5,
    ) -> list[dict[str, str]]:
        """Search accounts by display name (`contains(name, ...)`).

        Returns `[{id, name}, ...]`. Empty list means no match (not an error).
        """
        return await self._search_lookup(
            entity="accounts",
            name_field="name",
            id_field="accountid",
            token=token,
            query=query,
            top=top,
        )

    async def search_contacts(
        self,
        *,
        token: str,
        query: str,
        top: int = 5,
    ) -> list[dict[str, str]]:
        """Search contacts by `fullname` (Dataverse's composite first+last)."""
        return await self._search_lookup(
            entity="contacts",
            name_field="fullname",
            id_field="contactid",
            token=token,
            query=query,
            top=top,
        )

    async def _search_lookup(
        self,
        *,
        entity: str,
        name_field: str,
        id_field: str,
        token: str,
        query: str,
        top: int,
    ) -> list[dict[str, str]]:
        escaped = query.replace("'", "''")
        response = await self._http.get(
            f"{self._api}/{entity}",
            headers=_headers(token),
            params={
                "$select": f"{id_field},{name_field}",
                "$filter": f"contains({name_field}, '{escaped}')",
                "$top": top,
            },
        )
        response.raise_for_status()
        return [
            {"id": row[id_field], "name": row[name_field]}
            for row in response.json().get("value", [])
        ]


_RATING_BY_CODE = {1: "Hot", 2: "Warm", 3: "Cold"}


def _format_opportunity(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw Dataverse record into the agent-facing public shape."""
    return {
        "id": raw.get("opportunityid", ""),
        "topic": raw.get("name", ""),
        "potential_customer": _fv(raw, "_customerid_value"),
        "est_close_date": raw.get("estimatedclosedate", ""),
        "est_revenue": raw.get("estimatedvalue"),
        "contact": _fv(raw, "_parentcontactid_value"),
        "account": _fv(raw, "_parentaccountid_value"),
        "probability": raw.get("closeprobability"),
        "rating": raw.get(f"opportunityratingcode@{_FV_ANNOTATION}")
        or _RATING_BY_CODE.get(raw.get("opportunityratingcode"), ""),
    }


def _fv(raw: dict[str, Any], field: str) -> str:
    formatted = raw.get(f"{field}@{_FV_ANNOTATION}")
    if formatted:
        return formatted
    value = raw.get(field)
    return "" if value is None else str(value)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
        "Prefer": f"odata.include-annotations={_FV_ANNOTATION}",
    }
