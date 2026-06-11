#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
import sumolib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NET_FILE = PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
MAINROAD_AUDIT = PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_signal/mainroad_lane_recall_audit.csv"
MAINROAD_MAPPING = PROJECT_ROOT / "data_prepared/compact_v9/map/B04_toegye_segment_edge_mapping.csv"
OUTPUT_HTML = PROJECT_ROOT / "results/html/compact_v9_sumo_map_mainroad_realmap.html"
OUTPUT_JSON = PROJECT_ROOT / "results/html/compact_v9_sumo_map_mainroad_realmap.json"
OUTPUT_PNG = PROJECT_ROOT / "results/html/compact_v9_sumo_map_mainroad_realmap.png"
TILE_CACHE_DIR = PROJECT_ROOT / "results/html/osm_tile_cache"

LIGHT_BLUE = "#4fb0dd"
DARK_NAVY = "#063a73"
TILE_URL = "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"
TILE_USER_AGENT = "compact-v9-sumo-mainroad-map/1.0 (local research visualization)"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_shape(text: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in (text or "").split():
        if "," not in item:
            continue
        x, y = item.split(",", 1)
        points.append((float(x), float(y)))
    return points


def edge_shape_xy(edge: ET.Element) -> list[tuple[float, float]]:
    shape = parse_shape(edge.get("shape", ""))
    if len(shape) >= 2:
        return shape
    lane = edge.find("lane")
    if lane is None:
        return []
    return parse_shape(lane.get("shape", ""))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_mainroad_edges() -> set[str]:
    mainroad_edges: set[str] = set()
    if MAINROAD_AUDIT.is_file():
        for row in read_csv(MAINROAD_AUDIT):
            edge_id = row.get("edge_id", "")
            if edge_id and "mainroad" in row.get("edge_role", ""):
                mainroad_edges.add(edge_id)
    if MAINROAD_MAPPING.is_file():
        for row in read_csv(MAINROAD_MAPPING):
            edge_id = row.get("edge_id", "")
            if edge_id:
                mainroad_edges.add(edge_id)
    return mainroad_edges


def load_edges() -> list[dict[str, Any]]:
    sumo_net = sumolib.net.readNet(str(NET_FILE))
    root = ET.parse(NET_FILE).getroot()
    mainroad = load_mainroad_edges()
    edges: list[dict[str, Any]] = []
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.get("function") == "internal":
            continue
        shape_xy = edge_shape_xy(edge)
        if len(shape_xy) < 2:
            continue
        coords: list[list[float]] = []
        for x, y in shape_xy:
            lon, lat = sumo_net.convertXY2LonLat(float(x), float(y))
            coords.append([round(float(lat), 8), round(float(lon), 8)])
        lane = edge.find("lane")
        edges.append(
            {
                "edge_id": edge_id,
                "coords": coords,
                "lane_count": len(edge.findall("lane")),
                "length_m": safe_float(lane.get("length") if lane is not None else edge.get("length")),
                "is_mainroad": edge_id in mainroad,
            }
        )
    return edges


def bounds(edges: list[dict[str, Any]], pad_ratio: float = 0.035) -> dict[str, float]:
    lats = [lat for edge in edges for lat, _ in edge["coords"]]
    lons = [lon for edge in edges for _, lon in edge["coords"]]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    d_lat = max(max_lat - min_lat, 0.001)
    d_lon = max(max_lon - min_lon, 0.001)
    return {
        "min_lat": min_lat - d_lat * pad_ratio,
        "max_lat": max_lat + d_lat * pad_ratio,
        "min_lon": min_lon - d_lon * pad_ratio,
        "max_lon": max_lon + d_lon * pad_ratio,
        "center_lat": (min_lat + max_lat) / 2.0,
        "center_lon": (min_lon + max_lon) / 2.0,
    }


def latlon_to_global_px(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    lat_rad = math.radians(max(min(lat, 85.05112878), -85.05112878))
    scale = 256 * (2**zoom)
    x = (lon + 180.0) / 360.0 * scale
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * scale
    return x, y


def tile_path(zoom: int, x: int, y: int) -> Path:
    return TILE_CACHE_DIR / str(zoom) / str(x) / f"{y}.png"


def fetch_tile(zoom: int, x: int, y: int) -> Image.Image:
    path = tile_path(zoom, x, y)
    if path.is_file():
        return Image.open(path).convert("RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        TILE_URL.format(z=zoom, x=x, y=y),
        headers={"User-Agent": TILE_USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read()
    path.write_bytes(data)
    time.sleep(0.04)
    return Image.open(BytesIO(data)).convert("RGB")


def render_static_png(payload: dict[str, Any], zoom: int = 15, output_size: tuple[int, int] = (2400, 1700)) -> None:
    box = payload["bounds"]
    nw_x, nw_y = latlon_to_global_px(box["max_lat"], box["min_lon"], zoom)
    se_x, se_y = latlon_to_global_px(box["min_lat"], box["max_lon"], zoom)
    min_tile_x = math.floor(nw_x / 256)
    max_tile_x = math.floor(se_x / 256)
    min_tile_y = math.floor(nw_y / 256)
    max_tile_y = math.floor(se_y / 256)
    mosaic = Image.new("RGB", ((max_tile_x - min_tile_x + 1) * 256, (max_tile_y - min_tile_y + 1) * 256), "#eef2f4")
    for tile_x in range(min_tile_x, max_tile_x + 1):
        for tile_y in range(min_tile_y, max_tile_y + 1):
            try:
                tile = fetch_tile(zoom, tile_x, tile_y)
            except Exception:
                tile = Image.new("RGB", (256, 256), "#e5ebef")
            mosaic.paste(tile, ((tile_x - min_tile_x) * 256, (tile_y - min_tile_y) * 256))

    crop_left = int(round(nw_x - min_tile_x * 256))
    crop_top = int(round(nw_y - min_tile_y * 256))
    crop_right = int(round(se_x - min_tile_x * 256))
    crop_bottom = int(round(se_y - min_tile_y * 256))
    base = mosaic.crop((crop_left, crop_top, crop_right, crop_bottom)).resize(output_size, Image.Resampling.LANCZOS).convert("RGBA")
    draw = ImageDraw.Draw(base)
    crop_w = max(crop_right - crop_left, 1)
    crop_h = max(crop_bottom - crop_top, 1)
    sx = output_size[0] / crop_w
    sy = output_size[1] / crop_h

    def project(lat: float, lon: float) -> tuple[float, float]:
        x, y = latlon_to_global_px(lat, lon, zoom)
        return (x - nw_x) * sx, (y - nw_y) * sy

    for edge in payload["edges"]:
        if edge["is_mainroad"]:
            continue
        points = [project(lat, lon) for lat, lon in edge["coords"]]
        draw.line(points, fill=(79, 176, 221, 225), width=4, joint="curve")

    for edge in payload["edges"]:
        if not edge["is_mainroad"]:
            continue
        points = [project(lat, lon) for lat, lon in edge["coords"]]
        draw.line(points, fill=(255, 255, 255, 230), width=12, joint="curve")
    for edge in payload["edges"]:
        if not edge["is_mainroad"]:
            continue
        points = [project(lat, lon) for lat, lon in edge["coords"]]
        draw.line(points, fill=(6, 58, 115, 255), width=7, joint="curve")

    OUTPUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(OUTPUT_PNG, quality=95)


def render_html(payload: dict[str, Any]) -> str:
    edges_json = json.dumps(payload["edges"], ensure_ascii=False).replace("</", "<\\/")
    summary_json = json.dumps({k: v for k, v in payload.items() if k != "edges"}, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Compact V9 SUMO Map on CARTO Positron</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body {{ height: 100%; margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #0e1d30; }}
    .app {{ height: 100vh; background: #f5f8fb; }}
    #map {{ height: 100vh; min-height: 520px; }}
    @media (max-width: 900px) {{
      #map {{ height: 100vh; min-height: 0; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <main id="map"></main>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const EDGES = {edges_json};
    const SUMMARY = {summary_json};
    const map = L.map("map", {{ preferCanvas: true, zoomSnap: 0.25 }});
    L.tileLayer("https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png", {{
      subdomains: "abcd",
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
    }}).addTo(map);
    const bounds = L.latLngBounds(
      [SUMMARY.bounds.min_lat, SUMMARY.bounds.min_lon],
      [SUMMARY.bounds.max_lat, SUMMARY.bounds.max_lon]
    );
    map.fitBounds(bounds, {{ padding: [26, 26] }});
    const otherGroup = L.layerGroup().addTo(map);
    const mainHalo = L.layerGroup().addTo(map);
    const mainGroup = L.layerGroup().addTo(map);
    for (const edge of EDGES) {{
      const target = edge.is_mainroad ? mainGroup : otherGroup;
      if (edge.is_mainroad) {{
        L.polyline(edge.coords, {{
          color: "#ffffff",
          weight: 11,
          opacity: 0.82,
          interactive: false
        }}).addTo(mainHalo);
      }}
      L.polyline(edge.coords, {{
        color: edge.is_mainroad ? "{DARK_NAVY}" : "{LIGHT_BLUE}",
        weight: edge.is_mainroad ? 6.8 : 3.1,
        opacity: edge.is_mainroad ? 0.98 : 0.9,
        lineCap: "round",
        lineJoin: "round"
      }}).bindTooltip(`${{edge.edge_id}} · ${{edge.is_mainroad ? "mainroad" : "other"}}`).addTo(target);
    }}
  </script>
</body>
</html>
"""


def main() -> int:
    edges = load_edges()
    payload = {
        "schema": "compact_v9_sumo_map_mainroad_realmap.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bounds": bounds(edges),
        "edge_count": len(edges),
        "mainroad_edge_count": sum(1 for edge in edges if edge["is_mainroad"]),
        "other_edge_count": sum(1 for edge in edges if not edge["is_mainroad"]),
        "colors": {"mainroad": DARK_NAVY, "other": LIGHT_BLUE},
        "sources": {
            "net_file": NET_FILE.relative_to(PROJECT_ROOT).as_posix(),
            "mainroad_audit": MAINROAD_AUDIT.relative_to(PROJECT_ROOT).as_posix(),
            "mainroad_mapping": MAINROAD_MAPPING.relative_to(PROJECT_ROOT).as_posix(),
            "basemap": "CARTO Positron light_all tile layer",
        },
        "edges": edges,
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_HTML.write_text(render_html(payload), encoding="utf-8")
    render_static_png(payload)
    print(
        json.dumps(
            {
                "png": str(OUTPUT_PNG),
                "html": str(OUTPUT_HTML),
                "json": str(OUTPUT_JSON),
                "edge_count": payload["edge_count"],
                "mainroad_edge_count": payload["mainroad_edge_count"],
                "other_edge_count": payload["other_edge_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
