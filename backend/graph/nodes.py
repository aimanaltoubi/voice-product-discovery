"""LangGraph node implementations.

Pipeline: router -> (safety | planner) -> retrieve[rag.search + rerank]
          -> (web_compare -> reconcile | web_fallback | direct) -> answerer.

Design notes
------------
- Nodes are built by `build_nodes(mcp)` so they close over the MCP client;
  all tool access goes through the Model Context Protocol, never by import.
- Every node appends an entry to `steps` (name/input/output/timestamp); the
  frontend's AgentTrace renders the backend-provided labels and details.
- LLM outputs are validated against the Pydantic schemas in state.py and
  then re-checked deterministically (planner rubric enforcement, rerank
  subset check, answer grounding check). The LLM proposes; code verifies.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from graph.llm import call_structured
from graph.prompts import full_prompt, load
from graph.state import (
    AnswerOutput,
    PlanOutput,
    RerankOutput,
    RouterOutput,
)
from mcp_server.client import MCPToolClient

PROMPT_PREVIEW_CHARS = 800
PRICE_DISCREPANCY_RATIO = 0.15

# How many retrieval candidates the LLM reranker actually sees. Retrieval asks
# rag.search for 30 so the deterministic filters have room; the reranker gets
# only the strongest slice of that.
RERANK_SHORTLIST = 10


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def step(name: str, input: Any, output: Any, *, label: str = "", detail: str = "") -> dict:
    """One Agent Trace entry.

    `name`/`input`/`output` are the raw record the debug view still renders.
    `label`/`detail` are the human-readable summary the Agent Trace shows; both
    are derived from real node data by the caller — never hard-coded copy.
    """
    return {
        "name": name,
        "stage": name,
        "label": label or name,
        "status": "complete",
        "detail": detail,
        "input": input,
        "output": output,
        "timestamp": _now(),
    }


# --- grounded match reasons ------------------------------------------------
# A chip may only be emitted when the retrieved record itself supports it:
# either a structured metadata field (price) or a literal phrase present in the
# product's own text. Synonyms are listed explicitly so that the phrase shown on
# screen is always one that actually occurs in the evidence — the LLM never
# writes these strings.
_FEATURE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "easy to wash": ("machine washable", "machine wash", "easy care", "washable", "easy to wash"),
    "easy to clean": ("easy to clean", "wipe clean", "easy care", "machine washable"),
    "soft": ("ultra soft", "super soft", "extra soft", "softness", "soft"),
    "compact": ("compact", "space saving", "space-saving", "foldable", "mini"),
    "lightweight": ("lightweight", "light weight"),
    "durable": ("durable", "heavy duty", "heavy-duty", "long lasting", "tear resistant"),
    "comfortable": ("comfortable", "comfort", "cozy"),
    "breathable": ("breathable",),
    "hypoallergenic": ("hypoallergenic",),
    "waterproof": ("waterproof", "water resistant", "water-resistant"),
    "warm": ("warm", "all season", "all-season"),
    "quiet": ("quiet", "low noise", "noiseless"),
    "portable": ("portable", "travel"),
}


# --- search-session refinement ---------------------------------------------
# A refinement is not a new search: the utterance ("make it machine washable")
# is parsed by the same Router, then merged onto the constraints the session
# already carries. Structured scalars are replaced by the latest explicit
# value; qualitative preferences accumulate. The merged set then drives a fresh
# full-catalog retrieval — refinement never filters the previous result list.

# Latest explicit value wins for these — "under $40" must replace "under $50",
# not intersect with it.
_REPLACE_FIELDS = (
    "product_type", "budget", "size", "audience", "use_case",
    "brand", "category", "material", "eco_friendly",
)
_PRODUCT_DEPENDENT_FIELDS = (
    "category", "brand", "material", "size", "use_case",
    "eco_friendly", "qualitative_features",
)


def merge_constraints(prior: dict | None, new: dict | None) -> dict:
    prior = prior or {}
    new = new or {}
    merged = dict(prior)
    pivot = (
        prior.get("product_type") and new.get("product_type")
        and _norm(str(prior["product_type"])) != _norm(str(new["product_type"]))
    )
    if pivot:
        # A new product invalidates product-specific constraints from the old one;
        # budget and audience describe the shopper and remain useful.
        for field in _PRODUCT_DEPENDENT_FIELDS:
            merged.pop(field, None)
    for field in _REPLACE_FIELDS:
        value = new.get(field)
        if value not in (None, "", []):
            merged[field] = value
    # A generic refinement may mention "toy" while the retained product type is
    # still "puzzle"; keep its matching category unless product_type changed too.
    if merged.get("product_type") == prior.get("product_type") and prior.get("category"):
        merged["category"] = prior["category"]
    feats = list(merged.get("qualitative_features") or [])
    seen = {str(f).strip().lower() for f in feats}
    for f in new.get("qualitative_features") or []:
        key = str(f).strip().lower()
        if key and key not in seen:
            feats.append(f)
            seen.add(key)
    merged["qualitative_features"] = feats
    return merged


def diff_constraints(prior: dict | None, merged: dict) -> list[str]:
    """Human-readable description of what this refinement changed."""
    prior = prior or {}
    changes: list[str] = []
    for field in _REPLACE_FIELDS:
        before, after = prior.get(field), merged.get(field)
        if after in (None, "") or before == after:
            continue
        label = field.replace("_", " ").capitalize()
        if field == "budget":
            changes.append(f"Budget: {_money(before)} → {_money(after)}" if before
                           else f"Budget: ≤ {_money(after)}")
        elif before:
            changes.append(f"{label}: {before} → {after}")
        else:
            changes.append(f"{label}: {after}")
    before_f = {str(f).lower() for f in (prior.get("qualitative_features") or [])}
    added = [f for f in (merged.get("qualitative_features") or [])
             if str(f).lower() not in before_f]
    if added:
        changes.append("Added: " + ", ".join(added))
    return changes


def compose_query(constraints: dict, fallback: str = "") -> str:
    """Flatten the accumulated session into the semantic query for rag.search.

    Retrieval must see the whole intent, not just the latest utterance — a
    refinement like "make it machine washable" is meaningless as a search on
    its own, and the best product may sit outside the previous top 3.
    """
    c = constraints or {}
    parts = [c.get("product_type"), c.get("size"), c.get("material")]
    parts += list(c.get("qualitative_features") or [])
    if c.get("audience"):
        parts.append(f"for {c['audience']}")
    if c.get("use_case"):
        parts.append(f"for {c['use_case']}")
    if c.get("brand"):
        parts.append(str(c["brand"]))
    query = " ".join(str(p).strip() for p in parts if p)
    return query or fallback


# --- broad-query clarification --------------------------------------------
# One question, asked only when the request names a product but carries almost
# nothing to rank on. Deterministic so the behaviour is explainable and testable
# rather than depending on the model's mood.
_MIN_SIGNALS_TO_PROCEED = 1


def clarification_needed(router: dict) -> str | None:
    """Return a single clarifying question, or None to proceed with retrieval."""
    c = router.get("constraints") or {}
    if not c.get("product_type"):
        return None                      # nothing concrete to ask about
    if router.get("needs_live") or router.get("safety_flags"):
        return None                      # a live/price check is already specific
    signals = [
        c.get("budget"), c.get("audience"), c.get("use_case"), c.get("size"),
        c.get("brand"), c.get("material"),
        *(c.get("qualitative_features") or []),
    ]
    if sum(1 for s in signals if s not in (None, "", [])) >= _MIN_SIGNALS_TO_PROCEED:
        return None                      # specific enough to rank responsibly
    product = str(c["product_type"]).strip()
    return (f"Happy to help you find {'an' if product[:1].lower() in 'aeiou' else 'a'} "
            f"{product}. Who is it mainly for, and how will you use it?")


# --- audience-appropriate ranking ------------------------------------------
# Kid-targeted products are demoted (never removed) when the shopper gave no
# child signal. Applied only when the product's own text carries an explicit
# audience marker, so it cannot misfire on a category where these words are
# meaningless.
_CHILD_MARKERS = (
    "kids", "kid's", "children", "children's", "toddler", "baby", "infant",
    "nursery", "preschool", "boys", "girls", "juvenile", "for ages",
)
_CHILD_REQUEST = re.compile(
    r"\b(kid|kids|child|children|toddler|baby|infant|son|daughter|nursery|"
    r"preschool|school|boy|girl|year[- ]old|grandchild|niece|nephew)\b", re.I)


def wants_child_products(transcript: str, constraints: dict) -> bool:
    audience = (constraints.get("audience") or "").lower()
    if any(w in audience for w in ("child", "kid", "toddler", "baby", "infant", "teen")):
        return True
    if audience in ("adult", "myself", "me", "men", "women"):
        return False
    return bool(_CHILD_REQUEST.search(transcript or ""))


def _is_child_product(row: dict) -> bool:
    return any(m in _evidence_blob(row) for m in _CHILD_MARKERS)


def demote_child_products(rows: list[dict], transcript: str, constraints: dict) -> tuple[list[dict], int]:
    """Stable-partition kid-targeted rows to the end. Returns (rows, n_demoted)."""
    if not rows or wants_child_products(transcript, constraints):
        return rows, 0
    general = [r for r in rows if not _is_child_product(r)]
    child = [r for r in rows if _is_child_product(r)]
    if not child or not general:
        return rows, 0          # nothing to reorder, or the whole category is kids'
    return general + child, len(child)


def _evidence_blob(row: dict) -> str:
    """The literal text a chip is allowed to cite."""
    return " ".join(
        str(row.get(k) or "")
        for k in ("title", "features", "ingredients", "specification", "category")
    ).lower()


def _find_phrase(blob: str, candidates: tuple[str, ...] | str) -> str | None:
    """Return the first candidate phrase literally present in `blob`."""
    if isinstance(candidates, str):
        candidates = (candidates,)
    for phrase in candidates:
        if phrase and phrase.lower() in blob:
            return phrase
    return None


def _titlecase(phrase: str) -> str:
    return phrase[:1].upper() + phrase[1:]


# --- private <-> live product matching -------------------------------------
# Full-string similarity fails here: a 130-char Amazon catalog title and a
# 55-char search-result title describe the same product but score ~0.37 purely
# on length mismatch. Matching on distinctive tokens instead — the words that
# actually identify a product ("prima ballerina") rather than the ones every
# row in the category shares ("comforter", "set", "twin").
_SITE_BOILERPLATE = {
    "amazon", "amazoncom", "com", "www", "walmart", "target", "ebay", "wayfair",
    "overstock", "home", "kitchen", "buy", "online", "shop", "sale", "deals",
    "best", "price", "prices", "free", "shipping", "official", "site", "store",
    "reviews", "review", "new", "the", "and", "for", "with", "your",
}
# Category/spec words: real, but shared by every product in the category, so
# overlap on these alone is not evidence that two rows are the same product.
_GENERIC_PRODUCT_TOKENS = {
    "comforter", "comforters", "set", "sets", "bed", "bedding", "sheet", "sheets",
    "quilt", "duvet", "cover", "blanket", "pillow", "sham", "shams", "bag",
    "backpack", "bottle", "water", "piece", "pieces", "pc", "pcs", "count",
    "twin", "full", "queen", "king", "xl", "size", "sized", "inch", "inches",
    "oz", "ounce", "pack", "kids", "kid", "boys", "girls", "adult", "teen",
    "soft", "ultra", "super", "season", "all", "machine", "washable", "wash",
    "microfiber", "polyester", "cotton", "alternative", "lightweight", "warm",
    "color", "colors", "multi", "collection", "style", "design", "quality",
}


def _tokens(text: str) -> set[str]:
    raw = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {t for t in raw if len(t) >= 3 and t not in _SITE_BOILERPLATE}


def match_products(catalog_title: str, catalog_brand: str | None,
                   web_title: str) -> tuple[bool, dict]:
    """Decide whether a live result is the same product as a catalog row.

    Returns (is_match, evidence) where evidence explains the decision so the
    step log can show *why* two rows were linked rather than a bare number.
    """
    # "Unknown" is the ingest placeholder for this dataset's empty brand column;
    # treating it as a token would match every brandless row to every other.
    brand = (catalog_brand or "").strip().lower()
    brand_text = brand if brand and brand != "unknown" else ""

    a = _tokens(f"{catalog_title} {brand_text}")
    b = _tokens(web_title)
    if not a or not b:
        return False, {"reason": "no comparable tokens"}

    shared = a & b
    distinctive = shared - _GENERIC_PRODUCT_TOKENS
    containment = len(shared) / min(len(a), len(b))

    # Two distinctive tokens in common, or one plus strong containment of the
    # shorter title. Generic-only overlap never qualifies.
    is_match = len(distinctive) >= 2 or (len(distinctive) == 1 and containment >= 0.6)
    return is_match, {
        "shared_tokens": sorted(shared),
        "distinctive_tokens": sorted(distinctive),
        "containment": round(containment, 2),
    }


def _money(v: Any) -> str:
    """'$50' rather than '$50.00' — chips read better without dead cents."""
    if not isinstance(v, (int, float)):
        return "—"
    return f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"


def _display_size(phrase: str) -> str:
    """Title-case a size token, keeping XL-style suffixes upper ('twin/twin xl')."""
    words = []
    for w in phrase.split():
        words.append(w.upper() if w.lower() in ("xl", "xxl", "xs") else
                     "/".join(p[:1].upper() + p[1:] for p in w.split("/")))
    return " ".join(words)


def match_evidence(row: dict, constraints: dict) -> dict:
    """Build evidence-backed "why this product" chips for one retrieved row.

    Returns {match_reasons, matched_constraints, supported_constraints}.
    `supported_constraints` counts only the criteria we can actually check
    against this dataset, so an unverifiable preference is never silently
    scored as a match.
    """
    blob = _evidence_blob(row)
    reasons: list[str] = []
    supported = 0
    matched = 0

    # HARD: budget — verified against structured price metadata, not text.
    budget = constraints.get("budget")
    price = row.get("price")
    if isinstance(budget, (int, float)):
        supported += 1
        if isinstance(price, (int, float)) and price <= budget:
            matched += 1
            reasons.append(f"Under {_money(budget)}")

    # HARD: size — only claimed when the product text says so literally.
    size = constraints.get("size")
    if size:
        supported += 1
        hit = _find_phrase(blob, str(size).lower())
        if hit:
            matched += 1
            # Prefer a richer literal form when the record has one ("twin/twin xl").
            m = re.search(rf"\b[\w/]*{re.escape(hit)}[\w/]*(?:\s+x{{1,2}}l)?\b", blob)
            reasons.append(_display_size((m.group(0) if m else hit).strip()))

    # HARD: brand — only when the user asked for one.
    brand = constraints.get("brand")
    if brand:
        supported += 1
        if _find_phrase(blob, str(brand).lower()):
            matched += 1
            reasons.append(_titlecase(str(brand)))

    # SOFT: qualitative features — chip text is the phrase found in the record.
    for feat in constraints.get("qualitative_features") or []:
        f = str(feat).strip().lower()
        if not f:
            continue
        supported += 1
        hit = _find_phrase(blob, _FEATURE_SYNONYMS.get(f, (f,)))
        if hit:
            matched += 1
            reasons.append(_titlecase(hit))

    # De-duplicate while preserving order.
    seen: set[str] = set()
    deduped = [r for r in reasons if not (r.lower() in seen or seen.add(r.lower()))]
    return {
        "match_reasons": deduped,
        "matched_constraints": matched,
        "supported_constraints": supported,
    }


# Colorway variants share everything but the tail of the title:
#   "Heritage Club Ultra Soft – Sierra – … – Alternative Microfiber, Mint"
#   "Heritage Club Ultra Soft – Sierra – … – Alternative Microfiber – –, Purple"
# Same product, same price, two doc_ids. Matching on the leading words plus the
# price collapses them without touching genuinely different products, which
# diverge long before the eighth word.
_VARIANT_KEY_WORDS = 8


def dedupe_listings(rows: list[dict]) -> list[dict]:
    """Keep one row per product, dropping same-price colorway variants.

    Rows arrive in retrieval order, so the first occurrence is the strongest
    match. Without this a shopper sees the same comforter in two colors filling
    two of three comparison slots.
    """
    seen: set[tuple[str, Any]] = set()
    out: list[dict] = []
    for row in rows:
        head = " ".join(_norm(str(row.get("title") or "")).split()[:_VARIANT_KEY_WORDS])
        key = (head, row.get("price"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def rank_grounded(rows: list[dict]) -> list[dict]:
    """Prefer verified constraint matches, then the private retrieval score."""
    return sorted(
        rows,
        key=lambda row: (
            -(row.get("matched_constraints") or 0),
            -(row.get("score") or 0),
        ),
    )


def top_up_ranked(
    shortlist: list[dict], ranked_doc_ids: list[str], want: int,
) -> tuple[list[dict], int, list[str]]:
    if not ranked_doc_ids:
        return [], 0, []
    by_id = {row["doc_id"]: row for row in shortlist}
    ranked: list[dict] = []
    seen: set[str] = set()
    for doc_id in ranked_doc_ids:
        if doc_id in by_id and doc_id not in seen:
            ranked.append(by_id[doc_id])
            seen.add(doc_id)
        if len(ranked) == want:
            break
    before = len(ranked)
    for row in shortlist:
        if len(ranked) == want:
            break
        if row["doc_id"] not in seen:
            ranked.append(row)
            seen.add(row["doc_id"])
    return ranked, len(ranked) - before, [d for d in ranked_doc_ids if d not in by_id]


def live_price_missing(needs_live: bool, reconciliation: dict | None, doc_id: str) -> bool:
    match = ((reconciliation or {}).get("matches") or {}).get(doc_id) or {}
    return bool(needs_live and reconciliation is not None
                and not isinstance(match.get("web_price"), (int, float)))


def _preview(prompt: str) -> str:
    return prompt if len(prompt) <= PROMPT_PREVIEW_CHARS else prompt[:PROMPT_PREVIEW_CHARS] + " …[truncated]"


def _slim(row: dict, *, keep_features: int = 220) -> dict:
    """Reduce a catalog/web row to the fields the LLM and UI need."""
    out = {
        "doc_id": row.get("doc_id"),
        "sku": row.get("sku"),
        "title": row.get("title"),
        "brand": row.get("brand"),
        "price": row.get("price"),
        "rating": row.get("rating"),
        # Retrieval rank is evidence too: without it the reranker cannot tell
        # the vector-similarity winner from the 19th-best row.
        "score": row.get("score"),
        "price_per_oz": row.get("price_per_oz"),
        "eco_friendly": row.get("eco_friendly"),
        "ingredients": (row.get("ingredients") or None),
        "specification": (row.get("specification") or None),
        "features": (row.get("features") or "")[:keep_features] or None,
    }
    # NB: `image` is deliberately NOT included — _slim feeds the rerank and
    # answerer prompts, and a CDN URL there is dead tokens plus something the
    # model could try to cite. The UI reads images off the full retrieval row.
    if row.get("url"):
        out["url"] = row["url"]
    if row.get("availability"):
        out["availability"] = row["availability"]
    return {k: v for k, v in out.items() if v is not None or k in ("price", "rating", "brand")}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


_QUERY_STOPWORDS = {
    "recommend", "find", "show", "need", "want", "give", "buy", "price",
    "compare", "best", "top", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
    "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty",
    "ninety", "hundred", "thousand",
    "what", "which", "where", "when", "how", "who", "you", "your", "have",
    "has", "any", "some", "option", "options", "looking", "look", "does",
    "current", "latest", "right", "now", "less", "than", "under", "below", "dollar", "dollars",
    "this", "still", "sold", "available", "being", "stock", "restock", "restocked",
    "discontinued", "today", "these", "days", "date", "product", "products",
    "for", "with", "the", "and", "kids", "kid", "child", "children", "mom",
    "mother", "dad", "father", "boy", "boys", "girl", "girls",
    "year", "years", "old", "please", "me",
    "kit", "model", "set",
}

_MATERIAL_PATTERNS = {
    "stainless steel": re.compile(r"\bstainless[- ]steel\b", re.I),
    "steel": re.compile(r"\bsteel\b", re.I),
    "wood": re.compile(r"\b(?:wood|wooden)\b", re.I),
    "glass": re.compile(r"\bglass\b", re.I),
    "plastic": re.compile(r"\bplastic\b(?!\s*[- ]?free\b)", re.I),
    "ceramic": re.compile(r"\bceramic\b", re.I),
    "silicone": re.compile(r"\bsilicone\b", re.I),
    "aluminum": re.compile(r"\b(?:aluminum|aluminium)\b", re.I),
    "non-toxic": re.compile(r"\bnon[- ]?toxic\b", re.I),
}
_MATERIAL_NEGATION_RE = re.compile(
    r"\b(?:no|without|avoid(?:ing)?|free\s+of)(?:\s+\w+){0,2}\s*$", re.I,
)
def _material_match(pattern: re.Pattern, text: str) -> re.Match | None:
    for match in pattern.finditer(text):
        if _MATERIAL_NEGATION_RE.search(text[max(0, match.start() - 24):match.start()]):
            continue
        if re.match(r"\s*[- ]?free\b", text[match.end():], re.I):
            continue
        return match
    return None


def _extract_material(text: str) -> str | None:
    return next((name for name, pattern in _MATERIAL_PATTERNS.items()
                 if _material_match(pattern, text)), None)


def _excluded_materials(text: str) -> set[str]:
    excluded = set()
    for material in _MATERIAL_PATTERNS:
        if material == "non-toxic":
            continue
        term = re.escape(material).replace(r"\ ", r"[- ]")
        if re.search(
            rf"\b(?:{term}[- ]?free|no\s+{term}|without\s+{term}|"
            rf"free\s+of\s+{term}|avoid(?:ing)?\s+{term})\b",
            text, re.I,
        ):
            excluded.add(material)
    return excluded


def _explicitly_excludes_material(row: dict, material: str) -> bool:
    evidence = " ".join(str(row.get(k) or "") for k in ("title", "features", "specification"))
    return material in _excluded_materials(evidence)


def _query_terms(query: str) -> list[str]:
    for pattern in _MATERIAL_PATTERNS.values():
        query = pattern.sub(" ", query)
    return [
        term for term in re.findall(r"[a-z0-9]+", query.lower())
        if len(term) >= 3 and not term.isdigit() and term not in _QUERY_STOPWORDS
    ]


def _all_query_terms_present(phrase: str, text: str) -> bool:
    wanted = set(_query_terms(phrase))
    return bool(wanted) and wanted <= set(_query_terms(text))


def _lexical_anchor_terms(query: str, rows: list[dict]) -> set[str]:
    terms = set(_query_terms(query))
    present = {
        term for term in terms
        if any(re.search(rf"\b{re.escape(term)}s?\b", row.get("title", ""), re.I) for row in rows)
    }
    return present


def _matches_anchor(row: dict, anchors: set[str], *, require_all: bool = False) -> bool:
    evidence = str(row.get("title") or "") if require_all else " ".join(
        str(row.get(k) or "") for k in ("title", "features")
    )
    matches = [bool(re.search(rf"\b{re.escape(term)}s?\b", evidence, re.I))
               for term in anchors]
    return not anchors or (all(matches) if require_all else any(matches))


def _requires_all_anchors(router: dict) -> bool:
    return bool((router.get("constraints") or {}).get("product_type")) and not bool(
        (router.get("grounding_notes") or {}).get("inferred_product_type")
    )


def _matches_all_query_terms(row: dict, terms: set[str]) -> bool:
    title = str(row.get("title") or "")
    return bool(terms) and all(
        re.search(rf"\b{re.escape(term)}s?\b", title, re.I) for term in terms
    )


def _matches_material(row: dict, material: str | None) -> bool:
    if not material:
        return True
    evidence = " ".join(str(row.get(k) or "") for k in ("title", "features", "specification"))
    pattern = _MATERIAL_PATTERNS.get(material)
    return bool(_material_match(pattern, evidence)) if pattern else _norm(material) in _norm(evidence)


def _is_search_results_page(row: dict) -> bool:
    url = str(row.get("url") or "")
    return bool(re.search(r"/(?:s|search)(?:[/?]|$)|[?&](?:k|q)=", url, re.I))


def _web_row_allowed(
    row: dict, anchors: set[str], max_price: float | None, material: str | None = None,
    excluded_materials: set[str] | None = None, require_all: bool = False,
) -> bool:
    return (not _is_search_results_page(row)
            and _matches_anchor(row, anchors, require_all=require_all)
            and _matches_material(row, material)
            and all(_explicitly_excludes_material(row, m)
                    for m in (excluded_materials or set()))) and (
        max_price is None
        or isinstance(row.get("price"), (int, float)) and row["price"] <= max_price
    )


_PRICE_RE = re.compile(r"\$\s?(\d{1,4}(?:[.,]\d{2})?)")
_RATING_REQUEST_RE = re.compile(
    r"\b(?:highest|best|top)[- ]?rated\b|\bhighest\s+rating\b|\brating\b", re.I,
)
_REVIEW_REQUEST_RE = re.compile(r"\b(?:customer|product|user)\s+reviews?\b", re.I)
_LIVE_RE = re.compile(
    r"\b(?:current(?:ly)?|latest|right now|today|these days|in stock|out of stock|"
    r"restock\w*|availability|available now|still (?:sold|available|being sold)|"
    r"discontinued|live price|up to date)\b",
    re.I,
)


def _rating_from_text(*texts: str) -> float | None:
    for text in texts:
        match = re.search(
            r"\b([0-5](?:\.\d)?)\s*(?:out\s+of\s+5|/\s*5|stars?)\b",
            text or "", re.I,
        )
        if match:
            return float(match.group(1))
    return None
_MONEY_RE = re.compile(
    r"\$|\bdollars?\b|\bbucks?\b|\bprice[ds]?\b|\bbudget\b|\bcheap\w*\b|\bcost\w*\b",
    re.I,
)
_LIMIT_RE = re.compile(r"\b(?:under|below|less than|up to|no more than|max(?:imum)?)\b", re.I)
_AGE_RE = re.compile(r"\b\d+\s*(?:\+\s*)?(?:year|yr|month|mo)s?\b|\bages?\b", re.I)
_ECO_REQUEST_RE = re.compile(
    r"\b(?:eco(?:[- ]friendly)?|green|sustainab\w*|biodegradable|plant[- ]based|non[- ]?toxic)\b",
    re.I,
)


def _budget_is_grounded(text: str) -> bool:
    if _MONEY_RE.search(text):
        return True
    return bool(_LIMIT_RE.search(text)) and not _AGE_RE.search(text)


_ADULT_RECIPIENT_RE = re.compile(
    r"\b(?:adult|myself|mom|mother|dad|father|wife|husband|grandma|grandmother|"
    r"grandpa|grandfather)\b",
    re.I,
)
_CHEM_RE = re.compile(
    r"\b(?:bleach|ammonia|vinegar|hydrogen peroxide|rubbing alcohol|drain cleaner)\b"
    r"(?!\s*[- ]?free)",
    re.I,
)
_MIX_RE = re.compile(r"\b(?:mix|combine|blend|pour|add)\w*\b|\btogether\b", re.I)
_INGEST_RE = re.compile(r"\b(?:drink|ingest|swallow|eat)\w*\b", re.I)
_CLEANING_PRODUCT_RE = re.compile(r"\b(?:cleaners?|chemicals?|detergents?)\b", re.I)
_HARMFUL_RE = re.compile(
    r"\b(?:build|make|use)\b.{0,40}\b(?:bomb|weapon|explosive)\b|"
    r"\b(?:poison|harm|kill)\b.{0,30}\b(?:someone|person|people)\b",
    re.I,
)


def _deterministic_safety_flags(text: str) -> list[str]:
    chemicals = {match.group(0).lower() for match in _CHEM_RE.finditer(text)}
    flags = []
    if len(chemicals) >= 2 and _MIX_RE.search(text):
        flags.append("unsafe_chemical_mixing")
    if _INGEST_RE.search(text) and (chemicals or _CLEANING_PRODUCT_RE.search(text)):
        flags.append("ingestion_risk")
    if _HARMFUL_RE.search(text):
        flags.append("harmful_intent")
    return sorted(flags)


def _adult_recipient_requested(text: str, constraints: dict) -> bool:
    audience = str((constraints or {}).get("audience") or "").lower()
    return audience in {"adult", "myself", "me", "men", "women"} or bool(
        _ADULT_RECIPIENT_RE.search(text)
    )


def _price_from_text(*texts: str) -> float | None:
    for t in texts:
        m = _PRICE_RE.search(t or "")
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------------
# node factory
# --------------------------------------------------------------------------

def build_nodes(mcp: MCPToolClient) -> dict:
    """Return the node callables, closed over the shared MCP client."""

    # ---- 1. Router --------------------------------------------------------
    async def router_node(state: dict) -> dict:
        transcript = state["transcript"]

        # "apply" — the client edited the constraint set directly (e.g. removed a
        # preference chip). Running extraction here would be actively wrong:
        # "Remove the soft preference" contains the word "soft", so the router
        # would parse it straight back in. Use the given constraints verbatim.
        if state.get("apply_only"):
            c = dict(state.get("prior_constraints") or {})
            router = {
                "task": "product_recommendation",
                "constraints": c,
                "safety_flags": [],
                "needs_live": bool(state.get("prior_needs_live")),
                "top_k": max(1, min(int(state.get("prior_top_k") or 3), 5)),
            }
            effective = compose_query(c, transcript)
            return {
                "router": router,
                "effective_query": effective,
                "constraint_changes": state.get("applied_changes") or [],
                "steps": [step(
                    "session",
                    {"applied_constraints": c, "note": "client-edited constraints; "
                     "router extraction skipped so the edit is not re-parsed"},
                    {"merged_constraints": c, "search_query": effective},
                    label="Updating search",
                    detail=" · ".join(state.get("applied_changes") or []) or "Constraints updated",
                )],
            }

        prompt = full_prompt(
            "router",
            few_shots=load("few_shots_router"),
            transcript=transcript,
        )
        out: RouterOutput = await call_structured(
            prompt, RouterOutput, node="router", context={"transcript": transcript}
        )
        router = out.model_dump()
        constraints = router["constraints"] = router.get("constraints") or {}
        dropped: list[str] = []
        spoken_norm = _norm(transcript)

        for key in ("brand", "category", "material"):
            value = constraints.get(key)
            grounded = not value or _norm(str(value)) in spoken_norm
            if key == "brand" and value and not grounded:
                grounded = _all_query_terms_present(str(value), transcript)
            if key == "material" and value and not grounded:
                grounded = bool(set(_norm(str(value)).split()) & set(spoken_norm.split()))
            if value and not grounded:
                dropped.append(f"{key}={value!r} (not in the utterance)")
                constraints[key] = None
        if constraints.get("budget") is not None and not _budget_is_grounded(transcript):
            dropped.append(f"budget={constraints['budget']!r} (no price cue in the utterance)")
            constraints["budget"] = None
        if constraints.get("eco_friendly") is True and not _ECO_REQUEST_RE.search(transcript):
            dropped.append("eco_friendly=True (no eco preference in the utterance)")
            constraints["eco_friendly"] = None

        explicit_material = _extract_material(transcript)
        if explicit_material:
            if constraints.get("material") != explicit_material:
                dropped.append(
                    f"material={constraints.get('material')!r} replaced with explicit "
                    f"{explicit_material!r}"
                )
            constraints["material"] = explicit_material

        llm_needs_live = bool(router.get("needs_live"))
        llm_task = router.get("task")
        rating_request = bool(_RATING_REQUEST_RE.search(transcript))
        review_request = bool(_REVIEW_REQUEST_RE.search(transcript))
        # Regex handles explicit high-confidence cues; the structured Router
        # extends coverage to paraphrases the pattern list does not spell out.
        # English-only by design: the grounding checks below compare against an
        # ASCII-normalized transcript, so a non-English utterance would have
        # every extracted constraint dropped as "not in the utterance".
        router["needs_live"] = bool(
            _LIVE_RE.search(transcript) or llm_needs_live or rating_request or review_request
        )
        llm_safety_flags = sorted(set(router.get("safety_flags") or []))
        # Safety is the one axis we do not narrow: retain both the Router's
        # judgement and exact deterministic hazards.
        router["safety_flags"] = sorted(set(
            llm_safety_flags + _deterministic_safety_flags(transcript)
        ))
        router["task"] = (
            "safety_question" if router["safety_flags"] else
            "rating_check" if rating_request else
            "review_check" if review_request else
            "price_check" if router["needs_live"] else
            "product_recommendation"
        )

        inferred_product_type = None
        if (
            not constraints.get("product_type")
            and not router["safety_flags"]
            and not state.get("prior_constraints")
        ):
            inferred_product_type = " ".join(dict.fromkeys(_query_terms(transcript))) or None
            constraints["product_type"] = inferred_product_type
        router["grounding_notes"] = {
            "dropped_constraints": dropped,
            "inferred_product_type": inferred_product_type,
            "llm_needs_live": llm_needs_live,
            "enforced_needs_live": router["needs_live"],
            "rating_request": rating_request,
            "review_request": review_request,
            "llm_task": llm_task,
            "enforced_task": router.get("task"),
            "llm_safety_flags": llm_safety_flags,
            "enforced_safety_flags": router["safety_flags"],
        }

        # Refinement: fold this utterance's constraints onto the session's.
        prior = state.get("prior_constraints") or None
        changes: list[str] = []
        if prior:
            merged = merge_constraints(prior, router.get("constraints") or {})
            changes = diff_constraints(prior, merged)
            router["constraints"] = merged
            # top_k is only meaningful when this utterance asked for a count.
            if not re.search(r"\b(compare|show|top|best)\b.*\b(\d+|three|four|five)\b",
                             transcript, re.I):
                router["top_k"] = state.get("prior_top_k") or router.get("top_k") or 3
        router["top_k"] = max(1, min(int(router.get("top_k") or 3), 5))

        c = router.get("constraints") or {}
        bits = [
            c.get("product_type"),
            c.get("size"),
            f"≤ {_money(c["budget"])}" if isinstance(c.get("budget"), (int, float)) else None,
            *(c.get("qualitative_features") or []),
        ]
        detail = " · ".join(_titlecase(str(b)) for b in bits if b)
        effective = compose_query(c, transcript)
        out_steps = [step(
            "router",
            {
                "transcript": transcript,
                "refinement_of": prior or None,
                "prompt_file": "prompts/router.md (+ few_shots_router.md)",
                "prompt_preview": _preview(prompt),
                "llm": f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}",
            },
            router,
            label=("Understanding refinement" if prior else
                   "Fresh information requested" if router.get("needs_live") else
                   "Understanding request"),
            detail=(" · ".join(changes) if changes else detail) or "No explicit constraints",
        )]
        if prior:
            out_steps.append(step(
                "session",
                {"previous_constraints": prior, "utterance": transcript},
                {"merged_constraints": c, "changes": changes, "search_query": effective},
                label="Updating search",
                detail=detail or "No explicit constraints",
            ))
        return {
            "router": router,
            "effective_query": effective,
            "constraint_changes": changes,
            "steps": out_steps,
        }

    def route_after_router(state: dict) -> str:
        if state["router"].get("safety_flags"):
            return "safety"
        if clarification_needed(state["router"]):
            return "clarify"
        return "planner"

    # ---- 1b. Clarify (deterministic, one question) ------------------------
    async def clarify_node(state: dict) -> dict:
        question = clarification_needed(state["router"]) or ""
        c = (state["router"].get("constraints") or {})
        return {
            "clarify": {"question": question, "product_type": c.get("product_type")},
            "mode": "clarify",
            "answer": {"spoken_answer": question, "top_pick_doc_id": "", "citation_doc_ids": []},
            "steps": [step(
                "clarify",
                {"rule": "product_type given but no budget/audience/use_case/size/"
                         "brand/material/features to rank on",
                 "constraints": c},
                {"question": question},
                label="Asking one clarifying question",
                detail="Request too broad to rank responsibly — no retrieval run",
            )],
        }

    # ---- 2. Safety gate (deterministic) -----------------------------------
    async def safety_node(state: dict) -> dict:
        flags = state["router"].get("safety_flags", [])
        if "unsafe_chemical_mixing" in flags:
            refusal = (
                "I can't help mix household chemicals because dangerous combinations can "
                "release toxic gases. Follow product labels, and contact poison control if "
                "exposure has occurred."
            )
        elif "ingestion_risk" in flags:
            refusal = (
                "I can't advise swallowing or tasting cleaning products. Do not ingest them; "
                "follow the label and contact poison control if ingestion has occurred."
            )
        elif "harmful_intent" in flags:
            refusal = (
                "I can't help with instructions intended to harm someone. I can help with a "
                "safe product recommendation instead."
            )
        else:
            # Router-only flag: the hazard is real but unnamed by our patterns,
            # so refuse without asserting which one it was.
            refusal = (
                "I can't help with that — it involves unsafe use of household products. "
                "Follow the product label, and contact poison control if exposure has "
                "occurred. I'm happy to recommend a safe product instead."
            )
        answer = {"spoken_answer": refusal, "top_pick_doc_id": "", "citation_doc_ids": []}
        return {
            "blocked": True,
            "answer": answer,
            "steps": [step(
                "safety",
                {"safety_flags": flags, "policy": "docs/safety.md — no unsafe chemical advice"},
                {"action": "blocked", "spoken_answer": refusal},
                label="Safety check",
                detail=f"Blocked: {', '.join(flags) or 'unsafe request'}",
            )],
        }

    # ---- 3. Planner (LLM + deterministic rubric enforcement) ---------------
    async def planner_node(state: dict) -> dict:
        transcript = state["transcript"]
        router = state["router"]
        prompt = full_prompt(
            "planner",
            router_json=json.dumps(router, indent=2),
            transcript=transcript,
        )
        out: PlanOutput = await call_structured(
            prompt, PlanOutput, node="planner", context={"router": router}
        )
        plan = out.model_dump()
        enforced: list[str] = []

        # Rubric rule 1: rag.search is always the primary source of facts.
        if "rag.search" not in plan["sources"]:
            plan["sources"].insert(0, "rag.search")
            enforced.append("added rag.search (private catalog is always primary)")
        # Rubric rule 2: web.search iff the user asked for live info.
        if router.get("needs_live") and "web.search" not in plan["sources"]:
            plan["sources"].append("web.search")
            enforced.append("added web.search (router flagged needs_live)")
        if not router.get("needs_live") and "web.search" in plan["sources"]:
            plan["sources"] = [s for s in plan["sources"] if s != "web.search"]
            enforced.append("removed web.search (no live-data request; web is fallback-only)")

        # Merge router constraints into any filter slots the planner left empty.
        c = router.get("constraints") or {}
        f = plan["retrieval_filters"]
        merges = {
            "max_price": c.get("budget"),
            "category": c.get("category"),
            "material": c.get("material"),
            "eco_friendly": c.get("eco_friendly"),
        }
        for key, value in merges.items():
            if f.get(key) in (None, "") and value not in (None, ""):
                f[key] = value
                enforced.append(f"filled filter {key}={value!r} from router constraints")

        # The source corpus has no ratings; code removes that unsupported
        # criterion even if the unchanged planner prompt proposes it.
        criteria = plan.get("comparison_criteria") or []
        rating_request = bool((router.get("grounding_notes") or {}).get("rating_request"))
        grounded_criteria = [c for c in criteria
                             if rating_request or "rating" not in str(c).lower()]
        if grounded_criteria != criteria:
            enforced.append("removed rating criterion (not present in catalog)")
        if rating_request and not any("rating" in str(c).lower() for c in grounded_criteria):
            grounded_criteria.insert(0, "rating")
        plan["comparison_criteria"] = grounded_criteria or ["price", "features"]

        plan["enforced_rules"] = enforced
        srcs = plan["sources"]
        detail = ("Private Amazon catalog + live web" if "web.search" in srcs
                  else "Private Amazon catalog selected")
        return {
            "plan": plan,
            "steps": [step(
                "planner",
                {
                    "router_output": router,
                    "prompt_file": "prompts/planner.md",
                    "planner_rule": "prefer rag.search; add web.search only for current/latest/availability",
                },
                plan,
                label="Planning search",
                detail=detail,
            )],
        }

    # ---- 4. Retrieve via MCP rag.search + LLM rerank ----------------------
    async def retrieve_node(state: dict) -> dict:
        # Search on the accumulated intent, not the latest utterance alone.
        plan = state["plan"]
        f = plan.get("retrieval_filters") or {}
        router = state.get("router") or {}
        router_c = router.get("constraints") or {}
        require_all_anchors = _requires_all_anchors(router)
        product_type = str(router_c.get("product_type") or "")
        transcript = (
            state["transcript"]
            if product_type and _norm(product_type) in _norm(state["transcript"])
            else state.get("effective_query") or state["transcript"]
        )
        excluded_materials = _excluded_materials(transcript)
        recipient_text = f"{state.get('effective_query') or ''} {state['transcript']}"
        adult_recipient = _adult_recipient_requested(recipient_text, router_c)
        args: dict[str, Any] = {
            "query": transcript,
            "top_k": max(settings.RAG_TOP_K, settings.RAG_CANDIDATE_K),
        }
        for key in ("max_price", "category", "material", "eco_friendly"):
            if f.get(key) not in (None, ""):
                args[key] = f[key]

        resp = await mcp.call("rag.search", args)
        candidates = [r for r in resp.get("results", []) if r.get("doc_id")]
        rating_request = bool(_RATING_REQUEST_RE.search(state["transcript"]))
        review_request = bool(_REVIEW_REQUEST_RE.search(state["transcript"]))
        rating_catalog_unsupported = False
        if rating_request or review_request:
            # This corpus has neither ratings nor reviews; use live results rather
            # than pretending catalog prose supports either request.
            candidates = []
            rating_catalog_unsupported = rating_request
        spoken = state["transcript"]
        anchor_query = (
            spoken if product_type and _norm(product_type) in _norm(spoken)
            else " ".join(str(v) for v in (router_c.get("brand"), product_type) if v)
            or spoken
        )
        exact_terms = set(_query_terms(anchor_query))
        category_retry = None
        if (state.get("router") or {}).get("needs_live") and len(exact_terms) >= 2:
            exact = [row for row in candidates if _matches_all_query_terms(row, exact_terms)]
            if exact:
                candidates = exact
            elif args.get("category"):
                retry_args = {k: v for k, v in args.items() if k != "category"}
                retry_resp = await mcp.call("rag.search", retry_args)
                retry_rows = [r for r in retry_resp.get("results", []) if r.get("doc_id")]
                candidates = [
                    row for row in retry_rows if _matches_all_query_terms(row, exact_terms)
                ]
                category_retry = {
                    "reason": "live price target absent from category-filtered candidates",
                    "terms": sorted(exact_terms),
                    "arguments": retry_args,
                    "result_count": len(candidates),
                }
                resp = retry_resp
            else:
                candidates = []
        anchors = _lexical_anchor_terms(anchor_query, candidates)
        before_product_type = len(candidates)
        candidates = [row for row in candidates if _matches_anchor(
            row, anchors, require_all=require_all_anchors,
        )]
        excluded_for_product_type = before_product_type - len(candidates)
        before_negative_material = len(candidates)
        candidates = [row for row in candidates if all(
            _explicitly_excludes_material(row, material)
            for material in excluded_materials
        )]
        excluded_for_negative_material = before_negative_material - len(candidates)
        before_material = len(candidates)
        candidates = [row for row in candidates if _matches_material(row, f.get("material"))]
        excluded_for_material = before_material - len(candidates)
        excluded_for_recipient = 0
        if adult_recipient:
            kept = [row for row in candidates if not _is_child_product(row)]
            excluded_for_recipient = len(candidates) - len(kept)
            candidates = kept
        before_dupes = len(candidates)
        candidates = dedupe_listings(candidates)
        duplicates_dropped = before_dupes - len(candidates)

        want = max(1, min(int((state.get("router") or {}).get("top_k") or 3), 5))

        # --- HARD-constraint guard -----------------------------------------
        # Never let an over-budget row be presented as a match. rag.search
        # already filters on price, but its relaxation ladder can drop that
        # filter; re-check here so the budget is enforced no matter what.
        budget = router_c.get("budget")
        no_match: dict[str, Any] | None = None
        if isinstance(budget, (int, float)):
            within = [c for c in candidates if isinstance(c.get("price"), (int, float))
                      and c["price"] <= budget]
            if not within:
                # rag.search applies the price filter itself, so an empty result
                # cannot tell us *why* it is empty. Re-query without the budget:
                #   probe has rows -> the catalog stocks this product, just not
                #                     at this price  -> honest budget no-match
                #   probe is empty -> nothing relevant at any price -> ordinary
                #                     "no private match" -> existing web fallback
                # Blaming the budget in the second case would name the wrong
                # constraint and quote a price that does not exist.
                # Same filters, price removed — so a non-empty probe means
                # "this product exists here, just not at your price". Dropping
                # every filter instead would blame the budget for a miss the
                # category or material filter actually caused.
                probe_args = {k: v for k, v in args.items() if k != "max_price"}
                probe = await mcp.call("rag.search", probe_args)
                probe_rows = [r for r in probe.get("results", []) if r.get("doc_id")]
                probe_anchors = _lexical_anchor_terms(anchor_query, probe_rows)
                probe_rows = [r for r in probe_rows if _matches_anchor(
                    r, probe_anchors, require_all=require_all_anchors,
                )]
                probe_rows = [r for r in probe_rows if all(
                    _explicitly_excludes_material(r, material)
                    for material in excluded_materials
                )]
                probe_rows = [r for r in probe_rows if _matches_material(r, f.get("material"))]
                if adult_recipient:
                    probe_rows = [r for r in probe_rows if not _is_child_product(r)]
                prices = [r["price"] for r in probe_rows
                          if isinstance(r.get("price"), (int, float))]
                if probe_rows:
                    no_match = {
                        "match_status": "no_exact_match",
                        "failed_constraints": ["max_price"],
                        "requested_max_price": float(budget),
                        "closest_available_price": min(prices) if prices else None,
                        "closest_alternatives": [{**_slim(r), "image": r.get("image")} for r in sorted(
                            probe_rows,
                            key=lambda r: (r.get("price") if isinstance(r.get("price"), (int, float)) else 1e9),
                        )[:want]],
                    }
            candidates = within

        rerank_info: dict[str, Any] = {}
        top_picks: list[dict] = []
        if candidates:
            # Give the LLM explicit, code-verified match evidence instead of
            # asking it to infer every constraint again from catalog prose.
            for candidate in candidates:
                candidate.update(match_evidence(candidate, router_c))
            # Retrieve wide (top_k 30) so the anchor/material/budget filters have
            # room to work, but rerank narrow. Handing gpt-4o-mini ~19 rows made
            # it lose track of individual products: its rationale degraded from
            # naming each pick to a single generic sentence, and the vector-#1 row
            # fell out of the top 3 entirely. The tail is already the weakest part
            # of a similarity-ordered list, so cutting it costs nothing.
            shortlist = candidates[:RERANK_SHORTLIST]
            slim = [{**_slim(c), "match_reasons": c["match_reasons"]} for c in shortlist]
            prompt = full_prompt(
                "reranker",
                transcript=transcript,
                top_n=want,
                criteria_json=json.dumps(plan.get("comparison_criteria") or []),
                candidates_json=json.dumps(slim, indent=1),
            )
            out: RerankOutput = await call_structured(
                prompt, RerankOutput, node="reranker",
                context={"candidates": shortlist, "top_k": want},
            )
            # Deterministic validation: ranked ids must be a subset of the
            # shortlist the reranker was actually shown.
            # Top up by retrieval score. A short model output does not mean the other
            # rows were rejected" — and when the model returns only unusable ids
            # the alternative is discarding a healthy candidate set and falling
            # back to live web search the user never asked for.
            ranked, topped_up, hallucinated = top_up_ranked(
                shortlist, out.ranked_doc_ids, want,
            )
            # Deterministic audience guard, applied after the LLM rerank so the
            # outcome is guaranteed rather than hoped for.
            ranked = rank_grounded(ranked)
            ranked, demoted = demote_child_products(ranked, transcript, router_c)
            top_picks = ranked
            rerank_info = {
                "ranked_doc_ids": [r["doc_id"] for r in ranked],
                "rationale": out.rationale,
                "prompt_file": "prompts/reranker.md",
                "shortlist_size": len(shortlist),
                "topped_up_by_score": topped_up or None,
                "candidates_seen": f"{len(shortlist)} of {len(candidates)} "
                                   f"(top {RERANK_SHORTLIST} by retrieval score)",
            }
            if hallucinated:
                rerank_info["dropped_unknown_doc_ids"] = hallucinated
            if demoted:
                rerank_info["child_products_demoted"] = demoted
                rerank_info["audience_rule"] = (
                    "no child signal in the request — kid-targeted rows moved "
                    "below general-audience rows (demoted, not removed)")

        detail = (f"MCP → rag.search · {len(candidates)} candidates found"
                  if candidates else "MCP → rag.search · no qualifying products")
        if no_match:
            detail = (f"MCP → rag.search · nothing at or below "
                      f"{_money(no_match["requested_max_price"])}")

        out_state: dict[str, Any] = {
            "candidates": candidates,
            "top_picks": top_picks,
            "mode": "no_match" if no_match else "private",
            "steps": [step(
                "rag.search",
                {"tool": "rag.search", "transport": "MCP (stdio)", "arguments": args},
                {
                    "no_match": no_match,
                    "result_count": len(candidates),
                    "resolved_category": resp.get("resolved_category"),
                    "category_family": resp.get("category_family"),
                    "category_retry": category_retry,
                    "rating_catalog_unsupported": rating_catalog_unsupported,
                    "review_catalog_unsupported": review_request,
                    "relaxations": resp.get("relaxations"),
                    "relevance_floor": resp.get("relevance_floor"),
                    "dropped_below_floor": resp.get("dropped_below_floor"),
                    "error": resp.get("error"),
                    "excluded_for_recipient": excluded_for_recipient,
                    "excluded_materials": sorted(excluded_materials),
                    "excluded_for_negative_material": excluded_for_negative_material,
                    "excluded_for_product_type": excluded_for_product_type,
                    "excluded_for_material": excluded_for_material,
                    "duplicate_listings_dropped": duplicates_dropped,
                    "candidates": [_slim(c, keep_features=120) for c in candidates],
                    "rerank": rerank_info or None,
                },
                label="Searching private catalog",
                detail=detail,
            )] + ([step(
                "rerank",
                {"prompt_file": "prompts/reranker.md", "candidates": len(candidates),
                 "criteria": plan.get("comparison_criteria")},
                rerank_info,
                label="Comparing candidates",
                detail=f"Top {len(top_picks)} selected from {len(candidates)} candidates",
            )] if top_picks else []),
        }
        if no_match:
            out_state["no_match"] = no_match
        return out_state

    def route_after_retrieve(state: dict) -> str:
        # A hard constraint that nothing satisfies is an answer in itself — go
        # straight to the answerer rather than quietly firing a live web search
        # the user never asked for.
        if state.get("no_match"):
            return "answer"
        if not state.get("top_picks"):
            return "web_fallback"
        if "web.search" in (state.get("plan", {}).get("sources") or []):
            return "web_compare"
        return "answer"

    # ---- 5a. Web compare (live price/availability for the top pick) -------
    async def web_compare_node(state: dict) -> dict:
        top = state["top_picks"][0]
        # Placeholder values must never reach a live search: this dataset's
        # brand column is empty, so ingest stores "Unknown" — searching for
        # "Unknown Twin Comforter price" poisons the query.
        brand = (top.get("brand") or "").strip()
        if brand.lower() in ("unknown", "n/a", "na", "none", "null"):
            brand = ""
        # Long Amazon titles also hurt search recall; keep the leading phrase.
        title = " ".join(str(top.get("title") or "").split()[:12])
        query = " ".join(x for x in [title, brand, "price"] if x)
        args = {"query": query, "max_results": 5}
        resp = await mcp.call("web.search", args)
        web = {"mode": "compare", **resp}
        return {
            "web": web,
            "steps": [step(
                "web.search",
                {
                    "tool": "web.search",
                    "transport": "MCP (stdio)",
                    "arguments": args,
                    "reason": "user asked for current price/availability (needs_live)",
                },
                {
                    "provider": resp.get("provider"),
                    "cached": resp.get("cached", False),
                    "result_count": len(resp.get("results", [])),
                    "results": resp.get("results", []),
                    "error": resp.get("error"),
                },
                label="Searching the web",
                detail=f"MCP → web.search · {len(resp.get('results', []))} live results"
                       + (" (cached)" if resp.get("cached") else ""),
            )],
        }

    # ---- 5b. Web fallback (private catalog had no match) -------------------
    async def web_fallback_node(state: dict) -> dict:
        transcript = state["transcript"]
        filters = ((state.get("plan") or {}).get("retrieval_filters") or {})
        max_price = filters.get("max_price")
        material = filters.get("material")
        rating_request = bool(_RATING_REQUEST_RE.search(transcript))
        review_request = bool(_REVIEW_REQUEST_RE.search(transcript))
        suffix = ("product rating reviews" if rating_request else
                  "customer reviews" if review_request else "buy price")
        args = {"query": f"{transcript} {suffix}", "max_results": 6}
        resp = await mcp.call("web.search", args)
        raw_rows = []
        for i, r in enumerate(resp.get("results", [])):
            raw_rows.append({
                "doc_id": f"web-{i + 1}",
                "sku": f"web-{i + 1}",
                "title": r.get("title"),
                "brand": None,
                "price": r.get("price") if isinstance(r.get("price"), (int, float))
                         else _price_from_text(r.get("snippet", ""), r.get("title", "")),
                "rating": _rating_from_text(r.get("snippet", ""), r.get("title", "")),
                "features": r.get("snippet"),
                "url": r.get("url"),
                "availability": r.get("availability"),
                "source": "web",
            })
        constraints = ((state.get("router") or {}).get("constraints") or {})
        require_all_anchors = _requires_all_anchors(state.get("router") or {})
        excluded_materials = _excluded_materials(
            state.get("effective_query") or state["transcript"]
        )
        anchor_query = str(constraints.get("product_type") or transcript)
        anchors = _lexical_anchor_terms(anchor_query, raw_rows)
        rows = [
            row for row in raw_rows
            if _web_row_allowed(
                row, anchors, max_price, material,
                excluded_materials, require_all_anchors,
            )
            and (not rating_request or isinstance(row.get("rating"), (int, float)))
        ]
        web = {"mode": "fallback", **resp}
        return {
            "web": web,
            "top_picks": rows[:max(1, min(int(
                ((state.get("router") or {}).get("top_k") or 3)
            ), 5))],
            "mode": "web_fallback",
            "steps": [step(
                "web.search",
                {
                    "tool": "web.search",
                    "transport": "MCP (stdio)",
                    "arguments": args,
                    "plan_sources": (state.get("plan") or {}).get("sources") or [],
                    "reason": "private catalog returned 0 rows — graph fallback policy, "
                              "not a planner decision",
                },
                {
                    "provider": resp.get("provider"),
                    "cached": resp.get("cached", False),
                    "result_count": len(rows),
                    "results": resp.get("results", []),
                    "error": resp.get("error"),
                },
                label="Searching the web",
                detail=f"No private match · MCP → web.search · {len(rows)} live results",
            )],
        }

    # ---- 6. Reconcile (deterministic conflict handling) --------------------
    async def reconcile_node(state: dict) -> dict:
        web_results = (state.get("web") or {}).get("results", [])
        picks = state.get("top_picks", [])
        matches: dict[str, Any] = {}
        flags: list[str] = []
        for pick in picks:
            best, best_ev, best_score = None, None, -1.0
            for r in web_results:
                ok, ev = match_products(pick.get("title", ""), pick.get("brand"),
                                        r.get("title", ""))
                if not ok:
                    continue
                # Rank accepted candidates by distinctive overlap, then containment.
                score = len(ev["distinctive_tokens"]) + ev["containment"]
                if score > best_score:
                    best, best_ev, best_score = r, ev, score
            if not best:
                continue
            web_price = best.get("price") if isinstance(best.get("price"), (int, float)) \
                else _price_from_text(best.get("snippet", ""), best.get("title", ""))
            entry: dict[str, Any] = {
                "matched_by": "distinctive-token overlap (SKU-less web rows)",
                "distinctive_tokens": best_ev["distinctive_tokens"],
                "containment": best_ev["containment"],
                "web_title": best.get("title"),
                "web_url": best.get("url"),
                "web_price": web_price,
                "availability": best.get("availability"),
            }
            cat_price = pick.get("price")
            if isinstance(cat_price, (int, float)) and isinstance(web_price, (int, float)) and cat_price:
                delta_ratio = abs(web_price - cat_price) / cat_price
                delta_pct = round(delta_ratio * 100, 1)
                entry["catalog_price"] = cat_price
                entry["price_delta_pct"] = delta_pct
                if delta_ratio > PRICE_DISCREPANCY_RATIO:
                    entry["discrepancy"] = True
                    flags.append(
                        f"{pick.get('title')}: live price ${web_price:.2f} differs from "
                        f"catalog ${cat_price:.2f} ({delta_pct:.0f}%)"
                    )
            if best.get("availability"):
                entry.setdefault("notes", []).append(f"availability: {best['availability']}")
            matches[pick["doc_id"]] = entry

        reconciliation = {"matches": matches, "discrepancy_flags": flags}
        return {
            "reconciliation": reconciliation,
            "steps": [step(
                "reconcile",
                {
                    "method": "deterministic: >=2 distinctive shared tokens (generic "
                              "category words and 'Unknown' brand excluded); "
                              f"price delta > {PRICE_DISCREPANCY_RATIO:.0%} flagged",
                    "live_query_scope": "top_pick_only",
                    "catalog_picks": [p.get("title") for p in picks],
                    "web_titles": [r.get("title") for r in web_results],
                },
                reconciliation,
                label="Matching live results",
                detail=("Live lookup covered the top-ranked catalog pick only · "
                        f"{len(matches)} exact match{'es' if len(matches) != 1 else ''}"
                        + (f" · {len(flags)} price discrepancy flagged" if flags else "")),
            )],
        }

    # ---- 7. Answerer / Critic ---------------------------------------------
    async def answer_node(state: dict) -> dict:
        # Answer against the full accumulated request so the summary reflects
        # every constraint, not just the last thing the user said.
        transcript = state.get("effective_query") or state["transcript"]
        mode = state.get("mode", "private")
        picks = state.get("top_picks", [])

        # No-match is answered deterministically. There is nothing to ground an
        # LLM answer in, and asking a model to explain an empty result set is
        # exactly where a fabricated product or price would come from.
        nm = state.get("no_match")
        if nm:
            budget = _money(nm.get("requested_max_price"))
            closest = nm.get("closest_available_price")
            spoken = (f"I couldn't find anything at or below {budget} in the catalog. "
                      + (f"The closest options start around {_money(closest)}. "
                         if isinstance(closest, (int, float)) else "")
                      + "Would you like to raise the budget or search online?")
            return {
                "answer": {"spoken_answer": spoken, "top_pick_doc_id": "", "citation_doc_ids": []},
                "steps": [step(
                    "answerer",
                    {"mode": "no_match", "failed_constraints": nm.get("failed_constraints"),
                     "note": "deterministic — no LLM call, nothing to ground an answer in"},
                    {"spoken_answer": spoken},
                    label="Generating response",
                    detail="No qualifying products — reporting the gap instead of recommending",
                )],
            }
        if not picks:
            if _RATING_REQUEST_RE.search(state["transcript"]):
                spoken = (
                    "The private catalog does not include ratings, and I couldn't verify "
                    "comparable product ratings from live sources, so I can't determine "
                    "the highest-rated option."
                )
            elif _REVIEW_REQUEST_RE.search(state["transcript"]):
                spoken = (
                    "The private catalog does not include customer reviews, and I couldn't "
                    "verify a relevant review from live sources."
                )
            else:
                spoken = (
                    "I couldn't verify a product that meets all your requirements in the "
                    "catalog or live results. Try relaxing one constraint or searching online."
                )
            return {
                "answer": {
                    "spoken_answer": spoken,
                    "answer_detail": spoken,
                    "top_pick_doc_id": "",
                    "citation_doc_ids": [],
                },
                "steps": [step(
                    "answerer",
                    {"mode": mode, "note": "deterministic — zero grounded rows"},
                    {"spoken_answer": spoken},
                    label="Generating response",
                    detail="No grounded products — LLM answer generation skipped",
                )],
            }
        products = [_slim(p) for p in picks]
        reconciliation = state.get("reconciliation")
        web_block = None
        if reconciliation or (state.get("web") and mode == "private"):
            web_block = {
                "reconciliation": reconciliation,
                "live_results": (state.get("web") or {}).get("results", [])[:5],
            }
        criteria = (state.get("plan") or {}).get("comparison_criteria") or ["price", "features"]

        prompt = full_prompt(
            "answerer",
            transcript=transcript,
            criteria_json=json.dumps(criteria),
            mode=mode,
            products_json=json.dumps(products, indent=1),
            web_json=json.dumps(web_block, indent=1) if web_block else "null",
        )
        out: AnswerOutput = await call_structured(
            prompt, AnswerOutput, node="answerer",
            context={"top_picks": products, "reconciliation": reconciliation},
        )
        answer = out.model_dump()
        critic_notes: list[str] = []

        # The comparison order is the contract: the first reranked row is the
        # top pick. If the draft selects another option, regenerate against the
        # first row only so both the prose and id describe the same product.
        expected_top = products[0]["doc_id"] if products else ""
        if expected_top and answer.get("top_pick_doc_id") != expected_top:
            retry_prompt = full_prompt(
                "answerer",
                transcript=transcript,
                criteria_json=json.dumps(criteria),
                mode=mode,
                products_json=json.dumps(products[:1], indent=1),
                web_json=json.dumps(web_block, indent=1) if web_block else "null",
            )
            retry: AnswerOutput = await call_structured(
                retry_prompt, AnswerOutput, node="answerer",
                context={"top_picks": products[:1], "reconciliation": reconciliation},
            )
            answer = retry.model_dump()
            answer["top_pick_doc_id"] = expected_top
            critic_notes.append("regenerated answer to match reranker rank 1")

        # Grounding checks (the "critic checks grounding" duty, in code).
        known = {p["doc_id"] for p in products if p.get("doc_id")}
        cited = [d for d in answer.get("citation_doc_ids", []) if d in known]
        dropped = [d for d in answer.get("citation_doc_ids", []) if d not in known]
        if dropped:
            critic_notes.append(f"dropped uncited/unknown doc_ids: {dropped}")
        if not cited and known:
            cited = sorted(known)
            critic_notes.append("filled empty citations with retrieved row ids")
        answer["citation_doc_ids"] = cited
        if answer.get("top_pick_doc_id") not in known:
            fallback = products[0]["doc_id"] if products else ""
            if answer.get("top_pick_doc_id"):
                critic_notes.append(
                    f"top_pick_doc_id {answer['top_pick_doc_id']!r} not in rows; "
                    f"replaced with {fallback!r}")
            answer["top_pick_doc_id"] = fallback

        # Critic duty: discrepancies must be surfaced in the spoken answer.
        flags = (reconciliation or {}).get("discrepancy_flags") or []
        _voiced = re.compile(r"\b(differ|discrepan|changed|higher|lower)\w*", re.I)
        if flags:
            for field, note in (("spoken_answer", " Note: the live price differs from our catalog."),
                                ("answer_detail", " Note: the live web price differs from the catalog price.")):
                text = answer.get(field) or ""
                if text and not _voiced.search(text):
                    answer[field] = text.rstrip() + note
                    critic_notes.append(f"appended discrepancy notice to {field}")
        if products and live_price_missing(
            bool((state.get("router") or {}).get("needs_live")),
            reconciliation,
            answer.get("top_pick_doc_id") or "",
        ):
            price = _money(products[0].get("price"))
            answer["spoken_answer"] = (
                f"The catalog lists this item at {price}, but the live source did not show "
                "a current price. Details are on screen."
            )
            answer["answer_detail"] = (
                f"The catalog price for {products[0].get('title')} is {price}. "
                "A matching live source was found, but it did not expose a current price."
            )
            critic_notes.append("distinguished catalog price from unavailable live price")

        return {
            "answer": answer,
            "steps": [step(
                "answerer",
                {
                    "mode": mode,
                    "prompt_file": "prompts/answerer.md",
                    "product_rows": len(products),
                    "criteria": criteria,
                    "llm": f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}",
                },
                {**answer, "critic_notes": critic_notes or None},
                label="Generating response",
                detail=f"Grounded in {len(products)} retrieved product"
                       f"{'s' if len(products) != 1 else ''}",
            ), step(
                "grounding",
                {
                    "check": "citation doc_ids must exist in retrieved rows; "
                             "top_pick must be one of them; discrepancies must be voiced",
                    "known_doc_ids": sorted(known),
                },
                {
                    "citations_kept": answer.get("citation_doc_ids"),
                    "citations_dropped": dropped or None,
                    "critic_notes": critic_notes or None,
                },
                label="Checking grounding",
                detail=(" · ".join(critic_notes) if critic_notes
                        else "Citation IDs and top pick verified"),
            )],
        }

    return {
        "router": router_node,
        "route_after_router": route_after_router,
        "safety": safety_node,
        "clarify": clarify_node,
        "planner": planner_node,
        "retrieve": retrieve_node,
        "route_after_retrieve": route_after_retrieve,
        "web_compare": web_compare_node,
        "web_fallback": web_fallback_node,
        "reconcile": reconcile_node,
        "answer": answer_node,
    }
