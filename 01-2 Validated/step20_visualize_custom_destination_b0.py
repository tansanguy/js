#!/usr/bin/env python3
"""Build HTML visualization for accepted custom destination B0 runs."""

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
    parser = argparse.ArgumentParser(description="Visualize custom destination B0 outputs.")
    parser.add_argument("--manifest", type=Path, default=custom.DEFAULT_MANIFEST)
    parser.add_argument("--accepted-routes", type=Path, default=custom.ACCEPTED_ROUTES_CSV)
    parser.add_argument("--latest-json", type=Path, default=None)
    parser.add_argument("--output-html", type=Path, default=custom.CUSTOM_B0_OVERVIEW_HTML)
    parser.add_argument("--summary-json", type=Path, default=custom.CUSTOM_ROUTE_METRICS_DIR / "custom_b0_visualization_summary.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = custom.visualization_payload(
            vp.project_path(args.manifest),
            vp.project_path(args.accepted_routes),
            vp.project_path(args.latest_json) if args.latest_json else None,
        )
        custom.write_visualization_html(vp.project_path(args.output_html), payload)
        vp.write_json(vp.project_path(args.summary_json), payload)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {vp.rel(vp.project_path(args.output_html))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

