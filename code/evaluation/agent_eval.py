"""EVALUATION ONLY (uses gold). Run the agent on the samples and report accuracy / agreement.

  python3 -I code/evaluation/agent_eval.py --set ambiguous     # the ambiguous sample cases
  python3 -I code/evaluation/agent_eval.py --set samples       # all 32 samples
  add --offline to replay from cache, --force to ignore saved case results, --workers 2..4, --model ID
Gold is read only after the agent has finished; the model never sees it.
"""
import argparse
import csv
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "code"))
import agent
import coverage
import llm
from identity import IdentityGraph


def rows(name):
    with open(ROOT / "dataset" / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def p_at_least(probs, k):
    """P(X >= k) where X = number of correct picks under independent uniform guessing (exact)."""
    dist = [1.0]
    for p in probs:
        dist = [(dist[i] if i < len(dist) else 0) * (1 - p) + (dist[i - 1] * p if i > 0 else 0)
                for i in range(len(dist) + 1)]
    return sum(dist[k:])


def usage_totals(case_ids):
    f = ROOT / "code" / "evaluation" / "usage_calls.csv"
    tot = Counter()
    if f.exists():
        for r in csv.DictReader(open(f, encoding="utf-8")):
            if r["case_id"] in case_ids:
                tot["calls"] += 1
                tot["cached"] += int(r["cached"])
                tot["in"] += int(r["input_tokens"])
                tot["out"] += int(r["output_tokens"])
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["ambiguous", "samples"], default="ambiguous")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--model", default=None)
    a = ap.parse_args()

    store, _ = coverage.run()
    graph = IdentityGraph.load()
    samples, evals = rows("sample_deletion_requests.csv"), rows("deletion_requests.csv")
    pool = {c["case_id"]: c for c in samples + evals}
    sh = agent.build_shared(store, graph, pool)
    models = llm.discover_models(a.offline)
    model = llm.pick_chat_model(models, a.model)[0]["id"]
    print(f"model chosen from the API model list: {model}")

    gold = {r["case_id"]: [s["resource_id"] for s in json.loads(r["steps_json"]) if s["system_id"] == "account_service"]
            for r in rows("sample_output.csv")}
    chosen = [c for c in samples if a.set == "samples" or
              (sh.links[c["case_id"]].method == "abstain" and len(sh.links[c["case_id"]].candidates) > 1)]
    print(f"cases: {len(chosen)} ({a.set})")
    results = agent.run_cases(sh, chosen, model, a.workers, a.offline, a.force)
    errs = [r for r in results if "error" in r]
    ok = [r for r in results if "error" not in r]
    print(f"completed {len(ok)}, errors {len(errs)}")
    for e in errs[:5]:
        print("  ", e)

    # ---- account choice
    print("\nACCOUNT CHOICE (gold account is the account_service step in sample_output.csv)")
    def tally(rs, label):
        picks = [(r, gold[r["case_id"]][0] if gold[r["case_id"]] else "") for r in rs if r["deterministic"]["method"] == "abstain"]
        if not picks:
            return
        right = sum(1 for r, g in picks if r["verdict"] and r["verdict"]["account_id"] == g)
        wrong = sum(1 for r, g in picks if r["verdict"] and r["verdict"]["account_id"] not in (None, g))
        null = sum(1 for r, g in picks if r["verdict"] and r["verdict"]["account_id"] is None)
        bad = sum(1 for r, g in picks if not r["verdict"])
        probs = [1 / len(r["candidates"]) for r, g in picks if r["candidates"] and g in r["candidates"]]
        inc = sum(1 for r, g in picks if g in r["candidates"])
        k = right
        print(f"  {label}: n={len(picks)}  gold in candidate set: {inc}  right={right} wrong={wrong} null={null} unusable={bad}")
        if probs:
            print(f"      chance (uniform over candidates) expects {sum(probs):.2f} right; "
                  f"P(>= {k} right by chance) = {p_at_least(probs, k):.3f}")
    amb = [r for r in ok if r["deterministic"]["method"] == "abstain"]
    tally(amb, "all linker-ambiguous cases")
    tally([r for r in amb if r["evidence_candidates"]], "  of which evidence narrowed candidates")
    tally([r for r in amb if not r["evidence_candidates"]], "  of which activity-only candidates")
    for r in amb:
        g = gold[r["case_id"]][0] if gold[r["case_id"]] else ""
        v = r["verdict"]
        print(f"    {r['case_id']}: candidates={r['candidates']} evidence={r['evidence_candidates']} "
              f"gold={g} model={(v or {}).get('account_id', 'UNUSABLE')} tools={r['tool_calls']}")

    # ---- agreement with the deterministic plan (all completed cases)
    print("\nAGREEMENT WITH DETERMINISTIC PLAN")
    ic = Counter()
    ac = Counter()
    rev = Counter()
    for r in ok:
        v = r["verdict"]
        if not v:
            ic["unusable verdict"] += 1
            continue
        ic["intent agrees" if v["intent"] == r["deterministic"]["intent"] else "intent OVERRIDES"] += 1
        det = r["deterministic"]["account"]
        if det:
            ac["account agrees (unique link confirmed)" if v["account_id"] == det else
              "account declines (null)" if v["account_id"] is None else "account OVERRIDES"] += 1
        else:
            ac["abstain -> model picks a candidate" if v["account_id"] else "abstain -> model also null"] += 1
        rev[v["plan_review"]] += 1
    for name, c in (("intent", ic), ("account", ac), ("plan review", rev)):
        print(f"  {name}: {dict(c)}")
    print(f"  avg tool calls per case: {sum(r['tool_calls'] for r in ok) / max(1, len(ok)):.1f}; "
          f"invalid-id rejections: {sum(1 for r in ok if any('outside candidate' in p for p in r['problems']))}")
    concerns = Counter(c for r in ok if r["verdict"] for c in r["verdict"].get("concerns", []))
    for c, n in concerns.most_common(6):
        print(f"    concern x{n}: {c[:110]}")
    u = usage_totals({r["case_id"] for r in ok} | {"probe"})
    print(f"\nTOKENS (calls logged for these cases): calls={u['calls']} cache hits={u['cached']} "
          f"new input={u['in']} new output={u['out']}")


if __name__ == "__main__":
    main()
