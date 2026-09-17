# Wai Wai / CG Foods Sentiment Tracker

A small internal analytics tool that tracks public sentiment toward Wai Wai
(CG Foods' flagship instant noodle brand) across YouTube, X/Twitter, and
Facebook, and correlates it against known real-world events (price hikes,
the 2024 quality/health fine, the Current Noodles trademark dispute,
competitor gains, and India market expansion).

Read `PROJECT_INSTRUCTIONS.md`, `CONTEXT.md`, `DATA_SOURCES.md`, and
`METHODOLOGY.md` first -- they define scope, sourcing, and the
cleaning/classification methodology this pipeline implements.

## Features

- Five-stage pipeline (collect/generate &rarr; clean &rarr; classify &rarr; analyze &rarr; event-analysis) writing into one PostgreSQL database.
- Dual sentiment classification: VADER baseline + a HuggingFace multilingual transformer, compared per `METHODOLOGY.md`'s protocol.
- Keyword/theme tagging, per-competitor share-of-voice, and word-cloud term frequency.
- Chi-square/Fisher's-exact significance testing of sentiment shifts around known real-world events.
- A single-page dashboard (Chart.js) with platform/sentiment/region/date/keyword filters applied live across every chart and table.
- Downloadable PDF report with an auto-generated narrative summary.
- A pytest suite covering the pipeline logic and the full API filter surface.

## Tech stack

- **Language:** Python 3.9+
- **Backend:** FastAPI, Jinja2 templates, Uvicorn (ASGI server)
- **Data store:** PostgreSQL (`psycopg2`), via `DATABASE_URL` -- `docker-compose.yml` bundles a `db` service so you don't need to install Postgres yourself
- **NLP/classification:** VADER (`vaderSentiment`), HuggingFace `transformers` + `torch` (`cardiffnlp/twitter-xlm-roberta-base-sentiment`), `langdetect`
- **Stats:** `scipy` (chi-square / Fisher's exact)
- **Reporting:** `reportlab` (PDF generation)
- **Frontend:** vanilla HTML/CSS/JS + Chart.js (loaded via CDN, no build step/npm)
- **Testing:** `pytest` + FastAPI's `TestClient` (`httpx`)
- **Optional live collection:** `google-api-python-client` (YouTube Data API v3), `python-dotenv`

## Prerequisites

- Python 3.9 or newer
- A running PostgreSQL server (see "Start Postgres" below -- Docker is the
  easiest route if you don't already have one)
- ~1.5GB free disk (the HuggingFace transformer model is ~1.1GB, cached locally after first download)
- Internet access on the *first* `pipeline/classify.py` run only, to download that model (see "Switching to real data collection" below for offline/restricted-network alternatives)

## Quickstart (sample data, no API keys needed)

Everything below is run from a terminal, inside this project's folder.
No real API keys are required for any of this -- it all works on the
bundled synthetic sample data.

### 1. One-time setup (only do this once)

```bash
# Create an isolated Python environment just for this project
python3 -m venv .venv

# "Enter" that environment (your terminal prompt usually changes to show this)
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install every Python package the project needs
pip install -r requirements.txt
```

### 2. Start Postgres

The pipeline and the app both read/write a PostgreSQL database via the
`DATABASE_URL` env var (default: `postgresql://postgres:postgres@localhost:5432/cg_foods_sentiment`,
see `.env.example`). If you don't already have a Postgres server running
locally, the bundled `docker-compose.yml` will start one for you:

```bash
docker compose up -d db
```

This starts Postgres in the background, persisted in a Docker volume
(`pgdata`) across restarts, and creates the `cg_foods_sentiment` database
automatically. If you're pointing at a Postgres server you manage
yourself instead, `cp .env.example .env` and set `DATABASE_URL` there --
the app only needs the target database to exist; `pipeline/db.py` creates
its own tables on first use.

### 3. Build the dataset (run these five, in this exact order)

Each command feeds the next one -- together they turn raw fake comments
into the labeled, analyzed database the website reads from.

```bash
# Step A: invent ~320 realistic sample comments (YouTube/X/Facebook)
python pipeline/generate_sample_data.py

# Step B: clean them up -- remove duplicates, spam, HTML/links; guess
#         language and region -- and save everything into Postgres
python pipeline/clean.py

# Step C: label every comment as positive / negative / neutral
python pipeline/classify.py

# Step D: tag topics (price, taste, packaging...) and competitor mentions
python pipeline/analyze.py

# Step E: check whether sentiment really shifted around real events
#         (price hikes, the 2024 fine, etc.) using statistics, not guesswork
python pipeline/event_analysis.py
```

After this, all the results live in the `comments` (and `term_frequency`)
tables of your Postgres database.

### 4. Start the website

```bash
uvicorn app.main:app --reload
```

Now open **http://127.0.0.1:8000** in your browser. You should see the
full dashboard with charts, filters, and tables already populated.

To stop the server later, go back to that terminal and press `Ctrl+C`.

### Re-running / starting over

If you want a completely fresh dataset, just re-run step 3's five
commands again in order -- `pipeline/clean.py` drops and recreates the
`comments` table from scratch each time, so it's always safe to repeat.

### Notes

- This was built and tested on **Python 3.9** (the only version
  available in the build environment). Nothing here needs 3.11-only
  features, so it should also run fine on newer Python versions.
- Every command above must be run with the virtual environment active
  (step 1's `source .venv/bin/activate`). If a command says
  `command not found: python` or `uvicorn`, that's almost always why --
  just re-run the `source .venv/bin/activate` line first.

## Pipeline stages

| Stage | Script | What it does |
|---|---|---|
| 1. Collection | `pipeline/collect.py` (opt-in, real) / `pipeline/generate_sample_data.py` (synthetic, default) | Produces `data/raw_comments.csv` in the schema from `DATA_SOURCES.md` |
| 2. Cleaning | `pipeline/clean.py` | Dedup (exact + near-dup), strip URLs/HTML/emoji, langdetect, spam heuristics. Writes to Postgres (`comments` table), keeping `text_raw` and excluded rows for auditability |
| 3. Classification | `pipeline/classify.py` | VADER baseline on every kept row + a HuggingFace multilingual transformer (`cardiffnlp/twitter-xlm-roberta-base-sentiment`) on a sampled subset, per `METHODOLOGY.md`'s comparison protocol |
| 4. Analysis | `pipeline/analyze.py` + `pipeline/event_analysis.py` | Keyword/theme tagging from the `CONTEXT.md` seed dictionary + term-frequency word-cloud data per sentiment class; chi-square/Fisher's-exact significance testing of sentiment shifts around each known event |
| 5. Reporting | `app/main.py` + `app/templates/dashboard.html` | FastAPI JSON API + a single dashboard page (Chart.js) |

## Project structure

```
.
├── app/                      FastAPI app
│   ├── main.py                 Routes + JSON API (reads Postgres via pipeline/db.py)
│   ├── static/                 CSS + vanilla JS for the dashboard
│   └── templates/               dashboard.html (Jinja2)
├── pipeline/                  Data pipeline, run stage by stage
│   ├── generate_sample_data.py  Synthetic data generator (default path)
│   ├── collect.py                Real collection (YouTube/X/Facebook, opt-in)
│   ├── clean.py                   Dedup, strip HTML/URLs/emoji, langdetect, spam filter
│   ├── classify.py                 VADER + transformer sentiment classification
│   ├── analyze.py                   Theme/competitor tagging + word-cloud term frequency
│   ├── event_analysis.py             Chi-square/Fisher's-exact event significance testing
│   ├── report.py                      PDF report generation
│   ├── label_gold.py                   Interactive human gold-labeling CLI
│   ├── evaluate.py                      Scores VADER/transformer against human gold labels
│   ├── events.py                         Known real-world event dates (single source of truth)
│   └── db.py                              PostgreSQL schema + connection helpers (DATABASE_URL)
├── tests/                    pytest suite (see "Running tests")
├── data/                     Generated at runtime (gitignored except gold_labels.csv) --
│                             pipeline output files (raw_comments.csv, reports, the
│                             transformer model cache); the dataset itself lives in Postgres
├── requirements.txt
├── docker-compose.yml        `db` (Postgres) + `app` services
├── Dockerfile / docker-entrypoint.sh
├── .env.example              Copy to .env to configure DATABASE_URL and optional API keys
├── CONTEXT.md / DATA_SOURCES.md / METHODOLOGY.md / PROJECT_INSTRUCTIONS.md / TECHNICAL.md
│                             Scope, sourcing, methodology, and architecture this project implements
└── README.md
```

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
real hand-labeling effort worth keeping, unlike the regenerable Postgres
tables/`raw_comments.csv`) rather than a DB column, because `clean.py`
drops and recreates the `comments` table on every pipeline run and would
otherwise silently wipe hand-labeled data. `evaluate.py` re-joins it
against the DB by comment `id` every time it runs, so it always reflects
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

## Running tests

Tests need a reachable Postgres server -- the same one from "Start
Postgres" above works (`docker compose up -d db`). They run against a
separate `cg_foods_sentiment_test` database (`TEST_DATABASE_URL` env var
to override), created automatically on first run, so the suite never
touches your working `cg_foods_sentiment` dataset.

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
`exclusion_reason` never leaking through regardless of filters).

The API tests use FastAPI's `TestClient` against the scratch Postgres test
database above, reset and seeded with known rows per test (see
`tests/conftest.py`) -- they don't touch your working `cg_foods_sentiment`
dataset, so running the suite never disturbs it.

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

## Deployment

This repo has no CI/CD config -- it's set up for local/internal use, but
does include a `Dockerfile` + `docker-compose.yml` (`db` + `app`
services) for running it as a longer-lived service:

```bash
cp .env.example .env   # fill in optional API keys; DATABASE_URL defaults
                        # to the bundled `db` service, no edit needed
docker compose up -d
```

`docker-entrypoint.sh` waits for Postgres to become reachable, builds the
bundled sample dataset on first run only (tracked via
`data/.pipeline_complete`, since the dataset itself now lives in the `db`
service's own Postgres volume rather than a file under `./data`), then
starts Uvicorn. Re-run the pipeline stages manually (`docker compose exec
app python pipeline/clean.py`, etc.) to refresh the dataset later, or
delete `data/.pipeline_complete` and restart the `app` container to redo
the first-run build.

Without Docker, run it directly against any Postgres server you manage:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000   # no --reload in production
```

Put a reverse proxy (nginx, Caddy, etc.) in front if exposing it beyond
localhost, and run the pipeline (or `pipeline/collect.py` on a schedule)
to refresh the `comments` table before each deploy -- the app only reads
from Postgres, it never writes to it directly (only the pipeline scripts
write).

## Troubleshooting

- **`command not found: python` / `uvicorn`** -- the virtual environment
  isn't active; re-run `source .venv/bin/activate` (step 1).
- **`psycopg2.OperationalError: could not connect to server`** -- Postgres
  isn't running or `DATABASE_URL` points somewhere unreachable. If you're
  using the bundled service, `docker compose up -d db` (and give it a few
  seconds -- check `docker compose ps` shows it `healthy`); otherwise
  double-check `DATABASE_URL` in `.env` against wherever your Postgres
  actually is.
- **Dashboard loads but every chart is empty** -- the pipeline hasn't been
  run yet, or the database has no `comments` table. Run step 3's
  five commands in order.
- **`pipeline/classify.py` hangs or times out on the transformer pass** --
  expected on a restricted network; it falls back to VADER-only labels
  automatically after a wall-clock timeout (see "Switching to real data
  collection" above for the local-model-cache workaround).
- **Port 8000 already in use** -- another `uvicorn` process is still
  running; find and stop it (`lsof -ti:8000 | xargs kill` on macOS/Linux)
  or start this one on a different port (`--port 8001`).
- **Port 5432 already in use** -- another Postgres instance (local install
  or another container) is already bound to it; stop that one, or change
  the host port in `docker-compose.yml`'s `db.ports` and update
  `DATABASE_URL` to match.
- **`ModuleNotFoundError` for a pipeline script** -- make sure you're
  running commands from the project root (not from inside `pipeline/`),
  and that the virtual environment is active.

## Contributing

Internal project, no formal contribution process. If you're changing
pipeline or API behavior: run `python -m pytest tests/ -v` before and
after your change, and update the relevant doc (`METHODOLOGY.md`,
`DATA_SOURCES.md`, or this README) if the change affects what they
describe.

## License

No license file is included in this repository. Treat it as internal/
proprietary to this project unless the repository owner adds one.
