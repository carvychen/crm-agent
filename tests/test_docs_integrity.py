"""Meta-test: runbook hyperlinks must point at files that actually exist.

Documentation rot is the largest risk of shipping runbooks — a refactor
that renames `src/preflight/checks.py` without touching `docs/deployment/
preflight.md` means the customer opens a broken link on day one at UAT,
which Invariant 4 (delivered blind) makes unrecoverable.

This test walks every Markdown file under `docs/` and asserts that every
`[text](path)` link whose destination looks like a repo path resolves.
We intentionally skip backtick-wrapped content and other mentions —
they are too noisy (hostnames, globs, enum values, code snippets) and
rarely represent broken navigation.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]
_DOCS = _REPO / "docs"

# Match the destination of a markdown link: `[visible text](dest)`.
_LINK_PATTERN = re.compile(r"\]\(([^)]+)\)")

# Skip these prefixes — they're external and not our job to validate.
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")


def _all_doc_files() -> list[Path]:
    return sorted(_DOCS.rglob("*.md"))


def _resolve_candidate(doc: Path, candidate: str) -> Path | None:
    """Resolve a link destination to an absolute path inside the repo, or
    return None if it's not a repo path we should validate."""
    # Drop anchor + query fragments.
    candidate = candidate.split("#", 1)[0].split("?", 1)[0].strip()
    if not candidate:
        return None
    if candidate.startswith(_EXTERNAL_PREFIXES):
        return None

    if candidate.startswith("/"):
        # Repo-absolute. Strip the leading slash.
        resolved = (_REPO / candidate.lstrip("/")).resolve()
    else:
        # Relative to the doc's directory.
        resolved = (doc.parent / candidate).resolve()

    # Only validate if the result lands inside the repo (otherwise it's a
    # path outside our control, e.g. a parent-of-repo file).
    try:
        resolved.relative_to(_REPO)
    except ValueError:
        return None
    return resolved


@pytest.mark.parametrize(
    "doc", _all_doc_files(), ids=lambda p: str(p.relative_to(_REPO))
)
def test_markdown_links_in_doc_resolve(doc: Path):
    text = doc.read_text(encoding="utf-8")
    broken: list[str] = []
    for match in _LINK_PATTERN.finditer(text):
        resolved = _resolve_candidate(doc, match.group(1))
        if resolved is None:
            continue
        if not resolved.exists():
            broken.append(
                f"{match.group(1)!r} → {resolved.relative_to(_REPO).as_posix()}"
            )
    assert not broken, (
        f"{doc.relative_to(_REPO)} has broken internal links:\n  - "
        + "\n  - ".join(broken)
    )
