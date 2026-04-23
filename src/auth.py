"""OBO-over-WIF: exchange an incoming user JWT for a Dataverse-scoped token.

See docs/adr/0001-obo-with-wif.md. The Managed Identity hosting the service mints
a FIC-audience assertion; that assertion stands in for a client secret in the
OAuth 2.0 On-Behalf-Of request to Entra ID. Every Dataverse call therefore runs
under the real end-user identity and Dataverse RLS applies naturally.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import httpx

from config import CloudConfig

MiTokenProvider = Callable[[], str]
Clock = Callable[[], datetime]

_JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


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
        response.raise_for_status()
        body = response.json()
        token: str = body["access_token"]
        expires_in = int(body.get("expires_in", 3600))
        # Refresh 60 s before real expiry to avoid end-of-window requests.
        self._cache[user_jwt] = _Cached(
            token=token,
            expires_at=now + timedelta(seconds=max(expires_in - 60, 0)),
        )
        return token
