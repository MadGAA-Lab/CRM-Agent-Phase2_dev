"""Pre-fetch and cache commonly needed data for LLM prompts.

Instead of asking the LLM to write SQL to find articles/products,
we pre-fetch the data and inject it into the prompt. The LLM only
needs to reason over the data, not query for it.

Prompt templates live in config/prompts.yaml under prefetch_prompts.
"""

import logging
import os
import yaml

logger = logging.getLogger(__name__)

_PROMPTS_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def _load_prefetch_prompts() -> dict:
    with open(_PROMPTS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f).get("prefetch_prompts", {})


class DataPrefetcher:
    """Caches and provides pre-fetched data for LLM prompts."""

    def __init__(self, db):
        self.db = db
        self._products = None
        self._knowledge_index = None
        self._templates = _load_prefetch_prompts()

    # ── Products ──────────────────────────────────────────────────

    def get_products_text(self) -> str:
        """Get all products as a text block for prompt injection."""
        if self._products is None:
            result = self.db.execute_query(
                'SELECT Id, Name, Description FROM Product2 ORDER BY Name'
            )
            self._products = result["data"] if result["success"] else []

        lines = []
        for p in self._products:
            lines.append(f"- [{p['Id']}] {p['Name']}: {p.get('Description', '')}")
        return "\n".join(lines)

    # ── Knowledge Articles (index: title + summary only) ──────────

    def get_knowledge_index_text(self) -> str:
        """Get knowledge article titles + summaries for topic identification."""
        if self._knowledge_index is None:
            result = self.db.execute_query(
                'SELECT Id, Title, Summary FROM Knowledge__kav ORDER BY Title'
            )
            self._knowledge_index = result["data"] if result["success"] else []

        lines = []
        for a in self._knowledge_index:
            summary = (a.get("Summary") or "")[:120]
            lines.append(f"- [{a['Id']}] {a['Title']}: {summary}")
        return "\n".join(lines)

    # ── Category-specific prompt augmentation ─────────────────────

    def augment_prompt(self, category: str, task: dict) -> str:
        """Return pre-fetched data to inject into the LLM system prompt."""
        template = self._templates.get(category, "")
        if not template:
            return ""

        # Substitute data placeholders (only fetch what's needed)
        data = {}
        if "{products}" in template:
            data["products"] = self.get_products_text()
        if "{knowledge_index}" in template:
            data["knowledge_index"] = self.get_knowledge_index_text()
        return template.format(**data) if data else template
