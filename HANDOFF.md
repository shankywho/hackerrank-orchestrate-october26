# HANDOFF (phase 1: normalizer + store)
Done:
- code/scorer.py (phase 0). Run: python3 -I code/scorer.py [--pred ...]
- code/logparse.py (6 dialects), observations.py (Observation, Store), normalizer.py, coverage.py.
- Run: python3 -I code/coverage.py -> code/evaluation/coverage_report.md; python3 -I tests/test_normalizer.py
- Result: 94 files, 2892 obs; 79 parsed, 1 empty snapshot (evidence_90, valid), 15 skipped (11 PNG vision, 4 PDF).
Design notes:
- captured_at = manifest file time (cutoff/citation); observed_at = row time inside file (2 rows exceed captured_at).
- States: FOUND/NOT_FOUND, IDENTIFIABLE/REDACTED|ANONYMIZED, ACTIVE/DISABLED, ACTIVE(restorable)/BLOCKED.
- source field marks primary vs replica table (sql:customer_data_replica_*), log dialect, json kind.
Open / assumptions (flag in reports):
- Analytics: user_id/customer_reference alone counts as IDENTIFIABLE.
- support_replica contact_query_completed -> SUPPORT_MESSAGE IDENTIFIABLE (inferred, attr inferred=...).
- Empty collections in bundles (e.g. evidence_51) yield no obs; absence proofs not modelled yet.
- cache_lookup (miss), deletion_marker_check, restore_policy_check_started ignored (listed in report).
- 259 obs have empty subject_key (215 anonymized analytics, 37 404 profiles, 7 export/attachment log lines).
Next: PDFs (pdftotext), PNG vision cache, identity graph, get_state_at.
## Phase 1b: store vs samples (eval only: code/evaluation/store_vs_samples.py)
- 79 non-account gold resources: 75 in store, 69 state-match at audit_at. Mismatches: 7 audit ids (5 post-audit-only, 2 absent), support_001 (PDF), support_002 (wrong inference), analytics_9914 (eval filter; store has MARKETING_PROFILE FOUND).
- FINDING: 2 ledger rows are future-dated vs file captured_at (evidence_13 account_1417 08-14, evidence_36 account_5043 04-11): same account NOT_FOUND then FOUND; cases 135/383 audit_at is before the FOUND row -> cutoff must be per ROW (observed_at<=audit_at) as well as per file.
- deletion_marker_check/attachment thumbnail_failed are signal, not noise; cache_lookup is harmless.
- Pending user approval: (3) empty-collection coverage records, (5) pdftotext parser, support_002 fix, per-row cutoff.
## Phase 1c (applied)
- observations.visible(o, audit_at): file captured_at AND row observed_at <= audit_at. Store.visible(rid, audit_at). Demonstrated: account_1417/5043 FOUND rows invisible at case 135/383 audit_at.
- Store.coverage: CoverageRecord(file_id, store, collection, captured_at, row_count); 18 of 322 empty (evidence_90 all systems, evidence_07/31/51 profiles, etc).
- Support replica: contact_query_completed -> FOUND (SECONDARY_IDENTIFIER if legacy_contact_index batch, else SUPPORT_MESSAGE; inferred attr); deletion_marker_check -> FOUND attrs marker_found; thumbnail_failed -> SUPPORT_ATTACHMENT FOUND.
- code/pdfparse.py: pdftotext if on PATH (not installed here, untested path), else stdlib ASCII85+Flate reader. PDFs: evidence_55 audit_record, evidence_58 support attachment, evidence_27 legal register (6 rows keyed by ORD ref, attr keyed_by=order_reference; no step per SPEC), evidence_18 statement (identity facts only, 0 obs).
- Eval: 72/79 gold non-account resources match. Left: 7 audit ids (5 post-audit-only, 2 in no file) -> audit-id discovery rule.
- Risk: SECONDARY_IDENTIFIER for legacy_contact_index fits ONE sample (case_10); unvalidated elsewhere.
- Remaining PNG (11) need vision. Next: identity graph, get_state_at.
## Phase 2: pipeline (identity, state, policy, validator) -> output.csv exists (draft, deterministic only)
- Run: python3 -I code/main.py [--stats]; python3 -I code/validator.py; tests: tests/test_*.py (5).
- Samples (no accounts from activity): status 1.00; step P 1.00 / R 0.845 (with acct), 0.942 (without); exact seq 10/32 and 23/32.
- Gaps: 343/508 cases no account step (needs activity-based account linker); 7 anonymized-analytics gold steps (activity-linked); 2 audit ids in no file; 11 unresolved cases (image/LINK dependent: PNG vision + LINK_RESOURCE_IDENTITY).
- Rule from case_17: resource linked only if newest linking evidence observed_at >= requested_at (same person has per-request account ids). Fitted on 1 sample; flag.
- 124 cases used late-file audit-id discovery (id only; logged in CaseResult.notes). Unknown retention treated as retained.
- Intent classifier is regex fallback (518: exactly 10 NOT_APPLICABLE); LLM classifier + agent loop still to build.
Next: account linker (activity windows + singleton elimination), PNG vision, LINK bridges, LLM loop.
## Phase 3: linker built, NOT wired
- Legal: 6 steps were legal_hold_* rows (not the PDF); legal now needs known retained_until > audit_at.
- Late audit ids: 129 found, all by user_id; 0 have any retention_until (default retained, 5 sample hits support it); row observed_at <= audit_at now required (116 remain).
- state.anchor_file: absence-proof citation (4/4 gold). Changes 0 of 518 now.
- account_linker.py: samples 24/0/8; eval 508 deletion requests: 165 evidence + 187 singleton = 352 unique, 145 ambiguous, 11 no-candidate. tiebreak() flagged, unused (132 would be assigned; 5/5 right on samples, overfit risk).
- Analytics already-anonymized: SUCCESS ANONYMIZE_RECORD sits ~28.5 min before reported_at (124 of 132), but batches share times: window+elimination gets 2/7 right, 3 wrong/extra; tight band 1/7 exact + 8 false positives. Needs account link first; activity ids follow account DELETE id by +1..+19. Leave unlinked (abstain).
Next: wire linker (decision), PNG vision, LINK_RESOURCE_IDENTITY, LLM loop, reports.
## Phase 4: linker wired
- plan_case(case, store, graph, link): account step only for unique link (evidence 165 + singleton 187 = 352 of 508; 156 abstain). main.py pools sample+eval requests for elimination.
- Samples: status 1.00; step P 1.00 / R 0.912 / F1 0.954; exact seq 19/32. Lost vs unwired: evidence-found accounts of 3 ambiguous cases (25, 26, 28) now abstain (intended: no fallback).
- output.csv: 2955 steps, valid, 11 unresolved (image/LINK cases).
- Adjacency (activity id after account delete -> anonymized analytics): REJECTED, 2-3 wrong on samples at every gap; same next-anonymize shared by 3-4 requests; many cases have none. Eval script kept: code/evaluation/anon_adjacency_eval.py.
Next: PNG vision + LINK_RESOURCE_IDENTITY (11 unresolved), LLM loop, reports (evaluation_report, usage_report), README, commit.
## Phase 5: Groq agent (not applied to output.csv)
- code/llm.py (cache code/cache/llm, usage code/evaluation/usage_calls.csv, TPM limiter 6500, offline replay), code/agent.py (7 tools, 8 calls, candidate-set validation), code/evaluation/agent_eval.py, tests/test_agent.py.
- Model chosen by rule from /models: openai/gpt-oss-120b (also probed qwen/qwen3.8-27b, openai/gpt-oss-20b: all tool-capable). Tier limit 8000 TPM => ~15 min per 32 cases.
- Samples (32): intent agrees 32/32; 24 unique links confirmed, 0 overrides; 8 abstain -> model null. Ambiguous 5: 0 right/0 wrong/5 null vs chance 2.33. Evidence-narrowed non-unique candidates: none in samples (n=0).
- Plan review: 25 ok, 7 concern (mostly "plan incomplete" = missing unlinked anonymized analytics/accounts).
- Model quirk: wraps final JSON as {"name":"json","arguments":{...}}; handled in _parse_verdict + tool_use_failed path.
- Offline replay verified (--offline --force, 32 cases). output.csv unchanged.
Next: decide on eval run of agent (518 cases ~4 h at TPM limit), PNG vision, reports.
## Phase 6: co-activity (not wired) + routed runner
- coactivity sweep (ambiguous 5 samples | calibration non-trivial 8 of 24 | eval abstentions resolved of 156):
  0.75: 1R/1W/3A | 3 disagree | 9 ; 0.6: 0R/0W/5A | 1 disagree | 7 ; <=0.5: 0R/0W/5A | 0 disagree, 8 abstain | 4 ; 0.3: 0 | 0 | 0
  Literal 0-wrong bar passes only at <=0.5, where it never fires on a sample: no evidence it is right. Recommend NOT wiring. WIN_RATIO=0.5 in code.
- run_agent.py: routes 166/518 (145 abstain, 11 unresolved, 10 not_applicable); ~3.1k tokens/case measured => ~515k tokens, ~79 min at 6,500 TPM budget; resumable per case; nohup command in its docstring. Not started.
- Agent so far: returns null on ambiguous cases (5/5), so expected gain from running it on the 145 is low; run mainly for NA cross-check and usage numbers.
## Phase 7: images + links
- code/evidence_cache/images.md: 32 OBS rows for 11 PNGs (hand transcription via Claude Code vision; disclose in README). png obs carry NO identifiers; ownership only via LINK_RESOURCE_IDENTITY (code/links.py). Aliases (file names, customer key, statement no.) resolve only through link labels.
- Link time cutoff: only case_52 has a link after audit_at (activity_4009); case_33 excludes export_1260 by request-time scoping (earlier request of same person).
- INFERRED (labelled in code + notes): marketing by fields/ownership from link despite screenshot conflict; DELETION_PENDING = present; object attachments deleted only with 'belongs only to' link; backups seen in the console with no block indicator = ACTIVE.
- Samples unchanged (P 1.00 R 0.912). output.csv: 2987 steps, valid, 0 unresolved; account methods evidence 166 / singleton 187 / abstain 155.
- run_agent routing now: 155 abstain + 10 NA = 165 (unresolved 0).
Next: reports (evaluation_report, usage_report), README (disclose image cache), commit, optional agent run.
## Phase 8: freeze + docs
- Deterministic layer frozen. tests: python3 tests/run_all.py (10 pass). output.csv valid (355 INC / 153 COMP / 10 NA, 2987 steps, 0 unresolved). Nothing committed.
- Post-audit: tests/test_post_audit.py (case_39 no search_4801; case_55 restore result unused).
- Limits (headers): 1000 RPD, 8000 TPM; TPD 200K per docs (not in headers). Routed run = 165 cases, ~512k tokens > TPD, NOT started. Resumable: run in ~3 daily chunks or with a paid tier.
- Docs: README.md, code/evaluation/{evaluation_report.md,usage_report.md}; regenerate with make_reports.py / make_usage_report.py.
- usage_calls.csv now logs only NEW calls (132 rows). Raw header probe (94 tokens) not logged (noted in report).
Next: commit, build code.zip (zip -r code.zip README.md code -x ...), trace zip, interview prep. Optional: routed agent run (NA 10 cases fit under daily cap).
## Phase 9: final run + packaging
- Final run: main.py (intent step on gpt-oss-20b, 400 calls for 550 requests; 7 disagreements, all samples, regex wins ties) + run_agent.py (gpt-oss-120b): full agent ran on 22 (10 NA, 6 image-link, 6 ambiguous); 6 image-link calls failed (case_50/52/53/55/61/63; reason not captured, retry = rerun run_agent after the daily window resets); 139 ambiguous not reached. Tokens 164,368, ~$0.0233 list.
- Sample intents: model 25/32, regex 32/32, final vote 32/32.
- code.zip built (README command): 723 files, 616K; no dataset, no nested archives, no .env/key. Only .env contains gsk_ (git-ignored); shell grep is a function that skips ignored files: use /usr/bin/grep.
- User commits/uploads: output.csv, code.zip, log.txt. Nothing committed by the agent.
