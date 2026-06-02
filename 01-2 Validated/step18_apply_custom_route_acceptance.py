#!/usr/bin/env python3
"""Apply route acceptance JSON and write runner-ready custom routes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import custom_destination_pipeline as custom  # noqa: E402
import validated_pipeline as vp  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply custom route acceptance.")
    parser.add_argument("--route-candidates", type=Path, default=custom.ROUTE_CANDIDATES_CSV)
    parser.add_argument("--route-acceptance", type=Path, default=custom.ROUTE_ACCEPTANCE_JSON)
    parser.add_argument("--output-csv", type=Path, default=custom.ACCEPTED_ROUTES_CSV)
    parser.add_argument("--output-xml", type=Path, default=custom.ACCEPTED_ROUTES_XML)
    parser.add_argument("--summary-json", type=Path, default=custom.CUSTOM_ROUTE_METRICS_DIR / "accepted_routes_summary.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rows, summary = custom.apply_route_acceptance(vp.project_path(args.route_candidates), vp.project_path(args.route_acceptance))
        vp.write_csv(vp.project_path(args.output_csv), rows, custom.accepted_route_fields())
        custom.write_accepted_route_xml(vp.project_path(args.output_xml), rows)
        vp.write_json(vp.project_path(args.summary_json), summary)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {vp.rel(vp.project_path(args.output_csv))}")
    print(f"wrote {vp.rel(vp.project_path(args.output_xml))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

