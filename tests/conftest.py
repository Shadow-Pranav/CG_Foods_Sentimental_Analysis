"""
Shared pytest fixtures.

Ensures the project root is importable as `pipeline.*` / `app.*` regardless
of how pytest is invoked (mirrors the sys.path.insert pattern the pipeline
scripts themselves already use), and provides:
  - `seed_comments`: inserts known rows into a comments table for tests
    that talk to SQLite directly (event_analysis, engagement weighting).
  - `api_client`: a FastAPI TestClient wired to a temp, pre-seeded DB, for
    the API filter-combination tests.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.db import SCHEMA, TERM_FREQUENCY_SCHEMA, INDEXES  # noqa: E402

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


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA)
    conn.execute(TERM_FREQUENCY_SCHEMA)
    for stmt in INDEXES:
        conn.execute(stmt)
    conn.commit()


def seed_comments(conn: sqlite3.Connection, rows: list) -> None:
    """rows: list of dicts, each must at least have id/platform/timestamp;
    everything else falls back to ROW_DEFAULTS."""
    columns = list(ROW_DEFAULTS.keys()) + ["id", "platform", "timestamp"]
    full_rows = []
    for row in rows:
        full = dict(ROW_DEFAULTS)
        full.update(row)
        full_rows.append(full)
    placeholders = ", ".join(f":{c}" for c in columns)
    conn.executemany(
        f"INSERT INTO comments ({', '.join(columns)}) VALUES ({placeholders})",
        full_rows,
    )
    conn.commit()


@pytest.fixture
def conn():
    """In-memory SQLite connection with the real schema, empty."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """TestClient wired to a temp, empty-but-schema'd DB file. Tests seed
    it themselves via seed_comments(client_db_conn, rows) before making
    requests, then the client reads from the same file (FastAPI opens a
    fresh sqlite3 connection per request, so committed writes are visible
    immediately)."""
    import app.main as main_module
    from fastapi.testclient import TestClient

    db_path = tmp_path / "test_sentiment.db"
    setup_conn = sqlite3.connect(db_path)
    setup_conn.row_factory = sqlite3.Row  # matches app.main.get_db()'s real connections
    init_schema(setup_conn)

    monkeypatch.setattr(main_module, "DB_PATH", db_path)

    client = TestClient(main_module.app)
    client.db_conn = setup_conn  # tests seed rows via this handle
    yield client
    setup_conn.close()
