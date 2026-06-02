#!/usr/bin/env python3
"""Run B0 for accepted custom destination routes."""

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
    parser = argparse.ArgumentParser(description="Run custom destination B0 with FCD output.")
    parser.add_argument("--manifest", type=Path, default=custom.DEFAULT_MANIFEST)
    parser.add_argument("--accepted-routes", type=Path, default=custom.ACCEPTED_ROUTES_CSV)
    parser.add_argument("--output-prefix", default=custom.CUSTOM_B0_PREFIX)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = custom.run_custom_b0(vp.project_path(args.manifest), vp.project_path(args.accepted_routes), args.output_prefix)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"returncode {summary['returncode']}")
    print(f"wrote {vp.rel(custom.CUSTOM_B0_RUN_SUMMARY)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

