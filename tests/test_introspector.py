"""Tests for the schema introspector module."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema_introspector import SchemaIntrospector


MINI_SCHEMA = {
    "tables": {
        "Case": {
            "columns": ["Id", "Subject", "Status", "Priority", "AccountId", "OrderItemId__c"],
            "primary_key": "Id",
        },
        "Account": {
            "columns": ["Id", "Name", "BillingState"],
            "primary_key": "Id",
        },
        "Lead": {
            "columns": ["Id", "Name", "Status", "OwnerId"],
            "primary_key": "Id",
        },
    },
    "relationships": [
        {"from": "Case.AccountId", "to": "Account.Id", "description": "case to account"},
    ],
}


class TestSchemaIntrospector:
    def setup_method(self):
        self.introspector = SchemaIntrospector(MINI_SCHEMA)

    def test_identity_mapping_on_no_drift(self):
        task = {
            "entropy": {"drift_level": "none", "rot_level": "none"},
            "required_context": "some data",
        }
        mapping = self.introspector.introspect(task)
        assert mapping["Case"]["Status"] == "Status"
        assert mapping["Account"]["Name"] == "Name"
        assert mapping["Lead"]["OwnerId"] == "OwnerId"

    def test_identity_mapping_on_missing_entropy(self):
        task = {"required_context": "some data"}
        mapping = self.introspector.introspect(task)
        assert mapping["Case"]["Status"] == "Status"

    def test_uses_provided_drift_mappings(self):
        task = {
            "entropy": {
                "drift_level": "medium",
                "rot_level": "none",
                "drift_mappings": [
                    {
                        "table": "Case",
                        "original_column": "Status",
                        "drifted_column": "StatusCode",
                        "drift_type": "synonym",
                    },
                    {
                        "table": "Account",
                        "original_column": "BillingState",
                        "drifted_column": "invoice_region",
                        "drift_type": "synonym",
                    },
                ],
            },
            "required_context": "some data",
        }
        mapping = self.introspector.introspect(task)
        assert mapping["Case"]["Status"] == "StatusCode"
        assert mapping["Account"]["BillingState"] == "invoice_region"
        # Unchanged columns should remain identity
        assert mapping["Case"]["Subject"] == "Subject"
        assert mapping["Account"]["Name"] == "Name"

    def test_get_drifted_name(self):
        task = {
            "entropy": {
                "drift_level": "low",
                "drift_mappings": [
                    {
                        "table": "Lead",
                        "original_column": "OwnerId",
                        "drifted_column": "assigned_agent",
                    },
                ],
            },
        }
        self.introspector.introspect(task)
        assert self.introspector.get_drifted_name("Lead", "OwnerId") == "assigned_agent"
        assert self.introspector.get_drifted_name("Lead", "Name") == "Name"

    def test_schema_description_contains_tables(self):
        task = {"entropy": {"drift_level": "none"}}
        mapping = self.introspector.introspect(task)
        desc = self.introspector.get_schema_description(mapping)
        assert "Table Case" in desc
        assert "Table Account" in desc
        assert "Table Lead" in desc

    def test_schema_description_shows_drift(self):
        task = {
            "entropy": {
                "drift_level": "low",
                "drift_mappings": [
                    {
                        "table": "Case",
                        "original_column": "Status",
                        "drifted_column": "case_status",
                    },
                ],
            },
        }
        mapping = self.introspector.introspect(task)
        desc = self.introspector.get_schema_description(mapping)
        assert "case_status" in desc
        assert "canonical: Status" in desc
