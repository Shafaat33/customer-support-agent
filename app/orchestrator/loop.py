import json

from openai import OpenAI

from app.core.config import settings
from app.orchestrator.tools import GET_ORDER_STATUS_SCHEMA, get_order_status

# One user question here resolves in at most 1-2 tool round-trips (a single
# order lookup, maybe a follow-up). 5 leaves headroom for a legitimate
# multi-tool question without letting a confused model burn API calls
# (cost + latency) indefinitely if it never converges on an answer.
MAX_ITERATIONS = 5

TOOL_SCHEMAS = [GET_ORDER_STATUS_SCHEMA]

TOOL_DISPATCH = {
    "get_order_status": get_order_status,
}

_client = OpenAI(api_key=settings.openai_api_key)


class AgentLoopError(Exception):
    """Raised when the loop hits the iteration cap without a final answer."""


def run_agent_loop(user_message: str) -> str:
    messages = [{"role": "user", "content": user_message}]

    for _ in range(MAX_ITERATIONS):
        response = _client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            tools=TOOL_SCHEMAS,
        )
        message = response.choices[0].message

        if not message.tool_calls:
            return message.content or ""

        messages.append(message.model_dump(exclude_unset=True))

        for tool_call in message.tool_calls:
            tool_fn = TOOL_DISPATCH.get(tool_call.function.name)
            if tool_fn is None:
                result = {"error": f"Unknown tool '{tool_call.function.name}'"}
            else:
                args = json.loads(tool_call.function.arguments)
                result = tool_fn(**args)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

    raise AgentLoopError(
        f"Exceeded {MAX_ITERATIONS} tool-calling iterations without a final answer."
    )
