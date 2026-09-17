"""
PostgreSQL storage for the sentiment pipeline. One wide `comments` table
that each stage (clean -> classify -> analyze) fills in incrementally, so
the FastAPI app has a single place to read from.

Connects via the DATABASE_URL env var (see .env.example), defaulting to a
local Postgres instance matching docker-compose.yml's `db` service.
"""

import os

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/cg_foods_sentiment"
)

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
    region TEXT,
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
    "CREATE INDEX IF NOT EXISTS idx_comments_region ON comments(region);",
    "CREATE INDEX IF NOT EXISTS idx_comments_timestamp ON comments(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_comments_final_label ON comments(final_label);",
    "CREATE INDEX IF NOT EXISTS idx_comments_exclusion ON comments(exclusion_reason);",
]


class Connection(psycopg2.extensions.connection):
    """psycopg2 connection with sqlite3.Connection's `execute`/`executemany`
    shorthand (cursor + execute, returning the cursor) bolted on, since the
    pipeline/app code here is written against that convenience API."""

    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql, seq_of_params):
        cur = self.cursor()
        cur.executemany(sql, seq_of_params)
        return cur


def get_connection() -> Connection:
    return psycopg2.connect(
        DATABASE_URL,
        connection_factory=Connection,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def init_db(conn: Connection) -> None:
    conn.execute(SCHEMA)
    conn.execute(TERM_FREQUENCY_SCHEMA)
    for stmt in INDEXES:
        conn.execute(stmt)
    conn.commit()


def reset_comments_table(conn: Connection) -> None:
    """Drop and recreate the comments table (used at the start of a fresh
    clean.py run so re-running the pipeline doesn't leave stale rows)."""
    conn.execute("DROP TABLE IF EXISTS comments;")
    init_db(conn)
