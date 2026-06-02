#!/usr/bin/env python3
from __future__ import annotations

import argparse

from validated_pipeline import DEFAULT_LANE_OVERRIDES_CSV, DEFAULT_MAPPING_CSV, build_lane_overrides, project_path, read_csv, rel, write_csv, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Build edge lane override table from Toegye-ro mapping.")
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING_CSV))
    parser.add_argument("--output", default=str(DEFAULT_LANE_OVERRIDES_CSV))
    args = parser.parse_args()
    output = project_path(args.output)
    rows, summary = build_lane_overrides(read_csv(project_path(args.mapping)))
    write_csv(
        output,
        rows,
        [
            "edge_id",
            "target_lanes",
            "current_lanes",
            "lane_delta",
            "source_segment_ids",
            "source_directions",
            "source_row_count",
            "repair_reason",
        ],
    )
    write_json(output.with_suffix(".summary.json"), summary)
    print(f"wrote {rel(output)} overrides={len(rows)} changed={summary['changed_override_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
