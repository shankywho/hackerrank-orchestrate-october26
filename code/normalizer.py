"""Normalize evidence files into Observations (JSON, logs, SQL snapshots, handover txt).

No case, user or resource ids appear here. Mapping is by collection / record_type / event
names that come from the data format itself.

State vocabulary (matches the lookup rows that already carry a state in the data):
  USER_PROFILE, SEARCH_INDEX_ENTRY, USER_EXPORT, SUPPORT_ATTACHMENT, MARKETING_PROFILE,
  SECURITY_AUDIT_LOG, LEGAL_HOLD_RECORD   FOUND | NOT_FOUND
  SUPPORT_MESSAGE, ACCOUNT_TOMBSTONE      IDENTIFIABLE | REDACTED
  ANALYTICS_PROFILE                       IDENTIFIABLE | ANONYMIZED
  DATA_FLOW                               ACTIVE | DISABLED
  BACKUP_ARCHIVE                          ACTIVE (restorable) | BLOCKED
"""
import csv
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from logparse import parse_line
from observations import Observation, make, norm_id

EVIDENCE_ROOT = Path(__file__).resolve().parent.parent / "dataset"
HASH = re.compile(r"^[0-9a-f]{64}$")
PHONE = re.compile(r"^\+?[\d\s().\-]{7,}$")


def ids_from(v):
    """Classify one free-form subject reference into the ids dict."""
    n = norm_id(v)
    if not n:
        return {}
    if "@" in n:
        return {"email": n}
    if HASH.match(n):
        return {"email_hash": n}
    if n.startswith("user_"):
        return {"user_ref": n}
    if PHONE.match(n):
        return {"phone": n}
    return {}


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes")


def scrub_state(ids, scrubbed, live):
    return live if any(norm_id(v) for v in ids.values()) else scrubbed


# ---------------------------------------------------------------- collection records
def from_collection(fid, cap, system, coll, rec, observed, source):
    """Map one record of a known collection to a list of Observations."""
    rid = rec.get("resource_id")
    if not rid:
        return []
    M = lambda cat, state, ids, **a: [make(fid, cap, observed, system, rid, cat, state, ids, source, **a)]
    if coll == "customer_profiles":
        ids = dict(email=rec.get("email"), phone=rec.get("phone"), email_hash=rec.get("email_hash"),
                   user_ref=rec.get("customer_reference") or rec.get("user_id"))
        return M("USER_PROFILE", "FOUND", ids, account_status=rec.get("account_status"))
    if coll in ("customer_analytics_reports", "customer_analytics_profiles"):
        ids = dict(email=rec.get("email"), phone=rec.get("phone"), email_hash=rec.get("email_hash"),
                   user_ref=rec.get("user_id") or rec.get("customer_reference"))
        return M("ANALYTICS_PROFILE", scrub_state(ids, "ANONYMIZED", "IDENTIFIABLE"), ids,
                 audience_cohort=rec.get("audience_cohort"))
    if coll in ("customer_search_documents", "search_documents_primary"):
        d = rec.get("document") or rec
        ids = dict(email=d.get("email"), phone=d.get("phone"), email_hash=d.get("email_hash"),
                   user_ref=d.get("customer_reference"))
        return M("SEARCH_INDEX_ENTRY", "FOUND", ids, index_name=rec.get("index_name"),
                 source_resource_id=rec.get("source_resource_id"))
    if coll == "support_conversations":
        text = str(rec.get("message_text") or "")
        state = "IDENTIFIABLE" if ("@" in text or "[REDACTED]" not in text) else "REDACTED"
        return M("SUPPORT_MESSAGE", state, ids_from(rec.get("subject_reference")),
                 ticket_id=rec.get("ticket_id"))
    if coll == "support_records":
        ids = dict(email=rec.get("requester_email"), phone=rec.get("requester_phone"),
                   user_ref=rec.get("customer_reference"), name=rec.get("requester_name"))
        direct = {k: v for k, v in ids.items() if k != "user_ref"}
        return M("SUPPORT_MESSAGE", scrub_state(direct, "REDACTED", "IDENTIFIABLE"), ids)
    if coll == "ticket_attachments":
        return M("SUPPORT_ATTACHMENT", "FOUND", ids_from(rec.get("uploaded_by")),
                 file_name=rec.get("file_name"), ticket_id=rec.get("ticket_id"))
    if coll in ("customer_export_jobs", "customer_exports"):
        ids = dict(email=rec.get("owner_email"), phone=rec.get("owner_phone"),
                   user_ref=rec.get("customer_reference"))
        return M("USER_EXPORT", "FOUND", ids, file_name=rec.get("file_name") or rec.get("object_key"),
                 download_available=rec.get("download_available"))
    if coll in ("connector_configurations", "connector_subscriptions"):
        ids = {**ids_from(rec.get("subject_selector")), **ids_from(rec.get("customer_email"))}
        if rec.get("customer_reference"):
            ids["user_ref"] = rec["customer_reference"]
        return M("DATA_FLOW", "ACTIVE" if truthy(rec.get("enabled")) else "DISABLED", ids,
                 connector_name=rec.get("connector_name"), destination=rec.get("destination"))
    if coll in ("backup_catalog", "backup_restore_entries"):
        ids = {**ids_from(rec.get("subject_reference")), **ids_from(rec.get("customer_email"))}
        if rec.get("customer_reference"):
            ids["user_ref"] = rec["customer_reference"]
        return M("BACKUP_ARCHIVE", "ACTIVE" if truthy(rec.get("restore_allowed")) else "BLOCKED", ids,
                 archive=rec.get("archive_path") or rec.get("archive_key"))
    if coll in ("security_audit_events", "security_audit_records"):
        return M("SECURITY_AUDIT_LOG", "FOUND", ids_from(rec.get("subject_reference")),
                 retention_until=rec.get("retention_until"), event=rec.get("event_name") or rec.get("event_code"))
    if coll == "retained_order_records":
        return M("LEGAL_HOLD_RECORD", "FOUND", ids_from(rec.get("subject_reference")),
                 retained_until=rec.get("retained_until"), reason=rec.get("retention_reason"))
    if coll == "deleted_customer_references":
        ids = dict(email=rec.get("email"), phone=rec.get("phone"), email_hash=rec.get("email_hash"),
                   name=rec.get("name"))
        state = scrub_state(ids, "REDACTED", "IDENTIFIABLE")
        ids["user_ref"] = rec.get("subject_reference")
        return M("ACCOUNT_TOMBSTONE", state, ids, account_status=rec.get("account_status"))
    if coll == "operational_record_history" or "record_type" in rec:
        return from_lookup(fid, cap, rec, source)
    return []


def from_lookup(fid, cap, rec, source):
    """Row that already states system, record_type, result and subject."""
    subject = rec.get("subject_reference") or rec.get("lookup_value")
    when = rec.get("recorded_at") or rec.get("ts")
    return [make(fid, cap, when, rec["system"], rec["resource_id"], rec["record_type"], rec["result"],
                 ids_from(subject), source)]


# ---------------------------------------------------------------- JSON
def from_service_exports(fid, cap, doc, source):
    out = []
    ex = doc["service_exports"]
    for r in ex.get("account_service", {}).get("profile_lookups", []):
        p = r.get("profile") or {}
        ids = dict(email=p.get("email"), phone=p.get("phone"), email_hash=p.get("email_hash"),
                   user_ref=p.get("customer_reference"))
        out.append(make(fid, cap, r["observed_at"], "account_service", r["resource_id"], "USER_PROFILE",
                        "FOUND" if r.get("status_code") == 200 else "NOT_FOUND", ids, source,
                        status_code=r.get("status_code")))
    for r in ex.get("analytics_platform", {}).get("profiles", []):
        out += from_collection(fid, cap, "analytics_platform", "customer_analytics_profiles", r,
                               r["observed_at"], source)
    for r in ex.get("backup_service", {}).get("restore_entries", []):
        out += from_collection(fid, cap, "backup_service", "backup_restore_entries", r, r["observed_at"], source)
    for r in ex.get("export_service", {}).get("exports", []):
        out += from_collection(fid, cap, "export_service", "customer_exports", r, r["observed_at"], source)
    for r in ex.get("integration_service", {}).get("connectors", []):
        out += from_collection(fid, cap, "integration_service", "connector_subscriptions", r,
                               r["observed_at"], source)
    for q in ex.get("search_service", {}).get("index_queries", []):
        docs = (q.get("hits") or {}).get("documents") or []
        if not docs:
            out.append(make(fid, cap, q["observed_at"], "search_service", q["resource_id"],
                            "SEARCH_INDEX_ENTRY", "NOT_FOUND", ids_from(q.get("query")), source))
        for d in docs:
            ids = dict(email=d.get("email"), phone=d.get("phone"), email_hash=d.get("email_hash"),
                       user_ref=d.get("customer_reference"))
            out.append(make(fid, cap, q["observed_at"], "search_service", d["resource_id"],
                            "SEARCH_INDEX_ENTRY", "FOUND", ids, source))
    for r in ex.get("security_service", {}).get("audit_records", []):
        out += from_collection(fid, cap, "security_service", "security_audit_records", r,
                               r["observed_at"], source)
    for r in ex.get("support_platform", {}).get("tickets", []):
        ids = dict(email=r.get("requester_email"), phone=r.get("requester_phone"),
                   name=r.get("requester_name"), user_ref=r.get("customer_reference"))
        direct = {k: v for k, v in ids.items() if k != "user_ref"}
        out.append(make(fid, cap, r["observed_at"], "support_platform", r["resource_id"], "SUPPORT_MESSAGE",
                        scrub_state(direct, "REDACTED", "IDENTIFIABLE"), ids, source))
    return out


def norm_json(fid, cap, path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = []
    if "service_exports" in doc:
        return from_service_exports(fid, cap, doc, "json:service_exports"), None
    if "snapshots" in doc:
        for sn in doc["snapshots"]:
            for rec in sn.get("records", []):
                out += from_collection(fid, cap, sn["data_store"], sn["collection"], rec,
                                       sn.get("captured_at"), "json:snapshots")
        return out, None
    if "records" in doc:
        coll = doc.get("collection")
        for rec in doc["records"]:
            if "record_type" in rec:
                out += from_lookup(fid, cap, rec, "json:ledger")
            elif coll:
                out += from_collection(fid, cap, COLLECTION_SYSTEM.get(coll, doc.get("source", "")), coll, rec,
                                       doc.get("captured_at"), "json:collection")
        return out, None
    return [], "JSON shape not recognised"


# collection -> owning system, only used when a file carries no per-snapshot data_store
COLLECTION_SYSTEM = {
    "customer_analytics_reports": "analytics_platform",
    "customer_export_jobs": "export_service",
    "customer_profiles": "account_service",
    "customer_search_documents": "search_service",
}


# ---------------------------------------------------------------- handover txt
def norm_txt(fid, cap, path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.lstrip("- ").split("|")]
        if len(parts) == 6 and re.match(r"\d{4}-\d\d-\d\dT", parts[0]):
            ts, system, rid, cat, state, subj = parts
            out.append(make(fid, cap, ts, system, rid, cat, state, ids_from(subj), "txt:handover"))
    return out, (None if out else "no handover lines matched")


# ---------------------------------------------------------------- logs
SERVICE_ALIAS = {"profile_service": "account_service"}
STARTED_KEYS = ("lookup_key", "match_key", "scan_key", "subject_key", "subject_ref", "correlation_key")
IGNORED_NOTE = Counter()


def norm_log(fid, cap, path):
    parsed = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        dialect, rec = parse_line(line)
        if rec is None:
            continue
        rec = {k: (None if isinstance(v, str) and v.lower() == "null" else v) for k, v in rec.items()}
        parsed.append((dialect, rec))
    started = {}
    for _, r in parsed:
        for k in STARTED_KEYS:
            if r.get(k) and r.get("trace_id"):
                started.setdefault(r["trace_id"], r[k])
    out, ignored = [], Counter()
    for dialect, r in parsed:
        ev, svc = r.get("event"), SERVICE_ALIAS.get(r.get("service"), r.get("service"))
        rid, ts, src = r.get("resource_id"), r["ts"], f"log:{dialect}"
        subj = next((r[k] for k in ("correlation_key", "subject_reference", "subject_ref", "lookup_key",
                                    "match_key", "customer_email", "owner_email", "requester_email",
                                    "email") if r.get(k)), None) or started.get(r.get("trace_id"))
        base = ids_from(subj)
        M = lambda cat, state, ids=base, system=svc, **a: out.append(
            make(fid, cap, ts, system, rid, cat, state, ids, src, **a))
        if ev == "record_lookup":
            out += from_lookup(fid, cap, {**r, "system": r["system"]}, src)
        elif ev == "profile_lookup_completed":
            ok = r.get("http_status") == "200" or r.get("rows") not in (None, "0")
            ids = {**base, **{k: r[k] for k in ("phone", "customer_reference") if r.get(k)}}
            if "customer_reference" in ids:
                ids["user_ref"] = ids.pop("customer_reference")
            M("USER_PROFILE", "FOUND" if ok else "NOT_FOUND", ids, http_status=r.get("http_status"))
        elif ev in ("analytics_profile_read_completed", "analytics_profile_read", "analytics_identity_scan"):
            ids = dict(base, phone=r.get("phone"), email_hash=r.get("email_hash"),
                       user_ref=r.get("customer_reference"))
            if ev == "analytics_identity_scan":
                ids["email"] = r.get("matched_value")
                state = "IDENTIFIABLE"
            elif ev == "analytics_profile_read":
                state = "IDENTIFIABLE" if r.get("direct_identifiers") not in (None, "0") else "ANONYMIZED"
            else:
                state = scrub_state(ids, "ANONYMIZED", "IDENTIFIABLE")
            M("ANALYTICS_PROFILE", state, ids)
        elif ev == "contact_lookup_completed":
            M("MARKETING_PROFILE", "FOUND", base, email_opt_in=r.get("email_opt_in"))
        elif ev == "restore_policy_read_completed":
            ids = {**base, **ids_from(r.get("customer_email"))}
            M("BACKUP_ARCHIVE", "ACTIVE" if truthy(r.get("restore_allowed")) else "BLOCKED", ids,
              archive=r.get("archive_key"))
        elif ev == "restore_candidate_evaluated":
            M("BACKUP_ARCHIVE", "ACTIVE" if truthy(r.get("eligible")) and not truthy(r.get("deletion_block"))
              else "BLOCKED")
        elif ev == "restore_authorization_completed":
            M("BACKUP_ARCHIVE", "BLOCKED" if r.get("decision") == "deny" else "ACTIVE", policy=r.get("policy"))
        elif ev in ("export_metadata_read_completed", "export_download_check_completed"):
            M("USER_EXPORT", "FOUND" if truthy(r.get("available")) else "NOT_FOUND",
              file_name=r.get("object_key") or r.get("file"))
        elif ev in ("connector_config_read_completed", "connector_config_loaded", "connector_heartbeat"):
            ids = {**base, **ids_from(r.get("customer_email"))}
            M("DATA_FLOW", "ACTIVE" if truthy(r.get("enabled")) else "DISABLED", ids,
              connector_name=r.get("connector_name"), scheduler_registered=r.get("scheduler_registered"))
        elif ev == "index_query_completed":
            ids = dict(base, user_ref=r.get("customer_reference"))
            M("SEARCH_INDEX_ENTRY", "FOUND" if r.get("result") == "hit" else "NOT_FOUND", ids,
              index_name=r.get("index"))
        elif ev == "audit_record_read_completed":
            M("SECURITY_AUDIT_LOG", "FOUND", retention_until=r.get("retention_until"), event=r.get("event_code"))
        elif ev == "support_ticket_read_completed":
            ids = dict(email=r.get("requester_email"), phone=r.get("requester_phone"),
                       name=r.get("requester_name"), user_ref=r.get("customer_reference"))
            direct = {k: v for k, v in ids.items() if k != "user_ref"}
            M("SUPPORT_MESSAGE", scrub_state(direct, "REDACTED", "IDENTIFIABLE"), ids)
        elif ev == "message_pii_scan":
            M("SUPPORT_MESSAGE", "IDENTIFIABLE", ids_from(r.get("matched_value")))
        elif ev == "message_read":
            text = r.get("message_text") or ""
            M("SUPPORT_MESSAGE", "REDACTED" if "[REDACTED]" in text and "@" not in text else "IDENTIFIABLE",
              ids_from(r.get("subject_ref")))
        elif ev == "attachment_pii_scan":
            M("SUPPORT_ATTACHMENT", "FOUND", ids_from(r.get("matched_value")), file_name=r.get("file"))
        elif ev == "retained_order_loaded":
            M("LEGAL_HOLD_RECORD", "FOUND", base, reason=r.get("reason"), access=r.get("access"))
        elif ev == "contact_query_completed" and rid:
            M("SUPPORT_MESSAGE", "IDENTIFIABLE", base, source_index=r.get("source"), inferred="legacy_contact_index")
        else:
            ignored[f"{svc}:{ev}"] += 1
    IGNORED_NOTE.update(ignored)
    note = None if out else "no observation-bearing events"
    return out, note


# ---------------------------------------------------------------- SQL
EMPTY = "EMPTY snapshot (valid schema, 0 rows)"  # parsed fine; coverage does not count it as skipped

TABLE_COLLECTION = {  # typed (non record_json) table -> (system, collection)
    "customer_profiles": ("account_service", "customer_profiles"),
    "customer_profiles_primary": ("account_service", "customer_profiles"),
    "customer_profiles_failover_replica": ("account_service", "customer_profiles"),
    "customer_search_documents": ("search_service", "customer_search_documents"),
    "search_documents_primary": ("search_service", "search_documents_primary"),
    "customer_analytics_profiles": ("analytics_platform", "customer_analytics_profiles"),
    "support_records": ("support_platform", "support_records"),
    "customer_exports": ("export_service", "customer_exports"),
    "connector_subscriptions": ("integration_service", "connector_subscriptions"),
    "backup_restore_entries": ("backup_service", "backup_restore_entries"),
    "security_audit_records": ("security_service", "security_audit_records"),
}


def norm_sql(fid, cap, path):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(path.read_text(encoding="utf-8"))
    out, skipped = [], []
    for (t,) in db.execute("select name from sqlite_master where type='table' order by name").fetchall():
        rows = db.execute(f'select * from "{t}"').fetchall()
        source = f"sql:{t}"
        if t == "snapshot_scope":
            skipped.append(t)
            continue
        for row in rows:
            rec = dict(row)
            observed = rec.get("updated_at") or rec.get("indexed_at")
            if "record_json" in rec:
                out += from_collection(fid, cap, rec["origin_system"], rec["collection_name"],
                                       json.loads(rec["record_json"]), observed, source)
            elif t in TABLE_COLLECTION:
                system, coll = TABLE_COLLECTION[t]
                if t.startswith("customer_profiles_") and "user_id" in rec:
                    rec["customer_reference"] = rec.pop("user_id")
                out += from_collection(fid, cap, system, coll, rec, observed, source)
    return out, (None if out else EMPTY)


# ---------------------------------------------------------------- dispatch
def load_manifest():
    with open(EVIDENCE_ROOT / "evidence_files.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def normalize_file(row):
    """Return (observations, skip_reason). skip_reason is None when parsed."""
    fid, cap = row["file_id"], row["captured_at"]
    path = EVIDENCE_ROOT / row["file_path"]
    ext = path.suffix.lower()
    if ext == ".png":
        return [], "PNG, needs vision"
    if ext == ".pdf":
        return [], "PDF, needs pdftotext parser (not built yet)"
    parsers = {".json": norm_json, ".log": norm_log, ".sql": norm_sql, ".txt": norm_txt}
    if ext not in parsers:
        return [], f"unsupported extension {ext}"
    try:
        return parsers[ext](fid, cap, path)
    except Exception as exc:  # report, never hide: coverage shows the failure
        return [], f"PARSE ERROR {type(exc).__name__}: {exc}"
