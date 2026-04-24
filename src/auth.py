"""Exchange an incoming user JWT (or app credentials) for a Dataverse-scoped token.

Production path (ADR 0001 / AUTH_MODE=obo): Managed Identity mints a
FIC-audience assertion that stands in for a client secret in the OAuth 2.0
On-Behalf-Of request; every Dataverse call therefore runs under the real
end-user identity and Dataverse RLS applies naturally.

Dev/test path (ADR 0007 / AUTH_MODE=app_only_secret): `client_credentials`
flow using AZURE_CLIENT_ID/SECRET. Used by the live-integration test suite in
the author's dev tenant while WIF is not yet provisioned. Rejected in
production by `function_app.py`'s startup assertion.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Protocol

import httpx

from config import CloudConfig

MiTokenProvider = Callable[[], str]
Clock = Callable[[], datetime]

_JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_SUPPORTED_AUTH_MODES = ("obo", "app_only_secret")


class UnsupportedAuthModeError(ValueError):
    """Raised when AUTH_MODE is set to a value this build does not support."""


class _AuthLike(Protocol):
    async def get_dataverse_token(self, user_jwt: str) -> str: ...


@dataclass(frozen=True)
class _Cached:
    token: str
    expires_at: datetime


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


class DataverseAuth:
    """Mint Dataverse-scoped access tokens via OBO using a WIF assertion."""

    def __init__(
        self,
        config: CloudConfig,
        *,
        http: httpx.AsyncClient,
        mi_token_provider: MiTokenProvider,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._http = http
        self._mi_token_provider = mi_token_provider
        self._clock = clock or _default_clock
        self._cache: dict[str, _Cached] = {}

    async def get_dataverse_token(self, user_jwt: str) -> str:
        now = self._clock()
        cached = self._cache.get(user_jwt)
        if cached is not None and cached.expires_at > now:
            return cached.token

        cfg = self._config
        mi_token = self._mi_token_provider()
        response = await self._http.post(
            f"{cfg.authority}/{cfg.aad_app_tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": _JWT_BEARER,
                "client_id": cfg.aad_app_client_id,
                "client_assertion_type": _ASSERTION_TYPE,
                "client_assertion": mi_token,
                "assertion": user_jwt,
                "scope": f"{cfg.dataverse_url}/.default",
                "requested_token_use": "on_behalf_of",
            },
        )
        # Surface Entra's `error` / `error_description` / AADSTS code in the
        # exception message — httpx's default raise_for_status() body only
        # shows the URL, which makes OBO misconfiguration (FIC audience,
        # missing admin-consent, tenant mismatch) expensive to diagnose.
        if response.is_error:
            raise httpx.HTTPStatusError(
                f"OBO exchange failed: {response.status_code} {response.text}",
                request=response.request,
                response=response,
            )
        body = response.json()
        token: str = body["access_token"]
        expires_in = int(body.get("expires_in", 3600))
        # Refresh 60 s before real expiry to avoid end-of-window requests.
        self._cache[user_jwt] = _Cached(
            token=token,
            expires_at=now + timedelta(seconds=max(expires_in - 60, 0)),
        )
        return token


class ClientSecretDataverseAuth:
    """Dev/test path (ADR 0007): app-only Dataverse token via client_credentials.

    Ignores the inbound user JWT; every call returns the same service-account
    token. Use ONLY when AUTH_MODE=app_only_secret. Dataverse RLS does not
    filter per-user on this path — verification of per-user behaviour requires
    WIF + the OBO path (see DataverseAuth + Slice 8 preflight).
    """

    def __init__(
        self,
        config: CloudConfig,
        *,
        http: httpx.AsyncClient,
        client_secret: str,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._http = http
        self._client_secret = client_secret
        self._clock = clock or _default_clock
        self._cache: _Cached | None = None

    async def get_dataverse_token(self, user_jwt: str) -> str:  # noqa: ARG002 — parity with OBO path
        now = self._clock()
        if self._cache is not None and self._cache.expires_at > now:
            return self._cache.token

        cfg = self._config
        response = await self._http.post(
            f"{cfg.authority}/{cfg.aad_app_tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": cfg.aad_app_client_id,
                "client_secret": self._client_secret,
                "scope": f"{cfg.dataverse_url}/.default",
            },
        )
        response.raise_for_status()
        body = response.json()
        token: str = body["access_token"]
        expires_in = int(body.get("expires_in", 3600))
        self._cache = _Cached(
            token=token,
            expires_at=now + timedelta(seconds=max(expires_in - 60, 0)),
        )
        return token


def build_auth(
    config: CloudConfig,
    *,
    http: httpx.AsyncClient,
    mi_token_provider: MiTokenProvider,
) -> _AuthLike:
    """Pick the auth implementation per AUTH_MODE. Fails loudly on unknown values.

    Default mode is `obo` (production, ADR 0001). `app_only_secret` is the dev
    / test path described in ADR 0007 and refuses to silently degrade: the
    required `AZURE_CLIENT_SECRET` must be set or the call raises.
    """
    mode = os.environ.get("AUTH_MODE", "obo").strip().lower()
    if mode == "obo":
        return DataverseAuth(config, http=http, mi_token_provider=mi_token_provider)
    if mode == "app_only_secret":
        secret = os.environ.get("AZURE_CLIENT_SECRET")
        if not secret:
            raise EnvironmentError(
                "AUTH_MODE=app_only_secret requires AZURE_CLIENT_SECRET to be set "
                "(typically loaded from skills/crm-opportunity/.env during local "
                "runs or from GitHub secrets in CI)."
            )
        return ClientSecretDataverseAuth(config, http=http, client_secret=secret)
    raise UnsupportedAuthModeError(
        f"AUTH_MODE={mode!r} is not supported. "
        f"Expected one of: {_SUPPORTED_AUTH_MODES}."
    )
