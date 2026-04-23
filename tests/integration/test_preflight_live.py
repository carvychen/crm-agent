"""Live preflight: run scripts/preflight.py as a subprocess against the
author's Azure Global dev tenant and assert exit 0 + machine-parseable JSON.

This IS the test that closes the loop on ADR 0007's delivery-constrained
clause for `CLOUD_ENV=china`: our preflight is the artifact the customer
executes in their own Azure China tenant. If the program can't run green
against OUR tenant, there's no reason to believe it will help THEM.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_preflight_script_exits_zero_with_all_checks_passing_or_skipping():
    """Boot the script fresh (no shared Python state with pytest) and parse
    its JSON output. Every check must be pass or skip — a fail here means
    the dev tenant has drifted and the PR should not merge."""
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "preflight.py"

    # Inherit the pytest process env so AUTH_MODE / FOUNDRY_* / etc. that
    # conftest.py already loaded from .env files carry through.
    env = os.environ.copy()

    result = subprocess.run(
        [sys.executable, str(script), "--format", "json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"preflight failed with code {result.returncode}:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )

    payload = json.loads(result.stdout)
    assert payload["exit_code"] == 0
    assert payload["summary"]["fail"] == 0

    # Every expected check surfaces with a pass or skip status.
    names = {r["name"] for r in payload["results"]}
    assert {
        "dns-reachability",
        "token-acquisition",
        "dataverse-whoami",
        "foundry-reachability",
    } <= names

    # Remediation must be empty for non-failing checks — non-empty remediation
    # on a passing check signals we left debug noise in the output.
    for r in payload["results"]:
        if r["status"] != "fail":
            assert r["remediation"] == "", (
                f"check {r['name']!r} is {r['status']} but has remediation: "
                f"{r['remediation']!r}"
            )
