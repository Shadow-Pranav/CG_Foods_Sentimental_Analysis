#!/bin/sh
set -e

# db's healthcheck (docker-compose.yml) already gates container start via
# depends_on, but wait here too in case this is run outside compose.
echo "Waiting for Postgres..."
python - <<'PYEOF'
import os
import sys
import time

import psycopg2

url = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/cg_foods_sentiment")
for _ in range(30):
    try:
        psycopg2.connect(url).close()
        sys.exit(0)
    except psycopg2.OperationalError:
        time.sleep(1)
print("Postgres never became reachable", file=sys.stderr)
sys.exit(1)
PYEOF

# First run (no dataset yet in the mounted ./data volume, tracked by this
# marker file since the dataset itself now lives in Postgres, not a file
# under ./data): build the bundled synthetic sample dataset, the same five
# commands as the README's Quickstart. Safe to skip on later runs -- the
# Postgres data persists in its own volume across container
# restarts/rebuilds.
if [ ! -f /app/data/.pipeline_complete ]; then
  echo "No dataset found -- building the sample dataset (first run only)..."
  python pipeline/generate_sample_data.py
  python pipeline/clean.py
  python pipeline/classify.py
  python pipeline/analyze.py
  python pipeline/event_analysis.py
  touch /app/data/.pipeline_complete
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
