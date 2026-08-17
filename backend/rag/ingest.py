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
    # True ingredient lists only. `Product Specification` used to live here, but
    # it holds shipping weight / ASIN / sales-rank text, which the comparison
    # table renders under an "Ingredients" heading. It feeds `specs` instead:
    # useful as embedding signal, wrong as a displayed ingredient list.
    "ingredients": ["ingredients", "Ingredients"],
    "specs": ["Product Specification", "product_specification", "Technical Details"],
    "technical_details": ["Technical Details", "technical_details"],
    "model_number": ["Model Number", "model_number"],
    "shipping_weight": ["Shipping Weight", "shipping_weight"],
    "product_dimensions": ["Product Dimensions", "product_dimensions"],
    "product_url": ["Product Url", "Product URL", "product_url", "url"],
    "reviews": ["review_snippets", "reviews", "Customer Reviews", "customer_reviews"],
    # Product-level image. The 2020 dump ships a pipe-separated list of Amazon
    # CDN URLs on the same row as the product, so the first URL is provenanced
    # to this exact record — no lookup, no guessing, no third-party image.
    "image": ["image", "Image", "image_url", "Image Url"],
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
    """Parse the Kaggle `Selling Price` column, which is not clean currency.

    Real values seen in the 2020 dump include ranges ("$74.99 - $249.99"),
    space-split cents ("$ 19 99 $39.95"), and rows where Amazon's price-legal
    CSS leaked into the cell. Anchoring on `$` and requiring cents keeps the
    naive "first number anywhere" match from reading "$ 19 99" as 19.0 or
    picking up a stray pixel value out of the embedded stylesheet.
    """
    if value is None:
        return None
    s = str(value).replace(",", "")
    # Cut everything from the first CSS/HTML artefact onward.
    s = re.split(r"[{#]|margin-|text-decoration", s, maxsplit=1)[0]
    # "$ 19 99" -> "$19.99"  (dollars and cents split by whitespace)
    s = re.sub(r"\$\s*(\d{1,5})\s+(\d{2})\b(?!\s*\.)", r"$\1.\2", s)
    # "$ 6 . 94" -> "$6.94"
    s = re.sub(r"\$\s*(\d{1,5})\s*\.\s*(\d{1,2})\b", r"$\1.\2", s)
    prices = [float(m) for m in re.findall(r"\$\s*(\d{1,5}(?:\.\d{1,2})?)", s)]
    if not prices:
        # No currency marker at all — fall back to a bare number.
        m = re.search(r"\b(\d{1,5}(?:\.\d{1,2})?)\b", s)
        return float(m.group(1)) if m else None
    # For a range ("$74.99 - $249.99") the low end is the honest asking price.
    return min(prices)


def _fill_pct(df: pd.DataFrame, col: str) -> float:
    """Percentage of rows where `col` holds actual text.

    Uses fillna() rather than astype(str): under pandas 3's Arrow-backed string
    dtype an all-null column survives astype(str) as NaN (not the literal
    "nan"), so a naive `!= ""` test counts every null row as populated — which
    reported the blank `Brand Name` column as 100% full.
    """
    if col not in df.columns:
        return 0.0
    filled = int((df[col].fillna("").astype("string").str.strip() != "").sum())
    return round(100.0 * filled / max(len(df), 1), 1)


def _first_image(value: str | None) -> str | None:
    """First image URL from the row's pipe-separated list, if it is a real URL.

    Anything that is not a plain http(s) image URL is dropped rather than
    guessed at — an unverifiable image is worse than no image.
    """
    if not value:
        return None
    first = str(value).split("|")[0].strip()
    if not re.match(r"^https?://\S+\.(?:jpg|jpeg|png|webp)(?:\?\S*)?$", first, re.I):
        return None
    return first


def _product_url(value: str | None) -> str | None:
    """Keep only the source row's explicit http(s) product URL."""
    if not value:
        return None
    url = str(value).split("|")[0].strip()
    return url if re.match(r"^https?://\S+$", url, re.I) else None


def _pick(df: pd.DataFrame, field: str) -> str | None:
    """Resolve a target field to a real column.

    Candidates are tried in order, but a column that exists and is *entirely
    empty* is skipped rather than claimed: the Kaggle 2020 file ships a blank
    `Ingredients` column that would otherwise shadow `Product Specification`
    (83.7% populated) purely because it is listed first.
    """
    lower = {c.lower(): c for c in df.columns}
    fallback: str | None = None
    for cand in COLUMN_CANDIDATES[field]:
        col = lower.get(cand.lower())
        if col is None:
            continue
        if _fill_pct(df, col) > 0.0:
            return col
        if fallback is None:
            fallback = col  # remember it, but keep looking for a populated one
    return fallback


def load_and_normalize(
    csv_path: Path,
    category_filter: str | None,
    limit: int | None,
    max_per_category: int | None = None,
    require_price: bool = False,
    skip_uncategorized: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    df = pd.read_csv(csv_path, dtype=str, on_bad_lines="skip", engine="python")
    cols = {f: _pick(df, f) for f in COLUMN_CANDIDATES}
    if not cols["title"]:
        raise SystemExit(f"Could not find a title column in {csv_path.name}. Columns: {list(df.columns)[:15]}")

    # Which target fields this file can actually supply. Reported to the
    # console and stored in catalog_meta.json so a missing field is a recorded
    # fact rather than a silent None (the 2020 dump has no rating column at
    # all, and its Brand Name / Ingredients columns are entirely blank).
    coverage: dict[str, dict] = {}
    for field, col in cols.items():
        if col is None:
            coverage[field] = {"column": None, "fill_pct": 0.0}
        else:
            coverage[field] = {"column": col, "fill_pct": _fill_pct(df, col)}

    def val(row, field):
        c = cols[field]
        v = row.get(c) if c else None
        return None if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).lower() == "nan" else str(v).strip()

    products, reviews = [], []
    per_cat: dict[str, int] = {}
    for _, row in df.iterrows():
        title = val(row, "title")
        if not title:
            continue
        category = val(row, "category") or "Uncategorized"
        if skip_uncategorized and category == "Uncategorized":
            continue
        if category_filter and category_filter.lower() not in (category + " " + title).lower():
            continue
        # A product with no parseable price cannot take part in budget
        # filtering or price comparison — the two things this app is for.
        if require_price and _first_price(val(row, "price")) is None:
            continue
        # Curated slice: cap each top-level category so one section cannot
        # swamp the index. Taking the first N rows of the 2020 dump yields
        # ~77% Toys & Games; capping keeps real cross-category variety.
        if max_per_category:
            top = category.split("|")[0].strip() or "Uncategorized"
            if per_cat.get(top, 0) >= max_per_category:
                continue
            per_cat[top] = per_cat.get(top, 0) + 1
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
        specs = (val(row, "specs") or "")[:600]
        technical_details = (val(row, "technical_details") or "")[:800]
        snippets = (val(row, "reviews") or "")[:800]
        blob = f"{title} {features} {ingredients}".lower()
        size_oz = parse_size_oz(f"{title} {features}")
        products.append({
            "id": doc_id, "doc_id": doc_id, "title": title,
            "brand": val(row, "brand") or "Unknown", "category": category,
            "price": price, "rating": rating, "features": features,
            "ingredients": ingredients, "specs": specs,
            "technical_details": technical_details,
            "model_number": val(row, "model_number") or "",
            "shipping_weight": val(row, "shipping_weight") or "",
            "product_dimensions": val(row, "product_dimensions") or "",
            "product_url": _product_url(val(row, "product_url")),
            # First URL only; must be a plain http(s) image from this row.
            "image": _first_image(val(row, "image")),
            "eco_friendly": is_eco_friendly(blob),
            "size_oz": size_oz,
            "price_per_oz": round(price / size_oz, 4) if price and size_oz else None,
            "review_snippets": snippets,
        })
        for sn in [s.strip() for s in snippets.split("|") if s.strip()]:
            reviews.append({"product_id": doc_id, "stars": rating, "summary": sn})
        if limit and len(products) >= limit:
            break
    return pd.DataFrame(products), pd.DataFrame(reviews), coverage


def build_index(products: pd.DataFrame, provenance: dict | None = None) -> None:
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
        # title + features/description + product details (+ ingredients and any
        # review text when the source actually provides them).
        docs.append(
            f"{p['title']} | {p['brand']} | {p['category']} | "
            f"{p['features']} | {p['ingredients']} | {p.get('specs', '')} | "
            f"{p.get('technical_details', '')} | "
            f"{p['review_snippets']}".lower()[:4000]
        )
        meta = {
            "doc_id": p["doc_id"], "title": p["title"], "brand": p["brand"],
            "category": p["category"], "eco_friendly": bool(p["eco_friendly"]),
            "features": (p["features"] or "")[:400],
            "ingredients": (p["ingredients"] or "")[:300],
            **({"image": p["image"]} if p.get("image") else {}),
        }
        optional_text = {
            "specification": (p.get("specs") or "")[:600],
            "technical_details": (p.get("technical_details") or "")[:800],
            "model_number": p.get("model_number") or "",
            "shipping_weight": p.get("shipping_weight") or "",
            "product_dimensions": p.get("product_dimensions") or "",
            "product_url": p.get("product_url") or "",
        }
        meta.update({k: v for k, v in optional_text.items() if v})
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

    meta = {
        "embedder": embedder.name,
        "count": len(ids),
        "categories": sorted(products["category"].dropna().unique().tolist()),
    }
    meta.update(provenance or {})
    (CHROMA_DIR.parent / "catalog_meta.json").write_text(json.dumps(meta, indent=2))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Build the private-catalog parquet files + Chroma index.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sample", action="store_true", help="use the bundled synthetic sample catalog")
    src.add_argument("--csv", type=Path, help="path to the Kaggle Amazon 2020 CSV")
    ap.add_argument("--category", default=None, help="substring filter, e.g. 'Household' (curated slice)")
    ap.add_argument("--limit", type=int, default=None, help="max products to index")
    ap.add_argument("--max-per-category", type=int, default=None,
                    help="cap products per top-level category (curated slice; keeps variety)")
    ap.add_argument("--require-real", action="store_true",
                    help="final/demo mode: abort unless this run indexes real Amazon data")
    ap.add_argument("--require-price", action="store_true",
                    help="skip rows with no parseable price (they cannot be budget-filtered)")
    ap.add_argument("--skip-uncategorized", action="store_true",
                    help="skip rows whose category column is blank")
    args = ap.parse_args(argv)

    if args.sample and args.require_real:
        raise SystemExit(
            "REFUSING TO BUILD: --require-real was passed with --sample.\n"
            "Final mode expects the real Amazon Product Dataset 2020, not the "
            "synthetic sample catalog. Pass --csv <kaggle file> instead."
        )

    csv_path = DATA_DIR / "sample_products.csv" if args.sample else args.csv
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    print(f"Loading {csv_path} ...", file=sys.stderr)
    products, reviews, coverage = load_and_normalize(
        csv_path, args.category, args.limit, args.max_per_category,
        args.require_price, args.skip_uncategorized,
    )
    if products.empty:
        raise SystemExit("No products matched — check --category / the CSV columns.")

    # ---- data-source provenance -------------------------------------------
    # The Colab flow could previously fall back to the sample catalog on error,
    # leaving an index that looked real. The source is now recorded in
    # catalog_meta.json and printed as an unmissable banner.
    is_sample = bool(args.sample)
    real_ids = int(products["doc_id"].str.startswith("AMZ2020-").sum())
    if not is_sample and real_ids == 0:
        raise SystemExit(
            "REFUSING TO BUILD: --csv was given but no AMZ2020-* ids were produced.\n"
            "The id column did not resolve — check the CSV schema."
        )

    missing = [f for f, c in coverage.items() if c["column"] is None or c["fill_pct"] == 0.0]
    provenance = {
        "data_source": "synthetic-sample" if is_sample else "amazon-product-dataset-2020",
        "is_real_data": not is_sample,
        "source_file": csv_path.name,
        "source_rows_indexed": int(len(products)),
        "slice": {
            "category_filter": args.category,
            "limit": args.limit,
            "max_per_category": args.max_per_category,
            "require_price": args.require_price,
            "skip_uncategorized": args.skip_uncategorized,
        },
        "field_coverage": coverage,
        "missing_fields": missing,
    }

    products.to_parquet(PROCESSED_DIR / "products.parquet", index=False)
    if not reviews.empty:
        reviews.to_parquet(PROCESSED_DIR / "reviews.parquet", index=False)
    print(f"Wrote {len(products)} products -> {PROCESSED_DIR/'products.parquet'}", file=sys.stderr)

    print(f"Building Chroma index with embedder '{settings.EMBEDDINGS_PROVIDER}' ...", file=sys.stderr)
    build_index(products, provenance)

    banner = "SYNTHETIC SAMPLE DATA" if is_sample else "REAL AMAZON DATA"
    bar = "=" * 66
    print(f"\n{bar}\n  DATA SOURCE: {banner}\n"
          f"  {len(products)} products indexed from {csv_path.name}\n{bar}", file=sys.stderr)
    for field in ("rating", "reviews", "brand", "ingredients"):
        c = coverage.get(field) or {}
        if c.get("column") is None:
            print(f"  WARNING: no '{field}' column in this dataset — "
                  f"'{field}' will be absent from every record.", file=sys.stderr)
        elif c.get("fill_pct") == 0.0:
            print(f"  WARNING: column '{c['column']}' (-> {field}) is 100% empty — "
                  f"'{field}' will be absent from every record.", file=sys.stderr)
    print(f"{bar}\nDone. Index at {CHROMA_DIR}", file=sys.stderr)


if __name__ == "__main__":
    main()
