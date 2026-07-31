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
| 4. Analysis | `pipeline/analyze.py` | Keyword/theme tagging from the `CONTEXT.md` seed dictionary + term-frequency word-cloud data per sentiment class |
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

## API

All data endpoints accept the same filter query params: `platform`
(comma-separated: `youtube,twitter,facebook`), `sentiment`
(comma-separated: `positive,negative,neutral`), `start_date` /
`end_date` (`YYYY-MM-DD`), and `q` (keyword search over both raw and
cleaned text). Omitting a param means "no filter on that dimension";
sending it with an empty value means "match nothing" (e.g. every
checkbox unchecked).

- `GET /api/meta` -- platform/sentiment enums, dataset date bounds, total row count.
- `GET /api/summary` -- total comments, sentiment split (counts + %), most-discussed theme.
- `GET /api/timeline` -- monthly sentiment counts + the known-event markers from `CONTEXT.md`.
- `GET /api/events` -- the known-event list on its own.
- `GET /api/by-platform` -- sentiment split per platform.
- `GET /api/themes` -- theme x sentiment counts (CONTEXT.md seed dictionary + competitor/legal extensions).
- `GET /api/wordcloud` -- top term-frequency data per sentiment class (analysis-stage artifact; not rendered in the dashboard UI, but there for the methodology write-up).
- `GET /api/comments` -- paginated comment table (`page`, `page_size` params too).

## Dashboard

Single page at `/`, plain HTML/CSS/vanilla JS + Jinja2 + Chart.js (loaded
from a CDN `<script>` tag -- no build step, no npm). Top bar has the
project name and a date-range control; the left sidebar has platform and
sentiment checkboxes plus a keyword search box, all wired to the API
filters above and applied dashboard-wide (KPIs, charts, and table all
update together). Main area: a KPI row, the sentiment-over-time chart with
thin dashed vertical lines marking the `CONTEXT.md` events, three small
per-platform donut charts, a theme-frequency bar chart split by sentiment,
and a paginated comment table.

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
  controlled for.
