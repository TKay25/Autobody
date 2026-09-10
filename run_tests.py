"""Run the test suite and write a readable report to test-results.txt.

Handy on Windows terminals where long pytest output can get mangled.

    python run_tests.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "test-results.txt"


def main() -> int:
    os.chdir(ROOT)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:warnings", "--tb=short"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    REPORT.write_text(output, encoding="utf-8")
    print(output.strip().splitlines()[-1] if output.strip() else "no output")
    print(f"full report -> {REPORT}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
