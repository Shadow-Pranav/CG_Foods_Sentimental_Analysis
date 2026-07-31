# Wai Wai / CG Foods Sentiment Tracker

A small internal analytics tool that tracks public sentiment toward Wai Wai
(CG Foods' flagship instant noodle brand) across YouTube, X/Twitter, and
Facebook, and correlates it against known real-world events (price hikes,
the 2024 quality/health fine, the Current Noodles trademark dispute,
competitor gains, and India market expansion).

Read `PROJECT_INSTRUCTIONS.md`, `CONTEXT.md`, `DATA_SOURCES.md`, and
`METHODOLOGY.md` first -- they define scope, sourcing, and the
cleaning/classification methodology this pipeline implements.

## Quickstart (sample data, no API keys needed)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python pipeline/generate_sample_data.py   # writes data/raw_comments.csv (~320 synthetic rows)
python pipeline/clean.py                  # dedupe/strip/langdetect/spam-filter -> data/sentiment.db
python pipeline/classify.py               # VADER baseline + HuggingFace transformer validation pass
python pipeline/analyze.py                # theme tagging + word-cloud term frequency
python pipeline/event_analysis.py         # chi-square/Fisher's-exact significance per known event

uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000.

Re-running `generate_sample_data.py` → `clean.py` → `classify.py` →
`analyze.py` rebuilds `data/sentiment.db` from scratch each time (clean.py
drops and recreates the `comments` table), so it's safe to iterate.

Note: this environment only has Python 3.9 available (no 3.11), so the
project was built and verified against 3.9. Nothing here uses 3.11-only
syntax, so it should run unmodified on 3.11 too.

## Pipeline stages

| Stage | Script | What it does |
|---|---|---|
| 1. Collection | `pipeline/collect.py` (opt-in, real) / `pipeline/generate_sample_data.py` (synthetic, default) | Produces `data/raw_comments.csv` in the schema from `DATA_SOURCES.md` |
| 2. Cleaning | `pipeline/clean.py` | Dedup (exact + near-dup), strip URLs/HTML/emoji, langdetect, spam heuristics. Writes to `data/sentiment.db` (`comments` table), keeping `text_raw` and excluded rows for auditability |
| 3. Classification | `pipeline/classify.py` | VADER baseline on every kept row + a HuggingFace multilingual transformer (`cardiffnlp/twitter-xlm-roberta-base-sentiment`) on a sampled subset, per `METHODOLOGY.md`'s comparison protocol |
| 4. Analysis | `pipeline/analyze.py` + `pipeline/event_analysis.py` | Keyword/theme tagging from the `CONTEXT.md` seed dictionary + term-frequency word-cloud data per sentiment class; chi-square/Fisher's-exact significance testing of sentiment shifts around each known event |
| 5. Reporting | `app/main.py` + `app/templates/dashboard.html` | FastAPI JSON API + a single dashboard page (Chart.js) |

### Why a synthetic dataset?

Live collection needs a YouTube API key, working X scraping (unreliable
against current rate limits per `DATA_SOURCES.md`), and Facebook access
that barely exists anymore. None of that is available in this environment,
so `pipeline/generate_sample_data.py` stands in for `pipeline/collect.py`:
it produces ~320 rows spanning a 24-month window (2024-07 to 2026-07,
long enough to bracket every event in `CONTEXT.md`), with text templates
that plausibly cluster around the real events (price-hike complaints
around the 2024 and 2025 hikes, quality/health backlash around the KMC
fine, trademark-dispute chatter, competitor comparisons, nostalgia/taste
praise, and an India-expansion positive bump), across YouTube/X/Facebook
in roughly the volume mix `DATA_SOURCES.md` describes (YouTube heaviest,
Facebook thinnest). It also seeds a handful of intentional spam and
duplicate rows so `clean.py`'s dedup/spam filters have real work to do.

Every synthetic row carries a `gold_sentiment` column -- the sentiment the
template was *written* to express. This is **not** a substitute for the
50-100-record human-hand-labeled gold sample `METHODOLOGY.md` calls for;
it's a synthetic-data proxy so `classify.py` can still demonstrate the
VADER-vs-transformer comparison protocol end to end. `classify.py` logs
this caveat explicitly. A real deployment on collected data would have no
`gold_sentiment` column and needs a human researcher to hand-label a real
gold sample before trusting the decision rule.

## Switching to real data collection

1. `cp .env.example .env` and fill in what you have:
   - `YOUTUBE_API_KEY` -- a YouTube Data API v3 key. Free tier is 10,000
     quota units/day (`search.list` costs 100, `commentThreads.list` costs
     1 -- `collect.py` front-loads video discovery then batches comment
     pulls, per `DATA_SOURCES.md`).
   - `TWITTER_TRY_SNSCRAPE=true` to attempt a best-effort `snscrape` pass
     (frequently blocked by X's current defenses -- treat failure as
     expected, not a bug).
   - `TWITTER_MANUAL_EXPORT_CSV` / `FACEBOOK_MANUAL_EXPORT_CSV` -- paths to
     manually exported search results (X web UI) or a CrowdTangle/manual
     Facebook export, per the documented fallbacks in `DATA_SOURCES.md`
     and `PROJECT_INSTRUCTIONS.md`.
2. `pip install -r requirements.txt` already includes `google-api-python-client`
   for the YouTube path.
3. `python pipeline/collect.py` -- writes `data/raw_comments.csv` from
   whichever sources are configured. With none configured it prints setup
   instructions and leaves any existing sample data untouched.
4. Continue with `python pipeline/clean.py`, `classify.py`, `analyze.py` as
   above -- they don't care whether `raw_comments.csv` came from
   `collect.py` or `generate_sample_data.py`.
5. Before trusting `classify.py`'s transformer-vs-baseline decision rule on
   real data, hand-label a real gold sample -- see "Human gold-label
   evaluation" below.

If `pipeline/classify.py` is run somewhere without internet access to
download the transformer model weights (~1.1GB, cached after the first
run), it catches the failure, logs it, and falls back to VADER-only labels
rather than crashing the pipeline. `run_transformer()` wraps the actual
download/inference in a subprocess with a hard wall-clock timeout (not a
plain `signal.alarm`, which doesn't reliably interrupt a hung DNS call)
and kills it if it doesn't finish in time.

**If your network can reach `huggingface.co` but not its LFS/Xet CDN
subdomains** (some sandboxed/restricted networks resolve the main site's
DNS fine but not `cdn-lfs.huggingface.co` specifically, which
`huggingface_hub` uses internally): `classify.py` will also check for a
pre-fetched local copy of the model at
`data/models/twitter-xlm-roberta-base-sentiment/` (see `LOCAL_MODEL_DIR`
in `pipeline/classify.py`) and load straight from disk, skipping
`huggingface_hub`'s network resolution entirely. You can populate that
directory yourself with a plain `curl -L`, which hits `huggingface.co`'s
own `/resolve/main/<file>` redirect chain and works even when the
`hub`-internal resolution path doesn't:

```bash
mkdir -p data/models/twitter-xlm-roberta-base-sentiment
cd data/models/twitter-xlm-roberta-base-sentiment
BASE=https://huggingface.co/cardiffnlp/twitter-xlm-roberta-base-sentiment/resolve/main
for f in config.json sentencepiece.bpe.model special_tokens_map.json pytorch_model.bin; do
  curl -L -C - -o "$f" "$BASE/$f"
done
```
(`-C -` resumes an interrupted download rather than restarting it -- useful
for the ~1.1GB weights file on a slow or flaky connection.)

## Human gold-label evaluation

`classify.py`'s built-in comparison uses `gold_sentiment`, a
template-derived proxy (see above) -- fine for demonstrating the pipeline
mechanics, not a substitute for real human judgment. For an actual
METHODOLOGY.md-compliant evaluation:

```bash
python pipeline/label_gold.py           # interactive: draws a 75-comment sample
                                         # stratified by platform + month, then
                                         # walks you through labeling it
                                         # (p/n/u per comment, saves after every
                                         # answer, resumable, Ctrl+C-safe)
python pipeline/label_gold.py --status  # check progress without labeling
python pipeline/evaluate.py             # scores VADER, the transformer, and
                                         # today's published final_label
                                         # against whatever's labeled so far,
                                         # with a confusion matrix for each,
                                         # written into
                                         # data/classification_report.json
                                         # under "human_gold_evaluation"
```

The sample is written to `data/gold_labels.csv` (tracked in git -- it's
real hand-labeling effort worth keeping, unlike the regenerable
`sentiment.db`/`raw_comments.csv`) rather than a DB column, because
`clean.py` drops and recreates the `comments` table on every pipeline run
and would otherwise silently wipe hand-labeled data. `evaluate.py` re-joins
it against the DB by comment `id` every time it runs, so it always reflects
the current pipeline output.

## Report export

Per PROJECT_INSTRUCTIONS.md section 5 ("Reporting"), a downloadable PDF
snapshot covering overall sentiment split, by-platform, by-region, events
with statistical significance, theme frequency, and named-competitor
mentions -- plus a short narrative summary auto-generated from the actual
numbers (not boilerplate; re-running against different data changes the
text).

```bash
python pipeline/report.py   # writes data/report.pdf
```

Also served live at `GET /api/report`, with a "Download PDF report" button
in the dashboard's top bar. Always reports on the full, unfiltered
dataset -- like `/api/region-comparison` and `/api/event-analysis`, a
report is a fixed snapshot, not a filtered view.

## Statistical significance of event correlation

A visual dip on the sentiment-over-time chart isn't proof the event caused
it -- it could be noise in a small dataset. `pipeline/event_analysis.py`
tests this properly: for each event in `pipeline/events.py`, it compares
the negative-sentiment share in a &plusmn;30-day window before vs. after
the event date with a chi-square test of independence (or Fisher's exact
test when an expected cell count is below 5, the standard threshold for
chi-square validity on small samples).

```bash
python pipeline/event_analysis.py   # writes data/event_analysis.json
```

Also served live at `GET /api/event-analysis` (computed fresh from the
current `comments` table, not read from that file, so it can't go stale).
On the dashboard, the timeline's event markers are dashed by default;
an event with a statistically significant shift (p&lt;0.05) gets a solid
line and a trailing `*` on its label.

On the bundled sample data, only one of the six events clears
significance: the November 2024 KMC quality/health fine (33.3% &rarr;
82.6% negative, p=0.0074). The others show a visible directional shift on
the chart but don't reach significance at this sample size -- which is
the honest, expected result for ~10-25 comments per pre/post window, not
a bug. Treat a non-significant result as "not enough data to tell," not
as "no effect."

## API

All data endpoints accept the same filter query params: `platform`
(comma-separated: `youtube,twitter,facebook`), `sentiment`
(comma-separated: `positive,negative,neutral`), `region` (comma-separated:
`nepal,india,unknown`), `start_date` / `end_date` (`YYYY-MM-DD`), and `q`
(keyword search over both raw and cleaned text). Omitting a param means "no
filter on that dimension"; sending it with an empty value means "match
nothing" (e.g. every checkbox unchecked).

- `GET /api/meta` -- platform/sentiment/region enums, dataset date bounds, total row count.
- `GET /api/summary` -- total comments, sentiment split (counts + %), most-discussed theme, and an `engagement_weighted` block (see below).
- `GET /api/timeline` -- monthly sentiment counts (plain + engagement-weighted) + the known-event markers from `CONTEXT.md`.
- `GET /api/events` -- the known-event list on its own.
- `GET /api/event-analysis` -- unfiltered: for each known event, a &plusmn;30-day pre/post chi-square (or Fisher's exact, for small cells) test of whether the negative-sentiment share actually shifted, not just "looks like it did" on the chart. See "Statistical significance" below.
- `GET /api/by-platform` -- sentiment split per platform.
- `GET /api/by-region` -- sentiment split per region (nepal/india/unknown); see DATA_SOURCES.md's Region Heuristic section for why `unknown` dominates on real data.
- `GET /api/region-comparison` -- unfiltered: for each known event, Nepal vs. India sentiment counts and negative-share in a &plusmn;30-day window around it. Directly answers CONTEXT.md's "does Nepal-sourced sentiment differ from India-sourced sentiment" question.
- `GET /api/themes` -- theme x sentiment counts (CONTEXT.md seed dictionary + competitor/legal extensions).
- `GET /api/competitors` -- mention count and sentiment split per named competitor (Current Noodles, 2PM Noodles, Maggi, Sunfeast Yippee!), plus a monthly trend per competitor mirroring `/api/timeline`'s shape.
- `GET /api/wordcloud` -- top term-frequency data per sentiment class (`pipeline/analyze.py`'s precomputed artifact, not filter-aware). Rendered on the dashboard as three ranked horizontal-bar "top terms" charts (a Chart.js-consistent alternative to a true word cloud, per the no-extra-libraries constraint) rather than as a literal word cloud.
- `GET /api/comments` -- paginated comment table (`page`, `page_size` params too).
- `GET /api/report` -- downloads a PDF snapshot of the full (unfiltered) dataset: overall split, by-platform, by-region, events with statistical significance, theme frequency, competitor mentions, and an auto-generated narrative summary computed from the actual numbers. See "Report export" below.
- `POST /api/chat` -- natural-language question in, answer + the structured API data behind it out. Implemented as Claude tool-calling over the endpoints above (not free-text search, not a second RAG/embedding system) -- see "Chat assistant" below.

### Engagement-weighted sentiment

`/api/summary` and `/api/timeline` both return an engagement-weighted view
alongside the plain unweighted counts (`engagement_weighted` on summary;
`positive_weight`/`negative_weight`/`neutral_weight`/`total_weight` per
point on timeline). Weight is `ln(1 + engagement)` per comment, not a raw
linear sum -- a linear sum lets a handful of viral outliers (one comment
with thousands of likes next to a sea of comments with single digits)
silently dominate the number. `/api/summary`'s
`engagement_weighted.max_single_comment_weight_share_pct` makes whatever
concentration remains after the log transform visible rather than hidden
-- treat the weighted split with caution if that's large relative to the
comment count in the current filter.

## Dashboard

Single page at `/`, plain HTML/CSS/vanilla JS + Jinja2 + Chart.js (loaded
from a CDN `<script>` tag -- no build step, no npm). Top bar has the
project name and a date-range control; the left sidebar has platform,
sentiment, and region checkboxes plus a keyword search box, all wired to
the API filters above and applied dashboard-wide (KPIs, charts, and table
all update together). Main area: a KPI row, the sentiment-over-time chart
with thin dashed vertical lines marking the `CONTEXT.md` events, three
small per-platform donut charts, three small per-region donut charts, a
Nepal-vs-India comparison table around each known event (always unfiltered
-- it's answering a fixed analytical question, not a filtered view), a
theme-frequency bar chart split by sentiment, a named-competitor mentions
chart, three ranked "top terms" bar charts (one per sentiment class), and
a paginated comment table.

## Chat assistant

`POST /api/chat` (dashboard: the "Ask the data" panel at the bottom) lets
you ask a question in plain English -- e.g. "Which platform is most
negative?" or "Was the KMC fine statistically significant?" -- and get
back a natural-language answer plus the structured data behind it.

It's built as Claude tool-calling over the *existing* `/api/*` endpoints,
not a second retrieval system:

- The model (`claude-opus-5`) is given 8 tools, one per read endpoint
  (`summary`, `timeline`, `by_platform`, `by_region`, `themes`,
  `competitors`, `comments`, `events`) with the same filter parameters
  described above. Each tool call runs through FastAPI's `TestClient`
  against the real, running app -- so a chatbot answer is computed by the
  exact same SQL/aggregation code as the charts, never a separate
  implementation that could silently disagree with the dashboard.
- There is deliberately no free-text/vector search over raw comment text
  as a chatbot mechanism. Every number the model cites has to come from
  calling one of the endpoints above, so "how many negative comments on
  YouTube in Nepal" is answered by the same `by_region`/`summary` logic
  a human would get from the sidebar filters, not by the model guessing
  from retrieved snippets.
- The system prompt is built fresh per request from
  `build_label_source_context()` (`app/chat.py`), which reports the
  current `label_source` mix in the database (VADER vs. transformer vs.
  human-corrected, from `pipeline/classify.py`/`pipeline/evaluate.py`).
  This keeps the model from citing VADER-only numbers as if they were the
  transformer-validated or human-gold-checked results from steps 1-2.
- The tool-call loop is capped at 4 round-trips
  (`MAX_TOOL_ITERATIONS` in `app/chat.py`); if the model hasn't reached a
  final answer by then, it's forced to answer (or say it couldn't) with
  whatever it already retrieved rather than looping indefinitely.
- Requires `ANTHROPIC_API_KEY` in `.env` (see `.env.example`). Without it,
  `/api/chat` returns a clean `503` explaining what's missing instead of
  crashing -- the rest of the app works fine with no key configured.

```bash
curl -s http://127.0.0.1:8000/api/chat \
  -H "content-type: application/json" \
  -d '{"question": "Which platform is most negative?"}'
```

## Running tests

```bash
python -m pytest tests/ -v
```

Covers: `pipeline/clean.py`'s dedup/near-dup/spam-filter logic (known
duplicate/near-duplicate/spam/genuine fixtures, asserting the resulting
`exclusion_reason`); region assignment (`pick_region`'s weighted sampling,
`guess_region_from_location_text`'s heuristic); engagement-weighted
sentiment math (`compute_engagement_weighted`, including the log-transform
outlier test and the zero-engagement/negative-engagement edge cases);
event significance testing (`analyze_event`'s chi-square/Fisher's-exact
selection, the +inf-odds-ratio JSON-serialization fix, insufficient-data
handling); `app/main.py`'s API filter combinations (platform +
sentiment + region + date + keyword search together, empty-param-means-
nothing vs. omitted-param-means-no-filter, pagination, ordering, and
`exclusion_reason` never leaking through regardless of filters); and
`app/chat.py`'s tool-calling loop (`tests/test_chat.py`, with a scripted
mock standing in for the Anthropic client) -- tool-call routing through
the real `/api/*` endpoint code, the `MAX_TOOL_ITERATIONS` cap forcing a
final no-tools answer, refusal short-circuiting, empty-filter-value
handling, and the label-source provenance text that gets injected into
the system prompt.

The API tests use FastAPI's `TestClient` against a temp SQLite file seeded
with known rows per test (see `tests/conftest.py`) -- they don't touch
`data/sentiment.db`, so running the suite never disturbs your working
dataset.

## Known limitations (see `METHODOLOGY.md` section 5 for the full list)

- The dataset is synthetic, not real collected data -- treat everything in
  the dashboard as a demonstration of the pipeline mechanics, not an
  actual finding about Wai Wai sentiment.
- Platform sampling is intentionally uneven (YouTube heaviest, Facebook
  thinnest), matching what real collection would look like per
  `DATA_SOURCES.md` -- don't read cross-platform totals as comparable
  sample sizes even in the synthetic data.
- `langdetect` performs poorly on short, romanized Nepali/Hindi text (it
  often misclassifies it as Swahili, Tagalog, Somali, Indonesian, etc.
  rather than `ne`/`hi`) -- this is a real, documented weakness per
  `METHODOLOGY.md`, not a bug in `clean.py`. Rows are tagged and kept
  rather than dropped.
- Event-to-sentiment correlation shown on the timeline is suggestive, not
  causal -- no confounders (seasonality, unrelated news cycles) are
  controlled for, even for the one event (`pipeline/event_analysis.py`)
  that clears statistical significance.
- `region` (nepal/india/unknown) is a best-effort signal, not verified
  geography -- see DATA_SOURCES.md's Region Heuristic section. It's fully
  populated in the synthetic sample data (generated top-down from known
  event/theme context) but would skew heavily toward `unknown` on real
  collected data, since none of YouTube/X/Facebook's public APIs reliably
  expose a commenter's actual location.
- The chat assistant (`/api/chat`) has been tested against the real
  backend/tool-execution path with a mocked Anthropic client (tool-use
  loop, iteration-cap enforcement, refusal handling -- see
  `tests/test_chat.py`) and against the real API with no key configured
  (clean 503). It has **not** been tested end-to-end against the live
  Anthropic API in this environment, since doing so would require
  spending the user's own API credits without asking first -- verify
  this path with your own `ANTHROPIC_API_KEY` before relying on it.
