"""Normalize every evidence file and write a per-file coverage report.

Usage: python3 -I code/coverage.py [--out code/evaluation/coverage_report.md]
Every manifest file appears exactly once: with an observation count, or a skip reason.
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalizer
from observations import Store

ROOT = Path(__file__).resolve().parent.parent


def run():
    store = Store()
    rows = []
    for row in normalizer.load_manifest():
        obs, skip = normalizer.normalize_file(row)
        store.add_all(obs)
        rows.append((row, obs, skip))
    return store, rows


def is_skip(reason):
    return reason is not None and reason != normalizer.EMPTY


def family(row):
    ext = Path(row["file_path"]).suffix.lstrip(".")
    return f"{ext}/{row['origin']}"


def render(store, rows):
    L = ["# Evidence coverage report", "",
         f"manifest files: {len(rows)}  observations: {len(store)}  "
         f"parsed files: {sum(1 for *_, s in rows if not is_skip(s))}  "
         f"skipped: {sum(1 for *_, s in rows if is_skip(s))}", ""]
    L += ["## Per file", "", "| file_id | captured_at | path | obs | status |", "|---|---|---|---|---|"]
    for row, obs, skip in rows:
        status = "parsed" if skip is None else (skip if not is_skip(skip) else f"SKIPPED: {skip}")
        L.append(f"| {row['file_id']} | {row['captured_at']} | {row['file_path']} | {len(obs)} | {status} |")
    fam = defaultdict(lambda: [0, 0, 0])
    for row, obs, skip in rows:
        f = fam[family(row)]
        f[0] += 1
        f[1] += len(obs)
        f[2] += is_skip(skip)
    L += ["", "## Per family (extension / manifest origin)", "",
          "| family | files | observations | skipped files |", "|---|---|---|---|"]
    for k, (n, o, s) in sorted(fam.items()):
        L.append(f"| {k} | {n} | {o} | {s} |")
    by_fmt = defaultdict(lambda: [0, 0])
    for row, obs, skip in rows:
        by_fmt[Path(row["file_path"]).suffix.lstrip(".")][0] += 1
        by_fmt[Path(row["file_path"]).suffix.lstrip(".")][1] += len(obs)
    L += ["", "## Per format", "", "| format | files | observations |", "|---|---|---|"]
    for k, (n, o) in sorted(by_fmt.items()):
        L.append(f"| {k} | {n} | {o} |")
    src = Counter(o.source.split(":")[0] + ":" + o.source.split(":")[1] for o in store.obs)
    L += ["", "## Per observation source", "", "| source | observations |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(src.items())]
    cat = Counter((o.category, o.state) for o in store.obs)
    L += ["", "## Per (category, state)", "", "| category | state | observations |", "|---|---|---|"]
    L += [f"| {c} | {s} | {n} |" for (c, s), n in sorted(cat.items())]
    L += ["", "## Log events seen but not turned into observations", "",
          "| service:event | lines |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(normalizer.IGNORED_NOTE.items())]
    no_subject = sum(1 for o in store.obs if not o.subject_key)
    L += ["", f"observations with empty subject_key: {no_subject}", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "code" / "evaluation" / "coverage_report.md"))
    a = ap.parse_args()
    store, rows = run()
    text = render(store, rows)
    Path(a.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
