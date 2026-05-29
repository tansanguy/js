#!/usr/bin/env python3
"""Create an HTML map for current B1 controlled signal coverage."""

from __future__ import annotations

import copy
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.html_utils import json_for_inline_script, write_text  # noqa: E402


TLS_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_tls.geojson"
EDGE_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_edges.geojson"
TERMINAL_CSV = PROJECT_ROOT / "data_prepared/signals/priority_terminal_candidates.csv"
TLS_AUDIT_CSV = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_spine_v2.csv"
EMERGENCY_ROUTES_CSV = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
SIGNAL_EVENTS_CSV = PROJECT_ROOT / "results/metrics/b1_green_wave_v1_er_acc_002_signal_events.csv"
BATCH_SIGNAL_EVENTS_CSV = PROJECT_ROOT / "results/metrics/b1_b0_valid_route_signal_events.csv"
BATCH_SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b1_b0_valid_route_smoke_summary.json"
SMOKE_SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b1_green_wave_v1_er_acc_002_smoke_summary.json"
OUTPUT_HTML = PROJECT_ROOT / "results/html/controlled_signal_map.html"
OUTPUT_SUMMARY = PROJECT_ROOT / "results/metrics/controlled_signal_map_summary.json"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step14_make_controlled_signal_map_html.log"

MAIN_ROUTE_ID = "ER_ACC_002"
CONTROL_ACTIONS = {"extend_green", "advance_to_next_green"}
SKIP_ACTIONS = {"skip"}


class ControlledSignalMapError(RuntimeError):
    """Expected failure for controlled signal map generation."""


def rel(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def require_file(path: Path) -> None:
    if not path.is_file():
        raise ControlledSignalMapError(f"missing required file: {rel(path)}")


def load_json(path: Path) -> dict[str, Any]:
    require_file(path)
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ControlledSignalMapError(f"JSON root must be an object: {rel(path)}")
    return payload


def read_csv(path: Path) -> list[dict[str, str]]:
    require_file(path)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def parse_list(value: str) -> list[str]:
    return [part for part in value.replace(",", " ").split() if part]


def feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def clone_feature(feature: dict[str, Any], extra_properties: dict[str, Any]) -> dict[str, Any]:
    cloned = copy.deepcopy(feature)
    props = cloned.setdefault("properties", {})
    props.update(extra_properties)
    return cloned


def edge_features_for(edge_ids: list[str], edge_by_id: dict[str, dict[str, Any]], layer: str) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for order, edge_id in enumerate(edge_ids):
        feature = edge_by_id.get(edge_id)
        if feature is None:
            continue
        features.append(clone_feature(feature, {"route_order": order, "visual_layer": layer}))
    return features


def aggregate_events(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for row in rows:
        tls_id = row.get("tls_id", "")
        if not tls_id:
            continue
        current = stats.setdefault(
            tls_id,
            {
                "event_count": 0,
                "control_count": 0,
                "skip_count": 0,
                "restore_count": 0,
                "observe_count": 0,
                "actions": Counter(),
                "first_time": None,
                "last_time": None,
                "last_reason": "",
                "min_remaining_distance_m": None,
            },
        )
        action = row.get("action", "")
        current["event_count"] += 1
        current["actions"][action] += 1
        if action in CONTROL_ACTIONS:
            current["control_count"] += 1
        elif action in SKIP_ACTIONS:
            current["skip_count"] += 1
        elif action == "restore":
            current["restore_count"] += 1
        elif action == "observe_wait":
            current["observe_count"] += 1
        try:
            time_value = float(row.get("time", ""))
        except ValueError:
            time_value = None
        if time_value is not None:
            current["first_time"] = time_value if current["first_time"] is None else min(current["first_time"], time_value)
            current["last_time"] = time_value if current["last_time"] is None else max(current["last_time"], time_value)
        try:
            remaining = float(row.get("remaining_distance_m", ""))
        except ValueError:
            remaining = None
        if remaining is not None:
            current["min_remaining_distance_m"] = (
                remaining
                if current["min_remaining_distance_m"] is None
                else min(current["min_remaining_distance_m"], remaining)
            )
        if row.get("reason"):
            current["last_reason"] = row["reason"]

    serializable: dict[str, dict[str, Any]] = {}
    for tls_id, current in stats.items():
        copied = dict(current)
        copied["actions"] = dict(current["actions"])
        serializable[tls_id] = copied
    return serializable


def build_context() -> dict[str, Any]:
    tls_geojson = load_json(TLS_GEOJSON)
    edge_geojson = load_json(EDGE_GEOJSON)
    terminals = read_csv(TERMINAL_CSV)
    audit_rows = read_csv(TLS_AUDIT_CSV)
    route_rows = read_csv(EMERGENCY_ROUTES_CSV)
    event_rows = read_csv(SIGNAL_EVENTS_CSV)
    batch_event_rows = read_csv(BATCH_SIGNAL_EVENTS_CSV) if BATCH_SIGNAL_EVENTS_CSV.is_file() else []
    smoke_summary = load_json(SMOKE_SUMMARY_JSON)
    batch_summary = load_json(BATCH_SUMMARY_JSON) if BATCH_SUMMARY_JSON.is_file() else {}

    tls_by_id: dict[str, dict[str, Any]] = {}
    for feature in tls_geojson.get("features", []):
        props = feature.get("properties", {})
        for key in (props.get("tls_id"), props.get("junction_id")):
            if key:
                tls_by_id[str(key)] = feature

    edge_by_id: dict[str, dict[str, Any]] = {}
    for feature in edge_geojson.get("features", []):
        edge_id = feature.get("properties", {}).get("edge_id")
        if edge_id:
            edge_by_id[str(edge_id)] = feature

    route_by_id = {row["route_id"]: row for row in route_rows if row.get("route_id")}
    if MAIN_ROUTE_ID not in route_by_id:
        raise ControlledSignalMapError(f"route_id not found in emergency routes: {MAIN_ROUTE_ID}")

    main_route_edges = parse_list(route_by_id[MAIN_ROUTE_ID].get("route_edges", ""))
    all_route_edge_ids: list[str] = []
    seen_edges: set[str] = set()
    for row in route_rows:
        for edge_id in parse_list(row.get("route_edges", "")):
            if edge_id not in seen_edges:
                seen_edges.add(edge_id)
                all_route_edge_ids.append(edge_id)

    event_stats = aggregate_events(event_rows)
    batch_event_stats = aggregate_events(batch_event_rows)
    audit_route_ids_by_tls: dict[str, set[str]] = defaultdict(set)
    for row in audit_rows:
        if row.get("tls_id") and row.get("route_id"):
            audit_route_ids_by_tls[row["tls_id"]].add(row["route_id"])

    terminal_features: list[dict[str, Any]] = []
    terminal_status_counts: Counter[str] = Counter()
    terminal_control_counts: Counter[str] = Counter()
    missing_tls: list[str] = []
    for row in terminals:
        tls_id = row.get("tls_id", "")
        feature = tls_by_id.get(tls_id) or tls_by_id.get(row.get("junction_id", ""))
        if feature is None:
            missing_tls.append(tls_id)
            continue
        stats = event_stats.get(tls_id, {})
        batch_stats = batch_event_stats.get(tls_id, {})
        if batch_stats.get("control_count", 0) > 0:
            runtime_status = "CONTROLLED_IN_B1_BATCH"
        elif batch_stats.get("skip_count", 0) > 0:
            runtime_status = "SKIPPED_IN_B1_BATCH"
        elif batch_stats.get("event_count", 0) > 0:
            runtime_status = "OBSERVED_IN_B1_BATCH"
        else:
            runtime_status = "TERMINAL_CANDIDATE_ONLY"
        install_status = row.get("install_candidate_status", "")
        terminal_status_counts[install_status] += 1
        terminal_control_counts[runtime_status] += 1
        covered_route_ids = parse_list(row.get("covered_route_ids", ""))
        audit_route_ids = sorted(audit_route_ids_by_tls.get(tls_id, set()))
        terminal_features.append(
            clone_feature(
                feature,
                {
                    "terminal_id": row.get("terminal_id", ""),
                    "tls_id": tls_id,
                    "junction_id": row.get("junction_id", ""),
                    "install_candidate_status": install_status,
                    "install_candidate_reason": row.get("install_candidate_reason", ""),
                    "runtime_status": runtime_status,
                    "covered_route_count": row.get("covered_route_count", ""),
                    "covered_route_ids": covered_route_ids,
                    "audit_route_ids": audit_route_ids,
                    "avg_distance_from_firestation_m": row.get("avg_distance_from_firestation_m", ""),
                    "emergency_link_indices": row.get("emergency_link_indices", ""),
                    "green_phase_indices": row.get("green_phase_indices", ""),
                    "yellow_phase_indices": row.get("yellow_phase_indices", ""),
                    "clearance_phase_indices": row.get("clearance_phase_indices", ""),
                    "event_stats": stats,
                    "batch_event_stats": batch_stats,
                },
            )
        )

    all_tls_features = []
    terminal_tls_ids = {row.get("tls_id", "") for row in terminals}
    for feature in tls_geojson.get("features", []):
        props = feature.get("properties", {})
        tls_id = props.get("tls_id", "")
        if tls_id not in terminal_tls_ids:
            all_tls_features.append(
                clone_feature(feature, {"runtime_status": "NON_TERMINAL_TLS", "install_candidate_status": "REFERENCE"})
            )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_html": rel(OUTPUT_HTML),
        "main_route_id": MAIN_ROUTE_ID,
        "terminal_count": len(terminals),
        "terminal_feature_count": len(terminal_features),
        "missing_terminal_tls": missing_tls,
        "all_tls_reference_count": len(all_tls_features),
        "main_route_edge_count": len(main_route_edges),
        "all_route_union_edge_count": len(all_route_edge_ids),
        "terminal_install_status_counts": dict(terminal_status_counts),
        "terminal_runtime_status_counts": dict(terminal_control_counts),
        "signal_event_count": len(event_rows),
        "signal_event_action_counts": dict(Counter(row.get("action", "") for row in event_rows)),
        "batch_signal_event_count": len(batch_event_rows),
        "batch_signal_event_action_counts": dict(Counter(row.get("action", "") for row in batch_event_rows)),
        "batch_unique_tls_count": len(batch_event_stats),
        "batch_executed_route_count": batch_summary.get("executed_route_count"),
        "batch_status_counts": batch_summary.get("status_counts", {}),
        "controlled_tls_count": smoke_summary.get("controlled_tls_count"),
        "skipped_tls_count": smoke_summary.get("skipped_tls_count"),
        "intervention_count": smoke_summary.get("intervention_count"),
        "emergency_travel_time": smoke_summary.get("emergency_travel_time"),
        "source_files": {
            "tls_geojson": rel(TLS_GEOJSON),
            "edge_geojson": rel(EDGE_GEOJSON),
            "terminal_candidates": rel(TERMINAL_CSV),
            "tls_audit": rel(TLS_AUDIT_CSV),
            "emergency_routes": rel(EMERGENCY_ROUTES_CSV),
            "signal_events": rel(SIGNAL_EVENTS_CSV),
            "batch_signal_events": rel(BATCH_SIGNAL_EVENTS_CSV),
            "smoke_summary": rel(SMOKE_SUMMARY_JSON),
            "batch_summary": rel(BATCH_SUMMARY_JSON),
        },
    }

    return {
        "summary": summary,
        "terminal_features": feature_collection(terminal_features),
        "reference_tls_features": feature_collection(all_tls_features),
        "main_route_features": feature_collection(edge_features_for(main_route_edges, edge_by_id, "main_route")),
        "all_route_features": feature_collection(edge_features_for(all_route_edge_ids, edge_by_id, "all_routes")),
    }


def render_html(context: dict[str, Any]) -> str:
    context_json = json_for_inline_script(context)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Controlled Signal Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIINfQBdjlIeqio5I8fAFs1C7lYLVQ2wZj4=" crossorigin="">
  <style>
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
    .leaflet-container img {{
      max-width: none !important;
      max-height: none !important;
    }}
    .leaflet-tile {{
      width: 256px;
      height: 256px;
    }}
    .leaflet-tile-pane {{ z-index: 200; }}
    .leaflet-overlay-pane {{ z-index: 400; }}
    .leaflet-shadow-pane {{ z-index: 500; }}
    .leaflet-marker-pane {{ z-index: 600; }}
    .leaflet-tooltip-pane {{ z-index: 650; }}
    .leaflet-popup-pane {{ z-index: 700; }}
    .leaflet-control {{
      position: relative;
      z-index: 800;
      pointer-events: auto;
    }}
    .leaflet-top,
    .leaflet-bottom {{
      position: absolute;
      z-index: 1000;
      pointer-events: none;
    }}
    .leaflet-top {{ top: 0; }}
    .leaflet-right {{ right: 0; }}
    .leaflet-bottom {{ bottom: 0; }}
    .leaflet-left {{ left: 0; }}
    .leaflet-control-container .leaflet-top,
    .leaflet-control-container .leaflet-bottom {{
      position: absolute;
    }}
    .leaflet-control-zoom {{
      margin-left: 10px;
      margin-top: 10px;
    }}
    .leaflet-control-attribution {{
      margin: 0;
      padding: 0 5px;
      color: #334155;
      background: rgba(255,255,255,0.8);
      font-size: 11px;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; margin: 0; }}
    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #1f2937;
      background: #f6f7f9;
    }}
    .app {{ height: 100vh; display: flex; flex-direction: column; overflow: hidden; }}
    header {{
      flex: 0 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      min-height: 58px;
      padding: 10px 14px;
      border-bottom: 1px solid #d5dae3;
      background: #fff;
    }}
    h1 {{ margin: 0; font-size: 17px; line-height: 1.2; }}
    .subtitle {{ color: #667085; font-size: 12px; margin-top: 3px; }}
    .status-bar {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 0 8px;
      border: 1px solid #cfd6e2;
      border-radius: 6px;
      background: #fff;
      font-size: 12px;
      white-space: nowrap;
    }}
    .badge.pass {{ border-color: #9fd0b5; color: #166534; background: #eef8f1; }}
    .badge.warn {{ border-color: #f4c57e; color: #92400e; background: #fff7e8; }}
    .content {{ flex: 1 1 auto; min-height: 0; display: flex; }}
    #map {{ flex: 1 1 auto; min-width: 0; min-height: 0; }}
    aside {{
      width: 390px;
      flex: 0 0 390px;
      overflow: auto;
      border-left: 1px solid #d5dae3;
      background: #fff;
      padding: 14px;
    }}
    .section {{ padding-bottom: 14px; margin-bottom: 14px; border-bottom: 1px solid #e1e5ec; }}
    .section:last-child {{ border-bottom: 0; }}
    h2 {{ margin: 0 0 8px; font-size: 14px; }}
    .controls {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    label.toggle {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 34px;
      padding: 6px 8px;
      border: 1px solid #d5dae3;
      border-radius: 6px;
      font-size: 12px;
      background: #fbfcfe;
    }}
    .legend {{ display: grid; gap: 7px; font-size: 12px; }}
    .legend-row {{ display: grid; grid-template-columns: 18px 1fr; gap: 8px; align-items: center; }}
    .dot {{ width: 14px; height: 14px; border-radius: 50%; border: 2px solid #fff; box-shadow: 0 0 0 1px #778091; }}
    .dot.controlled {{ background: #16a34a; }}
    .dot.skipped {{ background: #f59e0b; }}
    .dot.observed {{ background: #2563eb; }}
    .dot.candidate {{ background: #64748b; }}
    .dot.ref {{ background: #cbd5e1; }}
    .metric-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    .metric {{
      border: 1px solid #d5dae3;
      border-radius: 6px;
      padding: 8px;
      background: #fbfcfe;
      min-width: 0;
    }}
    .metric .value {{ font-size: 18px; font-weight: 700; line-height: 1.1; }}
    .metric .label {{ margin-top: 3px; color: #667085; font-size: 11px; line-height: 1.25; }}
    .terminal-list {{ display: grid; gap: 6px; }}
    .terminal-item {{
      border: 1px solid #d5dae3;
      border-radius: 6px;
      padding: 8px;
      background: #fff;
      cursor: pointer;
    }}
    .terminal-item:hover {{ border-color: #2563eb; }}
    .terminal-title {{ font-size: 12px; font-weight: 700; }}
    .terminal-detail {{ margin-top: 3px; color: #667085; font-size: 11px; overflow-wrap: anywhere; }}
    .note {{ color: #667085; font-size: 12px; line-height: 1.45; }}
    .leaflet-popup-content {{ min-width: 260px; }}
    .popup-title {{ font-weight: 700; margin-bottom: 6px; }}
    .popup-row {{ display: grid; grid-template-columns: 110px 1fr; gap: 8px; font-size: 12px; margin: 3px 0; }}
    .popup-key {{ color: #667085; }}
  </style>
</head>
<body>
<div class="app">
  <header>
    <div>
      <h1>소방서-서울역 Priority Signal Control Map</h1>
      <div class="subtitle">20개 terminal 후보, 18-route B1 batch 이벤트, ER_ACC_002 Green Wave v1 단일 smoke</div>
    </div>
    <div class="status-bar" id="statusBar"></div>
  </header>
  <div class="content">
    <div id="map"></div>
    <aside>
      <div class="section">
        <h2>핵심 상태</h2>
        <div class="metric-grid" id="metricGrid"></div>
      </div>
      <div class="section">
        <h2>레이어</h2>
        <div class="controls">
          <label class="toggle"><input type="checkbox" data-layer="mainRoute" checked> ER_ACC_002 route</label>
          <label class="toggle"><input type="checkbox" data-layer="allRoutes" checked> 19 route union</label>
          <label class="toggle"><input type="checkbox" data-layer="terminals" checked> terminal 후보 20개</label>
          <label class="toggle"><input type="checkbox" data-layer="referenceTls"> 전체 TLS 참고</label>
        </div>
      </div>
      <div class="section">
        <h2>범례</h2>
        <div class="legend">
          <div class="legend-row"><span class="dot controlled"></span><span>18-route B1 batch에서 실제 제어 개입</span></div>
          <div class="legend-row"><span class="dot skipped"></span><span>18-route B1 batch에서 안전상 skip만 발생</span></div>
          <div class="legend-row"><span class="dot observed"></span><span>18-route B1 batch에서 관측만 됨</span></div>
          <div class="legend-row"><span class="dot candidate"></span><span>terminal 후보지만 batch 이벤트 없음</span></div>
          <div class="legend-row"><span class="dot ref"></span><span>terminal 후보가 아닌 전체 TLS 참고</span></div>
        </div>
      </div>
      <div class="section">
        <h2>해석</h2>
        <div class="note">
          기본 색상은 18개 B0-valid route B1 batch 기준입니다. 녹색/주황색은 route별 smoke 중 실제 판단이 발생한 신호이고,
          회색 terminal은 후보지만 batch 이벤트가 없는 신호입니다. ER_ACC_002 단일 Green Wave v1 이벤트는 팝업에서 별도 수치로 확인합니다.
          아직 “20개 terminal을 한 run에서 모두 강제 제어”한 실험은 아니며, route별 중앙형 제어가 어느 신호를 실제로 건드렸는지 보는 지도입니다.
        </div>
      </div>
      <div class="section">
        <h2>Terminal 목록</h2>
        <div class="terminal-list" id="terminalList"></div>
      </div>
    </aside>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
  integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
const ctx = {context_json};

function statusColor(status) {{
  if (status === 'CONTROLLED_IN_B1_BATCH') return '#16a34a';
  if (status === 'SKIPPED_IN_B1_BATCH') return '#f59e0b';
  if (status === 'OBSERVED_IN_B1_BATCH') return '#2563eb';
  if (status === 'NON_TERMINAL_TLS') return '#cbd5e1';
  return '#64748b';
}}

function statusLabel(status) {{
  const labels = {{
    CONTROLLED_IN_B1_BATCH: 'batch 제어 개입',
    SKIPPED_IN_B1_BATCH: 'batch skip',
    OBSERVED_IN_B1_BATCH: 'batch 관측',
    TERMINAL_CANDIDATE_ONLY: '후보',
    NON_TERMINAL_TLS: '참고 TLS'
  }};
  return labels[status] || status;
}}

function fmt(value, suffix = '') {{
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'number') return Number.isInteger(value) ? `${{value}}${{suffix}}` : `${{value.toFixed(2)}}${{suffix}}`;
  return `${{value}}${{suffix}}`;
}}

function popupHtml(props) {{
  const stats = props.event_stats || {{}};
  const batchStats = props.batch_event_stats || {{}};
  const routes = Array.isArray(props.covered_route_ids) ? props.covered_route_ids.join(' ') : (props.covered_route_ids || '');
  return `
    <div class="popup-title">${{props.terminal_id || ''}} ${{props.tls_id || ''}}</div>
    <div class="popup-row"><span class="popup-key">상태</span><span>${{statusLabel(props.runtime_status)}}</span></div>
    <div class="popup-row"><span class="popup-key">설치 판정</span><span>${{props.install_candidate_status || '-'}}</span></div>
    <div class="popup-row"><span class="popup-key">covered routes</span><span>${{routes || '-'}}</span></div>
    <div class="popup-row"><span class="popup-key">batch control/skip</span><span>${{fmt(batchStats.control_count)}} / ${{fmt(batchStats.skip_count)}}</span></div>
    <div class="popup-row"><span class="popup-key">ER002 control/skip</span><span>${{fmt(stats.control_count)}} / ${{fmt(stats.skip_count)}}</span></div>
    <div class="popup-row"><span class="popup-key">green phase</span><span>${{props.green_phase_indices || '-'}}</span></div>
    <div class="popup-row"><span class="popup-key">linkIndex</span><span>${{props.emergency_link_indices || '-'}}</span></div>
    <div class="popup-row"><span class="popup-key">사유</span><span>${{batchStats.last_reason || stats.last_reason || props.install_candidate_reason || '-'}}</span></div>
  `;
}}

const map = L.map('map', {{preferCanvas: true}}).setView([37.5585, 126.9865], 14);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors'
}}).addTo(map);

const layers = {{}};
layers.allRoutes = L.geoJSON(ctx.all_route_features, {{
  style: {{color: '#94a3b8', weight: 2, opacity: 0.35}}
}}).addTo(map);
layers.mainRoute = L.geoJSON(ctx.main_route_features, {{
  style: {{color: '#dc2626', weight: 5, opacity: 0.88}}
}}).addTo(map);
layers.terminals = L.geoJSON(ctx.terminal_features, {{
  pointToLayer: (feature, latlng) => {{
    const p = feature.properties || {{}};
    const radius = p.runtime_status === 'CONTROLLED_IN_ER_ACC_002' ? 9 : 7;
    return L.circleMarker(latlng, {{
      radius,
      color: '#ffffff',
      weight: 2,
      fillColor: statusColor(p.runtime_status),
      fillOpacity: 0.92
    }});
  }},
  onEachFeature: (feature, layer) => {{
    const p = feature.properties || {{}};
    layer.bindPopup(popupHtml(p));
    layer.bindTooltip(`${{p.terminal_id}} · ${{statusLabel(p.runtime_status)}}`, {{direction: 'top', sticky: true}});
  }}
}}).addTo(map);
layers.referenceTls = L.geoJSON(ctx.reference_tls_features, {{
  pointToLayer: (feature, latlng) => L.circleMarker(latlng, {{
    radius: 4,
    color: '#94a3b8',
    weight: 1,
    fillColor: '#cbd5e1',
    fillOpacity: 0.55
  }}),
  onEachFeature: (feature, layer) => {{
    const p = feature.properties || {{}};
    layer.bindTooltip(`TLS ${{p.tls_id || ''}}`, {{direction: 'top', sticky: true}});
  }}
}});

const group = L.featureGroup([layers.mainRoute, layers.terminals]);
if (group.getBounds().isValid()) map.fitBounds(group.getBounds(), {{padding: [24, 24]}});

document.querySelectorAll('input[data-layer]').forEach(input => {{
  input.addEventListener('change', () => {{
    const layer = layers[input.dataset.layer];
    if (!layer) return;
    if (input.checked) layer.addTo(map);
    else map.removeLayer(layer);
  }});
}});

const summary = ctx.summary;
const runtime = summary.terminal_runtime_status_counts || {{}};
const statusBar = document.getElementById('statusBar');
statusBar.innerHTML = `
  <span class="badge pass">terminal PASS ${{(summary.terminal_install_status_counts || {{}}).PASS || 0}}</span>
  <span class="badge pass">batch 제어 TLS ${{runtime.CONTROLLED_IN_B1_BATCH || 0}}</span>
  <span class="badge warn">batch skip-only TLS ${{runtime.SKIPPED_IN_B1_BATCH || 0}}</span>
`;

const metrics = [
  [summary.terminal_count, 'priority terminal 후보'],
  [summary.batch_unique_tls_count, '18-route batch 이벤트 TLS'],
  [runtime.CONTROLLED_IN_B1_BATCH || 0, '18-route batch 제어 TLS'],
  [runtime.SKIPPED_IN_B1_BATCH || 0, '18-route batch skip-only TLS'],
  [summary.controlled_tls_count, 'ER_ACC_002 v1 제어 TLS'],
  [summary.intervention_count, 'ER_ACC_002 v1 intervention'],
  [summary.all_tls_reference_count, 'terminal 외 TLS 참고'],
  [summary.emergency_travel_time, 'ER_ACC_002 travel time (s)'],
];
document.getElementById('metricGrid').innerHTML = metrics.map(([value, label]) => `
  <div class="metric"><div class="value">${{fmt(value)}}</div><div class="label">${{label}}</div></div>
`).join('');

const terminalList = document.getElementById('terminalList');
const markers = [];
layers.terminals.eachLayer(layer => markers.push(layer));
terminalList.innerHTML = markers
  .map((layer, idx) => {{
    const p = layer.feature.properties || {{}};
    const stats = p.event_stats || {{}};
    return `
      <div class="terminal-item" data-terminal-idx="${{idx}}">
        <div class="terminal-title">${{p.terminal_id}} · ${{statusLabel(p.runtime_status)}}</div>
        <div class="terminal-detail">${{p.tls_id}}</div>
        <div class="terminal-detail">routes ${{p.covered_route_count || '-'}} · batch control ${{fmt((p.batch_event_stats || {{}}).control_count)}} · skip ${{fmt((p.batch_event_stats || {{}}).skip_count)}}</div>
      </div>
    `;
  }})
  .join('');
document.querySelectorAll('.terminal-item').forEach(item => {{
  item.addEventListener('click', () => {{
    const layer = markers[Number(item.dataset.terminalIdx)];
    if (!map.hasLayer(layers.terminals)) {{
      layers.terminals.addTo(map);
      document.querySelector('input[data-layer="terminals"]').checked = true;
    }}
    map.setView(layer.getLatLng(), 17);
    layer.openPopup();
  }});
}});
</script>
</body>
</html>
"""


def main() -> int:
    lines = [
        "Step 14 controlled signal map HTML",
        "===================================",
        f"Project root: {PROJECT_ROOT}",
        "Policy: read existing Step 11/12/14 artifacts; do not alter net/routes/background demand.",
    ]
    try:
        context = build_context()
        write_text(OUTPUT_HTML, render_html(context))
        write_text(OUTPUT_SUMMARY, json.dumps(context["summary"], ensure_ascii=False, indent=2) + "\n")
        lines.extend(
            [
                "Status: PASS",
                f"Wrote HTML: {rel(OUTPUT_HTML)}",
                f"Wrote summary: {rel(OUTPUT_SUMMARY)}",
                f"terminal_count: {context['summary']['terminal_count']}",
                f"runtime_status_counts: {context['summary']['terminal_runtime_status_counts']}",
            ]
        )
        write_text(LOG_PATH, "\n".join(lines) + "\n")
        print("\n".join(lines))
        return 0
    except (ControlledSignalMapError, OSError, json.JSONDecodeError) as exc:
        lines.extend(["Status: FAIL", str(exc)])
        write_text(LOG_PATH, "\n".join(lines) + "\n")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
