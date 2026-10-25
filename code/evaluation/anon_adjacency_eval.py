"""EVALUATION ONLY (uses gold). Does activity-id adjacency after a linked account-delete activity
identify the already-anonymized analytics resource of a request?

Rule under test: for a request with a unique account link, take the account's DELETE_RECORD activity (number N)
and the NEXT ANONYMIZE_RECORD activity after N (any result). If that activity is SUCCESS, its resource is
ANONYMIZED in the store at audit_at, it is not already linked to the case by evidence, it is within
`maxgap` activity numbers and not later than reported_at+10min, and no other request picks the same activity,
propose it. Accepted only if it makes 0 wrong proposals on the samples.
Usage: python3 -I code/evaluation/anon_adjacency_eval.py
"""
import csv
import datetime as dt
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "code"))
import account_linker as al
import coverage
from identity import IdentityGraph
from policy import classify_intent
from state import get_state_at, linked_resources

T = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
num = lambda a: int(a["activity_id"].split("_")[1])


def rows(n):
    with open(ROOT / "dataset" / n, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main():
    store, _ = coverage.run()
    graph = IdentityGraph.load()
    samples, evals = rows("sample_deletion_requests.csv"), rows("deletion_requests.csv")
    cases = [c for c in samples + evals if classify_intent(c["request_text"])]
    adel = al.load_activities()
    links = al.link_all(cases, store, graph, adel)
    del_act = {a["resource_id"]: a for a in adel}
    anon = sorted((a for a in csv.DictReader(open(ROOT / "dataset" / "system_activity.csv", encoding="utf-8"))
                   if a["event_type"] == "ANONYMIZE_RECORD"), key=num)
    gold = {r["case_id"]: json.loads(r["steps_json"]) for r in rows("sample_output.csv")}
    truth = {cid: {s["resource_id"] for s in st if s["operation"] == "VERIFY_RECORD_ANONYMIZED"
                   and not any(f["resource_id"] == s["resource_id"] and f["phase"] == "FINDING" for f in st)}
             for cid, st in gold.items()}
    by_id = {c["case_id"]: c for c in cases}
    print(f"{'maxgap':>7} {'proposed(samples)':>18} {'right':>6} {'wrong':>6} {'recall(of 7)':>13} | eval: proposed, collisions")
    for maxgap in (3, 5, 10, 20, 10**9):
        prop = {}
        for c in cases:
            lk = links[c["case_id"]]
            if not lk.account or lk.account not in del_act:
                continue
            n0 = num(del_act[lk.account])
            nxt = next((a for a in anon if num(a) > n0), None)
            if not nxt or nxt["reported_result"] != "SUCCESS" or num(nxt) - n0 > maxgap:
                continue
            if T(nxt["event_time"]) > T(c["reported_at"]) + dt.timedelta(minutes=10):
                continue
            rid = nxt["resource_id"]
            if not any(s.state == "ANONYMIZED" for s in get_state_at(store, rid, c["audit_at"])):
                continue
            linked = set(linked_resources(store, graph.identifiers(graph.resolve_one(c["email"])),
                                          c["audit_at"], c["requested_at"]))
            if rid in linked:
                continue
            prop[c["case_id"]] = rid
        used = Counter(prop.values())
        coll = {k for k, v in prop.items() if used[v] > 1}
        prop = {k: v for k, v in prop.items() if k not in coll}
        right = sum(1 for k, v in prop.items() if k in gold and v in truth[k])
        wrong = sum(1 for k, v in prop.items() if k in gold and v not in truth[k])
        print(f"{maxgap if maxgap < 10**9 else 'any':>7} {sum(k in gold for k in prop):>18} {right:>6} {wrong:>6} "
              f"{right:>10}/7 | {sum(k not in gold for k in prop)}, {len(coll)}")


if __name__ == "__main__":
    main()
