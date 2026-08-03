"""
PDF report export — pipeline/report.py

Implements PROJECT_INSTRUCTIONS.md section 5 ("Reporting"): "Package
findings into a report/dashboard... covering: overall sentiment split,
sentiment trend over time, sentiment by platform, theme/keyword breakdown,
and an events-correlation narrative with recommendations."

Always reports on the full, unfiltered dataset (like GET /api/region-
comparison and GET /api/event-analysis) -- a report is a fixed snapshot,
not a filtered view tied to whatever a dashboard user happened to have
selected.

Usage:
    python pipeline/report.py                  # writes data/report.pdf
Also served live via GET /api/report (same gather_report_data() +
generate_report_pdf(), so the download always reflects the current DB).
"""

import io
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import COMPETITOR_DISPLAY_NAMES, COMPETITOR_TAGS  # noqa: E402
from db import get_connection  # noqa: E402
from event_analysis import analyze_event  # noqa: E402
from events import KNOWN_EVENTS, WINDOW_END, WINDOW_START  # noqa: E402

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

REPORT_PDF_PATH = Path(__file__).resolve().parent.parent / "data" / "report.pdf"

# Palette matching the dashboard (see app/static/css/style.css) so the PDF
# doesn't look like a different tool from the one it's summarizing.
COLOR_TEXT = colors.HexColor("#221E1A")
COLOR_MUTED = colors.HexColor("#6B6255")
COLOR_NEGATIVE = colors.HexColor("#A8122B")
COLOR_POSITIVE = colors.HexColor("#4B6B43")
COLOR_NEUTRAL = colors.HexColor("#91733F")
COLOR_BORDER = colors.HexColor("#DBD2C0")


def _empty_counts() -> dict:
    return {"positive": 0, "negative": 0, "neutral": 0}


def _month_range(start: str, end: str) -> list:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    periods, y, m = [], d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        periods.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return periods


def gather_report_data(conn) -> dict:
    kept_where = "exclusion_reason IS NULL"

    total_row = conn.execute(f"SELECT COUNT(*) as c FROM comments WHERE {kept_where}").fetchone()
    total = total_row["c"] or 0

    label_rows = conn.execute(
        f"SELECT final_label, COUNT(*) as c FROM comments WHERE {kept_where} GROUP BY final_label"
    ).fetchall()
    counts = _empty_counts()
    for r in label_rows:
        if r["final_label"] in counts:
            counts[r["final_label"]] = r["c"]
    percentages = {k: round(v / total * 100, 1) if total else 0.0 for k, v in counts.items()}

    platform_rows = conn.execute(
        f"SELECT platform, final_label, COUNT(*) as c FROM comments WHERE {kept_where} GROUP BY platform, final_label"
    ).fetchall()
    by_platform = {}
    for r in platform_rows:
        by_platform.setdefault(r["platform"], _empty_counts())
        if r["final_label"] in by_platform[r["platform"]]:
            by_platform[r["platform"]][r["final_label"]] = r["c"]

    region_rows = conn.execute(
        f"SELECT region, final_label, COUNT(*) as c FROM comments WHERE {kept_where} GROUP BY region, final_label"
    ).fetchall()
    by_region = {}
    for r in region_rows:
        reg = r["region"] or "unknown"
        by_region.setdefault(reg, _empty_counts())
        if r["final_label"] in by_region[reg]:
            by_region[reg][r["final_label"]] = r["c"]

    periods = _month_range(WINDOW_START, WINDOW_END)
    timeline_rows = conn.execute(
        f"SELECT strftime('%Y-%m', timestamp) as period, final_label, COUNT(*) as c "
        f"FROM comments WHERE {kept_where} GROUP BY period, final_label"
    ).fetchall()
    timeline = {p: _empty_counts() for p in periods}
    for r in timeline_rows:
        if r["period"] in timeline and r["final_label"] in timeline[r["period"]]:
            timeline[r["period"]][r["final_label"]] = r["c"]

    events = [analyze_event(conn, e) for e in KNOWN_EVENTS]

    theme_rows = conn.execute(
        f"SELECT themes, final_label FROM comments WHERE {kept_where} AND themes IS NOT NULL"
    ).fetchall()
    theme_counts = {}
    competitor_counts = {c: _empty_counts() for c in COMPETITOR_TAGS}
    for r in theme_rows:
        try:
            themes = json.loads(r["themes"]) if r["themes"] else []
        except (json.JSONDecodeError, TypeError):
            themes = []
        label = r["final_label"]
        for t in themes:
            if t in COMPETITOR_TAGS:
                if label in competitor_counts[t]:
                    competitor_counts[t][label] += 1
                continue
            bucket = theme_counts.setdefault(t, _empty_counts())
            if label in bucket:
                bucket[label] += 1

    themes_out = sorted(
        ({"theme": t, "total": sum(c.values()), **c} for t, c in theme_counts.items()),
        key=lambda x: x["total"], reverse=True,
    )
    competitors_out = sorted(
        ({"competitor": c, "display_name": COMPETITOR_DISPLAY_NAMES[c], "total": sum(v.values()), **v}
         for c, v in competitor_counts.items()),
        key=lambda x: x["total"], reverse=True,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "counts": counts,
        "percentages": percentages,
        "by_platform": by_platform,
        "by_region": by_region,
        "timeline": [{"period": p, **timeline[p]} for p in periods],
        "events": events,
        "themes": themes_out,
        "competitors": competitors_out,
    }


def _build_narrative(data: dict) -> list:
    """Auto-generated from the actual computed numbers -- not boilerplate.
    Regenerating the report with different data changes this text."""
    paras = []
    total = data["total"]
    pct = data["percentages"]

    paras.append(
        f"Of {total} comments analyzed, {pct['negative']}% were negative, {pct['positive']}% "
        f"positive, and {pct['neutral']}% neutral."
    )

    if data["themes"]:
        top_theme = data["themes"][0]
        paras.append(
            f"The most-discussed theme was “{top_theme['theme']}” ({top_theme['total']} "
            f"mentions: {top_theme['negative']} negative, {top_theme['positive']} positive, "
            f"{top_theme['neutral']} neutral)."
        )

    significant_events = [e for e in data["events"] if e.get("significant")]
    if significant_events:
        names = "; ".join(
            f"{e['label']} ({e['pre']['negative_pct']}% → {e['post']['negative_pct']}% negative, "
            f"p={e['p_value']})"
            for e in significant_events
        )
        paras.append(
            f"Of the {len(data['events'])} known events tracked, "
            f"{len(significant_events)} showed a statistically significant "
            f"(p<0.05) shift in negative-sentiment share across a "
            f"±30-day window: {names}."
        )
    else:
        paras.append(
            f"None of the {len(data['events'])} known events tracked showed a statistically "
            "significant (p<0.05) shift in negative-sentiment share across a ±30-day window "
            "at the current sample size -- visible directional shifts on the timeline should be "
            "read as suggestive, not confirmed."
        )

    nepal, india = data["by_region"].get("nepal"), data["by_region"].get("india")
    if nepal and india and sum(nepal.values()) and sum(india.values()):
        nepal_neg_pct = round(nepal["negative"] / sum(nepal.values()) * 100, 1)
        india_neg_pct = round(india["negative"] / sum(india.values()) * 100, 1)
        paras.append(
            f"Nepal-tagged comments ran {nepal_neg_pct}% negative overall vs. {india_neg_pct}% "
            "for India-tagged comments, consistent with CONTEXT.md's framing of Wai Wai as "
            "declining at home while growing in the Indian market -- though region is a "
            "best-effort signal, not verified geography (see DATA_SOURCES.md)."
        )

    if data["competitors"] and data["competitors"][0]["total"] > 0:
        top_comp = data["competitors"][0]
        paras.append(
            f"Among named competitors, {top_comp['display_name']} drew the most mentions "
            f"({top_comp['total']})."
        )

    paras.append(
        "Recommendation: correlation shown here is suggestive, not causal -- no confounders "
        "(seasonality, unrelated news cycles) are controlled for, and this report runs on "
        "synthetic sample data unless real collection (pipeline/collect.py) has been configured. "
        "Prioritize a real human-labeled gold sample (pipeline/label_gold.py) before acting on "
        "any specific percentage in this report."
    )
    return paras


def generate_report_pdf(data: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], textColor=COLOR_TEXT, fontSize=20)
    h2_style = ParagraphStyle("ReportH2", parent=styles["Heading2"], textColor=COLOR_TEXT, spaceBefore=16)
    body_style = ParagraphStyle("ReportBody", parent=styles["BodyText"], textColor=COLOR_TEXT, leading=15)
    sub_style = ParagraphStyle("ReportSub", parent=styles["BodyText"], textColor=COLOR_MUTED, fontSize=9)

    def sentiment_table(rows_dict: dict, first_col_header: str, name_map: dict = None) -> Table:
        header = [first_col_header, "Total", "Positive", "Negative", "Neutral"]
        body = []
        for key, counts in rows_dict.items():
            total = sum(counts.values())
            label = name_map.get(key, key) if name_map else key
            body.append([label, str(total), str(counts["positive"]), str(counts["negative"]), str(counts["neutral"])])
        table = Table([header] + body, hAlign="LEFT", colWidths=[1.8 * inch, 0.8 * inch, 0.9 * inch, 0.9 * inch, 0.9 * inch])
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_TEXT),
            ("TEXTCOLOR", (0, 1), (-1, -1), COLOR_TEXT),
            ("LINEBELOW", (0, 0), (-1, 0), 1, COLOR_BORDER),
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, COLOR_BORDER),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F1E9")]),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return table

    story = []
    story.append(Paragraph("Wai Wai / CG Foods Sentiment Tracker", title_style))
    story.append(Paragraph(
        f"Generated {data['generated_at'][:19].replace('T', ' ')} UTC &middot; {data['total']} comments analyzed",
        sub_style,
    ))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Summary", h2_style))
    for para in _build_narrative(data):
        story.append(Paragraph(para, body_style))
        story.append(Spacer(1, 6))

    story.append(Paragraph("Overall sentiment split", h2_style))
    story.append(sentiment_table({"All comments": data["counts"]}, "Scope"))

    story.append(Paragraph("Sentiment by platform", h2_style))
    story.append(sentiment_table(data["by_platform"], "Platform"))

    story.append(Paragraph("Sentiment by region", h2_style))
    story.append(Paragraph(
        "Best-effort signal -- see DATA_SOURCES.md's Region Heuristic section for the "
        "false-negative-rate caveat.", sub_style,
    ))
    story.append(sentiment_table(data["by_region"], "Region"))

    story.append(PageBreak())
    story.append(Paragraph("Events &amp; statistical significance", h2_style))
    event_header = ["Event", "Date", "Pre neg.%", "Post neg.%", "p-value", "Significant?"]
    event_body = []
    for e in data["events"]:
        sig = "Yes" if e.get("significant") else ("No" if e.get("significant") is False else "n/a")
        event_body.append([
            e["label"], e["date"],
            f"{e['pre']['negative_pct']}%" if e["pre"]["negative_pct"] is not None else "n/a",
            f"{e['post']['negative_pct']}%" if e["post"]["negative_pct"] is not None else "n/a",
            str(e["p_value"]) if e["p_value"] is not None else "n/a",
            sig,
        ])
    events_table = Table([event_header] + event_body, hAlign="LEFT",
                          colWidths=[2.0 * inch, 0.8 * inch, 0.8 * inch, 0.8 * inch, 0.7 * inch, 0.7 * inch])
    events_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 1, COLOR_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F1E9")]),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(events_table)

    story.append(Paragraph("Theme frequency", h2_style))
    story.append(sentiment_table({t["theme"]: {"positive": t["positive"], "negative": t["negative"], "neutral": t["neutral"]}
                                   for t in data["themes"]}, "Theme"))

    story.append(Paragraph("Named competitor mentions", h2_style))
    story.append(sentiment_table(
        {c["competitor"]: {"positive": c["positive"], "negative": c["negative"], "neutral": c["neutral"]}
         for c in data["competitors"]},
        "Competitor", name_map=COMPETITOR_DISPLAY_NAMES,
    ))

    doc.build(story)
    return buf.getvalue()


def main():
    conn = get_connection()
    data = gather_report_data(conn)
    conn.close()

    if not data["total"]:
        raise RuntimeError("No classified rows found. Run the pipeline (clean.py -> classify.py -> analyze.py) first.")

    pdf_bytes = generate_report_pdf(data)
    REPORT_PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PDF_PATH.write_bytes(pdf_bytes)
    print(f"Wrote {REPORT_PDF_PATH} ({len(pdf_bytes):,} bytes)")


if __name__ == "__main__":
    main()
