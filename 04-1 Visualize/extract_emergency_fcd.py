#!/usr/bin/env python3
"""Extract animation-ready JSON from FCD output (B0 vs BO-optimal B2).

Reads ``fcd.xml`` (and B2's ``signal_events.csv``), separates the emergency
vehicle from background traffic, aligns both modes on relative time (``t_rel``
from emergency depart), keeps only background vehicles inside a radius of the
emergency vehicle (the follow-camera region), anchors signal events to the
emergency position, and writes a single JSON consumed by the animated map.

Works identically on the mock fixtures (``mock/data/``) and on real run output;
only the input paths differ. See ``FCD_DATA_SPEC.md`` for the schema.
"""

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from config import (
    HTML_OUTPUT_DIR,
    SEOUL_STATION_ROUTE_ID,
    SEOUL_STATION_ROUTE_LENGTH_M,
)
from utils import parse_fcd
from utils.fcd_parser import FcdResult

MOCK_DIR = Path(__file__).resolve().parent / "mock" / "data"
B2_PARAMS = {"D_det": 450, "alpha": 6, "G_ext": 51, "T_change_sec": 10}


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth metre distance (fine for sub-km separations)."""
    dlat = (lat2 - lat1) * 111320.0
    dlon = (lon2 - lon1) * 111320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


def _emergency_pos_by_time(fcd: FcdResult) -> dict[float, tuple[float, float]]:
    return {p.time: (p.lat, p.lon) for p in fcd.emergency.points}


def _interp_pos(points: list, t: float) -> tuple[float, float]:
    """Linear-interpolate (lat, lon) along the emergency trajectory at abs time t."""
    if not points:
        return (0.0, 0.0)
    if t <= points[0].time:
        return (points[0].lat, points[0].lon)
    if t >= points[-1].time:
        return (points[-1].lat, points[-1].lon)
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        if a.time <= t <= b.time:
            span = b.time - a.time
            f = 0.0 if span == 0 else (t - a.time) / span
            return (a.lat + (b.lat - a.lat) * f, a.lon + (b.lon - a.lon) * f)
    return (points[-1].lat, points[-1].lon)


def build_mode_payload(fcd: FcdResult, bg_radius_m: float) -> dict[str, Any]:
    """Assemble the per-mode payload (emergency series, filtered background, meta)."""
    em = fcd.emergency
    pts = em.points
    anchor = em.start_time
    em_pos_at = _emergency_pos_by_time(fcd)

    # Cumulative distance from the lat/lon path. SUMO's FCD ``distance`` attr is
    # the lane position (not a route odometer) so we integrate the geometry.
    cum = 0.0
    cum_dist: list[float] = []
    prev = None
    for p in pts:
        if prev is not None:
            cum += meters_between(prev.lat, prev.lon, p.lat, p.lon)
        cum_dist.append(cum)
        prev = p

    # Per-step instantaneous speed is taken straight from FCD (accurate); only
    # the cumulative distance is normalised to the official route length so that
    # progress % and avg speed match the authoritative experiment metric (the raw
    # lat/lon integral overshoots ~30% from per-step coordinate jitter).
    cum_total = cum_dist[-1] if cum_dist else 0.0
    route_len = SEOUL_STATION_ROUTE_LENGTH_M

    def norm(d: float) -> float:
        return round(d / cum_total * route_len, 2) if cum_total else 0.0

    emergency = [{
        "t_rel": round(p.time - anchor, 2),
        "lat": round(p.lat, 6),
        "lon": round(p.lon, 6),
        "speed_kmh": round(p.speed_kmh, 2),
        "angle": round(p.angle, 1),
        "dist_m": norm(cum_dist[i]),
        "edge": p.edge_id,
    } for i, p in enumerate(pts)]

    # Keep only background vehicles within bg_radius of the emergency vehicle.
    background = []
    for snap in fcd.background:
        t = snap["time"]
        ref = em_pos_at.get(t)
        if ref is None:
            continue  # no emergency sample this step (e.g. before depart)
        elat, elon = ref
        near = [{
            "lat": round(v["lat"], 6),
            "lon": round(v["lon"], 6),
            "speed_kmh": v["speed_kmh"],
            "angle": v["angle"],
        } for v in snap["vehicles"]
            if meters_between(elat, elon, v["lat"], v["lon"]) <= bg_radius_m]
        if near:
            background.append({"t_rel": round(t - anchor, 2), "vehicles": near})

    speeds = [p.speed_kmh for p in pts]
    travel = round(em.total_travel_time_sec, 2)
    dist = route_len if cum_total else 0.0
    avg_kmh = round((dist / travel) * 3.6, 2) if travel else 0.0

    return {
        "mode": fcd.mode,
        "emergency_id": fcd.emergency_id,
        "travel_time_sec": travel,
        "avg_speed_kmh": avg_kmh,
        "max_speed_kmh": round(max(speeds), 2) if speeds else 0.0,
        "distance_m": round(dist, 2),
        "depart_time_sec": anchor,
        "emergency": emergency,
        "background": background,
        "route_polyline": [[round(p.lat, 6), round(p.lon, 6)] for p in pts],
    }


def load_signal_events(path: Path, fcd: FcdResult) -> list[dict[str, Any]]:
    """Parse signal_events.csv and anchor each event to the emergency position."""
    if not path or not Path(path).exists():
        return []
    anchor = fcd.emergency.start_time
    pts = fcd.emergency.points
    events: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            # Keep only real TLS control events; skip depart/analysis pseudo-events.
            if not row.get("tls_id"):
                continue
            try:
                t = float(row.get("time", ""))
            except (TypeError, ValueError):
                continue
            lat, lon = _interp_pos(pts, t)
            events.append({
                "t_rel": round(t - anchor, 2),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "tls_id": row.get("tls_id", ""),
                "junction_id": row.get("junction_id", ""),
                "action": row.get("action", ""),
                "reason": row.get("reason", ""),
                "remaining_distance_m": _to_float(row.get("remaining_distance_m")),
            })
    events.sort(key=lambda e: e["t_rel"])
    return events


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _bounds(payloads: list[dict[str, Any]]) -> dict[str, float]:
    lats, lons = [], []
    for p in payloads:
        for lat, lon in p["route_polyline"]:
            lats.append(lat)
            lons.append(lon)
    if not lats:
        return {}
    return {"min_lat": min(lats), "max_lat": max(lats),
            "min_lon": min(lons), "max_lon": max(lons),
            "center_lat": (min(lats) + max(lats)) / 2,
            "center_lon": (min(lons) + max(lons)) / 2}


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract B0/B2 FCD into animation JSON")
    parser.add_argument("--b0-fcd", type=Path, default=MOCK_DIR / "fcd_B0.xml")
    parser.add_argument("--b2-fcd", type=Path, default=MOCK_DIR / "fcd_B2.xml")
    parser.add_argument("--b2-signals", type=Path, default=MOCK_DIR / "signal_events_B2.csv")
    parser.add_argument("--bg-radius-m", type=float, default=250.0,
                        help="Keep background vehicles within this radius of the emergency vehicle")
    parser.add_argument("--output", type=Path, default=HTML_OUTPUT_DIR / "b0_b2_animation.json")
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

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {args.output}")
    for name, p in (("B0", b0_payload), ("B2", b2_payload)):
        print(f"  {name}: {len(p['emergency'])} steps, travel={p['travel_time_sec']}s, "
              f"avg={p['avg_speed_kmh']} km/h, bg_snaps={len(p['background'])}"
              + (f", signal_events={len(p.get('signal_events', []))}" if name == "B2" else ""))


if __name__ == "__main__":
    main()
