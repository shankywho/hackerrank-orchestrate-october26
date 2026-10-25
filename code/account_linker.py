"""Account linker: which account resource belongs to which deletion request.

Account ids rarely appear in evidence, but each request leaves a DELETE_RECORD activity on
account_service near its reported_at. Stages, in order of trust:
  1 evidence   exactly one account co-occurs with the user's identifiers in pre-audit evidence
  2 activity   account_service DELETE_RECORD within [reported_at-80min, reported_at+10min] whose
               reported_result matches reported_status (SUCCESS/COMPLETE, PARTIAL/PARTIALLY_COMPLETE,
               FAILED/NOT_STARTED)
  3 singleton  a request with one candidate takes it; the account leaves every other request's pool;
               repeat to a fixpoint
Everything else abstains. tiebreak() is a separate, flagged last resort and is never applied here.
"""
import csv
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from state import get_state_at, linked_resources

DATASET = Path(__file__).resolve().parent.parent / "dataset"
RESULT_FOR_STATUS = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}
WINDOW_BEFORE = dt.timedelta(minutes=80)
WINDOW_AFTER = dt.timedelta(minutes=10)


def _t(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class Link:
    case_id: str
    account: str = ""        # empty = abstain
    method: str = "abstain"  # evidence | singleton | abstain
    candidates: tuple = ()   # remaining candidates when abstaining
    reason: str = ""


def load_activities(dataset=DATASET):
    with open(dataset / "system_activity.csv", newline="", encoding="utf-8") as fh:
        return [a for a in csv.DictReader(fh)
                if a["event_type"] == "DELETE_RECORD" and a["target_system"] == "account_service"]


def window_candidates(case, activities):
    """Accounts whose DELETE_RECORD fits this request's time window and reported status."""
    rep = _t(case["reported_at"])
    want = RESULT_FOR_STATUS.get(case["reported_status"])
    return {a["resource_id"] for a in activities
            if rep - WINDOW_BEFORE <= _t(a["event_time"]) <= rep + WINDOW_AFTER and a["reported_result"] == want}


def evidence_accounts(case, store, graph):
    """Accounts that evidence ties to this request's subject (exact identifiers, since requested_at)."""
    user = graph.resolve_one(case["email"])
    if not user:
        return set()
    out = set()
    for rid in linked_resources(store, graph.identifiers(user), case["audit_at"], case["requested_at"]):
        if any(r.category == "USER_PROFILE" for r in get_state_at(store, rid, case["audit_at"])):
            out.add(rid)
    return out


def link_all(cases, store, graph, activities):
    """cases: every request (samples and evaluation together). Returns {case_id: Link}."""
    links = {c["case_id"]: Link(c["case_id"]) for c in cases}
    pool = {}
    taken = {}
    for c in cases:
        ev = evidence_accounts(c, store, graph)
        if len(ev) == 1:
            acct = next(iter(ev))
            links[c["case_id"]] = Link(c["case_id"], acct, "evidence")
            taken[acct] = c["case_id"]
    for c in cases:
        if not links[c["case_id"]].account:
            pool[c["case_id"]] = window_candidates(c, activities) - set(taken)
    changed = True
    while changed:
        changed = False
        for cid, cand in sorted(pool.items()):
            if links[cid].account or len(cand) != 1:
                continue
            acct = next(iter(cand))
            if acct in taken:
                continue
            links[cid] = Link(cid, acct, "singleton")
            taken[acct] = cid
            changed = True
            for other in pool.values():
                other.discard(acct)
    for cid, cand in pool.items():
        if not links[cid].account:
            links[cid] = Link(cid, "", "abstain", tuple(sorted(cand)),
                              "no candidate in window" if not cand else "ambiguous")
    return links


def tiebreak(cases, links):
    """LAST RESORT, not wired in: inside a group of ambiguous requests sharing the same candidate set,
    pair them in case-number order with candidates in event order. Overfit-prone; reported separately."""
    groups = defaultdict(list)
    for c in cases:
        lk = links[c["case_id"]]
        if lk.method == "abstain" and len(lk.candidates) > 1:
            groups[lk.candidates].append(c)
    out = {}
    for cand, cs in groups.items():
        if len(cs) == len(cand):
            for c, a in zip(sorted(cs, key=lambda c: int(c["case_id"].split("_")[1])), cand):
                out[c["case_id"]] = a
    return out
