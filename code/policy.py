"""Policy engine: observed state at audit_at -> ordered steps (docs/plan/01_SPEC.md).

Category -> (finding, remediation, verification) per state follows deletion_rules.csv:
DELETE categories are deleted, REDACT/ANONYMIZE categories keep the record, RETAIN categories
are only verified present, data flows are disabled and restores blocked before records change.
"""
import json
import re
from dataclasses import dataclass, field

from state import anchor_file, excluded_links, get_state_at, late_records, linked_resources

PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
OP_ORDER = [  # required within-phase order from problem_statement.md
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
OP_STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
OP_PHASE = {op: ("FINDING" if op.startswith("REPORT") else "VERIFICATION" if op.startswith("VERIFY")
                 else "REMEDIATION") for op in OP_ORDER}

# MARKETING_PROFILE (rule_10 DELETE) and SECONDARY_IDENTIFIER (rule_02 DELETE) are INFERRED members: the samples
# show them in at most one case each. They are acted on only when a record of that category is evidenced.
DELETE_FAMILY = {"USER_PROFILE", "SEARCH_INDEX_ENTRY", "USER_EXPORT", "SUPPORT_ATTACHMENT", "MARKETING_PROFILE",
                 "SECONDARY_IDENTIFIER"}
# (finding, remediation, verification) for a state that violates policy, and verification-only otherwise
TREATMENT = {
    "DELETE": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "FLOW": ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    "RESTORE": ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
}


def sort_key(step):
    """Phase, operation order, system_id, resource_id (plain string order; one place to change it)."""
    return (PHASES.index(step["phase"]), OP_ORDER.index(step["operation"]), step["system_id"], step["resource_id"])


# ------------------------------------------------------------------ intent
STRONG_NEGATIVE = re.compile(
    r"not asking for (a )?(deletion|erasure)|not (a )?request to (erase|delete)|don'?t (delete|change|remove)|"
    r"do not (delete|close|remove)|leave (my|the) (account|profile)|keep the account|keep it as it is|"
    r"still (need|use|want)\b|without removing", re.I)
DELETE_WORDS = re.compile(r"delet|remov|erase|erasure|forget|wipe|purge|close (this|my|the) account|"
                          r"\bgone\b|no longer exist|\bsuppress|\beliminat", re.I)
WEAK_NEGATIVE = re.compile(
    r"copy of|download|portable format|what personal information|correct it|misspell|out of date|replace it|"
    r"change the email|help resetting|can'?t access|promotional|marketing lists|unsubscribe|opt[- ]out|"
    r"access request|information request", re.I)


def classify_intent(text):
    """True when the text authorizes deletion. Deterministic fallback for the LLM classifier.
    Order: explicit refusal of deletion -> deletion wording -> other non-deletion wording ->
    non-English text (templated deletions in this dataset)."""
    if STRONG_NEGATIVE.search(text):
        return False
    if DELETE_WORDS.search(text):
        return True
    if WEAK_NEGATIVE.search(text):
        return False
    return bool(re.search(r"[^\x00-\x7f]", text))


# ------------------------------------------------------------------ planning
@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: list
    notes: list = field(default_factory=list)
    user_id: str = ""
    account_ids: list = field(default_factory=list)
    intent: object = None      # intent.Decision (votes) when the intent step ran


def _step(op, system, rid, evidence=""):
    return {"phase": OP_PHASE[op], "system_id": system, "resource_id": rid, "operation": op,
            "state": OP_STATE[op], "evidence_id": evidence}


def _treatment_for(res, audit_at):
    """(kind, violating) for a StateResult; kind None = no steps for this resource."""
    cat, state = res.category, res.state
    obs = res.observations[0]
    if obs.attr("keyed_by") == "order_reference":
        return None, False                       # legal register keyed by order: no step (SPEC I6)
    if cat in DELETE_FAMILY:
        return "DELETE", state != "NOT_FOUND"
    if cat == "SUPPORT_MESSAGE":
        return "REDACT", state != "REDACTED"
    if cat == "ACCOUNT_TOMBSTONE":
        return "REDACT", state != "REDACTED"
    if cat == "ANALYTICS_PROFILE":
        return "ANONYMIZE", state != "ANONYMIZED"
    if cat == "DATA_FLOW":
        return "FLOW", state != "DISABLED"
    if cat == "BACKUP_ARCHIVE":
        return "RESTORE", state != "BLOCKED"
    if cat == "LEGAL_HOLD_RECORD":
        until = obs.attr("retained_until") or ""
        return ("RETAIN", False) if until[:10] > audit_at[:10] else (None, False)  # unknown/expired: no step
    if cat == "SECURITY_AUDIT_LOG":
        until = obs.attr("retention_until") or ""
        # unknown retention counts as within retention (rule_12/13); only a known past date expires it
        return ("DELETE", True) if until and until[:10] <= audit_at[:10] else ("RETAIN", False)
    return None, False


def plan_case(case, store, graph, link=None, authorized=None):
    """Plan one deletion request. Returns CaseResult. No ids are hardcoded; all come from data.
    `link` is the account linker's verdict: an account step is emitted only for a unique link.
    `authorized` is the intent step's verdict (majority vote, intent.py); None falls back to the regex classifier."""
    cid, audit_at = case["case_id"], case["audit_at"]
    if not (classify_intent(case["request_text"]) if authorized is None else authorized):
        return CaseResult(cid, "NOT_APPLICABLE", [])
    notes = []
    user = graph.resolve_one(case["email"])
    if not user:
        return CaseResult(cid, "COMPLETE", [], ["email did not resolve to exactly one user"])
    idents = graph.identifiers(user)
    resources = linked_resources(store, idents, audit_at, case["requested_at"])
    # security audit ids may be discovered from a post-audit file (id only; state/citation stay pre-audit)
    have_audit = {r for r in resources if any(o.category == "SECURITY_AUDIT_LOG" for o in store.by_resource[r])}
    late_audit = {}
    for o in late_records(store, idents, audit_at, "SECURITY_AUDIT_LOG"):
        until = o.attr("retention_until") or ""
        if (o.resource_id not in have_audit and o.observed_at <= audit_at
                and not (until and until[:10] <= audit_at[:10])):
            late_audit[o.resource_id] = o
    items = []   # (system, resource, kind, violating, cite_file, category)
    accounts = []
    for rid in resources:
        for res in get_state_at(store, rid, audit_at):
            if res.category == "USER_PROFILE":
                continue            # account steps come only from the linker's unique verdict
            kind, bad = _treatment_for(res, audit_at)
            if kind is None:
                continue
            if res.category == "SUPPORT_ATTACHMENT" and res.system_id == "object_storage":
                # INFERRED (rule_06 "belongs only to the subject"): an object is deleted only when a link record
                # confirms it belongs only to this subject; an unlinked or shared object is left alone.
                if not any(lk.exclusive and lk.event_time <= audit_at and (lk.email in idents or lk.user_id in idents)
                           for lk in store.links_by_resource.get(rid, ())):
                    notes.append(f"object {rid} skipped: no 'belongs only to' link (shared or unlinked)")
                    continue
            for lk in store.links_by_resource.get(rid, ()):
                shown = res.observations[0].attr("visible_email")
                if shown and shown.lower() != lk.email and (lk.email in idents or lk.user_id in idents):
                    notes.append(f"INFERRED: {rid} screenshot shows {shown} but link record {lk.activity_id} owns it "
                                 f"({lk.email}); ownership taken from the link")
            for o in res.observations:
                if o.attr("account_status") == "DELETION_PENDING":
                    # INFERRED default (no sample covers it): DELETION_PENDING still exposes the data -> present
                    notes.append(f"INFERRED: {rid} DELETION_PENDING treated as still present")
                    break
            items.append((res.system_id, rid, kind, bad, res.cite_file, res.category))
    if link is not None and link.account:
        accounts.append(link.account)
        notes.append(f"account {link.account} linked by {link.method}")
        seen = [r for r in get_state_at(store, link.account, audit_at) if r.category == "USER_PROFILE"]
        if seen:   # evidence shows the profile at audit_at
            res = seen[0]
            if any(o.attr("account_status") == "DELETION_PENDING" for o in res.observations):
                # INFERRED default (no sample covers it): DELETION_PENDING still exposes the data -> present
                notes.append(f"INFERRED: {link.account} DELETION_PENDING treated as still present")
            items.append((res.system_id, link.account, "DELETE", res.state != "NOT_FOUND", res.cite_file,
                          "USER_PROFILE"))
        else:      # no file mentions it: treated as absent; the citation falls to the anchor rule
            items.append(("account_service", link.account, "DELETE", False, "", "USER_PROFILE"))
    elif link is not None:
        notes.append(f"account abstained: {link.reason}")
    for lk in excluded_links(store, idents, audit_at):
        notes.append(f"link {lk.activity_id} for {lk.resource_id} is dated after audit_at; excluded")
    for rid, o in sorted(late_audit.items()):
        notes.append(f"audit id {rid} discovered from post-audit file {o.file_id} (id only)")
        items.append((o.system_id, rid, "RETAIN", False, "", "SECURITY_AUDIT_LOG"))
    findings, rem, ver, ver_cats = [], [], [], []
    for system, rid, kind, bad, cite, cat in items:
        if kind == "RETAIN":
            ver.append(_step("VERIFY_RECORD_PRESENT", system, rid, cite))
            ver_cats.append(cat)
            continue
        f, r, v = TREATMENT[kind]
        if bad:
            findings.append(_step(f, system, rid, cite))
            rem.append(_step(r, system, rid))
        ver.append(_step(v, system, rid, cite))
        ver_cats.append(cat)
    incomplete = bool(findings)
    if incomplete:
        for s in ver:
            s["evidence_id"] = ""
    else:
        case_files = {s["evidence_id"] for s in ver if s["evidence_id"]}
        acct_file = next((s["evidence_id"] for s in ver if s["system_id"] == "account_service"
                          and s["evidence_id"]), "")
        for s, cat in zip(ver, ver_cats):
            if not s["evidence_id"]:
                s["evidence_id"] = anchor_file(store, cat, audit_at, case_files, acct_file)
                if s["evidence_id"]:
                    notes.append(f"anchor citation {s['evidence_id']} for {s['resource_id']}")
        dropped = [s for s in ver if not s["evidence_id"]]
        if dropped:  # a COMPLETE step must cite pre-audit evidence; never cite a later file
            notes.append(f"dropped {len(dropped)} uncitable verification step(s): no pre-audit evidence file")
            ver = [s for s in ver if s["evidence_id"]]
    steps = sorted({(s["phase"], s["system_id"], s["resource_id"], s["operation"]): s
                    for s in findings + rem + ver}.values(), key=sort_key)
    for i, s in enumerate(steps, 1):
        s["step_no"] = i
    return CaseResult(cid, "INCOMPLETE" if incomplete else "COMPLETE", steps, notes, user, sorted(set(accounts)))


def steps_json(steps):
    keys = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
    return json.dumps([{k: s[k] for k in keys} for s in steps], separators=(",", ":"), ensure_ascii=False)
