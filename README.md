# CRM Purple Agent — AgentX Phase 2

A CRM agent for Berkeley RDI **AgentX–AgentBeats Phase 2** competition. Evaluated by the Entropic CRMArena green agent across 2,140 CRM tasks (22 categories) with schema drift and context rot resistance.

> 繁體中文說明請見 [README.zh-TW.md](README.zh-TW.md)

## Project Structure

```
src/
├─ server.py              # A2A server (port 9009) + agent card
├─ executor.py            # A2A request handler
├─ agent.py               # 5-layer pipeline orchestrator
├─ privacy_guard.py       # Rule-based privacy rejection (3 categories)
├─ schema_introspector.py # Drift mapping (hardcoded + fuzzy fallback)
├─ context_filter.py      # Rot note stripping + heuristic filtering
├─ task_planner.py        # Strategy classifier (22 categories)
├─ sql_generator.py       # LLM reasoning with category-specific prompts
├─ answer_synthesizer.py  # Answer formatting + hallucination guard
├─ error_recovery.py      # Retry logic with re-introspection
├─ llm_client.py          # Multi-backend LLM (Anthropic / Nebius / OpenAI)
└─ messenger.py           # A2A messaging utilities
config/
├─ schema.json            # Canonical CRM schema (8 tables, 6 relationships)
└─ prompts.yaml           # 22 prompt templates (3 general + 19 category-specific)
tests/
├─ test_privacy.py        # Privacy guard tests
├─ test_task_planner.py   # Task planner tests
├─ test_introspector.py   # Schema introspector tests
├─ test_context_filter.py # Context filter tests
└─ test_agent.py          # A2A conformance tests
Dockerfile                    # Docker configuration
pyproject.toml                # Python dependencies
amber-manifest.json5          # Amber manifest
.github/
└─ workflows/
   └─ test-and-publish.yml    # CI workflow
```

## Pipeline Architecture

The agent implements a 5-layer pipeline:

| Layer | Component | Description |
|---|---|---|
| Privacy Guard | `privacy_guard.py` | Rule-based, zero LLM calls — instantly rejects 3 private categories |
| L1 | `schema_introspector.py` | Detects & maps drifted column names (low / medium / high) |
| L1 | `context_filter.py` | Strips rot noise, heuristic relevance filtering |
| L2 | `task_planner.py` | Classifies task into `exact_query_match`, `semantic_retrieval`, or `privacy_rejection` |
| L3 | `sql_generator.py` | Category-specific prompts with schema-aware LLM reasoning |
| L4 | `answer_synthesizer.py` | Cleans output, formats multi-value answers, hallucination grounding |
| L5 | `error_recovery.py` | Max 2 retries with schema re-introspection, graceful degradation |

## Getting Started

### Running Locally

```bash
# Install dependencies
uv sync

# Run with OpenAI as primary provider
OPENAI_API_KEY=sk-... uv run src/server.py

# Run with Claude as primary + Nebius as cheap tier
OPENAI_API_KEY=sk-ant-... LLM_PRIMARY_BASE_URL=https://api.anthropic.com/v1 LLM_PRIMARY_MODEL=claude-sonnet-4-6 \
  OPENAI_CHEAP_API_KEY=<nebius-key> LLM_CHEAP_BASE_URL=https://api.studio.nebius.com/v1 \
  uv run src/server.py
```

### Running with Docker

```bash
# Build the image
docker build -t crm-purple-agent .

# Run the container
docker run -p 9009:9009 -e OPENAI_API_KEY=sk-... crm-purple-agent
```

## Environment Variables

The client uses two independent **tiers** — primary (expensive) and cheap — each pointing at
any OpenAI-compatible provider via a key and an optional base URL.

### API Keys

| Variable | Purpose | Required |
|---|---|---|
| `OPENAI_API_KEY` | Key for the **primary** provider | At least one |
| `OPENAI_CHEAP_API_KEY` | Key for the **cheap** provider | Optional |

### Base URL Overrides

| Variable | Default | Description |
|---|---|---|
| `LLM_PRIMARY_BASE_URL` | _(OpenAI)_ | Base URL for the primary provider, e.g. `https://api.anthropic.com/v1` |
| `LLM_CHEAP_BASE_URL` | _(OpenAI)_ | Base URL for the cheap provider, e.g. `https://api.studio.nebius.com/v1` |

### Model Overrides

| Variable | Default | Description |
|---|---|---|
| `LLM_PRIMARY_MODEL` | `claude-sonnet-4-6` | Model for primary (expensive) calls |
| `LLM_CHEAP_MODEL` | `claude-haiku-4-5` | Model for cheap/fast calls |

### Provider Examples

```bash
# Claude Sonnet as primary (Anthropic OpenAI-compat endpoint)
OPENAI_API_KEY=sk-ant-...
LLM_PRIMARY_BASE_URL=https://api.anthropic.com/v1
LLM_PRIMARY_MODEL=claude-sonnet-4-6

# Nebius / Llama as cheap tier
OPENAI_CHEAP_API_KEY=<nebius-key>
LLM_CHEAP_BASE_URL=https://api.studio.nebius.com/v1
LLM_CHEAP_MODEL=claude-haiku-4-5

# Local vLLM as cheap tier
OPENAI_CHEAP_API_KEY=dummy
LLM_CHEAP_BASE_URL=http://localhost:8000/v1
LLM_CHEAP_MODEL=my-local-model
```

## Testing

```bash
# Install test dependencies
uv sync --extra test

# Run unit tests (no agent required)
uv run pytest tests/ --ignore=tests/test_agent.py -v

# Start the agent, then run A2A conformance tests
uv run pytest --agent-url http://localhost:9009
```

## Publishing

The repository includes a GitHub Actions workflow that automatically builds, tests, and publishes a Docker image to GitHub Container Registry.

Add your API keys in **Settings → Secrets and variables → Actions → Repository secrets**.

- **Push to `main`** → publishes `latest` tag:
```
ghcr.io/madgaa-lab/crm-agent-phase2_dev:latest
```

- **Create a git tag** (e.g. `git tag v1.0.0 && git push origin v1.0.0`) → publishes version tags:
```
ghcr.io/madgaa-lab/crm-agent-phase2_dev:1.0.0
ghcr.io/madgaa-lab/crm-agent-phase2_dev:1
```
