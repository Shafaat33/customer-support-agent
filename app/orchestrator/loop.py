import json
from dataclasses import dataclass

from openai import AsyncOpenAI
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.orchestrator.tools import (
    CANCEL_ORDER_SCHEMA,
    GET_ORDER_STATUS_SCHEMA,
    ISSUE_REFUND_SCHEMA,
    cancel_order,
    get_order_status,
    issue_refund,
)

# A single order lookup resolves in one tool round-trip; a couple of
# follow-up lookups in the same conversation might take two or three.
# 5 gives headroom for that while still bounding cost/latency if the
# model gets stuck re-requesting tools without converging.
MAX_ITERATIONS = 5

FAILURE_MESSAGE = (
    "I wasn't able to complete this request after several attempts. "
    "Please try again or contact support directly."
)

TOOL_SCHEMAS = [GET_ORDER_STATUS_SCHEMA, CANCEL_ORDER_SCHEMA, ISSUE_REFUND_SCHEMA]

TOOL_DISPATCH = {
    "get_order_status": get_order_status,
    "cancel_order": cancel_order,
    "issue_refund": issue_refund,
}

# Tools whose write must never be attempted twice with different identities
# on retry. The orchestrator, not the model, owns generating this key --
# an LLM has no reliable way to reproduce a byte-identical string across
# separate generations, which defeats the whole idempotency mechanism.
IDEMPOTENT_TOOLS = {"issue_refund"}

TOOL_CALL_MAX_ATTEMPTS = 3

_client = AsyncOpenAI(api_key=settings.openai_api_key)


@dataclass
class AgentResult:
    text: str
    succeeded: bool


async def _execute_with_retry(tool_fn, args: dict, tool_name: str) -> dict:
    last_exc: Exception | None = None
    for _ in range(TOOL_CALL_MAX_ATTEMPTS):
        try:
            return await tool_fn(**args)
        except (OSError, SQLAlchemyError) as exc:
            # Transient failure (dropped connection, etc.) -- retry with the
            # exact same args, including the idempotency key set below, so a
            # retried issue_refund is recognized as the same attempt, not a
            # new one.
            last_exc = exc
            continue
    return {"error": f"tool '{tool_name}' failed after {TOOL_CALL_MAX_ATTEMPTS} attempts: {last_exc}"}


async def _run_tool_call(tool_call) -> dict:
    tool_fn = TOOL_DISPATCH.get(tool_call.function.name)
    if tool_fn is None:
        return {"error": f"unknown tool '{tool_call.function.name}'"}

    try:
        args = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError:
        return {"error": "invalid tool call arguments: not valid JSON"}

    if tool_call.function.name in IDEMPOTENT_TOOLS:
        # Derived from the model's own tool_call.id, which is fixed for the
        # lifetime of processing this one tool call -- stable across any
        # retry of *executing* it, but distinct for every genuinely separate
        # tool call the model emits (each gets its own id from the API).
        args["idempotency_key"] = f"toolcall-{tool_call.id}"

    try:
        return await _execute_with_retry(tool_fn, args, tool_call.function.name)
    except TypeError as exc:
        return {"error": f"invalid arguments for tool '{tool_call.function.name}': {exc}"}


async def run_agent_loop(user_message: str) -> AgentResult:
    messages = [{"role": "user", "content": user_message}]

    for iteration in range(MAX_ITERATIONS):
        response = await _client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            tools=TOOL_SCHEMAS,
        )
        choice = response.choices[0]
        message = choice.message

        if choice.finish_reason != "tool_calls":
            return AgentResult(text=message.content or "", succeeded=True)

        if iteration == MAX_ITERATIONS - 1:
            # Out of budget and the model still wants tools. Don't execute
            # them: this turn is about to be abandoned, and tools can have
            # real side effects (e.g. issue_refund) that must never fire on
            # a turn whose result is discarded.
            break

        messages.append(message.model_dump(exclude_unset=True))

        for tool_call in message.tool_calls:
            result = await _run_tool_call(tool_call)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

    return AgentResult(text=FAILURE_MESSAGE, succeeded=False)
