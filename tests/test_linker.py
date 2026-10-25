"""Account linker invariants. Run: python3 -I tests/test_linker.py"""
import csv
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
import account_linker as al
import coverage
from identity import IdentityGraph
from policy import classify_intent

store, _ = coverage.run()
graph = IdentityGraph.load()
rd = lambda n: list(csv.DictReader(open(ROOT / "dataset" / n, newline="", encoding="utf-8")))
cases = [c for c in rd("sample_deletion_requests.csv") + rd("deletion_requests.csv") if classify_intent(c["request_text"])]
acts = al.load_activities()
links = al.link_all(cases, store, graph, acts)
assert len(links) == len(cases)
assigned = [l.account for l in links.values() if l.account]
assert len(assigned) == len(set(assigned)), "an account was assigned to two requests"
for l in links.values():
    assert (l.method == "abstain") == (not l.account)
    if l.method == "singleton":
        c = next(c for c in cases if c["case_id"] == l.case_id)
        assert l.account in al.window_candidates(c, acts), "singleton outside its window/status"
# window math
c = {"reported_at": "2026-01-01T12:00:00Z", "reported_status": "COMPLETE"}
mk = lambda t, r: {"event_time": t, "reported_result": r, "resource_id": "x_" + t}
got = al.window_candidates(c, [mk("2026-01-01T10:40:00Z", "SUCCESS"), mk("2026-01-01T10:39:59Z", "SUCCESS"),
                               mk("2026-01-01T12:10:00Z", "SUCCESS"), mk("2026-01-01T12:10:01Z", "SUCCESS"),
                               mk("2026-01-01T11:00:00Z", "FAILED")])
assert got == {"x_2026-01-01T10:40:00Z", "x_2026-01-01T12:10:00Z"}, got
assert not re.search(r"\b(case_\d+|user_\d+|account_\d+)\b", (ROOT / "code" / "account_linker.py").read_text())
print("ok", Counter(l.method for l in links.values()))
