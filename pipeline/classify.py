"""
Classification stage — pipeline/classify.py

Implements METHODOLOGY.md section 2 ("Sentiment Classification"):

  Baseline pass: VADER, run on every kept (non-excluded) comment.
  Neutral handling: VADER compound score in [-0.05, 0.05] -> neutral,
  per METHODOLOGY.md's explicit threshold example.

  Validation/upgrade pass: a HuggingFace multilingual transformer
  (cardiffnlp/twitter-xlm-roberta-base-sentiment, as suggested in
  METHODOLOGY.md for social text) run on a sampled subset. Compares:
    - Label agreement rate between VADER and the transformer on that sample.
    - Accuracy of each against a gold sample.

  Gold sample: METHODOLOGY.md calls for 50-100 hand-labeled records from a
  human researcher. There is no human labeler in this environment, so this
  script uses the `gold_sentiment` column written by
  pipeline/generate_sample_data.py -- the sentiment each synthetic comment
  was *written* to express -- as a synthetic stand-in, clearly logged as
  such. This is a proxy for pipeline demonstration only; a real deployment
  running on collected (non-synthetic) data would have no gold_sentiment
  column and should have a human researcher hand-label a real gold sample
  per METHODOLOGY.md before trusting the decision rule below.

  Decision rule: if the transformer's accuracy against gold beats VADER's
  by a material margin on the sampled subset, the transformer becomes the
  source of truth (final_label) for that subset; VADER remains the source
  of truth everywhere else (it was not run corpus-wide in the real-world
  cost/latency sense the methodology describes, even though this toy
  corpus is small enough that it technically could be).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection  # noqa: E402

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # noqa: E402

TRANSFORMER_MODEL = "cardiffnlp/twitter-xlm-roberta-base-sentiment"
TRANSFORMER_SAMPLE_FRACTION = 0.5
TRANSFORMER_SAMPLE_MIN = 60
TRANSFORMER_SAMPLE_MAX = 160
AGREEMENT_MARGIN_PP = 5  # percentage points the transformer must beat VADER by

REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "classification_report.json"

# Some sandboxed/restricted networks can reach huggingface.co (API/metadata)
# but fail DNS resolution against the LFS/Xet CDN hosts huggingface_hub
# tries to use internally for the actual weight download, causing it to
# hang or fail even though the file is reachable via a plain HTTP GET on
# huggingface.co's own /resolve/ redirect chain. If the model has already
# been fetched into this local directory (e.g. via `curl -L` straight from
# the /resolve/ URLs), load it from disk and skip huggingface_hub's network
# resolution entirely.
LOCAL_MODEL_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "twitter-xlm-roberta-base-sentiment"
LOCAL_MODEL_REQUIRED_FILES = ["config.json", "pytorch_model.bin", "sentencepiece.bpe.model"]


def _local_model_available() -> bool:
    return LOCAL_MODEL_DIR.is_dir() and all((LOCAL_MODEL_DIR / f).exists() for f in LOCAL_MODEL_REQUIRED_FILES)

_vader = SentimentIntensityAnalyzer()


def vader_label(text_clean: str) -> tuple:
    compound = _vader.polarity_scores(text_clean)["compound"]
    if compound >= 0.05:
        label = "positive"
    elif compound <= -0.05:
        label = "negative"
    else:
        label = "neutral"
    return compound, label


def normalize_transformer_label(raw_label: str) -> str:
    raw = raw_label.lower()
    if "pos" in raw or raw.endswith("_2") or raw == "label_2":
        return "positive"
    if "neg" in raw or raw.endswith("_0") or raw == "label_0":
        return "negative"
    return "neutral"


def load_kept_rows(conn) -> list:
    rows = conn.execute(
        "SELECT id, text_clean, gold_sentiment FROM comments WHERE exclusion_reason IS NULL"
    ).fetchall()
    return [dict(r) for r in rows]


class _TransformerTimeout(Exception):
    pass


def _transformer_worker(model_source: str, local_only: bool, texts: list, result_queue) -> None:
    """Runs in a separate process so it can be killed unconditionally --
    see run_transformer()'s docstring for why that's necessary here.

    Loads the model/tokenizer explicitly rather than passing
    local_files_only straight into pipeline(...): transformers' pipeline
    factory doesn't recognize that kwarg as a loading-time-only param and
    leaks it through to the tokenizer's per-call encode kwargs, which
    crashes with a TypeError at inference time."""
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

    model = AutoModelForSequenceClassification.from_pretrained(model_source, local_files_only=local_only)
    tokenizer = AutoTokenizer.from_pretrained(model_source, local_files_only=local_only)
    clf = pipeline("sentiment-analysis", model=model, tokenizer=tokenizer, truncation=True)
    results = clf(texts, batch_size=16)
    result_queue.put([(r["label"], float(r["score"])) for r in results])


def run_transformer(sample_texts: list, timeout_seconds: int = None) -> list:
    """Returns a list of (label, score) aligned with sample_texts.

    Runs the actual pipeline/inference in a child process with a hard
    wall-clock timeout, then force-kills it if it's still alive. A plain
    in-process signal.alarm() timeout is not reliable here: on a DNS-level
    failure, huggingface_hub's retry/backoff logic can block inside a C
    extension in a way that doesn't yield back to Python's signal handler,
    so it can hang for many minutes regardless of the alarm. Killing the
    OS process is the only fully reliable way to bound this.

    Prefers a pre-fetched local copy of the model (see LOCAL_MODEL_DIR) if
    present, which loads from disk in a few seconds with zero network
    calls; falls back to the normal huggingface_hub download otherwise."""
    import multiprocessing as mp

    use_local = _local_model_available()
    model_source = str(LOCAL_MODEL_DIR) if use_local else TRANSFORMER_MODEL
    if timeout_seconds is None:
        timeout_seconds = 60 if use_local else 40

    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue()
    proc = ctx.Process(target=_transformer_worker, args=(model_source, use_local, sample_texts, result_queue))
    proc.start()
    proc.join(timeout_seconds)

    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        raise _TransformerTimeout(
            f"Transformer pass did not complete within {timeout_seconds}s "
            "(likely unreachable model host); killed subprocess."
        )

    if result_queue.empty():
        raise RuntimeError(f"Transformer subprocess exited (code {proc.exitcode}) without producing results.")

    raw_results = result_queue.get()
    return [(normalize_transformer_label(label), score) for label, score in raw_results]


def main():
    conn = get_connection()
    rows = load_kept_rows(conn)
    if not rows:
        raise RuntimeError("No cleaned rows found. Run pipeline/clean.py first.")

    # --- Baseline pass: VADER on every kept row -----------------------------
    for row in rows:
        compound, label = vader_label(row["text_clean"])
        row["vader_compound"] = compound
        row["vader_label"] = label

    # --- Validation pass: transformer on a sampled subset --------------------
    import random
    rng = random.Random(42)
    sample_size = max(
        TRANSFORMER_SAMPLE_MIN,
        min(TRANSFORMER_SAMPLE_MAX, int(len(rows) * TRANSFORMER_SAMPLE_FRACTION)),
    )
    sample_size = min(sample_size, len(rows))
    sample_rows = rng.sample(rows, sample_size)
    sample_ids = {r["id"] for r in sample_rows}

    transformer_available = True
    try:
        results = run_transformer([r["text_clean"] for r in sample_rows])
    except Exception as exc:  # noqa: BLE001 - genuinely want to degrade gracefully offline
        print(f"[classify.py] Transformer pass unavailable ({exc}); falling back to VADER-only.")
        transformer_available = False
        results = [(None, None)] * len(sample_rows)

    for row, (label, score) in zip(sample_rows, results):
        row["transformer_label"] = label
        row["transformer_score"] = score
        row["transformer_sampled"] = 1

    for row in rows:
        if row["id"] not in sample_ids:
            row["transformer_label"] = None
            row["transformer_score"] = None
            row["transformer_sampled"] = 0

    # --- Comparison protocol (METHODOLOGY.md section 2) ----------------------
    report = {
        "model": TRANSFORMER_MODEL,
        "model_source": "local_cache" if _local_model_available() else "huggingface_hub",
        "transformer_available": transformer_available,
        "total_kept": len(rows),
        "sample_size": sample_size,
        "gold_sample_note": (
            "gold_sentiment is a synthetic template-derived proxy from "
            "generate_sample_data.py, not a human-hand-labeled gold sample. "
            "See METHODOLOGY.md section 2 for the real-data requirement."
        ),
    }

    if transformer_available:
        agree = sum(1 for r in sample_rows if r["vader_label"] == r["transformer_label"])
        report["agreement_rate"] = round(agree / sample_size, 4)

        gold_rows = [r for r in sample_rows if r.get("gold_sentiment")]
        if gold_rows:
            vader_correct = sum(1 for r in gold_rows if r["vader_label"] == r["gold_sentiment"])
            transformer_correct = sum(
                1 for r in gold_rows if r["transformer_label"] == r["gold_sentiment"]
            )
            vader_acc = vader_correct / len(gold_rows)
            transformer_acc = transformer_correct / len(gold_rows)
            report["gold_sample_size"] = len(gold_rows)
            report["vader_accuracy_vs_gold"] = round(vader_acc, 4)
            report["transformer_accuracy_vs_gold"] = round(transformer_acc, 4)

            transformer_wins = (transformer_acc - vader_acc) * 100 >= AGREEMENT_MARGIN_PP
            report["decision"] = "transformer" if transformer_wins else "vader_baseline"
        else:
            report["decision"] = "vader_baseline"
            report["decision_note"] = "No gold-labeled rows in sample; defaulted to baseline."
    else:
        report["decision"] = "vader_baseline"
        report["decision_note"] = "Transformer pass unavailable in this environment."

    # --- Apply decision rule to set final_label / label_source ---------------
    use_transformer = report["decision"] == "transformer"
    for row in rows:
        if use_transformer and row["transformer_label"] is not None:
            row["final_label"] = row["transformer_label"]
            row["label_source"] = "transformer"
        else:
            row["final_label"] = row["vader_label"]
            row["label_source"] = "vader"

    conn.executemany(
        """
        UPDATE comments SET
            vader_compound = :vader_compound,
            vader_label = :vader_label,
            transformer_label = :transformer_label,
            transformer_score = :transformer_score,
            transformer_sampled = :transformer_sampled,
            final_label = :final_label,
            label_source = :label_source
        WHERE id = :id
        """,
        rows,
    )
    conn.commit()
    conn.close()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Classified {len(rows)} rows.")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
