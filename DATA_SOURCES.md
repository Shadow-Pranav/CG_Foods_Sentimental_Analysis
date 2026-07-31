# Data Sources & Collection Notes

## Search Keywords (all platforms)

Core: `Wai Wai`, `CG Foods`, `waiwai noodles`. Regional/variant spellings worth including: `वाई वाई` (Nepali script), `wai wai noodles nepal`, `wai wai noodles india`. Add competitor terms (`Current Noodles`, `2PM Noodles`, `Maggi vs Wai Wai`) where comparative sentiment is being captured.

## YouTube

- **Method:** YouTube Data API v3 (`commentThreads.list`, `search.list`) — more stable than scraping. Pull comments from: (a) official Wai Wai/CG Foods ad uploads, (b) top review/unboxing/taste-test videos found via keyword search, (c) Nepali/Indian food-vlogger content mentioning the brand.
- **Quota:** Free tier is 10,000 units/day; a `commentThreads.list` call costs 1 unit but `search.list` costs 100 units — front-load video discovery, then batch comment pulls.
- **Fields to capture:** comment text, publish date, like count, video ID/title, reply count (top-level comments are usually sufficient; replies optional for volume).

## Facebook

- **Method:** Public Page/post scraping is heavily restricted since the Cambridge Analytica-era API lockdowns. Realistic options: CrowdTangle (if still accessible via an academic/researcher grant), manual export from public Pages the researcher can access, or a browser-based public-post scraper limited to genuinely public content only.
- **Expectation-setting:** treat Facebook as the thinnest, most supplementary data source. Do not assume parity in volume with YouTube/X.
- **Fields to capture:** post/comment text, timestamp, page name, reaction counts (as a rough engagement proxy).

## Twitter/X

- **Method:** `snscrape` historically worked without API keys but has become unreliable against X's current scraping defenses and rate limits; treat it as best-effort, not guaranteed. Alternative: X API v2 (paid tiers required for meaningful historical search since 2023). If budget-constrained, fall back to manual search-export via the X web search UI for a bounded sample, clearly documented as a convenience sample.
- **Fields to capture:** tweet text, timestamp, like/retweet count, whether it's a reply vs. original post (replies often carry more direct opinion).

## Legal / Ethical Notes

- Collect only public content; do not attempt to access private groups, DMs, or login-gated content.
- Respect each platform's Terms of Service — note explicitly in the methodology write-up where scraping (vs. official API use) was necessary and why.
- Store only what's needed for analysis (text, timestamp, platform, public engagement counts); avoid retaining personally identifying account details beyond a pseudonymous ID needed for deduplication.
- Since this is an academic project, cite that data collection followed a "public content, aggregate analysis, no individual profiling" standard — useful to state explicitly in any report or paper.

## Suggested Schema (post-collection, pre-cleaning)

| field | type | notes |
|---|---|---|
| `id` | string | platform-native ID or generated hash |
| `platform` | enum | youtube / facebook / twitter |
| `text_raw` | string | original text |
| `timestamp` | datetime | post/comment creation time |
| `source_ref` | string | video ID / post URL / tweet ID |
| `engagement` | int | likes/reactions as available |
| `language_guess` | string | populated during cleaning |
