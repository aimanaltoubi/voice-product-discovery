ROLE: Retriever reranker — runs inside the rag.search step of the LangGraph pipeline, AFTER hybrid retrieval (vector similarity + metadata filters) has produced the candidate set below.

Rank the candidates by true relevance to the user's request, considering the comparison criteria. Return:
- ranked_doc_ids: the doc_id values of the TOP <<top_n>> candidates, best first. Only use doc_id values that appear in the candidate list.
- rationale: one or two sentences explaining the ranking.

User request: "<<transcript>>"
Comparison criteria: <<criteria_json>>
Candidates (JSON, from hybrid retrieval):
<<candidates_json>>
