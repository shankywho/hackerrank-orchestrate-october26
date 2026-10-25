"""State of a resource at audit_at, and which resources belong to a subject.

get_state_at takes the newest observation(s) that pass the per-file AND per-row cutoff
(observed_at and captured_at both <= audit_at, inclusive). When several sources report the
newest time (primary plus replicas), the violating state wins: a replica that still holds the
record means the record is still present.
"""
from dataclasses import dataclass

from observations import visible

PRECEDENCE = ["FOUND", "IDENTIFIABLE", "ACTIVE", "NOT_FOUND", "REDACTED", "ANONYMIZED", "DISABLED", "BLOCKED"]


@dataclass(frozen=True)
class StateResult:
    resource_id: str
    system_id: str
    category: str
    state: str
    observations: tuple        # the newest observations that decided the state
    cite_file: str             # latest visible file that mentions the resource
    cite_captured_at: str
    replica_only: bool         # every deciding row comes from a replica table


def _rank(state):
    return PRECEDENCE.index(state) if state in PRECEDENCE else len(PRECEDENCE)


def _cite(obs):
    best = max(obs, key=lambda o: (o.captured_at, o.observed_at, o.file_id))
    return best.file_id, best.captured_at


def get_state_at(store, resource_id, audit_at):
    """One StateResult per category the resource appears under. Empty list = never observed."""
    vis = store.visible(resource_id, audit_at)
    by_cat = {}
    for o in vis:
        by_cat.setdefault(o.category, []).append(o)
    out = []
    for cat, obs in sorted(by_cat.items()):
        top = max(o.observed_at for o in obs)
        newest = [o for o in obs if o.observed_at == top]
        state = min((o.state for o in newest), key=_rank)
        deciding = tuple(o for o in newest if o.state == state)
        file_id, cap = _cite(obs)
        out.append(StateResult(resource_id, deciding[0].system_id, cat, state, deciding, file_id, cap,
                               all("replica" in o.source for o in deciding)))
    return out


def linked_resources(store, identifiers, audit_at, since=""):
    """Resources having at least one visible observation that carries one of the identifiers.
    Linking is by exact identifier only; all observations of a linked resource then count.
    `since` (the request time): a person can have separate resources for separate deletion
    requests, so a resource whose newest linking evidence predates the request belongs to an
    earlier request, not this one."""
    newest = {}
    for ident in identifiers:
        for o in store.by_identifier.get(ident, ()):
            if visible(o, audit_at):
                newest[o.resource_id] = max(newest.get(o.resource_id, ""), o.observed_at)
        # explicit link records (LINK_RESOURCE_IDENTITY) are the only ownership source for image/document
        # resources; a link dated after audit_at is not usable for this case
        for lk in store.links_by_ident.get(ident, ()):
            if lk.event_time <= audit_at and store.visible(lk.resource_id, audit_at):
                newest[lk.resource_id] = max(newest.get(lk.resource_id, ""), lk.event_time)
    return sorted(r for r, t in newest.items() if t >= since)


def excluded_links(store, identifiers, audit_at):
    """Link records for this subject that exist but are dated after audit_at (reported, never used)."""
    seen = {}
    for ident in identifiers:
        for lk in store.links_by_ident.get(ident, ()):
            if lk.event_time > audit_at:
                seen[lk.activity_id] = lk
    return sorted(seen.values(), key=lambda l: l.activity_id)


def late_records(store, identifiers, audit_at, category):
    """Observations of `category` linked to the identifiers whose FILE is captured after audit_at.
    Callers may use these only to discover a record id (never its state or citation)."""
    out = []
    for ident in identifiers:
        for o in store.by_identifier.get(ident, ()):
            if o.category == category and o.captured_at > audit_at:
                out.append(o)
    return out


# collection names (as recorded in coverage records) that hold each category
COLLECTIONS = {
    "USER_PROFILE": {"customer_profiles", "customer_profiles_primary", "customer_profiles_failover_replica"},
    "SEARCH_INDEX_ENTRY": {"customer_search_documents", "search_documents_primary", "index_queries"},
    "USER_EXPORT": {"customer_exports", "customer_export_jobs", "exports"},
    "SUPPORT_MESSAGE": {"support_records", "support_conversations", "tickets"},
    "ANALYTICS_PROFILE": {"customer_analytics_profiles", "customer_analytics_reports", "profiles"},
    "DATA_FLOW": {"connector_subscriptions", "connector_configurations", "connectors"},
    "BACKUP_ARCHIVE": {"backup_restore_entries", "backup_catalog", "restore_entries"},
    "SECURITY_AUDIT_LOG": {"security_audit_records", "security_audit_events", "audit_records"},
}


def covering_files(store, category, audit_at):
    """Visible snapshots (file AND snapshot time <= audit_at) that cover this category's collection,
    as (file_id, file_captured_at, snapshot_time, row_count), newest first."""
    names = COLLECTIONS.get(category, set())
    out = []
    for c in store.coverage:
        ft = store.file_times.get(c.file_id, "")
        if c.collection in names and c.captured_at <= audit_at and ft <= audit_at:
            out.append((c.file_id, ft, c.captured_at, c.row_count))
    return sorted(out, key=lambda x: (x[1], x[2], x[0]), reverse=True)


def anchor_file(store, category, audit_at, case_files, preferred_file=""):
    """Citation for a verification step whose resource no visible file mentions (absence proof).
    1) a file already cited by another step of the case that also covers the collection;
    2) for non-account categories, the case's anchor file: the account step's file, else the newest case file;
    3) the newest populated snapshot covering the collection, then the newest empty one.
    Returns "" when nothing pre-audit qualifies (the step is then dropped, never cited late)."""
    cover = covering_files(store, category, audit_at)
    covered = {f for f, *_ in cover}
    hit = [f for f in case_files if f in covered]
    if hit:
        return max(hit, key=lambda f: store.file_times[f])
    if category != "USER_PROFILE":
        if preferred_file:
            return preferred_file
        if case_files:
            return max(case_files, key=lambda f: store.file_times[f])
    populated = [c for c in cover if c[3] > 0]
    if populated:
        return populated[0][0]
    return cover[0][0] if cover else ""
