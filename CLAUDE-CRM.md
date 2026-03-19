# CLAUDE.md — CRM Purple Agent for AgentX–AgentBeats Phase 2

## Project Overview

Build a **purple (competing) agent** for the **Business Process Agent** track in the Berkeley RDI AgentX–AgentBeats Phase 2 competition. The agent will be evaluated by the **Entropic CRMArena** green agent on 2,140 CRM tasks across 22 categories, with adversarial robustness challenges (Schema Drift + Context Rot).

**Goal**: Beat the current leaderboard leader (20.7% pass rate, 60.2 7D score at medium/medium drift/rot) by building a schema-adaptive, efficient CRM agent.

---

## Competition Context

- **Platform**: AgentBeats (https://agentbeats.dev)
- **Green Agent**: Entropic CRMArena — AgentBeats ID `019ba211-13b7-7e83-9086-c8015a5e4957`
- **Green Agent Repo**: https://github.com/rkstu/entropic-crmarenapro
- **Agent Template**: https://github.com/RDI-Foundation/agent-template
- **Sprint 1 Deadline**: March 22, 2026
- **Protocol**: A2A (Agent-to-Agent) — JSON-RPC over HTTP

---

## Step 0: Project Setup

1. Clone the official agent template:
   ```bash
   git clone https://github.com/RDI-Foundation/agent-template.git crm-purple-agent
   cd crm-purple-agent
   ```

2. The template provides this structure:
   ```
   src/
   ├── server.py      # A2A server setup + agent card
   ├── executor.py    # A2A request handling
   ├── agent.py       # Agent logic (THIS IS WHERE WE BUILD)
   └── messenger.py   # A2A messaging utilities
   Dockerfile
   pyproject.toml
   uv.lock
   ```

3. Install dependencies:
   ```bash
   uv sync
   ```

4. Add required dependencies to `pyproject.toml`:
   ```toml
   [project]
   dependencies = [
       "a2a-sdk",
       "uvicorn",
       "httpx",
       "anthropic",    # For Claude API
       "openai",       # For Nebius/OpenAI-compatible APIs
       "pyyaml",
   ]
   ```

---

## Step 1: Understand the A2A Protocol

The agent communicates via JSON-RPC. The green agent sends a POST request to `http://your-agent:port/` with a task, and expects a response.

### Agent Card

The agent must expose `GET /.well-known/agent-card.json`:

```json
{
  "name": "MadGAA CRM Agent",
  "description": "CRM agent with runtime schema drift adaptation and context rot filtering for Entropic CRMArena",
  "url": "http://0.0.0.0:9009/",
  "version": "1.0.0",
  "capabilities": {
    "streaming": false,
    "pushNotifications": false
  },
  "skills": [
    {
      "id": "crm_task_solver",
      "name": "CRM Task Solver",
      "description": "Handles CRM tasks including lead qualification, case routing, sales analytics, and knowledge QA with schema drift adaptation"
    }
  ]
}
```

Configure this in `src/server.py`.

### Incoming Task Format (from green agent)

The green agent sends tasks via A2A message. The task payload is JSON embedded in the `parts[0].text` field:

```json
{
  "type": "crm_task",
  "task_id": "456",
  "task_category": "sales_insight_mining",
  "prompt": "Which competitors are we at a disadvantage against?",
  "persona": "You are detail-oriented and methodical.",
  "required_context": "Domain information, transcripts, and CRM records...",
  "config": {
    "org_type": "b2b",
    "max_steps": 15
  },
  "entropy": {
    "drift_level": "low",
    "rot_level": "low"
  }
}
```

### Expected Response Format

Return the answer as an A2A artifact:

```json
{
  "task_id": "456",
  "answer": "Quantum Circuits Inc.",
  "category": "sales_insight_mining",
  "metrics": {
    "tokens": 5000,
    "tool_calls": 3,
    "queries": 2
  }
}
```

The response must be wrapped in the A2A response format by the executor/messenger. The `answer` field is what gets evaluated by the green agent's scoring logic.

---

## Step 2: Understand the Scoring System

The green agent evaluates on 7 weighted dimensions:

| Dimension           | Weight | What It Measures                                     |
|---------------------|--------|------------------------------------------------------|
| FUNCTIONAL          | 30%    | Task completion accuracy (exact_match or fuzzy_match) |
| DRIFT_ADAPTATION    | 20%    | Success rate under schema column renaming            |
| TOKEN_EFFICIENCY    | 12%    | Fewer tokens used = higher score                     |
| QUERY_EFFICIENCY    | 12%    | Fewer DB queries = higher score                      |
| TRAJECTORY_EFFICIENCY | 10%  | Shortest path to correct answer                      |
| ERROR_RECOVERY      | 8%     | Graceful handling of failures                        |
| HALLUCINATION_RATE  | 8%     | % of tool calls that are valid (not hallucinated)    |

**Total Score = Σ (Dimension × Weight)**

### Current leader's scores (our target to beat):

| Dimension           | Leader  | Our Target |
|---------------------|---------|------------|
| FUNCTIONAL          | 41.8    | 65+        |
| DRIFT_ADAPTATION    | 17.3    | 60+        |
| TOKEN_EFFICIENCY    | 99.6    | 95+        |
| QUERY_EFFICIENCY    | 100.0   | 95+        |
| ERROR_RECOVERY      | 44.5    | 70+        |
| HALLUCINATION_RATE  | 83.8    | 95+        |
| TRAJECTORY_EFFICIENCY | 100.0 | 95+        |

**Key insight**: Drift Adaptation (17.3) is the competitor's weakest spot with 20% weight. This is our primary attack vector.

---

## Step 3: Understand the CRM Database Schema

The green agent uses the Salesforce CRMArenaPro dataset. The agent must understand these tables and relationships to generate correct queries.

### Core Tables

| Table                    | Key Columns                              |
|--------------------------|------------------------------------------|
| Account                  | Id, Name, BillingState                   |
| Contact                  | Id, Name, AccountId                      |
| Lead                     | Id, Name, Status, OwnerId               |
| Case                     | Id, Subject, AccountId, OrderItemId__c   |
| Opportunity              | Id, Name, StageName, Amount              |
| OrderItem                | Id, OrderId, Product2Id                  |
| Product2                 | Id, Name, ProductCode                    |
| VoiceCallTranscript__c   | Id, Body__c, LeadId__c                   |

### Key Relationships (critical for JOINs)

```
Case.OrderItemId__c → OrderItem.Id → Product2.Id    (case → product)
Case.AccountId → Account.Id                          (case → company)
Lead.Id → VoiceCallTranscript__c.LeadId__c           (lead → transcripts)
Contact.AccountId → Account.Id                       (contact → company)
Opportunity.AccountId → Account.Id                   (deal → company)
```

### 22 Task Categories

Each category has a specific evaluation metric:

**Exact Match categories** (most common):
- lead_qualification, lead_routing, case_routing, handle_time, transfer_count
- sales_insight_mining, monthly_trend_analysis, best_region_identification
- conversion_rate_comprehension, named_entity_disambiguation

**Fuzzy Match categories**:
- knowledge_qa

**Privacy Rejection categories** (agent must REFUSE to answer):
- private_customer_information, confidential_company_knowledge

The agent should detect privacy categories and immediately return a rejection message WITHOUT querying any data.

### Schema Drift

At different drift levels, column names are programmatically renamed:

| Level  | % Columns Renamed | Example                              |
|--------|-------------------|--------------------------------------|
| none   | 0%                | owner_id stays owner_id              |
| low    | ~10%              | owner_id → assigned_agent            |
| medium | ~30%              | BillingState → invoice_region        |
| high   | ~50%              | StageName → pipeline_phase           |

**The agent MUST NOT hardcode column names.** Instead, implement runtime schema introspection.

### Context Rot

At different rot levels, semantically plausible but irrelevant distractor records are injected into the `required_context`:

| Level  | Distractors Added |
|--------|-------------------|
| none   | 0                 |
| low    | 1-2               |
| medium | 3-4               |
| high   | 5+                |

The agent must filter these out to avoid using wrong data.

---

## Step 4: Implement the 5-Layer Architecture

### Architecture Overview

```
Green Agent (CRMArena)
         │  A2A JSON-RPC
         ▼
┌─────────────────────────────────────────┐
│         Purple Agent (our agent)         │
│                                          │
│  L1: Schema Introspector + Context Filter│
│         │                                │
│  L2: Task Planner (ReAct)               │
│         │                                │
│  L3: SQL Generator | Rot Filter | Privacy│
│         │                                │
│  L4: Answer Synthesizer + Halluc. Guard  │
│         │                                │
│  L5: Error Recovery (max 2 retries)      │
│                                          │
└─────────────────────────────────────────┘
         │  A2A Response
         ▼
Green Agent (scores the answer)
```

### File Structure to Create

```
crm-purple-agent/
├── src/
│   ├── server.py               # (from template) Configure agent card here
│   ├── executor.py             # (from template) Modify to route to our agent
│   ├── agent.py                # Main orchestrator — implement the 5-layer pipeline
│   ├── messenger.py            # (from template) A2A messaging utilities
│   ├── schema_introspector.py  # L1: Schema drift defense
│   ├── context_filter.py       # L1 + L3b: Context rot filtering
│   ├── task_planner.py         # L2: ReAct-style task classification + planning
│   ├── sql_generator.py        # L3a: Schema-aware SQL generation
│   ├── privacy_guard.py        # L3c: PII/confidential query detection
│   ├── answer_synthesizer.py   # L4: Answer formatting + hallucination guard
│   ├── error_recovery.py       # L5: Retry logic
│   └── llm_client.py           # Unified LLM client (Anthropic + OpenAI-compatible)
├── config/
│   ├── schema.json             # Canonical DB schema definition
│   └── prompts.yaml            # All LLM prompt templates
├── tests/
│   ├── test_introspector.py
│   ├── test_sql_gen.py
│   ├── test_privacy.py
│   └── test_context_filter.py
├── Dockerfile
├── pyproject.toml
├── README.md
└── .github/workflows/
    └── test-and-publish.yml
```

---

### L1: Schema Introspector (`src/schema_introspector.py`)

This is the **most important differentiating component**. It handles schema drift.

```python
class SchemaIntrospector:
    """
    Detects and maps drifted column names at runtime.
    
    Strategy:
    1. Read entropy.drift_level from incoming task
    2. If drift_level != "none":
       a. Parse the required_context to discover actual column names used
       b. Build a canonical_name → drifted_name mapping
       c. Cache the mapping for the session
    3. All downstream SQL generation uses drifted names
    
    Implementation approach:
    - Extract column names from the required_context data records
    - Use fuzzy matching (e.g., Levenshtein distance, semantic similarity)
      to map discovered names back to canonical schema
    - Maintain a mapping dict: {"owner_id": "assigned_agent", ...}
    """
    
    def __init__(self, canonical_schema: dict):
        self.canonical_schema = canonical_schema
        self._mapping_cache = {}
    
    def introspect(self, task: dict) -> dict:
        """
        Returns a column mapping dict.
        If drift_level is "none", returns identity mapping.
        """
        drift_level = task.get("entropy", {}).get("drift_level", "none")
        if drift_level == "none":
            return self._identity_mapping()
        
        # Parse required_context to find actual column names
        context = task.get("required_context", "")
        discovered_columns = self._extract_columns_from_context(context)
        
        # Map discovered columns to canonical names
        mapping = self._build_mapping(discovered_columns)
        self._mapping_cache.update(mapping)
        return mapping
    
    def get_drifted_name(self, canonical_name: str) -> str:
        """Look up the drifted name for a canonical column."""
        return self._mapping_cache.get(canonical_name, canonical_name)
```

**Key implementation details:**
- Parse the `required_context` string to extract JSON records, CSV data, or structured text
- Look for column-like tokens (snake_case, CamelCase patterns)
- Use fuzzy string matching to map unknown column names to the canonical schema
- The mapping should be built once per task (or cached per session if drift is consistent)

---

### L1 + L3b: Context Filter (`src/context_filter.py`)

Handles Context Rot by filtering distractor records.

```python
class ContextFilter:
    """
    Filters irrelevant distractor records from required_context.
    
    Strategy:
    1. Read entropy.rot_level from incoming task
    2. If rot_level != "none":
       a. Parse required_context into individual records
       b. Score each record for relevance to the task prompt
       c. Discard low-relevance records
    3. Pass only high-relevance records to downstream components
    
    Relevance scoring approaches (pick one or combine):
    - Entity-ID matching: check if record IDs are referenced in the prompt
    - Keyword overlap: measure word overlap between record and prompt
    - LLM-based: ask the LLM to score relevance (more expensive but accurate)
    """
    
    def filter(self, task: dict) -> str:
        """Returns cleaned required_context with distractors removed."""
        rot_level = task.get("entropy", {}).get("rot_level", "none")
        if rot_level == "none":
            return task.get("required_context", "")
        
        context = task.get("required_context", "")
        prompt = task.get("prompt", "")
        records = self._parse_records(context)
        
        relevant_records = []
        for record in records:
            if self._is_relevant(record, prompt):
                relevant_records.append(record)
        
        return self._reconstruct_context(relevant_records)
```

---

### L2: Task Planner (`src/task_planner.py`)

Classifies the task and selects a strategy.

```python
class TaskPlanner:
    """
    ReAct-style task planner. Classifies incoming task and creates an execution plan.
    
    Three strategy templates:
    
    1. EXACT_QUERY_MATCH: For most categories
       - Generate 1-3 SQL queries against the CRM data
       - Extract exact answer from results
       - Categories: lead_qualification, lead_routing, case_routing, 
         handle_time, transfer_count, sales_insight_mining,
         monthly_trend_analysis, best_region_identification,
         conversion_rate_comprehension, named_entity_disambiguation
    
    2. SEMANTIC_RETRIEVAL: For knowledge-based categories
       - Generate query to retrieve relevant records
       - Use LLM to synthesize fuzzy answer from results
       - Categories: knowledge_qa
    
    3. PRIVACY_REJECTION: For privacy categories
       - Immediately return rejection message
       - NO database queries, NO LLM calls
       - Categories: private_customer_information, 
         confidential_company_knowledge
    
    CRITICAL: Keep plans to MAX 3 STEPS to maintain trajectory efficiency.
    """
    
    PRIVACY_CATEGORIES = {
        "private_customer_information",
        "confidential_company_knowledge"
    }
    
    FUZZY_CATEGORIES = {
        "knowledge_qa"
    }
    
    def plan(self, task: dict) -> dict:
        category = task.get("task_category", "")
        
        if category in self.PRIVACY_CATEGORIES:
            return {"strategy": "privacy_rejection", "steps": []}
        
        if category in self.FUZZY_CATEGORIES:
            return {"strategy": "semantic_retrieval", "steps": ["query", "synthesize"]}
        
        return {"strategy": "exact_query_match", "steps": ["query", "extract"]}
```

---

### L3a: SQL Generator (`src/sql_generator.py`)

Generates schema-aware SQL using drifted column names.

```python
class SQLGenerator:
    """
    Generates SQL queries using the drifted column mapping from L1.
    
    CRITICAL RULES:
    1. ALWAYS use drifted column names from the schema mapping
    2. SELECT only the columns needed for the answer
    3. Understand the key relationships:
       - Case.OrderItemId__c → OrderItem.Id → Product2.Id
       - Case.AccountId → Account.Id
       - Lead.Id → VoiceCallTranscript__c.LeadId__c
    4. Keep queries simple — avoid unnecessary JOINs
    5. Validate SQL syntax before returning
    
    This component uses an LLM to generate SQL from the task prompt + schema mapping.
    
    Prompt template should include:
    - The task prompt
    - The task category
    - The available tables and their (drifted) column names
    - The key relationships
    - The persona context
    - Instructions to generate minimal SQL
    """
    
    def generate(self, task: dict, schema_mapping: dict, filtered_context: str) -> str:
        """
        Returns a SQL query string (or multiple queries as a list).
        Uses the LLM with the schema mapping to generate correct SQL.
        """
        pass
```

**LLM Prompt for SQL Generation** (store in `config/prompts.yaml`):

```yaml
sql_generation: |
  You are a CRM database expert. Generate a SQL query to answer the user's question.
  
  ## Database Schema (use EXACTLY these column names):
  {schema_with_drifted_names}
  
  ## Key Relationships:
  - Case.{case_order_item_col} → OrderItem.{order_item_id_col} → Product2.{product_id_col}
  - Case.{case_account_col} → Account.{account_id_col}
  - Lead.{lead_id_col} → VoiceCallTranscript__c.{transcript_lead_col}
  
  ## Context Data:
  {filtered_context}
  
  ## Task:
  Category: {task_category}
  Question: {prompt}
  Persona: {persona}
  
  ## Rules:
  - Use ONLY the column names listed above
  - SELECT only needed columns
  - Keep the query as simple as possible
  - Return ONLY the SQL query, no explanation
```

---

### L3c: Privacy Guard (`src/privacy_guard.py`)

```python
class PrivacyGuard:
    """
    Detects and rejects PII/confidential information requests.
    
    This is a RULE-BASED classifier — no LLM needed.
    Uses the task_category field as the primary signal.
    
    Returns a standardized rejection message.
    Zero token consumption = pure efficiency gain.
    """
    
    PRIVACY_CATEGORIES = {
        "private_customer_information",
        "confidential_company_knowledge"
    }
    
    REJECTION_MESSAGE = (
        "I'm sorry, but I cannot provide that information. "
        "This request involves private customer data or confidential company knowledge "
        "that I am not authorized to disclose."
    )
    
    def is_privacy_request(self, task: dict) -> bool:
        return task.get("task_category", "") in self.PRIVACY_CATEGORIES
    
    def get_rejection(self) -> str:
        return self.REJECTION_MESSAGE
```

---

### L4: Answer Synthesizer (`src/answer_synthesizer.py`)

```python
class AnswerSynthesizer:
    """
    Formats the final answer and performs hallucination checks.
    
    For exact_match tasks:
    - Extract the precise value from SQL query results
    - Do NOT add any extra text or explanation
    - Return the exact value as a string
    
    For fuzzy_match tasks:
    - Use LLM to synthesize a natural language answer from query results
    - Ground every claim in the query results
    
    HALLUCINATION GUARD:
    - Every entity/number in the answer MUST trace back to a query result row
    - If any claim is ungrounded, return "insufficient data" instead
    - This protects the HALLUCINATION_RATE score (8% weight)
    """
    
    def synthesize(self, task: dict, query_results: list, strategy: str) -> str:
        if strategy == "privacy_rejection":
            return PrivacyGuard.REJECTION_MESSAGE
        
        if strategy == "exact_query_match":
            return self._extract_exact_answer(query_results, task)
        
        if strategy == "semantic_retrieval":
            return self._synthesize_fuzzy_answer(query_results, task)
        
        return "insufficient data"
```

---

### L5: Error Recovery (`src/error_recovery.py`)

```python
class ErrorRecovery:
    """
    Handles failures with structured retry logic.
    
    RULES:
    - Max 2 retries per task (preserves TRAJECTORY_EFFICIENCY)
    - On SQL error: re-introspect schema → regenerate SQL → retry
    - On LLM error: retry with same prompt (transient failures)
    - On persistent failure: return graceful error response
    - Log error patterns for session-level learning
    """
    MAX_RETRIES = 2
    
    async def execute_with_retry(self, task_fn, task: dict, introspector, **kwargs):
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                return await task_fn(task, **kwargs)
            except SQLError as e:
                if attempt < self.MAX_RETRIES:
                    # Re-introspect schema on SQL errors
                    kwargs["schema_mapping"] = introspector.introspect(task)
                    continue
                return self._graceful_error(task, str(e))
            except Exception as e:
                if attempt < self.MAX_RETRIES:
                    continue
                return self._graceful_error(task, str(e))
```

---

### Main Orchestrator (`src/agent.py`)

This ties everything together:

```python
class CRMAgent:
    """
    Main agent orchestrator implementing the 5-layer pipeline.
    
    Flow:
    1. Parse incoming A2A message to extract CRM task
    2. L1: Introspect schema + filter context
    3. L2: Plan execution strategy
    4. L3: Execute (SQL gen / privacy rejection)
    5. L4: Synthesize answer with hallucination guard
    6. L5: Wrap in error recovery
    7. Return A2A-formatted response
    """
    
    def __init__(self):
        self.introspector = SchemaIntrospector(CANONICAL_SCHEMA)
        self.context_filter = ContextFilter()
        self.planner = TaskPlanner()
        self.sql_generator = SQLGenerator()
        self.privacy_guard = PrivacyGuard()
        self.synthesizer = AnswerSynthesizer()
        self.error_recovery = ErrorRecovery()
        self.llm = LLMClient()
    
    async def run(self, message: str) -> str:
        """
        Entry point called by executor.py.
        `message` is the raw text from the A2A message parts.
        Returns the answer string to be wrapped in A2A response.
        """
        # Parse the task JSON from the message
        task = json.loads(message)
        
        # L3c: Privacy check (fastest path — no LLM, no DB)
        if self.privacy_guard.is_privacy_request(task):
            return json.dumps({
                "task_id": task["task_id"],
                "answer": self.privacy_guard.get_rejection(),
                "category": task["task_category"],
                "metrics": {"tokens": 0, "tool_calls": 0, "queries": 0}
            })
        
        # L1: Schema introspection + context filtering
        schema_mapping = self.introspector.introspect(task)
        filtered_context = self.context_filter.filter(task)
        
        # L2: Plan
        plan = self.planner.plan(task)
        
        # L3a + L5: Execute with retry
        async def execute_task(task, schema_mapping=None, filtered_context=None):
            sql = self.sql_generator.generate(task, schema_mapping, filtered_context)
            # Note: The green agent provides data in required_context.
            # We don't have direct DB access — we extract answers from the context.
            results = self._execute_against_context(sql, filtered_context)
            return self.synthesizer.synthesize(task, results, plan["strategy"])
        
        answer = await self.error_recovery.execute_with_retry(
            execute_task, task, self.introspector,
            schema_mapping=schema_mapping,
            filtered_context=filtered_context
        )
        
        return json.dumps({
            "task_id": task["task_id"],
            "answer": answer,
            "category": task["task_category"],
            "metrics": self._collect_metrics()
        })
```

---

### LLM Client (`src/llm_client.py`)

```python
class LLMClient:
    """
    Unified LLM client supporting multiple backends.
    
    Environment variables:
    - ANTHROPIC_API_KEY: For Claude models (primary)
    - NEBIUS_API_KEY: For Llama models via Nebius (cost-optimized)
    - OPENAI_API_KEY: For OpenAI models (fallback)
    
    Model routing:
    - Planning + SQL generation + Answer synthesis → Claude Sonnet 4
    - Context filtering → Llama 3.3 70B via Nebius (cheaper)
    - Privacy guard → Rule-based (no LLM)
    
    IMPORTANT: Track token usage for the metrics response.
    """
    
    def __init__(self):
        self.anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        self.nebius_key = os.environ.get("NEBIUS_API_KEY")
        self.openai_key = os.environ.get("OPENAI_API_KEY")
        self._total_tokens = 0
    
    async def call(self, prompt: str, model: str = "claude-sonnet-4-20250514") -> str:
        """Call the specified model and return the response text."""
        # Implementation depends on which API key is available
        pass
    
    @property
    def total_tokens(self) -> int:
        return self._total_tokens
```

---

## Step 5: Critical Implementation Notes

### How the Green Agent Works

The green agent does NOT give you direct database access. Instead:
1. It sends `required_context` containing the relevant CRM data as text
2. Your agent must **parse and reason over this text context** to find the answer
3. The "SQL generation" is really about **structured reasoning over the context data**, not executing actual SQL against a live database
4. Think of it as: the green agent provides a "data dump" in text, and you need to extract the right answer from it

### What Actually Happens

```
Green Agent sends: {
  "prompt": "Which agent handles the most leads?",
  "required_context": "Lead records:\n- Lead 1: owner=Agent_A, status=Qualified\n- Lead 2: owner=Agent_B, status=New\n- Lead 3: owner=Agent_A, status=Converted\n...",
  "entropy": {"drift_level": "low", "rot_level": "low"}
}

Your agent must:
1. Parse the context data
2. Under drift: column "owner" might be renamed to "assigned_rep"
3. Under rot: some records are irrelevant distractors
4. Count leads per agent → Agent_A has 2 → return "Agent_A"
```

### So the Real Implementation Is

Instead of literal SQL execution, use the LLM to:
1. **Parse the required_context** (which contains structured CRM data)
2. **Reason over the data** using the prompt as the query
3. **Extract the precise answer** that the green agent expects

The "SQL generator" is better thought of as a **structured reasoning engine** that:
- Understands which data fields are relevant (handling drift)
- Filters out noise records (handling rot)
- Performs the right aggregation/lookup/comparison
- Returns a precise answer

### Prompt Engineering is Key

The core of this agent is really a well-engineered prompt that:
1. Presents the cleaned context data with correct column interpretations
2. Asks the LLM to perform the specific task category operation
3. Instructs the LLM to return ONLY the exact answer value
4. Includes grounding instructions to prevent hallucination

---

## Step 6: Docker & Deployment

### Dockerfile

```dockerfile
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install uv

# Copy dependency files first (cache layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source code
COPY src/ src/
COPY config/ config/

# Expose port
EXPOSE 9009

# Start the A2A server
CMD ["uv", "run", "src/server.py", "--host", "0.0.0.0", "--port", "9009"]
```

### Build & Push

```bash
# Build for linux/amd64 (required by AgentBeats)
docker build --platform linux/amd64 -t ghcr.io/<your-username>/crm-purple-agent:latest .

# Login to GHCR
echo $GITHUB_PAT | docker login ghcr.io -u <your-username> --password-stdin

# Push
docker push ghcr.io/<your-username>/crm-purple-agent:latest

# IMPORTANT: Go to GitHub → Packages → crm-purple-agent → Settings → Make Public
```

---

## Step 7: Local Testing

### Start the green agent

```bash
# Terminal 1
git clone https://github.com/rkstu/entropic-crmarenapro.git
cd entropic-crmarenapro
uv sync
export NEBIUS_API_KEY=your_key  # or OPENAI_API_KEY
uv run src/server.py --host 127.0.0.1 --port 9009
```

### Start your purple agent

```bash
# Terminal 2
cd crm-purple-agent
uv sync
export ANTHROPIC_API_KEY=your_key  # or NEBIUS_API_KEY or OPENAI_API_KEY
uv run src/server.py --host 127.0.0.1 --port 9009
```

### Run a test (1 task, no drift/rot)

```bash
# Terminal 3
curl -X POST http://127.0.0.1:9009/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "id": "1",
    "params": {
      "message": {
        "messageId": "test-001",
        "role": "user",
        "parts": [{
          "kind": "text",
          "text": "{\"participants\": {\"agent\": \"http://127.0.0.1:9009/\"}, \"config\": {\"task_limit\": 1, \"drift_level\": \"none\", \"rot_level\": \"none\", \"org_type\": \"b2b\"}}"
        }]
      }
    }
  }'
```

### Progressive testing

```bash
# Test with drift
curl -X POST http://127.0.0.1:9009/ ... "drift_level": "low", "rot_level": "none" ...

# Test with rot
curl -X POST http://127.0.0.1:9009/ ... "drift_level": "none", "rot_level": "low" ...

# Test with both (competition setting)
curl -X POST http://127.0.0.1:9009/ ... "drift_level": "medium", "rot_level": "medium" ...
```

---

## Step 8: Leaderboard Submission

### Quick Submit (Recommended)

1. Go to https://agentbeats.dev/agentbeater/entropic-crmarenapro
2. Click "Quick Submit"
3. Select your purple agent
4. Add secrets: `ANTHROPIC_API_KEY` (or `NEBIUS_API_KEY`)
5. Set config: `{"task_limit": 20, "drift_level": "medium", "rot_level": "medium", "org_type": "b2b"}`
6. Submit → wait for GitHub Actions → merge PR

### Manual Submit

1. Fork the leaderboard repo: https://github.com/RDI-Foundation/DeoGaze-agentbeats-leaderboard
2. Edit `scenario.toml`:
   ```toml
   [green_agent]
   agentbeats_id = "019ba211-13b7-7e83-9086-c8015a5e4957"
   env = { NEBIUS_API_KEY = "${NEBIUS_API_KEY}" }

   [[participants]]
   agentbeats_id = "YOUR_PURPLE_AGENT_ID"
   name = "agent"
   env = { ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}" }

   [config]
   task_limit = 2140
   drift_level = "medium"
   rot_level = "medium"
   org_type = "b2b"
   max_steps = 15
   timeout = 300
   ```
3. Add API keys as GitHub repo secrets
4. Push → GitHub Actions runs → submit PR

---

## Development Priorities (in order)

1. **Get A2A handshake working** — green sends task, purple returns valid response (even hardcoded)
2. **Parse task format correctly** — extract task_id, prompt, category, context, entropy
3. **Implement privacy guard** — easiest category to get 100% on
4. **Implement basic LLM reasoning** — send prompt + context to LLM, extract answer
5. **Add schema drift handling** — parse context to detect renamed columns
6. **Add context rot filtering** — filter distractor records
7. **Add error recovery** — graceful failure handling
8. **Optimize prompts** — iterate on prompt templates for accuracy
9. **Docker + submission** — containerize and submit to leaderboard

---

## Environment Variables

```bash
# Primary LLM (pick one)
ANTHROPIC_API_KEY=sk-ant-...      # For Claude Sonnet 4
NEBIUS_API_KEY=...                 # For Llama 3.3 70B (cheaper, free credits)
OPENAI_API_KEY=sk-...             # For GPT-4o (alternative)

# Server config
HOST=0.0.0.0
PORT=9009
```

---

## Success Criteria

- [ ] Agent responds to A2A requests without errors
- [ ] Privacy categories return rejection (100% accuracy)
- [ ] Pass rate > 40% at medium/medium drift/rot
- [ ] 7D Score > 70 (beating the leader's 60.2)
- [ ] Drift Adaptation > 50 (beating the leader's 17.3)
- [ ] Docker image builds and runs on linux/amd64
- [ ] Leaderboard submission accepted
