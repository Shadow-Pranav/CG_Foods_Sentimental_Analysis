"""
Analysis stage — pipeline/analyze.py

Implements METHODOLOGY.md section 3 ("Keyword / Theme Extraction"):
  - Seed a keyword dictionary from CONTEXT.md's recurring themes (price,
    spice, packaging, quality, taste, nostalgia, availability) to tag
    comments thematically, so it's possible to say e.g. "38% of negative
    comments in month X mention price" rather than relying on word clouds
    alone.
  - Also computes term-frequency word-cloud data per sentiment class
    (post-stopword-removal), split positive vs. negative vs. neutral per
    METHODOLOGY.md section 4's visualization plan.

Two extra themes (competitor, legal) are tagged beyond the required seed
set, since CONTEXT.md explicitly calls out tracking competitor-name
mentions (Current Noodles / 2PM Noodles) and the trademark-dispute chatter
as part of the analysis narrative.

Writes theme tags back onto comments.themes (JSON list) and populates the
term_frequency table. Run after pipeline/classify.py (needs final_label).
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection  # noqa: E402

# Seed dictionary from CONTEXT.md (required themes) plus two narrative
# extensions (competitor, legal) called out explicitly in CONTEXT.md's
# "Known Events" section.
THEME_KEYWORDS = {
    "price": [
        "price", "priced", "pricing", "expensive", "costly", "afford",
        "cheap", "mahango", "mahenga", "sasto", "rupees", "hike", "cost",
        "inflation",
    ],
    "spice": [
        "spice", "spicy", "masala", "chili", "chilli", "jhamjham", "tarka",
        "heat level", "spice packet", "spice level",
    ],
    "packaging": [
        "packaging", "wrapper", "packet design", "plastic wrap", "seal",
        "repackag",
    ],
    "quality": [
        "quality", "acid value", "health", "safety", "standard", "fine",
        "test failed", "contaminat", "ingredient", "kmc", "hazard",
    ],
    "taste": [
        "taste", "tasty", "flavor", "flavour", "delicious", "yum", "maja",
    ],
    "nostalgia": [
        "nostalgia", "childhood", "grew up", "bachpan", "mummy",
        "hostel days", "comfort food", "school days",
    ],
    "availability": [
        "availability", "available", "stock", "shelves", "out of stock",
        "find it", "everywhere", "supermarket", "store", "corner shop",
    ],
    "competitor": [
        "current noodles", "2pm noodles", "maggi", "yippee", "switched",
        "switch to", "compar",
    ],
    "legal": [
        "trademark", "lawsuit", "court", "ruling", "sue", "sued",
        "high court", "copying", "imitat", "legal muscle",
    ],
}

# Per-competitor sub-tags, seeded from CONTEXT.md's named rivals (Current
# Noodles and 2PM Noodles in the Nepal spicy-noodle segment; Maggi and
# Sunfeast Yippee! as the established India-market leaders Wai Wai is
# compared against nationally). A comment gets both the umbrella
# "competitor" tag above (for the existing theme chart) AND whichever
# specific competitor sub-tag(s) it names, so share-of-voice can be broken
# out per named rival rather than lumped together.
COMPETITOR_KEYWORDS = {
    "current_noodles": ["current noodles"],
    "2pm_noodles": ["2pm noodles"],
    "maggi": ["maggi"],
    "yippee": ["yippee", "sunfeast yippee"],
}
COMPETITOR_TAGS = list(COMPETITOR_KEYWORDS.keys())
COMPETITOR_DISPLAY_NAMES = {
    "current_noodles": "Current Noodles",
    "2pm_noodles": "2PM Noodles",
    "maggi": "Maggi",
    "yippee": "Sunfeast Yippee!",
}

STANDARD_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for",
    "with", "this", "that", "it", "its", "i", "my", "me", "so", "was",
    "were", "are", "is", "be", "been", "being", "as", "at", "have", "has",
    "had", "not", "no", "do", "does", "did", "will", "would", "can",
    "could", "should", "ive", "im", "youre", "dont", "didnt", "doesnt",
    "you", "your", "we", "our", "they", "their", "them", "he", "she",
    "his", "her", "if", "then", "than", "just", "really", "very", "still",
    "get", "got", "one", "all", "some", "about", "into", "out", "up",
    "down", "now", "also", "even", "much", "more", "most", "here",
    "there", "when", "what", "which", "who", "how", "why", "from", "by",
    "these", "those", "am", "ok", "okay",
    # Generator-artifact filler words injected by vary_template() in
    # generate_sample_data.py for dedup diversity -- these are synthetic
    # padding, not real content signal, so they're excluded here to keep
    # the word-cloud data meaningful. A real (collected) dataset would not
    # need this exclusion list.
    "honestly", "speaking", "gonna", "lie", "longtime", "customer",
    "thinking", "real", "talk", "experience", "saying", "personally",
    "wrong", "opinion", "feels", "lately", "curious", "others", "think",
    "noticing", "anyone", "else", "same", "take", "leave", "least",
    "thats", "tbh", "ngl", "lol", "fr", "wai", "waiwai", "noodles",
    "noodle", "cg", "foods", "packet", "packets",
}

TOKEN_PATTERN = re.compile(r"[a-z]{3,}")
TOP_N_TERMS = 30


def tag_themes(text_clean: str) -> list:
    lowered = text_clean.lower()
    tags = []
    for theme, keywords in THEME_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            tags.append(theme)
    for competitor, keywords in COMPETITOR_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            tags.append(competitor)
    return tags


def tokenize(text_clean: str) -> list:
    return [t for t in TOKEN_PATTERN.findall(text_clean.lower()) if t not in STANDARD_STOPWORDS]


def main():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, text_clean, final_label FROM comments WHERE exclusion_reason IS NULL"
    ).fetchall()
    rows = [dict(r) for r in rows]

    if not rows:
        raise RuntimeError("No cleaned rows found. Run pipeline/clean.py then pipeline/classify.py first.")
    if rows[0].get("final_label") is None:
        raise RuntimeError("Rows have no final_label. Run pipeline/classify.py before analyze.py.")

    theme_updates = []
    term_counters = {"positive": Counter(), "negative": Counter(), "neutral": Counter()}
    theme_sentiment_counts = Counter()

    for row in rows:
        themes = tag_themes(row["text_clean"])
        theme_updates.append({"id": row["id"], "themes": json.dumps(themes)})

        label = row["final_label"]
        for token in tokenize(row["text_clean"]):
            term_counters[label][token] += 1
        for theme in themes:
            theme_sentiment_counts[(theme, label)] += 1

    conn.executemany("UPDATE comments SET themes = %(themes)s WHERE id = %(id)s", theme_updates)

    conn.execute("DELETE FROM term_frequency;")
    term_rows = []
    for sentiment, counter in term_counters.items():
        for term, count in counter.most_common(TOP_N_TERMS):
            term_rows.append({"sentiment": sentiment, "term": term, "count": count})
    conn.executemany(
        "INSERT INTO term_frequency (sentiment, term, count) VALUES (%(sentiment)s, %(term)s, %(count)s)",
        term_rows,
    )

    conn.commit()
    conn.close()

    tagged_count = sum(1 for u in theme_updates if json.loads(u["themes"]))
    print(f"Tagged {tagged_count}/{len(rows)} comments with at least one theme.")
    print("Theme x sentiment counts:")
    for (theme, label), count in sorted(theme_sentiment_counts.items()):
        print(f"  {theme:12s} {label:8s} {count}")
    print(f"Stored top {TOP_N_TERMS} terms per sentiment class in term_frequency table.")


if __name__ == "__main__":
    main()
