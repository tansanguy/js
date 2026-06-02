#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from validated_pipeline import (
    DEFAULT_REFERENCE_DISTRIBUTED_V2_VARIANT_SUMMARY,
    DEFAULT_TLS_BOUNDARY_CANDIDATE_SUMMARY,
    PROJECT_ROOT,
    focus_over_open_count,
    project_path,
    read_csv,
    read_json,
    rel,
    selection_score,
    validated_manifest_payload,
    write_csv,
    write_json,
)


DEFAULT_SUMMARY = PROJECT_ROOT / "results/metrics/validated_b0_tls_boundary_sweep/sweep_summary.csv"
DEFAULT_REALITY_ROOT = PROJECT_ROOT / "results/metrics/validated_b0_tls_boundary_reality"
TELEPORT_STOP_COUNT = 10
TELEPORT_STOP_RATIO = 0.005


def run_command(command: list[str], timeout_sec: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False, capture_output=True, text=True, timeout=timeout_sec)


def first_b0_row(results_csv: Path) -> dict[str, str]:
    rows = [row for row in read_csv(results_csv) if row.get("mode") == "B0" and row.get("parameter_id") == "no_control"]
    return rows[0] if rows else {}


def int_cell(row: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(float(row.get(key) or default))
    except (TypeError, ValueError):
        return default


def float_cell(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def stop_reason(row: dict[str, Any]) -> str:
    if int_cell(row, "runner_returncode") != 0:
        return "runner_returncode_nonzero"
    if int_cell(row, "sumo_exit_code") != 0:
        return "sumo_exit_code_nonzero"
    if int_cell(row, "route_error_count") > 0:
        return "route_error_present"
    if int_cell(row, "background_teleported") >= TELEPORT_STOP_COUNT:
        return "background_teleport_count_stop"
    if float_cell(row, "background_teleport_ratio") >= TELEPORT_STOP_RATIO:
        return "background_teleport_ratio_stop"
    return ""


def validation_row(manifest_path: Path, results_csv: Path, output_run_id: str, timeout_sec: int) -> dict[str, Any]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "01-1 Validation/validate_b0_reality_recall.py"),
        "--reference-csv",
        "toegye_ro_mainstream_segments_english.csv",
        "--manifest",
        str(manifest_path),
        "--results-csv",
        str(results_csv),
        "--output-run-id",
        output_run_id,
        "--output-root",
        str(DEFAULT_REALITY_ROOT),
    ]
    completed = run_command(command, timeout_sec)
    row: dict[str, Any] = {"validation_returncode": completed.returncode, "validation_stdout": completed.stdout.strip()[-1000:], "validation_stderr": completed.stderr.strip()[-2000:]}
    summary_json = DEFAULT_REALITY_ROOT / output_run_id / "validation_summary.json"
    row["validation_summary_json"] = rel(summary_json) if summary_json.is_file() else ""
    if summary_json.is_file():
        summary = read_json(summary_json)
        edge_speed_csv = project_path(summary["outputs"].get("edge_speed_csv", ""))
        row.update(
            {
                "overall_status": summary.get("overall_status", ""),
                "lane_status": summary.get("lane_status", ""),
                "demand_status": summary.get("demand_status", ""),
                "speed_status": summary.get("speed_status", ""),
                "edge_speed_status": summary.get("edge_speed_status", ""),
                "lane_recall": summary.get("lane", {}).get("lane_recall", ""),
                "strict_lane_recall": summary.get("lane", {}).get("strict_lane_recall", ""),
                "median_scaled_recall": summary.get("demand", {}).get("median_scaled_recall", ""),
                "geh_pass_warn_ratio": summary.get("demand", {}).get("geh_pass_warn_ratio", ""),
                "speed_mae_kmh": summary.get("speed", {}).get("speed_mae_kmh", ""),
                "edge_speed_mae_kmh": summary.get("edge_speed", {}).get("edge_speed_mae_kmh", ""),
                "over_open_edge_count": summary.get("edge_speed", {}).get("over_open_edge_count", ""),
                "s15_s22_over_open_edge_count": focus_over_open_count(edge_speed_csv),
            }
        )
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Run B0 sweep for TLS/boundary calibrated net candidates.")
    parser.add_argument("--candidate-summary", default=str(DEFAULT_TLS_BOUNDARY_CANDIDATE_SUMMARY))
    parser.add_argument("--variant-summary", default=str(DEFAULT_REFERENCE_DISTRIBUTED_V2_VARIANT_SUMMARY))
    parser.add_argument("--summary-csv", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--candidate-id", action="append")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout-sec", type=int, default=7200)
    parser.add_argument("--stop-on-teleport", action="store_true")
    args = parser.parse_args()

    variants = read_csv(project_path(args.variant_summary))
    if not variants:
        raise SystemExit("missing demand variant")
    variant = variants[0]
    demand_file = project_path(variant["route_file"])
    candidates = read_csv(project_path(args.candidate_summary))
    if args.candidate_id:
        requested = set(args.candidate_id)
        candidates = [row for row in candidates if row.get("candidate_id") in requested]
    summary_csv = project_path(args.summary_csv)
    manifests_dir = summary_csv.parent / "manifests"
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        manifest_path = manifests_dir / f"manifest_{candidate_id}.json"
        manifest = validated_manifest_payload(project_path(candidate["net_file"]), demand_file, 1.0, 1.0, notes=f"Validated B0 TLS/boundary calibration manifest for {candidate_id}.")
        manifest["validated_calibration"] = {**candidate, "demand_variant": variant.get("scale_label", "")}
        write_json(manifest_path, manifest)
        output_prefix = f"validated_b0_tls_boundary_{candidate_id}"
        command = [
            sys.executable,
            str(PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py"),
            "--manifest",
            str(manifest_path),
            "--modes",
            "B0",
            "--repeats",
            str(args.repeats),
            "--workers",
            str(args.workers),
            "--output-prefix",
            output_prefix,
            "--allow-nonfinal-background",
        ]
        completed = run_command(command, args.timeout_sec)
        latest_json = PROJECT_ROOT / f"results/metrics/{output_prefix}/latest.json"
        results_csv = Path("")
        if latest_json.is_file():
            latest = read_json(latest_json)
            results_csv = project_path(latest.get("results_csv", ""))
        row: dict[str, Any] = {
            **candidate,
            **variant,
            "manifest": rel(manifest_path),
            "output_prefix": output_prefix,
            "runner_returncode": completed.returncode,
            "runner_stdout": completed.stdout.strip()[-1000:],
            "runner_stderr": completed.stderr.strip()[-2000:],
            "results_csv": rel(results_csv) if results_csv and results_csv.is_file() else "",
        }
        if completed.returncode == 0 and results_csv.is_file():
            b0 = first_b0_row(results_csv)
            for key in RESULT_KEYS:
                row[key] = b0.get(key, "")
            row.update(validation_row(manifest_path, results_csv, f"{candidate_id}_{results_csv.parent.name}", args.timeout_sec))
        row["stop_reason"] = stop_reason(row)
        row["selection_score"] = selection_score({key: str(value) for key, value in row.items()})
        rows.append(row)
        write_csv(summary_csv, rows, SWEEP_FIELDS)
        if args.stop_on_teleport and row["stop_reason"]:
            break
    print(f"wrote {rel(summary_csv)} rows={len(rows)}")
    return 0


RESULT_KEYS = [
    "final_status",
    "warning_reason",
    "failure_reason",
    "sumo_exit_code",
    "emergency_teleport",
    "route_error_count",
    "background_teleported",
    "background_teleport_ratio",
    "background_departed",
    "background_arrived",
    "remaining_vehicle_count",
    "network_avg_speed_kmh",
    "analysis_end_time_sec",
    "analysis_stop_reason",
    "elapsed_wall_sec",
]

SWEEP_FIELDS = [
    "candidate_id",
    "candidate_index",
    "up_delta_sec",
    "down_delta_sec",
    "offset_sec",
    "boundary_metering",
    "net_file",
    "route_file",
    "vehicle_count",
    "manifest",
    "output_prefix",
    "runner_returncode",
    "results_csv",
    *RESULT_KEYS,
    "overall_status",
    "lane_status",
    "demand_status",
    "speed_status",
    "edge_speed_status",
    "lane_recall",
    "strict_lane_recall",
    "median_scaled_recall",
    "geh_pass_warn_ratio",
    "speed_mae_kmh",
    "edge_speed_mae_kmh",
    "over_open_edge_count",
    "s15_s22_over_open_edge_count",
    "validation_returncode",
    "validation_summary_json",
    "stop_reason",
    "selection_score",
    "runner_stdout",
    "runner_stderr",
    "validation_stdout",
    "validation_stderr",
]


if __name__ == "__main__":
    raise SystemExit(main())
