"""Policy engine invariants (one assertion per rule in docs/plan/01_SPEC.md). Run: python3 -I tests/test_policy.py"""
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
import coverage
from identity import IdentityGraph
from policy import OP_ORDER, OP_STATE, TREATMENT, _treatment_for, classify_intent, plan_case, sort_key

# decision table
assert TREATMENT["DELETE"] == ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT")
assert TREATMENT["REDACT"][1:] == ("REDACT_RECORD", "VERIFY_RECORD_REDACTED")
assert TREATMENT["ANONYMIZE"][1:] == ("ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED")
assert TREATMENT["FLOW"] == ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED")
assert TREATMENT["RESTORE"] == ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED")
assert set(OP_ORDER) == set(OP_STATE) and len(OP_ORDER) == 15

# sort_key: phase, then operation table order, then system, then resource
a = {"phase": "VERIFICATION", "operation": "VERIFY_RECORD_ABSENT", "system_id": "b", "resource_id": "x"}
b = dict(a, system_id="a", operation="VERIFY_RECORD_PRESENT")
c = dict(a, phase="FINDING", operation="REPORT_RESTORE_RISK")
d = dict(a, resource_id="a")
assert [sort_key(x) for x in sorted([a, b, c, d], key=sort_key)] == [sort_key(x) for x in (c, d, a, b)]

# intent
assert classify_intent("Please permanently delete the account associated with this email address.")
assert classify_intent("Je demande la suppression définitive du compte associé à cette adresse e-mail.")
assert not classify_intent("I'd like to download my account information. Please keep the account as it is.")
assert not classify_intent("Please remove me from marketing lists only. I still want receipts.")
assert not classify_intent("I am not asking for deletion, please correct my surname.")

store, _ = coverage.run()
graph = IdentityGraph.load()
cases = list(csv.DictReader(open(ROOT / "dataset" / "deletion_requests.csv", newline="", encoding="utf-8")))
ph = ["FINDING", "REMEDIATION", "VERIFICATION"]
na = 0
files = {}
for r in coverage.normalizer.load_manifest():
    files[r["file_id"]] = r["captured_at"]
for case in cases:
    res = plan_case(case, store, graph)
    assert res.status in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
    if res.status == "NOT_APPLICABLE":
        na += 1
        assert res.steps == []
        continue
    assert [s["step_no"] for s in res.steps] == list(range(1, len(res.steps) + 1))
    assert res.steps == sorted(res.steps, key=sort_key)
    assert (res.status == "INCOMPLETE") == any(s["phase"] == "FINDING" for s in res.steps)
    for s in res.steps:
        assert s["state"] == OP_STATE[s["operation"]]
        ev = s["evidence_id"]
        if ev:  # evidence captured after audit_at is never cited
            assert files[ev] <= case["audit_at"], (case["case_id"], s)
        if res.status == "INCOMPLETE" and s["phase"] != "FINDING":
            assert ev == ""
src = (ROOT / "code" / "policy.py").read_text()
assert not re.search(r"\b(case_\d+|user_\d+|account_\d+|search_\d+|audit_record_\d+)\b", src), "hardcoded id"
assert na == 10
print("ok", len(cases), "cases planned; not-applicable:", na)
