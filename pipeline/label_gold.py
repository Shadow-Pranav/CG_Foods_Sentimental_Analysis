"""
Human gold-labeling CLI — pipeline/label_gold.py

METHODOLOGY.md section 2 calls for a 50-100 record hand-labeled gold sample
to evaluate VADER and the transformer against, rather than relying on
generate_sample_data.py's synthetic gold_sentiment column (which is
template-derived, not human judgment -- see that script's docstring and
README.md's caveat about it).

This script draws a stratified sample of comments (by platform and
year-month, so the gold set isn't accidentally dominated by one platform or
one month's event spike), then walks a human labeler through them one at a
time in the terminal, saving after every single response so nothing is
lost on interrupt.

Output: data/gold_labels.csv (id, platform, timestamp, text_raw, label,
labeled_at). Kept as a separate CSV rather than a DB column on purpose:
pipeline/clean.py drops and recreates the comments table on every pipeline
run (see reset_comments_table in pipeline/db.py), which would silently wipe
hours of human labeling work if it lived in that table. The CSV survives
pipeline re-runs; pipeline/evaluate.py joins it back against the DB by id.

Usage:
    python pipeline/label_gold.py            # interactive labeling session
    python pipeline/label_gold.py --sample-only   # just (re)generate the
                                                    # sample CSV with empty
                                                    # labels, no prompts --
                                                    # useful to inspect the
                                                    # sample or verify this
                                                    # script runs cleanly
                                                    # without labeling
                                                    # anything.
    python pipeline/label_gold.py --status    # print labeling progress and exit
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection  # noqa: E402

SAMPLE_SIZE = 75
GOLD_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "gold_labels.csv"
CSV_FIELDS = ["id", "platform", "timestamp", "text_raw", "label", "labeled_at"]
VALID_LABELS = {"p": "positive", "n": "negative", "u": "neutral"}
RNG_SEED = 7


def load_kept_rows(conn) -> list:
    rows = conn.execute(
        "SELECT id, platform, timestamp, text_raw FROM comments WHERE exclusion_reason IS NULL"
    ).fetchall()
    return [dict(r) for r in rows]


def stratified_sample(rows: list, target_n: int, seed: int = RNG_SEED) -> list:
    """Proportionally allocates target_n across (platform, year-month)
    strata by their share of the full kept dataset, then randomly samples
    within each stratum (largest-remainder rounding so the total is exact)."""
    import random
    from collections import defaultdict

    rng = random.Random(seed)
    strata = defaultdict(list)
    for row in rows:
        key = (row["platform"], row["timestamp"][:7])  # (platform, "YYYY-MM")
        strata[key].append(row)

    total = len(rows)
    raw_allocations = {key: (len(members) / total) * target_n for key, members in strata.items()}
    floor_allocations = {key: int(v) for key, v in raw_allocations.items()}
    remainder = target_n - sum(floor_allocations.values())

    remainders_sorted = sorted(raw_allocations.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True)
    for i in range(remainder):
        key = remainders_sorted[i % len(remainders_sorted)][0]
        floor_allocations[key] += 1

    sample = []
    for key, members in strata.items():
        n = min(floor_allocations.get(key, 0), len(members))
        sample.extend(rng.sample(members, n))

    # Largest-remainder rounding can occasionally land a couple short of
    # target_n if strata run out of members; top up randomly from whatever
    # wasn't already picked.
    if len(sample) < target_n:
        picked_ids = {r["id"] for r in sample}
        remaining = [r for r in rows if r["id"] not in picked_ids]
        rng.shuffle(remaining)
        sample.extend(remaining[: target_n - len(sample)])

    rng.shuffle(sample)
    return sample[:target_n]


def load_existing_csv() -> dict:
    if not GOLD_CSV_PATH.exists():
        return {}
    with open(GOLD_CSV_PATH, newline="", encoding="utf-8") as f:
        return {row["id"]: row for row in csv.DictReader(f)}


def write_csv(rows_by_id: dict) -> None:
    GOLD_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = GOLD_CSV_PATH.with_suffix(".csv.tmp")
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows_by_id.values():
            writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})
    tmp_path.replace(GOLD_CSV_PATH)


def ensure_sample(conn) -> dict:
    """Returns the current gold_labels.csv rows keyed by id, creating the
    stratified sample first if the file doesn't exist yet. Never re-samples
    once the file exists, so a partially-labeled session can be resumed."""
    existing = load_existing_csv()
    if existing:
        return existing

    kept_rows = load_kept_rows(conn)
    if len(kept_rows) < SAMPLE_SIZE:
        print(
            f"Warning: only {len(kept_rows)} kept comments available, "
            f"fewer than the target sample size of {SAMPLE_SIZE}. Sampling all of them."
        )
    sample = stratified_sample(kept_rows, min(SAMPLE_SIZE, len(kept_rows)))

    rows_by_id = {}
    for row in sample:
        rows_by_id[row["id"]] = {
            "id": row["id"],
            "platform": row["platform"],
            "timestamp": row["timestamp"],
            "text_raw": row["text_raw"],
            "label": "",
            "labeled_at": "",
        }
    write_csv(rows_by_id)
    print(f"Drew a new stratified sample of {len(rows_by_id)} comments -> {GOLD_CSV_PATH}")
    return rows_by_id


def print_status(rows_by_id: dict) -> None:
    total = len(rows_by_id)
    labeled = sum(1 for r in rows_by_id.values() if r.get("label"))
    print(f"Gold labeling progress: {labeled}/{total} labeled ({GOLD_CSV_PATH}).")
    if labeled < total:
        print(f"{total - labeled} remaining. Run `python pipeline/label_gold.py` to continue.")


def run_interactive_session(rows_by_id: dict) -> None:
    unlabeled = [r for r in rows_by_id.values() if not r.get("label")]
    if not unlabeled:
        print("All rows in the gold sample are already labeled.")
        print_status(rows_by_id)
        return

    total = len(rows_by_id)
    already = total - len(unlabeled)
    print(f"\nGold-labeling session: {len(unlabeled)} of {total} remaining.")
    print("For each comment, enter:")
    print("  p = positive   n = negative   u = neutral (unclear/mixed/no strong sentiment)")
    print("  s = skip (ask again later)    q = save and quit\n")

    labeled_this_session = 0
    for i, row in enumerate(unlabeled, start=1):
        print("-" * 70)
        print(f"[{already + i}/{total}]  platform={row['platform']}  date={row['timestamp'][:10]}")
        print(f"  {row['text_raw']}")
        while True:
            choice = input("  label (p/n/u/s/q): ").strip().lower()
            if choice == "q":
                write_csv(rows_by_id)
                print(f"\nSaved. {labeled_this_session} labeled this session.")
                print_status(rows_by_id)
                return
            if choice == "s":
                break
            if choice in VALID_LABELS:
                row["label"] = VALID_LABELS[choice]
                row["labeled_at"] = datetime.now(timezone.utc).isoformat()
                write_csv(rows_by_id)  # save immediately, never lose progress
                labeled_this_session += 1
                break
            print("  Not a valid choice -- enter p, n, u, s, or q.")

    print(f"\nDone. {labeled_this_session} labeled this session.")
    print_status(rows_by_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-only", action="store_true",
                         help="Generate/refresh the sample CSV without entering the labeling prompt.")
    parser.add_argument("--status", action="store_true",
                         help="Print labeling progress and exit.")
    args = parser.parse_args()

    conn = get_connection()
    rows_by_id = ensure_sample(conn)
    conn.close()

    if args.status:
        print_status(rows_by_id)
        return
    if args.sample_only:
        print_status(rows_by_id)
        return

    run_interactive_session(rows_by_id)


if __name__ == "__main__":
    main()
