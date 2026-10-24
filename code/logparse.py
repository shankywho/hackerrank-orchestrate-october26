"""Parse the six log dialects in dataset/evidence/logs into uniform event dicts.

Every parsed line yields: ts, level, service, trace_id, event, plus all key=value
fields of the line. Dialects (detected per line, not per file):
  kv       TS level=X service=Y trace_id=Z event=E k=v ...
  json     TS {"severity":..,"service":..,"event":..,...}
  syslog   TS <pri>1 node SERVICE TRACE - - k="v" ...
  bracket  TS [LEVEL] [service] [trace] event: k="v" ...
  cloud    TS requestId=T level=L function=F message="k=\\"v\\" ..."
  pipe     TS | LEVEL | service | trace | event | k="v" ...
"""
import json
import re

KV = re.compile(r'([A-Za-z_][\w.\-]*)=("(?:[^"\\]|\\.)*"|\S*)')
TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+(.*)$")
SYSLOG = re.compile(r"^<\d+>1\s+(\S+)\s+(\S+)\s+(\S+)\s+-\s+-\s+(.*)$")
BRACKET = re.compile(r"^\[(\w+)\]\s+\[([^\]]+)\]\s+\[([^\]]*)\]\s+([\w.\-]+):\s*(.*)$")
PIPE = re.compile(r"^\|?\s*(\w+)\s+\|\s+([^|]+?)\s+\|\s+([^|]*?)\s+\|\s+([^|]+?)\s+\|\s*(.*)$")


def unquote(v):
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def kvs(text):
    return {k: unquote(v) for k, v in KV.findall(text)}


def parse_line(line):
    """Return (dialect, dict) or (None, None) for blank/unparseable lines."""
    line = line.rstrip("\n")
    m = TS.match(line)
    if not m:
        return None, None
    ts, rest = m.groups()
    rec = {"ts": ts}
    if rest.startswith("{"):
        try:
            d = json.loads(rest)
        except json.JSONDecodeError:
            return None, None
        d["level"] = d.pop("severity", d.get("level"))
        d.pop("logger", None)
        rec.update({k: str(v) if v is not None else None for k, v in d.items()})
        return "json", rec
    if rest.startswith("<"):
        s = SYSLOG.match(rest)
        if not s:
            return None, None
        _, service, trace, tail = s.groups()
        rec.update(kvs(tail))
        rec.update({"service": service, "trace_id": trace})
        return "syslog", rec
    if rest.startswith("["):
        b = BRACKET.match(rest)
        if not b:
            return None, None
        level, service, trace, event, tail = b.groups()
        rec.update(kvs(tail))
        rec.update({"level": level, "service": service, "trace_id": trace, "event": event})
        return "bracket", rec
    if rest.startswith("|") or re.match(r"^\w+\s+\|", rest):
        p = PIPE.match(rest)
        if not p:
            return None, None
        level, service, trace, event, tail = p.groups()
        rec.update(kvs(tail))
        rec.update({"level": level, "service": service, "trace_id": trace, "event": event})
        return "pipe", rec
    top = kvs(rest)
    if "message" in top and "function" in top:
        inner = kvs(top.pop("message"))
        rec.update(top)
        rec.update(inner)
        rec["service"] = top["function"]
        rec["trace_id"] = top.get("requestId")
        return "cloud", rec
    rec.update(top)
    return "kv", rec
