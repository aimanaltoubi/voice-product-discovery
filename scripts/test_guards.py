"""Small regression check for deterministic Router and Retriever guards."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from graph.nodes import (  # noqa: E402
    _LIVE_RE,
    _budget_is_grounded,
    _deterministic_safety_flags,
    _extract_material,
    _lexical_anchor_terms,
    _matches_anchor,
    _matches_material,
    _query_terms,
)
from graph.prompts import load  # noqa: E402
from graph.state import RouterOutput  # noqa: E402


def test_budget() -> None:
    assert _budget_is_grounded("a calendar less than 20 dollars")
    assert _budget_is_grounded("a pan under 30")
    assert not _budget_is_grounded("a puzzle for a 5 year old")
    assert not _budget_is_grounded("a building set with 202 pieces")


def test_safety() -> None:
    assert _deterministic_safety_flags("mix bleach and ammonia") == ["unsafe_chemical_mixing"]
    assert _deterministic_safety_flags("a mixing bowl and ammonia-free cleaner") == []
    assert _deterministic_safety_flags("a bleach-free detergent") == []


def test_query_and_anchor() -> None:
    assert _query_terms("calendar less than 20 dollars") == ["calendar"]
    assert _query_terms("calendar under 20 dollars") == ["calendar"]
    rows = [{"title": "2020 Wall Calendar"}, {"title": "Invitation Postcards"}]
    anchors = _lexical_anchor_terms("calendar", rows)
    assert anchors == {"calendar"}
    assert _matches_anchor(rows[0], anchors)
    assert not _matches_anchor(rows[1], anchors)


def test_material() -> None:
    assert _extract_material("a wooden puzzle") == "wood"
    assert not _matches_material(
        {"title": "Bottle", "features": "plastic"}, "stainless steel"
    )
    assert _matches_material(
        {"title": "Bottle", "specification": "stainless steel"}, "stainless steel"
    )


def test_router_contract() -> None:
    assert "FORMAT ONLY" in load("few_shots_router")
    parsed = RouterOutput.model_validate({
        "task": "safety_question",
        "constraints": None,
        "safety_flags": ["ingestion_risk"],
        "needs_live": False,
    })
    assert parsed.constraints is None
    assert _LIVE_RE.search("is this discontinued")
    assert not _LIVE_RE.search("recommend a pen under 5 dollars")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all guard checks passed")
