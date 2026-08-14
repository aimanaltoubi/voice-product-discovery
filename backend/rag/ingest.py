"""Data preprocessing + indexing for the private catalog (brief: Data Section).

Pipeline:  CSV  ->  products.parquet (+ reviews.parquet)  ->  Chroma index

Sources:
  --sample            data/sample_products.csv (synthetic seed catalog, ships
                      with the repo so the app runs out of the box)
  --csv PATH          the real Kaggle "Amazon Product Dataset 2020" CSV
                      (marketing_sample_for_amazon_com-ecommerce_*.csv).
                      Column names are auto-mapped (see COLUMN_CANDIDATES).

Per row we compute:
  - a stable doc_id (kept if present, else AMZ2020-<sha1[:10]> of the source id)
  - size_oz + price_per_oz  (unit normalization: "Normalize units (e.g., price
    per oz) to support fair comparisons")
  - eco_friendly flag (keyword heuristic over title+features+ingredients)
  - the embedding text = title + features + ingredients + review snippets

Run from backend/:
  python -m rag.ingest --sample
  python -m rag.ingest --csv ../data/raw/amazon2020.csv --category "Household" --limit 3000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd

from app.config import CHROMA_DIR, DATA_DIR, PROCESSED_DIR, settings
from rag.embeddings import get_embedder

COLUMN_CANDIDATES: dict[str, list[str]] = {
    "id": ["doc_id", "Uniq Id", "uniq_id", "id", "asin"],
    "title": ["title", "Product Name", "name", "product_name"],
    "brand": ["brand", "Brand Name", "Brand", "manufacturer"],
    "category": ["category", "Category", "Amazon Category and Sub-category"],
    "price": ["price", "Selling Price", "List Price", "selling_price"],
    "rating": ["rating", "Average Rating", "stars", "average_review_rating"],
    "features": ["features", "About Product", "about_product", "Product Description", "description"],
    "ingredients": ["ingredients", "Product Specification", "product_specification"],
    "reviews": ["review_snippets", "reviews", "Customer Reviews", "customer_reviews"],
}

ECO_KEYWORDS = (
    "eco", "plant-based", "plant based", "biodegradable", "non-toxic",
    "nontoxic", "natural", "green seal", "epa safer choice", "vegan",
    "phosphate-free", "sustainab",
)

_ECO_NEG = re.compile(
    r"\b(?:not|isn'?t|no|never|without)\s+(?:\w+[- ]){0,2}?"
    r"(?:eco|plant[- ]based|biodegradable|non[- ]?toxic|natural|vegan|sustainab)",
    re.I,
)


def is_eco_friendly(blob: str) -> bool:
    """Keyword heuristic with basic negation handling.

    'plant-based formula' -> True; 'not plant-based' -> that mention is
    negated and doesn't count. A product is flagged only if at least one
    non-negated eco keyword remains.
    """
    cleaned = _ECO_NEG.sub(" ", blob)
    return any(k in cleaned for k in ECO_KEYWORDS)

_OZ_PER = {"oz": 1.0, "fl oz": 1.0, "floz": 1.0, "ounce": 1.0,
           "ml": 1.0 / 29.5735, "l": 33.814, "liter": 33.814, "litre": 33.814,
           "gal": 128.0, "gallon": 128.0, "qt": 32.0, "quart": 32.0,
           "pt": 16.0, "pint": 16.0, "lb": 16.0, "pound": 16.0}

_SIZE_RE = re.compile(
    r"(?:(\d+)\s*(?:x|pack of|pk of)\s*)?"
    r"(\d+(?:\.\d+)?)\s*(fl\.?\s*oz|floz|oz|ounce|ml|liter|litre|l|gallon|gal|quart|qt|pint|pt|pound|lb)s?\b",
    re.IGNORECASE,
)


def parse_size_oz(text: str) -> float | None:
    """Extract total fluid-ounce size from free text (handles '2 x 16 oz')."""
    if not text:
        return None
    best = None
    for m in _SIZE_RE.finditer(text):
        mult = float(m.group(1)) if m.group(1) else 1.0
        qty = float(m.group(2))
        unit = re.sub(r"[.\s]", "", m.group(3).lower())
        unit = {"floz": "oz", "ounce": "oz", "liter": "l", "litre": "l",
                "gallon": "gal", "quart": "qt", "pint": "pt", "pound": "lb"}.get(unit, unit)
        per = _OZ_PER.get(unit)
        if per:
            oz = round(mult * qty * per, 2)
            best = max(best, oz) if best else oz
    return best


def _first_price(value) -> float | None:
    if value is None:
        return None
    m = re.search(r"(\d{1,5}(?:\.\d{1,2})?)", str(value).replace(",", ""))
    return float(m.group(1)) if m else None


def _pick(df: pd.DataFrame, field: str) -> str | None:
    lower = {c.lower(): c for c in df.columns}
    for cand in COLUMN_CANDIDATES[field]:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def load_and_normalize(csv_path: Path, category_filter: str | None, limit: int | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(csv_path, dtype=str, on_bad_lines="skip", engine="python")
    cols = {f: _pick(df, f) for f in COLUMN_CANDIDATES}
    if not cols["title"]:
        raise SystemExit(f"Could not find a title column in {csv_path.name}. Columns: {list(df.columns)[:15]}")

    def val(row, field):
        c = cols[field]
        v = row.get(c) if c else None
        return None if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).lower() == "nan" else str(v).strip()

    products, reviews = [], []
    for _, row in df.iterrows():
        title = val(row, "title")
        if not title:
            continue
        category = val(row, "category") or "Uncategorized"
        if category_filter and category_filter.lower() not in (category + " " + title).lower():
            continue
        raw_id = val(row, "id") or title
        doc_id = raw_id if raw_id.startswith(("SAMPLE-", "AMZ2020-")) else \
            "AMZ2020-" + hashlib.sha1(raw_id.encode()).hexdigest()[:10]
        price = _first_price(val(row, "price"))
        rating_raw = val(row, "rating")
        rating = None
        if rating_raw:
            m = re.search(r"(\d(?:\.\d)?)", rating_raw)
            rating = min(float(m.group(1)), 5.0) if m else None
        features = (val(row, "features") or "")[:1200]
        ingredients = (val(row, "ingredients") or "")[:800]
        snippets = (val(row, "reviews") or "")[:800]
        blob = f"{title} {features} {ingredients}".lower()
        size_oz = parse_size_oz(f"{title} {features}")
        products.append({
            "id": doc_id, "doc_id": doc_id, "title": title,
            "brand": val(row, "brand") or "Unknown", "category": category,
            "price": price, "rating": rating, "features": features,
            "ingredients": ingredients,
            "eco_friendly": is_eco_friendly(blob),
            "size_oz": size_oz,
            "price_per_oz": round(price / size_oz, 4) if price and size_oz else None,
            "review_snippets": snippets,
        })
        for sn in [s.strip() for s in snippets.split("|") if s.strip()]:
            reviews.append({"product_id": doc_id, "stars": rating, "summary": sn})
        if limit and len(products) >= limit:
            break
    return pd.DataFrame(products), pd.DataFrame(reviews)


def build_index(products: pd.DataFrame) -> None:
    import chromadb

    embedder = get_embedder()
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        client.delete_collection(settings.CHROMA_COLLECTION)
    except Exception:
        pass
    col = client.create_collection(settings.CHROMA_COLLECTION, metadata={"embedder": embedder.name})

    ids, docs, metas = [], [], []
    for _, p in products.iterrows():
        ids.append(p["doc_id"])
        # Embedding text per brief: title + features + selected review snippets
        # (+ ingredients). Stored lowercased so where_document $contains
        # matching is case-insensitive; display fields live in metadata.
        docs.append(
            f"{p['title']} | {p['brand']} | {p['category']} | "
            f"{p['features']} | {p['ingredients']} | {p['review_snippets']}".lower()[:4000]
        )
        meta = {
            "doc_id": p["doc_id"], "title": p["title"], "brand": p["brand"],
            "category": p["category"], "eco_friendly": bool(p["eco_friendly"]),
            "features": (p["features"] or "")[:400],
            "ingredients": (p["ingredients"] or "")[:300],
        }
        for numf in ("price", "rating", "size_oz", "price_per_oz"):
            v = p[numf]
            if v is not None and not pd.isna(v):
                meta[numf] = float(v)
        metas.append(meta)

    B = 64
    for i in range(0, len(ids), B):
        embs = embedder.encode(docs[i : i + B])
        col.add(ids=ids[i : i + B], documents=docs[i : i + B],
                metadatas=metas[i : i + B], embeddings=embs)
        print(f"  indexed {min(i + B, len(ids))}/{len(ids)}", file=sys.stderr)

    (CHROMA_DIR.parent / "catalog_meta.json").write_text(json.dumps({
        "embedder": embedder.name,
        "count": len(ids),
        "categories": sorted(products["category"].dropna().unique().tolist()),
    }, indent=2))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Build the private-catalog parquet files + Chroma index.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sample", action="store_true", help="use the bundled synthetic sample catalog")
    src.add_argument("--csv", type=Path, help="path to the Kaggle Amazon 2020 CSV")
    ap.add_argument("--category", default=None, help="substring filter, e.g. 'Household' (curated slice)")
    ap.add_argument("--limit", type=int, default=None, help="max products to index")
    args = ap.parse_args(argv)

    csv_path = DATA_DIR / "sample_products.csv" if args.sample else args.csv
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    print(f"Loading {csv_path} ...", file=sys.stderr)
    products, reviews = load_and_normalize(csv_path, args.category, args.limit)
    if products.empty:
        raise SystemExit("No products matched — check --category / the CSV columns.")

    products.to_parquet(PROCESSED_DIR / "products.parquet", index=False)
    if not reviews.empty:
        reviews.to_parquet(PROCESSED_DIR / "reviews.parquet", index=False)
    print(f"Wrote {len(products)} products -> {PROCESSED_DIR/'products.parquet'}", file=sys.stderr)

    print(f"Building Chroma index with embedder '{settings.EMBEDDINGS_PROVIDER}' ...", file=sys.stderr)
    build_index(products)
    print("Done. Index at", CHROMA_DIR, file=sys.stderr)


if __name__ == "__main__":
    main()
