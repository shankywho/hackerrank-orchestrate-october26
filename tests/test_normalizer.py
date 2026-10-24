"""Invariants for the normalizer. Run: python3 -I tests/test_normalizer.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))
import coverage
import normalizer

store, rows = coverage.run()
by_id = {r["file_id"]: (obs, skip) for r, obs, skip in rows}

assert len(rows) == 94 and len(by_id) == 94, "every manifest file reported once"
for r, obs, skip in rows:
    ext = Path(r["file_path"]).suffix
    if ext in (".json", ".log", ".sql", ".txt"):
        assert skip is None or skip == normalizer.EMPTY, f"{r['file_id']} unexpectedly skipped: {skip}"
    else:
        assert skip and not obs, f"{r['file_id']} {ext} must be skipped with a reason"
for o in store.obs:
    assert o.resource_id and o.system_id and o.category and o.state, o
    assert o.captured_at and o.observed_at, o
assert all(i == i.lower() for o in store.obs for i in o.identifiers), "identifiers lowercased"
src = Path(__file__).resolve().parent.parent / "code"
import re
for f in ("normalizer.py", "logparse.py", "observations.py"):
    assert not re.search(r"\b(case_\d+|user_\d+|account_\d+|search_\d+)\b", (src / f).read_text()), f"hardcoded id in {f}"
print("ok", len(store), "observations,", len(rows), "files")
