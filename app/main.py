"""
FastAPI app for the Wai Wai / CG Foods Sentiment Tracker.

Serves the dashboard (Jinja2 template + static assets) and a small JSON API
over the SQLite database built by the pipeline (generate_sample_data.py /
collect.py -> clean.py -> classify.py -> analyze.py).

Run with: uvicorn app.main:app --reload
"""

import json
import sqlite3
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pipeline.db import DB_PATH
from pipeline.events import KNOWN_EVENTS, WINDOW_START, WINDOW_END

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="Wai Wai / CG Foods Sentiment Tracker")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

VALID_PLATFORMS = ["youtube", "twitter", "facebook"]
VALID_SENTIMENTS = ["positive", "negative", "neutral"]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


class Filters:
    """Parses the shared query-param filter set and builds a parameterized
    SQL WHERE clause fragment, reused by every data endpoint so the sidebar
    filters (platform, sentiment, keyword search) and top-bar date range
    behave consistently across the whole dashboard, not just the table."""

    def __init__(self, platform, sentiment, start_date, end_date, q):
        # `platform`/`sentiment` being None means "param not sent -> no
        # filter" (all values). Being present-but-empty (e.g. "") means the
        # caller explicitly selected zero values (every checkbox unchecked)
        # -> the query should match nothing, not everything.
        self._platform_present = platform is not None
        self._sentiment_present = sentiment is not None
        self.platforms = [p for p in (platform.split(",") if platform else []) if p in VALID_PLATFORMS]
        self.sentiments = [s for s in (sentiment.split(",") if sentiment else []) if s in VALID_SENTIMENTS]
        self.start_date = start_date or None
        self.end_date = end_date or None
        self.q = q.strip() if q else None

    def where_clause(self):
        clauses = ["exclusion_reason IS NULL"]
        params = []

        if self._platform_present:
            if self.platforms:
                placeholders = ",".join("?" for _ in self.platforms)
                clauses.append(f"platform IN ({placeholders})")
                params.extend(self.platforms)
            else:
                clauses.append("1=0")

        if self._sentiment_present:
            if self.sentiments:
                placeholders = ",".join("?" for _ in self.sentiments)
                clauses.append(f"final_label IN ({placeholders})")
                params.extend(self.sentiments)
            else:
                clauses.append("1=0")

        if self.start_date:
            clauses.append("timestamp >= ?")
            params.append(self.start_date)

        if self.end_date:
            clauses.append("timestamp <= ?")
            params.append(f"{self.end_date}T23:59:59")

        if self.q:
            escaped = self.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(LOWER(text_clean) LIKE ? ESCAPE '\\' OR LOWER(text_raw) LIKE ? ESCAPE '\\')")
            like_term = f"%{escaped.lower()}%"
            params.extend([like_term, like_term])

        return " AND ".join(clauses), params


def month_range(start: str, end: str) -> list:
    """Inclusive list of 'YYYY-MM' periods between two ISO date strings."""
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    periods = []
    y, m = d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        periods.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return periods


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@app.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "platforms": VALID_PLATFORMS,
            "sentiments": VALID_SENTIMENTS,
            "window_start": WINDOW_START,
            "window_end": WINDOW_END,
        },
    )


# ---------------------------------------------------------------------------
# API: meta
# ---------------------------------------------------------------------------

@app.get("/api/meta")
def api_meta():
    conn = get_db()
    if not table_exists(conn, "comments"):
        conn.close()
        return {
            "platforms": VALID_PLATFORMS,
            "sentiments": VALID_SENTIMENTS,
            "min_date": WINDOW_START,
            "max_date": WINDOW_END,
            "total_comments": 0,
            "pipeline_ready": False,
        }

    row = conn.execute(
        "SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts, COUNT(*) as total "
        "FROM comments WHERE exclusion_reason IS NULL"
    ).fetchone()
    conn.close()

    return {
        "platforms": VALID_PLATFORMS,
        "sentiments": VALID_SENTIMENTS,
        "min_date": (row["min_ts"] or WINDOW_START)[:10],
        "max_date": (row["max_ts"] or WINDOW_END)[:10],
        "total_comments": row["total"] or 0,
        "pipeline_ready": True,
    }


# ---------------------------------------------------------------------------
# API: overall sentiment split (KPI row)
# ---------------------------------------------------------------------------

@app.get("/api/summary")
def api_summary(platform: str = Query(None), sentiment: str = Query(None),
                 start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None)):
    filters = Filters(platform, sentiment, start_date, end_date, q)
    conn = get_db()
    if not table_exists(conn, "comments"):
        conn.close()
        return {"total_comments": 0, "counts": {"positive": 0, "negative": 0, "neutral": 0},
                "percentages": {"positive": 0, "negative": 0, "neutral": 0}, "most_discussed_theme": None}

    where_sql, params = filters.where_clause()
    rows = conn.execute(
        f"SELECT final_label, COUNT(*) as cnt FROM comments WHERE {where_sql} GROUP BY final_label",
        params,
    ).fetchall()

    counts = {"positive": 0, "negative": 0, "neutral": 0}
    for r in rows:
        if r["final_label"] in counts:
            counts[r["final_label"]] = r["cnt"]
    total = sum(counts.values())
    percentages = {
        k: round((v / total) * 100, 1) if total else 0.0 for k, v in counts.items()
    }

    theme_rows = conn.execute(
        f"SELECT themes FROM comments WHERE {where_sql} AND themes IS NOT NULL",
        params,
    ).fetchall()
    theme_counter = {}
    for r in theme_rows:
        try:
            themes = json.loads(r["themes"]) if r["themes"] else []
        except (json.JSONDecodeError, TypeError):
            themes = []
        for t in themes:
            theme_counter[t] = theme_counter.get(t, 0) + 1

    most_discussed = None
    if theme_counter:
        theme, count = max(theme_counter.items(), key=lambda kv: kv[1])
        most_discussed = {"theme": theme, "count": count}

    conn.close()
    return {
        "total_comments": total,
        "counts": counts,
        "percentages": percentages,
        "most_discussed_theme": most_discussed,
    }


# ---------------------------------------------------------------------------
# API: sentiment over time (+ event markers)
# ---------------------------------------------------------------------------

@app.get("/api/timeline")
def api_timeline(platform: str = Query(None), sentiment: str = Query(None),
                  start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None)):
    filters = Filters(platform, sentiment, start_date, end_date, q)
    conn = get_db()

    periods = month_range(
        filters.start_date or WINDOW_START,
        filters.end_date or WINDOW_END,
    )
    by_period = {p: {"positive": 0, "negative": 0, "neutral": 0} for p in periods}

    if table_exists(conn, "comments"):
        where_sql, params = filters.where_clause()
        rows = conn.execute(
            f"""
            SELECT strftime('%Y-%m', timestamp) as period, final_label, COUNT(*) as cnt
            FROM comments WHERE {where_sql}
            GROUP BY period, final_label
            """,
            params,
        ).fetchall()
        for r in rows:
            if r["period"] in by_period and r["final_label"] in by_period[r["period"]]:
                by_period[r["period"]][r["final_label"]] = r["cnt"]
    conn.close()

    points = []
    for p in periods:
        counts = by_period[p]
        total = sum(counts.values())
        points.append({
            "period": p,
            "positive": counts["positive"],
            "negative": counts["negative"],
            "neutral": counts["neutral"],
            "total": total,
        })

    return {"granularity": "month", "points": points, "events": KNOWN_EVENTS}


# ---------------------------------------------------------------------------
# API: known events (standalone)
# ---------------------------------------------------------------------------

@app.get("/api/events")
def api_events():
    return {"events": KNOWN_EVENTS}


# ---------------------------------------------------------------------------
# API: sentiment by platform
# ---------------------------------------------------------------------------

@app.get("/api/by-platform")
def api_by_platform(platform: str = Query(None), sentiment: str = Query(None),
                     start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None)):
    filters = Filters(platform, sentiment, start_date, end_date, q)
    conn = get_db()

    result = {p: {"positive": 0, "negative": 0, "neutral": 0} for p in VALID_PLATFORMS}
    if table_exists(conn, "comments"):
        where_sql, params = filters.where_clause()
        rows = conn.execute(
            f"""
            SELECT platform, final_label, COUNT(*) as cnt
            FROM comments WHERE {where_sql}
            GROUP BY platform, final_label
            """,
            params,
        ).fetchall()
        for r in rows:
            if r["platform"] in result and r["final_label"] in result[r["platform"]]:
                result[r["platform"]][r["final_label"]] = r["cnt"]
    conn.close()

    platforms_out = []
    for p in VALID_PLATFORMS:
        counts = result[p]
        total = sum(counts.values())
        percentages = {k: round((v / total) * 100, 1) if total else 0.0 for k, v in counts.items()}
        platforms_out.append({
            "platform": p,
            "total": total,
            "counts": counts,
            "percentages": percentages,
        })

    return {"platforms": platforms_out}


# ---------------------------------------------------------------------------
# API: theme frequency by sentiment
# ---------------------------------------------------------------------------

@app.get("/api/themes")
def api_themes(platform: str = Query(None), sentiment: str = Query(None),
                start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None)):
    filters = Filters(platform, sentiment, start_date, end_date, q)
    conn = get_db()

    theme_counts = {}
    if table_exists(conn, "comments"):
        where_sql, params = filters.where_clause()
        rows = conn.execute(
            f"SELECT themes, final_label FROM comments WHERE {where_sql} AND themes IS NOT NULL",
            params,
        ).fetchall()
        for r in rows:
            try:
                themes = json.loads(r["themes"]) if r["themes"] else []
            except (json.JSONDecodeError, TypeError):
                themes = []
            for t in themes:
                bucket = theme_counts.setdefault(t, {"positive": 0, "negative": 0, "neutral": 0})
                label = r["final_label"]
                if label in bucket:
                    bucket[label] += 1
    conn.close()

    themes_out = []
    for theme, counts in theme_counts.items():
        total = sum(counts.values())
        themes_out.append({"theme": theme, "total": total, **counts})
    themes_out.sort(key=lambda t: t["total"], reverse=True)

    return {"themes": themes_out}


# ---------------------------------------------------------------------------
# API: word-cloud term frequency (bonus, not wired into the main UI)
# ---------------------------------------------------------------------------

@app.get("/api/wordcloud")
def api_wordcloud():
    conn = get_db()
    if not table_exists(conn, "term_frequency"):
        conn.close()
        return {"positive": [], "negative": [], "neutral": []}

    rows = conn.execute("SELECT sentiment, term, count FROM term_frequency ORDER BY sentiment, count DESC").fetchall()
    conn.close()

    out = {"positive": [], "negative": [], "neutral": []}
    for r in rows:
        if r["sentiment"] in out:
            out[r["sentiment"]].append({"term": r["term"], "count": r["count"]})
    return out


# ---------------------------------------------------------------------------
# API: paginated comment table
# ---------------------------------------------------------------------------

@app.get("/api/comments")
def api_comments(platform: str = Query(None), sentiment: str = Query(None),
                  start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None),
                  page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    filters = Filters(platform, sentiment, start_date, end_date, q)
    conn = get_db()

    if not table_exists(conn, "comments"):
        conn.close()
        return {"total": 0, "page": page, "page_size": page_size, "total_pages": 0, "comments": []}

    where_sql, params = filters.where_clause()
    total = conn.execute(f"SELECT COUNT(*) as cnt FROM comments WHERE {where_sql}", params).fetchone()["cnt"]
    total_pages = max(1, (total + page_size - 1) // page_size)
    offset = (page - 1) * page_size

    rows = conn.execute(
        f"""
        SELECT id, platform, text_raw, text_clean, timestamp, source_ref,
               engagement, language_guess, final_label, label_source,
               vader_compound, transformer_label, themes
        FROM comments
        WHERE {where_sql}
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    ).fetchall()
    conn.close()

    comments = []
    for r in rows:
        try:
            themes = json.loads(r["themes"]) if r["themes"] else []
        except (json.JSONDecodeError, TypeError):
            themes = []
        comments.append({
            "id": r["id"],
            "platform": r["platform"],
            "text_raw": r["text_raw"],
            "text_clean": r["text_clean"],
            "timestamp": r["timestamp"],
            "source_ref": r["source_ref"],
            "engagement": r["engagement"],
            "language_guess": r["language_guess"],
            "final_label": r["final_label"],
            "label_source": r["label_source"],
            "vader_compound": r["vader_compound"],
            "transformer_label": r["transformer_label"],
            "themes": themes,
        })

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "comments": comments,
    }
