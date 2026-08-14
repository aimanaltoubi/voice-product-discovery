"""Verify what is actually in the Chroma index — real Amazon data or the
synthetic sample.

The Colab flow could previously fall back to `--sample` on error and leave an
index that looked real. This script answers the question directly by reading
the live collection rather than trusting the build log.

Run from the repo root:  python scripts/verify_index.py
Exit code 0 = real Amazon data indexed, 1 = synthetic/empty/mismatched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.config import CHROMA_DIR, STORAGE_DIR, settings  # noqa: E402


def main() -> int:
    meta_path = STORAGE_DIR / "catalog_meta.json"
    if not meta_path.exists():
        print("FAIL: no catalog_meta.json — the index has never been built.")
        return 1
    meta = json.loads(meta_path.read_text())

    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        col = client.get_collection(settings.CHROMA_COLLECTION)
    except Exception as e:
        print(f"FAIL: collection '{settings.CHROMA_COLLECTION}' not readable: {e}")
        return 1

    live_count = col.count()
    sample = col.get(limit=200, include=["metadatas"])
    ids = [m.get("doc_id", "") for m in sample["metadatas"]]
    n_sample_ids = sum(1 for i in ids if i.startswith("SAMPLE-"))
    n_real_ids = sum(1 for i in ids if i.startswith("AMZ2020-"))
    priced = sum(1 for m in sample["metadatas"] if m.get("price") is not None)
    rated = sum(1 for m in sample["metadatas"] if m.get("rating") is not None)

    source = meta.get("data_source", "unknown")
    is_real = bool(meta.get("is_real_data"))

    bar = "=" * 62
    print(bar)
    print(f"  DATA SOURCE : {'REAL AMAZON DATA' if is_real else 'SYNTHETIC SAMPLE DATA'}")
    print(f"  source_file : {meta.get('source_file')}")
    print(f"  data_source : {source}")
    print(f"  indexed     : {live_count} products (meta says {meta.get('count')})")
    print(f"  embedder    : {meta.get('embedder')}")
    print(f"  slice       : {meta.get('slice')}")
    print(f"  missing     : {meta.get('missing_fields')}")
    print(bar)
    print(f"  first {len(ids)} records: {n_real_ids} AMZ2020-* / {n_sample_ids} SAMPLE-*")
    print(f"  price present: {priced}/{len(ids)}   rating present: {rated}/{len(ids)}")
    print(bar)

    ok = True
    if not is_real:
        print("RESULT: SYNTHETIC SAMPLE DATA is powering the index.")
        ok = False
    elif n_sample_ids:
        print(f"RESULT: MIXED — {n_sample_ids} SAMPLE-* records found in a 'real' index.")
        ok = False
    elif live_count != meta.get("count"):
        print("RESULT: MISMATCH — live collection size differs from catalog_meta.json.")
        ok = False
    else:
        print("RESULT: OK — real Amazon Product Dataset 2020 records are indexed.")

    if meta.get("missing_fields"):
        print(f"NOTE: fields absent from this dataset: {', '.join(meta['missing_fields'])}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
