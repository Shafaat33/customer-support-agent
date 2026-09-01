"""Manual smoke test for run_agent_loop. Run with: uv run python scripts/test_agent_loop.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.orchestrator.loop import run_agent_loop

if __name__ == "__main__":
    for message in [
        "What's the status of order ORD-100?",
        "What's the status of order ORD-999?",
    ]:
        print(f"> {message}")
        print(run_agent_loop(message))
        print()
