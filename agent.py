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

Requires .env with:
    AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, DATAVERSE_URL
    FOUNDRY_PROJECT_ENDPOINT   (Azure AI Foundry project endpoint)
    FOUNDRY_MODEL              (optional, defaults to gpt-4o-mini)
"""

import asyncio
import logging
import os
from typing import Annotated, Any, Awaitable, Callable

from agent_framework import (
    Agent,
    ChatMiddlewareLayer,
    FunctionInvocationContext,
    Message,
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

from crm_skill import crm_opportunity_skill

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

    skills_provider = SkillsProvider(skills=[crm_opportunity_skill])

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
