#!/usr/bin/env python3
"""Build custom destination route candidates after point acceptance."""

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
    parser = argparse.ArgumentParser(description="Build accepted-destination route candidates.")
    parser.add_argument("--manifest", type=Path, default=custom.DEFAULT_MANIFEST)
    parser.add_argument("--point-acceptance", type=Path, default=custom.POINT_ACCEPTANCE_JSON)
    parser.add_argument("--output-csv", type=Path, default=custom.ROUTE_CANDIDATES_CSV)
    parser.add_argument("--output-html", type=Path, default=custom.ROUTE_REVIEW_HTML)
    parser.add_argument("--summary-json", type=Path, default=custom.CUSTOM_ROUTE_METRICS_DIR / "route_candidates_summary.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = vp.project_path(args.manifest)
        net_path = custom.manifest_net(manifest)
        acceptance = vp.project_path(args.point_acceptance)
        rows, summary = custom.build_route_candidates(net_path, acceptance)
        vp.write_csv(vp.project_path(args.output_csv), rows, custom.candidate_fields())
        vp.write_json(vp.project_path(args.summary_json), summary)
        custom.write_route_review_html(vp.project_path(args.output_html), custom.route_review_payload(rows, summary))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {vp.rel(vp.project_path(args.output_csv))}")
    print(f"wrote {vp.rel(vp.project_path(args.output_html))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

