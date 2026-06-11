#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NET_FILE = PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
ROUTE_CSV = PROJECT_ROOT / "runs/compact_v9_final_destination_validation/final_destination_validation_bo_best_20260608_viz_pass/inputs/final/FINAL_DEST_ER_ACC_006/firetruck_route.csv"
MAINROAD_MAPPING = PROJECT_ROOT / "data_prepared/compact_v9/map/B04_toegye_segment_edge_mapping.csv"
OUTPUT_HTML = PROJECT_ROOT / "results/html/final_route_006_static_map.html"
OUTPUT_JSON = PROJECT_ROOT / "results/html/final_route_006_static_map.json"
OUTPUT_PNG = PROJECT_ROOT / "results/html/final_route_006_static_map.png"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_shape(text: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in text.split():
        if "," not in item:
            continue
        x, y = item.split(",", 1)
        points.append((float(x), float(y)))
    return points


def load_edge_shapes(net_file: Path) -> tuple[dict[str, list[tuple[float, float]]], dict[str, float]]:
    root = ET.parse(net_file).getroot()
    shapes: dict[str, list[tuple[float, float]]] = {}
    lengths: dict[str, float] = {}
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.get("function") == "internal":
            continue
        shape = parse_shape(edge.get("shape", ""))
        if len(shape) < 2:
            lane = edge.find("lane")
            if lane is not None:
                shape = parse_shape(lane.get("shape", ""))
        if len(shape) < 2:
            continue
        lane = edge.find("lane")
        length = safe_float(lane.get("length") if lane is not None else edge.get("length"), 0.0)
        shapes[edge_id] = shape
        lengths[edge_id] = length
    return shapes, lengths


def mainroad_edges(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {row["edge_id"] for row in read_csv(path) if row.get("edge_id")}


def bounds(points: list[tuple[float, float]], pad_ratio: float = 0.10) -> dict[str, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    dx = max(max_x - min_x, 1.0)
    dy = max(max_y - min_y, 1.0)
    return {
        "min_x": min_x - dx * pad_ratio,
        "max_x": max_x + dx * pad_ratio,
        "min_y": min_y - dy * pad_ratio,
        "max_y": max_y + dy * pad_ratio,
    }


def in_bounds(shape: list[tuple[float, float]], box: dict[str, float]) -> bool:
    return any(
        box["min_x"] <= x <= box["max_x"] and box["min_y"] <= y <= box["max_y"]
        for x, y in shape
    )


def project(point: tuple[float, float], box: dict[str, float], width: int, height: int) -> tuple[float, float]:
    x, y = point
    span_x = max(box["max_x"] - box["min_x"], 1.0)
    span_y = max(box["max_y"] - box["min_y"], 1.0)
    sx = ((x - box["min_x"]) / span_x) * width
    sy = height - ((y - box["min_y"]) / span_y) * height
    return sx, sy


def points_attr(shape: list[tuple[float, float]], box: dict[str, float], width: int, height: int) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in (project(point, box, width, height) for point in shape))


def build_payload() -> dict[str, Any]:
    row = read_csv(ROUTE_CSV)[0]
    route_edges = row["route_edges"].split()
    edge_shapes, edge_lengths = load_edge_shapes(NET_FILE)
    main_edges = mainroad_edges(MAINROAD_MAPPING)
    route_points: list[tuple[float, float]] = []
    segments: list[dict[str, Any]] = []
    for index, edge_id in enumerate(route_edges, start=1):
        shape = edge_shapes.get(edge_id, [])
        if not shape:
            continue
        if route_points and route_points[-1] == shape[0]:
            route_points.extend(shape[1:])
        else:
            route_points.extend(shape)
        segments.append({
            "index": index,
            "edge_id": edge_id,
            "length_m": round(edge_lengths.get(edge_id, 0.0), 2),
            "mainroad": edge_id in main_edges,
            "shape": shape,
        })
    box = bounds(route_points)
    context_edges = [
        {"edge_id": edge_id, "shape": shape}
        for edge_id, shape in edge_shapes.items()
        if edge_id not in route_edges and in_bounds(shape, box)
    ]
    mainroad_length_m = sum(seg["length_m"] for seg in segments if seg["mainroad"])
    other_length_m = sum(seg["length_m"] for seg in segments if not seg["mainroad"])
    return {
        "schema": "final_route_006_static_map.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "route": {
            "route_id": row["route_id"],
            "scenario_id": row["scenario_id"],
            "target_edge_id": row["target_edge_id"],
            "route_edge_count": int(row["route_edge_count"]),
            "route_length_m": safe_float(row["route_length_m"]),
            "mainroad_length_ratio": safe_float(row["mainroad_length_ratio"]),
            "legacy_spine_length_ratio": safe_float(row["legacy_spine_length_ratio"]),
            "start_edge_id": row["start_edge_id"],
            "merge_edge_id": row["merge_edge_id"],
            "mainroad_length_m": round(mainroad_length_m, 2),
            "other_length_m": round(other_length_m, 2),
        },
        "bounds": box,
        "segments": segments,
        "context_edges": context_edges,
        "sources": {
            "net_file": NET_FILE.relative_to(PROJECT_ROOT).as_posix(),
            "route_csv": ROUTE_CSV.relative_to(PROJECT_ROOT).as_posix(),
            "mainroad_mapping": MAINROAD_MAPPING.relative_to(PROJECT_ROOT).as_posix(),
        },
    }


def render_html(payload: dict[str, Any]) -> str:
    width = 1500
    height = 920
    box = payload["bounds"]
    context = "\n".join(
        f'<polyline points="{points_attr(edge["shape"], box, width, height)}" class="road" />'
        for edge in payload["context_edges"]
    )
    route_lines = "\n".join(
        f'<polyline points="{points_attr(seg["shape"], box, width, height)}" '
        f'class="route {"main" if seg["mainroad"] else "other"}">'
        f'<title>{html.escape(seg["edge_id"])} · {seg["length_m"]:.1f}m</title></polyline>'
        for seg in payload["segments"]
    )
    first_shape = payload["segments"][0]["shape"]
    last_shape = payload["segments"][-1]["shape"]
    sx, sy = project(first_shape[0], box, width, height)
    tx, ty = project(last_shape[-1], box, width, height)
    route = payload["route"]
    main_pct = route["mainroad_length_ratio"] * 100.0
    other_pct = 100.0 - main_pct
    source_rows = "\n".join(
        f"<tr><td>{html.escape(k)}</td><td><code>{html.escape(v)}</code></td></tr>"
        for k, v in payload["sources"].items()
    )
    segment_rows = "\n".join(
        "<tr>"
        f"<td>{seg['index']:02d}</td>"
        f"<td><code>{html.escape(seg['edge_id'])}</code></td>"
        f"<td>{seg['length_m']:.1f}</td>"
        f"<td>{'main' if seg['mainroad'] else 'other'}</td>"
        "</tr>"
        for seg in payload["segments"]
    )
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FINAL_DEST_ER_ACC_006 Route Map</title>
  <style>
    :root {{
      --bg: #f7f8fb;
      --panel: #ffffff;
      --ink: #17202f;
      --muted: #596579;
      --line: #d9dee8;
      --road: #c9d1df;
      --main: #e11d48;
      --other: #f59e0b;
      --start: #059669;
      --target: #2563eb;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; min-height: 100%; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg); }}
    .layout {{ display: grid; grid-template-columns: 360px 1fr; min-height: 100vh; }}
    aside {{ background: var(--panel); border-right: 1px solid var(--line); padding: 18px; overflow: auto; }}
    main {{ min-width: 0; padding: 18px; }}
    h1 {{ margin: 0; font-size: 22px; line-height: 1.2; letter-spacing: 0; }}
    h2 {{ margin: 20px 0 8px; font-size: 14px; }}
    p {{ margin: 8px 0 0; color: var(--muted); line-height: 1.45; font-size: 13px; }}
    .metrics {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }}
    .metric {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px; min-width: 0; }}
    .metric strong {{ display: block; font-size: 22px; line-height: 1; }}
    .metric span {{ display: block; margin-top: 5px; color: var(--muted); font-size: 11px; }}
    .legend {{ display: grid; gap: 8px; margin-top: 14px; font-size: 13px; }}
    .legend span::before {{ content: ""; display: inline-block; width: 28px; height: 5px; border-radius: 99px; margin-right: 8px; vertical-align: middle; }}
    .legend .main::before {{ background: var(--main); }}
    .legend .other::before {{ background: var(--other); }}
    .legend .road::before {{ background: var(--road); }}
    .map-card {{ background: #eef1f6; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; box-shadow: 0 14px 36px rgba(15, 23, 42, .08); }}
    svg {{ display: block; width: 100%; height: auto; }}
    .road {{ fill: none; stroke: var(--road); stroke-width: 1.2; stroke-linecap: round; stroke-linejoin: round; opacity: .42; }}
    .route {{ fill: none; stroke-width: 7.5; stroke-linecap: round; stroke-linejoin: round; }}
    .route.main {{ stroke: var(--main); }}
    .route.other {{ stroke: var(--other); stroke-width: 6; }}
    .halo {{ fill: none; stroke: #fff; stroke-width: 13; stroke-linecap: round; stroke-linejoin: round; opacity: .88; }}
    .marker {{ stroke: #fff; stroke-width: 4; }}
    .start {{ fill: var(--start); }}
    .target {{ fill: var(--target); }}
    .label {{ paint-order: stroke; stroke: #fff; stroke-width: 5px; stroke-linejoin: round; font-size: 24px; font-weight: 800; fill: #0f172a; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 6px 4px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 700; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; overflow-wrap: anywhere; }}
    .segments {{ max-height: 340px; overflow: auto; border: 1px solid var(--line); border-radius: 8px; }}
    @media (max-width: 920px) {{
      .layout {{ grid-template-columns: 1fr; }}
      aside {{ border-right: 0; border-bottom: 1px solid var(--line); }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <h1>{html.escape(route["route_id"])}</h1>
      <p>최종 실행 추천 경로 ER_ACC_006. 붉은 구간은 B04 메인도로 매핑에 포함된 edge, 주황 구간은 접근/이탈 및 기타 edge입니다.</p>
      <div class="metrics">
        <div class="metric"><strong>{route["route_length_m"]:.1f}m</strong><span>route length</span></div>
        <div class="metric"><strong>{main_pct:.1f}%</strong><span>mainroad ratio</span></div>
        <div class="metric"><strong>{route["route_edge_count"]}</strong><span>route edges</span></div>
        <div class="metric"><strong>{route["mainroad_length_m"]:.1f}m</strong><span>mainroad length</span></div>
      </div>
      <div class="legend">
        <span class="main">메인도로 구간</span>
        <span class="other">기타 경로 구간 ({other_pct:.1f}%)</span>
        <span class="road">주변 도로망</span>
      </div>
      <h2>Route</h2>
      <table>
        <tr><th>scenario</th><td><code>{html.escape(route["scenario_id"])}</code></td></tr>
        <tr><th>start</th><td><code>{html.escape(route["start_edge_id"])}</code></td></tr>
        <tr><th>target</th><td><code>{html.escape(route["target_edge_id"])}</code></td></tr>
        <tr><th>merge</th><td><code>{html.escape(route["merge_edge_id"])}</code></td></tr>
      </table>
      <h2>Sources</h2>
      <table>{source_rows}</table>
      <h2>Edges</h2>
      <div class="segments">
        <table>
          <thead><tr><th>#</th><th>edge</th><th>m</th><th>type</th></tr></thead>
          <tbody>{segment_rows}</tbody>
        </table>
      </div>
    </aside>
    <main>
      <div class="map-card">
        <svg viewBox="0 0 {width} {height}" role="img" aria-label="FINAL_DEST_ER_ACC_006 route map">
          <rect width="{width}" height="{height}" fill="#eef1f6"/>
          {context}
          {''.join(f'<polyline points="{points_attr(seg["shape"], box, width, height)}" class="halo" />' for seg in payload["segments"])}
          {route_lines}
          <circle class="marker start" cx="{sx:.1f}" cy="{sy:.1f}" r="14"><title>start</title></circle>
          <circle class="marker target" cx="{tx:.1f}" cy="{ty:.1f}" r="15"><title>target</title></circle>
          <text class="label" x="{sx + 18:.1f}" y="{sy - 18:.1f}">START</text>
          <text class="label" x="{tx + 18:.1f}" y="{ty - 18:.1f}">TARGET</text>
        </svg>
      </div>
    </main>
  </div>
  <script id="route-data" type="application/json">{data}</script>
</body>
</html>
"""


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def render_png(payload: dict[str, Any]) -> None:
    canvas_w, canvas_h = 1800, 1100
    panel_w = 380
    map_w, map_h = canvas_w - panel_w, canvas_h
    image = Image.new("RGB", (canvas_w, canvas_h), "#f7f8fb")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, panel_w, canvas_h), fill="#ffffff")
    draw.line((panel_w, 0, panel_w, canvas_h), fill="#d9dee8", width=2)
    draw.rectangle((panel_w, 0, canvas_w, canvas_h), fill="#eef1f6")

    route = payload["route"]
    box = payload["bounds"]

    def xy(point: tuple[float, float]) -> tuple[float, float]:
        x, y = project(point, box, map_w, map_h)
        return x + panel_w, y

    for edge in payload["context_edges"]:
        points = [xy(point) for point in edge["shape"]]
        if len(points) >= 2:
            draw.line(points, fill="#c9d1df", width=2, joint="curve")

    for seg in payload["segments"]:
        points = [xy(point) for point in seg["shape"]]
        if len(points) >= 2:
            draw.line(points, fill="#ffffff", width=16, joint="curve")
    for seg in payload["segments"]:
        points = [xy(point) for point in seg["shape"]]
        if len(points) >= 2:
            draw.line(points, fill="#e11d48" if seg["mainroad"] else "#f59e0b", width=9 if seg["mainroad"] else 7, joint="curve")

    start = xy(payload["segments"][0]["shape"][0])
    target = xy(payload["segments"][-1]["shape"][-1])
    draw.ellipse((start[0] - 15, start[1] - 15, start[0] + 15, start[1] + 15), fill="#059669", outline="#ffffff", width=5)
    draw.ellipse((target[0] - 16, target[1] - 16, target[0] + 16, target[1] + 16), fill="#2563eb", outline="#ffffff", width=5)
    label_font = font(28, bold=True)
    draw.text((start[0] + 22, start[1] - 42), "START", fill="#0f172a", font=label_font, stroke_width=4, stroke_fill="#ffffff")
    draw.text((target[0] + 22, target[1] - 42), "TARGET", fill="#0f172a", font=label_font, stroke_width=4, stroke_fill="#ffffff")

    title_font = font(24, bold=True)
    body_font = font(16)
    small_font = font(14)
    metric_font = font(34, bold=True)
    draw.text((22, 22), route["route_id"], fill="#17202f", font=title_font)
    draw.text((22, 58), "Recommended final execution route", fill="#596579", font=body_font)

    metrics = [
        (f"{route['route_length_m']:.1f}m", "route length"),
        (f"{route['mainroad_length_ratio'] * 100:.1f}%", "mainroad ratio"),
        (str(route["route_edge_count"]), "route edges"),
        (f"{route['mainroad_length_m']:.1f}m", "mainroad length"),
    ]
    y = 112
    for i, (value, label) in enumerate(metrics):
        x = 22 + (i % 2) * 170
        if i and i % 2 == 0:
            y += 108
        draw.rounded_rectangle((x, y, x + 150, y + 82), radius=8, fill="#f8fafc", outline="#d9dee8", width=1)
        draw.text((x + 12, y + 12), value, fill="#17202f", font=metric_font)
        draw.text((x + 12, y + 54), label, fill="#596579", font=small_font)

    y = 350
    legend = [("#e11d48", "mainroad segment"), ("#f59e0b", "other route segment"), ("#c9d1df", "surrounding roads")]
    for color, label in legend:
        draw.line((22, y + 10, 64, y + 10), fill=color, width=7)
        draw.text((78, y), label, fill="#17202f", font=body_font)
        y += 34

    y = 490
    rows = [
        ("scenario", route["scenario_id"]),
        ("start", route["start_edge_id"]),
        ("merge", route["merge_edge_id"]),
        ("target", route["target_edge_id"]),
    ]
    for key, value in rows:
        draw.text((22, y), key, fill="#596579", font=small_font)
        draw.text((118, y), str(value), fill="#17202f", font=small_font)
        y += 30

    draw.text((22, canvas_h - 44), "Source: Compact V9 S1-forced net / final route CSV", fill="#596579", font=small_font)
    image.save(OUTPUT_PNG)


def main() -> None:
    payload = build_payload()
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_HTML.write_text(render_html(payload), encoding="utf-8")
    render_png(payload)
    print(OUTPUT_HTML)
    print(OUTPUT_JSON)
    print(OUTPUT_PNG)


if __name__ == "__main__":
    main()
