#!/usr/bin/env python3
"""Build a presentation-grade Leaflet road network visual."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ROAD_GEOJSON = ROOT / "data_prepared/geojson/ellipse_passenger_edges.geojson"
OUTPUT_HTML = Path(__file__).with_name("seoul_leaflet_road_network.html")


def rounded_geometry(geometry: dict) -> dict:
    def round_coords(value):
        if isinstance(value, list) and value and isinstance(value[0], (int, float)):
            return [round(float(value[0]), 6), round(float(value[1]), 6)]
        if isinstance(value, list):
            return [round_coords(item) for item in value]
        return value

    return {
        "type": geometry["type"],
        "coordinates": round_coords(geometry["coordinates"]),
    }


def road_class(priority: int) -> str:
    if priority >= 10:
        return "arterial"
    if priority >= 7:
        return "main"
    if priority >= 5:
        return "collector"
    return "local"


def load_roads() -> tuple[dict, dict]:
    source = json.loads(ROAD_GEOJSON.read_text(encoding="utf-8"))
    features = []
    class_counts: Counter[str] = Counter()
    lane_counts: Counter[int] = Counter()
    total_length_m = 0.0

    for feature in source["features"]:
        props = feature.get("properties") or {}
        if props.get("is_internal") or props.get("allows_passenger") is False:
            continue

        priority = int(props.get("priority") or 0)
        lane_count = int(props.get("lane_count") or 1)
        length_m = float(props.get("length_m") or 0)
        cls = road_class(priority)
        class_counts[cls] += 1
        lane_counts[lane_count] += 1
        total_length_m += length_m

        features.append(
            {
                "type": "Feature",
                "geometry": rounded_geometry(feature["geometry"]),
                "properties": {
                    "id": props.get("edge_id"),
                    "priority": priority,
                    "class": cls,
                    "lanes": lane_count,
                    "speedKmh": round(float(props.get("speed_mps") or 0) * 3.6, 1),
                    "lengthM": round(length_m, 1),
                },
            }
        )

    summary = {
        "edgeCount": len(features),
        "totalKm": round(total_length_m / 1000, 1),
        "arterialCount": class_counts["arterial"],
        "mainCount": class_counts["main"],
        "collectorCount": class_counts["collector"],
        "localCount": class_counts["local"],
        "maxLaneCount": max(lane_counts) if lane_counts else 0,
        "source": "OpenStreetMap-derived SUMO network",
    }

    return {"type": "FeatureCollection", "features": features}, summary


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_html(roads: dict, summary: dict) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>서울 도심 실제 지도 및 도로망</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    :root {{
      color-scheme: light;
      --arterial: #ef5a3c;
      --main: #1b6fd7;
      --collector: #00a887;
      --local: #7c8797;
      --focus: #f6c343;
    }}

    * {{ box-sizing: border-box; }}

    html,
    body {{
      width: 100%;
      height: 100%;
      margin: 0;
      overflow: hidden;
      background: #eef2f6;
    }}

    #stage {{
      position: relative;
      width: 100vw;
      height: 100vh;
      min-height: 620px;
      isolation: isolate;
    }}

    #map {{
      position: absolute;
      inset: 0;
      background: #dfe7ee;
    }}
  </style>
</head>
<body>
  <div id="stage">
    <div id="map"></div>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const ROAD_DATA = {dumps(roads)};
    const classStyle = {{
      arterial: {{ color: "#ef5a3c", weight: 4.6, opacity: 0.92 }},
      main: {{ color: "#1b6fd7", weight: 3.5, opacity: 0.84 }},
      collector: {{ color: "#00a887", weight: 2.7, opacity: 0.72 }},
      local: {{ color: "#7c8797", weight: 1.25, opacity: 0.42 }}
    }};

    const map = L.map("map", {{
      zoomControl: false,
      attributionControl: false,
      preferCanvas: true,
      maxBoundsViscosity: 0.15
    }});

    L.tileLayer("https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png", {{
      subdomains: "abcd",
      maxZoom: 20,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
    }}).addTo(map);

    map.createPane("roadPane");
    map.getPane("roadPane").style.zIndex = 430;

    const roadLayer = L.geoJSON(ROAD_DATA, {{
      pane: "roadPane",
      style: (feature) => {{
        const cls = feature.properties.class || "local";
        const base = classStyle[cls] || classStyle.local;
        const lanes = Number(feature.properties.lanes || 1);
        return {{
          ...base,
          weight: base.weight + Math.min(lanes - 1, 3) * 0.35,
          lineCap: "round",
          lineJoin: "round"
        }};
      }}
    }}).addTo(map);

    const bounds = roadLayer.getBounds();
    map.fitBounds(bounds, {{ padding: [18, 18], maxZoom: 15 }});
    map.setMaxBounds(bounds.pad(0.35));

    setTimeout(() => {{
      map.invalidateSize();
      map.fitBounds(bounds, {{ padding: [18, 18], maxZoom: 15 }});
    }}, 150);
  </script>
</body>
</html>
"""


def main() -> None:
    roads, summary = load_roads()
    OUTPUT_HTML.write_text(build_html(roads, summary), encoding="utf-8")
    print(f"Wrote {OUTPUT_HTML}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
