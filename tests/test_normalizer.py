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
    assert not coverage.is_skip(skip), f"{r['file_id']} unexpectedly skipped: {skip}"
    if ext == ".png":   # from code/evidence_cache/images.md: no identifiers, ownership only via link records
        assert obs and all(not o.identifiers and o.source == "png:cache" for o in obs), r["file_id"]
for o in store.obs:
    assert o.resource_id and o.system_id and o.category and o.state, o
    assert o.captured_at and o.observed_at, o
assert all(i == i.lower() for o in store.obs for i in o.identifiers), "identifiers lowercased"
# per-row cutoff: a row dated after audit_at is invisible even when its file was captured before
from observations import visible
late = [o for o in store.obs if o.observed_at > o.captured_at]
assert late and all(not visible(o, o.captured_at) for o in late), "late rows must fail the row cutoff"
assert all(c.row_count >= 0 for c in store.coverage) and any(c.row_count == 0 for c in store.coverage)
assert any(o.source.startswith("pdf:") for o in store.obs), "PDF parser produced observations"
assert len(store.links) == 12 and not any(o.attr("alias") and o.resource_id.startswith(("NST-", "delivery_")) and
                                          any(l.resource_id == o.resource_id for l in store.links) for o in store.obs)
src = Path(__file__).resolve().parent.parent / "code"
import re
for f in ("normalizer.py", "logparse.py", "observations.py", "pdfparse.py"):
    assert not re.search(r"\b(case_\d+|user_\d+|account_\d+|search_\d+)\b", (src / f).read_text()), f"hardcoded id in {f}"
print("ok", len(store), "observations,", len(rows), "files")
