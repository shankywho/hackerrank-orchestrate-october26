# Architecture (what the code judge and the interviewer will probe)

Principle: model investigates/proposes, tools verify, validator enforces. LLM only where it adds information.

## Deterministic layer (code/)
normalizer (txt, JSON, logs+trace_id join, SQLite incl. replicas, pdftotext, vision-JSON cache for PNGs) -> observation store (file_id, captured_at, system_id, resource_id, category, state, subject_key) | identity graph (user_id -> current+historical email/phone/hash) | get_state_at(audit_at) | policy engine (category,state)->steps (see 01_SPEC.md) | step builder + `sort_key` | validator (schema, ordering, consecutive steps, evidence rules, ids only from candidate sets) | account linker.

## Account linker (own scored component; measure it on samples first)
1. Evidence-first: accept an account when exactly one account co-occurs with the user's identifiers (current/old email, hash, user_id) in the same trace_id/line/object.
2. Else activities: account_service DELETE_RECORD within reported_at -80..+10 min (hand-made family: +-10), result consistent with reported_status (SUCCESS=COMPLETE, PARTIAL=PARTIALLY_COMPLETE, FAILED=NOT_STARTED).
3. Iterative singleton elimination across all 550 requests (accept only unique; remove from others). ABSTAIN when ambiguous.
Measured: samples 24 correct / 0 wrong / 8 abstain (04,06,08 hand-made; 11,14,23,28,29 ties). On 518: 369 unique, 149 ambiguous (104 two-way). Unique != verified: expect ~90-100 wrong account steps.
3b. Hand-made-style cases only (01,02,04-08,10 in samples): ids are zero-padded account_0NN matching user_NN; use only when the user id is 2-digit and the evidence has no account for the email; log as assumption.
4. Ties: (a) LLM picks only from tied candidates with a reason; (b) weak signal: related activities after the account delete (DISABLE_DATA_FLOW, BLOCK_RESTORE, REDACT/ANONYMIZE) cluster in same batch (UNTESTED probe); (c) case_id-order vs event_time tie-break is OVERFIT (holds in 3/7 batches): last resort only, report ambiguous cases separately; (d) hand-made family only: user_NN -> account_0NN, log as assumption.

## Agent layer (build the minimal loop EARLY, ~hour 2; add tools as they exist)
One agent per case, small model, temperature 0, max 8 tool calls. Tools: get_case, classify_request, resolve_identity, find_resources, get_state_at, read_evidence, link_account (candidates only), propose_plan (runs deterministic planner). LLM roles: multilingual intent (cross-check vs CLASSIFY_REQUEST activity), vision extraction (11 images, cached JSON), tie adjudication among candidates, review of the deterministic plan. Gates it cannot override: deletion authorization, retention/legal hold, evidence cutoff, candidate-set membership. Log agreement and override rates. Cache by input hash. `--offline` replay from cache so graders without a key can run it. Token logging for usage_report.

## LLM provider (user decision: Groq)
Use Groq's OpenAI-compatible API, key in .env as GROQ_API_KEY (never logged or committed). Do NOT hardcode model names from memory: at build time call the models list endpoint, pick one text model that supports tool calling and one vision-capable model, and record the exact ids in config + usage_report. Temperature 0, max 8 tool calls per case.
Free/dev tier has tight tokens-per-minute and daily limits: cache every LLM call by input hash (on disk), cap concurrency (start 2-4 workers), exponential backoff on 429, and make the run resumable. Keep prompts short; the deterministic layer does the heavy lifting. Read token counts from each response's usage field for usage_report.md; compute cost from Groq's published per-token prices and state the prices and date used (or state free tier = $0 plus the list-price estimate).
Images: 11 PNGs only, one cached vision call each, output stored as .md or .py-readable text (the code judge ignores .json).
