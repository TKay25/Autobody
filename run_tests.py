"""Run the test suite and write a readable report to pytest-report.txt.

Handy on Windows terminals where long pytest output can get mangled.

The report deliberately does *not* start with ``test-``. pytest collects any
file in the root whose name starts with ``test``, whatever its extension, so the
old ``test-results.txt`` name made the **next** plain ``pytest`` run die with a
collection error before a single test ran.

    python run_tests.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "pytest-report.txt"
LEGACY_REPORT = ROOT / "test-results.txt"


def _remove_legacy_report() -> None:
    """Delete the old report name, which pytest tried to collect as a module."""
    try:
        LEGACY_REPORT.unlink()
    except FileNotFoundError:
        pass


def main() -> int:
    os.chdir(ROOT)
    _remove_legacy_report()
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
