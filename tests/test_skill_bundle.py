"""Structural tests for the crm-opportunity skill bundle.

These checks do not need any Azure access — they run on every PR and catch
the two regressions this slice was created to prevent:

1. Credentials / Python scripts sneaking back into the bundle.
2. The `.mcp.json` contract drifting from what external MCP clients
   (Claude Desktop, VS Code Copilot MCP) expect.

The real MCP-over-HTTP live test lives in tests/integration/test_skill_bundle_live.py.
"""
from __future__ import annotations

import json
from pathlib import Path


_SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "crm-opportunity"


def test_mcp_json_matches_external_client_contract():
    path = _SKILL_DIR / ".mcp.json"
    assert path.is_file(), "skill bundle must ship .mcp.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    # `mcpServers` is the canonical MCP-client config key used by Claude
    # Desktop, VS Code Copilot MCP, and Cursor. Anything else is either
    # unknown or proprietary.
    assert "mcpServers" in data
    assert "crm" in data["mcpServers"], "skill advertises its server under 'crm'"
    server = data["mcpServers"]["crm"]
    assert server.get("type") == "streamable-http"
    assert server.get("url", "").endswith("/mcp"), "URL must point at the MCP endpoint"


def test_skill_bundle_has_no_code_or_credentials():
    """No Python scripts, no credentials — the bundle is pure content."""
    forbidden = ["scripts", "lib", ".env", ".env.example", "requirements.txt"]
    for entry in forbidden:
        assert not (_SKILL_DIR / entry).exists(), (
            f"skill bundle still contains legacy {entry!r} — Slice 7 "
            "required deleting it; if it was re-added, the PR probably "
            "has a stale rebase."
        )

    text = (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "```python" not in text.lower(), (
        "SKILL.md contains a Python code block — per ADR 0006 the skill "
        "is agent-neutral content; runtime code belongs in src/"
    )
    for secret_name in ("AZURE_CLIENT_SECRET", "client_secret"):
        assert secret_name not in text, (
            f"SKILL.md mentions {secret_name!r} — the whole point of "
            "Slice 7 was removing credential references (ADR 0001)"
        )
