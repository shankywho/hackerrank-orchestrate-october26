"""Intent step: does the request authorize deletion? Three voters, majority vote, every disagreement logged.

  regex     the deterministic classifier in policy.py
  activity  a CLASSIFY_REQUEST activity: "classified request for <account> as non-deletion". The account is tied to a
            request through evidence (the account's observations carry the requester's identifiers) and the activity
            time falls between the request and the audit. It only ever votes NOT_DELETE; no activity = no vote.
  model     one short call per case on the cheapest usable chat model (cached; replayable with offline=True)
Majority of the available votes wins. A 1-1 tie (only two voters) goes to the regex verdict and is logged as a tie.
If the model is unavailable (no key, offline cache miss, bad reply) the case is decided by the other voters and logged.
"""
import csv
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm
from policy import classify_intent

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "code" / "evaluation" / "intent_votes.md"

SYSTEM = ('Classify a customer privacy message. Answer DELETE only if the person asks for their account or personal '
          'data to be deleted, erased or the account closed. Answer NOT_DELETE for anything else: asking for a copy or '
          'access, correcting or changing data, stopping marketing, help with login, or explicitly saying not to delete, '
          'even if words like delete or close appear. Reply with ONLY {"intent":"DELETE"} or {"intent":"NOT_DELETE"}.')


@dataclass
class Decision:
    case_id: str
    votes: dict                      # voter -> "DELETE" | "NOT_DELETE" (missing voter = no vote)
    authorized: bool
    disagreement: bool
    tie: bool = False
    notes: list = field(default_factory=list)


def activity_votes(cases, store, graph):
    """case_id -> "NOT_DELETE" for requests that a CLASSIFY_REQUEST activity names, matched through evidence."""
    out = {}
    with open(ROOT / "dataset" / "system_activity.csv", newline="", encoding="utf-8") as fh:
        acts = [a for a in csv.DictReader(fh) if a["event_type"] == "CLASSIFY_REQUEST"]
    for a in acts:
        owners = set()
        for o in store.by_resource.get(a["resource_id"], ()):
            for ident in o.identifiers:
                owners |= graph.resolve(ident)
        if len(owners) != 1:
            continue
        for c in cases:
            if graph.resolve_one(c["email"]) in owners and c["requested_at"] <= a["event_time"] <= c["audit_at"]:
                out[c["case_id"]] = "NOT_DELETE"
    return out


def model_vote(case, model, offline=False):
    """("DELETE"|"NOT_DELETE"|None, note). One short cached call."""
    try:
        resp = llm.chat(model, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": case["request_text"]}],
                        None, case["case_id"], offline, max_tokens=400, purpose="intent", extra={"reasoning_effort": "low"})
    except llm.OfflineMiss:
        return None, "model vote unavailable (offline, not cached)"
    except Exception as exc:
        return None, f"model vote unavailable ({type(exc).__name__}: {str(exc)[:80]})"
    text = (resp["choices"][0]["message"].get("content") or "")
    m = re.search(r'"intent"\s*:\s*"(NOT_DELETE|DELETE)"', text)
    return (m.group(1), "") if m else (None, f"model reply unusable: {text[:60]!r}")


def decide(case, votes, notes):
    vals = list(votes.values())
    c = Counter(vals)
    tie = len(vals) >= 2 and len(c) == 2 and c["DELETE"] == c["NOT_DELETE"]
    if tie:
        verdict = votes["regex"]
    else:
        verdict = c.most_common(1)[0][0]
    return Decision(case["case_id"], votes, verdict == "DELETE", len(set(vals)) > 1, tie, notes)


def run_intent_step(cases, store, graph, mode="off", workers=3):
    """mode: "off" (regex + activity only), "online" (call the model, cached), "offline" (cache only).
    Returns {case_id: Decision}."""
    act = activity_votes(cases, store, graph)
    model_votes, model_notes = {}, {}
    if mode != "off":
        offline = mode == "offline"
        try:
            model = llm.cheapest_chat_model(llm.discover_models(offline))
        except Exception as exc:
            model, model_notes["*"] = None, f"model unavailable ({type(exc).__name__})"
        if model:
            with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as ex:
                for c, (v, note) in zip(cases, ex.map(lambda c: model_vote(c, model, offline), cases)):
                    if v:
                        model_votes[c["case_id"]] = v
                    if note:
                        model_notes[c["case_id"]] = note
    out = {}
    for c in cases:
        votes = {"regex": "DELETE" if classify_intent(c["request_text"]) else "NOT_DELETE"}
        if c["case_id"] in act:
            votes["activity"] = act[c["case_id"]]
        if c["case_id"] in model_votes:
            votes["model"] = model_votes[c["case_id"]]
        notes = [model_notes[k] for k in (c["case_id"], "*") if k in model_notes]
        out[c["case_id"]] = decide(c, votes, notes)
    return out


def write_log(decisions, cases, model_name, path=LOG):
    by = {c["case_id"]: c for c in cases}
    dis = [d for d in decisions.values() if d.disagreement]
    unavailable = [d for d in decisions.values() if "model" not in d.votes]
    L = ["# Intent vote log", "",
         "Generated by `code/intent.py` (`python3 code/main.py`). Voters: regex classifier, CLASSIFY_REQUEST activity "
         f"(only votes when one names the request), model (`{model_name}`, one short call per case).", "",
         f"- cases: {len(decisions)}; model voted on {len(decisions) - len(unavailable)}; "
         f"model vote unavailable on {len(unavailable)}",
         f"- cases voted NOT_DELETE (final): {sum(not d.authorized for d in decisions.values())}",
         f"- voters present: " + str(dict(Counter(len(d.votes) for d in decisions.values()))) + " (number of voters: cases)",
         f"- disagreements (not all present votes equal): **{len(dis)}**; ties decided by the regex: "
         f"{sum(d.tie for d in dis)}", ""]
    if dis:
        L += ["| case | regex | activity | model | final | tie | request text |", "|---|---|---|---|---|---|---|"]
        for d in sorted(dis, key=lambda d: d.case_id):
            v = d.votes
            L.append(f"| {d.case_id} | {v.get('regex', '-')} | {v.get('activity', '-')} | {v.get('model', '-')} | "
                     f"{'DELETE' if d.authorized else 'NOT_DELETE'} | {'yes' if d.tie else 'no'} | "
                     f"{by[d.case_id]['request_text'][:110].replace('|', '/')} |")
    else:
        L.append("No disagreements.")
    L.append("")
    path.write_text("\n".join(L), encoding="utf-8")
    return dis
