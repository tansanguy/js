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
    PROJECT_ROOT,
    focus_over_open_count,
    needs_downstream_or_tls_calibration,
    project_path,
    read_csv,
    read_json,
    rel,
    validated_manifest_payload,
    write_csv,
    write_json,
)


DEFAULT_CONGESTION_SUMMARY = PROJECT_ROOT / "results/metrics/validated_b0_congestion_mapping/congestion_mapping_summary.csv"
DEFAULT_CONGESTION_REALITY_ROOT = PROJECT_ROOT / "results/metrics/validated_b0_congestion_reality"
DEFAULT_SCALE_LABELS = ["warm0p15_sustain0p05", "warm0p25_sustain0p1", "warm0p4_sustain0p15"]
BASELINE_S15_S22_OVER_OPEN = 51
CLEAR_REDUCTION_THRESHOLD = 40
TELEPORT_STOP_COUNT = 10
TELEPORT_STOP_RATIO = 0.005


def run_command(command: list[str], timeout_sec: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False, capture_output=True, text=True, timeout=timeout_sec)


def b0_results_row(results_csv: Path) -> dict[str, str]:
    rows = [row for row in read_csv(results_csv) if row.get("mode") == "B0" and row.get("parameter_id") == "no_control"]
    if not rows:
        return {}
    selected = rows[0]
    keys = [
        "final_status",
        "warning_reason",
        "failure_reason",
        "sumo_exit_code",
        "emergency_teleport",
        "route_error_count",
        "background_teleported",
        "background_teleport_ratio",
        "remaining_vehicle_count",
        "network_avg_speed_kmh",
        "congestion_valid",
        "congestion_reason",
        "rolling_congestion_valid",
        "rolling_congestion_reason",
        "analysis_end_time_sec",
        "analysis_stop_reason",
        "elapsed_wall_sec",
    ]
    return {key: selected.get(key, "") for key in keys}


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
        str(DEFAULT_CONGESTION_REALITY_ROOT),
    ]
    completed = run_command(command, timeout_sec)
    row: dict[str, Any] = {
        "validation_returncode": completed.returncode,
        "validation_stdout": completed.stdout.strip()[-1000:],
        "validation_stderr": completed.stderr.strip()[-2000:],
    }
    summary_json = DEFAULT_CONGESTION_REALITY_ROOT / output_run_id / "validation_summary.json"
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


def float_cell(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def int_cell(row: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(float(row.get(key) or default))
    except (TypeError, ValueError):
        return default


def stop_reason(row: dict[str, Any]) -> str:
    if int_cell(row, "runner_returncode", 0) != 0:
        return "runner_returncode_nonzero"
    if int_cell(row, "sumo_exit_code", 0) != 0:
        return "sumo_exit_code_nonzero"
    if int_cell(row, "route_error_count", 0) > 0:
        return "route_error_present"
    if int_cell(row, "background_teleported", 0) >= TELEPORT_STOP_COUNT:
        return "background_teleport_count_stop"
    if float_cell(row, "background_teleport_ratio", 0.0) >= TELEPORT_STOP_RATIO:
        return "background_teleport_ratio_stop"
    jam_tail_count = row.get("runner_stderr", "").lower().count("waited too long (jam)")
    if float_cell(row, "elapsed_wall_sec", 0.0) >= 240.0 and jam_tail_count >= 3:
        return "slow_run_with_repeated_jam_teleports"
    return ""


def congestion_decision(row: dict[str, Any]) -> str:
    if stop_reason(row):
        return "DEMAND_EXCESSIVE_OR_GRIDLOCK"
    speed_mae = float_cell(row, "speed_mae_kmh", 999.0)
    focus_count = int_cell(row, "s15_s22_over_open_edge_count", 10**9)
    if row.get("speed_status") in {"PASS", "WARN"} and speed_mae <= 8.0 and focus_count <= CLEAR_REDUCTION_THRESHOLD:
        return "CONGESTION_RECALL_OK"
    if needs_downstream_or_tls_calibration({key: str(value) for key, value in row.items()}):
        return "DEMAND_ONLY_INSUFFICIENT_NEEDS_DOWNSTREAM_OR_TLS"
    return "BORDERLINE_REVIEW"


def filter_variants(rows: list[dict[str, str]], labels: list[str]) -> list[dict[str, str]]:
    by_label = {row["scale_label"]: row for row in rows}
    missing = [label for label in labels if label not in by_label]
    if missing:
        raise SystemExit(f"missing scale labels in variant summary: {', '.join(missing)}")
    return [by_label[label] for label in labels]


def main() -> int:
    parser = argparse.ArgumentParser(description="Progressively map B0 demand scale to congestion recall on the latest validated net.")
    parser.add_argument("--variant-summary", default=str(DEFAULT_SCALE_SUMMARY))
    parser.add_argument("--net", default=str(DEFAULT_REPAIRED_NET))
    parser.add_argument("--summary-csv", default=str(DEFAULT_CONGESTION_SUMMARY))
    parser.add_argument("--scale-label", action="append", help="Scale labels to run in order. Defaults to the three-step congestion sweep.")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout-sec", type=int, default=7200)
    args = parser.parse_args()

    labels = args.scale_label or DEFAULT_SCALE_LABELS
    variants = filter_variants(read_csv(project_path(args.variant_summary)), labels)
    rows: list[dict[str, Any]] = []
    summary_csv = project_path(args.summary_csv)
    manifests_dir = summary_csv.parent / "manifests"
    for variant in variants:
        label = variant["scale_label"]
        route_file = project_path(variant["route_file"])
        warmup_scale = float(variant["warmup_scale"])
        sustain_scale = float(variant["sustain_scale"])
        manifest_path = manifests_dir / f"manifest_{label}.json"
        manifest = validated_manifest_payload(
            project_path(args.net),
            route_file,
            warmup_scale,
            sustain_scale,
            notes=f"Validated B0 congestion mapping manifest for {label}.",
        )
        write_json(manifest_path, manifest)
        output_prefix = f"validated_b0_congestion_mapping_{label}"
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
            "baseline_s15_s22_over_open_edge_count": BASELINE_S15_S22_OVER_OPEN,
        }
        if completed.returncode == 0 and results_csv.is_file():
            row.update(b0_results_row(results_csv))
            row.update(validation_row(manifest_path, results_csv, output_run_id=f"{label}_{results_csv.parent.name}", timeout_sec=args.timeout_sec))
        row["stop_reason"] = stop_reason(row)
        row["congestion_decision"] = congestion_decision(row)
        row["needs_downstream_or_tls_calibration"] = str(row["congestion_decision"] != "CONGESTION_RECALL_OK")
        rows.append(row)
        write_csv(summary_csv, rows, CONGESTION_FIELDS)
        if row["stop_reason"]:
            break
    print(f"wrote {rel(summary_csv)} rows={len(rows)}")
    return 0 if rows and not rows[-1].get("runner_returncode") else int(rows[-1].get("runner_returncode") or 0)


CONGESTION_FIELDS = [
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
    "emergency_teleport",
    "route_error_count",
    "background_teleported",
    "background_teleport_ratio",
    "remaining_vehicle_count",
    "network_avg_speed_kmh",
    "congestion_valid",
    "congestion_reason",
    "rolling_congestion_valid",
    "rolling_congestion_reason",
    "analysis_end_time_sec",
    "analysis_stop_reason",
    "elapsed_wall_sec",
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
    "baseline_s15_s22_over_open_edge_count",
    "validation_returncode",
    "validation_summary_json",
    "stop_reason",
    "congestion_decision",
    "needs_downstream_or_tls_calibration",
    "runner_stdout",
    "runner_stderr",
    "validation_stdout",
    "validation_stderr",
]


if __name__ == "__main__":
    raise SystemExit(main())
