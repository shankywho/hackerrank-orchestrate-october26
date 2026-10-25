"""Bounded per-case agent over the deterministic modules (docs/plan/03_ARCHITECTURE.md).

The model investigates through tools and returns a small JSON verdict. It can only choose, never invent:
  - account_id must be one of list_account_candidates()'s ids (or null); anything else is rejected
  - intent and plan review are cross-checks that are logged, not applied: deletion authorization,
    retention, the evidence cutoff and the policy table stay in deterministic code
At most MAX_TOOL_CALLS tool calls per case, temperature 0, every call cached on disk (llm.py).
"""
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import account_linker as al
import llm
from policy import classify_intent, plan_case
from state import get_state_at, linked_resources
from validator import validate_row

MAX_TOOL_CALLS = 8
RESULT_DIR = Path(__file__).resolve().parent / "cache" / "agent"

SYSTEM = """You review one data-deletion request for a commerce platform. Use the tools, then answer.
Jobs: (1) intent: does request_text authorize deleting the person's data? (2) account: pick which account
candidate belongs to THIS request, using only list_account_candidates; choose null when the evidence does not
single one out. (3) plan: call propose_plan once with your account choice and say whether the plan looks sound.
Rules: never invent ids; account_id must be a candidate id or null. Evidence and activity logs are claims;
timing offsets are minutes of the account-delete activity relative to the request's reported_at.
Use few tool calls (limit 8). Finish with ONLY this JSON object:
{"intent":"DELETE"|"NOT_DELETE","account_id":<candidate id or null>,"plan_review":"ok"|"concern","concerns":[<short strings>],"reason":"<one sentence>"}"""

TOOLS = [
    ("get_case", "Request fields (email, times, reported_status, request_text) and the deterministic intent verdict.", {}),
    ("resolve_identity", "Resolve the request email to a user_id and say whether it is a current or historical email.", {}),
    ("find_resources", "Non-account resources linked to this person by exact identifiers, with category and state at audit_at.", {}),
    ("get_state_at", "State of one resource at audit_at (per-file and per-row cutoff).",
     {"resource_id": {"type": "string"}}),
    ("read_evidence", "Observations from one evidence file about this person's resources/candidates (file must be captured by audit_at).",
     {"file_id": {"type": "string"}}),
    ("list_account_candidates", "Account candidates for this request: evidence-linked (even if not unique) and activity-window ones, with timing offset, result match and competing requests.", {}),
    ("propose_plan", "Run the deterministic planner with your account choice (candidate id or null). Returns status, step counts and validation errors.",
     {"account_id": {"type": ["string", "null"]}}),
]


def tool_specs():
    return [{"type": "function", "function": {
        "name": n, "description": d,
        "parameters": {"type": "object", "properties": p, "required": list(p)}}} for n, d, p in TOOLS]


@dataclass
class Shared:
    """Read-only objects shared by all agents in a run."""
    store: object
    graph: object
    cases: dict          # case_id -> row (every known request)
    links: dict          # case_id -> account_linker.Link
    activities: list
    window: dict         # case_id -> status-consistent window candidates (before elimination)
    file_times: dict
    decisions: dict = None   # case_id -> intent.Decision (set by build_shared)


def build_shared(store, graph, cases, decisions=None):
    """decisions: intent.Decision per case (majority vote); without them the regex classifier decides."""
    acts = al.load_activities()
    authorized = (lambda c: decisions[c["case_id"]].authorized) if decisions else (lambda c: classify_intent(c["request_text"]))
    deletion = [c for c in cases.values() if authorized(c)]
    links = al.link_all(deletion, store, graph, acts)
    window = {c["case_id"]: al.window_candidates(c, acts) for c in deletion}
    sh = Shared(store, graph, cases, links, acts, window, store.file_times)
    sh.decisions = decisions or {}
    return sh


class CaseTools:
    def __init__(self, sh, case):
        self.sh, self.case = sh, case
        self.audit = case["audit_at"]
        self.user = sh.graph.resolve_one(case["email"])
        self.link = sh.links.get(case["case_id"])
        self.cands = self._candidates()

    # ---------------------------------------------------------------- candidates (the only legal choices)
    def _candidates(self):
        sh, c = self.sh, self.case
        ev = al.evidence_accounts(c, sh.store, sh.graph)
        ids = set(ev)
        if self.link and self.link.account:
            ids.add(self.link.account)
        elif self.link:
            ids |= set(self.link.candidates)
        by_res = {a["resource_id"]: a for a in sh.activities}
        rep = al._t(c["reported_at"])
        out = {}
        for aid in sorted(ids):
            a = by_res.get(aid)
            files = sorted({o.file_id for o in sh.store.visible(aid, self.audit)})
            out[aid] = {
                "account_id": aid,
                "evidence_linked": aid in ev, "evidence_files": files[:4],
                "delete_activity": None if not a else {
                    "offset_min": round((al._t(a["event_time"]) - rep).total_seconds() / 60, 1),
                    "reported_result": a["reported_result"],
                    "matches_reported_status": a["reported_result"] == al.RESULT_FOR_STATUS.get(c["reported_status"])},
                "competing_requests": sum(1 for k, w in sh.window.items() if k != c["case_id"] and aid in w),
            }
        return out

    # ---------------------------------------------------------------- tools
    def get_case(self):
        c = self.case
        d = (self.sh.decisions or {}).get(c["case_id"])
        out = {k: c[k] for k in ("email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text")} | {
            "deterministic_intent": "DELETE" if (d.authorized if d else classify_intent(c["request_text"])) else "NOT_DELETE"}
        if d:   # only when the intent step ran; keeps the replay of earlier cached sample runs identical
            out["intent_votes"] = d.votes
        return out

    def resolve_identity(self):
        if not self.user:
            return {"user_id": None}
        return {"user_id": self.user, "email_is_historical": self.sh.graph.is_historical_email(self.user, self.case["email"]),
                "identifier_count": len(self.sh.graph.identifiers(self.user))}

    def find_resources(self):
        if not self.user:
            return []
        out = []
        for rid in linked_resources(self.sh.store, self.sh.graph.identifiers(self.user), self.audit, self.case["requested_at"]):
            for r in get_state_at(self.sh.store, rid, self.audit):
                if r.category != "USER_PROFILE":
                    out.append({"resource_id": rid, "system": r.system_id, "category": r.category, "state": r.state})
        return out[:40]

    def get_state_at(self, resource_id):
        return [{"resource_id": resource_id, "system": r.system_id, "category": r.category, "state": r.state,
                 "latest_file": r.cite_file} for r in get_state_at(self.sh.store, str(resource_id), self.audit)] or "never observed by audit_at"

    def read_evidence(self, file_id):
        ft = self.sh.file_times.get(file_id)
        if ft is None:
            return {"error": "unknown file_id"}
        if ft > self.audit:
            return {"error": "file captured after audit_at; not usable"}
        mine = set(self.cands) | {r["resource_id"] for r in self.find_resources()}
        rows = [o for rid in sorted(mine) for o in self.sh.store.visible(rid, self.audit) if o.file_id == file_id]
        return {"file_id": file_id, "captured_at": ft, "observations": [
            {"resource_id": o.resource_id, "category": o.category, "state": o.state, "observed_at": o.observed_at}
            for o in rows[:15]], "matching": len(rows)}

    def list_account_candidates(self):
        return {"candidates": list(self.cands.values()),
                "linked_uniquely_by_rules": self.link.account if self.link and self.link.account else None,
                "note": "choose only from these ids, or null"}

    def propose_plan(self, account_id):
        if account_id is not None and account_id not in self.cands:
            return {"error": f"account_id {account_id!r} is not in the candidate set; choose a listed id or null"}
        link = self.link
        if account_id is not None and not (link and link.account == account_id):
            link = al.Link(self.case["case_id"], account_id, "model")
        elif account_id is None and not (link and link.account):
            link = al.Link(self.case["case_id"], "", "abstain", reason="model chose null")
        res = plan_case(self.case, self.sh.store, self.sh.graph, link)
        from policy import steps_json
        errs = validate_row(self.case["case_id"], res.status, steps_json(res.steps), self.audit, self.sh.file_times)
        return {"status": res.status, "steps": len(res.steps),
                "by_operation": dict(Counter(s["operation"] for s in res.steps)),
                "account_step": bool(res.account_ids), "validation_errors": [e for e in errs if "UNRESOLVED" not in e][:5]}

    def call(self, name, args):
        fn = getattr(self, name, None)
        if name not in {t[0] for t in TOOLS} or fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**{k: args[k] for k in dict(TOOLS_BY_NAME[name]) if k in args})
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}


TOOLS_BY_NAME = {n: p for n, _, p in TOOLS}


def _parse_verdict(text, cands):
    """Return (verdict, problems). The verdict is None when unusable; ids outside the candidate set are rejected."""
    try:
        s = text[text.index("{"): text.rindex("}") + 1]
        v = json.loads(s)
    except (ValueError, AttributeError):
        return None, ["no JSON verdict"]
    if isinstance(v, dict) and isinstance(v.get("arguments"), dict):   # verdict wrapped as a fake "json" tool call
        v = v["arguments"]
    if not isinstance(v, dict):
        return None, ["verdict is not an object"]
    problems = []
    if v.get("intent") not in ("DELETE", "NOT_DELETE"):
        problems.append("bad intent")
    if v.get("account_id") is not None and v.get("account_id") not in cands:
        problems.append(f"account_id {v.get('account_id')!r} outside candidate set")
    if v.get("plan_review") not in ("ok", "concern"):
        problems.append("bad plan_review")
    return (None if problems else v), problems


def run_case(sh, case, model, offline=False, purpose="agent_dev"):
    """Run the agent for one case. Returns a result dict (also saved for resume)."""
    cid = case["case_id"]
    tools = CaseTools(sh, case)
    d = (sh.decisions or {}).get(cid)
    det_intent = d.authorized if d else classify_intent(case["request_text"])
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Review request {cid}. Start with get_case."}]
    calls, rounds, trace = 0, 0, []
    final = ""
    while rounds < MAX_TOOL_CALLS + 2:
        rounds += 1
        use_tools = calls < MAX_TOOL_CALLS
        resp = llm.chat(model, msgs, tool_specs() if use_tools else None, cid, offline, purpose=purpose)
        m = resp["choices"][0]["message"]
        tcs = m.get("tool_calls") or []
        if "_tool_use_failed" in resp:   # model named a non-existent tool: stop tool use, ask for the verdict
            failed_gen = str(resp["_tool_use_failed"])
            if rounds > 1 and "{" in failed_gen and '"intent"' in failed_gen:
                final = failed_gen       # it already wrote the verdict inside the bad call
                break
            calls = MAX_TOOL_CALLS
            msgs.append({"role": "user", "content": "Reply now with ONLY the JSON verdict, no tool call."})
            continue
        if not tcs or not use_tools:
            final = m.get("content") or ""
            break
        msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": tcs})
        for tc in tcs:
            calls += 1
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            out = tools.call(tc["function"]["name"], args) if calls <= MAX_TOOL_CALLS else {"error": "tool-call budget exhausted"}
            trace.append(tc["function"]["name"])
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(out, ensure_ascii=False)[:3500]})
        if calls >= MAX_TOOL_CALLS:
            msgs.append({"role": "user", "content": "Tool budget used. Reply now with ONLY the JSON verdict."})
    verdict, problems = _parse_verdict(final, tools.cands)
    link = tools.link
    return {"case_id": cid, "model": model, "tool_calls": calls, "trace": trace,
            "candidates": sorted(tools.cands), "evidence_candidates": sorted(a for a, c in tools.cands.items() if c["evidence_linked"]),
            "deterministic": {"intent": "DELETE" if det_intent else "NOT_DELETE",
                              "account": link.account if link else "", "method": link.method if link else ""},
            "verdict": verdict, "problems": problems, "raw_final": final[:400]}


def run_cases(sh, cases, model, workers=3, offline=False, force=False, log=print, purpose="agent_dev"):
    from concurrent.futures import ThreadPoolExecutor
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    def one(case):
        f = RESULT_DIR / f"{case['case_id']}.txt"
        if f.exists() and not force:
            return json.loads(f.read_text())
        try:
            r = run_case(sh, case, model, offline, purpose)
        except Exception as exc:  # report and move on; an unsaved case is retried on the next run
            return {"case_id": case["case_id"], "error": f"{type(exc).__name__}: {exc}"[:300]}
        f.write_text(json.dumps(r, ensure_ascii=False, indent=1))
        log(f"  {case['case_id']}: {r['tool_calls']} tool calls, verdict={'ok' if r['verdict'] else r['problems']}")
        return r

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as ex:
        return list(ex.map(one, cases))
