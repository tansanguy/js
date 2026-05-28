#!/usr/bin/env python3
"""Generate Step 7 emergency routes, route review HTML, and preflight."""

from __future__ import annotations

import csv
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
LENGTH_WARNING_RATIO = 0.35
MANUAL_REVIEW_RATIO = 0.60

EMERGENCY_ROUTES_CSV = PROJECT_ROOT / "data_prepared/routes/emergency_routes.csv"
EMERGENCY_ROUTES_XML = PROJECT_ROOT / "data_prepared/routes/emergency_routes.rou.xml"
ROUTE_COMPARE_CSV = PROJECT_ROOT / "data_prepared/routes/route_compare_shortest_vs_major.csv"
ACCIDENT_SCENARIOS_CSV = PROJECT_ROOT / "data_prepared/scenarios/accident_scenarios.csv"
EMERGENCY_ROUTE_SUMMARY = PROJECT_ROOT / "data_prepared/routes/emergency_route_summary.json"
ROUTE_REVIEW_HTML = PROJECT_ROOT / "results/html/route_review.html"
ROUTE_REVIEW_SCHEMA = PROJECT_ROOT / "data_prepared/manual/route_review_decisions.schema.json"
PREFLIGHT_SUMMARY = PROJECT_ROOT / "data_prepared/preflight/preflight_summary.json"
PREFLIGHT_REPORT = PROJECT_ROOT / "data_prepared/preflight/preflight_report.csv"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step07_emergency_routes.log"


class Step07Error(RuntimeError):
    """Expected Step 7 failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


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
    #map {{ height:100vh; }}
    .route {{ border:1px solid #d7dce5; background:white; margin:0 0 10px; padding:10px; border-radius:6px; }}
    .route.active {{ border-color:#2563eb; box-shadow:0 0 0 2px rgba(37,99,235,.15); }}
    .meta {{ font-size:12px; color:#667085; line-height:1.5; }}
    button {{ margin:4px 4px 4px 0; border:1px solid #cfd6e2; background:white; border-radius:6px; padding:6px 8px; cursor:pointer; }}
    button.primary {{ background:#2563eb; color:white; border-color:#2563eb; }}
    textarea {{ width:100%; min-height:44px; box-sizing:border-box; margin-top:6px; }}
    .warn {{ color:#b45309; font-weight:600; }}
    @media (max-width: 900px) {{ .app {{ grid-template-columns:1fr; grid-template-rows:46vh 54vh; }} aside {{ grid-row:2; }} #map {{ grid-row:1; height:46vh; }} }}
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h2>Route Review</h2>
      <p class="meta">Blue=major-road-biased, gray=shortest. Decisions download as route_review_decisions.json.</p>
      <button class="primary" id="download">Download decisions</button>
      <div id="routes"></div>
    </aside>
    <main id="map"></main>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const ROUTE_REVIEW_DATA = {escaped_data};
    const decisions = {{}};
    const map = L.map('map');
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 19 }}).addTo(map);
    const layers = new Map();
    function latlngs(segments) {{ return segments.map(seg => seg.map(p => [p[1], p[0]])); }}
    function addRoute(route) {{
      const group = L.layerGroup();
      latlngs(route.shortest_segments).forEach(seg => L.polyline(seg, {{color:'#6b7280', weight:3, opacity:.55}}).addTo(group));
      latlngs(route.major_segments).forEach(seg => L.polyline(seg, {{color:'#2563eb', weight:5, opacity:.82}}).addTo(group));
      group.addTo(map);
      layers.set(route.route_id, group);
    }}
    function focusRoute(routeId) {{
      document.querySelectorAll('.route').forEach(el => el.classList.toggle('active', el.dataset.routeId === routeId));
      const group = layers.get(routeId);
      if (group) map.fitBounds(group.getBounds(), {{padding:[20,20]}});
    }}
    const container = document.getElementById('routes');
    ROUTE_REVIEW_DATA.routes.forEach(route => {{
      decisions[route.route_id] = {{ route_id: route.route_id, scenario_id: route.scenario_id, target_edge_id: route.target_edge_id, decision: 'pending', reject_reason: '' }};
      addRoute(route);
      const el = document.createElement('section');
      el.className = 'route';
      el.dataset.routeId = route.route_id;
      el.innerHTML = `<strong>${{route.route_id}}</strong> <span class="${{route.review_status === 'PASS' ? '' : 'warn'}}">${{route.review_status}}</span>
        <div class="meta">target=${{route.target_edge_id}}<br>major=${{route.major_length_m}}m, shortest=${{route.shortest_length_m}}m, increase=${{route.length_increase_pct}}%, edges=${{route.route_edge_count}}, TLS=${{route.route_tls_count}}</div>
        <button data-action="focus">Focus</button><button data-action="accept">Accept</button><button data-action="reject">Reject</button>
        <textarea placeholder="reject reason"></textarea>`;
      el.querySelector('[data-action=focus]').onclick = () => focusRoute(route.route_id);
      el.querySelector('[data-action=accept]').onclick = () => {{ decisions[route.route_id].decision = 'accept'; el.style.borderColor = '#12805c'; }};
      el.querySelector('[data-action=reject]').onclick = () => {{ decisions[route.route_id].decision = 'reject'; el.style.borderColor = '#c2410c'; }};
      el.querySelector('textarea').oninput = event => decisions[route.route_id].reject_reason = event.target.value;
      container.appendChild(el);
    }});
    const all = L.featureGroup(Array.from(layers.values()));
    if (all.getLayers().length) map.fitBounds(all.getBounds(), {{padding:[20,20]}});
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


def main() -> int:
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

        sumo_net = read_sumo_net(ACTIVE_NET)
        props_by_id, coords_by_id = load_edge_geojson(ACTIVE_EDGES_GEOJSON)
        axis = bearing_vector(load_yaml(MAP_CONFIG))
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
            shortest_length = route_length(sumo_net, shortest_ids)
            major_length = route_length(sumo_net, major_ids)
            increase_ratio = (major_length - shortest_length) / shortest_length if shortest_length else 0.0
            route_edges = route_objects(sumo_net, major_ids)
            tls_ids = sorted(route_tls_ids(route_edges))
            review_status = "PASS"
            warning_text = ""
            if increase_ratio > MANUAL_REVIEW_RATIO:
                review_status = "needs_manual_review"
                warning_text = f"major_route_length_gt_{MANUAL_REVIEW_RATIO:.0%}_over_shortest"
                manual_reviews += 1
            elif increase_ratio > LENGTH_WARNING_RATIO:
                review_status = "WARNING"
                warning_text = f"major_route_length_gt_{LENGTH_WARNING_RATIO:.0%}_over_shortest"
                warnings += 1
            route_text = " ".join(major_ids)
            route_rows.append(
                {
                    "route_id": route_id,
                    "scenario_id": scenario_id,
                    "target_edge_id": target_edge_id,
                    "route_edges": route_text,
                    "route_length_m": round(major_length, 3),
                    "route_edge_count": len(major_ids),
                    "route_tls_count": len(tls_ids),
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
                    "major_route_edges": route_text,
                    "major_length_m": round(major_length, 3),
                    "major_edge_count": len(major_ids),
                    "major_tls_count": len(tls_ids),
                    "length_increase_pct": round(increase_ratio * 100, 3),
                    "review_status": review_status,
                    "warnings": warning_text,
                }
            )
            scenario_rows.append(
                {
                    "scenario_id": scenario_id,
                    "route_id": route_id,
                    "target_edge_id": target_edge_id,
                    "start_edge_id": START_EDGE_ID,
                    "route_length_m": round(major_length, 3),
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
                    "length_increase_pct": round(increase_ratio * 100, 3),
                    "route_edge_count": len(major_ids),
                    "route_tls_count": len(tls_ids),
                    "review_status": review_status,
                    "shortest_segments": route_coords(shortest_ids, coords_by_id),
                    "major_segments": route_coords(major_ids, coords_by_id),
                }
            )

        route_fields = ["route_id", "scenario_id", "target_edge_id", "route_edges", "route_length_m", "route_edge_count", "route_tls_count", "review_status", "warnings"]
        compare_fields = ["route_id", "scenario_id", "target_edge_id", "shortest_route_edges", "shortest_length_m", "shortest_edge_count", "major_route_edges", "major_length_m", "major_edge_count", "major_tls_count", "length_increase_pct", "review_status", "warnings"]
        scenario_fields = ["scenario_id", "route_id", "target_edge_id", "start_edge_id", "route_length_m", "review_status"]
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
                "shortest_vs_major_warning_threshold": LENGTH_WARNING_RATIO,
                "needs_manual_review_threshold": MANUAL_REVIEW_RATIO,
                "warning_count": warnings,
                "needs_manual_review_count": manual_reviews,
                "outputs": [rel(path) for path in [EMERGENCY_ROUTES_CSV, EMERGENCY_ROUTES_XML, ROUTE_COMPARE_CSV, ACCIDENT_SCENARIOS_CSV, ROUTE_REVIEW_HTML, ROUTE_REVIEW_SCHEMA, PREFLIGHT_SUMMARY, PREFLIGHT_REPORT]],
            },
        )
        lines.extend(
            [
                f"route_count: {len(route_rows)}",
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
