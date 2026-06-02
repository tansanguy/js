#!/usr/bin/env python3
"""Build the B0 vs B2 emergency-progress animation (extract FCD -> animated HTML).

Run with no args to use the mock fixtures, or pass real run directories:

  python animate_b0_b2_progress.py \
      --b0-fcd runs/.../B0/.../fcd.xml \
      --b2-fcd runs/.../B2/.../fcd.xml \
      --b2-signals runs/.../B2/.../signal_events.csv
"""

import argparse
import json
from pathlib import Path

from config import HTML_OUTPUT_DIR
from extract_emergency_fcd import (
    MOCK_DIR,
    build_mode_payload,
    load_signal_events,
    _bounds,
    B2_PARAMS,
)
from config import SEOUL_STATION_ROUTE_ID, SEOUL_STATION_ROUTE_LENGTH_M
from utils import parse_fcd
from utils.animation_builder import build_animated_dual_map_html


def main() -> None:
    parser = argparse.ArgumentParser(description="Build B0 vs B2 progress animation HTML")
    parser.add_argument("--b0-fcd", type=Path, default=MOCK_DIR / "fcd_B0.xml")
    parser.add_argument("--b2-fcd", type=Path, default=MOCK_DIR / "fcd_B2.xml")
    parser.add_argument("--b2-signals", type=Path, default=MOCK_DIR / "signal_events_B2.csv")
    parser.add_argument("--bg-radius-m", type=float, default=250.0)
    parser.add_argument("--json-output", type=Path, default=HTML_OUTPUT_DIR / "b0_b2_animation.json")
    parser.add_argument("--output", type=Path, default=HTML_OUTPUT_DIR / "b0_b2_progress_animation.html")
    parser.add_argument("--title", default="B0 vs B2 응급차 진행 비교 — 서울역 경로")
    args = parser.parse_args()

    b0 = parse_fcd(args.b0_fcd, mode="B0")
    b2 = parse_fcd(args.b2_fcd, mode="B2")
    b0_payload = build_mode_payload(b0, args.bg_radius_m)
    b2_payload = build_mode_payload(b2, args.bg_radius_m)
    b2_payload["signal_events"] = load_signal_events(args.b2_signals, b2)

    doc = {
        "meta": {
            "route_id": SEOUL_STATION_ROUTE_ID,
            "route_length_m": SEOUL_STATION_ROUTE_LENGTH_M,
            "b2_params": B2_PARAMS,
            "bg_radius_m": args.bg_radius_m,
            "bounds": _bounds([b0_payload, b2_payload]),
        },
        "modes": {"B0": b0_payload, "B2": b2_payload},
    }

    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    build_animated_dual_map_html(doc, args.output, args.title)

    print(f"JSON : {args.json_output}")
    print(f"HTML : {args.output}")
    for name, p in (("B0", b0_payload), ("B2", b2_payload)):
        extra = f", signals={len(p.get('signal_events', []))}" if name == "B2" else ""
        print(f"  {name}: {len(p['emergency'])} steps, travel={p['travel_time_sec']}s, "
              f"avg={p['avg_speed_kmh']} km/h, bg_snaps={len(p['background'])}{extra}")


if __name__ == "__main__":
    main()
