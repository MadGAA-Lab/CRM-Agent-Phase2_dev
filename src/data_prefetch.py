"""Pre-fetch and cache commonly needed data for LLM prompts.

Instead of asking the LLM to write SQL to find articles/products,
we pre-fetch the data and inject it into the prompt. The LLM only
needs to reason over the data, not query for it.
"""

import logging

logger = logging.getLogger(__name__)


class DataPrefetcher:
    """Caches and provides pre-fetched data for LLM prompts."""

    def __init__(self, db):
        self.db = db
        self._products = None
        self._knowledge_index = None
        self._issues = None

    # ── Products ──────────────────────────────────────────────────

    def get_products_text(self) -> str:
        """Get all products as a text block for prompt injection."""
        if self._products is None:
            result = self.db.execute_query(
                'SELECT Id, Name, Description FROM Product2 ORDER BY Name'
            )
            if result["success"]:
                self._products = result["data"]
            else:
                self._products = []

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
            if result["success"]:
                self._knowledge_index = result["data"]
            else:
                self._knowledge_index = []

        lines = []
        for a in self._knowledge_index:
            summary = (a.get("Summary") or "")[:120]
            lines.append(f"- [{a['Id']}] {a['Title']}: {summary}")
        return "\n".join(lines)

    def get_article_content(self, article_id: str) -> str | None:
        """Fetch full content of a specific knowledge article."""
        result = self.db.execute_query(
            f"SELECT Title, FAQ_Answer__c, Summary FROM Knowledge__kav WHERE Id = '{article_id}'"
        )
        if result["success"] and result["data"]:
            a = result["data"][0]
            return f"Title: {a['Title']}\n\nContent:\n{a.get('FAQ_Answer__c', '')}\n\nSummary:\n{a.get('Summary', '')}"
        return None

    # ── Issues ────────────────────────────────────────────────────

    def get_issues_text(self) -> str:
        """Get all issues for case routing."""
        if self._issues is None:
            result = self.db.execute_query(
                'SELECT Id, Name, Description__c FROM Issue__c ORDER BY Name'
            )
            if result["success"]:
                self._issues = result["data"]
            else:
                self._issues = []

        lines = []
        for i in self._issues:
            desc = (i.get("Description__c") or "")[:100]
            lines.append(f"- [{i['Id']}] {i['Name']}: {desc}")
        return "\n".join(lines)

    # ── Category-specific prompt augmentation ─────────────────────

    def augment_prompt(self, category: str, task: dict) -> str:
        """Return pre-fetched data to inject into the LLM system prompt."""
        ctx = task.get("required_context", "")
        prompt = task.get("prompt", "")

        if category == "named_entity_disambiguation":
            return f"## Available Products\n{self.get_products_text()}\n\nMatch the description in the question to the correct product. Return the Product2 Id."

        if category == "knowledge_qa":
            return f"## Knowledge Article Index\n{self.get_knowledge_index_text()}\n\nFind the article most relevant to the question. If you need the full article content, use: SELECT FAQ_Answer__c FROM Knowledge__kav WHERE Id = '<article_id>'"

        if category in ("policy_violation_identification",):
            return f"## Knowledge Article Index\n{self.get_knowledge_index_text()}\n\nCheck if any policy was violated. Most cases have NO violation — only return an article Id if there is a CLEAR, SPECIFIC breach. Otherwise return None."

        if category in ("invalid_config", "quote_approval"):
            return f"## Knowledge Article Index\n{self.get_knowledge_index_text()}\n\nCheck quote items against these policies. Return the Knowledge__kav Id of the violated article, or None if compliant."

        if category == "lead_qualification":
            return "## BANT Framework\nBudget: Does the lead have budget allocated?\nAuthority: Is the lead a decision-maker?\nNeed: Does the lead have an identified business need?\nTimeline: Does the lead have a defined timeline for purchase?\n\nRead the voice call transcript and determine which BANT factor is MISSING or weakest."

        if category == "wrong_stage_rectification":
            return "## Stage Matching\nRead the opportunity's tasks carefully and match them against the stage criteria in the context. The CURRENT stage is often WRONG. Check each stage's criteria and return the one that actually matches the tasks."

        return ""
