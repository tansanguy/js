#!/usr/bin/env python3
"""Define Step 1 spatial reference files for the analysis area."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.geo_utils import (  # noqa: E402
    bbox_from_points,
    expand_bbox_m,
    geojson_feature,
    haversine_distance_m,
    initial_bearing_deg,
    midpoint_latlon,
    oriented_ellipse_polygon,
    validate_lat_lon,
)


CONFIG_PATH = PROJECT_ROOT / "config/map_config.yaml"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step01_define_area.log"


class Step01Error(RuntimeError):
    """Expected Step 1 failure with user-facing error text."""


def log_lines(lines: list[str]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def require_field(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for key in dotted_path.split("."):
        if not isinstance(current, dict) or key not in current:
            raise Step01Error(f"ERROR: missing required config field: {dotted_path}")
        current = current[key]
    return current


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        raise Step01Error(f"ERROR: config file not found: {CONFIG_PATH.relative_to(PROJECT_ROOT)}")

    try:
        import yaml
    except ImportError as exc:
        raise Step01Error(
            "ERROR: PyYAML is required to read config/map_config.yaml. "
            "Install it with: python3 -m pip install PyYAML"
        ) from exc

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise Step01Error("ERROR: config/map_config.yaml must contain a YAML mapping")
    return config


def require_number(config: dict[str, Any], dotted_path: str) -> float:
    value = require_field(config, dotted_path)
    if not isinstance(value, int | float):
        raise Step01Error(f"ERROR: {dotted_path} must be numeric")
    return float(value)


def require_int(config: dict[str, Any], dotted_path: str) -> int:
    value = require_field(config, dotted_path)
    if not isinstance(value, int):
        raise Step01Error(f"ERROR: {dotted_path} must be an integer")
    return value


def bbox_polygon_coordinates(bbox: dict[str, float]) -> list[list[list[float]]]:
    return [
        [
            [bbox["min_lon"], bbox["min_lat"]],
            [bbox["max_lon"], bbox["min_lat"]],
            [bbox["max_lon"], bbox["max_lat"]],
            [bbox["min_lon"], bbox["max_lat"]],
            [bbox["min_lon"], bbox["min_lat"]],
        ]
    ]


def lonlat_polygon(points: list[tuple[float, float]]) -> list[list[list[float]]]:
    return [[[lon, lat] for lat, lon in points]]


def point_coordinates(lat: float, lon: float) -> list[float]:
    return [lon, lat]


def validate_bbox_contains_points(bbox: dict[str, float], points: list[tuple[float, float]]) -> None:
    for lat, lon in points:
        if not (
            bbox["min_lat"] <= lat <= bbox["max_lat"]
            and bbox["min_lon"] <= lon <= bbox["max_lon"]
        ):
            raise Step01Error("ERROR: bbox polygon does not contain analysis ellipse")


def build_outputs(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    jungbu_name = require_field(config, "locations.jungbu_fire_station.name")
    seoul_name = require_field(config, "locations.seoul_station.name")
    jungbu_lat = require_number(config, "locations.jungbu_fire_station.lat")
    jungbu_lon = require_number(config, "locations.jungbu_fire_station.lon")
    seoul_lat = require_number(config, "locations.seoul_station.lat")
    seoul_lon = require_number(config, "locations.seoul_station.lon")

    try:
        jungbu_lat, jungbu_lon = validate_lat_lon(jungbu_lat, jungbu_lon, "jungbu_fire_station")
        seoul_lat, seoul_lon = validate_lat_lon(seoul_lat, seoul_lon, "seoul_station")
    except ValueError as exc:
        raise Step01Error(str(exc)) from exc

    shape = require_field(config, "analysis_area.shape")
    if shape != "ellipse":
        raise Step01Error(f"ERROR: unsupported analysis_area.shape: {shape}. Step 1 supports only ellipse.")

    ellipse_width_m = require_number(config, "analysis_area.ellipse_width_m")
    if ellipse_width_m <= 0:
        raise Step01Error("ERROR: analysis_area.ellipse_width_m must be greater than 0")

    bbox_buffer_m = require_number(config, "analysis_area.bbox_buffer_m")
    if bbox_buffer_m < 0:
        raise Step01Error("ERROR: analysis_area.bbox_buffer_m must be greater than or equal to 0")

    ellipse_num_points = require_int(config, "analysis_area.ellipse_num_points")
    if ellipse_num_points < 16:
        raise Step01Error("ERROR: analysis_area.ellipse_num_points must be greater than or equal to 16")

    meta_output = require_field(config, "outputs.analysis_area_meta")
    geojson_output = require_field(config, "outputs.analysis_area_geojson")

    major_axis_m = haversine_distance_m(jungbu_lat, jungbu_lon, seoul_lat, seoul_lon)
    if major_axis_m == 0:
        raise Step01Error("ERROR: reference points are identical; cannot define analysis axis")

    center_lat, center_lon = midpoint_latlon(jungbu_lat, jungbu_lon, seoul_lat, seoul_lon)
    semi_major_axis_m = major_axis_m / 2.0
    minor_axis_m = ellipse_width_m
    semi_minor_axis_m = ellipse_width_m / 2.0
    bearing_deg = initial_bearing_deg(jungbu_lat, jungbu_lon, seoul_lat, seoul_lon)

    ellipse_points = oriented_ellipse_polygon(
        center_lat=center_lat,
        center_lon=center_lon,
        semi_major_axis_m=semi_major_axis_m,
        semi_minor_axis_m=semi_minor_axis_m,
        bearing_deg=bearing_deg,
        num_points=ellipse_num_points,
    )
    bbox_wgs84 = expand_bbox_m(bbox_from_points(ellipse_points), bbox_buffer_m)
    validate_bbox_contains_points(bbox_wgs84, ellipse_points)

    locations = {
        "jungbu_fire_station": {
            "name": jungbu_name,
            "lat": jungbu_lat,
            "lon": jungbu_lon,
        },
        "seoul_station": {
            "name": seoul_name,
            "lat": seoul_lat,
            "lon": seoul_lon,
        },
    }
    outputs = {
        "analysis_area_meta": meta_output,
        "analysis_area_geojson": geojson_output,
        "net_file": require_field(config, "outputs.net_file"),
        "map_review_html": require_field(config, "outputs.map_review_html"),
    }

    meta = {
        "project_root": str(PROJECT_ROOT),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coordinate_crs": "EPSG:4326",
        "distance_method": "haversine_distance_m; local metric approximation for ellipse polygon",
        "locations": locations,
        "shape": shape,
        "major_axis_start": locations["jungbu_fire_station"],
        "major_axis_end": locations["seoul_station"],
        "major_axis_m": major_axis_m,
        "semi_major_axis_m": semi_major_axis_m,
        "minor_axis_m": minor_axis_m,
        "semi_minor_axis_m": semi_minor_axis_m,
        "ellipse_center": {
            "lat": center_lat,
            "lon": center_lon,
        },
        "bearing_deg": bearing_deg,
        "ellipse_width_m": ellipse_width_m,
        "bbox_buffer_m": bbox_buffer_m,
        "bbox_wgs84": bbox_wgs84,
        "outputs": outputs,
        "notes": [
            "Seoul Station is a reference point for analysis-area definition, not an emergency destination.",
            "The ellipse major axis is exactly the Jungbu Fire Station to Seoul Station segment.",
            "The OSM extraction bbox is a helper range expanded by bbox_buffer_m.",
            "Step 1 does not download OSM, build SUMO net.xml, select edges, or generate routes.",
        ],
    }

    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            geojson_feature(
                "Point",
                point_coordinates(jungbu_lat, jungbu_lon),
                {
                    "role": "jungbu_fire_station",
                    "name": jungbu_name,
                },
            ),
            geojson_feature(
                "Point",
                point_coordinates(seoul_lat, seoul_lon),
                {
                    "role": "seoul_station_reference",
                    "name": seoul_name,
                    "note": "Reference point for analysis area, not an incident destination.",
                },
            ),
            geojson_feature(
                "LineString",
                [
                    point_coordinates(jungbu_lat, jungbu_lon),
                    point_coordinates(seoul_lat, seoul_lon),
                ],
                {
                    "role": "analysis_axis",
                    "name": "Jungbu Fire Station to Seoul Station axis",
                    "major_axis_m": major_axis_m,
                    "bearing_deg": bearing_deg,
                },
            ),
            geojson_feature(
                "Polygon",
                lonlat_polygon(ellipse_points),
                {
                    "role": "analysis_ellipse",
                    "name": "Core analysis ellipse",
                    "major_axis_m": major_axis_m,
                    "minor_axis_m": minor_axis_m,
                    "bearing_deg": bearing_deg,
                },
            ),
            geojson_feature(
                "Polygon",
                bbox_polygon_coordinates(bbox_wgs84),
                {
                    "role": "osm_extract_bbox",
                    "name": "OSM extraction bbox",
                    "bbox_buffer_m": bbox_buffer_m,
                },
            ),
        ],
    }

    return meta, feature_collection


def write_json_file(path_text: str, payload: dict[str, Any]) -> None:
    path = PROJECT_ROOT / path_text
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise Step01Error(f"ERROR: failed to write output file: {path_text}") from exc


def main() -> int:
    lines: list[str] = [
        "Step 1 analysis area definition",
        "===============================",
        f"Project root: {PROJECT_ROOT}",
    ]

    try:
        config = load_config()
        meta, feature_collection = build_outputs(config)
        write_json_file(meta["outputs"]["analysis_area_meta"], meta)
        write_json_file(meta["outputs"]["analysis_area_geojson"], feature_collection)
        lines.extend(
            [
                "Status: PASS",
                f"Major axis m: {meta['major_axis_m']:.3f}",
                f"Minor axis m: {meta['minor_axis_m']:.3f}",
                f"Bearing deg: {meta['bearing_deg']:.6f}",
                f"BBox WGS84: {meta['bbox_wgs84']}",
                f"Wrote: {meta['outputs']['analysis_area_meta']}",
                f"Wrote: {meta['outputs']['analysis_area_geojson']}",
            ]
        )
        log_lines(lines)
        print("\n".join(lines))
        return 0
    except Step01Error as exc:
        lines.extend(["Status: FAIL", str(exc)])
        log_lines(lines)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
