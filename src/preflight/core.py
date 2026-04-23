"""Preflight core: CheckResult dataclass + runner + renderers.

Checks are Protocol-typed — any object with a `name: str` attribute and
`async def run() -> CheckResult` satisfies the contract. The runner is
sequential by design so that later checks can presume earlier ones (e.g. a
token check after a DNS check) and so that failure output ordering is
deterministic.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from typing import Iterable, Literal, Protocol, Sequence

Status = Literal["pass", "fail", "skip"]

_STATUS_MARK = {"pass": "✓", "fail": "✗", "skip": "·"}


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single preflight check, customer-readable.

    `name` uniquely identifies the check (stable across versions for tooling).
    `detail` is a one-line human description of what was observed.
    `remediation` is populated on `fail` with an actionable next step; empty
    on `pass` / `skip`.
    """

    name: str
    status: Status
    detail: str
    remediation: str = ""


class Check(Protocol):
    name: str

    async def run(self) -> CheckResult: ...


async def run_checks(checks: Sequence[Check]) -> list[CheckResult]:
    """Execute checks sequentially and collect their results.

    Sequential execution is a deliberate trade: slight latency cost buys
    deterministic ordering and the option of short-circuiting later checks
    based on earlier signals (a choice we intentionally don't exercise yet —
    every check runs regardless, so an operator can see the full picture in
    one pass).
    """
    results: list[CheckResult] = []
    for check in checks:
        try:
            result = await check.run()
        except Exception as exc:  # pragma: no cover — defensive
            result = CheckResult(
                name=getattr(check, "name", "unknown"),
                status="fail",
                detail=f"check raised {type(exc).__name__}: {exc}",
                remediation=(
                    "This is a bug in the preflight check itself; please "
                    "open an issue with the full traceback."
                ),
            )
        results.append(result)
    return results


def render_human(results: Iterable[CheckResult]) -> str:
    """Console-friendly rendering for platform engineers watching the script.

    Layout is one line per check: `<mark> <name:20> <detail>`; failures emit
    an additional indented `remediation` line so it's impossible to miss.
    """
    lines: list[str] = []
    for r in results:
        mark = _STATUS_MARK[r.status]
        lines.append(f"{mark} {r.name:<28} {r.status:<4} {r.detail}")
        if r.status == "fail" and r.remediation:
            lines.append(f"    remediation: {r.remediation}")
    summary = _summary(results)
    lines.append(
        f"\n{summary['pass']} passed · {summary['fail']} failed · "
        f"{summary['skip']} skipped"
    )
    return "\n".join(lines)


def render_json(results: Iterable[CheckResult]) -> str:
    """Machine-parseable output for CI / downstream tooling.

    Schema is pinned: the `results` list preserves check order, `summary`
    gives tallies, and `exit_code` is the same value `sys.exit()` should use.
    """
    results_list = list(results)
    payload = {
        "summary": _summary(results_list),
        "exit_code": exit_code_for(results_list),
        "results": [asdict(r) for r in results_list],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def exit_code_for(results: Iterable[CheckResult]) -> int:
    return 1 if any(r.status == "fail" for r in results) else 0


def _summary(results: Iterable[CheckResult]) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "skip": 0}
    for r in results:
        counts[r.status] += 1
    return counts
