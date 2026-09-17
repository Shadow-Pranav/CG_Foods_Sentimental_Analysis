"""
Statistical significance of event-driven sentiment shifts —
pipeline/event_analysis.py

The dashboard timeline shows dips in sentiment around known events, but a
visual dip could just be noise in a small dataset. For each event in
pipeline/events.py, this compares the negative-sentiment share in a
+/-30-day pre/post window using a chi-square test of independence
(falling back to Fisher's exact test when any expected cell count is below
5, the standard rule of thumb for chi-square validity on small samples).

Run after clean.py -> classify.py -> analyze.py (needs final_label):
    python pipeline/event_analysis.py

Writes data/event_analysis.json; also served live via GET /api/event-analysis
so the dashboard doesn't depend on a stale file if the underlying data
changes without a re-run being remembered.

Caveat this doesn't control for: events spaced less than 60 days apart
would have overlapping pre/post windows (none currently do -- see
pipeline/events.py -- but if events are added later without checking
spacing, that overlap would contaminate both events' results).
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection  # noqa: E402
from events import KNOWN_EVENTS  # noqa: E402

from scipy.stats import chi2_contingency, fisher_exact  # noqa: E402

WINDOW_DAYS = 30
SIGNIFICANCE_ALPHA = 0.05
REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "event_analysis.json"


def window_counts(conn, start_iso: str, end_iso: str) -> tuple:
    """Returns (negative_count, non_negative_count) for kept, classified
    comments with timestamp in [start_iso, end_iso T23:59:59]."""
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN final_label = 'negative' THEN 1 ELSE 0 END) as neg,
            SUM(CASE WHEN final_label IS NOT NULL AND final_label != 'negative' THEN 1 ELSE 0 END) as non_neg
        FROM comments
        WHERE exclusion_reason IS NULL AND timestamp >= %s AND timestamp <= %s
        """,
        (start_iso, f"{end_iso}T23:59:59"),
    ).fetchone()
    return (row["neg"] or 0), (row["non_neg"] or 0)


def analyze_event(conn, event: dict) -> dict:
    event_date = date.fromisoformat(event["date"])
    pre_start = (event_date - timedelta(days=WINDOW_DAYS)).isoformat()
    pre_end = (event_date - timedelta(days=1)).isoformat()
    post_start = event_date.isoformat()
    post_end = (event_date + timedelta(days=WINDOW_DAYS)).isoformat()

    pre_neg, pre_non_neg = window_counts(conn, pre_start, pre_end)
    post_neg, post_non_neg = window_counts(conn, post_start, post_end)
    pre_total = pre_neg + pre_non_neg
    post_total = post_neg + post_non_neg

    result = {
        "event_id": event["id"],
        "label": event["label"],
        "date": event["date"],
        "window_days": WINDOW_DAYS,
        "pre": {
            "negative": pre_neg, "non_negative": pre_non_neg, "total": pre_total,
            "negative_pct": round(pre_neg / pre_total * 100, 1) if pre_total else None,
        },
        "post": {
            "negative": post_neg, "non_negative": post_non_neg, "total": post_total,
            "negative_pct": round(post_neg / post_total * 100, 1) if post_total else None,
        },
    }

    if pre_total == 0 or post_total == 0:
        result.update({
            "test": None, "statistic": None, "p_value": None, "significant": None,
            "shift_pct_points": None,
            "note": "Not enough data in the pre- or post-window to run a test.",
        })
        return result

    table = [[pre_neg, pre_non_neg], [post_neg, post_non_neg]]
    chi2_stat, p_chi2, _dof, expected = chi2_contingency(table)
    use_fisher = bool((expected < 5).any())

    if use_fisher:
        odds_ratio, p_value = fisher_exact(table)
        test_name, statistic = "fisher_exact", odds_ratio
    else:
        test_name, statistic = "chi2", chi2_stat
        p_value = p_chi2

    # fisher_exact's odds ratio is +inf when a cell is exactly 0 (e.g. zero
    # negative comments in one window) -- not JSON-serializable as a float.
    statistic = float(statistic)
    json_safe_statistic = round(statistic, 4) if statistic not in (float("inf"), float("-inf")) else None

    result.update({
        "test": test_name,
        "statistic": json_safe_statistic,
        "p_value": round(float(p_value), 5),
        "significant": bool(p_value < SIGNIFICANCE_ALPHA),
        "alpha": SIGNIFICANCE_ALPHA,
        "shift_pct_points": round(result["post"]["negative_pct"] - result["pre"]["negative_pct"], 1),
    })
    return result


def main():
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) as c FROM comments WHERE exclusion_reason IS NULL AND final_label IS NOT NULL"
    ).fetchone()
    if not row or not row["c"]:
        conn.close()
        raise RuntimeError("No classified rows found. Run clean.py -> classify.py -> analyze.py first.")

    results = [analyze_event(conn, event) for event in KNOWN_EVENTS]
    conn.close()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump({"window_days": WINDOW_DAYS, "alpha": SIGNIFICANCE_ALPHA, "events": results}, f, indent=2)

    print(f"Event significance analysis ({WINDOW_DAYS}-day pre/post windows, alpha={SIGNIFICANCE_ALPHA}):")
    for r in results:
        if r["significant"] is None:
            status = "insufficient data"
        elif r["significant"]:
            status = "SIGNIFICANT"
        else:
            status = "not significant"
        print(f"  {r['label']:42s} pre={r['pre']['negative_pct']}%  post={r['post']['negative_pct']}%  "
              f"p={r['p_value']}  [{status}]")

    print(f"\nWrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
