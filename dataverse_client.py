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

import os
from typing import Any
from urllib.parse import urljoin

import requests
from azure.identity import ClientSecretCredential


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


def build_client_from_env() -> OpportunityClient:
    """
    Construct an OpportunityClient from environment variables.

    Required env vars:
        DATAVERSE_URL        e.g. https://org7339c4fb.crm.dynamics.com
        AZURE_TENANT_ID
        AZURE_CLIENT_ID
        AZURE_CLIENT_SECRET
    """
    missing = [
        v for v in ("DATAVERSE_URL", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
        if not os.environ.get(v)
    ]
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

    return OpportunityClient(
        dataverse_url=os.environ["DATAVERSE_URL"],
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
    )
