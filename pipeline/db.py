"""
SQLite storage for the sentiment pipeline. One wide `comments` table that
each stage (clean -> classify -> analyze) fills in incrementally, so the
FastAPI app has a single place to read from.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sentiment.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    text_raw TEXT NOT NULL,
    text_clean TEXT,
    timestamp TEXT NOT NULL,
    source_ref TEXT,
    engagement INTEGER DEFAULT 0,
    author_id TEXT,
    language_guess TEXT,
    emoji_count INTEGER DEFAULT 0,
    exclusion_reason TEXT,
    vader_compound REAL,
    vader_label TEXT,
    transformer_label TEXT,
    transformer_score REAL,
    transformer_sampled INTEGER DEFAULT 0,
    final_label TEXT,
    label_source TEXT,
    themes TEXT,
    gold_sentiment TEXT
);
"""

TERM_FREQUENCY_SCHEMA = """
CREATE TABLE IF NOT EXISTS term_frequency (
    sentiment TEXT NOT NULL,
    term TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (sentiment, term)
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_comments_platform ON comments(platform);",
    "CREATE INDEX IF NOT EXISTS idx_comments_timestamp ON comments(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_comments_final_label ON comments(final_label);",
    "CREATE INDEX IF NOT EXISTS idx_comments_exclusion ON comments(exclusion_reason);",
]


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA)
    conn.execute(TERM_FREQUENCY_SCHEMA)
    for stmt in INDEXES:
        conn.execute(stmt)
    conn.commit()


def reset_comments_table(conn: sqlite3.Connection) -> None:
    """Drop and recreate the comments table (used at the start of a fresh
    clean.py run so re-running the pipeline doesn't leave stale rows)."""
    conn.execute("DROP TABLE IF EXISTS comments;")
    init_db(conn)
