#!/usr/bin/env python3
"""Build an expanded-v7 B0 main-flow animation without editing 04_visualize."""

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
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_LATEST_JSON = PROJECT_ROOT / "results/metrics/expanded_v7_plausibility_first/latest.json"
FALLBACK_LATEST_JSON = PROJECT_ROOT / "results/metrics/expanded_v7_b0/latest.json"
DEFAULT_MANIFEST = PROJECT_ROOT / "configs/expanded_v7_b0_manifest.json"
DEFAULT_MAPPING_CSV = PROJECT_ROOT / "data_prepared/expanded_v7/map/toegye_segment_edge_mapping.csv"
DEFAULT_ACCEPTED_ROUTES = PROJECT_ROOT / "data_prepared/expanded_v7/routes/firetruck_accepted_routes.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results/html"
DEFAULT_OUTPUT_STEM = "expanded_v7_b0_main_flow"
DEFAULT_SAMPLE_SEC = 5.0
DEFAULT_BG_LIMIT = 900
STOP_SPEED_KMH = 5.0
FREE_SPEED_KMH = 35.0


class MainFlowVisualizeError(RuntimeError):
    """Expected expanded-v7 main-flow visualization failure."""


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
        raise MainFlowVisualizeError(f"json_root_not_object:{rel(path)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def safe_float(value: Any, default: float = 0.0) -> float:
    if value in {"", None}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    if value in {"", None}:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def bool_cell(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * 111_320.0
    dlon = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dlat, dlon)


def load_latest_row(latest_json: Path) -> tuple[dict[str, Any], dict[str, str], Path]:
    if not latest_json.is_file() and FALLBACK_LATEST_JSON.is_file():
        latest_json = FALLBACK_LATEST_JSON
    latest = read_json(latest_json)
    results_csv = project_path(str(latest.get("results_csv", "")))
    if not results_csv.is_file():
        raise MainFlowVisualizeError(f"missing_results_csv:{rel(results_csv)}")
    rows = [
        row for row in read_csv(results_csv)
        if row.get("mode") == "B0" and row.get("parameter_id") == "no_control"
    ]
    if not rows:
        raise MainFlowVisualizeError(f"no_b0_no_control_row:{rel(results_csv)}")
    return latest, rows[-1], latest_json


def active_net_from_manifest(manifest: Path) -> Path:
    payload = read_json(manifest)
    net = payload.get("active_net", "")
    if not net:
        raise MainFlowVisualizeError(f"manifest_missing_active_net:{rel(manifest)}")
    return project_path(str(net))


def edge_data_metrics(edge_data_xml: Path) -> dict[str, dict[str, float]]:
    metrics: dict[str, dict[str, float]] = {}
    if not edge_data_xml.is_file():
        return metrics
    for _event, elem in ET.iterparse(edge_data_xml, events=("end",)):
        if elem.tag == "edge":
            edge_id = elem.get("id", "")
            row = metrics.setdefault(edge_id, {
                "entered": 0.0,
                "left": 0.0,
                "speed_weighted_sum": 0.0,
                "speed_weight": 0.0,
                "waitingTime": 0.0,
            })
            entered = safe_float(elem.get("entered"))
            left = safe_float(elem.get("left"))
            sampled = safe_float(elem.get("sampledSeconds"))
            speed = safe_float(elem.get("speed"))
            weight = sampled if sampled > 0 else max(entered, left, 1.0)
            row["entered"] += entered
            row["left"] += left
            row["speed_weighted_sum"] += speed * 3.6 * weight
            row["speed_weight"] += weight
            row["waitingTime"] += safe_float(elem.get("waitingTime"))
            elem.clear()
    for row in metrics.values():
        weight = row.pop("speed_weight", 0.0)
        weighted_sum = row.pop("speed_weighted_sum", 0.0)
        row["observed_count"] = max(row["entered"], row["left"])
        row["speed_kmh"] = weighted_sum / weight if weight else 0.0
    return metrics


def lane_to_edge(lane_id: str) -> str:
    if not lane_id or lane_id.startswith(":"):
        return lane_id
    sep = lane_id.rfind("_")
    return lane_id[:sep] if sep > 0 else lane_id


def read_lonlat(elem: ET.Element) -> tuple[float, float] | None:
    lon = elem.get("lon") or elem.get("x")
    lat = elem.get("lat") or elem.get("y")
    if lon is None or lat is None:
        return None
    try:
        return float(lat), float(lon)
    except ValueError:
        return None


def stream_fcd(fcd_path: Path, sample_sec: float, bg_limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    emergency: list[dict[str, Any]] = []
    background: list[dict[str, Any]] = []
    anchor_time: float | None = None
    previous: tuple[float, float] | None = None
    distance_m = 0.0
    last_sample = -10**9
    if not fcd_path.is_file():
        raise MainFlowVisualizeError(f"missing_fcd:{rel(fcd_path)}")
    for _event, elem in ET.iterparse(fcd_path, events=("end",)):
        if elem.tag != "timestep":
            continue
        timestep = safe_float(elem.get("time"))
        if anchor_time is None:
            anchor_time = timestep
        t_rel = round(timestep - anchor_time, 2)
        snapshot: list[dict[str, Any]] = []
        for vehicle in elem.findall("vehicle"):
            coords = read_lonlat(vehicle)
            if coords is None:
                continue
            lat, lon = coords
            vehicle_id = vehicle.get("id", "")
            speed_kmh = safe_float(vehicle.get("speed")) * 3.6
            point = {
                "id": vehicle_id,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "speed_kmh": round(speed_kmh, 2),
                "angle": round(safe_float(vehicle.get("angle")), 1),
                "edge": lane_to_edge(vehicle.get("lane", "")),
            }
            if vehicle_id.startswith("emergency_"):
                if previous is not None:
                    distance_m += meters_between(previous[0], previous[1], lat, lon)
                previous = (lat, lon)
                emergency.append({**point, "t_rel": t_rel, "dist_m": round(distance_m, 2)})
            elif len(snapshot) < bg_limit:
                snapshot.append(point)
        if timestep - last_sample >= sample_sec:
            background.append({"t_rel": t_rel, "vehicles": snapshot})
            last_sample = timestep
        elem.clear()
    if not emergency:
        raise MainFlowVisualizeError(f"empty_emergency_fcd:{rel(fcd_path)}")
    return emergency, background


def route_edges_from_csv(path: Path) -> list[str]:
    rows = read_csv(path) if path.is_file() else []
    if not rows:
        return []
    return rows[0].get("route_edges", "").split()


def edge_shape_latlon(sumo_net: Any, edge_id: str) -> list[list[float]]:
    edge = sumo_net.getEdge(edge_id)
    points = []
    for x, y in edge.getShape():
        lon, lat = sumo_net.convertXY2LonLat(x, y)
        point = [round(lat, 6), round(lon, 6)]
        if not points or points[-1] != point:
            points.append(point)
    return points


def build_mainline_edges(net_file: Path, mapping_csv: Path, edge_metrics: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    from sumolib.net import readNet

    sumo_net = readNet(str(net_file))
    edge_to_segments: dict[str, set[str]] = {}
    edge_to_directions: dict[str, set[str]] = {}
    for row in read_csv(mapping_csv):
        edge_id = row.get("edge_id", "")
        if not edge_id:
            continue
        edge_to_segments.setdefault(edge_id, set()).add(row.get("segment_id", ""))
        edge_to_directions.setdefault(edge_id, set()).add(row.get("direction", ""))
    rows: list[dict[str, Any]] = []
    for edge_id in sorted(edge_to_segments):
        try:
            shape = edge_shape_latlon(sumo_net, edge_id)
            edge = sumo_net.getEdge(edge_id)
        except Exception:
            continue
        metrics = edge_metrics.get(edge_id, {})
        speed = safe_float(metrics.get("speed_kmh"))
        flow_state = "missing"
        if speed > FREE_SPEED_KMH:
            flow_state = "free_flow"
        elif 0 < speed < STOP_SPEED_KMH:
            flow_state = "stop_flow"
        elif speed > 0:
            flow_state = "congested"
        rows.append({
            "edge_id": edge_id,
            "segments": " ".join(sorted(edge_to_segments[edge_id])),
            "directions": " ".join(sorted(edge_to_directions[edge_id])),
            "shape": shape,
            "lane_count": int(edge.getLaneNumber()),
            "speed_limit_kmh": round(float(edge.getSpeed()) * 3.6, 2),
            "observed_count": round(safe_float(metrics.get("observed_count")), 2),
            "speed_kmh": round(speed, 2),
            "flow_state": flow_state,
        })
    return rows


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
    latest_json: Path,
    latest: dict[str, Any],
    result_row: dict[str, str],
    net_file: Path,
    mapping_csv: Path,
    accepted_routes: Path,
    sample_sec: float,
    bg_limit: int,
) -> dict[str, Any]:
    run_dir = project_path(result_row.get("run_dir", ""))
    fcd_path = run_dir / "fcd.xml"
    edge_data = run_dir / "edgeData.xml"
    metrics = edge_data_metrics(edge_data)
    emergency, background = stream_fcd(fcd_path, sample_sec, bg_limit)
    mainline_edges = build_mainline_edges(net_file, mapping_csv, metrics)
    route_edges = route_edges_from_csv(accepted_routes)
    route_polyline = [[point["lat"], point["lon"]] for point in emergency]
    all_points = route_polyline + [point for edge in mainline_edges for point in edge["shape"]]
    return {
        "schema": "expanded_v7_b0_main_flow_animation.v1",
        "meta": {
            "source": "04-2 Visualize wrapper; follows 04_visualize circleMarker/background-dot rendering pattern",
            "latest_json": rel(latest_json),
            "run_id": latest.get("run_id", result_row.get("run_id", "")),
            "results_csv": latest.get("results_csv", ""),
            "run_dir": rel(run_dir),
            "fcd_xml": rel(fcd_path),
            "edge_data_xml": rel(edge_data),
            "active_net": rel(net_file),
            "mapping_csv": rel(mapping_csv),
            "accepted_routes": rel(accepted_routes),
            "bounds": bounds(all_points),
            "sample_sec": sample_sec,
            "bg_limit": bg_limit,
        },
        "metrics": {
            "final_status": result_row.get("final_status", ""),
            "sumo_exit_code": result_row.get("sumo_exit_code", ""),
            "route_error_count": result_row.get("route_error_count", ""),
            "emergency_arrived": bool_cell(result_row.get("emergency_arrived")),
            "emergency_teleport": bool_cell(result_row.get("emergency_teleport")),
            "background_vehicle_count": result_row.get("background_vehicle_count", ""),
            "background_teleported": result_row.get("background_teleported", ""),
            "remaining_vehicle_count": result_row.get("remaining_vehicle_count", ""),
            "network_avg_speed_kmh": result_row.get("network_avg_speed_kmh", ""),
            "mainline_stop_edge_count": sum(1 for edge in mainline_edges if edge["flow_state"] == "stop_flow"),
            "mainline_free_edge_count": sum(1 for edge in mainline_edges if edge["flow_state"] == "free_flow"),
        },
        "route_edges": route_edges,
        "emergency": emergency,
        "background": background,
        "mainline_edges": mainline_edges,
        "route_polyline": route_polyline,
    }


def write_html(path: Path, doc: dict[str, Any]) -> None:
    data_json = json.dumps(doc, ensure_ascii=False)
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Expanded V7 B0 Main Traffic Flow</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body,#map{{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}}
.panel{{position:absolute;z-index:500;top:14px;left:14px;background:rgba(255,255,255,.95);border:1px solid #d1d5db;border-radius:8px;padding:12px 14px;width:360px;box-shadow:0 6px 20px rgba(15,23,42,.15);}}
.panel h1{{font-size:16px;margin:0 0 8px;}}
.panel p{{font-size:13px;color:#475569;margin:4px 0;line-height:1.35;}}
.controls{{display:flex;gap:8px;align-items:center;margin-top:10px;}}
button{{border:1px solid #94a3b8;background:#fff;border-radius:6px;padding:6px 10px;cursor:pointer;}}
input[type=range]{{width:160px;}}
.legend{{position:absolute;z-index:500;bottom:16px;left:16px;background:rgba(255,255,255,.94);border:1px solid #d1d5db;border-radius:8px;padding:10px 12px;font-size:12px;}}
.sw{{display:inline-block;width:18px;height:4px;border-radius:2px;margin-right:6px;vertical-align:middle;}}
code{{background:#f1f5f9;padding:1px 4px;border-radius:4px;}}
</style>
</head>
<body>
<div id="map"></div>
<section class="panel">
  <h1>Expanded V7 B0 메인 교통흐름</h1>
  <p>04번 방식과 동일하게 <code>L.circleMarker</code> 기반으로 소방차와 일반차 dot을 표시합니다. 04번 폴더는 수정하지 않았습니다.</p>
  <p id="status"></p>
  <div class="controls">
    <button id="play">Play</button>
    <button id="reset">Reset</button>
    <input id="seek" type="range" min="0" max="0" value="0" step="1">
    <span id="clock">0s</span>
  </div>
</section>
<section class="legend">
  <div><span class="sw" style="background:#7f1d1d"></span>stop &lt;5km/h</div>
  <div><span class="sw" style="background:#f59e0b"></span>5-20km/h</div>
  <div><span class="sw" style="background:#16a34a"></span>20-35km/h</div>
  <div><span class="sw" style="background:#2563eb"></span>free &gt;35km/h</div>
</section>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = {data_json};
const map = L.map("map", {{preferCanvas:true}});
L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{maxZoom:19, attribution:"© OpenStreetMap"}}).addTo(map);
const b = DATA.meta.bounds;
map.fitBounds([[b.min_lat,b.min_lon],[b.max_lat,b.max_lon]], {{padding:[28,28]}});
function speedColor(speed){{
  if(!speed || speed <= 0) return "#94a3b8";
  if(speed < 5) return "#7f1d1d";
  if(speed <= 20) return "#f59e0b";
  if(speed <= 35) return "#16a34a";
  return "#2563eb";
}}
const edgeLayer = L.layerGroup().addTo(map);
for(const edge of DATA.mainline_edges){{
  const line = L.polyline(edge.shape, {{color:speedColor(edge.speed_kmh), weight: edge.flow_state === "stop_flow" ? 7 : 5, opacity:.82}}).addTo(edgeLayer);
  line.bindTooltip(`${{edge.edge_id}} ${{edge.segments}} ${{edge.directions}}<br>${{edge.speed_kmh}}km/h, count ${{edge.observed_count}}, lanes ${{edge.lane_count}}`);
}}
L.polyline(DATA.route_polyline, {{color:"#f97316", weight:4, opacity:.76}}).addTo(map);
const bgLayer = L.layerGroup().addTo(map);
const bgByT = new Map(DATA.background.map((snap, idx) => [idx, snap]));
let emMarker = null;
let playing = false;
let index = 0;
const seek = document.getElementById("seek");
seek.max = Math.max(0, DATA.emergency.length - 1);
function emAt(i){{ return DATA.emergency[Math.max(0, Math.min(DATA.emergency.length - 1, i))]; }}
function updatePanel(point){{
  document.getElementById("clock").textContent = `${{Math.round(point.t_rel)}}s`;
  document.getElementById("status").innerHTML = `run <code>${{DATA.meta.run_id}}</code>, status <b>${{DATA.metrics.final_status}}</b><br>` +
    `speed ${{point.speed_kmh}}km/h, edge <code>${{point.edge}}</code><br>` +
    `bg ${{DATA.metrics.background_vehicle_count}}, remaining ${{DATA.metrics.remaining_vehicle_count}}, stop/free edges ${{DATA.metrics.mainline_stop_edge_count}}/${{DATA.metrics.mainline_free_edge_count}}`;
}}
function draw(i){{
  index = Math.max(0, Math.min(DATA.emergency.length - 1, i));
  seek.value = index;
  const point = emAt(index);
  if(!emMarker){{
    emMarker = L.circleMarker([point.lat, point.lon], {{radius:9,color:"#fff",weight:2,fillColor:"#ef4444",fillOpacity:1}}).addTo(map);
  }} else {{
    emMarker.setLatLng([point.lat, point.lon]);
  }}
  bgLayer.clearLayers();
  const bgIndex = Math.min(DATA.background.length - 1, Math.floor(index / Math.max(1, Math.round(DATA.meta.sample_sec))));
  const snap = bgByT.get(bgIndex) || DATA.background[0] || {{vehicles:[]}};
  for(const vehicle of snap.vehicles){{
    L.circleMarker([vehicle.lat, vehicle.lon], {{radius:2.2,color:"#334155",weight:0,fillColor:speedColor(vehicle.speed_kmh),fillOpacity:.58}}).addTo(bgLayer);
  }}
  updatePanel(point);
}}
function tick(){{
  if(!playing) return;
  draw(index + 1);
  if(index >= DATA.emergency.length - 1) playing = false;
  window.setTimeout(tick, 80);
}}
document.getElementById("play").onclick = () => {{ playing = !playing; if(playing) tick(); }};
document.getElementById("reset").onclick = () => {{ playing = false; draw(0); }};
seek.oninput = () => {{ playing = false; draw(Number(seek.value)); }};
draw(0);
</script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def write_index(path: Path, animation_html: Path, json_path: Path, doc: dict[str, Any]) -> None:
    metrics = doc["metrics"]
    html_text = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Expanded V7 B0 Main Flow Index</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:32px;background:#f8fafc;color:#111827}}main{{max-width:880px;margin:0 auto}}.card{{background:#fff;border:1px solid #d8dee8;border-radius:8px;padding:18px}}a{{display:inline-block;background:#2563eb;color:white;text-decoration:none;border-radius:6px;padding:9px 14px;margin-right:8px}}code{{background:#eef2f7;padding:2px 5px;border-radius:4px}}</style>
</head><body><main><h1>Expanded V7 B0 Main Flow</h1><section class="card">
<p>04-2 wrapper가 생성한 메인 교통흐름 시각화입니다. 04번 폴더는 수정하지 않습니다.</p>
<p>status <b>{html.escape(str(metrics.get("final_status", "")))}</b>, arrived={metrics.get("emergency_arrived")}, teleport={metrics.get("emergency_teleport")}, stop/free edge={metrics.get("mainline_stop_edge_count")}/{metrics.get("mainline_free_edge_count")}</p>
<p><a href="{animation_html.name}">B0 메인 흐름 애니메이션</a><a href="{json_path.name}">JSON</a></p>
</section></main></body></html>"""
    path.write_text(html_text, encoding="utf-8")


def build(
    latest_json: Path,
    manifest: Path,
    mapping_csv: Path,
    accepted_routes: Path,
    output_dir: Path,
    output_stem: str,
    sample_sec: float,
    bg_limit: int,
) -> dict[str, str]:
    latest, result_row, actual_latest = load_latest_row(latest_json)
    net_file = active_net_from_manifest(manifest)
    doc = build_doc(actual_latest, latest, result_row, net_file, mapping_csv, accepted_routes, sample_sec, bg_limit)
    json_path = output_dir / f"{output_stem}_animation.json"
    html_path = output_dir / f"{output_stem}_animation.html"
    index_path = output_dir / f"{output_stem}_index.html"
    write_json(json_path, doc)
    write_html(html_path, doc)
    write_index(index_path, html_path, json_path, doc)
    return {
        "json": rel(json_path),
        "animation_html": rel(html_path),
        "index_html": rel(index_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build expanded-v7 B0 main traffic flow visualization.")
    parser.add_argument("--latest-json", type=Path, default=DEFAULT_LATEST_JSON)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--mapping-csv", type=Path, default=DEFAULT_MAPPING_CSV)
    parser.add_argument("--accepted-routes", type=Path, default=DEFAULT_ACCEPTED_ROUTES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-stem", default=DEFAULT_OUTPUT_STEM)
    parser.add_argument("--sample-sec", type=float, default=DEFAULT_SAMPLE_SEC)
    parser.add_argument("--bg-limit", type=int, default=DEFAULT_BG_LIMIT)
    args = parser.parse_args()
    outputs = build(
        project_path(args.latest_json),
        project_path(args.manifest),
        project_path(args.mapping_csv),
        project_path(args.accepted_routes),
        project_path(args.output_dir),
        args.output_stem,
        args.sample_sec,
        args.bg_limit,
    )
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
