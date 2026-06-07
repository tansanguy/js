#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import sumolib


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = PROJECT_ROOT / "data_prepared/compact_v9"
TDATA_ROOT = PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_signal"
NET_FILE = TDATA_ROOT / "nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
GLOBAL_MAPPING_CSV = TDATA_ROOT / "global_tls_a008_itst_mapping.csv"
GLOBAL_PROFILES_CSV = TDATA_ROOT / "global_reality_signal_profiles.csv"
GLOBAL_APPLIED_CSV = TDATA_ROOT / "global_reality_applied_signal_profiles.csv"
CSV_SIGNAL_CANDIDATES_CSV = DATA_ROOT / "net/B04_csv_signal_candidates.csv"
MAINROAD_MAPPING_CSV = DATA_ROOT / "map/B04_toegye_segment_edge_mapping.csv"
OUTPUT_HTML = PROJECT_ROOT / "results/html/compact_v9_mainroad_signal_positions.html"
LEGACY_OUTPUT_HTML = PROJECT_ROOT / "results/html/compact_v9_signal_positions_s1forced.html"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def safe_float(value: str | None, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except ValueError:
        return default


def read_net_timing_by_tls(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    timings: dict[str, dict[str, Any]] = {}
    for event, elem in ET.iterparse(path.open("rb"), events=("end",)):
        if elem.tag != "tlLogic":
            continue
        tls_id = elem.attrib.get("id", "")
        phases = elem.findall("phase")
        durations = [safe_float(phase.attrib.get("duration"), 0.0) for phase in phases]
        names = [phase.attrib.get("name", "") for phase in phases]
        if not tls_id or not durations:
            elem.clear()
            continue
        cycle = sum(durations)
        if any(name.startswith("mainroad_green") for name in names):
            main_green = sum(duration for duration, name in zip(durations, names, strict=True) if name.startswith("mainroad_green"))
            yellow_values = [duration for duration, name in zip(durations, names, strict=True) if "yellow" in name]
            yellow = max(yellow_values) if yellow_values else 0.0
            red = sum(duration for duration, name in zip(durations, names, strict=True) if name == "side_green")
        else:
            main_green = durations[0] if len(durations) >= 1 else 0.0
            yellow = durations[1] if len(durations) >= 2 else 0.0
            red = max(0.0, cycle - main_green - yellow)
        timings[tls_id] = {
            "cycle_sec": cycle,
            "green_sec": main_green,
            "yellow_sec": yellow,
            "red_sec": red,
            "phase_count": len(durations),
            "phase_durations": durations,
            "phase_names": names,
        }
        elem.clear()
    return timings


def mainroad_edges() -> list[dict[str, str]]:
    rows = read_csv(MAINROAD_MAPPING_CSV)
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for row in rows:
        edge_id = row.get("edge_id", "")
        if not edge_id or edge_id in seen:
            continue
        seen.add(edge_id)
        result.append({
            "edge_id": edge_id,
            "segment_id": row.get("segment_id", ""),
            "direction": row.get("direction", ""),
            "edge_order": row.get("edge_order", ""),
        })
    return result


def mainroad_geometries(edge_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not NET_FILE.is_file():
        return []
    net = sumolib.net.readNet(str(NET_FILE))
    roads: list[dict[str, Any]] = []
    for row in edge_rows:
        edge_id = row["edge_id"]
        try:
            edge = net.getEdge(edge_id)
        except Exception:
            continue
        coords = []
        for x, y in edge.getShape():
            lon, lat = net.convertXY2LonLat(float(x), float(y))
            coords.append([round(float(lat), 8), round(float(lon), 8)])
        if not coords:
            continue
        roads.append({
            **row,
            "coords": coords,
            "lane_count": len(edge.getLanes()),
            "length_m": round(float(edge.getLength()), 2),
        })
    return roads


def signal_rows() -> list[dict[str, Any]]:
    candidates = read_csv(CSV_SIGNAL_CANDIDATES_CSV)
    applied = read_csv(GLOBAL_APPLIED_CSV)
    profiles = {row.get("tls_id", ""): row for row in (applied or read_csv(GLOBAL_PROFILES_CSV))}
    mapping = {row.get("tls_id", ""): row for row in read_csv(GLOBAL_MAPPING_CSV)}
    net_timings = read_net_timing_by_tls(NET_FILE)
    signals: list[dict[str, Any]] = []
    for row in candidates:
        tls_id = row.get("tls_id", "")
        lat = safe_float(row.get("csv_lat"))
        lon = safe_float(row.get("csv_lon"))
        if not tls_id or not lat or not lon:
            continue
        profile = profiles.get(tls_id, {})
        map_row = mapping.get(tls_id, {})
        net_timing = net_timings.get(tls_id, {})
        if net_timing:
            cycle = safe_float(str(net_timing.get("cycle_sec")), 0.0)
            main_green = safe_float(str(net_timing.get("green_sec")), 0.0)
            yellow = safe_float(str(net_timing.get("yellow_sec")), 0.0)
            red = safe_float(str(net_timing.get("red_sec")), 0.0)
            phase_durations = [round(float(v), 3) for v in net_timing.get("phase_durations", [])]
            phase_display = "/".join(str(int(v)) for v in phase_durations) if phase_durations else ""
            timing_source = "net_tlLogic_latest_policy"
        else:
            cycle = safe_float(profile.get("cycle_sec"))
            main_green = safe_float(profile.get("main_green_sec"))
            yellow = safe_float(profile.get("yellow_sec"))
            red = safe_float(profile.get("side_green_sec")) or max(0.0, cycle - main_green - yellow)
            phase_durations = [round(main_green, 3), round(yellow, 3), round(red, 3), round(yellow, 3)] if cycle else []
            phase_display = f"{int(main_green)}/{int(yellow)}/{int(red)}/{int(yellow)}" if cycle else ""
            timing_source = profile.get("source", "") or "profile_fallback"
        source = profile.get("source", "")
        if "TData_SPAT" in source:
            timing_kind = "direct API SPaT"
        elif "route_family_fallback" in source:
            timing_kind = "API average route-family fallback"
        elif "virtual" in source:
            timing_kind = "virtual control preserved"
        elif source:
            timing_kind = "profile-derived timing"
        else:
            timing_kind = "unmapped profile fallback"
        signal_kind = "intersection signal" if row.get("signal_type", "") == "intersection_signal" else "straight signal"
        signals.append({
            "tls_id": tls_id,
            "boundary_id": row.get("boundary_id", ""),
            "name": row.get("intersection_name", ""),
            "signal_type": row.get("signal_type", ""),
            "signal_kind": signal_kind,
            "timing_kind": timing_kind,
            "lat": lat,
            "lon": lon,
            "segment": row.get("csv_segments", ""),
            "cycle_sec": round(cycle, 3) if cycle else "",
            "green_sec": round(main_green, 3) if main_green else "",
            "yellow_sec": round(yellow, 3) if yellow else "",
            "red_sec": round(red, 3) if cycle else "",
            "phase_display": phase_display,
            "phase_durations": phase_durations,
            "phase_names": net_timing.get("phase_names", []),
            "source": profile.get("source", ""),
            "status": profile.get("status", ""),
            "timing_source": timing_source,
            "phase_count": net_timing.get("phase_count", ""),
            "timing_hit": map_row.get("timing_hit", ""),
            "coord_source": map_row.get("coord_source", ""),
            "a008": f"{map_row.get('itst_id', '')} {map_row.get('a008_name', '')}".strip(),
        })
    signals.sort(key=lambda item: (safe_float(item["lat"]), safe_float(item["lon"])), reverse=True)
    return signals


def build_payload() -> dict[str, Any]:
    edges = mainroad_edges()
    roads = mainroad_geometries(edges)
    signals = signal_rows()
    return {
        "schema": "compact_v9_mainroad_signal_positions.v1",
        "sources": {
            "net_file": str(NET_FILE.relative_to(PROJECT_ROOT)),
            "mainroad_mapping_csv": str(MAINROAD_MAPPING_CSV.relative_to(PROJECT_ROOT)),
            "csv_signal_candidates_csv": str(CSV_SIGNAL_CANDIDATES_CSV.relative_to(PROJECT_ROOT)),
            "global_profiles_csv": str(GLOBAL_PROFILES_CSV.relative_to(PROJECT_ROOT)),
            "global_applied_csv": str(GLOBAL_APPLIED_CSV.relative_to(PROJECT_ROOT)),
        },
        "summary": {
            "mainroad_edge_count": len(roads),
            "signal_count": len(signals),
            "signal_type_counts": dict(Counter(row["signal_type"] for row in signals)),
            "timing_source_counts": dict(Counter(row["timing_source"] for row in signals)),
        },
        "roads": roads,
        "signals": signals,
    }


def render_html(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    source_rows = "".join(
        f"<tr><td>{html.escape(key)}</td><td><code>{html.escape(str(value))}</code></td></tr>"
        for key, value in payload["sources"].items()
    )
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Compact V9 Mainroad Signals</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body {{ height: 100%; margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2933; }}
    .app {{ height: 100%; display: grid; grid-template-rows: auto 1fr; background: #f5f7fa; }}
    header {{ padding: 12px 16px; border-bottom: 1px solid #d6dde8; background: #fff; }}
    h1 {{ margin: 0; font-size: 18px; line-height: 1.25; letter-spacing: 0; }}
    .muted {{ color: #667085; font-size: 13px; margin-top: 4px; }}
    .workspace {{ min-height: 0; display: grid; grid-template-columns: 1fr 360px; gap: 0; }}
    .panel {{ min-height: 0; border-right: 1px solid #d6dde8; background: #fff; position: relative; overflow: hidden; }}
    .panel-title {{ position: absolute; left: 10px; top: 10px; z-index: 10; padding: 6px 8px; background: rgba(255,255,255,.92); border: 1px solid #d6dde8; border-radius: 6px; font-size: 12px; font-weight: 700; }}
    #map {{ width: 100%; height: 100%; }}
    aside {{ overflow: auto; padding: 14px; background: #fff; }}
    .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 8px 0 12px; }}
    .stat {{ border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px; background: #f8fafc; }}
    .stat strong {{ display: block; font-size: 18px; }}
    .stat span {{ font-size: 12px; color: #64748b; }}
    .legend {{ display: grid; gap: 7px; font-size: 12px; margin: 12px 0; }}
    .legend span {{ display: flex; align-items: center; gap: 7px; }}
    .sym {{ width: 14px; height: 14px; background: #e60012; border: 2px solid #fff; box-shadow: 0 0 0 1px #a20000; display: inline-block; }}
    .circle {{ border-radius: 50%; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ padding: 6px 5px; border-bottom: 1px solid #edf2f7; vertical-align: top; text-align: left; }}
    th {{ position: sticky; top: 0; background: #fff; z-index: 1; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; word-break: break-all; }}
    .leaflet-tooltip.sig-tip {{ border: 1px solid #cbd5e1; border-radius: 6px; box-shadow: 0 8px 18px rgba(15,23,42,.16); }}
    @media (max-width: 980px) {{ .workspace {{ grid-template-columns: 1fr; grid-template-rows: 58vh 42vh; }} .panel {{ border-right: 0; border-bottom: 1px solid #d6dde8; }} }}
  </style>
</head>
<body>
<div class="app">
  <header>
    <h1>Compact V9 Mainroad Signal Visualization</h1>
    <div class="muted">Leaflet 지도 배경 위에 main도로 edge와 CSV 현실좌표 신호만 표시합니다.</div>
  </header>
  <main class="workspace">
    <section class="panel">
      <div class="panel-title">Leaflet map + mainroad only</div>
      <div id="map"></div>
    </section>
    <aside>
      <h1>Mainroad Signals</h1>
      <div class="muted">교차로/직진 신호를 혼동하지 않도록 표식 모양을 분리했습니다.</div>
      <div class="stats" id="stats"></div>
      <div class="legend">
        <span><i class="sym"></i>intersection signal</span>
        <span><i class="sym circle"></i>straight signal</span>
        <span><i style="width:30px;height:4px;background:#1396d7;display:inline-block"></i>mainroad upbound</span>
        <span><i style="width:30px;height:4px;background:#1f78ff;display:inline-block"></i>mainroad downbound</span>
        <span><i style="width:30px;height:4px;background:#7c3aed;display:inline-block"></i>raw tlLogic phases from latest net</span>
      </div>
      <table><thead><tr><th>Signal</th><th>Type</th><th>Timing</th><th>Phases</th></tr></thead><tbody id="rows"></tbody></table>
      <h1 style="margin-top:18px">Sources</h1>
      <table><tbody>{source_rows}</tbody></table>
    </aside>
  </main>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const payload = {payload_json};
const map = L.map('map', {{ preferCanvas: true }});
const osm = L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors'
}});
const carto = L.tileLayer('https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap & CARTO'
}});
carto.addTo(map);
L.control.layers({{ 'CARTO Voyager': carto, 'OpenStreetMap': osm }}).addTo(map);

const bounds = [];
const roadLayer = L.layerGroup().addTo(map);
for (const road of payload.roads) {{
  const color = road.direction === 'upbound' ? '#1396d7' : '#1f78ff';
  L.polyline(road.coords, {{
    color,
    weight: Math.max(4, Math.min(8, Number(road.lane_count || 2) + 3)),
    opacity: 0.88,
    lineCap: 'round',
    lineJoin: 'round'
  }}).addTo(roadLayer).bindTooltip(`${{road.segment_id}} ${{road.direction}}<br><code>${{road.edge_id}}</code>`, {{sticky: true}});
  for (const p of road.coords) bounds.push(p);
}}

function markerHtml(signal) {{
  const shape = signal.signal_type === 'straight_signal' ? 'border-radius:50%;' : '';
  return `<div style="width:18px;height:18px;background:#e60012;border:2px solid #fff;box-shadow:0 0 0 1px #a20000;${{shape}}"></div>`;
}}
function sourceShort(source) {{
  return String(source || '').replace('A008_location_matched_', 'loc_').replace('nearest_TData_timing_fallback', 'nearest_fallback');
}}
function timingText(s) {{
  return `cycle ${{s.cycle_sec}}s; main G ${{s.green_sec}}s; Y ${{s.yellow_sec}}s; side/other ${{s.red_sec}}s`;
}}
for (const s of payload.signals) {{
  const icon = L.divIcon({{ html: markerHtml(s), className: '', iconSize: [22, 22], iconAnchor: [11, 11] }});
  L.marker([s.lat, s.lon], {{ icon }}).addTo(map).bindTooltip(
    `<strong>${{s.boundary_id}} ${{s.name}}</strong><br>${{s.signal_kind}}<br>${{s.timing_kind}}<br>${{timingText(s)}}<br>raw phases ${{s.phase_display}}<br>status: ${{s.status || ''}}<br>profile: ${{sourceShort(s.source)}}<br><code>${{s.tls_id}}</code>`,
    {{ sticky: true, className: 'sig-tip' }}
  );
  bounds.push([s.lat, s.lon]);
}}
if (bounds.length) map.fitBounds(bounds, {{ padding: [24, 24] }}); else map.setView([37.561, 126.995], 14);

const stats = [
  ['main edges', payload.summary.mainroad_edge_count],
  ['signals', payload.summary.signal_count],
  ['straight', payload.summary.signal_type_counts.straight_signal || 0],
  ['intersection', payload.summary.signal_type_counts.intersection_signal || 0]
];
document.getElementById('stats').innerHTML = stats.map(([k, v]) => `<div class="stat"><strong>${{v}}</strong><span>${{k}}</span></div>`).join('');
document.getElementById('rows').innerHTML = payload.signals.map(s => `<tr><td>${{s.boundary_id}}<br><code>${{s.name}}</code></td><td>${{s.signal_kind}}<br>${{s.a008}}</td><td>${{s.timing_kind}}<br>${{timingText(s)}}<br>${{sourceShort(s.source)}}</td><td>${{s.phase_display}}<br>${{s.status || ''}}</td></tr>`).join('');
</script>
</body>
</html>
"""


def main() -> None:
    payload = build_payload()
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_html(payload)
    OUTPUT_HTML.write_text(html_text, encoding="utf-8")
    LEGACY_OUTPUT_HTML.write_text(html_text, encoding="utf-8")
    print(json.dumps({
        "output_html": str(OUTPUT_HTML),
        "legacy_output_html": str(LEGACY_OUTPUT_HTML),
        "summary": payload["summary"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
