"""Conftest for the live-integration test layer (ADR 0007).

Loads credentials from the existing `skills/crm-opportunity/.env` (AZURE_* +
DATAVERSE_URL) and the root `.env` (FOUNDRY_*). Auto-skips every test in
`tests/integration/` when required variables are absent so contributors
without a dev tenant can still run the unit layer.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL_ENV = _REPO_ROOT / "skills" / "crm-opportunity" / ".env"
_ROOT_ENV = _REPO_ROOT / ".env"

# Load both env files in order; skill .env carries Dataverse creds, root .env
# carries Foundry. Existing env vars win (CI sets them from GitHub secrets
# rather than using these files).
for _env_file in (_SKILL_ENV, _ROOT_ENV):
    if _env_file.is_file():
        load_dotenv(_env_file, override=False)

# The Dataverse AAD app registration IS the AAD app used for OBO; our config
# module reads AAD_APP_* while the legacy demo's .env uses AZURE_*.
os.environ.setdefault("AAD_APP_CLIENT_ID", os.environ.get("AZURE_CLIENT_ID", ""))
os.environ.setdefault("AAD_APP_TENANT_ID", os.environ.get("AZURE_TENANT_ID", ""))
# Live tests drive the dev path — WIF is not yet provisioned in this tenant.
os.environ.setdefault("AUTH_MODE", "app_only_secret")
os.environ.setdefault("CLOUD_ENV", "global")

_REQUIRED = (
    "DATAVERSE_URL",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
)


def _missing_env() -> list[str]:
    return [name for name in _REQUIRED if not os.environ.get(name)]


def pytest_collection_modifyitems(config, items):
    missing = _missing_env()
    if not missing:
        return
    skip = pytest.mark.skip(
        reason=(
            "live integration tests require credentials in "
            "skills/crm-opportunity/.env or in GitHub secrets; "
            f"missing: {missing}"
        )
    )
    for item in items:
        if "tests/integration" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)
