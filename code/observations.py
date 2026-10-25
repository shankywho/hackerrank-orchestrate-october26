"""Observation record and in-memory store.

One Observation = one fact about one resource, taken from one evidence file.
  captured_at  file capture time from evidence_files.csv (cutoff and citation use this)
  observed_at  time inside the file for this row (may be earlier than captured_at)
  subject_key  best single identifier of the data subject (email lower > hash > user ref > phone)
  identifiers  every identifier seen on the row (lowercased)
Category comes from the record's own fields, not its id prefix or the system that reported it
(e.g. a contact_lookup row on an analytics_* resource is MARKETING_PROFILE, rule_10).
"""
from collections import defaultdict
from dataclasses import dataclass, field, replace


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


@dataclass(frozen=True)
class CoverageRecord:
    """A collection/table/section that a file covers, with its row count (0 = proven empty)."""
    file_id: str
    store: str            # system or table the rows came from
    collection: str
    captured_at: str
    row_count: int


def visible(o, audit_at):
    """Cutoff is per file AND per row: both must be on or before audit_at (inclusive)."""
    return o.captured_at <= audit_at and o.observed_at <= audit_at


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
        self.coverage = []
        self.file_times = {}   # file_id -> manifest captured_at
        self.links = []        # LinkRecord list (explicit ownership of image/document resources)
        self.links_by_ident = defaultdict(list)
        self.links_by_resource = defaultdict(list)
        self.by_resource = defaultdict(list)
        self.by_identifier = defaultdict(list)

    def add_all(self, observations):
        for o in observations:
            self.obs.append(o)
            self.by_resource[o.resource_id].append(o)
            for i in o.identifiers:
                self.by_identifier[i].append(o)

    def apply_links(self, links):
        """Resolve alias observations (file names, customer keys, statement numbers) to resource ids through
        link records whose label matches, and index the links by identifier and by resource."""
        by_label = {(l.system, l.label): l for l in links}
        old, self.obs = self.obs, []
        self.by_resource.clear()
        self.by_identifier.clear()
        fixed = []
        for o in old:
            if o.attr("alias"):
                lk = by_label.get((o.system_id, o.resource_id))
                if lk is None:
                    fixed.append(o)          # unlinked label: stays inert under its own name
                    continue
                o = replace(o, resource_id=lk.resource_id)
            fixed.append(o)
        self.add_all(fixed)
        self.links = list(links)
        for l in links:
            self.links_by_ident[l.email].append(l)
            self.links_by_ident[l.user_id].append(l)
            self.links_by_resource[l.resource_id].append(l)

    def add_coverage(self, records):
        self.coverage.extend(records)

    def visible(self, resource_id, audit_at):
        return [o for o in self.by_resource.get(resource_id, []) if visible(o, audit_at)]

    def __len__(self):
        return len(self.obs)
