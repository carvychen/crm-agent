"""Source-literal lint: cloud-specific endpoints must not escape src/config.py.

Per ADR 0003 and ADR 0007 (delivery-constrained slice clause), cloud-specific
hostnames and FIC audiences are the single source of regression for the
`CLOUD_ENV=china` branch we cannot live-test. This test fails if any forbidden
literal lands outside the approved source of truth, which is why it runs on
every PR — it's the closest thing to "did we break CN without noticing".
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"

# Files where the literals are intentionally centralised. Adding to this list
# should trigger a second pair of eyes on the PR — the whole point of the lint
# is to keep the blast radius tiny.
_ALLOWED = {
    _SRC / "config.py",
}

_FORBIDDEN_PATTERNS = {
    "Azure Global authority": re.compile(r"login\.microsoftonline\.com"),
    "Azure China authority": re.compile(r"login\.partner\.microsoftonline\.cn"),
    "Azure Global FIC audience": re.compile(r"AzureADTokenExchange(?!China)"),
    "Azure China FIC audience": re.compile(r"AzureADTokenExchangeChina"),
    "Azure Global Dataverse suffix": re.compile(r"\bcrm\.dynamics\.com\b"),
    "Azure China Dataverse suffix": re.compile(r"\bcrm\.dynamics\.cn\b"),
}


def _iter_source_files():
    for path in _SRC.rglob("*.py"):
        if path in _ALLOWED:
            continue
        if "__pycache__" in path.parts:
            continue
        yield path


@pytest.mark.parametrize(
    "snippet,label",
    [
        ('AUTHORITY = "https://login.microsoftonline.com"', "Azure Global authority"),
        ('AUTHORITY = "https://login.partner.microsoftonline.cn"', "Azure China authority"),
        ('audience = "api://AzureADTokenExchange"', "Azure Global FIC audience"),
        ('audience = "api://AzureADTokenExchangeChina"', "Azure China FIC audience"),
        ('URL = "https://org.crm.dynamics.com"', "Azure Global Dataverse suffix"),
        ('URL = "https://org.crm.dynamics.cn"', "Azure China Dataverse suffix"),
    ],
)
def test_forbidden_patterns_actually_match_each_violation(snippet: str, label: str):
    """Sanity check that the lint regexes catch the thing they claim to catch.

    Silently-broken regexes would let violations slip through with a
    false-green CI, which is exactly the failure mode this lint exists to
    prevent — so we double-lock the patterns here.
    """
    matched_labels = [
        name for name, pattern in _FORBIDDEN_PATTERNS.items() if pattern.search(snippet)
    ]
    assert label in matched_labels, (
        f"Pattern for {label!r} failed to match its canonical violation "
        f"{snippet!r}. Fix the regex before committing."
    )


@pytest.mark.parametrize("source_path", list(_iter_source_files()), ids=lambda p: str(p.relative_to(_REPO_ROOT)))
def test_no_cloud_specific_literals_in_source_file(source_path: Path):
    """Fail if any cloud-specific literal leaks into src/ outside config.py."""
    text = source_path.read_text(encoding="utf-8")
    violations: list[str] = []
    for label, pattern in _FORBIDDEN_PATTERNS.items():
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            violations.append(f"{label}: {match.group(0)!r} at line {line}")
    assert not violations, (
        f"Cloud-specific literal escaped {source_path.relative_to(_REPO_ROOT)} "
        f"(must live only in {{{', '.join(str(p.relative_to(_REPO_ROOT)) for p in _ALLOWED)}}} "
        f"— see ADR 0003 and ADR 0007):\n  - "
        + "\n  - ".join(violations)
    )
