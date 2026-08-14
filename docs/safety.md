# Safety & guardrails

## 1. Unsafe chemical advice — hard block

The router extracts `safety_flags` (unsafe mixing such as bleach + ammonia,
ingestion of cleaning products, harmful intent). Any non-empty flag routes
the graph to a **deterministic safety node** that:

- sets `blocked: true` (the UI shows the blocked banner),
- returns a fixed, safe spoken refusal that points to poison control and
  offers safe ready-made alternatives,
- skips retrieval, web search, and the LLM answerer entirely.

The answerer prompt additionally forbids unsafe chemical advice for the
non-blocked path, and the mock provider implements the same regex flags so
the guardrail is testable offline (`scripts/smoke_test.py`, scenario 3).

## 2. Web domain allowlist + polite crawling

- `web.search` filters results to `WEB_ALLOWED_DOMAINS` (retail/review
  sites; hostname suffix match). Non-strict mode logs
  `allowlist_relaxed: true` if filtering would leave zero results; strict
  mode never relaxes.
- We call **search-engine / provider APIs** (DuckDuckGo via `ddgs`, or
  Serper/Brave/Tavily) and read their snippets; the app does not scrape
  target retail pages, which keeps us on the right side of robots.txt/ToS
  for those sites. Provider terms apply to the provider call itself.
- Rate limits (`WEB_RATE_LIMIT_PER_MIN`) and the 60–300 s response cache
  keep request volume low.

## 3. No secrets in logs

- MCP tool logs (`backend/logs/mcp_server.jsonl`) contain tool arguments,
  truncated responses, timings, and source URLs — never environment
  variables or keys.
- Run logs (`backend/logs/runs/*.jsonl`) persist the user-visible payload
  (transcript, steps, answer) plus timing and the provider *names* only.
- `.env` is git-ignored; `.env.example` ships placeholders.

## 4. Grounding & honesty guardrails

- Reranker output is validated against the actual candidate set
  (hallucinated doc_ids are dropped and logged).
- The answerer may only cite provided rows; citations are intersected with
  known doc_ids and the top-pick id is validated in code.
- Reconciliation discrepancies (catalog vs live price > 15%, availability)
  must be spoken; if the LLM omits it, the critic code appends the notice
  and records `critic_notes` in the step log.
- Prompt injection surface: web snippets are treated as data (rendered in
  the table/citations), never as instructions; the answerer prompt scopes
  grounding to the structured rows.

## 5. Input hygiene

- `/api/discover` and `/api/speak` cap input length (2 000 chars);
  TTS text is additionally capped at 600 chars.
- Uploaded audio goes to a temp file that is always deleted after
  transcription.
