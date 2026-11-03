# Agent Company

An AI software-development company built from specialized CrewAI agents.
See ReadMe.mdx for roles, architecture and stack.

## Layout

- `src/agent_company/crew.py` – agents, tasks and the crew
- `src/agent_company/__main__.py` – CLI entry point
- `output/` – generated step outputs (git-ignored)

## Conventions

- Python 3.12, dependencies managed with `uv` (system pip is externally managed).
- Model is chosen via `MODEL` in `.env` (LiteLLM `provider/model` string); never hard-code keys.
- Start with the four implemented agents; add Research, Frontend and DevOps only once the
  pipeline is reliable.
- Agents must not get access to production servers; anything that deploys runs in an
  isolated environment with restricted permissions.

## Commands

- `uv sync` – install dependencies
- `uv run python -m agent_company "<request>"` – run the crew
