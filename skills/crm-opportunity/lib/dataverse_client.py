"""
Dataverse Web API client for Dynamics 365 CRM — Opportunity entity.

Fields used (matching the CRM list view):
  name                   Topic              (required)
  customerid@odata.bind  Potential Customer (required) — "/accounts({guid})" or "/contacts({guid})"
  estimatedclosedate     Est. Close Date    "YYYY-MM-DD"
  estimatedvalue         Est. Revenue       float
  _parentcontactid_value Contact GUID       (read-only; write via parentcontactid@odata.bind)
  _parentaccountid_value Account GUID       (read-only; write via parentaccountid@odata.bind)
  closeprobability       Probability        int 0–100
  opportunityratingcode  Rating             1=Hot, 2=Warm, 3=Cold
"""

import functools
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urljoin

import requests
from azure.identity import ClientSecretCredential

logger = logging.getLogger(__name__)

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def is_guid(value: str) -> bool:
    """Return True if *value* looks like a Dataverse GUID."""
    return bool(_GUID_RE.match(value.strip()))


def _odata_escape(value: str) -> str:
    """Escape single quotes for OData string literals."""
    return value.replace("'", "''")


def safe_script(func):
    """Decorator: catch exceptions in skill scripts and return error JSON."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.HTTPError as e:
            logger.exception("Dataverse API error in %s", func.__name__)
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        except Exception as e:
            logger.exception("Unexpected error in %s", func.__name__)
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    return wrapper


class DataverseClient:
    """Base authenticated client for Dataverse Web API."""

    API_VERSION = "v9.2"

    def __init__(
        self,
        dataverse_url: str,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ):
        self.base_url = dataverse_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/data/{self.API_VERSION}/"
        self._credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        self._scope = f"{self.base_url}/.default"

    def _get_headers(self) -> dict[str, str]:
        token = self._credential.get_token(self._scope).token
        return {
            "Authorization": f"Bearer {token}",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Prefer": "odata.include-annotations=OData.Community.Display.V1.FormattedValue",
        }

    def _url(self, entity: str, record_id: str | None = None) -> str:
        if record_id:
            return urljoin(self.api_base, f"{entity}({record_id})")
        return urljoin(self.api_base, entity)

    def _raise_for_status(self, response: requests.Response) -> None:
        if not response.ok:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise requests.HTTPError(
                f"[{response.status_code}] {response.url}\n{detail}",
                response=response,
            )

    def search_accounts(self, name: str, top: int = 10) -> list[dict[str, str]]:
        """Search accounts by name. Returns list of {id, name}."""
        response = requests.get(
            self._url("accounts"),
            headers=self._get_headers(),
            params={"$select": "accountid,name", "$filter": f"contains(name, '{_odata_escape(name)}')", "$top": top},
        )
        self._raise_for_status(response)
        return [{"id": a["accountid"], "name": a["name"]} for a in response.json().get("value", [])]

    def resolve_account_id(self, value: str) -> str:
        """Accept a GUID or account name; return a GUID."""
        if is_guid(value):
            return value.strip()
        accounts = self.search_accounts(value, top=5)
        if len(accounts) == 1:
            return accounts[0]["id"]
        if not accounts:
            raise ValueError(f"No account found matching '{value}'")
        names = [a["name"] for a in accounts]
        raise ValueError(f"Multiple accounts match '{value}': {names}. Please specify which one.")

    def search_contacts(self, name: str, top: int = 10) -> list[dict[str, str]]:
        """Search contacts by name. Returns list of {id, name}."""
        response = requests.get(
            self._url("contacts"),
            headers=self._get_headers(),
            params={"$select": "contactid,fullname", "$filter": f"contains(fullname, '{_odata_escape(name)}')", "$top": top},
        )
        self._raise_for_status(response)
        return [{"id": c["contactid"], "name": c["fullname"]} for c in response.json().get("value", [])]

    def resolve_contact_id(self, value: str) -> str:
        """Accept a GUID or contact name; return a GUID."""
        if is_guid(value):
            return value.strip()
        contacts = self.search_contacts(value, top=5)
        if len(contacts) == 1:
            return contacts[0]["id"]
        if not contacts:
            raise ValueError(f"No contact found matching '{value}'")
        names = [c["name"] for c in contacts]
        raise ValueError(f"Multiple contacts match '{value}': {names}. Please specify which one.")


class OpportunityClient(DataverseClient):
    """CRUD operations for the Opportunity entity in Dynamics 365."""

    ENTITY = "opportunities"

    # Fields matching the CRM list view columns
    DEFAULT_SELECT = ",".join([
        "opportunityid",
        "name",
        "estimatedclosedate",
        "estimatedvalue",
        "_customerid_value",
        "_parentcontactid_value",
        "_parentaccountid_value",
        "closeprobability",
        "opportunityratingcode",
    ])

    RATING = {1: "Hot", 2: "Warm", 3: "Cold"}

    # Formatted-value annotation key
    _FV = "OData.Community.Display.V1.FormattedValue"

    @classmethod
    def format_opportunity(cls, opp: dict) -> dict:
        """Convert a raw Dataverse opportunity record to a human-friendly dict."""
        fv = cls._FV
        return {
            "id": opp.get("opportunityid", ""),
            "topic": opp.get("name", ""),
            "potential_customer": opp.get(f"_customerid_value@{fv}") or opp.get("_customerid_value", ""),
            "est_close_date": opp.get("estimatedclosedate", ""),
            "est_revenue": opp.get("estimatedvalue"),
            "contact": opp.get(f"_parentcontactid_value@{fv}") or opp.get("_parentcontactid_value", ""),
            "account": opp.get(f"_parentaccountid_value@{fv}") or opp.get("_parentaccountid_value", ""),
            "probability": opp.get("closeprobability"),
            "rating": opp.get(f"opportunityratingcode@{fv}") or cls.RATING.get(opp.get("opportunityratingcode"), ""),
        }

    def list(
        self,
        select: str | None = None,
        filter_expr: str | None = None,
        order_by: str | None = None,
        top: int | None = None,
        expand: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve opportunities.

        Args:
            select:      OData $select (defaults to DEFAULT_SELECT).
            filter_expr: OData $filter, e.g. "estimatedvalue gt 50000",
                         "opportunityratingcode eq 1", "contains(name, 'SKU')".
            order_by:    OData $orderby, e.g. "estimatedvalue desc".
            top:         Max records to return.
            expand:      OData $expand, e.g. "parentaccountid($select=name)"

        Returns:
            List of opportunity dicts.
        """
        params: dict[str, Any] = {"$select": select or self.DEFAULT_SELECT}
        if filter_expr:
            params["$filter"] = filter_expr
        if order_by:
            params["$orderby"] = order_by
        if top:
            params["$top"] = top
        if expand:
            params["$expand"] = expand

        response = requests.get(
            self._url(self.ENTITY),
            headers=self._get_headers(),
            params=params,
        )
        self._raise_for_status(response)
        return response.json().get("value", [])

    def get(self, opportunity_id: str, select: str | None = None) -> dict[str, Any]:
        """
        Retrieve a single opportunity by GUID.

        Args:
            opportunity_id: GUID of the opportunity.
            select:         OData $select (defaults to DEFAULT_SELECT).

        Returns:
            Opportunity dict.
        """
        params = {"$select": select or self.DEFAULT_SELECT}
        response = requests.get(
            self._url(self.ENTITY, opportunity_id),
            headers=self._get_headers(),
            params=params,
        )
        self._raise_for_status(response)
        return response.json()

    def create(self, data: dict[str, Any]) -> str:
        """
        Create a new opportunity.

        Args:
            data: Field values. Required + supported fields:
                name (str)                               Topic (required)
                customerid_account@odata.bind (str)      Potential Customer = Account (use this OR contact below)
                customerid_contact@odata.bind (str)      Potential Customer = Contact
                estimatedclosedate (str)                 "YYYY-MM-DD"
                estimatedvalue (float)                   Est. Revenue
                parentaccountid@odata.bind (str)         "/accounts({guid})"
                parentcontactid@odata.bind (str)         "/contacts({guid})"
                closeprobability (int)                   0–100
                opportunityratingcode (int)              1=Hot, 2=Warm, 3=Cold

        Note: customerid is a polymorphic "Customer" field. Use
              customerid_account@odata.bind for accounts,
              customerid_contact@odata.bind for contacts.

        Returns:
            GUID of the newly created opportunity.
        """
        response = requests.post(
            self._url(self.ENTITY),
            headers=self._get_headers(),
            json=data,
        )
        self._raise_for_status(response)
        # New record URI in OData-EntityId header: .../opportunities(guid)
        entity_id_header = response.headers.get("OData-EntityId", "")
        return entity_id_header.rstrip(")").split("(")[-1]

    def update(self, opportunity_id: str, data: dict[str, Any]) -> None:
        """
        Partially update an opportunity (PATCH — only supplied fields change).

        Args:
            opportunity_id: GUID of the opportunity.
            data:           Fields to update (same names as create). Only supplied fields change.
        """
        response = requests.patch(
            self._url(self.ENTITY, opportunity_id),
            headers=self._get_headers(),
            json=data,
        )
        self._raise_for_status(response)

    def delete(self, opportunity_id: str) -> None:
        """
        Delete an opportunity permanently.

        Args:
            opportunity_id: GUID of the opportunity.
        """
        response = requests.delete(
            self._url(self.ENTITY, opportunity_id),
            headers=self._get_headers(),
        )
        self._raise_for_status(response)

    def win(self, opportunity_id: str, actual_revenue: float | None = None) -> None:
        """
        Close an opportunity as Won via the WinOpportunity action.

        Args:
            opportunity_id: GUID of the opportunity.
            actual_revenue: Optional actual revenue to record on close.
        """
        close_data: dict[str, Any] = {
            "subject": "Opportunity Won",
            "opportunityid@odata.bind": f"/opportunities({opportunity_id})",
        }
        if actual_revenue is not None:
            close_data["actualrevenue"] = actual_revenue

        response = requests.post(
            f"{self.base_url}/api/data/{self.API_VERSION}/WinOpportunity",
            headers=self._get_headers(),
            json={"Status": 3, "OpportunityClose": close_data},
        )
        self._raise_for_status(response)

    def lose(self, opportunity_id: str, reason: str | None = None) -> None:
        """
        Close an opportunity as Lost via the LoseOpportunity action.

        Args:
            opportunity_id: GUID of the opportunity.
            reason:         Optional description for the OpportunityClose activity.
        """
        close_data: dict[str, Any] = {
            "subject": reason or "Opportunity Lost",
            "opportunityid@odata.bind": f"/opportunities({opportunity_id})",
        }
        response = requests.post(
            f"{self.base_url}/api/data/{self.API_VERSION}/LoseOpportunity",
            headers=self._get_headers(),
            json={"Status": 4, "OpportunityClose": close_data},
        )
        self._raise_for_status(response)


_client: OpportunityClient | None = None


def get_client() -> OpportunityClient:
    """
    Return a shared OpportunityClient, lazily created on first call.

    This keeps a single ClientSecretCredential alive so its internal
    token cache is reused across all script invocations.

    Required env vars:
        DATAVERSE_URL        e.g. https://org7339c4fb.crm.dynamics.com
        AZURE_TENANT_ID
        AZURE_CLIENT_ID
        AZURE_CLIENT_SECRET
    """
    global _client
    if _client is not None:
        return _client

    missing = [
        v for v in ("DATAVERSE_URL", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
        if not os.environ.get(v)
    ]
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

    _client = OpportunityClient(
        dataverse_url=os.environ["DATAVERSE_URL"],
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
    )
    return _client
