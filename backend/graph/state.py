"""Shared graph state + structured-output schemas.

`DiscoveryState` is the single state object threaded through the LangGraph
nodes. The Pydantic models define the JSON contracts the LLM must return at
each reasoning node; they mirror the instructions in `prompts/*.md` exactly
(the prompts are the human-readable side of the same contract). Deterministic
nodes (safety, reconcile) write plain dicts.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from typing_extensions import TypedDict


# --------------------------------------------------------------------------
# LangGraph state
# --------------------------------------------------------------------------

class DiscoveryState(TypedDict, total=False):
    transcript: str                   # the latest utterance only
    # --- search session (refinement) ---
    prior_constraints: dict[str, Any]  # merged constraints carried in by the client
    prior_top_k: int
    apply_only: bool                   # use prior_constraints verbatim, skip extraction
    prior_needs_live: bool
    applied_changes: list[str]
    effective_query: str              # accumulated intent -> the actual search text
    constraint_changes: list[str]      # what this refinement changed
    router: dict[str, Any]            # RouterOutput.model_dump()
    plan: dict[str, Any]              # PlanOutput.model_dump() + enforcement notes
    candidates: list[dict[str, Any]]  # raw rag.search rows
    top_picks: list[dict[str, Any]]   # qualified reranked rows (<=8), or web fallback rows
    web: dict[str, Any]               # web.search payload (compare or fallback)
    reconciliation: dict[str, Any]    # discrepancy flags per doc_id
    answer: dict[str, Any]            # AnswerOutput.model_dump()
    mode: str                         # "private" | "web_fallback" | "no_match"
    blocked: bool
    # Set when the request was too broad to rank responsibly; the graph stops
    # at the clarify node and asks exactly one question.
    clarify: dict[str, Any]
    # Set when a HARD constraint (currently budget) eliminated every candidate.
    # Presence of this key routes retrieve -> answer instead of the web
    # fallback, so an over-budget product is never dressed up as a match.
    no_match: dict[str, Any]
    # Set when web.search returned pages but none qualified as a product.
    live_unverified: dict[str, Any]
    related_online: list[dict[str, Any]]
    # Every node appends its own entries; operator.add concatenates them
    # in execution order for the UI's agent step log.
    steps: Annotated[list[dict[str, Any]], operator.add]


# --------------------------------------------------------------------------
# Structured-output contracts (see prompts/README.md for the mapping)
# --------------------------------------------------------------------------

class Constraints(BaseModel):
    """Constraints the router extracts from the utterance (prompts/router.md).

    Split by how they can be enforced:
      HARD  — budget, brand, size: checkable against structured metadata or
              literal product text, so they may gate a recommendation.
      SOFT  — qualitative_features: adjectives ("soft", "easy to wash") that
              steer semantic ranking. They are never used to reject a product,
              and are only shown as a match chip when the retrieved evidence
              literally supports them.
    """
    product_type: Optional[str] = Field(
        default=None, description="What the user is shopping for, e.g. 'comforter set'.")
    audience: Optional[str] = Field(
        default=None,
        description="Who the product is for, in the user's own terms: "
                    "'adult', 'child', 'toddler', 'baby', 'family', 'gift'. "
                    "Only when the user said or clearly implied it.")
    use_case: Optional[str] = Field(
        default=None,
        description="How it will be used: 'gym', 'school', 'travel', 'hiking', "
                    "'office', 'everyday'. Only when stated.")
    budget: Optional[float] = Field(
        default=None, description="Budget ceiling in USD, if stated.")
    size: Optional[str] = Field(
        default=None, description="Size/variant if stated, e.g. 'twin', '1000 piece'.")
    qualitative_features: List[str] = Field(
        default_factory=list,
        description="Soft preferences as short phrases, e.g. ['soft', 'easy to wash'].")
    # Set by the UI's "Edit priorities" control, not by the LLM: a subset of
    # qualitative_features the user promoted to must-have. A product with no
    # literal evidence for one of these is not an exact match.
    required_features: List[str] = Field(default_factory=list)
    color: Optional[str] = Field(
        default=None,
        description="Desired colour as a single lowercase word ('pink', 'green', "
                    "'navy'). Colours are mutually exclusive, so a new colour "
                    "REPLACES the previous one.")
    material: Optional[str] = Field(
        default=None, description="Surface/material, e.g. 'stainless steel'.")
    brand: Optional[str] = None
    category: Optional[str] = Field(
        default=None, description="Product category, e.g. 'cleaner'.")
    eco_friendly: Optional[bool] = Field(
        default=None, description="True only if eco/green/natural was implied.")

    @field_validator("qualitative_features", mode="before")
    @classmethod
    def normalize_null_features(cls, value):
        return value or []


class RouterOutput(BaseModel):
    """Intent + constraint extraction, plus safety and freshness flags."""
    task: str = Field(
        default="product_recommendation",
        description="Short task label, e.g. 'product_recommendation'.")
    constraints: Optional[Constraints] = Field(default=None)
    safety_flags: List[str] = Field(
        default_factory=list,
        description="Non-empty if the request seeks unsafe chemical advice.")
    needs_live: bool = Field(
        default=False,
        description="True if the user asked for current/latest price, stock or availability.")
    top_k: int = Field(
        default=6,
        description="How many qualified products to return, capped at 8. Default 6.")

    # A model may send an explicit `null` for a field it has nothing to say
    # about ("It's for a child." carries no task). Pydantic only applies a
    # default when the key is ABSENT, so an explicit null raises instead.
    # Treat null as "not answered" and fall back to the declared default.
    @field_validator("task", "safety_flags", "needs_live", "top_k", mode="before")
    @classmethod
    def _null_means_default(cls, v, info):
        if v is not None:
            return v
        return cls.model_fields[info.field_name].get_default(call_default_factory=True)


class RetrievalFilters(BaseModel):
    """Metadata filters for the private catalog (prompts/planner.md)."""
    category: Optional[str] = None
    max_price: Optional[float] = None
    material: Optional[str] = None
    eco_friendly: Optional[bool] = None


class PlanOutput(BaseModel):
    """Which MCP tools to call, with what filters and criteria."""
    sources: List[Literal["rag.search", "web.search"]] = Field(
        default_factory=lambda: ["rag.search"])
    retrieval_filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    comparison_criteria: List[str] = Field(
        default_factory=lambda: ["price", "features"])


class RerankOutput(BaseModel):
    """LLM reranking of hybrid-retrieval candidates (prompts/reranker.md)."""
    ranked_doc_ids: List[str] = Field(
        description="Top candidate doc_ids, best first, max 8. "
                    "Must be a subset of the provided candidate doc_ids.")
    rationale: str = ""


class AnswerOutput(BaseModel):
    """Final grounded answer (prompts/answerer.md)."""
    spoken_answer: str = Field(
        description="20-30 word summary that is READ ALOUD (<=15s). No doc_ids, "
                    "URLs, or full product titles.")
    answer_detail: str = Field(
        default="",
        description="2-3 sentence on-screen explanation of the recommendation.")
    top_pick_doc_id: str = Field(
        default="", description="doc_id of the top pick; must exist in the rows.")
    citation_doc_ids: List[str] = Field(default_factory=list)
