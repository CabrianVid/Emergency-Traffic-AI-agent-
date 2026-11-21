"""Locate a working SUMO installation and make `sumolib`/`traci` importable.

SUMO is not a pip package here: it is the official Windows distribution,
which ships `sumolib`/`traci` as plain Python under `<install>/tools`. We
resolve, in order: the `SUMO_HOME` environment variable, then a short list of
well-known Windows install paths, then `sumo`/`netgenerate` found on PATH.
Nothing in this module claims SUMO works without actually locating a real
`sumo` executable -- see `scripts/doctor.py`.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

_CANDIDATE_HOMES = [
    r"C:\Program Files (x86)\SUMO",
    r"C:\Program Files\SUMO",
]


@dataclass
class SumoEnv:
    home: Path
    sumo_binary: Path
    netgenerate_binary: Path
    tools_dir: Path


def find_sumo_home() -> Path | None:
    import os

    env_home = os.environ.get("SUMO_HOME")
    if env_home and (Path(env_home) / "tools").is_dir():
        return Path(env_home)

    for candidate in _CANDIDATE_HOMES:
        p = Path(candidate)
        if (p / "tools").is_dir() and (p / "bin").is_dir():
            return p

    sumo_on_path = shutil.which("sumo") or shutil.which("sumo.exe")
    if sumo_on_path:
        bin_dir = Path(sumo_on_path).resolve().parent
        home = bin_dir.parent
        if (home / "tools").is_dir():
            return home
    return None


def resolve_sumo_env() -> SumoEnv | None:
    home = find_sumo_home()
    if home is None:
        return None
    exe_suffix = ".exe" if sys.platform == "win32" else ""
    sumo_bin = home / "bin" / f"sumo{exe_suffix}"
    netgen_bin = home / "bin" / f"netgenerate{exe_suffix}"
    if not sumo_bin.exists() or not netgen_bin.exists():
        return None
    return SumoEnv(home=home, sumo_binary=sumo_bin, netgenerate_binary=netgen_bin, tools_dir=home / "tools")


def ensure_sumolib_importable() -> SumoEnv:
    """Raises RuntimeError with a remediation hint if SUMO cannot be found."""
    env = resolve_sumo_env()
    if env is None:
        raise RuntimeError(
            "SUMO not found. Set SUMO_HOME to your SUMO install directory "
            "(the folder containing bin/ and tools/), e.g. "
            r"'C:\Program Files (x86)\SUMO', or install SUMO and ensure "
            "'sumo' is on PATH. See docs/RUNBOOK.md."
        )
    tools_str = str(env.tools_dir)
    if tools_str not in sys.path:
        sys.path.insert(0, tools_str)
    return env
