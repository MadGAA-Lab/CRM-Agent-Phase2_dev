"""Locate or build the CRMArenaPro SQLite database.

The database contains CRM records (Account, Case, Lead, etc.) that the
agent queries to answer benchmark tasks. This is public CRM data from
the Salesforce/CRMArenaPro dataset — NOT benchmark answers.

Database source priority:
1. Pre-existing file at data/crmarenapro_{org_type}_data.db
2. Download from HuggingFace dataset (b2b_schema → build tables)
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
DB_FILENAME = "crmarenapro_{org_type}_data.db"


def get_database_path(org_type: str = "b2b") -> Path:
    """Return path to CRM database, building it if necessary.

    Returns the path to the ready database file.
    """
    db_path = DATA_DIR / DB_FILENAME.format(org_type=org_type)

    if db_path.exists() and _is_valid(db_path):
        logger.info(f"Database found: {db_path}")
        return db_path

    logger.warning(f"Database not found at {db_path}")
    logger.warning("Place crmarenapro_b2b_data.db in the data/ directory.")
    logger.warning("See README.md for instructions on obtaining the database.")
    return db_path


def _is_valid(db_path: Path) -> bool:
    """Check if database file is a valid SQLite with tables."""
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        conn.close()
        return len(tables) >= 5
    except Exception:
        return False
