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


def test_prompt_loader_appends_provider_override_when_present(tmp_path: Path):
    """Per-provider override file (prompts/providers/{name}.md) is appended
    when the caller names the provider; absent file = no-op (provider-neutral)."""
    (tmp_path / "system.zh.md").write_text("你是 CRM 助手。", encoding="utf-8")
    providers = tmp_path / "providers"
    providers.mkdir()
    (providers / "azure-openai-cn.md").write_text(
        "CN 模型提示：注意时间用北京时区。", encoding="utf-8"
    )

    from agent.prompts.loader import PromptLoader

    rendered = PromptLoader(prompts_dir=tmp_path).render(provider="azure-openai-cn")
    assert "你是 CRM 助手。" in rendered
    assert "CN 模型提示：注意时间用北京时区。" in rendered
    assert rendered.index("你是 CRM 助手。") < rendered.index(
        "CN 模型提示：注意时间用北京时区。"
    )


def test_prompt_loader_ignores_missing_provider_override(tmp_path: Path):
    (tmp_path / "system.zh.md").write_text("你是 CRM 助手。", encoding="utf-8")

    from agent.prompts.loader import PromptLoader

    # provider="foundry" with no providers/foundry.md must not raise.
    rendered = PromptLoader(prompts_dir=tmp_path).render(provider="foundry")
    assert rendered == "你是 CRM 助手。"


def test_prompt_loader_appends_few_shot_examples_in_alphabetical_order(tmp_path: Path):
    """Few-shot markdown files under prompts/few_shot/ append after safety_rules."""
    (tmp_path / "system.zh.md").write_text("你是 CRM 助手。", encoding="utf-8")
    (tmp_path / "safety_rules.md").write_text("删前确认。", encoding="utf-8")
    few_shot = tmp_path / "few_shot"
    few_shot.mkdir()
    # Intentionally create files out of alphabetical order to check sorting.
    (few_shot / "b_second.md").write_text("## B\n二号示例", encoding="utf-8")
    (few_shot / "a_first.md").write_text("## A\n一号示例", encoding="utf-8")

    from agent.prompts.loader import PromptLoader

    rendered = PromptLoader(prompts_dir=tmp_path).render()

    # Order: system → safety_rules → few_shot/* (sorted alphabetically).
    positions = {
        token: rendered.index(token)
        for token in ("你是 CRM 助手。", "删前确认。", "一号示例", "二号示例")
    }
    assert (
        positions["你是 CRM 助手。"]
        < positions["删前确认。"]
        < positions["一号示例"]
        < positions["二号示例"]
    )
