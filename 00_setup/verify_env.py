#!/usr/bin/env python3
"""Step 0 environment verification for the emergency signal SUMO project."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REQUIRED_DIRS = [
    "config",
    "00_setup",
    "01_prepare/01_map",
    "01_prepare/02_manual_selection",
    "01_prepare/03_scenarios",
    "01_prepare/04_routes",
    "01_prepare/05_demand",
    "01_prepare/06_preflight",
    "02_simulation/controllers",
    "02_simulation/traci_helpers",
    "03_results",
    "common",
    "data_raw/osm",
    "data_raw/traffic_counts",
    "data_prepared/net",
    "data_prepared/geojson",
    "data_prepared/manual",
    "data_prepared/scenarios",
    "data_prepared/routes",
    "data_prepared/demand",
    "data_prepared/preflight",
    "runs/mvp",
    "runs/final",
    "results/raw",
    "results/metrics",
    "results/figures",
    "results/html",
    "results/reports",
    "outputs/logs",
    "outputs/debug",
    "docs",
]

REQUIRED_CONFIG_FILES = [
    "config/map_config.yaml",
    "config/demand_config.yaml",
    "config/simulation_config.yaml",
    "config/control_params_default.yaml",
    "config/run_plan_mvp.csv",
    "config/run_plan_final.csv",
]

REQUIRED_COMMANDS = ["sumo", "sumo-gui", "netconvert"]
REQUIRED_IMPORTS = ["traci", "sumolib"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def check_python_version() -> CheckResult:
    version = sys.version_info
    detail = f"{version.major}.{version.minor}.{version.micro}"
    return CheckResult("Python version", version.major == 3, detail)


def check_project_root(root: Path) -> CheckResult:
    expected = Path.cwd().resolve()
    passed = expected == root
    detail = str(root)
    if not passed:
        detail = f"script root={root}, cwd={expected}"
    return CheckResult("Project root", passed, detail)


def check_sumo_home() -> CheckResult:
    value = os.environ.get("SUMO_HOME")
    return CheckResult("SUMO_HOME", bool(value), value or "not set")


def command_version(command: str) -> str:
    path = shutil.which(command)
    if path is None:
        return "not found in PATH"

    try:
        completed = subprocess.run(
            [command, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics should show the real failure.
        return f"{path}; version check failed: {exc}"

    output = (completed.stdout or completed.stderr).strip().splitlines()
    version = output[0] if output else "version output empty"
    return f"{path}; {version}"


def check_command(command: str) -> CheckResult:
    path = shutil.which(command)
    return CheckResult(f"command {command}", path is not None, command_version(command))


def check_import(module_name: str) -> CheckResult:
    spec = importlib.util.find_spec(module_name)
    return CheckResult(
        f"Python import {module_name}",
        spec is not None,
        getattr(spec, "origin", None) or "not importable",
    )


def check_required_dirs(root: Path) -> CheckResult:
    missing = [path for path in REQUIRED_DIRS if not (root / path).is_dir()]
    detail = "all required folders exist" if not missing else "missing: " + ", ".join(missing)
    return CheckResult("Required folders", not missing, detail)


def check_required_config_files(root: Path) -> CheckResult:
    missing = [path for path in REQUIRED_CONFIG_FILES if not (root / path).is_file()]
    detail = "all required config files exist" if not missing else "missing: " + ", ".join(missing)
    return CheckResult("Required config files", not missing, detail)


def collect_checks(root: Path) -> list[CheckResult]:
    checks = [
        check_python_version(),
        check_project_root(root),
        check_sumo_home(),
    ]
    checks.extend(check_command(command) for command in REQUIRED_COMMANDS)
    checks.extend(check_import(module_name) for module_name in REQUIRED_IMPORTS)
    checks.extend(
        [
            check_required_dirs(root),
            check_required_config_files(root),
        ]
    )
    return checks


def print_report(checks: list[CheckResult]) -> None:
    print("Step 0 environment check")
    print("========================")
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")

    failed = [check for check in checks if not check.passed]
    print()
    if failed:
        print("Failed checks:")
        for check in failed:
            print(f"- {check.name}: {check.detail}")
    else:
        print("All checks passed.")


def main() -> int:
    root = project_root()
    checks = collect_checks(root)
    print_report(checks)
    return 0 if all(check.passed for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
