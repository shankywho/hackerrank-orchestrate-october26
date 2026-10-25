"""Identity graph invariants. Run: python3 -I tests/test_identity.py"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
from identity import IdentityGraph, email_hash

g = IdentityGraph.load()
rows = list(csv.DictReader(open(ROOT / "dataset" / "user_identities.csv", newline="", encoding="utf-8")))
assert len(g.users) == len(rows) == 850
for r in rows:
    uid = r["user_id"]
    assert g.resolve_one(r["email"]) == uid and g.resolve_one(r["email"].upper()) == uid
    assert g.resolve_one(r["email_hash"]) == uid and email_hash(r["email"]) == r["email_hash"]
    assert g.resolve_one(r["phone"]) == uid and g.resolve_one(uid) == uid
for _, uid, _, field, old, new in g.history:
    if field == "email":
        assert uid in g.resolve(old) and uid in g.resolve(new)
        assert g.resolve(email_hash(old)) >= {uid}
# exact only: altered values never match
r = rows[0]
for bad in (r["email"] + "x", "x" + r["email"], r["email"].split("@")[0], r["email"].replace(".", "", 1) if "." in r["email"].split("@")[0] else r["email"] + "."):
    assert not g.resolve(bad), bad
assert not g.resolve("") and not g.resolve("nobody@example.invalid")
amb = {k: v for k, v in g.owner.items() if len(v) > 1}
reqs = list(csv.DictReader(open(ROOT / "dataset" / "deletion_requests.csv", newline="", encoding="utf-8")))
hist = sum(1 for q in reqs if g.resolve_one(q["email"]) and g.is_historical_email(g.resolve_one(q["email"]), q["email"]))
unres = sum(1 for q in reqs if not g.resolve(q["email"]))
print(f"ok users={len(g.users)} ambiguous_identifiers={len(amb)} request_emails: historical={hist} unresolved={unres} of {len(reqs)}")
assert not amb and unres == 0
