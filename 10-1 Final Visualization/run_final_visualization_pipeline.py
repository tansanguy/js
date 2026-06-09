#!/usr/bin/env python3
"""Run final validation smoke and build the 10-1 presentation HTML.

This wrapper intentionally keeps the simulation runner and HTML builder as
separate subprocesses. That makes each artifact reproducible while giving a
single command for the common presentation workflow.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THIS_DIR = Path(__file__).resolve().parent
VALIDATION_SCRIPT = PROJECT_ROOT / "10 Final Destination Validation/final_destination_validation.py"
BUILD_SCRIPT = THIS_DIR / "build_seoul_fire_station_presentation.py"
REGRESSION_TEST_SCRIPT = THIS_DIR / "test_presentation_regressions.py"
DEFAULT_THETA_ALL_EVALUATIONS = (
    PROJECT_ROOT
    / "09-1 B4 Optimization S1forced/outputs/s1forced_bo_fixed_v2_n1_m50_t6_20260608/all_evaluations.csv"
)
DEFAULT_METRICS_ROOT = PROJECT_ROOT / "results/metrics/compact_v9_final_destination_validation"
DEFAULT_ROUTE_ID = "FINAL_DEST_DONGHO_001"
DEFAULT_REPEAT_ID = "repeat_001"


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def run_command(cmd: list[str], *, dry_run: bool) -> None:
    print("\n$ " + " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"missing {label}: {rel(path)}")


def resolve_repeat_run_dir(run_dir: Path, repeat_id: str) -> Path:
    if (run_dir / "fcd.xml").is_file():
        return run_dir
    candidates = []
    if run_dir.name == repeat_id:
        candidates.extend(sorted(run_dir.parent.glob(f"*/{repeat_id}/fcd.xml")))
    candidates.extend(sorted(run_dir.glob(f"*/{repeat_id}/fcd.xml")))
    candidates.extend(sorted(run_dir.glob("**/fcd.xml")))
    return candidates[0].parent if candidates else run_dir


def validate_run_artifacts(run_id: str, route_id: str, repeat_id: str) -> dict[str, Any]:
    manifest = DEFAULT_METRICS_ROOT / run_id / "final/task_manifest.csv"
    require_file(manifest, "task manifest")
    rows = [
        row
        for row in read_csv(manifest)
        if row.get("route_id") == route_id and row.get("repeat_id") == repeat_id
    ]
    if not rows:
        raise SystemExit(f"no manifest rows for route={route_id} repeat={repeat_id}")

    modes: dict[str, dict[str, str]] = {}
    for row in rows:
        mode = row.get("mode", "")
        run_dir = resolve_repeat_run_dir(PROJECT_ROOT / row.get("run_dir", ""), repeat_id)
        fcd = run_dir / "fcd.xml"
        tls_states = run_dir / "tls_states.csv"
        signal_events = run_dir / "signal_events.csv"
        require_file(fcd, f"{mode} fcd.xml")
        require_file(tls_states, f"{mode} tls_states.csv")
        if mode == "B4":
            require_file(signal_events, "B4 signal_events.csv")
        modes[mode] = {
            "run_dir": rel(run_dir),
            "fcd": rel(fcd),
            "tls_states": rel(tls_states),
            "signal_events": rel(signal_events) if signal_events.is_file() else "",
        }
    for mode in ("B04", "B4"):
        if mode not in modes:
            raise SystemExit(f"missing {mode} manifest row")
    return {"manifest": rel(manifest), "modes": modes}


def default_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"final_viz_pipeline_bo_{stamp}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run final smoke and build 10-1 visualization HTML.")
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--route-id", default=DEFAULT_ROUTE_ID)
    parser.add_argument("--repeat-id", default=DEFAULT_REPEAT_ID)
    parser.add_argument("--theta-all-evaluations", type=Path, default=DEFAULT_THETA_ALL_EVALUATIONS)
    parser.add_argument("--theta-method", default="BO", choices=["ALL", "BO", "CMA-ES", "Random Search"])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--final-selection-count", type=int, default=1)
    parser.add_argument("--background-cohort", choices=["intersection", "raw"], default="intersection")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--skip-validation", action="store_true", help="Build HTML from an existing run-id.")
    parser.add_argument("--skip-regression-tests", action="store_true", help="Do not run 10-1 presentation regression tests after build.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    theta_path = args.theta_all_evaluations.resolve()
    if not args.skip_validation:
        require_file(theta_path, "theta all_evaluations.csv")

    output = args.output
    if output is None:
        output = THIS_DIR / "fire_station_final_presentation.html"
    output = output.resolve()

    if not args.skip_validation:
        validation_cmd = [
            sys.executable,
            str(VALIDATION_SCRIPT),
            "--phase",
            "final",
            "--run-id",
            args.run_id,
            "--theta-all-evaluations",
            str(theta_path),
            "--theta-method",
            args.theta_method,
            "--selected-routes",
            args.route_id,
            "--final-selection-count",
            str(args.final_selection_count),
            "--repeats",
            str(args.repeats),
            "--workers",
            str(args.workers),
            "--emit-fcd",
            "--emit-tls-states",
        ]
        run_command(validation_cmd, dry_run=args.dry_run)

    if not args.dry_run:
        artifact_status = validate_run_artifacts(args.run_id, args.route_id, args.repeat_id)
    else:
        artifact_status = {"manifest": "", "modes": {}}

    build_cmd = [sys.executable, str(BUILD_SCRIPT), "--output", str(output)]
    run_command(build_cmd, dry_run=args.dry_run)

    data_output = output.with_name(f"{output.stem}_data.json")
    validation_output = output.with_name(f"{output.stem}_validation_report.json")
    if not args.dry_run:
        require_file(output, "HTML output")
        require_file(data_output, "data output")
        require_file(validation_output, "validation report output")
    if not args.skip_regression_tests:
        test_cmd = [
            sys.executable,
            str(REGRESSION_TEST_SCRIPT),
            "--data",
            str(data_output),
            "--report",
            str(validation_output),
        ]
        run_command(test_cmd, dry_run=args.dry_run)

    summary = {
        "schema": "final_visualization_pipeline.v1",
        "run_id": args.run_id,
        "route_id": args.route_id,
        "repeat_id": args.repeat_id,
        "theta_all_evaluations": rel(theta_path),
        "theta_method": args.theta_method,
        "skip_validation": args.skip_validation,
        "artifact_status": artifact_status,
        "outputs": {
            "html": rel(output),
            "data": rel(data_output),
            "validation_report": rel(validation_output),
            "regression_tests": rel(REGRESSION_TEST_SCRIPT),
        },
    }
    summary_output = output.with_name(f"{output.stem}_pipeline_summary.json")
    if not args.dry_run:
        summary_output.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {summary_output}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
