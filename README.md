# CRM Purple Agent — AgentX Phase 2

A CRM agent for Berkeley RDI **AgentX-AgentBeats Phase 2** competition. Evaluated by the [Entropic CRMArena](https://github.com/rkstu/entropic-crmarenapro) green agent across 2,140 CRM tasks (22 categories) with schema drift and context rot resistance.

> 繁體中文說明請見 [README.zh-TW.md](README.zh-TW.md)

## Architecture

The agent uses a **hybrid deterministic + LLM** architecture:

1. **Privacy Guard** — Rule-based instant rejection for 2 privacy categories (zero LLM calls)
2. **Deterministic Handlers** — Fixed SQL templates for 7 structured categories (handle_time, lead_routing, sales_cycle, conversion_rate, sales_amount, best_region, transfer_count)
3. **LLM ReAct Loop** — For categories requiring reasoning (transcript analysis, quote approval, knowledge QA, etc.)

```
Incoming A2A Task
    |
    v
[1] Privacy Guard (rule-based) ──> Instant rejection
    |
[2] Schema Drift Detection ──> Reverse-map drifted column names
    |
[3] Context Rot Filtering ──> Strip noise notes
    |
[4] Category Router
    |
    ├── Deterministic Handler? ──> Fixed SQL + parameter extraction ──> Answer
    |
    └── LLM ReAct Loop (max 8 turns)
        ├── <execute> SQL </execute>
        ├── <describe> Table </describe>
        └── <respond> Answer </respond>
```

### Why Hybrid?

Pure LLM SQL generation is unreliable — the LLM produces garbage answers (date fragments, column names) for structured queries. 
Pure deterministic misses edge cases. 
The hybrid approach:

- **Deterministic handlers** provide reliable, fast answers for well-defined categories (100% on lead_routing, 70% on handle_time)
- **LLM fallback** handles the long tail — transcript analysis, policy reasoning, ambiguous queries
- **Category-specific prompts** give the LLM only the relevant schema/joins, reducing token waste and hallucination

## Project Structure

```
src/
├── server.py                 # A2A server (Uvicorn) + agent card
├── executor.py               # A2A request handler with timeout
├── agent.py                  # Pipeline orchestrator (privacy → drift → rot → route)
├── deterministic_handlers.py # SQL handlers for 7 structured categories
├── privacy_guard.py          # Rule-based privacy rejection (2 categories)
├── schema_introspector.py    # Drift mapping (low / medium / high)
├── context_filter.py         # Rot note stripping + heuristic filtering
├── crm_database.py           # Read-only SQLite wrapper with safety guards
├── db_builder.py             # Database download from HuggingFace
├── llm_client.py             # Dual-tier LLM client (primary + cheap fallback)
├── time_budget.py            # Dynamic per-task timeout allocation
└── messenger.py             # A2A messaging utilities
config/
├── schema.json               # Canonical CRM schema (27 tables, extracted from DB)
└── prompts.yaml              # Category-routed prompt templates
data/
└── crmarenapro_b2b_data.db   # SQLite database (downloaded at startup)
tests/
├── test_e2e_mock.py          # End-to-end mock tests
├── test_privacy.py           # Privacy guard tests
├── test_introspector.py      # Schema introspector tests
├── test_context_filter.py    # Context filter tests
├── test_time_budget.py       # Time budget tests
└── test_agent.py             # A2A conformance tests
```

### Deterministic Handlers

| Category | Accuracy | Approach |
|---|---|---|
| `lead_routing` | 100% | Territory match → quote success → workload tiebreak |
| `handle_time` | 70% | Transfer-aware case counting + non-transferred handle time |
| `sales_amount_understanding` | 70% | Order + OrderItem join, grouped by agent |
| `best_region_identification` | 60% | Case → Account join, grouped by ShippingState |
| `conversion_rate_comprehension` | 55% | Lead conversion with ConvertedDate window check |
| `transfer_count` | 45% | CaseHistory owner-change tracking per agent |
| `sales_cycle_understanding` | 10% | Opportunity → Contract CompanySignedDate |

Handlers extract parameters from the question (time period, threshold, direction) via regex, build parameterized SQL, and execute directly — no LLM involved.

## Getting Started

### Running Locally

```bash
# Install dependencies
uv sync

# Set API key (create .env or export)
export OPENAI_PRIMARY_API_KEY=sk-...

# Start the agent (default port 9010)
uv run src/server.py --host 127.0.0.1 --port 9010
```

### Running with Docker

```bash
# Build the image
docker build -t crm-purple-agent .

# Run the container
docker run -p 9010:9010 \
  -e OPENAI_PRIMARY_API_KEY=sk-... \
  crm-purple-agent --host 0.0.0.0 --port 9010
```

### Running the Benchmark

```bash
# Pull the green agent evaluator
docker pull ghcr.io/rkstu/entropic-crmarena-green:latest

# Start green agent (port 9009)
docker run -d --name green-agent -p 9009:9009 \
  --add-host=host.docker.internal:host-gateway \
  -e OPENAI_API_KEY=<your-openai-key> \
  ghcr.io/rkstu/entropic-crmarena-green:latest --host 0.0.0.0 --port 9009

# Start purple agent locally (port 9010)
uv run src/server.py --host 0.0.0.0 --port 9010 \
  --card-url "http://host.docker.internal:9010/"

# Trigger evaluation (20 tasks)
curl -X POST http://127.0.0.1:9009/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "id": "bench",
    "params": {
      "message": {
        "messageId": "bench",
        "role": "user",
        "parts": [{"kind": "text",
          "text": "{\"participants\": {\"agent\": \"http://host.docker.internal:9010/\"}, \"config\": {\"task_limit\": 20}}"}]
      }
    }
  }'
```

## Environment Variables

### API Keys

| Variable | Purpose | Required |
|---|---|---|
| `OPENAI_PRIMARY_API_KEY` | Key for the primary LLM provider | Yes |
| `OPENAI_CHEAP_API_KEY` | Key for the cheap/fast fallback provider | Optional |

### Model Configuration

| Variable | Default | Description |
|---|---|---|
| `LLM_PRIMARY_BASE_URL` | _(OpenAI)_ | Base URL for primary provider |
| `LLM_CHEAP_BASE_URL` | _(OpenAI)_ | Base URL for cheap provider |
| `LLM_PRIMARY_MODEL` | `claude-sonnet-4-6` | Primary model |
| `LLM_CHEAP_MODEL` | `claude-haiku-4-5` | Cheap/fast model |

### Time Budget

| Variable | Default | Description |
|---|---|---|
| `TIME_BUDGET_TOTAL_MIN` | `4320` | Total budget in minutes (72 hours) |
| `TIME_BUDGET_TOTAL_TASKS` | `2140` | Total tasks for budget allocation |
| `TIME_BUDGET_CAP_SEC` | `300` | Max seconds per task |

## Testing

```bash
# Unit tests (no agent required)
uv run pytest tests/ --ignore=tests/test_agent.py -v

# A2A conformance tests (start agent first)
uv run pytest --agent-url http://localhost:9010
```

## Publishing

The repository includes a GitHub Actions workflow that automatically builds, tests, and publishes a Docker image to GitHub Container Registry.

Add your API keys in **Settings > Secrets and variables > Actions > Repository secrets**.

- **Push to `main`** → publishes `latest` tag:
```
ghcr.io/madgaa-lab/crm-agent-phase2_dev:latest
```

- **Create a git tag** (e.g. `git tag v1.0.0 && git push origin v1.0.0`) → publishes version tags:
```
ghcr.io/madgaa-lab/crm-agent-phase2_dev:1.0.0
ghcr.io/madgaa-lab/crm-agent-phase2_dev:1
```
