"""Participant-made evaluation cases, run against the real pipeline and the real evidence.

Each case states what must hold, runs the real code, and records what it saw. Cases that need a decoy, a
recreation or a replica row find one in the data instead of naming it, except the two recreation cases the task
names (they are fixtures here, not solver code). Used by make_reports.py; also runnable on its own:
  python3 -I code/evaluation/custom_cases.py
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "code"))
import account_linker as al
import main
from identity import IdentityGraph
from observations import visible
from policy import classify_intent, plan_case
from state import get_state_at, linked_resources


def _rows(name):
    with open(ROOT / "dataset" / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _custom_request(email, audit_at, text="Please permanently delete my account and all personal data."):
    """A request that is not in the dataset: it uses a real identity but no case id from the data."""
    return {"case_id": "custom", "email": email, "requested_at": "2000-01-01T00:00:00Z", "reported_status": "NOT_STARTED",
            "reported_at": "2000-01-02T00:00:00Z", "audit_at": audit_at, "request_text": text}


def run_all(results=None, store=None):
    if results is None:
        results, store = main.run(str(ROOT / "dataset" / "deletion_requests.csv"))
    graph = IdentityGraph.load()
    by = {r.case_id: r for r in results}
    cases = {c["case_id"]: c for c in _rows("deletion_requests.csv")}
    out = []

    # 1. look-alike decoy: an email in evidence that is one edit away from a real user's email but owns nothing
    owned = {}
    for uid in graph.users:
        for e in graph.ids[uid]["email"]:
            owned[e] = uid
    decoys = sorted((e, o) for e in store.by_identifier if "@" in e and e not in owned
                    for o in owned if e != o and e.split("@")[1] == o.split("@")[1]
                    and e.split("@")[0].startswith(o.split("@")[0] + "."))
    if decoys:
        decoy, real = decoys[0]
        uid = owned[real]
        decoy_res = {o.resource_id for o in store.by_identifier[decoy]}
        res = plan_case(_custom_request(graph.users[uid]["email"], "2030-01-01T00:00:00Z"), store, graph, None)
        touched = sorted(decoy_res & {s["resource_id"] for s in res.steps})
        out.append({
            "name": "Look-alike decoy email (exact matching only)",
            "setup": f"evidence holds `{decoy}`, which is `{real}` plus a suffix and belongs to no user; "
                     f"it appears on resources {sorted(decoy_res)}. A request is made for `{real}`.",
            "expect": "the decoy's resources never appear in the plan; the decoy resolves to no user",
            "observed": f"decoy resolves to {sorted(graph.resolve(decoy)) or 'nobody'}; decoy resources in plan: "
                        f"{touched or 'none'}; plan has {len(res.steps)} steps for the real user",
            "passed": not graph.resolve(decoy) and not touched})

    # 2. recreation after the audit: a connector/restore repopulated a record after audit_at (the two cases named by the task)
    acts = _rows("system_activity.csv")
    for cid, kind, keyword in (("case_39", "connector sync", "SYNC_RECORD"), ("case_55", "restore job", "RESTORE_BACKUP")):
        c, r = cases[cid], by[cid]
        recreated = [a for a in acts if a["event_type"] == keyword and "repopulated downstream resource" in a["details"]
                     and a["event_time"] > c["audit_at"] and a["resource_id"] in {s["resource_id"] for s in r.steps}]
        repop = sorted({a["details"].rsplit(" ", 1)[1].rstrip(".") for a in recreated})
        used = [x for x in repop if any(s["resource_id"] == x for s in r.steps)]
        out.append({
            "name": f"Recreation after audit_at ({kind}) - {cid}",
            "setup": f"audit_at {c['audit_at']}; the {kind} activity on {sorted({a['resource_id'] for a in recreated})} "
                     f"is dated {sorted({a['event_time'] for a in recreated})}, after the audit; it repopulated {repop}.",
            "expect": "the repopulated downstream record gets no step; the source process or archive is still handled",
            "observed": "steps: " + "; ".join(f"{s['operation']} {s['resource_id']}" for s in r.steps)
                        + f"; steps on repopulated resources: {used or 'none'}",
            "passed": bool(recreated) and not used})

    # 3. post-audit evidence: ledger rows dated after their file's capture time and after the case's audit_at
    late = [o for o in store.obs if o.observed_at > o.captured_at]
    for o in sorted(late, key=lambda o: o.observed_at):
        subj = graph.resolve_one(o.subject_key) if o.subject_key else None
        hit = [c for c in cases.values() if subj and graph.resolve_one(c["email"]) == subj
               and o.captured_at <= c["audit_at"] < o.observed_at]
        for c in hit:
            before = [(x.state, x.cite_file) for x in get_state_at(store, o.resource_id, c["audit_at"])]
            after = [(x.state, x.cite_file) for x in get_state_at(store, o.resource_id, o.observed_at)]
            out.append({
                "name": f"Post-audit ledger row inside an earlier file ({c['case_id']})",
                "setup": f"{o.file_id} was captured {o.captured_at} but holds a row for {o.resource_id} dated "
                         f"{o.observed_at} saying {o.state}; the case's audit_at is {c['audit_at']}.",
                "expect": f"at audit_at the resource is not {o.state} (the later row is invisible); once its own time "
                          f"has passed the state flips",
                "observed": f"state at audit_at: {before}; state at {o.observed_at}: {after}",
                "passed": all(s != o.state for s, _ in before) and any(s == o.state for s, _ in after)})

    # 4. replica drift: a record present only in a replica table still counts as present
    drift = []
    for rid in sorted(store.by_resource):
        for st in get_state_at(store, rid, "2030-01-01T00:00:00Z"):
            if st.replica_only and st.state == "FOUND":
                drift.append(st)
    if drift:
        st = drift[0]
        sources = sorted({o.source for o in st.observations})
        owner = next((graph.resolve_one(i) for o in st.observations for i in o.identifiers if graph.resolve_one(i)), None)
        res = None
        if owner:
            # account rows are planned only through an account link, so give the case one for this account
            link = al.Link("custom", st.resource_id, "custom") if st.category == "USER_PROFILE" else None
            res = plan_case(_custom_request(graph.users[owner]["email"], st.cite_captured_at), store, graph, link)
        out.append({
            "name": "Replica-only row (replication drift)",
            "setup": f"{st.resource_id} ({st.category}) exists only in {sources} in {st.cite_file}; "
                     f"{len(drift)} such resource states exist in the data.",
            "expect": "state FOUND (a replica that still holds the record means it is still present)"
                      + ("; the plan reports it as a remaining record" if res else ""),
            "observed": f"state {st.state}, replica_only={st.replica_only}"
                        + (f"; plan findings: {[s['resource_id'] + ' ' + s['operation'] for s in res.steps if s['phase'] == 'FINDING' and s['resource_id'] == st.resource_id]}" if res else ""),
            "passed": st.state == "FOUND" and (res is None or any(
                s["resource_id"] == st.resource_id and s["phase"] == "FINDING" for s in res.steps))})

    # 5. intent: non-deletion requests and a non-English deletion request
    texts = [("Please send me a copy of everything you hold about me. Do not delete anything.", False),
             ("I would like to correct my surname; keep my account active.", False),
             ("Solicito la eliminación permanente de mi cuenta y de todos mis datos personales.", True),
             ("アカウントを完全に削除してください。", True)]
    got = [(t, classify_intent(t), want) for t, want in texts]
    out.append({"name": "Intent: non-deletion and non-English requests",
                "setup": "four hand-written texts (two non-deletion, a Spanish and a Japanese deletion)",
                "expect": "non-deletion -> NOT_APPLICABLE, deletion in any language -> authorized",
                "observed": "; ".join(f"{'DELETE' if g else 'NOT_DELETE'} (want {'DELETE' if w else 'NOT_DELETE'})" for _, g, w in got),
                "passed": all(g == w for _, g, w in got)})

    # 6. link ownership: a screenshot proves state, the link record decides the owner
    mk = next((l for l in store.links if l.resource_id.startswith("marketing_")), None)
    if mk:
        shown = next(o.attr("visible_email") for o in store.by_resource[mk.resource_id] if o.attr("visible_email"))
        owner_cases = [c for c in cases.values() if graph.resolve_one(c["email"]) == mk.user_id
                       and c["requested_at"] <= mk.event_time <= c["audit_at"]]
        shown_user = graph.resolve_one(shown)
        out.append({"name": "Screenshot identity conflicts with the link record (ownership by link only)",
                    "setup": f"{mk.resource_id}'s screenshot shows `{shown}` (resolves to {shown_user or 'no user'}); "
                             f"link {mk.activity_id} assigns it to {mk.email} / {mk.user_id}.",
                    "expect": "the resource is planned for the link's owner, never for the on-screen identity",
                    "observed": "plans: " + "; ".join(f"{c['case_id']} -> {[s['operation'] for s in by[c['case_id']].steps if s['resource_id'] == mk.resource_id]}" for c in owner_cases),
                    "passed": bool(owner_cases) and shown_user is None and all(
                        any(s["resource_id"] == mk.resource_id for s in by[c["case_id"]].steps) for c in owner_cases)})
    return out


if __name__ == "__main__":
    for c in run_all():
        print(("PASS " if c["passed"] else "FAIL ") + c["name"])
        for k in ("setup", "expect", "observed"):
            print(f"   {k}: {c[k]}")
