# Ghost Data Still Exists - deletion-audit agent

HackerRank Orchestrate (October 2026). For each request in `dataset/deletion_requests.csv` the program decides whether
deletion was actually requested, reconstructs the state of the person's data at `audit_at` from the supplied evidence,
applies `deletion_rules.csv`, and writes `output.csv` (`case_id,deletion_status,steps_json`) with findings, an ordered
remediation plan and verification steps. The challenge contract is in `problem_statement.md`.

## Setup

- Python 3.10 or newer. **Standard library only**: no `pip install` is needed for the deterministic run.
- Optional: `pdftotext` (poppler). If it is absent, `code/pdfparse.py` reads the four PDFs with a built-in reader.
- A Groq key is needed only to make **new** model calls: copy `.env.example` to `.env` and set `GROQ_API_KEY` (read from the
  environment or `.env`, never logged, `.env` is git-ignored). Without a key, `python3 code/main.py` replays the saved model votes
  from `code/cache` and writes the same `output.csv`; `--offline` forces that replay.

## Run

```bash
python3 code/main.py                 # reads dataset/, runs the intent step (cached model calls), writes output.csv (518 rows, sorted)
python3 code/validator.py            # schema, ordering, step numbers, evidence_id rules
python3 tests/run_all.py             # every test, each in its own interpreter
python3 code/main.py --requests dataset/sample_deletion_requests.csv --out /tmp/sample_pred.csv
python3 code/scorer.py --pred /tmp/sample_pred.csv            # status, step P/R/F1, field and sequence accuracy vs sample gold
python3 code/scorer.py --pred /tmp/sample_pred.csv --exclude-system account_service   # the same without account steps
python3 code/evaluation/make_reports.py        # regenerates code/evaluation/evaluation_report.md from real runs
python3 code/evaluation/make_usage_report.py   # regenerates code/evaluation/usage_report.md from the call log
```

Model steps (a key is only needed to make new calls; `--offline` replays the saved ones; see "Model routing"):

```bash
python3 code/main.py --offline          # replay the intent-step model calls from code/cache (no network, no key)
python3 code/run_agent.py --dry-run     # routed cases in priority order, token budget and estimate
python3 code/run_agent.py --summary-only   # rewrite code/evaluation/agent_coverage.md from the saved results
python3 code/run_agent.py --offline     # replay saved agent calls from code/cache with no network
nohup python3 code/run_agent.py > code/cache/agent_run.log 2>&1 &    # background run; resumable, stops at the daily budget
```

## Architecture

Principle: the model proposes, tools verify, the validator enforces; code decides everything that can be decided from data.

| layer | file | what it does |
|---|---|---|
| normalizer | `code/normalizer.py`, `logparse.py`, `pdfparse.py` | JSON bundles and ledgers, six log dialects (joined on `trace_id`), SQLite snapshots including primary and replica tables, handover text, PDFs, and the image transcription cache, all into one observation format |
| observation store | `code/observations.py` | `file_id, captured_at, observed_at, system_id, resource_id, category, state, subject_key`, plus coverage records (collection, snapshot time, row count, including empty ones) and link records |
| identity graph | `code/identity.py` | user_id to every current and historical email, hash, phone; exact matching only (no fuzzy matching, look-alike emails stay unmatched) |
| state at `audit_at` | `code/state.py` | newest observation passing the cutoff on the file **and** on the row; primary versus replica ties resolve to "still present"; anchor-file citation for absence proofs |
| account linker | `code/account_linker.py` | evidence first, then the delete-activity window (-80 to +10 minutes from `reported_at`, result matching `reported_status`), then singleton elimination across all requests; abstains when ambiguous |
| link bridge | `code/links.py` | `LINK_RESOURCE_IDENTITY` activities are the only source of ownership for image and statement resources |
| policy engine | `code/policy.py` | category and state to ordered steps (decision table, `sort_key`, citation rules), intent classifier |
| validator | `code/validator.py` | rejects bad schema, ordering, step numbers, evidence ids and post-audit citations |
| agent layer | `code/llm.py`, `agent.py`, `run_agent.py` | bounded per-case model loop over the modules above |

Rules that matter:

- Workflow statuses and activity results are claims; state comes from evidence. Evidence captured after `audit_at` is never
  used for state or citation. The one exception is that a security-audit record **id** may be found in a later file
  (id only, and its own row must still be dated at or before `audit_at`); each use is recorded in the case notes.
- Every request that is a deletion gets steps for each record found by exact identifiers: deleted, redacted or anonymized
  per rule, retained records only verified present, data flows disabled and restores blocked before records change.
- No case, user or resource ids are hardcoded in the solver (`tests/test_*.py` check this). Gold labels are read only by
  the scripts in `code/evaluation/` and `code/scorer.py`.

## Model routing

The model is used in two places. In both, the code decides everything it can from data, and a model verdict can never invent an
id or override the evidence cutoff, retention or the policy table. **The model's account choices are applied nowhere**: every
account step in `output.csv` comes from the deterministic account linker.

**1. Intent step: every request (plain statement).** Every one of the 518 evaluation requests (and the 32 sample requests the
account linker uses for elimination) got one short model call on the cheapest usable model (`openai/gpt-oss-20b`, chosen by list
price among the models that passed a tool-calling probe). Identical request texts share one cached call, so the 550 requests cost
400 calls. The model's vote is combined with the regex classifier and the CLASSIFY_REQUEST activity by majority vote
(`code/intent.py`); a 1-1 tie goes to the regex, and every disagreement is logged in `code/evaluation/intent_votes.md`.
On the 32 samples the model got **25 of 32** intents right, against **32 of 32** for the regex classifier, and the final vote got
32 of 32. There are 7 disagreements, all on sample requests, all ties settled by the regex; none among the 518 evaluation requests.
The model vote therefore changed no decision in `output.csv`.

**2. Full agent: only 22 of the 518 evaluation requests (plain statement).** The full agent is a bounded per-case loop on
`openai/gpt-oss-120b` (temperature 0, at most 8 tool calls) with the tools `get_case`, `resolve_identity`, `find_resources`,
`get_state_at`, `read_evidence`, `list_account_candidates`, `propose_plan`. The large model's documented cap is 200,000 tokens a day,
so `code/run_agent.py` runs the agent in this priority until the budget is used: the not-applicable requests, then requests whose
plan relies on an image/link-owned resource, then ambiguous-account requests with the fewest candidates.

- **Full agent ran on 22:** all 10 NOT_APPLICABLE requests (`case_533` to `case_542`); 6 image-link requests (`case_33`, `case_34`,
  `case_35`, `case_39`, `case_45`, `case_46`); 6 ambiguous-account requests (`case_103`, `case_104`, `case_106`, `case_107`,
  `case_109`, `case_110`).
- **Routed but no result (model call failed, reason not captured):** 6 image-link requests (`case_50`, `case_52`, `case_53`,
  `case_55`, `case_61`, `case_63`). A re-run retries them.
- **Routed but not reached (daily cap):** 139 ambiguous-account requests.
- **Intent step only: the other 496 evaluation requests.**

The 22 agent verdicts agree with the intent vote on all 22; on the 19 ambiguous-or-unlinked cases it answered "no account",
on 2 it confirmed a unique link, and on 1 it picked a candidate (not applied, no gold). Five plans drew a "concern" remark
(logged, not acted on). `code/evaluation/agent_coverage.md` lists every case id; `usage_report.md` has the token totals.

Other model facts: models are discovered through the API's model list (nothing hardcoded); every call is cached under `code/cache`
as text files, so `python3 code/main.py --offline` and `python3 code/run_agent.py --offline` replay the final run from `code.zip`
without a key; calls stay under a per-model per-minute token budget with backoff on 429; token use per call is logged.

## Disclosures

- **Image evidence was transcribed by Claude Code vision during development.** The 11 PNG files were viewed by the coding
  agent and their visible fields typed into `code/evidence_cache/images.md`; the normalizer reads that cache instead of calling a
  vision model at run time. Image rows carry no identifiers: an image proves existence and state, never the owner. Ownership
  comes only from explicit `LINK_RESOURCE_IDENTITY` records, and a link dated after `audit_at` is not used.
- **Inferred rules (no sample gold):** marketing profiles by record fields (a contact or opt-in lookup is a marketing profile
  whatever its id prefix); `DELETION_PENDING` accounts count as still present; object attachments are deleted only when a link
  record says they belong only to the subject; backups shown in the console with no block indicator count as restorable; legacy
  contact-index rows are treated as secondary identifiers (fitted on one sample); a security-audit record with no
  `retention_until` counts as retained. Each is labelled `INFERRED` in code, in per-case notes or in the image cache.
- **Account abstentions:** account ids rarely appear in evidence, so they are linked from activity timing. When more than one
  account fits, the linker abstains and that case has no account step; a case-number tie-break exists in the code but is
  never applied. The sample scorer therefore reports results with and without account steps.
- **Known omissions:** analytics resources that are already anonymized cannot be tied to a request from the data (two
  activity-based experiments were rejected, see the evaluation report); some audit ids appear in no evidence file.
- **Honesty about accuracy:** rules were fitted on 32 samples that touch 13 of 94 evidence files and none of the 11 images.
  Sample scores are reported; no hidden-set accuracy is claimed. Expect it to be lower than the sample score.
- The solution uses only participant-facing files under `dataset/`. No organizer-only data, no hardcoded labels.

## Layout

```text
code/main.py                      entry point
code/{normalizer,logparse,pdfparse,observations,identity,state,links,account_linker,policy,validator}.py
code/{llm,agent,run_agent,coactivity}.py   model layer (coactivity is an experiment, not wired in)
code/scorer.py                    scorer against the sample gold
code/evidence_cache/images.md     transcription of the 11 PNG files
code/cache/                       saved model calls and per-case agent results (replay)
code/evaluation/                  evaluation_report.md, usage_report.md, generators, evaluation scripts, coverage_report.md
tests/                            invariant and regression tests (python3 tests/run_all.py)
docs/plan/                        planning notes
```

## Packaging

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```

`code.zip` must not contain `dataset/` or nested archives. Submit `output.csv`, `code.zip` and `log.txt` (the chat transcript)
at https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
