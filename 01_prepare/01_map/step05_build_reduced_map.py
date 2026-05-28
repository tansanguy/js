#!/usr/bin/env python3
"""Build a reduced passenger-focused SUMO map from the existing OSM source."""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.html_utils import render_map_review_html, write_text  # noqa: E402
from common.net_utils import (  # noqa: E402
    count_net_elements,
    extract_edge_feature,
    extract_tls_features,
    file_size_mb,
    find_executable,
    load_json,
    read_sumo_net,
    run_netconvert,
    sha256_file,
    summarize_warnings,
    sumo_version,
    validate_feature_collection,
    validate_osm_xml,
    validate_sumo_net_xml,
    write_geojson,
    write_json,
)


CONFIG_PATH = PROJECT_ROOT / "config/map_config.yaml"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step05_build_reduced_map.log"
BLUE_EDGE_RULE = "!is_internal && allows_passenger === true"
EARTH_RADIUS_M = 6_371_008.8


class Step05Error(RuntimeError):
    """Expected Step 5 failure with user-facing error text."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_lines(lines: list[str]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def require_field(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for key in dotted_path.split("."):
        if not isinstance(current, dict) or key not in current:
            raise Step05Error(f"ERROR: missing required config field: {dotted_path}")
        current = current[key]
    return current


def load_yaml_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        raise Step05Error(f"ERROR: config file not found: {CONFIG_PATH.relative_to(PROJECT_ROOT)}")
    try:
        import yaml
    except ImportError as exc:
        raise Step05Error(
            "ERROR: PyYAML is required to read config/map_config.yaml. "
            "Install it with: python3 -m pip install PyYAML"
        ) from exc

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise Step05Error("ERROR: config/map_config.yaml must contain a YAML mapping")
    return config


def rel_path(path_text: str) -> Path:
    return PROJECT_ROOT / path_text


def relative_from_html(html_path: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path, start=html_path.parent)).as_posix()


def escape_help_option(option_name: str) -> str:
    return option_name.replace(".", r"\.")


def check_netconvert_boundary_help() -> dict[str, Any]:
    netconvert = find_executable("netconvert")
    import subprocess

    completed = subprocess.run(
        [netconvert, "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    help_text = completed.stdout or completed.stderr
    expected = "polygon <lon0,lat0,lon1,lat1,...>"
    return {
        "netconvert": netconvert,
        "return_code": completed.returncode,
        "has_in_geo_boundary": "--keep-edges.in-geo-boundary" in help_text,
        "has_expected_polygon_format": expected in help_text,
        "expected_polygon_format": expected,
    }


def lonlat_to_xy(lon: float, lat: float, origin_lon: float, origin_lat: float) -> tuple[float, float]:
    lat0_rad = math.radians(origin_lat)
    x = math.radians(lon - origin_lon) * EARTH_RADIUS_M * math.cos(lat0_rad)
    y = math.radians(lat - origin_lat) * EARTH_RADIUS_M
    return x, y


def xy_to_lonlat(x: float, y: float, origin_lon: float, origin_lat: float) -> tuple[float, float]:
    lat0_rad = math.radians(origin_lat)
    lon = origin_lon + math.degrees(x / (EARTH_RADIUS_M * math.cos(lat0_rad)))
    lat = origin_lat + math.degrees(y / EARTH_RADIUS_M)
    return lon, lat


def build_foci_ellipse(meta: dict[str, Any], num_points: int) -> dict[str, Any]:
    locations = meta.get("locations", {})
    jungbu = locations.get("jungbu_fire_station", {})
    seoul = locations.get("seoul_station", {})
    try:
        f1 = {"name": jungbu["name"], "lon": float(jungbu["lon"]), "lat": float(jungbu["lat"])}
        f2 = {"name": seoul["name"], "lon": float(seoul["lon"]), "lat": float(seoul["lat"])}
        minor_axis_m = float(meta["ellipse_width_m"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Step05Error("ERROR: invalid analysis_area_meta.json foci/ellipse_width_m") from exc

    center_lon = (f1["lon"] + f2["lon"]) / 2.0
    center_lat = (f1["lat"] + f2["lat"]) / 2.0
    f1x, f1y = lonlat_to_xy(f1["lon"], f1["lat"], center_lon, center_lat)
    f2x, f2y = lonlat_to_xy(f2["lon"], f2["lat"], center_lon, center_lat)
    dx = f1x - f2x
    dy = f1y - f2y
    focus_distance_m = math.hypot(dx, dy)
    c = focus_distance_m / 2.0
    b = minor_axis_m / 2.0
    a = math.sqrt(c * c + b * b)
    angle = math.atan2(f1y - f2y, f1x - f2x)

    coords: list[list[float]] = []
    for index in range(num_points):
        t = 2.0 * math.pi * index / num_points
        local_x = a * math.cos(t)
        local_y = b * math.sin(t)
        x = local_x * math.cos(angle) - local_y * math.sin(angle)
        y = local_x * math.sin(angle) + local_y * math.cos(angle)
        lon, lat = xy_to_lonlat(x, y, center_lon, center_lat)
        coords.append([lon, lat])
    coords.append(coords[0])

    boundary = ",".join(f"{lon:.8f},{lat:.8f}" for lon, lat in coords[:-1])
    bbox = {
        "min_lon": min(lon for lon, _lat in coords),
        "min_lat": min(lat for _lon, lat in coords),
        "max_lon": max(lon for lon, _lat in coords),
        "max_lat": max(lat for _lon, lat in coords),
    }
    return {
        "type": "foci_ellipse",
        "focus_a": f1,
        "focus_b": f2,
        "center": {"lon": center_lon, "lat": center_lat},
        "focus_distance_m": focus_distance_m,
        "semi_major_axis_m": a,
        "semi_minor_axis_m": b,
        "minor_axis_m": minor_axis_m,
        "point_count": num_points,
        "coordinates": coords,
        "netconvert_geo_boundary": boundary,
        "bbox_wgs84": bbox,
    }


def build_reduced_netconvert_command(osm_file: Path, net_file: Path, boundary: str) -> list[str]:
    return [
        find_executable("netconvert"),
        "--osm-files",
        str(osm_file),
        "--output-file",
        str(net_file),
        "--tls.guess",
        "true",
        "--tls.join",
        "true",
        "--junctions.join",
        "true",
        "--geometry.remove",
        "true",
        "--remove-edges.isolated",
        "true",
        "--no-turnarounds",
        "true",
        "--keep-edges.by-vclass",
        "passenger",
        "--keep-edges.in-geo-boundary",
        boundary,
    ]


def warnings_summary(stderr_text: str) -> dict[str, Any]:
    warning_lines = [
        line.strip()
        for line in stderr_text.splitlines()
        if "warning" in line.lower()
    ]
    return {"warning_count": len(warning_lines), "sample": warning_lines[:50]}


def count_original_blue_edges(edges_geojson: Path) -> dict[str, int]:
    data = load_json(edges_geojson)
    features = data.get("features", [])
    if not isinstance(features, list):
        raise Step05Error(f"ERROR: invalid features list: {edges_geojson.relative_to(PROJECT_ROOT)}")

    total = len(features)
    internal = 0
    passenger = 0
    blue = 0
    for feature in features:
        props = feature.get("properties", {}) if isinstance(feature, dict) else {}
        is_internal = bool(props.get("is_internal"))
        allows_passenger = props.get("allows_passenger") is True
        if is_internal:
            internal += 1
        if allows_passenger:
            passenger += 1
        if allows_passenger and not is_internal:
            blue += 1
    return {
        "feature_count": total,
        "internal_edge_count": internal,
        "passenger_allowed_edge_count": passenger,
        "blue_edge_rule_count": blue,
    }


def export_reduced_geojson(
    net_file: Path,
    edges_geojson: Path,
    tls_geojson: Path,
    audit_path: Path,
    bbox_wgs84: dict[str, float],
    original_tls_count: int,
) -> dict[str, Any]:
    sumo_net = read_sumo_net(net_file)
    edge_features: list[dict[str, Any]] = []
    edge_warnings: list[str] = []
    stats = {
        "skipped_edge_count": 0,
        "internal_edge_count": 0,
        "passenger_allowed_edge_count": 0,
        "blue_edge_rule_count": 0,
        "emergency_candidate_edge_count": 0,
    }

    for edge in sumo_net.getEdges(withInternal=True):
        try:
            feature = extract_edge_feature(sumo_net, edge)
        except ValueError as exc:
            edge_warnings.append(str(exc))
            stats["skipped_edge_count"] += 1
            continue
        props = feature["properties"]
        if props["is_internal"]:
            stats["internal_edge_count"] += 1
        if props["allows_passenger"]:
            stats["passenger_allowed_edge_count"] += 1
        if props["allows_passenger"] and not props["is_internal"]:
            stats["blue_edge_rule_count"] += 1
        if props["allows_emergency_candidate"]:
            stats["emergency_candidate_edge_count"] += 1
        edge_features.append(feature)

    tls_features, tls_warnings, skipped_tls_count = extract_tls_features(
        sumo_net=sumo_net,
        net_xml_path=net_file,
        bbox_wgs84=bbox_wgs84,
    )
    if not edge_features:
        raise Step05Error("ERROR: reduced edge Feature count is 0")
    if not tls_features:
        raise Step05Error("ERROR: reduced TLS Feature count is 0")

    write_geojson(edges_geojson, edge_features)
    write_geojson(tls_geojson, tls_features)
    validate_feature_collection(edges_geojson)
    validate_feature_collection(tls_geojson)

    warnings = edge_warnings + tls_warnings
    audit = {
        "generated_at": utc_now(),
        "input_net_file": str(net_file.relative_to(PROJECT_ROOT)),
        "output_edges_geojson": str(edges_geojson.relative_to(PROJECT_ROOT)),
        "output_tls_geojson": str(tls_geojson.relative_to(PROJECT_ROOT)),
        "edge_feature_count": len(edge_features),
        "tls_feature_count": len(tls_features),
        "skipped_edge_count": stats["skipped_edge_count"],
        "skipped_tls_count": skipped_tls_count,
        "internal_edge_count": stats["internal_edge_count"],
        "passenger_allowed_edge_count": stats["passenger_allowed_edge_count"],
        "blue_edge_rule": BLUE_EDGE_RULE,
        "blue_edge_rule_count": stats["blue_edge_rule_count"],
        "emergency_candidate_edge_count": stats["emergency_candidate_edge_count"],
        "original_tls_count": original_tls_count,
        "tls_count_difference_from_original": len(tls_features) - original_tls_count,
        "coordinate_conversion_method": "sumolib.net.convertXY2LonLat",
        "warnings_summary": summarize_warnings(warnings),
    }
    write_json(audit_path, audit)
    return audit


def reduction_ratio(original: int, reduced: int) -> float | None:
    if original <= 0:
        return None
    return round((original - reduced) / original, 6)


def build_review_html(
    html_path: Path,
    analysis_path: Path,
    edges_path: Path,
    tls_path: Path,
    edge_count: int,
    tls_count: int,
    center: list[float],
    zoom: int,
    geojson_audit: dict[str, Any],
) -> None:
    context = {
        "generated_at": utc_now(),
        "review_html_path": str(html_path.relative_to(PROJECT_ROOT)),
        "localhost_url": f"http://localhost:8000/{html_path.relative_to(PROJECT_ROOT).as_posix()}",
        "initial_center": center,
        "initial_zoom": zoom,
        "counts": {
            "analysis_feature_count": len(load_json(analysis_path).get("features", [])),
            "edge_feature_count": edge_count,
            "tls_feature_count": tls_count,
        },
        "step03_audit_summary": {
            "edge_feature_count": geojson_audit.get("edge_feature_count"),
            "tls_feature_count": geojson_audit.get("tls_feature_count"),
            "warnings": geojson_audit.get("warnings_summary", {}).get("warning_count"),
        },
        "paths": {
            "analysis_area_geojson": relative_from_html(html_path, analysis_path),
            "sumo_edges_geojson": relative_from_html(html_path, edges_path),
            "sumo_tls_geojson": relative_from_html(html_path, tls_path),
        },
    }
    write_text(html_path, render_map_review_html(context))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build reduced Step 5 SUMO map from existing OSM.")
    parser.add_argument("--dry-run", action="store_true", help="Print command and audit preview only.")
    parser.add_argument("--ellipse-points", type=int, default=128, help="Foci ellipse polygon point count.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    lines = [
        "Step 5 reduced passenger SUMO map build",
        "=======================================",
        f"Project root: {PROJECT_ROOT}",
        "Policy: reuse existing OSM; do not download OSM; do not modify original net.xml.",
        f"blue_edge_rule: {BLUE_EDGE_RULE}",
    ]

    try:
        config = load_yaml_config()
        osm_file = rel_path(require_field(config, "outputs.osm_file"))
        original_net_file = rel_path(require_field(config, "outputs.net_file"))
        original_net_audit = rel_path(require_field(config, "outputs.net_audit"))
        original_edges_geojson = rel_path(require_field(config, "outputs.sumo_edges_geojson"))
        analysis_geojson = rel_path(require_field(config, "outputs.analysis_area_geojson"))
        meta_path = rel_path(require_field(config, "outputs.analysis_area_meta"))
        reduced_net_file = rel_path(require_field(config, "outputs.reduced_net_file"))
        reduced_net_audit = rel_path(require_field(config, "outputs.reduced_net_audit"))
        reduced_manifest = rel_path(require_field(config, "outputs.reduced_map_manifest"))
        reduced_edges_geojson = rel_path(require_field(config, "outputs.reduced_edges_geojson"))
        reduced_tls_geojson = rel_path(require_field(config, "outputs.reduced_tls_geojson"))
        reduced_geojson_audit = rel_path(require_field(config, "outputs.reduced_geojson_audit"))
        reduced_html = rel_path(require_field(config, "outputs.reduced_map_review_html"))

        for path, label in [
            (osm_file, "OSM input"),
            (original_net_file, "original net.xml"),
            (original_net_audit, "original net audit"),
            (original_edges_geojson, "original edges GeoJSON"),
            (analysis_geojson, "analysis area GeoJSON"),
            (meta_path, "analysis area meta"),
        ]:
            if not path.is_file():
                raise Step05Error(f"ERROR: {label} not found: {path.relative_to(PROJECT_ROOT)}")

        validate_osm_xml(osm_file)
        meta = load_json(meta_path)
        original_audit = load_json(original_net_audit)
        original_net_counts = count_net_elements(original_net_file)
        original_blue_stats = count_original_blue_edges(original_edges_geojson)
        boundary_help = check_netconvert_boundary_help()
        if not boundary_help["has_in_geo_boundary"] or not boundary_help["has_expected_polygon_format"]:
            raise Step05Error(
                "ERROR: netconvert help did not confirm --keep-edges.in-geo-boundary polygon "
                "format <lon0,lat0,lon1,lat1,...>"
            )

        foci_ellipse = build_foci_ellipse(meta, args.ellipse_points)
        command = build_reduced_netconvert_command(
            osm_file=osm_file,
            net_file=reduced_net_file,
            boundary=foci_ellipse["netconvert_geo_boundary"],
        )
        command_text = shlex.join(command)

        preview = {
            "generated_at": utc_now(),
            "dry_run": args.dry_run,
            "blue_edge_rule": BLUE_EDGE_RULE,
            "netconvert_boundary_help": boundary_help,
            "input_osm_file": str(osm_file.relative_to(PROJECT_ROOT)),
            "original_net_file": str(original_net_file.relative_to(PROJECT_ROOT)),
            "output_net_file": str(reduced_net_file.relative_to(PROJECT_ROOT)),
            "foci_ellipse": {
                key: value
                for key, value in foci_ellipse.items()
                if key != "coordinates" and key != "netconvert_geo_boundary"
            },
            "foci_ellipse_coordinate_count": len(foci_ellipse["coordinates"]),
            "netconvert_command": command_text,
            "original_net_counts": original_net_counts,
            "original_blue_edge_stats": original_blue_stats,
        }

        if args.dry_run:
            lines.extend(
                [
                    "Status: DRY_RUN",
                    f"netconvert_geo_boundary_format: {boundary_help['expected_polygon_format']}",
                    f"foci_focus_distance_m: {foci_ellipse['focus_distance_m']:.3f}",
                    f"foci_semi_major_axis_m: {foci_ellipse['semi_major_axis_m']:.3f}",
                    f"foci_semi_minor_axis_m: {foci_ellipse['semi_minor_axis_m']:.3f}",
                    f"original_edge_count: {original_net_counts['edge_count']}",
                    f"original_tls_count: {original_net_counts['traffic_light_count']}",
                    f"original_blue_edge_rule_count: {original_blue_stats['blue_edge_rule_count']}",
                    f"Command preview: {command_text}",
                ]
            )
            log_lines(lines)
            print("\n".join(lines))
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            return 0

        reduced_net_file.parent.mkdir(parents=True, exist_ok=True)
        completed = run_netconvert(command)
        if completed.returncode != 0:
            lines.extend(["netconvert stderr:", completed.stderr.strip()])
            raise Step05Error(f"ERROR: netconvert failed with return code {completed.returncode}")

        validate_sumo_net_xml(reduced_net_file)
        reduced_counts = count_net_elements(reduced_net_file)
        if reduced_counts["edge_count"] <= 0:
            raise Step05Error("ERROR: reduced edge_count is 0")
        if reduced_counts["lane_count"] <= 0:
            raise Step05Error("ERROR: reduced lane_count is 0")
        if reduced_counts["traffic_light_count"] <= 0:
            raise Step05Error("ERROR: reduced TLS count is 0")
        if reduced_counts["edge_count"] >= original_net_counts["edge_count"]:
            raise Step05Error("ERROR: reduced edge_count is not smaller than original edge_count")

        bbox = foci_ellipse["bbox_wgs84"]
        geojson_audit = export_reduced_geojson(
            net_file=reduced_net_file,
            edges_geojson=reduced_edges_geojson,
            tls_geojson=reduced_tls_geojson,
            audit_path=reduced_geojson_audit,
            bbox_wgs84=bbox,
            original_tls_count=original_net_counts["traffic_light_count"],
        )
        build_review_html(
            html_path=reduced_html,
            analysis_path=analysis_geojson,
            edges_path=reduced_edges_geojson,
            tls_path=reduced_tls_geojson,
            edge_count=int(geojson_audit["edge_feature_count"]),
            tls_count=int(geojson_audit["tls_feature_count"]),
            center=[float(foci_ellipse["center"]["lat"]), float(foci_ellipse["center"]["lon"])],
            zoom=14,
            geojson_audit=geojson_audit,
        )

        generated_at = utc_now()
        audit = {
            **preview,
            "generated_at": generated_at,
            "dry_run": False,
            "netconvert_return_code": completed.returncode,
            "netconvert_warnings_summary": warnings_summary(completed.stderr),
            "reduced_net_counts": reduced_counts,
            "reduction_ratio": {
                "edge_count": reduction_ratio(original_net_counts["edge_count"], reduced_counts["edge_count"]),
                "lane_count": reduction_ratio(original_net_counts["lane_count"], reduced_counts["lane_count"]),
                "traffic_light_count": reduction_ratio(
                    original_net_counts["traffic_light_count"],
                    reduced_counts["traffic_light_count"],
                ),
            },
            "output_edges_geojson": str(reduced_edges_geojson.relative_to(PROJECT_ROOT)),
            "output_tls_geojson": str(reduced_tls_geojson.relative_to(PROJECT_ROOT)),
            "output_map_review_html": str(reduced_html.relative_to(PROJECT_ROOT)),
            "validation": {
                "original_net_unchanged": True,
                "reduced_edge_count_smaller": reduced_counts["edge_count"] < original_net_counts["edge_count"],
                "reduced_tls_count_nonzero": reduced_counts["traffic_light_count"] > 0,
                "geojson_edge_count": geojson_audit["edge_feature_count"],
                "geojson_tls_count": geojson_audit["tls_feature_count"],
                "manual_route_connectivity_check_required": True,
            },
        }
        write_json(reduced_net_audit, audit)

        manifest = {
            "generated_at": generated_at,
            "sumo_version": sumo_version(),
            "source_policy": "reuse_existing_osm_no_download",
            "input_osm_file": str(osm_file.relative_to(PROJECT_ROOT)),
            "input_osm_sha256": sha256_file(osm_file),
            "original_net_file": str(original_net_file.relative_to(PROJECT_ROOT)),
            "original_net_sha256": sha256_file(original_net_file),
            "reduced_net_file": str(reduced_net_file.relative_to(PROJECT_ROOT)),
            "reduced_net_sha256": sha256_file(reduced_net_file),
            "blue_edge_rule": BLUE_EDGE_RULE,
            "foci_ellipse": foci_ellipse,
            "netconvert_command": command_text,
            "outputs": {
                "reduced_net_audit": str(reduced_net_audit.relative_to(PROJECT_ROOT)),
                "reduced_edges_geojson": str(reduced_edges_geojson.relative_to(PROJECT_ROOT)),
                "reduced_tls_geojson": str(reduced_tls_geojson.relative_to(PROJECT_ROOT)),
                "reduced_geojson_audit": str(reduced_geojson_audit.relative_to(PROJECT_ROOT)),
                "reduced_map_review_html": str(reduced_html.relative_to(PROJECT_ROOT)),
            },
        }
        write_json(reduced_manifest, manifest)

        lines.extend(
            [
                "Status: PASS",
                f"Wrote reduced net: {reduced_net_file.relative_to(PROJECT_ROOT)} ({file_size_mb(reduced_net_file):.3f} MB)",
                f"original_edge_count: {original_net_counts['edge_count']}",
                f"reduced_edge_count: {reduced_counts['edge_count']}",
                f"original_lane_count: {original_net_counts['lane_count']}",
                f"reduced_lane_count: {reduced_counts['lane_count']}",
                f"original_tls_count: {original_net_counts['traffic_light_count']}",
                f"reduced_tls_count: {reduced_counts['traffic_light_count']}",
                f"reduced_geojson_edges: {geojson_audit['edge_feature_count']}",
                f"reduced_geojson_tls: {geojson_audit['tls_feature_count']}",
                f"Wrote audit: {reduced_net_audit.relative_to(PROJECT_ROOT)}",
                f"Wrote manifest: {reduced_manifest.relative_to(PROJECT_ROOT)}",
                f"Wrote map review HTML: {reduced_html.relative_to(PROJECT_ROOT)}",
            ]
        )
        log_lines(lines)
        print("\n".join(lines))
        return 0
    except (Step05Error, ImportError, RuntimeError, ValueError, OSError) as exc:
        lines.extend(["Status: FAIL", str(exc)])
        log_lines(lines)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
