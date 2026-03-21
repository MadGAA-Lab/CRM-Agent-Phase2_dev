"""SQLite wrapper for CRM database access.

Read-only, with query timeout protection and result limiting.
Initialized once in Agent.__init__(), shared across all tasks.
"""

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CRMDatabase:
    """Read-only SQLite CRM database with safety guards."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn: sqlite3.Connection | None = None
        self.query_count = 0
        self.failed_queries = 0
        self._connect()

    def _connect(self) -> None:
        path = Path(self.db_path)
        if path.exists():
            # Open read-only via URI
            self.conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
            self.conn.row_factory = sqlite3.Row
            # Safety: query-only mode
            self.conn.execute("PRAGMA query_only = ON")
            logger.info(f"Connected to CRM database: {self.db_path}")
        else:
            logger.warning(f"Database not found: {self.db_path}")
            self.conn = None

    @property
    def is_available(self) -> bool:
        """Check if a real database is connected."""
        if self.conn is None:
            return False
        try:
            tables = self.get_tables()
            return len(tables) > 1  # More than just _empty
        except Exception:
            return False

    def get_tables(self) -> list[str]:
        """Return list of table names."""
        if not self.conn:
            return []
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [row[0] for row in cursor.fetchall()]

    def describe_table(self, table_name: str) -> dict[str, Any]:
        """Return table schema info: columns, types, row count."""
        if not self.conn:
            return {"success": False, "error": "No database connection"}
        try:
            # Sanitize table name
            table_name = table_name.strip().strip('"').strip("'")
            cursor = self.conn.execute(f'PRAGMA table_info("{table_name}")')
            columns = [{"name": row[1], "type": row[2]} for row in cursor.fetchall()]
            if not columns:
                return {"success": False, "error": f"Table '{table_name}' not found"}

            count_cursor = self.conn.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            row_count = count_cursor.fetchone()[0]
            return {
                "success": True,
                "table": table_name,
                "columns": columns,
                "row_count": row_count,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def execute_query(self, query: str) -> dict[str, Any]:
        """Execute a SQL query and return results.

        Returns dict with:
        - success: bool
        - data: list[dict] (max 15 rows)
        - count: int (total matching rows)
        OR
        - success: False
        - error: str
        """
        self.query_count += 1
        if not self.conn:
            self.failed_queries += 1
            return {"success": False, "error": "No database connection"}

        # Clean up LLM-generated SQL
        query = self._clean_sql(query)

        try:
            cursor = self.conn.execute(query)
            rows = cursor.fetchall()
            if rows:
                columns = [desc[0] for desc in cursor.description]
                result = [dict(zip(columns, row)) for row in rows]
                return {"success": True, "data": result[:15], "count": len(rows)}
            return {"success": True, "data": [], "count": 0}
        except Exception as e:
            self.failed_queries += 1
            return {"success": False, "error": str(e)}

    def _clean_sql(self, query: str) -> str:
        """Strip markdown fences, trailing semicolons, etc."""
        query = query.strip()
        # Remove markdown code fences
        query = re.sub(r"```(?:sql)?", "", query).strip()
        # Remove trailing semicolons
        query = query.rstrip(";")
        return query

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None
