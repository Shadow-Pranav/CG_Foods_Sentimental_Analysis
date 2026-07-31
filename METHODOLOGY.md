# Methodology Notes

## 1. Cleaning Pipeline

Order of operations, applied per record:

1. Deduplicate exact and near-duplicate text (hash normalized text; near-dup via simple similarity threshold to catch copy-pasted spam).
2. Strip URLs, emojis (optionally log emoji sentiment separately before stripping — can be a useful secondary signal), HTML entities, and excess whitespace.
3. Language filter: detect language (e.g., `langdetect` or `fasttext` lid model); drop or bucket separately anything not in English/Nepali/Hindi unless a later phase adds translation.
4. Bot/spam heuristic filter: drop near-identical repeated comments from the same account, comments that are pure emoji/links, and generic ad-spam patterns.
5. Retain both `text_raw` and `text_clean` columns — never overwrite the original, for auditability and reprocessing.

## 2. Sentiment Classification

**Baseline pass:** VADER (English, rule-based, fast) or TextBlob for all English-language records. Flag: both tools are English-tuned and will perform poorly on Nepali/Hindi/code-mixed text — do not report baseline accuracy on non-English subsets without caveat.

**Validation/upgrade pass:** Run a HuggingFace transformer sentiment model on a sampled subset (suggest a multilingual model such as `cardiffnlp/twitter-xlm-roberta-base-sentiment` for social text, or a Nepali/Hindi-capable model if available) and compare against the baseline on:
- Overall label agreement rate (% where VADER/TextBlob and transformer agree).
- A small hand-labeled gold sample (50–100 records, manually tagged positive/negative/neutral by the researcher) — report accuracy of each tool against this gold sample.

**Decision rule:** If the transformer materially outperforms the baseline on the gold sample (especially on non-English text), use it as the source of truth for the final report and clearly note the baseline was a pilot/comparison step, not the final method.

**Neutral handling:** Be explicit about the neutral threshold (e.g., VADER compound score between -0.05 and 0.05) since sentiment tools are sensitive to this cutoff and it affects the reported distribution.

## 3. Keyword / Theme Extraction

- Compute term frequency (post-stopword-removal) and bigrams/trigrams per sentiment class, so "positive theme words" and "negative theme words" are distinguished rather than pooled.
- Seed a small keyword dictionary from `CONTEXT.md` themes (price, spice, packaging, quality, taste, nostalgia, availability) to tag comments thematically in addition to free-form word clouds — this makes it possible to say "38% of negative comments in month X mention price" rather than relying on word clouds alone.

## 4. Visualization Plan

- **Overall sentiment distribution:** stacked bar or donut, positive/negative/neutral split, overall and by platform.
- **Sentiment over time:** weekly or monthly line/area chart of sentiment share, with vertical markers for the known events in `CONTEXT.md` (price hikes, quality controversy, competitor gains).
- **By platform:** small-multiples or grouped bar comparing YouTube vs. Facebook vs. X sentiment splits (call out sample-size differences given Facebook/X collection constraints).
- **Themes:** word cloud or top-N keyword bar chart, split by positive vs. negative comments.

## 5. Limitations to State in the Final Report

- Platform sampling is uneven (Facebook and X data will likely be smaller and less systematic than YouTube) — the report should not claim cross-platform volumes are comparable.
- Sentiment tools have known weaknesses on sarcasm, code-mixed language, and short/slang-heavy comments — treat sentiment labels as directional signal, not ground truth.
- Correlation between sentiment dips and known events is suggestive, not causal — other confounders (seasonality, unrelated news cycles) are not controlled for.
