"""Validator must accept good rows and reject each kind of bad row. Run: python3 -I tests/test_validator.py"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))
from validator import validate_row

FT = {"evidence_01": "2026-01-01T00:00:00Z", "evidence_02": "2026-12-01T00:00:00Z"}
AUDIT = "2026-06-01T00:00:00Z"


def st(n, phase, system, rid, op, state, ev=""):
    return {"step_no": n, "phase": phase, "system_id": system, "resource_id": rid, "operation": op, "state": state,
            "evidence_id": ev}


GOOD_INC = [
    st(1, "FINDING", "search_service", "s1", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
    st(2, "REMEDIATION", "search_service", "s1", "DELETE_RECORD", "ABSENT"),
    st(3, "VERIFICATION", "search_service", "s1", "VERIFY_RECORD_ABSENT", "ABSENT"),
]
GOOD_COM = [st(1, "VERIFICATION", "account_service", "a1", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_01")]


def check(status, steps, expect_ok, needle=""):
    errs = validate_row("c", status, json.dumps(steps) if not isinstance(steps, str) else steps, AUDIT, FT)
    assert (not errs) == expect_ok and (expect_ok or any(needle in e for e in errs)), (status, needle, errs)


check("INCOMPLETE", GOOD_INC, True)
check("COMPLETE", GOOD_COM, True)
check("NOT_APPLICABLE", [], True)
check("NOT_APPLICABLE", GOOD_COM, False, "[]")
check("MAYBE", GOOD_COM, False, "deletion_status")
check("COMPLETE", "not json", False, "valid JSON")
check("COMPLETE", '{"a":1}', False, "array")
bad = copy.deepcopy(GOOD_INC); bad[1]["step_no"] = 5
check("INCOMPLETE", bad, False, "consecutive")
bad = copy.deepcopy(GOOD_INC); bad[0]["extra"] = 1
check("INCOMPLETE", bad, False, "fields")
bad = copy.deepcopy(GOOD_INC); bad[1]["state"] = "PRESENT"
check("INCOMPLETE", bad, False, "state")
bad = copy.deepcopy(GOOD_INC); bad[0]["phase"] = "REMEDIATION"
check("INCOMPLETE", bad, False, "phase")
check("INCOMPLETE", list(reversed([dict(s, step_no=i) for i, s in enumerate(reversed(GOOD_INC), 1)])), False, "")
bad = [dict(s, step_no=i) for i, s in enumerate([GOOD_INC[2], GOOD_INC[0], GOOD_INC[1]], 1)]
check("INCOMPLETE", bad, False, "ordered")
bad = copy.deepcopy(GOOD_INC); bad[0]["evidence_id"] = "evidence_02"
check("INCOMPLETE", bad, False, "after audit_at")
bad = copy.deepcopy(GOOD_INC); bad[0]["evidence_id"] = "evidence_99"
check("INCOMPLETE", bad, False, "unknown evidence")
bad = copy.deepcopy(GOOD_INC); bad[0]["evidence_id"] = ""
check("INCOMPLETE", bad, False, "finding must cite")
bad = copy.deepcopy(GOOD_INC); bad[2]["evidence_id"] = "evidence_01"
check("INCOMPLETE", bad, False, "empty evidence_id")
bad = copy.deepcopy(GOOD_INC); del bad[1]; bad = [dict(s, step_no=i) for i, s in enumerate(bad, 1)]
check("INCOMPLETE", bad, False, "without remediation")
bad = copy.deepcopy(GOOD_COM); bad[0]["evidence_id"] = ""
check("COMPLETE", bad, False, "must cite")
check("COMPLETE", GOOD_INC, False, "verification steps only")
check("INCOMPLETE", GOOD_COM, False, "at least one finding")
check("COMPLETE", GOOD_COM + [dict(GOOD_COM[0], step_no=2)], False, "duplicates")
errs = validate_row("c", "COMPLETE", json.dumps(GOOD_COM), AUDIT, FT, known_resources={"zzz"})
assert any("candidate set" in e for e in errs)
print("ok validator")
