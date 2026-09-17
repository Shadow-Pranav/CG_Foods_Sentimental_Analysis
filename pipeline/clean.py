"""
Cleaning stage — pipeline/clean.py

Implements METHODOLOGY.md section 1 ("Cleaning Pipeline"):
  1. Deduplicate exact and near-duplicate text.
  2. Strip URLs, emojis (counted first as a secondary signal), HTML
     entities/tags, and excess whitespace.
  3. Language-detect each comment (langdetect).
  4. Bot/spam heuristic filter: ad-spam patterns, pure emoji/link comments,
     and repeated near-identical text.
  5. Retain both text_raw and text_clean — text_raw is never overwritten.

Reads data/raw_comments.csv (written by pipeline/generate_sample_data.py or
pipeline/collect.py) and writes every row into the `comments` PostgreSQL
table, including excluded rows (exclusion_reason set) so nothing is silently
dropped — downstream stages and the API simply filter on
`exclusion_reason IS NULL`. This keeps the pipeline auditable per
DATA_SOURCES.md's retention notes.
"""

import csv
import difflib
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection, reset_comments_table  # noqa: E402

from langdetect import DetectorFactory, LangDetectException, detect  # noqa: E402

DetectorFactory.seed = 0

RAW_PATH = Path(__file__).resolve().parent.parent / "data" / "raw_comments.csv"

URL_PATTERN = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "️‍"
    "]+",
    flags=re.UNICODE,
)
WHITESPACE_PATTERN = re.compile(r"\s+")
NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9\s]")

SPAM_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"free\s+giveaway",
        r"check\s+my\s+profile",
        r"dm\s+me\s+now",
        r"click\s+here",
        r"make\s+money\s+fast",
        r"follow\s+back",
        r"get\s*rich\s*quick",
    ]
]

NEAR_DUP_THRESHOLD = 0.92
ALLOWED_LANGUAGES = {"en", "ne", "hi"}  # per PROJECT_INSTRUCTIONS.md scope


def strip_text(text_raw: str) -> tuple:
    """Returns (text_clean, emoji_count). emoji_count sums the length of
    each matched run rather than counting matches directly: EMOJI_PATTERN
    is a `+`-quantified character class, so consecutive emoji (e.g. "😍😍")
    match as a single run and a plain len(findall(...)) would undercount
    them as 1 instead of 2."""
    emoji_count = sum(len(run) for run in EMOJI_PATTERN.findall(text_raw))

    text = html.unescape(text_raw)
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = URL_PATTERN.sub(" ", text)
    text = EMOJI_PATTERN.sub(" ", text)
    text = WHITESPACE_PATTERN.sub(" ", text).strip()
    return text, emoji_count


def normalize_for_hash(text_clean: str) -> str:
    lowered = text_clean.lower()
    stripped = NON_ALNUM_PATTERN.sub("", lowered)
    return WHITESPACE_PATTERN.sub(" ", stripped).strip()


def detect_language(text_clean: str) -> str:
    if len(text_clean.strip()) < 3:
        return "unknown"
    try:
        return detect(text_clean)
    except LangDetectException:
        return "unknown"


def is_spam_pattern(text_clean: str) -> bool:
    return any(p.search(text_clean) for p in SPAM_PATTERNS)


def load_raw_rows() -> list:
    if not RAW_PATH.exists():
        raise FileNotFoundError(
            f"{RAW_PATH} not found. Run pipeline/generate_sample_data.py "
            "(sample data) or pipeline/collect.py (live collection) first."
        )
    with open(RAW_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: r["timestamp"])  # chronological, for "keep first"
    return rows


def clean_rows(rows: list) -> list:
    seen_hashes = {}
    kept_normalized_texts = []  # (normalized_text, row_index) for near-dup checks
    cleaned = []

    for row in rows:
        text_raw = row["text_raw"]
        text_clean, emoji_count = strip_text(text_raw)
        normalized = normalize_for_hash(text_clean)
        language_guess = detect_language(text_clean)

        exclusion_reason = None

        if is_spam_pattern(text_clean):
            exclusion_reason = "spam_pattern"
        elif len(normalized) < 3:
            exclusion_reason = "empty_after_clean"
        elif normalized in seen_hashes:
            exclusion_reason = "duplicate"
        else:
            for existing_normalized, _ in kept_normalized_texts:
                if len(existing_normalized) == 0:
                    continue
                # Cheap length-ratio pre-filter before the more expensive
                # SequenceMatcher comparison.
                len_ratio = len(normalized) / max(len(existing_normalized), 1)
                if not (0.7 <= len_ratio <= 1.4):
                    continue
                ratio = difflib.SequenceMatcher(None, normalized, existing_normalized).ratio()
                if ratio >= NEAR_DUP_THRESHOLD:
                    exclusion_reason = "near_duplicate"
                    break

        if exclusion_reason is None:
            seen_hashes[normalized] = row["id"]
            kept_normalized_texts.append((normalized, row["id"]))

        cleaned.append({
            **row,
            "text_clean": text_clean,
            "language_guess": language_guess,
            "emoji_count": emoji_count,
            "exclusion_reason": exclusion_reason,
        })

    return cleaned


def write_to_db(cleaned_rows: list) -> None:
    conn = get_connection()
    reset_comments_table(conn)
    conn.executemany(
        """
        INSERT INTO comments (
            id, platform, text_raw, text_clean, timestamp, source_ref,
            engagement, author_id, language_guess, region, emoji_count,
            exclusion_reason, gold_sentiment
        ) VALUES (
            %(id)s, %(platform)s, %(text_raw)s, %(text_clean)s, %(timestamp)s, %(source_ref)s,
            %(engagement)s, %(author_id)s, %(language_guess)s, %(region)s, %(emoji_count)s,
            %(exclusion_reason)s, %(gold_sentiment)s
        )
        """,
        [
            {
                "id": r["id"],
                "platform": r["platform"],
                "text_raw": r["text_raw"],
                "text_clean": r["text_clean"],
                "timestamp": r["timestamp"],
                "source_ref": r.get("source_ref", ""),
                "engagement": int(r.get("engagement") or 0),
                "author_id": r.get("author_id", ""),
                "language_guess": r["language_guess"],
                "region": r.get("region") or "unknown",
                "emoji_count": r["emoji_count"],
                "exclusion_reason": r["exclusion_reason"],
                "gold_sentiment": r.get("gold_sentiment") or None,
            }
            for r in cleaned_rows
        ],
    )
    conn.commit()
    conn.close()


def main():
    rows = load_raw_rows()
    cleaned = clean_rows(rows)
    write_to_db(cleaned)

    total = len(cleaned)
    excluded = [r for r in cleaned if r["exclusion_reason"]]
    kept = total - len(excluded)

    reason_counts = {}
    for r in excluded:
        reason_counts[r["exclusion_reason"]] = reason_counts.get(r["exclusion_reason"], 0) + 1

    lang_counts = {}
    for r in cleaned:
        if not r["exclusion_reason"]:
            lang_counts[r["language_guess"]] = lang_counts.get(r["language_guess"], 0) + 1

    print(f"Cleaned {total} rows -> {kept} kept, {len(excluded)} excluded.")
    print("Exclusion reasons:", reason_counts)
    print("Language mix (kept rows):", lang_counts)
    non_allowed = {lang: c for lang, c in lang_counts.items() if lang not in ALLOWED_LANGUAGES}
    if non_allowed:
        print(
            "Note: kept rows include languages outside en/ne/hi scope "
            f"(PROJECT_INSTRUCTIONS.md): {non_allowed}. Retained and tagged "
            "rather than dropped, per METHODOLOGY.md 'bucket separately' "
            "guidance -- langdetect is unreliable on short/romanized text."
        )


if __name__ == "__main__":
    main()
