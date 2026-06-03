#!/usr/bin/env python3
"""Build a presentation-focused Leaflet map around Myeongdong Station."""

from __future__ import annotations

import json
import math
import importlib.util
import csv
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py"
NET_PATH = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
EDGES_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_edges.geojson"
TLS_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_tls.geojson"
OSM_XML = PROJECT_ROOT / "data_raw/osm/jungbu_bbox.osm.xml"
OUTPUT_HTML = PROJECT_ROOT / "results/html/presentation_map_myeongdong.html"
TOEGYE_SEGMENTS_CSV = Path("/Users/junlee/Downloads/구간골격_메인스트림_퇴계로.csv")

MYEONGDONG_CENTER = [37.56099, 126.98631]
FOCUS_RADIUS_M = 780.0
UNDERPASS_RADIUS_M = 430.0
MYEONGDONG_UNDERPASS_OSM_IDS = {"781985783"}
TOEGYE_CORRIDOR_BUFFER_M = 85.0


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("b0_b1_b2_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import runner: {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def distance_m(lat: float, lon: float, center: list[float] = MYEONGDONG_CENTER) -> float:
    return math.hypot((lat - center[0]) * 111_000.0, (lon - center[1]) * 88_000.0)


def point_to_segment_distance_m(lat: float, lon: float, segment: list[list[float]]) -> float:
    if len(segment) < 2:
        return float("inf")
    x = lon * 88_000.0
    y = lat * 111_000.0
    x1 = segment[0][0] * 88_000.0
    y1 = segment[0][1] * 111_000.0
    x2 = segment[-1][0] * 88_000.0
    y2 = segment[-1][1] * 111_000.0
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    px = x1 + t * dx
    py = y1 + t * dy
    return math.hypot(x - px, y - py)


def near_corridor(lat: float, lon: float, corridor_segments: list[list[list[float]]], buffer_m: float = TOEGYE_CORRIDOR_BUFFER_M) -> bool:
    return any(point_to_segment_distance_m(lat, lon, segment) <= buffer_m for segment in corridor_segments)


def geometry_points(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "Point" and len(coords) >= 2:
        return [(float(coords[1]), float(coords[0]))]
    if geometry.get("type") == "LineString":
        return [(float(lat), float(lon)) for lon, lat in coords if lon is not None and lat is not None]
    return []


def feature_near_center(feature: dict[str, Any], radius_m: float) -> bool:
    return any(distance_m(lat, lon) <= radius_m for lat, lon in geometry_points(feature.get("geometry") or {}))


def lane_color(lane_count: int) -> str:
    if lane_count <= 1:
        return "#64748b"
    if lane_count == 2:
        return "#2563eb"
    if lane_count == 3:
        return "#f59e0b"
    return "#dc2626"


def build_tls_points(corridor_segments: list[list[list[float]]]) -> list[dict[str, Any]]:
    payload = load_json(TLS_GEOJSON)
    features = []
    for feature in payload.get("features", []):
        points = geometry_points(feature.get("geometry") or {})
        if not points or not any(near_corridor(lat, lon, corridor_segments, 95.0) for lat, lon in points):
            continue
        props = feature.get("properties") or {}
        features.append(
            {
                "type": "Feature",
                "geometry": feature.get("geometry"),
                "properties": {
                    "tls_id": props.get("tls_id", ""),
                    "controlled_link_count": props.get("controlled_link_count", ""),
                    "phase_count": props.get("phase_count", ""),
                },
            }
        )
    return features


def edge_shape_latlon(sumo_net: Any, edge_id: str) -> list[list[float]]:
    edge = sumo_net.getEdge(edge_id)
    shape = list(edge.getShape())
    if len(shape) < 2 and edge.getLanes():
        shape = list(edge.getLanes()[0].getShape())
    coords = []
    for x, y in shape:
        lon, lat = sumo_net.convertXY2LonLat(float(x), float(y))
        coords.append([lon, lat])
    return coords


def build_seoul_station_route() -> dict[str, Any]:
    runner = load_runner()
    sumo_net = runner.S14.read_sumo_net(str(NET_PATH))
    selected = runner.synthetic_seoul_station_route(NET_PATH)
    edge_ids = selected["route_edges"].split()
    features = []
    for index, edge_id in enumerate(edge_ids):
        coords = edge_shape_latlon(sumo_net, edge_id)
        if len(coords) < 2:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {"edge_id": edge_id, "index": index},
            }
        )
    endpoint = features[-1]["geometry"]["coordinates"][-1] if features else [126.971443, 37.558488]
    return {
        "route_id": selected["route_id"],
        "policy": selected["selected_policy"],
        "target_edge": selected["target_edge_id"],
        "edge_count": len(edge_ids),
        "endpoint": [endpoint[1], endpoint[0]],
        "features": features,
    }


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def segment_speed_color(speed_kmh: float) -> str:
    if speed_kmh < 16.0:
        return "#ef4444"
    if speed_kmh < 18.0:
        return "#f97316"
    return "#7c3aed"


def load_osm_lane_fallbacks() -> list[dict[str, Any]]:
    root = ET.parse(OSM_XML).getroot()
    nodes = osm_node_lookup(root)
    rows = []
    for way in root.findall("way"):
        tags = tags_for(way)
        if "퇴계로" not in (tags.get("name:ko") or tags.get("name") or ""):
            continue
        total = parse_int(tags.get("lanes"))
        forward = parse_int(tags.get("lanes:forward"))
        backward = parse_int(tags.get("lanes:backward"))
        if total is None and forward is None and backward is None:
            continue
        coords = way_coords(way, nodes)
        if len(coords) < 2:
            continue
        rows.append({"coords": coords, "total": total, "forward": forward, "backward": backward})
    return rows


def nearest_osm_lane_pair(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    fallback_rows: list[dict[str, Any]],
) -> tuple[int | None, int | None]:
    if not fallback_rows:
        return None, None
    mid_lat = (start_lat + end_lat) / 2.0
    mid_lon = (start_lon + end_lon) / 2.0
    best = min(
        fallback_rows,
        key=lambda row: point_to_segment_distance_m(mid_lat, mid_lon, row["coords"]),
    )
    if point_to_segment_distance_m(mid_lat, mid_lon, best["coords"]) > 120.0:
        return None, None
    forward = best.get("forward")
    backward = best.get("backward")
    total = best.get("total")
    if forward is not None or backward is not None:
        if forward is None and total is not None and backward is not None:
            forward = max(total - backward, 1)
        if backward is None and total is not None and forward is not None:
            backward = max(total - forward, 1)
        return forward, backward
    if total is not None:
        each = max(int(round(total / 2.0)), 1)
        return each, each
    return None, None


def build_toegye_segments() -> dict[str, Any]:
    if not TOEGYE_SEGMENTS_CSV.is_file():
        return {"features": [], "labels": [], "critical_points": [], "source": str(TOEGYE_SEGMENTS_CSV)}
    features: list[dict[str, Any]] = []
    critical_seen: set[tuple[str, str]] = set()
    critical_points: list[dict[str, Any]] = []
    osm_lane_fallbacks = load_osm_lane_fallbacks()
    with TOEGYE_SEGMENTS_CSV.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    for row in rows:
        start_lat = parse_float(row.get("시작_lat(위도)"))
        start_lon = parse_float(row.get("시작_lon(경도)"))
        end_lat = parse_float(row.get("끝_lat(위도)"))
        end_lon = parse_float(row.get("끝_lon(경도)"))
        if not all([start_lat, start_lon, end_lat, end_lon]):
            continue
        speed_up = parse_float(row.get("평균속도_kmh(상행)"))
        csv_lane_up = parse_int(row.get("상행차로수(서울역방향)"))
        csv_lane_down = parse_int(row.get("하행차로수(성동고교방향)"))
        osm_lane_up, osm_lane_down = nearest_osm_lane_pair(start_lat, start_lon, end_lat, end_lon, osm_lane_fallbacks)
        lane_up = csv_lane_up or osm_lane_up or 1
        lane_down = csv_lane_down or osm_lane_down or lane_up
        lane_source = "CSV" if csv_lane_up is not None and csv_lane_down is not None else "OSM fallback"
        coords = [[start_lon, start_lat], [end_lon, end_lat]]
        props = {
            "section": row.get("구간", ""),
            "start": row.get("시작교차로", ""),
            "end": row.get("끝교차로", ""),
            "length_m": row.get("구간길이_m", ""),
            "lane_up": str(lane_up),
            "lane_down": str(lane_down),
            "lane_count": max(lane_up, lane_down),
            "lane_source": lane_source,
            "speed_up": row.get("평균속도_kmh(상행)", ""),
            "speed_down": row.get("평균속도_kmh(하행)", ""),
            "travel_up": row.get("통과시간_s(상행)", ""),
            "travel_down": row.get("통과시간_s(하행)", ""),
            "peak_volume": row.get("첨두교통량_대시(참고)", ""),
            "color": segment_speed_color(speed_up),
            "weight": min(5 + lane_up, 9),
        }
        features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": props})
        for side, flag_key, name_key, lat, lon in [
            ("start", "시작_결정적교차로", "시작교차로", start_lat, start_lon),
            ("end", "끝_결정적교차로", "끝교차로", end_lat, end_lon),
        ]:
            if not row.get(flag_key):
                continue
            key = (row.get(name_key, ""), f"{lat:.6f},{lon:.6f}")
            if key in critical_seen:
                continue
            critical_seen.add(key)
            critical_points.append(
                {
                    "name": row.get(name_key, ""),
                    "section": row.get("구간", ""),
                    "side": side,
                    "lat": lat,
                    "lon": lon,
                }
            )
    return {
        "features": features,
        "critical_points": critical_points,
        "source": str(TOEGYE_SEGMENTS_CSV),
    }


def build_toegye_lane_edges(toegye_features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    features = []
    for feature in toegye_features:
        props = feature.get("properties") or {}
        lane_count = int(props.get("lane_count") or 1)
        features.append(
            {
                "type": "Feature",
                "geometry": feature.get("geometry"),
                "properties": {
                    "section": props.get("section", ""),
                    "start": props.get("start", ""),
                    "end": props.get("end", ""),
                    "lane_count": lane_count,
                    "lane_up": props.get("lane_up", ""),
                    "lane_down": props.get("lane_down", ""),
                    "lane_source": props.get("lane_source", ""),
                    "length_m": props.get("length_m", ""),
                    "color": lane_color(lane_count),
                    "weight": min(4 + lane_count * 1.8, 10),
                },
            }
        )
    return features


def midpoint(coords: list[list[float]]) -> list[float] | None:
    if not coords:
        return None
    return coords[len(coords) // 2]


def osm_node_lookup(root: ET.Element) -> dict[str, tuple[float, float]]:
    nodes: dict[str, tuple[float, float]] = {}
    for node in root.findall("node"):
        node_id = node.get("id")
        lat = node.get("lat")
        lon = node.get("lon")
        if node_id and lat and lon:
            nodes[node_id] = (float(lat), float(lon))
    return nodes


def tags_for(elem: ET.Element) -> dict[str, str]:
    return {tag.get("k", ""): tag.get("v", "") for tag in elem.findall("tag") if tag.get("k")}


def way_coords(way: ET.Element, nodes: dict[str, tuple[float, float]]) -> list[list[float]]:
    coords = []
    for ref in [nd.get("ref") for nd in way.findall("nd")]:
        if ref in nodes:
            lat, lon = nodes[ref]
            coords.append([lon, lat])
    return coords


def build_osm_layers(corridor_segments: list[list[list[float]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    root = ET.parse(OSM_XML).getroot()
    nodes = osm_node_lookup(root)
    crosswalk_lines: list[dict[str, Any]] = []
    crosswalk_points: list[dict[str, Any]] = []
    underpasses: list[dict[str, Any]] = []

    for node in root.findall("node"):
        tags = tags_for(node)
        if tags.get("highway") != "crossing":
            continue
        lat = float(node.get("lat") or 0)
        lon = float(node.get("lon") or 0)
        if not near_corridor(lat, lon, corridor_segments):
            continue
        crosswalk_points.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"osm_id": node.get("id", ""), "kind": "crossing_node"},
            }
        )

    for way in root.findall("way"):
        tags = tags_for(way)
        coords = way_coords(way, nodes)
        if len(coords) < 2:
            continue
        center = midpoint(coords)
        if center is None:
            continue
        center_lat = center[1]
        center_lon = center[0]
        is_crossing = tags.get("footway") == "crossing" or tags.get("crossing") not in (None, "")
        if is_crossing and near_corridor(center_lat, center_lon, corridor_segments):
            crosswalk_lines.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {
                        "osm_id": way.get("id", ""),
                        "surface": tags.get("surface", ""),
                        "kind": "crossing_way",
                    },
                }
            )

        if way.get("id") in MYEONGDONG_UNDERPASS_OSM_IDS and distance_m(center_lat, center_lon) <= UNDERPASS_RADIUS_M:
            underpasses.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {
                        "osm_id": way.get("id", ""),
                        "name": tags.get("name:ko") or tags.get("name") or "퇴계로 지하차도",
                        "highway": tags.get("highway", ""),
                        "layer": tags.get("layer", ""),
                        "tunnel": tags.get("tunnel", ""),
                    },
                }
            )

    return crosswalk_lines, crosswalk_points, underpasses


def bounds_from_features(features: list[dict[str, Any]]) -> list[list[float]]:
    points: list[list[float]] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") == "Point":
            lon, lat = geometry.get("coordinates", [0, 0])
            points.append([lat, lon])
        elif geometry.get("type") == "LineString":
            points.extend([[lat, lon] for lon, lat in geometry.get("coordinates", [])])
    if not points:
        return [[MYEONGDONG_CENTER[0], MYEONGDONG_CENTER[1]], [MYEONGDONG_CENTER[0], MYEONGDONG_CENTER[1]]]
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


def build_payload() -> dict[str, Any]:
    seoul_station_route = build_seoul_station_route()
    toegye_segments = build_toegye_segments()
    toegye_features = toegye_segments["features"]
    lane_edges = build_toegye_lane_edges(toegye_features)
    corridor_segments = [feature["geometry"]["coordinates"] for feature in toegye_features]
    tls_points = build_tls_points(corridor_segments)
    crosswalk_lines, crosswalk_points, underpasses = build_osm_layers(corridor_segments)
    route_features = seoul_station_route["features"]
    all_focus_features = lane_edges + tls_points + crosswalk_lines + crosswalk_points + underpasses + route_features + toegye_features
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "title": "명동역 주변 발표용 SUMO 지도",
        "center": MYEONGDONG_CENTER,
        "focus_bounds": bounds_from_features(toegye_features + underpasses),
        "all_bounds": bounds_from_features(all_focus_features),
        "counts": {
            "lane_edges": len(lane_edges),
            "tls_points": len(tls_points),
            "crosswalk_lines": len(crosswalk_lines),
            "crosswalk_points": len(crosswalk_points),
            "underpasses": len(underpasses),
            "seoul_station_route_edges": len(route_features),
            "toegye_segments": len(toegye_features),
            "toegye_critical_points": len(toegye_segments["critical_points"]),
        },
        "lane_edges": {"type": "FeatureCollection", "features": lane_edges},
        "tls_points": {"type": "FeatureCollection", "features": tls_points},
        "seoul_station_route": {
            "type": "FeatureCollection",
            "route_id": seoul_station_route["route_id"],
            "policy": seoul_station_route["policy"],
            "target_edge": seoul_station_route["target_edge"],
            "edge_count": seoul_station_route["edge_count"],
            "endpoint": seoul_station_route["endpoint"],
            "features": route_features,
        },
        "toegye_segments": {
            "type": "FeatureCollection",
            "source": toegye_segments["source"],
            "critical_points": toegye_segments["critical_points"],
            "features": toegye_features,
        },
        "crosswalk_lines": {"type": "FeatureCollection", "features": crosswalk_lines},
        "crosswalk_points": {"type": "FeatureCollection", "features": crosswalk_points},
        "underpasses": {"type": "FeatureCollection", "features": underpasses},
    }


def render_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>명동역 주변 발표용 SUMO 지도</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body {{ height: 100%; margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111827; }}
    #map {{ height: 100vh; min-height: 620px; background: #eef2f7; }}
    .leaflet-control-attribution {{ font-size: 10px; }}
    .panel {{
      position: absolute;
      z-index: 500;
      top: 18px;
      left: 18px;
      width: 306px;
      background: rgba(255, 255, 255, .94);
      border: 1px solid rgba(15, 23, 42, .14);
      border-radius: 8px;
      box-shadow: 0 12px 30px rgba(15, 23, 42, .18);
      padding: 14px 14px 12px;
      backdrop-filter: blur(4px);
    }}
    .legend {{ display: grid; gap: 8px; margin: 0 0 12px; }}
    .legend-item {{ display: grid; grid-template-columns: 28px 1fr auto; align-items: center; gap: 8px; font-size: 12px; min-height: 24px; }}
    .line-swatch {{ height: 0; border-top: 5px solid #2563eb; border-radius: 999px; }}
    .line-swatch.lane1 {{ border-color: #64748b; border-width: 3px; }}
    .line-swatch.lane2 {{ border-color: #2563eb; border-width: 5px; }}
    .line-swatch.lane3 {{ border-color: #f59e0b; border-width: 6px; }}
    .line-swatch.lane4 {{ border-color: #dc2626; border-width: 7px; }}
    .line-swatch.crosswalk {{ width: 15px; height: 15px; border: 3px solid #111827; border-radius: 999px; background: #ffffff; box-shadow: 0 0 0 1px #ffffff; }}
    .line-swatch.underpass {{ border-color: #14b8a6; border-width: 8px; box-shadow: 0 0 0 2px white; }}
    .line-swatch.route {{ border-color: #1d4ed8; border-width: 8px; box-shadow: 0 0 0 2px white; }}
    .line-swatch.toegye {{ border-color: #7c3aed; border-width: 8px; box-shadow: 0 0 0 2px white; }}
    .dot-swatch {{ width: 16px; height: 16px; border-radius: 999px; background: #ef4444; border: 3px solid #fff; box-shadow: 0 0 0 2px #991b1b; margin-left: 4px; }}
    .count {{ color: #64748b; font-size: 11px; }}
    .actions {{ display: flex; gap: 7px; flex-wrap: wrap; }}
    button {{
      appearance: none;
      border: 1px solid #cbd5e1;
      background: #f8fafc;
      color: #0f172a;
      border-radius: 6px;
      padding: 6px 8px;
      font-size: 12px;
      cursor: pointer;
    }}
    button:hover {{ background: #e2e8f0; }}
    .leaflet-control-layers {{
      border-radius: 8px;
      border: 1px solid rgba(15, 23, 42, .14);
      box-shadow: 0 8px 24px rgba(15, 23, 42, .16);
    }}
    .underpass-label {{
      color: #0f766e;
      font-weight: 800;
      font-size: 14px;
      text-shadow: 0 1px 0 white, 0 0 4px white;
      white-space: nowrap;
    }}
    .station-label {{
      color: #0f172a;
      font-weight: 800;
      font-size: 13px;
      text-shadow: 0 1px 0 white, 0 0 4px white;
      white-space: nowrap;
    }}
    .seoul-label {{
      color: #1d4ed8;
      font-weight: 900;
      font-size: 14px;
      text-shadow: 0 1px 0 white, 0 0 5px white;
      white-space: nowrap;
    }}
    .critical-marker {{
      color: #92400e;
      font-size: 21px;
      line-height: 21px;
      font-weight: 900;
      text-shadow: 0 1px 0 #fff, 0 0 5px #fff;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <aside class="panel">
    <div class="legend">
      <div class="legend-item" data-legend-layer="lane"><span class="line-swatch lane1"></span><span>1차선 도로</span><span class="count">회색</span></div>
      <div class="legend-item" data-legend-layer="lane"><span class="line-swatch lane2"></span><span>2차선 도로</span><span class="count">파랑</span></div>
      <div class="legend-item" data-legend-layer="lane"><span class="line-swatch lane3"></span><span>3차선 도로</span><span class="count">노랑</span></div>
      <div class="legend-item" data-legend-layer="lane"><span class="line-swatch lane4"></span><span>4차선 이상</span><span class="count">빨강</span></div>
      <div class="legend-item" data-legend-layer="crosswalk"><span class="line-swatch crosswalk"></span><span>횡단보도</span><span class="count" id="crosswalk-count"></span></div>
      <div class="legend-item" data-legend-layer="tls"><span class="dot-swatch"></span><span>신호등 위치</span><span class="count" id="tls-count"></span></div>
      <div class="legend-item" data-legend-layer="underpass"><span class="line-swatch underpass"></span><span>명동역 지하차도</span><span class="count" id="underpass-count"></span></div>
    </div>
    <div class="actions">
      <button type="button" id="fit-focus">퇴계로 구간</button>
      <button type="button" id="fit-all">주변 전체</button>
    </div>
  </aside>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const DATA = {data};
    const map = L.map('map', {{ preferCanvas: true, zoomControl: false }}).setView(DATA.center, 17);
    L.control.zoom({{ position: 'bottomright' }}).addTo(map);
    const backgroundLayer = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 20,
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);

    function fitBounds(bounds, padding = [52, 52]) {{
      if (!bounds || !bounds.length) return;
      const leafletBounds = L.latLngBounds(bounds);
      if (leafletBounds.isValid()) map.fitBounds(leafletBounds, {{ padding }});
    }}

    function lineTooltip(props) {{
      return `<strong>${{props.section}} ${{props.start}} → ${{props.end}}</strong><br>` +
        `대표 차선수 ${{props.lane_count}} · 서울역방향/성동고교방향 ${{props.lane_up}}/${{props.lane_down}}<br>` +
        `길이 ${{props.length_m}} m · source=${{props.lane_source}}`;
    }}

    const laneHaloLayer = L.geoJSON(DATA.lane_edges, {{
      style: {{
        color: '#ffffff',
        weight: 13,
        opacity: .92,
        lineCap: 'round',
        lineJoin: 'round'
      }}
    }});
    const laneLineLayer = L.geoJSON(DATA.lane_edges, {{
      style: (feature) => ({{
        color: feature.properties.color,
        weight: feature.properties.weight,
        opacity: .82,
        lineCap: 'round',
        lineJoin: 'round'
      }}),
      onEachFeature: (feature, layer) => layer.bindTooltip(lineTooltip(feature.properties), {{ sticky: true }})
    }});
    const laneLayer = L.layerGroup([laneHaloLayer, laneLineLayer]);

    const seoulRouteHaloLayer = L.geoJSON(DATA.seoul_station_route, {{
      style: {{
        color: '#ffffff',
        weight: 13,
        opacity: .96,
        lineCap: 'round',
        lineJoin: 'round'
      }}
    }});
    const seoulRouteLineLayer = L.geoJSON(DATA.seoul_station_route, {{
      style: {{
        color: '#1d4ed8',
        weight: 8,
        opacity: .95,
        lineCap: 'round',
        lineJoin: 'round'
      }},
      onEachFeature: (feature, layer) => layer.bindTooltip(`서울역 연결 경로<br>#${{feature.properties.index}} ${{feature.properties.edge_id}}`, {{ sticky: true }})
    }});
    const seoulRouteLayer = L.layerGroup([seoulRouteHaloLayer, seoulRouteLineLayer]);

    function toegyeTooltip(props) {{
      return `<strong>${{props.section}} ${{props.start}} → ${{props.end}}</strong><br>` +
        `길이 ${{props.length_m}} m · 상행/하행 차로 ${{props.lane_up}}/${{props.lane_down}}<br>` +
        `평균속도 상행/하행 ${{props.speed_up}}/${{props.speed_down}} km/h<br>` +
        `통과시간 상행/하행 ${{props.travel_up}}/${{props.travel_down}} s<br>` +
        `첨두교통량 ${{props.peak_volume}} 대시`;
    }}
    const toegyeHaloLayer = L.geoJSON(DATA.toegye_segments, {{
      style: {{
        color: '#ffffff',
        weight: 15,
        opacity: .9,
        lineCap: 'round',
        lineJoin: 'round'
      }}
    }});
    const toegyeLineLayer = L.geoJSON(DATA.toegye_segments, {{
      style: (feature) => ({{
        color: feature.properties.color,
        weight: feature.properties.weight,
        opacity: .95,
        lineCap: 'round',
        lineJoin: 'round'
      }}),
      onEachFeature: (feature, layer) => layer.bindTooltip(toegyeTooltip(feature.properties), {{ sticky: true }})
    }});
    const criticalLayer = L.layerGroup(DATA.toegye_segments.critical_points.map((item) => L.marker([item.lat, item.lon], {{
      icon: L.divIcon({{ className: 'critical-marker', html: '★', iconSize: [24, 24], iconAnchor: [12, 12] }})
    }}).bindTooltip(`결정적 교차로<br>${{item.name}} (${{item.section}})`, {{ sticky: true }})));
    const toegyeLayer = L.layerGroup([toegyeHaloLayer, toegyeLineLayer, criticalLayer]).addTo(map);

    const crosswalkLinePointLayer = L.layerGroup(DATA.crosswalk_lines.features.map((feature) => {{
        const coords = feature.geometry.coordinates || [];
        const middle = coords[Math.floor(coords.length / 2)] || coords[0];
        return L.circleMarker([middle[1], middle[0]], {{
          radius: 5,
          color: '#111827',
          weight: 2,
          fillColor: '#ffffff',
          fillOpacity: .95
        }}).bindTooltip(`횡단보도 OSM ${{feature.properties.osm_id}}`, {{ sticky: true }});
    }}));

    const crosswalkPointLayer = L.geoJSON(DATA.crosswalk_points, {{
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {{
        radius: 5,
        color: '#111827',
        weight: 2,
        fillColor: '#ffffff',
        fillOpacity: .95
      }}),
      onEachFeature: (feature, layer) => layer.bindTooltip(`횡단보도 OSM ${{feature.properties.osm_id}}`, {{ sticky: true }})
    }});
    const crosswalkLayer = L.layerGroup([crosswalkLinePointLayer, crosswalkPointLayer]).addTo(map);

    const tlsLayer = L.geoJSON(DATA.tls_points, {{
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {{
        radius: 7,
        color: '#991b1b',
        weight: 2,
        fillColor: '#ef4444',
        fillOpacity: .95
      }}),
      onEachFeature: (feature, layer) => {{
        const p = feature.properties;
        layer.bindTooltip(`신호등 ${{p.tls_id}}<br>links=${{p.controlled_link_count}} phases=${{p.phase_count}}`, {{ sticky: true }});
      }}
    }}).addTo(map);

    const underpassLayer = L.geoJSON(DATA.underpasses, {{
      style: {{
        color: '#14b8a6',
        weight: 10,
        opacity: .92,
        lineCap: 'round',
        lineJoin: 'round'
      }},
      onEachFeature: (feature, layer) => {{
        const p = feature.properties;
        layer.bindTooltip(`${{p.name}} 지하차도 후보<br>OSM ${{p.osm_id}} · layer=${{p.layer}} · tunnel=${{p.tunnel}}`, {{ sticky: true }});
      }}
    }}).addTo(map);

    L.circleMarker(DATA.center, {{
      radius: 6,
      color: '#0f172a',
      weight: 2,
      fillColor: '#ffffff',
      fillOpacity: 1
    }}).addTo(map);
    L.marker(DATA.center, {{
      interactive: false,
      icon: L.divIcon({{
        className: 'station-label',
        html: '명동역',
        iconSize: [70, 18],
        iconAnchor: [-10, 20]
      }})
    }}).addTo(map);
    L.circleMarker(DATA.seoul_station_route.endpoint, {{
      radius: 7,
      color: '#1e3a8a',
      weight: 2,
      fillColor: '#3b82f6',
      fillOpacity: 1
    }}).bindTooltip(`서울역 도착 edge<br>${{DATA.seoul_station_route.target_edge}}`, {{ sticky: true }}).addTo(map);
    L.marker(DATA.seoul_station_route.endpoint, {{
      interactive: false,
      icon: L.divIcon({{
        className: 'seoul-label',
        html: '서울역',
        iconSize: [70, 20],
        iconAnchor: [-12, 22]
      }})
    }}).addTo(map);

    if (DATA.underpasses.features.length) {{
      const firstLine = DATA.underpasses.features[0].geometry.coordinates;
      const labelCoord = firstLine[Math.floor(firstLine.length / 2)];
      L.marker([labelCoord[1], labelCoord[0]], {{
        interactive: false,
        icon: L.divIcon({{
          className: 'underpass-label',
          html: '퇴계로 지하차도',
          iconSize: [120, 20],
          iconAnchor: [-12, 28]
        }})
      }}).addTo(map);
    }}

    L.control.layers(null, {{
      '차선 수': laneLayer,
      '배경 지도': backgroundLayer,
      '서울역 연결 경로': seoulRouteLayer,
      '퇴계로 구간 골격': toegyeLayer,
      '횡단보도': crosswalkLayer,
      '신호등': tlsLayer,
      '명동역 지하차도': underpassLayer
    }}, {{ collapsed: false, position: 'topright' }}).addTo(map);

    const legendLayerKeys = new Map([
      [laneLayer, 'lane'],
      [crosswalkLayer, 'crosswalk'],
      [tlsLayer, 'tls'],
      [underpassLayer, 'underpass']
    ]);
    function setLegendVisible(key, visible) {{
      document.querySelectorAll(`[data-legend-layer="${{key}}"]`).forEach((item) => {{
        item.style.display = visible ? '' : 'none';
      }});
    }}
    function syncLegendVisibility() {{
      legendLayerKeys.forEach((key, layer) => setLegendVisible(key, map.hasLayer(layer)));
    }}
    map.on('overlayadd overlayremove', (event) => {{
      const key = legendLayerKeys.get(event.layer);
      if (key) setLegendVisible(key, event.type === 'overlayadd');
    }});
    syncLegendVisibility();

    document.getElementById('crosswalk-count').textContent = DATA.counts.crosswalk_lines + DATA.counts.crosswalk_points;
    document.getElementById('tls-count').textContent = DATA.counts.tls_points;
    document.getElementById('underpass-count').textContent = DATA.counts.underpasses;
    document.getElementById('fit-focus').addEventListener('click', () => fitBounds(DATA.focus_bounds, [90, 90]));
    document.getElementById('fit-all').addEventListener('click', () => fitBounds(DATA.all_bounds, [38, 38]));

    setTimeout(() => fitBounds(DATA.focus_bounds, [90, 90]), 120);
  </script>
</body>
</html>
"""


def main() -> int:
    payload = build_payload()
    counts = payload["counts"]
    missing = [name for name, count in counts.items() if count == 0]
    if missing:
        raise RuntimeError(f"empty presentation layer(s): {', '.join(missing)}")

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"wrote {OUTPUT_HTML}")
    print(
        "layers: "
        f"lane_edges={counts['lane_edges']}, "
        f"tls={counts['tls_points']}, "
        f"crosswalks={counts['crosswalk_lines'] + counts['crosswalk_points']}, "
        f"underpasses={counts['underpasses']}, "
        f"seoul_station_route_edges={counts['seoul_station_route_edges']}, "
        f"toegye_segments={counts['toegye_segments']}, "
        f"toegye_critical_points={counts['toegye_critical_points']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
