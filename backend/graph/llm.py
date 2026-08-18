"""Model-agnostic LLM access.

The graph nodes never talk to a provider SDK directly; they call
`call_structured(prompt, Schema, node=...)`. The provider/model is selected via
env (LLM_PROVIDER / LLM_MODEL) and instantiated through LangChain's
`init_chat_model`, so swapping OpenAI <-> Anthropic <-> Gemini <-> Ollama is a
config change only (brief: "Model-Agnostic Requirement").

`LLM_PROVIDER=mock` is a deterministic, keyless heuristic used for offline
demos / CI. It is not a language model and every log entry marks it as such.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from app.config import settings

T = TypeVar("T", bound=BaseModel)

_PROVIDER_MAP = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google_genai",
    "google_genai": "google_genai",
    "gemini": "google_genai",
    "ollama": "ollama",
}


@lru_cache(maxsize=1)
def get_chat_model():
    """Instantiate the chat model once, from env config."""
    from langchain.chat_models import init_chat_model

    provider = _PROVIDER_MAP.get(settings.LLM_PROVIDER)
    if provider is None:
        raise ValueError(
            f"Unknown LLM_PROVIDER={settings.LLM_PROVIDER!r}. "
            "Use openai | anthropic | google_genai | ollama | mock."
        )
    return init_chat_model(settings.LLM_MODEL, model_provider=provider, temperature=0)


async def call_structured(
    prompt: str,
    schema: Type[T],
    *,
    node: str,
    context: dict[str, Any] | None = None,
) -> T:
    """Run one structured LLM call and return a validated `schema` instance.

    `context` carries the raw state a node already has; it is only consumed by
    the mock provider (real providers read the rendered prompt).
    """
    if settings.LLM_PROVIDER == "mock":
        return _mock_structured(schema, context or {})
    kwargs = {"method": "function_calling"} if settings.LLM_PROVIDER == "openai" else {}
    model = get_chat_model().with_structured_output(schema, **kwargs)
    result = await model.ainvoke(prompt)
    if result is None:
        raise RuntimeError(
            f"{node} returned no structured output from "
            f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}"
        )
    return result


# --------------------------------------------------------------------------
# Mock provider: deterministic heuristics per output schema. Keyless demo/CI.
# --------------------------------------------------------------------------

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty-five": 25, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100,
}
_MATERIALS = [
    "stainless steel", "stainless", "glass", "granite", "marble", "wood",
    "tile", "ceramic", "leather", "carpet", "fabric",
]
_SAFETY_PATTERNS = [
    (r"mix(ing)?\b.*(bleach|ammonia)", "unsafe_chemical_mixing"),
    (r"(bleach|ammonia)\b.*mix", "unsafe_chemical_mixing"),
    (r"\b(drink|ingest|swallow|eat)\b", "ingestion_risk"),
    (r"\b(explosive|weapon|poison(ing)? (someone|a person))\b", "harmful_intent"),
]
_LIVE_PATTERNS = r"\b(current|latest|right now|today|in stock|availability|available now|live price)\b"
_MOCK_FEATURES = {
    "machine washable": r"\bmachine[- ]washable\b",
    "soft": r"\bsoft\b",
    "lightweight": r"\blight[- ]?weight\b",
    "compact": r"\bcompact\b",
    "durable": r"\bdurable\b",
    "easy to wash": r"\beasy to wash\b",
}

# Category nouns, matched on word boundaries. A bare `"clean" in transcript`
# substring test also fires on "easy to clean", which made every appliance
# request ("an ice cream maker that's easy to clean") get filtered down to
# cleaning products.
_CATEGORY_NOUNS = re.compile(
    r"\b(?:cleaner|cleaners|cleanser|cleaning\s+(?:product|spray|solution|supplies)"
    r"|detergent|degreaser|polish|disinfectant|dish\s+soap|wipes)\b"
)
# "easy to clean", "simple-to-clean", "hard to clean" describe a product's
# upkeep, not the category being shopped for. Stripped before matching.
_CLEAN_ATTRIBUTE = re.compile(
    r"\b(?:easy|simple|quick|hard|difficult|effortless)[\s-]*(?:to[\s-]*)?clean\w*\b"
)


def _infer_category(text: str) -> str | None:
    return "cleaner" if _CATEGORY_NOUNS.search(_CLEAN_ATTRIBUTE.sub(" ", text)) else None


def _parse_budget(text: str) -> float | None:
    m = re.search(r"(?:under|below|less than|max(?:imum)?|up to)\s*\$?\s*(\d+(?:\.\d{1,2})?)", text)
    if m:
        return float(m.group(1))
    m = re.search(r"\$\s?(\d+(?:\.\d{1,2})?)", text)
    if m:
        return float(m.group(1))
    m = re.search(
        r"(?:under|below|less than|up to)\s+([a-z\-]+)\s+(?:dollars|bucks)", text
    )
    if m and m.group(1) in _WORD_NUMBERS:
        return float(_WORD_NUMBERS[m.group(1)])
    return None


def _mock_structured(schema: Type[T], ctx: dict[str, Any]) -> T:
    name = schema.__name__
    if name == "RouterOutput":
        t = (ctx.get("transcript") or "").lower()
        material = next((m for m in _MATERIALS if m in t), None)
        qualitative = [label for label, pattern in _MOCK_FEATURES.items() if re.search(pattern, t)]
        flags = sorted({f for pat, f in _SAFETY_PATTERNS if re.search(pat, t)})
        return schema(
            task="product_recommendation",
            constraints={
                "budget": _parse_budget(t),
                "material": material,
                "brand": None,
                "category": _infer_category(t),
                "eco_friendly": bool(re.search(r"\b(eco|green|natural|plant[- ]based|non[- ]toxic)\b", t)) or None,
                "qualitative_features": qualitative,
            },
            safety_flags=flags,
            needs_live=bool(re.search(_LIVE_PATTERNS, t)),
        )
    if name == "PlanOutput":
        router = ctx.get("router") or {}
        c = router.get("constraints") or {}
        sources = ["rag.search"] + (["web.search"] if router.get("needs_live") else [])
        return schema(
            sources=sources,
            retrieval_filters={
                "category": c.get("category"),
                "max_price": c.get("budget"),
                "material": c.get("material"),
                "eco_friendly": c.get("eco_friendly"),
            },
            comparison_criteria=["price", "features", "ingredients", "eco-friendliness"],
        )
    if name == "RerankOutput":
        cands = ctx.get("candidates") or []
        # Rating-first ranking only works when the catalog actually has ratings.
        # The Amazon-2020 dump has no rating column, so `-(rating or 0)` ties
        # every row at 0 and silently degrades to cheapest-first — which put a
        # 14-piece toddler puzzle above 1000-piece ones for "challenging for
        # adults". With no ratings anywhere, semantic score is the honest
        # signal: it is what encodes the qualitative part of the request.
        has_ratings = any(isinstance(p.get("rating"), (int, float)) for p in cands)
        if has_ratings:
            ranked = sorted(cands, key=lambda p: (-(p.get("rating") or 0), p.get("price") or 1e9))
            rationale = "[mock] Ranked by rating (desc) then price (asc) within the applied filters."
        else:
            ranked = sorted(cands, key=lambda p: -(p.get("score") or 0))
            rationale = ("[mock] No ratings in this catalog — ranked by semantic "
                         "relevance (retrieval score) within the applied filters.")
        return schema(
            ranked_doc_ids=[p["doc_id"] for p in ranked[:max(
                1, min(int(ctx.get("top_k") or 6), 8)
            )]],
            rationale=rationale,
        )
    if name == "AnswerOutput":
        picks = ctx.get("top_picks") or []
        if not picks:
            return schema(
                spoken_answer="I couldn't find a matching product in the catalog or on the web. Try rephrasing your request.",
                top_pick_doc_id="",
                citation_doc_ids=[],
            )
        top = picks[0]
        price = f"${top['price']:.2f}" if isinstance(top.get("price"), (int, float)) else "an unlisted price"
        return schema(
            spoken_answer=(
                f"My top pick is {top.get('title')} by {top.get('brand') or 'an unbranded maker'} — "
                f"typically {price}. I compared {len(picks)} grounded option"
                f"{'s' if len(picks) != 1 else ''}."
            ),
            answer_detail=f"{top.get('title')} is the top grounded option at {price}.",
            top_pick_doc_id=top["doc_id"],
            citation_doc_ids=[p["doc_id"] for p in picks],
        )
    raise ValueError(f"Mock provider has no heuristic for schema {name}")
