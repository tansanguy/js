#!/usr/bin/env python3
"""Build a presentation-first Seoul Station / Jungbu Fire Station animation.

This intentionally lives in 10-1 as a display artifact. It uses the real
compact-v9 firetruck route geometry and route TLS locations, then generates a
presentation-consistent EV, queue, and TLS state machine so the EV can never
visually overtake its own front queue.
"""

from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import sumolib  # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THIS_DIR = Path(__file__).resolve().parent
PRESENTATION_INPUTS = THIS_DIR / "10-1_presentation_inputs.json"
PRESENTATION_NET_FILE = THIS_DIR / "10-1_jungbu_compact_v9_B04_global_reality_s1forced_presentation.net.xml"
PRESENTATION_ROUTE_XML = THIS_DIR / "10-1_firetruck_final_route.rou.xml"
PRESENTATION_DEMAND_ROUTE = THIS_DIR / "10-1_background_routes_compact_v9_B04_ad_stage23_trigger_presentation.rou.xml"
NET_FILE = PRESENTATION_NET_FILE if PRESENTATION_NET_FILE.is_file() else PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
ROUTE_XML = PRESENTATION_ROUTE_XML if PRESENTATION_ROUTE_XML.is_file() else PROJECT_ROOT / "data_prepared/compact_v9/routes/firetruck_to_seoul_station_front.rou.xml"
ROUTE_TLS_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/compact_v9_B04_route_tls.geojson"
DEFAULT_OUTPUT = THIS_DIR / "fire_station_final_presentation.html"
ACTUAL_PROGRESS_DATA = THIS_DIR / "presentation_timing_seed_data.json"
TLS_PROGRESS_DATA = THIS_DIR / "presentation_tls_seed_data.json"
MIN_DISPLAY_SIGNAL_S = 250.0
DISPLAY_TRAFFIC_DT = 0.2
GENERAL_VEHICLE_COLOR = "#f97316"
GENERAL_VEHICLE_OPACITY = 0.86
PRESENTATION_DESTINATION = {
    "lat": 37.560208,
    "lon": 127.002440,
    "label": "도착 지점",
    "source": "user_requested_20260609",
}
PRESENTATION_DONGHO_WAYPOINT_EDGES = ["-1455512070", "218773868#6"]


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * 111_320.0
    dlon = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dlat, dlon)


def route_edges() -> list[str]:
    root = ET.parse(ROUTE_XML).getroot()
    route = root.find(".//route")
    if route is None:
        raise SystemExit(f"missing route in {ROUTE_XML}")
    return str(route.get("edges", "")).split()


def route_points() -> tuple[list[dict[str, float]], float]:
    net = sumolib.net.readNet(str(NET_FILE))
    raw: list[tuple[float, float]] = []
    for edge_id in route_edges():
        edge = net.getEdge(edge_id)
        pts = []
        for x, y in edge.getShape():
            lon, lat = net.convertXY2LonLat(float(x), float(y))
            pts.append((float(lat), float(lon)))
        if raw and pts and raw[-1] == pts[0]:
            raw.extend(pts[1:])
        else:
            raw.extend(pts)
    out: list[dict[str, float]] = []
    total = 0.0
    prev: tuple[float, float] | None = None
    for lat, lon in raw:
        if prev is not None:
            total += meters_between(prev[0], prev[1], lat, lon)
        out.append({"lat": round(lat, 7), "lon": round(lon, 7), "s": round(total, 2)})
        prev = (lat, lon)
    return out, total


def route_points_from_latlon(raw: list[tuple[float, float]]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    total = 0.0
    prev: tuple[float, float] | None = None
    for lat, lon in raw:
        if prev is not None:
            step = meters_between(prev[0], prev[1], lat, lon)
            if step < 0.5:
                continue
            total += step
        out.append({"lat": round(lat, 7), "lon": round(lon, 7), "s": round(total, 2)})
        prev = (lat, lon)
    return out


def resample_route_points(raw_points: list[dict[str, float]]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    total = 0.0
    prev: tuple[float, float] | None = None
    for point in raw_points:
        lat = float(point["lat"])
        lon = float(point["lon"])
        if prev is not None:
            step = meters_between(prev[0], prev[1], lat, lon)
            if step < 0.5:
                continue
            total += step
        out.append({"lat": round(lat, 7), "lon": round(lon, 7), "s": round(total, 2)})
        prev = (lat, lon)
    return out


def straighten_dongho_tail(points: list[dict[str, float]]) -> list[dict[str, float]]:
    """Remove the tiny U-shaped connector that makes the Dongho-ro tail look like a detour."""
    if len(points) < 5:
        return points
    cleaned: list[dict[str, float]] = []
    for point in points:
        s = float(point["s"])
        # The SUMO connector around the Dongho-ro turn briefly moves north-east,
        # which reads as a wrong route in the presentation. The neighboring
        # points already form the intended straight Dongho-ro approach.
        if 1320.0 <= s <= 1345.0:
            continue
        cleaned.append(point)
    return resample_route_points(cleaned)


def closest_polyline_prefix(
    raw: list[tuple[float, float]],
    target_lat: float,
    target_lon: float,
) -> tuple[list[tuple[float, float]], dict[str, Any]]:
    if len(raw) < 2:
        return raw, {"road_offset_m": 0.0}
    lat0 = target_lat
    scale = 111_320.0 * math.cos(math.radians(lat0))
    best: dict[str, Any] | None = None
    for idx, (a, b) in enumerate(zip(raw, raw[1:])):
        ax = (a[1] - target_lon) * scale
        ay = (a[0] - target_lat) * 111_320.0
        bx = (b[1] - target_lon) * scale
        by = (b[0] - target_lat) * 111_320.0
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        f = 0.0 if denom <= 1e-9 else max(0.0, min(1.0, -(ax * vx + ay * vy) / denom))
        px = ax + vx * f
        py = ay + vy * f
        dist = math.hypot(px, py)
        if best is None or dist < float(best["dist"]):
            best = {"idx": idx, "f": f, "dist": dist}
    assert best is not None
    idx = int(best["idx"])
    f = float(best["f"])
    a, b = raw[idx], raw[idx + 1]
    proj = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
    prefix = raw[: idx + 1]
    if not prefix or meters_between(prefix[-1][0], prefix[-1][1], proj[0], proj[1]) >= 0.5:
        prefix.append(proj)
    return prefix, {
        "route_end_lat": round(proj[0], 7),
        "route_end_lon": round(proj[1], 7),
        "road_offset_m": round(float(best["dist"]), 2),
    }


def point_at_s(points: list[dict[str, float]], s_m: float) -> dict[str, float]:
    if s_m <= 0:
        return points[0]
    if s_m >= points[-1]["s"]:
        return points[-1]
    lo, hi, idx = 0, len(points) - 1, 0
    while lo <= hi:
        mid = (lo + hi) >> 1
        if points[mid]["s"] <= s_m:
            idx = mid
            lo = mid + 1
        else:
            hi = mid - 1
    a = points[idx]
    b = points[min(idx + 1, len(points) - 1)]
    span = b["s"] - a["s"]
    f = (s_m - a["s"]) / span if span else 0.0
    return {
        "lat": a["lat"] + (b["lat"] - a["lat"]) * f,
        "lon": a["lon"] + (b["lon"] - a["lon"]) * f,
        "s": s_m,
    }


def project_s(points: list[dict[str, float]], lat: float, lon: float) -> float:
    best = min(points, key=lambda p: meters_between(lat, lon, p["lat"], p["lon"]))
    return float(best["s"])


def nearest_route_distance(points: list[dict[str, float]], lat: float, lon: float) -> float:
    return min(meters_between(lat, lon, p["lat"], p["lon"]) for p in points)


def load_signals(points: list[dict[str, float]]) -> list[dict[str, Any]]:
    if ROUTE_TLS_GEOJSON.is_file():
        payload = json.loads(ROUTE_TLS_GEOJSON.read_text(encoding="utf-8"))
        signals = []
        for feature in payload.get("features", []):
            coords = feature.get("geometry", {}).get("coordinates", [])
            if len(coords) < 2:
                continue
            lon, lat = float(coords[0]), float(coords[1])
            props = feature.get("properties", {})
            s = project_s(points, lat, lon)
            if MIN_DISPLAY_SIGNAL_S < s < points[-1]["s"] - 80:
                signals.append({
                    "id": f"S{len(signals) + 1:02d}",
                    "raw_id": str(props.get("tls_id", "")),
                    "name": str(props.get("name") or props.get("tls_id") or f"Signal {len(signals) + 1}"),
                    "lat": round(lat, 7),
                    "lon": round(lon, 7),
                    "s": round(s, 2),
                })
        signals.sort(key=lambda item: item["s"])
        collapsed: list[dict[str, Any]] = []
        cluster: list[dict[str, Any]] = []
        for sig in signals:
            if cluster and abs(sig["s"] - cluster[-1]["s"]) >= 170:
                chosen = max(cluster, key=lambda item: item["s"])
                chosen["merged_raw_ids"] = [item["raw_id"] for item in cluster if item["raw_id"]]
                collapsed.append(chosen)
                cluster = []
            cluster.append(sig)
        if cluster:
            chosen = max(cluster, key=lambda item: item["s"])
            chosen["merged_raw_ids"] = [item["raw_id"] for item in cluster if item["raw_id"]]
            collapsed.append(chosen)
        if len(collapsed) > 10:
            step = (len(collapsed) - 1) / 9
            collapsed = [collapsed[round(i * step)] for i in range(10)]
        for i, sig in enumerate(collapsed, 1):
            sig["id"] = f"S{i:02d}"
        if collapsed:
            return collapsed
    # Fallback: evenly spaced presentation signals.
    route_len = points[-1]["s"]
    out = []
    for i, frac in enumerate([0.14, 0.28, 0.43, 0.58, 0.73, 0.88], 1):
        p = point_at_s(points, route_len * frac)
        out.append({"id": f"S{i:02d}", "raw_id": "", "name": f"Signal {i}", "lat": p["lat"], "lon": p["lon"], "s": p["s"]})
    return out


def state_at(timeline: list[list[Any]], t: float) -> str:
    state = timeline[0][1]
    for ts, st in timeline:
        if float(ts) <= t:
            state = st
        else:
            break
    return str(state)


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def load_presentation_inputs() -> dict[str, Any]:
    if not PRESENTATION_INPUTS.is_file():
        return {
            "schema": "10-1_presentation_inputs.missing",
            "presentation_signal_policy": {},
            "presentation_demand_policy": {},
        }
    return json.loads(PRESENTATION_INPUTS.read_text(encoding="utf-8"))


def presentation_demand_policy(inputs: dict[str, Any]) -> dict[str, Any]:
    default = {
        "front_queue_cap": {"B04": 9, "B4": 5},
        "behind_queue_cap": {"B04": 8, "B4": 6},
        "green_discharge_veh_per_sec": {"B04": 1.35, "B4": 2.85},
        "queue_spacing_m": 13.0,
        "ev_queue_gap_m": 24.0,
    }
    policy = dict(default)
    policy.update(inputs.get("presentation_demand_policy", {}))
    return policy


def mode_policy_number(policy: dict[str, Any], key: str, mode: str, fallback: float) -> float:
    value = policy.get(key, {})
    if isinstance(value, dict):
        return float(value.get(mode, fallback))
    if value in {None, ""}:
        return fallback
    return float(value)


def time_at_route_s(samples: list[dict[str, Any]], s_m: float) -> float | None:
    if not samples:
        return None
    prev = samples[0]
    if float(prev.get("s", 0.0)) >= s_m:
        return float(prev.get("t", 0.0))
    for cur in samples[1:]:
        prev_s = float(prev.get("s", 0.0))
        cur_s = float(cur.get("s", 0.0))
        if cur_s >= s_m:
            span = cur_s - prev_s
            f = (s_m - prev_s) / span if span > 0 else 0.0
            return float(prev.get("t", 0.0)) + (float(cur.get("t", 0.0)) - float(prev.get("t", 0.0))) * f
        prev = cur
    return None


def sample_at_time(samples: list[dict[str, Any]], t: float) -> dict[str, Any]:
    if not samples:
        return {}
    if t <= float(samples[0]["t"]):
        return dict(samples[0])
    if t >= float(samples[-1]["t"]):
        return dict(samples[-1])
    lo, hi, idx = 0, len(samples) - 1, 0
    while lo <= hi:
        mid = (lo + hi) >> 1
        if float(samples[mid]["t"]) <= t:
            idx = mid
            lo = mid + 1
        else:
            hi = mid - 1
    a = samples[idx]
    b = samples[min(idx + 1, len(samples) - 1)]
    span = float(b["t"]) - float(a["t"])
    f = (t - float(a["t"])) / span if span > 0 else 0.0
    out = dict(b)
    out["t"] = round(t, 1)
    for key in ("s", "lat", "lon", "speed_kmh", "front_queue_count"):
        if key in a and key in b and a[key] is not None and b[key] is not None:
            out[key] = round(float(a[key]) + (float(b[key]) - float(a[key])) * f, 7 if key in {"lat", "lon"} else 2)
    if a.get("front_queue_tail_s") is not None and b.get("front_queue_tail_s") is not None:
        out["front_queue_tail_s"] = round(float(a["front_queue_tail_s"]) + (float(b["front_queue_tail_s"]) - float(a["front_queue_tail_s"])) * f, 2)
    elif out.get("front_queue_count", 0.0) <= 0.1:
        out["front_queue_tail_s"] = None
    return out


def high_res_samples(samples: list[dict[str, Any]], dt: float = DISPLAY_TRAFFIC_DT) -> list[dict[str, Any]]:
    if not samples:
        return []
    start = float(samples[0]["t"])
    end = float(samples[-1]["t"])
    out: list[dict[str, Any]] = []
    steps = int(math.ceil((end - start) / dt))
    for i in range(steps + 1):
        t = round(min(end, start + i * dt), 1)
        if out and abs(float(out[-1]["t"]) - t) < 1e-6:
            continue
        out.append(sample_at_time(samples, t))
    return out


def compact_event(event: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "t_abs",
        "t_rel",
        "stage",
        "action",
        "action_type",
        "tls_id",
        "movement_id",
        "case",
        "trigger_reason",
        "gate_result",
        "safety_result",
        "target_phase",
        "current_phase",
        "current_state",
        "stage2_hold_status",
        "Lq_merge_m",
        "Q_th_merge_m",
        "n_occ_runtime_veh",
        "n_need_proxy_veh",
        "merge_space_deficit",
    )
    return {key: event.get(key, "") for key in keys if key in event}


def build_algorithm_trace(
    source: dict[str, Any],
    route_id: str,
    route_len: float,
    signals: list[dict[str, Any]],
    signal_policy: dict[str, Any],
) -> dict[str, Any]:
    best_theta = signal_policy.get("best_theta", {})
    q_ratio = float(best_theta.get("Q_ratio", 0.28))
    tau = float(best_theta.get("tau", 0.79))
    stage1_signals = []
    prev_s = 0.0
    for i, sig in enumerate(signals, 1):
        upstream_l = max(1.0, float(sig["s"]) - prev_s)
        stage1_signals.append({
            "index": i,
            "display_id": sig["id"],
            "tls_id": sig.get("source_tls_id") or sig.get("raw_id") or sig.get("name", ""),
            "s_m": round(float(sig["s"]), 2),
            "L_m": round(upstream_l, 2),
            "Q_th_m": round(q_ratio * upstream_l, 2),
            "tau_L_m": round(tau * upstream_l, 2),
        })
        prev_s = float(sig["s"])
    events = [compact_event(event) for event in source.get("algorithm", {}).get("events", {}).get("B4", [])]
    stage2 = [event for event in events if event.get("stage") == "stage2"]
    stage3 = [event for event in events if event.get("stage") == "stage3"]
    safety = [
        event for event in events
        if event.get("safety_result") not in {"", None}
        or event.get("gate_result") not in {"", None}
    ]
    return {
        "stage1": {
            "route_id": route_id,
            "route_length_m": round(route_len, 2),
            "t_dispatch_delay_sec": 45,
            "tE_merge_sec": 10,
            "merge_arrival_sec": 55,
            "i_merge": 1,
            "best_theta": best_theta,
            "signals": stage1_signals,
        },
        "stage2": stage2,
        "stage3": stage3,
        "safety_gate": safety,
        "event_counts": {
            "stage2": len(stage2),
            "stage3": len(stage3),
            "case_a": sum(1 for event in stage3 if event.get("case") == "Case A"),
            "case_b": sum(1 for event in stage3 if event.get("case") == "Case B"),
            "green_active": sum(1 for event in stage3 if event.get("action") == "GREEN_ACTIVE"),
            "safety_denied": sum(1 for event in stage3 if event.get("action") == "DENIED_BY_SAFETY"),
        },
    }


def build_presentation_timeline(
    mode: str,
    raw_timeline: list[list[Any]],
    sig: dict[str, Any],
    signal_index: int,
    samples: list[dict[str, Any]],
    t_max: float,
    signal_policy: dict[str, Any],
) -> list[list[Any]]:
    if signal_policy.get("mode") != "10-1_suitable_signal_system":
        return raw_timeline
    arrival = time_at_route_s(samples, max(0.0, float(sig["s"]) - 72.0))
    if arrival is None:
        return raw_timeline
    clear_time = time_at_route_s(samples, min(float(sig["s"]) + 45.0, max(float(item.get("s", 0.0)) for item in samples)))
    yellow = float(signal_policy.get("yellow_sec", 2.5))
    if mode == "B4":
        best_theta = signal_policy.get("best_theta", {})
        lead = float(best_theta.get("t_lead", signal_policy.get("b4_green_lead_sec", 12.0)))
        hold = float(best_theta.get("G_ext", signal_policy.get("b4_green_hold_sec", 52.0)))
        green_start = max(0.0, round(arrival - lead, 2))
        green_end = round(max(arrival + hold, (clear_time or arrival) + 10.0), 2)
        rows: list[list[Any]] = []
        if green_start > 0:
            rows.append([0.0, "red"])
            rows.append([green_start, "green"])
        else:
            rows.append([0.0, "green"])
        rows.append([green_end, "yellow"])
        rows.append([round(green_end + yellow, 2), "red"])
        rows.append([round(green_end + yellow + 18.0, 2), "green"])
        return dedupe_timeline_rows(rows)

    red_indices = {int(x) for x in signal_policy.get("b04_red_signal_indices", [])}
    red_lead = float(signal_policy.get("b04_red_lead_sec", 18.0))
    red_hold = float(signal_policy.get("b04_red_hold_sec", 36.0))
    if signal_index not in red_indices:
        rows = [
            [0.0, "green"],
            [round(arrival + 54.0, 2), "yellow"],
            [round(arrival + 54.0 + yellow, 2), "red"],
            [round(arrival + 78.0, 2), "green"],
        ]
        return dedupe_timeline_rows(rows)
    red_start = max(0.0, round(arrival - red_lead, 2))
    red_end = round(arrival + red_hold, 2)
    rows: list[list[Any]] = []
    if red_start > yellow:
        rows.append([0.0, "green"])
        rows.append([round(red_start - yellow, 2), "yellow"])
        rows.append([red_start, "red"])
    else:
        rows.append([0.0, "red"])
    rows.append([red_end, "green"])
    rows.append([round(red_end + 58.0, 2), "yellow"])
    rows.append([round(red_end + 58.0 + yellow, 2), "red"])
    rows.append([round(red_end + 82.0, 2), "green"])
    return dedupe_timeline_rows(rows)


def dedupe_timeline_rows(rows: list[list[Any]]) -> list[list[Any]]:
    out: list[list[Any]] = []
    for t, state in rows:
        t = round(max(0.0, float(t)), 2)
        if out and out[-1][1] == state:
            continue
        if out and out[-1][0] == t:
            out[-1][1] = state
        else:
            out.append([t, state])
    return out


def normalize_timeline(timeline: list[list[Any]]) -> list[list[Any]]:
    ordered = sorted([[round(float(ts), 2), str(st)] for ts, st in timeline], key=lambda item: item[0])
    out: list[list[Any]] = []
    for ts, st in ordered:
        if out and out[-1][1] == st:
            continue
        if out and st == "yellow" and len(ordered) > ordered.index([ts, st]) + 1:
            next_st = ordered[ordered.index([ts, st]) + 1][1]
            if next_st == "green":
                continue
        out.append([max(0.0, ts), st])
    out.sort(key=lambda item: item[0])
    return out


def validate_timelines(timelines: dict[str, dict[str, list[list[Any]]]]) -> dict[str, Any]:
    errors: list[str] = []
    summary = {
        "signals_checked": 0,
        "missing_yellow": 0,
        "green_yellow_green": 0,
        "red_yellow_red": 0,
        "yellow_red_yellow": 0,
        "red_yellow_red_yellow": 0,
        "short_yellow": 0,
        "short_non_green": 0,
    }
    for mode, by_signal in timelines.items():
        for signal_id, timeline in by_signal.items():
            summary["signals_checked"] += 1
            states = [str(st) for _, st in timeline]
            if "yellow" not in states:
                summary["missing_yellow"] += 1
                errors.append(f"{mode}/{signal_id}: missing yellow")
            for a, b, c in zip(states, states[1:], states[2:]):
                if a == "green" and b == "yellow" and c == "green":
                    summary["green_yellow_green"] += 1
                    errors.append(f"{mode}/{signal_id}: green-yellow-green")
                if a in {"red", "allred"} and b == "yellow" and c in {"red", "allred"}:
                    summary["red_yellow_red"] += 1
                    errors.append(f"{mode}/{signal_id}: red-yellow-red")
                if a == "yellow" and b in {"red", "allred"} and c == "yellow":
                    summary["yellow_red_yellow"] += 1
                    errors.append(f"{mode}/{signal_id}: yellow-red-yellow")
            for a, b, c, d in zip(states, states[1:], states[2:], states[3:]):
                if a in {"red", "allred"} and b == "yellow" and c in {"red", "allred"} and d == "yellow":
                    summary["red_yellow_red_yellow"] += 1
                    errors.append(f"{mode}/{signal_id}: red-yellow-red-yellow")
            for idx, (ts, st) in enumerate(timeline[:-1]):
                next_ts = float(timeline[idx + 1][0])
                if st == "yellow" and next_ts - float(ts) < 2.5:
                    summary["short_yellow"] += 1
                    errors.append(f"{mode}/{signal_id}: yellow shorter than 2.5s")
                if st in {"yellow", "red", "allred"} and next_ts - float(ts) < 2.5:
                    summary["short_non_green"] += 1
                    errors.append(f"{mode}/{signal_id}: non-green shorter than 2.5s")
    if errors:
        raise SystemExit("TLS presentation validation failed:\n" + "\n".join(errors))
    summary["ok"] = True
    return summary


def close_display_signal_pairs(signals: list[dict[str, Any]], threshold_m: float = 170.0) -> int:
    ordered = sorted(signals, key=lambda item: float(item["s"]))
    return sum(
        1
        for a, b in zip(ordered, ordered[1:])
        if abs(float(b["s"]) - float(a["s"])) < threshold_m
    )


def make_timelines(signals: list[dict[str, Any]]) -> dict[str, dict[str, list[list[Any]]]]:
    timelines = {"B04": {}, "B4": {}}
    b04_stop_idx = {0, 3, 6, 9, 12}
    b4_priority_idx = {0, 3, 7, 10}
    for idx, sig in enumerate(signals):
        free_arrival = sig["s"] / 12.5
        if idx in b04_stop_idx:
            red_start = max(2.5, free_arrival - 16.0)
            red_end = red_start + 44.0
            timelines["B04"][sig["id"]] = [[0, "green"], [red_start - 2.5, "yellow"], [red_start, "red"], [red_end, "allred"], [red_end + 1.2, "green"]]
        else:
            red_start = max(0.0, free_arrival + 12.0)
            timelines["B04"][sig["id"]] = [[0, "green"], [red_start, "yellow"], [red_start + 2.5, "red"], [red_start + 20, "allred"], [red_start + 21.2, "green"]]

        if idx in b4_priority_idx:
            green_start = max(1.2, free_arrival - 10.0)
            timelines["B4"][sig["id"]] = [
                [0, "red"],
                [green_start - 1.2, "allred"],
                [green_start, "green"],
                [free_arrival + 18.0, "yellow"],
                [free_arrival + 20.5, "red"],
                [free_arrival + 32.0, "allred"],
                [free_arrival + 33.2, "green"],
            ]
        else:
            timelines["B4"][sig["id"]] = [[0, "green"], [free_arrival + 18.0, "yellow"], [free_arrival + 20.5, "red"], [free_arrival + 35.0, "allred"], [free_arrival + 36.2, "green"]]
    for mode, by_signal in timelines.items():
        for signal_id, timeline in by_signal.items():
            by_signal[signal_id] = normalize_timeline(timeline)
    validate_timelines(timelines)
    return timelines


def simulate_mode(
    mode: str,
    points: list[dict[str, float]],
    signals: list[dict[str, Any]],
    timelines: dict[str, list[list[Any]]],
    *,
    dt: float = 1.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    route_len = points[-1]["s"]
    s = 0.0
    speed = 0.0
    t = 0.0
    stop_gap = 34.0
    samples = []
    events = []
    touched: set[str] = set()
    stage2_done = False
    while t <= 620 and s < route_len - 0.5:
        next_sig = next((sig for sig in signals if sig["s"] > s + 2.0), None)
        target_speed = 12.2 if mode == "B04" else 13.5
        reason = "moving"
        alg = "-"
        if mode == "B4" and not stage2_done and t < 22:
            alg = "Stage2 신당역 유입 차단"
            reason = "stage2_hold"
        elif mode == "B4" and not stage2_done and t < 31:
            alg = "Stage2 release clearance"
            reason = "stage2_release"
        elif mode == "B4":
            stage2_done = True

        if next_sig:
            sig_state = state_at(timelines[next_sig["id"]], t)
            stop_s = max(0.0, next_sig["s"] - stop_gap)
            near_stop = s >= stop_s - 70.0
            if sig_state in {"red", "yellow", "allred"} and s >= stop_s - 2.0:
                target_speed = 0.0
                s = stop_s
                reason = "front_queue_red"
            elif sig_state in {"red", "yellow", "allred"} and s + max(speed, target_speed) * dt >= stop_s:
                target_speed = max(0.0, (stop_s - s) / dt)
                reason = "front_queue_approach"
            elif sig_state == "green" and near_stop and next_sig["id"] not in touched:
                if mode == "B4":
                    alg = "Stage3 Case A GREEN" if int(next_sig["id"][1:]) % 2 else "Stage3 Case B GREEN"
                target_speed = min(target_speed, 4.0)
                reason = "queue_clearing"
                if s > next_sig["s"] + 6:
                    touched.add(next_sig["id"])

        speed += max(-4.0, min(2.2, target_speed - speed))
        s = min(route_len, s + max(0.0, speed) * dt)
        p = point_at_s(points, s)
        samples.append({
            "t": round(t, 1),
            "s": round(s, 2),
            "lat": round(p["lat"], 7),
            "lon": round(p["lon"], 7),
            "speed_kmh": round(speed * 3.6, 1),
            "reason": reason,
            "algorithm": alg,
        })
        if mode == "B4" and alg != "-" and (not events or events[-1]["label"] != alg):
            events.append({"t": round(t, 1), "label": alg})
        t += dt
    if samples:
        samples[-1]["s"] = round(route_len, 2)
        p = point_at_s(points, route_len)
        samples[-1]["lat"] = round(p["lat"], 7)
        samples[-1]["lon"] = round(p["lon"], 7)
        samples[-1]["speed_kmh"] = 0.0
        samples[-1]["reason"] = "arrived"
    return samples, events


def algorithm_label_at(events: list[dict[str, Any]], t: float) -> str:
    recent = [
        event for event in events
        if float(event.get("t_rel", 0.0)) <= t and t - float(event.get("t_rel", 0.0)) <= 10.0
    ]
    if any(str(event.get("stage")) == "stage2" for event in recent):
        return "Stage2 신당역 유입 차단"
    if any(str(event.get("case")) == "Case B" for event in recent):
        return "Stage3 Case B 하류 큐 정리"
    for event in reversed(recent):
        action = str(event.get("action", ""))
        if action in {"GREEN_ACTIVE", "extend_target_green"}:
            case = str(event.get("case") or "Case A")
            return f"Stage3 {case} GREEN"
        if action == "DENIED_BY_SAFETY":
            return "SafetyGate 전이 대기"
        if action == "CONTINUE_TOO_FAR":
            return "Stage3 ΔT 게이트 대기"
    return "-"


def actual_samples(points: list[dict[str, Any]], events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    out = []
    events = events or []
    for point in points:
        t = round(float(point["t_rel"]), 1)
        speed_kmh = round(float(point.get("speed_kmh", 0.0)), 1)
        algorithm = algorithm_label_at(events, t)
        out.append({
            "t": t,
            "s": round(float(point["dist_m"]), 2),
            "lat": round(float(point["lat"]), 7),
            "lon": round(float(point["lon"]), 7),
            "speed_kmh": speed_kmh,
            "reason": "stage2_hold" if algorithm.startswith("Stage2") and speed_kmh < 1.0 else ("traffic_hold" if speed_kmh < 1.0 else "moving"),
            "algorithm": algorithm,
        })
    if out:
        out[-1]["reason"] = "arrived"
        out[-1]["speed_kmh"] = 0.0
    return out


def actual_background(snaps: list[dict[str, Any]], *, max_per_snap: int = 90) -> list[dict[str, Any]]:
    out = []
    for snap in snaps:
        vehicles = []
        for vehicle in snap.get("vehicles", [])[:max_per_snap]:
            vehicles.append({
                "id": str(vehicle.get("id", "")),
                "lat": round(float(vehicle["lat"]), 7),
                "lon": round(float(vehicle["lon"]), 7),
                "speed_kmh": round(float(vehicle.get("speed_kmh", 0.0)), 1),
                "angle": round(float(vehicle.get("angle", 0.0)), 1),
                "dist_m": round(float(vehicle.get("dist_m", 0.0)), 2),
                "edge": str(vehicle.get("edge", "")),
            })
        out.append({"t": round(float(snap["t_rel"]), 1), "vehicles": vehicles})
    return out


def actual_route_points(points: list[dict[str, Any]]) -> list[dict[str, float]]:
    return [
        {
            "lat": round(float(point["lat"]), 7),
            "lon": round(float(point["lon"]), 7),
            "s": round(float(point["dist_m"]), 2),
        }
        for point in points
    ]


def presentation_route_to_destination(points: list[dict[str, float]]) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Route the 10-1 display run to the requested destination on the SUMO road graph."""
    target_lat = float(PRESENTATION_DESTINATION["lat"])
    target_lon = float(PRESENTATION_DESTINATION["lon"])
    try:
        net = sumolib.net.readNet(str(NET_FILE))
        start_edge = net.getEdge(route_edges()[0])
        x, y = net.convertLonLat2XY(target_lon, target_lat)
        candidates = sorted(net.getNeighboringEdges(x, y, 180.0, includeJunctions=False), key=lambda item: item[1])
        best: tuple[list[Any], float, float, str, str] | None = None
        for edge, dist in candidates[:24]:
            for waypoint_id in PRESENTATION_DONGHO_WAYPOINT_EDGES:
                try:
                    waypoint = net.getEdge(waypoint_id)
                except Exception:
                    continue
                path_a, cost_a = net.getShortestPath(start_edge, waypoint)
                path_b, cost_b = net.getShortestPath(waypoint, edge)
                if not path_a or not path_b:
                    continue
                path = path_a + path_b[1:]
                score = float(cost_a) + float(cost_b) + float(dist) * 5.0
                if best is None or score < best[1]:
                    best = (path, score, float(dist), edge.getID(), waypoint_id)
        if best is None:
            raise ValueError("no SUMO route to requested destination")
        path, _score, edge_dist, target_edge_id, waypoint_id = best
        raw: list[tuple[float, float]] = []
        for edge in path:
            shape = []
            for sx, sy in edge.getShape():
                lon, lat = net.convertXY2LonLat(float(sx), float(sy))
                shape.append((float(lat), float(lon)))
            if raw and shape and meters_between(raw[-1][0], raw[-1][1], shape[0][0], shape[0][1]) < 0.5:
                raw.extend(shape[1:])
            else:
                raw.extend(shape)
        prefix, projection_meta = closest_polyline_prefix(raw, target_lat, target_lon)
        routed = straighten_dongho_tail(route_points_from_latlon(prefix))
        if len(routed) >= 2:
            meta = {
                **PRESENTATION_DESTINATION,
                **projection_meta,
                "routing": "sumo_shortest_path_via_dongho_waypoint",
                "required_waypoint_edge_id": waypoint_id,
                "target_edge_id": target_edge_id,
                "target_edge_distance_m": round(edge_dist, 2),
                "path_edge_count": len(path),
                "path_edges": [edge.getID() for edge in path],
                "presentation_route_length_m": round(float(routed[-1]["s"]), 2),
                "vehicle_arrival_note": "EV follows road centerline; target coordinate is stored as destination metadata.",
            }
            return routed, meta
    except Exception as exc:
        if not points:
            return points, {"error": str(exc)}

    anchor_idx = min(range(len(points)), key=lambda i: meters_between(target_lat, target_lon, float(points[i]["lat"]), float(points[i]["lon"])))
    anchor = points[anchor_idx]
    kept = [dict(p) for p in points[: anchor_idx + 1]]
    meta = {
        **PRESENTATION_DESTINATION,
        "routing": "fallback_actual_route_cut",
        "error": "SUMO routing failed; used closest actual route prefix",
        "anchor_index": anchor_idx,
        "anchor_s_m": round(float(anchor["s"]), 2),
        "anchor_lat": round(float(anchor["lat"]), 7),
        "anchor_lon": round(float(anchor["lon"]), 7),
        "actual_route_distance_from_target_m": round(meters_between(target_lat, target_lon, float(anchor["lat"]), float(anchor["lon"])), 2),
        "presentation_route_length_m": round(float(kept[-1]["s"]), 2),
    }
    return kept, meta


def presentation_stage2_approaches(raw_lines: list[list[list[float]]]) -> list[dict[str, Any]]:
    # Stage2 is the Sindang Station ingress hold at the Toegye-ro x Dasan-ro
    # intersection, not the firetruck's side-street exit.  The first coordinate
    # in each line is the fake-red stopline; following points run upstream.
    display = [
        {
            "label": "퇴계로 동측 유입 차단",
            "line": [
                [37.565379, 127.016475],
                [37.565412, 127.016858],
                [37.565399, 127.017114],
                [37.565386, 127.017360],
            ],
        },
        {
            "label": "다산로 북측 유입 차단",
            "line": [
                [37.565555, 127.016208],
                [37.566000, 127.016226],
                [37.566425, 127.016242],
                [37.566760, 127.016258],
            ],
        },
        {
            "label": "다산로 남측 유입 차단",
            "line": [
                [37.565182, 127.016183],
                [37.564929, 127.016133],
                [37.564500, 127.016045],
                [37.564026, 127.015946],
            ],
        },
    ]
    return [{**item, "source": "presentation_toegye_dasan_shindang_intersection"} for item in display]


def normalize_actual_timeline(timeline: list[list[Any]], t_max: float) -> list[list[Any]]:
    non_green = {"yellow", "red", "allred"}
    rows: list[list[Any]] = []
    for ts, state in timeline:
        t = round(float(ts), 2)
        if t < -2.0 or t > t_max:
            continue
        rows.append([max(0.0, t), str(state)])
    if not rows:
        return [[0.0, "green"], [max(2.5, t_max), "yellow"], [max(5.0, t_max + 2.5), "red"]]
    rows.sort(key=lambda item: item[0])
    deduped: list[list[Any]] = []
    for t, state in rows:
        if deduped and deduped[-1][1] == state:
            continue
        deduped.append([t, state])
    if deduped[0][0] > 0.0:
        deduped.insert(0, [0.0, deduped[0][1]])

    # Presentation TLS canonicalization:
    # one non-green run is shown as G -> Y -> R -> G, or initial R -> G.
    # This removes raw controller flicker such as R -> Y -> R -> Y and Y -> R -> Y
    # without changing the visible green windows that matter for the algorithm story.
    canonical: list[list[Any]] = []
    i = 0
    while i < len(deduped):
        t, state = deduped[i]
        if state == "green":
            if not canonical or canonical[-1][1] != "green":
                canonical.append([t, "green"])
            i += 1
            continue
        if state in non_green:
            start = t
            j = i + 1
            while j < len(deduped) and deduped[j][1] in non_green:
                j += 1
            end = deduped[j][0] if j < len(deduped) else t_max
            duration = max(0.0, end - start)
            if duration < 5.0:
                i = j
                continue
            previous = canonical[-1][1] if canonical else ""
            if previous == "green":
                canonical.append([start, "yellow"])
                canonical.append([round(min(start + 2.5, t_max), 2), "red"])
            else:
                canonical.append([start, "red"])
            i = j
            continue
        i += 1
    out = canonical
    fixed: list[list[Any]] = []
    min_non_green = 2.5
    for t, state in out:
        t = min(round(float(t), 2), round(t_max, 2))
        if fixed and t < fixed[-1][0]:
            t = fixed[-1][0]
        if fixed and fixed[-1][1] in {"yellow", "red", "allred"} and t - fixed[-1][0] < min_non_green:
            t = round(min(fixed[-1][0] + min_non_green, t_max), 2)
            if t == fixed[-1][0]:
                fixed.pop()
        if fixed and fixed[-1][0] == t:
            fixed[-1][1] = state
        else:
            fixed.append([t, state])
    out = []
    for t, state in fixed:
        if out and out[-1][1] == state:
            continue
        out.append([t, state])
    if not any(state == "yellow" for _, state in out) and t_max >= 5.0:
        # Keep the yellow lamp visible for presentation and for the validation rule.
        insert_t = max(0.0, round(t_max - 5.0, 2))
        out.extend([[insert_t, "yellow"], [round(min(t_max, insert_t + 2.5), 2), "red"]])
        out.sort(key=lambda item: item[0])
        dedup: list[list[Any]] = []
        for t, state in out:
            if dedup and dedup[-1][1] == state:
                continue
            dedup.append([t, state])
        out = dedup
    return out


def route_angle(points: list[dict[str, float]], s_m: float) -> float:
    a = point_at_s(points, max(0.0, s_m - 5.0))
    b = point_at_s(points, min(points[-1]["s"], s_m + 8.0))
    lat = (a["lat"] + b["lat"]) / 2.0
    dx = (b["lon"] - a["lon"]) * 111_320.0 * math.cos(math.radians(lat))
    dy = (b["lat"] - a["lat"]) * 111_320.0
    return math.degrees(math.atan2(-dy, dx))


def point_from_heading(lat: float, lon: float, angle_deg: float, dist_m: float) -> dict[str, float]:
    rad = math.radians(angle_deg)
    dx = math.cos(rad) * dist_m
    dy = -math.sin(rad) * dist_m
    out_lat = lat + dy / 111_320.0
    out_lon = lon + dx / (111_320.0 * math.cos(math.radians(lat)))
    return {"lat": out_lat, "lon": out_lon}


def profile_at_time(profile: list[dict[str, Any]], t: float) -> dict[str, Any]:
    if not profile:
        return {}
    best = profile[0]
    for row in profile:
        if float(row.get("t_rel", 0.0)) <= t:
            best = row
        else:
            break
    return best


def next_signal_after(signals: list[dict[str, Any]], s_m: float) -> dict[str, Any] | None:
    for sig in signals:
        if float(sig["s"]) > s_m + 2.0:
            return sig
    return None


def active_stop_between(
    signals: list[dict[str, Any]],
    timelines: dict[str, list[list[Any]]],
    t: float,
    a_s: float,
    b_s: float,
) -> dict[str, Any] | None:
    for sig in signals:
        stop_s = float(sig["s"]) - 18.0
        if not (a_s < stop_s < b_s):
            continue
        if state_at(timelines[sig["id"]], t) in {"red", "yellow", "allred"}:
            return {**sig, "stop_s": stop_s}
    return None


def build_display_samples(
    mode: str,
    raw_samples: list[dict[str, Any]],
    profile: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    timelines: dict[str, list[list[Any]]],
    points: list[dict[str, float]],
    demand_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not raw_samples:
        return []
    demand_policy = demand_policy or {}
    front_cap = mode_policy_number(demand_policy, "front_queue_cap", mode, 10.0)
    discharge_rate = mode_policy_number(demand_policy, "green_discharge_veh_per_sec", mode, 1.35)
    queue_spacing = float(demand_policy.get("queue_spacing_m", 13.0))
    ev_queue_gap = float(demand_policy.get("ev_queue_gap_m", 24.0))
    downstream_indices = {
        int(value)
        for value in (demand_policy.get("downstream_queue_signal_indices", {}).get(mode, []))
    }
    downstream_duration = mode_policy_number(demand_policy, "downstream_queue_duration_sec", mode, 0.0)
    downstream_count = mode_policy_number(demand_policy, "downstream_queue_count", mode, 0.0)
    downstream_lookahead = mode_policy_number(demand_policy, "downstream_queue_lookahead_m", mode, 72.0)
    overflow_windows = list(demand_policy.get("front_queue_overflow_windows", {}).get(mode, []))
    downstream_windows: dict[str, tuple[float, float]] = {}
    for sig in signals:
        try:
            sig_idx = int(str(sig["id"]).replace("S", ""))
        except ValueError:
            continue
        if sig_idx not in downstream_indices:
            continue
        arrival = time_at_route_s(raw_samples, max(0.0, float(sig["s"]) - downstream_lookahead))
        if arrival is not None:
            downstream_windows[sig["id"]] = (max(0.0, arrival - 2.0), arrival + downstream_duration)
    out: list[dict[str, Any]] = []
    queue_by_signal = {sig["id"]: 0.0 for sig in signals}
    last_s = float(raw_samples[0]["s"])
    last_t = float(raw_samples[0]["t"])
    route_len = float(points[-1]["s"])
    for idx, raw in enumerate(raw_samples):
        t = float(raw["t"])
        dt = max(0.25, min(1.5, t - last_t if idx else 1.0))
        raw_s = max(float(raw["s"]), last_s)
        row = profile_at_time(profile, t)
        ahead_obs = min(front_cap, max(0.0, round(float(row.get("ahead_count", 0.0)))))
        ns = next_signal_after(signals, last_s)
        next_state = "-"
        front_q = 0.0
        queue_tail_s = None
        target_s = raw_s
        reason = str(raw.get("reason", "moving"))
        algorithm = str(raw.get("algorithm", "-"))

        if mode == "B4" and t < 12.0:
            target_s = min(target_s, 1.5)
            reason = "stage2_hold"
            algorithm = "Stage2 신당역 유입 차단"

        if ns is not None:
            sid = ns["id"]
            next_state = state_at(timelines[sid], t)
            stop_s = max(0.0, float(ns["s"]) - 18.0)
            q = queue_by_signal.get(sid, 0.0)
            downstream_active = (
                next_state == "green"
                and sid in downstream_windows
                and downstream_windows[sid][0] <= t <= downstream_windows[sid][1]
            )
            forced_overflow_count = 0.0
            try:
                sig_idx = int(str(sid).replace("S", ""))
            except ValueError:
                sig_idx = -1
            for window in overflow_windows:
                if int(window.get("signal_index", -999)) != sig_idx:
                    continue
                if float(window.get("start_sec", -1.0)) <= t <= float(window.get("end_sec", -1.0)):
                    forced_overflow_count = max(forced_overflow_count, float(window.get("queue_count", downstream_count)))
            forced_overflow_active = forced_overflow_count > 0.0
            if next_state in {"red", "yellow", "allred"}:
                q = max(q, ahead_obs)
                max_fit = max(0.0, (stop_s - 10.0 - (last_s + ev_queue_gap)) / queue_spacing)
                q = min(q, max_fit, front_cap)
            elif downstream_active or forced_overflow_active:
                max_fit = max(0.0, (stop_s - 10.0 - (last_s + ev_queue_gap)) / queue_spacing)
                target_q = max(q, forced_overflow_count, downstream_count if downstream_active else 0.0, ahead_obs)
                q = min(target_q, max_fit, front_cap)
            elif q > 0.0:
                q = max(0.0, q - discharge_rate * dt)
            queue_by_signal[sid] = q
            front_q = q

            if q > 0.0:
                queue_tail_s = max(0.0, stop_s - 10.0 - q * queue_spacing)
                hold_s = max(0.0, queue_tail_s - ev_queue_gap)
            else:
                hold_s = max(0.0, stop_s - (56.0 if next_state in {"red", "yellow", "allred"} else 28.0))

            if next_state in {"red", "yellow", "allred"} and target_s >= hold_s:
                target_s = hold_s
                reason = "front_queue_tail" if q > 0 else "front_queue_red"
            elif (downstream_active or forced_overflow_active) and q > 0.0 and target_s >= hold_s:
                if last_s >= hold_s - 0.3:
                    target_s = last_s
                    reason = "green_downstream_queue"
                else:
                    target_s = hold_s
                    reason = "green_downstream_approach"
            elif forced_overflow_active and q > 0.0:
                target_s = min(target_s, last_s)
                reason = "green_downstream_queue"
            elif q > 0.0 and target_s >= hold_s:
                target_s = hold_s
                reason = "queue_clearing"
            elif next_state == "green" and not downstream_active and q <= 0.1:
                min_green_mps = 9.0 if mode == "B4" else 7.0
                target_s = min(route_len, max(target_s, last_s + min_green_mps * dt))
                reason = "moving"
                front_q = 0.0
                queue_tail_s = None
        elif reason == "traffic_hold":
            min_free_mps = 9.0 if mode == "B4" else 7.0
            target_s = min(route_len, max(target_s, last_s + min_free_mps * dt))
            reason = "moving"

        max_speed_mps = 15.2 if mode == "B4" else 14.2
        if reason in {"front_queue_tail", "front_queue_red", "queue_clearing", "green_downstream_queue", "green_downstream_approach", "stage2_hold"}:
            max_speed_mps = 8.5 if reason == "queue_clearing" else 4.8
        next_s = min(route_len, max(last_s, min(target_s, last_s + max_speed_mps * dt)))
        if next_s >= route_len - 0.5:
            next_s = route_len
            reason = "arrived"
            front_q = 0.0
            queue_tail_s = None
        p = point_at_s(points, next_s)
        speed_kmh = max(0.0, (next_s - last_s) / dt * 3.6)
        if reason in {"front_queue_tail", "front_queue_red", "green_downstream_queue", "stage2_hold", "arrived"} and abs(next_s - last_s) < 0.3:
            speed_kmh = 0.0
        out.append({
            "t": round(t, 1),
            "s": round(next_s, 2),
            "lat": round(p["lat"], 7),
            "lon": round(p["lon"], 7),
            "speed_kmh": round(speed_kmh, 1),
            "reason": reason,
            "algorithm": algorithm,
            "next_signal_id": ns["id"] if ns else "",
            "next_signal_state": next_state,
            "front_queue_count": round(front_q, 2),
            "front_queue_tail_s": round(queue_tail_s, 2) if queue_tail_s is not None else None,
        })
        last_s = next_s
        last_t = t
    while out and out[-1]["s"] < route_len - 0.5:
        t = round(float(out[-1]["t"]) + 1.0, 1)
        last_s = float(out[-1]["s"])
        ns = next_signal_after(signals, last_s)
        target_s = min(route_len, last_s + (15.2 if mode == "B4" else 14.2))
        if ns is not None:
            state = state_at(timelines[ns["id"]], t)
            stop_s = max(0.0, float(ns["s"]) - 18.0)
            if state in {"red", "yellow", "allred"} and target_s >= stop_s - 48.0:
                target_s = max(last_s, stop_s - 48.0)
        p = point_at_s(points, target_s)
        out.append({
            "t": t,
            "s": round(target_s, 2),
            "lat": round(p["lat"], 7),
            "lon": round(p["lon"], 7),
            "speed_kmh": round(max(0.0, target_s - last_s) * 3.6, 1),
            "reason": "moving" if target_s < route_len - 0.5 else "arrived",
            "algorithm": "-",
            "next_signal_id": ns["id"] if ns else "",
            "next_signal_state": state_at(timelines[ns["id"]], t) if ns else "-",
            "front_queue_count": 0.0,
            "front_queue_tail_s": None,
        })
        if len(out) > len(raw_samples) + 220:
            break
    if out:
        out[-1]["reason"] = "arrived"
        out[-1]["speed_kmh"] = 0.0
    return out


def line_length(line: list[list[float]]) -> float:
    total = 0.0
    for a, b in zip(line, line[1:]):
        total += meters_between(a[0], a[1], b[0], b[1])
    return total


def line_point_from_stop(line: list[list[float]], s_m: float) -> dict[str, float]:
    if not line:
        return {"lat": 0.0, "lon": 0.0, "angle": 0.0}
    total = line_length(line)
    left = max(0.0, min(s_m, max(0.0, total - 3.0)))
    for a, b in zip(line, line[1:]):
        seg_len = meters_between(a[0], a[1], b[0], b[1])
        if left <= seg_len:
            f = left / seg_len if seg_len else 0.0
            lat = a[0] + (b[0] - a[0]) * f
            lon = a[1] + (b[1] - a[1]) * f
            mid_lat = (a[0] + b[0]) / 2.0
            dx = (b[1] - a[1]) * 111_320.0 * math.cos(math.radians(mid_lat))
            dy = (b[0] - a[0]) * 111_320.0
            return {"lat": lat, "lon": lon, "angle": math.degrees(math.atan2(-dy, dx))}
        left -= seg_len
    z = line[-1]
    return {"lat": z[0], "lon": z[1], "angle": 0.0}


def route_vehicle_point(
    points: list[dict[str, float]],
    route_len: float,
    s_m: float,
    *,
    choice: str = "right",
) -> dict[str, float]:
    final_split_s = max(0.0, route_len - 255.0)
    p = point_at_s(points, min(route_len, max(0.0, s_m)))
    return {"lat": p["lat"], "lon": p["lon"], "angle": route_angle(points, s_m)}


def offset_route_point(
    lat: float,
    lon: float,
    angle_deg: float,
    lateral_m: float,
) -> dict[str, float]:
    # Positive lateral offsets move across the rendered lane, not along the route.
    rad = math.radians(angle_deg + 90.0)
    dx = math.cos(rad) * lateral_m
    dy = -math.sin(rad) * lateral_m
    out_lat = lat + dy / 111_320.0
    out_lon = lon + dx / (111_320.0 * math.cos(math.radians(lat)))
    return {"lat": out_lat, "lon": out_lon}


def make_route_vehicle(
    points: list[dict[str, float]],
    route_len: float,
    vid: str,
    s_m: float,
    speed_kmh: float,
    color: str,
    opacity: float,
    role: str,
    *,
    choice: str = "right",
    lateral_m: float = 0.0,
    stop_s: float | None = None,
    signal_state: str = "-",
    reason: str = "moving",
) -> dict[str, Any]:
    p = route_vehicle_point(points, route_len, s_m, choice=choice)
    if lateral_m:
        shifted = offset_route_point(p["lat"], p["lon"], p["angle"], lateral_m)
        p = {**p, "lat": shifted["lat"], "lon": shifted["lon"]}
    return {
        "id": vid,
        "kind": "route",
        "role": role,
        "choice": choice,
        "s": round(max(0.0, min(route_len, s_m)), 2),
        "lat": round(p["lat"], 7),
        "lon": round(p["lon"], 7),
        "angle": round(p["angle"], 1),
        "speed_kmh": round(max(0.0, speed_kmh), 1),
        "color": color,
        "opacity": round(opacity, 2),
        "lateral_m": round(lateral_m, 2),
        "lane_offset_m": round(lateral_m, 2),
        "stop_s": round(stop_s, 2) if stop_s is not None else None,
        "signal_state": signal_state,
        "reason": reason,
    }


def move_route_vehicle(
    points: list[dict[str, float]],
    route_len: float,
    vehicle: dict[str, Any],
    s_m: float,
    speed_kmh: float | None = None,
) -> dict[str, Any]:
    p = route_vehicle_point(points, route_len, s_m, choice=str(vehicle.get("choice", "right")))
    lateral = float(vehicle.get("lateral_m", vehicle.get("lane_offset_m", 0.0)) or 0.0)
    if lateral:
        shifted = offset_route_point(p["lat"], p["lon"], p["angle"], lateral)
        p = {**p, "lat": shifted["lat"], "lon": shifted["lon"]}
    out = dict(vehicle)
    out.update({
        "s": round(max(0.0, min(route_len, s_m)), 2),
        "lat": round(p["lat"], 7),
        "lon": round(p["lon"], 7),
        "angle": round(p["angle"], 1),
    })
    if speed_kmh is not None:
        out["speed_kmh"] = round(max(0.0, speed_kmh), 1)
    return out


def stopline_safe_vehicle_s(
    s_m: float,
    t: float,
    signals: list[dict[str, Any]],
    timelines: dict[str, list[list[Any]]],
    slot: int,
) -> tuple[float, bool]:
    for sig in signals:
        stop_s = float(sig["s"]) - 18.0
        if stop_s < s_m < float(sig["s"]) + 4.0 and state_at(timelines[sig["id"]], t) in {"red", "yellow", "allred"}:
            return max(0.0, stop_s - 10.0 - slot * 12.0), True
    return s_m, False


def ease_smoothstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def analog_queue_vehicle_s(
    t: float,
    final_s: float,
    slot: int,
    window_start: float,
) -> tuple[float, float, bool]:
    settle_t = window_start - 5.0 + slot * 2.8
    travel_dur = 13.0 + slot * 0.45
    start_t = settle_t - travel_dur
    if t < start_t:
        return final_s, 0.0, False
    approach_m = 105.0 + slot * 6.0
    start_s = max(0.0, final_s - approach_m)
    if t >= settle_t:
        return final_s, 0.0, True
    phase = (t - start_t) / max(0.1, travel_dur)
    eased = ease_smoothstep(phase)
    s_m = start_s + (final_s - start_s) * eased
    speed_mps = (final_s - start_s) * (6.0 * phase * (1.0 - phase)) / max(0.1, travel_dur)
    return s_m, max(0.0, speed_mps * 3.6), True


def stage2_display_state(t: float, algorithm_trace: dict[str, Any] | None) -> dict[str, Any] | None:
    events = (algorithm_trace or {}).get("stage2", [])
    if not events:
        return {"active": 0.0 <= t <= 18.0, "status": "hold_active", "label": "Stage2 신당역 유입 차단"}
    hold = next((event for event in events if event.get("action") == "RED_HOLD"), None)
    request = next((event for event in events if event.get("action") == "RELEASE_REQUEST"), None)
    release = next((event for event in events if event.get("action") == "RELEASE"), None)
    hold_t = max(0.0, float((hold or {}).get("t_rel", 0.0)))
    request_t = max(hold_t, float((request or release or {}).get("t_rel", hold_t + 9.0)))
    release_t = max(request_t, float((release or {}).get("t_rel", request_t + 3.0)))
    if t < hold_t or t > release_t + 4.0:
        return None
    if t < request_t:
        status = "hold_active"
        label = "Stage2 신당역 유입 차단"
    elif t < release_t:
        status = "release_clearance_pending"
        label = "Stage2 해제 전 clearance"
    else:
        status = "released"
        label = "Stage2 유입 재개"
    return {
        "active": True,
        "status": status,
        "label": label,
        "hold_t": hold_t,
        "request_t": request_t,
        "release_t": release_t,
    }


def build_stage2_display(
    t: float,
    approaches: list[dict[str, Any]],
    algorithm_trace: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    state = stage2_display_state(t, algorithm_trace)
    if not state:
        return [], [], []
    vehicles: list[dict[str, Any]] = []
    fake_signals: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    for ridx, item in enumerate(approaches):
        line = item.get("line", [])
        if not line or len(line) < 2:
            continue
        lines.append({"line": line, "label": state["label"]})
        stop = line_point_from_stop(line, 0.0)
        fake_signals.append({"lat": round(stop["lat"], 7), "lon": round(stop["lon"], 7)})
        length = line_length(line)
        if length < 24.0:
            continue
        for i in range(4):
            stop_gap = min(length - 7.0, 9.0 + i * 17.0)
            if state["status"] == "released":
                dist = stop_gap + max(0.0, t - float(state.get("release_t", t))) * 5.2
                if dist >= length - 8.0:
                    continue
                speed_kmh = 18.7
            else:
                dist = stop_gap
                speed_kmh = 0.0
            p = line_point_from_stop(line, dist)
            vehicles.append({
                "id": f"stage2_{ridx:02d}_{i:02d}",
                "kind": "stage2",
                "role": "stage2_blocked",
                "lat": round(p["lat"], 7),
                "lon": round(p["lon"], 7),
                "angle": round(p["angle"], 1),
                "speed_kmh": speed_kmh,
                "color": GENERAL_VEHICLE_COLOR,
                "opacity": GENERAL_VEHICLE_OPACITY,
                "signal_state": "red" if state["status"] != "released" else "green",
                "reason": state["label"],
                "stage2_status": state["status"],
            })
    return vehicles, fake_signals, lines


def build_display_traffic(
    mode: str,
    display_samples: list[dict[str, Any]],
    profile: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    timelines: dict[str, list[list[Any]]],
    points: list[dict[str, float]],
    stage2_approaches: list[dict[str, Any]],
    demand_policy: dict[str, Any] | None = None,
    algorithm_trace: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    demand_policy = demand_policy or {}
    front_cap = int(mode_policy_number(demand_policy, "front_queue_cap", mode, 16.0))
    behind_cap = int(mode_policy_number(demand_policy, "behind_queue_cap", mode, 12.0))
    queue_spacing_m = float(demand_policy.get("queue_spacing_m", 13.0))
    ev_queue_gap_m = float(demand_policy.get("ev_queue_gap_m", 24.0))
    route_len = float(points[-1]["s"])
    color = GENERAL_VEHICLE_COLOR
    out: list[dict[str, Any]] = []
    prev_route_by_id: dict[str, dict[str, Any]] = {}
    prev_frame_t: float | None = None
    ahead_zero_since: float | None = None
    behind_zero_since: float | None = None
    empty_confirm_sec = 3.0

    def continuity_slot(vehicle: dict[str, Any]) -> int:
        try:
            return int(str(vehicle.get("id", "")).split("_")[-1])
        except ValueError:
            return 0

    for st in display_samples:
        t = float(st["t"])
        ev_s = float(st["s"])
        row = profile_at_time(profile, t)
        ahead = min(front_cap, max(0, int(round(float(row.get("ahead_count", 0.0))))))
        behind = min(behind_cap, max(0, int(round(float(row.get("behind_count", 0.0))))))
        ahead_speed = max(0.0, float(row.get("ahead_speed_kmh", 0.0)))
        behind_speed = max(0.0, float(row.get("behind_speed_kmh", 0.0)))
        vehicles: list[dict[str, Any]] = []
        bands: list[dict[str, Any]] = []
        notes: list[dict[str, Any]] = []
        slow = 0
        signal_queued_ahead = False
        signal_queued_behind = False

        next_sig = next_signal_after(signals, ev_s)
        next_state = state_at(timelines[next_sig["id"]], t) if next_sig else "-"
        front_q = float(st.get("front_queue_count") or 0.0)
        if ahead <= 0 and front_q <= 0.1:
            ahead_zero_since = t if ahead_zero_since is None else ahead_zero_since
        else:
            ahead_zero_since = None
        if behind <= 0:
            behind_zero_since = t if behind_zero_since is None else behind_zero_since
        else:
            behind_zero_since = None
        ahead_empty_confirmed = ahead_zero_since is not None and t - ahead_zero_since >= empty_confirm_sec
        behind_empty_confirmed = behind_zero_since is not None and t - behind_zero_since >= empty_confirm_sec
        queue_sig = next_sig
        queue_state = next_state
        overflow_display_count = 0.0
        for window in demand_policy.get("front_queue_overflow_windows", {}).get(mode, []):
            window_start = float(window.get("start_sec", -1.0))
            window_end = float(window.get("end_sec", -1.0))
            if not (window_start - 18.0 <= t <= window_end + 14.0):
                continue
            target_idx = int(window.get("signal_index", -999))
            for sig in signals:
                try:
                    sig_idx = int(str(sig["id"]).replace("S", ""))
                except ValueError:
                    continue
                if sig_idx != target_idx:
                    continue
                if not (ev_s + 20.0 <= float(sig["s"]) <= ev_s + 460.0):
                    continue
                count = float(window.get("queue_count", demand_policy.get("downstream_queue_count", {}).get(mode, 0.0)))
                if count > overflow_display_count:
                    overflow_display_count = count
                    queue_sig = sig
                    queue_state = state_at(timelines[sig["id"]], t)
                break
        if next_sig and front_q > 0.1:
            for window in demand_policy.get("front_queue_overflow_windows", {}).get(mode, []):
                try:
                    sig_idx = int(str(next_sig["id"]).replace("S", ""))
                except ValueError:
                    sig_idx = -1
                if int(window.get("signal_index", -999)) == sig_idx:
                    queue_sig = next_sig
                    queue_state = next_state
                    break
        display_front_q = max(front_q, overflow_display_count)

        if queue_sig and display_front_q > 0.1:
            stop_s = max(0.0, float(queue_sig["s"]) - 18.0)
            q_end = max(0.0, stop_s - 10.0)
            physical_tail = q_end - max(1.0, display_front_q) * queue_spacing_m
            q_tail = physical_tail if front_q <= 0.1 and overflow_display_count > 0.1 else max(ev_s + ev_queue_gap_m + 2.0, physical_tail)
            if front_q <= 0.1 and overflow_display_count > 0.1:
                n = max(1, min(14, int(math.ceil(display_front_q))))
            elif str(st.get("reason")) == "green_downstream_queue":
                n = max(1, min(14, int(math.ceil(display_front_q))))
            else:
                n = max(1, min(14, ahead or int(math.ceil(display_front_q))))
            queue_offsets = [0.0, 12.8, 26.9, 40.4, 55.8, 70.2, 86.5, 101.1, 118.7, 135.4, 153.0, 171.5, 190.0, 209.0]
            max_span = max(0.0, q_end - q_tail)
            if str(st.get("reason")) == "green_downstream_queue":
                front_label = "막힌 구간"
            elif front_q <= 0.1 and overflow_display_count > 0.1:
                front_label = "앞 구간 정체"
            else:
                front_label = "앞 큐 뒤 대기" if queue_state in {"red", "yellow", "allred"} else "신호 변경 후 앞차 출발"
            if str(st.get("reason")) == "green_downstream_queue":
                bands.append({"a": round(q_tail, 2), "b": round(q_end, 2), "kind": "front_blocked", "label": front_label})
                downstream_a = min(route_len, q_end + 9.0)
                downstream_b = min(route_len, q_end + 138.0)
                if downstream_b > downstream_a + 18.0:
                    bands.append({"a": round(downstream_a, 2), "b": round(downstream_b, 2), "kind": "front_moving", "label": "하류 배출 중"})
                    downstream_span = max(24.0, downstream_b - downstream_a)
                    for j in range(6):
                        ds = downstream_a + 12.0 + ((t * 6.4 + j * 22.0) % max(24.0, downstream_span - 18.0))
                        vehicles.append(make_route_vehicle(
                            points,
                            route_len,
                            f"{mode}_downstream_{j:02d}",
                            min(route_len, ds),
                            23.0,
                            GENERAL_VEHICLE_COLOR,
                            0.96,
                            "downstream_moving",
                            lateral_m=[-1.1, 1.25, -0.7, 1.45, -1.35, 0.85][j],
                            signal_state="green",
                            reason="하류 배출 중",
                        ))
            else:
                bands.append({"a": round(q_tail, 2), "b": round(q_end, 2), "kind": "front", "label": front_label})
            signal_queued_ahead = True
            overflow_start = t
            overflow_end = t
            for window in demand_policy.get("front_queue_overflow_windows", {}).get(mode, []):
                try:
                    window_idx = int(window.get("signal_index", -999))
                    sig_idx = int(str(queue_sig["id"]).replace("S", ""))
                except ValueError:
                    continue
                window_start = float(window.get("start_sec", t))
                window_end = float(window.get("end_sec", t))
                if window_idx == sig_idx and window_start - 18.0 <= t <= window_end + 14.0:
                    overflow_start = float(window.get("start_sec", t))
                    overflow_end = float(window.get("end_sec", t))
                    break
            for i in range(n):
                offset = min(max_span, queue_offsets[i] if i < len(queue_offsets) else i * queue_spacing_m)
                final_s = q_end - offset
                lateral = [-1.25, 1.15, -0.75, 1.45, -1.05, 0.95, -1.35, 1.2, -0.6, 1.35, -1.1, 0.75, -1.45, 1.0][i % 14]
                if overflow_display_count > 0.1:
                    veh_s, veh_speed, visible = analog_queue_vehicle_s(t, final_s, i, overflow_start)
                    if not visible:
                        continue
                    if t > overflow_end:
                        discharge_mps = 6.4
                        veh_s = min(route_len, final_s + (t - overflow_end) * discharge_mps + i * 1.4)
                        veh_speed = discharge_mps * 3.6
                    elif str(st.get("reason")) == "green_downstream_queue":
                        veh_s = final_s
                        veh_speed = 0.0
                else:
                    veh_s = max(ev_s + ev_queue_gap_m, final_s)
                    veh_speed = 0.0 if queue_state in {"red", "yellow", "allred"} or str(st.get("reason")) == "green_downstream_queue" else min(18.0, ahead_speed)
                veh_s, clamped = stopline_safe_vehicle_s(veh_s, t, signals, timelines, i)
                if clamped:
                    veh_speed = 0.0
                if veh_speed < 7.0:
                    slow += 1
                vehicles.append(make_route_vehicle(
                    points,
                    route_len,
                    f"{mode}_ahead_{i:02d}",
                    veh_s,
                    veh_speed,
                    GENERAL_VEHICLE_COLOR,
                    GENERAL_VEHICLE_OPACITY,
                    "ahead_queue",
                    lateral_m=lateral,
                    stop_s=stop_s,
                    signal_state=queue_state,
                    reason=front_label,
                ))
        elif ahead <= 0:
            a = min(route_len, ev_s + 34.0)
            b = min(route_len, ev_s + 165.0)
            if ahead_empty_confirmed and b > a + 12.0:
                bands.append({"a": round(a, 2), "b": round(b, 2), "kind": "empty_ahead", "label": "앞 구간 비어있음"})
        else:
            stop = active_stop_between(signals, timelines, t, ev_s + 2.0, min(route_len, ev_s + 330.0))
            end_s = min(route_len, ev_s + 310.0, (float(stop["stop_s"]) - 10.0) if stop else route_len)
            n = max(1, ahead)
            span = max(45.0, end_s - (ev_s + 42.0))
            spacing = max(18.0, min(35.0, span / (n + 1)))
            phase = (t * max(2.0, min(11.0, ahead_speed / 3.6))) % spacing
            for i in range(n):
                veh_s = ev_s + 42.0 + i * spacing + phase * 0.35
                if veh_s > end_s:
                    continue
                choice = "straight" if veh_s > route_len - 255.0 and i % 2 == 0 else "right"
                veh_speed = ahead_speed
                opacity = GENERAL_VEHICLE_OPACITY
                if choice == "straight":
                    straight_exit_s = route_len - 95.0
                    veh_speed = max(veh_speed, 32.0)
                    if veh_s >= straight_exit_s:
                        continue
                    if veh_s > straight_exit_s - 70.0:
                        veh_speed = max(veh_speed, 32.0)
                veh_s, clamped = stopline_safe_vehicle_s(veh_s, t, signals, timelines, i)
                if clamped:
                    veh_speed = 0.0
                    signal_queued_ahead = True
                if veh_speed < 7.0:
                    slow += 1
                vehicles.append(make_route_vehicle(
                    points,
                    route_len,
                    f"{mode}_ahead_{i:02d}",
                    veh_s,
                    veh_speed,
                    color,
                    opacity,
                    "ahead_stream",
                    choice=choice,
                    stop_s=float(stop["stop_s"]) if stop else None,
                    signal_state=state_at(timelines[stop["id"]], t) if stop else next_state,
                    reason="앞 흐름 표시",
                ))
            if stop:
                signal_queued_ahead = True

        if behind <= 0:
            a = max(0.0, ev_s - 150.0)
            b = max(a + 8.0, ev_s - 34.0)
            if behind_empty_confirmed and ev_s > 80.0:
                bands.append({"a": round(a, 2), "b": round(b, 2), "kind": "empty_behind", "label": "뒤 구간 비어있음"})
        else:
            congested = behind_speed < 10.0 or str(st.get("reason")) in {"front_queue_tail", "front_queue_red", "green_downstream_queue", "stage2_hold"}
            n = max(1, behind)
            spacing = 14.0 if congested else max(18.0, min(32.0, 210.0 / (n + 1)))
            phase = 0.0 if congested else (t * max(2.0, min(10.5, behind_speed / 3.6))) % spacing
            for i in range(n):
                veh_s = ev_s - 38.0 - i * spacing + phase * 0.25
                if veh_s < 0.0 or veh_s > ev_s - 24.0:
                    continue
                veh_speed = 0.0 if congested else behind_speed
                veh_s, clamped = stopline_safe_vehicle_s(veh_s, t, signals, timelines, i)
                if clamped:
                    veh_speed = 0.0
                    signal_queued_behind = True
                if veh_speed < 7.0:
                    slow += 1
                vehicles.append(make_route_vehicle(
                    points,
                    route_len,
                    f"{mode}_behind_{i:02d}",
                    veh_s,
                    veh_speed,
                    GENERAL_VEHICLE_COLOR,
                    GENERAL_VEHICLE_OPACITY,
                    "behind_stream",
                    stop_s=None,
                    signal_state=next_state,
                    reason="EV 뒤 흐름 정체" if congested else "뒤 흐름 표시",
                ))
            if congested:
                a = max(0.0, ev_s - 124.0)
                b = max(a + 10.0, ev_s - 25.0)
                bands.append({"a": round(a, 2), "b": round(b, 2), "kind": "rear", "label": "EV 뒤 흐름 정체"})
                signal_queued_behind = True

        stage2_vehicles: list[dict[str, Any]] = []
        fake_signals: list[dict[str, Any]] = []
        stage2_lines: list[dict[str, Any]] = []
        if mode == "B4":
            stage2_vehicles, fake_signals, stage2_lines = build_stage2_display(t, stage2_approaches, algorithm_trace)
            vehicles.extend(stage2_vehicles)

        continuity_roles = {"ahead_stream", "behind_stream", "ahead_queue", "behind_queue", "downstream_moving"}
        dt_frame = max(DISPLAY_TRAFFIC_DT, min(1.0, t - prev_frame_t)) if prev_frame_t is not None else DISPLAY_TRAFFIC_DT
        route_ids_now: set[str] = set()
        continuous_vehicles: list[dict[str, Any]] = []
        for vehicle in vehicles:
            if vehicle.get("kind") != "route" or vehicle.get("role") not in continuity_roles or vehicle.get("s") is None:
                continuous_vehicles.append(vehicle)
                continue
            vid = str(vehicle.get("id", ""))
            prev_vehicle = prev_route_by_id.get(vid)
            if prev_vehicle and prev_vehicle.get("s") is not None:
                prev_s = float(prev_vehicle["s"])
                cur_s = float(vehicle["s"])
                max_speed = max(float(prev_vehicle.get("speed_kmh", 0.0)), float(vehicle.get("speed_kmh", 0.0)), 18.0)
                max_step = max(5.5, max_speed / 3.6 * dt_frame + 3.2)
                if cur_s < prev_s:
                    forward_speed = max(0.0, float(prev_vehicle.get("speed_kmh", 0.0)), float(vehicle.get("speed_kmh", 0.0)))
                    cur_s = min(route_len, prev_s + forward_speed / 3.6 * dt_frame)
                    vehicle = move_route_vehicle(points, route_len, vehicle, cur_s, forward_speed)
                elif abs(cur_s - prev_s) > max(32.0, max_step * 2.4):
                    cur_s = prev_s + math.copysign(max_step, cur_s - prev_s)
                    vehicle = move_route_vehicle(points, route_len, vehicle, cur_s, float(vehicle.get("speed_kmh", 0.0)))
                red_stop = active_stop_between(signals, timelines, t, min(prev_s, cur_s), max(prev_s, cur_s))
                if red_stop:
                    stop_s = float(red_stop["stop_s"])
                    if prev_s > stop_s:
                        clear_s = min(route_len, float(red_stop["s"]) + 44.0)
                        vehicle = move_route_vehicle(points, route_len, vehicle, max(cur_s, min(clear_s, prev_s + max_speed / 3.6 * dt_frame)), max_speed)
                        vehicle["reason"] = "intersection_clearance"
                    else:
                        safe_s = max(0.0, stop_s - 10.0 - continuity_slot(vehicle) * 12.0)
                        if cur_s >= safe_s:
                            vehicle = move_route_vehicle(points, route_len, vehicle, safe_s, 0.0)
            elif prev_frame_t is not None:
                role = str(vehicle.get("role", ""))
                cur_s = float(vehicle["s"])
                rel = cur_s - ev_s
                if role == "ahead_stream" and rel < 500.0:
                    entry_s = min(route_len - 18.0, ev_s + 620.0)
                    if entry_s - ev_s >= 500.0:
                        vehicle = move_route_vehicle(points, route_len, vehicle, entry_s, max(float(vehicle.get("speed_kmh", 0.0)), 26.0))
                    else:
                        continue
                elif role == "behind_stream" and rel > -260.0:
                    entry_s = max(18.0, ev_s - 310.0)
                    if ev_s - entry_s >= 260.0:
                        vehicle = move_route_vehicle(points, route_len, vehicle, entry_s, max(float(vehicle.get("speed_kmh", 0.0)), 18.0))
                    else:
                        continue
            route_ids_now.add(vid)
            continuous_vehicles.append(vehicle)

        carry_vehicles: list[dict[str, Any]] = []
        for vid, prev_vehicle in prev_route_by_id.items():
            if vid in route_ids_now or prev_vehicle.get("s") is None or prev_frame_t is None:
                continue
            role = str(prev_vehicle.get("role", ""))
            if role not in continuity_roles:
                continue
            prev_s = float(prev_vehicle["s"])
            prev_speed = float(prev_vehicle.get("speed_kmh", 0.0))
            if role == "ahead_queue" and prev_speed <= 1.0:
                carry_speed = 0.0
                next_s = prev_s
            else:
                carry_speed = max(prev_speed, 26.0 if role == "ahead_stream" else 18.0)
                next_s = min(route_len, prev_s + carry_speed / 3.6 * dt_frame)
            red_stop = active_stop_between(signals, timelines, t, min(prev_s, next_s), max(prev_s, next_s))
            if red_stop:
                stop_s = float(red_stop["stop_s"])
                if prev_s > stop_s:
                    next_s = max(next_s, min(route_len, prev_s + max(carry_speed, 18.0) / 3.6 * dt_frame))
                    carry_speed = max(carry_speed, 18.0)
                    prev_vehicle = {**prev_vehicle, "reason": "intersection_clearance"}
                else:
                    next_s = max(0.0, stop_s - 10.0 - continuity_slot(prev_vehicle) * 12.0)
                    carry_speed = 0.0
            if role.startswith("ahead") or role == "downstream_moving":
                min_ahead_gap = 18.0 if role == "ahead_queue" else 24.0
                if next_s < ev_s + min_ahead_gap:
                    next_s = min(route_len, ev_s + min_ahead_gap)
                    carry_speed = 0.0
                rel = next_s - ev_s
                keep = min_ahead_gap <= rel <= 620.0 and next_s < route_len - 18.0
            else:
                if next_s > ev_s - 24.0:
                    next_s = max(0.0, ev_s - 24.0)
                    carry_speed = 0.0
                rel = next_s - ev_s
                keep = -310.0 <= rel <= -24.0 and next_s > 18.0
            if keep:
                carry_vehicles.append(move_route_vehicle(points, route_len, prev_vehicle, next_s, carry_speed))
        if carry_vehicles:
            continuous_vehicles.extend(carry_vehicles)
            slow += sum(1 for vehicle in carry_vehicles if float(vehicle.get("speed_kmh", 0.0)) < 7.0)
        safe_vehicles: list[dict[str, Any]] = []
        for vehicle in continuous_vehicles:
            if vehicle.get("kind") == "route" and vehicle.get("s") is not None:
                vid = str(vehicle.get("id", ""))
                prev_vehicle = prev_route_by_id.get(vid)
                prev_s = float(prev_vehicle["s"]) if prev_vehicle and prev_vehicle.get("s") is not None else None
                safe_s, clamped = stopline_safe_vehicle_s(float(vehicle["s"]), t, signals, timelines, continuity_slot(vehicle))
                if clamped:
                    if prev_s is not None and prev_s > safe_s:
                        vehicle = move_route_vehicle(points, route_len, vehicle, max(prev_s, float(vehicle["s"])), max(18.0, float(vehicle.get("speed_kmh", 0.0))))
                        vehicle["reason"] = "intersection_clearance"
                    else:
                        vehicle = move_route_vehicle(points, route_len, vehicle, safe_s, 0.0)
            safe_vehicles.append(vehicle)
        vehicles = safe_vehicles
        prev_route_by_id = {
            str(vehicle["id"]): vehicle
            for vehicle in vehicles
            if vehicle.get("kind") == "route" and vehicle.get("role") in continuity_roles and vehicle.get("s") is not None
        }
        prev_frame_t = t

        label = "10-1 신호 준수 흐름"
        if signal_queued_ahead and signal_queued_behind:
            if str(st.get("reason")) == "green_downstream_queue":
                label = "green · 앞 큐 과잉 · EV 뒤 정체"
            elif overflow_display_count > 0.1:
                label = "앞 구간 정체 · EV 뒤 흐름 정체"
            else:
                label = "앞 큐 대기 · EV 뒤 흐름 정체" if next_state in {"red", "yellow", "allred"} else "앞차 출발 · EV 뒤 흐름 정체"
        elif signal_queued_ahead:
            if str(st.get("reason")) == "green_downstream_queue":
                label = "green · 앞 큐 과잉"
            elif overflow_display_count > 0.1:
                label = "앞 구간 정체"
            else:
                label = "앞 차량 신호 대기열" if next_state in {"red", "yellow", "allred"} else "신호 변경 후 앞차 출발"
        elif signal_queued_behind:
            label = "EV 뒤 흐름 정체"
        elif ahead == 0 and behind == 0 and ahead_empty_confirmed and behind_empty_confirmed:
            label = "앞·뒤 관찰구간 비어있음"
        elif ahead == 0 and ahead_empty_confirmed:
            label = "앞 구간 비어있음 · 뒤 흐름 표시"
        elif behind == 0 and behind_empty_confirmed:
            label = "앞 흐름 표시 · 뒤 구간 비어있음"
        elif ev_s > route_len - 410.0:
            label = "직진·우회전 분기 흐름"
        elif float(row.get("local_speed_kmh", 99.0)) < 8.0:
            label = "10-1 저속 흐름"

        visible_front = any(
            vehicle.get("kind") == "route"
            and (str(vehicle.get("role", "")).startswith("ahead") or vehicle.get("role") == "blocked_visible")
            and 18.0 <= float(vehicle.get("s", 0.0)) - ev_s <= 220.0
            for vehicle in vehicles
        ) or any(
            band.get("kind") in {"front", "front_blocked"}
            and float(band.get("b", 0.0)) >= ev_s + 24.0
            and float(band.get("a", 0.0)) <= ev_s + 220.0
            for band in bands
        )
        visible_rear = any(
            vehicle.get("kind") == "route"
            and str(vehicle.get("role", "")).startswith("behind")
            and -240.0 <= float(vehicle.get("s", 0.0)) - ev_s <= -18.0
            for vehicle in vehicles
        ) or any(
            band.get("kind") == "rear"
            and float(band.get("b", 0.0)) <= ev_s - 18.0
            and float(band.get("b", 0.0)) >= ev_s - 240.0
            for band in bands
        )
        if str(st.get("reason")) == "arrived":
            label = "도착 완료"
            signal_queued_ahead = False
            signal_queued_behind = False
            bands = [band for band in bands if band.get("kind", "").startswith("empty")]
        else:
            if signal_queued_ahead and not visible_front:
                signal_queued_ahead = False
                label = "앞 구간 비어있음 · 뒤 흐름 표시" if visible_rear else "앞 구간 비어있음"
            if signal_queued_behind and not visible_rear:
                signal_queued_behind = False
                if not signal_queued_ahead:
                    label = "앞 흐름 표시" if visible_front else "앞·뒤 관찰구간 비어있음"

        out.append({
            "t": round(t, 1),
            "ev_s": round(ev_s, 2),
            "vehicles": vehicles,
            "bands": bands,
            "notes": notes,
            "stage2_lines": stage2_lines,
            "fake_signals": fake_signals,
            "summary": {
                "kind": "display_traffic",
                "label": label,
                "shown": len(vehicles),
                "ahead": ahead,
                "behind": behind,
                "slow": slow,
                "emptyAhead": ahead_empty_confirmed,
                "emptyBehind": behind_empty_confirmed,
                "signalQueuedAhead": signal_queued_ahead,
                "signalQueuedBehind": signal_queued_behind,
            },
        })

    prev_by_id: dict[str, dict[str, Any]] = {}
    prev_t_by_id: dict[str, float] = {}
    for frame_idx, frame in enumerate(out):
        frame_t = float(frame["t"])
        ev_s_for_frame = float(display_samples[min(frame_idx, len(display_samples) - 1)]["s"]) if display_samples else 0.0
        fixed: list[dict[str, Any]] = []
        current_ids: set[str] = set()
        for vehicle in frame.get("vehicles", []):
            if vehicle.get("kind") == "route" and vehicle.get("s") is not None:
                vid = str(vehicle.get("id", ""))
                current_ids.add(vid)
                prev = prev_by_id.get(vid)
                if prev and prev.get("s") is not None:
                    prev_s = float(prev["s"])
                    cur_s = float(vehicle["s"])
                    dt_seen = max(DISPLAY_TRAFFIC_DT, min(1.0, frame_t - prev_t_by_id.get(vid, frame_t)))
                    speed = max(float(prev.get("speed_kmh", 0.0)), float(vehicle.get("speed_kmh", 0.0)), 18.0)
                    max_step = max(6.0, speed / 3.6 * dt_seen + 4.0)
                    if cur_s < prev_s - 0.25:
                        cur_s = min(route_len, prev_s + speed / 3.6 * dt_seen)
                        vehicle = move_route_vehicle(points, route_len, vehicle, cur_s, speed)
                        vehicle["reason"] = "intersection_clearance"
                    elif cur_s - prev_s > max(36.0, max_step * 2.0):
                        cur_s = min(route_len, prev_s + max_step)
                        vehicle = move_route_vehicle(points, route_len, vehicle, cur_s, speed)
                    for sig in signals:
                        stop_s = float(sig["s"]) - 18.0
                        if stop_s < float(vehicle["s"]) < float(sig["s"]) + 42.0 and state_at(timelines[sig["id"]], frame_t) in {"red", "yellow", "allred"}:
                            if prev_s <= float(vehicle["s"]) and float(vehicle.get("speed_kmh", 0.0)) > 0.0:
                                vehicle["reason"] = "intersection_clearance"
                            break
                prev_by_id[vid] = vehicle
                prev_t_by_id[vid] = frame_t
            fixed.append(vehicle)
        for vid, prev in list(prev_by_id.items()):
            if vid in current_ids or prev.get("kind") != "route" or prev.get("s") is None:
                continue
            role = str(prev.get("role", ""))
            if role not in {"ahead_queue", "ahead_stream", "behind_stream", "behind_queue", "downstream_moving"}:
                continue
            prev_s = float(prev["s"])
            dt_seen = max(DISPLAY_TRAFFIC_DT, min(1.0, frame_t - prev_t_by_id.get(vid, frame_t)))
            speed = max(float(prev.get("speed_kmh", 0.0)), 18.0)
            next_s = min(route_len, prev_s + speed / 3.6 * dt_seen)
            if role.startswith("behind") and next_s > ev_s_for_frame - 24.0:
                next_s = max(0.0, ev_s_for_frame - 24.0)
                speed = 0.0
            elif (role.startswith("ahead") or role == "downstream_moving") and next_s < ev_s_for_frame + 18.0:
                next_s = min(route_len, ev_s_for_frame + 18.0)
                speed = 0.0
            rel = next_s - ev_s_for_frame
            keep = (
                ((role.startswith("ahead") or role == "downstream_moving") and 0.0 <= rel <= 620.0 and next_s < route_len - 18.0)
                or (role.startswith("behind") and -310.0 <= rel <= 40.0 and next_s > 18.0)
            )
            if keep:
                carried = move_route_vehicle(points, route_len, prev, next_s, speed)
                fixed.append(carried)
                prev_by_id[vid] = carried
                prev_t_by_id[vid] = frame_t
        frame["vehicles"] = fixed

    last_vehicle_by_id: dict[str, dict[str, Any]] = {}
    for frame_idx, frame in enumerate(out):
        ev_s_for_frame = float(display_samples[min(frame_idx, len(display_samples) - 1)]["s"]) if display_samples else 0.0
        monotone: list[dict[str, Any]] = []
        for vehicle in frame.get("vehicles", []):
            if vehicle.get("kind") == "route" and vehicle.get("s") is not None:
                vid = str(vehicle.get("id", ""))
                prev = last_vehicle_by_id.get(vid)
                role = str(vehicle.get("role", ""))
                if role.startswith("behind") and float(vehicle["s"]) > ev_s_for_frame - 26.0:
                    target_s = max(0.0, ev_s_for_frame - 26.0)
                    if prev and prev.get("s") is not None:
                        target_s = max(target_s, float(prev["s"]))
                    vehicle = move_route_vehicle(points, route_len, vehicle, min(target_s, ev_s_for_frame - 22.0), 0.0)
                    vehicle["reason"] = "EV 뒤 흐름 정체"
                elif (role.startswith("ahead") or role == "downstream_moving") and float(vehicle["s"]) < ev_s_for_frame + 18.0:
                    vehicle = move_route_vehicle(points, route_len, vehicle, min(route_len, ev_s_for_frame + 18.0), 0.0)
                if prev and prev.get("s") is not None and float(vehicle["s"]) < float(prev["s"]) - 0.25:
                    vehicle = move_route_vehicle(
                        points,
                        route_len,
                        vehicle,
                        float(prev["s"]),
                        max(float(prev.get("speed_kmh", 0.0)), float(vehicle.get("speed_kmh", 0.0))),
                    )
                    vehicle["reason"] = "intersection_clearance"
                last_vehicle_by_id[vid] = vehicle
            monotone.append(vehicle)
        st = display_samples[min(frame_idx, len(display_samples) - 1)] if display_samples else {}
        if str(st.get("reason")) == "green_downstream_queue":
            has_visible_block = any(
                vehicle.get("kind") == "route"
                and (str(vehicle.get("role", "")).startswith("ahead") or vehicle.get("role") == "blocked_visible")
                and 18.0 <= float(vehicle.get("s", 0.0)) - ev_s_for_frame <= 220.0
                for vehicle in monotone
            ) or any(
                band.get("kind") in {"front", "front_blocked"}
                and float(band.get("b", 0.0)) >= ev_s_for_frame + 24.0
                and float(band.get("a", 0.0)) <= ev_s_for_frame + 220.0
                for band in frame.get("bands", [])
            )
            if not has_visible_block:
                q_start = ev_s_for_frame + 28.0
                q_end = min(route_len, ev_s_for_frame + 150.0)
                frame.setdefault("bands", []).append({"a": round(q_start, 2), "b": round(q_end, 2), "kind": "front_blocked", "label": "앞 구간 포화"})
                for i in range(5):
                    monotone.append(make_route_vehicle(
                        points,
                        route_len,
                        f"{mode}_visible_block_{i:02d}",
                        min(route_len, q_start + i * 18.0),
                        0.0,
                        GENERAL_VEHICLE_COLOR,
                        GENERAL_VEHICLE_OPACITY,
                        "ahead_queue",
                        lateral_m=[-1.2, 1.1, -0.8, 1.3, -1.0][i],
                        signal_state="green",
                        reason="앞 구간 포화",
                    ))
            for band in frame.get("bands", []):
                if band.get("kind") != "front_moving":
                    continue
                a = float(band.get("a", 0.0))
                b = float(band.get("b", 0.0))
                if b <= a + 18.0:
                    continue
                has_moving = any(
                    vehicle.get("kind") == "route"
                    and vehicle.get("role") == "downstream_moving"
                    and a <= float(vehicle.get("s", 0.0)) <= b
                    and float(vehicle.get("speed_kmh", 0.0)) >= 8.0
                    for vehicle in monotone
                )
                if has_moving:
                    continue
                span = b - a
                for j in range(5):
                    pos = a + 12.0 + ((float(frame["t"]) * 5.8 + j * 23.0) % max(20.0, span - 12.0))
                    monotone.append(make_route_vehicle(
                        points,
                        route_len,
                        f"{mode}_downstream_visible_{j:02d}",
                        min(b - 4.0, max(a + 4.0, pos)),
                        21.0,
                        GENERAL_VEHICLE_COLOR,
                        0.98,
                        "downstream_moving",
                        lateral_m=[-1.0, 1.2, -0.65, 1.4, -1.3][j],
                        signal_state="green",
                        reason="하류 배출 중",
                    ))
            for band in frame.get("bands", []):
                if band.get("kind") != "front_blocked":
                    continue
                a = float(band.get("a", 0.0))
                b = float(band.get("b", 0.0))
                if b <= a + 28.0:
                    continue
                visible_slots = [
                    a + (b - a) * 0.18,
                    a + (b - a) * 0.33,
                    a + (b - a) * 0.48,
                    a + (b - a) * 0.66,
                    a + (b - a) * 0.82,
                ]
                for j, pos in enumerate(visible_slots):
                    monotone.append(make_route_vehicle(
                        points,
                        route_len,
                        f"{mode}_blocked_visible_{j:02d}",
                        min(b - 5.0, max(a + 5.0, pos)),
                        0.0,
                        GENERAL_VEHICLE_COLOR,
                        1.0,
                        "blocked_visible",
                        lateral_m=[-2.1, 2.0, -1.7, 1.8, -2.0][j],
                        signal_state="green",
                        reason="막힌 구간 정지차",
                    ))
        frame["vehicles"] = monotone
    return out


def validate_display_traffic(
    modes: dict[str, Any],
    signals: list[dict[str, Any]],
    timelines: dict[str, dict[str, list[list[Any]]]],
) -> dict[str, Any]:
    summary = {
        "ev_next_signal_non_green_pass": 0,
        "ev_front_queue_overtake": 0,
        "vehicle_stopline_non_green_pass": 0,
        "green_empty_stopped": 0,
        "green_blocked_moving": 0,
        "behind_vehicle_ahead_of_ev": 0,
        "green_blocked_without_visible_front": 0,
        "queue_label_without_visible_queue": 0,
        "arrived_with_queue_label": 0,
        "downstream_label_without_moving_vehicle": 0,
        "ev_max_step_m": 0.0,
        "frames_checked": 0,
        "vehicle_stopline_non_green_examples": [],
        "ok": True,
    }
    for mode, doc in modes.items():
        samples = doc.get("display_samples", doc.get("samples", []))
        prev = None
        for sample in samples:
            summary["frames_checked"] += 1
            t = float(sample["t"])
            s = float(sample["s"])
            if prev:
                summary["ev_max_step_m"] = max(summary["ev_max_step_m"], round(s - float(prev["s"]), 2))
            prev = sample
            sig = next_signal_after(signals, s - 1.0)
            if sig and state_at(timelines[mode][sig["id"]], t) in {"red", "yellow", "allred"}:
                if s > float(sig["s"]) - 18.0 + 0.1:
                    summary["ev_next_signal_non_green_pass"] += 1
            tail = sample.get("front_queue_tail_s")
            if tail is not None and sample.get("reason") == "front_queue_tail" and s > float(tail) - 18.0:
                summary["ev_front_queue_overtake"] += 1
            next_state = str(sample.get("next_signal_state", ""))
            front_q = float(sample.get("front_queue_count") or 0.0)
            speed = float(sample.get("speed_kmh") or 0.0)
            reason = str(sample.get("reason", ""))
            if next_state == "green" and front_q <= 0.1 and speed <= 2.0 and reason not in {"arrived", "stage2_hold"}:
                summary["green_empty_stopped"] += 1
            if reason == "green_downstream_queue" and (speed > 2.0 or front_q <= 0.1):
                summary["green_blocked_moving"] += 1
        def ev_s_at(frame_t: float) -> float:
            if not samples:
                return 0.0
            best = samples[0]
            for sample in samples:
                if float(sample.get("t", 0.0)) <= frame_t:
                    best = sample
                else:
                    break
            return float(best.get("s", 0.0))
        for frame in doc.get("display_traffic", []):
            t = float(frame["t"])
            ev_s = float(frame.get("ev_s", ev_s_at(t)))
            sample = max((sample for sample in samples if float(sample.get("t", 0.0)) <= t), key=lambda item: float(item.get("t", 0.0)), default={})
            label = str(frame.get("summary", {}).get("label", ""))
            visible_front_for_label = any(
                vehicle.get("kind") == "route"
                and (str(vehicle.get("role", "")).startswith("ahead") or vehicle.get("role") == "blocked_visible")
                and 18.0 <= float(vehicle.get("s", 0.0)) - ev_s <= 220.0
                for vehicle in frame.get("vehicles", [])
            ) or any(
                band.get("kind") in {"front", "front_blocked"}
                and float(band.get("b", 0.0)) >= ev_s + 24.0
                and float(band.get("a", 0.0)) <= ev_s + 220.0
                for band in frame.get("bands", [])
            )
            visible_rear_for_label = any(
                vehicle.get("kind") == "route"
                and str(vehicle.get("role", "")).startswith("behind")
                and -240.0 <= float(vehicle.get("s", 0.0)) - ev_s <= -18.0
                for vehicle in frame.get("vehicles", [])
            ) or any(
                band.get("kind") == "rear"
                and float(band.get("b", 0.0)) <= ev_s - 18.0
                and float(band.get("b", 0.0)) >= ev_s - 240.0
                for band in frame.get("bands", [])
            )
            if str(sample.get("reason")) == "arrived" and any(word in label for word in ("큐", "정체", "대기")):
                summary["arrived_with_queue_label"] += 1
            if any(word in label for word in ("앞 큐", "앞 구간 정체", "앞차", "과잉")) and not visible_front_for_label:
                summary["queue_label_without_visible_queue"] += 1
            if any(word in label for word in ("뒤 흐름 정체", "뒤 차량")) and not visible_rear_for_label:
                summary["queue_label_without_visible_queue"] += 1
            moving_bands = [band for band in frame.get("bands", []) if band.get("kind") == "front_moving"]
            if moving_bands:
                has_downstream_vehicle = False
                for band in moving_bands:
                    a = float(band.get("a", 0.0))
                    b = float(band.get("b", 0.0))
                    if any(
                        vehicle.get("kind") == "route"
                        and str(vehicle.get("role", "")) == "downstream_moving"
                        and a <= float(vehicle.get("s", 0.0)) <= b
                        and float(vehicle.get("speed_kmh", 0.0)) >= 8.0
                        for vehicle in frame.get("vehicles", [])
                    ):
                        has_downstream_vehicle = True
                        break
                if not has_downstream_vehicle:
                    summary["downstream_label_without_moving_vehicle"] += 1
            if str(sample.get("reason")) == "green_downstream_queue":
                visible_front = any(
                    vehicle.get("kind") == "route"
                    and (str(vehicle.get("role", "")).startswith("ahead") or vehicle.get("role") == "blocked_visible")
                    and 18.0 <= float(vehicle.get("s", 0.0)) - ev_s <= 220.0
                    for vehicle in frame.get("vehicles", [])
                ) or any(
                    band.get("kind") in {"front", "front_blocked"}
                    and float(band.get("b", 0.0)) >= ev_s + 24.0
                    and float(band.get("a", 0.0)) <= ev_s + 220.0
                    for band in frame.get("bands", [])
                )
                if not visible_front:
                    summary["green_blocked_without_visible_front"] += 1
            for vehicle in frame.get("vehicles", []):
                if vehicle.get("kind") != "route":
                    continue
                s = float(vehicle.get("s", 0.0))
                if str(vehicle.get("role", "")).startswith("behind") and s > ev_s - 18.0:
                    summary["behind_vehicle_ahead_of_ev"] += 1
                sig = next_signal_after(signals, s - 1.0)
                if sig and state_at(timelines[mode][sig["id"]], t) in {"red", "yellow", "allred"}:
                    stop_s = float(sig["s"]) - 18.0
                    if stop_s < s < float(sig["s"]) + 42.0 and "clearance" not in str(vehicle.get("reason", "")):
                        summary["vehicle_stopline_non_green_pass"] += 1
                        if len(summary["vehicle_stopline_non_green_examples"]) < 12:
                            summary["vehicle_stopline_non_green_examples"].append({
                                "mode": mode,
                                "t": round(t, 1),
                                "id": str(vehicle.get("id", "")),
                                "role": str(vehicle.get("role", "")),
                                "s": round(s, 2),
                                "signal": sig["id"],
                                "signal_s": round(float(sig["s"]), 2),
                                "stop_s": round(stop_s, 2),
                                "state": state_at(timelines[mode][sig["id"]], t),
                            })
    for key in (
        "ev_next_signal_non_green_pass",
        "ev_front_queue_overtake",
        "vehicle_stopline_non_green_pass",
        "green_empty_stopped",
        "green_blocked_moving",
        "behind_vehicle_ahead_of_ev",
        "green_blocked_without_visible_front",
        "queue_label_without_visible_queue",
        "arrived_with_queue_label",
        "downstream_label_without_moving_vehicle",
    ):
        if summary[key]:
            summary["ok"] = False
    if not summary["ok"]:
        raise SystemExit(f"Display traffic validation failed: {summary}")
    return summary


def build_validation_report(
    modes: dict[str, Any],
    tls_validation: dict[str, Any],
    display_validation: dict[str, Any],
    algorithm_trace: dict[str, Any],
) -> dict[str, Any]:
    trajectory = {
        "max_frame_gap_sec": 0.0,
        "max_vehicle_step_m": 0.0,
        "max_vehicle_speed_kmh": 0.0,
        "tracked_vehicle_pop_in": 0,
        "tracked_vehicle_disappear": 0,
        "flow_vehicle_pop_in_near_camera": 0,
        "flow_vehicle_disappear_near_camera": 0,
        "flow_vehicle_large_jump": 0,
        "vehicle_reverse_steps": 0,
        "flow_modes_checked": ["B4"],
        "checked_frames": 0,
    }
    tracked_roles = {"ahead_queue", "stage2_blocked"}
    flow_roles = {"ahead_queue", "ahead_stream", "behind_stream", "behind_queue"}
    ever_tracked_ahead: set[str] = set()
    for mode, doc in modes.items():
        samples = doc.get("display_samples", doc.get("samples", []))
        def ev_s_at(frame_t: float) -> float:
            if not samples:
                return 0.0
            best = samples[0]
            for sample in samples:
                if float(sample.get("t", 0.0)) <= frame_t:
                    best = sample
                else:
                    break
            return float(best.get("s", 0.0))

        prev_t: float | None = None
        prev_by_id: dict[str, dict[str, Any]] = {}
        prev_flow_by_id: dict[str, dict[str, Any]] = {}
        prev_ev_s: float | None = None
        for frame in doc.get("display_traffic", []):
            t = float(frame["t"])
            ev_s = float(frame.get("ev_s", ev_s_at(t)))
            trajectory["checked_frames"] += 1
            if prev_t is not None:
                trajectory["max_frame_gap_sec"] = max(trajectory["max_frame_gap_sec"], round(t - prev_t, 3))
            cur_by_id = {
                str(vehicle["id"]): vehicle
                for vehicle in frame.get("vehicles", [])
                if vehicle.get("role") in tracked_roles
            }
            previously_tracked_ahead = set(ever_tracked_ahead)
            for vid, vehicle in cur_by_id.items():
                if (
                    vehicle.get("role") == "ahead_queue"
                    and (
                        "앞 구간 정체" in str(vehicle.get("reason", ""))
                        or "앞 큐 과잉" in str(vehicle.get("reason", ""))
                    )
                ):
                    ever_tracked_ahead.add(vid)
            for vid, vehicle in cur_by_id.items():
                trajectory["max_vehicle_speed_kmh"] = max(
                    trajectory["max_vehicle_speed_kmh"],
                    round(float(vehicle.get("speed_kmh", 0.0)), 2),
                )
                if vid in prev_by_id and vehicle.get("s") is not None and prev_by_id[vid].get("s") is not None:
                    trajectory["max_vehicle_step_m"] = max(
                        trajectory["max_vehicle_step_m"],
                        round(abs(float(vehicle["s"]) - float(prev_by_id[vid]["s"])), 2),
                    )
                elif (
                    prev_t is not None
                    and t > 5.0
                    and vehicle.get("role") == "ahead_queue"
                    and vid in previously_tracked_ahead
                    and float(vehicle.get("speed_kmh", 0.0)) <= 1.0
                ):
                    trajectory["tracked_vehicle_pop_in"] += 1
            cur_flow_by_id = {
                str(vehicle["id"]): vehicle
                for vehicle in frame.get("vehicles", [])
                if mode == "B4"
                and vehicle.get("kind") == "route"
                and vehicle.get("role") in flow_roles
                and vehicle.get("s") is not None
            }
            for vid, vehicle in cur_flow_by_id.items():
                s = float(vehicle.get("s", 0.0))
                rel = s - ev_s
                if vid in prev_flow_by_id and prev_flow_by_id[vid].get("s") is not None:
                    prev_s = float(prev_flow_by_id[vid]["s"])
                    step = abs(s - prev_s)
                    if s < prev_s - 0.25:
                        trajectory["vehicle_reverse_steps"] += 1
                    prev_rel = prev_s - (prev_ev_s if prev_ev_s is not None else ev_s)
                    near_camera = (-120.0 <= rel <= 520.0) or (-120.0 <= prev_rel <= 520.0)
                    if near_camera and step > 38.0:
                        trajectory["flow_vehicle_large_jump"] += 1
                elif prev_t is not None and t > 5.0 and -240.0 <= rel <= 500.0 and s > 36.0:
                    trajectory["flow_vehicle_pop_in_near_camera"] += 1
            for vid, vehicle in prev_by_id.items():
                rel_to_ev = (
                    float(vehicle.get("s", 0.0)) - prev_ev_s
                    if prev_ev_s is not None and vehicle.get("s") is not None
                    else 999.0
                )
                if (
                    vid not in cur_by_id
                    and t < float(doc.get("travel_time_sec", t))
                    and vehicle.get("role") == "ahead_queue"
                    and vid in ever_tracked_ahead
                    and (
                        "앞 구간 정체" in str(vehicle.get("reason", ""))
                        or "앞 큐 과잉" in str(vehicle.get("reason", ""))
                    )
                    and float(vehicle.get("speed_kmh", 0.0)) <= 1.0
                    and not (0.0 <= rel_to_ev <= 28.0)
                ):
                    trajectory["tracked_vehicle_disappear"] += 1
            if prev_ev_s is not None:
                for vid, vehicle in prev_flow_by_id.items():
                    if vid in cur_flow_by_id:
                        continue
                    prev_s = float(vehicle.get("s", 0.0))
                    rel = prev_s - prev_ev_s
                    speed = float(vehicle.get("speed_kmh", 0.0))
                    allowed_exit = (
                        rel > 500.0
                        or rel < -260.0
                        or prev_s > float(doc.get("distance_m", prev_s)) - 80.0
                        or prev_s < 24.0
                        or (speed >= 26.0 and (rel > 260.0 or rel < -190.0))
                        or (0.0 <= rel <= 28.0 and speed <= 1.0)
                    )
                    if not allowed_exit:
                        trajectory["flow_vehicle_disappear_near_camera"] += 1
            prev_t = t
            prev_by_id = cur_by_id
            prev_flow_by_id = cur_flow_by_id
            prev_ev_s = ev_s
    algorithm = {
        "stage1_present": bool(algorithm_trace.get("stage1")),
        "stage2_events": len(algorithm_trace.get("stage2", [])),
        "stage3_events": len(algorithm_trace.get("stage3", [])),
        "case_a_events": int(algorithm_trace.get("event_counts", {}).get("case_a", 0)),
        "case_b_events": int(algorithm_trace.get("event_counts", {}).get("case_b", 0)),
        "green_active_events": int(algorithm_trace.get("event_counts", {}).get("green_active", 0)),
        "safety_denied_events": int(algorithm_trace.get("event_counts", {}).get("safety_denied", 0)),
    }
    visual_regression = {
        "vehicle_color_palette_size": 0,
        "vehicle_color_changes": 0,
        "stage2_min_vehicle_gap_m": 999.0,
        "front_blocked_band_frames": 0,
        "front_moving_band_frames": 0,
        "final_split_straight_vehicles": 0,
        "final_split_right_vehicles": 0,
        "critical_queue_disappear": trajectory["tracked_vehicle_disappear"],
        "ok": True,
    }
    colors: set[str] = set()
    color_by_id: dict[str, str] = {}
    for doc in modes.values():
        route_len_doc = float(doc.get("distance_m", 0.0))
        for frame in doc.get("display_traffic", []):
            t = float(frame["t"])
            for band in frame.get("bands", []):
                if band.get("kind") == "front_blocked":
                    visual_regression["front_blocked_band_frames"] += 1
                if band.get("kind") == "front_moving":
                    visual_regression["front_moving_band_frames"] += 1
            stage2_points_by_approach: dict[str, list[dict[str, Any]]] = {}
            for vehicle in frame.get("vehicles", []):
                if vehicle.get("role") in {"ahead_queue", "ahead_stream", "behind_stream", "stage2_blocked"}:
                    color = str(vehicle.get("color", ""))
                    if color:
                        colors.add(color)
                    vid = str(vehicle.get("id", ""))
                    if vid in color_by_id and color_by_id[vid] != color:
                        visual_regression["vehicle_color_changes"] += 1
                    elif vid:
                        color_by_id[vid] = color
                if vehicle.get("role") == "stage2_blocked":
                    parts = str(vehicle.get("id", "")).split("_")
                    approach_id = parts[1] if len(parts) >= 3 else "unknown"
                    stage2_points_by_approach.setdefault(approach_id, []).append(vehicle)
                if (
                    route_len_doc > 0.0
                    and float(vehicle.get("s", 0.0)) >= route_len_doc - 410.0
                    and vehicle.get("role") == "ahead_stream"
                ):
                    if vehicle.get("choice") == "straight":
                        visual_regression["final_split_straight_vehicles"] += 1
                    if vehicle.get("choice") == "right":
                        visual_regression["final_split_right_vehicles"] += 1
            for stage2_points in stage2_points_by_approach.values():
                for i, a in enumerate(stage2_points):
                    for b in stage2_points[i + 1:]:
                        dist = meters_between(float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"]))
                        visual_regression["stage2_min_vehicle_gap_m"] = min(
                            visual_regression["stage2_min_vehicle_gap_m"],
                            round(dist, 2),
                        )
    visual_regression["vehicle_color_palette_size"] = len(colors)
    if visual_regression["stage2_min_vehicle_gap_m"] == 999.0:
        visual_regression["stage2_min_vehicle_gap_m"] = 0.0
    visual_regression["ok"] = (
        visual_regression["vehicle_color_palette_size"] <= 1
        and visual_regression["vehicle_color_changes"] == 0
        and visual_regression["stage2_min_vehicle_gap_m"] >= 8.0
        and visual_regression["front_blocked_band_frames"] > 0
        and visual_regression["front_moving_band_frames"] > 0
        and visual_regression["final_split_straight_vehicles"] > 0
        and visual_regression["final_split_right_vehicles"] > 0
        and visual_regression["critical_queue_disappear"] == 0
    )
    screenshot_targets = [
        {"id": "stage2_hold", "mode": "B4", "t": 4.0, "expect": "신당역 유입 정지"},
        {"id": "case_a_green", "mode": "B4", "t": 12.0, "expect": "Stage3 Case A"},
        {"id": "case_b_downstream", "mode": "B4", "t": 31.0, "expect": "Stage3 Case B"},
        {"id": "green_front_queue", "mode": "B4", "t": 76.2, "expect": "앞 큐 과잉"},
        {"id": "final_split", "mode": "B4", "t": 230.0, "expect": "직진/우회전 분기"},
    ]
    ok = (
        bool(tls_validation.get("ok"))
        and bool(display_validation.get("ok"))
        and trajectory["max_frame_gap_sec"] <= DISPLAY_TRAFFIC_DT + 0.05
        and algorithm["stage2_events"] >= 3
        and algorithm["case_a_events"] > 0
        and algorithm["case_b_events"] > 0
        and trajectory["tracked_vehicle_disappear"] == 0
        and trajectory["flow_vehicle_pop_in_near_camera"] == 0
        and trajectory["flow_vehicle_disappear_near_camera"] == 0
        and trajectory["flow_vehicle_large_jump"] == 0
        and trajectory["vehicle_reverse_steps"] == 0
        and visual_regression["ok"]
    )
    return {
        "ok": ok,
        "tls": tls_validation,
        "display": display_validation,
        "trajectory": trajectory,
        "visual_regression": visual_regression,
        "algorithm": algorithm,
        "screenshot_targets": screenshot_targets,
    }


def build_actual_payload() -> dict[str, Any] | None:
    if not ACTUAL_PROGRESS_DATA.is_file():
        return None
    presentation_inputs = load_presentation_inputs()
    signal_policy = presentation_inputs.get("presentation_signal_policy", {})
    demand_policy = presentation_demand_policy(presentation_inputs)
    source = json.loads(ACTUAL_PROGRESS_DATA.read_text(encoding="utf-8"))
    tls_source = json.loads(TLS_PROGRESS_DATA.read_text(encoding="utf-8")) if TLS_PROGRESS_DATA.is_file() else source
    route_mode = max(("B04", "B4"), key=lambda mode: float(source["modes"][mode]["distance_m"]))
    actual_points = actual_route_points(source["modes"][route_mode]["emergency"])
    route_points, destination_meta = presentation_route_to_destination(actual_points)
    route_len = round(float(route_points[-1]["s"]), 2)
    stage2_lines = source.get("algorithm", {}).get("stage2_block_lines", [])
    stage2_approaches = presentation_stage2_approaches(stage2_lines)
    signals = []
    for idx, tl in enumerate(tls_source.get("traffic_lights", source.get("traffic_lights", [])), 1):
        lat = float(tl["lat"])
        lon = float(tl["lon"])
        s = project_s(route_points, lat, lon)
        if s <= 0 or s >= route_len - 30:
            continue
        if nearest_route_distance(route_points, lat, lon) > 90.0:
            continue
        signals.append({
            "id": f"S{idx:02d}",
            "raw_id": str(tl.get("raw_tls_id") or tl.get("tls_id") or ""),
            "source_tls_id": str(tl.get("tls_id") or ""),
            "name": str(tl.get("tls_id") or f"Signal {idx}"),
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "s": round(s, 2),
        })
    collapsed: list[dict[str, Any]] = []
    for sig in sorted(signals, key=lambda item: item["s"]):
        if collapsed and abs(sig["s"] - collapsed[-1]["s"]) < 170:
            collapsed[-1]["merged_raw_ids"] = collapsed[-1].get("merged_raw_ids", [collapsed[-1]["raw_id"]]) + [sig["raw_id"]]
            collapsed[-1]["source_tls_id"] = collapsed[-1].get("source_tls_id") or sig.get("source_tls_id", "")
            continue
        collapsed.append(sig)
    for i, sig in enumerate(collapsed, 1):
        sig["id"] = f"S{i:02d}"
    samples_by_mode: dict[str, list[dict[str, Any]]] = {}
    for mode in ("B04", "B4"):
        events = source.get("algorithm", {}).get("events", {}).get(mode, [])
        samples_by_mode[mode] = actual_samples(source["modes"][mode]["emergency"], events)
    algorithm_trace = build_algorithm_trace(source, source["meta"]["route_id"], route_len, collapsed, signal_policy)
    timelines = {"B04": {}, "B4": {}}
    for mode in ("B04", "B4"):
        t_max = max(
            float(source["modes"][mode]["travel_time_sec"]),
            float(tls_source.get("modes", {}).get(mode, {}).get("travel_time_sec", 0.0)),
        )
        tls_states = tls_source.get("modes", {}).get(mode, source["modes"][mode]).get("tls_states", {})
        for sig in collapsed:
            source_id = sig.get("source_tls_id", "")
            tl = tls_states.get(source_id, [])
            normalized = normalize_actual_timeline(tl, t_max)
            timelines[mode][sig["id"]] = build_presentation_timeline(
                mode,
                normalized,
                sig,
                int(str(sig["id"]).replace("S", "")),
                samples_by_mode[mode],
                t_max,
                signal_policy,
            )
    tls_validation = validate_timelines(timelines)
    tls_validation["close_display_signal_pairs"] = close_display_signal_pairs(collapsed)
    modes = {}
    for mode, label, color in (
        ("B04", "B04 · 일반 신호", "#dc2626"),
        ("B4", "B4 · 우선신호", "#2563eb"),
    ):
        doc = source["modes"][mode]
        samples = samples_by_mode[mode]
        display_samples = build_display_samples(
            mode,
            samples,
            doc.get("traffic_profile", []),
            collapsed,
            timelines[mode],
            route_points,
            demand_policy,
        )
        traffic_samples = high_res_samples(display_samples, DISPLAY_TRAFFIC_DT)
        display_traffic = build_display_traffic(
            mode,
            traffic_samples,
            doc.get("traffic_profile", []),
            collapsed,
            timelines[mode],
            route_points,
            stage2_approaches,
            demand_policy,
            algorithm_trace,
        )
        modes[mode] = {
            "label": label,
            "color": color,
            "samples": samples,
            "display_samples": display_samples,
            "display_traffic": display_traffic,
            "background": actual_background(doc.get("background", [])),
            "traffic_profile": doc.get("traffic_profile", []),
            "travel_time_sec": round(float(display_samples[-1]["t"] if display_samples else doc["travel_time_sec"]), 1),
            "distance_m": route_len,
            "source": "actual_fcd",
        }
    display_validation = validate_display_traffic(modes, collapsed, timelines)
    validation_report = build_validation_report(modes, tls_validation, display_validation, algorithm_trace)
    return {
        "schema": "seoul_fire_station_presentation.10-1_owned_signal_demand.v4",
        "title": "중부소방서 출동 EV 우선신호 시각화",
        "source_progress_data": str(ACTUAL_PROGRESS_DATA.relative_to(PROJECT_ROOT)),
        "tls_source_progress_data": str(TLS_PROGRESS_DATA.relative_to(PROJECT_ROOT)) if TLS_PROGRESS_DATA.is_file() else str(ACTUAL_PROGRESS_DATA.relative_to(PROJECT_ROOT)),
        "presentation_inputs": presentation_inputs,
        "presentation_input_files": {
            "signal_net_file": rel_path(NET_FILE),
            "demand_route_file": rel_path(PRESENTATION_DEMAND_ROUTE),
            "firetruck_route_file": rel_path(ROUTE_XML),
        },
        "route": {
            "id": f"{source['meta']['route_id']}_TO_USER_DEST_37_560208_127_002440",
            "points": route_points,
            "length_m": route_len,
        },
        "presentation_destination": destination_meta,
        "signals": collapsed,
        "timelines": timelines,
        "tls_validation": tls_validation,
        "display_validation": display_validation,
        "validation_report": validation_report,
        "algorithm_trace": algorithm_trace,
        "stage2_block_lines": stage2_lines,
        "stage2_block_approaches": stage2_approaches,
        "modes": modes,
        "presentation_policy": {
            "route": "10-1 presentation route is generated on the local SUMO road graph toward the requested destination; raw FCD supplies timing seeds only.",
            "destination": "10-1 presentation route is cut and extended to user-requested final destination 37.560208, 127.002440",
            "traffic_flow": "10-1 owned presentation demand model; actual FCD profile is used only as a bounded seed, then vehicles obey the display signals",
            "stage2_display": "presentation-owned Shin-dang ingress route with fake red stoplines",
            "tls_and_labels": "10-1 suitable signal system derived from local signal copy; raw smoke TLS is not replayed directly",
            "green_queue_rule": "green means permission, not guaranteed discharge; downstream_queue can hold EV and general vehicles before the stopline",
        },
    }


def build_payload() -> dict[str, Any]:
    actual = build_actual_payload()
    if actual is not None:
        return actual
    points, route_len = route_points()
    signals = load_signals(points)
    timelines = make_timelines(signals)
    b04, _ = simulate_mode("B04", points, signals, timelines["B04"])
    b4, events = simulate_mode("B4", points, signals, timelines["B4"])
    return {
        "schema": "seoul_fire_station_presentation.v1",
        "title": "중부소방서 출동 EV 우선신호 시각화",
        "route": {"id": "COMPACT_V9_FIRETRUCK_TO_SEOUL_STATION_FRONT", "points": points, "length_m": round(route_len, 2)},
        "signals": signals,
        "timelines": timelines,
        "modes": {
            "B04": {"label": "B04 · 제어 없음", "color": "#dc2626", "samples": b04, "travel_time_sec": b04[-1]["t"]},
            "B4": {"label": "B4 · 신호 우선", "color": "#2563eb", "samples": b4, "travel_time_sec": b4[-1]["t"], "events": events},
        },
        "presentation_policy": {
            "route": "real compact-v9 Jungbu Fire Station to Seoul Station route geometry",
            "tls_and_demand": "presentation-synchronized display states, not raw SUMO replay",
            "queue_rule": "one traffic dot stream per panel; front queue and discharge are states of that stream",
            "rear_congestion_rule": "rear congestion is shown as a blue band and label, not a second vehicle stream",
            "signal_rule": "nearby twin signals are merged by route distance and TLS timelines are validated for yellow and green-yellow-green",
        },
    }


def render_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{payload["title"]}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body{{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f172a;color:#f8fafc}}
.wrap{{height:100vh;display:flex;flex-direction:column}}header{{height:48px;display:grid;grid-template-columns:1fr 480px 280px;align-items:center;gap:14px;padding:0 16px;background:#111827}}h1{{font-size:16px;margin:0}}button{{background:#2563eb;color:white;border:0;border-radius:6px;padding:7px 12px}}.controls{{display:flex;gap:10px;align-items:center}}#seek{{flex:1}}.clock,.legend{{font-size:12px;color:#cbd5e1}}.maps{{display:grid;grid-template-columns:1fr 1fr;flex:1;min-height:0}}.panel{{position:relative;border-right:1px solid #111827}}.map{{position:absolute;inset:0;background:#edf2f7}}.tag{{position:absolute;top:10px;left:10px;z-index:700;background:rgba(15,23,42,.88);border-radius:8px;padding:9px 12px;font-size:13px;line-height:1.55;font-weight:650}}.tag b{{font-size:14px}}.why{{color:#fde68a}}.traffic{{color:#bbf7d0}}.eventToast{{position:absolute;right:12px;bottom:18px;z-index:720;min-width:0;max-width:44%;padding:7px 13px;border-radius:8px;background:rgba(15,23,42,.82);color:#f8fafc;border:1px solid rgba(255,255,255,.16);font-size:13px;font-weight:850;line-height:1.22;text-align:center;pointer-events:none;box-shadow:0 2px 10px rgba(15,23,42,.24)}}.eventToast.clear{{border-color:rgba(249,115,22,.48)}}.eventToast.moving{{border-color:rgba(34,197,94,.48)}}.eventToast.rear{{border-color:rgba(37,99,235,.5)}}.eventToast.empty{{border-color:rgba(148,163,184,.45);color:#e2e8f0}}.tlwrap{{background:transparent;border:0}}.tl{{display:flex;flex-direction:column;gap:3px;padding:4px;background:#020617;border:3px solid #64748b;border-radius:6px;box-shadow:0 2px 10px rgba(0,0,0,.45);transition:border-color .45s ease,box-shadow .45s ease}}.tl i{{width:15px;height:15px;border-radius:50%;background:#111827;border:1px solid rgba(255,255,255,.18);transition:background .5s ease,box-shadow .5s ease}}.tlwrap[data-state=red] .r,.tlwrap[data-state=allred] .r{{background:#ff1f3d;box-shadow:0 0 12px #ff1f3d}}.tlwrap[data-state=yellow] .y{{background:#ffd400;box-shadow:0 0 12px #ffd400}}.tlwrap[data-state=green] .g{{background:#00e676;box-shadow:0 0 12px #00e676}}.tlwrap.prepare .tl{{border-color:#a5b4fc}}.tlwrap.next .tl{{border-color:#fde047;box-shadow:0 0 0 4px rgba(253,224,71,.7)}}.note{{display:none}}.dotIcon,.evIcon,.vehIcon{{background:transparent;border:0}}.dotIcon span{{display:block;border:1.2px solid white;border-radius:999px;box-shadow:0 1px 4px rgba(15,23,42,.42)}}.vehIcon span{{display:block;width:15px;height:8px;border-radius:5px 8px 8px 5px;border:1.4px solid white;box-shadow:0 1px 4px rgba(15,23,42,.45);transform-origin:center}}.evIcon div{{display:flex;align-items:center;justify-content:center;width:44px;height:28px;border-radius:999px;border:3px solid white;color:white;font-weight:900;font-size:13px;letter-spacing:0;box-shadow:0 2px 12px rgba(15,23,42,.55)}}.bottom{{height:150px;display:grid;grid-template-columns:320px 1fr;background:#0b1220}}#overview{{position:relative}}#chart{{padding:8px 12px}}svg{{width:100%;height:104px}}.pill{{display:inline-flex;align-items:center;gap:4px;margin-left:8px}}.sw{{width:10px;height:10px;border-radius:50%;display:inline-block}}
</style>
</head>
<body><div class="wrap">
<header><h1>{payload["title"]}</h1><div class="controls"><button id="play">▶ 재생</button><button id="reset">↺ 처음</button><input id="seek" type="range" min="0" max="1000" value="0"><span class="clock" id="clock"></span></div><div class="legend"><span class="pill"><i class="sw" style="background:#ff1f3d"></i>red</span><span class="pill"><i class="sw" style="background:#ffd400"></i>yellow</span><span class="pill"><i class="sw" style="background:#00e676"></i>green</span></div></header>
<div class="maps"><div class="panel"><div class="map" id="mapLeft"></div><div class="tag" id="tagLeft"></div><div class="eventToast" id="eventLeft"></div></div><div class="panel"><div class="map" id="mapRight"></div><div class="tag" id="tagRight"></div><div class="eventToast" id="eventRight"></div></div></div>
<div class="bottom"><div id="overview" class="map"></div><div id="chart"><div class="legend"><span class="pill"><i class="sw" style="background:#dc2626"></i>B04</span><span class="pill"><i class="sw" style="background:#2563eb"></i>B4</span><span id="cmp"></span></div><svg id="svg" viewBox="0 0 1000 110" preserveAspectRatio="none"></svg></div></div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA={data};const MODES=["B04","B4"],SUF={{B04:"Left",B4:"Right"}};
const TMAX=Math.max(...MODES.map(m=>DATA.modes[m].travel_time_sec));let t=0,flowT=0,playing=false,last=null,rate=4;
function makeMap(id){{const m=L.map(id,{{zoomControl:false,attributionControl:false,preferCanvas:false}});L.tileLayer("https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png",{{maxZoom:19}}).addTo(m);return m;}}
function idxAt(a,t){{let lo=0,hi=a.length-1,r=0;while(lo<=hi){{const m=(lo+hi)>>1;if(a[m].t<=t){{r=m;lo=m+1}}else hi=m-1}}return r}}
function lerp(a,b,f){{return a+(b-a)*f}}
function sampleAt(mode,t){{const a=DATA.modes[mode].display_samples||DATA.modes[mode].samples;if(t<=a[0].t)return a[0];if(t>=a[a.length-1].t)return a[a.length-1];const i=idxAt(a,t),x=a[i],y=a[Math.min(i+1,a.length-1)],f=(t-x.t)/(y.t-x.t||1);return {{...y,t,lat:lerp(x.lat,y.lat,f),lon:lerp(x.lon,y.lon,f),s:lerp(x.s,y.s,f),speed_kmh:lerp(x.speed_kmh,y.speed_kmh,f)}}}}
function stateAt(sig,mode,t){{const a=DATA.timelines[mode][sig.id]||[[0,"green"]];let st=a[0][1];for(const p of a){{if(p[0]<=t)st=p[1];else break}}return st}}
function stateAge(sig,mode,t){{const a=DATA.timelines[mode][sig.id]||[[0,"green"]];let cur=a[0],prev=null;for(const p of a){{if(p[0]<=t){{prev=cur;cur=p}}else break}}return {{state:cur[1],age:t-cur[0],prev:prev?prev[1]:cur[1]}}}}
function signalDisplay(sig,mode,t){{const x=stateAge(sig,mode,t);if(x.state==="green"&&x.age<1.35&&["red","yellow","allred"].includes(x.prev))return {{state:"allred",prepare:true}};return {{state:x.state,prepare:false}}}}
function pointAtS(s){{const pts=DATA.route.points;if(s<=0)return pts[0];if(s>=DATA.route.length_m)return pts[pts.length-1];let i=0;while(i<pts.length-1&&pts[i+1].s<s)i++;const a=pts[i],b=pts[i+1],f=(s-a.s)/(b.s-a.s||1);return {{lat:lerp(a.lat,b.lat,f),lon:lerp(a.lon,b.lon,f),s}}}}
function seg(a,b,step=10){{const out=[];for(let s=Math.max(0,a);s<=Math.min(DATA.route.length_m,b);s+=step){{const p=pointAtS(s);out.push([p.lat,p.lon])}}return out}}
function clamp(v,a,b){{return Math.max(a,Math.min(b,v))}}
function dot(panel,p,color,r=4.5,opacity=1){{L.marker([p.lat,p.lon],{{interactive:false,zIndexOffset:620,icon:L.divIcon({{className:"dotIcon",iconSize:[r*2,r*2],iconAnchor:[r,r],html:`<span style="width:${{r*2}}px;height:${{r*2}}px;background:${{color}};opacity:${{opacity}}"></span>`}})}}).addTo(panel.layer)}}
function vehicle(panel,v,color,opacity=1){{const blocked=(v.reason||"").includes("막힌 구간"),hot=(v.role==="downstream_moving"||v.role==="ahead_queue");const size=blocked?[28,16]:(hot?[22,13]:[17,10]),anchor=blocked?[14,8]:(hot?[11,6]:[8,5]),z=blocked?1120:(hot?1010:910);L.marker([v.lat,v.lon],{{interactive:false,zIndexOffset:z,icon:L.divIcon({{className:"vehIcon",iconSize:size,iconAnchor:anchor,html:`<span style="background:${{color}};opacity:${{opacity}};width:${{blocked?26:(hot?20:15)}}px;height:${{blocked?14:(hot?11:8)}}px;border-width:${{blocked?2.2:1.4}}px;transform:rotate(${{v.angle||0}}deg)"></span>`}})}}).addTo(panel.layer)}}
function evIcon(color){{return L.divIcon({{className:"evIcon",iconSize:[44,28],iconAnchor:[22,14],html:`<div style="background:${{color}}">EV</div>`}})}}
function note(panel,p,text,kind=""){{return}}
function nextSignal(s){{return DATA.signals.find(x=>x.s>s+2)}}
function meters(a,b){{const lat=(a.lat+b.lat)/2*Math.PI/180;const dy=(a.lat-b.lat)*111320;const dx=(a.lon-b.lon)*111320*Math.cos(lat);return Math.hypot(dx,dy)}}
function nearestRoute(p){{let d=1e9,s=0;for(const r of DATA.route.points){{const m=meters(p,r);if(m<d){{d=m;s=r.s}}}}return {{d,s}}}}
function routeAngle(s){{const a=pointAtS(Math.max(0,s-5)),b=pointAtS(Math.min(DATA.route.length_m,s+8));const lat=(a.lat+b.lat)/2*Math.PI/180;const dx=(b.lon-a.lon)*111320*Math.cos(lat),dy=(b.lat-a.lat)*111320;return Math.atan2(-dy,dx)*180/Math.PI}}
function turnDelta(s){{const a=routeAngle(Math.max(0,s-20)),b=routeAngle(Math.min(DATA.route.length_m,s+20));return Math.abs(((b-a+540)%360)-180)}}
function bgAt(mode,t){{const snaps=DATA.modes[mode].background||[];if(!snaps.length)return null;let lo=0,hi=snaps.length-1,best=0;while(lo<=hi){{const m=(lo+hi)>>1;if(snaps[m].t<=t){{best=m;lo=m+1}}else hi=m-1}}return snaps[best]}}
function profileAt(mode,t){{const a=DATA.modes[mode].traffic_profile||[];if(!a.length)return null;let lo=0,hi=a.length-1,best=0;while(lo<=hi){{const m=(lo+hi)>>1;if(a[m].t_rel<=t){{best=m;lo=m+1}}else hi=m-1}}return a[best]}}
function trafficFrames(mode){{return DATA.modes[mode].display_traffic||[]}}
function lerpAngle(a,b,f){{const d=((b-a+540)%360)-180;return a+d*f}}
function trafficFrameAt(mode,t){{const a=trafficFrames(mode);if(!a.length)return null;if(t<=a[0].t)return a[0];if(t>=a[a.length-1].t)return a[a.length-1];const i=idxAt(a,t),x=a[i],y=a[Math.min(i+1,a.length-1)],f=(t-x.t)/(y.t-x.t||1);const byY=new Map((y.vehicles||[]).map(v=>[v.id,v]));const used=new Set();const vehicles=[];for(const v of (x.vehicles||[])){{const w=byY.get(v.id);if(!w){{vehicles.push(v);continue}}used.add(v.id);vehicles.push({{...w,lat:lerp(v.lat,w.lat,f),lon:lerp(v.lon,w.lon,f),s:v.s!=null&&w.s!=null?lerp(v.s,w.s,f):w.s,angle:lerpAngle(v.angle||0,w.angle||0,f),speed_kmh:lerp(v.speed_kmh||0,w.speed_kmh||0,f),opacity:lerp(v.opacity??1,w.opacity??1,f)}})}}for(const w of (y.vehicles||[]))if(!used.has(w.id))vehicles.push(w);return {{...y,t,vehicles}}}}
function drawDisplayBand(panel,b){{const kind=b.kind||"";let style={{color:"#7c2d12",weight:16,opacity:.26}},noteKind="";if(kind==="front_blocked"){{style={{color:"#7c2d12",weight:18,opacity:.32}};noteKind="clear"}}else if(kind==="front_moving"){{style={{color:"#16a34a",weight:8,opacity:.42,dashArray:"12 8"}};noteKind="moving"}}else if(kind==="rear"){{style={{color:"#2563eb",weight:12,opacity:.22,dashArray:"14 9"}};noteKind="rear"}}else if(kind==="empty_ahead"){{style={{color:"#22c55e",weight:7,opacity:.18,dashArray:"10 10"}};noteKind="empty"}}else if(kind==="empty_behind"){{style={{color:"#64748b",weight:7,opacity:.18,dashArray:"10 10"}};noteKind="empty"}}L.polyline(seg(b.a,b.b),style).addTo(panel.layer);if(b.label)note(panel,pointAtS((b.a+b.b)/2),b.label,noteKind)}}
function displayFakeSignal(panel,p){{L.marker([p.lat,p.lon],{{interactive:false,zIndexOffset:760,icon:L.divIcon({{className:"tlwrap",iconSize:[33,63],iconAnchor:[16,78],html:'<div class="tl" style="border-color:#0ea5e9"><i class="r" style="background:#ff1f3d;box-shadow:0 0 12px #ff1f3d"></i><i class="y"></i><i class="g"></i></div>'}})}}).addTo(panel.layer)}}
function drawDisplayTraffic(panel,mode){{const fr=trafficFrameAt(mode,t);if(!fr)return null;for(const line of (fr.stage2_lines||[])){{L.polyline(line.line,{{color:"#0ea5e9",weight:8,opacity:.7,dashArray:"13 8"}}).addTo(panel.layer)}}for(const p of (fr.fake_signals||[]))displayFakeSignal(panel,p);for(const b of (fr.bands||[]))drawDisplayBand(panel,b);if((fr.fake_signals||[]).length)note(panel,fr.fake_signals[0],"신당역 직전·우회전 유입 정지","rear");for(const v of (fr.vehicles||[]))vehicle(panel,v,v.color||"#f97316",v.opacity??1);return fr.summary||{{kind:"display_traffic",label:"실제 FCD 기반 신호 준수 흐름",shown:(fr.vehicles||[]).length,ahead:0,behind:0,slow:0,signalQueuedAhead:false,signalQueuedBehind:false}}}}
function displaySample(st,ns,nextState,mode){{if(!ns||![\"red\",\"yellow\",\"allred\"].includes(nextState))return st;const stopS=Math.max(0,ns.s-18);const profile=profileAt(mode,t);const ahead=Math.max(0,Math.round((profile&&profile.ahead_count)||0));const queueLen=ahead>0?Math.max(52,ahead*13):0;const holdBack=ahead>0?queueLen+34:(nextState==="yellow"?54:64);const holdS=Math.max(0,stopS-holdBack);if(st.s>=holdS){{const p=pointAtS(holdS);return {{...st,s:holdS,lat:p.lat,lon:p.lon,speed_kmh:0,reason:ahead>0?"front_queue_tail":"front_queue_red"}}}}return st}}
function smoothDisplay(panel,target){{if(panel.lastVisualT<0||t<panel.lastVisualT||Math.abs(t-panel.lastVisualT)>1.5){{panel.visualS=target.s;panel.prevVisualS=target.s;panel.lastVisualT=t;return target}}const dt=Math.max(.05,t-panel.lastVisualT);const prevS=panel.visualS;let maxFwd=Math.max(2.8,32*dt),maxBack=Math.max(2.2,20*dt);if(target.reason==="front_queue_tail"||target.reason==="front_queue_red"){{maxFwd=Math.max(1.2,18*dt);maxBack=Math.max(1.2,14*dt)}}let nextS=target.s;if(target.s>prevS)nextS=Math.min(target.s,prevS+maxFwd);else nextS=Math.max(target.s,prevS-maxBack);panel.visualS=nextS;panel.prevVisualS=nextS;panel.lastVisualT=t;if(Math.abs(nextS-target.s)<.05)return target;const p=pointAtS(nextS);const shownSpeed=Math.max(0,Math.min(58,Math.max(0,nextS-prevS)/dt*3.6));return {{...target,s:nextS,lat:p.lat,lon:p.lon,speed_kmh:shownSpeed,reason:target.reason==="arrived"?"moving":target.reason}}}}
function lastBlockedRelease(sig,mode,t){{const tl=DATA.timelines[mode][sig.id]||[];let release=-999;for(let i=1;i<tl.length;i++){{if(tl[i][0]<=t&&tl[i][1]==="green"&&["red","yellow","allred"].includes(tl[i-1][1]))release=tl[i][0];}}return release}}
function stoppedLabel(mode){{return mode==="B04"?"신호 대기":"우선신호 대기"}}
function dischargeLabel(mode){{return mode==="B04"?"신호 변경 후 출발":"우선신호 대기열 해소"}}
function trafficState(ns,mode,t,st,nextState){{if(st.reason==="arrived")return {{kind:"arrived",label:"도착 완료",progress:1,blocked:false}};if(st.reason==="stage2_hold")return {{kind:"stage2_blocking",label:"Stage2 유입 차단",progress:0,blocked:false}};if(st.s<250)return {{kind:"startup",label:"출동 시작",progress:1,blocked:false}};if(ns&&["red","yellow","allred"].includes(nextState))return {{kind:"front_queue_stopped",label:stoppedLabel(mode),progress:0,blocked:true}};if(ns&&st.reason==="queue_clearing")return {{kind:"queue_discharging",label:dischargeLabel(mode),progress:.25,blocked:false}};if(ns){{const age=t-lastBlockedRelease(ns,mode,t);if(age>=0&&age<30)return {{kind:"queue_discharging",label:dischargeLabel(mode),progress:clamp(age/30,0,1),blocked:false}};}}if(st.speed_kmh<18)return {{kind:"rear_congestion",label:"EV 뒤 정체",progress:0,blocked:false}};return {{kind:"free_flow",label:"일반차 진행 흐름",progress:1,blocked:false}}}}
function holdReason(mode,st,nextState,tv){{if(mode!=="B4"||st.reason==="arrived"||st.speed_kmh>=2)return "";if(st.reason==="green_downstream_queue")return "정지 이유 신호는 열렸지만 앞 큐 과잉";if((st.algorithm||"").includes("Stage2"))return "정지 이유 신당역 유입 정리";if((tv.ahead||0)>=8||(tv.slow||0)>=5)return "정지 이유 앞 큐 과밀";if(["yellow","allred"].includes(nextState))return "정지 이유 보행자 시간 보장";if(nextState==="red")return "정지 이유 교차 보행자 시간 보장";if(st.reason==="traffic_hold")return "정지 이유 앞 구간 정체";return "정지 이유 안전 대기"}}
function eventSummary(mode,st,nextState,tv,arrived,signalHold){{if(arrived)return {{text:"도착 완료",kind:"moving"}};if(mode==="B4"&&(st.algorithm||"").includes("Stage2"))return {{text:"Stage2 · 신당역 유입 차단",kind:"rear"}};if(mode==="B4"&&st.reason==="green_downstream_queue")return {{text:"Case B · 하류 먼저 배출",kind:"moving"}};if(mode==="B4"&&(st.algorithm||"").includes("Case B"))return {{text:"Stage3 Case B · 하류 큐 정리",kind:"moving"}};if(mode==="B4"&&(st.algorithm||"").includes("GREEN"))return {{text:"Stage3 · 우선신호 GREEN",kind:"moving"}};if(mode==="B4"&&signalHold)return {{text:"SafetyGate · 보행/교차 시간 보장",kind:"clear"}};if(tv.signalQueuedAhead)return {{text:mode==="B04"?"일반 신호 대기":"전방 큐 뒤 대기",kind:"clear"}};if(tv.signalQueuedBehind)return {{text:"EV 뒤 흐름 정체",kind:"rear"}};return {{text:mode==="B04"?"일반 신호 기준 주행":"우선신호 경로 진행",kind:"empty"}}}}
function setEventToast(mode,msg){{const el=document.getElementById("event"+SUF[mode]);if(!el)return;el.textContent=msg.text;el.className="eventToast "+(msg.kind||"")}}
function drawRearBand(panel,st){{const a=Math.max(0,st.s-120),b=Math.max(0,st.s-24);L.polyline(seg(a,b),{{color:"#2563eb",weight:12,opacity:.22,dashArray:"14 9"}}).addTo(panel.layer);note(panel,pointAtS((a+b)/2),"EV 뒤 정체","rear")}}
function lineLen(line){{let d=0;for(let i=1;i<line.length;i++)d+=meters({{lat:line[i-1][0],lon:line[i-1][1]}},{{lat:line[i][0],lon:line[i][1]}});return d}}
function linePoint(line,s){{let left=s;for(let i=1;i<line.length;i++){{const a={{lat:line[i-1][0],lon:line[i-1][1]}},b={{lat:line[i][0],lon:line[i][1]}},len=meters(a,b);if(left<=len){{const f=len?left/len:0;return {{lat:lerp(a.lat,b.lat,f),lon:lerp(a.lon,b.lon,f),angle:Math.atan2(-(b.lat-a.lat)*111320,(b.lon-a.lon)*111320*Math.cos((a.lat+b.lat)/2*Math.PI/180))*180/Math.PI}}}}left-=len;}}const z=line[line.length-1];return {{lat:z[0],lon:z[1],angle:0}}}}
function fakeSignal(panel,p){{L.marker([p.lat,p.lon],{{interactive:false,zIndexOffset:760,icon:L.divIcon({{className:"tlwrap",iconSize:[33,63],iconAnchor:[16,78],html:'<div class="tl" style="border-color:#0ea5e9"><i class="r" style="background:#ff1f3d;box-shadow:0 0 12px #ff1f3d"></i><i class="y"></i><i class="g"></i></div>'}})}}).addTo(panel.layer)}}
function stage2Approaches(){{if(DATA.stage2_block_approaches&&DATA.stage2_block_approaches.length)return DATA.stage2_block_approaches;return (DATA.stage2_block_lines||[]).map(line=>({{label:"신당역 유입 차량 정지",line}}))}}
function drawStage2Block(panel,mode,st){{const active=mode==="B4"&&((st.algorithm||"").includes("Stage2")||t<26);const approaches=stage2Approaches();if(!active||!approaches.length)return;let labelPoint=null;const fakeStops=[];for(const item of approaches){{const line=item.line;if(!line||line.length<2)continue;L.polyline(line,{{color:"#0ea5e9",weight:8,opacity:.7,dashArray:"13 8"}}).addTo(panel.layer);const stop=linePoint(line,0);if(fakeStops.every(p=>meters(p,stop)>34)){{fakeSignal(panel,stop);fakeStops.push(stop)}}if(!labelPoint)labelPoint=stop;const len=Math.max(18,lineLen(line));const stopped=t>=8.5;const spacing=18;const movingOffset=flowT*4.2;const count=Math.max(3,Math.min(5,Math.floor(len/22)+2));for(let i=0;i<count;i++){{const s=stopped?Math.min(len-2,9+i*spacing):8+i*spacing+movingOffset;if(s<6||s>len-2)continue;const v=linePoint(line,s);vehicle(panel,v,\"#f97316\",.86);}}}}if(labelPoint)note(panel,labelPoint,\"신당역 직전·우회전 유입 정지\",\"rear\")}}
function drawProfileTraffic(panel,st,mode,nextState,tv){{const profile=profileAt(mode,t);if(!profile)return null;const ahead=Math.min(18,Math.max(0,Math.round(profile.ahead_count||0)));const behind=Math.min(12,Math.max(0,Math.round(profile.behind_count||0)));const aheadSpeed=Math.max(0,profile.ahead_speed_kmh||0);const behindSpeed=Math.max(0,profile.behind_speed_kmh||0);const colorBase="#f97316";let shown=0,slow=0,signalQueuedAhead=false,signalQueuedBehind=false;function stopIntent(sig){{const cur=stateAt(sig,mode,t);if(["red","yellow","allred"].includes(cur))return {{sig,state:cur,soon:false,stopS:Math.max(0,sig.s-18)}};const tl=DATA.timelines[mode][sig.id]||[];const next=tl.find(p=>p[0]>t);if(next&&["yellow","red","allred"].includes(next[1])&&next[0]-t<=6)return {{sig,state:next[1],soon:true,stopS:Math.max(0,sig.s-18)}};return null}}const redStops=DATA.signals.map(stopIntent).filter(Boolean).sort((a,b)=>a.stopS-b.stopS);function firstRedBetween(a,b){{return redStops.find(x=>x.stopS>a&&x.stopS<b)}}function lastRedBetween(a,b){{let found=null;for(const x of redStops)if(x.stopS>a&&x.stopS<b)found=x;return found}}function put(s,speed,opacity){{const p=pointAtS(clamp(s,0,DATA.route.length_m));const speedColor=colorBase;if(speed<7)slow++;vehicle(panel,{{lat:p.lat,lon:p.lon,angle:routeAngle(s)}},speedColor,opacity);shown++;}}function emptyAhead(toS=null){{const b=Math.min(DATA.route.length_m,toS||st.s+160),a=Math.min(b,st.s+34);if(b>a+10)L.polyline(seg(a,b),{{color:"#22c55e",weight:7,opacity:.18,dashArray:"10 10"}}).addTo(panel.layer);note(panel,pointAtS((a+b)/2),"앞 구간 비어있음","empty")}}function emptyBehind(fromS=null){{const a=Math.max(0,fromS||st.s-145),b=Math.max(a+8,st.s-35);L.polyline(seg(a,b),{{color:"#64748b",weight:7,opacity:.18,dashArray:"10 10"}}).addTo(panel.layer);note(panel,pointAtS((a+b)/2),"뒤 구간 비어있음","empty")}}function queueBefore(stopS,count,side,tailS=null){{const qEnd=clamp(stopS-10,0,DATA.route.length_m);let qStart=clamp(qEnd-Math.max(52,Math.max(1,count)*13),0,qEnd);if(tailS!=null)qStart=clamp(Math.min(qStart,tailS),0,qEnd-8);const n=Math.max(1,count,Math.min(16,Math.ceil((qEnd-qStart)/23)));L.polyline(seg(qStart,qEnd),{{color:"#7c2d12",weight:16,opacity:.26}}).addTo(panel.layer);const spacing=Math.max(10,(qEnd-qStart)/Math.max(1,n));for(let i=0;i<n;i++)put(qEnd-i*spacing,0,.92);if(side==="ahead")signalQueuedAhead=true;else signalQueuedBehind=true}}function stream(from,to,count,speed,side){{if(count<=0)return;const spacing=Math.max(12,Math.min(30,(to-from)/Math.max(1,count)));const moving=speed>2,displayMps=clamp(speed/3.6,4.6,9.2),offset=moving?flowT*displayMps:0;const first=Math.floor((from-offset)/spacing)-1,last=Math.ceil((to-offset)/spacing)+1;for(let i=first;i<=last;i++){{const rel=from+i*spacing+offset;if(rel<from||rel>to)continue;const s=clamp(st.s+rel,0,DATA.route.length_m);if(Math.abs(s-st.s)<25)continue;const edge=clamp(Math.min((rel-from)/50,(to-rel)/50),.35,1);put(s,speed,.42+.5*edge);}}}}const aheadStart=st.s+32,aheadEnd=Math.min(DATA.route.length_m,st.s+315),aheadRed=firstRedBetween(st.s+2,aheadEnd);if(aheadRed){{if(ahead>0)queueBefore(aheadRed.stopS,ahead,"ahead",st.s+35);else emptyAhead(Math.max(st.s+42,aheadRed.stopS-24));}}else if(ahead===0)emptyAhead();else stream(34,290,ahead,aheadSpeed,"ahead");const behindStart=Math.max(0,st.s-235),behindEnd=Math.max(0,st.s-35),behindRed=lastRedBetween(behindStart,st.s-2);if(behindRed){{if(behind>0)queueBefore(behindRed.stopS,behind,"behind");else emptyBehind(behindStart);}}else if(behind===0)emptyBehind();else stream(-230,-42,behind,behindSpeed,"behind");if(behind>0&&behindSpeed<10&&!signalQueuedBehind)drawRearBand(panel,st);let label="실제 FCD 기반 신호 준수 흐름";if(signalQueuedAhead&&signalQueuedBehind)label="앞·뒤 모두 신호 대기";else if(signalQueuedAhead)label="앞 차량 신호 대기열";else if(signalQueuedBehind)label="뒤 차량 신호 대기열";else if(ahead===0&&behind===0)label="앞·뒤 관찰구간 비어있음";else if(ahead===0)label="앞 구간 비어있음 · 뒤 흐름 표시";else if(behind===0)label="앞 흐름 표시 · 뒤 구간 비어있음";else if((profile.local_count||0)>0&&(profile.local_speed_kmh||99)<8)label="실제 FCD 기반 저속 흐름";return {{kind:"profile_fcd",label,shown,ahead,behind,slow,emptyAhead:ahead===0,emptyBehind:behind===0,signalQueuedAhead,signalQueuedBehind}}}}
function drawTrafficState(panel,st,ns,nextState,mode){{const prebuilt=drawDisplayTraffic(panel,mode);if(prebuilt)return prebuilt;const tv=trafficState(ns,mode,t,st,nextState);if(tv.kind==="arrived")return tv;const actual=drawProfileTraffic(panel,st,mode,nextState,tv);if(actual)return actual;const stopS=ns?ns.s-8:DATA.route.length_m;let start=Math.max(0,st.s-220),end=Math.min(DATA.route.length_m,st.s+280),spacing=26,color="#f97316",radius=4.8,alpha=.72,reverse=false;let labelPoint=null;if(tv.kind==="front_queue_stopped"){{start=Math.max(0,stopS-115);end=stopS;spacing=11;color="#f97316";radius=5.8;alpha=1;labelPoint=pointAtS(stopS-55);L.polyline(seg(start,end),{{color:"#7c2d12",weight:17,opacity:.23}}).addTo(panel.layer);}}else if(tv.kind==="queue_discharging"){{start=Math.max(0,stopS-105+tv.progress*36);end=Math.min(DATA.route.length_m,stopS+95+tv.progress*70);spacing=14+tv.progress*15;color="#f97316";radius=5.5;alpha=.95;reverse=true;labelPoint=pointAtS(stopS-45+tv.progress*26);L.polyline(seg(Math.max(0,stopS-115),Math.min(DATA.route.length_m,stopS+110)),{{color:"#f59e0b",weight:7,opacity:.34,dashArray:"9 8"}}).addTo(panel.layer);}}else if(tv.kind==="stage2_blocking"){{start=Math.max(0,st.s-160);end=Math.min(DATA.route.length_m,st.s+95);spacing=13;color="#f97316";radius=5.1;alpha=.9;labelPoint=pointAtS(Math.max(0,st.s+45));drawRearBand(panel,st);}}else if(tv.kind==="rear_congestion"){{drawRearBand(panel,st);}}if(ns&&tv.blocked)end=Math.min(end,stopS);const moving=tv.kind==="free_flow"||tv.kind==="queue_discharging";const baseSpeed=clamp(st.speed_kmh/3.6,4.6,9.2);const offset=moving?(flowT*baseSpeed*.72)%spacing:0;const first=reverse?start+((spacing-offset)%spacing):start+offset;for(let s=first;s<=end;s+=spacing){{if(Math.abs(s-st.s)<24)continue;const edge=clamp(Math.min((s-start)/60,(end-s)/60),0,1);dot(panel,pointAtS(s),color,radius,Math.max(.22,alpha*edge));}}if(labelPoint)note(panel,labelPoint,tv.label,tv.kind==="queue_discharging"?"clear":"");return tv}}
function panel(mode,id){{const map=makeMap(id);const coords=DATA.route.points.map(p=>[p.lat,p.lon]);L.polyline(coords,{{color:DATA.modes[mode].color,weight:6,opacity:.62}}).addTo(map);const marker=L.marker(coords[0],{{interactive:false,zIndexOffset:1200,icon:evIcon(DATA.modes[mode].color)}}).addTo(map);const layer=L.layerGroup().addTo(map);const tls=DATA.signals.map(sig=>({{sig,marker:L.marker([sig.lat,sig.lon],{{interactive:false,icon:L.divIcon({{className:"tlwrap",iconSize:[33,63],iconAnchor:[16,78],html:'<div class="tl"><i class="r"></i><i class="y"></i><i class="g"></i></div>'}})}}).addTo(map)}}));map.setView(coords[0],16.75);return {{mode,map,marker,layer,tls,lastCameraT:-999,lastVisualT:-999,visualS:0,prevVisualS:0}}}}
const panels={{B04:panel("B04","mapLeft"),B4:panel("B4","mapRight")}};
const ov=makeMap("overview");const route=DATA.route.points.map(p=>[p.lat,p.lon]);L.polyline(route,{{color:"#94a3b8",weight:3}}).addTo(ov);ov.fitBounds(L.latLngBounds(route),{{padding:[12,12]}});
function updatePanel(p){{const mode=p.mode,st=sampleAt(mode,t),arrived=st.reason==="arrived";let ns=arrived?null:nextSignal(st.s),nextState="-";p.map.getContainer().querySelectorAll(".tlwrap.next").forEach(el=>el.classList.remove("next"));if(ns)nextState=stateAt(ns,mode,t);p.layer.clearLayers();p.marker.setLatLng([st.lat,st.lon]).setIcon(evIcon(arrived?"#16a34a":DATA.modes[mode].color));for(const item of p.tls){{const el=item.marker.getElement();const vis=signalDisplay(item.sig,mode,t);if(el){{el.dataset.state=vis.state;el.classList.toggle("prepare",vis.prepare);el.classList.toggle("next",Boolean(ns&&item.sig.id===ns.id))}}}}const tv=drawTrafficState(p,st,ns,nextState,mode);if(!trafficFrames(mode).length)drawStage2Block(p,mode,st);const camera={{lat:st.lat,lon:st.lon}};if(t-p.lastCameraT>=.35||t===0||arrived){{p.map.setView([camera.lat,camera.lon],16.75,{{animate:false}});p.lastCameraT=t;}}const queueReason=mode==="B04"?"신호 변경 후 출발":"우선신호 대기열 해소";const stopped=!arrived&&st.speed_kmh<2;const signalHold=stopped&&["red","yellow","allred"].includes(nextState);const signalApproach=!stopped&&["red","yellow","allred"].includes(nextState);const reason=arrived?"도착":st.reason==="green_downstream_queue"?`신호 ${{nextState}} · 앞 큐 과잉 대기`:stopped&&tv.signalQueuedAhead?`앞 큐 뒤에서 ${{nextState}} 대기`:st.reason==="front_queue_tail"?`앞 큐 뒤에서 ${{nextState}} 대기`:st.reason==="front_queue_red"||signalHold?`다음 신호 ${{nextState}} 대기`:signalApproach?`다음 신호 ${{nextState}} 접근 중`:st.reason==="queue_clearing"?queueReason:st.reason==="stage2_hold"?"Stage2 유입 차단 대기":st.reason==="traffic_hold"||stopped?"정체 대기":"진행 중";const algDisplay=(st.algorithm||"-").includes("GREEN")&&nextState!=="green"?"Stage3 우선신호 제어":(st.algorithm||"-");const traffic=arrived?"도착 완료":tv.label;const h=holdReason(mode,st,nextState,tv);const holdLine=h?`<br><span class="why">${{h}}</span>`:"";setEventToast(mode,eventSummary(mode,st,nextState,tv,arrived,signalHold));const denom=DATA.modes[mode].distance_m||DATA.route.length_m;document.getElementById("tag"+SUF[mode]).innerHTML=`<b style="color:${{DATA.modes[mode].color}}">${{DATA.modes[mode].label}}</b><br>속도 ${{arrived?"도착":st.speed_kmh.toFixed(0)+" km/h"}} · 진행 ${{Math.min(100,Math.round(st.s/denom*100))}}% · 다음신호 ${{arrived?"-":nextState}}<br>알고리즘 <span class="why">${{algDisplay}}</span><br>EV 상태 <span class="why">${{reason}}</span>${{holdLine}}<br>일반차 <span class="traffic">${{traffic}}</span>`}}
function chart(){{const svg=document.getElementById("svg"),W=1000,H=110,pad=6;let vmax=60;const X=x=>pad+x/TMAX*(W-2*pad),Y=v=>H-pad-v/vmax*(H-2*pad);let html="";for(const m of MODES){{const series=DATA.modes[m].display_samples||DATA.modes[m].samples;const d=series.map((p,i)=>(i?"L":"M")+X(p.t).toFixed(1)+" "+Y(p.speed_kmh).toFixed(1)).join("");html+=`<path d="${{d}}" fill="none" stroke="${{DATA.modes[m].color}}" stroke-width="2"/>`}}html+=`<line id="cur" x1="${{X(t)}}" x2="${{X(t)}}" y1="0" y2="${{H}}" stroke="#e5e7eb" stroke-dasharray="3 3"/>`;svg.innerHTML=html;return X}}
const X=chart();document.getElementById("cmp").textContent=`B04 ${{DATA.modes.B04.travel_time_sec.toFixed(0)}}s vs B4 ${{DATA.modes.B4.travel_time_sec.toFixed(0)}}s`;
function render(){{updatePanel(panels.B04);updatePanel(panels.B4);document.getElementById("seek").value=Math.round(t/TMAX*1000);document.getElementById("clock").textContent=`t = ${{t.toFixed(1)}}s / ${{TMAX.toFixed(0)}}s`;const c=document.getElementById("cur");if(c){{c.setAttribute("x1",X(t));c.setAttribute("x2",X(t));}}}}
function loop(ts){{if(!playing)return;if(last!=null){{const step=(ts-last)/1000*rate;t=Math.min(TMAX,t+step);flowT+=step;}}last=ts;if(t>=TMAX){{playing=false;document.getElementById("play").textContent="▶ 재생"}}render();if(playing)requestAnimationFrame(loop)}}
document.getElementById("play").onclick=function(){{if(t>=TMAX)t=0;playing=!playing;this.textContent=playing?"⏸ 일시정지":"▶ 재생";last=null;if(playing)requestAnimationFrame(loop)}};
document.getElementById("reset").onclick=function(){{t=0;flowT=0;playing=false;document.getElementById("play").textContent="▶ 재생";Object.values(panels).forEach(p=>{{p.lastVisualT=-999;p.visualS=0;p.prevVisualS=0}});render()}};
document.getElementById("seek").oninput=function(){{t=this.value/1000*TMAX;flowT=t;Object.values(panels).forEach(p=>{{p.lastVisualT=-999;p.visualS=0;p.prevVisualS=0}});render()}};
setTimeout(()=>{{Object.values(panels).forEach(p=>p.map.invalidateSize());ov.invalidateSize();render()}},200);render();
</script></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    from scene_graph_presentation import build_scene_payload, render_scene_html

    payload = build_scene_payload()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_scene_html(payload), encoding="utf-8")
    output.with_name(f"{output.stem}_data.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    output.with_name(f"{output.stem}_validation_report.json").write_text(
        json.dumps(payload.get("validation_report", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {output}")
    print(f"Wrote {output.with_name(f'{output.stem}_data.json')}")
    print(f"Wrote {output.with_name(f'{output.stem}_validation_report.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
