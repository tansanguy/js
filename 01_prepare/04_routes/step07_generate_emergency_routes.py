#!/usr/bin/env python3
"""Generate Step 7 emergency routes, route review HTML, and preflight."""

from __future__ import annotations

import csv
import argparse
import heapq
import html
import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import read_sumo_net  # noqa: E402


ACTIVE_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
ACTIVE_EDGES_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_edges.geojson"
ACTIVE_TLS_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_tls.geojson"
STEP6_SUMMARY = PROJECT_ROOT / "data_prepared/routes/route_connectivity_summary.json"
STEP6_ROUTES = PROJECT_ROOT / "data_prepared/routes/route_connectivity_check.csv"
STATION_START_EDGE = PROJECT_ROOT / "data_prepared/routes/station_start_edge.json"
MAP_CONFIG = PROJECT_ROOT / "config/map_config.yaml"
START_EDGE_ID = "-381802881#2"
SPINE_LENGTH_WARNING_RATIO = 0.25
SPINE_MANUAL_REVIEW_RATIO = 0.40
SPINE_MIN_LENGTH_RATIO = 0.25
SPINE_MIN_CONSECUTIVE_LENGTH_M = 300.0
SPINE_BUFFER_M = 300.0
SPINE_MIN_EDGE_LENGTH_M = 10.0
SPINE_SCORE_THRESHOLD = 0.65
SPINE_BIAS_STRENGTHS = [0.25, 0.40, 0.60, 0.80]
SPINE_V2_BIAS_STRENGTHS = [0.35, 0.55, 0.75, 0.95, 1.15]
SHARP_TURN_ANGLE_DEG = 120.0
UTURN_LIKE_ANGLE_DEG = 160.0
TURN_GAP_THRESHOLD_M = 100.0

EMERGENCY_ROUTES_CSV = PROJECT_ROOT / "data_prepared/routes/emergency_routes.csv"
EMERGENCY_ROUTES_XML = PROJECT_ROOT / "data_prepared/routes/emergency_routes.rou.xml"
ROUTE_COMPARE_CSV = PROJECT_ROOT / "data_prepared/routes/route_compare_shortest_major_spine.csv"
CORRIDOR_SPINE_EDGES_CSV = PROJECT_ROOT / "data_prepared/routes/corridor_spine_edges.csv"
EMERGENCY_ROUTES_V2_CSV = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
EMERGENCY_ROUTES_V2_XML = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.rou.xml"
ROUTE_COMPARE_V2_CSV = PROJECT_ROOT / "data_prepared/routes/route_compare_shortest_major_spine_v2.csv"
TURN_DIAGNOSTICS_V2_CSV = PROJECT_ROOT / "data_prepared/routes/spine_route_turn_diagnostics.csv"
DELETED_ROUTE_CANDIDATES_CSV = PROJECT_ROOT / "data_prepared/routes/deleted_route_candidates.csv"
SPINE_ROUTE_IMPROVEMENT_TARGETS_CSV = PROJECT_ROOT / "data_prepared/routes/spine_route_improvement_targets.csv"
ACCIDENT_SCENARIOS_CSV = PROJECT_ROOT / "data_prepared/scenarios/accident_scenarios.csv"
EMERGENCY_ROUTE_SUMMARY = PROJECT_ROOT / "data_prepared/routes/emergency_route_summary.json"
EMERGENCY_ROUTE_V2_SUMMARY = PROJECT_ROOT / "data_prepared/routes/emergency_route_summary_spine_v2.json"
ROUTE_REVIEW_HTML = PROJECT_ROOT / "results/html/route_review.html"
ROUTE_REVIEW_V2_HTML = PROJECT_ROOT / "results/html/route_review_spine_v2.html"
ROUTE_REVIEW_SCHEMA = PROJECT_ROOT / "data_prepared/manual/route_review_decisions.schema.json"
ROUTE_REVIEW_DECISIONS = PROJECT_ROOT / "data_prepared/manual/route_review_decisions.json"
ROUTE_REVIEW_DECISIONS_SPINE = PROJECT_ROOT / "data_prepared/manual/route_review_decisions_spine.json"
PREFLIGHT_SUMMARY = PROJECT_ROOT / "data_prepared/preflight/preflight_summary.json"
PREFLIGHT_REPORT = PROJECT_ROOT / "data_prepared/preflight/preflight_report.csv"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step07_emergency_routes.log"
LOG_V2_PATH = PROJECT_ROOT / "outputs/logs/step07_spine_route_v2_plan.log"

V1_ACCEPT_ROUTE_IDS = {
    "ER_ACC_001",
    "ER_ACC_002",
    "ER_ACC_004",
    "ER_ACC_006",
    "ER_ACC_007",
    "ER_ACC_013",
    "ER_ACC_014",
    "ER_ACC_015",
    "ER_ACC_016",
    "ER_ACC_018",
}
V1_REJECT_REASONS = {
    "ER_ACC_003": "two_route_visual_confusion",
    "ER_ACC_005": "insufficient_spine_usage",
    "ER_ACC_008": "two_route_visual_confusion",
    "ER_ACC_009": "insufficient_spine_usage",
    "ER_ACC_010": "two_route_visual_confusion",
    "ER_ACC_011": "two_route_visual_confusion",
    "ER_ACC_012": "insufficient_spine_usage",
    "ER_ACC_017": "two_route_visual_confusion;uturn_or_direction_reversal",
    "ER_ACC_019": "uturn_or_direction_reversal",
    "ER_ACC_020": "deleted_candidate",
}
DELETED_ROUTE_ID = "ER_ACC_020"


class Step07Error(RuntimeError):
    """Expected Step 7 failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Step 7 emergency route artifacts.")
    parser.add_argument(
        "--variant",
        choices=["default", "spine-v2"],
        default="default",
        help="Generate legacy Step 7 outputs or separate spine route v2 outputs.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise Step07Error(f"JSON root must be object: {rel(path)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise Step07Error("PyYAML is required") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise Step07Error(f"YAML root must be mapping: {rel(path)}")
    return data


def load_edge_geojson(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, list[list[float]]]]:
    data = load_json(path)
    features = data.get("features")
    if data.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise Step07Error(f"Invalid edge GeoJSON: {rel(path)}")
    props_by_id: dict[str, dict[str, Any]] = {}
    coords_by_id: dict[str, list[list[float]]] = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        edge_id = props.get("edge_id") if isinstance(props, dict) else None
        if not isinstance(edge_id, str):
            continue
        props_by_id[edge_id] = props
        coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
        if isinstance(coords, list):
            coords_by_id[edge_id] = coords
    return props_by_id, coords_by_id


def edge_from_net(sumo_net: Any, edge_id: str) -> Any | None:
    try:
        return sumo_net.getEdge(edge_id)
    except Exception:  # noqa: BLE001
        return None


def route_tls_ids(route_edges: list[Any]) -> set[str]:
    tls_ids: set[str] = set()
    for from_edge, to_edge in zip(route_edges, route_edges[1:], strict=False):
        connections = from_edge.getOutgoing().get(to_edge, [])
        for connection in connections:
            if connection.getTLSID() and connection.getTLLinkIndex() >= 0:
                tls_ids.add(connection.getTLSID())
    return tls_ids


def route_length(sumo_net: Any, edge_ids: list[str]) -> float:
    return sum(edge_from_net(sumo_net, edge_id).getLength() for edge_id in edge_ids)


def route_objects(sumo_net: Any, edge_ids: list[str]) -> list[Any]:
    edges = []
    for edge_id in edge_ids:
        edge = edge_from_net(sumo_net, edge_id)
        if edge is None:
            raise Step07Error(f"Route edge missing in reduced net: {edge_id}")
        edges.append(edge)
    return edges


def shortest_route(sumo_net: Any, start_id: str, target_id: str) -> list[str]:
    start = edge_from_net(sumo_net, start_id)
    target = edge_from_net(sumo_net, target_id)
    if start is None or target is None:
        raise Step07Error(f"Missing route endpoint: {start_id}->{target_id}")
    edges, _cost = sumo_net.getOptimalPath(
        start,
        target,
        vClass="passenger",
        withInternal=False,
        includeFromToCost=True,
    )
    if not edges:
        raise Step07Error(f"No shortest route: {target_id}")
    return [edge.getID() for edge in edges]


def bearing_vector(config: dict[str, Any]) -> tuple[float, float]:
    locations = config["locations"]
    start = locations["jungbu_fire_station"]
    end = locations["seoul_station"]
    dx = float(end["lon"]) - float(start["lon"])
    dy = float(end["lat"]) - float(start["lat"])
    norm = math.hypot(dx, dy)
    if norm == 0:
        return (1.0, 0.0)
    return (dx / norm, dy / norm)


def axis_context(config: dict[str, Any]) -> dict[str, float]:
    locations = config["locations"]
    start = locations["jungbu_fire_station"]
    end = locations["seoul_station"]
    lat0 = math.radians((float(start["lat"]) + float(end["lat"])) / 2.0)
    scale_x = 111_320.0 * math.cos(lat0)
    scale_y = 111_320.0
    end_x = (float(end["lon"]) - float(start["lon"])) * scale_x
    end_y = (float(end["lat"]) - float(start["lat"])) * scale_y
    axis_len = math.hypot(end_x, end_y)
    if axis_len == 0:
        raise Step07Error("Analysis axis length is zero")
    return {
        "start_lon": float(start["lon"]),
        "start_lat": float(start["lat"]),
        "scale_x": scale_x,
        "scale_y": scale_y,
        "ux": end_x / axis_len,
        "uy": end_y / axis_len,
        "axis_len_m": axis_len,
    }


def local_xy(lon: float, lat: float, axis_ctx: dict[str, float]) -> tuple[float, float]:
    return (
        (lon - axis_ctx["start_lon"]) * axis_ctx["scale_x"],
        (lat - axis_ctx["start_lat"]) * axis_ctx["scale_y"],
    )


def coords_to_xy(coords: list[list[float]], axis_ctx: dict[str, float]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for coord in coords:
        if not isinstance(coord, list | tuple) or len(coord) < 2:
            continue
        points.append(local_xy(float(coord[0]), float(coord[1]), axis_ctx))
    return points


def point_distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def edge_centroid_xy(edge_id: str, coords_by_id: dict[str, list[list[float]]], axis_ctx: dict[str, float]) -> tuple[float, float] | None:
    points = coords_to_xy(coords_by_id.get(edge_id, []), axis_ctx)
    if not points:
        return None
    return (sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points))


def point_to_edge_distance_m(
    source_edge_id: str,
    target_edge_id: str,
    coords_by_id: dict[str, list[list[float]]],
    axis_ctx: dict[str, float],
) -> float | str:
    source_points = coords_to_xy(coords_by_id.get(source_edge_id, []), axis_ctx)
    target_centroid = edge_centroid_xy(target_edge_id, coords_by_id, axis_ctx)
    if not source_points or target_centroid is None:
        return ""
    return point_distance_m(source_points[-1], target_centroid)


def edge_axis_metrics(edge_id: str, coords_by_id: dict[str, list[list[float]]], axis_ctx: dict[str, float]) -> dict[str, float]:
    coords = coords_by_id.get(edge_id, [])
    if len(coords) < 2:
        return {"axis_alignment": 0.0, "axis_distance_m": float("inf"), "axis_position": 0.0}
    points = [local_xy(float(lon), float(lat), axis_ctx) for lon, lat in coords]
    ux = axis_ctx["ux"]
    uy = axis_ctx["uy"]
    axis_len = axis_ctx["axis_len_m"]
    projections = [(x * ux + y * uy) / axis_len for x, y in points]
    distances = [abs(x * uy - y * ux) for x, y in points]
    dx = points[-1][0] - points[0][0]
    dy = points[-1][1] - points[0][1]
    norm = math.hypot(dx, dy)
    alignment = abs((dx / norm) * ux + (dy / norm) * uy) if norm else 0.0
    return {
        "axis_alignment": alignment,
        "axis_distance_m": sum(distances) / len(distances),
        "axis_position": sum(projections) / len(projections),
    }


def edge_alignment(edge_id: str, coords_by_id: dict[str, list[list[float]]], axis: tuple[float, float]) -> float:
    coords = coords_by_id.get(edge_id, [])
    if len(coords) < 2:
        return 0.0
    dx = float(coords[-1][0]) - float(coords[0][0])
    dy = float(coords[-1][1]) - float(coords[0][1])
    norm = math.hypot(dx, dy)
    if norm == 0:
        return 0.0
    return abs((dx / norm) * axis[0] + (dy / norm) * axis[1])


def major_edge_cost(
    edge: Any,
    props_by_id: dict[str, dict[str, Any]],
    coords_by_id: dict[str, list[list[float]]],
    axis: tuple[float, float],
) -> float:
    edge_id = edge.getID()
    props = props_by_id.get(edge_id, {})
    length = max(float(edge.getLength()), 1.0)
    lane_count = max(float(props.get("lane_count") or edge.getLaneNumber() or 1), 1.0)
    speed = max(float(props.get("speed_mps") or edge.getSpeed() or 1.0), 1.0)
    priority = max(float(props.get("priority") or edge.getPriority() or 1), 1.0)
    alignment = edge_alignment(edge_id, coords_by_id, axis)
    lane_factor = 1.0 / (1.0 + 0.18 * min(lane_count - 1.0, 4.0))
    speed_factor = math.sqrt(13.89 / speed)
    priority_factor = 1.0 / (1.0 + 0.05 * min(priority, 10.0))
    continuous_factor = 0.85 if length >= 150 else (0.92 if length >= 80 else 1.0)
    alignment_factor = 1.0 - 0.16 * alignment
    return max(length * lane_factor * speed_factor * priority_factor * continuous_factor * alignment_factor, 0.1)


def major_route(
    sumo_net: Any,
    start_id: str,
    target_id: str,
    props_by_id: dict[str, dict[str, Any]],
    coords_by_id: dict[str, list[list[float]]],
    axis: tuple[float, float],
) -> list[str]:
    start = edge_from_net(sumo_net, start_id)
    target = edge_from_net(sumo_net, target_id)
    if start is None or target is None:
        raise Step07Error(f"Missing major route endpoint: {start_id}->{target_id}")
    heap: list[tuple[float, int, Any]] = [(major_edge_cost(start, props_by_id, coords_by_id, axis), 0, start)]
    costs = {start: heap[0][0]}
    previous: dict[Any, Any] = {}
    seen_counter = 1
    visited: set[Any] = set()
    while heap:
        cost, _counter, edge = heapq.heappop(heap)
        if edge in visited:
            continue
        visited.add(edge)
        if edge == target:
            break
        for next_edge in edge.getOutgoing():
            if not next_edge.allows("passenger") or next_edge.isSpecial():
                continue
            next_cost = cost + major_edge_cost(next_edge, props_by_id, coords_by_id, axis)
            if next_cost < costs.get(next_edge, float("inf")):
                costs[next_edge] = next_cost
                previous[next_edge] = edge
                heapq.heappush(heap, (next_cost, seen_counter, next_edge))
                seen_counter += 1
    if target not in costs:
        raise Step07Error(f"No major-road route: {target_id}")
    path = [target]
    while path[-1] != start:
        path.append(previous[path[-1]])
    path.reverse()
    return [edge.getID() for edge in path]


def numeric_prop(props: dict[str, Any], key: str, fallback: float) -> float:
    value = props.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def build_spine_edges(
    sumo_net: Any,
    props_by_id: dict[str, dict[str, Any]],
    coords_by_id: dict[str, list[list[float]]],
    axis_ctx: dict[str, float],
) -> tuple[list[dict[str, Any]], set[str], dict[str, dict[str, float]]]:
    rows: list[dict[str, Any]] = []
    spine_ids: set[str] = set()
    metrics_by_id: dict[str, dict[str, float]] = {}
    for edge_id, props in props_by_id.items():
        edge = edge_from_net(sumo_net, edge_id)
        length = numeric_prop(props, "length_m", float(edge.getLength()) if edge is not None else 0.0)
        lane_count = numeric_prop(props, "lane_count", float(edge.getLaneNumber()) if edge is not None else 1.0)
        speed_mps = numeric_prop(props, "speed_mps", float(edge.getSpeed()) if edge is not None else 0.0)
        priority = numeric_prop(props, "priority", float(edge.getPriority()) if edge is not None else 0.0)
        axis_metrics = edge_axis_metrics(edge_id, coords_by_id, axis_ctx)
        axis_alignment = axis_metrics["axis_alignment"]
        axis_distance_m = axis_metrics["axis_distance_m"]
        axis_position = axis_metrics["axis_position"]
        eligible = (
            edge is not None
            and bool(props.get("allows_passenger"))
            and not bool(props.get("is_internal"))
            and length >= SPINE_MIN_EDGE_LENGTH_M
            and -0.08 <= axis_position <= 1.08
            and axis_distance_m <= SPINE_BUFFER_M
        )
        lane_score = min(max((lane_count - 1.0) / 2.0, 0.0), 1.0)
        speed_score = min(max((speed_mps - 11.0) / 11.0, 0.0), 1.0)
        priority_score = min(max((priority - 3.0) / 4.0, 0.0), 1.0)
        length_score = min(max(length / 120.0, 0.0), 1.0)
        distance_score = max(0.0, 1.0 - axis_distance_m / SPINE_BUFFER_M) if math.isfinite(axis_distance_m) else 0.0
        spine_score = (
            0.25 * lane_score
            + 0.20 * speed_score
            + 0.20 * priority_score
            + 0.20 * axis_alignment
            + 0.10 * length_score
            + 0.05 * distance_score
        )
        is_spine = eligible and spine_score >= SPINE_SCORE_THRESHOLD
        if is_spine:
            spine_ids.add(edge_id)
        metrics_by_id[edge_id] = {
            "axis_alignment": axis_alignment,
            "axis_distance_m": axis_distance_m,
            "axis_position": axis_position,
            "spine_score": spine_score,
        }
        rows.append(
            {
                "edge_id": edge_id,
                "length_m": round(length, 3),
                "lane_count": lane_count,
                "speed_mps": round(speed_mps, 3),
                "priority": priority,
                "axis_alignment": round(axis_alignment, 6),
                "spine_score": round(spine_score, 6),
                "is_spine_edge": is_spine,
            }
        )
    rows.sort(key=lambda row: (not bool(row["is_spine_edge"]), -float(row["spine_score"]), row["edge_id"]))
    if not spine_ids:
        raise Step07Error("No corridor spine edges were selected")
    return rows, spine_ids, metrics_by_id


def spine_edge_cost(
    edge: Any,
    props_by_id: dict[str, dict[str, Any]],
    coords_by_id: dict[str, list[list[float]]],
    axis: tuple[float, float],
    spine_ids: set[str],
    spine_metrics: dict[str, dict[str, float]],
    bias_strength: float,
) -> float:
    base = major_edge_cost(edge, props_by_id, coords_by_id, axis)
    edge_id = edge.getID()
    metrics = spine_metrics.get(edge_id, {})
    if edge_id in spine_ids:
        score = min(max(float(metrics.get("spine_score", 0.0)), 0.0), 1.0)
        discount = 1.0 - bias_strength * (0.45 + 0.45 * score)
        return max(base * discount, 0.05)
    axis_distance = float(metrics.get("axis_distance_m", SPINE_BUFFER_M + 1.0))
    axis_position = float(metrics.get("axis_position", 0.0))
    in_corridor = -0.08 <= axis_position <= 1.08 and axis_distance <= SPINE_BUFFER_M
    penalty = 1.0 + bias_strength * (0.20 if in_corridor else 0.08)
    return max(base * penalty, 0.05)


def weighted_route(
    sumo_net: Any,
    start_id: str,
    target_id: str,
    cost_func: Any,
) -> list[str]:
    start = edge_from_net(sumo_net, start_id)
    target = edge_from_net(sumo_net, target_id)
    if start is None or target is None:
        raise Step07Error(f"Missing weighted route endpoint: {start_id}->{target_id}")
    heap: list[tuple[float, int, Any]] = [(cost_func(start), 0, start)]
    costs = {start: heap[0][0]}
    previous: dict[Any, Any] = {}
    seen_counter = 1
    visited: set[Any] = set()
    while heap:
        cost, _counter, edge = heapq.heappop(heap)
        if edge in visited:
            continue
        visited.add(edge)
        if edge == target:
            break
        for next_edge in edge.getOutgoing():
            if not next_edge.allows("passenger") or next_edge.isSpecial():
                continue
            next_cost = cost + cost_func(next_edge)
            if next_cost < costs.get(next_edge, float("inf")):
                costs[next_edge] = next_cost
                previous[next_edge] = edge
                heapq.heappush(heap, (next_cost, seen_counter, next_edge))
                seen_counter += 1
    if target not in costs:
        raise Step07Error(f"No weighted route: {target_id}")
    path = [target]
    while path[-1] != start:
        path.append(previous[path[-1]])
    path.reverse()
    return [edge.getID() for edge in path]


def edge_heading(edge_id: str, coords_by_id: dict[str, list[list[float]]], axis_ctx: dict[str, float]) -> float | None:
    points = coords_to_xy(coords_by_id.get(edge_id, []), axis_ctx)
    if len(points) < 2:
        return None
    for start, end in zip(reversed(points[:-1]), reversed(points[1:]), strict=False):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        if math.hypot(dx, dy) > 0.5:
            return math.degrees(math.atan2(dy, dx))
    return None


def heading_change_deg(from_edge_id: str, to_edge_id: str, coords_by_id: dict[str, list[list[float]]], axis_ctx: dict[str, float]) -> float:
    from_heading = edge_heading(from_edge_id, coords_by_id, axis_ctx)
    to_heading = edge_heading(to_edge_id, coords_by_id, axis_ctx)
    if from_heading is None or to_heading is None:
        return 0.0
    delta = abs((to_heading - from_heading + 180.0) % 360.0 - 180.0)
    return delta


def turn_transition_penalty(from_edge_id: str, to_edge_id: str, coords_by_id: dict[str, list[list[float]]], axis_ctx: dict[str, float]) -> float:
    angle = heading_change_deg(from_edge_id, to_edge_id, coords_by_id, axis_ctx)
    if angle >= UTURN_LIKE_ANGLE_DEG:
        return 1800.0 + 35.0 * (angle - UTURN_LIKE_ANGLE_DEG)
    if angle >= SHARP_TURN_ANGLE_DEG:
        return 420.0 + 8.0 * (angle - SHARP_TURN_ANGLE_DEG)
    return 0.0


def weighted_route_v2(
    sumo_net: Any,
    start_id: str,
    target_id: str,
    cost_func: Any,
    coords_by_id: dict[str, list[list[float]]],
    axis_ctx: dict[str, float],
) -> list[str]:
    start = edge_from_net(sumo_net, start_id)
    target = edge_from_net(sumo_net, target_id)
    if start is None or target is None:
        raise Step07Error(f"Missing weighted route endpoint: {start_id}->{target_id}")
    start_state = ("", start)
    heap: list[tuple[float, int, tuple[str, Any]]] = [(cost_func(start), 0, start_state)]
    costs = {start_state: heap[0][0]}
    previous: dict[tuple[str, Any], tuple[str, Any]] = {}
    seen_counter = 1
    best_target_state: tuple[str, Any] | None = None
    while heap:
        cost, _counter, state = heapq.heappop(heap)
        if cost != costs.get(state):
            continue
        _prev_id, edge = state
        if edge == target:
            best_target_state = state
            break
        for next_edge in edge.getOutgoing():
            if not next_edge.allows("passenger") or next_edge.isSpecial():
                continue
            next_state = (edge.getID(), next_edge)
            turn_penalty = turn_transition_penalty(edge.getID(), next_edge.getID(), coords_by_id, axis_ctx)
            next_cost = cost + cost_func(next_edge) + turn_penalty
            if next_cost < costs.get(next_state, float("inf")):
                costs[next_state] = next_cost
                previous[next_state] = state
                heapq.heappush(heap, (next_cost, seen_counter, next_state))
                seen_counter += 1
    if best_target_state is None:
        raise Step07Error(f"No weighted route: {target_id}")
    states = [best_target_state]
    while states[-1] != start_state:
        states.append(previous[states[-1]])
    states.reverse()
    return [state[1].getID() for state in states]


def dedupe_route_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    unique = []
    for candidate in candidates:
        key = tuple(candidate["edge_ids"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def route_spine_metrics(
    sumo_net: Any,
    edge_ids: list[str],
    shortest_length: float,
    spine_ids: set[str],
    spine_metrics: dict[str, dict[str, float]],
    coords_by_id: dict[str, list[list[float]]] | None = None,
    axis_ctx: dict[str, float] | None = None,
    target_edge_id: str | None = None,
) -> dict[str, Any]:
    total_length = route_length(sumo_net, edge_ids)
    spine_edge_count = 0
    spine_length = 0.0
    current_consecutive = 0.0
    max_consecutive = 0.0
    positions: list[float] = []
    last_spine_edge_id = ""
    for edge_id in edge_ids:
        length = edge_from_net(sumo_net, edge_id).getLength()
        if edge_id in spine_ids:
            spine_edge_count += 1
            spine_length += length
            current_consecutive += length
            max_consecutive = max(max_consecutive, current_consecutive)
            positions.append(float(spine_metrics.get(edge_id, {}).get("axis_position", 0.0)))
            last_spine_edge_id = edge_id
        else:
            current_consecutive = 0.0
    increase_ratio = (total_length - shortest_length) / shortest_length if shortest_length else 0.0
    spine_ratio = spine_length / total_length if total_length else 0.0
    distance_to_target_at_spine_exit = ""
    if coords_by_id is not None and axis_ctx is not None and target_edge_id and last_spine_edge_id:
        distance_to_target_at_spine_exit = point_to_edge_distance_m(last_spine_edge_id, target_edge_id, coords_by_id, axis_ctx)
    return {
        "route_length_m": total_length,
        "length_increase_ratio": increase_ratio,
        "spine_edge_count": spine_edge_count,
        "spine_length_m": spine_length,
        "spine_length_ratio": spine_ratio,
        "max_consecutive_spine_length_m": max_consecutive,
        "spine_entry_position": positions[0] if positions else "",
        "spine_exit_position": positions[-1] if positions else "",
        "spine_entry_position_ratio": positions[0] if positions else "",
        "spine_exit_position_ratio": positions[-1] if positions else "",
        "distance_to_target_at_spine_exit_m": distance_to_target_at_spine_exit,
    }


def select_spine_route(
    sumo_net: Any,
    start_id: str,
    target_id: str,
    shortest_ids: list[str],
    major_ids: list[str],
    props_by_id: dict[str, dict[str, Any]],
    coords_by_id: dict[str, list[list[float]]],
    axis: tuple[float, float],
    spine_ids: set[str],
    spine_metrics: dict[str, dict[str, float]],
) -> dict[str, Any]:
    shortest_length = route_length(sumo_net, shortest_ids)
    candidates: list[dict[str, Any]] = []
    for label, edge_ids in [("shortest", shortest_ids), ("major", major_ids)]:
        metrics = route_spine_metrics(sumo_net, edge_ids, shortest_length, spine_ids, spine_metrics)
        candidates.append({"candidate_policy": label, "bias_strength": "", "edge_ids": edge_ids, **metrics})
    for bias_strength in SPINE_BIAS_STRENGTHS:
        edge_ids = weighted_route(
            sumo_net,
            start_id,
            target_id,
            lambda edge, strength=bias_strength: spine_edge_cost(edge, props_by_id, coords_by_id, axis, spine_ids, spine_metrics, strength),
        )
        metrics = route_spine_metrics(sumo_net, edge_ids, shortest_length, spine_ids, spine_metrics)
        candidates.append({"candidate_policy": "spine", "bias_strength": bias_strength, "edge_ids": edge_ids, **metrics})

    unique_candidates = dedupe_route_candidates(candidates)
    for candidate in unique_candidates:
        increase_ratio = float(candidate["length_increase_ratio"])
        candidate["selection_score"] = (
            100.0 * float(candidate["spine_length_ratio"])
            + 0.05 * min(float(candidate["max_consecutive_spine_length_m"]), 1500.0)
            - 45.0 * max(increase_ratio, 0.0)
            - 20.0 * max(increase_ratio - SPINE_LENGTH_WARNING_RATIO, 0.0)
            - (8.0 if candidate["candidate_policy"] != "spine" else 0.0)
        )
    selected = max(unique_candidates, key=lambda candidate: candidate["selection_score"])
    selected["candidate_count"] = len(unique_candidates)
    return selected


def route_geometry_diagnostics(edge_ids: list[str], coords_by_id: dict[str, list[list[float]]], axis_ctx: dict[str, float]) -> dict[str, Any]:
    segment_count = 0
    gap_count = 0
    max_gap = 0.0
    previous_end: tuple[float, float] | None = None
    for edge_id in edge_ids:
        points = coords_to_xy(coords_by_id.get(edge_id, []), axis_ctx)
        if len(points) < 2:
            continue
        if previous_end is None:
            segment_count = 1
        else:
            gap = point_distance_m(previous_end, points[0])
            max_gap = max(max_gap, gap)
            if gap > TURN_GAP_THRESHOLD_M:
                gap_count += 1
                segment_count += 1
        previous_end = points[-1]
    repeated_edge_count = len(edge_ids) - len(set(edge_ids))
    return {
        "segment_count": segment_count,
        "gap_count": gap_count,
        "max_gap_distance_m": max_gap,
        "repeated_edge_count": repeated_edge_count,
        "route_geometry_continuous": gap_count == 0,
        "visual_confusion_check": "geometry_gap_or_repeat" if gap_count or repeated_edge_count else "layer_overlap_likely",
    }


def route_turn_diagnostics(
    route_id: str,
    edge_ids: list[str],
    coords_by_id: dict[str, list[list[float]]],
    axis_ctx: dict[str, float],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    max_turn_angle = 0.0
    worst_from = ""
    worst_to = ""
    sharp_turn_count = 0
    uturn_like_count = 0
    rows: list[dict[str, Any]] = []
    for index, (from_id, to_id) in enumerate(zip(edge_ids, edge_ids[1:], strict=False), start=1):
        angle = heading_change_deg(from_id, to_id, coords_by_id, axis_ctx)
        turn_class = "normal"
        if angle >= UTURN_LIKE_ANGLE_DEG:
            turn_class = "uturn_like"
            uturn_like_count += 1
        elif angle >= SHARP_TURN_ANGLE_DEG:
            turn_class = "sharp_turn"
            sharp_turn_count += 1
        if angle > max_turn_angle:
            max_turn_angle = angle
            worst_from = from_id
            worst_to = to_id
        rows.append(
            {
                "route_id": route_id,
                "transition_index": index,
                "from_edge_id": from_id,
                "to_edge_id": to_id,
                "turn_angle_deg": round(angle, 3),
                "turn_class": turn_class,
            }
        )
    return (
        {
            "max_turn_angle": max_turn_angle,
            "sharp_turn_count": sharp_turn_count,
            "uturn_like_transition_count": uturn_like_count,
            "worst_turn_from_edge": worst_from,
            "worst_turn_to_edge": worst_to,
        },
        rows,
    )


def select_spine_route_v2(
    sumo_net: Any,
    start_id: str,
    target_id: str,
    shortest_ids: list[str],
    major_ids: list[str],
    props_by_id: dict[str, dict[str, Any]],
    coords_by_id: dict[str, list[list[float]]],
    axis: tuple[float, float],
    axis_ctx: dict[str, float],
    spine_ids: set[str],
    spine_metrics: dict[str, dict[str, float]],
) -> dict[str, Any]:
    shortest_length = route_length(sumo_net, shortest_ids)
    candidates: list[dict[str, Any]] = []
    for label, edge_ids in [("shortest", shortest_ids), ("major", major_ids)]:
        metrics = route_spine_metrics(sumo_net, edge_ids, shortest_length, spine_ids, spine_metrics, coords_by_id, axis_ctx, target_id)
        turn_summary, _turn_rows = route_turn_diagnostics("", edge_ids, coords_by_id, axis_ctx)
        candidates.append({"candidate_policy": label, "bias_strength": "", "edge_ids": edge_ids, **metrics, **turn_summary})
    for bias_strength in SPINE_V2_BIAS_STRENGTHS:
        edge_ids = weighted_route_v2(
            sumo_net,
            start_id,
            target_id,
            lambda edge, strength=bias_strength: spine_edge_cost(edge, props_by_id, coords_by_id, axis, spine_ids, spine_metrics, strength),
            coords_by_id,
            axis_ctx,
        )
        metrics = route_spine_metrics(sumo_net, edge_ids, shortest_length, spine_ids, spine_metrics, coords_by_id, axis_ctx, target_id)
        turn_summary, _turn_rows = route_turn_diagnostics("", edge_ids, coords_by_id, axis_ctx)
        candidates.append({"candidate_policy": "spine_v2", "bias_strength": bias_strength, "edge_ids": edge_ids, **metrics, **turn_summary})

    unique_candidates = dedupe_route_candidates(candidates)
    for candidate in unique_candidates:
        increase_ratio = float(candidate["length_increase_ratio"])
        spine_ratio = float(candidate["spine_length_ratio"])
        max_consecutive = float(candidate["max_consecutive_spine_length_m"])
        distance_to_target = candidate.get("distance_to_target_at_spine_exit_m")
        if distance_to_target == "":
            distance_to_target_bonus = 0.0
        else:
            distance_to_target_bonus = max(0.0, 30.0 * (1.0 - min(float(distance_to_target), 900.0) / 900.0))
        early_exit_penalty = 0.0
        if max_consecutive < 450.0:
            early_exit_penalty += (450.0 - max_consecutive) * 0.08
        if spine_ratio < 0.35:
            early_exit_penalty += (0.35 - spine_ratio) * 90.0
        turn_penalty = 22.0 * int(candidate["sharp_turn_count"]) + 85.0 * int(candidate["uturn_like_transition_count"])
        candidate["selection_score"] = (
            145.0 * spine_ratio
            + 0.095 * min(max_consecutive, 1800.0)
            + distance_to_target_bonus
            - 42.0 * max(increase_ratio, 0.0)
            - 34.0 * max(increase_ratio - SPINE_LENGTH_WARNING_RATIO, 0.0)
            - early_exit_penalty
            - turn_penalty
            - (10.0 if candidate["candidate_policy"] != "spine_v2" else 0.0)
        )
    selected = max(unique_candidates, key=lambda candidate: candidate["selection_score"])
    selected["candidate_count"] = len(unique_candidates)
    return selected


def route_review_status(metrics: dict[str, Any]) -> tuple[str, str, bool]:
    warnings: list[str] = []
    needs_manual_review = False
    increase_ratio = float(metrics["length_increase_ratio"])
    spine_ratio = float(metrics["spine_length_ratio"])
    max_consecutive = float(metrics["max_consecutive_spine_length_m"])
    if increase_ratio > SPINE_MANUAL_REVIEW_RATIO:
        warnings.append(f"spine_route_length_gt_{SPINE_MANUAL_REVIEW_RATIO:.0%}_over_shortest")
        needs_manual_review = True
    elif increase_ratio > SPINE_LENGTH_WARNING_RATIO:
        warnings.append(f"spine_route_length_gt_{SPINE_LENGTH_WARNING_RATIO:.0%}_over_shortest")
    if spine_ratio < SPINE_MIN_LENGTH_RATIO:
        warnings.append(f"spine_length_ratio_lt_{SPINE_MIN_LENGTH_RATIO:.0%}")
        needs_manual_review = True
    if max_consecutive < SPINE_MIN_CONSECUTIVE_LENGTH_M:
        warnings.append(f"max_consecutive_spine_lt_{SPINE_MIN_CONSECUTIVE_LENGTH_M:.0f}m")
        needs_manual_review = True
    if needs_manual_review:
        return "needs_manual_review", ";".join(warnings), True
    if warnings:
        return "WARNING", ";".join(warnings), False
    return "PASS", "", False


def route_review_status_v2(metrics: dict[str, Any], geometry: dict[str, Any]) -> tuple[str, str, bool, bool]:
    warnings: list[str] = []
    needs_manual_review = False
    increase_ratio = float(metrics["length_increase_ratio"])
    spine_ratio = float(metrics["spine_length_ratio"])
    max_consecutive = float(metrics["max_consecutive_spine_length_m"])
    length_detour_warning = increase_ratio > SPINE_LENGTH_WARNING_RATIO
    if increase_ratio > SPINE_MANUAL_REVIEW_RATIO:
        warnings.append(f"spine_route_length_gt_{SPINE_MANUAL_REVIEW_RATIO:.0%}_over_shortest")
        needs_manual_review = True
    elif length_detour_warning:
        warnings.append(f"spine_route_length_gt_{SPINE_LENGTH_WARNING_RATIO:.0%}_over_shortest")
    if spine_ratio < SPINE_MIN_LENGTH_RATIO:
        warnings.append(f"spine_length_ratio_lt_{SPINE_MIN_LENGTH_RATIO:.0%}")
        needs_manual_review = True
    if max_consecutive < SPINE_MIN_CONSECUTIVE_LENGTH_M:
        warnings.append(f"max_consecutive_spine_lt_{SPINE_MIN_CONSECUTIVE_LENGTH_M:.0f}m")
        needs_manual_review = True
    if int(metrics.get("uturn_like_transition_count", 0)) > 0:
        warnings.append("uturn_like_transition_present")
        needs_manual_review = True
    if int(metrics.get("sharp_turn_count", 0)) > 0:
        warnings.append("sharp_turn_present")
    if int(geometry.get("gap_count", 0)) > 0:
        warnings.append("route_geometry_gap_present")
    if int(geometry.get("repeated_edge_count", 0)) > 0:
        warnings.append("repeated_edge_present")
        needs_manual_review = True
    if needs_manual_review:
        return "needs_manual_review", ";".join(warnings), True, length_detour_warning
    if warnings:
        return "WARNING", ";".join(warnings), False, length_detour_warning
    return "PASS", "", False, length_detour_warning


def route_coords(edge_ids: list[str], coords_by_id: dict[str, list[list[float]]]) -> list[list[list[float]]]:
    return [coords_by_id[edge_id] for edge_id in edge_ids if edge_id in coords_by_id]


def write_route_xml(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("routes")
    ET.SubElement(
        root,
        "vType",
        {
            "id": "emergency",
            "vClass": "emergency",
            "color": "1,0,0",
            "guiShape": "emergency",
        },
    )
    for row in rows:
        ET.SubElement(root, "route", {"id": row["route_id"], "edges": row["route_edges"]})
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_route_review_html(path: Path, review_data: dict[str, Any]) -> None:
    data = json.dumps(review_data, ensure_ascii=False)
    escaped_data = data.replace("</", "<\\/")
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Route Review</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#1f2937; }}
    .app {{ display:grid; grid-template-columns:380px 1fr; height:100vh; }}
    aside {{ overflow:auto; border-right:1px solid #d7dce5; padding:14px; background:#f7f8fb; }}
    #map {{ height:100vh; background:#e5e7eb; }}
    .leaflet-container {{ overflow: hidden; }}
    .leaflet-pane,
    .leaflet-tile,
    .leaflet-marker-icon,
    .leaflet-marker-shadow,
    .leaflet-tile-container,
    .leaflet-pane > svg,
    .leaflet-pane > canvas,
    .leaflet-zoom-box,
    .leaflet-image-layer,
    .leaflet-layer {{
      position: absolute;
      left: 0;
      top: 0;
    }}
    .leaflet-container img {{ max-width: none !important; max-height: none !important; }}
    .leaflet-tile {{ width: 256px; height: 256px; }}
    .route {{ border:1px solid #d7dce5; background:white; margin:0 0 10px; padding:10px; border-radius:6px; transition:border-color .12s ease, box-shadow .12s ease, transform .12s ease; }}
    .route:hover {{ border-color:#2563eb; box-shadow:0 0 0 2px rgba(37,99,235,.12); }}
    .route.active {{ border-color:#2563eb; box-shadow:0 0 0 2px rgba(37,99,235,.18); }}
    .route.accepted {{ border-color:#12805c !important; box-shadow:0 0 0 2px rgba(18,128,92,.18); }}
    .route.rejected {{ border-color:#c2410c !important; box-shadow:0 0 0 2px rgba(194,65,12,.18); }}
    .route-title {{ display:flex; align-items:center; gap:7px; margin-bottom:4px; }}
    .route-swatch {{ width:12px; height:12px; border-radius:3px; flex:0 0 12px; box-shadow:0 0 0 1px rgba(0,0,0,.12) inset; }}
    .decision-badge {{ margin-left:auto; border-radius:999px; padding:2px 7px; font-size:11px; line-height:1.4; background:#eef2f7; color:#475467; }}
    .decision-badge.accepted {{ background:#dcfce7; color:#166534; }}
    .decision-badge.rejected {{ background:#ffedd5; color:#9a3412; }}
    .meta {{ font-size:12px; color:#667085; line-height:1.5; }}
    button {{ margin:4px 4px 4px 0; border:1px solid #cfd6e2; background:white; border-radius:6px; padding:6px 8px; cursor:pointer; }}
    button.primary {{ background:#2563eb; color:white; border-color:#2563eb; }}
    button.selected[data-action="accept"] {{ background:#12805c; border-color:#12805c; color:white; }}
    button.selected[data-action="reject"] {{ background:#c2410c; border-color:#c2410c; color:white; }}
    textarea {{ width:100%; min-height:44px; box-sizing:border-box; margin-top:6px; }}
    .warn {{ color:#b45309; font-weight:600; }}
    .status-panel {{ border:1px solid #d7dce5; background:#fff; border-radius:6px; padding:10px; margin:10px 0; font-size:12px; line-height:1.5; white-space:pre-wrap; }}
    .status-panel strong {{ display:block; margin-bottom:4px; }}
    @media (max-width: 900px) {{ .app {{ grid-template-columns:1fr; grid-template-rows:46vh 54vh; }} aside {{ grid-row:2; }} #map {{ grid-row:1; height:46vh; }} }}
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h2>Route Review</h2>
      <p class="meta">Each route has its own color. Solid=spine-corridor route, semi-transparent=major-road-biased, dashed gray=shortest.</p>
      <button class="primary" id="download">Download decisions</button>
      <div class="status-panel"><strong>Map status</strong><span id="map-status">Initializing...</span></div>
      <div id="routes"></div>
    </aside>
    <main id="map"></main>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const ROUTE_REVIEW_DATA = {escaped_data};
    const decisions = {{}};
    const FALLBACK_CENTER = [37.5616815, 126.9934095];
    const mapStatus = document.getElementById('map-status');
    const map = L.map('map').setView(FALLBACK_CENTER, 14);
    const allPolylines = [];
    const allBounds = L.latLngBounds([]);
    const ROUTE_COLORS = [
      '#2563eb', '#dc2626', '#16a34a', '#9333ea', '#ea580c',
      '#0891b2', '#be123c', '#4f46e5', '#65a30d', '#c026d3',
      '#0f766e', '#b45309', '#7c3aed', '#0284c7', '#db2777',
      '#ca8a04', '#059669', '#e11d48', '#475569', '#f97316'
    ];
    const statusState = {{
      routeCount: ROUTE_REVIEW_DATA.routes.length,
      majorSegmentCount: 0,
      shortestSegmentCount: 0,
      spineSegmentCount: 0,
      firstCoordinate: null,
      boundsValid: false,
      layerCount: 0,
      tileStatus: 'loading',
      highlightedRoute: 'none',
      lastError: '',
    }};
    function renderStatus() {{
      const center = map.getCenter();
      const size = document.getElementById('map').getBoundingClientRect();
      mapStatus.textContent = [
        `route count: ${{statusState.routeCount}}`,
        `major segments: ${{statusState.majorSegmentCount}}`,
        `shortest segments: ${{statusState.shortestSegmentCount}}`,
        `spine segments: ${{statusState.spineSegmentCount}}`,
        `first coordinate [lon, lat]: ${{JSON.stringify(statusState.firstCoordinate)}}`,
        `bounds valid: ${{statusState.boundsValid}}`,
        `route layers: ${{statusState.layerCount}}`,
        `highlighted route: ${{statusState.highlightedRoute}}`,
        `tile status: ${{statusState.tileStatus}}`,
        `map center/zoom: ${{center.lat.toFixed(6)}}, ${{center.lng.toFixed(6)}} / ${{map.getZoom()}}`,
        `map size: ${{Math.round(size.width)}}x${{Math.round(size.height)}}`,
        `last JS error: ${{statusState.lastError || 'none'}}`,
      ].join('\\n');
    }}
    window.onerror = (_message, _source, _line, _column, error) => {{
      statusState.lastError = error && error.message ? error.message : String(_message);
      renderStatus();
    }};
    window.onunhandledrejection = event => {{
      statusState.lastError = event.reason && event.reason.message ? event.reason.message : String(event.reason);
      renderStatus();
    }};
    const tiles = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 19 }});
    tiles.on('load', () => {{ statusState.tileStatus = 'loaded'; renderStatus(); }});
    tiles.on('tileerror', () => {{ statusState.tileStatus = 'tile error; route layers still available'; renderStatus(); }});
    tiles.addTo(map);
    const layers = new Map();
    let selectedRouteId = null;
    let hoveredRouteId = null;
    window.ROUTE_REVIEW_DATA = ROUTE_REVIEW_DATA;
    window.map = map;
    window.layers = layers;
    window.ROUTE_COLORS = ROUTE_COLORS;
    function latlngs(segments) {{
      return segments
        .map(seg => seg.map(p => [p[1], p[0]]))
        .filter(seg => seg.length >= 2 && seg.every(p => Number.isFinite(p[0]) && Number.isFinite(p[1])));
    }}
    function routeColor(index) {{
      return ROUTE_COLORS[index % ROUTE_COLORS.length];
    }}
    function styleLine(line, entry, isActive, hasActive) {{
      const kind = line.options.routeKind;
      let style;
      if (kind === 'spine') {{
        style = {{ color: entry.color, weight: isActive ? 10 : (hasActive ? 4 : 7), opacity: isActive ? 1 : (hasActive ? .28 : .9) }};
      }} else if (kind === 'major') {{
        style = {{ color: entry.color, weight: isActive ? 6 : (hasActive ? 2.5 : 4), opacity: isActive ? .72 : (hasActive ? .12 : .38) }};
      }} else {{
        style = {{ color: '#6b7280', weight: isActive ? 4 : (hasActive ? 1.5 : 2.5), opacity: isActive ? .65 : (hasActive ? .06 : .35), dashArray:'7 5' }};
      }}
      line.setStyle(style);
    }}
    function applyRouteHighlight(fitToRoute = false) {{
      const routeId = hoveredRouteId || selectedRouteId;
      statusState.highlightedRoute = routeId || 'none';
      document.querySelectorAll('.route').forEach(el => el.classList.toggle('active', Boolean(routeId) && el.dataset.routeId === routeId));
      const hasActive = Boolean(routeId);
      layers.forEach((entry, id) => {{
        const isActive = id === routeId;
        entry.lines.forEach(line => styleLine(line, entry, isActive, hasActive));
        if (isActive && entry.group.bringToFront) entry.group.bringToFront();
      }});
      if (fitToRoute && routeId) {{
        const entry = layers.get(routeId);
        const groupBounds = L.latLngBounds([]);
        if (entry) {{
          entry.lines.forEach(line => {{
            if (line.getBounds) groupBounds.extend(line.getBounds());
          }});
        }}
        if (groupBounds.isValid()) {{
          map.invalidateSize();
          map.fitBounds(groupBounds, {{padding:[20,20]}});
        }}
      }}
      renderStatus();
    }}
    function setRouteHighlight(routeId, fitToRoute = false) {{
      selectedRouteId = routeId;
      hoveredRouteId = null;
      applyRouteHighlight(fitToRoute);
    }}
    function setRouteHover(routeId) {{
      hoveredRouteId = routeId;
      applyRouteHighlight(false);
    }}
    window.setRouteHighlight = setRouteHighlight;
    window.setRouteHover = setRouteHover;
    function addRoute(route, index) {{
      const group = L.layerGroup();
      const color = routeColor(index);
      const lines = [];
      statusState.shortestSegmentCount += route.shortest_segments.length;
      statusState.majorSegmentCount += route.major_segments.length;
      statusState.spineSegmentCount += route.spine_segments.length;
      if (!statusState.firstCoordinate && route.major_segments.length && route.major_segments[0].length) {{
        statusState.firstCoordinate = route.major_segments[0][0];
      }}
      latlngs(route.shortest_segments).forEach(seg => {{
        const line = L.polyline(seg, {{color:'#6b7280', weight:2.5, opacity:.35, dashArray:'7 5', routeKind:'shortest'}});
        line.addTo(group);
        lines.push(line);
        allPolylines.push(line);
        allBounds.extend(line.getBounds());
      }});
      latlngs(route.major_segments).forEach(seg => {{
        const line = L.polyline(seg, {{color, weight:4, opacity:.38, routeKind:'major'}});
        line.addTo(group);
        lines.push(line);
        allPolylines.push(line);
        allBounds.extend(line.getBounds());
      }});
      latlngs(route.spine_segments).forEach(seg => {{
        const line = L.polyline(seg, {{color, weight:7, opacity:.9, routeKind:'spine'}});
        line.addTo(group);
        lines.push(line);
        allPolylines.push(line);
        allBounds.extend(line.getBounds());
      }});
      group.addTo(map);
      layers.set(route.route_id, {{group, lines, color}});
      statusState.layerCount = allPolylines.length;
      route.review_color = color;
    }}
    function addSpineCorridor() {{
      const segments = ROUTE_REVIEW_DATA.spine_segments || [];
      latlngs(segments).forEach(seg => {{
        const line = L.polyline(seg, {{color:'#111827', weight:2, opacity:.16, interactive:false}});
        line.addTo(map);
      }});
    }}
    function focusRoute(routeId) {{
      setRouteHighlight(routeId, true);
    }}
    function setDecision(route, el, decision) {{
      decisions[route.route_id].decision = decision;
      el.classList.toggle('accepted', decision === 'accept');
      el.classList.toggle('rejected', decision === 'reject');
      el.querySelector('[data-action=accept]').classList.toggle('selected', decision === 'accept');
      el.querySelector('[data-action=reject]').classList.toggle('selected', decision === 'reject');
      const badge = el.querySelector('[data-role=decision]');
      badge.textContent = decision === 'accept' ? 'ACCEPTED' : 'REJECTED';
      badge.classList.toggle('accepted', decision === 'accept');
      badge.classList.toggle('rejected', decision === 'reject');
    }}
    const container = document.getElementById('routes');
    addSpineCorridor();
    ROUTE_REVIEW_DATA.routes.forEach((route, index) => {{
      decisions[route.route_id] = {{ route_id: route.route_id, scenario_id: route.scenario_id, target_edge_id: route.target_edge_id, decision: 'pending', reject_reason: '' }};
      addRoute(route, index);
      const el = document.createElement('section');
      el.className = 'route';
      el.dataset.routeId = route.route_id;
      el.innerHTML = `<div class="route-title"><span class="route-swatch" style="background:${{route.review_color}}"></span><strong>${{route.route_id}}</strong> <span class="${{route.review_status === 'PASS' ? '' : 'warn'}}">${{route.review_status}}</span><span class="decision-badge" data-role="decision">PENDING</span></div>
        <div class="meta">target=${{route.target_edge_id}}<br>spine=${{route.spine_length_m}}m, shortest=${{route.shortest_length_m}}m, major=${{route.major_length_m}}m<br>increase=${{route.length_increase_pct}}%, spine_ratio=${{route.spine_length_ratio}}, max_spine=${{route.max_consecutive_spine_length_m}}m<br>entry=${{route.spine_entry_position}}, exit=${{route.spine_exit_position}}, edges=${{route.route_edge_count}}, TLS=${{route.route_tls_count}}<br>warnings=${{route.warnings || 'none'}}</div>
        <button data-action="focus">Focus</button><button data-action="accept">Accept</button><button data-action="reject">Reject</button>
        <textarea placeholder="reject reason"></textarea>`;
      el.querySelector('[data-action=focus]').onclick = event => {{ event.stopPropagation(); focusRoute(route.route_id); }};
      el.querySelector('[data-action=accept]').onclick = event => {{ event.stopPropagation(); setDecision(route, el, 'accept'); }};
      el.querySelector('[data-action=reject]').onclick = event => {{ event.stopPropagation(); setDecision(route, el, 'reject'); }};
      el.querySelector('textarea').oninput = event => decisions[route.route_id].reject_reason = event.target.value;
      el.addEventListener('mouseenter', () => setRouteHover(route.route_id));
      el.addEventListener('mouseleave', () => {{
        if (hoveredRouteId === route.route_id) setRouteHover(null);
      }});
      el.addEventListener('focusin', () => setRouteHover(route.route_id));
      el.addEventListener('click', event => {{
        if (!event.target.closest('button, textarea')) focusRoute(route.route_id);
      }});
      container.appendChild(el);
    }});
    statusState.boundsValid = allBounds.isValid();
    map.invalidateSize();
    if (statusState.boundsValid) {{
      map.fitBounds(allBounds, {{padding:[20,20]}});
    }} else {{
      map.setView(FALLBACK_CENTER, 14);
      statusState.lastError = 'Route bounds invalid; using fallback center.';
    }}
    setTimeout(() => {{ map.invalidateSize(); renderStatus(); }}, 0);
    renderStatus();
    document.getElementById('download').onclick = () => {{
      const payload = {{ created_from: 'results/html/route_review.html', generated_at: new Date().toISOString(), decisions: Object.values(decisions) }};
      const blob = new Blob([JSON.stringify(payload, null, 2)], {{type:'application/json'}});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'route_review_decisions.json';
      a.click();
      URL.revokeObjectURL(a.href);
    }};
  </script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def write_route_review_v2_html(path: Path, review_data: dict[str, Any]) -> None:
    escaped_data = json.dumps(review_data, ensure_ascii=False).replace("</", "<\\/")
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spine Route Review v2</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#1f2937; background:#f6f7f9; }}
    .app {{ display:grid; grid-template-columns:410px 1fr; height:100vh; }}
    aside {{ overflow:auto; border-right:1px solid #d7dce5; padding:14px; background:#f7f8fb; }}
    #map {{ height:100vh; background:#e5e7eb; }}
    .leaflet-container {{ overflow:hidden; }}
    .leaflet-pane,.leaflet-tile,.leaflet-marker-icon,.leaflet-marker-shadow,.leaflet-tile-container,.leaflet-pane>svg,.leaflet-pane>canvas,.leaflet-zoom-box,.leaflet-image-layer,.leaflet-layer {{ position:absolute; left:0; top:0; }}
    .leaflet-container img {{ max-width:none !important; max-height:none !important; }}
    .leaflet-tile {{ width:256px; height:256px; }}
    h2 {{ margin:0 0 8px; font-size:18px; }}
    .meta {{ font-size:12px; color:#667085; line-height:1.5; }}
    .toolbar,.layers {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin:10px 0; }}
    select,button {{ min-height:32px; border:1px solid #cfd6e2; background:white; border-radius:6px; padding:5px 8px; font-size:13px; }}
    button {{ cursor:pointer; }}
    button.primary {{ background:#2563eb; color:white; border-color:#2563eb; }}
    label {{ display:inline-flex; gap:5px; align-items:center; font-size:12px; white-space:nowrap; }}
    .route {{ border:1px solid #d7dce5; background:white; margin:0 0 10px; padding:10px; border-radius:6px; }}
    .route.active {{ border-color:#2563eb; box-shadow:0 0 0 2px rgba(37,99,235,.18); }}
    .route-title {{ display:flex; align-items:center; gap:7px; margin-bottom:5px; }}
    .route-swatch {{ width:12px; height:12px; border-radius:3px; flex:0 0 12px; }}
    .badge {{ margin-left:auto; border-radius:999px; padding:2px 7px; font-size:11px; background:#eef2f7; color:#475467; }}
    .warn {{ color:#b45309; font-weight:600; }}
    .bad {{ color:#b42318; font-weight:600; }}
    textarea {{ width:100%; min-height:42px; margin-top:6px; }}
    .status-panel {{ border:1px solid #d7dce5; background:#fff; border-radius:6px; padding:10px; margin:10px 0; font-size:12px; line-height:1.5; white-space:pre-wrap; }}
    @media (max-width:900px) {{ .app {{ grid-template-columns:1fr; grid-template-rows:46vh 54vh; }} aside {{ grid-row:2; }} #map {{ grid-row:1; height:46vh; }} }}
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h2>Spine Route Review v2</h2>
      <div class="meta">기본 표시: v2 spine route only. 비교 레이어는 수동으로 켠다.</div>
      <div class="toolbar">
        <select id="route-select"><option value="">All v2 routes</option></select>
        <button class="primary" id="download">Download decisions</button>
      </div>
      <div class="layers">
        <label><input type="checkbox" data-layer="v2" checked> v2 spine</label>
        <label><input type="checkbox" data-layer="old"> old spine</label>
        <label><input type="checkbox" data-layer="major"> major</label>
        <label><input type="checkbox" data-layer="shortest"> shortest</label>
        <label><input type="checkbox" id="focus-only"> focus only</label>
      </div>
      <div class="status-panel"><strong>Map status</strong><span id="map-status">Initializing...</span></div>
      <div id="routes"></div>
    </aside>
    <main id="map"></main>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const DATA = {escaped_data};
    const FALLBACK_CENTER = [37.5616815, 126.9934095];
    const ROUTE_COLORS = ['#2563eb','#dc2626','#16a34a','#9333ea','#ea580c','#0891b2','#be123c','#4f46e5','#65a30d','#c026d3','#0f766e','#b45309','#7c3aed','#0284c7','#db2777','#ca8a04','#059669','#e11d48','#475569'];
    const map = L.map('map').setView(FALLBACK_CENTER, 14);
    const statusEl = document.getElementById('map-status');
    const routeSelect = document.getElementById('route-select');
    const focusOnly = document.getElementById('focus-only');
    const layerState = {{ v2:true, old:false, major:false, shortest:false }};
    const routeLayers = new Map();
    const decisions = {{}};
    let selectedRouteId = '';
    let bounds = L.latLngBounds([]);
    let lastError = '';
    window.DATA = DATA;
    window.map = map;
    window.onerror = msg => {{ lastError = String(msg); renderStatus(); }};
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom:19}}).addTo(map);
    function latlngs(segments) {{
      return segments.map(seg => seg.map(p => [p[1], p[0]])).filter(seg => seg.length >= 2);
    }}
    function color(index) {{ return ROUTE_COLORS[index % ROUTE_COLORS.length]; }}
    function addPolyline(group, route, kind, segments, index) {{
      const c = color(index);
      const styles = {{
        v2: {{ color:c, weight:8, opacity:.92 }},
        old: {{ color:'#111827', weight:3, opacity:.55, dashArray:'4 5' }},
        major: {{ color:c, weight:4, opacity:.38, dashArray:'9 6' }},
        shortest: {{ color:'#6b7280', weight:3, opacity:.35, dashArray:'2 7' }},
      }};
      latlngs(segments).forEach(seg => {{
        const base = styles[kind];
        const line = L.polyline(seg, {{...base, baseWeight:base.weight, baseOpacity:base.opacity, routeId:route.route_id, routeKind:kind}});
        line.addTo(group);
        bounds.extend(line.getBounds());
      }});
    }}
    function buildLayers() {{
      DATA.routes.forEach((route, index) => {{
        const entry = {{ color:color(index), groups:{{}} }};
        ['v2','old','major','shortest'].forEach(kind => entry.groups[kind] = L.layerGroup());
        addPolyline(entry.groups.v2, route, 'v2', route.spine_segments, index);
        addPolyline(entry.groups.old, route, 'old', route.old_spine_segments || [], index);
        addPolyline(entry.groups.major, route, 'major', route.major_segments || [], index);
        addPolyline(entry.groups.shortest, route, 'shortest', route.shortest_segments || [], index);
        routeLayers.set(route.route_id, entry);
      }});
    }}
    function syncLayers() {{
      routeLayers.forEach((entry, routeId) => {{
        const selected = !selectedRouteId || selectedRouteId === routeId;
        Object.entries(entry.groups).forEach(([kind, group]) => {{
          const visible = layerState[kind] && (!focusOnly.checked || selected);
          if (visible && !map.hasLayer(group)) group.addTo(map);
          if (!visible && map.hasLayer(group)) map.removeLayer(group);
        }});
        Object.values(entry.groups).forEach(group => group.eachLayer(line => {{
          const active = selectedRouteId && selectedRouteId === routeId;
          const muted = selectedRouteId && selectedRouteId !== routeId;
          line.setStyle({{ opacity: muted ? .08 : (active ? 1 : line.options.baseOpacity), weight: active && line.options.routeKind === 'v2' ? 11 : line.options.baseWeight }});
          if (active && line.bringToFront) line.bringToFront();
        }}));
      }});
      document.querySelectorAll('.route').forEach(el => el.classList.toggle('active', selectedRouteId && el.dataset.routeId === selectedRouteId));
      renderStatus();
    }}
    function renderStatus() {{
      const center = map.getCenter();
      statusEl.textContent = [
        `route count: ${{DATA.routes.length}}`,
        `deleted candidates: ${{(DATA.deleted_candidates || []).length}}`,
        `selected route: ${{selectedRouteId || 'all'}}`,
        `visible layers: ${{Object.entries(layerState).filter(([,v]) => v).map(([k]) => k).join(', ')}}`,
        `map center/zoom: ${{center.lat.toFixed(6)}}, ${{center.lng.toFixed(6)}} / ${{map.getZoom()}}`,
        `last JS error: ${{lastError || 'none'}}`,
      ].join('\\n');
    }}
    function focusRoute(routeId, fit=true) {{
      selectedRouteId = routeId || '';
      routeSelect.value = selectedRouteId;
      syncLayers();
      if (fit && selectedRouteId) {{
        const entry = routeLayers.get(selectedRouteId);
        const b = L.latLngBounds([]);
        Object.values(entry.groups).forEach(group => group.eachLayer(line => b.extend(line.getBounds())));
        if (b.isValid()) map.fitBounds(b, {{padding:[20,20]}});
      }}
    }}
    function metricClass(route) {{
      return route.needs_manual_review ? 'bad' : (route.warnings ? 'warn' : '');
    }}
    function renderRoutes() {{
      const container = document.getElementById('routes');
      DATA.routes.forEach((route, index) => {{
        routeSelect.appendChild(new Option(`${{route.route_id}} ${{route.previous_review_status}}`, route.route_id));
        decisions[route.route_id] = {{route_id:route.route_id, scenario_id:route.scenario_id, target_edge_id:route.target_edge_id, decision:'pending', reject_reason:''}};
        const el = document.createElement('section');
        el.className = 'route';
        el.dataset.routeId = route.route_id;
        el.innerHTML = `<div class="route-title"><span class="route-swatch" style="background:${{color(index)}}"></span><strong>${{route.route_id}}</strong><span class="${{metricClass(route)}}">${{route.review_status}}</span><span class="badge">${{route.previous_review_status}} -> pending</span></div>
          <div class="meta">target=${{route.target_edge_id}}<br>spine=${{route.spine_length_m}}m, ratio=${{route.spine_length_ratio}}, max=${{route.max_consecutive_spine_length_m}}m<br>shortest_ratio=${{route.shortest_length_ratio}}, detour_warning=${{route.length_detour_warning}}<br>segments=${{route.segment_count}}, gaps=${{route.gap_count}}, max_gap=${{route.max_gap_distance_m}}m, repeat=${{route.repeated_edge_count}}<br>max_turn=${{route.max_turn_angle}}, sharp=${{route.sharp_turn_count}}, uturn_like=${{route.uturn_like_transition_count}}<br>manual=${{route.needs_manual_review}}, visual=${{route.visual_confusion_check}}<br>warnings=${{route.warnings || 'none'}}</div>
          <button data-action="focus">Focus</button><button data-action="accept">Accept</button><button data-action="reject">Reject</button>
          <textarea placeholder="reject reason"></textarea>`;
        el.querySelector('[data-action=focus]').onclick = event => {{ event.stopPropagation(); focusRoute(route.route_id); }};
        el.querySelector('[data-action=accept]').onclick = event => {{ event.stopPropagation(); decisions[route.route_id].decision = 'accept'; }};
        el.querySelector('[data-action=reject]').onclick = event => {{ event.stopPropagation(); decisions[route.route_id].decision = 'reject'; }};
        el.querySelector('textarea').oninput = event => decisions[route.route_id].reject_reason = event.target.value;
        el.onclick = event => {{ if (!event.target.closest('button,textarea')) focusRoute(route.route_id); }};
        container.appendChild(el);
      }});
    }}
    buildLayers();
    renderRoutes();
    syncLayers();
    if (bounds.isValid()) map.fitBounds(bounds, {{padding:[20,20]}});
    document.querySelectorAll('[data-layer]').forEach(input => input.onchange = () => {{ layerState[input.dataset.layer] = input.checked; syncLayers(); }});
    focusOnly.onchange = syncLayers;
    routeSelect.onchange = () => focusRoute(routeSelect.value);
    document.getElementById('download').onclick = () => {{
      const payload = {{created_from:'results/html/route_review_spine_v2.html', generated_at:new Date().toISOString(), decisions:Object.values(decisions)}};
      const blob = new Blob([JSON.stringify(payload, null, 2)], {{type:'application/json'}});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'route_review_decisions_spine_v2.json';
      a.click();
      URL.revokeObjectURL(a.href);
    }};
    renderStatus();
  </script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def write_review_schema(path: Path) -> None:
    write_json(
        path,
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Route review decisions",
            "type": "object",
            "required": ["created_from", "generated_at", "decisions"],
            "properties": {
                "created_from": {"const": "results/html/route_review.html"},
                "generated_at": {"type": "string"},
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["route_id", "scenario_id", "target_edge_id", "decision", "reject_reason"],
                        "properties": {
                            "route_id": {"type": "string"},
                            "scenario_id": {"type": "string"},
                            "target_edge_id": {"type": "string"},
                            "decision": {"enum": ["pending", "accept", "reject"]},
                            "reject_reason": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    )


def previous_reject_reasons(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError, Step07Error):
        return []
    decisions = payload.get("decisions", [])
    if not isinstance(decisions, list):
        return []
    reasons = []
    for item in decisions:
        if not isinstance(item, dict) or item.get("decision") != "reject":
            continue
        reasons.append(
            {
                "route_id": str(item.get("route_id", "")),
                "target_edge_id": str(item.get("target_edge_id", "")),
                "reject_reason": str(item.get("reject_reason", "")),
            }
        )
    return reasons


def write_spine_review_decisions(path: Path, generated_at: str, route_rows: list[dict[str, str]]) -> None:
    decisions = []
    for row in route_rows:
        route_id = row["route_id"]
        if route_id == DELETED_ROUTE_ID:
            decision = "delete"
        elif route_id in V1_ACCEPT_ROUTE_IDS:
            decision = "accept"
        else:
            decision = "reject"
        decisions.append(
            {
                "route_id": route_id,
                "scenario_id": row["scenario_id"],
                "target_edge_id": row["target_edge_id"],
                "decision": decision,
                "reason_type": V1_REJECT_REASONS.get(route_id, "accepted_v1"),
            }
        )
    write_json(
        path,
        {
            "created_from": "manual_user_route_review_v1",
            "generated_at": generated_at,
            "notes": "Manual decisions supplied before Step 7 spine route v2. ER_ACC_020 is deleted; remaining routes are regenerated for v2 review.",
            "decisions": decisions,
        },
    )


def main_spine_v2() -> int:
    generated_at = utc_now()
    lines = ["Step 7 spine route v2 generation", "================================", f"generated_at: {generated_at}"]
    try:
        for path in [ACTIVE_NET, ACTIVE_EDGES_GEOJSON, ACTIVE_TLS_GEOJSON, STEP6_SUMMARY, STEP6_ROUTES, STATION_START_EDGE, ROUTE_COMPARE_CSV]:
            if not path.is_file():
                raise Step07Error(f"Required input missing: {rel(path)}")
        step6 = load_json(STEP6_SUMMARY)
        if step6.get("final_status") not in {"PASS", "WARNING"}:
            raise Step07Error(f"Step 6 final_status blocks Step 7 v2: {step6.get('final_status')}")
        if int(step6.get("reachable_count", 0)) != 20:
            raise Step07Error(f"Step 6 reachable_count must be 20: {step6.get('reachable_count')}")

        sumo_net = read_sumo_net(ACTIVE_NET)
        props_by_id, coords_by_id = load_edge_geojson(ACTIVE_EDGES_GEOJSON)
        map_config = load_yaml(MAP_CONFIG)
        axis = bearing_vector(map_config)
        axis_ctx = axis_context(map_config)
        spine_rows, spine_ids, spine_metrics = build_spine_edges(sumo_net, props_by_id, coords_by_id, axis_ctx)

        step6_rows = [row for row in read_csv(STEP6_ROUTES) if row.get("route_status") in {"PASS", "WARNING"}]
        if len(step6_rows) != 20:
            raise Step07Error(f"Expected 20 reachable Step 6 rows, got {len(step6_rows)}")
        step6_rows.sort(key=lambda row: row["target_edge_id"])
        route_key_rows = [
            {
                "route_id": f"ER_ACC_{index:03d}",
                "scenario_id": f"ACC_{index:03d}",
                "target_edge_id": row["target_edge_id"],
            }
            for index, row in enumerate(step6_rows, start=1)
        ]
        write_spine_review_decisions(ROUTE_REVIEW_DECISIONS_SPINE, generated_at, route_key_rows)

        deleted_rows = [row for row in route_key_rows if row["route_id"] == DELETED_ROUTE_ID]
        if len(deleted_rows) != 1:
            raise Step07Error(f"Deleted route id not found in Step 6 target ordering: {DELETED_ROUTE_ID}")
        deleted_target = deleted_rows[0]["target_edge_id"]
        write_csv(
            DELETED_ROUTE_CANDIDATES_CSV,
            [
                {
                    **deleted_rows[0],
                    "delete_reason": "manual_delete_after_review",
                    "source_step": "step7_route_review_v1",
                }
            ],
            ["route_id", "scenario_id", "target_edge_id", "delete_reason", "source_step"],
        )

        improvement_rows = []
        for row in route_key_rows:
            if row["route_id"] == DELETED_ROUTE_ID:
                continue
            previous_review_status = "accept" if row["route_id"] in V1_ACCEPT_ROUTE_IDS else "reject"
            improvement_rows.append(
                {
                    **row,
                    "previous_review_status": previous_review_status,
                    "reject_reason_type": V1_REJECT_REASONS.get(row["route_id"], ""),
                    "v2_review_status": "pending",
                }
            )
        write_csv(
            SPINE_ROUTE_IMPROVEMENT_TARGETS_CSV,
            improvement_rows,
            ["route_id", "scenario_id", "target_edge_id", "previous_review_status", "reject_reason_type", "v2_review_status"],
        )

        old_compare_by_route = {row["route_id"]: row for row in read_csv(ROUTE_COMPARE_CSV)}
        route_rows: list[dict[str, Any]] = []
        compare_rows: list[dict[str, Any]] = []
        review_routes: list[dict[str, Any]] = []
        turn_rows: list[dict[str, Any]] = []
        warnings = 0
        manual_reviews = 0

        for source in improvement_rows:
            route_id = source["route_id"]
            scenario_id = source["scenario_id"]
            target_edge_id = source["target_edge_id"]
            shortest_ids = shortest_route(sumo_net, START_EDGE_ID, target_edge_id)
            major_ids = major_route(sumo_net, START_EDGE_ID, target_edge_id, props_by_id, coords_by_id, axis)
            spine = select_spine_route_v2(sumo_net, START_EDGE_ID, target_edge_id, shortest_ids, major_ids, props_by_id, coords_by_id, axis, axis_ctx, spine_ids, spine_metrics)
            spine_ids_for_route = spine["edge_ids"]
            shortest_length = route_length(sumo_net, shortest_ids)
            major_length = route_length(sumo_net, major_ids)
            spine_route_length = float(spine["route_length_m"])
            increase_ratio = float(spine["length_increase_ratio"])
            route_edges = route_objects(sumo_net, spine_ids_for_route)
            tls_ids = sorted(route_tls_ids(route_edges))
            geometry = route_geometry_diagnostics(spine_ids_for_route, coords_by_id, axis_ctx)
            turn_summary, route_turn_rows = route_turn_diagnostics(route_id, spine_ids_for_route, coords_by_id, axis_ctx)
            spine.update(turn_summary)
            turn_rows.extend(route_turn_rows)
            review_status, warning_text, needs_manual_review, length_detour_warning = route_review_status_v2(spine, geometry)
            if needs_manual_review:
                manual_reviews += 1
            elif warning_text:
                warnings += 1
            route_text = " ".join(spine_ids_for_route)
            old_row = old_compare_by_route.get(route_id, {})
            old_spine_ids = old_row.get("spine_route_edges", "").split()
            previous_review_status = source["previous_review_status"]
            route_row = {
                "route_id": route_id,
                "scenario_id": scenario_id,
                "target_edge_id": target_edge_id,
                "selected_policy": "spine_corridor_biased_v2",
                "route_edges": route_text,
                "route_length_m": round(spine_route_length, 3),
                "route_edge_count": len(spine_ids_for_route),
                "route_tls_count": len(tls_ids),
                "spine_edge_count": spine["spine_edge_count"],
                "spine_length_m": round(float(spine["spine_length_m"]), 3),
                "spine_length_ratio": round(float(spine["spine_length_ratio"]), 6),
                "max_consecutive_spine_length_m": round(float(spine["max_consecutive_spine_length_m"]), 3),
                "length_increase_pct": round(increase_ratio * 100, 3),
                "length_detour_warning": length_detour_warning,
                "review_status": review_status,
                "needs_manual_review": needs_manual_review,
                "warnings": warning_text,
                "previous_review_status": previous_review_status,
            }
            route_rows.append(route_row)
            distance_to_target = spine.get("distance_to_target_at_spine_exit_m", "")
            compare_rows.append(
                {
                    **route_row,
                    "shortest_route_edges": " ".join(shortest_ids),
                    "shortest_length_m": round(shortest_length, 3),
                    "shortest_edge_count": len(shortest_ids),
                    "major_route_edges": " ".join(major_ids),
                    "major_length_m": round(major_length, 3),
                    "major_edge_count": len(major_ids),
                    "major_tls_count": len(route_tls_ids(route_objects(sumo_net, major_ids))),
                    "old_spine_route_edges": old_row.get("spine_route_edges", ""),
                    "old_spine_length_m": old_row.get("spine_length_m", ""),
                    "old_spine_length_ratio": old_row.get("spine_length_ratio", ""),
                    "spine_route_edges": route_text,
                    "spine_length_m_on_corridor": round(float(spine["spine_length_m"]), 3),
                    "spine_entry_position": round(float(spine["spine_entry_position"]), 6) if spine["spine_entry_position"] != "" else "",
                    "spine_exit_position": round(float(spine["spine_exit_position"]), 6) if spine["spine_exit_position"] != "" else "",
                    "spine_entry_position_ratio": round(float(spine["spine_entry_position_ratio"]), 6) if spine["spine_entry_position_ratio"] != "" else "",
                    "spine_exit_position_ratio": round(float(spine["spine_exit_position_ratio"]), 6) if spine["spine_exit_position_ratio"] != "" else "",
                    "distance_to_target_at_spine_exit_m": round(float(distance_to_target), 3) if distance_to_target != "" else "",
                    "selected_candidate_policy": spine["candidate_policy"],
                    "selected_bias_strength": spine["bias_strength"],
                    "selection_score": round(float(spine["selection_score"]), 6),
                    "candidate_count": spine["candidate_count"],
                    "shortest_length_ratio": round(spine_route_length / shortest_length, 6) if shortest_length else "",
                    **{key: round(value, 3) if isinstance(value, float) else value for key, value in geometry.items()},
                    "max_turn_angle": round(float(spine["max_turn_angle"]), 3),
                    "sharp_turn_count": spine["sharp_turn_count"],
                    "uturn_like_transition_count": spine["uturn_like_transition_count"],
                    "worst_turn_from_edge": spine["worst_turn_from_edge"],
                    "worst_turn_to_edge": spine["worst_turn_to_edge"],
                }
            )
            review_routes.append(
                {
                    **route_row,
                    "shortest_length_m": round(shortest_length, 3),
                    "major_length_m": round(major_length, 3),
                    "shortest_length_ratio": round(spine_route_length / shortest_length, 6) if shortest_length else "",
                    "spine_entry_position": round(float(spine["spine_entry_position"]), 6) if spine["spine_entry_position"] != "" else "",
                    "spine_exit_position": round(float(spine["spine_exit_position"]), 6) if spine["spine_exit_position"] != "" else "",
                    "distance_to_target_at_spine_exit_m": round(float(distance_to_target), 3) if distance_to_target != "" else "",
                    **{key: round(value, 3) if isinstance(value, float) else value for key, value in geometry.items()},
                    "max_turn_angle": round(float(spine["max_turn_angle"]), 3),
                    "sharp_turn_count": spine["sharp_turn_count"],
                    "uturn_like_transition_count": spine["uturn_like_transition_count"],
                    "worst_turn_from_edge": spine["worst_turn_from_edge"],
                    "worst_turn_to_edge": spine["worst_turn_to_edge"],
                    "v2_review_status": "pending",
                    "shortest_segments": route_coords(shortest_ids, coords_by_id),
                    "major_segments": route_coords(major_ids, coords_by_id),
                    "old_spine_segments": route_coords(old_spine_ids, coords_by_id),
                    "spine_segments": route_coords(spine_ids_for_route, coords_by_id),
                }
            )

        route_fields = [
            "route_id", "scenario_id", "target_edge_id", "selected_policy", "route_edges", "route_length_m", "route_edge_count",
            "route_tls_count", "spine_edge_count", "spine_length_m", "spine_length_ratio", "max_consecutive_spine_length_m",
            "length_increase_pct", "length_detour_warning", "review_status", "needs_manual_review", "warnings", "previous_review_status",
        ]
        compare_fields = [
            "route_id", "scenario_id", "target_edge_id", "previous_review_status", "selected_policy", "shortest_route_edges",
            "shortest_length_m", "shortest_edge_count", "major_route_edges", "major_length_m", "major_edge_count", "major_tls_count",
            "old_spine_route_edges", "old_spine_length_m", "old_spine_length_ratio", "spine_route_edges", "route_length_m",
            "route_edge_count", "route_tls_count", "spine_edge_count", "spine_length_m", "spine_length_ratio", "spine_length_m_on_corridor",
            "max_consecutive_spine_length_m", "spine_entry_position", "spine_exit_position", "spine_entry_position_ratio",
            "spine_exit_position_ratio", "distance_to_target_at_spine_exit_m", "selected_candidate_policy", "selected_bias_strength",
            "selection_score", "candidate_count", "length_increase_pct", "shortest_length_ratio", "length_detour_warning",
            "segment_count", "gap_count", "max_gap_distance_m", "repeated_edge_count", "route_geometry_continuous",
            "visual_confusion_check", "max_turn_angle", "sharp_turn_count", "uturn_like_transition_count", "worst_turn_from_edge",
            "worst_turn_to_edge", "review_status", "needs_manual_review", "warnings",
        ]
        turn_fields = ["route_id", "transition_index", "from_edge_id", "to_edge_id", "turn_angle_deg", "turn_class"]
        write_csv(EMERGENCY_ROUTES_V2_CSV, route_rows, route_fields)
        write_csv(ROUTE_COMPARE_V2_CSV, compare_rows, compare_fields)
        write_csv(TURN_DIAGNOSTICS_V2_CSV, turn_rows, turn_fields)
        write_route_xml(EMERGENCY_ROUTES_V2_XML, route_rows)
        write_route_review_v2_html(
            ROUTE_REVIEW_V2_HTML,
            {
                "generated_at": generated_at,
                "active_map": {"net": rel(ACTIVE_NET), "edges_geojson": rel(ACTIVE_EDGES_GEOJSON), "tls_geojson": rel(ACTIVE_TLS_GEOJSON)},
                "selected_policy": "spine_corridor_biased_v2",
                "deleted_candidates": [{"route_id": DELETED_ROUTE_ID, "target_edge_id": deleted_target}],
                "routes": review_routes,
            },
        )

        xml_root = ET.parse(EMERGENCY_ROUTES_V2_XML).getroot()
        edge_failures: list[str] = []
        emergency_disallowed: list[str] = []
        starts_wrong: list[str] = []
        for row in route_rows:
            edge_ids = row["route_edges"].split()
            if not edge_ids or edge_ids[0] != START_EDGE_ID:
                starts_wrong.append(row["route_id"])
            for edge_id in edge_ids:
                edge = edge_from_net(sumo_net, edge_id)
                if edge is None:
                    edge_failures.append(edge_id)
                elif not edge.allows("emergency"):
                    emergency_disallowed.append(edge_id)
        preflight_rows = [
            {"check": "route_xml_root", "status": "PASS" if xml_root.tag == "routes" else "FAIL", "detail": xml_root.tag},
            {"check": "route_edges_in_reduced_net", "status": "PASS" if not edge_failures else "FAIL", "detail": ";".join(sorted(set(edge_failures)))},
            {"check": "route_edges_allow_emergency", "status": "PASS" if not emergency_disallowed else "FAIL", "detail": ";".join(sorted(set(emergency_disallowed)))},
            {"check": "route_count", "status": "PASS" if len(route_rows) == 19 else "FAIL", "detail": len(route_rows)},
            {"check": "deleted_candidate_excluded", "status": "PASS" if all(row["route_id"] != DELETED_ROUTE_ID for row in route_rows) else "FAIL", "detail": DELETED_ROUTE_ID},
            {"check": "start_edge", "status": "PASS" if not starts_wrong else "FAIL", "detail": ";".join(starts_wrong)},
            {"check": "route_review_spine_v2_html", "status": "PASS" if ROUTE_REVIEW_V2_HTML.is_file() else "FAIL", "detail": rel(ROUTE_REVIEW_V2_HTML)},
        ]
        preflight_status = "FAIL" if any(row["status"] == "FAIL" for row in preflight_rows) else ("WARNING" if warnings or manual_reviews else "PASS")
        write_json(
            EMERGENCY_ROUTE_V2_SUMMARY,
            {
                "generated_at": generated_at,
                "final_status": preflight_status,
                "active_net": rel(ACTIVE_NET),
                "route_count": len(route_rows),
                "deleted_route_id": DELETED_ROUTE_ID,
                "deleted_target_edge_id": deleted_target,
                "selected_policy": "spine_corridor_biased_v2",
                "corridor_spine_edge_count": len(spine_ids),
                "warning_count": warnings,
                "needs_manual_review_count": manual_reviews,
                "checks": preflight_rows,
                "outputs": [
                    rel(ROUTE_REVIEW_DECISIONS_SPINE),
                    rel(DELETED_ROUTE_CANDIDATES_CSV),
                    rel(SPINE_ROUTE_IMPROVEMENT_TARGETS_CSV),
                    rel(ROUTE_COMPARE_V2_CSV),
                    rel(EMERGENCY_ROUTES_V2_CSV),
                    rel(EMERGENCY_ROUTES_V2_XML),
                    rel(TURN_DIAGNOSTICS_V2_CSV),
                    rel(ROUTE_REVIEW_V2_HTML),
                ],
            },
        )
        lines.extend(
            [
                f"route_count: {len(route_rows)}",
                f"deleted_route_id: {DELETED_ROUTE_ID}",
                f"deleted_target_edge_id: {deleted_target}",
                f"warning_count: {warnings}",
                f"needs_manual_review_count: {manual_reviews}",
                f"preflight_status: {preflight_status}",
                f"route_review_html: {rel(ROUTE_REVIEW_V2_HTML)}",
            ]
        )
        LOG_V2_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_V2_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if preflight_status in {"PASS", "WARNING"} else 1
    except (Step07Error, OSError, ET.ParseError, ValueError, RuntimeError, ImportError) as exc:
        lines.extend(["Status: FAIL", str(exc)])
        LOG_V2_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_V2_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


def main() -> int:
    args = parse_args()
    if args.variant == "spine-v2":
        return main_spine_v2()
    generated_at = utc_now()
    lines = ["Step 7 emergency route artifact generation", "==========================================", f"generated_at: {generated_at}"]
    try:
        for path in [ACTIVE_NET, ACTIVE_EDGES_GEOJSON, ACTIVE_TLS_GEOJSON, STEP6_SUMMARY, STEP6_ROUTES, STATION_START_EDGE]:
            if not path.is_file():
                raise Step07Error(f"Required input missing: {rel(path)}")
        step6 = load_json(STEP6_SUMMARY)
        if step6.get("final_status") not in {"PASS", "WARNING"}:
            raise Step07Error(f"Step 6 final_status blocks Step 7: {step6.get('final_status')}")
        if int(step6.get("reachable_count", 0)) != 20:
            raise Step07Error(f"Step 6 reachable_count must be 20: {step6.get('reachable_count')}")
        previous_rejects = previous_reject_reasons(ROUTE_REVIEW_DECISIONS)

        sumo_net = read_sumo_net(ACTIVE_NET)
        props_by_id, coords_by_id = load_edge_geojson(ACTIVE_EDGES_GEOJSON)
        map_config = load_yaml(MAP_CONFIG)
        axis = bearing_vector(map_config)
        axis_ctx = axis_context(map_config)
        spine_rows, spine_ids, spine_metrics = build_spine_edges(sumo_net, props_by_id, coords_by_id, axis_ctx)
        spine_fields = ["edge_id", "length_m", "lane_count", "speed_mps", "priority", "axis_alignment", "spine_score", "is_spine_edge"]
        write_csv(CORRIDOR_SPINE_EDGES_CSV, spine_rows, spine_fields)
        step6_rows = [row for row in read_csv(STEP6_ROUTES) if row.get("route_status") in {"PASS", "WARNING"}]
        if len(step6_rows) != 20:
            raise Step07Error(f"Expected 20 reachable Step 6 rows, got {len(step6_rows)}")
        step6_rows.sort(key=lambda row: row["target_edge_id"])

        route_rows: list[dict[str, Any]] = []
        compare_rows: list[dict[str, Any]] = []
        scenario_rows: list[dict[str, Any]] = []
        review_routes: list[dict[str, Any]] = []
        warnings = 0
        manual_reviews = 0

        for index, source in enumerate(step6_rows, start=1):
            scenario_id = f"ACC_{index:03d}"
            route_id = f"ER_{scenario_id}"
            target_edge_id = source["target_edge_id"]
            shortest_ids = shortest_route(sumo_net, START_EDGE_ID, target_edge_id)
            major_ids = major_route(sumo_net, START_EDGE_ID, target_edge_id, props_by_id, coords_by_id, axis)
            spine = select_spine_route(sumo_net, START_EDGE_ID, target_edge_id, shortest_ids, major_ids, props_by_id, coords_by_id, axis, spine_ids, spine_metrics)
            spine_ids_for_route = spine["edge_ids"]
            shortest_length = route_length(sumo_net, shortest_ids)
            major_length = route_length(sumo_net, major_ids)
            spine_length = float(spine["route_length_m"])
            increase_ratio = float(spine["length_increase_ratio"])
            route_edges = route_objects(sumo_net, spine_ids_for_route)
            tls_ids = sorted(route_tls_ids(route_edges))
            review_status, warning_text, needs_manual_review = route_review_status(spine)
            if needs_manual_review:
                manual_reviews += 1
            elif warning_text:
                warnings += 1
            route_text = " ".join(spine_ids_for_route)
            route_rows.append(
                {
                    "route_id": route_id,
                    "scenario_id": scenario_id,
                    "target_edge_id": target_edge_id,
                    "selected_policy": "spine_corridor_biased",
                    "route_edges": route_text,
                    "route_length_m": round(spine_length, 3),
                    "route_edge_count": len(spine_ids_for_route),
                    "route_tls_count": len(tls_ids),
                    "spine_edge_count": spine["spine_edge_count"],
                    "spine_length_m": round(float(spine["spine_length_m"]), 3),
                    "spine_length_ratio": round(float(spine["spine_length_ratio"]), 6),
                    "max_consecutive_spine_length_m": round(float(spine["max_consecutive_spine_length_m"]), 3),
                    "length_increase_pct": round(increase_ratio * 100, 3),
                    "review_status": review_status,
                    "warnings": warning_text,
                }
            )
            compare_rows.append(
                {
                    "route_id": route_id,
                    "scenario_id": scenario_id,
                    "target_edge_id": target_edge_id,
                    "shortest_route_edges": " ".join(shortest_ids),
                    "shortest_length_m": round(shortest_length, 3),
                    "shortest_edge_count": len(shortest_ids),
                    "major_route_edges": " ".join(major_ids),
                    "major_length_m": round(major_length, 3),
                    "major_edge_count": len(major_ids),
                    "major_tls_count": len(route_tls_ids(route_objects(sumo_net, major_ids))),
                    "spine_route_edges": route_text,
                    "spine_length_m": round(spine_length, 3),
                    "spine_edge_count": len(spine_ids_for_route),
                    "spine_tls_count": len(tls_ids),
                    "spine_length_ratio": round(float(spine["spine_length_ratio"]), 6),
                    "spine_length_m_on_corridor": round(float(spine["spine_length_m"]), 3),
                    "max_consecutive_spine_length_m": round(float(spine["max_consecutive_spine_length_m"]), 3),
                    "spine_entry_position": round(float(spine["spine_entry_position"]), 6) if spine["spine_entry_position"] != "" else "",
                    "spine_exit_position": round(float(spine["spine_exit_position"]), 6) if spine["spine_exit_position"] != "" else "",
                    "selected_candidate_policy": spine["candidate_policy"],
                    "selected_bias_strength": spine["bias_strength"],
                    "selection_score": round(float(spine["selection_score"]), 6),
                    "candidate_count": spine["candidate_count"],
                    "length_increase_pct": round(increase_ratio * 100, 3),
                    "review_status": review_status,
                    "needs_manual_review": needs_manual_review,
                    "warnings": warning_text,
                }
            )
            scenario_rows.append(
                {
                    "scenario_id": scenario_id,
                    "route_id": route_id,
                    "target_edge_id": target_edge_id,
                    "start_edge_id": START_EDGE_ID,
                    "route_length_m": round(spine_length, 3),
                    "selected_policy": "spine_corridor_biased",
                    "review_status": review_status,
                }
            )
            review_routes.append(
                {
                    "route_id": route_id,
                    "scenario_id": scenario_id,
                    "target_edge_id": target_edge_id,
                    "shortest_length_m": round(shortest_length, 3),
                    "major_length_m": round(major_length, 3),
                    "spine_length_m": round(spine_length, 3),
                    "length_increase_pct": round(increase_ratio * 100, 3),
                    "route_edge_count": len(spine_ids_for_route),
                    "route_tls_count": len(tls_ids),
                    "spine_edge_count": spine["spine_edge_count"],
                    "spine_length_on_corridor_m": round(float(spine["spine_length_m"]), 3),
                    "spine_length_ratio": round(float(spine["spine_length_ratio"]), 6),
                    "max_consecutive_spine_length_m": round(float(spine["max_consecutive_spine_length_m"]), 3),
                    "spine_entry_position": round(float(spine["spine_entry_position"]), 6) if spine["spine_entry_position"] != "" else "",
                    "spine_exit_position": round(float(spine["spine_exit_position"]), 6) if spine["spine_exit_position"] != "" else "",
                    "selected_candidate_policy": spine["candidate_policy"],
                    "selected_bias_strength": spine["bias_strength"],
                    "needs_manual_review": needs_manual_review,
                    "warnings": warning_text,
                    "review_status": review_status,
                    "shortest_segments": route_coords(shortest_ids, coords_by_id),
                    "major_segments": route_coords(major_ids, coords_by_id),
                    "spine_segments": route_coords(spine_ids_for_route, coords_by_id),
                }
            )

        route_fields = ["route_id", "scenario_id", "target_edge_id", "selected_policy", "route_edges", "route_length_m", "route_edge_count", "route_tls_count", "spine_edge_count", "spine_length_m", "spine_length_ratio", "max_consecutive_spine_length_m", "length_increase_pct", "review_status", "warnings"]
        compare_fields = ["route_id", "scenario_id", "target_edge_id", "shortest_route_edges", "shortest_length_m", "shortest_edge_count", "major_route_edges", "major_length_m", "major_edge_count", "major_tls_count", "spine_route_edges", "spine_length_m", "spine_edge_count", "spine_tls_count", "spine_length_ratio", "spine_length_m_on_corridor", "max_consecutive_spine_length_m", "spine_entry_position", "spine_exit_position", "selected_candidate_policy", "selected_bias_strength", "selection_score", "candidate_count", "length_increase_pct", "review_status", "needs_manual_review", "warnings"]
        scenario_fields = ["scenario_id", "route_id", "target_edge_id", "start_edge_id", "route_length_m", "selected_policy", "review_status"]
        write_csv(EMERGENCY_ROUTES_CSV, route_rows, route_fields)
        write_csv(ROUTE_COMPARE_CSV, compare_rows, compare_fields)
        write_csv(ACCIDENT_SCENARIOS_CSV, scenario_rows, scenario_fields)
        write_route_xml(EMERGENCY_ROUTES_XML, route_rows)
        write_route_review_html(
            ROUTE_REVIEW_HTML,
            {
                "generated_at": generated_at,
                "active_map": {
                    "net": rel(ACTIVE_NET),
                    "edges_geojson": rel(ACTIVE_EDGES_GEOJSON),
                    "tls_geojson": rel(ACTIVE_TLS_GEOJSON),
                },
                "selected_policy": "spine_corridor_biased",
                "spine_segments": route_coords(sorted(spine_ids), coords_by_id),
                "routes": review_routes,
            },
        )
        write_review_schema(ROUTE_REVIEW_SCHEMA)

        preflight_rows = []
        xml_root = ET.parse(EMERGENCY_ROUTES_XML).getroot()
        preflight_rows.append({"check": "route_xml_root", "status": "PASS" if xml_root.tag == "routes" else "FAIL", "detail": xml_root.tag})
        edge_failures: list[str] = []
        emergency_disallowed: list[str] = []
        for row in route_rows:
            for edge_id in row["route_edges"].split():
                edge = edge_from_net(sumo_net, edge_id)
                if edge is None:
                    edge_failures.append(edge_id)
                elif not edge.allows("emergency"):
                    emergency_disallowed.append(edge_id)
        preflight_rows.append({"check": "route_edges_in_reduced_net", "status": "PASS" if not edge_failures else "FAIL", "detail": ";".join(sorted(set(edge_failures)))})
        preflight_rows.append({"check": "route_edges_allow_emergency", "status": "PASS" if not emergency_disallowed else "FAIL", "detail": ";".join(sorted(set(emergency_disallowed)))})
        preflight_rows.append({"check": "route_count", "status": "PASS" if len(route_rows) == 20 else "FAIL", "detail": len(route_rows)})
        preflight_rows.append({"check": "corridor_spine_edge_count", "status": "PASS" if len(spine_ids) > 0 else "FAIL", "detail": len(spine_ids)})
        preflight_rows.append({"check": "spine_route_count", "status": "PASS" if sum(1 for row in route_rows if row.get("selected_policy") == "spine_corridor_biased") == 20 else "FAIL", "detail": sum(1 for row in route_rows if row.get("selected_policy") == "spine_corridor_biased")})
        preflight_rows.append({"check": "route_review_html", "status": "PASS" if ROUTE_REVIEW_HTML.is_file() else "FAIL", "detail": rel(ROUTE_REVIEW_HTML)})
        preflight_status = "FAIL" if any(row["status"] == "FAIL" for row in preflight_rows) else ("WARNING" if warnings or manual_reviews else "PASS")
        write_csv(PREFLIGHT_REPORT, preflight_rows, ["check", "status", "detail"])
        write_json(
            PREFLIGHT_SUMMARY,
            {
                "generated_at": generated_at,
                "final_status": preflight_status,
                "active_net": rel(ACTIVE_NET),
                "route_count": len(route_rows),
                "selected_policy": "spine_corridor_biased",
                "corridor_spine_edge_count": len(spine_ids),
                "warning_count": warnings,
                "needs_manual_review_count": manual_reviews,
                "checks": preflight_rows,
            },
        )
        write_json(
            EMERGENCY_ROUTE_SUMMARY,
            {
                "generated_at": generated_at,
                "final_status": preflight_status,
                "active_net": rel(ACTIVE_NET),
                "route_count": len(route_rows),
                "selected_policy": "spine_corridor_biased",
                "corridor_spine_edge_count": len(spine_ids),
                "spine_length_warning_threshold": SPINE_LENGTH_WARNING_RATIO,
                "spine_needs_manual_review_threshold": SPINE_MANUAL_REVIEW_RATIO,
                "spine_min_length_ratio": SPINE_MIN_LENGTH_RATIO,
                "spine_min_consecutive_length_m": SPINE_MIN_CONSECUTIVE_LENGTH_M,
                "previous_reject_reason_count": len(previous_rejects),
                "previous_reject_reasons": previous_rejects,
                "warning_count": warnings,
                "needs_manual_review_count": manual_reviews,
                "outputs": [rel(path) for path in [CORRIDOR_SPINE_EDGES_CSV, EMERGENCY_ROUTES_CSV, EMERGENCY_ROUTES_XML, ROUTE_COMPARE_CSV, ACCIDENT_SCENARIOS_CSV, ROUTE_REVIEW_HTML, ROUTE_REVIEW_SCHEMA, PREFLIGHT_SUMMARY, PREFLIGHT_REPORT]],
            },
        )
        lines.extend(
            [
                f"route_count: {len(route_rows)}",
                f"selected_policy: spine_corridor_biased",
                f"corridor_spine_edge_count: {len(spine_ids)}",
                f"warning_count: {warnings}",
                f"needs_manual_review_count: {manual_reviews}",
                f"preflight_status: {preflight_status}",
                f"route_review_html: {rel(ROUTE_REVIEW_HTML)}",
            ]
        )
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if preflight_status in {"PASS", "WARNING"} else 1
    except (Step07Error, OSError, ET.ParseError, ValueError, RuntimeError, ImportError) as exc:
        lines.extend(["Status: FAIL", str(exc)])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
