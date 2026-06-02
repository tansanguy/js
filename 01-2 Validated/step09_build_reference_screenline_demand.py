#!/usr/bin/env python3
from __future__ import annotations

import argparse

from validated_pipeline import (
    DEFAULT_DEMAND_DIR,
    DEFAULT_REFERENCE_CSV,
    DEFAULT_REFERENCE_SCREENLINE_VARIANT_SUMMARY,
    DEFAULT_REPAIRED_NET,
    PROJECT_ROOT,
    REFERENCE_SCREENLINE_LABEL,
    build_reference_screenline_demand,
    project_path,
    rel,
    write_csv,
    write_json,
)


DEFAULT_OUTPUT_ROUTE = DEFAULT_DEMAND_DIR / f"background_routes_validated_{REFERENCE_SCREENLINE_LABEL}.rou.xml"
DEFAULT_FLOW_SUMMARY = PROJECT_ROOT / "results/metrics/validated_reference_screenline_demand/flow_summary.csv"
DEFAULT_SUMMARY_JSON = PROJECT_ROOT / "results/metrics/validated_reference_screenline_demand/summary.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build validated B0 demand directly from Toegye-ro reference screenline volumes.")
    parser.add_argument("--reference-csv", default=str(DEFAULT_REFERENCE_CSV))
    parser.add_argument("--net", default=str(DEFAULT_REPAIRED_NET))
    parser.add_argument("--output-route", default=str(DEFAULT_OUTPUT_ROUTE))
    parser.add_argument("--flow-summary-csv", default=str(DEFAULT_FLOW_SUMMARY))
    parser.add_argument("--summary-json", default=str(DEFAULT_SUMMARY_JSON))
    parser.add_argument("--variant-summary", default=str(DEFAULT_REFERENCE_SCREENLINE_VARIANT_SUMMARY))
    parser.add_argument("--duration-sec", type=float, default=7200.0)
    args = parser.parse_args()

    output_route = project_path(args.output_route)
    flow_rows, summary = build_reference_screenline_demand(
        project_path(args.reference_csv),
        project_path(args.net),
        output_route,
        duration_sec=args.duration_sec,
    )
    flow_fields = [
        "flow_id",
        "direction",
        "segment_scope",
        "vph",
        "vehicle_count",
        "route_edge_count",
        "start_edge",
        "target_edge",
    ]
    write_csv(project_path(args.flow_summary_csv), flow_rows, flow_fields)
    write_json(project_path(args.summary_json), {**summary, "flows": flow_rows})
    write_csv(
        project_path(args.variant_summary),
        [
            {
                "scale_label": REFERENCE_SCREENLINE_LABEL,
                "warmup_scale": 1.0,
                "sustain_scale": 1.0,
                "route_file": rel(output_route),
                "vehicle_count": summary["vehicle_count"],
                "vehicle_counts_by_cycle": "reference_screenline_7200s",
            }
        ],
        ["scale_label", "warmup_scale", "sustain_scale", "route_file", "vehicle_count", "vehicle_counts_by_cycle"],
    )
    print(f"wrote {rel(output_route)} vehicles={summary['vehicle_count']} flows={summary['flow_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
