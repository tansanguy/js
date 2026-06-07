#!/usr/bin/env python3
"""Build a standalone SVG route review HTML for final destination validation."""

from __future__ import annotations

import csv
import html
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NET_PATH = PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
DRY_RUN_ROOT = PROJECT_ROOT / "runs/compact_v9_final_destination_validation/final_route_plan_dry_run/inputs/screening"
CANDIDATE_SELECTION_CSV = PROJECT_ROOT / "results/metrics/compact_v9_final_destination_validation/final_route_plan_dry_run/screening/candidate_selection.csv"
OUTPUT_HTML = PROJECT_ROOT / "results/html/compact_v9_final_destination_route_plan.html"
OUTPUT_JSON = PROJECT_ROOT / "results/html/compact_v9_final_destination_route_plan.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {"", None}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_shape(text: str) -> list[list[float]]:
    points: list[list[float]] = []
    for item in text.split():
        if "," not in item:
            continue
        x_text, y_text = item.split(",", 1)
        points.append([float(x_text), float(y_text)])
    return points


def load_edge_shapes(net_path: Path) -> dict[str, list[list[float]]]:
    root = ET.parse(net_path).getroot()
    shapes: dict[str, list[list[float]]] = {}
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.get("function") == "internal":
            continue
        shape = parse_shape(edge.get("shape", ""))
        if len(shape) < 2:
            lane = edge.find("lane")
            if lane is not None:
                shape = parse_shape(lane.get("shape", ""))
        if len(shape) >= 2:
            shapes[edge_id] = shape
    return shapes


def route_csv_path(route_id: str) -> Path:
    return DRY_RUN_ROOT / route_id / "firetruck_route.csv"


def route_edges(route_id: str) -> list[str]:
    path = route_csv_path(route_id)
    if not path.is_file():
        return []
    rows = read_csv(path)
    if not rows:
        return []
    return rows[0].get("route_edges", "").split()


def route_polyline(edge_ids: list[str], edge_shapes: dict[str, list[list[float]]]) -> list[list[float]]:
    points: list[list[float]] = []
    for edge_id in edge_ids:
        shape = edge_shapes.get(edge_id, [])
        if not shape:
            continue
        if points and shape and points[-1] == shape[0]:
            points.extend(shape[1:])
        else:
            points.extend(shape)
    return points


def build_payload() -> dict[str, Any]:
    candidate_rows = read_csv(CANDIDATE_SELECTION_CSV)
    edge_shapes = load_edge_shapes(NET_PATH)
    routes: list[dict[str, Any]] = []
    all_points: list[list[float]] = []
    for row in candidate_rows:
        route_id = row.get("route_id", "")
        edges = route_edges(route_id)
        points = route_polyline(edges, edge_shapes)
        if points:
            all_points.extend(points)
        rank = int(safe_float(row.get("candidate_rank"), 9999))
        routes.append({
            "candidate_rank": rank,
            "route_id": route_id,
            "source_route_id": row.get("source_route_id", ""),
            "target_edge_id": row.get("target_edge_id", ""),
            "status": row.get("selection_status", ""),
            "reason": row.get("selection_reason", ""),
            "route_edge_count": int(safe_float(row.get("route_edge_count"), 0.0)),
            "route_length_m": safe_float(row.get("route_length_m"), 0.0),
            "mainroad_length_ratio": safe_float(row.get("mainroad_length_ratio"), 0.0),
            "legacy_spine_length_ratio": safe_float(row.get("legacy_spine_length_ratio"), 0.0),
            "screening_priority_top3": rank <= 3 and bool(points),
            "edge_ids": edges,
            "points": points,
        })
    if all_points:
        xs = [point[0] for point in all_points]
        ys = [point[1] for point in all_points]
        bounds = {"min_x": min(xs), "max_x": max(xs), "min_y": min(ys), "max_y": max(ys)}
    else:
        bounds = {"min_x": 0.0, "max_x": 1.0, "min_y": 0.0, "max_y": 1.0}
    return {
        "schema": "compact_v9_final_destination_route_plan_html.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "net": NET_PATH.relative_to(PROJECT_ROOT).as_posix(),
            "candidate_selection_csv": CANDIDATE_SELECTION_CSV.relative_to(PROJECT_ROOT).as_posix(),
            "route_input_root": DRY_RUN_ROOT.relative_to(PROJECT_ROOT).as_posix(),
        },
        "note": "Dry-run route plan. Final 3 destinations are decided only after screening execution, not by this HTML.",
        "bounds": bounds,
        "routes": routes,
    }


def scale_points(points: list[list[float]], bounds: dict[str, float], width: int, height: int, pad: int) -> str:
    min_x, max_x = bounds["min_x"], bounds["max_x"]
    min_y, max_y = bounds["min_y"], bounds["max_y"]
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    out = []
    for x, y in points:
        sx = pad + ((x - min_x) / span_x) * (width - 2 * pad)
        sy = height - pad - ((y - min_y) / span_y) * (height - 2 * pad)
        out.append(f"{sx:.1f},{sy:.1f}")
    return " ".join(out)


def render_html(payload: dict[str, Any]) -> str:
    width = 1500
    height = 950
    pad = 60
    bounds = payload["bounds"]
    route_rows = sorted(payload["routes"], key=lambda row: row["candidate_rank"])
    polylines = []
    markers = []
    for route in route_rows:
        if not route["points"]:
            continue
        top3 = route["screening_priority_top3"]
        color = "#ef4444" if top3 else "#2563eb"
        opacity = "0.95" if top3 else "0.24"
        weight = "7" if top3 else "3"
        pts = scale_points(route["points"], bounds, width, height, pad)
        title = html.escape(f"{route['candidate_rank']}. {route['source_route_id']} -> {route['target_edge_id']}")
        polylines.append(
            f'<polyline class="route-line{" top3" if top3 else ""}" data-route="{html.escape(route["route_id"])}" '
            f'points="{pts}" fill="none" stroke="{color}" stroke-width="{weight}" stroke-opacity="{opacity}" '
            f'stroke-linecap="round" stroke-linejoin="round"><title>{title}</title></polyline>'
        )
        start = scale_points([route["points"][0]], bounds, width, height, pad)
        end = scale_points([route["points"][-1]], bounds, width, height, pad)
        if top3:
            markers.append(f'<circle class="marker start" cx="{start.split(",")[0]}" cy="{start.split(",")[1]}" r="7"><title>Start {title}</title></circle>')
            markers.append(f'<circle class="marker target" cx="{end.split(",")[0]}" cy="{end.split(",")[1]}" r="8"><title>Target {title}</title></circle>')
    table_rows = []
    for route in route_rows:
        flag = "Top-priority" if route["screening_priority_top3"] else route["status"]
        table_rows.append(
            "<tr>"
            f"<td>{route['candidate_rank']}</td>"
            f"<td><code>{html.escape(route['source_route_id'])}</code></td>"
            f"<td><code>{html.escape(route['target_edge_id'])}</code></td>"
            f"<td>{route['route_edge_count']}</td>"
            f"<td>{route['route_length_m']:.1f}</td>"
            f"<td>{route['mainroad_length_ratio']:.3f}</td>"
            f"<td>{html.escape(flag)}</td>"
            "</tr>"
        )
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Compact V9 Final Destination Route Plan</title>
  <style>
    html, body {{ margin: 0; min-height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111827; background: #f8fafc; }}
    .layout {{ display: grid; grid-template-columns: 390px 1fr; min-height: 100vh; }}
    aside {{ padding: 18px; background: #fff; border-right: 1px solid #d1d5db; overflow: auto; }}
    main {{ min-width: 0; padding: 16px; }}
    h1 {{ font-size: 21px; line-height: 1.25; margin: 0 0 8px; }}
    h2 {{ font-size: 14px; margin: 18px 0 8px; }}
    p {{ margin: 0 0 10px; line-height: 1.45; }}
    .note {{ color: #4b5563; font-size: 13px; }}
    .map-wrap {{ background: #eef2f7; border: 1px solid #cbd5e1; border-radius: 8px; overflow: hidden; }}
    svg {{ display: block; width: 100%; height: auto; min-height: 620px; }}
    .grid {{ stroke: #d1d5db; stroke-width: 1; opacity: .55; }}
    .marker.start {{ fill: #10b981; stroke: #064e3b; stroke-width: 2; }}
    .marker.target {{ fill: #f97316; stroke: #7c2d12; stroke-width: 2; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 6px 5px; text-align: left; vertical-align: top; }}
    th {{ color: #64748b; font-weight: 700; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; }}
    .legend {{ display: flex; gap: 12px; flex-wrap: wrap; font-size: 12px; color: #374151; margin: 10px 0 0; }}
    .swatch {{ display: inline-block; width: 24px; height: 4px; border-radius: 999px; vertical-align: middle; margin-right: 5px; }}
    .red {{ background: #ef4444; }}
    .blue {{ background: #2563eb; opacity: .45; }}
    .meta {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px; }}
    .box {{ background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 7px; padding: 8px; min-width: 0; }}
    .label {{ display: block; color: #64748b; font-size: 11px; margin-bottom: 2px; }}
    .value {{ font-weight: 700; font-size: 13px; overflow-wrap: anywhere; }}
    @media (max-width: 980px) {{
      .layout {{ grid-template-columns: 1fr; }}
      aside {{ border-right: 0; border-bottom: 1px solid #d1d5db; }}
      svg {{ min-height: 420px; }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <h1>Final Simulation Route Plan</h1>
      <p class="note">10번 최종 검증은 full screening 후 3개 목적지가 확정된다. 이 HTML은 dry-run 기준의 18개 후보 경로와 현재 우선순위 상위 3개를 보여주는 경로 검토용 산출물이다.</p>
      <div class="legend">
        <span><span class="swatch red"></span>screening priority top 3</span>
        <span><span class="swatch blue"></span>other runnable candidates</span>
      </div>
      <div class="meta">
        <div class="box"><span class="label">Runnable</span><span class="value">17 / 18</span></div>
        <div class="box"><span class="label">Excluded</span><span class="value">ER_ACC_018</span></div>
        <div class="box"><span class="label">Start edge</span><span class="value">420331801#1</span></div>
        <div class="box"><span class="label">Basis</span><span class="value">S1-forced net</span></div>
      </div>
      <h2>How to read</h2>
      <p class="note">빨간 경로는 dry-run 우선순위 상위 후보일 뿐 최종 성능 결과가 아니다. 실제 최종 3개는 screening에서 B004/B04/B4를 실행한 뒤 개선폭, B04 지연, 개입량, 대표성 기준으로 확정한다.</p>
      <h2>Route Table</h2>
      <table>
        <thead><tr><th>#</th><th>Route</th><th>Target</th><th>Edges</th><th>m</th><th>Main</th><th>Status</th></tr></thead>
        <tbody>{''.join(table_rows)}</tbody>
      </table>
    </aside>
    <main>
      <div class="map-wrap">
        <svg viewBox="0 0 {width} {height}" role="img" aria-label="Final destination candidate route plan">
          <rect x="0" y="0" width="{width}" height="{height}" fill="#eef2f7"/>
          <g>
            <line class="grid" x1="0" y1="{height * .25:.0f}" x2="{width}" y2="{height * .25:.0f}"/>
            <line class="grid" x1="0" y1="{height * .50:.0f}" x2="{width}" y2="{height * .50:.0f}"/>
            <line class="grid" x1="0" y1="{height * .75:.0f}" x2="{width}" y2="{height * .75:.0f}"/>
            <line class="grid" x1="{width * .25:.0f}" y1="0" x2="{width * .25:.0f}" y2="{height}"/>
            <line class="grid" x1="{width * .50:.0f}" y1="0" x2="{width * .50:.0f}" y2="{height}"/>
            <line class="grid" x1="{width * .75:.0f}" y1="0" x2="{width * .75:.0f}" y2="{height}"/>
          </g>
          <g>{''.join(polylines)}</g>
          <g>{''.join(markers)}</g>
        </svg>
      </div>
    </main>
  </div>
  <script>window.ROUTE_PLAN = {data};</script>
</body>
</html>
"""


def main() -> int:
    payload = build_payload()
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(OUTPUT_HTML)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
