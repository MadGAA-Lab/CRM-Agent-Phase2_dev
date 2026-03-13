"""Schema drift defense — maps drifted column names at runtime.

The green agent provides drift_mappings in the entropy field, making this
straightforward: we build a bidirectional mapping between canonical and
drifted column names.

When drift_mappings are NOT provided, we fall back to parsing the
required_context to discover actual column names and fuzzy-match them
back to canonical schema.
"""

import json
import logging
import os
import re
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class SchemaIntrospector:
    """
    Detects and maps drifted column names at runtime.

    Strategy:
    1. Read entropy.drift_level and drift_mappings from incoming task
    2. If drift_mappings provided: use them directly
    3. If drift_level != "none" but no mappings: parse context to discover columns
    4. Build canonical_name → drifted_name mapping
    5. All downstream reasoning uses drifted names
    """

    def __init__(self, canonical_schema: dict):
        self.canonical_schema = canonical_schema
        self._mapping_cache: dict[str, dict[str, str]] = {}
        # Build flat column set for fuzzy matching
        self._all_canonical_columns: dict[str, str] = {}  # column -> table
        for table_name, table_info in canonical_schema.get("tables", {}).items():
            for col in table_info.get("columns", []):
                key = f"{table_name}.{col}"
                self._all_canonical_columns[key] = table_name
                # Also store just the column name for simpler lookups
                self._all_canonical_columns[col] = table_name

    def introspect(self, task: dict) -> dict[str, dict[str, str]]:
        """
        Returns a per-table column mapping dict:
        {
            "Case": {"Status": "StatusCode", "Priority": "Priority", ...},
            "Account": {"Name": "Name", ...},
            ...
        }

        If drift_level is "none", returns identity mapping.
        """
        entropy = task.get("entropy", {})
        drift_level = entropy.get("drift_level", "none")

        if drift_level == "none":
            return self._identity_mapping()

        # Check if drift_mappings are provided by the green agent
        drift_mappings = entropy.get("drift_mappings", [])
        if drift_mappings:
            return self._build_mapping_from_provided(drift_mappings)

        # Fallback: parse required_context to discover drifted columns
        context = task.get("required_context", "")
        return self._build_mapping_from_context(context)

    def get_drifted_name(self, table: str, canonical_name: str) -> str:
        """Look up the drifted name for a canonical column in a specific table."""
        table_mapping = self._mapping_cache.get(table, {})
        return table_mapping.get(canonical_name, canonical_name)

    def get_schema_description(self, mapping: dict[str, dict[str, str]]) -> str:
        """
        Generate a human-readable schema description using drifted names.
        Used in LLM prompts to inform the model about actual column names.
        """
        lines = []
        for table_name, table_info in self.canonical_schema.get("tables", {}).items():
            table_map = mapping.get(table_name, {})
            col_descriptions = []
            for col in table_info.get("columns", []):
                drifted = table_map.get(col, col)
                if drifted != col:
                    col_descriptions.append(f"{drifted} (canonical: {col})")
                else:
                    col_descriptions.append(col)
            lines.append(f"Table {table_name}: {', '.join(col_descriptions)}")

        # Add relationships
        lines.append("\nKey Relationships:")
        for rel in self.canonical_schema.get("relationships", []):
            lines.append(f"  {rel['from']} → {rel['to']} ({rel['description']})")

        return "\n".join(lines)

    def _identity_mapping(self) -> dict[str, dict[str, str]]:
        """Return identity mapping (canonical = drifted)."""
        mapping = {}
        for table_name, table_info in self.canonical_schema.get("tables", {}).items():
            mapping[table_name] = {col: col for col in table_info.get("columns", [])}
        self._mapping_cache = mapping
        return mapping

    def _build_mapping_from_provided(
        self, drift_mappings: list[dict]
    ) -> dict[str, dict[str, str]]:
        """Build mapping from green agent's provided drift_mappings."""
        # Start with identity mapping
        mapping = self._identity_mapping()

        # Override with drifted columns
        for dm in drift_mappings:
            table = dm.get("table", "")
            original = dm.get("original_column", "")
            drifted = dm.get("drifted_column", "")
            if table in mapping and original in mapping[table]:
                mapping[table][original] = drifted
                logger.info(f"Drift: {table}.{original} → {drifted}")

        self._mapping_cache = mapping
        return mapping

    def _build_mapping_from_context(self, context: str) -> dict[str, dict[str, str]]:
        """
        Fallback: parse required_context to discover drifted column names
        and fuzzy-match them to canonical schema.
        """
        mapping = self._identity_mapping()

        if not context:
            return mapping

        # Extract potential column names from context
        # Look for patterns like "column_name: value" or "column_name = value"
        # or header rows in tabular data
        discovered_columns = set()

        # Pattern 1: Key-value pairs (e.g., "Status: Open")
        kv_pattern = re.compile(r"(?:^|\n)\s*[-•]?\s*(\w[\w\s]*\w|\w+)\s*[:=]\s*", re.MULTILINE)
        for match in kv_pattern.finditer(context):
            col_name = match.group(1).strip()
            if len(col_name) < 50:  # Reasonable column name length
                discovered_columns.add(col_name)

        # Pattern 2: Table headers (pipe-separated)
        header_pattern = re.compile(r"\|([^|]+)")
        for match in header_pattern.finditer(context):
            col_name = match.group(1).strip()
            if col_name and len(col_name) < 50 and not col_name.startswith("-"):
                discovered_columns.add(col_name)

        # Pattern 3: JSON keys
        json_key_pattern = re.compile(r'"(\w+)"\s*:')
        for match in json_key_pattern.finditer(context):
            discovered_columns.add(match.group(1))

        # Fuzzy match discovered columns to canonical schema
        for table_name, table_info in self.canonical_schema.get("tables", {}).items():
            for canonical_col in table_info.get("columns", []):
                best_match = canonical_col
                best_score = 0.0

                for discovered in discovered_columns:
                    # Exact match
                    if discovered.lower() == canonical_col.lower():
                        best_match = discovered
                        best_score = 1.0
                        break

                    # Fuzzy match
                    score = SequenceMatcher(
                        None, canonical_col.lower(), discovered.lower()
                    ).ratio()
                    if score > best_score and score > 0.6:
                        best_match = discovered
                        best_score = score

                if best_score > 0.6:
                    mapping[table_name][canonical_col] = best_match

        self._mapping_cache = mapping
        return mapping


def load_canonical_schema() -> dict:
    """Load the canonical schema from config/schema.json."""
    schema_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "schema.json"
    )
    with open(schema_path) as f:
        return json.load(f)
