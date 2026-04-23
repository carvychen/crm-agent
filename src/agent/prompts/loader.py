"""PromptLoader — reads Markdown prompt files and applies {variable} substitution.

Scoped to the reference agent (ADR 0006). Prompt files may contain only simple
`{name}` substitution — no conditionals, loops, or templating logic. If logic
is needed, it belongs in the agent's Python code, not the prompt.
"""
from __future__ import annotations

from pathlib import Path


class PromptLoader:
    """Load Markdown prompt files and render them with variable substitution."""

    SYSTEM_FILE = "system.zh.md"
    SAFETY_FILE = "safety_rules.md"

    def __init__(self, prompts_dir: Path) -> None:
        self._dir = prompts_dir

    def render(self, **variables: str) -> str:
        parts: list[str] = [(self._dir / self.SYSTEM_FILE).read_text(encoding="utf-8")]
        safety = self._dir / self.SAFETY_FILE
        if safety.is_file():
            parts.append(safety.read_text(encoding="utf-8"))
        combined = "\n\n".join(parts)
        return combined.format_map(_StrictMapping(variables))


class _StrictMapping(dict):
    """dict subclass that raises KeyError on missing keys with a clear message.

    `str.format_map` defers missing-key handling to __missing__ — we reuse that
    hook so prompt authors see the actual offending {name} token rather than a
    stack trace with an opaque IndexError.
    """

    def __missing__(self, key: str):
        raise KeyError(
            f"Prompt references unknown variable {{{key}}}; supply it via "
            f"PromptLoader.render({key}=...) or remove the placeholder."
        )
