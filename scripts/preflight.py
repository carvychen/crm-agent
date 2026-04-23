"""Preflight validation — the "delivered blind" gate (Invariant 4).

Run before any deployment (dev, UAT, prod) to catch the common
misconfiguration failure modes:

    python scripts/preflight.py                # human-readable output
    python scripts/preflight.py --format json  # machine-parseable

Exit 0 = all checks pass or skip. Exit 1 = at least one check failed; the
remediation line of each failure names the concrete next step.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Expose src/ on sys.path so imports line up with the Function App runtime.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Load .env files so the operator can run preflight locally without manually
# exporting every variable. Values already in the process env win; CI sets
# everything explicitly via secrets and no .env file exists there.
try:
    from dotenv import load_dotenv

    for _env in (
        _REPO_ROOT / "skills" / "crm-opportunity" / ".env",
        _REPO_ROOT / ".env",
    ):
        if _env.is_file():
            load_dotenv(_env, override=False)
except ImportError:
    pass

# Map legacy demo credential names onto what src/config.py expects.
os.environ.setdefault("AAD_APP_CLIENT_ID", os.environ.get("AZURE_CLIENT_ID", ""))
os.environ.setdefault("AAD_APP_TENANT_ID", os.environ.get("AZURE_TENANT_ID", ""))

import httpx  # noqa: E402

from config import get_config  # noqa: E402
from preflight.checks import (  # noqa: E402
    DnsReachabilityCheck,
    FoundryReachabilityCheck,
    TokenAcquisitionCheck,
    WhoAmICheck,
)
from preflight.core import (  # noqa: E402
    exit_code_for,
    render_human,
    render_json,
    run_checks,
)


def _agent_enabled() -> bool:
    return os.environ.get("ENABLE_REFERENCE_AGENT", "true").strip().lower() != "false"


def _build_foundry_credential():
    """Reuse the integration-test dual-mode credential resolver.

    Local dev: `az login` into the Foundry tenant; CI: dedicated SP via
    FOUNDRY_AZURE_{TENANT,CLIENT,SECRET}_*. See tests/integration/*.
    """
    cid = os.environ.get("FOUNDRY_AZURE_CLIENT_ID")
    csecret = os.environ.get("FOUNDRY_AZURE_CLIENT_SECRET")
    ctenant = os.environ.get("FOUNDRY_AZURE_TENANT_ID")
    if cid and csecret and ctenant:
        from azure.identity import ClientSecretCredential

        return ClientSecretCredential(
            tenant_id=ctenant, client_id=cid, client_secret=csecret
        )
    from azure.identity import AzureCliCredential

    return AzureCliCredential()


async def _build_checks(http: httpx.AsyncClient) -> list:
    config = get_config()

    dns_hosts = [
        config.authority.removeprefix("https://").removeprefix("http://"),
        config.dataverse_url.removeprefix("https://").removeprefix("http://"),
    ]
    if _agent_enabled() and os.environ.get("FOUNDRY_PROJECT_ENDPOINT"):
        dns_hosts.append(
            os.environ["FOUNDRY_PROJECT_ENDPOINT"]
            .removeprefix("https://")
            .removeprefix("http://")
            .split("/", 1)[0]
        )

    client_secret = os.environ.get("AZURE_CLIENT_SECRET")
    token_check = TokenAcquisitionCheck(
        authority=config.authority,
        tenant_id=config.aad_app_tenant_id,
        client_id=config.aad_app_client_id,
        client_secret=client_secret or "",
        dataverse_url=config.dataverse_url,
        http=http,
    )

    # We can only compose the WhoAmI check after a successful token — resolve
    # that lazily by running the token check first and handing its token in.
    checks: list = [DnsReachabilityCheck(hosts=dns_hosts), token_check]

    async def _run_and_continue() -> list:
        dns_result = await checks[0].run()
        token_result = await token_check.run()
        chain: list = [
            _ConstantResult(dns_result),
            _ConstantResult(token_result),
        ]
        # Feed the token (if any) into WhoAmI.
        token_value = ""
        if token_result.status == "pass" and client_secret:
            # Re-acquire to get the raw token — TokenAcquisitionCheck does
            # not return it (by design) so we reissue here from the same
            # HTTP client. The second request hits the same MSAL edge and
            # is essentially free (token endpoint caches on its side).
            response = await http.post(
                f"{config.authority}/{config.aad_app_tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": config.aad_app_client_id,
                    "client_secret": client_secret,
                    "scope": f"{config.dataverse_url}/.default",
                },
            )
            if response.status_code == 200:
                token_value = response.json().get("access_token", "")
        chain.append(
            WhoAmICheck(
                dataverse_url=config.dataverse_url, token=token_value, http=http
            )
        )
        chain.append(
            FoundryReachabilityCheck(
                agent_enabled=_agent_enabled(),
                project_endpoint=os.environ.get("FOUNDRY_PROJECT_ENDPOINT"),
                model=os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini"),
                credential_factory=_build_foundry_credential,
            )
        )
        return chain

    return await _run_and_continue()


class _ConstantResult:
    """Wrap an already-computed CheckResult as a Check (trivially replaying)."""

    def __init__(self, result):
        self.name = result.name
        self._result = result

    async def run(self):
        return self._result


async def _amain(output_format: str) -> int:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        checks = await _build_checks(http)
        results = await run_checks(checks)

    if output_format == "json":
        print(render_json(results))
    else:
        print(render_human(results))
    return exit_code_for(results)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that the MCP server and reference agent can reach every "
            "external dependency they need before deploying."
        )
    )
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="Output format (default: human; json is for CI parsing).",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_amain(args.format)))


if __name__ == "__main__":
    main()
