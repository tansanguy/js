#!/usr/bin/env python3
"""Custom destination route acceptance and visualization helpers."""

from __future__ import annotations

import csv
import html
import json
import math
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import validated_pipeline as vp


STEP07_PATH = vp.PROJECT_ROOT / "01_prepare/04_routes/step07_generate_emergency_routes.py"
RUNNER_PATH = vp.PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py"
DEFAULT_MANIFEST = vp.CALIBRATED_MANIFEST
START_EDGE_ID = "-381802881#2"
CUSTOM_DESTINATION_DIR = vp.PROJECT_ROOT / "data_prepared/validated/custom_destinations"
CUSTOM_ROUTE_DIR = vp.PROJECT_ROOT / "data_prepared/validated/custom_routes"
CUSTOM_METRICS_DIR = vp.PROJECT_ROOT / "results/metrics/validated_custom_destinations"
CUSTOM_ROUTE_METRICS_DIR = vp.PROJECT_ROOT / "results/metrics/validated_custom_routes"
POINT_CANDIDATES_CSV = CUSTOM_DESTINATION_DIR / "custom_destination_point_candidates.csv"
POINT_ACCEPTANCE_JSON = CUSTOM_DESTINATION_DIR / "custom_destination_point_acceptance.json"
ROUTE_CANDIDATES_CSV = CUSTOM_ROUTE_DIR / "custom_route_candidates.csv"
ROUTE_ACCEPTANCE_JSON = CUSTOM_ROUTE_DIR / "custom_route_acceptance.json"
ACCEPTED_ROUTES_CSV = CUSTOM_ROUTE_DIR / "accepted_custom_routes.csv"
ACCEPTED_ROUTES_XML = CUSTOM_ROUTE_DIR / "accepted_custom_routes.rou.xml"
POINT_REVIEW_HTML = vp.PROJECT_ROOT / "results/html/custom_destination_point_review.html"
ROUTE_REVIEW_HTML = vp.PROJECT_ROOT / "results/html/custom_destination_route_review.html"
CUSTOM_B0_PREFIX = "validated_custom_destination_b0"
CUSTOM_B0_RUN_SUMMARY = vp.PROJECT_ROOT / f"results/metrics/{CUSTOM_B0_PREFIX}/custom_destination_run_summary.json"
CUSTOM_B0_OVERVIEW_HTML = vp.PROJECT_ROOT / "results/html/custom_destination_b0_visualization.html"

DEFAULT_DESTINATIONS = [
    {
        "destination_id": "JUNG_GU_PILDONG2_84_101",
        "address": "서울 중구 필동2가 84-101",
        "label_ko": "필동2가 84-101",
        "lat": "37.556682",
        "lon": "126.993665",
        "coordinate_source": "user_manual_coordinate",
    },
    {
        "destination_id": "JUNG_GU_HOEHYEON1_147_23",
        "address": "HX4J+6G 서울특별시 / 서울 중구 회현동1가 147-23",
        "label_ko": "회현동1가 147-23",
        "lat": "37.555766",
        "lon": "126.981288",
        "coordinate_source": "user_manual_coordinate",
    },
]


class CustomDestinationError(RuntimeError):
    """Expected custom destination pipeline failure."""


def step07() -> Any:
    return vp.load_module("custom_destination_step07", STEP07_PATH)


def read_manifest(path: Path) -> dict[str, Any]:
    payload = vp.read_json(path)
    if not payload.get("active_net"):
        raise CustomDestinationError(f"manifest_missing_active_net:{vp.rel(path)}")
    if not payload.get("background_route"):
        raise CustomDestinationError(f"manifest_missing_background_route:{vp.rel(path)}")
    return payload


def manifest_net(path: Path) -> Path:
    return vp.project_path(str(read_manifest(path)["active_net"]))


def manifest_background_route(path: Path) -> Path:
    return vp.project_path(str(read_manifest(path)["background_route"]))


def load_sumo_net(net_path: Path) -> Any:
    return step07().read_sumo_net(net_path)


def geocode_address(address: str, timeout_sec: int = 12) -> dict[str, Any]:
    query = f"{address}, Seoul, South Korea"
    params = urllib.parse.urlencode({"format": "jsonv2", "limit": "5", "q": query})
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "validated-b0-custom-destination-review/1.0"},
    )
    try:
        import certifi  # type: ignore

        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - fallback to platform defaults.
        ssl_context = None
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec, context=ssl_context) as response:
            results = json.loads(response.read().decode("utf-8"))
    except ssl.SSLError:
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec, context=ssl._create_unverified_context()) as response:  # noqa: S323
                results = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - geocoding failure is shown in review HTML.
            return {"status": "FAIL", "reason": f"{type(exc).__name__}:{exc}", "results": []}
    except Exception as exc:  # noqa: BLE001 - geocoding failure is shown in review HTML.
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            try:
                with urllib.request.urlopen(request, timeout=timeout_sec, context=ssl._create_unverified_context()) as response:  # noqa: S323
                    results = json.loads(response.read().decode("utf-8"))
            except Exception as retry_exc:  # noqa: BLE001
                return {"status": "FAIL", "reason": f"{type(retry_exc).__name__}:{retry_exc}", "results": []}
        else:
            return {"status": "FAIL", "reason": f"{type(exc).__name__}:{exc}", "results": []}
    if not isinstance(results, list) or not results:
        return {"status": "FAIL", "reason": "no_nominatim_result", "results": []}
    parsed = []
    for result in results:
        try:
            parsed.append(
                {
                    "lat": float(result["lat"]),
                    "lon": float(result["lon"]),
                    "display_name": str(result.get("display_name", "")),
                    "importance": float(result.get("importance") or 0.0),
                    "osm_type": str(result.get("osm_type", "")),
                    "osm_id": str(result.get("osm_id", "")),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not parsed:
        return {"status": "FAIL", "reason": "invalid_nominatim_result", "results": []}
    parsed.sort(key=lambda item: item["importance"], reverse=True)
    return {"status": "PASS", "reason": "", "results": parsed}


def edge_shape_xy(edge: Any) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in edge.getShape(False)]


def edge_shape_lonlat(sumo_net: Any, edge: Any) -> list[list[float]]:
    coords: list[list[float]] = []
    for x, y in edge_shape_xy(edge):
        lon, lat = sumo_net.convertXY2LonLat(x, y)
        coords.append([float(lon), float(lat)])
    return coords


def point_segment_distance_m(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    denom = dx * dx + dy * dy
    if denom == 0.0:
        return math.hypot(px - sx, py - sy)
    t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / denom))
    return math.hypot(px - (sx + t * dx), py - (sy + t * dy))


def point_polyline_distance_m(point: tuple[float, float], polyline: list[tuple[float, float]]) -> float:
    if not polyline:
        return float("inf")
    if len(polyline) == 1:
        return math.hypot(point[0] - polyline[0][0], point[1] - polyline[0][1])
    return min(point_segment_distance_m(point, start, end) for start, end in zip(polyline, polyline[1:], strict=False))


def nearest_passenger_edges(sumo_net: Any, lon: float, lat: float, limit: int = 5) -> list[dict[str, Any]]:
    x, y = sumo_net.convertLonLat2XY(float(lon), float(lat))
    point = (float(x), float(y))
    rows = []
    for edge in sumo_net.getEdges():
        if edge.isSpecial() or not edge.allows("passenger"):
            continue
        shape = edge_shape_xy(edge)
        distance_m = point_polyline_distance_m(point, shape)
        rows.append(
            {
                "edge_id": edge.getID(),
                "distance_m": round(distance_m, 3),
                "lane_count": edge.getLaneNumber(),
                "speed_kmh": round(edge.getSpeed() * 3.6, 3),
                "length_m": round(edge.getLength(), 3),
                "shape": edge_shape_lonlat(sumo_net, edge),
            }
        )
    rows.sort(key=lambda row: (float(row["distance_m"]), -float(row["length_m"]), row["edge_id"]))
    return rows[:limit]


def nearest_passenger_edges_from_geojson(
    sumo_net: Any,
    coords_by_id: dict[str, list[list[float]]],
    axis_ctx: dict[str, float],
    lon: float,
    lat: float,
    limit: int = 5,
) -> list[dict[str, Any]]:
    s07 = step07()
    point = s07.local_xy(float(lon), float(lat), axis_ctx)
    rows = []
    for edge_id, coords in coords_by_id.items():
        try:
            edge = sumo_net.getEdge(edge_id)
        except Exception:  # noqa: BLE001
            continue
        if edge.isSpecial() or not edge.allows("passenger"):
            continue
        shape = s07.coords_to_xy(coords, axis_ctx)
        distance_m = point_polyline_distance_m(point, shape)
        rows.append(
            {
                "edge_id": edge_id,
                "distance_m": round(distance_m, 3),
                "lane_count": edge.getLaneNumber(),
                "speed_kmh": round(edge.getSpeed() * 3.6, 3),
                "length_m": round(edge.getLength(), 3),
                "shape": coords,
            }
        )
    rows.sort(key=lambda row: (float(row["distance_m"]), -float(row["length_m"]), row["edge_id"]))
    return rows[:limit]


def edge_midpoint_lonlat(coords: list[list[float]]) -> list[float]:
    if not coords:
        return ["", ""]
    middle = coords[len(coords) // 2]
    return [float(middle[0]), float(middle[1])]


def build_point_candidates(net_path: Path, destinations: list[dict[str, str]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    destinations = destinations or DEFAULT_DESTINATIONS
    sumo_net = load_sumo_net(net_path)
    s07 = step07()
    _props_by_id, coords_by_id = s07.load_edge_geojson(s07.ACTIVE_EDGES_GEOJSON)
    map_config = s07.load_yaml(s07.MAP_CONFIG)
    axis_ctx = s07.axis_context(map_config)
    rows: list[dict[str, Any]] = []
    summaries = []
    for destination in destinations:
        manual_lat = str(destination.get("lat", "")).strip()
        manual_lon = str(destination.get("lon", "")).strip()
        coordinate_source = str(destination.get("coordinate_source", "")).strip()
        geocode = geocode_address(destination["address"])
        geocode_results = geocode["results"]
        if manual_lat and manual_lon:
            selected = {
                "lat": float(manual_lat),
                "lon": float(manual_lon),
                "display_name": destination.get("address", ""),
                "importance": "",
            }
            coordinate_source = coordinate_source or "manual_coordinate"
            edge_candidates = nearest_passenger_edges_from_geojson(sumo_net, coords_by_id, axis_ctx, selected["lon"], selected["lat"], limit=5)
        elif geocode_results:
            selected = geocode_results[0]
            coordinate_source = "nominatim_geocode"
            edge_candidates = nearest_passenger_edges_from_geojson(sumo_net, coords_by_id, axis_ctx, selected["lon"], selected["lat"], limit=5)
        else:
            selected = {"lat": "", "lon": "", "display_name": "", "importance": ""}
            coordinate_source = "missing_coordinate"
            edge_candidates = []
        for rank, candidate in enumerate(edge_candidates, start=1):
            midpoint = edge_midpoint_lonlat(candidate["shape"])
            try:
                shortest = s07.shortest_route(sumo_net, START_EDGE_ID, candidate["edge_id"])
                reachable = True
                route_error = ""
                shortest_route_edge_count = len(shortest)
                shortest_route_length_m = round(s07.route_length(sumo_net, shortest), 3)
            except Exception as exc:  # noqa: BLE001 - shown in review UI.
                reachable = False
                route_error = str(exc)
                shortest_route_edge_count = ""
                shortest_route_length_m = ""
            rows.append(
                {
                    "destination_id": destination["destination_id"],
                    "label_ko": destination.get("label_ko", ""),
                    "address": destination["address"],
                    "coordinate_source": coordinate_source,
                    "geocode_status": geocode["status"],
                    "geocode_reason": geocode.get("reason", ""),
                    "lat": selected["lat"],
                    "lon": selected["lon"],
                    "geocode_display_name": selected.get("display_name", ""),
                    "candidate_rank": rank,
                    "edge_id": candidate["edge_id"],
                    "distance_m": candidate["distance_m"],
                    "lane_count": candidate["lane_count"],
                    "speed_kmh": candidate["speed_kmh"],
                    "length_m": candidate["length_m"],
                    "edge_mid_lon": midpoint[0],
                    "edge_mid_lat": midpoint[1],
                    "reachable_from_start": reachable,
                    "shortest_route_edge_count": shortest_route_edge_count,
                    "shortest_route_length_m": shortest_route_length_m,
                    "route_error": route_error,
                    "edge_shape": json.dumps(candidate["shape"], ensure_ascii=False),
                }
            )
        if not edge_candidates:
            rows.append(
                {
                    "destination_id": destination["destination_id"],
                    "label_ko": destination.get("label_ko", ""),
                    "address": destination["address"],
                    "coordinate_source": coordinate_source,
                    "geocode_status": geocode["status"],
                    "geocode_reason": geocode.get("reason", ""),
                    "lat": selected["lat"],
                    "lon": selected["lon"],
                    "geocode_display_name": selected.get("display_name", ""),
                    "candidate_rank": "",
                    "edge_id": "",
                    "distance_m": "",
                    "lane_count": "",
                    "speed_kmh": "",
                    "length_m": "",
                    "edge_mid_lon": "",
                    "edge_mid_lat": "",
                    "reachable_from_start": "",
                    "shortest_route_edge_count": "",
                    "shortest_route_length_m": "",
                    "route_error": "",
                    "edge_shape": "[]",
                }
            )
        reachable_candidates = [candidate for candidate in edge_candidates if any(row["edge_id"] == candidate["edge_id"] and row["reachable_from_start"] for row in rows)]
        summaries.append(
            {
                "destination_id": destination["destination_id"],
                "geocode_status": geocode["status"],
                "coordinate_source": coordinate_source,
                "candidate_count": len(edge_candidates),
                "recommended_edge_id": reachable_candidates[0]["edge_id"] if reachable_candidates else edge_candidates[0]["edge_id"] if edge_candidates else "",
            }
        )
    summary = {
        "schema": "custom_destination_point_candidates.v1",
        "generated_at": vp.utc_now(),
        "net_file": vp.rel(net_path),
        "destination_count": len(destinations),
        "candidate_row_count": len(rows),
        "destinations": summaries,
    }
    return rows, summary


def point_review_payload(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    recommended_by_destination = {
        str(row.get("destination_id")): str(row.get("recommended_edge_id", ""))
        for row in summary.get("destinations", [])
        if isinstance(row, dict)
    }
    for row in rows:
        dest = grouped.setdefault(
            str(row["destination_id"]),
            {
                "destination_id": row["destination_id"],
                "label_ko": row["label_ko"],
                "address": row["address"],
                "coordinate_source": row.get("coordinate_source", ""),
                "geocode_status": row["geocode_status"],
                "geocode_reason": row["geocode_reason"],
                "lat": row["lat"],
                "lon": row["lon"],
                "geocode_display_name": row["geocode_display_name"],
                "recommended_edge_id": recommended_by_destination.get(str(row["destination_id"]), ""),
                "edge_candidates": [],
            },
        )
        if row.get("edge_id"):
            dest["edge_candidates"].append(
                {
                    "rank": int(row["candidate_rank"]),
                    "edge_id": row["edge_id"],
                    "distance_m": float(row["distance_m"]),
                    "lane_count": int(row["lane_count"]),
                    "speed_kmh": float(row["speed_kmh"]),
                    "length_m": float(row["length_m"]),
                    "edge_mid_lon": float(row["edge_mid_lon"]),
                    "edge_mid_lat": float(row["edge_mid_lat"]),
                    "reachable_from_start": vp.bool_cell(row.get("reachable_from_start")),
                    "shortest_route_edge_count": int(row["shortest_route_edge_count"]) if row.get("shortest_route_edge_count") else "",
                    "shortest_route_length_m": float(row["shortest_route_length_m"]) if row.get("shortest_route_length_m") else "",
                    "route_error": row.get("route_error", ""),
                    "shape": json.loads(row["edge_shape"]),
                }
            )
    return {"summary": summary, "destinations": list(grouped.values())}


def write_point_review_html(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(
        f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Custom Destination Point Accept</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#16202a; }}
    main {{ display:grid; grid-template-columns:minmax(380px, 470px) 1fr; min-height:100vh; }}
    aside {{ padding:18px; overflow:auto; border-right:1px solid #d9e1e8; background:#f7f9fb; }}
    #map {{ min-height:100vh; }}
    h1 {{ font-size:20px; margin:0 0 12px; }}
    .dest {{ background:white; border:1px solid #d9e1e8; border-radius:8px; padding:12px; margin:12px 0; }}
    label {{ display:block; margin:8px 0; font-size:13px; }}
    input[type="number"] {{ width:46%; padding:6px; margin-right:4px; }}
    button {{ padding:9px 11px; border:1px solid #0b65c2; background:#0b65c2; color:white; border-radius:6px; cursor:pointer; }}
    code {{ font-size:12px; }}
    .muted {{ color:#5f6b77; font-size:13px; }}
    .edge {{ border-top:1px solid #edf1f5; padding:8px 6px; border-radius:6px; }}
    .edge.active {{ background:#e9f3ff; outline:2px solid #0b65c2; }}
    .edge.unreachable {{ color:#8a1f11; background:#fff5f2; }}
    .rank {{ display:inline-flex; align-items:center; justify-content:center; width:22px; height:22px; border-radius:50%; background:#e2e8f0; font-weight:700; margin-right:4px; }}
    .candidate-meta {{ display:block; color:#475569; margin:3px 0 0 28px; }}
    .rank-marker {{ display:flex; align-items:center; justify-content:center; width:24px; height:24px; border-radius:50%; border:2px solid white; color:white; font-weight:700; box-shadow:0 1px 4px rgba(0,0,0,.25); }}
  </style>
</head>
<body>
<main>
  <aside>
    <h1>목적지 지점 Accept</h1>
    <p class="muted">각 목적지의 좌표와 target edge를 확인한 뒤 JSON을 내려받아 <code>{vp.rel(POINT_ACCEPTANCE_JSON)}</code>에 저장하면 다음 route 후보 단계로 진행됩니다. 지도 번호는 후보 edge 중심점입니다.</p>
    <div id="controls"></div>
    <button id="download">Accept JSON 다운로드</button>
  </aside>
  <div id="map"></div>
</main>
<script>
const DATA = {data};
const map = L.map('map').setView([37.559, 126.995], 15);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 20, attribution: '&copy; OpenStreetMap' }}).addTo(map);
const colors = ['#0b65c2', '#c2410c', '#047857', '#7c3aed', '#b91c1c'];
const controls = document.getElementById('controls');
const bounds = [];
const layersByDestination = new Map();
function markerIcon(rank, color) {{
  return L.divIcon({{
    className: '',
    html: `<div class="rank-marker" style="background:${{color}}">${{rank}}</div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12]
  }});
}}
function updateDestinationStyles(destinationId) {{
  const selected = document.querySelector(`input[name="edge-${{destinationId}}"]:checked`);
  const selectedEdge = selected ? selected.value : '';
  document.querySelectorAll(`[data-destination="${{destinationId}}"]`).forEach(el => {{
    el.classList.toggle('active', el.dataset.edge === selectedEdge);
  }});
  (layersByDestination.get(destinationId) || []).forEach(item => {{
    const active = item.edgeId === selectedEdge;
    item.poly.setStyle({{ weight: active ? 8 : 3, opacity: active ? 0.95 : 0.35 }});
    item.connector.setStyle({{ weight: active ? 3 : 1, opacity: active ? 0.75 : 0.18 }});
  }});
}}
DATA.destinations.forEach((dest, dIndex) => {{
  const section = document.createElement('section');
  section.className = 'dest';
  const lat = dest.lat || '';
  const lon = dest.lon || '';
  section.innerHTML = `<strong>${{dest.label_ko}}</strong><br><span class="muted">${{dest.address}}</span>
    <label>lat/lon<br><input type="number" step="0.0000001" id="lat-${{dest.destination_id}}" value="${{lat}}"><input type="number" step="0.0000001" id="lon-${{dest.destination_id}}" value="${{lon}}"></label>
    <div class="muted">coordinate: ${{dest.coordinate_source || ''}} / geocode: ${{dest.geocode_status}} ${{dest.geocode_reason || ''}}</div>`;
  layersByDestination.set(dest.destination_id, []);
  dest.edge_candidates.forEach((edge, index) => {{
    const div = document.createElement('div');
    div.className = 'edge';
    if (!edge.reachable_from_start) div.classList.add('unreachable');
    div.dataset.destination = dest.destination_id;
    div.dataset.edge = edge.edge_id;
    const checked = edge.edge_id === dest.recommended_edge_id ? 'checked' : '';
    const routeStatus = edge.reachable_from_start ? `reachable · route=${{edge.shortest_route_length_m.toFixed(1)}}m/${{edge.shortest_route_edge_count}} edges` : `NO ROUTE · ${{edge.route_error || ''}}`;
    div.innerHTML = `<label><input type="radio" name="edge-${{dest.destination_id}}" value="${{edge.edge_id}}" ${{checked}} ${{edge.reachable_from_start ? '' : 'disabled'}}> <span class="rank">${{edge.rank}}</span><code>${{edge.edge_id}}</code></label><span class="candidate-meta">${{edge.distance_m.toFixed(1)}}m · lanes=${{edge.lane_count}} · speed=${{edge.speed_kmh.toFixed(1)}}km/h · length=${{edge.length_m.toFixed(1)}}m<br>${{routeStatus}}</span>`;
    section.appendChild(div);
    const color = colors[index % colors.length];
    const poly = L.polyline(edge.shape.map(p => [p[1], p[0]]), {{color, weight: index === 0 ? 8 : 3, opacity: index === 0 ? 0.95 : 0.35}}).addTo(map);
    poly.bindPopup(`${{dest.label_ko}} candidate #${{edge.rank}}<br>${{edge.edge_id}}<br>${{edge.distance_m.toFixed(1)}}m`);
    const midpoint = [edge.edge_mid_lat, edge.edge_mid_lon];
    L.marker(midpoint, {{icon: markerIcon(edge.rank, color)}}).addTo(map).bindPopup(`${{dest.label_ko}} #${{edge.rank}}<br>${{edge.edge_id}}`);
    const connector = L.polyline([[Number(lat), Number(lon)], midpoint], {{color, weight: index === 0 ? 3 : 1, opacity: index === 0 ? 0.75 : 0.18, dashArray:'5 5'}}).addTo(map);
    layersByDestination.get(dest.destination_id).push({{edgeId: edge.edge_id, poly, connector}});
    edge.shape.forEach(p => bounds.push([p[1], p[0]]));
    bounds.push(midpoint);
  }});
  controls.appendChild(section);
  if (lat && lon) {{
    const marker = L.marker([Number(lat), Number(lon)]).addTo(map);
    marker.bindPopup(`${{dest.label_ko}}<br>${{dest.address}}`);
    bounds.push([Number(lat), Number(lon)]);
  }}
  section.querySelectorAll(`input[name="edge-${{dest.destination_id}}"]`).forEach(input => {{
    input.addEventListener('change', () => updateDestinationStyles(dest.destination_id));
  }});
  updateDestinationStyles(dest.destination_id);
}});
if (bounds.length) map.fitBounds(bounds, {{padding:[30,30]}});
document.getElementById('download').onclick = () => {{
  const decisions = DATA.destinations.map(dest => {{
    const edge = document.querySelector(`input[name="edge-${{dest.destination_id}}"]:checked`);
    return {{
      destination_id: dest.destination_id,
      address: dest.address,
      label_ko: dest.label_ko,
      decision: edge ? 'accept' : 'review_needed',
      lat: Number(document.getElementById(`lat-${{dest.destination_id}}`).value),
      lon: Number(document.getElementById(`lon-${{dest.destination_id}}`).value),
      target_edge_id: edge ? edge.value : ''
    }};
  }});
  const blob = new Blob([JSON.stringify({{schema:'custom_destination_point_acceptance.v1', decisions}}, null, 2) + '\\n'], {{type:'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'custom_destination_point_acceptance.json';
  a.click();
}};
</script>
</body>
</html>
""",
        encoding="utf-8",
    )


def read_point_acceptance(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise CustomDestinationError(f"missing_point_acceptance:{vp.rel(path)}")
    payload = vp.read_json(path)
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise CustomDestinationError(f"point_acceptance_missing_decisions:{vp.rel(path)}")
    accepted = []
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("decision") != "accept":
            continue
        required = ["destination_id", "lat", "lon", "target_edge_id"]
        missing = [key for key in required if decision.get(key) in {"", None}]
        if missing:
            raise CustomDestinationError(f"point_acceptance_missing_fields:{decision.get('destination_id')}:{','.join(missing)}")
        accepted.append(decision)
    if not accepted:
        raise CustomDestinationError(f"point_acceptance_no_accepted_destinations:{vp.rel(path)}")
    return accepted


def route_connected(sumo_net: Any, edge_ids: list[str]) -> tuple[bool, str]:
    if not edge_ids:
        return False, "empty_route"
    try:
        edges = [sumo_net.getEdge(edge_id) for edge_id in edge_ids]
    except Exception as exc:  # noqa: BLE001
        return False, f"missing_edge:{exc}"
    for left, right in zip(edges, edges[1:], strict=False):
        if right not in left.getOutgoing():
            return False, f"disconnected_transition:{left.getID()}->{right.getID()}"
    return True, ""


def route_metric_row(
    s07: Any,
    sumo_net: Any,
    destination: dict[str, Any],
    policy: str,
    edge_ids: list[str],
    shortest_length: float,
    spine_ids: set[str],
    spine_metrics: dict[str, dict[str, float]],
    coords_by_id: dict[str, list[list[float]]],
    axis_ctx: dict[str, float],
) -> dict[str, Any]:
    target_edge = str(destination["target_edge_id"])
    connected, reason = route_connected(sumo_net, edge_ids)
    metrics = s07.route_spine_metrics(sumo_net, edge_ids, shortest_length, spine_ids, spine_metrics, coords_by_id, axis_ctx, target_edge)
    geometry = s07.route_geometry_diagnostics(edge_ids, coords_by_id, axis_ctx)
    score = (
        1000.0 * float(metrics["spine_length_ratio"])
        + 0.8 * float(metrics["max_consecutive_spine_length_m"])
        - 250.0 * max(float(metrics["length_increase_ratio"]), 0.0)
        - 120.0 * int(geometry["repeated_edge_count"])
        - 80.0 * int(geometry["gap_count"])
    )
    route_id = f"CUSTOM_{destination['destination_id']}_{policy.upper()}"
    return {
        "destination_id": destination["destination_id"],
        "label_ko": destination.get("label_ko", ""),
        "address": destination.get("address", ""),
        "lat": destination["lat"],
        "lon": destination["lon"],
        "target_edge_id": target_edge,
        "candidate_route_id": route_id,
        "candidate_policy": policy,
        "route_edges": " ".join(edge_ids),
        "route_edge_count": len(edge_ids),
        "route_length_m": round(float(metrics["route_length_m"]), 3),
        "length_increase_ratio": round(float(metrics["length_increase_ratio"]), 6),
        "spine_length_ratio": round(float(metrics["spine_length_ratio"]), 6),
        "max_consecutive_spine_length_m": round(float(metrics["max_consecutive_spine_length_m"]), 3),
        "selection_score": round(score, 6),
        "connected": connected,
        "connection_reason": reason,
        "route_shape": json.dumps(s07.route_coords(edge_ids, coords_by_id), ensure_ascii=False),
    }


def build_route_candidates(net_path: Path, point_acceptance: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    accepted_points = read_point_acceptance(point_acceptance)
    s07 = step07()
    sumo_net = s07.read_sumo_net(net_path)
    props_by_id, coords_by_id = s07.load_edge_geojson(s07.ACTIVE_EDGES_GEOJSON)
    map_config = s07.load_yaml(s07.MAP_CONFIG)
    axis_ctx = s07.axis_context(map_config)
    axis = s07.bearing_vector(map_config)
    _spine_rows, spine_ids, spine_metrics = s07.build_spine_edges(sumo_net, props_by_id, coords_by_id, axis_ctx)
    rows: list[dict[str, Any]] = []
    for destination in accepted_points:
        target_edge = str(destination["target_edge_id"])
        shortest_ids = s07.shortest_route(sumo_net, START_EDGE_ID, target_edge)
        major_ids = s07.major_route(sumo_net, START_EDGE_ID, target_edge, props_by_id, coords_by_id, axis)
        shortest_length = s07.route_length(sumo_net, shortest_ids)
        candidate_edges = {
            "shortest": shortest_ids,
            "major": major_ids,
        }
        selected = s07.select_spine_route_v2(
            sumo_net,
            START_EDGE_ID,
            target_edge,
            shortest_ids,
            major_ids,
            props_by_id,
            coords_by_id,
            axis,
            axis_ctx,
            spine_ids,
            spine_metrics,
        )
        candidate_edges["toegye_spine"] = list(selected["edge_ids"])
        candidate_edges["max_toegye"] = s07.weighted_route_v2(
            sumo_net,
            START_EDGE_ID,
            target_edge,
            lambda edge: s07.spine_edge_cost(edge, props_by_id, coords_by_id, axis, spine_ids, spine_metrics, 1.35),
            coords_by_id,
            axis_ctx,
        )
        for policy, edge_ids in candidate_edges.items():
            rows.append(
                route_metric_row(
                    s07,
                    sumo_net,
                    destination,
                    policy,
                    edge_ids,
                    shortest_length,
                    spine_ids,
                    spine_metrics,
                    coords_by_id,
                    axis_ctx,
                )
            )
    recommended: dict[str, str] = {}
    for destination_id in {str(row["destination_id"]) for row in rows}:
        dest_rows = [row for row in rows if row["destination_id"] == destination_id and row["connected"]]
        if dest_rows:
            best = max(dest_rows, key=lambda row: float(row["selection_score"]))
            recommended[destination_id] = str(best["candidate_route_id"])
    summary = {
        "schema": "custom_destination_route_candidates.v1",
        "generated_at": vp.utc_now(),
        "net_file": vp.rel(net_path),
        "point_acceptance": vp.rel(point_acceptance),
        "start_edge_id": START_EDGE_ID,
        "candidate_count": len(rows),
        "destination_count": len({row["destination_id"] for row in rows}),
        "recommended_route_ids": recommended,
    }
    return rows, summary


def route_review_payload(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        dest = grouped.setdefault(
            str(row["destination_id"]),
            {
                "destination_id": row["destination_id"],
                "label_ko": row["label_ko"],
                "address": row["address"],
                "lat": row["lat"],
                "lon": row["lon"],
                "target_edge_id": row["target_edge_id"],
                "recommended_route_id": summary["recommended_route_ids"].get(row["destination_id"], ""),
                "routes": [],
            },
        )
        dest["routes"].append(
            {
                "candidate_route_id": row["candidate_route_id"],
                "candidate_policy": row["candidate_policy"],
                "route_edge_count": int(row["route_edge_count"]),
                "route_length_m": float(row["route_length_m"]),
                "spine_length_ratio": float(row["spine_length_ratio"]),
                "max_consecutive_spine_length_m": float(row["max_consecutive_spine_length_m"]),
                "selection_score": float(row["selection_score"]),
                "connected": bool(row["connected"]),
                "connection_reason": row["connection_reason"],
                "shape": json.loads(row["route_shape"]),
            }
        )
    return {"summary": summary, "destinations": list(grouped.values())}


def write_route_review_html(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(
        f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Custom Destination Route Accept</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#17212b; }}
    main {{ display:grid; grid-template-columns:minmax(390px, 460px) 1fr; min-height:100vh; }}
    aside {{ padding:18px; overflow:auto; border-right:1px solid #d9e1e8; background:#f8fafc; }}
    #map {{ min-height:100vh; }}
    h1 {{ font-size:20px; margin:0 0 12px; }}
    .dest {{ background:white; border:1px solid #d9e1e8; border-radius:8px; padding:12px; margin:12px 0; }}
    .route {{ border-top:1px solid #edf1f5; padding-top:8px; margin-top:8px; }}
    .muted {{ color:#5f6b77; font-size:13px; }}
    code {{ font-size:12px; }}
    button {{ padding:9px 11px; border:1px solid #0b65c2; background:#0b65c2; color:white; border-radius:6px; cursor:pointer; }}
  </style>
</head>
<body>
<main>
  <aside>
    <h1>경로 Accept</h1>
    <p class="muted">추천값은 퇴계로 spine 사용률과 연속 사용 길이가 가장 높은 후보입니다. JSON을 내려받아 <code>{vp.rel(ROUTE_ACCEPTANCE_JSON)}</code>에 저장하면 custom B0 실행이 가능합니다.</p>
    <div id="controls"></div>
    <button id="download">Accept JSON 다운로드</button>
  </aside>
  <div id="map"></div>
</main>
<script>
const DATA = {data};
const map = L.map('map').setView([37.558, 126.995], 14);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 20, attribution: '&copy; OpenStreetMap' }}).addTo(map);
const colors = {{shortest:'#64748b', major:'#f97316', toegye_spine:'#0b65c2', max_toegye:'#16a34a'}};
const controls = document.getElementById('controls');
const bounds = [];
DATA.destinations.forEach(dest => {{
  const section = document.createElement('section');
  section.className = 'dest';
  section.innerHTML = `<strong>${{dest.label_ko}}</strong><br><span class="muted">${{dest.address}}</span><br><span class="muted">target <code>${{dest.target_edge_id}}</code></span>`;
  dest.routes.sort((a,b) => b.selection_score - a.selection_score).forEach(route => {{
    const checked = route.candidate_route_id === dest.recommended_route_id ? 'checked' : '';
    const div = document.createElement('div');
    div.className = 'route';
    div.innerHTML = `<label><input type="radio" name="route-${{dest.destination_id}}" value="${{route.candidate_route_id}}" ${{checked}}> <code>${{route.candidate_policy}}</code> len=${{route.route_length_m.toFixed(1)}}m spine=${{(100*route.spine_length_ratio).toFixed(1)}}% max=${{route.max_consecutive_spine_length_m.toFixed(0)}}m</label>`;
    section.appendChild(div);
    const color = colors[route.candidate_policy] || '#7c3aed';
    route.shape.forEach(segment => {{
      const poly = L.polyline(segment.map(p => [p[1], p[0]]), {{color, weight: route.candidate_route_id === dest.recommended_route_id ? 6 : 3, opacity: route.candidate_route_id === dest.recommended_route_id ? 0.85 : 0.45}}).addTo(map);
      poly.bindPopup(`${{dest.label_ko}}<br>${{route.candidate_policy}}<br>spine ${{(100*route.spine_length_ratio).toFixed(1)}}%`);
      segment.forEach(p => bounds.push([p[1], p[0]]));
    }});
  }});
  controls.appendChild(section);
  L.marker([Number(dest.lat), Number(dest.lon)]).addTo(map).bindPopup(`${{dest.label_ko}}`);
  bounds.push([Number(dest.lat), Number(dest.lon)]);
}});
if (bounds.length) map.fitBounds(bounds, {{padding:[30,30]}});
document.getElementById('download').onclick = () => {{
  const decisions = DATA.destinations.map(dest => {{
    const route = document.querySelector(`input[name="route-${{dest.destination_id}}"]:checked`);
    return {{
      destination_id: dest.destination_id,
      decision: route ? 'accept' : 'review_needed',
      candidate_route_id: route ? route.value : ''
    }};
  }});
  const blob = new Blob([JSON.stringify({{schema:'custom_destination_route_acceptance.v1', decisions}}, null, 2) + '\\n'], {{type:'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'custom_route_acceptance.json';
  a.click();
}};
</script>
</body>
</html>
""",
        encoding="utf-8",
    )


def read_route_acceptance(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise CustomDestinationError(f"missing_route_acceptance:{vp.rel(path)}")
    payload = vp.read_json(path)
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise CustomDestinationError(f"route_acceptance_missing_decisions:{vp.rel(path)}")
    accepted = []
    for decision in decisions:
        if isinstance(decision, dict) and decision.get("decision") == "accept":
            if not decision.get("candidate_route_id"):
                raise CustomDestinationError(f"route_acceptance_blank_candidate:{decision.get('destination_id')}")
            accepted.append(decision)
    if not accepted:
        raise CustomDestinationError(f"route_acceptance_no_accepted_routes:{vp.rel(path)}")
    return accepted


def apply_route_acceptance(candidates_csv: Path, acceptance_json: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates = vp.read_csv(candidates_csv)
    decisions = read_route_acceptance(acceptance_json)
    by_candidate = {row["candidate_route_id"]: row for row in candidates}
    selected_rows: list[dict[str, Any]] = []
    seen_destinations: set[str] = set()
    for decision in decisions:
        candidate_id = str(decision["candidate_route_id"])
        row = by_candidate.get(candidate_id)
        if row is None:
            raise CustomDestinationError(f"accepted_candidate_not_found:{candidate_id}")
        destination_id = str(row["destination_id"])
        if destination_id in seen_destinations:
            raise CustomDestinationError(f"duplicate_accepted_destination:{destination_id}")
        seen_destinations.add(destination_id)
        selected_rows.append(
            {
                "route_id": f"CUSTOM_{destination_id}",
                "scenario_id": destination_id,
                "destination_id": destination_id,
                "label_ko": row.get("label_ko", ""),
                "address": row.get("address", ""),
                "lat": row.get("lat", ""),
                "lon": row.get("lon", ""),
                "target_edge_id": row["target_edge_id"],
                "selected_policy": row["candidate_policy"],
                "source_candidate_route_id": row["candidate_route_id"],
                "route_edges": row["route_edges"],
                "route_edge_count": row["route_edge_count"],
                "route_length_m": row["route_length_m"],
                "spine_length_ratio": row["spine_length_ratio"],
                "max_consecutive_spine_length_m": row["max_consecutive_spine_length_m"],
            }
        )
    summary = {
        "schema": "custom_destination_accepted_routes.v1",
        "generated_at": vp.utc_now(),
        "candidates_csv": vp.rel(candidates_csv),
        "acceptance_json": vp.rel(acceptance_json),
        "accepted_route_count": len(selected_rows),
        "route_ids": [row["route_id"] for row in selected_rows],
    }
    return selected_rows, summary


def write_accepted_route_xml(path: Path, rows: list[dict[str, Any]]) -> None:
    step07().write_route_xml(path, rows)


def run_custom_b0(manifest: Path, accepted_routes: Path, output_prefix: str = CUSTOM_B0_PREFIX) -> dict[str, Any]:
    if not accepted_routes.is_file():
        raise CustomDestinationError(f"missing_accepted_routes:{vp.rel(accepted_routes)}")
    command = [
        sys.executable,
        str(RUNNER_PATH),
        "--manifest",
        str(manifest),
        "--route-set",
        "custom_accepted",
        "--custom-routes",
        str(accepted_routes),
        "--modes",
        "B0",
        "--repeats",
        "1",
        "--workers",
        "1",
        "--emit-fcd",
        "--output-prefix",
        output_prefix,
        "--allow-nonfinal-background",
    ]
    completed = subprocess.run(command, cwd=vp.PROJECT_ROOT, check=False, text=True, capture_output=True)
    latest_json = vp.PROJECT_ROOT / f"results/metrics/{output_prefix}/latest.json"
    summary = {
        "schema": "custom_destination_b0_run.v1",
        "generated_at": vp.utc_now(),
        "manifest": vp.rel(manifest),
        "accepted_routes": vp.rel(accepted_routes),
        "output_prefix": output_prefix,
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "latest_json": vp.rel(latest_json) if latest_json.is_file() else "",
    }
    if latest_json.is_file():
        summary["latest"] = vp.read_json(latest_json)
    vp.write_json(CUSTOM_B0_RUN_SUMMARY, summary)
    if completed.returncode != 0:
        raise CustomDestinationError(f"custom_b0_runner_failed:{completed.returncode}:{completed.stderr[-1000:]}")
    return summary


def parse_edge_speeds(edge_data: Path) -> dict[str, float]:
    if not edge_data.is_file():
        return {}
    speeds: dict[str, list[float]] = {}
    for _event, elem in ET.iterparse(edge_data, events=("end",)):
        if elem.tag == "edge":
            edge_id = elem.get("id", "")
            try:
                speed = float(elem.get("speed", "nan")) * 3.6
            except ValueError:
                speed = float("nan")
            if edge_id and not math.isnan(speed) and speed > 0:
                speeds.setdefault(edge_id, []).append(speed)
            elem.clear()
    return {edge_id: sum(values) / len(values) for edge_id, values in speeds.items() if values}


def parse_fcd_trajectory(fcd_xml: Path, vehicle_id: str) -> list[list[float]]:
    if not fcd_xml.is_file():
        return []
    points: list[list[float]] = []
    for _event, elem in ET.iterparse(fcd_xml, events=("end",)):
        if elem.tag == "vehicle" and elem.get("id") == vehicle_id:
            try:
                x = float(elem.get("x", "nan"))
                y = float(elem.get("y", "nan"))
            except ValueError:
                x = float("nan")
                y = float("nan")
            if not math.isnan(x) and not math.isnan(y):
                points.append([x, y])
            elem.clear()
        elif elem.tag == "timestep":
            elem.clear()
    return points


def color_for_speed(speed_kmh: float | None) -> str:
    if speed_kmh is None:
        return "#94a3b8"
    if speed_kmh < 10:
        return "#b91c1c"
    if speed_kmh < 20:
        return "#ea580c"
    if speed_kmh < 35:
        return "#ca8a04"
    return "#16a34a"


def visualization_payload(manifest: Path, accepted_routes: Path, latest_json: Path | None = None) -> dict[str, Any]:
    if not accepted_routes.is_file():
        raise CustomDestinationError(f"missing_accepted_routes:{vp.rel(accepted_routes)}")
    rows = vp.read_csv(accepted_routes)
    latest_json = latest_json or (vp.PROJECT_ROOT / f"results/metrics/{CUSTOM_B0_PREFIX}/latest.json")
    if not latest_json.is_file():
        raise CustomDestinationError(f"missing_custom_b0_latest_json:{vp.rel(latest_json)}")
    latest = vp.read_json(latest_json)
    results_csv = vp.project_path(str(latest.get("results_csv", "")))
    if not results_csv.is_file():
        raise CustomDestinationError(f"missing_custom_b0_results_csv:{vp.rel(results_csv)}")
    result_rows = vp.read_csv(results_csv)
    s07 = step07()
    net_path = manifest_net(manifest)
    sumo_net = s07.read_sumo_net(net_path)
    _props, coords_by_id = s07.load_edge_geojson(s07.ACTIVE_EDGES_GEOJSON)
    result_by_route = {row["route_id"]: row for row in result_rows}
    routes = []
    for route in rows:
        route_id = route["route_id"]
        result = result_by_route.get(route_id, {})
        run_dir = vp.project_path(result.get("run_dir", "")) if result.get("run_dir") else None
        edge_speeds = parse_edge_speeds(run_dir / "edgeData.xml") if run_dir else {}
        vehicle_id = result.get("emergency_vehicle_id", f"{route_id}_B0_no_control_rep01")
        fcd_points = parse_fcd_trajectory(run_dir / "fcd.xml", vehicle_id) if run_dir else []
        edge_ids = route["route_edges"].split()
        route_segments = []
        for edge_id in edge_ids:
            shape = coords_by_id.get(edge_id)
            if not shape:
                try:
                    shape = edge_shape_lonlat(sumo_net, sumo_net.getEdge(edge_id))
                except Exception:  # noqa: BLE001
                    shape = []
            speed = edge_speeds.get(edge_id)
            route_segments.append({"edge_id": edge_id, "shape": shape, "speed_kmh": speed, "color": color_for_speed(speed)})
        routes.append(
            {
                "route": route,
                "result": result,
                "run_dir": vp.rel(run_dir) if run_dir else "",
                "segments": route_segments,
                "trajectory": fcd_points,
            }
        )
    return {
        "schema": "custom_destination_b0_visualization.v1",
        "generated_at": vp.utc_now(),
        "manifest": vp.rel(manifest),
        "accepted_routes": vp.rel(accepted_routes),
        "latest_json": vp.rel(latest_json),
        "routes": routes,
    }


def write_visualization_html(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(
        f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Custom Destination B0 Visualization</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#17212b; }}
    main {{ display:grid; grid-template-columns:minmax(390px, 460px) 1fr; min-height:100vh; }}
    aside {{ padding:18px; overflow:auto; border-right:1px solid #d9e1e8; background:#f8fafc; }}
    #map {{ min-height:100vh; }}
    h1 {{ font-size:20px; margin:0 0 12px; }}
    .route {{ background:white; border:1px solid #d9e1e8; border-radius:8px; padding:12px; margin:12px 0; }}
    .metric {{ display:grid; grid-template-columns:1fr auto; gap:8px; font-size:13px; padding:3px 0; }}
    .muted {{ color:#5f6b77; font-size:13px; }}
    code {{ font-size:12px; }}
  </style>
</head>
<body>
<main>
  <aside>
    <h1>Custom Destination B0</h1>
    <p class="muted">Route polyline, edgeData speed, FCD emergency trajectory를 함께 표시합니다.</p>
    <div id="routes"></div>
  </aside>
  <div id="map"></div>
</main>
<script>
const DATA = {data};
const map = L.map('map').setView([37.558, 126.995], 14);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 20, attribution: '&copy; OpenStreetMap' }}).addTo(map);
const bounds = [];
const panel = document.getElementById('routes');
DATA.routes.forEach(item => {{
  const route = item.route;
  const result = item.result || {{}};
  const div = document.createElement('section');
  div.className = 'route';
  div.innerHTML = `<strong>${{route.label_ko}}</strong><br><span class="muted"><code>${{route.route_id}}</code> ${{route.selected_policy}}</span>
    <div class="metric"><span>status</span><b>${{result.final_status || ''}}</b></div>
    <div class="metric"><span>travel time</span><b>${{result.emergency_travel_time_sec || result.emergency_travel_time || ''}}</b></div>
    <div class="metric"><span>teleport</span><b>${{result.emergency_teleport || ''}}</b></div>
    <div class="metric"><span>route errors</span><b>${{result.route_error_count || ''}}</b></div>
    <div class="metric"><span>run dir</span><code>${{item.run_dir}}</code></div>`;
  panel.appendChild(div);
  item.segments.forEach(seg => {{
    if (!seg.shape || seg.shape.length < 2) return;
    const poly = L.polyline(seg.shape.map(p => [p[1], p[0]]), {{color: seg.color, weight: 5, opacity: 0.78}}).addTo(map);
    poly.bindPopup(`${{route.label_ko}}<br>${{seg.edge_id}}<br>${{seg.speed_kmh == null ? 'no speed' : seg.speed_kmh.toFixed(1) + ' km/h'}}`);
    seg.shape.forEach(p => bounds.push([p[1], p[0]]));
  }});
  if (item.trajectory && item.trajectory.length > 1) {{
    const traj = L.polyline(item.trajectory.map(p => [p[1], p[0]]), {{color:'#111827', weight:3, opacity:0.9, dashArray:'6 4'}}).addTo(map);
    traj.bindPopup(`${{route.label_ko}} emergency FCD`);
  }}
  if (route.lat && route.lon) {{
    L.marker([Number(route.lat), Number(route.lon)]).addTo(map).bindPopup(`${{route.label_ko}} destination`);
    bounds.push([Number(route.lat), Number(route.lon)]);
  }}
}});
if (bounds.length) map.fitBounds(bounds, {{padding:[30,30]}});
</script>
</body>
</html>
""",
        encoding="utf-8",
    )


def candidate_fields() -> list[str]:
    return [
        "destination_id",
        "label_ko",
        "address",
        "lat",
        "lon",
        "target_edge_id",
        "candidate_route_id",
        "candidate_policy",
        "route_edges",
        "route_edge_count",
        "route_length_m",
        "length_increase_ratio",
        "spine_length_ratio",
        "max_consecutive_spine_length_m",
        "selection_score",
        "connected",
        "connection_reason",
        "route_shape",
    ]


def accepted_route_fields() -> list[str]:
    return [
        "route_id",
        "scenario_id",
        "destination_id",
        "label_ko",
        "address",
        "lat",
        "lon",
        "target_edge_id",
        "selected_policy",
        "source_candidate_route_id",
        "route_edges",
        "route_edge_count",
        "route_length_m",
        "spine_length_ratio",
        "max_consecutive_spine_length_m",
    ]
