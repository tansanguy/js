#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from validated_pipeline import (
    DEFAULT_REPAIRED_NET,
    DEFAULT_SCALE_SUMMARY,
    DEFAULT_SWEEP_SUMMARY,
    PROJECT_ROOT,
    focus_over_open_count,
    project_path,
    read_csv,
    read_json,
    rel,
    validated_manifest_payload,
    write_csv,
    write_json,
)


def run_command(command: list[str], timeout_sec: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False, capture_output=True, text=True, timeout=timeout_sec)


def validation_row(scale_row: dict[str, str], manifest_path: Path, results_csv: Path, output_run_id: str, timeout_sec: int) -> dict[str, Any]:
    validation_root = PROJECT_ROOT / "results/metrics/validated_b0_reality"
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
        str(validation_root),
    ]
    completed = run_command(command, timeout_sec)
    row: dict[str, Any] = {
        "validation_returncode": completed.returncode,
        "validation_stdout": completed.stdout.strip()[-1000:],
        "validation_stderr": completed.stderr.strip()[-2000:],
    }
    summary_json = validation_root / output_run_id / "validation_summary.json"
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
                "median_scaled_recall": summary.get("demand", {}).get("median_scaled_recall", ""),
                "geh_pass_warn_ratio": summary.get("demand", {}).get("geh_pass_warn_ratio", ""),
                "speed_mae_kmh": summary.get("speed", {}).get("speed_mae_kmh", ""),
                "edge_speed_mae_kmh": summary.get("edge_speed", {}).get("edge_speed_mae_kmh", ""),
                "over_open_edge_count": summary.get("edge_speed", {}).get("over_open_edge_count", ""),
                "s15_s22_over_open_edge_count": focus_over_open_count(edge_speed_csv),
            }
        )
    return row


def runner_result_row(results_csv: Path) -> dict[str, Any]:
    rows = [row for row in read_csv(results_csv) if row.get("mode") == "B0" and row.get("parameter_id") == "no_control"]
    if not rows:
        return {}
    selected = rows[0]
    return {
        "final_status": selected.get("final_status", ""),
        "warning_reason": selected.get("warning_reason", ""),
        "failure_reason": selected.get("failure_reason", ""),
        "sumo_exit_code": selected.get("sumo_exit_code", ""),
        "route_error_count": selected.get("route_error_count", ""),
        "background_teleported": selected.get("background_teleported", ""),
        "background_teleport_ratio": selected.get("background_teleport_ratio", ""),
        "remaining_vehicle_count": selected.get("remaining_vehicle_count", ""),
        "network_avg_speed_kmh": selected.get("network_avg_speed_kmh", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run B0 demand scale sweep on the lane-repaired validated net.")
    parser.add_argument("--variant-summary", default=str(DEFAULT_SCALE_SUMMARY))
    parser.add_argument("--net", default=str(DEFAULT_REPAIRED_NET))
    parser.add_argument("--summary-csv", default=str(DEFAULT_SWEEP_SUMMARY))
    parser.add_argument("--max-variants", type=int, default=0, help="Limit variants for smoke runs. 0 means all.")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout-sec", type=int, default=7200)
    args = parser.parse_args()

    variants = read_csv(project_path(args.variant_summary))
    if args.max_variants > 0:
        variants = variants[: args.max_variants]
    rows: list[dict[str, Any]] = []
    manifests_dir = PROJECT_ROOT / "results/metrics/validated_b0_scale_sweep/manifests"
    for variant in variants:
        label = variant["scale_label"]
        route_file = project_path(variant["route_file"])
        warmup_scale = float(variant["warmup_scale"])
        sustain_scale = float(variant["sustain_scale"])
        manifest_path = manifests_dir / f"manifest_{label}.json"
        manifest = validated_manifest_payload(project_path(args.net), route_file, warmup_scale, sustain_scale, notes=f"Validated B0 sweep manifest for {label}.")
        write_json(manifest_path, manifest)
        output_prefix = f"validated_b0_scale_sweep_{label}"
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
            **variant,
            "manifest": rel(manifest_path),
            "output_prefix": output_prefix,
            "runner_returncode": completed.returncode,
            "runner_stdout": completed.stdout.strip()[-1000:],
            "runner_stderr": completed.stderr.strip()[-2000:],
            "results_csv": rel(results_csv) if results_csv and results_csv.is_file() else "",
        }
        if completed.returncode == 0 and results_csv.is_file():
            row.update(runner_result_row(results_csv))
            row.update(validation_row(variant, manifest_path, results_csv, output_run_id=f"{label}_{results_csv.parent.name}", timeout_sec=args.timeout_sec))
        rows.append(row)
        write_csv(project_path(args.summary_csv), rows, SWEEP_FIELDS)
    print(f"wrote {rel(project_path(args.summary_csv))} rows={len(rows)}")
    return 0 if all(int(row.get("runner_returncode", 1)) == 0 for row in rows) else 1


SWEEP_FIELDS = [
    "scale_label",
    "warmup_scale",
    "sustain_scale",
    "route_file",
    "vehicle_count",
    "vehicle_counts_by_cycle",
    "manifest",
    "output_prefix",
    "runner_returncode",
    "results_csv",
    "final_status",
    "warning_reason",
    "failure_reason",
    "sumo_exit_code",
    "route_error_count",
    "background_teleported",
    "background_teleport_ratio",
    "remaining_vehicle_count",
    "network_avg_speed_kmh",
    "overall_status",
    "lane_status",
    "demand_status",
    "speed_status",
    "edge_speed_status",
    "lane_recall",
    "median_scaled_recall",
    "geh_pass_warn_ratio",
    "speed_mae_kmh",
    "edge_speed_mae_kmh",
    "over_open_edge_count",
    "s15_s22_over_open_edge_count",
    "validation_returncode",
    "validation_summary_json",
    "runner_stdout",
    "runner_stderr",
    "validation_stdout",
    "validation_stderr",
]


if __name__ == "__main__":
    raise SystemExit(main())
