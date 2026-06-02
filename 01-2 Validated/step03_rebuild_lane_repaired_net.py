#!/usr/bin/env python3
from __future__ import annotations

import argparse

from validated_pipeline import DEFAULT_BASE_NET, DEFAULT_LANE_OVERRIDES_CSV, DEFAULT_REPAIRED_NET, PROJECT_ROOT, rebuild_lane_repaired_net, rel, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild lane-repaired SUMO net via netconvert plain round-trip.")
    parser.add_argument("--base-net", default=str(DEFAULT_BASE_NET))
    parser.add_argument("--lane-overrides", default=str(DEFAULT_LANE_OVERRIDES_CSV))
    parser.add_argument("--output-net", default=str(DEFAULT_REPAIRED_NET))
    parser.add_argument("--work-dir", default=str(PROJECT_ROOT / "data_prepared/validated/net/plain_work"))
    args = parser.parse_args()
    from validated_pipeline import project_path

    output = project_path(args.output_net)
    report = rebuild_lane_repaired_net(project_path(args.base_net), project_path(args.lane_overrides), output, project_path(args.work_dir))
    report_path = output.with_name("lane_repair_report.json")
    write_json(report_path, report)
    print(f"wrote {rel(output)} report={rel(report_path)} changed={report['rewrite_summary']['changed_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
