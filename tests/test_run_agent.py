"""Routing and resumability, no network. Run: python3 -I tests/test_run_agent.py"""
import csv
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
import agent
import coactivity
import coverage
import main
import run_agent
from identity import IdentityGraph

results, store = main.run(str(ROOT / "dataset" / "deletion_requests.csv"))      # intent_mode "off": no model call
graph = IdentityGraph.load()
sh = agent.build_shared(store, graph, {c["case_id"]: c for c in main.run.pool}, main.run.decisions)
evals = main.read_cases(ROOT / "dataset" / "deletion_requests.csv")
plan = run_agent.priority_order(sh, evals)
groups = Counter(g for _, g, _ in plan)
assert groups["not_applicable"] == 10 and groups["image_link"] >= 11 and set(groups) == {"not_applicable", "image_link", "ambiguous"}, groups
order = [g for _, g, _ in plan]
assert order == sorted(order, key=["not_applicable", "image_link", "ambiguous"].index), "priority: NA, then image links, then ambiguous"
amb = [n for _, g, n in plan if g == "ambiguous"]
assert amb == sorted(amb) and min(amb) >= 2, "ambiguous cases: fewest candidates first"
assert not any(sh.links[c["case_id"]].account for c, g, _ in plan if g == "ambiguous"), "uniquely linked cases are never routed as ambiguous"
# resumable: second run does no new model calls
calls = {"n": 0}
VERDICT = '{"intent":"DELETE","account_id":null,"plan_review":"ok","concerns":[],"reason":"r"}'


def fake_chat(model, messages, tools=None, case_id="", offline=False, **kw):
    calls["n"] += 1
    return {"choices": [{"message": {"content": VERDICT}}], "usage": {}}


agent.llm.chat = fake_chat
agent.RESULT_DIR = Path(tempfile.mkdtemp())
cases = [c for c, _, _ in plan][:3]
agent.run_cases(sh, cases, "fake", 2, False, False, log=lambda m: None)
first = calls["n"]
agent.run_cases(sh, cases, "fake", 2, False, False, log=lambda m: None)
assert first == 3 and calls["n"] == first, "second run must reuse saved results"

# co-activity signal: needs a clear win on both measures
d = {"a": {"event_time": "2026-01-01T10:00:00Z", "activity_id": "activity_100"},
     "b": {"event_time": "2026-01-01T11:00:00Z", "activity_id": "activity_160"}}
acts = [{"event_time": "2026-01-01T10:05:00Z", "activity_id": "activity_103"}]
assert coactivity.decide(["a", "b"], d, acts)[0] == "a"
assert coactivity.decide(["a", "b"], {**d, "b": {"event_time": "2026-01-01T10:01:00Z", "activity_id": "activity_101"}},
                         acts)[0] == "", "close call must abstain"
print("ok priority routing + resume + coactivity", dict(groups))
