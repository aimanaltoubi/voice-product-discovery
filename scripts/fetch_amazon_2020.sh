#!/usr/bin/env bash
# Download the Amazon Product Dataset 2020 into data/raw/ and print the exact
# ingest command for it.
#
# Credentials: this script never reads, writes, prints, or copies them. The
# Kaggle CLI picks them up itself from ~/.kaggle/kaggle.json (chmod 600) or
# from KAGGLE_USERNAME / KAGGLE_KEY. Nothing here is committed: data/raw/ is
# gitignored, and no credential file is ever created.
#
# Usage:  bash scripts/fetch_amazon_2020.sh
set -euo pipefail
cd "$(dirname "$0")/.."

DATASET="promptcloud/amazon-product-dataset-2020"
RAW_DIR="data/raw"

# Prefer the project venv's CLI so this works without a global install.
KAGGLE_BIN=".venv/bin/kaggle"
[ -x "$KAGGLE_BIN" ] || KAGGLE_BIN="$(command -v kaggle || true)"
if [ -z "$KAGGLE_BIN" ]; then
  echo "ERROR: kaggle CLI not found. Install it with:" >&2
  echo "  .venv/bin/pip install kaggle" >&2
  exit 1
fi

if [ ! -f "$HOME/.kaggle/kaggle.json" ] && [ -z "${KAGGLE_USERNAME:-}" ]; then
  cat >&2 <<'EOF'
ERROR: no Kaggle credentials found.

  Either log in via the browser:
      .venv/bin/kaggle auth login

  or place your API token (Kaggle -> Settings -> API -> Create New API Token):
      mv ~/Downloads/kaggle.json ~/.kaggle/kaggle.json
      chmod 600 ~/.kaggle/kaggle.json

Do NOT commit kaggle.json or place it inside this repository.
EOF
  exit 1
fi

mkdir -p "$RAW_DIR"
echo "[fetch] downloading $DATASET -> $RAW_DIR"
"$KAGGLE_BIN" datasets download -d "$DATASET" -p "$RAW_DIR" --unzip

# The archive nests its payload, so locate the real CSV wherever it landed.
CSV="$(find "$RAW_DIR" -name '*.csv' -type f -print0 \
       | xargs -0 ls -S 2>/dev/null | head -1 || true)"
if [ -z "$CSV" ]; then
  echo "ERROR: no CSV found under $RAW_DIR after download." >&2
  exit 1
fi

echo
echo "[fetch] dataset ready: $CSV"
echo
echo "Now build the real index (from backend/):"
echo
echo "  cd backend && python -m rag.ingest \\"
echo "      --csv \"../$CSV\" \\"
echo "      --max-per-category 500 --require-price --skip-uncategorized --require-real"
echo
echo "Then verify it really is Amazon data:"
echo "  python scripts/verify_index.py"
