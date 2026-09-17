# Technical Documentation — Wai Wai / CG Foods Sentiment Tracker

This document covers the system's architecture, data model, and
implementation details -- the "how it's built," complementing
`PROJECT_INSTRUCTIONS.md` (scope), `CONTEXT.md` (business narrative),
`DATA_SOURCES.md` (sourcing/collection), and `METHODOLOGY.md` (cleaning/
classification methodology). Start with `README.md` for setup and usage;
this document is for anyone modifying or operating the system.

## 1. System overview

A batch pipeline turns raw social comments into a labeled, queryable
dataset in PostgreSQL; a FastAPI app serves that dataset as JSON plus a
single-page dashboard. There is no continuously-running worker or queue --
the pipeline is a sequence of one-shot scripts run manually (or on a
schedule, e.g. cron/CI) that each read from and write to the same
Postgres database.

```
                    ┌─────────────────────────────────────────────┐
                    │              Pipeline (batch, CLI)           │
                    │                                               │
 raw_comments.csv ◄─┤ generate_sample_data.py  /  collect.py        │
        │           └───────────────────┬───────────────────────────┘
        ▼                               │
   clean.py  ──────────► comments table (Postgres) ◄────── classify.py
        │                       ▲    ▲                          │
        │                       │    │                          ▼
        │                       │    └──────────── analyze.py ──┘
        │                       │                       │
        │                       │                       ▼
        │                       │              term_frequency table
        │                       │
        ▼                       │
  event_analysis.py ────────────┘ (reads comments, writes
        │                          data/event_analysis.json,
        ▼                          also computed live by the API)
  data/event_analysis.json

                    ┌─────────────────────────────────────────────┐
                    │                FastAPI app                   │
                    │  app/main.py -- JSON API + dashboard route    │
                    │  reads: comments, term_frequency (Postgres)   │
                    └───────────────────┬───────────────────────────┘
                                        │
                                        ▼
                          app/templates/dashboard.html
                          + app/static/js/dashboard.js (Chart.js)
```

Two independent, parallel evaluation paths sit alongside the main
pipeline and don't feed back into `comments`:
- `label_gold.py` / `evaluate.py` -- human gold-labeling CLI and scoring,
  persisted to `data/gold_labels.csv` (not the DB -- see §3.4).
- `report.py` -- PDF snapshot generation, reading the same `comments`
  table (`gather_report_data()`, shared by the CLI script and
  `GET /api/report`).

## 2. Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.9+ |
| Web framework | FastAPI + Uvicorn (ASGI) |
| Templates | Jinja2 |
| Database | PostgreSQL, via `psycopg2` |
| NLP | VADER (`vaderSentiment`), HuggingFace `transformers`/`torch` (`cardiffnlp/twitter-xlm-roberta-base-sentiment`), `langdetect` |
| Stats | `scipy` (chi-square / Fisher's exact) |
| PDF | `reportlab` |
| Frontend | Vanilla HTML/CSS/JS + Chart.js (CDN, no build step) |
| Testing | `pytest`, FastAPI `TestClient` (`httpx`) |
| Containerization | Docker + Docker Compose (`db` + `app` services) |

## 3. Data model

### 3.1 `comments` table

One row per collected/generated comment, filled in incrementally as it
moves through the pipeline stages. Defined in `pipeline/db.py::SCHEMA`.

| Column | Type | Written by | Notes |
|---|---|---|---|
| `id` | `TEXT` PK | collect/generate | e.g. `youtube_00042` |
| `platform` | `TEXT` | collect/generate | `youtube` \| `twitter` \| `facebook` |
| `text_raw` | `TEXT` | collect/generate | Never overwritten downstream |
| `text_clean` | `TEXT` | `clean.py` | HTML/URL/emoji-stripped |
| `timestamp` | `TEXT` | collect/generate | ISO 8601 string (`YYYY-MM-DDTHH:MM:SS`), stored as text, not a native timestamp type -- see §5.4 |
| `source_ref` | `TEXT` | collect/generate | Source video/post/tweet id |
| `engagement` | `INTEGER` | collect/generate | Likes/reactions count |
| `author_id` | `TEXT` | collect/generate | |
| `language_guess` | `TEXT` | `clean.py` | `langdetect` output, or `unknown` |
| `region` | `TEXT` | collect/generate | `nepal` \| `india` \| `unknown` (best-effort, see `DATA_SOURCES.md`) |
| `emoji_count` | `INTEGER` | `clean.py` | |
| `exclusion_reason` | `TEXT`, nullable | `clean.py` | `NULL` = kept; else `spam_pattern`/`duplicate`/`near_duplicate`/`empty_after_clean` |
| `vader_compound` | `REAL` | `classify.py` | |
| `vader_label` | `TEXT` | `classify.py` | |
| `transformer_label` | `TEXT`, nullable | `classify.py` | Only set for the sampled subset |
| `transformer_score` | `REAL`, nullable | `classify.py` | |
| `transformer_sampled` | `INTEGER` (0/1) | `classify.py` | |
| `final_label` | `TEXT` | `classify.py` | The label every downstream query/API actually uses |
| `label_source` | `TEXT` | `classify.py` | `vader` or `transformer` |
| `themes` | `TEXT`, nullable | `analyze.py` | JSON-encoded array of theme/competitor tags |
| `gold_sentiment` | `TEXT`, nullable | collect/generate | Synthetic-data-only proxy; see `classify.py`'s docstring |

Indexes (`pipeline/db.py::INDEXES`): `platform`, `region`, `timestamp`,
`final_label`, `exclusion_reason` -- covering the columns every API
endpoint filters or groups on.

`exclusion_reason IS NULL` is the "kept" predicate used everywhere
downstream (queries, API filters) -- excluded rows stay in the table
rather than being deleted, so the pipeline is auditable end to end.

### 3.2 `term_frequency` table

```sql
CREATE TABLE term_frequency (
    sentiment TEXT NOT NULL,
    term TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (sentiment, term)
);
```

Top-30 stopword-filtered tokens per sentiment class, fully rebuilt
(`DELETE` + re-`INSERT`) by every `analyze.py` run. Backs
`GET /api/wordcloud`.

### 3.3 Lifecycle: full rebuild, not incremental upsert

`pipeline/clean.py::reset_comments_table()` drops and recreates the
`comments` table (`DROP TABLE IF EXISTS` + re-run `SCHEMA`) at the start
of every run -- there is no upsert/merge logic anywhere in the pipeline.
Re-running the five pipeline stages is always safe and always produces a
consistent, fully-labeled table; there's no notion of "append new
comments to an existing dataset."

### 3.4 Why gold labels live in a CSV, not the DB

`pipeline/label_gold.py` writes human-labeled gold-sample rows to
`data/gold_labels.csv`, deliberately outside the `comments` table:
`clean.py` drops that table on every run, which would silently destroy
hours of hand-labeling if it lived there. `evaluate.py` re-joins the CSV
against the DB by `id` on every run, so it always reflects the current
labels regardless of how many times the pipeline has been rebuilt since.

## 4. Pipeline stages (implementation notes)

| # | Script | Reads | Writes |
|---|---|---|---|
| 1 | `generate_sample_data.py` (default) / `collect.py` (opt-in, real) | -- | `data/raw_comments.csv` |
| 2 | `clean.py` | `data/raw_comments.csv` | `comments` (full rebuild) |
| 3 | `classify.py` | `comments` (kept rows) | `comments.{vader_*, transformer_*, final_label, label_source}`, `data/classification_report.json` |
| 4 | `analyze.py` | `comments` (kept, classified rows) | `comments.themes`, `term_frequency` (full rebuild) |
| 5 | `event_analysis.py` | `comments` | `data/event_analysis.json` (also computed live by `GET /api/event-analysis`) |

Notes specific to the Postgres implementation:

- **`clean.py` → `comments` insert** uses `conn.executemany()` with
  `%(name)s`-style named placeholders against a list of per-row dicts --
  psycopg2's parameter-substitution style, analogous to sqlite3's
  `:name` style this code originally used.
- **`classify.py`'s transformer pass** runs in a spawned subprocess with a
  hard wall-clock timeout (`multiprocessing`, not `signal.alarm`, since a
  hung DNS resolution inside `huggingface_hub` can block in a way plain
  signal handlers don't reliably interrupt). Unrelated to the DB layer,
  but relevant to why a run can take 40-60s even when it ultimately falls
  back to VADER-only.
- **`analyze.py`** rebuilds `term_frequency` with a `DELETE FROM
  term_frequency` followed by a bulk `executemany INSERT` -- not
  transactionally atomic against concurrent readers (a reader could see
  an empty table mid-run), acceptable given this is a single-operator
  batch pipeline, not a live multi-writer system.
- **`event_analysis.py`** computes a chi-square (or Fisher's exact, for
  small expected cell counts) test per event on ±30-day pre/post windows,
  reading counts via two `SUM(CASE WHEN ...)` aggregate queries
  (`window_counts()`). The same function (`analyze_event()`) backs both
  the CLI script's JSON file output and `GET /api/event-analysis`, so the
  API can't drift from a stale file.

## 5. Database layer (`pipeline/db.py`)

### 5.1 Connection wrapper

`psycopg2` connections don't offer sqlite3's `conn.execute(sql, params)`
convenience (cursor-creation + execute + return-the-cursor in one call).
Since every pipeline script and `app/main.py` were written against that
shape, `pipeline/db.py` defines a thin subclass:

```python
class Connection(psycopg2.extensions.connection):
    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql, seq_of_params):
        cur = self.cursor()
        cur.executemany(sql, seq_of_params)
        return cur
```

`get_connection()` passes this as `connection_factory` and
`psycopg2.extras.RealDictCursor` as `cursor_factory`, so every cursor
returns dict-like rows (`row["column"]`), matching the previous
`sqlite3.Row` access pattern used throughout the codebase.

### 5.2 Configuration

`DATABASE_URL` (env var, read at call time from the module global so
tests can monkeypatch it -- see §7) drives the connection. Default:
`postgresql://postgres:postgres@localhost:5432/cg_foods_sentiment`,
matching `docker-compose.yml`'s bundled `db` service.

### 5.3 SQL differences from the old SQLite version

| SQLite | PostgreSQL | Where |
|---|---|---|
| `?` positional placeholder | `%s` | `app/main.py`'s `Filters.where_clause()`, `event_analysis.py`, `evaluate.py`'s dynamic `IN (...)` |
| `:name` named placeholder | `%(name)s` | `clean.py`, `classify.py`, `analyze.py`, `tests/conftest.py::seed_comments` |
| `strftime('%Y-%m', timestamp)` | `LEFT(timestamp, 7)` | Every monthly-grouping query (`/api/timeline`, `/api/competitors`, `report.py`) -- works because `timestamp` is stored as an ISO 8601 string, so its first 7 characters already are `YYYY-MM` |
| `MAX(engagement, 0)` (SQLite's 2-arg scalar max) | `GREATEST(engagement, 0)` | `compute_engagement_weighted()`'s and `/api/timeline`'s `LN(1 + ...)` weighting queries -- Postgres's `MAX` is aggregate-only, no scalar 2-arg form |
| `SELECT name FROM sqlite_master WHERE type='table' AND name=?` | `SELECT to_regclass(%s)` | `app/main.py::table_exists()` |
| (n/a -- SQLite is dynamically typed) | `::double precision` cast on the `GREATEST(...)` argument before `LN(...)` | See §5.4 below -- avoids `Decimal` results |

### 5.4 The `Decimal` trap

Postgres resolves an ambiguous numeric function call (like `LN(integer)`)
to its `numeric` overload by default (since `numeric` is the "preferred"
type in that type category), returning a `numeric` value -- which
`psycopg2` maps to Python's `Decimal`, not `float`. Comparing a
`Decimal` computed this way against a plain Python `float` (e.g. in
tests asserting against `math.log(...)`) can fail even for
"equal-looking" values, because `Decimal`-vs-`float` equality compares
against the float's exact binary representation, not its printed value.

Fix: explicitly cast the argument to `double precision` before calling
`LN`, e.g. `LN(1 + GREATEST(engagement, 0)::double precision)`, which
makes Postgres pick the `double precision` overload and return a native
float. Applied everywhere the codebase computes `ln(1 + engagement)`
(`app/main.py::compute_engagement_weighted()` and the `/api/timeline`
weighted-points query). Verified with a live Postgres 16 instance that
`SUM(LN(1 + GREATEST(engagement, 0)::double precision))` round-trips as
Python `float`, matching `math.log(...)` to full double precision.

### 5.5 What did *not* need to change

- The `comments`/`term_frequency` schema DDL itself (`TEXT`, `INTEGER`,
  `REAL`, `PRIMARY KEY`, `CREATE TABLE/INDEX IF NOT EXISTS`) is valid
  Postgres syntax unchanged.
- `DROP TABLE IF EXISTS` behaves the same way for `reset_comments_table()`.
- Named-placeholder `executemany()` calls with dicts carrying extra keys
  the query doesn't reference (e.g. `classify.py`'s row dicts, which
  carry `text_clean`/`gold_sentiment` alongside the columns the `UPDATE`
  actually sets) work unchanged -- psycopg2, like sqlite3, only pulls the
  keys the SQL text names.

## 6. Backend API (`app/main.py`)

### 6.1 Request flow

Every data endpoint follows the same shape: parse query params into a
`Filters` instance → `filters.where_clause()` builds a parameterized
`WHERE` fragment + params list → `conn.execute(f"... WHERE {where_sql}
...", params)`. `table_exists()` guards every endpoint against a
not-yet-provisioned database (fresh install, pipeline not run yet),
returning zeroed/empty responses instead of erroring.

### 6.2 `Filters` / `where_clause()` semantics

- Omitting a filter param (`platform` not in the query string at all) →
  no filter on that dimension.
- Sending it with an empty value (`?platform=`) → matches *nothing*
  (`1=0`), since that represents "every checkbox unchecked" in the UI,
  the opposite of "no filter."
- `q` (keyword search) escapes `%`/`_`/`\` in the user's input before
  wrapping it in `%...%` for a `LIKE ... ESCAPE '\'` match against both
  `text_clean` and `text_raw` (case-insensitive via `LOWER(...)` on both
  sides) -- prevents a user-typed `%` or `_` from acting as a SQL
  wildcard.

### 6.3 Engagement-weighted sentiment

`compute_engagement_weighted()` (used by `/api/summary` and
`/api/timeline`) weights each comment by `ln(1 + max(engagement, 0))`
rather than a linear sum, so a handful of viral outliers can't silently
dominate the split. `max_single_comment_weight_share_pct` surfaces
whatever concentration remains after the log transform, so a caller can
judge how much to trust the weighted number.

### 6.4 Endpoint reference

See `README.md`'s API section for the full endpoint list and query
params; not duplicated here since it's user-facing/consumer-facing
documentation, not implementation detail.

## 7. Testing (`tests/`)

### 7.1 Strategy

Tests exercise the pipeline logic (`clean.py`'s dedup/spam heuristics,
`event_analysis.py`'s statistical test selection, `app/main.py`'s
engagement-weighting math) as direct unit tests, plus the full API filter
surface via FastAPI's `TestClient` against a real, scratch Postgres
database -- not mocks, and not SQLite in-memory (as the pre-migration
version used).

### 7.2 Why a real Postgres server is required for tests

SQLite's `:memory:` and per-test temp-file databases gave the old test
suite free, zero-config isolation. Postgres has no equivalent
zero-install ephemeral mode, so tests now require a reachable Postgres
server (`TEST_DATABASE_URL`, defaulting to
`postgresql://postgres:postgres@localhost:5432/cg_foods_sentiment_test`)
-- `docker compose up -d db` is the easiest way to get one. This is an
inherent, accepted cost of moving off SQLite, not an oversight.

### 7.3 Fixtures (`tests/conftest.py`)

- `_ensure_test_database()` connects to the server's `postgres`
  maintenance database (autocommit, since `CREATE DATABASE` can't run
  inside a transaction) and creates the test database if it's missing --
  so contributors only need a reachable Postgres *server*, not a
  pre-provisioned test database.
- `conn` fixture: a fresh connection with the schema dropped and
  recreated (`reset_schema()`) before each test -- mirrors
  `pipeline.db.reset_comments_table()`'s "full rebuild" semantics, giving
  every test a known-empty, known-schema starting point.
- `api_client` fixture: monkeypatches `pipeline.db.DATABASE_URL` to the
  test database URL (read fresh at call time inside
  `get_connection()`, so the monkeypatch takes effect on every request
  the `TestClient` makes -- see §5.2), resets the schema, and exposes the
  seeding connection as `client.db_conn`.
- `seed_comments(conn, rows)`: fills in `ROW_DEFAULTS` for any column a
  test doesn't care about, then a single `executemany` INSERT with
  `%(column)s` placeholders.

### 7.4 Running

```bash
docker compose up -d db     # once, if you don't already have Postgres running
python -m pytest tests/ -v
```

## 8. Deployment (Docker)

### 8.1 `docker-compose.yml`

Two services:
- **`db`** -- `postgres:16-alpine`, a named volume (`pgdata`) for
  persistence across restarts/rebuilds, and a `pg_isready` healthcheck.
- **`app`** -- builds from the repo's `Dockerfile`, `depends_on: db`
  with `condition: service_healthy` (won't start until Postgres accepts
  connections), `DATABASE_URL` wired to the `db` service via Compose's
  variable substitution.

### 8.2 `docker-entrypoint.sh`

1. Waits for Postgres to become reachable (a short Python retry loop
   using `psycopg2.connect(...)`/`OperationalError`) -- belt-and-suspenders
   alongside Compose's healthcheck-gated `depends_on`, in case the
   container is ever run outside Compose.
2. First-run check: previously gated on `data/sentiment.db` not
   existing; now gated on a `data/.pipeline_complete` marker file, since
   the dataset itself lives in the `db` service's Postgres volume, not a
   file under the bind-mounted `./data` directory. On first run, builds
   the bundled synthetic dataset (the same five pipeline commands as the
   README Quickstart) and touches the marker.
3. `exec uvicorn app.main:app --host 0.0.0.0 --port 8000`.

To force a full pipeline rebuild inside a running container: `rm
data/.pipeline_complete && docker compose restart app`.

## 9. Configuration reference (env vars)

| Var | Used by | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | `pipeline/db.py`, `app/main.py` | `postgresql://postgres:postgres@localhost:5432/cg_foods_sentiment` | Main app/pipeline database connection |
| `TEST_DATABASE_URL` | `tests/conftest.py` | `postgresql://postgres:postgres@localhost:5432/cg_foods_sentiment_test` | Scratch database for the pytest suite |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `docker-compose.yml` only | `cg_foods_sentiment` / `postgres` / `postgres` | Configures the bundled `db` service; irrelevant if pointing `DATABASE_URL` at a Postgres instance you manage yourself |
| `YOUTUBE_API_KEY`, `YOUTUBE_RESOLVE_COMMENTER_REGION`, `TWITTER_TRY_SNSCRAPE`, `TWITTER_MANUAL_EXPORT_CSV`, `FACEBOOK_MANUAL_EXPORT_CSV` | `pipeline/collect.py` | unset/`false` | Real data collection (opt-in) -- see `DATA_SOURCES.md` |

## 10. Verification performed for the SQLite → PostgreSQL migration

Run against a live `postgres:16-alpine` container (not just read-through):

- `pipeline/db.py::get_connection()`/`init_db()`/`reset_comments_table()`
  against real Postgres.
- All five pipeline stages end-to-end (`generate_sample_data.py` →
  `clean.py` → `classify.py` [VADER-fallback path] → `analyze.py` →
  `event_analysis.py`) on the bundled 320-row synthetic dataset.
- `label_gold.py --sample-only` and `evaluate.py`.
- Every `GET /api/*` endpoint (`meta`, `summary`, `timeline`,
  `by-platform`, `by-region`, `region-comparison`, `themes`,
  `competitors`, `wordcloud`, `comments`, `event-analysis`, `report`)
  via FastAPI's `TestClient` against the real, pipeline-populated
  database.
- Full `pytest` suite (60 tests) against a scratch
  `cg_foods_sentiment_test` database.
- Confirmed `SUM(LN(1 + GREATEST(engagement, 0)::double precision))`
  returns a native Python `float` (not `Decimal`) from psycopg2.

Not exercised in this pass: the HuggingFace transformer path in
`classify.py` (no network access to download model weights in the
verification environment -- falls back to VADER-only, which is itself a
tested/expected code path, not a workaround) and a full `docker compose
up` build (validated the compose/entrypoint logic by inspection and by
running the equivalent commands directly against a containerized
Postgres instead of building the app image, which would additionally
require downloading `torch`/`transformers`).

## 11. Known limitations

See `METHODOLOGY.md` §5 and `README.md`'s "Known limitations" section for
data/methodology caveats (synthetic dataset, `langdetect` weaknesses,
correlation-not-causation, `region` heuristic accuracy). Implementation-
level limitations specific to this system:

- No connection pooling -- every request/pipeline call opens a fresh
  `psycopg2` connection and closes it. Fine at this project's scale
  (single-operator batch pipeline + low-traffic internal dashboard); a
  higher-traffic deployment should introduce a pool (e.g. `psycopg2.pool`
  or moving to `asyncpg`/`SQLAlchemy`).
- No schema migration tooling (Alembic, etc.) -- `pipeline/db.py`'s
  `CREATE TABLE IF NOT EXISTS` plus `clean.py`'s full-table-rebuild model
  means schema changes are made by editing `SCHEMA` and re-running the
  pipeline from scratch, not by writing incremental migrations. Acceptable
  given the pipeline already fully rebuilds `comments` on every run;
  would need revisiting for a deployment with data that isn't
  regenerable from `raw_comments.csv`.
- `term_frequency`'s rebuild (`DELETE` then bulk `INSERT`) is not wrapped
  in an explicit transaction boundary beyond psycopg2's default
  (autocommit is off by default, and `analyze.py` calls `conn.commit()`
  once at the end) -- a concurrent reader mid-run could see a transiently
  empty table between the `DELETE` and the `INSERT`s landing, since both
  happen before the single `commit()`. Not a practical issue for a
  single-operator batch pipeline with no concurrent writers.
