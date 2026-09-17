"""
Shared pytest fixtures.

Ensures the project root is importable as `pipeline.*` / `app.*` regardless
of how pytest is invoked (mirrors the sys.path.insert pattern the pipeline
scripts themselves already use), and provides:
  - `seed_comments`: inserts known rows into a comments table for tests
    that talk to Postgres directly (event_analysis, engagement weighting).
  - `api_client`: a FastAPI TestClient wired to a scratch, pre-seeded
    Postgres database, for the API filter-combination tests.

Requires a reachable Postgres server (see TEST_DATABASE_URL below) --
`docker-compose up db` starts one that matches the defaults here. The test
database itself is created automatically on first use if it doesn't exist.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pipeline.db as db  # noqa: E402
from pipeline.db import INDEXES, SCHEMA, TERM_FREQUENCY_SCHEMA  # noqa: E402

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/cg_foods_sentiment_test",
)

# Default values for any column a test doesn't care about, so fixtures can
# specify only the fields relevant to what they're testing.
ROW_DEFAULTS = {
    "text_raw": "placeholder raw text",
    "text_clean": "placeholder raw text",
    "source_ref": "src:1",
    "engagement": 0,
    "author_id": "author_1",
    "language_guess": "en",
    "region": "unknown",
    "emoji_count": 0,
    "exclusion_reason": None,
    "vader_compound": 0.0,
    "vader_label": "neutral",
    "transformer_label": None,
    "transformer_score": None,
    "transformer_sampled": 0,
    "final_label": "neutral",
    "label_source": "vader",
    "themes": "[]",
    "gold_sentiment": None,
}


def _ensure_test_database() -> None:
    """Creates the test database if it doesn't exist yet, connecting to the
    server's `postgres` maintenance database to run CREATE DATABASE (which
    can't run inside a transaction block, hence autocommit)."""
    parts = urlsplit(TEST_DATABASE_URL)
    dbname = parts.path.lstrip("/")
    admin_url = urlunsplit(parts._replace(path="/postgres"))
    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        conn.close()


def _connect() -> db.Connection:
    _ensure_test_database()
    return psycopg2.connect(
        TEST_DATABASE_URL,
        connection_factory=db.Connection,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def init_schema(conn: db.Connection) -> None:
    conn.execute(SCHEMA)
    conn.execute(TERM_FREQUENCY_SCHEMA)
    for stmt in INDEXES:
        conn.execute(stmt)
    conn.commit()


def reset_schema(conn: db.Connection) -> None:
    """Drops and recreates both tables so each test starts from an empty,
    known schema, mirroring pipeline.db.reset_comments_table's behavior."""
    conn.execute("DROP TABLE IF EXISTS comments;")
    conn.execute("DROP TABLE IF EXISTS term_frequency;")
    conn.commit()
    init_schema(conn)


def seed_comments(conn: db.Connection, rows: list) -> None:
    """rows: list of dicts, each must at least have id/platform/timestamp;
    everything else falls back to ROW_DEFAULTS."""
    columns = list(ROW_DEFAULTS.keys()) + ["id", "platform", "timestamp"]
    full_rows = []
    for row in rows:
        full = dict(ROW_DEFAULTS)
        full.update(row)
        full_rows.append(full)
    placeholders = ", ".join(f"%({c})s" for c in columns)
    conn.executemany(
        f"INSERT INTO comments ({', '.join(columns)}) VALUES ({placeholders})",
        full_rows,
    )
    conn.commit()


@pytest.fixture
def conn():
    """Postgres connection to a scratch test database, with the real
    schema, empty."""
    c = _connect()
    reset_schema(c)
    yield c
    c.close()


@pytest.fixture
def api_client(monkeypatch):
    """TestClient wired to the scratch test database, freshly reset. Tests
    seed it themselves via seed_comments(client.db_conn, rows) before
    making requests, then the client reads from the same database (each
    request opens its own connection via app.main.get_db(), which reads
    pipeline.db.DATABASE_URL -- patched below to point at the test DB)."""
    import app.main as main_module
    from fastapi.testclient import TestClient

    monkeypatch.setattr(db, "DATABASE_URL", TEST_DATABASE_URL)

    setup_conn = _connect()
    reset_schema(setup_conn)

    client = TestClient(main_module.app)
    client.db_conn = setup_conn  # tests seed rows via this handle
    yield client
    setup_conn.close()
