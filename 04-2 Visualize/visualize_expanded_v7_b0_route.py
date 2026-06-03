#!/usr/bin/env python3
"""Visualize the expanded-v7 B0 firetruck route without editing 04/04-1."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VISUALIZE_04_1 = PROJECT_ROOT / "04-1 Visualize"
if str(VISUALIZE_04_1) not in sys.path:
    sys.path.insert(0, str(VISUALIZE_04_1))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.animation_builder import build_animated_single_map_html  # noqa: E402


DEFAULT_LATEST_JSON = PROJECT_ROOT / "results/metrics/expanded_v7_b0/latest.json"
DEFAULT_ACCEPTED_ROUTES = PROJECT_ROOT / "data_prepared/expanded_v7/routes/firetruck_accepted_routes.csv"
DEFAULT_NET = PROJECT_ROOT / "data_prepared/expanded_v7/net/jungbu_expanded_v7_passenger_lanes_repaired.net.xml"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results/html"
DEFAULT_BG_RADIUS_M = 250.0
DEFAULT_BG_SAMPLE_SEC = 2.0
DEFAULT_BG_LIMIT = 160


class ExpandedV7VisualizeError(RuntimeError):
    """Expected visualization failure."""


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def project_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else PROJECT_ROOT / value


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ExpandedV7VisualizeError(f"json_root_not_object:{rel(path)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def float_cell(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def bool_cell(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * 111_320.0
    dlon = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dlat, dlon)


def load_latest_b0_result(latest_json: Path) -> tuple[dict[str, Any], dict[str, str]]:
    latest = read_json(latest_json)
    results_csv = project_path(str(latest.get("results_csv", "")))
    if not results_csv.is_file():
        raise ExpandedV7VisualizeError(f"missing_results_csv:{rel(results_csv)}")
    rows = [
        row for row in read_csv(results_csv)
        if row.get("mode") == "B0"
        and row.get("parameter_id") == "no_control"
        and row.get("route_id") == "FIRETRUCK_TO_SEOUL_STATION_FRONT"
    ]
    if not rows:
        raise ExpandedV7VisualizeError(f"no_expanded_v7_b0_route_row:{rel(results_csv)}")
    return latest, rows[0]


def load_route_row(path: Path, route_id: str) -> dict[str, str]:
    rows = [row for row in read_csv(path) if row.get("route_id") == route_id]
    if not rows:
        raise ExpandedV7VisualizeError(f"missing_route_id:{route_id}:{rel(path)}")
    return rows[0]


def read_lonlat(elem: ET.Element) -> tuple[float, float] | None:
    lon = elem.get("lon") or elem.get("x")
    lat = elem.get("lat") or elem.get("y")
    if lon is None or lat is None:
        return None
    try:
        return float(lat), float(lon)
    except ValueError:
        return None


def lane_to_edge(lane_id: str) -> str:
    if not lane_id or lane_id.startswith(":"):
        return lane_id
    sep = lane_id.rfind("_")
    return lane_id[:sep] if sep > 0 else lane_id


def stream_fcd_payload(
    fcd_path: Path,
    result_row: dict[str, str],
    route_row: dict[str, str],
    bg_radius_m: float,
    bg_sample_sec: float,
    bg_limit: int,
) -> dict[str, Any]:
    emergency_id = ""
    emergency_points: list[dict[str, Any]] = []
    background: list[dict[str, Any]] = []
    anchor_time: float | None = None
    last_bg_sample = -10**9
    cumulative = 0.0
    previous: tuple[float, float] | None = None

    for _event, elem in ET.iterparse(fcd_path, events=("end",)):
        if elem.tag != "timestep":
            continue
        try:
            timestep = float(elem.get("time", "0") or 0.0)
        except ValueError:
            elem.clear()
            continue
        emergency_elem: ET.Element | None = None
        background_candidates: list[dict[str, Any]] = []
        for vehicle in elem.findall("vehicle"):
            vehicle_id = vehicle.get("id", "")
            coords = read_lonlat(vehicle)
            if coords is None:
                continue
            lat, lon = coords
            speed_kmh = float(vehicle.get("speed", "0") or 0.0) * 3.6
            angle = float(vehicle.get("angle", "0") or 0.0)
            if vehicle_id.startswith("emergency_"):
                emergency_elem = vehicle
                emergency_id = vehicle_id
            else:
                background_candidates.append({
                    "lat": lat,
                    "lon": lon,
                    "speed_kmh": round(speed_kmh, 2),
                    "angle": round(angle, 1),
                })
        if emergency_elem is not None:
            coords = read_lonlat(emergency_elem)
            if coords is not None:
                lat, lon = coords
                if anchor_time is None:
                    anchor_time = timestep
                if previous is not None:
                    cumulative += meters_between(previous[0], previous[1], lat, lon)
                previous = (lat, lon)
                emergency_points.append({
                    "t_rel": round(timestep - anchor_time, 2),
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "speed_kmh": round(float(emergency_elem.get("speed", "0") or 0.0) * 3.6, 2),
                    "angle": round(float(emergency_elem.get("angle", "0") or 0.0), 1),
                    "dist_m": round(cumulative, 2),
                    "edge": lane_to_edge(emergency_elem.get("lane", "")),
                })
                if timestep - last_bg_sample >= bg_sample_sec:
                    nearby = [
                        vehicle for vehicle in background_candidates
                        if meters_between(lat, lon, vehicle["lat"], vehicle["lon"]) <= bg_radius_m
                    ][:bg_limit]
                    if nearby:
                        background.append({"t_rel": round(timestep - anchor_time, 2), "vehicles": nearby})
                    last_bg_sample = timestep
        elem.clear()

    if not emergency_points:
        raise ExpandedV7VisualizeError(f"empty_emergency_fcd:{rel(fcd_path)}")
    route_length_m = float_cell(route_row, "route_length_m", emergency_points[-1]["dist_m"])
    observed_duration = emergency_points[-1]["t_rel"]
    result_travel_time = float_cell(result_row, "emergency_travel_time_sec", 0.0)
    travel_time = result_travel_time if result_travel_time > 0 else observed_duration
    if emergency_points[-1]["dist_m"] > 0 and route_length_m > 0:
        scale = route_length_m / emergency_points[-1]["dist_m"]
        for point in emergency_points:
            point["dist_m"] = round(point["dist_m"] * scale, 2)
    speeds = [point["speed_kmh"] for point in emergency_points]
    return {
        "mode": "B0",
        "route_id": result_row["route_id"],
        "destination_id": route_row.get("destination_id", ""),
        "label_ko": "Expanded V7 firetruck to Seoul Station front",
        "target_edge_id": route_row.get("target_edge_id", ""),
        "emergency_id": emergency_id,
        "travel_time_sec": round(travel_time, 2),
        "avg_speed_kmh": round(route_length_m / travel_time * 3.6, 2) if travel_time else 0.0,
        "max_speed_kmh": round(max(speeds), 2) if speeds else 0.0,
        "distance_m": round(route_length_m, 2),
        "depart_time_sec": anchor_time or 0.0,
        "final_status": result_row.get("final_status", ""),
        "warning_reason": result_row.get("warning_reason", ""),
        "route_error_count": result_row.get("route_error_count", ""),
        "emergency_teleport": bool_cell(result_row.get("emergency_teleport")),
        "emergency_arrived": bool_cell(result_row.get("emergency_arrived")),
        "background_vehicle_count": result_row.get("background_vehicle_count", ""),
        "remaining_vehicle_count": result_row.get("remaining_vehicle_count", ""),
        "destination": {
            "lat": float_cell(route_row, "lat"),
            "lon": float_cell(route_row, "lon"),
            "address": route_row.get("address", ""),
        },
        "emergency": emergency_points,
        "background": background,
        "route_polyline": [[point["lat"], point["lon"]] for point in emergency_points],
    }


def route_shape_from_net(net_file: Path, route_edges: list[str]) -> list[list[float]]:
    from sumolib.net import readNet

    sumo_net = readNet(str(net_file))
    points: list[list[float]] = []
    for edge_id in route_edges:
        edge = sumo_net.getEdge(edge_id)
        for x, y in edge.getShape():
            lon, lat = sumo_net.convertXY2LonLat(x, y)
            point = [round(lat, 6), round(lon, 6)]
            if not points or points[-1] != point:
                points.append(point)
    return points


def bounds(points: list[list[float]]) -> dict[str, float]:
    lats = [point[0] for point in points]
    lons = [point[1] for point in points]
    return {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
        "center_lat": (min(lats) + max(lats)) / 2.0,
        "center_lon": (min(lons) + max(lons)) / 2.0,
    }


def build_doc(
    latest: dict[str, Any],
    result_row: dict[str, str],
    route_row: dict[str, str],
    fcd_path: Path,
    bg_radius_m: float,
    bg_sample_sec: float,
    bg_limit: int,
) -> dict[str, Any]:
    route_length = float_cell(route_row, "route_length_m")
    payload = stream_fcd_payload(fcd_path, result_row, route_row, bg_radius_m, bg_sample_sec, bg_limit)
    doc = {
        "schema": "expanded_v7_b0_route_animation.v1",
        "meta": {
            "source_builder": "04-1 Visualize/utils/animation_builder.py:build_animated_single_map_html",
            "source_fcd": rel(fcd_path),
            "latest_json": rel(DEFAULT_LATEST_JSON),
            "run_id": latest.get("run_id", ""),
            "route_length_m": route_length,
            "bg_radius_m": bg_radius_m,
            "bg_sample_sec": bg_sample_sec,
            "bg_limit": bg_limit,
            "bounds": bounds(payload["route_polyline"]),
        },
        "modes": {"B0": payload},
    }
    return doc


def write_route_map_html(path: Path, doc: dict[str, Any], route_points: list[list[float]], result_row: dict[str, str]) -> None:
    actual_points = doc["modes"]["B0"]["route_polyline"]
    destination = doc["modes"]["B0"]["destination"]
    all_points = route_points + actual_points
    map_bounds = bounds(all_points)
    data = {
        "route": route_points,
        "actual": actual_points,
        "destination": destination,
        "bounds": map_bounds,
        "metrics": {
            "run_id": result_row.get("run_id", ""),
            "final_status": result_row.get("final_status", ""),
            "emergency_arrived": result_row.get("emergency_arrived", ""),
            "background_vehicle_count": result_row.get("background_vehicle_count", ""),
            "background_teleported": result_row.get("background_teleported", ""),
            "remaining_vehicle_count": result_row.get("remaining_vehicle_count", ""),
            "network_avg_speed_kmh": result_row.get("network_avg_speed_kmh", ""),
        },
    }
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Expanded V7 Firetruck Route Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body,#map{{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}}
.panel{{position:absolute;z-index:500;top:14px;left:14px;background:rgba(255,255,255,.94);border:1px solid #d1d5db;border-radius:8px;padding:12px 14px;max-width:380px;box-shadow:0 6px 20px rgba(15,23,42,.15);}}
.panel h1{{font-size:16px;margin:0 0 8px;}}
.panel p{{margin:4px 0;color:#475569;font-size:13px;line-height:1.35;}}
.key{{display:inline-block;width:16px;height:4px;border-radius:2px;vertical-align:middle;margin-right:6px;}}
code{{background:#f1f5f9;padding:1px 4px;border-radius:4px;}}
</style>
</head>
<body>
<div id="map"></div>
<section class="panel">
  <h1>Expanded V7 Firetruck Route</h1>
  <p><span class="key" style="background:#ef4444"></span>accepted full route</p>
  <p><span class="key" style="background:#f97316"></span>actual B0 FCD trace</p>
  <p>run <code>{html.escape(result_row.get("run_id", ""))}</code>, status <b>{html.escape(result_row.get("final_status", ""))}</b></p>
  <p>arrived={html.escape(result_row.get("emergency_arrived", ""))}, background={html.escape(result_row.get("background_vehicle_count", ""))}, remaining={html.escape(result_row.get("remaining_vehicle_count", ""))}</p>
</section>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = {json.dumps(data, ensure_ascii=False)};
const map = L.map("map", {{preferCanvas:true}});
L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{maxZoom:19, attribution:"© OpenStreetMap"}}).addTo(map);
const route = L.polyline(DATA.route, {{color:"#ef4444", weight:5, opacity:.82}}).addTo(map);
L.polyline(DATA.actual, {{color:"#f97316", weight:4, opacity:.72}}).addTo(map);
L.circleMarker(DATA.route[0], {{radius:9,color:"#fff",weight:2,fillColor:"#0ea5e9",fillOpacity:1}}).addTo(map).bindTooltip("START", {{permanent:true, direction:"right"}});
L.circleMarker(DATA.destination, {{radius:8,color:"#fff",weight:2,fillColor:"#111827",fillOpacity:1}}).addTo(map).bindTooltip("TARGET", {{permanent:true, direction:"left"}});
map.fitBounds(route.getBounds(), {{padding:[30,30]}});
</script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def write_index(path: Path, animation_html: Path, map_html: Path, doc: dict[str, Any]) -> None:
    payload = doc["modes"]["B0"]
    html_text = f"""<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Expanded V7 Route Visualization</title>
<style>
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;color:#111827;}}
main{{max-width:900px;margin:0 auto;padding:32px;}}
.card{{background:#fff;border:1px solid #d8dee8;border-radius:8px;padding:18px;margin:14px 0;}}
a.button{{display:inline-block;background:#2563eb;color:#fff;text-decoration:none;border-radius:6px;padding:9px 14px;margin-right:8px;}}
code{{background:#eef2f7;padding:2px 5px;border-radius:4px;}}
</style></head>
<body><main>
<h1>Expanded V7 Route Visualization</h1>
<section class="card">
<p>04-2 wrapper가 04-1의 B0 single-map animation builder를 호출해 생성했습니다. 04/04-1 파일은 수정하지 않았습니다.</p>
<p>route <code>{html.escape(payload["route_id"])}</code>, status <b>{html.escape(payload["final_status"])}</b>, arrived={payload["emergency_arrived"]}, background={html.escape(str(payload["background_vehicle_count"]))}</p>
<a class="button" href="{animation_html.name}">B0 애니메이션</a>
<a class="button" href="{map_html.name}">전체 경로 지도</a>
</section>
</main></body></html>
"""
    path.write_text(html_text, encoding="utf-8")


def build(
    latest_json: Path,
    accepted_routes: Path,
    net_file: Path,
    output_dir: Path,
    bg_radius_m: float,
    bg_sample_sec: float,
    bg_limit: int,
) -> dict[str, str]:
    latest, result_row = load_latest_b0_result(latest_json)
    route_row = load_route_row(accepted_routes, result_row["route_id"])
    run_dir = project_path(result_row.get("run_dir", ""))
    fcd_path = run_dir / "fcd.xml"
    if not fcd_path.is_file():
        raise ExpandedV7VisualizeError(f"missing_fcd:{rel(fcd_path)}")
    doc = build_doc(latest, result_row, route_row, fcd_path, bg_radius_m, bg_sample_sec, bg_limit)
    route_points = route_shape_from_net(net_file, route_row["route_edges"].split())

    json_path = output_dir / "expanded_v7_b0_firetruck_route_animation.json"
    animation_html = output_dir / "expanded_v7_b0_firetruck_route_animation.html"
    route_map_html = output_dir / "expanded_v7_b0_firetruck_route_map.html"
    index_html = output_dir / "expanded_v7_b0_firetruck_route_index.html"
    write_json(json_path, doc)
    build_animated_single_map_html(doc, animation_html, "Expanded V7 B0 Firetruck Route")
    write_route_map_html(route_map_html, doc, route_points, result_row)
    write_index(index_html, animation_html, route_map_html, doc)
    return {
        "json": rel(json_path),
        "animation_html": rel(animation_html),
        "route_map_html": rel(route_map_html),
        "index_html": rel(index_html),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build expanded-v7 B0 route visualizations.")
    parser.add_argument("--latest-json", type=Path, default=DEFAULT_LATEST_JSON)
    parser.add_argument("--accepted-routes", type=Path, default=DEFAULT_ACCEPTED_ROUTES)
    parser.add_argument("--net-file", type=Path, default=DEFAULT_NET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--bg-radius-m", type=float, default=DEFAULT_BG_RADIUS_M)
    parser.add_argument("--bg-sample-sec", type=float, default=DEFAULT_BG_SAMPLE_SEC)
    parser.add_argument("--bg-limit", type=int, default=DEFAULT_BG_LIMIT)
    args = parser.parse_args()
    outputs = build(
        project_path(args.latest_json),
        project_path(args.accepted_routes),
        project_path(args.net_file),
        project_path(args.output_dir),
        args.bg_radius_m,
        args.bg_sample_sec,
        args.bg_limit,
    )
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
