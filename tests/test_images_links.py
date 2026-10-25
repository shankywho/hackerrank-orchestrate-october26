"""Image cache + link bridge. Run: python3 -I tests/test_images_links.py"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
import coverage
import normalizer
import policy
from identity import IdentityGraph
from links import LINKED, ONLY, load_links
from state import excluded_links, linked_resources

head = (ROOT / "code" / "evidence_cache" / "images.md").read_text().split("\n## ")[0]
assert "Transcribed by Claude Code vision during development" in head
assert set(normalizer.image_rows()) == {r["file_id"] for r in normalizer.load_manifest() if r["file_path"].endswith(".png")}

m = LINKED.search("Data catalog linked a_1 (a_1) to x@y.com and user_1.")
assert m.groups() == ("a_1", "a_1", "x@y.com", "user_1")
assert ONLY.search("Attachment catalog confirmed object_9 (f.pdf) belongs only to x@y.com and user_1; no other subject is linked.")
links = load_links()
assert len(links) == 12 and sum(l.exclusive for l in links) == 1

store, _ = coverage.run()
graph = IdentityGraph.load()
# images carry no identifiers; aliases resolved only through links; unlinked aliases stay inert
png = [o for o in store.obs if o.source == "png:cache"]
assert png and all(not o.identifiers for o in png)
assert any(o.resource_id == "delivery_confirmation_20.pdf" for o in png), "unlinked label stays under its own name"
linked_ids = {l.resource_id for l in links}
assert all(r in store.by_resource for r in linked_ids)
# ownership by link only, with the cutoff: before the link time nothing is attached
lk = next(l for l in links if l.exclusive)
idents = graph.identifiers(graph.resolve_one(lk.email))
assert lk.resource_id in linked_resources(store, idents, lk.event_time, "")
assert lk.resource_id not in linked_resources(store, idents, "2026-02-13T08:00:30Z", "")
assert any(l.activity_id == lk.activity_id for l in excluded_links(store, idents, "2026-02-13T08:00:30Z"))
# a link made by a different subject's identifier never attaches this resource
other = next(u for u in graph.users if u not in (graph.resolve_one(lk.email),))
assert lk.resource_id not in linked_resources(store, graph.identifiers(other), "2030-01-01T00:00:00Z", "")
# marketing conflict: the screenshot shows another identity; ownership comes from the link and is noted
rq = {c["case_id"]: c for c in csv.DictReader(open(ROOT / "dataset" / "deletion_requests.csv", newline="", encoding="utf-8"))}
mk = next(l for l in links if l.resource_id.startswith("marketing_"))
case = next(c for c in rq.values() if graph.resolve_one(c["email"]) == mk.user_id and c["audit_at"] >= mk.event_time
            and c["requested_at"] <= mk.event_time)
res = policy.plan_case(case, store, graph, None)
assert any(s["resource_id"] == mk.resource_id and s["operation"] == "REPORT_REMAINING_RECORD" for s in res.steps)
assert any("INFERRED" in n and mk.resource_id in n for n in res.notes)
print("ok images + links")
