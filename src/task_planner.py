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
        "internal_operation_data",
    })

    FUZZY_CATEGORIES = frozenset({
        "knowledge_qa",
        "sales_insight_mining",  # Some instances use fuzzy match
    })

    # Categories that require exact match answers
    EXACT_CATEGORIES = frozenset({
        "lead_qualification",
        "lead_routing",
        "case_routing",
        "handle_time",
        "transfer_count",
        "sales_insight_mining",  # Also in fuzzy — dual-mode category
        "monthly_trend_analysis",
        "best_region_identification",
        "conversion_rate_comprehension",
        "named_entity_disambiguation",
        "activity_priority",
        "invalid_config",
        "policy_violation_identification",
        "quote_approval",
        "sales_amount_understanding",
        "sales_cycle_understanding",
        "top_issue_identification",
        "wrong_stage_rectification",
    })

    # Category-specific hints for the reasoning engine
    CATEGORY_HINTS = {
        "lead_qualification": "Determine lead qualification status (e.g., Qualified, Unqualified, Working) based on lead attributes, scores, and qualification criteria.",
        "lead_routing": "Identify which agent/owner a lead should be routed to based on routing rules, territory, or round-robin assignment.",
        "case_routing": "Determine case routing/assignment based on case attributes (priority, type, product) and routing rules.",
        "handle_time": "Calculate handle time, resolution time, or duration for cases. Compare CreatedDate vs ClosedDate.",
        "transfer_count": "Count case transfers, escalations, or owner changes from case history or audit trail.",
        "sales_insight_mining": "Analyze opportunity/deal data for sales insights — competitor mentions, win/loss reasons, pipeline metrics.",
        "monthly_trend_analysis": "Analyze time-series data for monthly/quarterly trends — growth rates, period-over-period comparisons.",
        "best_region_identification": "Identify the best-performing region/state/territory based on revenue, deal count, or other metrics.",
        "conversion_rate_comprehension": "Calculate lead-to-opportunity conversion rates — count converted vs total leads.",
        "named_entity_disambiguation": "Disambiguate between entities with similar names — use IDs, departments, or other unique attributes.",
        "activity_priority": "Determine activity/task priority based on due dates, importance, and associated record status.",
        "invalid_config": "Identify invalid or misconfigured settings — validation rules, workflow errors, field mismatches.",
        "policy_violation_identification": "Detect policy violations — SLA breaches, approval bypasses, unauthorized actions.",
        "quote_approval": "Determine quote approval status, required approvers, or approval chain based on quote amount and rules.",
        "sales_amount_understanding": "Analyze opportunity amounts — total pipeline, weighted amounts, deal sizes, revenue figures.",
        "sales_cycle_understanding": "Analyze sales cycle length — days from creation to close, stage durations, velocity metrics.",
        "top_issue_identification": "Identify the most common/frequent issues, case types, or complaint categories from case data.",
        "wrong_stage_rectification": "Identify opportunities in incorrect stages based on stage criteria, dates, or business rules.",
        "knowledge_qa": "Answer a knowledge-based question using available documentation, articles, and context data.",
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
