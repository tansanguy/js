#!/usr/bin/env python3
from __future__ import annotations

import argparse

from validated_pipeline import DEFAULT_BASE_NET, DEFAULT_MAPPING_CSV, DEFAULT_REFERENCE_CSV, build_toegye_edge_mapping, project_path, rel, write_csv, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Toegye-ro segment to SUMO edge mapping for validated lane repair.")
    parser.add_argument("--reference-csv", default=str(DEFAULT_REFERENCE_CSV))
    parser.add_argument("--net", default=str(DEFAULT_BASE_NET))
    parser.add_argument("--output", default=str(DEFAULT_MAPPING_CSV))
    args = parser.parse_args()
    output = project_path(args.output)
    rows, summary = build_toegye_edge_mapping(project_path(args.reference_csv), project_path(args.net))
    write_csv(
        output,
        rows,
        [
            "segment_id",
            "direction",
            "edge_id",
            "edge_order",
            "axis_position",
            "matched_length_m",
            "segment_length_m",
            "match_ratio",
            "current_lanes",
            "target_lanes",
            "lane_delta",
            "repair_target",
            "repair_reason",
        ],
    )
    write_json(output.with_suffix(".summary.json"), summary)
    print(f"wrote {rel(output)} rows={len(rows)} repair_targets={summary['repair_target_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
