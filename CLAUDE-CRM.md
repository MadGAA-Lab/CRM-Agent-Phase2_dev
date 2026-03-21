# CLAUDE.md — CRM Purple Agent for AgentX–AgentBeats Phase 2

## Project Overview

Build a **purple (competing) agent** for the **Business Process Agent** track in the Berkeley RDI AgentX–AgentBeats Phase 2 competition. The agent will be evaluated by the **Entropic CRMArena** green agent on 2,140 CRM tasks across 22 categories, with adversarial robustness challenges (Schema Drift + Context Rot).

**Goal**: Beat the current leaderboard leader (41.8% functional, 60.2 7D score at medium/medium drift/rot) by building a **Hybrid ReAct agent with real SQLite database + schema drift/rot defenses**.

---

## Competition Context

- **Platform**: AgentBeats (https://agentbeats.dev)
- **Green Agent**: Entropic CRMArena — AgentBeats ID `019ba211-13b7-7e83-9086-c8015a5e4957`
- **Green Agent Repo**: https://github.com/rkstu/entropic-crmarenapro
- **Baseline Agent Repo**: https://github.com/rkstu/purple-crm-agent (submodule at `third_party/purple-crm-agent`)
- **Agent Template**: https://github.com/RDI-Foundation/agent-template
- **Sprint 1 Deadline**: March 22, 2026
- **Protocol**: A2A (Agent-to-Agent) — JSON-RPC over HTTP

---

## Key Insight: Why the Previous Approach Failed

The previous implementation used **single-shot LLM reasoning over `required_context` text**. This scored near 0 because:

1. **No database** — tasks like "which month had the most cases for product X over 6 quarters" require SQL aggregation over hundreds of records, not prose reasoning
2. **Single LLM call** — no iterative tool use, no ability to explore data
3. **"insufficient data"** — the LLM correctly identified it couldn't do aggregations from text

The baseline agent (`third_party/purple-crm-agent`) scores ~41.8% functional by using:
- A **real SQLite database** built from the CRMArenaPro dataset
- A **ReAct loop** (think → execute SQL → observe → repeat, up to 8 turns)
- **Schema-aware system prompt** with join relationships

**Our edge over the baseline**: The baseline ignores drift/rot entirely. Drift Adaptation is worth 20% of the 7D score and the leader only gets 17.3. Our schema introspector + context filter can capture those free points.

---

## Architecture: Hybrid ReAct + SQLite

```
Green Agent (CRMArena)
         │  A2A JSON-RPC
         ▼
┌──────────────────────────────────────────────────┐
│              Purple Agent (our agent)            │
│                                                  │
│  ┌─ Fast Path ──────────────────────────────────┐│
│  │ Privacy Guard (rule-based, no LLM)           ││
│  │ → Instant rejection for privacy categories   ││
│  └──────────────────────────────────────────────┘│
│                    │ (not privacy)               │
│  ┌─ Pre-processing ─────────────────────────────┐│
│  │ Schema Introspector (drift mapping)          ││
│  │ Context Filter (rot note stripping)          ││
│  │ → Injects drift warnings into system prompt  ││
│  │ → Cleans required_context for LLM            ││
│  └──────────────────────────────────────────────┘│
│                    │                             │
│  ┌─ Core: ReAct Loop (max 8 turns) ─────────────┐│
│  │ System prompt with:                          ││
│  │   - Schema relationships & join patterns     ││
│  │   - Drift warnings (if drift detected)       ││
│  │   - required_context as supplemental data    ││
│  │                                              ││
│  │ Tools (text-based tags):                     ││
│  │   <execute> SQL </execute>  → SQLite query   ││
│  │   <describe> Table </describe> → PRAGMA info ││
│  │   <respond> answer </respond> → final answer ││
│  │                                              ││
│  │ Loop: LLM thinks → picks tool → observe →    ││
│  │       LLM thinks → ... → <respond>           ││
│  └──────────────────────────────────────────────┘│
│                    │                             │
│  ┌─ Post-processing ───────────────────────────┐ │
│  │ Fallback answer extraction (if no <respond>)│ │
│  │ Metrics collection                          │ │
│  └─────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
         │  A2A Response
         ▼
Green Agent (scores the answer)
```

### What We Keep (from previous implementation)
- **Privacy Guard** — rule-based, zero LLM calls, instant rejection (FIX: remove `internal_operation_data` from rejection list — it uses exact_match, not privacy_rejection)
- **Schema Introspector** — drift mapping with known drift tables; simplified role: generates drift warning text + reverse mapping dict for context de-drifting
- **Context Filter** — rot note stripping (brackets patterns)
- **LLM Client** — dual-tier async OpenAI-compatible client (use existing `AsyncOpenAI`, not sync)
- **Server/Executor/Messenger** — A2A scaffolding

### What We Replace
- ~~Single-shot sql_generator~~ → **ReAct loop with real SQL execution**
- ~~task_planner (3-strategy)~~ → **Simplified: privacy vs ReAct**
- ~~answer_synthesizer~~ → **Integrated into ReAct `<respond>` step**
- ~~error_recovery (retry wrapper)~~ → **Built into ReAct loop (SQL errors become observations)**

### What We Add
- **`db_builder.py`** — Downloads CRMArenaPro from HuggingFace, builds SQLite at startup
- **`crm_database.py`** — SQLite connection, query execution, table description
- **ReAct executor** — Multi-turn LLM loop with tool parsing (in `agent.py`)

---

## File Structure

```
src/
├── server.py               # A2A server setup + agent card (keep)
├── executor.py              # A2A request handling (keep)
├── agent.py                 # Main orchestrator — Hybrid ReAct pipeline (rewrite)
├── messenger.py             # A2A messaging utilities (keep)
├── db_builder.py            # NEW: Build SQLite from HuggingFace dataset at startup
├── crm_database.py          # NEW: SQLite connection + query execution
├── schema_introspector.py   # Drift detection — inject warnings into ReAct prompt (simplify)
├── context_filter.py        # Rot note stripping (keep, simplify)
├── privacy_guard.py         # Rule-based privacy rejection (keep as-is)
└── llm_client.py            # Dual-tier LLM client (keep)
config/
├── schema.json              # Canonical DB schema definition (keep)
└── prompts.yaml             # REMOVED — system prompt lives in agent.py now
data/
└── crmarenapro_b2b_data.db  # Built at startup by db_builder.py (gitignored)
```

### Removed Files
- `task_planner.py` — replaced by simple privacy check in agent.py
- `sql_generator.py` — replaced by ReAct loop with real SQL
- `answer_synthesizer.py` — replaced by ReAct `<respond>` tag
- `error_recovery.py` — error recovery is natural in ReAct (SQL errors become observations)

---

## Step 1: Database Builder (`src/db_builder.py`)

Downloads the Salesforce/CRMArenaPro dataset from HuggingFace and builds a SQLite database at container startup.

**Behavior:**
1. Called in `server.py` **before** `uvicorn.run()` — server does NOT accept requests until DB is ready
2. Check if `data/crmarenapro_b2b_data.db` exists and is valid
3. If not: download from HuggingFace `Salesforce/CRMArenaPro` dataset (no auth required)
4. Extract CRM records, create SQLite tables with canonical column names
5. Tables: Account, Case, Contact, Lead, Opportunity, User, Order, OrderItem, Product2, VoiceCallTranscript__c, Knowledge__kav, Issue__c, CaseHistory__c, Territory2
6. Use file lock to prevent race conditions on concurrent builds
7. Cache the `.db` file for subsequent runs (~10-15s first boot, instant after)
8. **Fallback**: If download fails, log error and start anyway — agent degrades to LLM-only reasoning over `required_context`

**Key**: The database uses **canonical column names** (not drifted). Schema drift only affects the `required_context` text, not the actual database. The ReAct agent queries the real DB with real column names, then uses drift knowledge to interpret context clues.

---

## Step 2: CRM Database (`src/crm_database.py`)

SQLite wrapper with query execution and table introspection.

```python
class CRMDatabase:
    def __init__(self, db_path, org_type="b2b")
    def get_tables(self) -> List[str]
    def describe_table(self, table_name) -> Dict  # columns, types, row count
    def execute_query(self, sql) -> Dict           # {success, data, count} or {success, error}
    def close(self)
```

**Safety**:
- Open in **read-only mode**: `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`
- Set `PRAGMA query_only = ON`
- Use `conn.set_progress_handler()` to abort queries exceeding ~5 seconds
- Initialize **once** in `Agent.__init__()`, share across all tasks (`check_same_thread=False`)
- Strip markdown fences, trailing semicolons from LLM-generated SQL
- Limit result rows to 15

---

## Step 3: ReAct Agent (`src/agent.py`)

The core reasoning engine. Replaces the entire 5-layer pipeline with a hybrid approach.

### Flow:
1. Parse incoming A2A message → extract task JSON
2. **Privacy check** (rule-based) → instant rejection if privacy category
3. **Schema introspection** → detect drift, build warning text
4. **Context filter** → strip rot notes from `required_context`
5. **Build ReAct messages**:
   - System prompt with schema, relationships, drift warnings, tools
   - User message with question + cleaned context
6. **ReAct loop** (max 8 turns):
   - Call LLM → get response with `<thought>`, `<execute>`/`<describe>`/`<respond>`
   - If `<execute>`: run SQL, append observation
   - If `<describe>`: return table schema, append observation
   - If `<respond>`: extract answer, break
   - If no valid action: prompt for one
7. **Fallback**: If no `<respond>` after max turns, extract best answer from last response
8. Return A2A artifact with answer + metrics

### System Prompt Design:

The system prompt includes:
- **Schema relationships** (Case→OrderItem→Product2, Lead→Transcript, etc.)
- **Tool definitions** (`<execute>`, `<describe>`, `<respond>`)
- **Database tables list** (from `db.get_tables()`)
- **Drift warnings** (if drift detected, e.g. "OwnerId may appear as AssignedAgent in context")
- **Key SQL patterns** for common task categories
- **Response format rules** (concise answers only)

### Drift Integration (Our Competitive Edge):

Unlike the baseline which ignores drift entirely, we use a two-layer approach:

**Layer 1 — Deterministic reverse mapping** (reliable baseline):
- Apply reverse drift mapping to `required_context` text before passing to LLM
- Replace drifted names back to canonical: "AssignedAgent" → "OwnerId", "StatusCode" → "Status", etc.
- The LLM sees consistent canonical names in both DB and context

**Layer 2 — Prompt-based drift awareness** (safety net):
- Drift warnings injected into system prompt:
  ```
  ⚠️ SCHEMA DRIFT (medium): Context may use renamed columns.
  Known renames: OwnerId→AssignedAgent, Status→StatusCode, AccountId→ClientId
  The DATABASE uses canonical names. Use <describe> if unsure about column names.
  ```
- The LLM can use `<describe>` to verify actual DB column names when uncertain

### Context as Supplementary Data:

The `required_context` is NOT the primary data source — the SQLite DB is. But context serves as:
- **Domain definitions** (what "past 6 quarters" means, BANT criteria, etc.)
- **Transcript text** (voice call transcripts referenced in tasks)
- **Task-specific hints** (which Lead ID, which Product, which time period)
- **Supplementary records** that may not be in the DB

---

## Step 4: Scoring Optimization

### 7D Scoring Targets:

| Dimension | Leader | Baseline | Our Target | Strategy |
|-----------|--------|----------|------------|----------|
| FUNCTIONAL (30%) | 41.8 | ~41.8 | 65+ | Real DB + ReAct = accurate answers |
| DRIFT_ADAPTATION (20%) | 17.3 | ~0 | 60+ | Schema introspector + drift-aware prompts |
| TOKEN_EFFICIENCY (12%) | 99.6 | ~80 | 90+ | Gemini (cheap tokens), concise prompts |
| QUERY_EFFICIENCY (12%) | 100.0 | ~70 | 85+ | Targeted SQL, max 8 turns |
| ERROR_RECOVERY (8%) | 44.5 | ~50 | 70+ | SQL errors as observations, graceful fallback |
| HALLUCINATION_RATE (8%) | 83.8 | ~80 | 90+ | Text-based tools (no invalid function calls) |
| TRAJECTORY_EFFICIENCY (10%) | 100.0 | ~60 | 80+ | Schema-aware prompts reduce exploration turns |

### Key Optimizations:
1. **Privacy = free points** — rule-based, 0 tokens, 100% accurate
2. **DESCRIBE before query** — reduces failed queries (ERROR_RECOVERY + HALLUCINATION)
3. **Drift warnings in prompt** — the LLM knows to map drifted context names to DB names
4. **Context as domain guide** — tells the LLM what time periods, IDs, and criteria to use
5. **Concise system prompt** — category-specific SQL examples reduce exploration turns

---

## Step 5: A2A Protocol

### Incoming Task Format (from green agent)

```json
{
  "type": "crm_task",
  "task_id": "456",
  "task_category": "sales_insight_mining",
  "prompt": "Which competitors are we at a disadvantage against?",
  "persona": "You are detail-oriented and methodical.",
  "required_context": "Domain information, transcripts, and CRM records...",
  "config": { "org_type": "b2b", "max_steps": 15 },
  "entropy": { "drift_level": "medium", "rot_level": "medium" }
}
```

### Response Format

```json
{
  "task_id": "456",
  "answer": "Quantum Circuits Inc.",
  "category": "sales_insight_mining",
  "metrics": { "tokens": 5000, "tool_calls": 3, "queries": 2 }
}
```

---

## Step 6: Docker & Deployment

### Dockerfile

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm

RUN adduser agent
USER agent
WORKDIR /home/agent

COPY pyproject.toml uv.lock README.md ./
COPY src src
COPY config config

RUN \
    --mount=type=cache,target=/home/agent/.cache/uv,uid=1000 \
    uv sync --locked

# DB is built at runtime from HuggingFace (cached in /home/agent/data/)
ENTRYPOINT ["uv", "run", "src/server.py"]
CMD ["--host", "0.0.0.0"]
EXPOSE 9009
```

### Dependencies (pyproject.toml)

```toml
[project]
dependencies = [
    "a2a-sdk",
    "uvicorn",
    "httpx",
    "openai",        # OpenAI-compatible client (Gemini, Claude, etc.)
    "pyyaml",
    "datasets",      # HuggingFace datasets for DB building
]
```

---

## Step 7: Environment Variables

```bash
# Primary LLM tier (required)
OPENAI_PRIMARY_API_KEY=...                                          # API key
LLM_PRIMARY_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/  # Gemini
LLM_PRIMARY_MODEL=gemini-3.1-pro-preview                           # Primary model

# Cheap LLM tier (optional — falls back to primary)
OPENAI_CHEAP_API_KEY=...                                            # Optional
LLM_CHEAP_BASE_URL=...                                              # Optional
LLM_CHEAP_MODEL=gemini-3.1-flash-lite-preview                      # Cheap model

# Server
HOST=0.0.0.0
PORT=9009
```

---

## Step 8: Local Testing

### Docker Compose (existing setup)

```bash
docker compose up --exit-code-from agentbeats-client --abort-on-container-exit
```

### Progressive testing

```bash
# 1 task, no entropy — verify basic functionality
config: {"task_limit": 1}

# 5 tasks, medium drift/rot — match competition conditions
config: {"task_limit": 5}

# 20 tasks — meaningful accuracy measurement
config: {"task_limit": 20}

# Full run — final validation
config: {"task_limit": 2140}
```

---

## Development Priorities (in order)

1. **Build `db_builder.py`** — download CRMArenaPro, create SQLite, verify tables exist
2. **Build `crm_database.py`** — SQLite wrapper with query/describe/tables
3. **Rewrite `agent.py`** — Hybrid ReAct loop with real SQL execution
4. **Wire drift into ReAct** — inject drift warnings from schema_introspector into system prompt
5. **Wire context into ReAct** — pass cleaned required_context as supplemental data in user message
6. **Test with 1 task** — verify end-to-end with Docker, check answer is not "insufficient data"
7. **Test with 5 tasks** — measure functional accuracy, check drift handling
8. **Optimize system prompt** — add category-specific SQL examples, tune for accuracy
9. **Test with 20 tasks** — validate 65+ functional target
10. **Docker + submission** — containerize and submit to leaderboard

---

## Success Criteria

- [ ] SQLite database builds from HuggingFace at startup (<15s)
- [ ] Agent responds to A2A requests without errors
- [ ] Privacy categories return rejection (100% accuracy)
- [ ] ReAct loop executes real SQL queries against SQLite
- [ ] Functional accuracy > 50% at medium/medium drift/rot (5-task sample)
- [ ] Drift Adaptation > 40% (beating leader's 17.3)
- [ ] 7D Score > 65 (beating leader's 60.2)
- [ ] Docker image builds and runs on linux/amd64
- [ ] Leaderboard submission accepted

---

## CRM Database Schema

### Core Tables

| Table | Key Columns |
|---|---|
| Account | Id, Name, BillingState, OwnerId |
| Contact | Id, Name, AccountId, OwnerId |
| Lead | Id, Name, Status, OwnerId, IsConverted, ConvertedDate |
| Case | Id, Subject, AccountId, ContactId, OwnerId, OrderItemId__c, Status, CreatedDate, ClosedDate |
| Opportunity | Id, Name, StageName, Amount, AccountId, OwnerId, CloseDate, CreatedDate |
| OrderItem | Id, OrderId, Product2Id |
| Product2 | Id, Name, ProductCode |
| VoiceCallTranscript__c | Id, Body__c, LeadId__c, OpportunityId__c, CreatedDate |
| Knowledge__kav | Id, Title, ArticleBody |
| User | Id, Name, Username |
| CaseHistory__c | Id, CaseId, OldValue, NewValue, Field, CreatedDate |
| Territory2 | Id, Name |
| Issue__c | Id, Name, Description |
| Order | Id, AccountId, Status |

### Key Relationships (critical for JOINs)

```
Case.OrderItemId__c → OrderItem.Id → OrderItem.Product2Id → Product2.Id  (case → product)
Case.AccountId → Account.Id                                               (case → company)
Case.ContactId → Contact.Id                                               (case → person)
Case.OwnerId → User.Id                                                    (case → agent)
Lead.Id → VoiceCallTranscript__c.LeadId__c                               (lead → transcripts)
Opportunity.Id → VoiceCallTranscript__c.OpportunityId__c                  (opp → transcripts)
Contact.AccountId → Account.Id                                            (contact → company)
Opportunity.AccountId → Account.Id                                        (deal → company)
```

### Schema Drift Mappings (medium level — competition default)

| Table.Column | Drifted Name |
|---|---|
| Account.OwnerId | AssignedAgent |
| Contact.AccountId | ClientId |
| Contact.OwnerId | AssignedAgent |
| Lead.Status | StatusCode |
| Lead.OwnerId | AssignedAgent |
| Case.Subject | Title |
| Case.Description | Details |
| Case.Status | StatusCode |
| Case.AccountId | ClientId |
| Case.ContactId | PersonRef |
| Case.OwnerId | AssignedAgent |
| Opportunity.AccountId | ClientId |
| Opportunity.OwnerId | AssignedAgent |
| OrderItem.Description | Details |
| Product2.Description | Details |

### 22 Task Categories

**Exact Match** (16 categories):
lead_qualification, lead_routing, case_routing, handle_time, transfer_count,
sales_insight_mining, monthly_trend_analysis, best_region_identification,
conversion_rate_comprehension, named_entity_disambiguation, activity_priority,
invalid_config, policy_violation_identification, quote_approval,
sales_amount_understanding, sales_cycle_understanding, top_issue_identification,
wrong_stage_rectification

**Fuzzy Match** (1 category):
knowledge_qa

**Privacy Rejection** (2 categories — must REFUSE to answer):
private_customer_information, confidential_company_knowledge

**Safety/Other** (3 categories):
internal_operation_data — exact_match (but may require refusal depending on context)
