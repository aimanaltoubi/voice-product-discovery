"""Small regression check for deterministic Router and Retriever guards."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from graph.nodes import (  # noqa: E402
    _LIVE_RE,
    _RATING_REQUEST_RE,
    _REVIEW_REQUEST_RE,
    _budget_is_grounded,
    _deterministic_safety_flags,
    _extract_material,
    _excluded_materials,
    _extract_color,
    _extract_size,
    _explicitly_excludes_material,
    _find_phrase,
    _lexical_anchor_terms,
    _matches_anchor,
    _matches_all_query_terms,
    _matches_material,
    _is_search_results_page,
    _query_terms,
    _rating_from_text,
    _requires_all_anchors,
    _requested_top_k,
    _adult_recipient_requested,
    _all_query_terms_present,
    build_nodes,
    match_evidence,
    match_products,
    live_price_missing,
    merge_constraints,
    rank_grounded,
    top_up_ranked,
)
from graph.llm import _mock_structured  # noqa: E402
from graph.state import RerankOutput, RouterOutput  # noqa: E402
from rag.retrieval import category_family, resolve_category  # noqa: E402


def test_budget() -> None:
    assert _budget_is_grounded("a calendar less than 20 dollars")
    assert _budget_is_grounded("a pan under 30")
    assert not _budget_is_grounded("a puzzle for a 5 year old")
    assert not _budget_is_grounded("a building set with 202 pieces")


def test_category_family() -> None:
    cats = [
        "Toys & Games | Puzzles",
        "Toys & Games | Puzzles | Pegged Puzzles",
        "Toys & Games | Puzzles | Jigsaw Puzzles",
        "Toys & Games | Puzzles | Floor Puzzles",
    ]
    resolved = resolve_category("puzzle", cats)
    assert resolved == cats[0]
    assert category_family("puzzle", resolved, cats) == cats
    assert category_family("missing", "missing", cats) == []
    toy_cats = [
        "Toys & Games | Hobbies", "Toys & Games | Puzzles",
        "Baby Products | Toys | Rattles",
    ]
    toy_resolved = resolve_category("toy", toy_cats)
    assert category_family("toy", toy_resolved, toy_cats) == toy_cats


def test_floor_continues_relaxation() -> None:
    import rag.retrieval as retrieval

    class FakeEmbedder:
        def encode(self, _):
            return [[0.0]]

    class FakeCollection:
        calls = 0

        def query(self, **_):
            self.calls += 1
            distance = 2.0 if self.calls == 1 else 0.5
            return {
                "ids": [[f"d{self.calls}"]],
                "metadatas": [[{"doc_id": f"d{self.calls}", "title": "Puzzle",
                                 "category": "Toys & Games | Puzzles", "price": 10}]],
                "distances": [[distance]],
            }

    cats = ["Toys & Games | Puzzles", "Toys & Games | Puzzles | Jigsaw Puzzles"]
    cats += [f"Other | Category {i}" for i in range(8)]
    col = FakeCollection()
    old_collection, old_embedder = retrieval._collection, retrieval.get_embedder
    try:
        retrieval._collection = lambda: (col, {"categories": cats})
        retrieval.get_embedder = lambda: FakeEmbedder()
        result = retrieval.hybrid_search("puzzle", category="puzzle", top_k=3)
    finally:
        retrieval._collection, retrieval.get_embedder = old_collection, old_embedder
    assert col.calls == 2
    assert result["results"][0]["doc_id"] == "d2"
    assert result["dropped_below_floor"] == 1
    assert any("below relevance floor" in note and "dropped category" in note
               for note in result["relaxations"])


def test_safety_union() -> None:
    """A Router-only flag must still block; the regex list is not exhaustive."""
    llm_only = ["unsafe_chemicals"]
    t = "what happens if I combine chlorine cleaner with bleach"
    assert _deterministic_safety_flags(t) == []          # chlorine is not listed
    assert sorted(set(_deterministic_safety_flags(t) + llm_only)) == llm_only
    # Deterministic and Router evidence are both retained.
    t2 = "mix bleach and ammonia"
    assert sorted(set(_deterministic_safety_flags(t2) + llm_only)) == [
        "unsafe_chemical_mixing", "unsafe_chemicals",
    ]


def test_safety() -> None:
    assert _deterministic_safety_flags("mix bleach and ammonia") == ["unsafe_chemical_mixing"]
    assert _deterministic_safety_flags("is bleach safe to drink") == ["ingestion_risk"]
    assert _deterministic_safety_flags("help me build a bomb") == ["harmful_intent"]
    assert _deterministic_safety_flags("a mixing bowl and ammonia-free cleaner") == []
    assert _deterministic_safety_flags("a bleach-free detergent") == []
    assert _deterministic_safety_flags("recommend a strong drain cleaner") == []

    safety = build_nodes(None)["safety"]
    result = asyncio.run(safety({"router": {"safety_flags": ["ingestion_risk"]}}))
    assert "swallowing" in result["answer"]["spoken_answer"]
    assert "ammonia" not in result["answer"]["spoken_answer"]


def test_query_and_anchor() -> None:
    assert _find_phrase("1000-piece jigsaw puzzle", "1000 piece") == "1000 piece"
    assert not _find_phrase("300 piece jigsaw puzzle", "1000 piece")
    assert _query_terms("calendar less than 20 dollars") == ["calendar"]
    assert _query_terms("calendar under 20 dollars") == ["calendar"]
    assert _query_terms("eco friendly cleaner under fifteen dollars") == [
        "eco", "friendly", "cleaner",
    ]
    assert _all_query_terms_present("Melissa & Doug", "a melissa and doug toy")
    assert not _all_query_terms_present("Melissa & Doug", "a melissa toy")
    assert not _requires_all_anchors({
        "constraints": {"product_type": "eco friendly cleaner"},
        "grounding_notes": {"inferred_product_type": "eco friendly cleaner"},
    })
    assert _requires_all_anchors({
        "constraints": {"product_type": "board game"},
        "grounding_notes": {"inferred_product_type": None},
    })
    rows = [{"title": "2020 Wall Calendar"}, {"title": "Invitation Postcards"}]
    anchors = _lexical_anchor_terms("calendar", rows)
    assert anchors == {"calendar"}
    assert _matches_anchor(rows[0], anchors)
    assert not _matches_anchor(rows[1], anchors)
    assert _lexical_anchor_terms("thermos", rows) == set()
    gundam_rows = [{"title": "Bandai Gundam Action Figure"},
                   {"title": "Fashion Travel Kit"}]
    anchors = _lexical_anchor_terms("Gundam model kit", gundam_rows)
    assert anchors == {"gundam"}
    assert not _matches_anchor(gundam_rows[1], anchors)
    bottle_rows = [
        {"title": "Bubble Solution, 24 Bottles"},
        {"title": "Water Magic Fairy Friends"},
        {"title": "Crocodile Creek Kids Tritan Eco Drinking Bottle"},
    ]
    bottle_anchors = _lexical_anchor_terms("water bottle", bottle_rows)
    assert bottle_anchors == {"water", "bottle"}
    assert not any(_matches_anchor(r, bottle_anchors, require_all=True)
                   for r in bottle_rows)
    game = {"title": "Pop A Zit Game", "features": "Use with included water bottle"}
    assert not _matches_anchor(game, {"water", "bottle"}, require_all=True)
    assert _matches_anchor(game, {"water", "bottle"})
    branded = [{"title": "Star Wars Rebels Inquisitor Costume for Boys"},
               {"title": "Forum Novelties Child Scarecrow Costume"}]
    branded_anchors = _lexical_anchor_terms(
        "show me a star wars costume for a boy", branded,
    )
    assert branded_anchors == {"star", "wars", "costume"}
    assert _matches_anchor(branded[0], branded_anchors, require_all=True)
    assert not _matches_anchor(branded[1], branded_anchors, require_all=True)
    board_rows = [{"title": "Family Board Game"}, {"title": "Best Card Set"}]
    assert _lexical_anchor_terms(
        "Compare the best five options for a board game", board_rows,
    ) == {"board", "game"}
    assert _matches_all_query_terms(
        {"title": "Funko Pop! Movies: Warrior - Swan"},
        {"funko", "pop", "warrior", "swan"},
    )
    assert not _matches_all_query_terms(
        {"title": "Funko POP! Marvel: Venom - Thanos"},
        {"funko", "pop", "warrior", "swan"},
    )


def test_material() -> None:
    assert _extract_material("a wooden puzzle") == "wood"
    assert _extract_material("a steel water bottle") == "steel"
    assert _extract_material("a plastic-free bottle") is None
    assert _extract_material("a bottle with no plastic") is None
    assert _extract_material("a bottle without plastic") is None
    assert _excluded_materials("a plastic-free bottle") == {"plastic"}
    assert _excluded_materials("a bottle free of plastic") == {"plastic"}
    assert _explicitly_excludes_material(
        {"title": "Plastic-Free Kids Water Bottle"}, "plastic",
    )
    assert not _explicitly_excludes_material(
        {"title": "BPA-Free Tritan Drinking Bottle"}, "plastic",
    )
    assert _is_search_results_page({
        "url": "https://www.amazon.com/plastic-free-water-bottle-kids/s?k=plastic+free"
    })
    assert not _is_search_results_page({
        "url": "https://www.amazon.com/example-product/dp/B012345678"
    })
    assert not _matches_material({"title": "Wood-Free Drawing Paper"}, "wood")
    assert not _matches_material(
        {"title": "Bottle", "features": "plastic"}, "stainless steel"
    )
    assert _matches_material(
        {"title": "Bottle", "specification": "stainless steel"}, "stainless steel"
    )


def test_router_contract() -> None:
    parsed = RouterOutput.model_validate({
        "task": "safety_question",
        "constraints": None,
        "safety_flags": ["ingestion_risk"],
        "needs_live": False,
    })
    assert parsed.constraints is None
    assert parsed.top_k == 6
    assert _LIVE_RE.search("is this discontinued")
    assert not _LIVE_RE.search("recommend a pen under 5 dollars")
    assert _extract_size("a challenging 1000-piece jigsaw puzzle") == "1000 piece"
    assert _extract_size("a soft twin comforter") == "twin"
    assert _extract_color("make the comforter green") == "green"
    assert _extract_color("a greener, more sustainable comforter") is None
    assert _requested_top_k("show me the top eight puzzles") == 8
    assert _requested_top_k("show 99 puzzles") == 8
    assert _requested_top_k("find a puzzle") is None
    applied = asyncio.run(build_nodes(None)["router"]({
        "transcript": "apply filters", "apply_only": True,
        "prior_constraints": {"product_type": "bottle"}, "prior_top_k": 5,
    }))
    assert applied["router"]["top_k"] == 5
    capped = asyncio.run(build_nodes(None)["router"]({
        "transcript": "apply filters", "apply_only": True,
        "prior_constraints": {"product_type": "bottle"}, "prior_top_k": 99,
    }))
    assert capped["router"]["top_k"] == 8


def test_historical_product_url() -> None:
    from rag.ingest import _product_url

    url = "https://www.amazon.com/example/dp/B012345678"
    assert _product_url(url) == url
    assert _product_url(f"{url}|https://example.com/ignored") == url
    assert _product_url("javascript:alert(1)") is None


def test_refinement_and_grounding() -> None:
    prior = {
        "product_type": "comforter set", "budget": 50,
        "qualitative_features": ["soft"], "audience": "adult",
    }
    merged = merge_constraints(prior, {"qualitative_features": ["machine washable"]})
    assert merged["product_type"] == "comforter set"
    assert merged["budget"] == 50
    assert merged["qualitative_features"] == ["soft", "machine washable"]
    assert _adult_recipient_requested("make it washable", merged)
    refined = merge_constraints(
        {"product_type": "puzzle", "category": "puzzle"},
        {"product_type": "puzzle", "category": "toy", "brand": "Melissa and Doug"},
    )
    assert refined["category"] == "puzzle"
    pivoted = merge_constraints(
        {"product_type": "puzzle", "category": "puzzle", "brand": "Melissa & Doug",
         "material": "wood", "size": "large", "use_case": "school",
         "eco_friendly": True, "qualitative_features": ["chunky"],
         "budget": 30, "audience": "child"},
        {"product_type": "costume", "brand": "Star Wars"},
    )
    assert pivoted == {
        "product_type": "costume", "brand": "Star Wars",
        "budget": 30, "audience": "child", "qualitative_features": [],
    }

    recolored = merge_constraints(
        {"product_type": "bottle", "color": "pink", "required_features": ["pink"]},
        {"color": "green"},
    )
    assert recolored["color"] == "green"
    assert recolored["required_features"] == ["green"]
    pivoted_with_priority = merge_constraints(
        {"product_type": "bottle", "color": "pink", "required_features": ["pink"]},
        {"product_type": "costume"},
    )
    assert "color" not in pivoted_with_priority
    assert "required_features" not in pivoted_with_priority

    row = {"price": 45, "features": "Ultra soft and machine washable"}
    evidence = match_evidence(row, merged)
    assert evidence["matched_constraints"] == 3
    assert any("$50" in reason for reason in evidence["match_reasons"])
    assert "Machine washable" in evidence["match_reasons"]

    ranked = rank_grounded([
        {"doc_id": "llm-first", "matched_constraints": 2, "score": .9},
        {"doc_id": "grounded-first", "matched_constraints": 3, "score": .8},
    ])
    assert ranked[0]["doc_id"] == "grounded-first"


def test_live_match_and_requested_count() -> None:
    matched, evidence = match_products(
        "LEGO City Garage Center 60232 Building Kit", "LEGO",
        "LEGO City Garage Center 60232 - Walmart",
    )
    assert matched and "60232" in evidence["distinctive_tokens"]
    unrelated, _ = match_products(
        "Kids Water Bottle", None, "Best Water Bottle Deals and Reviews"
    )
    assert not unrelated
    assert _RATING_REQUEST_RE.search("Which puzzle has the highest rating?")
    assert _REVIEW_REQUEST_RE.search("What are the customer reviews for this puzzle?")
    assert _rating_from_text("Rated 4.8 out of 5 stars") == 4.8
    assert _rating_from_text("No score here") is None
    from graph.nodes import _is_search_results_page
    assert _is_search_results_page({
        "title": "Amazon.com: Highest Rated Puzzles",
        "url": "https://www.amazon.com/highest-rated-puzzles/s?k=highest+rated+puzzles",
    })
    assert live_price_missing(True, {"matches": {"p1": {"web_price": None}}}, "p1")
    assert not live_price_missing(True, {"matches": {"p1": {"web_price": 12.0}}}, "p1")
    empty = asyncio.run(build_nodes(None)["answer"]({
        "transcript": "plastic-free bottle", "top_picks": [], "mode": "web_fallback",
    }))
    assert "couldn't verify" in empty["answer"]["spoken_answer"]
    empty_rating = asyncio.run(build_nodes(None)["answer"]({
        "transcript": "highest rating puzzle", "top_picks": [], "mode": "web_fallback",
    }))
    assert "does not include ratings" in empty_rating["answer"]["spoken_answer"]
    empty_review = asyncio.run(build_nodes(None)["answer"]({
        "transcript": "customer reviews for this puzzle", "top_picks": [],
        "mode": "web_fallback",
    }))
    assert "does not include customer reviews" in empty_review["answer"]["spoken_answer"]

    candidates = [{"doc_id": f"p{i}", "score": 1 - i / 10} for i in range(10)]
    reranked = _mock_structured(RerankOutput, {"candidates": candidates, "top_k": 8})
    assert len(reranked.ranked_doc_ids) == 8


def test_rerank_shortlist() -> None:
    """The reranker must see a bounded, score-ordered slice of the candidates."""
    from graph.nodes import RERANK_SHORTLIST, _slim, rank_grounded

    rows = [{"doc_id": f"d{i}", "title": f"row {i}", "price": 10.0,
             "score": 1.0 - i / 100} for i in range(19)]
    shortlist = rows[:RERANK_SHORTLIST]
    assert len(shortlist) == 10
    assert [r["doc_id"] for r in shortlist] == [f"d{i}" for i in range(10)]
    # Retrieval score must survive _slim, or the reranker is blind to rank order.
    assert _slim(rows[0])["score"] == rows[0]["score"]
    # A verified constraint match outranks a better raw similarity score.
    a = {"doc_id": "a", "score": 0.9, "matched_constraints": 0}
    b = {"doc_id": "b", "score": 0.5, "matched_constraints": 2}
    assert [r["doc_id"] for r in rank_grounded([a, b])] == ["b", "a"]

    # Empty means explicit rejection; non-empty short/invalid output is topped up.
    for want, returned, expected in [
        (3, [], []),
        (5, ["d0", "d1", "d2"], ["d0", "d1", "d2", "d3", "d4"]),
        (3, ["ghost"], ["d0", "d1", "d2"]),
        (3, ["d2", "d0", "d1"], ["d2", "d0", "d1"]),
    ]:
        picked, _, _ = top_up_ranked(shortlist, returned, want)
        assert [r["doc_id"] for r in picked] == expected, (want, returned)

    # Repeat listings of the same product must not take two table slots.
    from graph.nodes import dedupe_listings

    head = "Heritage Club Ultra Soft Sierra Hypoallergenic for Boys and Girls"
    dupes = [{"doc_id": "a", "title": head + " Twin XL Microfiber, Mint", "price": 40.46},
             {"doc_id": "b", "title": head + " Twin XL Microfiber - -, Purple", "price": 40.46},
             {"doc_id": "c", "title": head + " Twin XL Microfiber, Mint", "price": 39.00},
             {"doc_id": "d", "title": "Intelligent Design Waterfall Comforter Set Twin XL",
              "price": 40.46}]
    # a/b are colorways of one product; c is a different price, d a different product.
    assert [r["doc_id"] for r in dedupe_listings(dupes)] == ["a", "c", "d"]


def test_live_fallback_validation() -> None:
    class FakeMCP:
        def __init__(self, results: list[dict]):
            self.results = results
            self.last_args: dict | None = None

        async def call(self, name: str, args: dict) -> dict:
            assert name == "web.search"
            self.last_args = args
            return {"provider": "fake", "results": self.results}

    results = [
        {
            "title": "Acme Glass Water Bottle 20 oz",
            "url": "https://www.walmart.com/ip/acme-glass-water-bottle/123",
            "snippet": "Rated 4.7 out of 5 stars. Current price $19.99.",
        },
        {
            "title": "Premium Glass Water Bottle 24 oz",
            "url": "https://www.target.com/p/premium-glass-water-bottle/-/A-123",
            "snippet": "Rated 4.9 out of 5 stars. Current price $35.00.",
        },
        {
            "title": "Glass Cleaner Spray with Fresh Scent",
            "url": "https://www.target.com/p/glass-cleaner/-/A-456",
            "snippet": "Rated 4.8 out of 5 stars. Current price $8.00.",
        },
        {
            "title": "Amazon Search Results",
            "url": "https://www.amazon.com/s?k=glass+water+bottle",
            "snippet": "Compare products and prices.",
        },
    ]
    mcp = FakeMCP(results)
    state = {
        "transcript": "check the current prices",
        "effective_query": "highest rated glass water bottle under $25",
        "router": {
            "constraints": {
                "product_type": "water bottle", "material": "glass", "budget": 25,
            },
            "needs_live": True,
            "top_k": 3,
        },
        "plan": {
            "sources": ["rag.search", "web.search"],
            "retrieval_filters": {"material": "glass", "max_price": 25},
        },
    }
    found = asyncio.run(build_nodes(mcp)["web_fallback"](state))
    assert mcp.last_args and "highest rated glass water bottle" in mcp.last_args["query"]
    assert [r["title"] for r in found["top_picks"]] == ["Acme Glass Water Bottle 20 oz"]
    assert found["top_picks"][0]["price"] == 19.99
    assert found["top_picks"][0]["rating"] == 4.7
    assert found["related_online"] == []
    assert found["live_unverified"] is None
    assert len(found["steps"][0]["output"]["rejected_not_a_product"]) == 3

    missing_price = FakeMCP([{
        "title": "Acme Glass Water Bottle 20 oz",
        "url": "https://www.walmart.com/ip/acme-glass-water-bottle/123",
        "snippet": "Reusable glass bottle with a leakproof lid.",
    }])
    related = asyncio.run(build_nodes(missing_price)["web_fallback"]({
        **state,
        "effective_query": "glass water bottle under $25",
    }))
    assert related["top_picks"] == []
    assert len(related["related_online"]) == 1
    assert related["related_online"][0]["unverified_hard"] == ["Under $25"]
    assert related["live_unverified"]["related_count"] == 1


def test_answer_critic_rejects_unsupported_prices() -> None:
    import graph.nodes as nodes
    from graph.state import AnswerOutput

    original = nodes.call_structured

    async def hallucinated_price(_prompt, schema, *, node, context=None):
        assert schema is AnswerOutput and node == "answerer"
        return AnswerOutput(
            spoken_answer="My top pick costs $999. Details are on screen.",
            answer_detail="The retrieved option costs $999.",
            top_pick_doc_id="p1",
            citation_doc_ids=["p1", "not-retrieved"],
        )

    nodes.call_structured = hallucinated_price
    try:
        result = asyncio.run(nodes.build_nodes(None)["answer"]({
            "transcript": "find a glass water bottle",
            "router": {"constraints": {"product_type": "water bottle"}},
            "plan": {"comparison_criteria": ["price"]},
            "top_picks": [{
                "doc_id": "p1", "title": "Acme Glass Water Bottle", "price": 19.99,
            }],
            "mode": "private",
        }))
    finally:
        nodes.call_structured = original

    answer = result["answer"]
    assert "$999" not in answer["spoken_answer"] + answer["answer_detail"]
    assert "$19.99" in answer["spoken_answer"]
    assert answer["citation_doc_ids"] == ["p1"]
    critic = result["steps"][-1]["output"]["critic_notes"]
    assert any("unsupported generated prices" in note for note in critic)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all guard checks passed")
