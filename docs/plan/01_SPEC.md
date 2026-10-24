# SPEC — decision table, ordering, evidence rules (from problem_statement.md + 32 samples)

Sources: problem_statement.md (schema, operations, ordering) and deletion_rules.csv are AUTHORITATIVE; the per-category step mapping below is INFERRED from sample_output.csv.
Sample families: cases 11..32 (22, generated-style: 4-digit account ids, security-audit step, batch audit_at). Cases 01,02,04,05,06,07,08,10 (8) are hand-made style: zero-padded account_0NN ids, no audit step, evidence_54..61. Cases 03 and 09 look generated-style (account_7566, account_3960, with audit step). Fit global rules on generated-style; use hand-made for format tests and single-file categories.

## Output schema (problem_statement.md)
Each step: {"step_no","phase","system_id","resource_id","operation","state","evidence_id"}; step_no from 1 consecutive; JSON key order ignored, array order significant. Exactly 518 rows sorted by case_id.
Operation -> state, in the REQUIRED within-phase order (table order from problem_statement):
FINDING: REPORT_REMAINING_RECORD(PRESENT), REPORT_EXPOSED_PII(PRESENT), REPORT_ACTIVE_DATA_FLOW(ACTIVE), REPORT_RESTORE_RISK(ACTIVE).
REMEDIATION: DISABLE_DATA_FLOW(DISABLED), BLOCK_RESTORE(BLOCKED), DELETE_RECORD(ABSENT), REDACT_RECORD(REDACTED), ANONYMIZE_RECORD(ANONYMIZED).
VERIFICATION: VERIFY_RECORD_ABSENT(ABSENT), VERIFY_RECORD_PRESENT(PRESENT), VERIFY_RECORD_REDACTED(REDACTED), VERIFY_RECORD_ANONYMIZED(ANONYMIZED), VERIFY_DATA_FLOW_DISABLED(DISABLED), VERIFY_RESTORE_BLOCKED(BLOCKED).

## deletion_rules.csv (15 rules) and sample-gold coverage
rule_01 USER_PROFILE DELETE | rule_02 SECONDARY_IDENTIFIER DELETE | rule_03 SEARCH_INDEX_ENTRY DELETE | rule_04 CACHE_ENTRY DELETE | rule_05 SUPPORT_MESSAGE REDACT (shared customer/agent/order context retained) | rule_06 SUPPORT_ATTACHMENT DELETE (belongs only to subject) | rule_07 ANALYTICS_PROFILE ANONYMIZE (still attributable) | rule_08 PRODUCT_TELEMETRY ANONYMIZE | rule_09 USER_EXPORT DELETE | rule_10 MARKETING_PROFILE DELETE | rule_11 EMAIL_DELIVERY_LOG REDACT (contains email) | rule_12 SECURITY_AUDIT_LOG RETAIN (within retention) | rule_13 LEGAL_HOLD_RECORD RETAIN (active hold) | rule_14 BACKUP_ARCHIVE RETAIN (restore access can be blocked) | rule_15 ACCOUNT_TOMBSTONE REDACT.
Sample gold exists for: USER_PROFILE(account_service), SEARCH_INDEX, SUPPORT_MESSAGE/ATTACHMENT, ANALYTICS, USER_EXPORT(export_service), DATA_FLOW(integration_service), BACKUP(backup_service), SECURITY_AUDIT(security_service), ACCOUNT_TOMBSTONE(identity_service REDACT, 1 case).
NO sample gold: SECONDARY_IDENTIFIER, CACHE_ENTRY, PRODUCT_TELEMETRY, EMAIL_DELIVERY_LOG, MARKETING_PROFILE, LEGAL_HOLD_RECORD. Activities for cache (REFRESH_CACHE 284 on cache_service) and email (SEND_EMAIL 368 on email_delivery_service) exist, yet NO sample step uses cache_service or email_delivery_service. DEFAULT: treat those two as background noise unless evidence has an explicit record row of that category for the subject; flag as OPEN RISK (apply the rule only when a record of that category is evidenced).
Sample system_ids seen in gold: account_service, search_service, analytics_platform, support_platform, export_service, integration_service, backup_service, security_service, identity_service. Inferred (unsampled): legal_service, marketing_platform, object_storage.

## State
State at audit_at = latest observation per resource with captured_at <= audit_at (inclusive). reported_status / reported_result are CLAIMS, not facts (case_24: deletion "SUCCESS" yet profile still 200). Sync/restore activity after a delete = recreation risk (ignore if it occurs after audit_at).

## Category -> steps (finding / remediation / verification)
| Category (state) | Finding | Remediation | Verification |
|---|---|---|---|
| USER_PROFILE, SEARCH_INDEX_ENTRY, USER_EXPORT, SUPPORT_ATTACHMENT, MARKETING_PROFILE* (present) | REPORT_REMAINING_RECORD | DELETE_RECORD | VERIFY_RECORD_ABSENT |
| same, absent | none | none | VERIFY_RECORD_ABSENT |
| SUPPORT_MESSAGE (IDENTIFIABLE) | REPORT_EXPOSED_PII | REDACT_RECORD | VERIFY_RECORD_REDACTED |
| ANALYTICS_PROFILE (IDENTIFIABLE) | REPORT_EXPOSED_PII | ANONYMIZE_RECORD | VERIFY_RECORD_ANONYMIZED |
| DATA_FLOW (ACTIVE) | REPORT_ACTIVE_DATA_FLOW | DISABLE_DATA_FLOW | VERIFY_DATA_FLOW_DISABLED |
| BACKUP_ARCHIVE (restorable) | REPORT_RESTORE_RISK | BLOCK_RESTORE | VERIFY_RESTORE_BLOCKED |
| SECURITY_AUDIT_LOG within retention | none | none | VERIFY_RECORD_PRESENT |
| ACCOUNT_TOMBSTONE with PII | REPORT_EXPOSED_PII | REDACT_RECORD | VERIFY_RECORD_REDACTED |
| LEGAL_HOLD_RECORD* (linked to user, retained_until > audit_at) | none | none | VERIFY_RECORD_PRESENT (system legal_service) |
Already-remediated states (REDACTED / ANONYMIZED / DISABLED / BLOCKED / NOT_FOUND) -> verification only.
Account service: EVERY sample (32/32, COMPLETE and INCOMPLETE) has an account_service VERIFY_RECORD_ABSENT; if the profile is present, also REMAINING+DELETE.
* = INFERRED, no sample gold (see list above). Cap effort 30 min total (about 4 of 518 cases). Label as inferred in reports.

Support DELETE vs REDACT: DELETE only with an explicit attachment marker (category SUPPORT_ATTACHMENT; log `attachment_pii_scan ... file=`; SQL attachments/file_name; `attachment_preview`; activity "belongs only to"). Everything else support = message -> REDACT. Samples: 07,10,13 DELETE; 19 REDACT. Risk: a missed marker silently flips DELETE to REDACT -> add a test counting support ids with neither marker.
Object storage: activity "X belongs only to <subject>" -> DELETE (rule_06), system object_storage. Shared -> do not delete.
Category comes from a record's own fields, not its id prefix: a record with event contact_lookup / email_opt_in is a contact/marketing record (rule_10 DELETE) even if id is analytics_NNNN (this explains case_12 generically; do not hardcode the case).
Replica-only rows (read/failover replica, reporting) count as PRESENT, system_id stays account_service (case_05, case_16). Rows with status DELETION_PENDING: ASSUMPTION = present (email still exposed); no sample covers it (7 eval cases: 67,129,177,247,249,333,499). Flag it.

## Ordering of steps
Phase (FINDING, REMEDIATION, VERIFICATION), then the operation order of the table above, then system_id ascending, then resource_id ascending (verified: 0 violations over all 32 samples) (STRING order; untested tie-break, keep in one function `sort_key`). Step numbers consecutive from 1.

## Evidence ids
- INCOMPLETE cases: findings cite evidence; ALL remediation and verification steps have evidence_id "" (including the account absent check).
- COMPLETE cases: verification steps only, each cites a file.
- NOT_APPLICABLE: steps_json = [].
- Citation rule: latest file with captured_at <= audit_at (inclusive) that mentions the resource: 71/75 sample steps right. If the resource is mentioned nowhere (absence proof): cite the case's anchor file (file holding the case's other resources, or the snapshot with an empty collection, e.g. case_31 -> evidence_51 customer_profiles []). Activity/audit ids only as fallback; no sample cites them.
- Security audit record id: may be DISCOVERED from any file (it is a future-verification step with empty evidence), but state and citation use only pre-audit files; require retention_until > audit_at; match by the user's identifier not proximity; LOG each use of a post-audit file (interview answer: "id lookup only, state from pre-audit files"). In 5/22 samples the id exists only in a later file (case_09 evidence_06; 23,24,29,30 evidence_02). audit_record_6645 / 5569 (cases 18, 20) are in no file: unrecoverable.
- Security-audit VERIFY_RECORD_PRESENT (security_service) appears in 24 of 32 samples: all 22 of cases 11..32 plus 03 and 09; absent in 01,02,04-08,10 (verified).

## deletion_status
Only three values: COMPLETE, INCOMPLETE, NOT_APPLICABLE (problem_statement.md). COMPLETE = nothing remains at audit_at (verification steps only). INCOMPLETE = anything remains/recreated. Derive from observed state at audit_at, NOT from reported_status (that field may be COMPLETE / PARTIALLY_COMPLETE / NOT_STARTED and is only a claim).
NOT_APPLICABLE: exactly 10 cases are non-deletion (case_533..542): each has a CLASSIFY_REQUEST activity and is in ledger evidence_50. LLM intent classifier must agree with that activity or flag. One-off text is NOT a signal (295 of 375 distinct texts are used once); classify by meaning. 62 non-English texts are all templated deletions.

## State cutoff is PER ROW as well as per file (found by build validation, store_vs_samples.py)
A file's captured_at can be earlier than rows inside it. Ledger files evidence_13 and evidence_36 contain a profile NOT_FOUND at the audit time and FOUND two days later, inside a file captured before that. Regression targets (eval cases, no gold): case_135 (audit_at 08-12 09:00, account_1417) and case_383 (audit_at 04-09 12:00, account_5043): the later FOUND row is post-audit, so the profile is ABSENT at audit_at.
Rule: a row counts for state only if observed_at <= audit_at (inclusive). A file captured after audit_at is not citable. Whether a post-capture file's earlier-dated rows may inform state is untested: default NO; measure on samples before changing.
Log events that carry state (do not ignore): deletion_marker_check (marker_found=false => record not marked deleted => still present; marker_found=true => deleted. Direction verified on one sample, case_10 support_002 only: re-verify across all evidence), attachment_preview thumbnail_failed (attachment marker => DELETE not REDACT). cache_lookup (always a miss, no subject) is proven harmless.
