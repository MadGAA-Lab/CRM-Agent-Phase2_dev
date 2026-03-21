"""Locate or build the CRMArenaPro SQLite database.

The database contains CRM records (Account, Case, Lead, etc.) that the
agent queries to answer benchmark tasks. This is public CRM data from
the Salesforce/CRMArenaPro dataset — NOT benchmark answers.

Database source priority:
1. Pre-existing file at data/crmarenapro_{org_type}_data.db
2. Download from GitHub repository
"""

import logging
import sqlite3
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
DB_FILENAME = "crmarenapro_{org_type}_data.db"
DB_DOWNLOAD_URL = (
    "https://github.com/MadGAA-Lab/CRM-Agent-Phase2_dev/raw/main/data/"
    "crmarenapro_{org_type}_data.db"
)


def _download_database(db_path: Path, org_type: str) -> bool:
    """Download the database file from GitHub. Returns True on success."""
    url = DB_DOWNLOAD_URL.format(org_type=org_type)
    logger.info(f"Downloading database from {url} ...")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(url, db_path)
        logger.info(f"Database downloaded to {db_path}")
        return True
    except Exception as exc:
        logger.error(f"Failed to download database: {exc}")
        if db_path.exists():
            db_path.unlink()
        return False


def get_database_path(org_type: str = "b2b") -> Path:
    """Return path to CRM database, downloading it if necessary.

    Returns the path to the ready database file.
    """
    db_path = DATA_DIR / DB_FILENAME.format(org_type=org_type)

    if db_path.exists() and _is_valid(db_path):
        logger.info(f"Database found: {db_path}")
        return db_path

    logger.warning(f"Database not found at {db_path}")
    if _download_database(db_path, org_type) and _is_valid(db_path):
        return db_path

    logger.error("Could not obtain a valid database.")
    logger.error("Place crmarenapro_b2b_data.db in the data/ directory manually.")
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
