#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import math
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
FIRETRUCK_ROUTE_XML = DATA_ROOT / "routes/firetruck_to_seoul_station_front.rou.xml"
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


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return math.hypot((lat1 - lat2) * 111000.0, (lon1 - lon2) * 88000.0)


def net_connection_diagnostics() -> dict[str, dict[str, Any]]:
    if not NET_FILE.is_file():
        return {}
    net = sumolib.net.readNet(str(NET_FILE))
    root = ET.parse(NET_FILE).getroot()
    by_tls: dict[str, list[dict[str, str]]] = {}
    for connection in root.findall("connection"):
        tls_id = connection.get("tl", "")
        if tls_id:
            by_tls.setdefault(tls_id, []).append(dict(connection.attrib))

    def edge_endpoint(edge_id: str, end: bool) -> tuple[float, float] | None:
        try:
            edge = net.getEdge(edge_id)
            shape = edge.getShape()
        except Exception:
            return None
        if not shape:
            return None
        x, y = shape[-1] if end else shape[0]
        lon, lat = net.convertXY2LonLat(float(x), float(y))
        return float(lat), float(lon)

    diagnostics: dict[str, dict[str, Any]] = {}
    for tls_id, connections in by_tls.items():
        points: list[tuple[float, float]] = []
        for connection in connections:
            from_point = edge_endpoint(connection.get("from", ""), True)
            to_point = edge_endpoint(connection.get("to", ""), False)
            if from_point:
                points.append(from_point)
            if to_point:
                points.append(to_point)
        if not points:
            continue
        diagnostics[tls_id] = {
            "control_lat": sum(point[0] for point in points) / len(points),
            "control_lon": sum(point[1] for point in points) / len(points),
            "controlled_connection_count": len(connections),
            "controlled_from_edges": sorted({connection.get("from", "") for connection in connections if connection.get("from")}),
            "controlled_to_edges": sorted({connection.get("to", "") for connection in connections if connection.get("to")}),
        }
    return diagnostics


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


def route_edges() -> list[str]:
    if not FIRETRUCK_ROUTE_XML.is_file():
        return []
    route = ET.parse(FIRETRUCK_ROUTE_XML).getroot().find(".//route")
    return str(route.get("edges") if route is not None else "").split()


def ev_route_geometry() -> dict[str, Any]:
    edges = route_edges()
    if not NET_FILE.is_file() or not edges:
        return {"edge_count": len(edges), "coords": [], "length_m": 0.0, "shape_length_m": 0.0}
    net = sumolib.net.readNet(str(NET_FILE))
    coords: list[list[float]] = []
    length_m = 0.0
    shape_length_m = 0.0
    for edge_id in edges:
        try:
            edge = net.getEdge(edge_id)
        except Exception:
            continue
        length_m += float(edge.getLength())
        shape_xy = [(float(x), float(y)) for x, y in edge.getShape()]
        shape_length_m += sum(
            math.hypot(x2 - x1, y2 - y1)
            for (x1, y1), (x2, y2) in zip(shape_xy, shape_xy[1:])
        )
        points: list[list[float]] = []
        for x, y in shape_xy:
            lon, lat = net.convertXY2LonLat(float(x), float(y))
            points.append([round(float(lat), 8), round(float(lon), 8)])
        if not points:
            continue
        if coords and coords[-1] == points[0]:
            coords.extend(points[1:])
        else:
            coords.extend(points)
    return {
        "edge_count": len(edges),
        "start_edge": edges[0] if edges else "",
        "target_edge": edges[-1] if edges else "",
        "coords": coords,
        "length_m": round(length_m, 2),
        "shape_length_m": round(shape_length_m, 2),
    }


def signal_rows() -> list[dict[str, Any]]:
    candidates = read_csv(CSV_SIGNAL_CANDIDATES_CSV)
    applied = read_csv(GLOBAL_APPLIED_CSV)
    profiles = {row.get("tls_id", ""): row for row in (applied or read_csv(GLOBAL_PROFILES_CSV))}
    mapping = {row.get("tls_id", ""): row for row in read_csv(GLOBAL_MAPPING_CSV)}
    net_timings = read_net_timing_by_tls(NET_FILE)
    diagnostics = net_connection_diagnostics()
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
        diag = diagnostics.get(tls_id, {})
        control_lat = safe_float(str(diag.get("control_lat", "")))
        control_lon = safe_float(str(diag.get("control_lon", "")))
        csv_to_control = distance_m(lat, lon, control_lat, control_lon) if control_lat and control_lon else 0.0
        route_from = row.get("route_from_edge", "")
        route_to = row.get("route_to_edge", "")
        controlled_from_edges = set(diag.get("controlled_from_edges", []))
        controlled_to_edges = set(diag.get("controlled_to_edges", []))
        route_controlled = route_from in controlled_from_edges and route_to in controlled_to_edges
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
            "control_lat": round(control_lat, 8) if control_lat else "",
            "control_lon": round(control_lon, 8) if control_lon else "",
            "csv_to_control_m": round(csv_to_control, 1) if control_lat and control_lon else "",
            "controlled_connection_count": diag.get("controlled_connection_count", ""),
            "route_controlled": route_controlled,
            "route_from_edge": route_from,
            "route_to_edge": route_to,
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
    ev_route = ev_route_geometry()
    return {
        "schema": "compact_v9_mainroad_signal_positions.v2",
        "sources": {
            "net_file": str(NET_FILE.relative_to(PROJECT_ROOT)),
            "firetruck_route_xml": str(FIRETRUCK_ROUTE_XML.relative_to(PROJECT_ROOT)),
            "mainroad_mapping_csv": str(MAINROAD_MAPPING_CSV.relative_to(PROJECT_ROOT)),
            "csv_signal_candidates_csv": str(CSV_SIGNAL_CANDIDATES_CSV.relative_to(PROJECT_ROOT)),
            "global_profiles_csv": str(GLOBAL_PROFILES_CSV.relative_to(PROJECT_ROOT)),
            "global_applied_csv": str(GLOBAL_APPLIED_CSV.relative_to(PROJECT_ROOT)),
        },
        "summary": {
            "mainroad_edge_count": len(roads),
            "ev_route_edge_count": ev_route["edge_count"],
            "ev_route_length_m": ev_route["length_m"],
            "ev_route_shape_length_m": ev_route["shape_length_m"],
            "signal_count": len(signals),
            "signal_type_counts": dict(Counter(row["signal_type"] for row in signals)),
            "timing_source_counts": dict(Counter(row["timing_source"] for row in signals)),
        },
        "roads": roads,
        "ev_route": ev_route,
        "signals": signals,
    }


def svg_point_projector(payload: dict[str, Any], width: int = 1200, height: int = 820, pad: int = 42) -> tuple[Any, Any]:
    points: list[tuple[float, float]] = []
    for road in payload["roads"]:
        for lat, lon in road["coords"]:
            points.append((float(lat), float(lon)))
    for lat, lon in payload.get("ev_route", {}).get("coords", []):
        points.append((float(lat), float(lon)))
    for signal in payload["signals"]:
        points.append((float(signal["lat"]), float(signal["lon"])))
        if signal.get("control_lat") and signal.get("control_lon"):
            points.append((float(signal["control_lat"]), float(signal["control_lon"])))
    min_lat = min(point[0] for point in points)
    max_lat = max(point[0] for point in points)
    min_lon = min(point[1] for point in points)
    max_lon = max(point[1] for point in points)

    def x(lon: float) -> float:
        return pad + (lon - min_lon) / (max_lon - min_lon or 1.0) * (width - pad * 2)

    def y(lat: float) -> float:
        return height - pad - (lat - min_lat) / (max_lat - min_lat or 1.0) * (height - pad * 2)

    return x, y


def static_svg_map(payload: dict[str, Any]) -> str:
    width = 1200
    height = 820
    x, y = svg_point_projector(payload, width=width, height=height)
    roads: list[str] = []
    for road in payload["roads"]:
        color = "#1396d7" if road.get("direction") == "upbound" else "#1f78ff"
        parts = [
            f"{'M' if index == 0 else 'L'}{x(float(lon)):.1f} {y(float(lat)):.1f}"
            for index, (lat, lon) in enumerate(road["coords"])
        ]
        title = html.escape(f"{road.get('segment_id', '')} {road.get('direction', '')} {road.get('edge_id', '')}")
        roads.append(
            f'<path d="{" ".join(parts)}" fill="none" stroke="{color}" '
            f'stroke-width="{max(3, min(7, int(road.get("lane_count", 2)) + 2))}" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="0.82"><title>{title}</title></path>'
        )
    signals: list[str] = []
    for signal in payload["signals"]:
        csv_x = x(float(signal["lon"]))
        csv_y = y(float(signal["lat"]))
        control_x = x(float(signal.get("control_lon") or signal["lon"]))
        control_y = y(float(signal.get("control_lat") or signal["lat"]))
        label = html.escape(
            f"{signal['boundary_id']} {signal['name']}\n"
            f"{signal['signal_kind']}\n"
            f"CSV->SUMO control {signal['csv_to_control_m']}m\n"
            f"route controlled: {signal['route_controlled']}\n"
            f"cycle {signal['cycle_sec']}s; main G {signal['green_sec']}s; "
            f"Y {signal['yellow_sec']}s; side/other {signal['red_sec']}s\n"
            f"raw phases {signal['phase_display']}"
        )
        if signal["signal_type"] == "straight_signal":
            csv_shape = (
                f'<circle cx="{csv_x:.1f}" cy="{csv_y:.1f}" r="7" fill="#e60012" '
                f'stroke="#fff" stroke-width="2"><title>{label}</title></circle>'
            )
        else:
            csv_shape = (
                f'<rect x="{csv_x - 7:.1f}" y="{csv_y - 7:.1f}" width="14" height="14" '
                f'fill="#e60012" stroke="#fff" stroke-width="2"><title>{label}</title></rect>'
            )
        boundary = html.escape(str(signal["boundary_id"]))
        signals.append(
            f'<line x1="{csv_x:.1f}" y1="{csv_y:.1f}" x2="{control_x:.1f}" y2="{control_y:.1f}" '
            f'stroke="#ef4444" stroke-width="1.3" stroke-dasharray="5 5" opacity="0.85" />'
            f'<rect x="{control_x - 5:.1f}" y="{control_y - 5:.1f}" width="10" height="10" '
            f'fill="#111827" stroke="#fff" stroke-width="2" '
            f'transform="rotate(45 {control_x:.1f} {control_y:.1f})"><title>SUMO control centroid\n{label}</title></rect>'
            f'{csv_shape}'
            f'<text x="{csv_x + 9:.1f}" y="{csv_y - 9:.1f}" font-size="10" fill="#111827" '
            f'stroke="#fff" stroke-width="3" paint-order="stroke">{boundary}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="100%" style="background:#eef2f7">'
        f'<rect width="100%" height="100%" fill="#eef2f7"/>'
        f'<text x="16" y="24" font-size="14" font-weight="700" fill="#111827">'
        f'Static map: roads, CSV signal positions, and SUMO control centroids</text>'
        f'{"".join(roads)}{"".join(signals)}</svg>'
    )


def render_html(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    stats_html = "".join(
        f'<div class="stat"><strong>{value}</strong><span>{html.escape(label)}</span></div>'
        for label, value in [
            ("main edges", payload["summary"]["mainroad_edge_count"]),
            ("EV edges", payload["summary"]["ev_route_edge_count"]),
            ("signals", payload["summary"]["signal_count"]),
            ("intersection", payload["summary"]["signal_type_counts"].get("intersection_signal", 0)),
        ]
    )
    signal_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(signal['boundary_id']))}<br><code>{html.escape(str(signal['name']))}</code></td>"
        f"<td>{html.escape(str(signal['signal_kind']))}<br>{html.escape(str(signal['a008']))}</td>"
        f"<td>CSV->control {html.escape(str(signal['csv_to_control_m']))}m<br>"
        f"route controlled: {html.escape(str(signal['route_controlled']))}<br>"
        f"connections: {html.escape(str(signal['controlled_connection_count']))}</td>"
        f"<td>{html.escape(str(signal['timing_kind']))}<br>"
        f"cycle {html.escape(str(signal['cycle_sec']))}s; main G {html.escape(str(signal['green_sec']))}s; "
        f"Y {html.escape(str(signal['yellow_sec']))}s; side/other {html.escape(str(signal['red_sec']))}s<br>"
        f"{html.escape(str(signal['source']))}</td>"
        f"<td>{html.escape(str(signal['phase_display']))}<br>{html.escape(str(signal['status']))}</td>"
        "</tr>"
        for signal in payload["signals"]
    )
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
    #tileWarning {{ position: absolute; left: 10px; bottom: 10px; z-index: 1000; display: none; padding: 7px 9px; background: rgba(127,29,29,.94); color: #fff; border-radius: 6px; font-size: 12px; }}
    aside {{ overflow: auto; padding: 14px; background: #fff; }}
    .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 8px 0 12px; }}
    .stat {{ border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px; background: #f8fafc; }}
    .stat strong {{ display: block; font-size: 18px; }}
    .stat span {{ font-size: 12px; color: #64748b; }}
    .legend {{ display: grid; gap: 7px; font-size: 12px; margin: 12px 0; }}
    .legend span {{ display: flex; align-items: center; gap: 7px; }}
    .sym {{ width: 14px; height: 14px; background: #e60012; border: 2px solid #fff; box-shadow: 0 0 0 1px #a20000; display: inline-block; }}
    .control-sym {{ width: 14px; height: 14px; background: #111827; border: 2px solid #fff; box-shadow: 0 0 0 1px #111827; display: inline-block; transform: rotate(45deg); }}
    .circle {{ border-radius: 50%; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ padding: 6px 5px; border-bottom: 1px solid #edf2f7; vertical-align: top; text-align: left; }}
    th {{ position: sticky; top: 0; background: #fff; z-index: 1; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; word-break: break-all; }}
    .leaflet-pane.leaflet-tile-pane {{ z-index: 200; }}
    .leaflet-pane.leaflet-overlay-pane {{ z-index: 400; }}
    .leaflet-pane.leaflet-route-pane {{ z-index: 640; }}
    .leaflet-pane.leaflet-road-pane {{ z-index: 650; }}
    .leaflet-pane.leaflet-controlLink-pane {{ z-index: 670; }}
    .leaflet-pane.leaflet-signal-pane {{ z-index: 700; }}
    .leaflet-pane.leaflet-label-pane {{ z-index: 720; pointer-events: none; }}
    .leaflet-tooltip.sig-tip {{ border: 1px solid #cbd5e1; border-radius: 6px; box-shadow: 0 8px 18px rgba(15,23,42,.16); }}
    .signal-marker {{ width: 18px; height: 18px; background: #e60012; border: 2px solid #fff; box-shadow: 0 0 0 1px #a20000, 0 3px 8px rgba(15,23,42,.35); }}
    .signal-marker.straight {{ border-radius: 50%; }}
    .control-marker {{ width: 14px; height: 14px; background: #111827; border: 2px solid #fff; box-shadow: 0 0 0 1px #111827, 0 3px 8px rgba(15,23,42,.35); transform: rotate(45deg); }}
    .signal-label {{ color: #111827; font-size: 11px; font-weight: 800; text-shadow: -1px -1px 0 #fff, 1px -1px 0 #fff, -1px 1px 0 #fff, 1px 1px 0 #fff; white-space: nowrap; }}
    @media (max-width: 980px) {{ .workspace {{ grid-template-columns: 1fr; grid-template-rows: 58vh 42vh; }} .panel {{ border-right: 0; border-bottom: 1px solid #d6dde8; }} }}
  </style>
</head>
<body>
<div class="app">
  <header>
    <h1>Compact V9 Mainroad Signal Visualization</h1>
    <div class="muted">지도 타일 위에 main도로 edge, CSV 현실좌표 신호, SUMO 제어 중심을 겹쳐 표시합니다.</div>
  </header>
  <main class="workspace">
    <section class="panel">
      <div class="panel-title">Leaflet map + signal position audit</div>
      <div id="map"></div>
      <div id="tileWarning">지도 타일 로딩 실패: 네트워크 또는 CDN 접근을 확인하세요.</div>
    </section>
    <aside>
      <h1>Mainroad Signals</h1>
      <div class="muted">교차로/직진 신호를 혼동하지 않도록 표식 모양을 분리했습니다.</div>
      <div class="stats" id="stats">{stats_html}</div>
      <div class="legend">
        <span><i class="sym"></i>intersection signal</span>
        <span><i class="sym circle"></i>straight signal</span>
        <span><i class="control-sym"></i>SUMO controlled connection centroid</span>
        <span><i style="width:30px;height:4px;background:#111827;display:inline-block"></i>EV route from corrected SUMO net</span>
        <span><i style="width:30px;height:4px;background:#1396d7;display:inline-block"></i>mainroad upbound</span>
        <span><i style="width:30px;height:4px;background:#1f78ff;display:inline-block"></i>mainroad downbound</span>
        <span><i style="width:30px;height:1px;background:#ef4444;display:inline-block"></i>CSV position to SUMO control centroid</span>
        <span><i style="width:30px;height:4px;background:#111827;display:inline-block"></i>raw tlLogic phases listed in tooltips/table</span>
      </div>
      <table><thead><tr><th>Signal</th><th>Type</th><th>Position</th><th>Timing</th><th>Phases</th></tr></thead><tbody id="rows">{signal_rows}</tbody></table>
      <h1 style="margin-top:18px">Sources</h1>
      <table><tbody>{source_rows}</tbody></table>
    </aside>
  </main>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const payload = {payload_json};
const map = L.map('map', {{ preferCanvas: false, zoomControl: true }});
map.createPane('routePane');
map.createPane('roadPane');
map.createPane('controlLinkPane');
map.createPane('signalPane');
map.createPane('labelPane');
map.getPane('routePane').style.zIndex = 640;
map.getPane('roadPane').style.zIndex = 650;
map.getPane('controlLinkPane').style.zIndex = 670;
map.getPane('signalPane').style.zIndex = 700;
map.getPane('labelPane').style.zIndex = 720;
map.getPane('labelPane').style.pointerEvents = 'none';

const tileWarning = document.getElementById('tileWarning');
const carto = L.tileLayer('https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  maxZoom: 19,
  opacity: 0.92,
  attribution: '&copy; OpenStreetMap & CARTO'
}});
const osm = L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19,
  opacity: 0.92,
  attribution: '&copy; OpenStreetMap contributors'
}});
carto.on('tileerror', () => {{ tileWarning.style.display = 'block'; }});
osm.on('tileerror', () => {{ tileWarning.style.display = 'block'; }});
carto.addTo(map);
L.control.layers({{ 'CARTO Voyager': carto, 'OpenStreetMap': osm }}, null, {{ collapsed: false }}).addTo(map);

const bounds = [];
function sourceShort(source) {{
  return String(source || '').replace('A008_location_matched_', 'loc_').replace('TData_measured_route_family_fallback', 'route_family_fallback');
}}
function timingText(s) {{
  return 'cycle ' + s.cycle_sec + 's; main G ' + s.green_sec + 's; Y ' + s.yellow_sec + 's; side/other ' + s.red_sec + 's';
}}
function tooltipHtml(s, isControl) {{
  const title = isControl ? 'SUMO control centroid' : 'CSV signal position';
  return '<strong>' + title + '</strong><br>' +
    '<strong>' + s.boundary_id + ' ' + s.name + '</strong><br>' +
    s.signal_kind + '<br>' +
    'CSV->control ' + s.csv_to_control_m + 'm<br>' +
    'route controlled: ' + s.route_controlled + '<br>' +
    s.timing_kind + '<br>' +
    timingText(s) + '<br>' +
    'raw phases ' + s.phase_display + '<br>' +
    'status: ' + (s.status || '') + '<br>' +
    'profile: ' + sourceShort(s.source) + '<br>' +
    '<code>' + s.tls_id + '</code>';
}}

if (payload.ev_route && payload.ev_route.coords && payload.ev_route.coords.length) {{
  L.polyline(payload.ev_route.coords, {{
    pane: 'routePane',
    color: '#111827',
    weight: 7,
    opacity: 0.9,
    lineCap: 'round',
    lineJoin: 'round',
    interactive: true
  }}).addTo(map).bindTooltip(
    'EV route<br>' +
    '<code>' + payload.ev_route.start_edge + '</code> -> <code>' + payload.ev_route.target_edge + '</code><br>' +
    payload.ev_route.edge_count + ' edges<br>' +
    'SUMO edge length ' + payload.ev_route.length_m + 'm<br>' +
    'shape polyline ' + payload.ev_route.shape_length_m + 'm',
    {{ sticky: true, className: 'sig-tip' }}
  );
  for (const point of payload.ev_route.coords) bounds.push(point);
}}

for (const road of payload.roads) {{
  const color = road.direction === 'upbound' ? '#00a3e0' : '#155eef';
  L.polyline(road.coords, {{
    pane: 'roadPane',
    color,
    weight: Math.max(5, Math.min(9, Number(road.lane_count || 2) + 4)),
    opacity: 0.95,
    lineCap: 'round',
    lineJoin: 'round',
    interactive: true
  }}).addTo(map).bindTooltip(
    road.segment_id + ' ' + road.direction + '<br><code>' + road.edge_id + '</code>',
    {{ sticky: true, className: 'sig-tip' }}
  );
  for (const point of road.coords) bounds.push(point);
}}

for (const s of payload.signals) {{
  const csv = [s.lat, s.lon];
  const control = s.control_lat && s.control_lon ? [s.control_lat, s.control_lon] : csv;
  L.polyline([csv, control], {{
    pane: 'controlLinkPane',
    color: '#ef4444',
    weight: 2,
    opacity: 0.95,
    dashArray: '6 5'
  }}).addTo(map);

  const controlIcon = L.divIcon({{
    html: '<div class="control-marker"></div>',
    className: '',
    iconSize: [18, 18],
    iconAnchor: [9, 9]
  }});
  L.marker(control, {{ icon: controlIcon, pane: 'signalPane' }})
    .addTo(map)
    .bindTooltip(tooltipHtml(s, true), {{ sticky: true, className: 'sig-tip' }});

  const shapeClass = s.signal_type === 'straight_signal' ? ' straight' : '';
  const signalIcon = L.divIcon({{
    html: '<div class="signal-marker' + shapeClass + '"></div>',
    className: '',
    iconSize: [22, 22],
    iconAnchor: [11, 11]
  }});
  L.marker(csv, {{ icon: signalIcon, pane: 'signalPane' }})
    .addTo(map)
    .bindTooltip(tooltipHtml(s, false), {{ sticky: true, className: 'sig-tip' }});

  const labelIcon = L.divIcon({{
    html: '<div class="signal-label">' + s.boundary_id + '</div>',
    className: '',
    iconSize: [120, 18],
    iconAnchor: [-12, 20]
  }});
  L.marker(csv, {{ icon: labelIcon, pane: 'labelPane', interactive: false }}).addTo(map);
  bounds.push(csv);
  bounds.push(control);
}}

if (bounds.length) {{
  map.fitBounds(bounds, {{ padding: [28, 28] }});
}} else {{
  map.setView([37.561, 126.995], 14);
}}
setTimeout(() => map.invalidateSize(), 100);
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
