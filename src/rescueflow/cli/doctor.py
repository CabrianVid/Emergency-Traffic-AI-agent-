"""`rescueflow doctor` / `scripts/doctor.py`: pass/fail environment checks
with exact remediation. Never prints "SUMO works" without actually having
run an executable (docs/RUNBOOK.md).
"""

from __future__ import annotations

import importlib
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass

from rescueflow.sumo.env import resolve_sumo_env


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    remediation: str = ""


def _check_python() -> CheckResult:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 11)
    return CheckResult(
        "Python >= 3.11", ok, f"found {platform.python_version()} at {sys.executable}",
        "" if ok else "Install Python 3.11+ and recreate the venv: python -m venv .venv",
    )


def _check_module(module_name: str) -> CheckResult:
    try:
        mod = importlib.import_module(module_name)
        version = getattr(mod, "__version__", getattr(mod, "VERSION", "unknown"))
        return CheckResult(f"python module {module_name}", True, f"version {version}")
    except ImportError as exc:
        return CheckResult(
            f"python module {module_name}", False, str(exc),
            f"pip install {module_name} (or recreate .venv with --system-site-packages if offline)",
        )


def _check_git() -> CheckResult:
    git_path = shutil.which("git")
    if not git_path:
        return CheckResult("git", False, "git not found on PATH", "Install Git for Windows")
    result = subprocess.run(["git", "status", "--short"], capture_output=True, text=True)
    return CheckResult("git", result.returncode == 0, f"git at {git_path}; `git status` exit {result.returncode}")


def _check_node() -> CheckResult:
    node_path = shutil.which("node")
    if not node_path:
        return CheckResult(
            "node (optional, stage 3 frontend)", False, "node not found on PATH",
            "Not required for prompt 1; install Node 18+ before the stage-3 dashboard.",
        )
    result = subprocess.run(["node", "--version"], capture_output=True, text=True)
    return CheckResult("node (optional, stage 3 frontend)", True, result.stdout.strip())


def _check_sumo() -> CheckResult:
    env = resolve_sumo_env()
    if env is None:
        return CheckResult(
            "SUMO (sumo + netgenerate executables)", False, "SUMO installation not found",
            "Set SUMO_HOME to your SUMO install directory, or install SUMO and ensure "
            "'sumo'/'netgenerate' are on PATH. See docs/RUNBOOK.md.",
        )
    try:
        result = subprocess.run([str(env.sumo_binary), "--version"], capture_output=True, text=True, check=True)
        version_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
        return CheckResult("SUMO (sumo + netgenerate executables)", True, f"{version_line}; home={env.home}")
    except (subprocess.CalledProcessError, OSError) as exc:
        return CheckResult(
            "SUMO (sumo + netgenerate executables)", False, f"found binaries but failed to execute: {exc}",
            "Reinstall SUMO or check PATH/permissions. See docs/RUNBOOK.md.",
        )


def _check_sumolib() -> CheckResult:
    env = resolve_sumo_env()
    if env is None:
        return CheckResult("sumolib/traci importable", False, "SUMO not found (see above)")
    tools_str = str(env.tools_dir)
    if tools_str not in sys.path:
        sys.path.insert(0, tools_str)
    try:
        import sumolib  # noqa: F401
        import traci  # noqa: F401

        return CheckResult("sumolib/traci importable", True, f"imported from {env.tools_dir}")
    except ImportError as exc:
        return CheckResult(
            "sumolib/traci importable", False, str(exc),
            f"Check that {env.tools_dir} contains sumolib/ and traci/ packages.",
        )


def run_all_checks() -> list[CheckResult]:
    return [
        _check_python(),
        _check_module("pydantic"),
        _check_module("click"),
        _check_module("pytest"),
        _check_git(),
        _check_node(),
        _check_sumo(),
        _check_sumolib(),
    ]


def print_report(results: list[CheckResult]) -> bool:
    all_ok = True
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        print(f"[{status}] {r.name}: {r.detail}")
        if not r.ok:
            all_ok = False
            if r.remediation:
                print(f"       remediation: {r.remediation}")
    print()
    print("Overall: " + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED -- see remediation above"))
    return all_ok
