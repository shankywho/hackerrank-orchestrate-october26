"""Observation record and in-memory store.

One Observation = one fact about one resource, taken from one evidence file.
  captured_at  file capture time from evidence_files.csv (cutoff and citation use this)
  observed_at  time inside the file for this row (may be earlier than captured_at)
  subject_key  best single identifier of the data subject (email lower > hash > user ref > phone)
  identifiers  every identifier seen on the row (lowercased)
"""
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Observation:
    file_id: str
    captured_at: str
    observed_at: str
    system_id: str
    resource_id: str
    category: str
    state: str
    subject_key: str
    identifiers: tuple = ()
    source: str = ""          # e.g. "json:snapshots", "log:kv", "sql:customer_data_replica_eu_west"
    attrs: tuple = field(default_factory=tuple)  # sorted (key, value) pairs, e.g. restore_allowed

    def attr(self, key, default=None):
        return dict(self.attrs).get(key, default)


def norm_id(v):
    """Lowercase string identifier, or None for null-like values."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("null", "none", "no direct identifier", "[redacted]"):
        return None
    return s.lower()


def subject_key(email=None, email_hash=None, user_ref=None, phone=None):
    for v in (email, email_hash, user_ref, phone):
        n = norm_id(v)
        if n:
            return n
    return ""


def make(file_id, captured_at, observed_at, system_id, resource_id, category, state,
         ids, source, **attrs):
    """Build an Observation. ids = dict(email=, email_hash=, user_ref=, phone=, ...)."""
    key = subject_key(ids.get("email"), ids.get("email_hash"), ids.get("user_ref"), ids.get("phone"))
    allids = tuple(sorted({norm_id(v) for v in ids.values() if norm_id(v)}))
    clean = tuple(sorted((k, v) for k, v in attrs.items() if v is not None))
    return Observation(file_id, captured_at, observed_at or captured_at, system_id, str(resource_id),
                       category, state, key, allids, source, clean)


class Store:
    def __init__(self):
        self.obs = []
        self.by_resource = defaultdict(list)
        self.by_identifier = defaultdict(list)

    def add_all(self, observations):
        for o in observations:
            self.obs.append(o)
            self.by_resource[o.resource_id].append(o)
            for i in o.identifiers:
                self.by_identifier[i].append(o)

    def __len__(self):
        return len(self.obs)
