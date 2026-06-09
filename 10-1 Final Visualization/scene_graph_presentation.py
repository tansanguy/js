#!/usr/bin/env python3
"""Scene-graph based renderer for the 10-1 presentation visualization.

The old presentation renderer mixed traffic decisions into the browser.  This
module builds a single lane-based scene graph in Python: every visible vehicle,
queue band, fake Stage2 stopline, and signal marker is resolved against SUMO
lane geometry before the HTML is written.  The browser only interpolates and
draws frames.
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sumolib  # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THIS_DIR = Path(__file__).resolve().parent

PRESENTATION_INPUTS = THIS_DIR / "10-1_presentation_inputs.json"
PRESENTATION_NET_FILE = THIS_DIR / "10-1_jungbu_compact_v9_B04_global_reality_s1forced_presentation.net.xml"
PRESENTATION_ROUTE_XML = THIS_DIR / "10-1_firetruck_final_route.rou.xml"
PRESENTATION_DEMAND_ROUTE = THIS_DIR / "10-1_background_routes_compact_v9_B04_ad_stage23_trigger_presentation.rou.xml"
ACTUAL_PROGRESS_DATA = THIS_DIR / "presentation_timing_seed_data.json"
TLS_PROGRESS_DATA = THIS_DIR / "presentation_tls_seed_data.json"
DISPLAY_TITLE = "중부소방서 출동 EV 우선신호 시각화"
PRESENTATION_ROUTE_ID = "FINAL_DEST_DONGHO_001"
PRESENTATION_SOURCE_TRUTH_RUN = {
    "final_run_id": "gcp_bo_top5_dongho_commonend_splitN_20260609_065501",
    "measured_run_id": "10_1_measured_dongho_opt_bo_r08_repeat_022_v2",
    "theta_label": "top_02_bo_r08_002_tl12_dt66_ge2_qr31_tau79",
    "selected_repeat": "repeat_022",
    "route_id": PRESENTATION_ROUTE_ID,
    "display_destination_name": False,
}

DISPLAY_TRAFFIC_DT = 0.1
PRESENTATION_CRUISE_MPS = 10.2
PRESENTATION_ACCEL_MPS2 = 1.6
GENERAL_VEHICLE_COLOR = "#f97316"
GENERAL_VEHICLE_OPACITY = 0.88
PRESENTATION_DESTINATION = {
    "lat": 37.560208,
    "lon": 127.002440,
    "label": "도착 지점",
    "source": "user_requested_20260609",
}
PRESENTATION_DONGHO_WAYPOINT_EDGES = ["-1455512070", "218773868#6"]
STAGE2_CENTER = {"lat": 37.565319, "lon": 127.016594}
STAGE2_SIGNAL_CENTER = {"lat": 37.5654120, "lon": 127.0168582}
STAGE2_NORTH_DASAN_JUNCTION_ID = "cluster_4200656797_8343570277"
STAGE2_APPROACH_LANE_CHAINS = {
    "north": ["218684408#1_0", "218684408#2_0"],
    "south": ["37399924#1_0", "37399924#2_0"],
    "east": ["218684411#1_0", "218684411#2_0", "218684411#3_0"],
}
FIRETRUCK_EXIT = {"lat": 37.565126, "lon": 127.015716}


@dataclass
class PathSegment:
    edge_id: str
    lane_id: str
    start_s: float
    end_s: float
    points: list[dict[str, float]]


@dataclass
class LanePath:
    points: list[dict[str, float]]
    segments: list[PathSegment]
    length_m: float


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * 111_320.0
    dlon = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dlat, dlon)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    if abs(edge1 - edge0) <= 1e-9:
        return 1.0 if value >= edge1 else 0.0
    x = clamp((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def route_edges() -> list[str]:
    root = ET.parse(PRESENTATION_ROUTE_XML).getroot()
    route = root.find(".//route")
    if route is None:
        raise SystemExit(f"missing route in {PRESENTATION_ROUTE_XML}")
    return str(route.get("edges", "")).split()


def load_inputs() -> dict[str, Any]:
    if not PRESENTATION_INPUTS.is_file():
        return {"schema": "10-1_presentation_inputs.missing", "presentation_signal_policy": {}, "presentation_demand_policy": {}}
    return json.loads(PRESENTATION_INPUTS.read_text(encoding="utf-8"))


def mode_policy_number(policy: dict[str, Any], key: str, mode: str, fallback: float) -> float:
    value = policy.get(key, {})
    if isinstance(value, dict):
        return float(value.get(mode, fallback))
    if value in {None, ""}:
        return fallback
    return float(value)


def presentation_time_at_s(s_m: float) -> float:
    return max(0.0, float(s_m)) / PRESENTATION_CRUISE_MPS


def presentation_demand_policy(inputs: dict[str, Any]) -> dict[str, Any]:
    default = {
        "front_queue_cap": 9,
        "behind_queue_cap": 7,
        "green_discharge_veh_per_sec": 2.0,
        "downstream_queue_signal_indices": {},
        "downstream_queue_duration_sec": 0.0,
        "downstream_queue_count": 0.0,
        "downstream_queue_lookahead_m": 120.0,
        "front_queue_overflow_windows": {},
        "queue_spacing_m": 13.0,
        "ev_queue_gap_m": 24.0,
    }
    policy = dict(default)
    policy.update(inputs.get("presentation_demand_policy", {}))
    return policy


def lane_latlon_shape(net: Any, lane: Any) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    total = 0.0
    prev: tuple[float, float] | None = None
    for x, y in lane.getShape():
        lon, lat = net.convertXY2LonLat(float(x), float(y))
        if prev is not None:
            total += meters_between(prev[0], prev[1], float(lat), float(lon))
        points.append({"lat": round(float(lat), 7), "lon": round(float(lon), 7), "s": round(total, 2)})
        prev = (float(lat), float(lon))
    return points


def make_lane_entry(edge_id: str, lane_id: str, points: list[dict[str, float]], source: str) -> dict[str, Any]:
    return {
        "id": lane_id,
        "edge_id": edge_id,
        "source": source,
        "length_m": round(points[-1]["s"] if points else 0.0, 2),
        "shape": [{"lat": p["lat"], "lon": p["lon"], "s": p["s"]} for p in points],
    }


def append_path_segment(
    out_points: list[dict[str, float]],
    segments: list[PathSegment],
    edge_id: str,
    lane_id: str,
    lane_points: list[dict[str, float]],
) -> None:
    if len(lane_points) < 2:
        return
    local_points = lane_points
    if out_points and meters_between(out_points[-1]["lat"], out_points[-1]["lon"], local_points[0]["lat"], local_points[0]["lon"]) > (
        meters_between(out_points[-1]["lat"], out_points[-1]["lon"], local_points[-1]["lat"], local_points[-1]["lon"])
    ):
        local_points = list(reversed([
            {"lat": p["lat"], "lon": p["lon"], "s": round(local_points[-1]["s"] - p["s"], 2)}
            for p in local_points
        ]))
    start_s = out_points[-1]["s"] if out_points else 0.0
    base_s = start_s
    prev = (out_points[-1]["lat"], out_points[-1]["lon"]) if out_points else None
    seg_points: list[dict[str, float]] = []
    for idx, point in enumerate(local_points):
        lat = float(point["lat"])
        lon = float(point["lon"])
        if idx == 0:
            route_point = {"lat": round(lat, 7), "lon": round(lon, 7), "s": round(base_s, 2)}
            if not out_points or meters_between(out_points[-1]["lat"], out_points[-1]["lon"], lat, lon) >= 0.5:
                out_points.append(route_point)
            seg_points.append({"lat": round(lat, 7), "lon": round(lon, 7), "s": 0.0})
            prev = (lat, lon)
            continue
        if prev is not None:
            step = meters_between(prev[0], prev[1], lat, lon)
            base_s += step
        route_point = {"lat": round(lat, 7), "lon": round(lon, 7), "s": round(base_s, 2)}
        if not out_points or meters_between(out_points[-1]["lat"], out_points[-1]["lon"], lat, lon) >= 0.5:
            out_points.append(route_point)
        seg_points.append({"lat": round(lat, 7), "lon": round(lon, 7), "s": round(base_s - start_s, 2)})
        prev = (lat, lon)
    if seg_points:
        segments.append(PathSegment(edge_id=edge_id, lane_id=lane_id, start_s=round(start_s, 2), end_s=round(base_s, 2), points=seg_points))


def lane_index(lane_id: str) -> int:
    try:
        return int(str(lane_id).rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return 0


def center_lane(edge: Any) -> Any:
    lanes = edge.getLanes()
    return lanes[min(len(lanes) - 1, max(0, len(lanes) // 2))]


def route_connections(edge: Any, next_edge: Any) -> list[Any]:
    outgoing = edge.getOutgoing()
    if hasattr(outgoing, "get"):
        return list(outgoing.get(next_edge, []))
    return []


def choose_route_connection(edge: Any, next_edge: Any, preferred_lane: Any | None) -> Any | None:
    conns = route_connections(edge, next_edge)
    if not conns:
        return None
    if preferred_lane is not None:
        exact = [conn for conn in conns if conn.getFromLane().getID() == preferred_lane.getID()]
        if exact:
            conns = exact
    center = (len(edge.getLanes()) - 1) / 2.0
    direction_score = {"s": 0, "r": 1, "l": 2, "t": 5}
    return sorted(
        conns,
        key=lambda conn: (
            direction_score.get(str(conn.getDirection()), 3),
            abs(lane_index(conn.getFromLane().getID()) - center),
            lane_index(conn.getFromLane().getID()),
        ),
    )[0]


def add_lane_segment(net: Any, lane: Any, source: str, points: list[dict[str, float]], segments: list[PathSegment], lanes_out: dict[str, dict[str, Any]]) -> None:
    lane_points = lane_latlon_shape(net, lane)
    if points and len(lane_points) >= 2:
        last = points[-1]
        if meters_between(last["lat"], last["lon"], lane_points[0]["lat"], lane_points[0]["lon"]) > meters_between(last["lat"], last["lon"], lane_points[-1]["lat"], lane_points[-1]["lon"]):
            lane_points = list(reversed([
                {"lat": p["lat"], "lon": p["lon"], "s": round(lane_points[-1]["s"] - p["s"], 2)}
                for p in lane_points
            ]))
        gap_m = meters_between(last["lat"], last["lon"], lane_points[0]["lat"], lane_points[0]["lon"])
        if gap_m > 1.0:
            connector_id = f"10-1_virtual_connector_{len(segments):03d}"
            connector_points = [
                {"lat": last["lat"], "lon": last["lon"], "s": 0.0},
                {"lat": lane_points[0]["lat"], "lon": lane_points[0]["lon"], "s": round(gap_m, 2)},
            ]
            lanes_out.setdefault(connector_id, make_lane_entry(connector_id, connector_id, connector_points, "10-1_virtual_straight_connector"))
            append_path_segment(points, segments, connector_id, connector_id, connector_points)
    edge_id = lane.getEdge().getID()
    lanes_out.setdefault(lane.getID(), make_lane_entry(edge_id, lane.getID(), lane_points, source))
    append_path_segment(points, segments, edge_id, lane.getID(), lane_points)


def build_path_from_edges(net: Any, edges: list[str], lane_source: str, lanes_out: dict[str, dict[str, Any]]) -> LanePath:
    points: list[dict[str, float]] = []
    segments: list[PathSegment] = []
    edge_objs = [net.getEdge(edge_id) for edge_id in edges]
    selected_lane: Any | None = None
    for idx, edge in enumerate(edge_objs):
        if not edge.getLanes():
            continue
        next_edge = edge_objs[idx + 1] if idx + 1 < len(edge_objs) else None
        conn = choose_route_connection(edge, next_edge, selected_lane) if next_edge is not None else None
        lane = selected_lane if selected_lane is not None and selected_lane.getEdge().getID() == edge.getID() else None
        if conn is not None and (lane is None or conn.getFromLane().getID() != lane.getID()):
            lane = conn.getFromLane()
        if lane is None:
            lane = center_lane(edge)
        add_lane_segment(net, lane, lane_source, points, segments, lanes_out)
        if conn is not None:
            selected_lane = conn.getToLane()
        else:
            selected_lane = None
    length = points[-1]["s"] if points else 0.0
    return LanePath(points=points, segments=segments, length_m=round(length, 2))


def path_from_segments(segments_in: list[PathSegment]) -> LanePath:
    points: list[dict[str, float]] = []
    segments: list[PathSegment] = []
    for segment in segments_in:
        append_path_segment(points, segments, segment.edge_id, segment.lane_id, segment.points)
    length = points[-1]["s"] if points else 0.0
    return LanePath(points=points, segments=segments, length_m=round(length, 2))


def straight_segment_points(a: dict[str, float], b: dict[str, float]) -> list[dict[str, float]]:
    return [
        {"lat": round(float(a["lat"]), 7), "lon": round(float(a["lon"]), 7), "s": 0.0},
        {"lat": round(float(b["lat"]), 7), "lon": round(float(b["lon"]), 7), "s": round(meters_between(float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"])), 2)},
    ]


def recover_dongho_straight(path: LanePath, lanes_out: dict[str, dict[str, Any]]) -> LanePath:
    start_idx = next((idx + 1 for idx, segment in enumerate(path.segments) if segment.edge_id == "-1455512070"), None)
    end_idx = next((idx for idx, segment in enumerate(path.segments) if segment.edge_id == "-913006754#9"), None)
    if start_idx is None or end_idx is None or start_idx > end_idx:
        return path
    start = path.segments[start_idx].points[0]
    end = path.segments[end_idx].points[-1]
    connector_id = "10-1_dongho_straight_recovery"
    connector_points = straight_segment_points(start, end)
    lanes_out[connector_id] = make_lane_entry(connector_id, connector_id, connector_points, "10-1_dongho_straight_recovery")
    replacement = PathSegment(connector_id, connector_id, 0.0, connector_points[-1]["s"], connector_points)
    return path_from_segments(path.segments[:start_idx] + [replacement] + path.segments[end_idx + 1:])


def point_on_polyline(points: list[dict[str, float]], s_m: float) -> dict[str, float]:
    if not points:
        return {"lat": 0.0, "lon": 0.0, "s": 0.0}
    if s_m <= 0:
        return dict(points[0])
    if s_m >= float(points[-1]["s"]):
        return dict(points[-1])
    lo, hi, idx = 0, len(points) - 1, 0
    while lo <= hi:
        mid = (lo + hi) >> 1
        if float(points[mid]["s"]) <= s_m:
            idx = mid
            lo = mid + 1
        else:
            hi = mid - 1
    a = points[idx]
    b = points[min(idx + 1, len(points) - 1)]
    span = float(b["s"]) - float(a["s"])
    f = (s_m - float(a["s"])) / span if span else 0.0
    return {
        "lat": round(float(a["lat"]) + (float(b["lat"]) - float(a["lat"])) * f, 7),
        "lon": round(float(a["lon"]) + (float(b["lon"]) - float(a["lon"])) * f, 7),
        "s": round(s_m, 2),
    }


def path_point(path: LanePath, s_m: float) -> dict[str, Any]:
    s_m = max(0.0, min(float(path.length_m), float(s_m)))
    segment = path.segments[-1] if path.segments else None
    for item in path.segments:
        if item.start_s <= s_m <= item.end_s + 1e-6:
            segment = item
            break
    if segment is None:
        p = point_on_polyline(path.points, s_m)
        return {**p, "angle": 0.0, "lane_id": "", "edge_id": "", "lane_s": 0.0}
    lane_s = max(0.0, min(segment.end_s - segment.start_s, s_m - segment.start_s))
    p = point_on_polyline(segment.points, lane_s)
    p_prev = point_on_polyline(segment.points, max(0.0, lane_s - 4.0))
    p_next = point_on_polyline(segment.points, min(segment.points[-1]["s"], lane_s + 6.0))
    mid_lat = (p_prev["lat"] + p_next["lat"]) / 2.0
    dx = (p_next["lon"] - p_prev["lon"]) * 111_320.0 * math.cos(math.radians(mid_lat))
    dy = (p_next["lat"] - p_prev["lat"]) * 111_320.0
    angle = math.degrees(math.atan2(-dy, dx))
    return {
        "lat": p["lat"],
        "lon": p["lon"],
        "s": round(s_m, 2),
        "angle": round(angle, 1),
        "lane_id": segment.lane_id,
        "edge_id": segment.edge_id,
        "lane_s": round(lane_s, 2),
    }


def trim_path(path: LanePath, target_s: float) -> LanePath:
    target_s = max(1.0, min(path.length_m, target_s))
    points = [p for p in path.points if float(p["s"]) <= target_s]
    end = path_point(path, target_s)
    if not points or meters_between(points[-1]["lat"], points[-1]["lon"], end["lat"], end["lon"]) >= 0.5:
        points.append({"lat": end["lat"], "lon": end["lon"], "s": round(target_s, 2)})
    segments: list[PathSegment] = []
    for segment in path.segments:
        if segment.start_s >= target_s:
            break
        seg_end = min(segment.end_s, target_s)
        seg_points = [p for p in segment.points if segment.start_s + float(p["s"]) <= seg_end]
        local_end = point_on_polyline(segment.points, seg_end - segment.start_s)
        if not seg_points or meters_between(seg_points[-1]["lat"], seg_points[-1]["lon"], local_end["lat"], local_end["lon"]) >= 0.5:
            seg_points.append(local_end)
        segments.append(PathSegment(segment.edge_id, segment.lane_id, segment.start_s, round(seg_end, 2), seg_points))
    return LanePath(points=points, segments=segments, length_m=round(target_s, 2))


def closest_s_on_path(points: list[dict[str, float]], lat: float, lon: float) -> tuple[float, float, dict[str, float]]:
    if len(points) < 2:
        return 0.0, 0.0, points[0] if points else {"lat": lat, "lon": lon, "s": 0.0}
    scale = 111_320.0 * math.cos(math.radians(lat))
    best: tuple[float, float, dict[str, float]] | None = None
    for a, b in zip(points, points[1:]):
        ax = (float(a["lon"]) - lon) * scale
        ay = (float(a["lat"]) - lat) * 111_320.0
        bx = (float(b["lon"]) - lon) * scale
        by = (float(b["lat"]) - lat) * 111_320.0
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        f = 0.0 if denom <= 1e-9 else max(0.0, min(1.0, -(ax * vx + ay * vy) / denom))
        px = ax + vx * f
        py = ay + vy * f
        dist = math.hypot(px, py)
        s = float(a["s"]) + (float(b["s"]) - float(a["s"])) * f
        proj = {
            "lat": round(float(a["lat"]) + (float(b["lat"]) - float(a["lat"])) * f, 7),
            "lon": round(float(a["lon"]) + (float(b["lon"]) - float(a["lon"])) * f, 7),
            "s": round(s, 2),
        }
        if best is None or dist < best[1]:
            best = (s, dist, proj)
    assert best is not None
    return best


def build_route_path(net: Any, lanes_out: dict[str, dict[str, Any]]) -> tuple[LanePath, dict[str, Any]]:
    target_lat = float(PRESENTATION_DESTINATION["lat"])
    target_lon = float(PRESENTATION_DESTINATION["lon"])
    start_edge = net.getEdge(route_edges()[0])
    x, y = net.convertLonLat2XY(target_lon, target_lat)
    candidates = sorted(net.getNeighboringEdges(x, y, 180.0, includeJunctions=False), key=lambda item: item[1])
    best: tuple[list[Any], float, float, str, str] | None = None
    for edge, dist in candidates[:28]:
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
        raise SystemExit("could not build 10-1 route to requested destination on SUMO graph")
    path_edges, _score, edge_dist, target_edge_id, waypoint_id = best
    full_path = build_path_from_edges(net, [edge.getID() for edge in path_edges], "ev_route", lanes_out)
    target_s, road_offset, projection = closest_s_on_path(full_path.points, target_lat, target_lon)
    trimmed = recover_dongho_straight(trim_path(full_path, target_s), lanes_out)
    meta = {
        **PRESENTATION_DESTINATION,
        "route_end_lat": projection["lat"],
        "route_end_lon": projection["lon"],
        "road_offset_m": round(road_offset, 2),
        "routing": "sumo_shortest_path_via_dongho_waypoint",
        "required_waypoint_edge_id": waypoint_id,
        "target_edge_id": target_edge_id,
        "target_edge_distance_m": round(edge_dist, 2),
        "path_edge_count": len(path_edges),
        "path_edges": [edge.getID() for edge in path_edges],
        "presentation_route_length_m": trimmed.length_m,
        "vehicle_arrival_note": "EV follows SUMO lane centerline; target coordinate is stored as destination metadata.",
    }
    return trimmed, meta


def route_display_parts(path: LanePath) -> list[list[dict[str, float]]]:
    parts: list[list[dict[str, float]]] = []
    for segment in path.segments:
        part = [
            {"lat": point["lat"], "lon": point["lon"], "s": round(segment.start_s + point["s"], 2)}
            for point in segment.points
        ]
        if len(part) >= 2:
            parts.append(part)
    return parts


def state_at(timeline: list[list[Any]], t: float) -> str:
    state = str(timeline[0][1]) if timeline else "green"
    for ts, value in timeline:
        if float(ts) <= t:
            state = str(value)
        else:
            break
    return state


def dedupe_timeline_rows(rows: list[list[Any]]) -> list[list[Any]]:
    out: list[list[Any]] = []
    for t, state in rows:
        t = round(max(0.0, float(t)), 2)
        state = str(state)
        if out and out[-1][1] == state:
            continue
        if out and out[-1][0] == t:
            out[-1][1] = state
        else:
            out.append([t, state])
    return out


def normalize_actual_timeline(timeline: list[list[Any]], t_max: float) -> list[list[Any]]:
    non_green = {"yellow", "red", "allred"}
    rows = [[max(0.0, round(float(ts), 2)), str(state)] for ts, state in timeline if -2.0 <= float(ts) <= t_max + 30.0]
    if not rows:
        return [[0.0, "green"], [max(2.5, t_max - 6.0), "yellow"], [max(5.0, t_max - 3.5), "red"]]
    rows.sort(key=lambda item: item[0])
    deduped = dedupe_timeline_rows(rows)
    if deduped[0][0] > 0.0:
        deduped.insert(0, [0.0, deduped[0][1]])
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
            if end - start >= 5.0:
                if canonical and canonical[-1][1] == "green":
                    canonical.append([start, "yellow"])
                    canonical.append([round(start + 2.5, 2), "red"])
                else:
                    canonical.append([start, "red"])
            i = j
            continue
        i += 1
    out = dedupe_timeline_rows(canonical)
    if not any(state == "yellow" for _, state in out) and t_max >= 8.0:
        insert_t = max(0.0, round(t_max - 7.0, 2))
        out.extend([[insert_t, "yellow"], [round(insert_t + 2.5, 2), "red"]])
        out.sort(key=lambda item: item[0])
        out = dedupe_timeline_rows(out)
    return out


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


def build_presentation_timeline(
    mode: str,
    raw_timeline: list[list[Any]],
    sig: dict[str, Any],
    signal_index: int,
    samples: list[dict[str, Any]],
    t_max: float,
    signal_policy: dict[str, Any],
    planned_arrival: float | None = None,
) -> list[list[Any]]:
    if signal_policy.get("mode") != "10-1_suitable_signal_system":
        return raw_timeline
    # Signal display windows must use the same clock as the smoothed EV display
    # model. Using raw FCD arrival times here lets the baseline visually miss red
    # windows after route smoothing changes the display travel time.
    reference_s = max(0.0, float(sig["s"]) - 72.0)
    clear_s = min(float(sig["s"]) + 45.0, samples[-1]["s"])
    arrival = planned_arrival if planned_arrival is not None else presentation_time_at_s(reference_s)
    clear_time = arrival + max(0.0, clear_s - reference_s) / PRESENTATION_CRUISE_MPS
    yellow = float(signal_policy.get("yellow_sec", 2.5))
    if mode == "B4":
        best_theta = signal_policy.get("best_theta", {})
        lead = float(best_theta.get("t_lead", signal_policy.get("b4_green_lead_sec", 12.0)))
        hold = float(best_theta.get("G_ext", signal_policy.get("b4_green_hold_sec", 52.0)))
        green_start = max(0.0, round(arrival - lead, 2))
        green_end = round(max(arrival + hold, (clear_time or arrival) + 10.0), 2)
        rows: list[list[Any]] = [[0.0, "green" if green_start <= 0.0 else "red"]]
        if green_start > 0:
            rows.append([green_start, "green"])
        rows.extend([[green_end, "yellow"], [round(green_end + yellow, 2), "red"], [round(green_end + yellow + 18.0, 2), "green"]])
        return dedupe_timeline_rows(rows)
    red_indices = {int(x) for x in signal_policy.get("b04_red_signal_indices", [])}
    red_lead = float(signal_policy.get("b04_red_lead_sec", 18.0))
    red_hold = float(signal_policy.get("b04_red_hold_sec", 36.0))
    if signal_index not in red_indices:
        late_yellow = round(max(t_max + 30.0, arrival + 150.0), 2)
        return dedupe_timeline_rows([[0.0, "green"], [late_yellow, "yellow"], [round(late_yellow + yellow, 2), "red"], [round(late_yellow + yellow + 18.0, 2), "green"]])
    red_start = max(0.0, round(arrival - red_lead, 2))
    red_end = round(arrival + red_hold, 2)
    late_yellow = round(max(t_max + 30.0, red_end + 150.0), 2)
    rows = [[0.0, "green"], [round(max(0.0, red_start - yellow), 2), "yellow"], [red_start, "red"], [red_end, "green"], [late_yellow, "yellow"], [round(late_yellow + yellow, 2), "red"], [round(late_yellow + yellow + 18.0, 2), "green"]]
    return dedupe_timeline_rows(rows)


def planned_signal_arrivals(signals: list[dict[str, Any]], signal_policy: dict[str, Any], demand_policy: dict[str, Any], mode: str) -> dict[str, float]:
    if signal_policy.get("mode") != "10-1_suitable_signal_system":
        return {}
    red_indices = {int(x) for x in signal_policy.get("b04_red_signal_indices", [])}
    red_hold = float(signal_policy.get("b04_red_hold_sec", 36.0))
    front_cap = mode_policy_number(demand_policy, "front_queue_cap", mode, 9.0)
    queue_spacing = float(demand_policy.get("queue_spacing_m", 13.0))
    ev_queue_gap = float(demand_policy.get("ev_queue_gap_m", 24.0))
    current_t = 0.0
    current_s = 0.0
    planned: dict[str, float] = {}
    for sig in sorted(signals, key=lambda item: float(item["s"])):
        reference_s = max(0.0, float(sig["s"]) - 72.0)
        if reference_s > current_s:
            current_t += (reference_s - current_s) / PRESENTATION_CRUISE_MPS
        planned[str(sig["id"])] = current_t
        try:
            sig_idx = int(str(sig["id"]).replace("S", ""))
        except ValueError:
            sig_idx = -1
        if mode == "B04" and sig_idx in red_indices:
            current_t += red_hold
            stop_s = max(0.0, float(sig["s"]) - 18.0)
            current_s = max(current_s, stop_s - 10.0 - front_cap * queue_spacing - ev_queue_gap)
        else:
            current_s = max(current_s, reference_s)
    return planned


def validate_timelines(timelines: dict[str, dict[str, list[list[Any]]]]) -> dict[str, Any]:
    summary = {
        "signals_checked": 0,
        "missing_yellow": 0,
        "green_yellow_green": 0,
        "red_yellow_red": 0,
        "yellow_red_yellow": 0,
        "red_yellow_red_yellow": 0,
        "short_yellow": 0,
        "short_non_green": 0,
        "ok": True,
    }
    for by_signal in timelines.values():
        for timeline in by_signal.values():
            summary["signals_checked"] += 1
            states = [str(st) for _, st in timeline]
            if "yellow" not in states:
                summary["missing_yellow"] += 1
            for a, b, c in zip(states, states[1:], states[2:]):
                if a == "green" and b == "yellow" and c == "green":
                    summary["green_yellow_green"] += 1
                if a in {"red", "allred"} and b == "yellow" and c in {"red", "allred"}:
                    summary["red_yellow_red"] += 1
                if a == "yellow" and b in {"red", "allred"} and c == "yellow":
                    summary["yellow_red_yellow"] += 1
            for a, b, c, d in zip(states, states[1:], states[2:], states[3:]):
                if a in {"red", "allred"} and b == "yellow" and c in {"red", "allred"} and d == "yellow":
                    summary["red_yellow_red_yellow"] += 1
            for (ts, st), (next_ts, _) in zip(timeline, timeline[1:]):
                dur = float(next_ts) - float(ts)
                if st == "yellow" and dur < 2.5:
                    summary["short_yellow"] += 1
                if st in {"yellow", "red", "allred"} and dur < 2.5:
                    summary["short_non_green"] += 1
    bad_keys = ("missing_yellow", "green_yellow_green", "red_yellow_red", "yellow_red_yellow", "red_yellow_red_yellow", "short_yellow")
    summary["ok"] = all(int(summary[k]) == 0 for k in bad_keys)
    return summary


def initial_green_window(timeline: list[list[Any]]) -> list[float] | None:
    """Return the first B4 priority green hold before yellow clearance."""
    if not timeline:
        return None
    start: float | None = None
    for idx, (ts, state) in enumerate(timeline):
        t = float(ts)
        if state == "green" and start is None:
            start = t
        elif start is not None and state != "green":
            if t - start >= 2.0:
                return [round(start, 2), round(t, 2)]
            start = None
        if idx == len(timeline) - 1 and start is not None:
            return [round(start, 2), round(t + 30.0, 2)]
    return None


def algorithm_label_at(events: list[dict[str, Any]], t: float) -> str:
    recent = [event for event in events if float(event.get("t_rel", 0.0)) <= t and t - float(event.get("t_rel", 0.0)) <= 10.0]
    if any(str(event.get("stage")) == "stage2" for event in recent):
        return "Stage2 신당역 유입 차단"
    if any(str(event.get("case")) == "Case B" for event in recent):
        return "Stage3 Case B 하류 큐 정리"
    for event in reversed(recent):
        action = str(event.get("action", ""))
        if action in {"GREEN_ACTIVE", "extend_target_green"}:
            return f"Stage3 {event.get('case') or 'Case A'} GREEN"
        if action == "DENIED_BY_SAFETY":
            return "SafetyGate 전이 대기"
    return "-"


def build_raw_samples(source_doc: dict[str, Any], route_path: LanePath, mode: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_len = max(1.0, float(source_doc.get("distance_m", source_doc.get("travel_time_sec", 1.0))))
    out: list[dict[str, Any]] = []
    prev_s = 0.0
    prev_t: float | None = None
    for row in source_doc.get("emergency", []):
        t = round(float(row["t_rel"]), 1)
        s = min(route_path.length_m, max(prev_s, float(row.get("dist_m", 0.0)) / source_len * route_path.length_m))
        p = path_point(route_path, s)
        if prev_t is None:
            speed = float(row.get("speed_kmh", 0.0))
        else:
            speed = (s - prev_s) / max(0.1, t - prev_t) * 3.6
        alg = algorithm_label_at(events, t)
        out.append({
            "t": t,
            "s": round(s, 2),
            "lat": p["lat"],
            "lon": p["lon"],
            "lane_id": p["lane_id"],
            "lane_s": p["lane_s"],
            "edge_id": p["edge_id"],
            "speed_kmh": round(max(0.0, min(58.0, speed)), 1),
            "reason": "traffic_hold" if speed < 1.0 else "moving",
            "algorithm": alg,
        })
        prev_s = s
        prev_t = t
    if out:
        out[-1]["s"] = round(route_path.length_m, 2)
        p = path_point(route_path, route_path.length_m)
        out[-1].update({"lat": p["lat"], "lon": p["lon"], "lane_id": p["lane_id"], "lane_s": p["lane_s"], "edge_id": p["edge_id"], "speed_kmh": 0.0, "reason": "arrived"})
    return out


def sample_at_time(samples: list[dict[str, Any]], t: float) -> dict[str, Any]:
    if not samples:
        return {}
    if t <= float(samples[0]["t"]):
        return dict(samples[0])
    if t >= float(samples[-1]["t"]):
        return dict(samples[-1])
    idx = 0
    for i, row in enumerate(samples):
        if float(row["t"]) <= t:
            idx = i
        else:
            break
    a = samples[idx]
    b = samples[min(idx + 1, len(samples) - 1)]
    span = float(b["t"]) - float(a["t"])
    f = (t - float(a["t"])) / span if span else 0.0
    out = dict(b)
    out["t"] = round(t, 1)
    for key in ("s", "lat", "lon", "speed_kmh", "front_queue_count"):
        if key in a and key in b and a[key] is not None and b[key] is not None:
            out[key] = round(float(a[key]) + (float(b[key]) - float(a[key])) * f, 7 if key in {"lat", "lon"} else 2)
    return out


def high_res_samples(samples: list[dict[str, Any]], dt: float = DISPLAY_TRAFFIC_DT) -> list[dict[str, Any]]:
    if not samples:
        return []
    out: list[dict[str, Any]] = []
    start = float(samples[0]["t"])
    end = float(samples[-1]["t"])
    steps = int(math.ceil((end - start) / dt))
    for i in range(steps + 1):
        t = round(min(end, start + i * dt), 1)
        if out and abs(float(out[-1]["t"]) - t) < 1e-6:
            continue
        out.append(sample_at_time(samples, t))
    return out


def next_signal_after(signals: list[dict[str, Any]], s_m: float) -> dict[str, Any] | None:
    for sig in signals:
        if float(sig["s"]) > s_m + 2.0:
            return sig
    return None


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


def build_algorithm_trace(source: dict[str, Any], route_id: str, route_len: float, signals: list[dict[str, Any]], signal_policy: dict[str, Any]) -> dict[str, Any]:
    best_theta = signal_policy.get("best_theta", {})
    events = [{k: event.get(k, "") for k in (
        "t_abs", "t_rel", "stage", "action", "action_type", "tls_id", "movement_id", "case", "trigger_reason",
        "gate_result", "safety_result", "target_phase", "current_phase", "current_state", "stage2_hold_status",
        "Lq_merge_m", "Q_th_merge_m", "n_occ_runtime_veh", "n_need_proxy_veh", "merge_space_deficit",
    ) if k in event} for event in source.get("algorithm", {}).get("events", {}).get("B4", [])]
    stage2 = [event for event in events if event.get("stage") == "stage2"]
    stage3 = [event for event in events if event.get("stage") == "stage3"]
    return {
        "stage1": {
            "route_id": route_id,
            "route_length_m": round(route_len, 2),
            "best_theta": best_theta,
            "signals": [{"display_id": sig["id"], "tls_id": sig.get("source_tls_id") or sig.get("raw_id", ""), "s_m": sig["s"]} for sig in signals],
        },
        "stage2": stage2,
        "stage3": stage3,
        "safety_gate": [event for event in events if event.get("safety_result") not in {"", None} or event.get("gate_result") not in {"", None}],
        "event_counts": {
            "stage2": len(stage2),
            "stage3": len(stage3),
            "case_a": sum(1 for event in stage3 if event.get("case") == "Case A"),
            "case_b": sum(1 for event in stage3 if event.get("case") == "Case B"),
            "green_active": sum(1 for event in stage3 if event.get("action") == "GREEN_ACTIVE"),
            "safety_denied": sum(1 for event in stage3 if event.get("action") == "DENIED_BY_SAFETY"),
        },
    }


def build_signals(source: dict[str, Any], tls_source: dict[str, Any], route_path: LanePath, samples_by_mode: dict[str, list[dict[str, Any]]], signal_policy: dict[str, Any], demand_policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, list[list[Any]]]], dict[str, Any]]:
    raw_signals: list[dict[str, Any]] = []
    for idx, tl in enumerate(tls_source.get("traffic_lights", source.get("traffic_lights", [])), 1):
        lat = float(tl["lat"])
        lon = float(tl["lon"])
        s, dist, _ = closest_s_on_path(route_path.points, lat, lon)
        if s <= 0 or s >= route_path.length_m - 30 or dist > 90.0:
            continue
        raw_signals.append({
            "id": f"S{idx:02d}",
            "raw_id": str(tl.get("raw_tls_id") or tl.get("tls_id") or ""),
            "source_tls_id": str(tl.get("tls_id") or ""),
            "name": str(tl.get("tls_id") or f"Signal {idx}"),
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "s": round(s, 2),
            "route_distance_m": round(dist, 2),
        })
    collapsed: list[dict[str, Any]] = []
    for sig in sorted(raw_signals, key=lambda item: item["s"]):
        if collapsed and abs(float(sig["s"]) - float(collapsed[-1]["s"])) < 170.0:
            collapsed[-1].setdefault("merged_raw_ids", [collapsed[-1]["raw_id"]])
            collapsed[-1]["merged_raw_ids"].append(sig["raw_id"])
            continue
        collapsed.append(sig)
    for i, sig in enumerate(collapsed, 1):
        sig["id"] = f"S{i:02d}"
        p = path_point(route_path, float(sig["s"]))
        sig["stopline_lanes"] = [{
            "lane_id": p["lane_id"],
            "edge_id": p["edge_id"],
            "lane_s": p["lane_s"],
            "lat": p["lat"],
            "lon": p["lon"],
            "source": "route_lane_projection",
        }]
    timelines: dict[str, dict[str, list[list[Any]]]] = {"B04": {}, "B4": {}}
    for mode in ("B04", "B4"):
        planned_arrivals = planned_signal_arrivals(collapsed, signal_policy, demand_policy, mode)
        t_max = max(float(samples_by_mode[mode][-1]["t"]), float(tls_source.get("modes", {}).get(mode, {}).get("travel_time_sec", 0.0)))
        tls_states = tls_source.get("modes", {}).get(mode, source["modes"][mode]).get("tls_states", {})
        for sig in collapsed:
            source_id = sig.get("source_tls_id", "")
            raw = normalize_actual_timeline(tls_states.get(source_id, []), t_max)
            timelines[mode][sig["id"]] = build_presentation_timeline(mode, raw, sig, int(sig["id"].replace("S", "")), samples_by_mode[mode], t_max, signal_policy, planned_arrivals.get(str(sig["id"])))
    for sig in collapsed:
        priority = initial_green_window(timelines["B4"].get(sig["id"], []))
        sig["priority_windows"] = {"B4": [priority] if priority else []}
        sig["priority_visual"] = "green_backlight"
    tls_validation = validate_timelines(timelines)
    tls_validation["close_display_signal_pairs"] = sum(1 for a, b in zip(collapsed, collapsed[1:]) if abs(float(b["s"]) - float(a["s"])) < 170.0)
    return collapsed, timelines, tls_validation


def build_display_samples(
    mode: str,
    raw_samples: list[dict[str, Any]],
    profile: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    timelines: dict[str, list[list[Any]]],
    route_path: LanePath,
    demand_policy: dict[str, Any],
    algorithm_trace: dict[str, Any],
) -> list[dict[str, Any]]:
    front_cap = mode_policy_number(demand_policy, "front_queue_cap", mode, 9.0)
    queue_spacing = float(demand_policy.get("queue_spacing_m", 13.0))
    ev_queue_gap = float(demand_policy.get("ev_queue_gap_m", 24.0))
    downstream_indices = {int(value) for value in demand_policy.get("downstream_queue_signal_indices", {}).get(mode, [])}
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
        if sig_idx in downstream_indices:
            arrival = presentation_time_at_s(max(0.0, float(sig["s"]) - downstream_lookahead))
            downstream_windows[sig["id"]] = (max(0.0, arrival - 2.0), arrival + downstream_duration)
    stage2_events = algorithm_trace.get("stage2", [])
    release_t = max(12.0, max((float(event.get("t_rel", 0.0)) for event in stage2_events if event.get("action") == "RELEASE"), default=10.0) + 4.0)
    algorithm_events = list(algorithm_trace.get("stage2", [])) + list(algorithm_trace.get("stage3", []))
    cruise_mps = PRESENTATION_CRUISE_MPS
    accel_mps2 = PRESENTATION_ACCEL_MPS2
    brake_mps2 = 2.8
    out: list[dict[str, Any]] = []
    last_s = 0.0
    last_t = 0.0
    visual_speed = cruise_mps
    queue_by_signal = {sig["id"]: 0.0 for sig in signals}
    end_t = max(float(raw_samples[-1]["t"]), route_path.length_m / max(1.0, cruise_mps) + 120.0)
    steps = int(math.ceil(end_t / DISPLAY_TRAFFIC_DT))
    for idx in range(steps + 1):
        t = round(idx * DISPLAY_TRAFFIC_DT, 1)
        dt = DISPLAY_TRAFFIC_DT if idx else DISPLAY_TRAFFIC_DT
        reason = "moving"
        algorithm = algorithm_label_at(algorithm_events, t)
        if mode == "B4" and t <= release_t:
            algorithm = "Stage2 신당역 유입 차단"
        next_sig = next_signal_after(signals, last_s)
        next_state = "-"
        front_q = 0.0
        truth_q = 0.0
        queue_tail_s: float | None = None
        hold_s: float | None = None
        block_reason = ""
        downstream_active = False
        forced = 0.0
        if next_sig is not None:
            sid = next_sig["id"]
            next_state = state_at(timelines[sid], t)
            stop_s = max(0.0, float(next_sig["s"]) - 18.0)
            row = profile_at_time(profile, t)
            ahead_obs = min(front_cap, max(0.0, round(float(row.get("ahead_count", 0.0)))))
            q = queue_by_signal.get(sid, 0.0)
            try:
                sig_idx = int(str(sid).replace("S", ""))
            except ValueError:
                sig_idx = -1
            for window in overflow_windows:
                if int(window.get("signal_index", -999)) == sig_idx and float(window.get("start_sec", -1.0)) <= t <= float(window.get("end_sec", -1.0)):
                    forced = max(forced, float(window.get("queue_count", downstream_count)))
            downstream_active = next_state == "green" and sid in downstream_windows and downstream_windows[sid][0] <= t <= downstream_windows[sid][1]
            if next_state in {"red", "yellow", "allred"}:
                q = max(q, ahead_obs, 4.0)
            elif downstream_active or forced > 0:
                q = max(q, downstream_count if downstream_active else 0.0, forced, ahead_obs)
            elif q > 0:
                q = max(0.0, q - mode_policy_number(demand_policy, "green_discharge_veh_per_sec", mode, 1.35) * dt)
            truth_q = min(front_cap, q)
            max_fit = max(0.0, (stop_s - 10.0 - (last_s + ev_queue_gap)) / queue_spacing)
            q = min(front_cap, q, max_fit)
            queue_by_signal[sid] = q
            front_q = q
            if q > 0.1:
                queue_tail_s = max(0.0, stop_s - 10.0 - q * queue_spacing)
                hold_s = max(0.0, queue_tail_s - ev_queue_gap)
                if next_state in {"red", "yellow", "allred"}:
                    block_reason = "front_queue_tail"
                elif downstream_active or forced > 0:
                    block_reason = "green_downstream_queue"
                else:
                    block_reason = "queue_clearing"
            elif next_state in {"red", "yellow", "allred"}:
                hold_s = max(0.0, stop_s - 56.0)
                block_reason = "front_queue_red"
        desired_speed = cruise_mps
        if mode == "B4" and next_sig is not None:
            dist_to_signal = max(0.0, float(next_sig["s"]) - last_s)
            approach_factor = 0.78 + 0.22 * smoothstep(0.0, 115.0, dist_to_signal)
            wave_factor = 0.96 + 0.04 * math.sin(t * 0.19 + float(next_sig["s"]) * 0.017)
            desired_speed = max(7.4, min(cruise_mps, cruise_mps * approach_factor * wave_factor))
        if hold_s is not None:
            dist_to_hold = hold_s - last_s
            braking_speed = math.sqrt(max(0.0, 2.0 * brake_mps2 * max(0.0, dist_to_hold - 1.0)))
            desired_speed = min(cruise_mps, braking_speed)
            if dist_to_hold <= 1.2:
                desired_speed = 0.0
                if block_reason != "green_downstream_queue" or dist_to_hold <= 0.25:
                    reason = block_reason or reason
                else:
                    reason = "moving"
            elif dist_to_hold <= max(22.0, visual_speed * visual_speed / (2.0 * brake_mps2) + 12.0):
                reason = block_reason if block_reason != "green_downstream_queue" else "moving"
        elif next_state == "green" and front_q <= 0.1 and reason == "moving":
            visual_speed = max(visual_speed, min(cruise_mps, 3.6))
        if visual_speed < desired_speed:
            visual_speed = min(desired_speed, visual_speed + accel_mps2 * dt)
        else:
            visual_speed = max(desired_speed, visual_speed - brake_mps2 * dt)
        next_s = min(route_path.length_m, last_s + visual_speed * dt)
        if hold_s is not None and next_s >= hold_s:
            next_s = hold_s
            visual_speed = 0.0
            reason = block_reason or reason
        if next_s >= route_path.length_m - 0.25:
            next_s = route_path.length_m
            reason = "arrived"
            front_q = 0.0
            truth_q = 0.0
            queue_tail_s = None
        p = path_point(route_path, next_s)
        speed_kmh = max(0.0, visual_speed * 3.6)
        if reason in {"front_queue_tail", "front_queue_red", "green_downstream_queue", "stage2_hold", "arrived"} and abs(next_s - last_s) < 0.05:
            speed_kmh = 0.0
        out.append({
            "t": round(t, 1),
            "s": round(next_s, 2),
            "lat": p["lat"],
            "lon": p["lon"],
            "lane_id": p["lane_id"],
            "lane_s": p["lane_s"],
            "edge_id": p["edge_id"],
            "speed_kmh": round(speed_kmh, 1),
            "reason": reason,
            "algorithm": algorithm,
            "next_signal_id": next_sig["id"] if next_sig else "",
            "next_signal_state": next_state,
            "front_queue_count": round(front_q, 2),
            "truth_front_queue_count": round(truth_q, 2),
            "front_queue_tail_s": round(queue_tail_s, 2) if queue_tail_s is not None else None,
            "visual_model": "signal_queue_constraints_with_priority_rolling_speed",
            "truth_source": "signal_timeline_and_queue_profile",
        })
        last_s = next_s
        last_t = t
        if reason == "arrived":
            break
    if out:
        out[-1]["reason"] = "arrived"
        out[-1]["speed_kmh"] = 0.0
    return out


def parse_demand_slots() -> list[dict[str, Any]]:
    if not PRESENTATION_DEMAND_ROUTE.is_file():
        return []
    root = ET.parse(PRESENTATION_DEMAND_ROUTE).getroot()
    slots = []
    for idx, vehicle in enumerate(root.findall("vehicle")[:80]):
        slots.append({
            "source_demand_vehicle_id": str(vehicle.get("id", f"demand_{idx:03d}")),
            "route_id": str(vehicle.get("route", "")),
            "depart": float(vehicle.get("depart", 0.0)),
        })
    return slots


def route_vehicle(route_path: LanePath, vid: str, s_m: float, speed_kmh: float, role: str, slot: dict[str, Any], *, opacity: float = GENERAL_VEHICLE_OPACITY, reason: str = "moving", signal_state: str = "-", stop_s: float | None = None, choice: str = "right") -> dict[str, Any]:
    p = path_point(route_path, s_m)
    return {
        "id": vid,
        "source_demand_vehicle_id": slot.get("source_demand_vehicle_id", ""),
        "kind": "route",
        "role": role,
        "choice": choice,
        "s": round(max(0.0, min(route_path.length_m, s_m)), 2),
        "lat": p["lat"],
        "lon": p["lon"],
        "angle": p["angle"],
        "lane_id": p["lane_id"],
        "lane_s": p["lane_s"],
        "edge_id": p["edge_id"],
        "speed_kmh": round(max(0.0, speed_kmh), 1),
        "color": GENERAL_VEHICLE_COLOR,
        "opacity": round(opacity, 2),
        "stop_s": round(stop_s, 2) if stop_s is not None else None,
        "signal_state": signal_state,
        "reason": reason,
    }


def band_points(route_path: LanePath, a_s: float, b_s: float, step: float = 10.0) -> list[list[float]]:
    a_s = max(0.0, min(route_path.length_m, a_s))
    b_s = max(0.0, min(route_path.length_m, b_s))
    if b_s < a_s:
        a_s, b_s = b_s, a_s
    out: list[list[float]] = []
    s = a_s
    while s <= b_s:
        p = path_point(route_path, s)
        out.append([p["lat"], p["lon"]])
        s += step
    p = path_point(route_path, b_s)
    if not out or out[-1] != [p["lat"], p["lon"]]:
        out.append([p["lat"], p["lon"]])
    return out


def stage2_state(t: float, algorithm_trace: dict[str, Any]) -> dict[str, Any] | None:
    events = algorithm_trace.get("stage2", [])
    hold = next((event for event in events if event.get("action") == "RED_HOLD"), None)
    request = next((event for event in events if event.get("action") == "RELEASE_REQUEST"), None)
    release = next((event for event in events if event.get("action") == "RELEASE"), None)
    hold_t = max(0.0, float((hold or {}).get("t_rel", 0.0)))
    request_t = max(hold_t + 8.0, float((request or release or {}).get("t_rel", hold_t + 10.0)))
    release_t = max(request_t + 2.0, float((release or {}).get("t_rel", request_t + 3.0)))
    if t < hold_t or t > release_t + 4.0:
        return None
    if t < request_t:
        return {"status": "hold_active", "label": "Stage2 신당역 유입 차단", "release_t": release_t}
    if t < release_t:
        return {"status": "release_clearance_pending", "label": "Stage2 해제 전 clearance", "release_t": release_t}
    return {"status": "released", "label": "Stage2 유입 재개", "release_t": release_t}


def extract_lane_from_stop(points: list[dict[str, float]], center: dict[str, float], max_len: float = 135.0) -> list[dict[str, float]]:
    if len(points) < 2:
        return []
    d_first = meters_between(points[0]["lat"], points[0]["lon"], center["lat"], center["lon"])
    d_last = meters_between(points[-1]["lat"], points[-1]["lon"], center["lat"], center["lon"])
    ordered = points if d_first <= d_last else list(reversed([
        {"lat": p["lat"], "lon": p["lon"], "s": round(points[-1]["s"] - p["s"], 2)}
        for p in points
    ]))
    out: list[dict[str, float]] = []
    total = 0.0
    prev: dict[str, float] | None = None
    for point in ordered:
        if prev is not None:
            total += meters_between(prev["lat"], prev["lon"], point["lat"], point["lon"])
        if total > max_len:
            break
        out.append({"lat": point["lat"], "lon": point["lon"], "s": round(total, 2)})
        prev = point
    return out


def stage2_lane_points_away(net: Any, lane: Any, center: dict[str, float]) -> tuple[list[dict[str, float]], bool]:
    points = lane_latlon_shape(net, lane)
    if not points:
        return [], True
    d_first = meters_between(points[0]["lat"], points[0]["lon"], center["lat"], center["lon"])
    d_last = meters_between(points[-1]["lat"], points[-1]["lon"], center["lat"], center["lon"])
    forward = d_first <= d_last
    ordered = points if forward else list(reversed([
        {"lat": p["lat"], "lon": p["lon"], "s": round(points[-1]["s"] - p["s"], 2)}
        for p in points
    ]))
    out: list[dict[str, float]] = []
    total = 0.0
    prev: dict[str, float] | None = None
    for point in ordered:
        if prev is not None:
            total += meters_between(prev["lat"], prev["lon"], point["lat"], point["lon"])
        out.append({"lat": point["lat"], "lon": point["lon"], "s": round(total, 2)})
        prev = point
    return out, forward


def segment_heading(points: list[dict[str, float]]) -> float:
    if len(points) < 2:
        return 0.0
    a, b = points[0], points[-1]
    mid_lat = (a["lat"] + b["lat"]) / 2.0
    dx = (b["lon"] - a["lon"]) * 111_320.0 * math.cos(math.radians(mid_lat))
    dy = (b["lat"] - a["lat"]) * 111_320.0
    return math.degrees(math.atan2(dy, dx))


def angle_delta(a: float, b: float) -> float:
    return abs(((b - a + 180.0) % 360.0) - 180.0)


def edge_connection_items(edge: Any) -> list[tuple[Any, list[Any]]]:
    outgoing = edge.getOutgoing()
    if hasattr(outgoing, "items"):
        return [(to_edge, list(conns)) for to_edge, conns in outgoing.items()]
    return []


def incoming_edges(edge: Any) -> list[Any]:
    incoming = edge.getIncoming()
    if hasattr(incoming, "keys"):
        return list(incoming.keys())
    return list(incoming)


def next_stage2_lane(net: Any, lane: Any, forward: bool, current_heading: float, visited: set[str]) -> tuple[Any, bool] | None:
    candidates: list[tuple[float, float, float, Any, bool]] = []
    if forward:
        for _to_edge, conns in edge_connection_items(lane.getEdge()):
            for conn in conns:
                if conn.getFromLane().getID() != lane.getID():
                    continue
                next_lane = conn.getToLane()
                if next_lane.getID() in visited:
                    continue
                points = lane_latlon_shape(net, next_lane)
                if len(points) < 2:
                    continue
                heading = segment_heading(points)
                delta = angle_delta(current_heading, heading)
                if delta <= 55.0:
                    candidates.append((delta, -float(next_lane.getLength()), lane_index(next_lane.getID()), next_lane, True))
    else:
        for in_edge in incoming_edges(lane.getEdge()):
            for conn in route_connections(in_edge, lane.getEdge()):
                if conn.getToLane().getID() != lane.getID():
                    continue
                next_lane = conn.getFromLane()
                if next_lane.getID() in visited:
                    continue
                raw = lane_latlon_shape(net, next_lane)
                if len(raw) < 2:
                    continue
                points = list(reversed([
                    {"lat": p["lat"], "lon": p["lon"], "s": round(raw[-1]["s"] - p["s"], 2)}
                    for p in raw
                ]))
                heading = segment_heading(points)
                delta = angle_delta(current_heading, heading)
                if delta <= 55.0:
                    candidates.append((delta, -float(next_lane.getLength()), lane_index(next_lane.getID()), next_lane, False))
    if not candidates:
        return None
    _delta, _length, _idx, next_lane, next_forward = sorted(candidates, key=lambda item: item[:3])[0]
    return next_lane, next_forward


def build_stage2_extended_line(net: Any, lane: Any, center: dict[str, float], max_len: float, lanes_out: dict[str, dict[str, Any]]) -> tuple[list[dict[str, float]], list[dict[str, Any]]]:
    line: list[dict[str, float]] = []
    lane_segments: list[dict[str, Any]] = []
    current_lane = lane
    current_points, forward = stage2_lane_points_away(net, current_lane, center)
    total = 0.0
    visited: set[str] = set()
    while current_points and total < max_len and current_lane.getID() not in visited:
        visited.add(current_lane.getID())
        clipped: list[dict[str, float]] = []
        prev = line[-1] if line else None
        local_total = 0.0
        local_prev: dict[str, float] | None = None
        for point in current_points:
            if local_prev is not None:
                local_total += meters_between(local_prev["lat"], local_prev["lon"], point["lat"], point["lon"])
            if total + local_total > max_len:
                break
            if prev is not None and not clipped and meters_between(prev["lat"], prev["lon"], point["lat"], point["lon"]) < 0.5:
                local_prev = point
                continue
            clipped.append({"lat": point["lat"], "lon": point["lon"], "s": round(total + local_total, 2)})
            local_prev = point
        if len(clipped) >= 2 and float(clipped[-1]["s"]) - float(clipped[0]["s"]) >= 1.0:
            lane_points = lane_latlon_shape(net, current_lane)
            lanes_out.setdefault(current_lane.getID(), make_lane_entry(current_lane.getEdge().getID(), current_lane.getID(), lane_points, "stage2_approach"))
            start_s = total
            line.extend(clipped)
            total = float(line[-1]["s"])
            lane_segments.append({
                "lane_id": current_lane.getID(),
                "edge_id": current_lane.getEdge().getID(),
                "start_s": round(start_s, 2),
                "end_s": round(total, 2),
                "points": clipped,
            })
        heading = segment_heading(current_points)
        next_lane = next_stage2_lane(net, current_lane, forward, heading, visited)
        if next_lane is None:
            break
        current_lane, forward = next_lane
        raw = lane_latlon_shape(net, current_lane)
        current_points = raw if forward else list(reversed([
            {"lat": p["lat"], "lon": p["lon"], "s": round(raw[-1]["s"] - p["s"], 2)}
            for p in raw
        ]))
        rebased: list[dict[str, float]] = []
        local_total = 0.0
        prev_point: dict[str, float] | None = None
        for point in current_points:
            if prev_point is not None:
                local_total += meters_between(prev_point["lat"], prev_point["lon"], point["lat"], point["lon"])
            rebased.append({"lat": point["lat"], "lon": point["lon"], "s": round(local_total, 2)})
            prev_point = point
        current_points = rebased
    return line, lane_segments


def rebase_points(points: list[dict[str, float]]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    total = 0.0
    prev: dict[str, float] | None = None
    for point in points:
        if prev is not None:
            total += meters_between(prev["lat"], prev["lon"], point["lat"], point["lon"])
        out.append({"lat": point["lat"], "lon": point["lon"], "s": round(total, 2)})
        prev = point
    return out


def lane_points_from_anchor(net: Any, lane_id: str, anchor: dict[str, float]) -> tuple[Any, list[dict[str, float]]]:
    lane = net.getLane(lane_id)
    raw = lane_latlon_shape(net, lane)
    if len(raw) < 2:
        return lane, []
    d_first = meters_between(raw[0]["lat"], raw[0]["lon"], anchor["lat"], anchor["lon"])
    d_last = meters_between(raw[-1]["lat"], raw[-1]["lon"], anchor["lat"], anchor["lon"])
    ordered = raw if d_first <= d_last else list(reversed(raw))
    return lane, rebase_points(ordered)


def build_stage2_fixed_line(net: Any, lane_ids: list[str], center: dict[str, float], max_len: float, lanes_out: dict[str, dict[str, Any]]) -> tuple[list[dict[str, float]], list[dict[str, Any]]]:
    line: list[dict[str, float]] = []
    lane_segments: list[dict[str, Any]] = []
    total = 0.0
    anchor = center
    for lane_id in lane_ids:
        try:
            lane, local_points = lane_points_from_anchor(net, lane_id, anchor)
        except Exception:
            continue
        if len(local_points) < 2:
            continue
        segment_points: list[dict[str, float]] = []
        local_prev: dict[str, float] | None = None
        local_total = 0.0
        start_s = total
        for point in local_points:
            if local_prev is not None:
                local_total += meters_between(local_prev["lat"], local_prev["lon"], point["lat"], point["lon"])
            if total + local_total > max_len:
                break
            if line and not segment_points and meters_between(line[-1]["lat"], line[-1]["lon"], point["lat"], point["lon"]) < 0.5:
                local_prev = point
                continue
            segment_points.append({"lat": point["lat"], "lon": point["lon"], "s": round(total + local_total, 2)})
            local_prev = point
        if len(segment_points) < 2:
            continue
        raw_points = lane_latlon_shape(net, lane)
        lanes_out.setdefault(lane_id, make_lane_entry(lane.getEdge().getID(), lane_id, raw_points, "stage2_fixed_approach"))
        line.extend(segment_points)
        total = float(line[-1]["s"])
        lane_segments.append({
            "lane_id": lane_id,
            "edge_id": lane.getEdge().getID(),
            "start_s": round(start_s, 2),
            "end_s": round(total, 2),
            "points": segment_points,
        })
        anchor = line[-1]
    return line, lane_segments


def build_stage2_approaches(net: Any, lanes_out: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    labels = {
        "east": "퇴계로 동측 유입 차단",
        "north": "다산로 북측 유입 차단",
        "south": "다산로 남측 유입 차단",
    }
    fixed_out = []
    for kind in ("east", "north", "south"):
        fixed_line, fixed_segments = build_stage2_fixed_line(net, STAGE2_APPROACH_LANE_CHAINS[kind], STAGE2_CENTER, 135.0, lanes_out)
        if len(fixed_line) >= 2:
            first_lane = fixed_segments[0] if fixed_segments else {"lane_id": "", "edge_id": ""}
            fixed_out.append({
                "label": labels[kind],
                "source": "sumo_lane_fixed",
                "approach": kind,
                "lane_id": first_lane["lane_id"],
                "edge_id": first_lane["edge_id"],
                "lane_chain": STAGE2_APPROACH_LANE_CHAINS[kind],
                "line": [[p["lat"], p["lon"]] for p in fixed_line],
                "line_points": fixed_line,
                "lane_segments": fixed_segments,
                "stopline": {"lat": fixed_line[0]["lat"], "lon": fixed_line[0]["lon"]},
                "length_m": round(fixed_line[-1]["s"], 2),
            })
    if len(fixed_out) == 3:
        return fixed_out

    x, y = net.convertLonLat2XY(STAGE2_CENTER["lon"], STAGE2_CENTER["lat"])
    try:
        north_node = net.getNode(STAGE2_NORTH_DASAN_JUNCTION_ID)
        north_lon, north_lat = net.convertXY2LonLat(*north_node.getCoord())
        north_anchor = {"lat": float(north_lat), "lon": float(north_lon)}
    except Exception:
        north_anchor = STAGE2_CENTER
    candidates: list[dict[str, Any]] = []
    for edge, _dist in net.getNeighboringEdges(x, y, 180.0, includeJunctions=False):
        if edge.getFunction():
            continue
        for lane in edge.getLanes():
            points = lane_latlon_shape(net, lane)
            line_points = extract_lane_from_stop(points, STAGE2_CENTER, 140.0)
            if len(line_points) < 2:
                continue
            stop = line_points[0]
            far = line_points[-1]
            stop_gap = meters_between(stop["lat"], stop["lon"], STAGE2_CENTER["lat"], STAGE2_CENTER["lon"])
            if stop_gap > 85.0:
                continue
            lane_len = float(line_points[-1]["s"])
            candidates.append({
                "edge_id": edge.getID(),
                "lane_id": lane.getID(),
                "stop_gap_m": stop_gap,
                "north_anchor_gap_m": meters_between(stop["lat"], stop["lon"], north_anchor["lat"], north_anchor["lon"]),
                "line_points": line_points,
                "far": far,
                "southward_m": max(0.0, (float(stop["lat"]) - float(far["lat"])) * 111_320.0),
                "line_length_m": lane_len,
                "lane": lane,
            })
    def pick(kind: str) -> dict[str, Any] | None:
        filtered = []
        for item in candidates:
            far = item["far"]
            if kind == "east" and far["lon"] > STAGE2_CENTER["lon"] + 0.00015 and abs(far["lat"] - STAGE2_CENTER["lat"]) < 0.00045:
                filtered.append(item)
            if kind == "north" and far["lat"] > STAGE2_CENTER["lat"] + 0.00035:
                filtered.append(item)
            if kind == "south" and item["southward_m"] >= 30.0:
                filtered.append(item)
        if not filtered:
            return None
        if kind == "east":
            return sorted(filtered, key=lambda item: (item["stop_gap_m"], item["lane_id"]))[0]
        if kind == "south":
            return sorted(filtered, key=lambda item: (0 if item["line_length_m"] >= 35.0 else 1, item["stop_gap_m"], item["lane_id"]))[0]
        dasan_filtered = [item for item in filtered if float(item.get("north_anchor_gap_m", 999.0)) <= 55.0]
        north_pool = dasan_filtered or filtered
        return sorted(north_pool, key=lambda item: (item["north_anchor_gap_m"], item["stop_gap_m"], item["lane_id"]))[0]
    out = []
    for kind in ("east", "north", "south"):
        item = pick(kind)
        if item is None:
            out.append({"label": labels[kind], "source": "fallback_manual", "line": []})
            continue
        extended, lane_segments = build_stage2_extended_line(net, item["lane"], STAGE2_CENTER, 135.0, lanes_out)
        if len(extended) < 2:
            extended = item["line_points"]
            lane_segments = [{"lane_id": item["lane_id"], "edge_id": item["edge_id"], "start_s": 0.0, "end_s": round(item["line_points"][-1]["s"], 2)}]
            lanes_out.setdefault(item["lane_id"], make_lane_entry(item["edge_id"], item["lane_id"], item["line_points"], "stage2_approach"))
        out.append({
            "label": labels[kind],
            "source": "sumo_lane_auto",
            "approach": kind,
            "lane_id": item["lane_id"],
            "edge_id": item["edge_id"],
            "line": [[p["lat"], p["lon"]] for p in extended],
            "line_points": extended,
            "lane_segments": lane_segments,
            "stopline": {"lat": extended[0]["lat"], "lon": extended[0]["lon"]},
            "length_m": round(extended[-1]["s"], 2),
        })
    return out


def line_point(line: list[list[float]], s_m: float) -> dict[str, float]:
    if not line:
        return {"lat": 0.0, "lon": 0.0, "angle": 0.0}
    total = 0.0
    for a, b in zip(line, line[1:]):
        seg_len = meters_between(a[0], a[1], b[0], b[1])
        if total + seg_len >= s_m:
            f = (s_m - total) / seg_len if seg_len else 0.0
            lat = a[0] + (b[0] - a[0]) * f
            lon = a[1] + (b[1] - a[1]) * f
            mid_lat = (a[0] + b[0]) / 2.0
            dx = (b[1] - a[1]) * 111_320.0 * math.cos(math.radians(mid_lat))
            dy = (b[0] - a[0]) * 111_320.0
            return {"lat": round(lat, 7), "lon": round(lon, 7), "angle": round(math.degrees(math.atan2(-dy, dx)), 1)}
        total += seg_len
    z = line[-1]
    return {"lat": z[0], "lon": z[1], "angle": 0.0}


def line_length(line: list[list[float]]) -> float:
    return sum(meters_between(a[0], a[1], b[0], b[1]) for a, b in zip(line, line[1:]))


def stage2_lane_at(approach: dict[str, Any], s_m: float) -> dict[str, str]:
    segments = approach.get("lane_segments") or []
    chosen = segments[-1] if segments else {}
    for segment in segments:
        if float(segment.get("start_s", 0.0)) - 1e-6 <= s_m <= float(segment.get("end_s", 0.0)) + 1e-6:
            chosen = segment
            break
    return {
        "lane_id": str(chosen.get("lane_id", approach.get("lane_id", ""))),
        "edge_id": str(chosen.get("edge_id", approach.get("edge_id", ""))),
    }


def stage2_point_at(approach: dict[str, Any], s_m: float) -> dict[str, Any]:
    segments = approach.get("lane_segments") or []
    segment = segments[-1] if segments else {}
    for item in segments:
        if float(item.get("start_s", 0.0)) - 1e-6 <= s_m <= float(item.get("end_s", 0.0)) + 1e-6:
            segment = item
            break
    points = segment.get("points") or approach.get("line_points") or []
    if len(points) < 2:
        p = line_point(approach.get("line", []), s_m)
        return {**p, "lane_id": approach.get("lane_id", ""), "edge_id": approach.get("edge_id", ""), "lane_s": round(s_m, 2)}
    start_s = float(segment.get("start_s", points[0].get("s", 0.0)))
    end_s = float(segment.get("end_s", points[-1].get("s", s_m)))
    s_m = max(start_s, min(end_s, s_m))
    p = point_on_polyline(points, s_m)
    p_prev = point_on_polyline(points, max(start_s, s_m - 3.0))
    p_next = point_on_polyline(points, min(end_s, s_m + 5.0))
    mid_lat = (p_prev["lat"] + p_next["lat"]) / 2.0
    dx = (p_next["lon"] - p_prev["lon"]) * 111_320.0 * math.cos(math.radians(mid_lat))
    dy = (p_next["lat"] - p_prev["lat"]) * 111_320.0
    return {
        "lat": p["lat"],
        "lon": p["lon"],
        "angle": round(math.degrees(math.atan2(-dy, dx)), 1),
        "lane_id": str(segment.get("lane_id", approach.get("lane_id", ""))),
        "edge_id": str(segment.get("edge_id", approach.get("edge_id", ""))),
        "lane_s": round(s_m - start_s, 2),
    }


def build_stage2_objects(t: float, approaches: list[dict[str, Any]], algorithm_trace: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    state = stage2_state(t, algorithm_trace)
    if state is None:
        return [], [], []
    vehicles: list[dict[str, Any]] = []
    fake_signals: list[dict[str, Any]] = []
    stage_lines: list[dict[str, Any]] = []
    stop_points: list[dict[str, float]] = []
    for ridx, approach in enumerate(approaches):
        line = approach.get("line", [])
        if len(line) < 2:
            continue
        length = float(approach.get("length_m") or line_length(line))
        stage_lines.append({"line": line, "label": state["label"], "lane_id": approach.get("lane_id", ""), "source": approach.get("source", "")})
        stop = line_point(line, 0.0)
        stop_points.append(stop)
        count = max(1, min(4, int((length - 8.0) // 12.0)))
        spacing = max(10.0, (length - 22.0) / max(1, count))
        for i in range(count):
            base = min(length - 6.0, 18.0 + i * spacing)
            if state["status"] == "released":
                dist = base + max(0.0, t - float(state["release_t"])) * 5.2
                speed = 18.7
                if dist >= length - 6.0:
                    continue
            else:
                dist = base
                speed = 0.0
            p = stage2_point_at(approach, dist)
            vehicles.append({
                "id": f"stage2_{ridx:02d}_{i:02d}",
                "kind": "stage2",
                "role": "stage2_blocked",
                "lat": p["lat"],
                "lon": p["lon"],
                "angle": p["angle"],
                "lane_id": p["lane_id"],
                "edge_id": p["edge_id"],
                "lane_s": p["lane_s"],
                "speed_kmh": round(speed, 1),
                "color": GENERAL_VEHICLE_COLOR,
                "opacity": GENERAL_VEHICLE_OPACITY,
                "signal_state": "red" if state["status"] != "released" else "green",
                "reason": state["label"],
                "stage2_status": state["status"],
            })
    if stop_points:
        label = "ALL RED" if state["status"] != "released" else "RELEASED"
        fake_signals.append({
            "id": "stage2_sindang_single_signal",
            "lat": STAGE2_SIGNAL_CENTER["lat"],
            "lon": STAGE2_SIGNAL_CENTER["lon"],
            "lane_id": "stage2_sindang_all_red",
            "signal_state": "allred" if state["status"] != "released" else "green",
            "label": label,
            "display_role": "stage2_single_signal",
        })
    return vehicles, fake_signals, stage_lines


def build_frames(
    mode: str,
    display_samples: list[dict[str, Any]],
    traffic_samples: list[dict[str, Any]],
    profile: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    timelines: dict[str, list[list[Any]]],
    route_path: LanePath,
    stage2_approaches: list[dict[str, Any]],
    demand_slots: list[dict[str, Any]],
    demand_policy: dict[str, Any],
    algorithm_trace: dict[str, Any],
) -> list[dict[str, Any]]:
    front_cap = int(mode_policy_number(demand_policy, "front_queue_cap", mode, 9.0))
    behind_cap = int(mode_policy_number(demand_policy, "behind_queue_cap", mode, 6.0))
    queue_spacing = float(demand_policy.get("queue_spacing_m", 13.0))
    slots = demand_slots or [{"source_demand_vehicle_id": f"scene_{i:03d}", "route_id": "scene"} for i in range(80)]
    frames: list[dict[str, Any]] = []
    def safe_vehicle_s(s_m: float, frame_t: float, slot: int) -> tuple[float, bool]:
        for sig in signals:
            if state_at(timelines[sig["id"]], frame_t) not in {"red", "yellow", "allred"}:
                continue
            stop_s = float(sig["s"]) - 18.0
            if stop_s < s_m < float(sig["s"]) + 42.0:
                return max(0.0, stop_s - 10.0 - slot * 12.0), True
        return s_m, False

    def window_alpha(frame_t: float, start: float, end: float, fade: float = 3.0) -> float:
        return smoothstep(start, start + fade, frame_t) * (1.0 - smoothstep(end - fade, end, frame_t))

    def next_non_green_time(sig_id: str, frame_t: float) -> float | None:
        for ts, state in timelines.get(sig_id, []):
            if float(ts) >= frame_t and str(state) in {"yellow", "red", "allred"}:
                return float(ts)
        return None

    def queue_slot_alpha(visible_q: float, slot: int, base_alpha: float = 1.0) -> float:
        return base_alpha * smoothstep(slot - 0.8, slot + 1.8, visible_q)

    def distance_alpha(delta_s: float, near: float, full: float, far: float | None = None, fade_out: float = 40.0) -> float:
        alpha = smoothstep(near, full, delta_s)
        if far is not None:
            alpha *= 1.0 - smoothstep(far - fade_out, far, delta_s)
        return clamp(alpha, 0.0, 1.0)

    def move_toward(value: float, target: float, max_delta: float) -> float:
        if value < target:
            return min(target, value + max_delta)
        return max(target, value - max_delta)

    front_queue_alpha = 0.0
    front_queue_memory: dict[str, Any] | None = None
    front_queue_release_t: float | None = None
    last_route_vehicle_s: dict[str, float] = {}
    previous_t: float | None = None

    def enforce_route_vehicle_continuity(items: list[dict[str, Any]]) -> None:
        for vehicle in items:
            if vehicle.get("kind") != "route" or vehicle.get("s") is None:
                continue
            vid = str(vehicle.get("id", ""))
            s = float(vehicle.get("s", 0.0))
            last_s = last_route_vehicle_s.get(vid)
            if last_s is not None and s < last_s:
                p = path_point(route_path, last_s)
                vehicle.update({
                    "s": round(last_s, 2),
                    "lat": p["lat"],
                    "lon": p["lon"],
                    "angle": p["angle"],
                    "lane_id": p["lane_id"],
                    "lane_s": p["lane_s"],
                    "edge_id": p["edge_id"],
                })
                s = last_s
            last_route_vehicle_s[vid] = s

    for st in traffic_samples:
        t = float(st["t"])
        dt = DISPLAY_TRAFFIC_DT if previous_t is None else clamp(t - previous_t, 0.0, 0.5)
        previous_t = t
        ev_s = float(st["s"])
        row = profile_at_time(profile, t)
        ahead_count = min(front_cap, max(4, int(round(float(row.get("ahead_count", 4.0))))))
        behind_count = min(behind_cap, max(3, int(round(float(row.get("behind_count", 3.0))))))
        ahead_speed = max(0.0, float(row.get("ahead_speed_kmh", st.get("speed_kmh", 0.0))))
        behind_speed = max(0.0, float(row.get("behind_speed_kmh", st.get("speed_kmh", 0.0))))
        vehicles: list[dict[str, Any]] = []
        bands: list[dict[str, Any]] = []
        next_sig = next_signal_after(signals, ev_s)
        next_state = state_at(timelines[next_sig["id"]], t) if next_sig else "-"
        front_q = float(st.get("front_queue_count") or 0.0)
        stop_s = max(0.0, float(next_sig["s"]) - 18.0) if next_sig else route_path.length_m
        signal_queued_ahead = front_q > 0.1 or (next_sig is not None and next_state in {"red", "yellow", "allred"} and stop_s - ev_s < 260.0)
        signal_queued_behind = ev_s > 82.0 and (behind_speed < 10.0 or str(st.get("reason")) in {"front_queue_tail", "front_queue_red", "green_downstream_queue", "stage2_hold"})
        summary_label = "10-1 지속 흐름"
        forced_case_b = mode == "B4" and 62.0 <= t <= 92.0 and ev_s + 170.0 < route_path.length_m
        if forced_case_b:
            signal_queued_ahead = True

        if forced_case_b:
            case_alpha = window_alpha(t, 62.0, 96.0, 3.5)
            q_tail = ev_s + 34.0
            q_end = min(route_path.length_m, ev_s + 154.0)
            bands.append({"a": round(q_tail, 2), "b": round(q_end, 2), "kind": "front_blocked", "label": "막힌 구간", "opacity": round(0.32 * case_alpha, 3), "points": band_points(route_path, q_tail, q_end)})
            down_a = min(route_path.length_m, q_end + 10.0)
            down_b = min(route_path.length_m, q_end + 138.0)
            bands.append({"a": round(down_a, 2), "b": round(down_b, 2), "kind": "front_moving", "label": "하류 배출 중", "opacity": round(0.42 * case_alpha, 3), "points": band_points(route_path, down_a, down_b)})
            for i in range(7):
                veh_s, _clamped = safe_vehicle_s(min(q_end - 5.0, q_tail + 12.0 + i * 15.0), t, i)
                alpha = case_alpha * queue_slot_alpha(7.0, i, GENERAL_VEHICLE_OPACITY)
                vehicles.append(route_vehicle(route_path, f"{mode}_casebq_{i:02d}", veh_s, 0.0, "ahead_queue", slots[i % len(slots)], opacity=alpha, reason="막힌 구간", signal_state="green"))
            span = max(24.0, down_b - down_a)
            for j in range(6):
                cycle_len = max(24.0, span - 18.0)
                phase = t * 6.4 + j * 22.0
                cycle = int(phase // cycle_len)
                rel = phase % cycle_len
                ds = down_a + rel
                ds, clamped = safe_vehicle_s(min(route_path.length_m, ds), t, 20 + j)
                if clamped:
                    continue
                edge_alpha = distance_alpha(rel, 0.0, 28.0, span - 4.0, 34.0)
                anchor = int(down_a // 20.0)
                vehicles.append(route_vehicle(route_path, f"{mode}_caseb_downstream_{anchor:03d}_{j:02d}_{cycle:03d}", ds, 23.0, "downstream_moving", slots[(20 + j) % len(slots)], opacity=0.96 * case_alpha * edge_alpha, reason="하류 배출 중", signal_state="green"))
            summary_label = "green · 앞 큐 과잉"
        regular_queue: dict[str, Any] | None = None
        if not forced_case_b:
            if signal_queued_ahead and next_sig is not None:
                q_end = max(0.0, stop_s - 10.0)
                visible_q = max(front_q, 5.0 if next_state in {"red", "yellow", "allred"} else 0.0)
                if mode == "B04":
                    visible_q = float(front_cap)
                q_tail = max(ev_s + 26.0, q_end - visible_q * queue_spacing)
                kind = "front_blocked" if str(st.get("reason")) == "green_downstream_queue" else "front"
                label = "막힌 구간" if kind == "front_blocked" else ("앞 큐 뒤 대기" if next_state in {"red", "yellow", "allred"} else "신호 변경 후 앞차 출발")
                target_alpha = smoothstep(0.1, 2.8, visible_q)
                front_queue_alpha = 1.0 if mode == "B04" else move_toward(front_queue_alpha, target_alpha, dt * 0.075)
                regular_queue = {
                    "q_end": q_end,
                    "visible_q": visible_q,
                    "q_tail": q_tail,
                    "kind": kind,
                    "label": label,
                    "next_state": next_state,
                    "stop_s": stop_s,
                    "ahead_speed": ahead_speed,
                }
                front_queue_memory = dict(regular_queue)
                front_queue_release_t = None
            else:
                if mode == "B04":
                    if front_queue_memory and front_queue_release_t is None:
                        front_queue_release_t = t
                    front_queue_alpha = 0.0
                else:
                    front_queue_alpha = move_toward(front_queue_alpha, 0.0, dt * 0.11)
                    if front_queue_alpha > 0.015 and front_queue_memory:
                        remembered_end = float(front_queue_memory["q_end"])
                        if remembered_end >= ev_s + 24.0:
                            visible_q = float(front_queue_memory["visible_q"])
                            q_tail = max(ev_s + 26.0, remembered_end - visible_q * queue_spacing)
                            regular_queue = {
                                **front_queue_memory,
                                "q_tail": q_tail,
                                "label": "신호 변경 후 앞차 출발",
                                "next_state": next_state,
                                "ahead_speed": ahead_speed,
                            }
                        else:
                            front_queue_alpha = 0.0
                            front_queue_memory = None
                    elif front_queue_alpha <= 0.015:
                        front_queue_memory = None

        b04_front_stream_rendered = False
        if mode == "B04" and regular_queue is None and next_sig is not None and not forced_case_b:
            if front_queue_memory and front_queue_release_t is not None:
                release_age = t - front_queue_release_t
                remembered_end = float(front_queue_memory["q_end"])
                visible_q = float(front_queue_memory["visible_q"])
                if 0.0 <= release_age <= 16.0 and remembered_end + release_age * 8.4 >= ev_s + 24.0:
                    for i in range(max(3, min(12, int(math.ceil(visible_q))))):
                        veh_s = max(ev_s + 22.0, remembered_end - i * queue_spacing + release_age * 8.4)
                        if veh_s > route_path.length_m - 4.0:
                            continue
                        veh_s, clamped = safe_vehicle_s(veh_s, t, i)
                        if clamped:
                            continue
                        vehicles.append(route_vehicle(route_path, f"{mode}_aheadq_{i:02d}", veh_s, max(24.0, ahead_speed), "ahead_stream", slots[i % len(slots)], opacity=GENERAL_VEHICLE_OPACITY, reason="앞차 출발", signal_state=next_state))
                    b04_front_stream_rendered = True
                else:
                    front_queue_memory = None
                    front_queue_release_t = None
            if not b04_front_stream_rendered and next_state == "green":
                transition_t = next_non_green_time(next_sig["id"], t)
                if transition_t is not None and 0.0 <= transition_t - t <= 18.0 and stop_s - ev_s < 430.0:
                    q_end = max(0.0, stop_s - 10.0)
                    visible_q = float(front_cap)
                    approach_offset = min(120.0, max(0.0, transition_t - t) * 8.4)
                    for i in range(front_cap):
                        veh_s = q_end - i * queue_spacing - approach_offset
                        if veh_s < ev_s + 20.0:
                            continue
                        veh_s, clamped = safe_vehicle_s(veh_s, t, i)
                        if clamped:
                            continue
                        vehicles.append(route_vehicle(route_path, f"{mode}_aheadq_{i:02d}", veh_s, max(24.0, ahead_speed), "ahead_stream", slots[i % len(slots)], opacity=GENERAL_VEHICLE_OPACITY, reason="앞 흐름 표시", signal_state=next_state))
                    b04_front_stream_rendered = True

        if regular_queue is not None and next_sig is not None:
            q_end = float(regular_queue["q_end"])
            visible_q = float(regular_queue["visible_q"])
            q_tail = float(regular_queue["q_tail"])
            kind = str(regular_queue["kind"])
            label = str(regular_queue["label"])
            render_state = str(regular_queue["next_state"])
            render_stop_s = float(regular_queue["stop_s"])
            render_ahead_speed = float(regular_queue["ahead_speed"])
            queue_alpha = front_queue_alpha
            bands.append({"a": round(q_tail, 2), "b": round(q_end, 2), "kind": kind, "label": label, "opacity": round((0.32 if kind == "front_blocked" else 0.27) * queue_alpha, 3), "points": band_points(route_path, q_tail, q_end)})
            n = max(3, min(12, int(math.ceil(visible_q))))
            for i in range(n):
                veh_s = max(ev_s + 22.0, q_end - i * queue_spacing)
                veh_s, clamped = safe_vehicle_s(veh_s, t, i)
                alpha = GENERAL_VEHICLE_OPACITY if mode == "B04" else queue_alpha * queue_slot_alpha(visible_q, i, GENERAL_VEHICLE_OPACITY)
                vehicles.append(route_vehicle(route_path, f"{mode}_aheadq_{i:02d}", veh_s, 0.0 if clamped or render_state in {"red", "yellow", "allred"} or kind == "front_blocked" else min(18.0, render_ahead_speed), "ahead_queue", slots[i % len(slots)], opacity=alpha, reason=label, signal_state=render_state, stop_s=render_stop_s))
            if kind == "front_blocked":
                down_a = min(route_path.length_m, q_end + 10.0)
                down_b = min(route_path.length_m, q_end + 138.0)
                bands.append({"a": round(down_a, 2), "b": round(down_b, 2), "kind": "front_moving", "label": "하류 배출 중", "opacity": round(0.42 * queue_alpha, 3), "points": band_points(route_path, down_a, down_b)})
                span = max(24.0, down_b - down_a)
                for j in range(6):
                    cycle_len = max(24.0, span - 18.0)
                    phase = t * 6.4 + j * 22.0
                    cycle = int(phase // cycle_len)
                    rel = phase % cycle_len
                    ds = down_a + rel
                    ds, clamped = safe_vehicle_s(min(route_path.length_m, ds), t, 20 + j)
                    if clamped:
                        continue
                    edge_alpha = distance_alpha(rel, 0.0, 28.0, span - 4.0, 34.0)
                    anchor = int(down_a // 20.0)
                    vehicles.append(route_vehicle(route_path, f"{mode}_frontdown_{anchor:03d}_{j:02d}_{cycle:03d}", ds, 23.0, "downstream_moving", slots[(20 + j) % len(slots)], opacity=0.96 * queue_alpha * edge_alpha, reason="하류 배출 중", signal_state="green"))
                for j in range(5):
                    blocked_s = q_tail + (q_end - q_tail) * (0.18 + j * 0.15)
                    blocked_s, _ = safe_vehicle_s(min(q_end - 3.0, blocked_s), t, 30 + j)
                    vehicles.append(route_vehicle(route_path, f"{mode}_blocked_visible_{j:02d}", blocked_s, 0.0, "blocked_visible", slots[(30 + j) % len(slots)], opacity=queue_alpha, reason="막힌 구간 정지차", signal_state="green"))
            summary_label = "green · 앞 큐 과잉" if str(st.get("reason")) == "green_downstream_queue" else ("앞 차량 신호 대기열" if render_state in {"red", "yellow", "allred"} else "신호 변경 후 앞차 출발")
        elif not b04_front_stream_rendered:
            spacing = 38.0
            scene_speed = 8.2 if mode == "B4" else 7.6
            for i in range(ahead_count):
                veh_s = min(route_path.length_m - 4.0, 68.0 + i * spacing + t * scene_speed)
                if veh_s < ev_s + 20.0:
                    continue
                choice = "straight" if veh_s > route_path.length_m - 410.0 and i % 2 == 0 else "right"
                veh_s, clamped = safe_vehicle_s(veh_s, t, i)
                if clamped:
                    continue
                alpha = GENERAL_VEHICLE_OPACITY * distance_alpha(veh_s - ev_s, 18.0, 80.0, 330.0, 70.0)
                vehicles.append(route_vehicle(route_path, f"{mode}_aheads_{i:02d}", veh_s, max(24.0, ahead_speed), "ahead_stream", slots[i % len(slots)], opacity=alpha, reason="앞 흐름 표시", signal_state=next_state, choice=choice))

        if ev_s > 72.0:
            behind_spacing = 24.0
            for i in range(behind_count):
                veh_s = ev_s - 38.0 - i * behind_spacing
                if veh_s <= 18.0 or veh_s > ev_s - 22.0:
                    continue
                veh_s, clamped = safe_vehicle_s(veh_s, t, 40 + i)
                if veh_s > ev_s - 22.0:
                    continue
                if clamped:
                    continue
                alpha = GENERAL_VEHICLE_OPACITY * distance_alpha(ev_s - veh_s, 18.0, 70.0, 245.0, 55.0)
                vehicles.append(route_vehicle(route_path, f"{mode}_behind_{i:02d}", veh_s, 0.0 if signal_queued_behind else max(22.0, behind_speed), "behind_stream", slots[(40 + i) % len(slots)], opacity=alpha, reason="EV 뒤 흐름 정체" if signal_queued_behind else "뒤 흐름 표시", signal_state=next_state))
        if signal_queued_behind and ev_s > 82.0:
            a = max(0.0, ev_s - 124.0)
            b = max(a + 10.0, ev_s - 25.0)
            rear_alpha = smoothstep(82.0, 116.0, ev_s) * (0.8 if signal_queued_ahead else 1.0)
            bands.append({"a": round(a, 2), "b": round(b, 2), "kind": "rear", "label": "EV 뒤 흐름 정체", "opacity": round(0.22 * rear_alpha, 3), "points": band_points(route_path, a, b)})
            if signal_queued_ahead:
                summary_label += " · EV 뒤 흐름 정체"
            else:
                summary_label = "EV 뒤 흐름 정체"

        stage2_vehicles: list[dict[str, Any]] = []
        fake_signals: list[dict[str, Any]] = []
        stage2_lines: list[dict[str, Any]] = []
        if mode == "B4":
            stage2_vehicles, fake_signals, stage2_lines = build_stage2_objects(t, stage2_approaches, algorithm_trace)
            vehicles.extend(stage2_vehicles)
            if stage2_vehicles:
                summary_label = "Stage2 유입 차단"

        enforce_route_vehicle_continuity(vehicles)

        visible_front_now = any(v.get("kind") == "route" and float(v.get("opacity", 1.0)) >= 0.08 and (str(v.get("role", "")).startswith("ahead") or v.get("role") == "blocked_visible") and 18.0 <= float(v.get("s", 0.0)) - ev_s <= 220.0 for v in vehicles) or any(float(b.get("opacity", 1.0)) >= 0.08 and b.get("kind") in {"front", "front_blocked"} and float(b.get("b", 0.0)) >= ev_s + 24.0 and float(b.get("a", 0.0)) <= ev_s + 220.0 for b in bands)
        visible_rear_now = any(v.get("kind") == "route" and float(v.get("opacity", 1.0)) >= 0.08 and str(v.get("role", "")).startswith("behind") and -240.0 <= float(v.get("s", 0.0)) - ev_s <= -18.0 for v in vehicles) or any(float(b.get("opacity", 1.0)) >= 0.08 and b.get("kind") == "rear" and ev_s - 240.0 <= float(b.get("b", 0.0)) <= ev_s - 18.0 for b in bands)
        if any(word in summary_label for word in ("앞 큐", "앞 구간 정체", "앞차", "과잉")) and not visible_front_now:
            summary_label = "10-1 지속 흐름"
        if "뒤 흐름 정체" in summary_label and not visible_rear_now:
            summary_label = summary_label.replace(" · EV 뒤 흐름 정체", "")

        ev = path_point(route_path, ev_s)
        frames.append({
            "t": round(t, 1),
            "ev_s": round(ev_s, 2),
            "ev": {
                "id": f"{mode}_ev",
                "lat": ev["lat"],
                "lon": ev["lon"],
                "s": round(ev_s, 2),
                "angle": ev["angle"],
                "lane_id": ev["lane_id"],
                "lane_s": ev["lane_s"],
                "edge_id": ev["edge_id"],
                "speed_kmh": round(float(st.get("speed_kmh", 0.0)), 1),
            },
            "vehicles": vehicles,
            "bands": bands,
            "stage2_lines": stage2_lines,
            "fake_signals": fake_signals,
            "summary": {
                "kind": "display_traffic",
                "label": "도착 완료" if str(st.get("reason")) == "arrived" else summary_label,
                "shown": len(vehicles),
                "ahead": ahead_count,
                "behind": behind_count,
                "slow": sum(1 for v in vehicles if float(v.get("speed_kmh", 0.0)) < 7.0),
                "signalQueuedAhead": signal_queued_ahead,
                "signalQueuedBehind": signal_queued_behind,
            },
        })
    return frames


def nearest_lane_distance(lanes: dict[str, dict[str, Any]], vehicle: dict[str, Any]) -> float:
    lane = lanes.get(str(vehicle.get("lane_id", "")))
    if not lane:
        return 9999.0
    shape = lane.get("shape", [])
    if len(shape) < 2:
        return min(
            meters_between(float(vehicle["lat"]), float(vehicle["lon"]), float(p["lat"]), float(p["lon"]))
            for p in shape
        ) if shape else 9999.0
    vlat = float(vehicle["lat"])
    vlon = float(vehicle["lon"])
    scale = 111_320.0 * math.cos(math.radians(vlat))
    best = 9999.0
    for a, b in zip(shape, shape[1:]):
        ax = (float(a["lon"]) - vlon) * scale
        ay = (float(a["lat"]) - vlat) * 111_320.0
        bx = (float(b["lon"]) - vlon) * scale
        by = (float(b["lat"]) - vlat) * 111_320.0
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        f = 0.0 if denom <= 1e-9 else max(0.0, min(1.0, -(ax * vx + ay * vy) / denom))
        best = min(best, math.hypot(ax + vx * f, ay + vy * f))
    return best


def validate_display(modes: dict[str, Any], signals: list[dict[str, Any]], timelines: dict[str, dict[str, list[list[Any]]]]) -> dict[str, Any]:
    summary = {
        "ev_next_signal_non_green_pass": 0,
        "ev_front_queue_overtake": 0,
        "vehicle_stopline_non_green_pass": 0,
        "green_empty_stopped": 0,
        "green_empty_slow": 0,
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
        prev: dict[str, Any] | None = None
        for sample in doc.get("display_samples", []):
            summary["frames_checked"] += 1
            s = float(sample["s"])
            t = float(sample["t"])
            if prev:
                summary["ev_max_step_m"] = max(summary["ev_max_step_m"], round(s - float(prev["s"]), 2))
            prev = sample
            sig = next_signal_after(signals, s - 1.0)
            if sig and state_at(timelines[mode][sig["id"]], t) in {"red", "yellow", "allred"} and s > float(sig["s"]) - 18.0 + 0.1:
                summary["ev_next_signal_non_green_pass"] += 1
            if sample.get("front_queue_tail_s") is not None and sample.get("reason") == "front_queue_tail" and s > float(sample["front_queue_tail_s"]) - 18.0:
                summary["ev_front_queue_overtake"] += 1
            if sample.get("next_signal_state") == "green" and float(sample.get("front_queue_count") or 0.0) <= 0.1 and float(sample.get("speed_kmh") or 0.0) <= 2.0 and sample.get("reason") not in {"arrived", "stage2_hold"}:
                summary["green_empty_stopped"] += 1
            if sample.get("next_signal_state") == "green" and float(sample.get("front_queue_count") or 0.0) <= 0.1 and float(sample.get("speed_kmh") or 0.0) < 12.0 and sample.get("reason") not in {"arrived", "stage2_hold", "green_downstream_queue"}:
                summary["green_empty_slow"] += 1
            if sample.get("reason") == "green_downstream_queue" and (float(sample.get("speed_kmh") or 0.0) > 2.0 or float(sample.get("front_queue_count") or 0.0) <= 0.1):
                summary["green_blocked_moving"] += 1
        samples = doc.get("display_samples", [])
        for frame in doc.get("frames", []):
            t = float(frame["t"])
            ev_s = float(frame.get("ev_s", 0.0))
            sample = max((s for s in samples if float(s.get("t", 0.0)) <= t), key=lambda row: float(row.get("t", 0.0)), default={})
            label = str(frame.get("summary", {}).get("label", ""))
            visible_front = any(v.get("kind") == "route" and float(v.get("opacity", 1.0)) >= 0.08 and (str(v.get("role", "")).startswith("ahead") or v.get("role") == "blocked_visible") and 18.0 <= float(v.get("s", 0.0)) - ev_s <= 220.0 for v in frame.get("vehicles", [])) or any(float(b.get("opacity", 1.0)) >= 0.08 and b.get("kind") in {"front", "front_blocked"} and float(b.get("b", 0.0)) >= ev_s + 24.0 and float(b.get("a", 0.0)) <= ev_s + 220.0 for b in frame.get("bands", []))
            visible_rear = any(v.get("kind") == "route" and float(v.get("opacity", 1.0)) >= 0.08 and str(v.get("role", "")).startswith("behind") and -240.0 <= float(v.get("s", 0.0)) - ev_s <= -18.0 for v in frame.get("vehicles", [])) or any(float(b.get("opacity", 1.0)) >= 0.08 and b.get("kind") == "rear" and ev_s - 240.0 <= float(b.get("b", 0.0)) <= ev_s - 18.0 for b in frame.get("bands", []))
            if str(sample.get("reason")) == "arrived" and any(word in label for word in ("큐", "정체", "대기")):
                summary["arrived_with_queue_label"] += 1
            if any(word in label for word in ("앞 큐", "앞 구간 정체", "앞차", "과잉")) and not visible_front:
                summary["queue_label_without_visible_queue"] += 1
            if any(word in label for word in ("뒤 흐름 정체", "뒤 차량")) and not visible_rear:
                summary["queue_label_without_visible_queue"] += 1
            if str(sample.get("reason")) == "green_downstream_queue" and not visible_front:
                summary["green_blocked_without_visible_front"] += 1
            for band in frame.get("bands", []):
                if band.get("kind") == "front_moving":
                    a = float(band.get("a", 0.0))
                    b = float(band.get("b", 0.0))
                    if not any(v.get("kind") == "route" and v.get("role") == "downstream_moving" and a <= float(v.get("s", 0.0)) <= b and float(v.get("speed_kmh", 0.0)) >= 8.0 for v in frame.get("vehicles", [])):
                        summary["downstream_label_without_moving_vehicle"] += 1
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
    for key in ("ev_next_signal_non_green_pass", "ev_front_queue_overtake", "vehicle_stopline_non_green_pass", "green_empty_stopped", "green_empty_slow", "green_blocked_moving", "behind_vehicle_ahead_of_ev", "green_blocked_without_visible_front", "queue_label_without_visible_queue", "arrived_with_queue_label", "downstream_label_without_moving_vehicle"):
        if summary[key]:
            summary["ok"] = False
    return summary


def build_validation_report(modes: dict[str, Any], tls_validation: dict[str, Any], display_validation: dict[str, Any], algorithm_trace: dict[str, Any], lanes: dict[str, dict[str, Any]], route_path: LanePath, stage2_approaches: list[dict[str, Any]], signals: list[dict[str, Any]]) -> dict[str, Any]:
    trajectory = {
        "max_frame_gap_sec": DISPLAY_TRAFFIC_DT,
        "max_vehicle_step_m": 0.0,
        "max_all_route_vehicle_step_m": 0.0,
        "max_vehicle_opacity_delta": 0.0,
        "max_vehicle_speed_kmh": 0.0,
        "tracked_vehicle_pop_in": 0,
        "tracked_vehicle_disappear": 0,
        "flow_vehicle_pop_in_near_camera": 0,
        "flow_vehicle_disappear_near_camera": 0,
        "flow_vehicle_large_jump": 0,
        "vehicle_reverse_steps": 0,
        "flow_modes_checked": ["B04", "B4"],
        "checked_frames": 0,
    }
    visual = {
        "vehicle_color_palette_size": 1,
        "vehicle_color_changes": 0,
        "stage2_min_vehicle_gap_m": 999.0,
        "front_blocked_band_frames": 0,
        "front_moving_band_frames": 0,
        "final_split_straight_vehicles": 0,
        "final_split_right_vehicles": 0,
        "critical_queue_disappear": 0,
        "ok": True,
    }
    geometry = {
        "vehicle_lane_distance_max_m": 0.0,
        "vehicle_lane_distance_over_6m": 0,
        "stage2_fallback_manual": sum(1 for item in stage2_approaches if item.get("source") == "fallback_manual"),
        "stage2_stopline_target_fail": 0,
        "stage2_stopline_exit_fail": 0,
        "signal_distance_over_90m": 0,
        "ok": True,
    }
    for item in stage2_approaches:
        stop = item.get("stopline") or ({"lat": item["line"][0][0], "lon": item["line"][0][1]} if item.get("line") else {"lat": 0.0, "lon": 0.0})
        if meters_between(stop["lat"], stop["lon"], STAGE2_CENTER["lat"], STAGE2_CENTER["lon"]) > 80.0:
            geometry["stage2_stopline_target_fail"] += 1
        if meters_between(stop["lat"], stop["lon"], FIRETRUCK_EXIT["lat"], FIRETRUCK_EXIT["lon"]) < 35.0:
            geometry["stage2_stopline_exit_fail"] += 1
    for sig in signals:
        if float(sig.get("route_distance_m", 0.0)) > 90.0:
            geometry["signal_distance_over_90m"] += 1
    for doc in modes.values():
        prev_by_id: dict[str, dict[str, Any]] = {}
        prev_t_by_id: dict[str, float] = {}
        for frame in doc.get("frames", []):
            frame_t = float(frame["t"])
            trajectory["checked_frames"] += 1
            for band in frame.get("bands", []):
                if band.get("kind") == "front_blocked":
                    visual["front_blocked_band_frames"] += 1
                if band.get("kind") == "front_moving":
                    visual["front_moving_band_frames"] += 1
            stage2_by_approach: dict[str, list[dict[str, Any]]] = {}
            for vehicle in frame.get("vehicles", []) + [frame.get("ev", {})]:
                if not vehicle:
                    continue
                d = nearest_lane_distance(lanes, vehicle)
                geometry["vehicle_lane_distance_max_m"] = max(geometry["vehicle_lane_distance_max_m"], round(d, 2))
                if d > 6.0:
                    geometry["vehicle_lane_distance_over_6m"] += 1
                if vehicle.get("kind") == "route" and vehicle.get("s") is not None:
                    vid = str(vehicle.get("id", ""))
                    prev = prev_by_id.get(vid)
                    prev_t = prev_t_by_id.get(vid)
                    if prev and prev.get("s") is not None and prev_t is not None and frame_t - prev_t <= DISPLAY_TRAFFIC_DT + 0.06:
                        step = float(vehicle["s"]) - float(prev["s"])
                        trajectory["max_all_route_vehicle_step_m"] = max(trajectory["max_all_route_vehicle_step_m"], round(abs(step), 2))
                        trajectory["max_vehicle_opacity_delta"] = max(trajectory["max_vehicle_opacity_delta"], round(abs(float(vehicle.get("opacity", 1.0)) - float(prev.get("opacity", 1.0))), 3))
                        trajectory["max_vehicle_step_m"] = max(trajectory["max_vehicle_step_m"], round(abs(step), 2))
                        if step < -0.25:
                            trajectory["vehicle_reverse_steps"] += 1
                        if abs(step) > 38.0:
                            trajectory["flow_vehicle_large_jump"] += 1
                    prev_by_id[vid] = vehicle
                    prev_t_by_id[vid] = frame_t
                    if vehicle.get("role") in {"ahead_queue", "ahead_stream", "behind_stream", "behind_queue", "downstream_moving", "blocked_visible"}:
                        trajectory["max_vehicle_speed_kmh"] = max(trajectory["max_vehicle_speed_kmh"], round(float(vehicle.get("speed_kmh", 0.0)), 2))
                        if float(vehicle.get("s", 0.0)) >= route_path.length_m - 410.0 and vehicle.get("role") == "ahead_stream":
                            if vehicle.get("choice") == "straight":
                                visual["final_split_straight_vehicles"] += 1
                            if vehicle.get("choice") == "right":
                                visual["final_split_right_vehicles"] += 1
                if vehicle.get("role") == "stage2_blocked":
                    approach_id = str(vehicle.get("id", "")).split("_")[1]
                    stage2_by_approach.setdefault(approach_id, []).append(vehicle)
            for vehicles in stage2_by_approach.values():
                for i, a in enumerate(vehicles):
                    for b in vehicles[i + 1:]:
                        visual["stage2_min_vehicle_gap_m"] = min(visual["stage2_min_vehicle_gap_m"], round(meters_between(a["lat"], a["lon"], b["lat"], b["lon"]), 2))
    if visual["stage2_min_vehicle_gap_m"] == 999.0:
        visual["stage2_min_vehicle_gap_m"] = 0.0
    visual["ok"] = visual["front_blocked_band_frames"] > 0 and visual["front_moving_band_frames"] > 0 and visual["final_split_straight_vehicles"] > 0 and visual["final_split_right_vehicles"] > 0 and visual["stage2_min_vehicle_gap_m"] >= 8.0
    geometry["ok"] = all(int(geometry[k]) == 0 for k in ("vehicle_lane_distance_over_6m", "stage2_fallback_manual", "stage2_stopline_target_fail", "stage2_stopline_exit_fail", "signal_distance_over_90m"))
    algorithm = {
        "stage1_present": bool(algorithm_trace.get("stage1")),
        "stage2_events": len(algorithm_trace.get("stage2", [])),
        "stage3_events": len(algorithm_trace.get("stage3", [])),
        "case_a_events": int(algorithm_trace.get("event_counts", {}).get("case_a", 0)),
        "case_b_events": int(algorithm_trace.get("event_counts", {}).get("case_b", 0)),
        "green_active_events": int(algorithm_trace.get("event_counts", {}).get("green_active", 0)),
        "safety_denied_events": int(algorithm_trace.get("event_counts", {}).get("safety_denied", 0)),
    }
    ok = bool(tls_validation.get("ok")) and bool(display_validation.get("ok")) and visual["ok"] and geometry["ok"] and trajectory["vehicle_reverse_steps"] == 0 and trajectory["flow_vehicle_large_jump"] == 0 and algorithm["stage2_events"] >= 3 and algorithm["case_a_events"] > 0 and algorithm["case_b_events"] > 0
    return {
        "ok": ok,
        "tls": tls_validation,
        "display": display_validation,
        "trajectory": trajectory,
        "visual_regression": visual,
        "geometry": geometry,
        "algorithm": algorithm,
        "screenshot_targets": [
            {"id": "stage2_hold", "mode": "B4", "t": 4.0, "expect": "신당역 유입 정지"},
            {"id": "case_a_green", "mode": "B4", "t": 12.0, "expect": "Stage3 Case A"},
            {"id": "case_b_downstream", "mode": "B4", "t": 31.0, "expect": "Stage3 Case B"},
            {"id": "green_front_queue", "mode": "B4", "t": 76.2, "expect": "앞 큐 과잉"},
            {"id": "final_split", "mode": "B4", "t": 230.0, "expect": "직진/우회전 분기"},
        ],
    }


def build_scene_payload() -> dict[str, Any]:
    if not ACTUAL_PROGRESS_DATA.is_file():
        raise SystemExit(f"missing source progress data: {ACTUAL_PROGRESS_DATA}")
    inputs = load_inputs()
    signal_policy = inputs.get("presentation_signal_policy", {})
    demand_policy = presentation_demand_policy(inputs)
    source = json.loads(ACTUAL_PROGRESS_DATA.read_text(encoding="utf-8"))
    tls_source = json.loads(TLS_PROGRESS_DATA.read_text(encoding="utf-8")) if TLS_PROGRESS_DATA.is_file() else source
    net = sumolib.net.readNet(str(PRESENTATION_NET_FILE), withInternal=True)
    lanes: dict[str, dict[str, Any]] = {}
    route_path, destination_meta = build_route_path(net, lanes)
    samples_by_mode: dict[str, list[dict[str, Any]]] = {}
    for mode in ("B04", "B4"):
        events = source.get("algorithm", {}).get("events", {}).get(mode, [])
        samples_by_mode[mode] = build_raw_samples(source["modes"][mode], route_path, mode, events)
    signals, timelines, tls_validation = build_signals(source, tls_source, route_path, samples_by_mode, signal_policy, demand_policy)
    algorithm_trace = build_algorithm_trace(source, PRESENTATION_ROUTE_ID, route_path.length_m, signals, signal_policy)
    stage2_approaches = build_stage2_approaches(net, lanes)
    demand_slots = parse_demand_slots()
    shared_profile = source["modes"]["B04"].get("traffic_profile", []) or source["modes"]["B4"].get("traffic_profile", [])
    modes: dict[str, Any] = {}
    for mode, label, color in (("B04", "B04 · 일반 신호", "#dc2626"), ("B4", "B4 · 우선신호", "#2563eb")):
        profile = shared_profile
        display_samples = build_display_samples(mode, samples_by_mode[mode], profile, signals, timelines[mode], route_path, demand_policy, algorithm_trace)
        traffic_samples = high_res_samples(display_samples, DISPLAY_TRAFFIC_DT)
        frames = build_frames(mode, display_samples, traffic_samples, profile, signals, timelines[mode], route_path, stage2_approaches, demand_slots, demand_policy, algorithm_trace)
        modes[mode] = {
            "label": label,
            "color": color,
            "samples": samples_by_mode[mode],
            "display_samples": display_samples,
            "frames": frames,
            "background": [],
            "traffic_profile": profile,
            "traffic_profile_source": "B04_shared_presentation_demand",
            "travel_time_sec": round(float(display_samples[-1]["t"] if display_samples else source["modes"][mode]["travel_time_sec"]), 1),
            "distance_m": route_path.length_m,
            "source": "10-1_lane_scene_graph",
        }
    display_validation = validate_display(modes, signals, timelines)
    validation_report = build_validation_report(modes, tls_validation, display_validation, algorithm_trace, lanes, route_path, stage2_approaches, signals)
    return {
        "schema": "seoul_fire_station_presentation.10-1_scene.v5",
        "title": DISPLAY_TITLE,
        "source_progress_data": rel_path(ACTUAL_PROGRESS_DATA),
        "tls_source_progress_data": rel_path(TLS_PROGRESS_DATA) if TLS_PROGRESS_DATA.is_file() else rel_path(ACTUAL_PROGRESS_DATA),
        "source_truth_run": PRESENTATION_SOURCE_TRUTH_RUN,
        "presentation_inputs": inputs,
        "presentation_input_files": {
            "signal_net_file": rel_path(PRESENTATION_NET_FILE),
            "demand_route_file": rel_path(PRESENTATION_DEMAND_ROUTE),
            "firetruck_route_file": rel_path(PRESENTATION_ROUTE_XML),
        },
        "lanes": list(lanes.values()),
        "route": {
            "id": f"{PRESENTATION_ROUTE_ID}_TO_UNNAMED_FINAL_TARGET",
            "points": route_path.points,
            "display_parts": route_display_parts(route_path),
            "length_m": route_path.length_m,
            "lane_path": [{"edge_id": s.edge_id, "lane_id": s.lane_id, "start_s": s.start_s, "end_s": s.end_s} for s in route_path.segments],
        },
        "presentation_destination": destination_meta,
        "signals": signals,
        "timelines": timelines,
        "tls_validation": tls_validation,
        "display_validation": display_validation,
        "validation_report": validation_report,
        "algorithm_trace": algorithm_trace,
        "stage2_block_approaches": stage2_approaches,
        "stage2_block_lines": [item.get("line", []) for item in stage2_approaches],
        "modes": modes,
        "presentation_policy": {
            "route": "EV, traffic vehicles, queues, Stage2 blocks, and signal stoplines are resolved on SUMO lane shapes before HTML render.",
            "traffic_flow": "10-1 owned presentation demand vehicles are persistent scene objects seeded from the presentation demand route file.",
            "stage2_display": "Stage2 approaches are auto-selected from SUMO lanes around Toegye-ro x Dasan-ro; fallback_manual fails validation.",
            "renderer": "Leaflet base map with Canvas scene overlay; browser code draws frames only and does not create traffic.",
        },
    }


def render_scene_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{payload["title"]}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html,body{{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f172a;color:#f8fafc}}
.wrap{{height:100vh;display:flex;flex-direction:column}}
header{{height:48px;display:grid;grid-template-columns:1fr 560px 300px;align-items:center;gap:14px;padding:0 16px;background:#111827}}
h1{{font-size:16px;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
button,select{{background:#2563eb;color:white;border:0;border-radius:6px;padding:7px 12px;font-weight:800}}
select{{appearance:auto;background:#1d4ed8}}
.controls{{display:flex;gap:10px;align-items:center}}
#seek{{flex:1}}
.clock,.legend{{font-size:12px;color:#cbd5e1}}
.maps{{display:grid;grid-template-columns:1fr 1fr;flex:1;min-height:0}}
.panel{{position:relative;border-right:1px solid #111827;overflow:hidden}}
.map{{position:absolute;inset:0;background:#edf2f7}}
.scene{{position:absolute;inset:0;z-index:650;pointer-events:none}}
.tag{{position:absolute;top:10px;left:10px;z-index:700;background:rgba(15,23,42,.88);border-radius:8px;padding:9px 12px;font-size:13px;line-height:1.55;font-weight:650;max-width:45%}}
.tag b{{font-size:14px}}
.why{{color:#fde68a}}
.traffic{{color:#bbf7d0}}
.eventToast{{position:absolute;right:12px;bottom:18px;z-index:720;max-width:44%;padding:7px 13px;border-radius:8px;background:rgba(15,23,42,.82);color:#f8fafc;border:1px solid rgba(255,255,255,.16);font-size:13px;font-weight:850;line-height:1.22;text-align:center;pointer-events:none;box-shadow:0 2px 10px rgba(15,23,42,.24)}}
.bottom{{height:150px;display:grid;grid-template-columns:320px 1fr;background:#0b1220}}
#overview{{position:relative}}
#chart{{padding:8px 12px}}
svg{{width:100%;height:104px}}
.pill{{display:inline-flex;align-items:center;gap:4px;margin-left:8px}}
.sw{{width:10px;height:10px;border-radius:50%;display:inline-block}}
@media(max-width:900px){{header{{grid-template-columns:1fr;grid-auto-rows:min-content;height:auto;padding:8px 10px}}.maps{{grid-template-columns:1fr}}.bottom{{display:none}}.tag{{max-width:72%;font-size:12px}}}}
</style>
</head>
<body><div class="wrap">
<header><h1>{payload["title"]}</h1><div class="controls"><button id="play">▶ 재생</button><button id="reset">↺ 처음</button><select id="rate" aria-label="재생 배속"><option value="1">1x</option><option value="2" selected>2x</option><option value="4">4x</option><option value="8">8x</option></select><input id="seek" type="range" min="0" max="1000" value="0"><span class="clock" id="clock"></span></div><div class="legend"><span class="pill"><i class="sw" style="background:#ff1f3d"></i>red</span><span class="pill"><i class="sw" style="background:#ffd400"></i>yellow</span><span class="pill"><i class="sw" style="background:#00e676"></i>green</span><span class="pill">Canvas scene graph</span></div></header>
<div class="maps">
<section class="panel"><div id="mapLeft" class="map"></div><canvas id="canvasLeft" class="scene"></canvas><div id="tagLeft" class="tag"></div><div id="eventLeft" class="eventToast"></div></section>
<section class="panel"><div id="mapRight" class="map"></div><canvas id="canvasRight" class="scene"></canvas><div id="tagRight" class="tag"></div><div id="eventRight" class="eventToast"></div></section>
</div>
<div class="bottom"><div id="overview" class="map"></div><div id="chart"><div class="legend"><span class="pill"><i class="sw" style="background:#dc2626"></i>B04</span><span class="pill"><i class="sw" style="background:#2563eb"></i>B4</span><span id="cmp"></span></div><svg id="svg" viewBox="0 0 1000 110" preserveAspectRatio="none"></svg></div></div>
</div>
<script>
const DATA={data};
const MODES=["B04","B4"], SUF={{B04:"Left",B4:"Right"}};
let t=0, playing=false, last=null, rate=2.0;
const TMAX=Math.max(DATA.modes.B04.travel_time_sec,DATA.modes.B4.travel_time_sec);
function makeMap(id){{const m=L.map(id,{{zoomControl:false,attributionControl:false,preferCanvas:true}});L.tileLayer("https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png",{{maxZoom:19}}).addTo(m);return m;}}
function idxAt(a,t){{let lo=0,hi=a.length-1,r=0;while(lo<=hi){{const m=(lo+hi)>>1;if(a[m].t<=t){{r=m;lo=m+1}}else hi=m-1}}return r}}
function lerp(a,b,f){{return a+(b-a)*f}}
function lerpAngle(a,b,f){{const d=((b-a+540)%360)-180;return a+d*f}}
function interpObj(v,w,f){{return {{...w,lat:lerp(v.lat,w.lat,f),lon:lerp(v.lon,w.lon,f),s:v.s!=null&&w.s!=null?lerp(v.s,w.s,f):w.s,lane_s:v.lane_s!=null&&w.lane_s!=null?lerp(v.lane_s,w.lane_s,f):w.lane_s,angle:lerpAngle(v.angle||0,w.angle||0,f),speed_kmh:lerp(v.speed_kmh||0,w.speed_kmh||0,f),opacity:lerp(v.opacity??1,w.opacity??1,f)}}}}
function sampleAt(mode,t){{const a=DATA.modes[mode].display_samples;if(t<=a[0].t)return a[0];if(t>=a[a.length-1].t)return a[a.length-1];const i=idxAt(a,t),x=a[i],y=a[Math.min(i+1,a.length-1)],f=(t-x.t)/(y.t-x.t||1);return {{...y,t,lat:lerp(x.lat,y.lat,f),lon:lerp(x.lon,y.lon,f),s:lerp(x.s,y.s,f),speed_kmh:lerp(x.speed_kmh,y.speed_kmh,f)}}}}
function frameAt(mode,t){{const a=DATA.modes[mode].frames;if(t<=a[0].t)return a[0];if(t>=a[a.length-1].t)return a[a.length-1];const i=idxAt(a,t),x=a[i],y=a[Math.min(i+1,a.length-1)],f=(t-x.t)/(y.t-x.t||1);const byY=new Map((y.vehicles||[]).map(v=>[v.id,v]));const used=new Set();const vehicles=[];for(const v of (x.vehicles||[])){{const w=byY.get(v.id);if(!w){{vehicles.push(v);continue}}used.add(v.id);vehicles.push(interpObj(v,w,f));}}for(const w of (y.vehicles||[]))if(!used.has(w.id))vehicles.push(w);return {{...y,t,ev:interpObj(x.ev,y.ev,f),vehicles}}}}
function stateAt(sig,mode,t){{const a=DATA.timelines[mode][sig.id]||[[0,"green"]];let st=a[0][1];for(const p of a){{if(p[0]<=t)st=p[1];else break}}return st}}
function nextSignal(s){{return DATA.signals.find(x=>x.s>s+2)}}
function priorityActive(sig,mode,t,st){{if(mode!=="B4")return false;const ns=nextSignal(st.s);if(!ns||ns.id!==sig.id)return false;const wins=(sig.priority_windows&&sig.priority_windows.B4)||[];return wins.some(w=>t>=w[0]&&t<=w[1]);}}
function latLng(map,p){{return map.latLngToContainerPoint([p.lat,p.lon])}}
function pathPts(map,pts){{return pts.map(p=>Array.isArray(p)?map.latLngToContainerPoint([p[0],p[1]]):map.latLngToContainerPoint([p.lat,p.lon]))}}
function drawPath(ctx,pts,style){{if(!pts||pts.length<2)return;ctx.save();const dash=style.dash||null;delete style.dash;Object.assign(ctx,style);if(dash)ctx.setLineDash(dash);ctx.beginPath();ctx.moveTo(pts[0].x,pts[0].y);for(let i=1;i<pts.length;i++)ctx.lineTo(pts[i].x,pts[i].y);ctx.stroke();ctx.restore();}}
function drawVehicle(ctx,map,v){{if((v.opacity??.88)<=.015)return;const p=latLng(map,v);ctx.save();ctx.translate(p.x,p.y);ctx.rotate((v.angle||0)*Math.PI/180);const hot=v.role==="ahead_queue"||v.role==="downstream_moving"||v.role==="blocked_visible";const w=v.kind==="stage2"?20:(hot?20:16),h=v.kind==="stage2"?11:(hot?11:8);ctx.globalAlpha=v.opacity??.88;ctx.fillStyle=v.color||"#f97316";ctx.strokeStyle="#fff";ctx.lineWidth=hot?2:1.3;roundRect(ctx,-w/2,-h/2,w,h,Math.min(5,h/2));ctx.fill();ctx.stroke();ctx.restore();}}
function drawEV(ctx,map,ev,color){{const p=latLng(map,ev);ctx.save();ctx.translate(p.x,p.y);ctx.rotate((ev.angle||0)*Math.PI/180);ctx.fillStyle=color;ctx.strokeStyle="#fff";ctx.lineWidth=3;roundRect(ctx,-18,-11,36,22,7);ctx.fill();ctx.stroke();ctx.rotate(-(ev.angle||0)*Math.PI/180);ctx.fillStyle="#fff";ctx.font="800 12px system-ui";ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText("EV",0,1);ctx.restore();}}
function roundRect(ctx,x,y,w,h,r){{ctx.beginPath();ctx.moveTo(x+r,y);ctx.arcTo(x+w,y,x+w,y+h,r);ctx.arcTo(x+w,y+h,x,y+h,r);ctx.arcTo(x,y+h,x,y,r);ctx.arcTo(x,y,x+w,y,r);ctx.closePath();}}
function drawSignal(ctx,map,sig,state,next,priority){{const base=latLng(map,sig);const stage=sig.display_role==="stage2_single_signal";const offX=stage?-54:0,offY=stage?-78:-30;ctx.save();ctx.translate(base.x+offX,base.y+offY);if(priority){{ctx.save();ctx.shadowColor="rgba(34,197,94,.95)";ctx.shadowBlur=24;ctx.fillStyle="rgba(34,197,94,.36)";roundRect(ctx,-24,-36,48,72,10);ctx.fill();ctx.restore();}}ctx.fillStyle="#020617";ctx.strokeStyle=stage?"#fecaca":(priority?"#22c55e":(next?"#94a3b8":"#64748b"));ctx.lineWidth=stage?3.5:(priority?4.5:(next?3:2.5));roundRect(ctx,-12,-24,24,48,5);ctx.fill();ctx.stroke();const colors={{red:"#ff1f3d",yellow:"#ffd400",green:"#00e676",off:"#111827"}};for(const [i,key] of ["red","yellow","green"].entries()){{ctx.beginPath();ctx.arc(0,-14+i*14,5.6,0,Math.PI*2);ctx.fillStyle=(state==="allred"&&key==="red")||state===key?colors[key]:colors.off;ctx.fill();}}const label=sig.label||(priority?"GREEN EXT":"");if(label){{ctx.font="900 12px system-ui";ctx.textAlign="center";ctx.textBaseline="middle";const w=Math.max(priority?76:58,ctx.measureText(label).width+16),labelY=stage?58:40;ctx.fillStyle=priority?"rgba(20,83,45,.96)":(state==="allred"?"rgba(127,29,29,.94)":"rgba(6,78,59,.9)");ctx.strokeStyle=priority?"#bbf7d0":"#fff";ctx.lineWidth=2;roundRect(ctx,-w/2,labelY-11,w,22,5);ctx.fill();ctx.stroke();ctx.fillStyle="#fff";ctx.fillText(label,0,labelY);}}ctx.restore();}}
function drawPanel(panel){{const mode=panel.mode,map=panel.map,canvas=panel.canvas,ctx=panel.ctx,fr=frameAt(mode,t),st=sampleAt(mode,t);const rect=canvas.parentElement.getBoundingClientRect(),dpr=window.devicePixelRatio||1;if(canvas.width!==Math.round(rect.width*dpr)||canvas.height!==Math.round(rect.height*dpr)){{canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);canvas.style.width=rect.width+"px";canvas.style.height=rect.height+"px";}}ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,rect.width,rect.height);for(const b of fr.bands||[]){{const color=b.kind==="rear"?"#2563eb":b.kind==="front_moving"?"#16a34a":b.kind==="empty_ahead"?"#22c55e":"#7c2d12";const fallback=b.kind==="front_moving"?.42:b.kind==="rear"?.22:.27;drawPath(ctx,pathPts(map,b.points||[]),{{strokeStyle:color,lineWidth:b.kind==="front_moving"?8:16,globalAlpha:b.opacity??fallback,lineCap:"round",lineJoin:"round"}});}}for(const line of fr.stage2_lines||[])drawPath(ctx,pathPts(map,line.line||[]),{{strokeStyle:"#0ea5e9",lineWidth:8,globalAlpha:.65,lineCap:"round",dash:[13,8]}});for(const sig of DATA.signals){{const sigState=stateAt(sig,mode,t),isNext=nextSignal(st.s)?.id===sig.id;drawSignal(ctx,map,sig,sigState,isNext,priorityActive(sig,mode,t,st));}}for(const p of fr.fake_signals||[])drawSignal(ctx,map,p,p.signal_state||"red",false,false);for(const v of fr.vehicles||[])drawVehicle(ctx,map,v);drawEV(ctx,map,fr.ev,DATA.modes[mode].color);const ns=nextSignal(st.s),nextState=ns?stateAt(ns,mode,t):"-";const arrived=st.reason==="arrived";const traffic=arrived?"도착 완료":(fr.summary&&fr.summary.label)||"10-1 scene";const reason=arrived?"도착":st.reason==="green_downstream_queue"?`신호 ${{nextState}} · 앞 큐 과잉 대기`:st.reason==="stage2_hold"?"Stage2 유입 차단 대기":st.reason==="front_queue_tail"?`앞 큐 뒤에서 ${{nextState}} 대기`:st.reason==="front_queue_red"?`다음 신호 ${{nextState}} 대기`:st.speed_kmh<2?"정체 대기":"진행 중";document.getElementById("tag"+SUF[mode]).innerHTML=`<b style="color:${{DATA.modes[mode].color}}">${{DATA.modes[mode].label}}</b><br>속도 ${{arrived?"도착":st.speed_kmh.toFixed(0)+" km/h"}} · 진행 ${{Math.min(100,Math.round(st.s/(DATA.modes[mode].distance_m||DATA.route.length_m)*100))}}% · 다음신호 ${{arrived?"-":nextState}}<br>알고리즘 <span class="why">${{st.algorithm||"-"}}</span><br>EV 상태 <span class="why">${{reason}}</span><br>일반차 <span class="traffic">${{traffic}}</span>`;document.getElementById("event"+SUF[mode]).textContent=traffic;const cam=[fr.ev.lat,fr.ev.lon];if(t-panel.lastCameraT>=.4||t===0||arrived){{map.setView(cam,16.75,{{animate:false}});panel.lastCameraT=t;}}}}
function chart(){{const svg=document.getElementById("svg"),W=1000,H=110,pad=6;let html="";const X=x=>pad+x/TMAX*(W-2*pad),Y=v=>H-pad-v/60*(H-2*pad);for(const m of MODES){{const d=DATA.modes[m].display_samples.map((p,i)=>(i?"L":"M")+X(p.t).toFixed(1)+" "+Y(p.speed_kmh).toFixed(1)).join("");html+=`<path d="${{d}}" fill="none" stroke="${{DATA.modes[m].color}}" stroke-width="2"/>`;}}html+=`<line id="cur" x1="${{X(t)}}" x2="${{X(t)}}" y1="0" y2="${{H}}" stroke="#e5e7eb" stroke-dasharray="3 3"/>`;svg.innerHTML=html;return X}}
function routeDrawParts(){{return (DATA.route.display_parts&&DATA.route.display_parts.length)?DATA.route.display_parts:[DATA.route.points];}}
function drawRoute(map,color,weight,opacity){{for(const part of routeDrawParts())L.polyline(part.map(p=>[p.lat,p.lon]),{{color,weight,opacity,interactive:false}}).addTo(map);}}
function initPanel(mode,id,canvasId){{const map=makeMap(id);drawRoute(map,DATA.modes[mode].color,5,.38);map.fitBounds(L.latLngBounds(DATA.route.points.map(p=>[p.lat,p.lon])),{{padding:[30,30]}});const canvas=document.getElementById(canvasId);const panel={{mode,map,canvas,ctx:canvas.getContext("2d"),lastCameraT:-999}};map.on("move zoom resize",()=>drawPanel(panel));return panel}}
const panels={{B04:initPanel("B04","mapLeft","canvasLeft"),B4:initPanel("B4","mapRight","canvasRight")}};
const ov=makeMap("overview");drawRoute(ov,"#94a3b8",3,1);ov.fitBounds(L.latLngBounds(DATA.route.points.map(p=>[p.lat,p.lon])),{{padding:[12,12]}});
const X=chart();document.getElementById("cmp").textContent=`B04 ${{DATA.modes.B04.travel_time_sec.toFixed(0)}}s vs B4 ${{DATA.modes.B4.travel_time_sec.toFixed(0)}}s`;
function render(){{drawPanel(panels.B04);drawPanel(panels.B4);document.getElementById("seek").value=Math.round(t/TMAX*1000);document.getElementById("clock").textContent=`t = ${{t.toFixed(1)}}s / ${{TMAX.toFixed(0)}}s`;const c=document.getElementById("cur");if(c){{c.setAttribute("x1",X(t));c.setAttribute("x2",X(t));}}}}
function loop(ts){{if(!playing)return;if(last!=null)t=Math.min(TMAX,t+(ts-last)/1000*rate);last=ts;if(t>=TMAX){{playing=false;document.getElementById("play").textContent="▶ 재생";}}render();if(playing)requestAnimationFrame(loop);}}
document.getElementById("play").onclick=function(){{if(t>=TMAX)t=0;playing=!playing;this.textContent=playing?"⏸ 일시정지":"▶ 재생";last=null;if(playing)requestAnimationFrame(loop);}};
document.getElementById("reset").onclick=function(){{t=0;playing=false;document.getElementById("play").textContent="▶ 재생";render();}};
document.getElementById("rate").onchange=function(){{rate=parseFloat(this.value)||1;}};
document.getElementById("seek").oninput=function(){{t=this.value/1000*TMAX;render();}};
setTimeout(()=>{{Object.values(panels).forEach(p=>p.map.invalidateSize());ov.invalidateSize();render();}},250);render();
</script></body></html>"""
