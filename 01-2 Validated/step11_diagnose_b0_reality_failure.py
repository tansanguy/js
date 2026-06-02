#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from validated_pipeline import PROJECT_ROOT, project_path, read_csv, read_json, rel, write_csv, write_json


DEFAULT_VALIDATION_SUMMARY = PROJECT_ROOT / "results/metrics/validated_b0_congestion_reality/reference_distributed_od_20260602T094859_814119Z0000/validation_summary.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results/metrics/validated_b0_failure_diagnosis"


def classify_speed(error: float) -> str:
    if error >= 8.0:
        return "over_open"
    if error <= -8.0:
        return "over_congested"
    if abs(error) <= 5.0:
        return "near_target"
    return "warn"


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose the latest validated B0 reality recall failure by segment/direction.")
    parser.add_argument("--validation-summary", default=str(DEFAULT_VALIDATION_SUMMARY))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    summary_path = project_path(args.validation_summary)
    output_dir = project_path(args.output_dir)
    summary = read_json(summary_path)
    speed_csv = project_path(summary["outputs"]["speed_csv"])
    demand_csv = project_path(summary["outputs"]["demand_csv"])
    speed_rows = read_csv(speed_csv)
    demand_by_key = {(row["segment_id"], row["direction"]): row for row in read_csv(demand_csv)}
    rows = []
    for row in speed_rows:
        key = (row["segment_id"], row["direction"])
        demand = demand_by_key.get(key, {})
        error = float(row.get("speed_error_kmh") or 0.0)
        rows.append(
            {
                "segment_id": row["segment_id"],
                "direction": row["direction"],
                "reference_speed_kmh": row.get("reference_speed_kmh", ""),
                "simulated_speed_kmh": row.get("simulated_speed_kmh", ""),
                "speed_error_kmh": row.get("speed_error_kmh", ""),
                "speed_problem": classify_speed(error),
                "scaled_reference_count": demand.get("scaled_reference_count", ""),
                "observed_count": demand.get("observed_count", ""),
                "scaled_recall": demand.get("scaled_recall", ""),
                "geh": demand.get("geh", ""),
                "demand_status": demand.get("status", ""),
            }
        )
    output_csv = output_dir / "segment_failure_diagnosis.csv"
    fields = [
        "segment_id",
        "direction",
        "reference_speed_kmh",
        "simulated_speed_kmh",
        "speed_error_kmh",
        "speed_problem",
        "scaled_reference_count",
        "observed_count",
        "scaled_recall",
        "geh",
        "demand_status",
    ]
    write_csv(output_csv, rows, fields)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["speed_problem"]] = counts.get(row["speed_problem"], 0) + 1
    output_json = output_dir / "diagnosis_summary.json"
    write_json(
        output_json,
        {
            "schema": "validated_b0_failure_diagnosis.v1",
            "validation_summary": rel(summary_path),
            "segment_diagnosis_csv": rel(output_csv),
            "speed_problem_counts": counts,
            "overall_status": summary.get("overall_status", ""),
            "demand": summary.get("demand", {}),
            "speed": summary.get("speed", {}),
            "edge_speed": summary.get("edge_speed", {}),
        },
    )
    print(f"wrote {rel(output_csv)} and {rel(output_json)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
