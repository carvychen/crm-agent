"""Tests for src/config.py — cloud-neutral configuration."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "cloud_env,authority,fic_audience",
    [
        ("global", "https://login.microsoftonline.com", "api://AzureADTokenExchange"),
        ("china", "https://login.partner.microsoftonline.cn", "api://AzureADTokenExchangeChina"),
    ],
    ids=["global", "china"],
)
def test_cloud_env_resolves_to_expected_authority_and_fic_audience(
    monkeypatch, cloud_env, authority, fic_audience
):
    """Each supported cloud branch produces the documented Entra authority and
    FIC audience. Both branches carry equal weight in CI."""
    monkeypatch.setenv("CLOUD_ENV", cloud_env)
    monkeypatch.setenv("DATAVERSE_URL", "https://orgtest.crm.example")
    monkeypatch.setenv("AAD_APP_CLIENT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("AAD_APP_TENANT_ID", "22222222-2222-2222-2222-222222222222")

    from config import get_config

    cfg = get_config()

    assert cfg.authority == authority
    assert cfg.fic_audience == fic_audience
    assert cfg.dataverse_url == "https://orgtest.crm.example"
    assert cfg.aad_app_client_id == "11111111-1111-1111-1111-111111111111"
    assert cfg.aad_app_tenant_id == "22222222-2222-2222-2222-222222222222"


def test_unknown_cloud_env_raises_structured_error(monkeypatch):
    """Unknown CLOUD_ENV values must fail loudly with a message that names
    BOTH supported clouds so an operator immediately knows the valid set
    across Global and China deployments (US 22)."""
    monkeypatch.setenv("CLOUD_ENV", "mars")
    monkeypatch.setenv("DATAVERSE_URL", "https://orgtest.crm.example")
    monkeypatch.setenv("AAD_APP_CLIENT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("AAD_APP_TENANT_ID", "22222222-2222-2222-2222-222222222222")

    from config import UnsupportedCloudError, get_config

    with pytest.raises(UnsupportedCloudError) as excinfo:
        get_config()

    message = str(excinfo.value)
    assert "mars" in message
    assert "global" in message
    assert "china" in message
