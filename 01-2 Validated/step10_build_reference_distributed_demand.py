#!/usr/bin/env python3
from __future__ import annotations

import argparse

from validated_pipeline import (
    DEFAULT_BASE_DEMAND,
    DEFAULT_DEMAND_DIR,
    DEFAULT_MAPPING_CSV,
    DEFAULT_REFERENCE_CSV,
    DEFAULT_REFERENCE_DISTRIBUTED_VARIANT_SUMMARY,
    PROJECT_ROOT,
    REFERENCE_DISTRIBUTED_LABEL,
    build_reference_distributed_demand,
    project_path,
    rel,
    write_csv,
    write_json,
)


DEFAULT_OUTPUT_ROUTE = DEFAULT_DEMAND_DIR / f"background_routes_validated_{REFERENCE_DISTRIBUTED_LABEL}.rou.xml"
DEFAULT_SEGMENT_SUMMARY = PROJECT_ROOT / "results/metrics/validated_reference_distributed_demand/segment_target_summary.csv"
DEFAULT_SUMMARY_JSON = PROJECT_ROOT / "results/metrics/validated_reference_distributed_demand/summary.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build distributed validated demand from reference segment volumes using existing OD route templates.")
    parser.add_argument("--reference-csv", default=str(DEFAULT_REFERENCE_CSV))
    parser.add_argument("--mapping-csv", default=str(DEFAULT_MAPPING_CSV))
    parser.add_argument("--base-route", default=str(DEFAULT_BASE_DEMAND))
    parser.add_argument("--output-route", default=str(DEFAULT_OUTPUT_ROUTE))
    parser.add_argument("--segment-summary-csv", default=str(DEFAULT_SEGMENT_SUMMARY))
    parser.add_argument("--summary-json", default=str(DEFAULT_SUMMARY_JSON))
    parser.add_argument("--variant-summary", default=str(DEFAULT_REFERENCE_DISTRIBUTED_VARIANT_SUMMARY))
    parser.add_argument("--duration-sec", type=float, default=7200.0)
    parser.add_argument("--max-vehicles", type=int, default=10000)
    args = parser.parse_args()

    output_route = project_path(args.output_route)
    segment_rows, summary = build_reference_distributed_demand(
        project_path(args.reference_csv),
        project_path(args.mapping_csv),
        project_path(args.base_route),
        output_route,
        duration_sec=args.duration_sec,
        max_vehicles=args.max_vehicles,
    )
    segment_fields = ["segment_id", "direction", "target_count", "generated_template_count", "generated_recall", "remaining_count"]
    write_csv(project_path(args.segment_summary_csv), segment_rows, segment_fields)
    write_json(project_path(args.summary_json), {**summary, "segment_targets": segment_rows})
    write_csv(
        project_path(args.variant_summary),
        [
            {
                "scale_label": REFERENCE_DISTRIBUTED_LABEL,
                "warmup_scale": 1.0,
                "sustain_scale": 1.0,
                "route_file": rel(output_route),
                "vehicle_count": summary["vehicle_count"],
                "vehicle_counts_by_cycle": "distributed_reference_od_7200s",
            }
        ],
        ["scale_label", "warmup_scale", "sustain_scale", "route_file", "vehicle_count", "vehicle_counts_by_cycle"],
    )
    print(
        f"wrote {rel(output_route)} vehicles={summary['vehicle_count']} "
        f"templates={summary['candidate_template_count']} mean_recall={summary['mean_generated_recall']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
