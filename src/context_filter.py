"""Context rot filtering — strips injected noise notes from required_context.

Discovery from green agent source analysis: context rot is NOT fake records.
It's just appended text notes like:
  '[Note: Some records may have been updated recently. Verify timestamps.]'
  '[System Notice: Database migration in progress. Some field names may vary.]'

Low rot: 1 note appended
Medium rot: 2 notes appended
High rot: 3 notes appended

Strategy: Strip these known noise patterns, then pass through existing
heuristic filtering for any remaining structure issues.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


class ContextFilter:
    """
    Filters noise from required_context.

    Primary: Strip appended rot notes (bracketed [Note:...] and [System Notice:...] patterns)
    Secondary: Heuristic relevance filtering for multi-section contexts
    Tertiary: LLM-based filtering for high rot levels (if LLM available)
    """

    # Known rot note patterns from green agent source (_apply_context_rot)
    ROT_PATTERNS = [
        r'\[Note:[^\]]*\]',
        r'\[System Notice:[^\]]*\]',
        r'\[Advisory:[^\]]*\]',
        r'\[Warning:[^\]]*\]',
        r'\[Info:[^\]]*\]',
        r'\[Update:[^\]]*\]',
        r'\[Notice:[^\]]*\]',
        r'\[Alert:[^\]]*\]',
        r'\[Caution:[^\]]*\]',
    ]

    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def filter(self, task: dict) -> str:
        """Returns cleaned required_context with rot notes and distractors removed."""
        rot_level = task.get("entropy", {}).get("rot_level", "none")
        context = task.get("required_context", "")

        if rot_level == "none" or not context:
            return context

        # Step 1: Strip known rot note patterns
        cleaned = self._strip_rot_notes(context)

        # Step 2: For multi-section contexts, apply heuristic filtering
        prompt = task.get("prompt", "")
        category = task.get("task_category", "")
        sections = self._parse_sections(cleaned)

        if len(sections) <= 1:
            return cleaned.strip()

        # Heuristic filtering for remaining sections
        relevant = self._heuristic_filter(sections, prompt, category)

        if not relevant:
            logger.warning("Context filter removed all sections — returning cleaned context")
            return cleaned.strip()

        return "\n\n".join(relevant)

    def _strip_rot_notes(self, context: str) -> str:
        """
        Strip known rot note patterns from context.

        The green agent appends notes like:
          [Note: Some records may have been updated recently. Verify timestamps.]
          [System Notice: Database migration in progress. Some field names may vary.]
        """
        cleaned = context
        for pattern in self.ROT_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned)

        # Also strip any trailing whitespace/newlines left by removal
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    def _parse_sections(self, context: str) -> list[str]:
        """
        Parse required_context into distinct sections/records.

        The context can be:
        - Markdown sections separated by ## headers
        - Newline-separated records
        - JSON arrays
        - Bullet-point lists
        """
        # Try to detect JSON array
        stripped = context.strip()
        if stripped.startswith("["):
            try:
                records = json.loads(stripped)
                return [json.dumps(r, indent=2) for r in records]
            except json.JSONDecodeError:
                pass

        # Try markdown section splits
        md_sections = re.split(r"\n(?=##\s)", context)
        if len(md_sections) > 1:
            return [s.strip() for s in md_sections if s.strip()]

        # Try double-newline splits (paragraph-based)
        paragraphs = re.split(r"\n\n+", context)
        if len(paragraphs) > 1:
            return [p.strip() for p in paragraphs if p.strip()]

        # Try record-like splits (lines starting with - or *)
        records = re.split(r"\n(?=[-*•]\s)", context)
        if len(records) > 1:
            return [r.strip() for r in records if r.strip()]

        # No clear structure — return as single section
        return [context]

    def _heuristic_filter(
        self, sections: list[str], prompt: str, category: str
    ) -> list[str]:
        """
        Filter sections using keyword overlap and entity matching.

        Scoring:
        - Extract significant words from prompt
        - Count overlap with each section
        - Keep sections above threshold
        """
        prompt_lower = prompt.lower()
        prompt_words = set(self._extract_significant_words(prompt_lower))

        # Also extract IDs and entity names from prompt
        prompt_ids = set(re.findall(r"[A-Za-z0-9]{15,18}", prompt))  # Salesforce IDs
        prompt_entities = set(re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", prompt))

        scored_sections = []
        for section in sections:
            score = self._score_section(
                section, prompt_words, prompt_ids, prompt_entities, category
            )
            scored_sections.append((score, section))

        if not scored_sections:
            return sections

        # Sort by score descending
        scored_sections.sort(key=lambda x: x[0], reverse=True)

        # Keep sections with score above threshold
        # Use adaptive threshold based on scores
        max_score = scored_sections[0][0]
        if max_score == 0:
            return sections  # No signal — keep everything

        threshold = max_score * 0.2  # Keep sections with at least 20% of max score
        relevant = [s for score, s in scored_sections if score >= threshold]

        # Always keep at least half the sections to avoid over-filtering
        min_keep = max(1, len(sections) // 2)
        if len(relevant) < min_keep:
            relevant = [s for _, s in scored_sections[:min_keep]]

        return relevant

    def _score_section(
        self,
        section: str,
        prompt_words: set[str],
        prompt_ids: set[str],
        prompt_entities: set[str],
        category: str,
    ) -> float:
        """Score a section for relevance to the prompt."""
        score = 0.0
        section_lower = section.lower()
        section_words = set(self._extract_significant_words(section_lower))

        # Word overlap
        overlap = prompt_words & section_words
        if prompt_words:
            score += len(overlap) / len(prompt_words) * 5.0

        # ID matching (strong signal)
        for pid in prompt_ids:
            if pid in section:
                score += 10.0

        # Entity name matching
        for entity in prompt_entities:
            if entity.lower() in section_lower:
                score += 3.0

        # Category-specific keywords
        category_keywords = self._get_category_keywords(category)
        for kw in category_keywords:
            if kw in section_lower:
                score += 1.0

        return score

    def _extract_significant_words(self, text: str) -> list[str]:
        """Extract significant words (skip stop words)."""
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "dare", "ought",
            "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "above",
            "below", "between", "out", "off", "over", "under", "again",
            "further", "then", "once", "here", "there", "when", "where", "why",
            "how", "all", "each", "every", "both", "few", "more", "most",
            "other", "some", "such", "no", "not", "only", "own", "same", "so",
            "than", "too", "very", "just", "because", "but", "and", "or", "if",
            "while", "that", "this", "these", "those", "what", "which", "who",
        }
        words = re.findall(r"\b[a-z]{3,}\b", text)
        return [w for w in words if w not in stop_words]

    def _get_category_keywords(self, category: str) -> list[str]:
        """Return category-specific keywords for relevance scoring."""
        category_kw_map = {
            "lead_qualification": ["lead", "qualified", "qualification", "status", "rating", "converted"],
            "lead_routing": ["lead", "owner", "routing", "assigned", "agent", "rep"],
            "case_routing": ["case", "routing", "assigned", "agent", "owner", "queue"],
            "handle_time": ["case", "handle", "time", "duration", "closed", "created", "resolution"],
            "transfer_count": ["case", "transfer", "escalat", "reassign", "owner"],
            "sales_insight_mining": ["opportunity", "deal", "competitor", "revenue", "pipeline", "stage"],
            "monthly_trend_analysis": ["month", "trend", "growth", "decline", "over time", "quarter"],
            "best_region_identification": ["region", "state", "billing", "territory", "area", "geography"],
            "conversion_rate_comprehension": ["conversion", "rate", "converted", "lead", "opportunity"],
            "named_entity_disambiguation": ["name", "entity", "disambiguat", "identify", "person", "company"],
            "knowledge_qa": ["knowledge", "article", "guide", "faq", "documentation", "help"],
            "activity_priority": ["activity", "task", "priority", "due", "date", "overdue", "urgent"],
            "invalid_config": ["config", "invalid", "validation", "rule", "error", "field", "setting"],
            "policy_violation_identification": ["policy", "violation", "sla", "breach", "compliance", "approval"],
            "quote_approval": ["quote", "approval", "approver", "amount", "discount", "price"],
            "sales_amount_understanding": ["amount", "revenue", "pipeline", "total", "sum", "deal", "value"],
            "sales_cycle_understanding": ["cycle", "days", "duration", "stage", "velocity", "close"],
            "top_issue_identification": ["issue", "top", "common", "frequent", "type", "category", "complaint"],
            "wrong_stage_rectification": ["stage", "wrong", "incorrect", "opportunity", "rectif", "criteria"],
        }
        return category_kw_map.get(category, [])

    async def _llm_filter(
        self, sections: list[str], prompt: str, category: str
    ) -> list[str]:
        """Use LLM to filter sections (for high rot levels)."""
        records_text = "\n\n".join(
            f"[Record {i}]:\n{s}" for i, s in enumerate(sections)
        )

        filter_prompt = (
            f"You are a data relevance filter. Given a CRM task prompt and a set of context records, "
            f"identify which records are relevant to answering the prompt.\n\n"
            f"## Task Prompt:\n{prompt}\n\n"
            f"## Task Category:\n{category}\n\n"
            f"## Records:\n{records_text}\n\n"
            f"## Instructions:\n"
            f"Return a JSON array of indices (0-based) of the RELEVANT records.\n"
            f"A record is relevant if it contains entities, IDs, or data needed by the prompt.\n"
            f"Return ONLY the JSON array, e.g. [0, 2, 5]"
        )

        try:
            response = await self._llm.call_cheap(filter_prompt, max_tokens=256)
            # Parse the JSON array from response
            match = re.search(r"\[[\d,\s]*\]", response)
            if match:
                indices = json.loads(match.group())
                return [sections[i] for i in indices if 0 <= i < len(sections)]
        except Exception as e:
            logger.warning(f"LLM filtering failed, falling back to heuristic: {e}")

        return self._heuristic_filter(sections, prompt, category)
