"""
Engagement-weighted sentiment (step 4): app.main.compute_engagement_weighted().

Uses a raw in-memory connection (not the API) so the weighting math itself
is pinned down independent of HTTP/JSON plumbing.
"""

import math

from app.main import compute_engagement_weighted
from tests.conftest import seed_comments


def test_weighted_percentages_match_manual_ln_calculation(conn):
    seed_comments(conn, [
        {"id": "a", "platform": "youtube", "timestamp": "2025-01-01T00:00:00",
         "final_label": "positive", "engagement": 9},   # ln(10) ~= 2.303
        {"id": "b", "platform": "youtube", "timestamp": "2025-01-02T00:00:00",
         "final_label": "negative", "engagement": 0},   # ln(1) = 0
    ])
    result = compute_engagement_weighted(conn, "exclusion_reason IS NULL", [])

    expected_pos_weight = math.log(10)
    expected_total = expected_pos_weight + math.log(1)  # math.log(1) == 0
    assert result["percentages"]["positive"] == round(expected_pos_weight / expected_total * 100, 1)
    assert result["percentages"]["negative"] == 0.0
    assert result["total_weight"] == round(expected_total, 2)


def test_log_transform_prevents_single_outlier_from_dominating(conn):
    """A comment with 100x the engagement of everyone else should NOT get
    anywhere near 100x the weight -- that's the entire point of using
    ln(1+engagement) instead of a linear sum."""
    rows = [{"id": f"n{i}", "platform": "youtube", "timestamp": "2025-01-01T00:00:00",
             "final_label": "negative", "engagement": 5} for i in range(9)]
    rows.append({"id": "viral", "platform": "youtube", "timestamp": "2025-01-01T00:00:00",
                 "final_label": "positive", "engagement": 50000})
    seed_comments(conn, rows)

    result = compute_engagement_weighted(conn, "exclusion_reason IS NULL", [])
    # Linear-sum weighting would put the viral outlier's share near 100%;
    # log-transformed, it should be well under half.
    assert result["percentages"]["positive"] < 50.0
    assert result["max_single_comment_weight_share_pct"] < 50.0


def test_max_single_comment_weight_share_identifies_the_true_outlier(conn):
    seed_comments(conn, [
        {"id": "small", "platform": "youtube", "timestamp": "2025-01-01T00:00:00",
         "final_label": "neutral", "engagement": 1},
        {"id": "big", "platform": "youtube", "timestamp": "2025-01-01T00:00:00",
         "final_label": "neutral", "engagement": 999},
    ])
    result = compute_engagement_weighted(conn, "exclusion_reason IS NULL", [])
    big_weight = math.log(1000)
    small_weight = math.log(2)
    expected_share = round(big_weight / (big_weight + small_weight) * 100, 1)
    assert result["max_single_comment_weight_share_pct"] == expected_share


def test_empty_result_set_does_not_crash(conn):
    result = compute_engagement_weighted(conn, "exclusion_reason IS NULL", [])
    assert result["total_weight"] == 0
    assert result["percentages"] == {"positive": 0.0, "negative": 0.0, "neutral": 0.0}
    assert result["max_single_comment_weight_share_pct"] == 0.0


def test_negative_engagement_is_clamped_not_negative_weight(conn):
    """engagement should never be negative in real data, but the SQL uses
    MAX(engagement, 0) defensively -- confirm that actually holds."""
    seed_comments(conn, [
        {"id": "a", "platform": "youtube", "timestamp": "2025-01-01T00:00:00",
         "final_label": "negative", "engagement": -5},
    ])
    result = compute_engagement_weighted(conn, "exclusion_reason IS NULL", [])
    assert result["total_weight"] == 0.0  # ln(1 + max(-5, 0)) = ln(1) = 0
