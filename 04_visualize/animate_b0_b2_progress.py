#!/usr/bin/env python3
"""Build the B0 vs B2 emergency-progress animation (extract FCD -> animated HTML).

Run with no args to use the mock fixtures, or pass real run directories:

  python animate_b0_b2_progress.py \
      --b0-fcd runs/.../B0/.../fcd.xml \
      --b2-fcd runs/.../B2/.../fcd.xml \
      --b2-signals runs/.../B2/.../signal_events.csv
"""

import argparse
import csv
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
from utils.traffic_lights import (
    augment_doc_with_tls,
    DEFAULT_ROUTE_TLS_GEOJSON,
    CONTROL_ACTIONS,
)
from utils.road_network import augment_doc_with_lanes, DEFAULT_LANES_GEOJSON


def build_control_history(signals_csv: Path, net_file: Path) -> dict[str, dict[str, object]]:
    """Group real B4 control events by runtime tls_id, with net coordinates.

    Returns ``{tls_id: {"events": [{"time","action_type"}...], "lat","lon"}}``
    for the lights the B4 runtime actually controlled (CONTROL_ACTIONS rows).
    The coordinates come from the SUMO net (the runtime tls_id is a TLS id there)
    so traffic_lights.augment_doc_with_tls can position-match them onto the
    on-route geojson lights, whose ids live in a different namespace.
    """
    if not signals_csv or not Path(signals_csv).exists():
        return {}
    grouped: dict[str, list[dict[str, str]]] = {}
    with Path(signals_csv).open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("action_type") not in CONTROL_ACTIONS:
                continue
            tid = (row.get("tls_id") or "").strip()
            if not tid:
                continue
            grouped.setdefault(tid, []).append(
                {"time": row.get("time", ""), "action_type": row.get("action_type", "")}
            )
    if not grouped:
        return {}

    import sumolib  # local import; only needed for the real-history path

    net = sumolib.net.readNet(str(net_file))
    history: dict[str, dict[str, object]] = {}
    for tid, events in grouped.items():
        try:
            tls = net.getTLS(tid)
        except KeyError:
            continue
        xs, ys = [], []
        for conn in tls.getConnections():
            x, y = conn[0].getShape()[-1]  # stop-line end of each controlled lane
            xs.append(x)
            ys.append(y)
        if not xs:
            continue
        lon, lat = net.convertXY2LonLat(sum(xs) / len(xs), sum(ys) / len(ys))
        history[tid] = {"events": events, "lat": round(lat, 6), "lon": round(lon, 6)}
    return history


def _tls_link_coords_from_net(
    net_file: Path, keys: set[tuple[str, str]]
) -> dict[tuple[str, str], tuple[float, float]]:
    """Resolve ``{(tls_id, link_index): (lat, lon)}`` from SUMO net stop-lines.

    Used when a ``tls_states.csv`` dump omits lat/lon columns: each controlled
    link's incoming-lane stop line (its last shape point) is the signal position.
    Read-only: this only *reads* the static net file, like build_control_history.
    """
    import sumolib  # local import; only needed for the net-fallback path

    want: dict[str, set[str]] = {}
    for tid, link in keys:
        want.setdefault(tid, set()).add(link)
    net = sumolib.net.readNet(str(net_file))
    out: dict[tuple[str, str], tuple[float, float]] = {}
    for tid, links in want.items():
        try:
            tls = net.getTLS(tid)
        except KeyError:
            continue
        per_link: dict[str, tuple[float, float]] = {}
        all_xy: list[tuple[float, float]] = []
        for conn in tls.getConnections():
            x, y = conn[0].getShape()[-1]  # incoming-lane stop line
            per_link.setdefault(str(conn[2]), (x, y))
            all_xy.append((x, y))
        if not all_xy:
            continue
        # Junction-level fallback for link indices the net does not expose as a
        # vehicle connection (e.g. pedestrian-crossing link indices): average the
        # TLS's controlled stop-lines so the icon still lands on the junction.
        fx = sum(p[0] for p in all_xy) / len(all_xy)
        fy = sum(p[1] for p in all_xy) / len(all_xy)
        for link in links:
            x, y = per_link.get(link, (fx, fy))
            lon, lat = net.convertXY2LonLat(x, y)
            out[(tid, link)] = (round(lat, 6), round(lon, 6))
    return out


def build_tls_dump_history(tls_csv: Path, net_file: Path | None = None) -> dict[str, dict[str, object]]:
    """Load the real per-step TLS state dump (tls_states.csv).

    Returns ``{key: {"events": [{"time","state"}...], "kind": "tls_dump",
    "lat","lon"}}`` — the authoritative EV-facing signal-colour timeline for every
    on-route MOVEMENT the simulation recorded. The unit is (tls_id, link_index),
    not tls_id, because one runtime TLS can control several on-route stop-lines
    (a junction the route crosses twice); each gets its own stop-line lat/lon so
    it matches the correct icon downstream.

    Coordinates come from the dump's own ``lat``/``lon`` columns when present.
    Some dumps omit them; in that case we recover each movement's stop-line
    position from the SUMO net via ``(tls_id, link_index)`` using ``net_file``.
    This keeps the real signal-dump mode working (all on-route lights replay
    real colours) without re-running the simulation.
    """
    if not tls_csv or not Path(tls_csv).exists():
        return {}
    grouped: dict[str, dict[str, object]] = {}
    with Path(tls_csv).open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            tid = (row.get("tls_id") or "").strip()
            if not tid:
                continue
            link = (row.get("link_index") or "0").strip()
            key = f"{tid}#{link}"
            entry = grouped.get(key)
            if entry is None:
                try:
                    lat = float(row["lat"]); lon = float(row["lon"])
                except (TypeError, ValueError, KeyError):
                    lat = lon = None
                entry = {"events": [], "kind": "tls_dump", "lat": lat, "lon": lon,
                         "tls_id": tid, "link_index": link}
                grouped[key] = entry
            entry["events"].append({"time": row.get("time", ""), "state": row.get("state", "")})
    # Recover any coordinates the dump did not carry from the SUMO net (a static
    # input), so dumps without lat/lon columns still geolocate every movement.
    missing = [e for e in grouped.values() if e.get("lat") is None]
    if missing and net_file is not None and Path(net_file).exists():
        coords = _tls_link_coords_from_net(
            net_file, {(str(e["tls_id"]), str(e["link_index"])) for e in missing}
        )
        for e in missing:
            ll = coords.get((str(e["tls_id"]), str(e["link_index"])))
            if ll is not None:
                e["lat"], e["lon"] = ll
    # Drop any movement we still could not geolocate.
    return {k: v for k, v in grouped.items() if v.get("lat") is not None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build B0 vs B2 progress animation HTML")
    parser.add_argument("--b0-fcd", type=Path, default=MOCK_DIR / "fcd_B0.xml")
    parser.add_argument("--b2-fcd", type=Path, default=MOCK_DIR / "fcd_B2.xml")
    parser.add_argument("--b2-signals", type=Path, default=MOCK_DIR / "signal_events_B2.csv")
    parser.add_argument("--bg-radius-m", type=float, default=250.0)
    parser.add_argument("--tls-geojson", type=Path, default=DEFAULT_ROUTE_TLS_GEOJSON,
                        help="On-route TLS positions for the signal-light icons "
                             "(authoritative corridor subset from export_route_tls.py)")
    parser.add_argument("--route-buffer-m", type=float, default=60.0)
    parser.add_argument("--stop-speed-kmh", type=float, default=5.0)
    parser.add_argument("--approach-m", type=float, default=45.0,
                        help="Activate a TLS icon when the EV is within this many "
                             "metres ahead of it. Increase to light up the next / "
                             "next-next signals the B4 algorithm controls downstream.")
    parser.add_argument("--exit-m", type=float, default=15.0,
                        help="Keep a TLS icon active until the EV is this many "
                             "metres past it.")
    parser.add_argument("--edges-geojson", type=Path, default=DEFAULT_LANES_GEOJSON,
                        help="Per-lane geometry drawn as parallel lanes under the vehicles")
    parser.add_argument("--lane-buffer-m", type=float, default=200.0,
                        help="Keep lanes within this distance of the route")
    parser.add_argument("--net-file", type=Path, default=None,
                        help="SUMO net for the B2/B4 run. Required with "
                             "--b2-control-history to position-match runtime TLS ids.")
    parser.add_argument("--b2-control-history", action="store_true",
                        help="Use the REAL B4 control timeline from --b2-signals for "
                             "the B2 (B4) lights instead of the motion proxy. The B0 "
                             "(B04) lights always use the proxy (no control there).")
    parser.add_argument("--b0-tls-states", type=Path, default=None,
                        help="Real per-step TLS state dump (tls_states.csv) for the "
                             "B0/B04 run. When given, ALL on-route lights replay the "
                             "actual SUMO signal colours instead of the motion proxy.")
    parser.add_argument("--b2-tls-states", type=Path, default=None,
                        help="Real per-step TLS state dump (tls_states.csv) for the "
                             "B2/B4 run. Authoritative signal timeline for every light.")
    parser.add_argument("--json-output", type=Path, default=HTML_OUTPUT_DIR / "b0_b2_animation.json")
    parser.add_argument("--output", type=Path, default=HTML_OUTPUT_DIR / "b0_b2_progress_animation.html")
    parser.add_argument(
        "--basemap",
        choices=["carto_light", "carto_light_nolabels", "carto_dark", "osm", "none"],
        default="carto_light",
        help="Background map: carto_light=clean map with road/district labels, no "
             "shop POIs/buildings (default); none=solid canvas + SUMO geometry only")
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
    # Real per-step TLS state dump (preferred) -> per-mode history.
    # Falls back to B4 control-event history, then to the motion proxy.
    control_history: dict[str, dict[str, object]] = {}
    control_modes: tuple[str, ...] = ()
    if args.b0_tls_states or args.b2_tls_states:
        if args.net_file is None:
            parser.error("--b0-tls-states/--b2-tls-states require --net-file")
        control_history = {
            "B0": build_tls_dump_history(args.b0_tls_states, args.net_file) if args.b0_tls_states else {},
            "B2": build_tls_dump_history(args.b2_tls_states, args.net_file) if args.b2_tls_states else {},
        }
    elif args.b2_control_history:
        if args.net_file is None:
            parser.error("--b2-control-history requires --net-file")
        control_history = build_control_history(args.b2_signals, args.net_file)
        control_modes = ("B2",)

    tls_summary = augment_doc_with_tls(
        doc, args.tls_geojson,
        route_buffer_m=args.route_buffer_m, stop_speed_kmh=args.stop_speed_kmh,
        approach_m=args.approach_m, exit_m=args.exit_m,
        control_history=control_history, control_modes=control_modes,
    )
    lanes_summary = augment_doc_with_lanes(doc, args.edges_geojson, buffer_m=args.lane_buffer_m)

    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    build_animated_dual_map_html(doc, args.output, args.title, basemap=args.basemap)

    print(f"JSON : {args.json_output}")
    print(f"HTML : {args.output}")
    for name, p in (("B0", b0_payload), ("B2", b2_payload)):
        extra = f", signals={len(p.get('signal_events', []))}" if name == "B2" else ""
        print(f"  {name}: {len(p['emergency'])} steps, travel={p['travel_time_sec']}s, "
              f"avg={p['avg_speed_kmh']} km/h, bg_snaps={len(p['background'])}{extra}")
    print(f"  TLS: {tls_summary['tls_kept']}/{tls_summary['tls_total']} on route, "
          f"states {tls_summary['per_mode']}")
    if args.b0_tls_states or args.b2_tls_states or args.b2_control_history:
        print(f"  signal source: {doc['meta']['tls_approx']['method']}, "
              f"matched {tls_summary['control_matched']}, used {tls_summary['control_used']}")
    print(f"  lanes: {lanes_summary['lanes_kept']} road edges near route")


if __name__ == "__main__":
    main()
