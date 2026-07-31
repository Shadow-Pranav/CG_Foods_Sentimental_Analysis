"""
Collection stage — pipeline/collect.py

Implements DATA_SOURCES.md's per-platform collection notes. This is the
*real* collection path (opt-in, requires API keys in .env) as opposed to
pipeline/generate_sample_data.py, which the app runs against out of the
box in this environment (no live API keys / scraping access here).

    YouTube    -- YouTube Data API v3 (search.list + commentThreads.list).
                  Stable, quota-metered. Requires YOUTUBE_API_KEY.
    Twitter/X  -- Best-effort via snscrape if it still works against
                  current X rate limiting (it frequently does not -- see
                  DATA_SOURCES.md). Falls back to ingesting a manually
                  exported CSV from the X web search UI, per
                  PROJECT_INSTRUCTIONS.md's documented fallback.
    Facebook   -- Thinnest source. No reliable free API since the
                  Cambridge-Analytica-era lockdowns. Stubbed as a manual
                  CSV-export ingester (e.g. from CrowdTangle or a
                  researcher's own Page access), matching DATA_SOURCES.md's
                  "treat as supplementary, not primary" guidance.

Output schema matches DATA_SOURCES.md's "Suggested Schema" table and
generate_sample_data.py's CSV, so pipeline/clean.py can consume either
source interchangeably: id, platform, text_raw, timestamp, source_ref,
engagement, language_guess, author_id, gold_sentiment, region. See
DATA_SOURCES.md's "Region Heuristic" section for how `region`
(nepal/india/unknown) is derived per platform and its expected
false-negative rate -- none of these platforms reliably expose commenter
geography via public APIs, so "unknown" will dominate in practice.

Usage:
    python pipeline/collect.py

Requires a .env file (see .env.example) with at least YOUTUBE_API_KEY set
to do anything live. With no keys configured, this prints setup
instructions and exits without touching data/raw_comments.csv, so it never
clobbers the working sample dataset.
"""

import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "raw_comments.csv"

SEARCH_KEYWORDS = [
    "Wai Wai", "CG Foods", "waiwai noodles", "वाई वाई",
    "wai wai noodles nepal", "wai wai noodles india",
]

CSV_FIELDS = [
    "id", "platform", "text_raw", "timestamp", "source_ref",
    "engagement", "language_guess", "author_id", "gold_sentiment", "region",
]

# ---------------------------------------------------------------------------
# Region heuristic (Nepal / India / unknown) -- see DATA_SOURCES.md's
# "Region Heuristic" section for the false-negative-rate discussion. None
# of YouTube/X/Facebook's public APIs expose a commenter's actual location
# directly; this matches self-reported, free-text location signal where a
# platform exposes one at all, and returns "unknown" otherwise. Expect a
# high unknown rate in practice (most users don't fill in a location field,
# and YouTube's public comment API exposes none at all by default) -- this
# is a best-effort signal, not ground truth.
# ---------------------------------------------------------------------------

NEPAL_LOCATION_KEYWORDS = [
    "nepal", "kathmandu", "pokhara", "lalitpur", "biratnagar", "birgunj",
    "np ",
]
INDIA_LOCATION_KEYWORDS = [
    "india", "assam", "guwahati", "north east india", "ne india", "delhi",
    "mumbai", "kolkata", "bengaluru", "bangalore", "manipur", "meghalaya",
    "nagaland", "tripura", "sikkim", "mizoram", "arunachal",
]


def guess_region_from_location_text(location_text: str) -> str:
    """Best-effort match against a free-text location field (X profile
    location, Facebook Page name, YouTube channel country name). Returns
    'nepal', 'india', or 'unknown'. Deliberately does NOT match bare 2-letter
    country codes like 'np'/'in' as substrings (too many false positives in
    free text -- 'in' collides with the English word); only Nepal's code is
    matched, and only as a trailing token with a space, which is still weak
    and mostly here for completeness rather than expected to fire often."""
    if not location_text:
        return "unknown"
    lowered = f" {location_text.lower().strip()} "
    if any(k in lowered for k in NEPAL_LOCATION_KEYWORDS):
        return "nepal"
    if any(k in lowered for k in INDIA_LOCATION_KEYWORDS):
        return "india"
    return "unknown"


# ---------------------------------------------------------------------------
# YouTube -- YouTube Data API v3
# ---------------------------------------------------------------------------

def collect_youtube(api_key: str, max_videos: int = 15, max_comments_per_video: int = 100) -> list:
    """search.list (100 quota units/call) to discover videos, then
    commentThreads.list (1 unit/call) to pull top-level comments -- the
    front-load-discovery-then-batch-comments approach DATA_SOURCES.md
    recommends to stay under the 10,000 units/day free-tier quota.

    Region: YouTube's public commentThreads API does not expose a
    commenter's location at all. The only real signal available is each
    commenter's own channel `snippet.country` field (self-reported, and
    most users never set it) via an extra channels.list call per unique
    commenter -- expensive in quota (1 unit/call, but one per distinct
    author). Off by default; set YOUTUBE_RESOLVE_COMMENTER_REGION=true to
    enable it. With it off (the default), region is always "unknown" for
    YouTube rows -- see DATA_SOURCES.md's Region Heuristic section."""
    from googleapiclient.discovery import build

    youtube = build("youtube", "v3", developerKey=api_key)
    resolve_region = os.environ.get("YOUTUBE_RESOLVE_COMMENTER_REGION", "").lower() == "true"
    channel_country_cache = {}

    def channel_region(channel_id: str) -> str:
        if not channel_id:
            return "unknown"
        if channel_id in channel_country_cache:
            return channel_country_cache[channel_id]
        try:
            resp = youtube.channels().list(part="snippet", id=channel_id).execute()
            items = resp.get("items", [])
            country = items[0]["snippet"].get("country", "") if items else ""
        except Exception:  # noqa: BLE001 - quota/permission errors shouldn't kill collection
            country = ""
        region = "nepal" if country == "NP" else "india" if country == "IN" else "unknown"
        channel_country_cache[channel_id] = region
        return region

    rows = []

    for keyword in SEARCH_KEYWORDS:
        search_resp = youtube.search().list(
            q=keyword, part="id", type="video", maxResults=min(max_videos, 50),
        ).execute()

        for item in search_resp.get("items", []):
            video_id = item["id"].get("videoId")
            if not video_id:
                continue
            try:
                comments_resp = youtube.commentThreads().list(
                    part="snippet", videoId=video_id, maxResults=min(max_comments_per_video, 100),
                    textFormat="plainText",
                ).execute()
            except Exception as exc:  # noqa: BLE001 - comments disabled, private video, etc.
                print(f"[collect.py] Skipping video {video_id} ({exc})")
                continue

            for c in comments_resp.get("items", []):
                top = c["snippet"]["topLevelComment"]["snippet"]
                author_channel_id = top.get("authorChannelId", {}).get("value", "unknown")
                region = channel_region(author_channel_id) if resolve_region else "unknown"
                rows.append({
                    "id": f"youtube_{c['id']}",
                    "platform": "youtube",
                    "text_raw": top.get("textDisplay", ""),
                    "timestamp": top.get("publishedAt", "")[:19],
                    "source_ref": f"yt:video_{video_id}",
                    "engagement": top.get("likeCount", 0),
                    "language_guess": "",
                    "author_id": author_channel_id,
                    "gold_sentiment": "",
                    "region": region,
                })

    return rows


# ---------------------------------------------------------------------------
# Twitter/X -- best-effort snscrape, with a manual-export fallback
# ---------------------------------------------------------------------------

def collect_twitter_snscrape(max_tweets: int = 200) -> list:
    """Best-effort per DATA_SOURCES.md: snscrape historically worked
    without API keys but is unreliable against X's current scraping
    defenses. Treat any failure here as expected, not a bug -- fall back
    to collect_twitter_from_manual_export()."""
    try:
        import snscrape.modules.twitter as sntwitter
    except ImportError:
        print(
            "[collect.py] snscrape not installed / non-functional against "
            "current X rate limiting. Falling back to manual export path "
            "-- see collect_twitter_from_manual_export()."
        )
        return []

    rows = []
    query = " OR ".join(f'"{k}"' for k in SEARCH_KEYWORDS)
    try:
        for i, tweet in enumerate(sntwitter.TwitterSearchScraper(query).get_items()):
            if i >= max_tweets:
                break
            profile_location = getattr(tweet.user, "location", "") if tweet.user else ""
            rows.append({
                "id": f"twitter_{tweet.id}",
                "platform": "twitter",
                "text_raw": tweet.rawContent,
                "timestamp": tweet.date.strftime("%Y-%m-%dT%H:%M:%S"),
                "source_ref": f"tw:tweet_{tweet.id}",
                "engagement": (tweet.likeCount or 0) + (tweet.retweetCount or 0),
                "language_guess": "",
                "author_id": str(tweet.user.id) if tweet.user else "unknown",
                "gold_sentiment": "",
                "region": guess_region_from_location_text(profile_location),
            })
    except Exception as exc:  # noqa: BLE001 - X blocks scraping unpredictably
        print(f"[collect.py] snscrape failed ({exc}); falling back to manual export path.")
        return []

    return rows


def collect_twitter_from_manual_export(csv_path: str) -> list:
    """Ingests a bounded convenience sample manually exported from the X
    web search UI, per PROJECT_INSTRUCTIONS.md's documented fallback for
    when snscrape/API access isn't available. Expected columns: text,
    timestamp, likes, retweets, author_id, location (location is optional --
    the profile's free-text location field, if you captured it)."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Manual X export not found at {csv_path}")

    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for i, r in enumerate(csv.DictReader(f)):
            rows.append({
                "id": f"twitter_manual_{i:05d}",
                "platform": "twitter",
                "text_raw": r.get("text", ""),
                "timestamp": r.get("timestamp", ""),
                "source_ref": r.get("url", "tw:manual_export"),
                "engagement": int(r.get("likes", 0) or 0) + int(r.get("retweets", 0) or 0),
                "language_guess": "",
                "author_id": r.get("author_id", "unknown"),
                "gold_sentiment": "",
                "region": guess_region_from_location_text(r.get("location", "")),
            })
    return rows


# ---------------------------------------------------------------------------
# Facebook -- thinnest source, manual-export path only
# ---------------------------------------------------------------------------

def collect_facebook_live():
    """No reliable free/public API remains for Page post/comment scraping
    since the Cambridge-Analytica-era lockdowns (DATA_SOURCES.md). A real
    deployment would use CrowdTangle (if an academic/researcher grant is
    available) or a browser-based scraper limited to genuinely public
    content. Neither is wired up here -- use
    collect_facebook_from_manual_export() instead."""
    raise NotImplementedError(
        "Live Facebook collection is not implemented -- see DATA_SOURCES.md's "
        "Facebook notes. Use collect_facebook_from_manual_export(csv_path) "
        "with a CrowdTangle export or manually saved public-post data."
    )


def collect_facebook_from_manual_export(csv_path: str) -> list:
    """Expected columns: text, timestamp, page_name, reactions. Region is
    guessed from page_name (e.g. a "CG Foods Nepal" vs "CG Foods India"
    Page) -- this describes the Page's market, not necessarily the
    individual commenter's location, so treat it as an even weaker signal
    than the X profile-location heuristic."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Manual Facebook export not found at {csv_path}")

    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for i, r in enumerate(csv.DictReader(f)):
            page_name = r.get("page_name", "")
            rows.append({
                "id": f"facebook_manual_{i:05d}",
                "platform": "facebook",
                "text_raw": r.get("text", ""),
                "timestamp": r.get("timestamp", ""),
                "source_ref": page_name or "fb:manual_export",
                "engagement": int(r.get("reactions", 0) or 0),
                "language_guess": "",
                "author_id": "unknown",  # public Page posts rarely expose a stable pseudonymous commenter ID via manual export
                "gold_sentiment": "",
                "region": guess_region_from_location_text(page_name),
            })
    return rows


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def main():
    youtube_key = os.environ.get("YOUTUBE_API_KEY")
    twitter_manual_csv = os.environ.get("TWITTER_MANUAL_EXPORT_CSV")
    facebook_manual_csv = os.environ.get("FACEBOOK_MANUAL_EXPORT_CSV")

    if not (youtube_key or twitter_manual_csv or facebook_manual_csv):
        print(
            "No collection sources configured.\n\n"
            "This pipeline stage is opt-in and requires real credentials. "
            "Copy .env.example to .env and set at least one of:\n"
            "  YOUTUBE_API_KEY               (YouTube Data API v3 key)\n"
            "  TWITTER_MANUAL_EXPORT_CSV     (path to a manually exported X search CSV)\n"
            "  FACEBOOK_MANUAL_EXPORT_CSV    (path to a manual/CrowdTangle Facebook export)\n\n"
            "Until then, run pipeline/generate_sample_data.py instead -- the "
            "app is wired to run on that synthetic dataset out of the box.\n"
            "Existing data/raw_comments.csv was left untouched."
        )
        return

    rows = []
    if youtube_key:
        print("[collect.py] Collecting from YouTube Data API v3...")
        rows.extend(collect_youtube(youtube_key))

    if os.environ.get("TWITTER_TRY_SNSCRAPE", "").lower() == "true":
        print("[collect.py] Attempting best-effort snscrape collection...")
        rows.extend(collect_twitter_snscrape())

    if twitter_manual_csv:
        print(f"[collect.py] Ingesting manual X export from {twitter_manual_csv}...")
        rows.extend(collect_twitter_from_manual_export(twitter_manual_csv))

    if facebook_manual_csv:
        print(f"[collect.py] Ingesting manual Facebook export from {facebook_manual_csv}...")
        rows.extend(collect_facebook_from_manual_export(facebook_manual_csv))

    if not rows:
        print("[collect.py] No rows collected from any configured source. Nothing written.")
        return

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[collect.py] Wrote {len(rows)} rows to {OUT_PATH}")
    print(f"[collect.py] Collected at {datetime.now(timezone.utc).isoformat()}")
    print("[collect.py] Next: python pipeline/clean.py")


if __name__ == "__main__":
    main()
