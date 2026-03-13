"""Tests for the context filter module."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from context_filter import ContextFilter


class TestContextFilter:
    def setup_method(self):
        self.filter = ContextFilter(llm_client=None)

    @pytest.mark.asyncio
    async def test_no_rot_returns_original(self):
        task = {
            "entropy": {"rot_level": "none"},
            "required_context": "Original context data",
            "prompt": "What is the status?",
        }
        result = await self.filter.filter(task)
        assert result == "Original context data"

    @pytest.mark.asyncio
    async def test_missing_entropy_returns_original(self):
        task = {
            "required_context": "Original context data",
            "prompt": "What is the status?",
        }
        result = await self.filter.filter(task)
        assert result == "Original context data"

    @pytest.mark.asyncio
    async def test_empty_context_returns_empty(self):
        task = {
            "entropy": {"rot_level": "low"},
            "required_context": "",
            "prompt": "What is the status?",
        }
        result = await self.filter.filter(task)
        assert result == ""

    @pytest.mark.asyncio
    async def test_filters_irrelevant_sections(self):
        task = {
            "entropy": {"rot_level": "low"},
            "prompt": "What is the lead status for Agent_A?",
            "task_category": "lead_qualification",
            "required_context": (
                "## Lead Records\n"
                "- Lead 1: owner=Agent_A, status=Qualified, company=Acme Corp\n"
                "- Lead 2: owner=Agent_A, status=New, company=Beta Inc\n"
                "\n\n"
                "## Unrelated Weather Data\n"
                "The temperature in San Francisco is 65°F with partly cloudy skies.\n"
                "Expected rainfall is 0.2 inches this weekend."
            ),
        }
        result = await self.filter.filter(task)
        # The lead records section should be kept
        assert "Agent_A" in result
        assert "Qualified" in result

    @pytest.mark.asyncio
    async def test_single_section_returns_as_is(self):
        task = {
            "entropy": {"rot_level": "medium"},
            "required_context": "Just one single block of text with no clear sections.",
            "prompt": "What is the status?",
        }
        result = await self.filter.filter(task)
        assert result == "Just one single block of text with no clear sections."

    def test_parse_sections_markdown(self):
        context = "## Section 1\nData 1\n## Section 2\nData 2\n## Section 3\nData 3"
        sections = self.filter._parse_sections(context)
        assert len(sections) == 3

    def test_parse_sections_paragraphs(self):
        context = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        sections = self.filter._parse_sections(context)
        assert len(sections) == 3

    def test_parse_sections_json_array(self):
        context = '[{"id": 1}, {"id": 2}, {"id": 3}]'
        sections = self.filter._parse_sections(context)
        assert len(sections) == 3
