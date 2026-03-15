"""
Mock end-to-end tests using real task payloads from the green agent dataset.

Tests the entire pipeline EXCEPT LLM calls:
- Task JSON parsing (all 22 categories)
- Schema drift mapping (low/medium/high)
- Context rot stripping
- Privacy rejection (3 categories)
- Answer formatting (single, multi-value, bracket-list)
- Response JSON structure for evaluator compatibility
- Full pipeline integration with mock LLM

Requires: /tmp/entropic-crmarenapro/data/crmarena_b2b_tasks.json
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from privacy_guard import PrivacyGuard
from task_planner import TaskPlanner
from schema_introspector import SchemaIntrospector, load_canonical_schema
from context_filter import ContextFilter
from answer_synthesizer import AnswerSynthesizer
from sql_generator import SQLGenerator


# ── Fixtures ──

TASK_DATA_PATH = "/tmp/entropic-crmarenapro/data/crmarena_b2b_tasks.json"

def load_tasks():
    """Load real tasks from green agent dataset."""
    if not os.path.exists(TASK_DATA_PATH):
        pytest.skip("Green agent dataset not found at /tmp/entropic-crmarenapro")
    with open(TASK_DATA_PATH) as f:
        return json.load(f)


def build_task_context(raw_task, drift_level="medium", rot_level="medium"):
    """
    Simulate green agent's _build_task_context().
    Applies drift + rot exactly as the green agent does.
    """
    prompt = raw_task["query"]
    required_context = raw_task["metadata"]["required"]

    # Apply schema drift (text replacement, same as green agent)
    drift_mappings = {
        "low": {"Status": "CaseStatus", "OwnerId": "AssignedTo", "AccountId": "CustomerRef"},
        "medium": {"Status": "StatusCode", "OwnerId": "AssignedAgent", "AccountId": "ClientId",
                    "ContactId": "PersonRef", "Subject": "Title", "Description": "Details"},
        "high": {"Status": "st_code", "OwnerId": "own_ref", "AccountId": "acct_id",
                 "ContactId": "cont_ref", "Subject": "subj", "Description": "desc",
                 "Priority": "pri_level", "CreatedDate": "create_dt", "CaseNumber": "ticket_num"},
    }
    if drift_level != "none":
        for original, drifted in drift_mappings.get(drift_level, {}).items():
            prompt = prompt.replace(original, drifted)
            required_context = required_context.replace(original, drifted)

    # Apply context rot (appended notes, same as green agent)
    rot_notes = {
        "low": ["\n\n[Note: Some records may have been updated recently. Verify timestamps.]"],
        "medium": [
            "\n\n[System Notice: Database migration in progress. Some field names may vary.]",
            "\n\n[Info: Legacy records from previous CRM system included for reference.]",
        ],
        "high": [
            "\n\n[Warning: Multiple customer records with similar names exist. Verify IDs carefully.]",
            "\n\n[Notice: Archived cases from 2019-2020 included. Filter by date if needed.]",
            "\n\n[Alert: Some account records are marked as duplicates pending merge.]",
        ],
    }
    if rot_level != "none":
        notes = rot_notes.get(rot_level, [])
        required_context += "".join(notes)

    return {
        "type": "crm_task",
        "task_id": raw_task["idx"],
        "task_category": raw_task["task"],
        "prompt": prompt,
        "persona": raw_task["persona"],
        "required_context": required_context,
        "config": {"org_type": "b2b", "max_steps": 10},
        "entropy": {
            "drift_level": drift_level,
            "rot_level": rot_level,
            "drift_mappings": [],  # Always empty in green agent
            "note": "Schema/context has been modified for robustness testing",
        },
    }


def get_sample_tasks(tasks, n_per_category=1):
    """Get n sample tasks per category."""
    seen = {}
    for t in tasks:
        cat = t["task"]
        if cat not in seen:
            seen[cat] = []
        if len(seen[cat]) < n_per_category:
            seen[cat].append(t)
    return [t for tasks_list in seen.values() for t in tasks_list]


# ── Test Classes ──


class TestTaskParsing:
    """Test that our agent can parse all 22 category payloads."""

    def test_parse_all_categories(self):
        tasks = load_tasks()
        samples = get_sample_tasks(tasks)
        assert len(samples) == 22, f"Expected 22 categories, got {len(samples)}"

        for raw in samples:
            ctx = build_task_context(raw)
            # Verify all required fields present
            assert "task_id" in ctx
            assert "task_category" in ctx
            assert "prompt" in ctx
            assert "required_context" in ctx
            assert "entropy" in ctx
            assert ctx["entropy"]["drift_level"] == "medium"
            assert ctx["entropy"]["rot_level"] == "medium"
            assert ctx["entropy"]["drift_mappings"] == []

    def test_task_json_roundtrip(self):
        """Verify task context survives JSON serialization (as sent over A2A)."""
        tasks = load_tasks()
        for raw in get_sample_tasks(tasks):
            ctx = build_task_context(raw)
            serialized = json.dumps(ctx)
            deserialized = json.loads(serialized)
            assert deserialized["task_category"] == raw["task"]
            assert deserialized["task_id"] == raw["idx"]


class TestPrivacyRejection:
    """Test privacy rejection on real privacy tasks."""

    def test_all_privacy_tasks_detected(self):
        tasks = load_tasks()
        guard = PrivacyGuard()
        privacy_cats = {"private_customer_information", "confidential_company_knowledge", "internal_operation_data"}
        privacy_tasks = [t for t in tasks if t["task"] in privacy_cats]

        assert len(privacy_tasks) == 240, f"Expected 240 privacy tasks, got {len(privacy_tasks)}"

        for raw in privacy_tasks:
            ctx = build_task_context(raw)
            assert guard.is_privacy_request(ctx), f"Failed to detect privacy: {raw['task']} idx={raw['idx']}"

    def test_no_false_positives(self):
        tasks = load_tasks()
        guard = PrivacyGuard()
        non_privacy = [t for t in tasks if t["task"] not in
                       {"private_customer_information", "confidential_company_knowledge", "internal_operation_data"}]

        for raw in get_sample_tasks([t for t in non_privacy], n_per_category=2):
            ctx = build_task_context(raw)
            assert not guard.is_privacy_request(ctx), f"False positive for {raw['task']} idx={raw['idx']}"

    def test_privacy_rejection_message_format(self):
        guard = PrivacyGuard()
        msg = guard.get_rejection()
        assert isinstance(msg, str)
        assert len(msg) > 10
        # Should NOT contain brackets (evaluator checks for None-equivalents)
        assert "[" not in msg


class TestDriftMapping:
    """Test schema drift mapping with real tasks."""

    def setup_method(self):
        self.schema = load_canonical_schema()
        self.introspector = SchemaIntrospector(self.schema)

    def test_medium_drift_maps_correctly(self):
        """Medium drift should map Status→StatusCode, OwnerId→AssignedAgent, etc."""
        task = {
            "entropy": {"drift_level": "medium", "drift_mappings": []},
            "required_context": "StatusCode: Open, AssignedAgent: 005xxx",
        }
        mapping = self.introspector.introspect(task)

        # Verify known medium drift mappings applied
        for table_name, table_map in mapping.items():
            if "Status" in table_map:
                assert table_map["Status"] == "StatusCode"
            if "OwnerId" in table_map:
                assert table_map["OwnerId"] == "AssignedAgent"
            if "AccountId" in table_map:
                assert table_map["AccountId"] == "ClientId"

    def test_all_drift_levels(self):
        for level in ("low", "medium", "high"):
            task = {"entropy": {"drift_level": level, "drift_mappings": []}}
            mapping = self.introspector.introspect(task)
            # Should have entries for all tables in schema
            for table in self.schema["tables"]:
                assert table in mapping, f"Missing table {table} in {level} drift mapping"

    def test_schema_description_shows_drifted_names(self):
        task = {"entropy": {"drift_level": "medium", "drift_mappings": []}}
        mapping = self.introspector.introspect(task)
        desc = self.introspector.get_schema_description(mapping)

        # Should mention drifted names
        assert "StatusCode" in desc
        assert "canonical: Status" in desc

    def test_drift_on_real_task_context(self):
        """Apply drift to a real task and verify introspector recovers."""
        tasks = load_tasks()
        # Find a task with Status in its context
        for raw in tasks:
            if "Status" in raw["metadata"]["required"]:
                ctx = build_task_context(raw, drift_level="medium")
                mapping = self.introspector.introspect(ctx)
                # Verify Status maps to StatusCode
                found = any(
                    m.get("Status") == "StatusCode"
                    for m in mapping.values()
                )
                assert found, "Drift mapping should map Status→StatusCode"
                break


class TestRotStripping:
    """Test context rot stripping on real rotted contexts."""

    def setup_method(self):
        self.filter = ContextFilter(llm_client=None)

    def test_strips_medium_rot_notes(self):
        raw_context = "Lead: John Smith, Status: Qualified, Company: Acme"
        rotted = (
            raw_context
            + "\n\n[System Notice: Database migration in progress. Some field names may vary.]"
            + "\n\n[Info: Legacy records from previous CRM system included for reference.]"
        )
        cleaned = self.filter._strip_rot_notes(rotted)
        assert "[System Notice:" not in cleaned
        assert "[Info:" not in cleaned
        assert "John Smith" in cleaned

    def test_strips_high_rot_notes(self):
        raw_context = "Case 12345: Priority High"
        rotted = (
            raw_context
            + "\n\n[Warning: Multiple customer records with similar names exist. Verify IDs carefully.]"
            + "\n\n[Notice: Archived cases from 2019-2020 included. Filter by date if needed.]"
            + "\n\n[Alert: Some account records are marked as duplicates pending merge.]"
        )
        cleaned = self.filter._strip_rot_notes(rotted)
        assert "[Warning:" not in cleaned
        assert "[Notice:" not in cleaned  # Note: [Notice:...] not in our patterns, but [Alert:...] isn't either
        assert "Case 12345" in cleaned

    def test_strips_rot_from_real_tasks(self):
        tasks = load_tasks()
        for raw in get_sample_tasks(tasks, n_per_category=1):
            ctx = build_task_context(raw, rot_level="medium")
            original_context = raw["metadata"]["required"]
            rotted_context = ctx["required_context"]

            # Rotted should be longer (has appended notes)
            assert len(rotted_context) >= len(original_context)

            # Strip should remove the notes
            cleaned = self.filter._strip_rot_notes(rotted_context)
            assert "[System Notice:" not in cleaned
            assert "[Info:" not in cleaned

    def test_preserves_real_bracket_data(self):
        """Ensure we don't strip legitimate brackets in CRM data."""
        context = "Tasks: [Task1, Task2], Status: [Active]"
        cleaned = self.filter._strip_rot_notes(context)
        # Legitimate brackets should survive (our patterns are specific: [Note:...], [System Notice:...])
        assert "[Task1, Task2]" in cleaned
        assert "[Active]" in cleaned


class TestAnswerFormatting:
    """Test answer synthesizer produces evaluator-compatible output."""

    def setup_method(self):
        self.synth = AnswerSynthesizer()

    def test_single_value_passthrough(self):
        answer = self.synth.synthesize(
            {"task_category": "lead_qualification"},
            "Authority",
            "exact_query_match",
            "some context",
        )
        assert answer == "Authority"

    def test_multi_value_bracket_wrapping(self):
        answer = self.synth.synthesize(
            {"task_category": "activity_priority"},
            "ID1, ID2, ID3",
            "exact_query_match",
            "some context with ID1 ID2 ID3",
        )
        assert answer == "[ID1, ID2, ID3]"

    def test_already_bracketed_passthrough(self):
        answer = self.synth.synthesize(
            {"task_category": "activity_priority"},
            "[ID1, ID2, ID3]",
            "exact_query_match",
            "some context with ID1 ID2 ID3",
        )
        assert answer == "[ID1, ID2, ID3]"

    def test_strips_llm_prefix(self):
        answer = self.synth.synthesize(
            {"task_category": "lead_qualification"},
            "The answer is Authority",
            "exact_query_match",
            "Authority in context",
        )
        assert answer == "Authority"

    def test_strips_quotes(self):
        answer = self.synth.synthesize(
            {"task_category": "best_region_identification"},
            '"California"',
            "exact_query_match",
            "California in context",
        )
        assert answer == "California"

    def test_privacy_rejection_format(self):
        answer = self.synth.synthesize(
            {"task_category": "private_customer_information"},
            "",
            "privacy_rejection",
            "",
        )
        assert "cannot provide" in answer.lower() or "sorry" in answer.lower()

    def test_insufficient_data(self):
        answer = self.synth.synthesize(
            {"task_category": "handle_time"},
            "insufficient data",
            "exact_query_match",
            "",
        )
        assert answer == "insufficient data"


class TestTaskPlanning:
    """Test task planner routes all 22 real categories correctly."""

    def test_all_real_categories_have_strategy(self):
        tasks = load_tasks()
        planner = TaskPlanner()
        categories = {t["task"] for t in tasks}

        for cat in categories:
            ctx = {"task_category": cat}
            plan = planner.plan(ctx)
            assert plan["strategy"] in (
                "privacy_rejection", "exact_query_match", "semantic_retrieval"
            ), f"Unknown strategy for {cat}: {plan['strategy']}"

    def test_all_real_categories_have_hints(self):
        tasks = load_tasks()
        planner = TaskPlanner()
        non_privacy = {t["task"] for t in tasks} - {
            "private_customer_information", "confidential_company_knowledge", "internal_operation_data"
        }

        for cat in non_privacy:
            plan = planner.plan({"task_category": cat})
            assert plan.get("category_hint"), f"Missing hint for {cat}"


class TestResponseFormat:
    """Test that our response JSON matches what the green agent evaluator expects."""

    def test_response_json_structure(self):
        """Green agent's _parse_response_metrics expects this structure."""
        response = {
            "task_id": "42",
            "answer": "Authority",
            "category": "lead_qualification",
            "metrics": {
                "tokens": 500,
                "tool_calls": 1,
                "queries": 1,
            },
        }
        serialized = json.dumps(response)
        parsed = json.loads(serialized)

        # Evaluator extracts "answer" key
        assert "answer" in parsed
        # _parse_response_metrics extracts "metrics"
        assert "metrics" in parsed
        assert "tokens" in parsed["metrics"]
        assert "tool_calls" in parsed["metrics"]
        assert "queries" in parsed["metrics"]

    def test_response_contains_answer_key_for_continuation_check(self):
        """
        Green agent's _check_needs_continuation() stops if '"answer"' in response.
        Our response must contain this to prevent multi-turn loops.
        """
        response = json.dumps({"answer": "test", "metrics": {"tokens": 0}})
        assert '"answer"' in response

    def test_multi_value_answer_evaluator_parse(self):
        """
        Simulate the evaluator's _heuristic_parse() on our bracket-list format.
        regex: re.search(r'\\[(.*?)\\]', content, re.DOTALL)
        """
        import re
        answer = "[ID1, ID2, ID3]"
        match = re.search(r"\[(.*?)\]", answer, re.DOTALL)
        assert match
        values = [v.strip().strip("'\"") for v in match.group(1).split(",")]
        assert values == ["ID1", "ID2", "ID3"]


class TestFullPipelineIntegration:
    """Test the full pipeline with mock LLM, using real task payloads."""

    def test_privacy_pipeline_no_llm(self):
        """Privacy tasks should complete without any LLM calls."""
        tasks = load_tasks()
        guard = PrivacyGuard()
        planner = TaskPlanner()
        synth = AnswerSynthesizer()

        privacy_tasks = [t for t in tasks if t["task"] in guard.PRIVACY_CATEGORIES]
        assert len(privacy_tasks) > 0

        for raw in privacy_tasks[:5]:
            ctx = build_task_context(raw)

            # L0: Privacy check
            assert guard.is_privacy_request(ctx)
            rejection = guard.get_rejection()

            # L2: Plan
            plan = planner.plan(ctx)
            assert plan["strategy"] == "privacy_rejection"

            # L4: Synthesize
            answer = synth.synthesize(ctx, "", "privacy_rejection", "")

            # Build response
            response = json.dumps({
                "task_id": ctx["task_id"],
                "answer": answer,
                "metrics": {"tokens": 0, "tool_calls": 0, "queries": 0},
            })

            parsed = json.loads(response)
            assert parsed["metrics"]["tokens"] == 0  # Zero token efficiency
            assert '"answer"' in response  # Stops green agent continuation

    def test_exact_match_pipeline_with_mock_llm(self):
        """Test exact match pipeline end-to-end with mocked LLM."""
        tasks = load_tasks()
        schema = load_canonical_schema()
        introspector = SchemaIntrospector(schema)
        cf = ContextFilter(llm_client=None)
        planner = TaskPlanner()
        synth = AnswerSynthesizer()

        # Get a lead_qualification task (answer is ['Authority'])
        raw = next(t for t in tasks if t["task"] == "lead_qualification")
        ctx = build_task_context(raw)
        expected_answer = raw["answer"]  # ['Authority']

        # L1: Introspect
        mapping = introspector.introspect(ctx)
        schema_info = introspector.get_schema_description(mapping)
        assert "StatusCode" in schema_info  # medium drift applied

        # L1: Filter rot
        cleaned = asyncio.get_event_loop().run_until_complete(cf.filter(ctx))
        assert "[System Notice:" not in cleaned

        # L2: Plan
        plan = planner.plan(ctx)
        assert plan["strategy"] == "exact_query_match"
        assert plan["category_hint"]

        # L3: Mock LLM returning the correct answer
        mock_llm_answer = expected_answer[0]  # "Authority"

        # L4: Synthesize
        answer = synth.synthesize(ctx, mock_llm_answer, "exact_query_match", cleaned)
        assert answer == "Authority"

        # Build response
        response = json.dumps({
            "task_id": ctx["task_id"],
            "answer": answer,
            "metrics": {"tokens": 500, "tool_calls": 1, "queries": 1},
        })
        parsed = json.loads(response)
        assert parsed["answer"] == "Authority"

    def test_multi_value_pipeline(self):
        """Test multi-value answer formatting through the pipeline."""
        tasks = load_tasks()
        synth = AnswerSynthesizer()

        # Find a multi-value task
        raw = next(t for t in tasks if isinstance(t["answer"], list) and len(t["answer"]) > 2)
        ctx = build_task_context(raw)

        # Simulate LLM returning comma-separated IDs
        mock_answer = ", ".join(raw["answer"])
        answer = synth.synthesize(ctx, mock_answer, "exact_query_match", "context")

        # Should be bracket-wrapped
        assert answer.startswith("[")
        assert answer.endswith("]")

        # Verify evaluator can parse it
        import re
        match = re.search(r"\[(.*?)\]", answer, re.DOTALL)
        assert match
        parsed_values = [v.strip().strip("'\"") for v in match.group(1).split(",")]
        assert sorted(parsed_values) == sorted(raw["answer"])

    def test_sql_generator_loads_category_prompts(self):
        """Verify SQLGenerator loads all 19 category-specific prompts."""
        # Mock LLM client
        mock_llm = MagicMock()
        gen = SQLGenerator(mock_llm)

        # Check all category prompts exist
        expected_categories = [
            "lead_qualification", "lead_routing", "case_routing", "handle_time",
            "transfer_count", "sales_insight_mining", "monthly_trend_analysis",
            "best_region_identification", "conversion_rate_comprehension",
            "named_entity_disambiguation", "knowledge_qa", "activity_priority",
            "invalid_config", "policy_violation_identification", "quote_approval",
            "sales_amount_understanding", "sales_cycle_understanding",
            "top_issue_identification", "wrong_stage_rectification",
        ]

        for cat in expected_categories:
            key = f"category_{cat}"
            assert key in gen._prompts, f"Missing prompt template: {key}"
            # Verify template has required placeholders
            template = gen._prompts[key]
            assert "{schema_info}" in template, f"Missing {{schema_info}} in {key}"
            assert "{filtered_context}" in template, f"Missing {{filtered_context}} in {key}"
            assert "{prompt}" in template, f"Missing {{prompt}} in {key}"

    def test_category_prompt_format_strings_valid(self):
        """Verify all category prompts can be formatted without errors."""
        mock_llm = MagicMock()
        gen = SQLGenerator(mock_llm)

        test_vars = {
            "schema_info": "Table Case: Id, Status",
            "filtered_context": "Some CRM data",
            "task_category": "test",
            "prompt": "What is the status?",
            "persona": "You are an analyst",
            "category_hint": "Check status field",
        }

        for key, template in gen._prompts.items():
            if key.startswith("category_"):
                try:
                    formatted = template.format(**test_vars)
                    assert len(formatted) > 0
                except KeyError as e:
                    pytest.fail(f"Template {key} has unknown placeholder: {e}")
