"""
Evaluation against the human gold sample — pipeline/evaluate.py

Reads data/gold_labels.csv (produced by pipeline/label_gold.py) and scores
VADER, the transformer, and whichever final_label the pipeline actually
published against real human judgment -- the METHODOLOGY.md-mandated
comparison that pipeline/classify.py could only approximate with a
synthetic gold_sentiment proxy.

Writes a "human_gold_evaluation" section into data/classification_report.json
(merged in alongside classify.py's existing fields, not overwriting them).

Run after at least some rows in data/gold_labels.csv have a label:
    python pipeline/label_gold.py   # human labeling session
    python pipeline/evaluate.py     # score against what's labeled so far
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection  # noqa: E402

GOLD_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "gold_labels.csv"
REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "classification_report.json"
LABELS = ["positive", "negative", "neutral"]


def load_gold_labels() -> dict:
    if not GOLD_CSV_PATH.exists():
        return {}
    with open(GOLD_CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {r["id"]: r["label"] for r in rows if r.get("label")}


def empty_confusion() -> dict:
    return {gold: {pred: 0 for pred in LABELS} for gold in LABELS}


def score(gold_by_id: dict, predicted_by_id: dict) -> dict:
    """predicted_by_id may have None/missing values for some ids (e.g. the
    transformer only ran on a sampled subset) -- those are excluded from
    both the accuracy denominator and the confusion matrix, and counted
    separately so the report is explicit about coverage."""
    confusion = empty_confusion()
    correct = 0
    scored = 0
    skipped_no_prediction = 0

    for comment_id, gold_label in gold_by_id.items():
        pred = predicted_by_id.get(comment_id)
        if not pred or pred not in LABELS:
            skipped_no_prediction += 1
            continue
        scored += 1
        confusion[gold_label][pred] += 1
        if pred == gold_label:
            correct += 1

    accuracy = round(correct / scored, 4) if scored else None
    return {
        "n_scored": scored,
        "n_skipped_no_prediction": skipped_no_prediction,
        "accuracy": accuracy,
        "confusion_matrix": confusion,
        "confusion_matrix_note": "rows=human gold label, columns=predicted label",
    }


def main():
    gold_by_id = load_gold_labels()
    n_total_sampled = 0
    if GOLD_CSV_PATH.exists():
        with open(GOLD_CSV_PATH, newline="", encoding="utf-8") as f:
            n_total_sampled = sum(1 for _ in csv.DictReader(f))

    if not gold_by_id:
        print(
            f"No human-labeled rows found in {GOLD_CSV_PATH}.\n"
            "Run `python pipeline/label_gold.py` to label some, then re-run this script.\n"
            f"(Sample file has {n_total_sampled} rows drawn, 0 labeled.)"
        )
        result = {
            "n_gold_sample_drawn": n_total_sampled,
            "n_gold_labeled": 0,
            "note": "No human labels yet -- run pipeline/label_gold.py first.",
        }
    else:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, vader_label, transformer_label, final_label, label_source "
            "FROM comments WHERE id IN ({})".format(",".join("?" for _ in gold_by_id)),
            list(gold_by_id.keys()),
        ).fetchall()
        conn.close()

        vader_by_id = {r["id"]: r["vader_label"] for r in rows}
        transformer_by_id = {r["id"]: r["transformer_label"] for r in rows}
        final_by_id = {r["id"]: r["final_label"] for r in rows}

        result = {
            "n_gold_sample_drawn": n_total_sampled,
            "n_gold_labeled": len(gold_by_id),
            "vader": score(gold_by_id, vader_by_id),
            "transformer": score(gold_by_id, transformer_by_id),
            "final_label_as_published": score(gold_by_id, final_by_id),
            "note": (
                "transformer.n_skipped_no_prediction counts gold rows the "
                "transformer never scored (it only ran on the "
                "transformer_sampled subset); final_label_as_published is "
                "the actual accuracy of what the dashboard shows today, "
                "mixing whichever source (vader/transformer) won per row."
            ),
        }

        print(f"Scored against {len(gold_by_id)}/{n_total_sampled} human-labeled gold rows.")
        print(f"  VADER accuracy:          {result['vader']['accuracy']}")
        print(f"  Transformer accuracy:    {result['transformer']['accuracy']} "
              f"(n={result['transformer']['n_scored']}, "
              f"{result['transformer']['n_skipped_no_prediction']} not transformer-scored)")
        print(f"  Published final_label:   {result['final_label_as_published']['accuracy']}")

    # Merge into the existing report rather than clobbering classify.py's fields.
    existing = {}
    if REPORT_PATH.exists():
        with open(REPORT_PATH) as f:
            existing = json.load(f)
    existing["human_gold_evaluation"] = result
    with open(REPORT_PATH, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"\nWrote human_gold_evaluation into {REPORT_PATH}")


if __name__ == "__main__":
    main()
