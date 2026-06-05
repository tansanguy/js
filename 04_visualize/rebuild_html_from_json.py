#!/usr/bin/env python3
"""Rebuild the B0/B2 animation HTML from an existing animation JSON.

No SUMO, no FCD, no re-simulation: this re-applies the traffic-light
augmentation (positions + per-mode signal-state timelines) to an already
extracted ``b0_b2_animation.json`` and re-renders the HTML. Use it to preview
the signal-light visualisation immediately, or after tweaking the TLS thresholds
(``--route-buffer-m`` / ``--stop-speed-kmh``).

For a full run from raw simulation output, use ``animate_b0_b2_progress.py``.
"""

import argparse
import json
from pathlib import Path

from config import HTML_OUTPUT_DIR
from utils.animation_builder import build_animated_dual_map_html
from utils.traffic_lights import augment_doc_with_tls, DEFAULT_TLS_GEOJSON


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild animation HTML from existing JSON")
    parser.add_argument("--json", type=Path, default=HTML_OUTPUT_DIR / "b0_b2_animation.json",
                        help="Existing animation JSON (from animate_b0_b2_progress.py)")
    parser.add_argument("--tls-geojson", type=Path, default=DEFAULT_TLS_GEOJSON)
    parser.add_argument("--route-buffer-m", type=float, default=60.0)
    parser.add_argument("--stop-speed-kmh", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=HTML_OUTPUT_DIR / "b0_b2_progress_animation.html")
    parser.add_argument("--title", default="B0 vs B2 응급차 진행 — 신호등 흐름")
    args = parser.parse_args()

    doc = json.loads(args.json.read_text(encoding="utf-8"))
    summary = augment_doc_with_tls(
        doc, args.tls_geojson,
        route_buffer_m=args.route_buffer_m, stop_speed_kmh=args.stop_speed_kmh,
    )
    args.json.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    build_animated_dual_map_html(doc, args.output, args.title)

    print(f"JSON : {args.json}")
    print(f"HTML : {args.output}")
    print(f"TLS  : {summary['tls_kept']}/{summary['tls_total']} on route, states {summary['per_mode']}")


if __name__ == "__main__":
    main()
