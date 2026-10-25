"""Groq client (OpenAI-compatible HTTP API) with an on-disk call cache, offline replay, 429 backoff
and per-call token logging. Standard library only. The key comes from GROQ_API_KEY (env or .env).

Cache: every chat call is stored under code/cache/llm/<sha256 of the request>.txt (JSON text). With offline=True
a cache miss raises instead of calling the network, so a grader without a key can replay a run.
Usage log: one CSV row per NEW call in code/evaluation/usage_calls.csv (cache hits are not logged).
"""
import csv
import hashlib
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.groq.com/openai/v1"
CACHE = ROOT / "code" / "cache"
USAGE = ROOT / "code" / "evaluation" / "usage_calls.csv"
NON_CHAT = re.compile(r"whisper|orpheus|tts|guard|safeguard|embed|allam", re.I)
_lock = threading.Lock()
TPM_BUDGET = int(os.environ.get("GROQ_TPM_BUDGET", "6500"))  # stay under the tier's tokens-per-minute limit
_windows = defaultdict(deque)                                # model -> (time, tokens) of recent calls; limits are per model
_wlock = threading.Lock()


class OfflineMiss(RuntimeError):
    pass


def _throttle(model, est_tokens):
    """Block until the last 60 s of usage of THIS model plus this call fits the per-minute token budget."""
    while True:
        with _wlock:
            win, now = _windows[model], time.time()
            while win and now - win[0][0] > 60:
                win.popleft()
            used = sum(t for _, t in win)
            if used + est_tokens <= TPM_BUDGET or not win:
                win.append((now, est_tokens))
                return
            wait = 60 - (now - win[0][0]) + 0.2
        time.sleep(max(0.2, wait))


def _correct_window(model, est, actual):
    with _wlock:
        win = _windows[model]
        for i in range(len(win) - 1, -1, -1):
            if win[i][1] == est:
                win[i] = (win[i][0], actual)
                return


def api_key():
    key = os.environ.get("GROQ_API_KEY", "")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("GROQ_API_KEY="):
                key = line.split("=", 1)[1].strip()
    return key


def _http(path, payload=None, timeout=90):
    req = urllib.request.Request(
        BASE + path, data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + api_key(), "Content-Type": "application/json",
                 "User-Agent": "orchestrate-agent/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _size(model_id):
    nums = [int(n) for n in re.findall(r"(\d+)b", model_id.lower())]
    return max(nums) if nums else 0


def discover_models(offline=False):
    """Models the API lists right now, cached. Returns the raw list."""
    f = CACHE / "models.txt"
    if f.exists() and (offline or time.time() - f.stat().st_mtime < 86400):
        return json.loads(f.read_text())
    if offline:
        raise OfflineMiss("models.txt not cached")
    data = _http("/models")["data"]
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1))
    return data


def pick_chat_model(models, override=None):
    """Chat candidates, best first: active, >=32k context, not audio/guard/small; largest parameter count
    in the id wins, then the larger context. Nothing is hardcoded: the list comes from the API."""
    ok = [m for m in models if m.get("active", True) and not NON_CHAT.search(m["id"])
          and (m.get("context_window") or 0) >= 32768]
    if override:
        return [m for m in models if m["id"] == override] or [{"id": override}]
    return sorted(ok, key=lambda m: (-_size(m["id"]), -(m.get("context_window") or 0), m["id"]))


# USD per 1M tokens (input, output): list prices copied from each model's page on console.groq.com/docs/model/<id>,
# read 2026-10-25. Used to pick the cheaper model and to price the usage report; a model not listed is never priced.
LIST_PRICES = {"openai/gpt-oss-120b": (0.15, 0.60), "openai/gpt-oss-20b": (0.075, 0.30), "qwen/qwen3.8-27b": (0.80, 4.00)}


def cheapest_chat_model(models):
    """Cheapest of the usable chat models by known list price (blended 1:1 input:output); None if none is priced."""
    priced = [m for m in pick_chat_model(models) if m["id"] in LIST_PRICES]
    return min(priced, key=lambda m: (sum(LIST_PRICES[m["id"]]), m["id"]))["id"] if priced else None


def _key(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


USAGE_HEADER = ["ts", "case_id", "purpose", "model", "input_tokens", "output_tokens", "latency_s"]
PURPOSE = "agent_dev"   # set by callers: "intent", "agent_final", "agent_dev", "probe"


def log_usage(case_id, model, usage, cached, latency, purpose=None):
    if cached:      # replayed calls cost nothing and are not logged, so a replay never changes the usage totals
        return
    with _lock:
        new = not USAGE.exists()
        USAGE.parent.mkdir(parents=True, exist_ok=True)
        with open(USAGE, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            if new:
                w.writerow(USAGE_HEADER)
            w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), case_id, purpose or PURPOSE, model,
                        usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), round(latency, 2)])


def tokens_used_last_day(model):
    """Tokens this model has used in the last 24 h according to the log. Old rows without a timestamp are
    counted (conservative: they were all logged within one day of development)."""
    total, cutoff = 0, datetime.now(timezone.utc) - timedelta(hours=24)
    if USAGE.exists():
        for r in csv.DictReader(open(USAGE, encoding="utf-8")):
            if r["model"] != model:
                continue
            if r["ts"] and datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) < cutoff:
                continue
            total += int(r["input_tokens"]) + int(r["output_tokens"])
    return total


def chat(model, messages, tools=None, case_id="", offline=False, max_tokens=900, retries=6, purpose=None, extra=None):
    """One chat completion at temperature 0. Returns the API response dict (from cache when possible)."""
    payload = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if extra:
        payload.update(extra)      # e.g. {"reasoning_effort": "low"}; part of the cache key
    k = _key(payload)
    f = CACHE / "llm" / f"{k}.txt"
    if f.exists():
        resp = json.loads(f.read_text())
        log_usage(case_id, model, resp.get("usage", {}), True, 0.0, purpose)
        return resp
    if offline:
        raise OfflineMiss(f"no cached response for request {k[:12]} (case {case_id})")
    est = int(len(json.dumps(payload, ensure_ascii=False)) / 3.2) + max_tokens // 2
    for attempt in range(retries):
        _throttle(model, est)
        t0 = time.time()
        try:
            resp = _http("/chat/completions", payload)
            _correct_window(model, est, resp.get("usage", {}).get("total_tokens", est))
            break
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            body = raw[:300]
            if e.code == 400 and "tool_use_failed" in raw:
                # the model tried to "call" a tool that does not exist (usually to emit its final JSON).
                # Cache that outcome too so offline replay follows the same path.
                try:
                    failed = json.loads(raw)["error"].get("failed_generation", "")
                except (ValueError, KeyError, AttributeError):
                    failed = ""
                resp = {"choices": [{"message": {"content": "", "tool_calls": []}}], "usage": {},
                        "_tool_use_failed": failed or "tool_use_failed"}
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(resp, ensure_ascii=False))
                log_usage(case_id, model, {}, False, time.time() - t0, purpose)
                return resp
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                m = re.search(r"try again in ([\d.]+)s", body)
                wait = float(e.headers.get("retry-after") or 0) or (float(m.group(1)) if m else min(60, 2 ** attempt * 2))
                wait += 2
                time.sleep(wait + 0.5)
                continue
            raise RuntimeError(f"Groq HTTP {e.code}: {body}") from None
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(resp, ensure_ascii=False))
    log_usage(case_id, model, resp.get("usage", {}), False, time.time() - t0, purpose)
    return resp
