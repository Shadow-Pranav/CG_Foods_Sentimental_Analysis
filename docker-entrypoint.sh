#!/bin/sh
set -e

# First run (no dataset yet in the mounted ./data volume): build the
# bundled synthetic sample dataset, the same five commands as the
# README's Quickstart. Safe to skip on later runs -- data/sentiment.db
# persists in the volume across container restarts and rebuilds.
if [ ! -f /app/data/sentiment.db ]; then
  echo "No dataset found at data/sentiment.db -- building the sample dataset (first run only)..."
  python pipeline/generate_sample_data.py
  python pipeline/clean.py
  python pipeline/classify.py
  python pipeline/analyze.py
  python pipeline/event_analysis.py
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
