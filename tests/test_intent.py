"""Intent vote: voters, majority, ties, fallbacks. No network. Run: python3 -I tests/test_intent.py"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
import coverage
import intent
import llm
from identity import IdentityGraph

# majority and ties
case = {"case_id": "c", "request_text": "x"}
d = intent.decide(case, {"regex": "DELETE", "activity": "NOT_DELETE", "model": "NOT_DELETE"}, [])
assert not d.authorized and d.disagreement and not d.tie
d = intent.decide(case, {"regex": "DELETE", "model": "NOT_DELETE"}, [])
assert d.authorized and d.tie and d.disagreement, "1-1 tie goes to the regex verdict and is logged"
d = intent.decide(case, {"regex": "NOT_DELETE", "model": "DELETE"}, [])
assert not d.authorized and d.tie
d = intent.decide(case, {"regex": "DELETE", "model": "DELETE"}, [])
assert d.authorized and not d.disagreement
d = intent.decide(case, {"regex": "DELETE"}, [])
assert d.authorized and not d.disagreement and not d.tie

store, _ = coverage.run()
graph = IdentityGraph.load()
cases = list(csv.DictReader(open(ROOT / "dataset" / "deletion_requests.csv", newline="", encoding="utf-8")))
act = intent.activity_votes(cases, store, graph)
assert len(act) == 10 and set(act.values()) == {"NOT_DELETE"}, "one CLASSIFY_REQUEST activity per non-deletion request"
# the activity must match the request through evidence, inside the request-to-audit window; nobody else gets a vote
for cid in act:
    c = next(c for c in cases if c["case_id"] == cid)
    assert c["requested_at"] <= c["audit_at"]

# offline with an empty cache for these prompts: the model vote is unavailable, the other voters still decide
orig = llm.CACHE
llm.CACHE = Path("/nonexistent-cache")
try:
    sub = cases[:6]
    dec = intent.run_intent_step(sub, store, graph, mode="offline")
finally:
    llm.CACHE = orig
assert all("model" not in x.votes and x.notes for x in dec.values()), "unavailable model vote is noted, not guessed"
off = intent.run_intent_step(cases, store, graph, mode="off")
assert sum(not x.authorized for x in off.values()) == 10 and all(set(x.votes) <= {"regex", "activity"} for x in off.values())
# the cheaper model is chosen from the listed models by known list price, not by name
models = [{"id": "a/big-120b", "context_window": 131072}, {"id": "a/small-20b", "context_window": 131072}]
saved = dict(llm.LIST_PRICES)
llm.LIST_PRICES.update({"a/big-120b": (1.0, 1.0), "a/small-20b": (0.1, 0.1)})
assert llm.cheapest_chat_model(models) == "a/small-20b"
llm.LIST_PRICES.clear(); llm.LIST_PRICES.update(saved)
assert llm.cheapest_chat_model([{"id": "unpriced-1b", "context_window": 131072}]) is None
print("ok intent vote")
