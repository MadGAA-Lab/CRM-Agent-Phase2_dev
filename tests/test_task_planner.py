"""Tests for the task planner module."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from task_planner import TaskPlanner


class TestTaskPlanner:
    def setup_method(self):
        self.planner = TaskPlanner()

    def test_privacy_rejection_for_private_customer(self):
        task = {"task_category": "private_customer_information"}
        plan = self.planner.plan(task)
        assert plan["strategy"] == "privacy_rejection"
        assert plan["steps"] == []

    def test_privacy_rejection_for_confidential_company(self):
        task = {"task_category": "confidential_company_knowledge"}
        plan = self.planner.plan(task)
        assert plan["strategy"] == "privacy_rejection"

    def test_semantic_retrieval_for_knowledge_qa(self):
        task = {"task_category": "knowledge_qa"}
        plan = self.planner.plan(task)
        assert plan["strategy"] == "semantic_retrieval"
        assert len(plan["steps"]) <= 3

    def test_exact_match_for_lead_qualification(self):
        task = {"task_category": "lead_qualification"}
        plan = self.planner.plan(task)
        assert plan["strategy"] == "exact_query_match"
        assert len(plan["steps"]) <= 3

    def test_exact_match_for_standard_categories(self):
        standard = [
            "lead_routing",
            "case_routing",
            "handle_time",
            "transfer_count",
            "sales_insight_mining",
            "monthly_trend_analysis",
            "best_region_identification",
            "conversion_rate_comprehension",
            "named_entity_disambiguation",
        ]
        for cat in standard:
            task = {"task_category": cat}
            plan = self.planner.plan(task)
            assert plan["strategy"] == "exact_query_match", f"Failed for {cat}"
            assert len(plan["steps"]) <= 3, f"Too many steps for {cat}"

    def test_unknown_category_defaults_to_exact(self):
        task = {"task_category": "unknown_new_category"}
        plan = self.planner.plan(task)
        assert plan["strategy"] == "exact_query_match"

    def test_empty_category_defaults_to_exact(self):
        task = {"task_category": ""}
        plan = self.planner.plan(task)
        assert plan["strategy"] == "exact_query_match"

    def test_plan_has_category_hint(self):
        task = {"task_category": "lead_qualification"}
        plan = self.planner.plan(task)
        assert "category_hint" in plan
        assert plan["category_hint"]  # Not empty
