"""Tests for src/agent/prompts/loader.py — Markdown-based prompt module (ADR 0006)."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_prompt_loader_reads_system_prompt_file(tmp_path: Path):
    (tmp_path / "system.zh.md").write_text("你是一个 CRM 助手。", encoding="utf-8")

    from agent.prompts.loader import PromptLoader

    loader = PromptLoader(prompts_dir=tmp_path)
    assert loader.render() == "你是一个 CRM 助手。"


def test_prompt_loader_appends_safety_rules(tmp_path: Path):
    (tmp_path / "system.zh.md").write_text("你是一个 CRM 助手。", encoding="utf-8")
    (tmp_path / "safety_rules.md").write_text(
        "在执行删除操作前，必须向用户确认。", encoding="utf-8"
    )

    from agent.prompts.loader import PromptLoader

    rendered = PromptLoader(prompts_dir=tmp_path).render()

    assert "你是一个 CRM 助手。" in rendered
    assert "在执行删除操作前，必须向用户确认。" in rendered
    assert rendered.index("你是一个 CRM 助手。") < rendered.index(
        "在执行删除操作前，必须向用户确认。"
    )


def test_prompt_loader_substitutes_current_date(tmp_path: Path):
    (tmp_path / "system.zh.md").write_text(
        "今天是 {current_date}。", encoding="utf-8"
    )

    from agent.prompts.loader import PromptLoader

    rendered = PromptLoader(prompts_dir=tmp_path).render(current_date="2026-04-23")
    assert rendered == "今天是 2026-04-23。"


def test_prompt_loader_raises_on_unknown_variable(tmp_path: Path):
    (tmp_path / "system.zh.md").write_text("Hello {user_name}!", encoding="utf-8")

    from agent.prompts.loader import PromptLoader

    with pytest.raises(KeyError) as excinfo:
        PromptLoader(prompts_dir=tmp_path).render(current_date="2026-04-23")
    assert "user_name" in str(excinfo.value)
