"""Run the deletion-audit pipeline.

  python3 code/main.py                                  # all 518 cases -> output.csv
  python3 code/main.py --requests dataset/sample_deletion_requests.csv --out /tmp/sample_pred.csv
Reads only dataset/. Deterministic: no network, no randomness.
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coverage
import intent
import llm
from identity import IdentityGraph
from account_linker import link_all, load_activities
from policy import classify_intent, plan_case, steps_json

ROOT = Path(__file__).resolve().parent.parent


def policy_ops():
    from policy import OP_ORDER
    return list(OP_ORDER)


def read_cases(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run(requests_path, intent_mode="off"):
    """Plan every case in requests_path. The account linker eliminates across ALL known requests
    (samples and evaluation together) because an account can only belong to one request.
    intent_mode: "off" = regex + CLASSIFY_REQUEST activity votes only (no model, used by tests);
    "online" = also one short cached model call per case; "offline" = model votes replayed from the cache only."""
    store, _ = coverage.run()
    graph = IdentityGraph.load()
    cases = read_cases(requests_path)
    pool = {c["case_id"]: c for p in ("sample_deletion_requests.csv", "deletion_requests.csv")
            for c in read_cases(ROOT / "dataset" / p)}
    pool.update({c["case_id"]: c for c in cases})
    decisions = intent.run_intent_step(list(pool.values()), store, graph, intent_mode)
    links = link_all([c for c in pool.values() if decisions[c["case_id"]].authorized], store, graph, load_activities())
    results = []
    for c in cases:
        r = plan_case(c, store, graph, links.get(c["case_id"]), decisions[c["case_id"]].authorized)
        r.intent = decisions[c["case_id"]]
        results.append(r)
    run.decisions, run.pool = decisions, list(pool.values())   # kept for the vote log and the agent runner
    return sorted(results, key=lambda r: r.case_id), store


def write(results, out):
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for r in results:
            w.writerow([r.case_id, r.status, steps_json(r.steps)])


def stats(results):
    st = Counter(r.status for r in results)
    ops = Counter((s["phase"], s["operation"], s["system_id"]) for r in results for s in r.steps)
    print("cases by status:", dict(st))
    print("steps by phase/operation/system:")
    for (ph, op, sy), n in sorted(ops.items(), key=lambda x: (["FINDING", "REMEDIATION", "VERIFICATION"].index(x[0][0]), x[0][1], x[0][2])):
        print(f"  {ph:12s} {op:26s} {sy:20s} {n}")
    print("total steps:", sum(ops.values()))
    print("account link methods:", dict(Counter(n.split(" linked by ")[1] if " linked by " in n else "abstain"
          for r in results for n in r.notes if n.startswith("account ")))) 
    print("cases with no account step:", sum(1 for r in results if r.status != "NOT_APPLICABLE" and not r.account_ids))
    print("late-file audit id lookups:", sum(1 for r in results for n in r.notes if n.startswith("audit id")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", default=str(ROOT / "dataset" / "deletion_requests.csv"))
    ap.add_argument("--out", default=str(ROOT / "output.csv"))
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--offline", action="store_true", help="replay model calls from code/cache only (no network, no key)")
    ap.add_argument("--no-model", action="store_true", help="skip the model intent vote (regex + activity only)")
    a = ap.parse_args()
    mode = "off" if a.no_model else "offline" if (a.offline or not llm.api_key()) else "online"
    if mode == "offline" and not a.offline and not a.no_model:
        print("no GROQ_API_KEY found: replaying model votes from the cache")
    results, _ = run(a.requests, mode)
    write(results, a.out)
    print(f"wrote {len(results)} rows to {a.out} (intent mode: {mode})")
    if mode != "off":
        name = llm.cheapest_chat_model(llm.discover_models(mode == "offline"))
        dis = intent.write_log(run.decisions, run.pool, name)
        print(f"intent votes: {len(dis)} disagreement(s), logged in {intent.LOG.relative_to(ROOT)}")
    if a.stats:
        stats(results)


if __name__ == "__main__":
    main()
