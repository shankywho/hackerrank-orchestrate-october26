"""Identity graph: user_id <-> every current and historical identifier. Exact matching only.

Sources: user_identities.csv (current snapshot) and user_audit_log.csv (old/new values of
email, email_hash and phone changes). A hash is also derived as sha256(lower(email)) for each
known email, because older systems store hashes of old addresses. Matching is case-insensitive
equality after trimming; there is no fuzzy or substring matching.
"""
import csv
import hashlib
from collections import defaultdict
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / "dataset"


def norm(v):
    return v.strip().lower() if v else ""


def email_hash(email):
    return hashlib.sha256(norm(email).encode("utf-8")).hexdigest()


class IdentityGraph:
    def __init__(self):
        self.users = {}                       # user_id -> current row
        self.ids = defaultdict(lambda: defaultdict(set))  # user_id -> kind -> {values}
        self.owner = defaultdict(set)         # identifier -> {user_id}
        self.history = []                     # (audit_id, user_id, time, field, old, new)

    def _add(self, user_id, kind, value):
        v = norm(value)
        if not v:
            return
        self.ids[user_id][kind].add(v)
        self.owner[v].add(user_id)
        if kind == "email":
            h = email_hash(v)
            self.ids[user_id]["email_hash"].add(h)
            self.owner[h].add(user_id)

    @classmethod
    def load(cls, dataset=DATASET):
        g = cls()
        with open(dataset / "user_identities.csv", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                g.users[r["user_id"]] = r
                g._add(r["user_id"], "user_id", r["user_id"])
                g._add(r["user_id"], "email", r["email"])
                g._add(r["user_id"], "phone", r["phone"])
                g._add(r["user_id"], "email_hash", r["email_hash"])
        with open(dataset / "user_audit_log.csv", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                g.history.append((r["audit_id"], r["user_id"], r["event_time"], r["field_name"],
                                  r["old_value"], r["new_value"]))
                if r["field_name"] in ("email", "email_hash", "phone"):
                    g._add(r["user_id"], r["field_name"], r["old_value"])
                    g._add(r["user_id"], r["field_name"], r["new_value"])
        return g

    def resolve(self, value):
        """Return the set of user_ids that own this exact identifier (empty, one, or ambiguous)."""
        return set(self.owner.get(norm(value), ()))

    def resolve_one(self, value):
        owners = self.resolve(value)
        return next(iter(owners)) if len(owners) == 1 else None

    def identifiers(self, user_id):
        """All identifiers of the user, flattened and lowercased."""
        return {v for vals in self.ids[user_id].values() for v in vals}

    def is_historical_email(self, user_id, email):
        return norm(email) in self.ids[user_id]["email"] and norm(email) != norm(self.users[user_id]["email"])
