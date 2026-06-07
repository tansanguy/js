#!/usr/bin/env python3
"""Build a B04/B4 destination-arrival animation from SUMO FCD output."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIZ_04_1_DIR = PROJECT_ROOT / "04-1 Visualize"
if str(VIZ_04_1_DIR) not in sys.path:
    sys.path.insert(0, str(VIZ_04_1_DIR))

from utils.fcd_parser import FcdResult, lane_to_edge, parse_fcd  # noqa: E402


RUN_ROOT = PROJECT_ROOT / "runs/compact_v9_B4"
HTML_OUTPUT_DIR = PROJECT_ROOT / "results/html"
DEFAULT_RUN_ID = "b4_thold_seed1_fcd_viz"
DEFAULT_OUTPUT_JSON = HTML_OUTPUT_DIR / "compact_v9_b04_b4_destination_animation.json"
DEFAULT_OUTPUT_HTML = HTML_OUTPUT_DIR / "compact_v9_b04_b4_destination_animation.html"
DEFAULT_NET_FILE = PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
DEFAULT_ROUTE_XML = PROJECT_ROOT / "data_prepared/compact_v9/routes/firetruck_to_seoul_station_front.rou.xml"
EV_ID = "emergency_0"
TARGET_LABEL = "Seoul Station Front"
MAP_TILES = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_ATTRIBUTION = "&copy; OpenStreetMap contributors"
MODE_COLORS = {"B04": "#dc2626", "B4": "#2563eb"}


class B04B4AnimationError(RuntimeError):
    """Expected visualization failure."""


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {"", None}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def bool_from_tripinfo(value: Any) -> bool:
    return value not in {"", None, "-1"}


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * 111_320.0
    dlon = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dlat, dlon)


def route_edges_from_xml(path: Path) -> list[str]:
    if not path.is_file():
        return []
    route = ET.parse(path).getroot().find(".//route")
    return str(route.get("edges") if route is not None else "").split()


def polyline_length_m(coords: list[list[float]]) -> float:
    return sum(
        meters_between(a[0], a[1], b[0], b[1])
        for a, b in zip(coords, coords[1:])
    )


def route_path_from_polyline(coords: list[list[float]]) -> list[dict[str, float]]:
    path: list[dict[str, float]] = []
    total = 0.0
    previous: list[float] | None = None
    for point in coords:
        if previous is not None:
            total += meters_between(previous[0], previous[1], point[0], point[1])
        path.append({"lat": point[0], "lon": point[1], "dist_m": round(total, 6)})
        previous = point
    return path


def route_position_at_distance(route_path: list[dict[str, float]], distance_m: float) -> dict[str, float]:
    if not route_path:
        return {"lat": 0.0, "lon": 0.0, "angle": 0.0}
    if distance_m <= route_path[0]["dist_m"]:
        return {"lat": route_path[0]["lat"], "lon": route_path[0]["lon"], "angle": 0.0}
    for prev_point, next_point in zip(route_path, route_path[1:]):
        if distance_m <= next_point["dist_m"]:
            span = max(next_point["dist_m"] - prev_point["dist_m"], 1.0e-9)
            ratio = (distance_m - prev_point["dist_m"]) / span
            lat = prev_point["lat"] + (next_point["lat"] - prev_point["lat"]) * ratio
            lon = prev_point["lon"] + (next_point["lon"] - prev_point["lon"]) * ratio
            angle = math.degrees(math.atan2(next_point["lon"] - prev_point["lon"], next_point["lat"] - prev_point["lat"]))
            return {"lat": round(lat, 6), "lon": round(lon, 6), "angle": round((angle + 360.0) % 360.0, 1)}
    last = route_path[-1]
    return {"lat": last["lat"], "lon": last["lon"], "angle": 0.0}


def route_geometry_from_net(net_file: Path, route_xml: Path) -> dict[str, Any]:
    edges = route_edges_from_xml(route_xml)
    if not edges or not net_file.is_file():
        return {"coords": [], "path": [], "edge_measures": {}, "length_m": 0.0}
    try:
        import sumolib  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise B04B4AnimationError("sumolib_required_for_route_geometry_animation") from exc

    net = sumolib.net.readNet(str(net_file))
    coords: list[list[float]] = []
    edge_measures: dict[str, dict[str, Any]] = {}
    route_distance = 0.0
    for edge_id in edges:
        try:
            edge = net.getEdge(edge_id)
        except Exception:
            continue
        points: list[list[float]] = []
        for x, y in edge.getShape():
            lon, lat = net.convertXY2LonLat(float(x), float(y))
            points.append([round(float(lat), 6), round(float(lon), 6)])
        if not points:
            continue
        shape_length = polyline_length_m(points)
        edge_measures[edge_id] = {
            "start_m": route_distance,
            "shape_length_m": shape_length,
            "sumo_length_m": float(edge.getLength()),
        }
        route_distance += shape_length
        if coords and coords[-1] == points[0]:
            coords.extend(points[1:])
        else:
            coords.extend(points)
    route_path = route_path_from_polyline(coords)
    return {
        "coords": coords,
        "path": route_path,
        "edge_measures": edge_measures,
        "length_m": round(route_path[-1]["dist_m"], 6) if route_path else 0.0,
    }


def route_polyline_from_net(net_file: Path, route_xml: Path) -> list[list[float]]:
    return route_geometry_from_net(net_file, route_xml)["coords"]


def projected_route_distance(point: Any, route_geometry: dict[str, Any], previous_distance_m: float) -> float:
    edge_info = route_geometry.get("edge_measures", {}).get(point.edge_id)
    if edge_info:
        sumo_length = max(safe_float(edge_info.get("sumo_length_m")), 1.0e-9)
        shape_length = max(safe_float(edge_info.get("shape_length_m")), 0.0)
        ratio = max(0.0, min(1.0, safe_float(getattr(point, "lane_pos_m", 0.0)) / sumo_length))
        distance = safe_float(edge_info.get("start_m")) + ratio * shape_length
    else:
        # Internal junction lanes and skipped short edges should not pull the marker
        # away from the confirmed route. Keep monotonic progress on the route.
        step_distance = max(0.0, safe_float(getattr(point, "speed_kmh", 0.0)) / 3.6)
        distance = previous_distance_m + step_distance
    route_length = safe_float(route_geometry.get("length_m"))
    return max(previous_distance_m, min(distance, route_length if route_length else distance))


def parse_tripinfo(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise B04B4AnimationError(f"missing_tripinfo:{rel(path)}")
    try:
        for _event, elem in ET.iterparse(path, events=("end",)):
            if elem.tag == "tripinfo" and elem.get("id") == EV_ID:
                return dict(elem.attrib)
            elem.clear()
    except ET.ParseError:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped.startswith("<tripinfo ") or not stripped.endswith("/>"):
                continue
            try:
                elem = ET.fromstring(stripped)
            except ET.ParseError:
                continue
            if elem.get("id") == EV_ID:
                return dict(elem.attrib)
    raise B04B4AnimationError(f"missing_emergency_tripinfo:{rel(path)}")


def tripinfo_or_fcd_fallback(path: Path, fcd: FcdResult, mode: str) -> dict[str, str]:
    try:
        return parse_tripinfo(path)
    except B04B4AnimationError as exc:
        if not str(exc).startswith("missing_emergency_tripinfo:"):
            raise
        points = fcd.emergency.points
        if not points:
            raise
        duration = max(0.0, points[-1].time - fcd.emergency.start_time)
        return {
            "id": EV_ID,
            "depart": str(fcd.emergency.start_time),
            "arrival": "-1",
            "arrivalLane": points[-1].edge_id,
            "arrivalPos": "",
            "arrivalSpeed": "0",
            "duration": str(duration),
            "waitingTime": "0",
            "waitingCount": "0",
            "timeLoss": "0",
            "fallback_reason": f"{mode}_missing_emergency_tripinfo_partial_fcd",
        }


def emergency_pos_by_time(fcd: FcdResult) -> dict[float, tuple[float, float]]:
    return {point.time: (point.lat, point.lon) for point in fcd.emergency.points}


def interp_pos(fcd: FcdResult, t_abs: float) -> tuple[float, float]:
    points = fcd.emergency.points
    if not points:
        return (0.0, 0.0)
    if t_abs <= points[0].time:
        return (points[0].lat, points[0].lon)
    if t_abs >= points[-1].time:
        return (points[-1].lat, points[-1].lon)
    for index in range(1, len(points)):
        prev_point = points[index - 1]
        next_point = points[index]
        if prev_point.time <= t_abs <= next_point.time:
            span = next_point.time - prev_point.time
            ratio = 0.0 if span == 0 else (t_abs - prev_point.time) / span
            return (
                prev_point.lat + (next_point.lat - prev_point.lat) * ratio,
                prev_point.lon + (next_point.lon - prev_point.lon) * ratio,
            )
    return (points[-1].lat, points[-1].lon)


def build_mode_payload(
    *,
    mode: str,
    fcd: FcdResult,
    tripinfo: dict[str, str],
    bg_radius_m: float,
    route_geometry: dict[str, Any],
    planned_edges: list[str],
) -> dict[str, Any]:
    points = fcd.emergency.points
    if not points:
        raise B04B4AnimationError(f"empty_emergency_fcd:{mode}")

    anchor = fcd.emergency.start_time
    route_path = route_geometry.get("path", [])
    route_geometry_length_m = safe_float(route_geometry.get("length_m"))
    route_length_m = safe_float(tripinfo.get("routeLength"), route_geometry_length_m)

    series: list[dict[str, Any]] = []
    previous_route_distance = 0.0
    previous_raw_point: Any | None = None
    for point in points:
        if previous_raw_point is not None:
            previous_route_distance += meters_between(previous_raw_point.lat, previous_raw_point.lon, point.lat, point.lon)
        route_distance = min(previous_route_distance, route_length_m or route_geometry_length_m or previous_route_distance)
        previous_route_distance = route_distance
        series.append({
            "t_rel": round(point.time - anchor, 2),
            "lat": round(point.lat, 6),
            "lon": round(point.lon, 6),
            "raw_lat": round(point.lat, 6),
            "raw_lon": round(point.lon, 6),
            "speed_kmh": round(point.speed_kmh, 2),
            "angle": round(point.angle, 1),
            "raw_angle": round(point.angle, 1),
            "dist_m": round(route_distance, 2),
            "edge": point.edge_id,
            "lane": getattr(point, "lane_id", ""),
            "lane_pos_m": round(safe_float(getattr(point, "lane_pos_m", 0.0)), 2),
            "position_source": "fcd_raw_geo",
        })
        previous_raw_point = point

    arrival_lane = tripinfo.get("arrivalLane", "")
    arrival_edge = lane_to_edge(arrival_lane)
    travel_time_sec = safe_float(tripinfo.get("duration"), points[-1].time - anchor)
    arrival_speed_kmh = round(safe_float(tripinfo.get("arrivalSpeed")) * 3.6, 2)
    if (
        route_path
        and arrival_edge
        and travel_time_sec > series[-1]["t_rel"]
        and safe_float(tripinfo.get("arrival"), -1.0) >= 0.0
    ):
        route_position = route_position_at_distance(route_path, route_geometry_length_m)
        arrival_gap_m = meters_between(series[-1]["lat"], series[-1]["lon"], route_position["lat"], route_position["lon"])
        position_source = "route_end_arrival"
        if arrival_gap_m > 50.0:
            route_position = {
                "lat": series[-1]["lat"],
                "lon": series[-1]["lon"],
                "angle": series[-1]["angle"],
            }
            position_source = "fcd_raw_last_arrival_fallback"
        series.append({
            "t_rel": round(travel_time_sec, 2),
            "lat": route_position["lat"],
            "lon": route_position["lon"],
            "raw_lat": route_position["lat"],
            "raw_lon": route_position["lon"],
            "speed_kmh": arrival_speed_kmh,
            "angle": route_position["angle"],
            "raw_angle": route_position["angle"],
            "dist_m": round(max(series[-1]["dist_m"], route_length_m or route_geometry_length_m), 2),
            "edge": arrival_edge,
            "lane": arrival_lane,
            "lane_pos_m": safe_float(tripinfo.get("arrivalPos")),
            "position_source": position_source,
            "synthetic_arrival": True,
        })

    emergency_positions = emergency_pos_by_time(fcd)
    background = []
    for snap in fcd.background:
        ref = emergency_positions.get(safe_float(snap.get("time")))
        if ref is None:
            continue
        elat, elon = ref
        nearby = [
            {
                "lat": round(vehicle["lat"], 6),
                "lon": round(vehicle["lon"], 6),
                "speed_kmh": round(safe_float(vehicle.get("speed_kmh")), 2),
                "angle": round(safe_float(vehicle.get("angle")), 1),
            }
            for vehicle in snap["vehicles"]
            if meters_between(elat, elon, vehicle["lat"], vehicle["lon"]) <= bg_radius_m
        ]
        if nearby:
            background.append({"t_rel": round(safe_float(snap.get("time")) - anchor, 2), "vehicles": nearby})

    observed_edges: list[str] = []
    for point in points:
        if point.edge_id and not point.edge_id.startswith(":") and (not observed_edges or observed_edges[-1] != point.edge_id):
            observed_edges.append(point.edge_id)
    planned_set = set(planned_edges)
    missing_edges = [edge for edge in planned_edges if edge not in observed_edges]
    extra_edges = [edge for edge in observed_edges if edge not in planned_set]

    final_edge = series[-1]["edge"]
    route_integrity = {
        "tripinfo_reroute_no": safe_float(tripinfo.get("rerouteNo"), 0.0),
        "arrival_edge": arrival_edge,
        "final_animation_edge": final_edge,
        "planned_edge_count": len(planned_edges),
        "observed_edge_count": len(observed_edges),
        "missing_sampled_planned_edge_count": len(missing_edges),
        "extra_non_planned_edge_count": len(extra_edges),
        "missing_sampled_planned_edges": missing_edges,
        "extra_non_planned_edges": extra_edges,
        "note": "FCD samples can skip short edges; tripinfo rerouteNo=0 and extra_non_planned_edge_count=0 are the route-integrity gate.",
    }

    speeds = [point.speed_kmh for point in points]
    return {
        "mode": mode,
        "emergency_id": fcd.emergency_id,
        "depart_time_sec": anchor,
        "travel_time_sec": round(travel_time_sec, 2),
        "arrival_time_sec": safe_float(tripinfo.get("arrival")),
        "route_length_m": round(route_length_m, 2),
        "route_geometry_length_m": round(route_geometry_length_m, 2),
        "avg_speed_kmh": round(route_length_m / travel_time_sec * 3.6, 2) if travel_time_sec else 0.0,
        "max_speed_kmh": round(max(speeds), 2) if speeds else 0.0,
        "waiting_time_sec": safe_float(tripinfo.get("waitingTime")),
        "waiting_count": int(safe_float(tripinfo.get("waitingCount"))),
        "time_loss_sec": safe_float(tripinfo.get("timeLoss")),
        "arrival_edge": arrival_edge,
        "arrival_lane": arrival_lane,
        "arrival_pos": safe_float(tripinfo.get("arrivalPos")),
        "arrival_speed_kmh": arrival_speed_kmh,
        "emergency_arrived": bool_from_tripinfo(tripinfo.get("arrival")),
        "emergency_teleport": bool(tripinfo.get("vaporized")),
        "route_integrity": route_integrity,
        "emergency": series,
        "background": background,
        "route_polyline": route_geometry.get("coords") or [[point["lat"], point["lon"]] for point in series],
        "route_path": route_path,
        "destination": {"lat": series[-1]["lat"], "lon": series[-1]["lon"], "label": TARGET_LABEL},
    }


def load_signal_events(path: Path, fcd: FcdResult) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    anchor = fcd.emergency.start_time
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if not row.get("tls_id"):
                continue
            t_abs = safe_float(row.get("time"), -1.0)
            if t_abs < anchor:
                continue
            lat, lon = interp_pos(fcd, t_abs)
            events.append({
                "t_rel": round(t_abs - anchor, 2),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "stage": row.get("stage", ""),
                "action_type": row.get("action_type", ""),
                "tls_id": row.get("tls_id", ""),
                "movement_id": row.get("movement_id", ""),
                "trigger_reason": row.get("trigger_reason", ""),
                "target_phase": row.get("target_phase", ""),
                "previous_phase": row.get("previous_phase", ""),
            })
    return sorted(events, key=lambda item: item["t_rel"])


def bounds_for(modes: list[dict[str, Any]]) -> dict[str, float]:
    lats: list[float] = []
    lons: list[float] = []
    for mode in modes:
        for lat, lon in mode["route_polyline"]:
            lats.append(lat)
            lons.append(lon)
    return {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
        "center_lat": (min(lats) + max(lats)) / 2.0,
        "center_lon": (min(lons) + max(lons)) / 2.0,
    }


def resolve_b4_repeat_dir(root: Path, b4_parameter_id: str | None) -> Path:
    if b4_parameter_id:
        return root / "B4" / b4_parameter_id / "repeat_001"
    default_dir = root / "B4/B4_MVP_DEFAULT/repeat_001"
    if default_dir.is_dir():
        return default_dir
    candidates = sorted((root / "B4").glob("*/repeat_001"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise B04B4AnimationError(f"missing_b4_repeat_dir:{rel(root / 'B4')}")
    names = ",".join(candidate.parent.name for candidate in candidates[:8])
    raise B04B4AnimationError(f"ambiguous_b4_repeat_dir:{names}")


def run_paths(run_id: str, run_root: Path, b4_parameter_id: str | None) -> dict[str, dict[str, Path]]:
    root = run_root / run_id
    b4_repeat_dir = resolve_b4_repeat_dir(root, b4_parameter_id)
    return {
        "B04": {
            "run_dir": root / "B04/no_control/repeat_001",
            "fcd": root / "B04/no_control/repeat_001/fcd.xml",
            "tripinfo": root / "B04/no_control/repeat_001/tripinfo.xml",
        },
        "B4": {
            "run_dir": b4_repeat_dir,
            "fcd": b4_repeat_dir / "fcd.xml",
            "tripinfo": b4_repeat_dir / "tripinfo.xml",
            "signal_events": b4_repeat_dir / "signal_events.csv",
        },
    }


def build_doc(args: argparse.Namespace) -> dict[str, Any]:
    paths = run_paths(args.run_id, args.run_root, args.b4_parameter_id)
    for mode in ("B04", "B4"):
        if not paths[mode]["fcd"].is_file():
            raise B04B4AnimationError(f"missing_fcd:{rel(paths[mode]['fcd'])}")

    b04_fcd = parse_fcd(paths["B04"]["fcd"], mode="B04")
    b4_fcd = parse_fcd(paths["B4"]["fcd"], mode="B4")
    b04_tripinfo = tripinfo_or_fcd_fallback(paths["B04"]["tripinfo"], b04_fcd, "B04")
    b4_tripinfo = tripinfo_or_fcd_fallback(paths["B4"]["tripinfo"], b4_fcd, "B4")
    planned_edges = route_edges_from_xml(args.route_xml)
    route_geometry = route_geometry_from_net(args.net_file, args.route_xml)
    b04_payload = build_mode_payload(
        mode="B04",
        fcd=b04_fcd,
        tripinfo=b04_tripinfo,
        bg_radius_m=args.bg_radius_m,
        route_geometry=route_geometry,
        planned_edges=planned_edges,
    )
    b4_payload = build_mode_payload(
        mode="B4",
        fcd=b4_fcd,
        tripinfo=b4_tripinfo,
        bg_radius_m=args.bg_radius_m,
        route_geometry=route_geometry,
        planned_edges=planned_edges,
    )
    b4_payload["signal_events"] = load_signal_events(paths["B4"]["signal_events"], b4_fcd)

    return {
        "schema": "compact_v9_b04_b4_destination_animation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "meta": {
            "bg_radius_m": args.bg_radius_m,
            "b4_parameter_id": args.b4_parameter_label or paths["B4"]["run_dir"].parent.name,
            "b4_run_folder": paths["B4"]["run_dir"].parent.name,
            "target_edge": b4_payload["arrival_edge"],
            "target_label": TARGET_LABEL,
            "net_file": rel(args.net_file),
            "route_xml": rel(args.route_xml),
            "route_geometry_source": "corrected SUMO net + EV route XML",
            "source": {
                "B04": {key: rel(value) for key, value in paths["B04"].items()},
                "B4": {key: rel(value) for key, value in paths["B4"].items()},
            },
            "bounds": bounds_for([b04_payload, b4_payload]),
        },
        "modes": {"B04": b04_payload, "B4": b4_payload},
    }


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  :root{--b04:#dc2626;--b4:#2563eb;--panel:#111827;--line:#263244;}
  html,body{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0b1220;color:#f8fafc;}
  .wrap{display:grid;grid-template-rows:auto 1fr 190px;height:100vh;min-height:680px;}
  header{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:10px 14px;background:#111827;border-bottom:1px solid #263244;}
  h1{font-size:16px;line-height:1.2;margin:0;font-weight:750;}
  .controls{display:flex;align-items:center;gap:10px;flex:1;min-width:320px;}
  button{height:32px;border:0;border-radius:6px;background:#2563eb;color:#fff;padding:0 13px;font-weight:700;cursor:pointer;}
  button.secondary{background:#374151;}
  select{height:30px;border-radius:6px;border:1px solid #475569;background:#111827;color:#fff;}
  input[type=range]{flex:1;min-width:160px;}
  .clock{font-variant-numeric:tabular-nums;min-width:170px;color:#cbd5e1;font-size:13px;}
  .maps{display:grid;grid-template-columns:1fr 1fr;min-height:0;}
  .panel{position:relative;min-width:0;border-right:1px solid #263244;}
  .map{position:absolute;inset:0;background:#1e293b;}
  .tag{position:absolute;z-index:500;left:10px;top:10px;background:rgba(15,23,42,.88);border:1px solid rgba(148,163,184,.28);border-radius:8px;padding:9px 11px;line-height:1.45;font-size:12px;max-width:330px;}
  .tag b{display:block;font-size:14px;margin-bottom:2px;}
  .tag span{font-variant-numeric:tabular-nums;}
  .bottom{display:grid;grid-template-columns:330px 1fr;background:#0f172a;border-top:1px solid #263244;}
  .overview{position:relative;border-right:1px solid #263244;}
  .overview .label{position:absolute;z-index:500;left:8px;top:8px;background:rgba(15,23,42,.82);padding:4px 7px;border-radius:5px;color:#cbd5e1;font-size:12px;}
  .stats{padding:10px 14px;overflow:auto;}
  .stats h2{font-size:13px;margin:0 0 8px;color:#cbd5e1;}
  .grid{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:8px;}
  .metric{border:1px solid #263244;border-radius:8px;padding:8px;background:#111827;}
  .metric small{display:block;color:#94a3b8;font-size:11px;margin-bottom:2px;}
  .metric strong{display:block;font-size:18px;font-variant-numeric:tabular-nums;line-height:1.15;overflow-wrap:anywhere;}
  .metric strong.small{font-size:13px;line-height:1.25;}
  .leaflet-container{background:#1e293b;}
  @media (max-width:900px){.maps{grid-template-columns:1fr;}.wrap{grid-template-rows:auto 1fr 240px;}.bottom{grid-template-columns:1fr;}.overview{display:none}.grid{grid-template-columns:repeat(2,minmax(120px,1fr));}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>B04/B4 Emergency Destination Animation</h1>
    <div class="controls">
      <button id="play">Play</button>
      <button class="secondary" id="reset">Reset</button>
      <input type="range" id="seek" min="0" max="1000" value="0">
      <span class="clock" id="clock">t = 0.0s</span>
      <select id="rate"><option>1</option><option selected>4</option><option>8</option><option>16</option></select>
    </div>
  </header>
  <main class="maps">
    <section class="panel"><div class="map" id="mapB04"></div><div class="tag" id="tagB04"></div></section>
    <section class="panel"><div class="map" id="mapB4"></div><div class="tag" id="tagB4"></div></section>
  </main>
  <section class="bottom">
    <div class="overview"><div class="map" id="mapOverview"></div><div class="label">Route overview</div></div>
    <div class="stats">
      <h2>Run __RUN_ID__ / target edge __TARGET_EDGE__</h2>
      <div class="grid" id="statsGrid"></div>
    </div>
  </section>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = __DATA__;
const COLORS = {B04:"__B04COLOR__", B4:"__B4COLOR__"};
const TILES = "__TILES__";
const ATTR = "__ATTR__";
const MODES = ["B04", "B4"];
const FOLLOW_ZOOM = 17;
let now = 0, playing = false, rate = 4, lastFrame = null;
const tMax = Math.max(...MODES.map(mode => DATA.modes[mode].travel_time_sec));

function speedColor(kmh){
  if(kmh < 5) return "#991b1b";
  if(kmh < 15) return "#dc2626";
  if(kmh < 30) return "#f59e0b";
  if(kmh < 45) return "#10b981";
  return "#2563eb";
}
function indexAt(points,t){
  let lo=0, hi=points.length-1, result=0;
  while(lo<=hi){const mid=(lo+hi)>>1;if(points[mid].t_rel<=t){result=mid;lo=mid+1;}else{hi=mid-1;}}
  return result;
}
function lerp(a,b,f){return a+(b-a)*f;}
function pointAtRouteDistance(routePath, distM){
  if(!routePath || !routePath.length) return null;
  if(distM <= routePath[0].dist_m) return routePath[0];
  for(let i=1;i<routePath.length;i++){
    const a=routePath[i-1], b=routePath[i];
    if(distM <= b.dist_m){
      const span=Math.max(b.dist_m-a.dist_m, 1e-9);
      const f=(distM-a.dist_m)/span;
      return {lat:lerp(a.lat,b.lat,f), lon:lerp(a.lon,b.lon,f), dist_m:distM};
    }
  }
  return routePath[routePath.length-1];
}
function stateAt(data,t){
  const points=data.emergency;
  if(!points.length) return null;
  if(t<=points[0].t_rel) return points[0];
  if(t>=points[points.length-1].t_rel) {
    const last=points[points.length-1];
    return {...last, arrived:true};
  }
  const index=indexAt(points,t), a=points[index], b=points[Math.min(index+1,points.length-1)];
  const span=b.t_rel-a.t_rel, f=span ? (t-a.t_rel)/span : 0;
  const distM=lerp(a.dist_m,b.dist_m,f);
  return {
    t_rel:t,
    lat:lerp(a.lat,b.lat,f),
    lon:lerp(a.lon,b.lon,f),
    speed_kmh:lerp(a.speed_kmh,b.speed_kmh,f),
    dist_m:distM,
    edge:a.edge
  };
}
function makeMap(id){
  const map=L.map(id,{zoomControl:false,attributionControl:false,preferCanvas:true});
  L.tileLayer(TILES,{maxZoom:19,attribution:ATTR}).addTo(map);
  return map;
}
function bgByTime(modeData){
  const byTime = {};
  modeData.background.forEach(snap => byTime[Math.round(snap.t_rel)] = snap.vehicles);
  return byTime;
}
function makePanel(mode){
  const data=DATA.modes[mode];
  const map=makeMap("map"+mode);
  const bounds=L.latLngBounds(data.route_polyline);
  map.fitBounds(bounds,{padding:[24,24]});
  L.polyline(data.route_polyline,{color:COLORS[mode],weight:4,opacity:.48}).addTo(map);
  L.circleMarker([data.destination.lat,data.destination.lon],{radius:8,color:"#fff",weight:2,fillColor:"#16a34a",fillOpacity:1}).bindTooltip("Destination").addTo(map);
  const marker=L.circleMarker(data.route_polyline[0],{radius:9,color:"#fff",weight:2,fillColor:COLORS[mode],fillOpacity:1}).addTo(map);
  const bg=L.layerGroup().addTo(map);
  const events=L.layerGroup().addTo(map);
  if(mode === "B4"){
    data.signal_events.slice(0,200).forEach(event => L.circleMarker([event.lat,event.lon],{radius:3,color:"#f8fafc",weight:1,fillColor:"#f97316",fillOpacity:.85}).bindTooltip(event.action_type || "signal").addTo(events));
  }
  return {mode,data,map,marker,bg,bgByT:bgByTime(data)};
}
const panels = Object.fromEntries(MODES.map(mode => [mode, makePanel(mode)]));
const overview=makeMap("mapOverview");
overview.fitBounds(L.latLngBounds(DATA.modes.B04.route_polyline),{padding:[14,14]});
MODES.forEach(mode => L.polyline(DATA.modes[mode].route_polyline,{color:COLORS[mode],weight:3,opacity:.55}).addTo(overview));
const ovDots=Object.fromEntries(MODES.map(mode => [mode, L.circleMarker(DATA.modes[mode].route_polyline[0],{radius:6,color:"#fff",weight:1,fillColor:COLORS[mode],fillOpacity:1}).addTo(overview)]));

function renderPanel(panel){
  const mode=panel.mode, data=panel.data, capped=Math.min(now,data.travel_time_sec);
  const st=stateAt(data,capped);
  if(!st) return;
  const point=[st.lat,st.lon], arrived=now>=data.travel_time_sec;
  panel.marker.setLatLng(point).setStyle({fillColor:arrived ? "#16a34a" : speedColor(st.speed_kmh)});
  panel.map.setView(point,FOLLOW_ZOOM,{animate:false});
  ovDots[mode].setLatLng(point);
  panel.bg.clearLayers();
  const nearby=panel.bgByT[Math.round(capped)] || [];
  nearby.forEach(v => L.circleMarker([v.lat,v.lon],{radius:3.5,color:"#cbd5e1",weight:1,fillColor:"#94a3b8",fillOpacity:.75}).addTo(panel.bg));
  const routeLen=data.route_geometry_length_m || data.route_length_m;
  const progress=Math.min(100,Math.round(st.dist_m / routeLen * 100));
  document.getElementById("tag"+mode).innerHTML =
    `<b style="color:${COLORS[mode]}">${mode} ${arrived ? "arrived" : "en route"}</b>` +
    `time <span>${capped.toFixed(1)}</span> / <span>${data.travel_time_sec.toFixed(0)}</span>s<br>` +
    `speed <span>${arrived ? 0 : st.speed_kmh.toFixed(1)}</span> km/h / progress <span>${progress}</span>%<br>` +
    `edge <span>${arrived ? data.arrival_edge : st.edge}</span><br>` +
    `nearby vehicles <span>${nearby.length}</span>`;
}
function renderStats(){
  const grid=document.getElementById("statsGrid");
  const b04=DATA.modes.B04, b4=DATA.modes.B4;
  const delta=b4.travel_time_sec-b04.travel_time_sec;
  const improvement=b04.travel_time_sec-b4.travel_time_sec;
  const thetaLabel = DATA.meta.b4_parameter_id.replace(/^bo_best_s1forced_n3m12_seed\d+_r\d+_/, "").replaceAll("_", " ");
  const b04ri = b04.route_integrity || {};
  const b4ri = b4.route_integrity || {};
  const rows=[
    ["B04 travel", `${b04.travel_time_sec.toFixed(0)}s`],
    ["B4 travel", `${b4.travel_time_sec.toFixed(0)}s`],
    ["B4 minus B04", `${delta.toFixed(0)}s`],
    ["B4 improvement", `${improvement.toFixed(0)}s`],
    ["B04 waiting", `${b04.waiting_time_sec.toFixed(0)}s`],
    ["B4 waiting", `${b4.waiting_time_sec.toFixed(0)}s`],
    ["B4 signal events", `${(b4.signal_events||[]).length}`],
    ["B04 reroute / extra", `${b04ri.tripinfo_reroute_no ?? ""} / ${b04ri.extra_non_planned_edge_count ?? ""}`],
    ["B4 reroute / extra", `${b4ri.tripinfo_reroute_no ?? ""} / ${b4ri.extra_non_planned_edge_count ?? ""}`],
    ["Route geometry", DATA.meta.route_geometry_source || "FCD samples"],
    ["B4 theta", thetaLabel],
  ];
  grid.innerHTML=rows.map(([label,value])=>`<div class="metric"><small>${label}</small><strong class="${String(value).length > 18 ? "small" : ""}">${value}</strong></div>`).join("");
}
function render(){
  MODES.forEach(mode => renderPanel(panels[mode]));
  document.getElementById("seek").value = Math.round(now / tMax * 1000);
  document.getElementById("clock").textContent = `t = ${now.toFixed(1)}s / ${tMax.toFixed(0)}s`;
}
function frame(ts){
  if(!playing) return;
  if(lastFrame !== null){
    now += (ts-lastFrame)/1000*rate;
    if(now >= tMax){now=tMax;playing=false;document.getElementById("play").textContent="Play";}
  }
  lastFrame=ts;render();
  if(playing) requestAnimationFrame(frame);
}
document.getElementById("play").onclick=function(){
  if(now >= tMax) now=0;
  playing=!playing;
  this.textContent=playing ? "Pause" : "Play";
  lastFrame=null;
  if(playing) requestAnimationFrame(frame);
};
document.getElementById("reset").onclick=function(){now=0;playing=false;document.getElementById("play").textContent="Play";render();};
document.getElementById("rate").onchange=function(){rate=parseFloat(this.value);};
document.getElementById("seek").oninput=function(){now=parseFloat(this.value)/1000*tMax;render();};
renderStats();
setTimeout(()=>{MODES.forEach(mode=>panels[mode].map.invalidateSize());overview.invalidateSize();render();},200);
render();
</script>
</body>
</html>
"""


def write_html(doc: dict[str, Any], output_path: Path) -> None:
    html = (
        HTML_TEMPLATE
        .replace("__TITLE__", "Compact V9 B04/B4 Destination Animation")
        .replace("__RUN_ID__", str(doc["run_id"]))
        .replace("__TARGET_EDGE__", str(doc["meta"]["target_edge"]))
        .replace("__DATA__", json.dumps(doc, ensure_ascii=False))
        .replace("__TILES__", MAP_TILES)
        .replace("__ATTR__", MAP_ATTRIBUTION)
        .replace("__B04COLOR__", MODE_COLORS["B04"])
        .replace("__B4COLOR__", MODE_COLORS["B4"])
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build B04/B4 destination animation from FCD.")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--run-root", type=Path, default=RUN_ROOT)
    parser.add_argument("--b4-parameter-id", default=None)
    parser.add_argument("--b4-parameter-label", default=None)
    parser.add_argument("--bg-radius-m", type=float, default=250.0)
    parser.add_argument("--net-file", type=Path, default=DEFAULT_NET_FILE)
    parser.add_argument("--route-xml", type=Path, default=DEFAULT_ROUTE_XML)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-html", type=Path, default=DEFAULT_OUTPUT_HTML)
    args = parser.parse_args(argv)
    try:
        doc = build_doc(args)
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_html(doc, args.output_html)
    except (B04B4AnimationError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"json": rel(args.output_json), "html": rel(args.output_html)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
