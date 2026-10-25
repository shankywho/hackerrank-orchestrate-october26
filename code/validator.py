"""Validate an output.csv: schema, ordering, step numbers and the evidence_id rules.

  python3 code/validator.py [--pred output.csv] [--requests dataset/deletion_requests.csv]
Exit code 1 and a list of errors when anything is wrong. Checks only what problem_statement.md and
docs/plan/01_SPEC.md require; it does not compare against gold.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy import OP_PHASE, OP_STATE, PHASES, sort_key

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
UNRESOLVED = "UNRESOLVED"  # reported separately: a data gap, not a format error


def validate_row(case_id, status, steps_text, audit_at, file_times, known_resources=None):
    """Return a list of error strings for one output row."""
    err = []
    E = lambda m: err.append(f"{case_id}: {m}")
    if status not in STATUSES:
        E(f"bad deletion_status {status!r}")
        return err
    try:
        steps = json.loads(steps_text)
    except json.JSONDecodeError as exc:
        return err + [f"{case_id}: steps_json is not valid JSON ({exc})"]
    if not isinstance(steps, list):
        return err + [f"{case_id}: steps_json is not a JSON array"]
    if status == "NOT_APPLICABLE":
        if steps:
            E("NOT_APPLICABLE must have steps_json []")
        return err
    if not steps:
        E(f"{UNRESOLVED}: {status} case has no grounded resources, so no steps can be cited")
        return err
    seen = set()
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict) or sorted(s) != sorted(FIELDS):
            E(f"step {i} fields must be exactly {FIELDS}")
            return err
        if s["step_no"] != i or isinstance(s["step_no"], bool):
            E(f"step_no {s['step_no']!r} at position {i}: must be consecutive from 1")
        if s["phase"] not in PHASES:
            E(f"step {i} unknown phase {s['phase']!r}")
        if s["operation"] not in OP_STATE:
            E(f"step {i} unknown operation {s['operation']!r}")
            continue
        if OP_PHASE[s["operation"]] != s["phase"]:
            E(f"step {i} operation {s['operation']} does not belong to phase {s['phase']}")
        if OP_STATE[s["operation"]] != s["state"]:
            E(f"step {i} state {s['state']!r} does not match operation {s['operation']}")
        if not s["system_id"] or not s["resource_id"]:
            E(f"step {i} empty system_id or resource_id")
        key = (s["system_id"], s["resource_id"], s["operation"])
        if key in seen:
            E(f"step {i} duplicates {key}")
        seen.add(key)
        ev = s["evidence_id"]
        if ev:
            if ev not in file_times:
                E(f"step {i} cites unknown evidence {ev!r}")
            elif file_times[ev] > audit_at:
                E(f"step {i} cites {ev} captured {file_times[ev]} after audit_at {audit_at}")
        if known_resources is not None and s["resource_id"] not in known_resources:
            E(f"step {i} resource {s['resource_id']} is in no evidence or activity candidate set")
    if err:
        return err
    if [sort_key(s) for s in steps] != sorted(sort_key(s) for s in steps):
        E("steps are not ordered by phase, operation order, system_id, resource_id")
    phases = {s["phase"] for s in steps}
    if status == "COMPLETE":
        if phases != {"VERIFICATION"}:
            E("COMPLETE must contain verification steps only")
        for i, s in enumerate(steps, 1):
            if not s["evidence_id"]:
                E(f"step {i}: COMPLETE verification step must cite evidence")
    else:
        if "FINDING" not in phases:
            E("INCOMPLETE must contain at least one finding")
        for i, s in enumerate(steps, 1):
            if s["phase"] == "FINDING" and not s["evidence_id"]:
                E(f"step {i}: finding must cite evidence")
            if s["phase"] != "FINDING" and s["evidence_id"]:
                E(f"step {i}: {s['phase']} step must have empty evidence_id")
        found = {(s["system_id"], s["resource_id"]) for s in steps if s["phase"] == "FINDING"}
        fixed = {(s["system_id"], s["resource_id"]) for s in steps if s["phase"] == "REMEDIATION"}
        verified = {(s["system_id"], s["resource_id"]) for s in steps if s["phase"] == "VERIFICATION"}
        if found - fixed:
            E(f"findings without remediation: {sorted(found - fixed)}")
        if fixed - found:
            E(f"remediation without finding: {sorted(fixed - found)}")
        if fixed - verified:
            E(f"remediated records without verification: {sorted(fixed - verified)}")
    return err


def validate_file(pred_path, requests_path, file_times, known_resources=None):
    """Return (errors, unresolved). unresolved lists cases the planner could not ground in any resource."""
    errors = []
    with open(requests_path, newline="", encoding="utf-8") as fh:
        cases = {r["case_id"]: r for r in csv.DictReader(fh)}
    with open(pred_path, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        header = next(rd, None)
        rows = list(rd)
    if header != ["case_id", "deletion_status", "steps_json"]:
        errors.append(f"header must be case_id,deletion_status,steps_json, got {header}")
    ids = [r[0] for r in rows if r]
    if any(len(r) != 3 for r in rows):
        errors.append("every row must have exactly 3 columns")
    if len(ids) != len(set(ids)):
        errors.append("duplicate case_id rows")
    if set(ids) != set(cases):
        errors.append(f"case_id mismatch: missing {sorted(set(cases) - set(ids))[:5]} "
                      f"extra {sorted(set(ids) - set(cases))[:5]}")
    if ids != sorted(ids):
        errors.append("rows are not sorted by case_id (string order)")
    for r in rows:
        if len(r) == 3 and r[0] in cases:
            errors += validate_row(r[0], r[1], r[2], cases[r[0]]["audit_at"], file_times, known_resources)
    unresolved = [e for e in errors if UNRESOLVED in e]
    return [e for e in errors if UNRESOLVED not in e], unresolved


def manifest_times():
    import normalizer
    return {r["file_id"]: r["captured_at"] for r in normalizer.load_manifest()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default=str(ROOT / "output.csv"))
    ap.add_argument("--requests", default=str(ROOT / "dataset" / "deletion_requests.csv"))
    a = ap.parse_args()
    errs, unresolved = validate_file(a.pred, a.requests, manifest_times())
    if unresolved:
        print(f"WARNING: {len(unresolved)} unresolved case(s) with no steps (data gap, not a format error):")
        for u in unresolved[:20]:
            print(" ", u.split(":")[0])
    if errs:
        print(f"INVALID: {len(errs)} error(s)")
        for e in errs[:50]:
            print(" ", e)
        sys.exit(1)
    print(f"VALID: {a.pred} (format checks pass; {len(unresolved)} unresolved case(s))")


if __name__ == "__main__":
    main()
