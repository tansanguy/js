"""Load SUMO road/lane geometry near the route for the animation basemap.

The network edges (with ``lane_count``) already exist as a geojson
(``ellipse_passenger_edges.geojson``, LineString in lon/lat). We keep only the
edges near the emergency route and attach them to the animation document so the
follow-camera maps can draw the actual roads/lanes under the vehicles (line
width scales with lane count).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Per-EDGE centerlines (one line per edge). Per-LANE shapes (one line per lane,
# so a 2-lane road draws as two parallel lines) are preferred for the animation.
DEFAULT_EDGES_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_edges.geojson"
DEFAULT_LANES_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_lanes.geojson"


def _meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * 111320.0
    dlon = (lon2 - lon1) * 111320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


def load_road_lanes(
    geojson_path: Path,
    route_polylines: list[list[list[float]]],
    buffer_m: float = 200.0,
    sample_every: int = 8,
) -> list[dict[str, Any]]:
    """Return ``[{"coords": [[lat,lon],...], "lanes": n}]`` for edges near the route.

    ``route_polylines`` are lists of ``[lat, lon]`` points (one per mode). Edges
    whose geometry comes within ``buffer_m`` of any sampled route point are kept;
    internal junction edges are dropped.
    """
    data = json.loads(Path(geojson_path).read_text(encoding="utf-8"))

    route_pts: list[tuple[float, float]] = []
    for pl in route_polylines:
        for i, pt in enumerate(pl):
            if i % sample_every == 0:
                route_pts.append((pt[0], pt[1]))
    if not route_pts:
        return []

    lats = [p[0] for p in route_pts]
    lons = [p[1] for p in route_pts]
    dlat = buffer_m / 111320.0
    mid_lat = (min(lats) + max(lats)) / 2
    dlon = buffer_m / (111320.0 * math.cos(math.radians(mid_lat)))
    bb = (min(lats) - dlat, max(lats) + dlat, min(lons) - dlon, max(lons) + dlon)

    def in_bbox(lat: float, lon: float) -> bool:
        return bb[0] <= lat <= bb[1] and bb[2] <= lon <= bb[3]

    lanes: list[dict[str, Any]] = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        if props.get("is_internal"):
            continue
        geom = feat.get("geometry", {})
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates", [])  # [[lon, lat], ...]
        keep = False
        for lon, lat in coords:
            if not in_bbox(lat, lon):
                continue
            for rla, rlo in route_pts:
                if _meters(lat, lon, rla, rlo) <= buffer_m:
                    keep = True
                    break
            if keep:
                break
        if keep:
            lanes.append({
                "coords": [[round(lat, 6), round(lon, 6)] for lon, lat in coords],
                "lanes": int(props.get("lane_count") or 1),
            })
    return lanes


def augment_doc_with_lanes(
    doc: dict[str, Any],
    geojson_path: Path = DEFAULT_LANES_GEOJSON,
    buffer_m: float = 200.0,
) -> dict[str, Any]:
    """Attach ``doc["lanes"]`` (road geometry near both modes' routes)."""
    polylines = [m["route_polyline"] for m in doc.get("modes", {}).values() if m.get("route_polyline")]
    lanes = load_road_lanes(Path(geojson_path), polylines, buffer_m=buffer_m)
    doc["lanes"] = lanes
    return {"lanes_kept": len(lanes), "source": Path(geojson_path).name}
