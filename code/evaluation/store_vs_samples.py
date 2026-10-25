"""EVALUATION ONLY. Validate the observation store against sample_output.csv.

Uses gold labels. Never import this from the solver path.
  (1) every non-account resource in the gold steps: exists in store with implied category/state?
  (4) do ignored log events touch any sample subject or resource?
Usage: python3 -I code/evaluation/store_vs_samples.py [--list-all]
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "code"))
import coverage
import normalizer
from logparse import parse_line
from observations import visible

DS = ROOT / "dataset"
SYSTEM_CATEGORY = {  # gold system_id -> acceptable store categories
    "search_service": {"SEARCH_INDEX_ENTRY"}, "export_service": {"USER_EXPORT"},
    "support_platform": {"SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT", "SECONDARY_IDENTIFIER"}, "object_storage": {"SUPPORT_ATTACHMENT"},
    "marketing_platform": {"MARKETING_PROFILE"}, "analytics_platform": {"ANALYTICS_PROFILE"},
    "integration_service": {"DATA_FLOW"}, "backup_service": {"BACKUP_ARCHIVE"},
    "security_service": {"SECURITY_AUDIT_LOG"}, "legal_service": {"LEGAL_HOLD_RECORD"},
    "identity_service": {"ACCOUNT_TOMBSTONE"}, "account_service": {"USER_PROFILE"},
}
# Category comes from the record's own fields, so a row reported under one gold system can carry
# another category (a contact_lookup row on an analytics_* resource is MARKETING_PROFILE, rule_10).
ALT_CATEGORY = {"analytics_platform": {"MARKETING_PROFILE"}}
FINDING_STATE = {"REPORT_REMAINING_RECORD": "FOUND", "REPORT_EXPOSED_PII": "IDENTIFIABLE",
                 "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE"}
VERIFY_STATE = {"VERIFY_RECORD_ABSENT": "NOT_FOUND", "VERIFY_RECORD_PRESENT": "FOUND",
                "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
                "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED"}


def read(name):
    with open(DS / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def implied(steps):
    """resource -> (system, implied state at audit, gold evidence ids cited, phase of implication)."""
    by = defaultdict(list)
    for s in steps:
        by[(s["system_id"], s["resource_id"])].append(s)
    out = {}
    for key, ss in by.items():
        finding = next((s for s in ss if s["operation"] in FINDING_STATE), None)
        if finding:
            state, why = FINDING_STATE[finding["operation"]], "finding"
        else:
            v = next(s for s in ss if s["operation"] in VERIFY_STATE)
            state, why = VERIFY_STATE[v["operation"]], "verify-only"
        out[key] = (state, why, sorted({s["evidence_id"] for s in ss if s["evidence_id"]}))
    return out


def latest_states(obs_list, audit_at):
    """States from the newest observation(s) with file captured_at <= audit_at, by observed_at."""
    ok = [o for o in obs_list if visible(o, audit_at)]  # per file AND per row
    if not ok:
        return None, set(), []
    top = max(o.observed_at for o in ok)
    newest = [o for o in ok if o.observed_at == top]
    return newest, {o.state for o in newest}, ok


def part1(store, list_all):
    cases = {r["case_id"]: r for r in read("sample_deletion_requests.csv")}
    gold = {r["case_id"]: r for r in read("sample_output.csv")}
    cat_rows = defaultdict(list)
    mism = []
    for cid, g in sorted(gold.items()):
        audit = cases[cid]["audit_at"]
        for (system, rid), (state, why, cited) in implied(json.loads(g["steps_json"])).items():
            if system == "account_service":
                continue
            cats = SYSTEM_CATEGORY.get(system, set()) | ALT_CATEGORY.get(system, set())
            obs = [o for o in store.by_resource.get(rid, []) if o.category in cats]
            newest, states, ok = latest_states(obs, audit)
            cited_hit = [e for e in cited if any(o.file_id == e for o in store.by_resource.get(rid, []))]
            exists = bool(obs)
            exists_pre = bool(ok)
            match_latest = state in states
            match_any = any(o.state == state for o in ok)
            # absence is proven by a NOT_FOUND row or by no row at all (unverifiable here)
            row = dict(case=cid, system=system, rid=rid, state=state, why=why, cited=cited,
                       cats=sorted(cats), exists=exists, exists_pre=exists_pre, latest=sorted(states),
                       match_latest=match_latest, match_any=match_any, cited_hit=bool(cited_hit),
                       files=sorted({o.file_id for o in obs}), audit=audit,
                       late_only=exists and not exists_pre)
            cat_rows[(system, state)].append(row)
            if not (match_latest and (cited_hit or not cited)):
                mism.append(row)
    print("PART 1: per (system, implied state) recall, non-account resources, state = latest obs <= audit_at")
    print(f"{'system':20s}{'implied':14s}{'n':>3s} {'exists':>6s} {'pre-audit':>9s} {'latest=':>8s} "
          f"{'any=':>5s} {'cited-file-has-row':>19s}")
    tot = Counter()
    for (system, state), rows in sorted(cat_rows.items()):
        n = len(rows)
        c = dict(exists=sum(r["exists"] for r in rows), pre=sum(r["exists_pre"] for r in rows),
                 lat=sum(r["match_latest"] for r in rows), any=sum(r["match_any"] for r in rows),
                 cit=sum(r["cited_hit"] for r in rows))
        print(f"{system:20s}{state:14s}{n:3d} {c['exists']:6d} {c['pre']:9d} {c['lat']:8d} {c['any']:5d} "
              f"{c['cit']:19d}")
        tot.update(n=n, **c)
    print(f"{'TOTAL':34s}{tot['n']:3d} {tot['exists']:6d} {tot['pre']:9d} {tot['lat']:8d} {tot['any']:5d} "
          f"{tot['cit']:19d}")
    print(f"\nMISMATCH / MISSING LIST ({len(mism)} of {tot['n']}): resource, expected, found-latest, "
          "store files, gold-cited files")
    for r in mism if not list_all else [x for rs in cat_rows.values() for x in rs]:
        reason = ("NOT IN STORE" if not r["exists"] else
                  "only in post-audit files" if r["late_only"] else
                  "state mismatch" if not r["match_latest"] else "gold-cited file has no row")
        print(f"  {r['case']} {r['system']:18s}{r['rid']:20s} want={r['state']:12s} got={r['latest']} "
              f"files={r['files']} cited={r['cited']} -> {reason}")
    return cat_rows


def part2(store):
    print("\nPART 2: observations with observed_at > file captured_at")
    for o in store.obs:
        if o.observed_at > o.captured_at:
            print(f"  {o.file_id} cap={o.captured_at} obs={o.observed_at} {o.system_id} {o.resource_id} "
                  f"{o.category} {o.state} source={o.source}")


def part4(store, cat_rows):
    """Do ignored log lines touch sample subjects/resources?"""
    ident = {r["user_id"]: r for r in read("user_identities.csv")}
    by_email = {r["email"].lower(): r["user_id"] for r in ident.values()}
    hist = defaultdict(set)
    for a in read("user_audit_log.csv"):
        for v in (a["old_value"], a["new_value"]):
            if v:
                hist[a["user_id"]].add(v.lower())
                if "@" in v:
                    by_email.setdefault(v.lower(), a["user_id"])
    sample_ids = set()
    for r in read("sample_deletion_requests.csv"):
        uid = by_email.get(r["email"].lower())
        sample_ids.add(r["email"].lower())
        if uid:
            sample_ids |= {uid, ident[uid]["email"].lower(), ident[uid]["email_hash"].lower()} | hist[uid]
    sample_rids = {r["rid"] for rows in cat_rows.values() for r in rows}
    ignored_keys = {tuple(k.split(":", 1)) for k in normalizer.IGNORED_NOTE}
    hits, fields = [], defaultdict(Counter)
    for f in sorted((DS / "evidence" / "logs").glob("*.log")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            d, r = parse_line(ln)
            if not d:
                continue
            key = (normalizer.SERVICE_ALIAS.get(r.get("service"), r.get("service")), r.get("event"))
            if key not in ignored_keys:
                continue
            for k in r:
                if k not in ("ts", "level", "service", "trace_id", "event", "instance", "pod", "region"):
                    fields[key][k] += 1
            vals = {str(v).lower() for v in r.values() if v}
            if (vals & sample_ids) or (vals & sample_rids):
                hits.append((f.name, key, r.get("resource_id"), sorted(vals & (sample_ids | sample_rids))[:2]))
    cache_like = {k for k in ignored_keys if k[1] in ("cache_lookup", "deletion_marker_check")}
    print("\nPART 4: ignored log events vs sample identifiers/resources")
    print(f"  ignored event kinds: {len(ignored_keys)}  sample identifiers: {len(sample_ids)} "
          f"sample resources: {len(sample_rids)}")
    print(f"  ignored lines touching a sample identifier or gold resource: {len(hits)}")
    per = Counter(k for _, k, _, _ in hits)
    for k, n in sorted(per.items()):
        print(f"    {k[0]}:{k[1]} lines={n}")
    for h in hits[:40]:
        print("   ", h)
    print("  payload fields carried by cache_lookup / deletion_marker_check lines:")
    for k in sorted(cache_like):
        print(f"    {k[0]}:{k[1]} {dict(fields[k])}")


def main():
    store, rows = coverage.run()
    cat_rows = part1(store, "--list-all" in sys.argv)
    part2(store)
    part4(store, cat_rows)


if __name__ == "__main__":
    main()
