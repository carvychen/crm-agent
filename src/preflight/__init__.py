"""Preflight validation framework.

Structured health checks for the MCP server + reference agent stack. Runs
locally (`python scripts/preflight.py`) in CI against the author's dev tenant
and, more importantly, at Lenovo's UAT against their Azure China tenant — the
"delivered blind" gate from Invariant 4.
"""
