"""Live Bicep: `az bicep build` must succeed for main.bicep and both
parameter files, on every PR. `az deployment group what-if` additionally
runs against a scratch resource group when `BICEP_WHATIF_RESOURCE_GROUP`
is set — gated so PRs without subscription access still run the build step.

Every combination below is exercised: {global, china} × {agent on, agent
off}. That's the test matrix ADR 0007's delivery-constrained clause asks
for — we cannot hit Azure China, but we CAN render its Bicep and catch
drift between the two parameter files at PR time.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
_INFRA = _REPO / "infra"
_MAIN = _INFRA / "main.bicep"

_AZ = shutil.which("az")


def _run_az(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_AZ or "az", *args],
        capture_output=True,
        text=True,
        timeout=180,
    )


def _require_az() -> None:
    if _AZ is None:
        pytest.skip("az CLI not on PATH")


def test_bicep_build_main_succeeds():
    """Schema + syntax validation for the root template. Fast (<5s), requires
    no Azure subscription — runs on every PR."""
    _require_az()
    result = _run_az("bicep", "build", "--file", str(_MAIN))
    assert result.returncode == 0, (
        f"az bicep build failed:\n--- stdout ---\n{result.stdout}"
        f"\n--- stderr ---\n{result.stderr}"
    )


@pytest.mark.parametrize(
    "parameters_file",
    ["parameters.global.json", "parameters.china.json"],
    ids=["global", "china"],
)
def test_bicep_parameter_files_parse_and_match_declared_params(parameters_file):
    """Every parameter in the per-cloud file corresponds to a declared
    parameter in main.bicep (and vice versa, modulo optional params).

    A parameter-file drift is the single most likely human error when adding
    a new cloud-specific setting — this catches it at PR time, well before
    `what-if` against a live RG would reject it.
    """
    _require_az()
    params_path = _INFRA / parameters_file
    assert params_path.is_file()
    params = json.loads(params_path.read_text())["parameters"]
    param_names = set(params)

    # Extract parameter names from compiled main.json (produced by the
    # previous test, or rebuild on the fly if it has gone stale).
    main_json = _INFRA / "main.json"
    if not main_json.is_file():
        subprocess.run(
            [_AZ or "az", "bicep", "build", "--file", str(_MAIN)], check=True
        )
    declared = set(json.loads(main_json.read_text()).get("parameters", {}).keys())

    unknown = param_names - declared
    assert not unknown, (
        f"{parameters_file} references parameters that don't exist in main.bicep: "
        f"{sorted(unknown)}"
    )


@pytest.mark.parametrize(
    "parameters_file",
    ["parameters.global.json", "parameters.china.json"],
    ids=["global", "china"],
)
@pytest.mark.parametrize("agent_enabled", [True, False], ids=["agent-on", "agent-off"])
def test_bicep_whatif_against_scratch_rg(parameters_file, agent_enabled):
    """Live what-if against a scratch RG in the author's subscription.

    Skipped unless `BICEP_WHATIF_RESOURCE_GROUP` is set — local runs use
    `export BICEP_WHATIF_RESOURCE_GROUP=crm-agent-ci-scratch`, CI uses a
    repo secret. what-if renders the template server-side and validates
    every resource shape without actually deploying.
    """
    _require_az()
    rg = os.environ.get("BICEP_WHATIF_RESOURCE_GROUP")
    if not rg:
        pytest.skip("BICEP_WHATIF_RESOURCE_GROUP not set — what-if gated")

    # Override enableReferenceAgent on the command line rather than editing
    # the parameter file, so the test parametrisation stays visible.
    agent_flag = "true" if agent_enabled else "false"
    result = _run_az(
        "deployment",
        "group",
        "what-if",
        "--resource-group",
        rg,
        "--template-file",
        str(_MAIN),
        "--parameters",
        str(_INFRA / parameters_file),
        "--parameters",
        f"enableReferenceAgent={agent_flag}",
        "--no-prompt",
        "true",
    )
    assert result.returncode == 0, (
        f"what-if failed for {parameters_file} agent_enabled={agent_flag}:\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
