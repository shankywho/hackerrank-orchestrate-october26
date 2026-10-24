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
