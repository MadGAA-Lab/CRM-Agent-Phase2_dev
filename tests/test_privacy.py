"""Tests for the privacy guard module."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from privacy_guard import PrivacyGuard


class TestPrivacyGuard:
    def setup_method(self):
        self.guard = PrivacyGuard()

    def test_detects_private_customer_info(self):
        task = {"task_category": "private_customer_information"}
        assert self.guard.is_privacy_request(task) is True

    def test_detects_confidential_company_knowledge(self):
        task = {"task_category": "confidential_company_knowledge"}
        assert self.guard.is_privacy_request(task) is True

    def test_allows_internal_operation_data(self):
        """internal_operation_data uses exact_match, not privacy_rejection."""
        task = {"task_category": "internal_operation_data"}
        assert self.guard.is_privacy_request(task) is False

    def test_allows_normal_categories(self):
        normal_categories = [
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
            "knowledge_qa",
            "activity_priority",
            "invalid_config",
            "internal_operation_data",
            "policy_violation_identification",
            "quote_approval",
            "sales_amount_understanding",
            "sales_cycle_understanding",
            "top_issue_identification",
            "wrong_stage_rectification",
        ]
        for cat in normal_categories:
            task = {"task_category": cat}
            assert self.guard.is_privacy_request(task) is False, f"Should allow {cat}"

    def test_allows_empty_category(self):
        task = {"task_category": ""}
        assert self.guard.is_privacy_request(task) is False

    def test_allows_missing_category(self):
        task = {}
        assert self.guard.is_privacy_request(task) is False

    def test_rejection_message_not_empty(self):
        msg = self.guard.get_rejection()
        assert msg
        assert "cannot provide" in msg.lower() or "sorry" in msg.lower()
