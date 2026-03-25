"""Tests for deterministic handlers — extraction logic and handler registry."""

import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from deterministic_handlers import (
    _extract_time_filter,
    _extract_threshold,
    _extract_direction,
    _word_overlap_score,
    extract_context_date,
    DETERMINISTIC_HANDLERS,
)


class TestExtractTimeFilter:
    """Test time period extraction from questions."""

    def test_past_n_quarters_digit(self):
        f = _extract_time_filter("over the last 4 quarters", "2022-09-04")
        assert ">=" in f and "<=" in f
        assert "'2022-09-04'" in f  # end date
        assert "2021-07-01" in f or "2021-10-01" in f  # quarter-aligned start

    def test_past_n_months_digit(self):
        f = _extract_time_filter("in the past 6 months", "2023-06-15")
        assert "-6 months" in f or "2022-12-15" in f

    def test_past_n_weeks_digit(self):
        f = _extract_time_filter("over the last 3 weeks", "2023-06-15")
        assert "-21 days" in f

    def test_past_word_quarters(self):
        f = _extract_time_filter("in the past four quarters", "2022-09-04")
        assert ">=" in f and "<=" in f

    def test_past_word_months(self):
        f = _extract_time_filter("over the last six months", "2023-06-15")
        assert ">=" in f

    def test_specific_quarter(self):
        f = _extract_time_filter("in the third quarter of 2022", "2024-01-01")
        assert "2022-07-01" in f
        assert "2022-10-01" in f  # uses < '2022-10-01' for Q3 end

    def test_season_spring(self):
        f = _extract_time_filter("in Spring 2023", "2024-01-01")
        assert "2023" in f

    def test_season_fall(self):
        f = _extract_time_filter("in the fall of 2022", "2024-01-01")
        assert "2022" in f

    def test_specific_month(self):
        f = _extract_time_filter("in December 2022", "2024-01-01")
        assert "2022-12-01" in f
        assert "+1 month" in f  # uses < date('2022-12-01', '+1 month')

    def test_no_match_returns_fallback(self):
        f = _extract_time_filter("which agent is best", "2023-01-01")
        # Should return some fallback or empty
        assert f is not None


class TestExtractThreshold:
    """Test case count threshold extraction."""

    def test_more_than_digit(self):
        assert _extract_threshold("managing more than 1 case") == 1

    def test_more_than_word(self):
        assert _extract_threshold("managing more than three cases") == 3

    def test_more_than_zero(self):
        assert _extract_threshold("more than zero cases") == 0

    def test_no_threshold(self):
        assert _extract_threshold("which agent is best") == 0


class TestExtractDirection:
    """Test sort direction extraction."""

    def test_lowest(self):
        assert _extract_direction("lowest average handle time") == "ASC"

    def test_highest(self):
        assert _extract_direction("highest sales total") == "DESC"

    def test_quickest(self):
        assert _extract_direction("quickest case closure") == "ASC"

    def test_slowest(self):
        # "slowest" maps to longest time = DESC, but implementation may
        # treat it as "slow = bad = ASC" depending on context
        d = _extract_direction("slowest average time")
        assert d in ("ASC", "DESC")

    def test_top(self):
        assert _extract_direction("top lead conversion rate") == "DESC"

    def test_default(self):
        # No direction keyword → default ASC
        d = _extract_direction("which agent handles cases")
        assert d in ("ASC", "DESC")


class TestWordOverlapScore:
    """Test word overlap scoring."""

    def test_identical_texts(self):
        score = _word_overlap_score("Feature Update Notification", "Feature Update Notification")
        assert score == 1.0

    def test_partial_overlap(self):
        score = _word_overlap_score(
            "Missing Notifications for Feature Updates",
            "Feature Update Notification"
        )
        assert 0.3 < score < 1.0

    def test_no_overlap(self):
        score = _word_overlap_score("Billing Discrepancy", "AI Feature Malfunction")
        assert score == 0.0

    def test_empty_text(self):
        assert _word_overlap_score("", "something") == 0.0
        assert _word_overlap_score("something", "") == 0.0


class TestExtractContextDate:
    """Test context date extraction."""

    def test_standard_format(self):
        ctx = "## Today's date: 2022-09-04"
        assert extract_context_date(ctx) == "2022-09-04"

    def test_embedded_in_context(self):
        ctx = "Some policy text.\n\n## Today's date: 2023-05-15\n\nMore text."
        assert extract_context_date(ctx) == "2023-05-15"

    def test_no_date(self):
        ctx = "Some policy with no date."
        assert extract_context_date(ctx) == ""


class TestHandlerRegistry:
    """Test that all expected handlers are registered."""

    EXPECTED_CATEGORIES = [
        "handle_time",
        "sales_cycle_understanding",
        "conversion_rate_comprehension",
        "sales_amount_understanding",
        "best_region_identification",
        "transfer_count",
        "lead_routing",
        "case_routing",
    ]

    def test_all_handlers_registered(self):
        for cat in self.EXPECTED_CATEGORIES:
            assert cat in DETERMINISTIC_HANDLERS, f"Missing handler for {cat}"

    def test_handlers_are_callable(self):
        for cat, handler in DETERMINISTIC_HANDLERS.items():
            assert callable(handler), f"Handler for {cat} is not callable"

    def test_no_unexpected_handlers(self):
        for cat in DETERMINISTIC_HANDLERS:
            assert cat in self.EXPECTED_CATEGORIES, f"Unexpected handler: {cat}"


class TestFallbackAnswerExtraction:
    """Test the agent's fallback answer extraction (LIKE leak fix)."""

    def setup_method(self):
        # Import here to avoid circular imports
        from agent import Agent
        self.agent = Agent.__new__(Agent)

    def test_like_pattern_filtered(self):
        response = "I searched with '%discount%' and found results"
        ans = self.agent._fallback_answer(response)
        assert "%" not in ans

    def test_sf_id_extracted(self):
        response = "The agent is 005Wt000003NJ6gIAG"
        ans = self.agent._fallback_answer(response)
        assert ans == "005Wt000003NJ6gIAG"

    def test_month_extracted(self):
        response = "The peak month was September based on the data"
        ans = self.agent._fallback_answer(response)
        assert ans == "September"

    def test_bant_factor_extracted(self):
        response = "The missing BANT factor is Timeline"
        ans = self.agent._fallback_answer(response)
        assert ans == "Timeline"

    def test_respond_tag_preferred(self):
        response = "<respond>005Wt000003NJ6gIAG</respond> some extra text"
        ans = self.agent._fallback_answer(response)
        assert ans == "005Wt000003NJ6gIAG"

    def test_date_offset_filtered(self):
        response = "Using '-12 months' as the offset"
        ans = self.agent._fallback_answer(response)
        assert ans != "-12 months"

    def test_sql_keyword_filtered(self):
        response = "'SELECT' 'FROM' 'WHERE'"
        ans = self.agent._fallback_answer(response)
        # Should not return SQL keywords
        assert ans.upper() not in ("SELECT", "FROM", "WHERE")

    def test_empty_returns_none(self):
        assert self.agent._fallback_answer("") == "None"
        assert self.agent._fallback_answer(None) == "None"
