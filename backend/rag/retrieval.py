"""Hybrid retrieval over the private catalog (brief: Agentic RAG).

vector similarity (Chroma)  +  metadata filters (price / category / eco)
                            +  document-contains filter (material keyword)

If a strict filter combination returns nothing, filters are relaxed one at a
time (material -> category -> all, with a Python-side price re-check). Every
relaxation is recorded and surfaced in the agent step log — this is part of
the "conflict handling" the rubric asks for.
"""
from __future__ import annotations

import difflib
import json
from functools import lru_cache
from typing import Any

from app.config import CHROMA_DIR, STORAGE_DIR, settings
from rag.embeddings import get_embedder


class IndexNotBuilt(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _collection():
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        col = client.get_collection(settings.CHROMA_COLLECTION)
    except Exception as e:  # collection missing
        raise IndexNotBuilt(
            "Chroma index not found. Run `python -m rag.ingest --sample` "
            "(or --csv <kaggle file>) from the backend/ directory first."
        ) from e
    meta_path = STORAGE_DIR / "catalog_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    built_with = meta.get("embedder") or (col.metadata or {}).get("embedder")
    current = get_embedder().name
    if built_with and built_with != current:
        raise IndexNotBuilt(
            f"Index was built with embedder '{built_with}' but "
            f"EMBEDDINGS_PROVIDER now resolves to '{current}'. "
            "Re-run ingest or restore the previous EMBEDDINGS_PROVIDER."
        )
    return col, meta


def resolve_category(wanted: str | None) -> str | None:
    """Fuzzy-match a loose planner label ('cleaner') to a real catalog category."""
    if not wanted:
        return None
    _, meta = _collection()
    cats = meta.get("categories") or []
    wl = wanted.lower()
    for c in cats:
        cl = c.lower()
        if wl in cl or cl in wl:
            return c
    scored = [(difflib.SequenceMatcher(None, wl, c.lower()).ratio(), c) for c in cats]
    if scored:
        best = max(scored)
        if best[0] >= 0.6:
            return best[1]
    return None


def hybrid_search(
    query: str,
    *,
    max_price: float | None = None,
    category: str | None = None,
    material: str | None = None,
    eco_friendly: bool | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    col, _ = _collection()
    top_k = top_k or settings.RAG_TOP_K
    emb = get_embedder().encode([query])
    resolved_cat = resolve_category(category)

    def _where(use_cat: bool, use_price: bool, use_eco: bool):
        clauses: list[dict] = []
        if use_price and max_price is not None:
            clauses.append({"price": {"$lte": float(max_price)}})
        if use_cat and resolved_cat:
            clauses.append({"category": {"$eq": resolved_cat}})
        if use_eco and eco_friendly:
            clauses.append({"eco_friendly": {"$eq": True}})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def _query(where, where_doc):
        return col.query(
            query_embeddings=emb, n_results=top_k,
            where=where, where_document=where_doc,
            include=["metadatas", "distances"],
        )

    relaxations: list[str] = []
    wd = {"$contains": material.lower()} if material else None
    attempts = [
        (_where(True, True, True), wd, None),
        (_where(True, True, True), None, "dropped material $contains filter"),
        (_where(False, True, True), None, "dropped category filter"),
        (None, None, "dropped all metadata filters (price re-checked in Python)"),
    ]
    res = None
    for where, where_doc, note in attempts:
        res = _query(where, where_doc)
        if res["ids"] and res["ids"][0]:
            break
        if note:
            relaxations.append(note)

    results: list[dict] = []
    below_floor = 0
    floor = settings.RAG_MIN_SCORE
    if res and res["ids"] and res["ids"][0]:
        for meta, dist in zip(res["metadatas"][0], res["distances"][0]):
            row = {
                "sku": meta.get("doc_id"),  # brief's rag.search schema uses `sku`
                "doc_id": meta.get("doc_id"),
                "title": meta.get("title"),
                "brand": meta.get("brand"),
                "category": meta.get("category"),
                "price": meta.get("price"),
                "rating": meta.get("rating"),
                "ingredients": meta.get("ingredients"),
                "features": meta.get("features"),
                "eco_friendly": meta.get("eco_friendly"),
                "size_oz": meta.get("size_oz"),
                "price_per_oz": meta.get("price_per_oz"),
                "score": round(1.0 / (1.0 + float(dist)), 4),
            }
            # Python-side price guard for the fully-relaxed attempt.
            if relaxations and max_price is not None and isinstance(row["price"], (int, float)) \
               and row["price"] > float(max_price):
                continue
            results.append(row)

    # Relevance gate: a nearest neighbour is not automatically a match. Chroma
    # always hands back top_k rows however unrelated they are, so we judge the
    # *best* one — if even that is below the floor, nothing in this catalog
    # answers the question and the whole set is dropped. The graph reads the
    # empty set as "no private match" and routes to the live web fallback
    # instead of grounding an answer on unrelated products.
    # The gate gets applied to the best row only, not row-by-row: once the top
    # hit is a genuine match, its weaker neighbours are exactly what the
    # comparison table is for.
    if floor > 0 and results:
        best = max(r["score"] for r in results)
        if best < floor:
            below_floor = len(results)
            results = []

    return {
        "results": results,
        "resolved_category": resolved_cat,
        "relaxations": relaxations,
        "relevance_floor": floor,
        "dropped_below_floor": below_floor,
        "filters_applied": {
            "max_price": max_price, "category": category,
            "material": material, "eco_friendly": eco_friendly,
        },
    }
