"""EVALUATION ONLY (uses gold). Score the co-activity signal; see code/coactivity.py.
Usage: python3 -I code/evaluation/coactivity_eval.py
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "code"))
import account_linker as al
import agent
import coactivity as co
import coverage
from identity import IdentityGraph
from state import linked_resources


def rows(n):
    with open(ROOT / "dataset" / n, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main():
    store, _ = coverage.run()
    graph = IdentityGraph.load()
    samples, evals = rows("sample_deletion_requests.csv"), rows("deletion_requests.csv")
    pool = {c["case_id"]: c for c in samples + evals}
    sh = agent.build_shared(store, graph, pool)
    by_res = co.load_by_resource()
    dele = {a["resource_id"]: a for a in sh.activities}
    gold = {r["case_id"]: [s["resource_id"] for s in json.loads(r["steps_json"]) if s["system_id"] == "account_service"]
            for r in rows("sample_output.csv")}

    def known(c):
        u = graph.resolve_one(c["email"])
        return [r for r in linked_resources(store, graph.identifiers(u), c["audit_at"], c["requested_at"])
                if not r.startswith("account_")] if u else []

    def cands(c, lk):
        ev = al.evidence_accounts(c, store, graph)
        return sorted(ev | set(lk.candidates) | ({lk.account} if lk.account else set()))

    def run(c, candidate_set):
        return co.decide(candidate_set, dele, co.case_activities(by_res, known(c)))

    print(f"window ratio {co.WIN_RATIO}")
    print("\nAMBIGUOUS SAMPLES (linker abstained with >=2 candidates)")
    amb = Counter()
    for c in samples:
        lk = sh.links[c["case_id"]]
        if lk.method != "abstain" or len(lk.candidates) < 2:
            continue
        pick, why = run(c, cands(c, lk))
        g = gold[c["case_id"]][0]
        res = "abstain" if not pick else "right" if pick == g else "WRONG"
        amb[res] += 1
        print(f"  {c['case_id']}: candidates={cands(c, lk)} gold={g} pick={pick or '-'} ({why}) -> {res}")
    print("  totals:", dict(amb))

    print("\nCALIBRATION: 24 uniquely linked samples, candidate set = evidence + status-consistent window accounts")
    cal = Counter()
    for c in samples:
        lk = sh.links[c["case_id"]]
        if not lk.account:
            continue
        cs = sorted(al.window_candidates(c, sh.activities) | al.evidence_accounts(c, store, graph) | {lk.account})
        if len(cs) < 2:
            cal["single candidate (trivially agrees)"] += 1
            continue
        pick, why = co.decide(cs, dele, co.case_activities(by_res, known(c)))
        cal["abstain" if not pick else "agrees with known answer" if pick == lk.account else "DISAGREES (wrong)"] += 1
    print("  ", dict(cal))

    print("\nEVAL ABSTENTIONS (156 = linker abstained on deletion requests among the 518)")
    picks = {}
    stat = Counter()
    for c in evals:
        lk = sh.links.get(c["case_id"])
        if lk is None or lk.method != "abstain":
            continue
        if len(lk.candidates) < 2:
            stat["no candidate / <2 candidates (untouched)"] += 1
            continue
        pick, why = run(c, cands(c, lk))
        if pick:
            picks[c["case_id"]] = pick
        else:
            stat["abstain: " + why] += 1
    used = Counter(picks.values())
    coll = {k for k, v in picks.items() if used[v] > 1}
    stat["resolved (clear win, unique)"] = len(picks) - len(coll)
    stat["dropped: two requests chose the same account"] = len(coll)
    for k, v in sorted(stat.items()):
        print(f"  {v:4d}  {k}")
    print(f"  total abstentions considered: {sum(stat.values()) - len(coll) + 0}")


if __name__ == "__main__":
    main()
