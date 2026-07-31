"""
pipeline/clean.py -- dedup / near-dup / spam-filter logic.

Feeds known duplicate/spam/genuine fixtures through clean_rows() and
asserts the resulting exclusion_reason per row, per METHODOLOGY.md
section 1's cleaning pipeline.
"""

from pipeline.clean import clean_rows, is_spam_pattern, normalize_for_hash, strip_text


def make_row(id_, text_raw, timestamp="2025-01-01T00:00:00", **extra):
    row = {"id": id_, "platform": "youtube", "text_raw": text_raw, "timestamp": timestamp,
           "source_ref": "x", "engagement": "0", "author_id": "a", "gold_sentiment": "", "region": "unknown"}
    row.update(extra)
    return row


def exclusions_by_id(cleaned):
    return {r["id"]: r["exclusion_reason"] for r in cleaned}


def test_unique_genuine_comment_is_kept():
    rows = [make_row("a", "Wai Wai got way more expensive after the price hike this year.")]
    cleaned = clean_rows(rows)
    assert exclusions_by_id(cleaned)["a"] is None


def test_exact_duplicate_text_is_flagged_after_first():
    rows = [
        make_row("a", "Still the best masala noodles around.", timestamp="2025-01-01T00:00:00"),
        make_row("b", "Still the best masala noodles around.", timestamp="2025-01-02T00:00:00"),
        make_row("c", "Still the best masala noodles around.", timestamp="2025-01-03T00:00:00"),
    ]
    exclusions = exclusions_by_id(clean_rows(rows))
    assert exclusions["a"] is None  # first occurrence (chronologically) is kept
    assert exclusions["b"] == "duplicate"
    assert exclusions["c"] == "duplicate"


def test_near_duplicate_is_flagged_but_not_as_exact_duplicate():
    rows = [
        make_row("a", "The spice packet is nowhere near as strong as it used to be.",
                  timestamp="2025-01-01T00:00:00"),
        # Same sentence plus one trailing word -- near-identical (ratio
        # ~0.97 after normalization), not byte-identical.
        make_row("b", "The spice packet is nowhere near as strong as it used to be now.",
                  timestamp="2025-01-02T00:00:00"),
    ]
    exclusions = exclusions_by_id(clean_rows(rows))
    assert exclusions["a"] is None
    assert exclusions["b"] == "near_duplicate"


def test_a_short_prefix_addition_can_fall_just_under_the_near_dup_threshold():
    """Documents the actual, intentionally strict 0.92 similarity cutoff:
    adding a whole clause (not just a word) to a short sentence can drop
    the ratio below threshold, so it's correctly NOT flagged. This is
    expected behavior, not a gap -- catching every paraphrase would risk
    false-positiving genuinely distinct opinions that happen to share
    structure."""
    rows = [
        make_row("a", "The price to quantity ratio has gotten so bad since the hike.",
                  timestamp="2025-01-01T00:00:00"),
        make_row("b", "Just saying, the price to quantity ratio has gotten so bad since the hike.",
                  timestamp="2025-01-02T00:00:00"),
    ]
    exclusions = exclusions_by_id(clean_rows(rows))
    assert exclusions["a"] is None
    assert exclusions["b"] is None


def test_distinct_comments_about_the_same_topic_are_not_flagged_as_duplicates():
    """Two genuinely different sentences sharing topical vocabulary
    (both about price) shouldn't collide -- dedup should key off actual
    text similarity, not shared keywords."""
    rows = [
        make_row("a", "Why did Wai Wai raise the price again this month?"),
        make_row("b", "Instant noodles used to be the cheap meal option, not anymore."),
    ]
    exclusions = exclusions_by_id(clean_rows(rows))
    assert exclusions["a"] is None
    assert exclusions["b"] is None


def test_ad_spam_pattern_is_flagged():
    rows = [make_row("a", "CHECK MY PROFILE FOR FREE GIVEAWAY http://bit.ly/xyz click here now")]
    exclusions = exclusions_by_id(clean_rows(rows))
    assert exclusions["a"] == "spam_pattern"


def test_pure_emoji_comment_is_empty_after_clean():
    rows = [make_row("a", "\U0001F600\U0001F600\U0001F600\U0001F600")]
    exclusions = exclusions_by_id(clean_rows(rows))
    assert exclusions["a"] == "empty_after_clean"


def test_url_and_html_and_emoji_are_stripped_but_text_survives():
    text_clean, emoji_count = strip_text(
        "Loving it \U0001F60D\U0001F60D check the review here: https://youtu.be/xyz &amp; more <b>bold</b>"
    )
    assert "http" not in text_clean
    assert "<b>" not in text_clean
    assert "\U0001F60D" not in text_clean
    assert "Loving it" in text_clean
    assert "&amp;" not in text_clean  # unescaped to a literal "&"
    assert emoji_count == 2


def test_text_raw_is_never_overwritten_by_cleaning():
    """METHODOLOGY.md: retain text_raw untouched alongside text_clean."""
    original = "Check this out \U0001F600 http://example.com"
    rows = [make_row("a", original)]
    cleaned = clean_rows(rows)[0]
    assert cleaned["text_raw"] == original
    assert cleaned["text_clean"] != original


def test_normalize_for_hash_ignores_case_and_punctuation():
    assert normalize_for_hash("Still the BEST masala noodles!") == normalize_for_hash("still the best masala noodles")


def test_is_spam_pattern_does_not_flag_genuine_complaints():
    assert is_spam_pattern("Why did Wai Wai raise the price again this month?") is False
    assert is_spam_pattern("free giveaway click here now") is True
