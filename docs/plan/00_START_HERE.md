# START HERE — Ghost Data Still Exists (plan context for Claude Code)

Goal: rank #1. Solo, 24h. Owner: Shankar. Source of truth for the plan = these files (docs/plan/). Read all of them before building. If a doc conflicts with problem_statement.md / AGENTS.md, the repo files win; tell the user.

## Deliverables
- output.csv (repo root): 518 rows, columns case_id, deletion_status, steps_json. Sorted by case_id as STRING order (decision: spec says "sorted by case_id"; keep in one `sort_key` fn).
- code.zip: root README.md + code/ (include code/evaluation/evaluation_report.md and code/evaluation/usage_report.md). No dataset, venv, nested archives. Judge ignores .json/.csv/.log/.sql: keep prompts and metrics in .py/.md.
- log.txt (repo root, beside AGENTS.md): chat transcript; follow AGENTS.md section 5 EVERY turn; every entry has real tool= and model= lines. Re-read the entry after writing.
- Possibly agent-trace.zip (see agent_trace_instructions.md). AI interview afterwards.
- Deadline is NOT configured. Do not invent one. User will confirm on the HackerRank page.

## Scoring (HackerRank blog; weights may vary)
30% code (architecture 30, prompt/tool craft 30, robustness 25, eng rigor 15) | 30% output.csv vs hidden gold | 30% AI interview (depth/ownership 40, understanding 25, communication 20, honesty 15) | 10% chat log (ownership 35, specificity 25, iteration/verification 25, safety 15). Tie-break: interview, code, output, chat.
Past lesson: a deterministic-only solver scored 3/30 on code. A pure rules engine is a trap. Build a REAL bounded agent loop over deterministic tools.

## Thesis
1. Deterministic data layer gets the CSV right (normalizer, identity graph, observation store, policy engine, step builder, validator).
2. Bounded LLM agent per case (small model, temp 0, max 8 tool calls) calls those tools; model proposes, tools verify, validator enforces.
3. Honesty everywhere: never claim hidden-set accuracy. Report sample accuracy and invariant pass rate separately. Rules fitted on 32 samples that touch only 13 of 94 evidence files and 0 of 11 images.

## Hard rules for the build
- No case ids, user ids or resource ids hardcoded in source. No answers copied from sample_output by email (same person appears in samples and eval with different audit_at).
- Evidence captured AFTER audit_at is never used for state or citation. Inclusive: captured_at <= audit_at.
- Exact identifier matching only (case-insensitive). No fuzzy matching (decoys exist).
- Tell the user when a draft output exists, a limitation appears, or direction changes.

Files: 01_SPEC.md (rules), 02_TRAPS_AND_DATA.md (data facts), 03_ARCHITECTURE.md, 04_BUILD_ORDER.md, 05_REPORTS_LOG_INTERVIEW.md, 06_ARENA_LEDGER.md (decisions, open risks).
