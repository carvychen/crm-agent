"""Cloud-neutral configuration resolved from environment variables.

The `CLOUD_ENV` switch selects the set of cloud-specific endpoints, authorities,
and FIC audiences at runtime (see docs/adr/0003-dual-cloud-parity.md).
Only the `global` branch is implemented in Slice 1; the `china` branch lands
in Slice 5 (#7).
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


_GLOBAL = {
    "authority": "https://login.microsoftonline.com",
    "fic_audience": "api://AzureADTokenExchange",
}


def get_config() -> CloudConfig:
    cloud = os.environ.get("CLOUD_ENV", "global").strip().lower()
    if cloud == "global":
        cloud_defaults = _GLOBAL
    else:
        raise UnsupportedCloudError(
            f"CLOUD_ENV={cloud!r} is not supported in this build. "
            "Expected one of: 'global'. (China support lands in Slice 5.)"
        )

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
