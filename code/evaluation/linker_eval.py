"""EVALUATION ONLY (uses gold). Score the account linker on the samples and describe it on the 518.
Usage: python3 -I code/evaluation/linker_eval.py
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "code"))
import account_linker as al
import coverage
from identity import IdentityGraph
from policy import classify_intent


def rows(name):
    with open(ROOT / "dataset" / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main():
    store, _ = coverage.run()
    graph = IdentityGraph.load()
    samples, evals = rows("sample_deletion_requests.csv"), rows("deletion_requests.csv")
    deletion = [c for c in samples + evals if classify_intent(c["request_text"])]
    links = al.link_all(deletion, store, graph, al.load_activities())
    gold = {r["case_id"]: [s["resource_id"] for s in json.loads(r["steps_json"]) if s["system_id"] == "account_service"]
            for r in rows("sample_output.csv")}
    print(f"requests in elimination pool: {len(deletion)} (samples {sum(c['case_id'] in gold for c in deletion)}, "
          f"eval {sum(c['case_id'] not in gold for c in deletion)})")
    print("\nTABLE 1: samples (32)")
    res = Counter()
    per = []
    for cid, g in sorted(gold.items()):
        lk = links[cid]
        want = g[0] if g else ""
        if lk.method == "abstain":
            res["abstain"] += 1
            per.append((cid, "abstain", lk.reason, len(lk.candidates), want in lk.candidates))
        elif lk.account == want:
            res["correct"] += 1
            res[f"correct via {lk.method}"] += 1
        else:
            res["wrong"] += 1
            per.append((cid, "WRONG", lk.method, lk.account, want))
    for k in ("correct", "wrong", "abstain", "correct via evidence", "correct via singleton"):
        print(f"  {k:24s}{res[k]}")
    for p in per:
        print("   ", p)
    print("\nTABLE 2: the 518 evaluation requests")
    ev_ids = {c["case_id"] for c in evals}
    t = Counter(links[c["case_id"]].method for c in deletion if c["case_id"] in ev_ids)
    ab = [links[c["case_id"]] for c in deletion if c["case_id"] in ev_ids and links[c["case_id"]].method == "abstain"]
    print(f"  deletion requests among them: {sum(c['case_id'] in ev_ids for c in deletion)}")
    print(f"  unique (evidence): {t['evidence']}   unique (singleton elimination): {t['singleton']}   "
          f"unique total: {t['evidence'] + t['singleton']}")
    print(f"  ambiguous: {sum(1 for a in ab if a.candidates)}   no candidate in window: {sum(1 for a in ab if not a.candidates)}")
    print("  ambiguity sizes:", dict(sorted(Counter(len(a.candidates) for a in ab if a.candidates).items())))
    tb = al.tiebreak(deletion, links)
    ok = sum(1 for c, a in tb.items() if c in gold and gold[c] and gold[c][0] == a)
    bad = sum(1 for c, a in tb.items() if c in gold and gold[c] and gold[c][0] != a)
    print(f"\nTIE-BREAK (last resort, NOT applied): would assign {len(tb)} requests "
          f"({sum(c in gold for c in tb)} samples: {ok} right, {bad} wrong)")


if __name__ == "__main__":
    main()
