"""Tests for the preflight runner framework (src/preflight/core.py)."""
from __future__ import annotations

import json

import pytest


def _scripted(name: str, status: str, detail: str = "", remediation: str = ""):
    """Build a fake Check that returns a pre-canned CheckResult."""
    from preflight.core import CheckResult

    class _Scripted:
        def __init__(self):
            self.name = name

        async def run(self) -> CheckResult:
            return CheckResult(
                name=name, status=status, detail=detail, remediation=remediation
            )

    return _Scripted()


async def test_runner_returns_all_pass_when_every_check_passes():
    from preflight.core import run_checks

    results = await run_checks(
        [
            _scripted("dns", "pass", detail="ok"),
            _scripted("token", "pass", detail="ok"),
        ]
    )
    assert [r.status for r in results] == ["pass", "pass"]
    assert [r.name for r in results] == ["dns", "token"]


async def test_runner_collects_mixed_results():
    from preflight.core import run_checks

    results = await run_checks(
        [
            _scripted("dns", "pass"),
            _scripted("token", "fail", detail="401", remediation="rotate secret"),
            _scripted("foundry", "skip", detail="agent disabled"),
        ]
    )
    statuses = {r.name: r.status for r in results}
    assert statuses == {"dns": "pass", "token": "fail", "foundry": "skip"}
    failing = next(r for r in results if r.status == "fail")
    assert failing.remediation == "rotate secret"


async def test_render_human_format_marks_each_check_with_status_icon():
    """Human output must be scannable by a platform engineer on the console."""
    from preflight.core import render_human, run_checks

    results = await run_checks(
        [
            _scripted("dns", "pass", detail="all hosts resolved"),
            _scripted(
                "token",
                "fail",
                detail="Entra returned 401",
                remediation="rotate AZURE_CLIENT_SECRET",
            ),
            _scripted("foundry", "skip", detail="ENABLE_REFERENCE_AGENT=false"),
        ]
    )
    text = render_human(results)

    # Pass / fail / skip all appear with a visible status marker.
    assert "dns" in text and "pass" in text.lower()
    assert "token" in text and "fail" in text.lower()
    assert "foundry" in text and "skip" in text.lower()
    # Remediation surfaces only on fail, and is a separate visible line.
    assert "rotate AZURE_CLIENT_SECRET" in text
    assert "all hosts resolved" in text  # pass detail still shown
    assert "ENABLE_REFERENCE_AGENT=false" in text  # skip detail still shown


async def test_render_json_format_is_machine_parseable():
    from preflight.core import render_json, run_checks

    results = await run_checks(
        [
            _scripted("dns", "pass", detail="ok"),
            _scripted("token", "fail", detail="401", remediation="rotate secret"),
        ]
    )
    payload = json.loads(render_json(results))

    # Shape pinned so CI / scripts can depend on it.
    assert payload["exit_code"] == 1  # one failure → non-zero
    assert payload["summary"] == {"pass": 1, "fail": 1, "skip": 0}
    assert payload["results"][0] == {
        "name": "dns",
        "status": "pass",
        "detail": "ok",
        "remediation": "",
    }
    assert payload["results"][1]["remediation"] == "rotate secret"


def test_exit_code_zero_when_all_pass_or_skip():
    from preflight.core import CheckResult, exit_code_for

    all_pass = [CheckResult(name="x", status="pass", detail="")]
    all_skip = [CheckResult(name="x", status="skip", detail="disabled")]
    mixed_ok = [
        CheckResult(name="x", status="pass", detail=""),
        CheckResult(name="y", status="skip", detail="agent off"),
    ]
    assert exit_code_for(all_pass) == 0
    assert exit_code_for(all_skip) == 0
    assert exit_code_for(mixed_ok) == 0


def test_exit_code_nonzero_when_any_fail():
    from preflight.core import CheckResult, exit_code_for

    one_fail = [
        CheckResult(name="x", status="pass", detail=""),
        CheckResult(name="y", status="fail", detail="", remediation="fix me"),
    ]
    assert exit_code_for(one_fail) == 1
