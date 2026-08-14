# Prompt Disclosure

This folder satisfies the brief's **Prompt Disclosure** requirement (5 pts):
it contains every prompt used by the agents, and these files are **not copies**
— they are loaded at runtime by `backend/graph/prompts.py`, so what you read
here is exactly what the LLM receives.

Placeholders use `<<name>>` syntax and are substituted by the node before the
LLM call (see `render()` in `backend/graph/prompts.py`).

## Prompt → node / tool mapping

| Prompt file | LangGraph node | Structured output schema | Placeholders |
|---|---|---|---|
| `system.md` | prepended to **every** LLM call (system message) | — | — |
| `router.md` | `router` (Intent Classifier) | `RouterOutput` | `<<transcript>>`, `<<few_shots>>` |
| `few_shots_router.md` | injected into `router.md` | — | — |
| `planner.md` | `planner` | `PlanOutput` | `<<router_json>>`, `<<transcript>>` |
| `reranker.md` | `retrieve` (the LLM rerank stage of `rag.search`) | `RerankOutput` | `<<transcript>>`, `<<criteria_json>>`, `<<candidates_json>>` |
| `answerer.md` | `answer` (Answerer/Critic) | `AnswerOutput` | `<<transcript>>`, `<<criteria_json>>`, `<<mode>>`, `<<products_json>>`, `<<web_json>>` |

## Non-LLM (deterministic) steps

Three pipeline steps intentionally use **no prompt** because they are
deterministic code, which is more auditable than an LLM for these jobs:

- `safety` — hard block + fixed safe refusal whenever the router raised
  `safety_flags` (`backend/graph/nodes.py`).
- `reconcile` — SKU/brand/title fuzzy matching between catalog and live web
  results + price-delta discrepancy flags (`backend/graph/nodes.py`).
- MCP tools `web.search` and `rag.search` — pure retrieval, no generation
  (`backend/mcp_server/server.py`).

## Planner rubric (as required by the brief)

The planner rule from the brief — *"prefer rag.search for facts; if user asks
'current price/availability/now/latest,' also call web.search"* — is stated in
`planner.md` **and** enforced deterministically after the LLM call in
`planner_node` (the graph guarantees `rag.search` is always present and adds
`web.search` whenever the router set `needs_live=true`, even if the LLM plan
omitted it). Enforcement events appear in the agent step log.
