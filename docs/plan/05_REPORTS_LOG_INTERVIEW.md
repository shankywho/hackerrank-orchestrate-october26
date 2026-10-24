# Reports, chat log, interview

## Paths (decision)
README.md at repo root. Reports in code/evaluation/evaluation_report.md and code/evaluation/usage_report.md (CONFIRMED in README.md: reports go in code/evaluation/, bundled by `zip -r code.zip README.md code ...`; problem_statement.md's bare evaluation/ path is superseded by the README note). Keep prompts in .py or .md files; the code judge ignores .csv/.tsv/.json/.jsonl/.sql/.log.
evaluation_report.md: status accuracy + confusion; step-level P/R; field-level; sequence exact + LCS; >=3 custom cases; failures/fixes table; full-run invariants; account-linking stats (24/0/8 on samples; 369 unique/149 ambiguous on 518); list inferred rules. usage_report.md: provider/model, calls, tokens in/out, total/avg per request, cost, per-model + overall, cache hit rate.

## Honest wording (use in README, reports, interview)
"Rules are fitted on 32 samples that touch 13 of 94 evidence files and none of the 11 images. Other families are built from format inspection and checked by invariants, not gold. Ordering and decision table reach 32/32 on samples. Account linking resolves uniquely on 369 of 518 cases using timing heuristics. Image-derived cases were checked by hand. About 12 cases rest on inferred rules. Expected hidden accuracy is lower than the sample score." Never quote a hidden-set number.

## log.txt (10%)
Every turn per AGENTS.md section 5, real tool=/model=. Prompts must be owned and specific (paste scorer numbers, name the rule, say what you verified). Push back on wrong output. Mention safety (no hardcoded ids, post-audit evidence ignored, no secrets in logs). Verify claims before accepting.

## Interview prep (30%)
Know cold: the decision table and WHY (rule ids in deletion_rules.csv); the state-at-audit_at rule; account-linking method and its error rate; what the LLM does vs what is gated; the post-audit audit-id lookup (id only, state from pre-audit files); every inferred rule and its risk; what you would do with more time. Be honest about gaps (honesty = 15%).
