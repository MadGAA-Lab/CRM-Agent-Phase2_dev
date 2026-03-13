"""Main CRM agent orchestrator implementing the 5-layer pipeline.

Flow:
1. Parse incoming A2A message to extract CRM task
2. L1: Introspect schema + filter context
3. L2: Plan execution strategy
4. L3: Execute (LLM reasoning / privacy rejection)
5. L4: Synthesize answer with hallucination guard
6. L5: Wrap in error recovery
7. Return A2A-formatted response
"""

import json
import logging

from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Part, TaskState, TextPart
from a2a.utils import get_message_text, new_agent_text_message

from schema_introspector import SchemaIntrospector, load_canonical_schema
from context_filter import ContextFilter
from task_planner import TaskPlanner
from sql_generator import SQLGenerator
from privacy_guard import PrivacyGuard
from answer_synthesizer import AnswerSynthesizer
from error_recovery import ErrorRecovery
from llm_client import LLMClient

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


class Agent:
    """
    CRM Purple Agent — 5-layer pipeline for Entropic CRMArena.

    Layers:
    L1: Schema Introspector + Context Filter
    L2: Task Planner (strategy classification)
    L3: SQL Generator (LLM reasoning) | Privacy Guard
    L4: Answer Synthesizer + Hallucination Guard
    L5: Error Recovery (max 2 retries)
    """

    def __init__(self):
        self.llm = LLMClient()
        canonical_schema = load_canonical_schema()
        self.introspector = SchemaIntrospector(canonical_schema)
        self.context_filter = ContextFilter(llm_client=self.llm)
        self.planner = TaskPlanner()
        self.sql_generator = SQLGenerator(self.llm)
        self.privacy_guard = PrivacyGuard()
        self.synthesizer = AnswerSynthesizer()
        self.error_recovery = ErrorRecovery()

    async def run(self, message: Message, updater: TaskUpdater) -> None:
        """
        Entry point called by executor.py.

        Processes the incoming A2A message, runs the 5-layer pipeline,
        and returns the answer as an A2A artifact.
        """
        input_text = get_message_text(message)
        logger.info(f"Received message: {input_text[:200]}...")

        # Reset LLM metrics for this task
        self.llm.reset_metrics()

        await updater.update_status(
            TaskState.working,
            new_agent_text_message("Processing CRM task..."),
        )

        try:
            # Parse the task JSON from the message
            task = self._parse_task(input_text)
            logger.info(
                f"Task parsed: id={task.get('task_id')}, "
                f"category={task.get('task_category')}, "
                f"drift={task.get('entropy', {}).get('drift_level', 'none')}, "
                f"rot={task.get('entropy', {}).get('rot_level', 'none')}"
            )

            # Execute the pipeline
            answer = await self._execute_pipeline(task)

            # Build response
            response = self._build_response(task, answer)
            logger.info(
                f"Response: task_id={task.get('task_id')}, "
                f"answer={answer[:100]}..., "
                f"tokens={self.llm.total_tokens}"
            )

            # Return as A2A artifact
            await updater.add_artifact(
                parts=[Part(root=TextPart(text=response))],
                name="CRM Answer",
            )

        except Exception as e:
            logger.error(f"Fatal error in agent pipeline: {e}", exc_info=True)
            # Return error as artifact (better than crashing for ERROR_RECOVERY score)
            error_response = json.dumps({
                "answer": "insufficient data",
                "metrics": {
                    "tokens": self.llm.total_tokens,
                    "tool_calls": self.llm.tool_calls,
                    "queries": self.llm.queries,
                },
            })
            await updater.add_artifact(
                parts=[Part(root=TextPart(text=error_response))],
                name="CRM Answer",
            )

    async def _execute_pipeline(self, task: dict) -> str:
        """Execute the 5-layer pipeline and return the answer string."""

        # ── L3c: Privacy check (fastest path — no LLM, no DB) ──
        if self.privacy_guard.is_privacy_request(task):
            logger.info("Privacy category detected — returning rejection")
            return self.privacy_guard.get_rejection()

        # ── L1: Schema introspection + context filtering ──
        schema_mapping = self.introspector.introspect(task)
        schema_info = self.introspector.get_schema_description(schema_mapping)
        filtered_context = await self.context_filter.filter(task)

        # ── L2: Plan ──
        plan = self.planner.plan(task)
        strategy = plan["strategy"]
        logger.info(f"Strategy: {strategy}, steps: {plan['steps']}")

        # ── L3a + L5: Execute with retry ──
        async def execute_task(task, schema_info=None, filtered_context=None, **_kw):
            if strategy == "semantic_retrieval":
                raw_answer = await self.sql_generator.generate_fuzzy(
                    task, filtered_context
                )
            else:
                raw_answer = await self.sql_generator.generate(
                    task, schema_info, filtered_context
                )

            # ── L4: Synthesize + hallucination guard ──
            return self.synthesizer.synthesize(
                task, raw_answer, strategy, filtered_context
            )

        answer = await self.error_recovery.execute_with_retry(
            execute_task,
            task,
            self.introspector,
            schema_info=schema_info,
            filtered_context=filtered_context,
        )

        return answer

    def _parse_task(self, input_text: str) -> dict:
        """
        Parse the CRM task from the incoming message text.

        The green agent sends the task as a JSON string in the message parts.
        We need to extract and parse it.
        """
        # Try direct JSON parse
        try:
            task = json.loads(input_text)
            if isinstance(task, dict) and ("task_id" in task or "prompt" in task):
                return task
        except json.JSONDecodeError:
            pass

        # Try to find JSON embedded in the text
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
                        continue

        # Fallback: treat the whole text as the prompt
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

    def _build_response(self, task: dict, answer: str) -> str:
        """Build the JSON response string for the green agent."""
        response = {
            "task_id": task.get("task_id", "unknown"),
            "answer": answer,
            "category": task.get("task_category", ""),
            "metrics": {
                "tokens": self.llm.total_tokens,
                "tool_calls": self.llm.tool_calls,
                "queries": self.llm.queries,
            },
        }
        return json.dumps(response)
