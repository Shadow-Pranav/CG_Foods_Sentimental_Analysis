"""
Statistical significance testing (step 5): pipeline/event_analysis.py.
"""

from pipeline.event_analysis import SIGNIFICANCE_ALPHA, analyze_event, window_counts
from tests.conftest import seed_comments

EVENT = {"id": "test_event", "date": "2025-06-15", "label": "Test Event"}


def _seed_pre_post(conn, pre_negative, pre_non_negative, post_negative, post_non_negative):
    rows = []
    i = 0
    for _ in range(pre_negative):
        rows.append({"id": f"pre_neg_{i}", "platform": "youtube", "timestamp": "2025-05-20T00:00:00", "final_label": "negative"})
        i += 1
    for _ in range(pre_non_negative):
        rows.append({"id": f"pre_pos_{i}", "platform": "youtube", "timestamp": "2025-05-20T00:00:00", "final_label": "positive"})
        i += 1
    for _ in range(post_negative):
        rows.append({"id": f"post_neg_{i}", "platform": "youtube", "timestamp": "2025-06-20T00:00:00", "final_label": "negative"})
        i += 1
    for _ in range(post_non_negative):
        rows.append({"id": f"post_pos_{i}", "platform": "youtube", "timestamp": "2025-06-20T00:00:00", "final_label": "positive"})
        i += 1
    seed_comments(conn, rows)


def test_window_counts_correctly_partitions_negative_vs_non_negative(conn):
    seed_comments(conn, [
        {"id": "a", "platform": "youtube", "timestamp": "2025-06-10T00:00:00", "final_label": "negative"},
        {"id": "b", "platform": "youtube", "timestamp": "2025-06-10T00:00:00", "final_label": "positive"},
        {"id": "c", "platform": "youtube", "timestamp": "2025-06-10T00:00:00", "final_label": "neutral"},
        {"id": "d", "platform": "youtube", "timestamp": "2025-07-10T00:00:00", "final_label": "negative"},  # outside window
    ])
    neg, non_neg = window_counts(conn, "2025-06-01", "2025-06-30")
    assert neg == 1
    assert non_neg == 2  # positive + neutral both count as non-negative


def test_window_counts_excludes_filtered_rows(conn):
    seed_comments(conn, [
        {"id": "a", "platform": "youtube", "timestamp": "2025-06-10T00:00:00",
         "final_label": "negative", "exclusion_reason": "spam_pattern"},
    ])
    neg, non_neg = window_counts(conn, "2025-06-01", "2025-06-30")
    assert (neg, non_neg) == (0, 0)


def test_dramatic_shift_with_adequate_sample_is_significant(conn):
    # Pre: 20% negative (2/10). Post: 90% negative (18/20). Large, real shift.
    _seed_pre_post(conn, pre_negative=2, pre_non_negative=8, post_negative=18, post_non_negative=2)
    result = analyze_event(conn, EVENT)
    assert result["significant"] is True
    assert result["p_value"] < SIGNIFICANCE_ALPHA
    assert result["pre"]["negative_pct"] == 20.0
    assert result["post"]["negative_pct"] == 90.0


def test_small_shift_with_tiny_sample_is_not_significant(conn):
    # A couple of comments each side -- even a big-looking percentage swing
    # shouldn't clear significance with this little data.
    _seed_pre_post(conn, pre_negative=1, pre_non_negative=2, post_negative=2, post_non_negative=1)
    result = analyze_event(conn, EVENT)
    assert result["significant"] is False


def test_uses_fishers_exact_when_expected_cell_count_is_small(conn):
    _seed_pre_post(conn, pre_negative=1, pre_non_negative=2, post_negative=2, post_non_negative=1)
    result = analyze_event(conn, EVENT)
    assert result["test"] == "fisher_exact"


def test_uses_chi_square_when_sample_is_large_enough(conn):
    _seed_pre_post(conn, pre_negative=20, pre_non_negative=30, post_negative=25, post_non_negative=25)
    result = analyze_event(conn, EVENT)
    assert result["test"] == "chi2"


def test_infinite_odds_ratio_is_reported_as_null_not_a_crash(conn):
    """fisher_exact's odds ratio for table [[a,b],[c,d]] is (a*d)/(b*c) --
    +inf when b*c == 0 but a*d != 0 (here: post_negative=0, i.e. c=0).
    JSON can't serialize inf, so this must come back as None with the
    p-value still valid (this was a real bug found while building the
    /api/event-analysis endpoint, on the real india_expansion_2026 event
    which has exactly this zero-post-negative shape)."""
    _seed_pre_post(conn, pre_negative=1, pre_non_negative=2, post_negative=0, post_non_negative=3)
    result = analyze_event(conn, EVENT)
    assert result["statistic"] is None
    assert result["p_value"] is not None
    assert isinstance(result["p_value"], float)


def test_empty_window_returns_none_significance_not_an_exception(conn):
    seed_comments(conn, [
        {"id": "a", "platform": "youtube", "timestamp": "2025-06-20T00:00:00", "final_label": "negative"},
    ])
    result = analyze_event(conn, EVENT)  # pre-window has zero comments
    assert result["significant"] is None
    assert result["test"] is None
    assert result["pre"]["total"] == 0


def test_shift_pct_points_is_post_minus_pre(conn):
    _seed_pre_post(conn, pre_negative=2, pre_non_negative=8, post_negative=18, post_non_negative=2)
    result = analyze_event(conn, EVENT)
    assert result["shift_pct_points"] == 90.0 - 20.0
