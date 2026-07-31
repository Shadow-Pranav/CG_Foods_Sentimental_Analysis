"""
API filter-combination tests (app/main.py): platform+sentiment+region+
date+q combined, empty-param-means-nothing vs. omitted-param-means-no-
filter, pagination, and exclusion-reason handling.
"""

from tests.conftest import seed_comments

SEED_ROWS = [
    {"id": "y1", "platform": "youtube", "timestamp": "2025-01-10T00:00:00",
     "final_label": "positive", "region": "nepal", "text_raw": "Wai Wai tastes amazing"},
    {"id": "y2", "platform": "youtube", "timestamp": "2025-01-15T00:00:00",
     "final_label": "negative", "region": "india", "text_raw": "Price went up again, annoying"},
    {"id": "y3", "platform": "youtube", "timestamp": "2025-02-01T00:00:00",
     "final_label": "neutral", "region": "unknown", "text_raw": "Anyone know the calories"},
    {"id": "t1", "platform": "twitter", "timestamp": "2025-02-10T00:00:00",
     "final_label": "positive", "region": "india", "text_raw": "Loving the new Indian stores"},
    {"id": "t2", "platform": "twitter", "timestamp": "2025-03-01T00:00:00",
     "final_label": "negative", "region": "nepal", "text_raw": "Quality dropped a lot lately"},
    {"id": "f1", "platform": "facebook", "timestamp": "2025-03-15T00:00:00",
     "final_label": "neutral", "region": "unknown", "text_raw": "zzzznarwhal unique marker keyword"},
    {"id": "excluded1", "platform": "youtube", "timestamp": "2025-01-20T00:00:00",
     "final_label": "negative", "region": "nepal", "text_raw": "spam spam spam",
     "exclusion_reason": "spam_pattern"},
]


def seed(client):
    seed_comments(client.db_conn, SEED_ROWS)


def get_ids(response_json):
    return {c["id"] for c in response_json["comments"]}


def test_no_filters_returns_all_non_excluded_rows(api_client):
    seed(api_client)
    r = api_client.get("/api/comments")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 6  # excludes "excluded1"
    assert "excluded1" not in get_ids(data)


def test_excluded_rows_never_appear_regardless_of_filters(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?sentiment=negative&platform=youtube")
    assert "excluded1" not in get_ids(r.json())


def test_platform_filter_single_value(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?platform=twitter")
    data = r.json()
    assert data["total"] == 2
    assert get_ids(data) == {"t1", "t2"}


def test_platform_filter_multiple_values_comma_separated(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?platform=twitter,facebook")
    data = r.json()
    assert data["total"] == 3
    assert get_ids(data) == {"t1", "t2", "f1"}


def test_sentiment_filter(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?sentiment=negative")
    data = r.json()
    assert data["total"] == 2
    assert get_ids(data) == {"y2", "t2"}


def test_region_filter(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?region=india")
    data = r.json()
    assert data["total"] == 2
    assert get_ids(data) == {"y2", "t1"}


def test_date_range_filter(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?start_date=2025-02-01&end_date=2025-02-28")
    data = r.json()
    assert get_ids(data) == {"y3", "t1"}


def test_keyword_search_matches_raw_text(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?q=narwhal")
    data = r.json()
    assert data["total"] == 1
    assert get_ids(data) == {"f1"}


def test_keyword_search_is_case_insensitive(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?q=NARWHAL")
    assert get_ids(r.json()) == {"f1"}


def test_combined_platform_sentiment_date_and_keyword_filters(api_client):
    seed(api_client)
    r = api_client.get(
        "/api/comments"
        "?platform=youtube,twitter"
        "&sentiment=negative"
        "&start_date=2025-01-01&end_date=2025-01-31"
        "&q=price"
    )
    data = r.json()
    assert get_ids(data) == {"y2"}


def test_omitted_param_means_no_filter_on_that_dimension(api_client):
    seed(api_client)
    with_sentiment_omitted = api_client.get("/api/comments").json()
    assert with_sentiment_omitted["total"] == 6


def test_empty_param_value_means_match_nothing(api_client):
    """Sending sentiment= with an empty value (every sentiment checkbox
    unchecked in the UI) must return zero rows, not all rows -- this is
    the opposite of omitting the param entirely."""
    seed(api_client)
    r = api_client.get("/api/comments?sentiment=")
    assert r.json()["total"] == 0


def test_empty_platform_param_means_match_nothing(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?platform=")
    assert r.json()["total"] == 0


def test_empty_region_param_means_match_nothing(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?region=")
    assert r.json()["total"] == 0


def test_pagination_page_size_and_total_pages(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?page_size=2&page=1")
    data = r.json()
    assert data["total"] == 6
    assert data["total_pages"] == 3
    assert len(data["comments"]) == 2

    r2 = api_client.get("/api/comments?page_size=2&page=3")
    assert len(r2.json()["comments"]) == 2


def test_pagination_page_beyond_last_returns_empty_not_an_error(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?page_size=2&page=99")
    assert r.status_code == 200
    assert r.json()["comments"] == []


def test_comments_are_ordered_newest_first(api_client):
    seed(api_client)
    r = api_client.get("/api/comments?page_size=10")
    timestamps = [c["timestamp"] for c in r.json()["comments"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_meta_endpoint_reflects_seeded_data(api_client):
    seed(api_client)
    r = api_client.get("/api/meta")
    data = r.json()
    assert data["pipeline_ready"] is True
    assert data["total_comments"] == 6
    assert set(data["regions"]) == {"nepal", "india", "unknown"}


def test_summary_percentages_sum_to_roughly_100(api_client):
    seed(api_client)
    r = api_client.get("/api/summary")
    data = r.json()
    total_pct = sum(data["percentages"].values())
    # Each of the 3 categories is independently rounded to 1dp, so the sum
    # can legitimately land a little off 100 (and float repr adds noise).
    assert 99.0 <= total_pct <= 101.0


def test_summary_on_empty_db_does_not_crash(api_client):
    r = api_client.get("/api/summary")
    data = r.json()
    assert data["total_comments"] == 0
