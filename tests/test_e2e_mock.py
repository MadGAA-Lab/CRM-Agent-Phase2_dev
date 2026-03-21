"""
Mock end-to-end tests for the Hybrid ReAct agent.

Tests the pipeline components that DON'T require LLM calls:
- Task JSON parsing
- Schema drift mapping (low/medium/high)
- Context rot stripping
- Privacy rejection (2 categories)
- Action extraction (<execute>, <describe>, <respond>)
- Fallback answer extraction
- Response JSON structure for evaluator compatibility
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from privacy_guard import PrivacyGuard
from schema_introspector import SchemaIntrospector, load_canonical_schema
from context_filter import ContextFilter
from agent import Agent


# ── Fixtures ──

TASK_DATA_PATH = "/tmp/entropic-crmarenapro/data/crmarena_b2b_tasks.json"


def load_tasks():
    """Load real tasks from green agent dataset."""
    if not os.path.exists(TASK_DATA_PATH):
        pytest.skip("Green agent dataset not found at /tmp/entropic-crmarenapro")
    with open(TASK_DATA_PATH) as f:
        return json.load(f)


def build_task_context(raw_task, drift_level="medium", rot_level="medium"):
    """Simulate green agent's _build_task_context()."""
    prompt = raw_task["query"]
    required_context = raw_task["metadata"]["required"]

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
            "drift_mappings": [],
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
            assert "task_id" in ctx
            assert "task_category" in ctx
            assert "prompt" in ctx
            assert "required_context" in ctx
            assert "entropy" in ctx

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
        privacy_tasks = [t for t in tasks if t["task"] in guard.PRIVACY_CATEGORIES]

        assert len(privacy_tasks) >= 100, f"Expected many privacy tasks, got {len(privacy_tasks)}"

        for raw in privacy_tasks[:10]:
            ctx = build_task_context(raw)
            assert guard.is_privacy_request(ctx), f"Failed to detect privacy: {raw['task']} idx={raw['idx']}"

    def test_no_false_positives(self):
        tasks = load_tasks()
        guard = PrivacyGuard()
        non_privacy = [t for t in tasks if t["task"] not in guard.PRIVACY_CATEGORIES]

        for raw in get_sample_tasks(non_privacy, n_per_category=2):
            ctx = build_task_context(raw)
            assert not guard.is_privacy_request(ctx), f"False positive for {raw['task']} idx={raw['idx']}"


class TestDriftMapping:
    """Test schema drift mapping with real tasks."""

    def setup_method(self):
        self.schema = load_canonical_schema()
        self.introspector = SchemaIntrospector(self.schema)

    def test_medium_drift_maps_correctly(self):
        task = {
            "entropy": {"drift_level": "medium", "drift_mappings": []},
            "required_context": "StatusCode: Open, AssignedAgent: 005xxx",
        }
        mapping = self.introspector.introspect(task)

        for table_name, table_map in mapping.items():
            if "Status" in table_map:
                assert table_map["Status"] == "StatusCode"
            if "OwnerId" in table_map:
                assert table_map["OwnerId"] == "AssignedAgent"

    def test_all_drift_levels(self):
        for level in ("low", "medium", "high"):
            task = {"entropy": {"drift_level": level, "drift_mappings": []}}
            mapping = self.introspector.introspect(task)
            for table in self.schema["tables"]:
                assert table in mapping, f"Missing table {table} in {level} drift mapping"


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

    def test_preserves_real_bracket_data(self):
        context = "Tasks: [Task1, Task2], Status: [Active]"
        cleaned = self.filter._strip_rot_notes(context)
        assert "[Task1, Task2]" in cleaned
        assert "[Active]" in cleaned


class TestActionExtraction:
    """Test the ReAct action parser."""

    def setup_method(self):
        self.agent = Agent()

    def test_extract_execute(self):
        response = '<thought>Need to query</thought>\n<execute>SELECT * FROM "Case"</execute>'
        action = self.agent._extract_action(response)
        assert action["type"] == "execute"
        assert "SELECT" in action["content"]
        assert action["thought"] == "Need to query"

    def test_extract_describe(self):
        response = "<thought>Check schema</thought>\n<describe>Case</describe>"
        action = self.agent._extract_action(response)
        assert action["type"] == "describe"
        assert action["content"] == "Case"

    def test_extract_respond(self):
        response = "<thought>Found it</thought>\n<respond>Authority</respond>"
        action = self.agent._extract_action(response)
        assert action["type"] == "respond"
        assert action["content"] == "Authority"

    def test_respond_takes_priority(self):
        """If both execute and respond are present, respond wins."""
        response = '<execute>SELECT 1</execute>\n<respond>September</respond>'
        action = self.agent._extract_action(response)
        assert action["type"] == "respond"
        assert action["content"] == "September"

    def test_fallback_raw_sql(self):
        response = "Let me query: SELECT COUNT(*) FROM Lead WHERE Status = 'Open'"
        action = self.agent._extract_action(response)
        assert action["type"] == "execute"
        assert "SELECT" in action["content"]

    def test_no_action(self):
        response = "I'm not sure what to do."
        action = self.agent._extract_action(response)
        assert action["type"] is None


class TestFallbackAnswer:
    """Test fallback answer extraction."""

    def setup_method(self):
        self.agent = Agent()

    def test_extracts_respond_tag(self):
        assert self.agent._fallback_answer("<respond>CA</respond>") == "CA"

    def test_extracts_salesforce_id(self):
        answer = self.agent._fallback_answer("The agent is 005Wt000003NJ6gIAG")
        assert answer == "005Wt000003NJ6gIAG"

    def test_extracts_month(self):
        assert self.agent._fallback_answer("The busiest month was September") == "September"

    def test_extracts_bant_factor(self):
        assert self.agent._fallback_answer("Missing factor is Authority") == "Authority"

    def test_extracts_quoted_string(self):
        assert self.agent._fallback_answer("The answer is 'California'") == "California"

    def test_empty_returns_none(self):
        assert self.agent._fallback_answer("") == "None"


class TestResponseFormat:
    """Test that response JSON matches what the green agent evaluator expects."""

    def test_response_json_structure(self):
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

        assert "answer" in parsed
        assert "metrics" in parsed
        assert "tokens" in parsed["metrics"]
        assert "tool_calls" in parsed["metrics"]
        assert "queries" in parsed["metrics"]

    def test_response_contains_answer_key(self):
        """Green agent's _check_needs_continuation() stops if '"answer"' in response."""
        response = json.dumps({"answer": "test", "metrics": {"tokens": 0}})
        assert '"answer"' in response

    def test_multi_value_answer_evaluator_parse(self):
        """Simulate the evaluator's _heuristic_parse() on bracket-list format."""
        import re
        answer = "[ID1, ID2, ID3]"
        match = re.search(r"\[(.*?)\]", answer, re.DOTALL)
        assert match
        values = [v.strip().strip("'\"") for v in match.group(1).split(",")]
        assert values == ["ID1", "ID2", "ID3"]
