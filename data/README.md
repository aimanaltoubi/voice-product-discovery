# Data

## Private catalog source (assignment dataset)

The brief specifies the **Amazon Product Dataset 2020** slice from Kaggle:

> https://www.kaggle.com/datasets/promptcloud/amazon-product-dataset-2020

That dataset is **not redistributed in this repo** (Kaggle datasets carry
their own licenses). The Colab walkthrough downloads it automatically at
run time via `kagglehub` (public dataset, no account needed):

```python
import kagglehub
path = kagglehub.dataset_download("promptcloud/amazon-product-dataset-2020")
```

### Local reproducibility (5 steps)

```bash
# 1) credentials — Kaggle -> Settings -> API -> Create New API Token.
#    NEVER commit kaggle.json or place it inside this repo.
mv ~/Downloads/kaggle.json ~/.kaggle/kaggle.json && chmod 600 ~/.kaggle/kaggle.json
#    (or: .venv/bin/kaggle auth login)

# 2) download into data/raw/ (gitignored). Prints the exact ingest command.
bash scripts/fetch_amazon_2020.sh

# 3) + 4) preprocess to parquet AND build the Chroma index, in one command
cd backend
python -m rag.ingest \
    --csv "../data/raw/<the-kaggle-file>.csv" \
    --max-per-category 500 --require-price --skip-uncategorized --require-real

# 5) verify real data really is what got indexed (exit 0 = real)
cd .. && python scripts/verify_index.py
```

**Flags:**

| Flag | Effect |
|---|---|
| `--max-per-category N` | caps each top-level category. The 2020 dump is 66.6% Toys & Games; without a cap a plain `--limit` yields ~77% toys |
| `--require-price` | skips rows with no parseable price (they cannot be budget-filtered) |
| `--skip-uncategorized` | skips the ~830 rows with a blank category |
| `--require-real` | **final/demo mode** — aborts rather than producing an index that is not real Amazon data |
| `--category SUBSTR` | substring filter on the category column. Note: `"Household"` matches almost nothing here — the relevant slice is `Home & Kitchen` |
| `--limit N` | hard cap on total products |

### Data source is always explicit

`rag.ingest` prints an unmissable banner (`REAL AMAZON DATA` vs `SYNTHETIC
SAMPLE DATA`), records `data_source` / `is_real_data` / `field_coverage` in
`backend/storage/catalog_meta.json`, and the running API reports it at
`GET /api/health` under `catalog`. `--require-real` makes a failed real-data
ingest a hard error instead of a silent fallback to the sample catalog.

### Known gaps in this dataset (verified, not assumed)

The 2020 dump has **no rating column and no review text**, and its
`Brand Name` and `Ingredients` columns are 100% empty. Ingest warns about each
and records them in `missing_fields`. Ratings are not fabricated — anything
depending on "highly rated" degrades to price/semantic ranking.

## Bundled sample (`sample_products.csv`)

A **synthetic** 24-product household-cleaning catalog written for this
project (no Kaggle rows, safe to commit). It exists so the whole pipeline —
including the demo query *"eco-friendly stainless-steel cleaner under
fifteen dollars"* — runs end-to-end before you download anything:

```bash
cd backend
python -m rag.ingest --sample
```

Columns mirror the Kaggle schema the ingester expects: id, title, brand,
category, price, rating, features, ingredients, review snippets (pipe-
separated). Sizes are embedded in titles so the price-per-oz normalization
has something to parse.

## Generated outputs (git-ignored)

- `data/processed/products.parquet` — normalized catalog (doc_id, title,
  brand, category, price, rating, features, ingredients, eco_friendly,
  size_oz, price_per_oz)
- `data/processed/reviews.parquet` — exploded review snippets per doc_id
- `backend/storage/chroma/` — the vector index (embeddings over
  title + features + review snippets)
- `backend/storage/catalog_meta.json` — which embedder built the index
  (retrieval refuses to run against a mismatched index)
