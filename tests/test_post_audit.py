"""Post-audit exclusions on two real cases (fixtures named in the task, not solver code).
Run: python3 -I tests/test_post_audit.py
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
import main
from state import get_state_at

results, store = main.run(str(ROOT / "dataset" / "deletion_requests.csv"))
by = {r.case_id: r for r in results}
cases = {c["case_id"]: c for c in csv.DictReader(open(ROOT / "dataset" / "deletion_requests.csv", newline="", encoding="utf-8"))}
acts = list(csv.DictReader(open(ROOT / "dataset" / "system_activity.csv", newline="", encoding="utf-8")))


def show(cid):
    r = by[cid]
    print(f"{cid} {r.status} audit_at={cases[cid]['audit_at']}")
    for s in r.steps:
        print(f"   {s['step_no']} {s['phase']:12s} {s['system_id']:18s} {s['resource_id']:14s} {s['operation']:26s} ev={s['evidence_id']}")


# case 39: a connector repopulated search_4801 on 08-25, after the 08-08 audit
c39 = cases["case_39"]
sync = next(a for a in acts if a["resource_id"] == "data_flow_1121" and a["event_type"] == "SYNC_RECORD"
            and "search_4801" in a["details"])
assert sync["event_time"] > c39["audit_at"], "fixture: the repopulation really is after audit_at"
assert not get_state_at(store, "search_4801", c39["audit_at"]), "search_4801 is invisible at audit_at"
assert all(o.captured_at > c39["audit_at"] for o in store.by_resource["search_4801"]), "its only evidence is post-audit"
assert not any(s["resource_id"] == "search_4801" for s in by["case_39"].steps), "no step for search_4801"
assert any(s["resource_id"] == "data_flow_1121" and s["operation"] == "REPORT_ACTIVE_DATA_FLOW" for s in by["case_39"].steps)

# case 55: a restore job on 08-22 repopulated search_4882, after the 04-30 audit
c55 = cases["case_55"]
rest = next(a for a in acts if a["event_type"] == "RESTORE_BACKUP" and a["resource_id"] == "backup_9287")
assert rest["event_time"] > c55["audit_at"]
assert not any(s["resource_id"] == "search_4882" for s in by["case_55"].steps), "restore result is not used"
steps55 = {(s["resource_id"], s["operation"]) for s in by["case_55"].steps}
assert steps55 == {("backup_9287", "REPORT_RESTORE_RISK"), ("backup_9287", "BLOCK_RESTORE"),
                   ("backup_9287", "VERIFY_RESTORE_BLOCKED")}, steps55
for cid in ("case_39", "case_55"):
    for s in by[cid].steps:
        if s["evidence_id"]:
            assert store.file_times[s["evidence_id"]] <= cases[cid]["audit_at"], (cid, s)
print("ok post-audit exclusions")
show("case_39")
show("case_55")
