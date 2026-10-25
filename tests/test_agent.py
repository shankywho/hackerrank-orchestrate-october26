"""Agent guards, no network. Run: python3 -I tests/test_agent.py"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
import agent
import coverage
import llm
from identity import IdentityGraph

# model choice comes from the listed models only
models = [{"id": "x/small-8b", "context_window": 131072}, {"id": "x/big-120b", "context_window": 131072},
          {"id": "whisper-large", "context_window": 131072}, {"id": "x/tiny", "context_window": 4096},
          {"id": "x/guard-200b", "context_window": 131072}]
assert [m["id"] for m in llm.pick_chat_model(models)] == ["x/big-120b", "x/small-8b"]
assert llm.pick_chat_model(models, "x/small-8b")[0]["id"] == "x/small-8b"

# verdict validation: ids outside the candidate set are rejected
v, p = agent._parse_verdict('{"intent":"DELETE","account_id":"account_X","plan_review":"ok","concerns":[],"reason":""}',
                            {"account_1": {}})
assert v is None and any("outside candidate" in x for x in p)
v, p = agent._parse_verdict('x {"intent":"DELETE","account_id":null,"plan_review":"ok","concerns":[],"reason":""} y', {})
assert v and not p
assert agent._parse_verdict("no json", {})[0] is None

# offline mode never touches the network
try:
    llm.chat("m", [{"role": "user", "content": "unlikely-prompt-" + "z" * 40}], case_id="t", offline=True)
    raise SystemExit("offline miss must raise")
except llm.OfflineMiss:
    pass

store, _ = coverage.run()
graph = IdentityGraph.load()
rd = lambda n: list(csv.DictReader(open(ROOT / "dataset" / n, newline="", encoding="utf-8")))
pool = {c["case_id"]: c for c in rd("sample_deletion_requests.csv") + rd("deletion_requests.csv")}
sh = agent.build_shared(store, graph, pool)
amb = next(c for c in rd("sample_deletion_requests.csv")
           if sh.links[c["case_id"]].method == "abstain" and sh.links[c["case_id"]].candidates)
t = agent.CaseTools(sh, amb)
assert set(t.cands) == set(sh.links[amb["case_id"]].candidates) | set(agent.al.evidence_accounts(amb, store, graph))
assert "error" in t.propose_plan("account_not_a_candidate")
assert "steps" in t.propose_plan(None) and "steps" in t.propose_plan(sorted(t.cands)[0])
late = next(f for f, ts in sh.file_times.items() if ts > amb["audit_at"])
assert "error" in t.read_evidence(late), "files after audit_at are refused"

# tool-call budget: a model that always asks for a tool is cut off at MAX_TOOL_CALLS
calls = {"n": 0}


def fake_chat(model, messages, tools=None, case_id="", offline=False, **kw):
    if tools:
        calls["n"] += 1
        return {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": f"c{calls['n']}", "type": "function", "function": {"name": "get_case", "arguments": "{}"}}]}}],
            "usage": {}}
    return {"choices": [{"message": {"content":
            '{"intent":"DELETE","account_id":null,"plan_review":"ok","concerns":[],"reason":"r"}'}}], "usage": {}}


agent.llm.chat = fake_chat
r = agent.run_case(sh, amb, "fake")
assert r["tool_calls"] == agent.MAX_TOOL_CALLS == 8 and r["verdict"]
print("ok agent guards")
