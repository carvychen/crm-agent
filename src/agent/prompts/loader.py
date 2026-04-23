"""PromptLoader — reads Markdown prompt files and applies {variable} substitution.

Per ADR 0006 prompt files may contain only simple `{name}` substitution — no
conditionals, loops, or templating logic. If logic is needed it belongs in
Python code, not the prompt.
"""
from __future__ import annotations

from pathlib import Path


class PromptLoader:
    """Load Markdown prompt files and render them with variable substitution.

    Layout (ADR 0006):
    - `system.zh.md`     — base system prompt (required)
    - `safety_rules.md`  — destructive-op rules (optional, appended second)
    - `few_shot/*.md`    — worked examples (optional, appended alphabetically)
    """

    SYSTEM_FILE = "system.zh.md"
    SAFETY_FILE = "safety_rules.md"
    FEW_SHOT_DIR = "few_shot"

    def __init__(self, prompts_dir: Path) -> None:
        self._dir = prompts_dir

    def render(self, **variables: str) -> str:
        # `{variable}` substitution is restricted to the system prompt file.
        # safety_rules and few_shot are literal content — JSON examples inside
        # them legitimately contain braces and must not be reinterpreted.
        system = (self._dir / self.SYSTEM_FILE).read_text(encoding="utf-8")
        parts: list[str] = [system.format_map(_StrictMapping(variables))]

        safety = self._dir / self.SAFETY_FILE
        if safety.is_file():
            parts.append(safety.read_text(encoding="utf-8"))

        few_shot_dir = self._dir / self.FEW_SHOT_DIR
        if few_shot_dir.is_dir():
            for path in sorted(few_shot_dir.glob("*.md")):
                parts.append(path.read_text(encoding="utf-8"))

        return "\n\n".join(parts)


class _StrictMapping(dict):
    """dict subclass that makes missing-key errors name the offending token."""

    def __missing__(self, key: str):
        raise KeyError(
            f"Prompt references unknown variable {{{key}}}; supply it via "
            f"PromptLoader.render({key}=...) or remove the placeholder."
        )
