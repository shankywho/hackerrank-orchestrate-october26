"""Co-activity signal for ambiguous account candidates (NOT wired into the linker).

Idea: activities that touch the request's already-known resources (found by exact identifiers in
evidence) should cluster in time and in activity-id distance around the request's own account
DELETE_RECORD activity. For each candidate account, measure that cluster; prefer a candidate only if it
wins clearly on BOTH measures, otherwise abstain.
"""
import csv
import datetime as dt
import statistics
from collections import defaultdict
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / "dataset"
WIN_RATIO = 0.5   # best median time-distance must be <= this fraction of the runner-up's (sweep in HANDOFF.md)


def _t(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _n(a):
    return int(a["activity_id"].split("_")[1])


def load_by_resource(dataset=DATASET):
    by = defaultdict(list)
    with open(dataset / "system_activity.csv", newline="", encoding="utf-8") as fh:
        for a in csv.DictReader(fh):
            by[a["resource_id"]].append(a)
    return by


def case_activities(by_resource, known_resources):
    """Activities touching the known (non-account) resources."""
    return [a for r in known_resources for a in by_resource.get(r, ())]


def measure(candidate_delete, acts):
    """(median minutes, median id distance) between one candidate's delete activity and the case's activities."""
    if not acts:
        return None
    dts = [abs((_t(a["event_time"]) - _t(candidate_delete["event_time"])).total_seconds()) / 60 for a in acts]
    ids = [abs(_n(a) - _n(candidate_delete)) for a in acts]
    return statistics.median(dts), statistics.median(ids)


def decide(candidates, delete_by_account, acts):
    """Return (account or "", reason). Clear win = best on both measures and time ratio <= WIN_RATIO."""
    scored = {}
    for c in candidates:
        d = delete_by_account.get(c)
        m = measure(d, acts) if d else None
        if m is None:
            return "", "a candidate has no measurable activity cluster"
        scored[c] = m
    if len(scored) < 2:
        return "", "fewer than two candidates"
    by_t = sorted(scored, key=lambda c: (scored[c][0], scored[c][1], c))
    by_i = sorted(scored, key=lambda c: (scored[c][1], scored[c][0], c))
    best, second = by_t[0], by_t[1]
    if by_i[0] != best:
        return "", "time and id-distance disagree"
    if scored[best][1] >= scored[second][1]:
        return "", "id distance not strictly better"
    if scored[best][0] > WIN_RATIO * scored[second][0]:
        return "", "time margin too small"
    return best, "clear win"
