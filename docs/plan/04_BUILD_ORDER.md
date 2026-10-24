# Build order (relative; today = Oct 25 IST, deadline UNCONFIRMED — ask user, do not invent)

0. Setup: read AGENTS.md, write SESSION START in log.txt (tool=, model= real), .env with API key, .gitignore .env, venv ignored. Confirm deadline with user.
1. Scorer first: compare against sample_output.csv with status acc, step-level P/R, field-level, sequence (exact + LCS). Run after every change and paste numbers in chat.
2. Parsers for biggest families (JSON, key=value logs, SQL replicas) -> observation store. Identity graph. get_state_at.
3. Minimal agent loop (3 tools, 8-call cap, validator, offline cache) BEFORE the policy engine is finished; add each tool as it lands.
4. Policy engine + step builder. Goal: 22 generated samples exact, then 32/32 (use 01-10 for format).
5. Validated 518-row output.csv from deterministic path, COMMIT before sleeping (insurance).
6. After sleep: account linker + elimination; remaining parser families (regional/reporting replicas, export manifest, notification log, connector configs); invariant checks (every deletion case's identifiers appear in a pre-audit file: 505/518 today, 13 exceptions = 9 NOT_APPLICABLE + cases 39,55,61,63 via LINK).
7. Images via vision model (11 calls, cached), LINK bridges, marketing conflict rule. Inferred categories (legal hold, marketing, object storage) max 30 min. Contact-lookup category rule.
8. LLM roles in the loop; measure agreement/override.
9. Reports (code/evaluation/), README at repo root, >=3 custom cases (decoy email, sync recreation, post-audit evidence, retention expired, replica drift).
10. Final run (this is the run usage_report describes) -> output.csv, code.zip, log.txt, trace zip if required -> submit. Keep ~1h for interview prep.
Tests: tests/test_samples.py with one assertion per rule in 01_SPEC.md. No case ids in source.
