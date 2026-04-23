"""Tests for src/agent/prompts/loader.py — Markdown-based prompt module."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_prompt_loader_reads_system_prompt_file(tmp_path: Path):
    """PromptLoader.render() reads the system.zh.md file from disk."""
    (tmp_path / "system.zh.md").write_text("你是一个 CRM 助手。", encoding="utf-8")

    from agent.prompts.loader import PromptLoader

    loader = PromptLoader(prompts_dir=tmp_path)
    assert loader.render() == "你是一个 CRM 助手。"


def test_prompt_loader_appends_safety_rules(tmp_path: Path):
    """safety_rules.md is concatenated after the system prompt when present."""
    (tmp_path / "system.zh.md").write_text("你是一个 CRM 助手。", encoding="utf-8")
    (tmp_path / "safety_rules.md").write_text(
        "在执行删除操作前，必须向用户确认。", encoding="utf-8"
    )

    from agent.prompts.loader import PromptLoader

    rendered = PromptLoader(prompts_dir=tmp_path).render()

    assert "你是一个 CRM 助手。" in rendered
    assert "在执行删除操作前，必须向用户确认。" in rendered
    # System prompt comes first; safety rules follow.
    assert rendered.index("你是一个 CRM 助手。") < rendered.index(
        "在执行删除操作前，必须向用户确认。"
    )


def test_prompt_loader_substitutes_current_date(tmp_path: Path):
    """{current_date} is replaced with the supplied value."""
    (tmp_path / "system.zh.md").write_text(
        "今天的日期是 {current_date}。", encoding="utf-8"
    )

    from agent.prompts.loader import PromptLoader

    rendered = PromptLoader(prompts_dir=tmp_path).render(current_date="2026-04-23")

    assert rendered == "今天的日期是 2026-04-23。"


def test_prompt_loader_raises_on_unknown_variable(tmp_path: Path):
    """Prompt files that reference an unsupplied {variable} fail loudly."""
    (tmp_path / "system.zh.md").write_text(
        "Hello {user_display_name}!", encoding="utf-8"
    )

    from agent.prompts.loader import PromptLoader

    with pytest.raises(KeyError) as excinfo:
        PromptLoader(prompts_dir=tmp_path).render(current_date="2026-04-23")

    # The error names the unknown variable so a prompt author can fix it.
    assert "user_display_name" in str(excinfo.value)
