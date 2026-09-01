# Customer Support Agent

An agentic customer support system: FastAPI + async SQLAlchemy/Postgres backend, an
OpenAI-driven tool-calling orchestrator loop, with guardrails and escalation logic
layered on top as the project progresses.

## Stack

- Python 3.12, dependency management via **uv** (`pyproject.toml` + `uv.lock`) — not pip/requirements.txt
- FastAPI for the HTTP layer
- SQLAlchemy (async) + asyncpg against PostgreSQL
- OpenAI Chat Completions API for the LLM, via the `openai` SDK
- Config via `pydantic-settings` (`app/core/config.py`) — required fields have no
  default, so a missing `.env` var fails at import time with a clear pydantic error
  rather than failing later, confusingly, mid-request
- Docker Compose for local dev (`app` + `postgres` services); `postgres` has a
  healthcheck and `app` waits on it via `depends_on: condition: service_healthy`

## Rules the reviewer should enforce

**Guardrail checks must be re-validated inside the same DB transaction as the
state-changing write they gate.** Never trust a check performed earlier (e.g. in a
separate request, or earlier in the same request before other awaits) as sufficient
grounds for a write later — re-check inside the transaction that performs the write.
This is defense in depth against races and stale reads, not redundant caution.

**Tool call arguments must be validated/parsed defensively before execution.** A
JSON parse failure, a schema mismatch, or an unknown tool name must produce a
structured error result fed back to the model as that tool call's result — never an
uncaught exception. The orchestrator is calling code the model controls the input
to; treat it like an external boundary. See `app/orchestrator/loop.py::_run_tool_call`
for the reference pattern (parse errors and `TypeError` from bad call signatures are
both caught and turned into `{"error": ...}` results).

**Orchestrator/agent-loop code must stay framework-independent.** No FastAPI
imports (`Request`, `Response`, routers, etc.) in `app/orchestrator/`. It must be
callable from a plain script or a unit test with no web server running — LLM calls
are slow and cost money, so isolating them from the web framework is what makes
them testable/mockable at all.

**Any loop that calls an LLM repeatedly must have a hard iteration cap.** No
loop driven by model output (tool-calling, agentic retries, etc.) may run
unbounded — always cap total iterations and define an explicit, non-crashing
behavior for when the cap is hit (e.g. `app/orchestrator/loop.py`'s
`MAX_ITERATIONS` returning a clear failure message rather than looping forever
or raising past the caller).

## Local dev

```bash
docker compose up -d --build   # rebuild is cheap when unchanged, correct when not
docker compose logs -f app
curl http://localhost:8000/health
```

`docker compose down` preserves the `pgdata` volume; only pass `-v` if you
deliberately want to wipe the database.
