"""Tests for src/config.py — cloud-neutral configuration."""
from __future__ import annotations

import pytest


def test_global_cloud_returns_entra_authority_and_fic_audience(monkeypatch):
    """CLOUD_ENV=global resolves to the Azure Global authority and FIC audience."""
    monkeypatch.setenv("CLOUD_ENV", "global")
    monkeypatch.setenv("DATAVERSE_URL", "https://orgtest.crm.dynamics.com")
    monkeypatch.setenv("AAD_APP_CLIENT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("AAD_APP_TENANT_ID", "22222222-2222-2222-2222-222222222222")

    from config import get_config

    cfg = get_config()

    assert cfg.authority == "https://login.microsoftonline.com"
    assert cfg.fic_audience == "api://AzureADTokenExchange"
    assert cfg.dataverse_url == "https://orgtest.crm.dynamics.com"
    assert cfg.aad_app_client_id == "11111111-1111-1111-1111-111111111111"
    assert cfg.aad_app_tenant_id == "22222222-2222-2222-2222-222222222222"


def test_unknown_cloud_env_raises_structured_error(monkeypatch):
    """Unknown CLOUD_ENV values must fail loudly with a message naming valid choices."""
    monkeypatch.setenv("CLOUD_ENV", "mars")
    monkeypatch.setenv("DATAVERSE_URL", "https://orgtest.crm.dynamics.com")
    monkeypatch.setenv("AAD_APP_CLIENT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("AAD_APP_TENANT_ID", "22222222-2222-2222-2222-222222222222")

    from config import UnsupportedCloudError, get_config

    with pytest.raises(UnsupportedCloudError) as excinfo:
        get_config()

    assert "mars" in str(excinfo.value)
    assert "global" in str(excinfo.value)
