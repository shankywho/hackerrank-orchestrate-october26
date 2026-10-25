"""Run every test file in its own interpreter. Usage: python3 tests/run_all.py"""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
bad = 0
for t in sorted(here.glob("test_*.py")):
    r = subprocess.run([sys.executable, "-I", str(t)], capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or ["(no output)"])[0 if t.name == "test_post_audit.py" else -1]
    print(f"{'PASS' if r.returncode == 0 else 'FAIL'}  {t.name}: {last[:100]}")
    if r.returncode:
        bad += 1
        print(r.stderr[-600:])
print(f"{len(list(here.glob('test_*.py'))) - bad} passed, {bad} failed")
sys.exit(1 if bad else 0)
