#!/usr/bin/env python
"""Thin wrapper so `python scripts/doctor.py` works without the package
installed. Prefer `rescueflow doctor` once the venv is set up."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rescueflow.cli.doctor import print_report, run_all_checks  # noqa: E402

if __name__ == "__main__":
    ok = print_report(run_all_checks())
    sys.exit(0 if ok else 1)
