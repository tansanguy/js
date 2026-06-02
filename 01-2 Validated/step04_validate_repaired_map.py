#!/usr/bin/env python3
from __future__ import annotations

import argparse

from validated_pipeline import DEFAULT_REFERENCE_CSV, DEFAULT_REPAIRED_NET, PROJECT_ROOT, project_path, rel, validate_repaired_map, write_csv, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate lane-repaired net before demand calibration.")
    parser.add_argument("--reference-csv", default=str(DEFAULT_REFERENCE_CSV))
    parser.add_argument("--net", default=str(DEFAULT_REPAIRED_NET))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "results/metrics/validated_map"))
    args = parser.parse_args()
    output_dir = project_path(args.output_dir)
    summary = validate_repaired_map(project_path(args.reference_csv), project_path(args.net))
    lane_rows = summary.pop("lane_rows")
    lane_csv = output_dir / "lane_recall.csv"
    summary_json = output_dir / "map_validation_summary.json"
    write_csv(
        lane_csv,
        lane_rows,
        [
            "segment_id",
            "direction",
            "reference_lanes",
            "matched_edge_count",
            "matched_edge_ids",
            "matched_lane_counts",
            "mode_lane_count",
            "median_lane_count",
            "max_lane_count",
            "status",
        ],
    )
    summary["outputs"] = {"lane_csv": rel(lane_csv), "summary_json": rel(summary_json)}
    write_json(summary_json, summary)
    print(f"status={summary['overall_status']} lane={summary['lane_status']} summary={rel(summary_json)}")
    return 0 if summary["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
