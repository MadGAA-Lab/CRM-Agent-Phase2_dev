"""Hybrid ReAct CRM Agent — SQLite DB + schema drift/rot defenses.

Flow:
1. Parse incoming A2A message → extract task JSON
2. Privacy check (rule-based) → instant rejection
3. Schema introspection → drift warnings for prompt
4. Context filter → strip rot notes from required_context
5. ReAct loop (max 8 turns): LLM thinks → SQL/describe/respond → observe
6. Fallback answer extraction if no <respond> after max turns
7. Return A2A artifact with answer + metrics
"""

import json
import logging
import os
import re

import yaml

from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Part, TaskState, TextPart, DataPart
from a2a.utils import get_message_text, new_agent_text_message

from crm_database import CRMDatabase
from schema_introspector import SchemaIntrospector, load_canonical_schema
from context_filter import ContextFilter
from privacy_guard import PrivacyGuard
from llm_client import LLMClient
from time_budget import TimeBudget
from deterministic_handlers import try_deterministic

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


def _load_prompts() -> dict:
    """Load prompt templates from config/prompts.yaml."""
    prompts_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "prompts.yaml"
    )
    with open(prompts_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


PROMPTS = _load_prompts()


class Agent:
    """Hybrid ReAct CRM Agent with real SQLite + schema drift/rot defenses."""

    def __init__(self, db: CRMDatabase | None = None, time_budget: TimeBudget | None = None):
        self.llm = LLMClient()
        self.db = db
        self.time_budget = time_budget
        self._last_response = ""
        self.privacy_guard = PrivacyGuard()
        self.canonical_schema = load_canonical_schema()
        self.introspector = SchemaIntrospector(self.canonical_schema)
        self.context_filter = ContextFilter()
        self.max_turns = int(os.getenv("MAX_TURNS", "8"))
        self.metrics = {
            "tokens": 0,
            "tool_calls": 0,
            "queries": 0,
            "turns": 0,
            "failed_queries": 0,
        }

    def reset_metrics(self) -> None:
        self.metrics = {
            "tokens": 0,
            "tool_calls": 0,
            "queries": 0,
            "turns": 0,
            "failed_queries": 0,
        }
        self.llm.reset_metrics()

    # ── A2A entry point ────────────────────────────────────────────────

    async def run(self, message: Message, updater: TaskUpdater) -> None:
        self.reset_metrics()
        input_text = get_message_text(message)
        task = self._parse_task(input_text)
        task_id = task.get("task_id", "unknown")
        category = task.get("task_category", "unknown")

        logger.info(f"Processing task {task_id} ({category})")
        await updater.update_status(
            TaskState.working,
            new_agent_text_message(f"Processing: {category}"),
        )

        try:
            answer = await self._process_task(task, updater)
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            answer = "insufficient data"

        self.metrics["tokens"] = self.llm.total_tokens
        self.metrics["tool_calls"] = self.llm.tool_calls
        logger.info(f"Task {task_id} answer: {answer[:100]}")

        await updater.add_artifact(
            parts=[
                Part(root=TextPart(text=answer)),
                Part(root=DataPart(data={
                    "task_id": task_id,
                    "category": category,
                    "answer": answer,
                    "metrics": self.metrics,
                })),
            ],
            name="Answer",
        )

    async def handle_timeout(self, message: Message, updater: TaskUpdater) -> None:
        """Produce a best-effort answer when the task times out."""
        input_text = get_message_text(message)
        task = self._parse_task(input_text)
        task_id = task.get("task_id", "unknown")
        category = task.get("task_category", "unknown")

        answer = self._fallback_answer(self._last_response) if self._last_response else "insufficient data"
        self.metrics["tokens"] = self.llm.total_tokens
        self.metrics["tool_calls"] = self.llm.tool_calls
        logger.warning(f"Task {task_id} timed out — fallback answer: {answer[:100]}")

        await updater.add_artifact(
            parts=[
                Part(root=TextPart(text=answer)),
                Part(root=DataPart(data={
                    "task_id": task_id,
                    "category": category,
                    "answer": answer,
                    "metrics": self.metrics,
                    "timed_out": True,
                })),
            ],
            name="Answer",
        )

    # ── Core logic ─────────────────────────────────────────────────────

    async def _process_task(self, task: dict, updater: TaskUpdater) -> str:
        category = task.get("task_category", "")

        # Fast path: privacy rejection (no LLM, no DB)
        if self.privacy_guard.is_privacy_request(task):
            logger.info("Privacy category — returning rejection")
            return PROMPTS.get("privacy_rejection", self.privacy_guard.get_rejection()).strip()

        # Fast path: deterministic SQL handlers (no LLM needed)
        if self.db and self.db.is_available:
            ref_date = self._get_reference_date()
            det_answer = try_deterministic(
                category, task.get("prompt", ""),
                self.db, ref_date,
                context=task.get("required_context", ""),
            )
            if det_answer:
                logger.info(f"Deterministic answer for {category}: {det_answer}")
                return det_answer

        # Pre-processing: drift detection + context cleaning
        entropy = task.get("entropy", {})
        drift_level = entropy.get("drift_level", "none")

        # Build drift warning for system prompt + reverse map for de-drifting context
        drift_section = ""
        reverse_map: dict[str, str] = {}
        if drift_level != "none" and drift_level in self.introspector.KNOWN_DRIFT_MAPS:
            known = self.introspector.KNOWN_DRIFT_MAPS[drift_level]
            reverse_map = {v: k for k, v in known.items()}
            drift_lines = "\n".join(f"  {k} → {v}" for k, v in known.items())
            drift_section = PROMPTS["drift_warning"].format(
                drift_level=drift_level,
                drift_lines=drift_lines,
            )

        # Strip rot notes from context
        cleaned_context = await self.context_filter.filter(task)

        # Apply reverse drift mapping to context text (de-drift)
        if reverse_map and cleaned_context:
            for drifted, canonical in reverse_map.items():
                cleaned_context = cleaned_context.replace(drifted, canonical)

        # Build ReAct messages with category-specific guide
        tables = self.db.get_tables() if self.db and self.db.is_available else []
        category_guides = PROMPTS.get("category_guides", {})
        category_guide = category_guides.get(category, category_guides.get("_default", ""))

        # Pre-compute reference date from DB so LLM doesn't need to query for it
        ref_date = self._get_reference_date()

        system_msg = PROMPTS["base_prompt"].format(
            tables=", ".join(tables) if tables else "(no database available)",
            drift_section=drift_section,
            category_guide=category_guide,
        )
        if ref_date:
            system_msg += f"\n\n## IMPORTANT: Database reference date\nThe most recent data is from {ref_date}. Use this as 'today' for all date calculations. Example: 'past 4 quarters' = date('{ref_date}', '-12 months')."

        user_content = f"Question: {task.get('prompt', '')}"
        if task.get("persona"):
            user_content += f"\nPersona: {task['persona']}"
        if cleaned_context:
            ctx = cleaned_context[:6000] if len(cleaned_context) > 6000 else cleaned_context
            user_content += f"\n\nContext:\n{ctx}"

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_content},
        ]

        logger.info(f"Context length: {len(cleaned_context) if cleaned_context else 0}, DB tables: {len(tables)}")

        # ── ReAct Loop ─────────────────────────────────────────────────
        final_answer = None
        last_response = ""

        for turn in range(self.max_turns):
            self.metrics["turns"] += 1
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(f"Turn {turn + 1}/{self.max_turns}"),
            )

            response = await self._call_llm(messages)
            last_response = response
            self._last_response = response
            action = self._extract_action(response)

            logger.info(
                f"Turn {turn + 1}: type={action['type']}, "
                f"content={str(action.get('content', ''))[:80]}"
            )

            if action["type"] == "execute" and action["content"]:
                self.metrics["tool_calls"] += 1
                self.metrics["queries"] += 1

                if self.db and self.db.is_available:
                    result = self.db.execute_query(action["content"])
                    if result["success"]:
                        obs = f"Result ({result['count']} rows): {json.dumps(result['data'][:8], default=str)}"
                    else:
                        obs = f"SQL Error: {result['error']}"
                        self.metrics["failed_queries"] += 1
                else:
                    obs = "Error: No database available. Use the Context data to answer."

                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": f"[Observation: {obs}]"})

            elif action["type"] == "describe" and action["content"]:
                self.metrics["tool_calls"] += 1

                if self.db and self.db.is_available:
                    result = self.db.describe_table(action["content"])
                    if result["success"]:
                        cols = [c["name"] for c in result["columns"]]
                        obs = f"{result['table']} ({result['row_count']} rows): {', '.join(cols)}"
                    else:
                        obs = f"Error: {result['error']}"
                else:
                    obs = "Error: No database available."

                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": f"[Schema: {obs}]"})

            elif action["type"] == "respond" and action["content"]:
                final_answer = action["content"]
                break

            else:
                # No valid action — nudge
                if turn >= self.max_turns - 2:
                    final_answer = self._fallback_answer(response)
                    break
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": (
                        "Please use <execute> for SQL, <describe> for schema, "
                        "or <respond> for your final answer."
                    ),
                })

        if not final_answer:
            final_answer = self._fallback_answer(last_response)

        return final_answer or "insufficient data"

    def _get_reference_date(self) -> str:
        """Get the most recent date in the database to use as reference for relative date calculations."""
        if not self.db or not self.db.is_available:
            return ""
        try:
            result = self.db.execute_query(
                'SELECT MAX(CreatedDate) FROM "Case"'
            )
            if result["success"] and result["data"]:
                max_date = list(result["data"][0].values())[0]
                if max_date:
                    return max_date[:10]  # Just the date part: YYYY-MM-DD
        except Exception:
            pass
        return ""

    # ── LLM call ───────────────────────────────────────────────────────

    async def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Call LLM with full message history."""
        try:
            client = self.llm._primary_client or self.llm._cheap_client
            if not client:
                raise RuntimeError("No LLM API key configured")

            model = self.llm.primary_model
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
                max_tokens=2048,
            )
            if response.usage:
                self.llm._total_tokens += response.usage.total_tokens
            self.llm._tool_calls += 1
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return f"Error: {e}"

    # ── Action extraction ──────────────────────────────────────────────

    def _extract_action(self, response: str) -> dict:
        """Parse <thought>, <execute>, <describe>, <respond> from LLM response."""
        action: dict = {"thought": "", "type": None, "content": None}

        thought_match = re.search(
            r"<thought>(.*?)</thought>", response, re.DOTALL | re.IGNORECASE
        )
        if thought_match:
            action["thought"] = thought_match.group(1).strip()

        execute_match = re.search(
            r"<execute>(.*?)</execute>", response, re.DOTALL | re.IGNORECASE
        )
        describe_match = re.search(
            r"<describe>(.*?)</describe>", response, re.DOTALL | re.IGNORECASE
        )
        respond_match = re.search(
            r"<respond>(.*?)</respond>", response, re.DOTALL | re.IGNORECASE
        )

        # Priority: respond > execute > describe (so we capture final answers first)
        if respond_match:
            action["type"] = "respond"
            action["content"] = respond_match.group(1).strip()
        elif execute_match:
            action["type"] = "execute"
            action["content"] = execute_match.group(1).strip()
        elif describe_match:
            action["type"] = "describe"
            action["content"] = describe_match.group(1).strip()
        else:
            # Fallback: look for raw SQL
            if "SELECT" in response.upper():
                sql_match = re.search(
                    r"(SELECT\s+[\s\S]+?(?:;|$))", response, re.IGNORECASE
                )
                if sql_match:
                    action["type"] = "execute"
                    action["content"] = sql_match.group(1).strip()

        return action

    # ── Fallback answer extraction ─────────────────────────────────────

    def _fallback_answer(self, response: str) -> str:
        """Extract best answer from response when no <respond> tag."""
        if not response:
            return "None"

        # Check for respond tag
        respond = re.search(
            r"<respond>(.*?)</respond>", response, re.DOTALL | re.IGNORECASE
        )
        if respond:
            return respond.group(1).strip()

        # Look for Salesforce ID (specific prefixes: 005=User, 00Q=Lead, 01t=Product, etc.)
        # Salesforce IDs: 15 or 18 chars, start with [0-9a-zA-Z]{3} prefix + alphanumeric
        sf_ids = re.findall(r"\b([0-9]{3}[A-Za-z][A-Za-z0-9]{11,14})\b", response)
        if sf_ids:
            return sf_ids[-1]  # last one is usually the final answer

        # Look for month name
        months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        for month in months:
            if month.lower() in response.lower():
                return month

        # Look for BANT factors
        for factor in ["Budget", "Authority", "Need", "Timeline"]:
            if factor.lower() in response.lower():
                return factor

        # Look for quoted strings (but filter out SQL fragments)
        quoted = re.findall(r"['\"]([^'\"]{2,60})['\"]", response)
        sql_keywords = {"select", "from", "where", "join", "case", "order", "group", "null", "not", "and", "true", "false"}
        filtered = [q for q in quoted if q.lower() not in sql_keywords and not q.startswith("0")]
        if filtered:
            return filtered[-1]

        return "None"

    # ── Task parsing ───────────────────────────────────────────────────

    def _parse_task(self, input_text: str) -> dict:
        """Parse the CRM task JSON from the incoming message text."""
        try:
            data = json.loads(input_text)
            if isinstance(data, dict) and ("task_id" in data or "prompt" in data):
                return data
        except json.JSONDecodeError:
            pass

        # Find JSON embedded in text
        brace_depth = 0
        start = -1
        for i, ch in enumerate(input_text):
            if ch == "{":
                if brace_depth == 0:
                    start = i
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0 and start >= 0:
                    try:
                        candidate = json.loads(input_text[start : i + 1])
                        if isinstance(candidate, dict) and (
                            "task_id" in candidate
                            or "prompt" in candidate
                            or "task_category" in candidate
                        ):
                            return candidate
                    except json.JSONDecodeError:
                        start = -1

        logger.warning("Could not parse task JSON, treating as raw prompt")
        return {
            "task_id": "unknown",
            "task_category": "knowledge_qa",
            "prompt": input_text,
            "persona": "",
            "required_context": "",
            "config": {},
            "entropy": {"drift_level": "none", "rot_level": "none"},
        }
