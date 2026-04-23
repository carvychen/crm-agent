"""Cloud-neutral configuration resolved from environment variables.

The `CLOUD_ENV` switch selects the set of cloud-specific endpoints, authorities,
and FIC audiences at runtime (ADR 0003 / ADR 0007). Both `global` and `china`
branches are first-class; the `china` branch has no live tenant access from
our side (Invariant 4) so its verification leans on parametric unit tests,
source-literal lint, Bicep what-if, HITL review, and the customer's own
pre-flight script at UAT.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


class UnsupportedCloudError(ValueError):
    """Raised when CLOUD_ENV is set to a value this build does not support."""


@dataclass(frozen=True)
class CloudConfig:
    authority: str
    dataverse_url: str
    fic_audience: str
    aad_app_client_id: str
    aad_app_tenant_id: str
    managed_identity_client_id: str | None = None


_CLOUD_DEFAULTS: dict[str, dict[str, str]] = {
    # Azure Global / Public cloud.
    "global": {
        "authority": "https://login.microsoftonline.com",
        "fic_audience": "api://AzureADTokenExchange",
    },
    # Azure China / 21Vianet. Authority and FIC audience per Microsoft Learn:
    # https://learn.microsoft.com/azure/china/resources-developer-guide
    # https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/how-to-configure-workload-identity-federation-other-cloud#configure-a-federated-identity-credential-on-an-app
    "china": {
        "authority": "https://login.partner.microsoftonline.cn",
        "fic_audience": "api://AzureADTokenExchangeChina",
    },
}


def get_config() -> CloudConfig:
    cloud = os.environ.get("CLOUD_ENV", "global").strip().lower()
    if cloud not in _CLOUD_DEFAULTS:
        valid = ", ".join(f"'{c}'" for c in _CLOUD_DEFAULTS)
        raise UnsupportedCloudError(
            f"CLOUD_ENV={cloud!r} is not supported in this build. "
            f"Expected one of: {valid}. "
            "Set CLOUD_ENV=global for Azure Public / dev; "
            "set CLOUD_ENV=china for Azure China / 21Vianet (Lenovo production)."
        )
    cloud_defaults = _CLOUD_DEFAULTS[cloud]

    return CloudConfig(
        authority=cloud_defaults["authority"],
        fic_audience=cloud_defaults["fic_audience"],
        dataverse_url=_require("DATAVERSE_URL"),
        aad_app_client_id=_require("AAD_APP_CLIENT_ID"),
        aad_app_tenant_id=_require("AAD_APP_TENANT_ID"),
        managed_identity_client_id=os.environ.get("MANAGED_IDENTITY_CLIENT_ID") or None,
    )


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value
