# Design: Hybrid ReAct + SQLite CRM Agent

**Date**: 2026-03-21
**Status**: Draft — pending user review
**Goal**: Boost functional accuracy from near-0 to 65+ by replacing single-shot LLM reasoning with a ReAct loop backed by a real SQLite database, while retaining schema drift and context rot defenses as a competitive edge.

---

## Problem Statement

The current CRM agent scores near 0% functional accuracy because it attempts to answer complex CRM queries (aggregations, trend analysis, multi-table joins) by reasoning over prose text in `required_context`. This fundamentally cannot work for tasks like "which month had the most cases for product X over 6 quarters" — those require actual data queries.

The baseline agent (`third_party/purple-crm-agent`) achieves ~41.8% functional by using a real SQLite database + ReAct loop, but ignores schema drift/rot entirely, leaving 20% of the 7D score on the table.

## Solution: Hybrid ReAct

Combine the baseline's proven database + ReAct approach with our existing drift/rot defenses.

### Components

#### 1. Database Builder (`src/db_builder.py`) — NEW

Downloads the `Salesforce/CRMArenaPro` dataset from HuggingFace and builds a SQLite database.

**How the HuggingFace dataset maps to SQLite:**
- The dataset has 4 splits: `b2b`, `b2c`, `b2b_interactive`, `b2c_interactive`
- Each split contains task records with `metadata.required` (context text) and `metadata.optional` (supplementary domain info)
- The **actual CRM data** (Account, Case, Lead, etc.) is stored as structured records within those metadata fields — it needs to be parsed out
- Alternatively, the green agent's `crm/tasks.py` shows the dataset structure — each record has `idx`, `query`, `answer`, `task`, `reward_metric`, `persona`, `metadata`

**However**: The HuggingFace dataset contains *task definitions*, not raw CRM tables. The actual CRM database (SQLite with Account/Case/Lead tables) must be reconstructed from the data embedded across all task metadata fields, OR sourced from a companion data artifact.

**Startup strategy (build at server startup, before accepting requests):**
1. Check if `data/crmarenapro_{org_type}_data.db` exists and is valid
2. If not: download dataset, extract CRM records from task metadata, build SQLite tables
3. Use a file lock to prevent race conditions if multiple processes start simultaneously
4. Server does NOT accept requests until DB is ready (build happens in `server.py` before `uvicorn.run()`)
5. Cache the `.db` file for subsequent runs

**Fallback if download fails:**
- Log error, start server anyway
- Agent falls back to LLM-only reasoning over `required_context` (degraded but functional)
- Consider: pre-build DB into Docker image as a build step if network reliability is a concern

**Tables to create**: Account, Case, Contact, Lead, Opportunity, User, Order, OrderItem, Product2, VoiceCallTranscript__c, Knowledge__kav, Issue__c, CaseHistory__c, Territory2

**Key design decision**: The database uses **canonical column names** (not drifted). Schema drift only affects the `required_context` text, not the actual database.

#### 2. CRM Database (`src/crm_database.py`) — NEW

Thin SQLite wrapper with safety guarantees.

- `get_tables() -> List[str]` — list all tables
- `describe_table(name) -> Dict` — columns, types, row count via PRAGMA
- `execute_query(sql) -> Dict` — execute read-only SQL, return up to 15 rows
- Tracks `query_count` and `failed_queries` for metrics
- SQL sanitization: strip markdown fences, trailing semicolons

**Safety requirements:**
- Open connection in **read-only mode**: `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`
- Set `PRAGMA query_only = ON` as additional guard
- Use `conn.set_progress_handler()` to abort queries exceeding ~5 seconds
- Initialize **once** in `Agent.__init__()`, share across all tasks (with `check_same_thread=False`)

#### 3. Agent ReAct Loop (`src/agent.py`) — REWRITE

Replaces the 5-layer pipeline with:

```
parse_task → privacy_check → drift_introspect → rot_filter → build_messages → react_loop → return
```

**Task parsing**: Preserve the existing robust JSON parsing from current agent.py (direct parse → brace-depth scanning → raw prompt fallback).

**ReAct loop** (max 8 turns):
1. Call LLM with messages (system + conversation history)
2. Parse response for `<thought>`, `<execute>`, `<describe>`, `<respond>` tags
3. If `<execute>`: run SQL against SQLite, append result as observation
4. If `<describe>`: return table schema via PRAGMA, append as observation
5. If `<respond>`: extract answer, exit loop
6. If no valid action and near max turns: extract fallback answer
7. Append assistant response + user observation to messages, continue

**LLM calls**: Use the existing `LLMClient` (async, dual-tier). The ReAct loop calls `self.llm.call()` for each turn. Do NOT create a separate OpenAI client.

**System prompt** includes:
- Schema relationships (Case→OrderItem→Product2, etc.)
- Tool definitions with examples
- Database tables list (dynamic from `db.get_tables()`)
- Drift warnings (dynamic from schema introspector, if drift detected)
- Key SQL patterns for common categories (monthly trend, handle time, lead qualification, knowledge QA)
- Response format rules (concise answers, Salesforce ID patterns)
- Persona from the task (appended to system prompt)

**Drift integration** (competitive edge):
- Schema introspector detects drift level + known mappings
- Drift warnings injected into system prompt with both approaches:
  1. **Deterministic**: Apply reverse drift mapping to `required_context` text before passing to LLM (replace "AssignedAgent" back to "OwnerId" etc.) — reliable baseline
  2. **Prompt-based**: Also tell the LLM about drift in the system prompt as a safety net
- The LLM can use `<describe>` to verify actual DB column names when uncertain

**Context integration**:
- `required_context` (cleaned of rot notes, optionally reverse-drift-mapped) passed in user message as "Context"
- Serves as domain definitions, transcript text, ID references, time period specs
- NOT the primary data source — the SQLite DB is
- Do NOT blindly truncate — use the existing context_filter's heuristic relevance scoring to keep important sections if context exceeds ~4000 chars

**knowledge_qa handling**:
- System prompt includes a specific pattern for searching Knowledge__kav:
  ```sql
  SELECT Title, ArticleBody FROM Knowledge__kav WHERE ArticleBody LIKE '%keyword%'
  ```
- Fuzzy match answers should be concise prose grounded in the article content

#### 4. Schema Introspector (`src/schema_introspector.py`) — SIMPLIFY

Reduced role:
- Detects drift level from `task.entropy.drift_level`
- Returns a human-readable drift warning string for the system prompt
- Provides reverse mapping dict for deterministic context de-drifting
- Keeps the known drift mapping tables (low/medium/high)
- No longer does fuzzy column discovery (the DB has canonical names)
- Can optionally discover actual DB schema via `db.describe_table()` to update `config/schema.json` dynamically

#### 5. Context Filter (`src/context_filter.py`) — KEEP

Same role: strip rot notes (`[Note:...]`, `[System Notice:...]`, etc.) from `required_context` before passing to the LLM. Heuristic section filtering stays as a secondary defense for long contexts.

#### 6. Privacy Guard (`src/privacy_guard.py`) — FIX

**Bug fix**: Remove `internal_operation_data` from `PRIVACY_CATEGORIES`. This category uses `exact_match` evaluation — many tasks expect real answers, not rejections. Rejecting them all loses points.

Updated privacy categories (only these two):
- `private_customer_information`
- `confidential_company_knowledge`

#### 7. LLM Client (`src/llm_client.py`) — KEEP

Dual-tier OpenAI-compatible async client. Primary tier (`gemini-3.1-pro-preview`) for ReAct reasoning, cheap tier (`gemini-3.1-flash-lite-preview`) for context filtering (if needed). Already uses `AsyncOpenAI` — no event loop blocking issues.

### Removed Components

| Component | Reason |
|---|---|
| `task_planner.py` | Replaced by simple privacy check in agent.py |
| `sql_generator.py` | Replaced by ReAct loop with real SQL execution |
| `answer_synthesizer.py` | Replaced by ReAct `<respond>` tag |
| `error_recovery.py` | SQL errors naturally become observations in ReAct loop |
| `config/prompts.yaml` | System prompt lives in agent.py now (single source of truth) |

### Fallback Answer Extraction

If the ReAct loop exhausts max turns without `<respond>`:
1. Check last `<thought>` for a clear answer statement
2. Look for Salesforce ID pattern (15-18 char alphanumeric — but only in the last response, not in reasoning about multiple IDs)
3. Look for month names
4. Look for BANT factors (Budget, Authority, Need, Timeline)
5. Look for quoted strings
6. Default to "None"

### Required Dependency Changes

Add to `pyproject.toml`:
- `datasets` — HuggingFace datasets library for DB building

### Schema Updates

Update `config/schema.json` to include all 14 tables with complete column lists and foreign keys:
- Add: User, Order, CaseHistory__c, Territory2, Issue__c, Knowledge__kav
- Add FK: VoiceCallTranscript__c.OpportunityId__c
- Add: Lead.IsConverted, Lead.ConvertedDate

## Scoring Impact

| Dimension | Leader | Expected | Strategy |
|---|---|---|---|
| FUNCTIONAL (30%) | 41.8 | 55-65 | Real DB queries instead of text reasoning |
| DRIFT_ADAPTATION (20%) | 17.3 | 50-60 | Reverse drift mapping + drift-aware prompts + DESCRIBE |
| TOKEN_EFFICIENCY (12%) | 99.6 | 80-85 | Multi-turn ReAct uses more tokens than single-shot; Gemini helps |
| QUERY_EFFICIENCY (12%) | 100.0 | 75-80 | ReAct may take 3-5 queries per task; schema-aware prompt helps |
| ERROR_RECOVERY (8%) | 44.5 | 65-75 | SQL errors become observations, agent self-corrects |
| HALLUCINATION_RATE (8%) | 83.8 | 85-90 | Text-based tools, grounded in DB results |
| TRAJECTORY_EFFICIENCY (10%) | 100.0 | 65-75 | Multi-turn loop costs trajectory; category-specific examples help |

**Projected 7D score: ~63-70** (vs leader's 60.2)

Note: TOKEN/QUERY/TRAJECTORY efficiency will be lower than single-shot approaches because ReAct inherently uses multiple turns. The tradeoff is worth it — FUNCTIONAL (30% weight) and DRIFT_ADAPTATION (20% weight) gains far outweigh the efficiency losses.

## Testing Strategy

1. Build DB, verify all 14 tables populated with expected row counts
2. Run 1 task — verify agent produces non-"insufficient data" answer
3. Run 5 tasks — measure functional accuracy, verify drift handling works
4. Run 20 tasks — validate functional > 50%
5. Compare scores against baseline agent on same 5 tasks
6. Test Gemini tag-following reliability early (does it produce valid `<execute>`/`<respond>` tags?)

## Risks

1. **HuggingFace download reliability** — may fail in sandboxed competition environments. Mitigation: build DB at `docker build` time as a layer, OR cache in a persistent volume, OR fall back to LLM-only reasoning.
2. **Gemini + text-based tools** — Gemini may not follow `<execute>`/`<respond>` tag format reliably. Mitigation: strong examples in system prompt, fallback extraction, test early.
3. **DB schema mismatch** — HuggingFace dataset may not contain all 14 tables as clean records. Mitigation: use `DESCRIBE` in the agent, adapt dynamically, verify during DB build.
4. **Context window growth** — Each ReAct turn appends ~500-2000 chars to the conversation. With 8 turns + system prompt + context, this could approach limits. Mitigation: cap conversation history to last 4 turns if approaching limit.
5. **Concurrent task safety** — SQLite is shared across tasks. Mitigation: read-only mode, single connection initialized at startup with `check_same_thread=False`.
6. **DB build race condition** — Multiple processes could try to build simultaneously. Mitigation: file lock during build, build before server starts accepting requests.
