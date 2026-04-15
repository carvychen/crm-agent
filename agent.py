"""
CRM Agent — a production-grade agent with Dynamics 365 Opportunity skill.

Features:
  - Session-based multi-turn memory
  - Human-in-the-loop approval for destructive operations (delete)
  - Streaming responses for better UX
  - Context compaction for long conversations
  - Exception handling middleware for graceful API error recovery
  - Auto retry on rate limits
  - Usage/token tracking

Usage:
    python agent.py
"""

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable

from agent_framework import (
    Agent,
    ChatMiddlewareLayer,
    FunctionInvocationContext,
    Message,
    Skill,
    SkillScript,
    SkillsProvider,
    SlidingWindowStrategy,
    chat_middleware,
    function_middleware,
    tool,
)
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

load_dotenv()


# ── Skill script runner ──────────────────────────────────────────────────────

def subprocess_script_runner(
    skill: Skill, script: SkillScript, args: dict[str, Any] | None = None
) -> str:
    """Run a file-based skill script as a local Python subprocess."""
    if not skill.path or not script.path:
        return f"Error: Skill '{skill.name}' or script '{script.name}' has no file path."
    script_path = (Path(skill.path) / script.path).resolve()
    if not script_path.is_file():
        return f"Error: Script file not found: {script_path}"

    cmd = [sys.executable, str(script_path)]
    if args:
        for key, value in args.items():
            # Normalize key: strip leading dashes, then add exactly "--"
            flag = f"--{key.lstrip('-')}"
            if isinstance(value, bool):
                if value:
                    cmd.append(flag)
            elif value is not None:
                cmd.append(flag)
                cmd.append(str(value))

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            cwd=str(script_path.parent),
        )
        output = result.stdout
        if result.stderr:
            output += f"\nStderr:\n{result.stderr}"
        if result.returncode != 0:
            output += f"\nScript exited with code {result.returncode}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Script '{script.name}' timed out after 30 seconds."
    except OSError as e:
        return f"Error: Failed to execute script '{script.name}': {e}"

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("crm_agent")
logger.setLevel(logging.INFO)


# ── Middleware: exception handling ────────────────────────────────────────────

@function_middleware
async def error_handling_middleware(
    context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]
) -> None:
    """Catch Dataverse API errors and return a friendly message instead of crashing."""
    try:
        await call_next()
    except Exception as e:
        error_msg = str(e)
        logger.warning("Tool '%s' failed: %s", context.function.name, error_msg)
        # Return error as string so the LLM can reason about it and retry or inform user
        context.result = f"Error: {error_msg}"


# ── Middleware: rate limit retry ──────────────────────────────────────────────

@chat_middleware
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def rate_limit_retry_middleware(
    context: ChatMiddlewareLayer, call_next: Callable[[], Awaitable[None]]
) -> None:
    """Retry model calls on transient failures with exponential backoff."""
    await call_next()


# ── Middleware: usage tracking ────────────────────────────────────────────────

call_count = 0

@chat_middleware
async def usage_tracking_middleware(
    context: ChatMiddlewareLayer, call_next: Callable[[], Awaitable[None]]
) -> None:
    """Track LLM usage for cost monitoring."""
    global call_count
    call_count += 1
    await call_next()
    if hasattr(context, "response") and context.response and hasattr(context.response, "usage_details"):
        usage = context.response.usage_details
        if usage:
            logger.info("[Call #%d] Tokens — prompt: %s, completion: %s, total: %s",
                        call_count,
                        getattr(usage, "prompt_tokens", "?"),
                        getattr(usage, "completion_tokens", "?"),
                        getattr(usage, "total_tokens", "?"))


# ── Approval handling for destructive operations ─────────────────────────────

async def run_with_approval(agent: Agent, user_input: str, session) -> str:
    """
    Run the agent. If it requests user approval (for destructive tool calls),
    prompt the user in the terminal before proceeding.
    """
    response = await agent.run(user_input, session=session)

    # Check if the agent is requesting approval for any tool calls
    while hasattr(response, "user_input_requests") and response.user_input_requests:
        for request in response.user_input_requests:
            print(f"\n  [Approval Required] The agent wants to call: {request.function.name}")
            if hasattr(request, "arguments"):
                print(f"  Arguments: {request.arguments}")
            approval = input("  Approve? (y/n): ").strip().lower()

            approval_response = request.to_function_approval_response(approved=(approval == "y"))
            messages = [
                Message("assistant", [request]),
                Message("user", [approval_response]),
            ]
            response = await agent.run(messages, session=session)

    return str(response)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    deployment = os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini")

    client = FoundryChatClient(
        project_endpoint=endpoint,
        model=deployment,
        credential=AzureCliCredential(),
    )

    skills_dir = Path(__file__).parent / "skills"
    skills_provider = SkillsProvider(
        skill_paths=str(skills_dir),
        script_runner=subprocess_script_runner,
    )

    async with Agent(
        client=client,
        instructions=(
            "You are a CRM assistant that manages Dynamics 365 opportunities. "
            "Use the crm-opportunity skill to answer questions about deals, "
            "create new opportunities, update existing ones, or delete them. "
            "Always present results in a clear, readable format. "
            "When listing opportunities, show Topic, Account, Revenue, Close Date, "
            "Probability, and Rating. "
            "Before deleting any record, always confirm with the user."
        ),
        context_providers=[skills_provider],
        # Context compaction: keep conversation manageable for long sessions
        compaction_strategy=SlidingWindowStrategy(keep_last_groups=20),
        # Middleware stack
        middleware=[
            error_handling_middleware,
            rate_limit_retry_middleware,
            usage_tracking_middleware,
        ],
    ) as agent:
        session = agent.create_session()

        print("CRM Agent ready. Type your questions (Ctrl+C to exit).")
        print("Examples:")
        print("  - 列出所有 Hot 评级的商机")
        print("  - 创建商机，名称 Enterprise Deal，账户 Fourth Coffee，收入 50000")
        print("  - 把那个商机的概率改成 80%")
        print("  - 删掉刚才创建的商机")
        print()

        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye!")
                break
            if not user_input:
                continue

            try:
                response = await run_with_approval(agent, user_input, session)
                print(f"\nAgent: {response}\n")
            except Exception as e:
                logger.error("Unhandled error: %s", e)
                print(f"\nAgent: Sorry, something went wrong: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
