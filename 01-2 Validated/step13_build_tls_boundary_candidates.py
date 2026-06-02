#!/usr/bin/env python3
from __future__ import annotations

import argparse

from validated_pipeline import (
    CALIBRATED_NET_DIR,
    DEFAULT_MAPPING_CSV,
    DEFAULT_REPAIRED_NET,
    DEFAULT_TLS_BOUNDARY_CANDIDATE_SUMMARY,
    PROJECT_ROOT,
    build_tls_boundary_candidate_net,
    project_path,
    rel,
    tls_boundary_label,
    write_csv,
    write_json,
)


DEFAULT_TLS_AUDIT = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_spine_v2.csv"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "results/metrics/validated_tls_boundary_candidates"
GRID_UP = [0.0, 10.0, 20.0]
GRID_DOWN = [0.0, -10.0, -20.0]
GRID_OFFSET = [0.0, 15.0, 30.0, 45.0]
GRID_METERING = ["none", "mild", "medium"]
SMOKE_CANDIDATES = [(0.0, 0.0, 0.0, "none"), (10.0, -10.0, 15.0, "mild")]


def candidate_grid(smoke: bool) -> list[tuple[float, float, float, str]]:
    if smoke:
        return SMOKE_CANDIDATES
    return [(up, down, offset, metering) for up in GRID_UP for down in GRID_DOWN for offset in GRID_OFFSET for metering in GRID_METERING]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build TLS/boundary calibrated B0 net candidates.")
    parser.add_argument("--base-net", default=str(DEFAULT_REPAIRED_NET))
    parser.add_argument("--tls-audit", default=str(DEFAULT_TLS_AUDIT))
    parser.add_argument("--mapping-csv", default=str(DEFAULT_MAPPING_CSV))
    parser.add_argument("--summary-csv", default=str(DEFAULT_TLS_BOUNDARY_CANDIDATE_SUMMARY))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--smoke", action="store_true", help="Build only the baseline and one calibrated candidate.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    rows = []
    report_dir = project_path(args.report_dir)
    candidates = candidate_grid(args.smoke)
    if args.limit:
        candidates = candidates[: args.limit]
    for index, (up_delta, down_delta, offset, metering) in enumerate(candidates, start=1):
        label = tls_boundary_label(up_delta, down_delta, offset, metering)
        output_net = CALIBRATED_NET_DIR / f"jungbu_ellipse_passenger_speed50_lanes_repaired_{label}.net.xml"
        work_dir = report_dir / "plain_work" / label
        summary = build_tls_boundary_candidate_net(
            project_path(args.base_net),
            output_net,
            work_dir,
            project_path(args.tls_audit),
            project_path(args.mapping_csv),
            up_delta,
            down_delta,
            offset,
            metering,
        )
        summary_json = report_dir / f"{label}.json"
        write_json(summary_json, summary)
        rows.append(
            {
                "candidate_id": label,
                "candidate_index": index,
                "net_file": rel(output_net),
                "summary_json": rel(summary_json),
                "up_delta_sec": up_delta,
                "down_delta_sec": down_delta,
                "offset_sec": offset,
                "boundary_metering": metering,
                "changed_phase_count": summary["tls_summary"]["changed_phase_count"],
                "metered_edge_count": summary["edge_summary"]["metered_edge_count"],
            }
        )
        write_csv(project_path(args.summary_csv), rows, CANDIDATE_FIELDS)
    print(f"wrote {rel(project_path(args.summary_csv))} rows={len(rows)}")
    return 0


CANDIDATE_FIELDS = [
    "candidate_id",
    "candidate_index",
    "net_file",
    "summary_json",
    "up_delta_sec",
    "down_delta_sec",
    "offset_sec",
    "boundary_metering",
    "changed_phase_count",
    "metered_edge_count",
]


if __name__ == "__main__":
    raise SystemExit(main())
