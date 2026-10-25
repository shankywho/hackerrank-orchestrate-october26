"""get_state_at invariants. Run: python3 -I tests/test_state.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))
import coverage
from observations import visible
from state import get_state_at, linked_resources

store, _ = coverage.run()
late = [o for o in store.obs if o.observed_at > o.captured_at]
assert late
for o in late:  # the future-dated row never decides state before its own time
    before = get_state_at(store, o.resource_id, o.captured_at)
    assert before and all(o not in r.observations for r in before)
    after = get_state_at(store, o.resource_id, o.observed_at)
    assert any(o in r.observations for r in after)
# nothing visible after the cutoff, ever
for rid in list(store.by_resource)[:400]:
    for r in get_state_at(store, rid, "2026-05-01T00:00:00Z"):
        assert all(visible(o, "2026-05-01T00:00:00Z") for o in r.observations)
        assert r.cite_captured_at <= "2026-05-01T00:00:00Z"
assert get_state_at(store, "no_such_resource", "2030-01-01T00:00:00Z") == []
# replica precedence: when sources disagree at the same time the violating state wins
import itertools
conflict = 0
for rid in store.by_resource:
    for r in get_state_at(store, rid, "2030-01-01T00:00:00Z"):
        top = max(o.observed_at for o in store.by_resource[rid] if o.category == r.category)
        states = {o.state for o in store.by_resource[rid] if o.category == r.category and o.observed_at == top}
        if len(states) > 1:
            conflict += 1
            assert r.state == min(states, key=lambda s: ["FOUND", "IDENTIFIABLE", "ACTIVE", "NOT_FOUND", "REDACTED",
                                                         "ANONYMIZED", "DISABLED", "BLOCKED"].index(s))
print("ok; same-time conflicts resolved:", conflict)
