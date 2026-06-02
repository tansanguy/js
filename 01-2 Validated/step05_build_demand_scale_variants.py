#!/usr/bin/env python3
from __future__ import annotations

import argparse

from validated_pipeline import DEFAULT_BASE_DEMAND, DEFAULT_DEMAND_DIR, DEFAULT_SCALE_SUMMARY, build_demand_variants, parse_scale_pairs, project_path, rel, write_csv, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Build validated sustained demand scale variants.")
    parser.add_argument("--base-route", default=str(DEFAULT_BASE_DEMAND))
    parser.add_argument("--output-dir", default=str(DEFAULT_DEMAND_DIR))
    parser.add_argument("--summary-csv", default=str(DEFAULT_SCALE_SUMMARY))
    parser.add_argument("--scale", action="append", help="Scale pair as warmup/sustain, e.g. 0.40/0.15. Defaults to the validated grid.")
    parser.add_argument("--sampling-seed", default="validated_scale_grid_v1")
    args = parser.parse_args()
    rows, summary = build_demand_variants(project_path(args.base_route), project_path(args.output_dir), parse_scale_pairs(args.scale), args.sampling_seed)
    summary_csv = project_path(args.summary_csv)
    write_csv(summary_csv, rows, ["scale_label", "warmup_scale", "sustain_scale", "route_file", "vehicle_count", "vehicle_counts_by_cycle"])
    write_json(summary_csv.with_suffix(".json"), summary)
    print(f"wrote {rel(summary_csv)} variants={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
