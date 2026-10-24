#!/usr/bin/env python3
"""Safety net for AGENTS.md section 5 logging. Never writes log.txt itself.
  snapshot (UserPromptSubmit): remember log.txt size/mtime at turn start.
  check    (Stop): if log.txt did not change this turn, or its last entry lacks
           a populated tool= / model= line, block the stop once with a reminder.
"""
import json, os, re, sys, tempfile

root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
log = os.path.join(root, "log.txt")
try:
    data = json.load(sys.stdin)
except Exception:
    data = {}
sid = str(data.get("session_id", "nosession"))
snap = os.path.join(tempfile.gettempdir(), f"hro_log_snapshot_{sid}.json")

def state():
    try:
        st = os.stat(log)
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None

mode = sys.argv[1] if len(sys.argv) > 1 else "check"
if mode == "snapshot":
    with open(snap, "w") as f:
        json.dump(state(), f)
    sys.exit(0)

if data.get("stop_hook_active"):  # already nudged once this turn; never loop
    sys.exit(0)

problems = []
try:
    before = json.load(open(snap))
except Exception:
    before = "unknown"
now = state()
if now is None:
    problems.append("log.txt does not exist in the repo root")
elif before != "unknown" and before == now:
    problems.append("log.txt was not updated during this turn")
if now is not None:
    text = open(log, errors="ignore").read()
    last = text[text.rfind("\n## ["):] if "\n## [" in text else text
    tool = re.findall(r"^tool=(.*)$", last, re.M)
    model = re.findall(r"^model=(.*)$", last, re.M)
    bad = lambda v: len(v) != 1 or not v[0].strip() or "<" in v[0] or v[0].strip().lower() in ("unknown", "ai")
    if bad(tool):
        problems.append("last log entry needs exactly one populated tool= line")
    if bad(model):
        problems.append("last log entry needs exactly one populated model= line (exact runtime id, never unknown)")
if problems:
    print(json.dumps({"decision": "block", "reason":
        "Logging check (AGENTS.md section 5): " + "; ".join(problems) +
        ". Append/fix the entry with real tool= and model= values, re-read it, then finish."}))
sys.exit(0)
