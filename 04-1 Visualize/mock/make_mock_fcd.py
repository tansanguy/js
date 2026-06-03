#!/usr/bin/env python3
"""Generate mock SUMO FCD output for visualization development.

Produces files that match the contract in ``04_visualize/FCD_DATA_SPEC.md`` so the
parser / extractor / renderer can be built and validated *before* the real
simulation re-run exists. When real FCD arrives, only the input paths change.

Outputs (into ``04_visualize/mock/data/``):
  fcd_B0.xml            emergency (slow, full stops) + background, geo coords
  fcd_B2.xml            emergency (priority, no full stops) + background, geo coords
  signal_events_B2.csv  B2 controller events (schema subset used by viz)
"""

import csv
import math
from pathlib import Path
from xml.sax.saxutils import quoteattr

MOCK_DIR = Path(__file__).resolve().parent
DATA_DIR = MOCK_DIR / "data"

ROUTE_LENGTH_M = 2990.0
START_LAT, START_LON = 37.5500, 126.9700
EMERGENCY_DEPART = 600.0  # matches --emergency-depart 600
DT = 1.0

# Corridor geometry: (bearing_deg_from_north, segment_length_m). Sums to ROUTE_LENGTH_M.
SEGMENTS = [(70.0, 500.0), (55.0, 700.0), (80.0, 600.0), (50.0, 650.0), (75.0, 540.0)]

# Signalised intersections along the corridor (distance from start, fake ids).
INTERSECTIONS = [
    {"dist_m": 550.0, "tls_id": "cluster_A", "junction_id": "J_A"},
    {"dist_m": 1150.0, "tls_id": "cluster_B", "junction_id": "J_B"},
    {"dist_m": 1750.0, "tls_id": "cluster_C", "junction_id": "J_C"},
    {"dist_m": 2400.0, "tls_id": "cluster_D", "junction_id": "J_D"},
]

B2_PARAMS = {"D_det": 450, "alpha": 6, "G_ext": 51, "T_change_sec": 10}


# ---------------------------------------------------------------------------
# geo helpers
# ---------------------------------------------------------------------------
def offset(lat: float, lon: float, de_m: float, dn_m: float) -> tuple[float, float]:
    """Offset a lat/lon by east/north metres (small-distance flat approx)."""
    dlat = dn_m / 111320.0
    dlon = de_m / (111320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def build_polyline() -> list[tuple[float, float, float]]:
    """Return cumulative waypoints as (cum_dist_m, lat, lon)."""
    pts = [(0.0, START_LAT, START_LON)]
    lat, lon, cum = START_LAT, START_LON, 0.0
    for bearing, length in SEGMENTS:
        de = math.sin(math.radians(bearing)) * length
        dn = math.cos(math.radians(bearing)) * length
        lat, lon = offset(lat, lon, de, dn)
        cum += length
        pts.append((cum, lat, lon))
    return pts


_POLY = build_polyline()


def point_at_distance(d: float) -> tuple[float, float, float]:
    """Interpolate (lat, lon, bearing_deg) at distance ``d`` along the corridor."""
    d = max(0.0, min(d, ROUTE_LENGTH_M))
    for i in range(1, len(_POLY)):
        c0, lat0, lon0 = _POLY[i - 1]
        c1, lat1, lon1 = _POLY[i]
        if d <= c1 or i == len(_POLY) - 1:
            frac = 0.0 if c1 == c0 else (d - c0) / (c1 - c0)
            lat = lat0 + (lat1 - lat0) * frac
            lon = lon0 + (lon1 - lon0) * frac
            bearing = SEGMENTS[min(i - 1, len(SEGMENTS) - 1)][0]
            return lat, lon, bearing
    return _POLY[-1][1], _POLY[-1][2], SEGMENTS[-1][0]


# ---------------------------------------------------------------------------
# emergency speed simulation
# ---------------------------------------------------------------------------
def simulate_emergency(mode: str) -> list[dict]:
    """Step-integrate emergency motion. Returns per-second records with abs time."""
    if mode == "B0":
        cruise, accel, decel = 12.0, 2.5, 3.0
        full_stops, dwell = True, 14.0
        slow_zone_speed = 0.0
    else:  # B2 priority: rolls through, brief slowdown only
        cruise, accel, decel = 13.8, 2.8, 3.0
        full_stops, dwell = False, 0.0
        slow_zone_speed = 8.0

    passed = [False] * len(INTERSECTIONS)
    dwell_left = [0.0] * len(INTERSECTIONS)
    t, d, v = EMERGENCY_DEPART, 0.0, 0.0
    records: list[dict] = []
    guard = 0
    while d < ROUTE_LENGTH_M and guard < 100000:
        guard += 1
        target = cruise
        for idx, inter in enumerate(INTERSECTIONS):
            sx = inter["dist_m"]
            if passed[idx]:
                continue
            dist_to = sx - d
            if full_stops:
                brake_dist = (v * v) / (2 * decel) + 6.0
                if dwell_left[idx] > 0.0:  # currently stopped, waiting out red
                    target = 0.0
                    dwell_left[idx] -= DT
                    if dwell_left[idx] <= 0.0:
                        passed[idx] = True  # release -> accelerate away next step
                elif v < 0.3 and dist_to <= 10.0:  # arrived & halted -> start dwell
                    dwell_left[idx] = dwell
                    target = 0.0
                elif 0 <= dist_to <= brake_dist:  # approaching -> brake
                    target = 0.0
            else:
                if -25.0 <= dist_to <= 25.0:
                    target = slow_zone_speed
                if dist_to < -25.0:
                    passed[idx] = True
        # adjust speed toward target with accel/decel caps
        if target > v:
            v = min(target, v + accel * DT)
        else:
            v = max(target, v - decel * DT)
        v = max(0.0, min(v, cruise))
        d += v * DT
        lat, lon, bearing = point_at_distance(d)
        records.append({
            "time": round(t, 2), "lat": lat, "lon": lon,
            "speed": round(v, 3), "angle": round(bearing, 1),
            "dist": round(min(d, ROUTE_LENGTH_M), 2),
            "edge": _edge_for_distance(d),
        })
        t += DT
    return records


def _edge_for_distance(d: float) -> str:
    """Fake but stable edge id per corridor segment (for lane attr)."""
    cum = 0.0
    for i, (_, length) in enumerate(SEGMENTS):
        cum += length
        if d <= cum:
            return f"corridor_seg{i}"
    return f"corridor_seg{len(SEGMENTS) - 1}"


# ---------------------------------------------------------------------------
# background (side-street cross traffic + a couple corridor followers)
# ---------------------------------------------------------------------------
def simulate_background(em_records: list[dict]) -> dict[float, list[dict]]:
    """Return {abs_time: [vehicle dicts]} for background traffic."""
    by_time: dict[float, list[dict]] = {}
    t0 = em_records[0]["time"]
    t_end = em_records[-1]["time"]

    def add(t: float, veh: dict) -> None:
        by_time.setdefault(round(t, 2), []).append(veh)

    # Cross traffic at each intersection: a vehicle crossing perpendicular,
    # timed to be near the junction when the emergency vehicle is around there.
    for n, inter in enumerate(INTERSECTIONS):
        lat_c, lon_c, bearing = point_at_distance(inter["dist_m"])
        perp = bearing + 90.0
        de_u = math.sin(math.radians(perp))
        dn_u = math.cos(math.radians(perp))
        # when does emergency reach this intersection (approx)?
        reach = next((r["time"] for r in em_records if r["dist"] >= inter["dist_m"]), t_end)
        for k in range(3):  # a few cross vehicles per junction
            start = reach - 12 + k * 9
            for step in range(0, 24):
                t = start + step
                if not (t0 <= t <= t_end):
                    continue
                off = -45.0 + step * 4.0  # metres across the junction
                speed = 6.5
                lat, lon = offset(lat_c, lon_c, de_u * off, dn_u * off)
                add(t, {"id": f"bg_x{n}_{k}", "lat": lat, "lon": lon,
                        "speed": round(speed, 3), "angle": round(perp % 360, 1),
                        "edge": f"side_{inter['junction_id']}"})

    # Two slow followers on the corridor ahead of the emergency vehicle.
    for f, lead in enumerate([180.0, 420.0]):
        for r in em_records:
            d_ahead = r["dist"] + lead
            if d_ahead >= ROUTE_LENGTH_M:
                continue
            lat, lon, bearing = point_at_distance(d_ahead)
            add(r["time"], {"id": f"bg_c{f}", "lat": lat, "lon": lon,
                            "speed": 7.0, "angle": round(bearing, 1),
                            "edge": _edge_for_distance(d_ahead)})
    return by_time


# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------
def emergency_id(mode: str) -> str:
    param = "bo_top3_01_d450_a6_g51_t10" if mode == "B2" else "no_control"
    return f"emergency_FIRE_TO_SEOUL_STATION_{mode}_{param}_repeat_001"


def write_fcd(path: Path, mode: str, em_records: list[dict],
              bg_by_time: dict[float, list[dict]]) -> None:
    em_id = emergency_id(mode)
    em_by_time = {r["time"]: r for r in em_records}
    all_times = sorted(set(em_by_time) | set(bg_by_time))
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<fcd-export>"]
    for t in all_times:
        lines.append(f'    <timestep time="{t:.2f}">')
        r = em_by_time.get(t)
        if r:
            lines.append(_veh_xml(em_id, r, "b1_emergency_type"))
        for veh in bg_by_time.get(t, []):
            lines.append(_veh_xml(veh["id"], veh, "passenger"))
        lines.append("    </timestep>")
    lines.append("</fcd-export>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _veh_xml(vid: str, r: dict, vtype: str) -> str:
    # geo=true => x holds longitude, y holds latitude (SUMO convention).
    return (
        f'        <vehicle id={quoteattr(vid)} x="{r["lon"]:.6f}" y="{r["lat"]:.6f}" '
        f'angle="{r["angle"]:.1f}" type="{vtype}" speed="{r["speed"]:.3f}" '
        f'pos="0.00" lane="{r["edge"]}_0" slope="0.00" distance="{r.get("dist", 0.0):.2f}"/>'
    )


SIGNAL_COLUMNS = [
    "time", "mode", "parameter_id", "route_id", "vehicle_id", "tls_id", "junction_id",
    "remaining_distance_m", "D_det", "alpha", "G_ext", "T_change_sec",
    "phase_before", "phase_after", "action", "reason", "pass_time",
]


def write_signal_events(path: Path, em_records: list[dict]) -> None:
    """Mock B2 controller events: request_green at ~D_det, then pass at junction."""
    em_id = emergency_id("B2")
    rows: list[dict] = []
    for inter in INTERSECTIONS:
        sx = inter["dist_m"]
        d_det = B2_PARAMS["D_det"]
        req = next((r for r in em_records if r["dist"] >= sx - d_det), None)
        passing = next((r for r in em_records if r["dist"] >= sx), None)
        pass_time = passing["time"] if passing else ""
        base = {
            "mode": "B2", "parameter_id": "bo_top3_01_d450_a6_g51_t10",
            "route_id": "FIRE_TO_SEOUL_STATION", "vehicle_id": em_id,
            "tls_id": inter["tls_id"], "junction_id": inter["junction_id"],
            "D_det": d_det, "alpha": B2_PARAMS["alpha"], "G_ext": B2_PARAMS["G_ext"],
            "T_change_sec": B2_PARAMS["T_change_sec"], "pass_time": pass_time,
        }
        if req:
            rows.append({**base, "time": req["time"],
                         "remaining_distance_m": round(sx - req["dist"], 2),
                         "phase_before": 2, "phase_after": 4,
                         "action": "request_green",
                         "reason": "emergency_approaching;green_requested"})
        if passing:
            rows.append({**base, "time": passing["time"], "remaining_distance_m": 0.0,
                         "phase_before": 4, "phase_after": 4,
                         "action": "restore", "reason": "emergency_passed;restore_program"})
    rows.sort(key=lambda r: r["time"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SIGNAL_COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in SIGNAL_COLUMNS})


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for mode in ("B0", "B2"):
        em = simulate_emergency(mode)
        bg = simulate_background(em)
        out = DATA_DIR / f"fcd_{mode}.xml"
        write_fcd(out, mode, em, bg)
        travel = em[-1]["time"] - em[0]["time"]
        avg_kmh = (ROUTE_LENGTH_M / travel) * 3.6 if travel else 0.0
        bg_count = sum(len(v) for v in bg.values())
        print(f"{mode}: {len(em)} emergency steps, travel={travel:.0f}s, "
              f"avg={avg_kmh:.1f} km/h, {bg_count} background samples -> {out.name}")
    write_signal_events(DATA_DIR / "signal_events_B2.csv", simulate_emergency("B2"))
    print(f"signal_events_B2.csv written -> {DATA_DIR}")


if __name__ == "__main__":
    main()
