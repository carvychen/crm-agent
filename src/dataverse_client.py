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
