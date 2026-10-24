"""Score an output.csv against a gold CSV (default: dataset/sample_output.csv).

Metrics:
  status      case-level deletion_status accuracy + confusion matrix
  step-level  precision/recall/F1 over whole steps (step_no ignored), micro and macro
  field-level accuracy per step field over gold steps, aligned by (resource_id, operation)
  sequence    exact ordered match per case, and LCS ratio 2*LCS/(|gold|+|pred|)

Usage:
  python3 code/scorer.py [--pred output.csv] [--gold dataset/sample_output.csv] [--verbose]
  python3 code/scorer.py --self-test
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
STEP_KEY = [f for f in FIELDS if f != "step_no"]
STATUSES = ["COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"]


def load(path):
    """case_id -> (status, steps list). Bad steps_json becomes None (scored as empty)."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                steps = json.loads(row["steps_json"])
                if not isinstance(steps, list):
                    steps = None
            except (json.JSONDecodeError, TypeError):
                steps = None
            out[row["case_id"]] = (row["deletion_status"], steps)
    return out


def tup(step):
    return tuple(str(step.get(f, "")) for f in STEP_KEY)


def lcs_len(a, b):
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[-1]))
        prev = cur
    return prev[-1]


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def score(gold, pred):
    res = {"n": len(gold), "missing": [c for c in gold if c not in pred],
           "extra": [c for c in pred if c not in gold], "invalid_json": []}
    confusion = Counter()
    status_ok = 0
    tp = fp = fn = 0
    per_case_f1 = []
    field_ok = Counter()
    field_tot = 0
    seq_exact = 0
    lcs_ratios = []
    per_case = {}

    for cid, (gstatus, gsteps) in sorted(gold.items()):
        gsteps = gsteps or []
        pstatus, psteps = pred.get(cid, ("<MISSING>", []))
        if psteps is None:
            res["invalid_json"].append(cid)
            psteps = []
        confusion[(gstatus, pstatus)] += 1
        status_ok += gstatus == pstatus

        gc, pc = Counter(map(tup, gsteps)), Counter(map(tup, psteps))
        ctp = sum((gc & pc).values())
        cfp, cfn = sum(pc.values()) - ctp, sum(gc.values()) - ctp
        tp, fp, fn = tp + ctp, fp + cfp, fn + cfn
        per_case_f1.append(prf(ctp, cfp, cfn)[2])

        # field-level: align gold step to a pred step with same (resource_id, operation)
        pidx = {}
        for s in psteps:
            pidx.setdefault((s.get("resource_id"), s.get("operation")), s)
        for g in gsteps:
            p = pidx.get((g.get("resource_id"), g.get("operation")))
            field_tot += 1
            if p is not None:
                for f in FIELDS:
                    field_ok[f] += str(p.get(f, "")) == str(g.get(f, ""))

        gseq, pseq = [tup(s) for s in gsteps], [tup(s) for s in psteps]
        exact = gstatus == pstatus and gseq == pseq and \
            [s.get("step_no") for s in psteps] == list(range(1, len(psteps) + 1))
        seq_exact += exact
        denom = len(gseq) + len(pseq)
        ratio = 2 * lcs_len(gseq, pseq) / denom if denom else 1.0
        lcs_ratios.append(ratio)
        per_case[cid] = {"status_ok": gstatus == pstatus, "f1": per_case_f1[-1],
                         "lcs": ratio, "exact": exact, "gold_n": len(gseq), "pred_n": len(pseq)}

    n = len(gold) or 1
    p, r, f = prf(tp, fp, fn)
    res.update({
        "status_acc": status_ok / n, "confusion": confusion,
        "step_tp": tp, "step_fp": fp, "step_fn": fn,
        "step_p": p, "step_r": r, "step_f1": f, "step_macro_f1": sum(per_case_f1) / n,
        "field_acc": {f: field_ok[f] / field_tot if field_tot else 1.0 for f in FIELDS},
        "field_gold_steps": field_tot,
        "seq_exact": seq_exact / n, "seq_exact_n": seq_exact, "seq_lcs": sum(lcs_ratios) / n,
        "per_case": per_case,
    })
    return res


def report(res, verbose=False):
    n = res["n"]
    L = []
    L.append(f"cases scored: {n}  missing in pred: {len(res['missing'])}  "
             f"extra in pred: {len(res['extra'])}  invalid steps_json: {len(res['invalid_json'])}")
    L.append(f"STATUS accuracy: {res['status_acc']:.4f}")
    L.append("  confusion (gold -> pred):")
    for (g, p), c in sorted(res["confusion"].items()):
        L.append(f"    {g:15s} -> {p:15s} {c}")
    L.append(f"STEP-LEVEL (whole step, step_no ignored, micro): "
             f"P={res['step_p']:.4f} R={res['step_r']:.4f} F1={res['step_f1']:.4f} "
             f"(tp={res['step_tp']} fp={res['step_fp']} fn={res['step_fn']})")
    L.append(f"  macro per-case F1: {res['step_macro_f1']:.4f}")
    L.append(f"FIELD-LEVEL accuracy over {res['field_gold_steps']} gold steps "
             "(aligned by resource_id+operation; unmatched = wrong):")
    for f, v in res["field_acc"].items():
        L.append(f"    {f:12s} {v:.4f}")
    L.append(f"SEQUENCE: exact-case match {res['seq_exact']:.4f} "
             f"({res['seq_exact_n']}/{n})  mean LCS ratio {res['seq_lcs']:.4f}")
    if verbose:
        L.append("per-case failures (status wrong or not exact):")
        for cid, c in sorted(res["per_case"].items()):
            if not c["exact"]:
                L.append(f"    {cid} status_ok={c['status_ok']} f1={c['f1']:.2f} "
                         f"lcs={c['lcs']:.2f} gold_steps={c['gold_n']} pred_steps={c['pred_n']}")
    return "\n".join(L)


def perturb(gold):
    """Deterministic degraded copy of gold, used by --self-test."""
    pred = {}
    for i, (cid, (st, steps)) in enumerate(sorted(gold.items())):
        steps = [dict(s) for s in (steps or [])]
        if i % 8 == 1:
            st = "INCOMPLETE" if st == "COMPLETE" else "COMPLETE"
        elif i % 8 == 3 and steps:
            steps.pop()                          # drop last step
        elif i % 8 == 5 and len(steps) > 1:
            steps[0], steps[1] = steps[1], steps[0]  # swap order, renumber below
            for k, s in enumerate(steps, 1):
                s["step_no"] = k
        elif i % 8 == 7 and steps:
            steps[-1]["evidence_id"] = "evidence_00"  # wrong citation
        pred[cid] = (st, steps)
    return pred


def self_test(gold):
    ok = True
    perfect = score(gold, {c: (s, [dict(x) for x in (st or [])]) for c, (s, st) in gold.items()})
    checks = [("perfect status", perfect["status_acc"] == 1.0),
              ("perfect F1", perfect["step_f1"] == 1.0),
              ("perfect seq exact", perfect["seq_exact"] == 1.0),
              ("perfect lcs", perfect["seq_lcs"] == 1.0),
              ("perfect fields", all(v == 1.0 for v in perfect["field_acc"].values()))]
    empty = score(gold, {})
    checks += [("empty status 0", empty["status_acc"] == 0.0),
               ("empty recall 0", empty["step_r"] == 0.0 or empty["step_tp"] == 0)]
    deg = score(gold, perturb(gold))
    checks += [("degraded status < 1", deg["status_acc"] < 1.0),
               ("degraded F1 < 1", deg["step_f1"] < 1.0),
               ("degraded seq < perfect", deg["seq_exact"] < 1.0)]
    for name, passed in checks:
        print(f"  [{'ok' if passed else 'FAIL'}] {name}")
        ok &= passed
    print("--- scorer output on degraded copy of gold ---")
    print(report(deg))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default=str(ROOT / "output.csv"))
    ap.add_argument("--gold", default=str(ROOT / "dataset" / "sample_output.csv"))
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    gold = load(a.gold)
    if a.self_test:
        print("self-test:")
        sys.exit(0 if self_test(gold) else 1)
    if not Path(a.pred).exists():
        sys.exit(f"pred file not found: {a.pred}")
    print(report(score(gold, load(a.pred)), a.verbose))


if __name__ == "__main__":
    main()
