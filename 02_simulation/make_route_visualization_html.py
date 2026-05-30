#!/usr/bin/env python3
"""Build a standalone Leaflet HTML for the fixed Seoul Station route."""

from __future__ import annotations

import csv
import importlib.util
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py"
NET_PATH = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml"
DEFAULT_RESULTS_PREFIX = "seoul_station_straight_final_smoke"
OUTPUT_HTML = PROJECT_ROOT / "results/html/seoul_station_straight_route.html"


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("b0_b1_b2_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import runner: {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def latest_results_csv(prefix: str = DEFAULT_RESULTS_PREFIX) -> Path | None:
    latest_path = PROJECT_ROOT / "results/metrics" / prefix / "latest.json"
    if not latest_path.is_file():
        return None
    data = json.loads(latest_path.read_text(encoding="utf-8"))
    path = PROJECT_ROOT / str(data.get("results_csv", ""))
    return path if path.is_file() else None


def load_lane_connection_data(net_path: Path) -> tuple[dict[str, int], dict[tuple[str, str], set[int]]]:
    lane_counts: dict[str, int] = {}
    transition_lanes: dict[tuple[str, str], set[int]] = {}
    root = ET.parse(net_path).getroot()
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.get("function") == "internal":
            continue
        lanes = edge.findall("lane")
        if lanes:
            lane_counts[edge_id] = len(lanes)
    for connection in root.findall("connection"):
        from_edge = connection.get("from", "")
        to_edge = connection.get("to", "")
        from_lane = connection.get("fromLane", "")
        if from_edge in lane_counts and to_edge in lane_counts and from_lane.isdigit():
            transition_lanes.setdefault((from_edge, to_edge), set()).add(int(from_lane))
    return lane_counts, transition_lanes


def edge_shape_latlon(sumo_net: Any, edge_id: str) -> list[list[float]]:
    edge = sumo_net.getEdge(edge_id)
    shape = list(edge.getShape())
    if len(shape) < 2 and edge.getLanes():
        shape = list(edge.getLanes()[0].getShape())
    coords = []
    for x, y in shape:
        lon, lat = sumo_net.convertXY2LonLat(float(x), float(y))
        coords.append([lat, lon])
    return coords


def edge_center(sumo_net: Any, edge_id: str) -> list[float] | None:
    coords = edge_shape_latlon(sumo_net, edge_id)
    return coords[len(coords) // 2] if coords else None


def route_length_m(sumo_net: Any, edge_ids: list[str]) -> float:
    return sum(float(sumo_net.getEdge(edge_id).getLength()) for edge_id in edge_ids)


def route_segments(sumo_net: Any, edge_ids: list[str]) -> list[dict[str, Any]]:
    segments = []
    for index, edge_id in enumerate(edge_ids):
        coords = edge_shape_latlon(sumo_net, edge_id)
        if len(coords) >= 2:
            segments.append({"index": index, "edge_id": edge_id, "coords": coords})
    return segments


def lane_loss_transitions(
    sumo_net: Any,
    edge_ids: list[str],
    lane_counts: dict[str, int],
    transition_lanes: dict[tuple[str, str], set[int]],
) -> list[dict[str, Any]]:
    rows = []
    for index, (from_edge, to_edge) in enumerate(zip(edge_ids, edge_ids[1:], strict=False), start=1):
        from_lane_count = lane_counts.get(from_edge, 1)
        connected_count = len(transition_lanes.get((from_edge, to_edge), set()))
        lost_lanes = max(from_lane_count - connected_count, 0)
        point = edge_center(sumo_net, from_edge)
        if lost_lanes > 0 and point is not None:
            rows.append(
                {
                    "index": index,
                    "from_edge": from_edge,
                    "to_edge": to_edge,
                    "from_lanes": from_lane_count,
                    "connected_lanes": connected_count,
                    "lost_lanes": lost_lanes,
                    "point": point,
                }
            )
    return rows


def load_metrics(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None or not path.is_file():
        return {}
    fields = [
        "mode",
        "final_status",
        "emergency_travel_time_sec",
        "emergency_avg_speed_kmh",
        "A_delay_sec",
        "N_delay_sec",
        "rolling_congestion_valid",
        "background_teleported",
        "route_error_count",
        "emergency_stop_warning_count",
        "emergency_lane_connection_warning_count",
    ]
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    metrics = {}
    for row in rows:
        mode = row.get("mode", "")
        if mode:
            metrics[mode] = {field: row.get(field, "") for field in fields}
    return {mode: metrics[mode] for mode in ["B00", "B0", "B2"] if mode in metrics}


def build_payload() -> dict[str, Any]:
    runner = load_runner()
    sumo_net = runner.S14.read_sumo_net(str(NET_PATH))
    selected = runner.synthetic_seoul_station_route(NET_PATH)
    edge_ids = selected["route_edges"].split()
    lane_counts, transition_lanes = load_lane_connection_data(NET_PATH)
    results_csv = latest_results_csv()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "net_path": NET_PATH.relative_to(PROJECT_ROOT).as_posix(),
        "results_csv": results_csv.relative_to(PROJECT_ROOT).as_posix() if results_csv else "",
        "route_id": selected["route_id"],
        "policy": selected["selected_policy"],
        "start_edge": runner.SEOUL_STATION_START_EDGE,
        "target_edge": runner.SEOUL_STATION_TARGET_EDGE,
        "edge_count": len(edge_ids),
        "length_m": round(route_length_m(sumo_net, edge_ids), 2),
        "edge_ids": edge_ids,
        "segments": route_segments(sumo_net, edge_ids),
        "lane_loss_transitions": lane_loss_transitions(sumo_net, edge_ids, lane_counts, transition_lanes),
        "metrics": load_metrics(results_csv),
    }


def render_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Seoul Station Fixed Route</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body {{ height: 100%; margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111827; }}
    .app {{ display: grid; grid-template-columns: 360px 1fr; height: 100vh; background: #f3f4f6; }}
    aside {{ overflow: auto; padding: 16px; border-right: 1px solid #d1d5db; background: #ffffff; }}
    #map {{ height: 100vh; min-height: 420px; background: #e5e7eb; }}
    h1 {{ font-size: 20px; line-height: 1.2; margin: 0 0 8px; font-weight: 700; }}
    h2 {{ font-size: 14px; margin: 18px 0 8px; }}
    p {{ margin: 0 0 12px; }}
    .meta {{ color: #6b7280; font-size: 12px; line-height: 1.45; }}
    .stat-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-top: 10px; }}
    .stat {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px; min-width: 0; }}
    .stat-label {{ display: block; color: #6b7280; font-size: 11px; line-height: 1.2; margin-bottom: 3px; }}
    .stat-value {{ display: block; font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #e5e7eb; padding: 6px 4px; vertical-align: top; }}
    th {{ color: #6b7280; font-weight: 600; }}
    textarea {{ width: 100%; height: 120px; box-sizing: border-box; margin-top: 8px; font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; color: #374151; }}
    .lane-loss-marker {{ border: 2px solid #fff; border-radius: 999px; background: #dc2626; box-shadow: 0 1px 4px rgba(0,0,0,.35); }}
    @media (max-width: 900px) {{
      .app {{ grid-template-columns: 1fr; grid-template-rows: 46vh 54vh; }}
      aside {{ grid-row: 2; border-right: 0; border-top: 1px solid #d1d5db; }}
      #map {{ grid-row: 1; height: 46vh; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h1>서울역 고정 직선 경로</h1>
      <p class="meta">실험에는 이 단일 경로만 사용한다. 빨간 점은 차로 수가 줄어드는 연결부다.</p>
      <div class="stat-grid">
        <div class="stat"><span class="stat-label">Route</span><span class="stat-value" id="route-id"></span></div>
        <div class="stat"><span class="stat-label">Policy</span><span class="stat-value" id="policy"></span></div>
        <div class="stat"><span class="stat-label">Target</span><span class="stat-value" id="target"></span></div>
        <div class="stat"><span class="stat-label">Length</span><span class="stat-value" id="length"></span></div>
        <div class="stat"><span class="stat-label">Edges</span><span class="stat-value" id="edges"></span></div>
        <div class="stat"><span class="stat-label">Lane-loss</span><span class="stat-value" id="lane-loss"></span></div>
      </div>
      <h2>Latest metrics</h2>
      <div id="metrics"></div>
      <h2>Edge IDs</h2>
      <textarea id="edge-list" readonly></textarea>
    </aside>
    <main id="map"></main>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const DATA = {data};
    const map = L.map('map', {{ preferCanvas: true }});
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);

    function fmt(value, digits = 1) {{
      const number = Number(value);
      return Number.isFinite(number) ? number.toLocaleString(undefined, {{ maximumFractionDigits: digits }}) : (value || '');
    }}

    const bounds = L.latLngBounds([]);
    DATA.segments.forEach((segment) => {{
      const line = L.polyline(segment.coords, {{
        color: '#2563eb',
        weight: 6,
        opacity: 0.9,
        lineJoin: 'round',
        lineCap: 'round'
      }}).bindTooltip(`#${{segment.index}} ${{segment.edge_id}}`, {{ sticky: true }}).addTo(map);
      bounds.extend(line.getBounds());
    }});
    if (DATA.segments.length) {{
      const start = DATA.segments[0].coords[0];
      const last = DATA.segments[DATA.segments.length - 1].coords;
      const end = last[last.length - 1];
      L.circleMarker(start, {{ radius: 7, color: '#047857', fillColor: '#10b981', fillOpacity: 1, weight: 2 }}).bindPopup(`Start<br><code>${{DATA.start_edge}}</code>`).addTo(map);
      L.circleMarker(end, {{ radius: 7, color: '#7c2d12', fillColor: '#f97316', fillOpacity: 1, weight: 2 }}).bindPopup(`Target<br><code>${{DATA.target_edge}}</code>`).addTo(map);
    }}
    DATA.lane_loss_transitions.forEach((item) => {{
      L.marker(item.point, {{
        icon: L.divIcon({{ className: 'lane-loss-marker', iconSize: [13, 13] }})
      }}).bindPopup(
        `<strong>Lane-loss transition</strong><br>` +
        `#${{item.index}} <code>${{item.from_edge}}</code> -> <code>${{item.to_edge}}</code><br>` +
        `${{item.connected_lanes}} of ${{item.from_lanes}} lanes connect`
      ).addTo(map);
    }});
    if (bounds.isValid()) map.fitBounds(bounds, {{ padding: [24, 24] }});
    else map.setView([37.556, 126.98], 14);

    document.getElementById('route-id').textContent = DATA.route_id;
    document.getElementById('policy').textContent = DATA.policy;
    document.getElementById('target').textContent = DATA.target_edge;
    document.getElementById('length').textContent = `${{fmt(DATA.length_m, 0)}} m`;
    document.getElementById('edges').textContent = DATA.edge_count;
    document.getElementById('lane-loss').textContent = DATA.lane_loss_transitions.length;
    document.getElementById('edge-list').value = DATA.edge_ids.join(' ');

    const metrics = document.getElementById('metrics');
    const modes = Object.keys(DATA.metrics || {{}});
    if (!modes.length) {{
      metrics.innerHTML = '<p class="meta">No latest metrics found for this route.</p>';
    }} else {{
      metrics.innerHTML = `
        <table>
          <thead><tr><th>Mode</th><th>Status</th><th>Travel</th><th>Avg</th><th>Warnings</th></tr></thead>
          <tbody>
            ${{modes.map((mode) => {{
              const row = DATA.metrics[mode];
              const warnings = [
                `teleport ${{row.background_teleported || 0}}`,
                `route ${{row.route_error_count || 0}}`,
                `stop ${{row.emergency_stop_warning_count || 0}}`,
                `lane ${{row.emergency_lane_connection_warning_count || 0}}`
              ].join('<br>');
              return `<tr><td>${{mode}}</td><td>${{row.final_status}}</td><td>${{fmt(row.emergency_travel_time_sec, 0)}} s</td><td>${{fmt(row.emergency_avg_speed_kmh)}} km/h</td><td>${{warnings}}</td></tr>`;
            }}).join('')}}
          </tbody>
        </table>
      `;
    }}
  </script>
</body>
</html>
"""


def main() -> None:
    payload = build_payload()
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"wrote {OUTPUT_HTML}")
    print(
        "fixed route: "
        f"{payload['edge_count']} edges, {payload['length_m']:.0f} m, "
        f"{len(payload['lane_loss_transitions'])} lane-loss transitions, "
        f"target {payload['target_edge']}"
    )


if __name__ == "__main__":
    main()
