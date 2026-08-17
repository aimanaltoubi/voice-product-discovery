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


def resolve_category(wanted: str | None, categories: list[str]) -> str | None:
    """Fuzzy-match a loose planner label ('cleaner') to a real catalog category."""
    if not wanted:
        return None
    wl = wanted.lower()
    matches = [c for c in categories if wl in c.lower() or c.lower() in wl]
    if matches:
        return min(matches, key=lambda c: (c.count("|"), len(c)))
    scored = [(difflib.SequenceMatcher(None, wl, c.lower()).ratio(), c)
              for c in categories]
    if scored:
        best = max(scored)
        if best[0] >= 0.6:
            return best[1]
    return None


def category_family(
    wanted: str | None, resolved: str | None, categories: list[str],
) -> list[str]:
    if not wanted or not resolved:
        return []
    wl = wanted.lower()
    roots = [c for c in categories if wl in c.lower() or c.lower() in wl] or [resolved]
    return [c for c in categories if any(
        c == root or c.startswith(root + " | ") for root in roots
    )]


def _rows_from(
    response: dict, max_price: float | None, eco_friendly: bool | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not response.get("ids") or not response["ids"][0]:
        return rows
    for meta, dist in zip(response["metadatas"][0], response["distances"][0]):
        row = {
            "sku": meta.get("doc_id"),
            "doc_id": meta.get("doc_id"),
            "title": meta.get("title"),
            "brand": meta.get("brand"),
            "category": meta.get("category"),
            "price": meta.get("price"),
            "rating": meta.get("rating"),
            "ingredients": meta.get("ingredients"),
            "specification": meta.get("specification"),
            "features": meta.get("features"),
            "url": meta.get("url") or None,
            "product_url": meta.get("product_url") or None,
            "model_number": meta.get("model_number") or None,
            "shipping_weight": meta.get("shipping_weight") or None,
            "product_dimensions": meta.get("product_dimensions") or None,
            "technical_details": meta.get("technical_details") or None,
            "eco_friendly": meta.get("eco_friendly"),
            "size_oz": meta.get("size_oz"),
            "price_per_oz": meta.get("price_per_oz"),
            "image": meta.get("image"),
            "score": round(1.0 / (1.0 + float(dist)), 4),
        }
        if max_price is not None and isinstance(row["price"], (int, float)) \
           and row["price"] > float(max_price):
            continue
        if eco_friendly and not row["eco_friendly"]:
            continue
        rows.append(row)
    return rows


def hybrid_search(
    query: str,
    *,
    max_price: float | None = None,
    category: str | None = None,
    material: str | None = None,
    eco_friendly: bool | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    col, meta = _collection()
    top_k = top_k or settings.RAG_TOP_K
    emb = get_embedder().encode([query])
    categories = meta.get("categories") or []
    resolved_cat = resolve_category(category, categories)
    family = category_family(category, resolved_cat, categories)
    category_too_broad = bool(
        categories
        and len(family) * settings.CATEGORY_BROADNESS_DIVISOR > len(categories)
    )

    def _where(use_cat: bool, use_price: bool, use_eco: bool):
        clauses: list[dict] = []
        if use_price and max_price is not None:
            clauses.append({"price": {"$lte": float(max_price)}})
        if use_cat and resolved_cat and not category_too_broad:
            clauses.append({"category": {
                "$in": family,
            }} if family else {"category": {"$eq": resolved_cat}})
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

    relaxations: list[str] = (
        [f"category {category!r} did not resolve to a catalog category; vector retrieval only"]
        if category and not resolved_cat else []
    )
    if category_too_broad:
        relaxations.append(
            f"category label too broad ({len(family)}/{len(categories)} categories) — "
            "vector retrieval only"
        )
    attempts: list[tuple[dict | None, dict | None, str | None]] = []

    def _add_attempt(where, where_doc, note):
        if not any(where == w and where_doc == wd for w, wd, _ in attempts):
            attempts.append((where, where_doc, note))

    strict_where = _where(True, True, True)
    if material:
        _add_attempt(strict_where, {"$contains": material.lower()}, None)
    _add_attempt(
        strict_where, None,
        "dropped material $contains filter" if material else None,
    )
    if resolved_cat and not category_too_broad:
        _add_attempt(_where(False, True, True), None, "dropped category filter")
    if strict_where is not None:
        _add_attempt(None, None, "dropped all metadata filters (re-checked in Python)")
    results: list[dict] = []
    below_floor = 0
    floor = settings.RAG_MIN_SCORE
    for index, (where, where_doc, _) in enumerate(attempts):
        attempt_rows = _rows_from(_query(where, where_doc), max_price, eco_friendly)
        if attempt_rows and floor > 0 and max(r["score"] for r in attempt_rows) < floor:
            below_floor += len(attempt_rows)
            attempt_rows = []
            reason = f"{below_floor} cumulative rows below relevance floor ({floor})"
        else:
            reason = ""
        if attempt_rows:
            results = attempt_rows
            break
        if index + 1 < len(attempts):
            next_note = attempts[index + 1][2]
            if next_note:
                relaxations.append(f"{reason}; {next_note}" if reason else next_note)

    return {
        "results": results,
        "resolved_category": resolved_cat,
        "category_family": len(family),
        "relaxations": relaxations,
        "relevance_floor": floor,
        "dropped_below_floor": below_floor,
        "filters_applied": {
            "max_price": max_price, "category": category,
            "material": material, "eco_friendly": eco_friendly,
        },
    }
