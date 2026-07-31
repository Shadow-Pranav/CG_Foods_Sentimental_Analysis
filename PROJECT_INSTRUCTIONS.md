# Wai Wai / CG Foods Sentiment Tracker — Project Instructions

## Objective

Measure and explain public sentiment toward Wai Wai (CG Foods' flagship instant noodle brand) using user-generated content from social and video platforms, and surface actionable insight into why sentiment moves — particularly around price hikes, quality controversies, and competitor launches.

Wai Wai is Nepal's market leader and dominant in North East India (see `CONTEXT.md`), but CG Foods has posted a revenue decline in Nepal amid rising competition from spicy-noodle rivals. Consumer perception is therefore a timely, decision-relevant variable.

## Scope

In scope: YouTube comments (ad, review, unboxing videos), public Facebook posts/comments, and Twitter/X posts mentioning "Wai Wai" or "CG Foods." Text in English, Nepali, and Hindi/Assamese-region content where feasible. Time window: as far back as platform access allows, minimum trailing 18–24 months to capture at least one price-hike and one competitor-launch event.

Out of scope: private/DM content, paid survey data, primary data collection (interviews), custom model training, real-time streaming (batch/exploratory analysis only).

## Pipeline (5 stages)

1. **Collection** — Scrape/query each platform for posts and comments matching brand keywords. See `DATA_SOURCES.md` for per-platform tools, query strings, and legal/ToS notes.
2. **Cleaning** — Deduplicate, strip emojis/links/HTML, drop non-relevant-language and spam/bot-like content, normalize whitespace and encoding, retain original text alongside cleaned text for auditability.
3. **Classification** — Run sentiment scoring (positive/negative/neutral). Baseline with VADER/TextBlob; validate against a HuggingFace transformer (e.g., a multilingual or Twitter-tuned sentiment model) on a sampled subset. See `METHODOLOGY.md` for the comparison protocol and labeling rubric.
4. **Analysis** — Aggregate sentiment overall, by platform, and over time (weekly/monthly). Overlay known events (price hikes, quality controversies, competitor launches — see `CONTEXT.md`) onto the sentiment timeline to look for correlated dips/spikes. Extract keyword/theme frequency (praise: taste, nostalgia, availability; complaints: price, spice level, packaging, quality) via word clouds or n-gram frequency charts.
5. **Reporting** — Package findings into a report/dashboard (static notebook charts, or a lightweight dashboard) covering: overall sentiment split, sentiment trend over time, sentiment by platform, theme/keyword breakdown, and an events-correlation narrative with recommendations.

## Deliverables

- Cleaned, labeled dataset (raw text + cleaned text + platform + timestamp + sentiment label + score).
- Analysis notebook/scripts (collection → cleaning → classification → visualization).
- Final report/dashboard with the visualizations described above and a written insight section.
- Short methodology note documenting tool choices, sample sizes, and known limitations (see `METHODOLOGY.md`).

## Suggested Phasing

1. Data collection pilot (small sample per platform, validate scraper access and rate limits).
2. Full collection + cleaning pass.
3. Sentiment classification (baseline, then transformer validation on a sample).
4. Exploratory analysis + event correlation.
5. Report/dashboard assembly and write-up.

## Known Constraints to Flag Early

- Twitter/X API access is limited/paid; `snscrape`-style scraping is unreliable against current X rate limiting — plan a fallback (e.g., reduce X's share of the dataset, or use manually exported search results) rather than assuming full historical access.
- YouTube Data API has daily quota limits — batch comment pulls across days if targeting many videos.
- Facebook's public post/comment scraping is heavily restricted since API lockdowns — expect this to be the thinnest data source; treat it as supplementary rather than a primary pillar.
- Language mix (Nepali/Hindi/English, code-mixed) will reduce baseline sentiment-tool accuracy; budget time for the transformer validation step rather than trusting VADER/TextBlob alone on non-English text.
