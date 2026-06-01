"""Build Leaflet HTML maps."""

import json
from pathlib import Path
from typing import Any

from config import MAP_TILES, MAP_ATTRIBUTION, MAP_DEFAULT_CENTER, MAP_DEFAULT_ZOOM


def build_leaflet_html(
    title: str,
    geojson_data: dict[str, Any],
    output_path: Path,
    center: list[float] = MAP_DEFAULT_CENTER,
    zoom: int = MAP_DEFAULT_ZOOM,
    additional_js: str = "",
) -> None:
    """
    Build standalone Leaflet HTML file.
    
    Args:
        title: HTML page title
        geojson_data: GeoJSON FeatureCollection
        output_path: Output HTML file path
        center: Map center [lat, lon]
        zoom: Initial zoom level
        additional_js: Additional JavaScript code to include
    """
    geojson_str = json.dumps(geojson_data, ensure_ascii=False)
    
    html_content = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body {{ height: 100%; margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111827; }}
    .app {{ display: grid; grid-template-columns: 320px 1fr; height: 100vh; background: #f3f4f6; }}
    aside {{ overflow: auto; padding: 16px; border-right: 1px solid #d1d5db; background: #ffffff; }}
    #map {{ height: 100vh; min-height: 420px; background: #e5e7eb; }}
    h1 {{ font-size: 18px; line-height: 1.2; margin: 0 0 12px; font-weight: 700; }}
    h2 {{ font-size: 13px; margin: 16px 0 8px; font-weight: 600; color: #374151; }}
    p {{ margin: 0 0 10px; font-size: 12px; line-height: 1.5; }}
    .meta {{ color: #6b7280; font-size: 11px; }}
    .stat-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 8px; }}
    .stat {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 4px; padding: 6px; }}
    .stat-label {{ display: block; color: #6b7280; font-size: 10px; margin-bottom: 2px; }}
    .stat-value {{ display: block; font-size: 12px; font-weight: 600; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #e5e7eb; padding: 4px; }}
    th {{ color: #6b7280; font-weight: 600; }}
    .color-swatch {{ display: inline-block; width: 12px; height: 12px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
    @media (max-width: 900px) {{
      .app {{ grid-template-columns: 1fr; grid-template-rows: 50vh 50vh; }}
      aside {{ grid-row: 2; border-right: 0; border-top: 1px solid #d1d5db; }}
      #map {{ grid-row: 1; height: 50vh; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h1>{title}</h1>
      <div id="sidebar"></div>
    </aside>
    <main id="map"></main>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const GEOJSON_DATA = {geojson_str};
    
    const map = L.map('map', {{ preferCanvas: true }});
    L.tileLayer('{MAP_TILES}', {{
      maxZoom: 19,
      attribution: '{MAP_ATTRIBUTION}'
    }}).addTo(map);
    
    // Initialize map
    map.setView({center}, {zoom});
    
    // Add base layers and controls
    {additional_js}
  </script>
</body>
</html>
"""
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")
