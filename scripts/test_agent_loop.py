"""Manual smoke test for run_agent_loop. Run with: uv run python scripts/test_agent_loop.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.orchestrator import loop
from app.orchestrator.loop import run_agent_loop


def run_case(message: str) -> None:
    print(f"> {message}")
    result = run_agent_loop(message)
    print(f"[succeeded={result.succeeded}] {result.text}")
    print()


def test_iteration_cap_prevents_execution() -> None:
    """Force MAX_ITERATIONS=1 against a question that requires a tool call,
    and prove the tool never actually runs once the budget is exhausted."""
    message = "What's the status of order ORD-100?"
    original_max_iterations = loop.MAX_ITERATIONS
    original_tool_fn = loop.TOOL_DISPATCH["get_order_status"]
    call_count = 0

    def tracking_get_order_status(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original_tool_fn(*args, **kwargs)

    loop.MAX_ITERATIONS = 1
    loop.TOOL_DISPATCH["get_order_status"] = tracking_get_order_status
    try:
        result = run_agent_loop(message)
    finally:
        loop.MAX_ITERATIONS = original_max_iterations
        loop.TOOL_DISPATCH["get_order_status"] = original_tool_fn

    print(f"> [forced MAX_ITERATIONS=1] {message}")
    print(f"[succeeded={result.succeeded}] {result.text}")
    print(f"tool execution count: {call_count}")

    assert result.succeeded is False, "expected a failure result once budget is exhausted"
    assert call_count == 0, "tool must never execute on the abandoned final iteration"
    print("OK: iteration cap hit before the tool ever ran")
    print()


if __name__ == "__main__":
    run_case("What's the status of order ORD-100?")
    run_case("What's the status of order ORD-999?")
    test_iteration_cap_prevents_execution()
