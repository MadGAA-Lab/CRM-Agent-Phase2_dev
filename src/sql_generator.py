"""LLM-based structured reasoning engine over CRM context data.

Despite the name "sql_generator", this module does NOT execute SQL against
a live database. The green agent provides CRM data as text in required_context,
and this module uses the LLM to reason over that data to extract answers.

The "SQL generation" metaphor is maintained from the spec, but the actual
implementation is prompt-based reasoning with schema awareness.
"""

import logging
import os

import yaml

logger = logging.getLogger(__name__)


class SQLGenerator:
    """
    Schema-aware reasoning engine that generates answers from CRM context data.

    Uses the LLM with schema mapping (including drifted names) to:
    1. Understand which data fields are relevant (handling drift)
    2. Perform the right aggregation/lookup/comparison
    3. Return a precise answer

    CRITICAL RULES:
    1. ALWAYS inform the LLM about drifted column names
    2. Instruct the LLM to extract ONLY the needed information
    3. Keep reasoning focused on the task category
    4. Validate that the answer is grounded in the data
    """

    def __init__(self, llm_client):
        self._llm = llm_client
        self._prompts = self._load_prompts()

    def _load_prompts(self) -> dict:
        """Load prompt templates from config/prompts.yaml."""
        prompts_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config", "prompts.yaml"
        )
        try:
            with open(prompts_path) as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning("prompts.yaml not found, using inline prompts")
            return {}

    async def generate(
        self,
        task: dict,
        schema_info: str,
        filtered_context: str,
        category_hint: str = "",
    ) -> str:
        """
        Use LLM to reason over the context data and extract an answer.

        Args:
            task: The full task dict with prompt, category, persona, etc.
            schema_info: Human-readable schema description (with drifted names)
            filtered_context: Cleaned context with distractors removed
            category_hint: Category-specific guidance from task planner

        Returns:
            The raw answer string from the LLM
        """
        category = task.get("task_category", "")

        # Try category-specific prompt first, fall back to general
        prompt_key = f"category_{category}"
        prompt_template = self._prompts.get(
            prompt_key,
            self._prompts.get("reasoning", self._default_prompt()),
        )

        formatted_prompt = prompt_template.format(
            schema_info=schema_info,
            filtered_context=filtered_context,
            task_category=category,
            prompt=task.get("prompt", ""),
            persona=task.get("persona", ""),
            category_hint=category_hint,
        )

        logger.info(
            f"Generating answer for category={category}, "
            f"using_template={prompt_key if prompt_key in self._prompts else 'reasoning'}, "
            f"prompt_length={len(formatted_prompt)}"
        )

        response = await self._llm.call(
            formatted_prompt,
            temperature=0.0,
            max_tokens=1024,
        )

        return response.strip()

    async def generate_fuzzy(
        self,
        task: dict,
        schema_info: str,
        filtered_context: str,
        category_hint: str = "",
    ) -> str:
        """
        Generate a fuzzy/synthesized answer for knowledge_qa or sales_insight_mining tasks.

        Uses a different prompt template optimized for natural language synthesis.
        """
        category = task.get("task_category", "")
        prompt_key = f"category_{category}"
        prompt_template = self._prompts.get(
            prompt_key,
            self._prompts.get("fuzzy_answer", self._default_fuzzy_prompt()),
        )

        formatted_prompt = prompt_template.format(
            schema_info=schema_info,
            filtered_context=filtered_context,
            task_category=category,
            prompt=task.get("prompt", ""),
            persona=task.get("persona", ""),
            category_hint=category_hint,
        )

        response = await self._llm.call(
            formatted_prompt,
            temperature=0.1,  # Slightly more creative for fuzzy answers
            max_tokens=1024,
        )

        return response.strip()

    def _default_prompt(self) -> str:
        return (
            "You are a CRM data analyst. Analyze the provided CRM data context to answer the user's question.\n\n"
            "## Database Schema (canonical column names → actual names in data):\n"
            "{schema_info}\n\n"
            "## Context Data:\n"
            "{filtered_context}\n\n"
            "## Task:\n"
            "Category: {task_category}\n"
            "Question: {prompt}\n"
            "Persona: {persona}\n"
            "Guidance: {category_hint}\n\n"
            "## Answer Format Rules:\n"
            "- Return ONLY the answer value, no explanation or commentary\n"
            "- For a single value: return it as-is (e.g., 'Authority' or '42')\n"
            "- For multiple values: return as bracket-delimited list (e.g., '[ID1, ID2, ID3]')\n"
            "- If the data is insufficient to answer, respond with 'insufficient data'\n\n"
            "## Rules:\n"
            "- Analyze ONLY the data provided in Context Data above\n"
            "- If column names differ from canonical names, use the ACTUAL names present in the data\n"
            "- Count carefully, double-check any aggregations\n"
            "- For entity disambiguation, use IDs and unique attributes to distinguish\n"
        )

    def _default_fuzzy_prompt(self) -> str:
        return (
            "You are a CRM knowledge expert. Synthesize a clear, grounded answer from the provided data.\n\n"
            "## Database Schema:\n"
            "{schema_info}\n\n"
            "## Context Data:\n"
            "{filtered_context}\n\n"
            "## Task:\n"
            "Category: {task_category}\n"
            "Question: {prompt}\n"
            "Persona: {persona}\n"
            "Guidance: {category_hint}\n\n"
            "## Answer Format Rules:\n"
            "- Return a concise, grounded answer\n"
            "- For a single value: return it as-is\n"
            "- For multiple values: return as bracket-delimited list (e.g., '[val1, val2]')\n"
            "- If the data is insufficient to answer, respond with 'insufficient data'\n\n"
            "## Rules:\n"
            "- Answer using ONLY information found in the context data\n"
            "- Every claim MUST be traceable to a specific data point\n"
            "- Be concise and direct\n"
        )
