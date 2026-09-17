"""
FastAPI app for the Wai Wai / CG Foods Sentiment Tracker.

Serves the dashboard (Jinja2 template + static assets) and a small JSON API
over the PostgreSQL database built by the pipeline (generate_sample_data.py /
collect.py -> clean.py -> classify.py -> analyze.py).

Run with: uvicorn app.main:app --reload
"""

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import pipeline.db as db
from pipeline.analyze import COMPETITOR_DISPLAY_NAMES, COMPETITOR_TAGS
from pipeline.event_analysis import SIGNIFICANCE_ALPHA, WINDOW_DAYS, analyze_event
from pipeline.events import KNOWN_EVENTS, WINDOW_START, WINDOW_END
from pipeline.report import gather_report_data, generate_report_pdf

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="Wai Wai / CG Foods Sentiment Tracker")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

VALID_PLATFORMS = ["youtube", "twitter", "facebook"]
VALID_SENTIMENTS = ["positive", "negative", "neutral"]
VALID_REGIONS = ["nepal", "india", "unknown"]


def empty_counts() -> dict:
    return dict.fromkeys(VALID_SENTIMENTS, 0)


def pct_split(counts: dict, total: float = None) -> dict:
    """counts -> {label: percent}, each rounded to 1dp. total defaults to
    sum(counts.values()) but can be passed explicitly (e.g. a separately
    computed weighted total)."""
    total = sum(counts.values()) if total is None else total
    return {k: round((v / total) * 100, 1) if total else 0.0 for k, v in counts.items()}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db() -> db.Connection:
    return db.get_connection()


def table_exists(conn: db.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT to_regclass(%s) as name", (name,)
    ).fetchone()
    return row["name"] is not None


class Filters:
    """Parses the shared query-param filter set and builds a parameterized
    SQL WHERE clause fragment, reused by every data endpoint so the sidebar
    filters (platform, sentiment, keyword search) and top-bar date range
    behave consistently across the whole dashboard, not just the table."""

    def __init__(self, platform, sentiment, start_date, end_date, q, region=None):
        # `platform`/`sentiment`/`region` being None means "param not sent
        # -> no filter" (all values). Being present-but-empty (e.g. "")
        # means the caller explicitly selected zero values (every checkbox
        # unchecked) -> the query should match nothing, not everything.
        self._platform_present = platform is not None
        self._sentiment_present = sentiment is not None
        self._region_present = region is not None
        self.platforms = [p for p in (platform.split(",") if platform else []) if p in VALID_PLATFORMS]
        self.sentiments = [s for s in (sentiment.split(",") if sentiment else []) if s in VALID_SENTIMENTS]
        self.regions = [r for r in (region.split(",") if region else []) if r in VALID_REGIONS]
        self.start_date = start_date or None
        self.end_date = end_date or None
        self.q = q.strip() if q else None

    def where_clause(self):
        clauses = ["exclusion_reason IS NULL"]
        params = []

        if self._platform_present:
            if self.platforms:
                placeholders = ",".join("%s" for _ in self.platforms)
                clauses.append(f"platform IN ({placeholders})")
                params.extend(self.platforms)
            else:
                clauses.append("1=0")

        if self._sentiment_present:
            if self.sentiments:
                placeholders = ",".join("%s" for _ in self.sentiments)
                clauses.append(f"final_label IN ({placeholders})")
                params.extend(self.sentiments)
            else:
                clauses.append("1=0")

        if self._region_present:
            if self.regions:
                placeholders = ",".join("%s" for _ in self.regions)
                clauses.append(f"region IN ({placeholders})")
                params.extend(self.regions)
            else:
                clauses.append("1=0")

        if self.start_date:
            clauses.append("timestamp >= %s")
            params.append(self.start_date)

        if self.end_date:
            clauses.append("timestamp <= %s")
            params.append(f"{self.end_date}T23:59:59")

        if self.q:
            escaped = self.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(LOWER(text_clean) LIKE %s ESCAPE '\\' OR LOWER(text_raw) LIKE %s ESCAPE '\\')")
            like_term = f"%{escaped.lower()}%"
            params.extend([like_term, like_term])

        return " AND ".join(clauses), params


def compute_engagement_weighted(conn, where_sql: str, params: list) -> dict:
    """Engagement-weighted sentiment split, alongside the plain unweighted
    counts every endpoint already returns. Weight = ln(1 + engagement)
    rather than a naive linear sum of engagement: a raw linear sum lets a
    handful of viral outliers (a comment with 5,000 likes next to a sea of
    comments with 0-10) completely dominate the number invisibly. The log
    transform compresses that range while still giving more-engaged
    comments more say. max_single_comment_weight_share_pct makes whatever
    concentration remains visible rather than hiding it -- if one comment
    is still, say, 15% of the total weight, that's worth knowing before
    trusting the weighted split."""
    counts = empty_counts()
    rows = conn.execute(
        f"SELECT final_label, SUM(LN(1 + GREATEST(engagement, 0)::double precision)) as weight "
        f"FROM comments WHERE {where_sql} GROUP BY final_label",
        params,
    ).fetchall()
    for r in rows:
        if r["final_label"] in counts:
            counts[r["final_label"]] = r["weight"] or 0.0
    total_weight = sum(counts.values())
    percentages = pct_split(counts, total_weight)

    max_row = conn.execute(
        f"SELECT MAX(LN(1 + GREATEST(engagement, 0)::double precision)) as max_weight FROM comments WHERE {where_sql}",
        params,
    ).fetchone()
    max_weight = (max_row["max_weight"] or 0.0) if max_row else 0.0
    max_share_pct = round((max_weight / total_weight) * 100, 1) if total_weight else 0.0

    return {
        "weight_function": "ln(1 + engagement)",
        "percentages": percentages,
        "total_weight": round(total_weight, 2),
        "max_single_comment_weight_share_pct": max_share_pct,
        "note": (
            "Weighted by ln(1+engagement) per comment, not a linear sum, so "
            "viral outliers can't silently dominate. "
            "max_single_comment_weight_share_pct is what the single "
            "highest-engagement comment in this filtered set contributes to "
            "the total weight -- treat the weighted split with caution if "
            "this is large relative to the number of comments."
        ),
    }


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
            "regions": VALID_REGIONS,
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
            "regions": VALID_REGIONS,
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
        "regions": VALID_REGIONS,
        "min_date": (row["min_ts"] or WINDOW_START)[:10],
        "max_date": (row["max_ts"] or WINDOW_END)[:10],
        "total_comments": row["total"] or 0,
        "pipeline_ready": True,
    }


# ---------------------------------------------------------------------------
# API: overall sentiment split (KPI row)
# ---------------------------------------------------------------------------

@app.get("/api/summary")
def api_summary(platform: str = Query(None), sentiment: str = Query(None), region: str = Query(None),
                 start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None)):
    filters = Filters(platform, sentiment, start_date, end_date, q, region)
    conn = get_db()
    if not table_exists(conn, "comments"):
        conn.close()
        return {"total_comments": 0, "counts": empty_counts(),
                "percentages": empty_counts(), "most_discussed_theme": None,
                "engagement_weighted": None}

    where_sql, params = filters.where_clause()
    rows = conn.execute(
        f"SELECT final_label, COUNT(*) as cnt FROM comments WHERE {where_sql} GROUP BY final_label",
        params,
    ).fetchall()

    counts = empty_counts()
    for r in rows:
        if r["final_label"] in counts:
            counts[r["final_label"]] = r["cnt"]
    total = sum(counts.values())
    percentages = pct_split(counts, total)

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

    engagement_weighted = compute_engagement_weighted(conn, where_sql, params)

    conn.close()
    return {
        "total_comments": total,
        "counts": counts,
        "percentages": percentages,
        "most_discussed_theme": most_discussed,
        "engagement_weighted": engagement_weighted,
    }


# ---------------------------------------------------------------------------
# API: sentiment over time (+ event markers)
# ---------------------------------------------------------------------------

@app.get("/api/timeline")
def api_timeline(platform: str = Query(None), sentiment: str = Query(None), region: str = Query(None),
                  start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None)):
    filters = Filters(platform, sentiment, start_date, end_date, q, region)
    conn = get_db()

    periods = month_range(
        filters.start_date or WINDOW_START,
        filters.end_date or WINDOW_END,
    )
    by_period = {p: empty_counts() for p in periods}
    by_period_weight = {p: empty_counts() for p in periods}

    if table_exists(conn, "comments"):
        where_sql, params = filters.where_clause()
        rows = conn.execute(
            f"""
            SELECT LEFT(timestamp, 7) as period, final_label, COUNT(*) as cnt
            FROM comments WHERE {where_sql}
            GROUP BY period, final_label
            """,
            params,
        ).fetchall()
        for r in rows:
            if r["period"] in by_period and r["final_label"] in by_period[r["period"]]:
                by_period[r["period"]][r["final_label"]] = r["cnt"]

        # Engagement-weighted (ln(1+engagement), see compute_engagement_weighted's
        # docstring for why not a naive linear sum) alongside the plain counts.
        weight_rows = conn.execute(
            f"""
            SELECT LEFT(timestamp, 7) as period, final_label,
                   SUM(LN(1 + GREATEST(engagement, 0)::double precision)) as weight
            FROM comments WHERE {where_sql}
            GROUP BY period, final_label
            """,
            params,
        ).fetchall()
        for r in weight_rows:
            if r["period"] in by_period_weight and r["final_label"] in by_period_weight[r["period"]]:
                by_period_weight[r["period"]][r["final_label"]] = r["weight"] or 0.0
    conn.close()

    points = []
    for p in periods:
        counts = by_period[p]
        total = sum(counts.values())
        weights = by_period_weight[p]
        total_weight = sum(weights.values())
        points.append({
            "period": p,
            "positive": counts["positive"],
            "negative": counts["negative"],
            "neutral": counts["neutral"],
            "total": total,
            "positive_weight": round(weights["positive"], 2),
            "negative_weight": round(weights["negative"], 2),
            "neutral_weight": round(weights["neutral"], 2),
            "total_weight": round(total_weight, 2),
        })

    return {
        "granularity": "month",
        "points": points,
        "events": KNOWN_EVENTS,
        "weight_function": "ln(1 + engagement)",
    }


# ---------------------------------------------------------------------------
# API: known events (standalone)
# ---------------------------------------------------------------------------

@app.get("/api/events")
def api_events():
    return {"events": KNOWN_EVENTS}


# ---------------------------------------------------------------------------
# API: statistical significance of event-driven sentiment shifts
# ---------------------------------------------------------------------------

@app.get("/api/event-analysis")
def api_event_analysis():
    """Live chi-square/Fisher's-exact test per event (pipeline/event_analysis.py's
    logic, computed fresh rather than read from its output file so this
    can't go stale relative to the current comments table). Unfiltered by
    design -- it's answering a fixed statistical question about the whole
    dataset, like /api/region-comparison."""
    conn = get_db()
    if not table_exists(conn, "comments"):
        conn.close()
        return {"window_days": WINDOW_DAYS, "alpha": SIGNIFICANCE_ALPHA, "events": []}

    results = [analyze_event(conn, event) for event in KNOWN_EVENTS]
    conn.close()
    return {"window_days": WINDOW_DAYS, "alpha": SIGNIFICANCE_ALPHA, "events": results}


# ---------------------------------------------------------------------------
# API: sentiment by platform
# ---------------------------------------------------------------------------

@app.get("/api/by-platform")
def api_by_platform(platform: str = Query(None), sentiment: str = Query(None), region: str = Query(None),
                     start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None)):
    filters = Filters(platform, sentiment, start_date, end_date, q, region)
    conn = get_db()

    result = {p: empty_counts() for p in VALID_PLATFORMS}
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
        percentages = pct_split(counts, total)
        platforms_out.append({
            "platform": p,
            "total": total,
            "counts": counts,
            "percentages": percentages,
        })

    return {"platforms": platforms_out}


# ---------------------------------------------------------------------------
# API: sentiment by region (nepal / india / unknown)
# ---------------------------------------------------------------------------

@app.get("/api/by-region")
def api_by_region(platform: str = Query(None), sentiment: str = Query(None), region: str = Query(None),
                   start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None)):
    filters = Filters(platform, sentiment, start_date, end_date, q, region)
    conn = get_db()

    result = {r: empty_counts() for r in VALID_REGIONS}
    if table_exists(conn, "comments"):
        where_sql, params = filters.where_clause()
        rows = conn.execute(
            f"""
            SELECT region, final_label, COUNT(*) as cnt
            FROM comments WHERE {where_sql}
            GROUP BY region, final_label
            """,
            params,
        ).fetchall()
        for r in rows:
            key = r["region"] if r["region"] in result else "unknown"
            if r["final_label"] in result[key]:
                result[key][r["final_label"]] += r["cnt"]
    conn.close()

    regions_out = []
    for reg in VALID_REGIONS:
        counts = result[reg]
        total = sum(counts.values())
        percentages = pct_split(counts, total)
        regions_out.append({
            "region": reg,
            "total": total,
            "counts": counts,
            "percentages": percentages,
        })

    return {
        "regions": regions_out,
        "note": (
            "region is a best-effort signal (see DATA_SOURCES.md's Region "
            "Heuristic section) -- on real collected data expect 'unknown' "
            "to dominate; only the sample dataset has region populated on "
            "every row."
        ),
    }


# ---------------------------------------------------------------------------
# API: Nepal vs. India sentiment around specific events (CONTEXT.md's
# central open question: does Nepal-sourced sentiment differ from
# India-sourced sentiment, particularly around the Nepal price hike/quality
# fine vs. the India expansion push?)
# ---------------------------------------------------------------------------

REGION_COMPARISON_WINDOW_DAYS = 30


@app.get("/api/region-comparison")
def api_region_comparison():
    conn = get_db()
    if not table_exists(conn, "comments"):
        conn.close()
        return {"window_days": REGION_COMPARISON_WINDOW_DAYS, "events": []}

    events_out = []
    for event in KNOWN_EVENTS:
        event_date = date.fromisoformat(event["date"])
        window_start = (event_date - timedelta(days=REGION_COMPARISON_WINDOW_DAYS)).isoformat()
        window_end = (event_date + timedelta(days=REGION_COMPARISON_WINDOW_DAYS)).isoformat()

        regions = {}
        for reg in ("nepal", "india"):
            rows = conn.execute(
                """
                SELECT final_label, COUNT(*) as cnt FROM comments
                WHERE exclusion_reason IS NULL AND region = %s
                  AND timestamp >= %s AND timestamp <= %s
                GROUP BY final_label
                """,
                (reg, window_start, f"{window_end}T23:59:59"),
            ).fetchall()
            counts = empty_counts()
            for r in rows:
                if r["final_label"] in counts:
                    counts[r["final_label"]] = r["cnt"]
            total = sum(counts.values())
            regions[reg] = {
                "total": total,
                "counts": counts,
                "negative_pct": round((counts["negative"] / total) * 100, 1) if total else None,
            }

        events_out.append({
            "event_id": event["id"],
            "date": event["date"],
            "label": event["label"],
            "category": event["category"],
            "window_days": REGION_COMPARISON_WINDOW_DAYS,
            "regions": regions,
        })

    conn.close()
    return {"window_days": REGION_COMPARISON_WINDOW_DAYS, "events": events_out}


# ---------------------------------------------------------------------------
# API: theme frequency by sentiment
# ---------------------------------------------------------------------------

@app.get("/api/themes")
def api_themes(platform: str = Query(None), sentiment: str = Query(None), region: str = Query(None),
                start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None)):
    filters = Filters(platform, sentiment, start_date, end_date, q, region)
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
                bucket = theme_counts.setdefault(t, empty_counts())
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
# API: per-competitor share of voice (Current Noodles / 2PM Noodles / Maggi
# / Sunfeast Yippee!, per CONTEXT.md's named rivals) -- overall split plus
# a monthly trend, mirroring /api/timeline's shape.
# ---------------------------------------------------------------------------

@app.get("/api/competitors")
def api_competitors(platform: str = Query(None), sentiment: str = Query(None), region: str = Query(None),
                     start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None)):
    filters = Filters(platform, sentiment, start_date, end_date, q, region)
    conn = get_db()

    totals = {c: empty_counts() for c in COMPETITOR_TAGS}
    periods = month_range(filters.start_date or WINDOW_START, filters.end_date or WINDOW_END)
    monthly = {c: {p: empty_counts() for p in periods} for c in COMPETITOR_TAGS}

    if table_exists(conn, "comments"):
        where_sql, params = filters.where_clause()
        rows = conn.execute(
            f"""
            SELECT LEFT(timestamp, 7) as period, final_label, themes
            FROM comments WHERE {where_sql} AND themes IS NOT NULL
            """,
            params,
        ).fetchall()
        for r in rows:
            try:
                themes = json.loads(r["themes"]) if r["themes"] else []
            except (json.JSONDecodeError, TypeError):
                themes = []
            label = r["final_label"]
            period = r["period"]
            for competitor in themes:
                if competitor not in totals:
                    continue
                if label in totals[competitor]:
                    totals[competitor][label] += 1
                if period in monthly[competitor] and label in monthly[competitor][period]:
                    monthly[competitor][period][label] += 1
    conn.close()

    competitors_out = []
    for c in COMPETITOR_TAGS:
        counts = totals[c]
        total = sum(counts.values())
        competitors_out.append({
            "competitor": c,
            "display_name": COMPETITOR_DISPLAY_NAMES[c],
            "total": total,
            "counts": counts,
            "monthly": [
                {"period": p, **monthly[c][p], "total": sum(monthly[c][p].values())}
                for p in periods
            ],
        })
    competitors_out.sort(key=lambda c: c["total"], reverse=True)

    return {"competitors": competitors_out}


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
def api_comments(platform: str = Query(None), sentiment: str = Query(None), region: str = Query(None),
                  start_date: str = Query(None), end_date: str = Query(None), q: str = Query(None),
                  page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    filters = Filters(platform, sentiment, start_date, end_date, q, region)
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
        SELECT id, platform, region, text_raw, text_clean, timestamp, source_ref,
               engagement, language_guess, final_label, label_source,
               vader_compound, transformer_label, themes
        FROM comments
        WHERE {where_sql}
        ORDER BY timestamp DESC
        LIMIT %s OFFSET %s
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
            "region": r["region"],
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


# ---------------------------------------------------------------------------
# API: PDF report export (PROJECT_INSTRUCTIONS.md section 5 "Reporting")
# ---------------------------------------------------------------------------

@app.get("/api/report")
def api_report():
    """Always reports on the full, unfiltered dataset -- a report is a
    fixed snapshot, not a filtered view (same reasoning as
    /api/region-comparison and /api/event-analysis)."""
    conn = get_db()
    if not table_exists(conn, "comments"):
        conn.close()
        raise HTTPException(status_code=409, detail="No data yet -- run the pipeline first.")

    data = gather_report_data(conn)
    conn.close()

    if not data["total"]:
        raise HTTPException(status_code=409, detail="No classified comments yet -- run the pipeline first.")

    pdf_bytes = generate_report_pdf(data)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=waiwai_sentiment_report.pdf"},
    )
