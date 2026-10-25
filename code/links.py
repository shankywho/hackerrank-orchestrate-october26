"""LINK_RESOURCE_IDENTITY activities: the only source of ownership for image-derived resources.

An activity says "linked <resource> (<label>) to <email> and <user_id>" or, for attachments,
"confirmed <resource> (<label>) belongs only to <email> and <user_id>; no other subject is linked".
The label is what a screenshot or document shows (file name, customer key, statement number).
"""
import csv
import re
from dataclasses import dataclass
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / "dataset"
LINKED = re.compile(r"linked (\S+) \((.+?)\) to (\S+) and (user_\w+)")
ONLY = re.compile(r"confirmed (\S+) \((.+?)\) belongs only to (\S+) and (user_\w+)")


@dataclass(frozen=True)
class LinkRecord:
    activity_id: str
    event_time: str
    system: str          # target_system of the activity
    resource_id: str
    label: str
    email: str
    user_id: str
    exclusive: bool      # "belongs only to": no other subject is linked


def load_links(dataset=DATASET):
    out = []
    with open(dataset / "system_activity.csv", newline="", encoding="utf-8") as fh:
        for a in csv.DictReader(fh):
            if a["event_type"] != "LINK_RESOURCE_IDENTITY" or a["reported_result"] != "SUCCESS":
                continue
            m, excl = ONLY.search(a["details"]), True
            if not m:
                m, excl = LINKED.search(a["details"]), False
            if m:
                rid, label, email, user = m.groups()
                out.append(LinkRecord(a["activity_id"], a["event_time"], a["target_system"], rid, label,
                                      email.lower().rstrip(";."), user.lower(), excl))
    return out
