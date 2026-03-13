"""ReAct-style task planner — classifies incoming tasks and creates execution plans.

Three strategy templates:
1. EXACT_QUERY_MATCH: For most categories — extract exact answer from context
2. SEMANTIC_RETRIEVAL: For knowledge_qa — fuzzy/synthesized answer
3. PRIVACY_REJECTION: For privacy categories — immediate rejection, no LLM
"""

import logging

logger = logging.getLogger(__name__)


class TaskPlanner:
    """
    Classifies incoming task and creates an execution plan.

    CRITICAL: Keep plans to MAX 3 STEPS to maintain trajectory efficiency.
    """

    PRIVACY_CATEGORIES = frozenset({
        "private_customer_information",
        "confidential_company_knowledge",
    })

    FUZZY_CATEGORIES = frozenset({
        "knowledge_qa",
    })

    # Categories that require exact match answers
    EXACT_CATEGORIES = frozenset({
        "lead_qualification",
        "lead_routing",
        "case_routing",
        "handle_time",
        "transfer_count",
        "sales_insight_mining",
        "monthly_trend_analysis",
        "best_region_identification",
        "conversion_rate_comprehension",
        "named_entity_disambiguation",
    })

    # Category-specific hints for the reasoning engine
    CATEGORY_HINTS = {
        "lead_qualification": "Determine lead qualification status based on lead data and qualification criteria.",
        "lead_routing": "Identify which agent/owner a lead should be routed to based on routing rules.",
        "case_routing": "Determine case routing/assignment based on case attributes and routing rules.",
        "handle_time": "Calculate handle time, resolution time, or duration for cases.",
        "transfer_count": "Count case transfers, escalations, or owner changes.",
        "sales_insight_mining": "Analyze opportunity/deal data for sales insights, competitor analysis, or pipeline metrics.",
        "monthly_trend_analysis": "Analyze time-series data for monthly/quarterly trends and patterns.",
        "best_region_identification": "Identify the best-performing region/state based on given metrics.",
        "conversion_rate_comprehension": "Calculate or analyze lead-to-opportunity conversion rates.",
        "named_entity_disambiguation": "Disambiguate between entities with similar names or attributes.",
        "knowledge_qa": "Answer a knowledge-based question using available documentation and context.",
    }

    def plan(self, task: dict) -> dict:
        """
        Create an execution plan for the task.

        Returns:
            {
                "strategy": "privacy_rejection" | "exact_query_match" | "semantic_retrieval",
                "steps": list of step descriptions,
                "category_hint": str,
            }
        """
        category = task.get("task_category", "")

        if category in self.PRIVACY_CATEGORIES:
            return {
                "strategy": "privacy_rejection",
                "steps": [],
                "category_hint": "Reject immediately — privacy-sensitive category.",
            }

        if category in self.FUZZY_CATEGORIES:
            return {
                "strategy": "semantic_retrieval",
                "steps": ["parse_context", "synthesize_answer"],
                "category_hint": self.CATEGORY_HINTS.get(category, ""),
            }

        return {
            "strategy": "exact_query_match",
            "steps": ["parse_context", "extract_answer"],
            "category_hint": self.CATEGORY_HINTS.get(category, "Analyze the CRM data to answer the question."),
        }
