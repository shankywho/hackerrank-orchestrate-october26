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

import pdfparse
from logparse import parse_line
from observations import CoverageRecord, Observation, make, norm_id

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
def from_service_exports(fid, cap, doc, source, cov):
    out = []
    ex = doc["service_exports"]
    for system, sections in ex.items():
        for name, rows in sections.items():
            cov.append(CoverageRecord(fid, system, name, doc.get("exported_at") or cap, len(rows)))
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


def norm_json(fid, cap, path, cov):
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = []
    if "service_exports" in doc:
        return from_service_exports(fid, cap, doc, "json:service_exports", cov), None
    if "snapshots" in doc:
        for sn in doc["snapshots"]:
            cov.append(CoverageRecord(fid, sn["data_store"], sn["collection"], sn.get("captured_at") or cap,
                                      len(sn.get("records", []))))
            for rec in sn.get("records", []):
                out += from_collection(fid, cap, sn["data_store"], sn["collection"], rec,
                                       sn.get("captured_at"), "json:snapshots")
        return out, None
    if "records" in doc:
        coll = doc.get("collection")
        cov.append(CoverageRecord(fid, COLLECTION_SYSTEM.get(coll, doc.get("source") or doc.get("origin") or ""),
                                  coll or "ledger", doc.get("captured_at") or doc.get("exported_at") or cap,
                                  len(doc["records"])))
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
def norm_txt(fid, cap, path, cov):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.lstrip("- ").split("|")]
        if len(parts) == 6 and re.match(r"\d{4}-\d\d-\d\dT", parts[0]):
            ts, system, rid, cat, state, subj = parts
            out.append(make(fid, cap, ts, system, rid, cat, state, ids_from(subj), "txt:handover"))
    return out, (None if out else "no handover lines matched")


# ---------------------------------------------------------------- logs
SERVICE_ALIAS = {"profile_service": "account_service", "support_replica": "support_platform",
                 "attachment_preview": "support_platform"}
STARTED_KEYS = ("lookup_key", "match_key", "scan_key", "subject_key", "subject_ref", "correlation_key")
IGNORED_NOTE = Counter()


def norm_log(fid, cap, path, cov):
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
    batch_source = {path.name: next((r["source"] for _, r in parsed if r.get("event") == "batch_started"
                                     and r.get("source")), None)}
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
            # INFERRED (rule_10, no sample gold beyond one case): a contact/opt-in lookup is a marketing profile
            # whatever the resource id prefix says; the category comes from the record's own fields.
            M("MARKETING_PROFILE", "FOUND", base, email_opt_in=r.get("email_opt_in"), inferred="marketing_by_fields")
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
            # presence only: a contact/case index row exists. Category by index (inferred, flagged).
            idx = r.get("source") or batch_source.get(path.name)
            cat = "SECONDARY_IDENTIFIER" if idx == "legacy_contact_index" else "SUPPORT_MESSAGE"
            M(cat, "FOUND", base, source_index=idx, replica_of=r.get("service"), inferred="contact_index_presence")
        elif ev == "deletion_marker_check" and rid:
            # marker_found=false: the record exists and no deletion was recorded against it
            M("SECONDARY_IDENTIFIER", "FOUND", base, marker_found=r.get("marker_found"),
              last_write=r.get("last_write"), replica_of=r.get("service"), inferred="deletion_marker")
        elif ev == "thumbnail_failed" and rid:
            M("SUPPORT_ATTACHMENT", "FOUND", base, reason=r.get("reason"), replica_of=r.get("service"))
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


def norm_sql(fid, cap, path, cov):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(path.read_text(encoding="utf-8"))
    out = []
    scope = {}
    if db.execute("select 1 from sqlite_master where name='snapshot_scope'").fetchone():
        scope = {r[0]: r[1] for r in db.execute("select origin_system, captured_at from snapshot_scope")}
    for (t,) in db.execute("select name from sqlite_master where type='table' order by name").fetchall():
        rows = db.execute(f'select * from "{t}"').fetchall()
        source = f"sql:{t}"
        if t == "snapshot_scope":
            continue
        system = TABLE_COLLECTION.get(t, (t, t))[0]
        cov.append(CoverageRecord(fid, system, t, scope.get(system, cap), len(rows)))
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


# ---------------------------------------------------------------- PNG (from the transcription cache)
IMAGE_CACHE = Path(__file__).resolve().parent / "evidence_cache" / "images.md"
_image_rows = None


def image_rows():
    """Rows of code/evidence_cache/images.md keyed by file_id (hand transcription made with Claude Code vision)."""
    global _image_rows
    if _image_rows is None:
        _image_rows, fid = {}, None
        for line in IMAGE_CACHE.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                fid = line[3:].split()[0]
            elif line.startswith("OBS |") and fid:
                parts = [p.strip() for p in line.split("|")[1:]]
                if len(parts) == 10:
                    _image_rows.setdefault(fid, []).append(parts)
    return _image_rows


def norm_png(fid, cap, path, cov):
    """Images prove existence and state. They carry NO identifiers: ownership comes only from link records
    (links.py), so what is visible on screen is kept as attributes for cross-checks."""
    rows = image_rows().get(fid)
    if not rows:
        return [], "PNG, not in evidence_cache/images.md"
    out = []
    for system, label, kind, cat, state, email, user, phone, times, note in rows:
        out.append(make(fid, cap, cap, system, label, cat, state, {}, "png:cache", alias=(kind == "alias") or None,
                        visible_email=email or None, visible_user_id=user or None, visible_phone=phone or None,
                        visible_times=times or None, inferred="INFERRED" if "INFERRED" in note else None))
    return out, None


# ---------------------------------------------------------------- PDF
NO_RESOURCE = "PARSED, no resource id (identity facts only)"
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                      "Nov", "Dec"), 1)}
DATE = r"(\d{1,2}) (%s) (\d{4}) (\d{1,2})\s*:?\s*(\d{2}) UTC" % "|".join(MONTHS)
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")
PHONE_TXT = re.compile(r"\+\d[\d ()\-]{7,}\d")


def pdf_time(text, label):
    m = re.search(label + r"\s+" + DATE, text)
    if not m:
        return None
    d, mon, y, h, mi = m.groups()
    return f"{int(y):04d}-{MONTHS[mon]:02d}-{int(d):02d}T{int(h):02d}:{mi}:00Z"


def norm_pdf(fid, cap, path, cov):
    text = " ".join(pdfparse.extract_lines(path))
    out = []
    if m := re.search(r"Event reference\s+(\w+)\s+.*?subject reference\s+(\w+)", text):  # audit journal extract
        rid, user = m.groups()
        out.append(make(fid, cap, pdf_time(text, "recorded"), "security_service", rid, "SECURITY_AUDIT_LOG",
                        "FOUND", ids_from(user), "pdf:journal", retention_until=pdf_time(text, "retain until")))
    elif m := re.search(r"[Ss]upport reference\s+\S+\s*/\s*(support_\w+)", text):  # attachment document
        ids = {}
        if e := EMAIL.search(text):
            ids["email"] = e.group(0)
        if ph := PHONE_TXT.search(text):
            ids["phone"] = ph.group(0)
        f = re.search(r"\bfile\s+(\S+\.\w+)", text)
        out.append(make(fid, cap, pdf_time(text, "captured"), "support_platform", m.group(1),
                        "SUPPORT_ATTACHMENT", "FOUND", ids, "pdf:attachment",
                        file_name=f.group(1) if f else None))
    elif "Retain until" in text and re.search(r"ORD-\d+\s*\|", text):  # legal register, keyed by order
        when = pdf_time(text, "generated") or cap
        for order, user, until in re.findall(
                r"(ORD-\d+)\s*\|\s*(user_\w+)\s*\|[^|]*\|[^|]*\|[^|]*\|\s*Retain until\s+(\d{1,2} \w{3} \d{4})", text):
            d, mon, y = until.split()
            out.append(make(fid, cap, when, "legal_service", order, "LEGAL_HOLD_RECORD", "FOUND", ids_from(user),
                            "pdf:register", keyed_by="order_reference",
                            retained_until=f"{int(y):04d}-{MONTHS[mon]:02d}-{int(d):02d}"))
    elif m := re.search(r"CUSTOMER STATEMENT.*?Statement\s+(NST-[\w-]+)", text):
        # a customer statement is an export artifact; it names no resource id, only its own number, so it is an
        # alias resolved through a link record (LINK_RESOURCE_IDENTITY) and carries no identifiers itself
        out.append(make(fid, cap, cap, "export_service", m.group(1), "USER_EXPORT", "FOUND", {}, "pdf:statement",
                        alias=True))
    else:
        return [], "PDF layout not recognised"
    return out, (None if out else "PDF matched a layout but no rows")


# ---------------------------------------------------------------- dispatch
def load_manifest():
    with open(EVIDENCE_ROOT / "evidence_files.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def normalize_file(row, cov=None):
    """Return (observations, note). note None = parsed; EMPTY / NO_RESOURCE = parsed, nothing to emit.
    Coverage records (one per collection, incl. row_count 0) are appended to `cov`."""
    cov = [] if cov is None else cov
    fid, cap = row["file_id"], row["captured_at"]
    path = EVIDENCE_ROOT / row["file_path"]
    ext = path.suffix.lower()
    parsers = {".json": norm_json, ".log": norm_log, ".sql": norm_sql, ".txt": norm_txt, ".pdf": norm_pdf,
               ".png": norm_png}
    if ext not in parsers:
        return [], f"unsupported extension {ext}"
    try:
        return parsers[ext](fid, cap, path, cov)
    except Exception as exc:  # report, never hide: coverage shows the failure
        return [], f"PARSE ERROR {type(exc).__name__}: {exc}"
