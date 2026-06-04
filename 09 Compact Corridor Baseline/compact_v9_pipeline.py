#!/usr/bin/env python3
"""Compact V9 ellipse-corridor map pipeline.

This pipeline intentionally restarts from the map stage.  It keeps outputs
isolated under data_prepared/compact_v9 and results/html/compact_v9_*.
"""

from __future__ import annotations

import argparse
import csv
import html
import importlib.util
import json
import math
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.geo_utils import (  # noqa: E402
    bbox_from_points,
    geojson_feature,
    haversine_distance_m,
    initial_bearing_deg,
    midpoint_latlon,
    oriented_ellipse_polygon,
)
from common.net_utils import (  # noqa: E402
    count_net_elements,
    extract_edge_feature,
    find_executable,
    read_sumo_net,
    run_netconvert,
    sha256_file,
    validate_osm_xml,
    validate_sumo_net_xml,
)


PIPELINE_DIR = PROJECT_ROOT / "09 Compact Corridor Baseline"
DATA_ROOT = PROJECT_ROOT / "data_prepared/compact_v9"
HTML_ROOT = PROJECT_ROOT / "results/html"
METRICS_ROOT = PROJECT_ROOT / "results/metrics/compact_v9"
CONFIG_PATH = PROJECT_ROOT / "configs/compact_v9_b0_manifest.json"
REFERENCE_CSV = PROJECT_ROOT / "toegye_ro_mainstream_segments_english.csv"
ANALYSIS_META = PROJECT_ROOT / "data_prepared/geojson/analysis_area_meta.json"
SOURCE_OSM = PROJECT_ROOT / "data_prepared/expanded_v7/osm/jungbu_bbox_expanded_v7.osm.xml"
V7_ACCEPTED_ROUTES = PROJECT_ROOT / "data_prepared/expanded_v7/routes/firetruck_accepted_routes.csv"

AREA_META = DATA_ROOT / "geojson/compact_v9_ellipse_meta.json"
ELLIPSE_GEOJSON = DATA_ROOT / "geojson/compact_v9_ellipse.geojson"
BASE_NET = DATA_ROOT / "net/jungbu_compact_v9_ellipse.net.xml"
BASE_EDGES_GEOJSON = DATA_ROOT / "geojson/compact_v9_edges.geojson"
MAP_MANIFEST = DATA_ROOT / "net/compact_v9_map_manifest.json"
MAPPING_CSV = DATA_ROOT / "map/toegye_segment_edge_mapping.csv"
EDGE_LANE_TARGETS_CSV = DATA_ROOT / "map/edge_lane_targets_simple.csv"
LANE_OVERRIDES_CSV = DATA_ROOT / "map/lane_overrides.csv"
LANE_REPAIRED_NET = DATA_ROOT / "net/jungbu_compact_v9_ellipse_lanes_repaired.net.xml"
LANE_REPAIR_REPORT = DATA_ROOT / "net/lane_repair_report.json"
GLOBAL_LANE_DROP_FIXED_NET = DATA_ROOT / "net/jungbu_compact_v9_ellipse_lanes_repaired_global_3to1_fixed.net.xml"
GLOBAL_LANE_DROP_REPORT = DATA_ROOT / "net/global_3to1_lane_drop_fix_report.json"
FIRETRUCK_ROUTE_CSV = DATA_ROOT / "routes/firetruck_route.csv"
FIRETRUCK_ROUTE_XML = DATA_ROOT / "routes/firetruck_to_seoul_station_front.rou.xml"
MERGE_TLS_NET = DATA_ROOT / "net/jungbu_compact_v9_ellipse_lanes_repaired_entry_tls.net.xml"
MERGE_TLS_SUMMARY = DATA_ROOT / "net/entry_tls_summary.json"
CONNECTED_NET = DATA_ROOT / "net/jungbu_compact_v9_ellipse_lanes_repaired_entry_tls_connected.net.xml"
CONNECTED_SUMMARY = DATA_ROOT / "net/connected_component_prune_summary.json"
ROAD_AUDIT_DIR = METRICS_ROOT / "road_integrity"
ROAD_AUDIT_SUMMARY = ROAD_AUDIT_DIR / "compact_v9_road_integrity_summary.json"
ROAD_AUDIT_EDGES = ROAD_AUDIT_DIR / "compact_v9_road_integrity_edges.csv"
ROAD_AUDIT_PAIRS = ROAD_AUDIT_DIR / "compact_v9_road_integrity_pairs.csv"
SIGNAL_AUDIT_SUMMARY = ROAD_AUDIT_DIR / "compact_v9_signal_integrity_summary.json"
SIGNAL_AUDIT_TLS = ROAD_AUDIT_DIR / "compact_v9_signal_integrity_tls.csv"
SIGNAL_AUDIT_CONNECTIONS = ROAD_AUDIT_DIR / "compact_v9_signal_integrity_connections.csv"
FIRETRUCK_SMOKE_DIR = METRICS_ROOT / "firetruck_smoke"
FIRETRUCK_SMOKE_SUMMARY = FIRETRUCK_SMOKE_DIR / "compact_v9_firetruck_smoke_summary.json"
FIRETRUCK_SMOKE_TRIPINFO = FIRETRUCK_SMOKE_DIR / "tripinfo.xml"
FIRETRUCK_SMOKE_FCD = FIRETRUCK_SMOKE_DIR / "fcd.xml"
MAP_REVIEW_HTML = HTML_ROOT / "compact_v9_map_review.html"
MAP_ACCEPTANCE_JSON = DATA_ROOT / "acceptance/compact_v9_map_acceptance.json"
MAINLINE_GREEN_TLS_ID = "joinedS_11203052957_cluster_11203052955_11203052956_11203052960_11203052961_#11more"
MAINLINE_GREEN_FROM_EDGE = "781985787#0"
MAINLINE_GREEN_TO_EDGE = "218915135#3"
MAINLINE_GREEN_LINK_INDEX = 18
MAINLINE_GREEN_PHASE_INDEX = 4
MAINLINE_GREEN_TARGETS = {
    "green12": 12.0,
    "green18": 18.0,
    "green24": 24.0,
}
MAINLINE_GREEN_SUMMARY = METRICS_ROOT / "signal_green/compact_v9_signal_green_candidate_summary.json"
MAINLINE_GREEN_HTML = HTML_ROOT / "compact_v9_signal_green_review.html"

JUNGBU_FIRE_STATION = {"lat": 37.564875, "lon": 127.015376}
SEOUL_STATION_REFERENCE = {"lat": 37.558488, "lon": 126.971443}
SEOUL_STATION_FRONT = {"lat": 37.556152, "lon": 126.973187}
DEFAULT_TLS_ID = "COMPACT_V9_FIRE_STATION_ENTRY_TLS"


class CompactV9Error(RuntimeError):
    """Expected compact-v9 pipeline failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise CompactV9Error(f"json_root_not_object:{rel(path)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CompactV9Error(f"module_load_failed:{rel(path)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validated_pipeline() -> Any:
    return load_module("compact_v9_validated_pipeline", PROJECT_ROOT / "01-2 Validated/validated_pipeline.py")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {"", None}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in {"", None}:
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def define_ellipse_area(end_margin_m: float = 180.0, num_points: int = 96) -> dict[str, Any]:
    fire = JUNGBU_FIRE_STATION
    station = SEOUL_STATION_REFERENCE
    center_lat, center_lon = midpoint_latlon(fire["lat"], fire["lon"], station["lat"], station["lon"])
    focus_distance_m = haversine_distance_m(fire["lat"], fire["lon"], station["lat"], station["lon"])
    semi_focus_m = focus_distance_m / 2.0
    semi_major_axis_m = semi_focus_m + end_margin_m
    semi_minor_axis_m = math.sqrt(max(1.0, semi_major_axis_m**2 - semi_focus_m**2))
    bearing_deg = initial_bearing_deg(fire["lat"], fire["lon"], station["lat"], station["lon"])
    points = oriented_ellipse_polygon(center_lat, center_lon, semi_major_axis_m, semi_minor_axis_m, bearing_deg, num_points)
    bbox = bbox_from_points(points)
    coordinates = [[lon, lat] for lat, lon in points]
    payload = {
        "schema": "compact_v9_ellipse_area.v1",
        "generated_at": utc_now(),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "shape": "ellipse",
        "focus_1": {"name": "Jungbu Fire Station", **fire},
        "focus_2": {"name": "Seoul Station Reference", **station},
        "focus_distance_m": round(focus_distance_m, 3),
        "end_margin_m": round(end_margin_m, 3),
        "semi_major_axis_m": round(semi_major_axis_m, 3),
        "semi_minor_axis_m": round(semi_minor_axis_m, 3),
        "ellipse_area_km2": round(math.pi * semi_major_axis_m * semi_minor_axis_m / 1_000_000, 3),
        "center": {"lat": center_lat, "lon": center_lon},
        "bearing_deg": round(bearing_deg, 6),
        "bbox_wgs84": bbox,
        "note": "OSM input is bbox based, but final SUMO net is filtered with --keep-edges.in-geo-boundary using this ellipse polygon.",
    }
    write_json(AREA_META, payload)
    write_json(
        ELLIPSE_GEOJSON,
        {
            "type": "FeatureCollection",
            "features": [geojson_feature("Polygon", [coordinates], {"name": "compact_v9_ellipse"})],
        },
    )
    return payload


def geo_boundary_string(area: dict[str, Any]) -> str:
    feature = read_json(ELLIPSE_GEOJSON)["features"][0]
    coords = feature["geometry"]["coordinates"][0]
    return ",".join(f"{lon:.8f},{lat:.8f}" for lon, lat in coords)


def build_net() -> dict[str, Any]:
    area = read_json(AREA_META) if AREA_META.is_file() else define_ellipse_area()
    if not SOURCE_OSM.is_file():
        raise CompactV9Error(f"missing_source_osm:{rel(SOURCE_OSM)}")
    validate_osm_xml(SOURCE_OSM)
    boundary = geo_boundary_string(area)
    command = [
        find_executable("netconvert"),
        "--osm-files", str(SOURCE_OSM),
        "--output-file", str(BASE_NET),
        "--keep-edges.in-geo-boundary", boundary,
        "--tls.guess", "true",
        "--tls.join", "true",
        "--junctions.join", "true",
        "--geometry.remove", "true",
        "--remove-edges.isolated", "true",
        "--no-turnarounds", "true",
    ]
    BASE_NET.parent.mkdir(parents=True, exist_ok=True)
    completed = run_netconvert(command)
    if completed.returncode != 0:
        raise CompactV9Error(f"netconvert_failed:{completed.stderr[-3000:]}")
    validate_sumo_net_xml(BASE_NET)
    edge_geojson = export_edges_geojson(BASE_NET, BASE_EDGES_GEOJSON)
    manifest = {
        "schema": "compact_v9_map_manifest.v1",
        "generated_at": utc_now(),
        "area_meta": rel(AREA_META),
        "ellipse_geojson": rel(ELLIPSE_GEOJSON),
        "source_osm": rel(SOURCE_OSM),
        "source_osm_sha256": sha256_file(SOURCE_OSM),
        "net_file": rel(BASE_NET),
        "net_file_sha256": sha256_file(BASE_NET),
        "net_counts": count_net_elements(BASE_NET),
        "netconvert_command": command,
        "geo_boundary_point_count": len(boundary.split(",")) // 2,
        **edge_geojson,
    }
    write_json(MAP_MANIFEST, manifest)
    return manifest


def export_edges_geojson(net_file: Path, output_path: Path) -> dict[str, Any]:
    sumo_net = read_sumo_net(net_file)
    features = []
    skipped = 0
    for edge in sumo_net.getEdges():
        if edge.isSpecial():
            continue
        try:
            features.append(extract_edge_feature(sumo_net, edge))
        except Exception:
            skipped += 1
    write_json(output_path, {"type": "FeatureCollection", "features": features})
    return {"edge_feature_count": len(features), "skipped_edge_count": skipped, "edges_geojson": rel(output_path)}


def build_mapping() -> dict[str, Any]:
    vp = validated_pipeline()
    rows, summary = vp.build_toegye_edge_mapping(REFERENCE_CSV, BASE_NET)
    write_csv(MAPPING_CSV, rows, [
        "segment_id", "direction", "edge_id", "edge_order", "axis_position",
        "matched_length_m", "segment_length_m", "match_ratio", "current_lanes",
        "target_lanes", "lane_delta", "repair_target", "repair_reason",
    ])
    write_json(MAPPING_CSV.with_suffix(".summary.json"), summary)
    return summary


def is_mainline_candidate(sumo_net: Any, row: dict[str, str]) -> bool:
    edge_id = row.get("edge_id", "")
    try:
        edge = sumo_net.getEdge(edge_id)
    except Exception:
        return False
    if edge.isSpecial() or edge_id.startswith(":") or not edge.allows("passenger"):
        return False
    if float(edge.getLength()) < 8.0 and safe_float(row.get("matched_length_m")) < 6.0:
        return False
    return safe_float(row.get("matched_length_m")) > 0.5 or safe_float(row.get("match_ratio")) > 0.01


def weighted_lane_target(votes: list[tuple[int, float]]) -> int:
    weights: dict[int, float] = {}
    for lane_count, weight in votes:
        weights[lane_count] = weights.get(lane_count, 0.0) + max(0.0, weight)
    if not weights:
        return 2
    return sorted(weights.items(), key=lambda item: (item[1], item[0]))[-1][0]


def build_lane_targets() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sumo_net = read_sumo_net(BASE_NET)
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in read_csv(MAPPING_CSV):
        if is_mainline_candidate(sumo_net, row):
            grouped.setdefault(row["edge_id"], []).append(row)
    rows: list[dict[str, Any]] = []
    for edge_id, matches in grouped.items():
        edge = sumo_net.getEdge(edge_id)
        current = int(edge.getLaneNumber())
        votes = [
            (max(2, safe_int(row.get("target_lanes"), current)), safe_float(row.get("matched_length_m")))
            for row in matches
        ]
        target = max(2, weighted_lane_target(votes))
        rows.append({
            "edge_id": edge_id,
            "current_lanes": current,
            "target_lanes": target,
            "source_segment_ids": " ".join(sorted({row.get("segment_id", "") for row in matches})),
            "source_directions": " ".join(sorted({row.get("direction", "") for row in matches})),
            "total_matched_length_m": round(sum(safe_float(row.get("matched_length_m")) for row in matches), 3),
            "max_match_ratio": round(max(safe_float(row.get("match_ratio")) for row in matches), 6),
            "repair_reason": "compact_v9_one_edge_one_lane_target_mainroad_min_2",
        })
    rows.sort(key=lambda row: row["edge_id"])
    summary = {
        "schema": "compact_v9_edge_lane_targets.v1",
        "generated_at": utc_now(),
        "target_edge_count": len(rows),
        "changed_edge_count": sum(1 for row in rows if int(row["current_lanes"]) != int(row["target_lanes"])),
        "mainroad_one_lane_target_count": sum(1 for row in rows if int(row["target_lanes"]) <= 1),
        "method": "1 edge = 1 lane-count target; Toegye mainroad minimum is 2 lanes.",
    }
    write_csv(EDGE_LANE_TARGETS_CSV, rows, [
        "edge_id", "current_lanes", "target_lanes", "source_segment_ids",
        "source_directions", "total_matched_length_m", "max_match_ratio", "repair_reason",
    ])
    write_json(EDGE_LANE_TARGETS_CSV.with_suffix(".summary.json"), summary)
    return rows, summary


def repair_lanes() -> dict[str, Any]:
    rows, target_summary = build_lane_targets()
    overrides = []
    for row in rows:
        current = int(row["current_lanes"])
        target = int(row["target_lanes"])
        overrides.append({
            "edge_id": row["edge_id"],
            "target_lanes": target,
            "current_lanes": current,
            "lane_delta": target - current,
            "source_segment_ids": row["source_segment_ids"],
            "source_directions": row["source_directions"],
            "source_row_count": "",
            "dominant_segment_ids": row["source_segment_ids"],
            "dominant_directions": row["source_directions"],
            "dominant_match_ratio": row["max_match_ratio"],
            "repair_reason": row["repair_reason"],
        })
    write_csv(LANE_OVERRIDES_CSV, overrides, [
        "edge_id", "target_lanes", "current_lanes", "lane_delta", "source_segment_ids",
        "source_directions", "source_row_count", "dominant_segment_ids",
        "dominant_directions", "dominant_match_ratio", "repair_reason",
    ])
    vp = validated_pipeline()
    report = vp.rebuild_lane_repaired_net(BASE_NET, LANE_OVERRIDES_CSV, LANE_REPAIRED_NET, DATA_ROOT / "net/plain_work")
    report["lane_target_summary"] = target_summary
    write_json(LANE_REPAIR_REPORT, report)
    return report


def lane_base_net() -> Path:
    return GLOBAL_LANE_DROP_FIXED_NET if GLOBAL_LANE_DROP_FIXED_NET.is_file() else LANE_REPAIRED_NET


def global_3_to_1_targets(net_file: Path) -> dict[str, int]:
    edge_meta, connections = load_net_meta(net_file)
    targets: dict[str, int] = {}
    for from_edge, to_edge in connections:
        from_lanes = int(edge_meta.get(from_edge, {}).get("lane_count", 0))
        to_lanes = int(edge_meta.get(to_edge, {}).get("lane_count", 0))
        if from_lanes >= 3 and to_lanes <= 1:
            targets[to_edge] = max(2, targets.get(to_edge, 0))
    return targets


def rebuild_with_lane_targets(input_net: Path, output_net: Path, targets: dict[str, int], work_dir: Path) -> dict[str, Any]:
    if not targets:
        shutil.copyfile(input_net, output_net)
        return {"created": False, "reason": "no_global_3_to_1_targets", "output_net": rel(output_net)}
    netconvert = find_executable("netconvert")
    work_dir.mkdir(parents=True, exist_ok=True)
    prefix = work_dir / "global_3to1_plain"
    export_command = [netconvert, "--sumo-net-file", str(input_net), "--plain-output-prefix", str(prefix)]
    export = run_netconvert(export_command)
    if export.returncode != 0:
        raise CompactV9Error(f"global_3to1_plain_export_failed:{export.stderr[-3000:]}")
    edge_file = prefix.with_suffix(".edg.xml")
    fixed_edge_file = work_dir / "global_3to1_fixed.edg.xml"
    tree = ET.parse(edge_file)
    root = tree.getroot()
    changed = []
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        target = targets.get(edge_id)
        if target is None:
            continue
        current = safe_int(edge.get("numLanes"), 1)
        if current < target:
            edge.set("numLanes", str(target))
            changed.append({"edge_id": edge_id, "old_numLanes": current, "new_numLanes": target})
    tree.write(fixed_edge_file, encoding="utf-8", xml_declaration=True)
    node_file = prefix.with_suffix(".nod.xml")
    rebuild_command = [
        netconvert,
        "--node-files", str(node_file),
        "--edge-files", str(fixed_edge_file),
        "--output-file", str(output_net),
        "--no-turnarounds", "true",
        "--tls.rebuild", "true",
    ]
    rebuild = run_netconvert(rebuild_command)
    if rebuild.returncode != 0:
        raise CompactV9Error(f"global_3to1_rebuild_failed:{rebuild.stderr[-3000:]}")
    return {
        "created": True,
        "input_net": rel(input_net),
        "output_net": rel(output_net),
        "target_edge_count": len(targets),
        "changed_edge_count": len(changed),
        "changed_edges": changed,
        "plain_export_command": export_command,
        "rebuild_command": rebuild_command,
        "rebuild_stderr_tail": rebuild.stderr[-2000:],
    }


def fix_global_3_to_1_lane_drops() -> dict[str, Any]:
    if not LANE_REPAIRED_NET.is_file():
        repair_lanes()
    targets = global_3_to_1_targets(LANE_REPAIRED_NET)
    report = rebuild_with_lane_targets(
        LANE_REPAIRED_NET,
        GLOBAL_LANE_DROP_FIXED_NET,
        targets,
        DATA_ROOT / "net/global_3to1_plain_work",
    )
    report.update({
        "schema": "compact_v9_global_3to1_lane_drop_fix.v1",
        "generated_at": utc_now(),
        "meaning": "전역 3→1 차로 급감 금지: any connected 3+ lane edge flowing into a 1-lane edge is raised to at least 2 lanes.",
    })
    write_json(GLOBAL_LANE_DROP_REPORT, report)
    return report


def route_edges_from_v7() -> list[str]:
    rows = read_csv(V7_ACCEPTED_ROUTES) if V7_ACCEPTED_ROUTES.is_file() else []
    if not rows:
        return []
    return rows[0].get("route_edges", "").split()


def route_connected(sumo_net: Any, edge_ids: list[str]) -> bool:
    for from_id, to_id in zip(edge_ids, edge_ids[1:], strict=False):
        try:
            outgoing = {edge.getID() for edge in sumo_net.getEdge(from_id).getOutgoing()}
        except Exception:
            return False
        if to_id not in outgoing:
            return False
    return True


def shortest_route(sumo_net: Any, start_edge_id: str, target_edge_id: str) -> list[str]:
    start = sumo_net.getEdge(start_edge_id)
    target = sumo_net.getEdge(target_edge_id)
    result = sumo_net.getShortestPath(start, target)
    if not result or result[0] is None:
        return []
    return [edge.getID() for edge in result[0]]


def nearest_passenger_edges(sumo_net: Any, lat: float, lon: float, limit: int = 20) -> list[str]:
    x, y = sumo_net.convertLonLat2XY(lon, lat)
    scored = []
    for edge in sumo_net.getEdges():
        if edge.isSpecial() or not edge.allows("passenger"):
            continue
        shape = edge.getShape() or (edge.getLanes()[0].getShape() if edge.getLanes() else [])
        if not shape:
            continue
        dist = min(math.hypot(float(px) - x, float(py) - y) for px, py in shape)
        scored.append((dist, edge.getID()))
    return [edge_id for _dist, edge_id in sorted(scored)[:limit]]


def build_firetruck_route() -> dict[str, Any]:
    sumo_net = read_sumo_net(lane_base_net())
    v7_edges = [edge for edge in route_edges_from_v7() if edge in {e.getID() for e in sumo_net.getEdges()}]
    selected = []
    route_source = ""
    if len(v7_edges) >= 2 and route_connected(sumo_net, v7_edges):
        selected = v7_edges
        route_source = "v7_accepted_route_reused"
    else:
        starts = nearest_passenger_edges(sumo_net, 37.56276, 127.00757, limit=20)
        targets = nearest_passenger_edges(sumo_net, SEOUL_STATION_FRONT["lat"], SEOUL_STATION_FRONT["lon"], limit=20)
        best: list[str] = []
        for start in starts:
            for target in targets:
                try:
                    candidate = shortest_route(sumo_net, start, target)
                except Exception:
                    candidate = []
                if len(candidate) > len(best):
                    best = candidate
        selected = best
        route_source = "compact_v9_shortest_route_fallback"
    if len(selected) < 2 or not route_connected(sumo_net, selected):
        raise CompactV9Error("firetruck_route_not_connected")
    route_len = sum(float(sumo_net.getEdge(edge_id).getLength()) for edge_id in selected)
    row = {
        "route_id": "COMPACT_V9_FIRETRUCK_TO_SEOUL_STATION_FRONT",
        "scenario_id": "COMPACT_V9_SEOUL_STATION_FRONT",
        "target_edge_id": selected[-1],
        "selected_policy": route_source,
        "route_edges": " ".join(selected),
        "route_edge_count": len(selected),
        "route_length_m": round(route_len, 3),
        "start_edge_id": selected[0],
        "merge_edge_id": selected[1],
    }
    write_csv(FIRETRUCK_ROUTE_CSV, [row], [
        "route_id", "scenario_id", "target_edge_id", "selected_policy", "route_edges",
        "route_edge_count", "route_length_m", "start_edge_id", "merge_edge_id",
    ])
    FIRETRUCK_ROUTE_XML.parent.mkdir(parents=True, exist_ok=True)
    FIRETRUCK_ROUTE_XML.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<routes>
    <vType id="firetruck" vClass="emergency" guiShape="emergency" color="1,0,0" length="8.0" width="2.5" accel="1.2" decel="5.0" maxSpeed="16.67" speedFactor="1.05" lcAssertive="1.0" lcCooperative="0.7" lcStrategic="3.0" lcSpeedGain="1.0"/>
    <route id="{row['route_id']}" edges="{html.escape(row['route_edges'])}"/>
    <vehicle id="emergency_0" type="firetruck" route="{row['route_id']}" depart="600" departLane="best" departPos="0" departSpeed="max"/>
</routes>
""",
        encoding="utf-8",
    )
    write_json(FIRETRUCK_ROUTE_CSV.with_suffix(".summary.json"), row)
    return row


def build_entry_tls() -> dict[str, Any]:
    if not FIRETRUCK_ROUTE_CSV.is_file():
        build_firetruck_route()
    row = read_csv(FIRETRUCK_ROUTE_CSV)[0]
    merge_edge = row["merge_edge_id"]
    ev_depart_edge = row["start_edge_id"]
    source_net = lane_base_net()
    tree = ET.parse(source_net)
    root = tree.getroot()
    for logic in list(root.findall("tlLogic")):
        if logic.get("id") == DEFAULT_TLS_ID:
            root.remove(logic)
    via_index: dict[str, int] = {}
    for junction in root.findall("junction"):
        for index, lane in enumerate(junction.get("intLanes", "").split()):
            via_index[lane] = index
    controlled = []
    ev_uncontrolled = []
    red_indices = set()
    max_index = -1
    junction_id = ""
    for connection in root.findall("connection"):
        if connection.get("to") != merge_edge:
            continue
        from_edge = connection.get("from", "")
        via = connection.get("via", "")
        link_index = via_index.get(via)
        if link_index is None:
            text = connection.get("linkIndex")
            link_index = int(text) if text and text.isdigit() else None
        if link_index is None:
            continue
        max_index = max(max_index, link_index)
        record = {
            "from": from_edge, "to": merge_edge, "fromLane": connection.get("fromLane", ""),
            "toLane": connection.get("toLane", ""), "via": via, "linkIndex": link_index,
        }
        if from_edge == ev_depart_edge:
            ev_uncontrolled.append(record)
            continue
        connection.set("tl", DEFAULT_TLS_ID)
        connection.set("linkIndex", str(link_index))
        connection.set("state", "O")
        red_indices.add(link_index)
        controlled.append(record)
        if via.startswith(":"):
            junction_id = via[1:].rsplit("_", 2)[0]
    if not controlled:
        summary = {
            "schema": "compact_v9_entry_tls.v1",
            "created": False,
            "reason": "no_non_ev_incoming_connection_to_merge_edge",
            "input_net": rel(source_net),
            "output_net": rel(source_net),
            "merge_edge": merge_edge,
            "ev_depart_edge": ev_depart_edge,
        }
        write_json(MERGE_TLS_SUMMARY, summary)
        shutil.copyfile(source_net, MERGE_TLS_NET)
        return summary
    if junction_id:
        for junction in root.findall("junction"):
            if junction.get("id") == junction_id:
                junction.set("type", "traffic_light")
                break
    length = max(max_index + 1, max(red_indices) + 1)
    normal = ["G"] * length
    yellow = ["G"] * length
    red = ["G"] * length
    for index in red_indices:
        yellow[index] = "y"
        red[index] = "r"
    tl_logic = ET.Element("tlLogic", {"id": DEFAULT_TLS_ID, "type": "static", "programID": "COMPACT_V9_ENTRY", "offset": "0"})
    ET.SubElement(tl_logic, "phase", {"duration": "45", "state": "".join(normal), "name": "entry_open"})
    ET.SubElement(tl_logic, "phase", {"duration": "3", "state": "".join(yellow), "name": "entry_yellow"})
    ET.SubElement(tl_logic, "phase", {"duration": "12", "state": "".join(red), "name": "entry_hold"})
    root.insert(0, tl_logic)
    MERGE_TLS_NET.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="    ")
    tree.write(MERGE_TLS_NET, encoding="utf-8", xml_declaration=True)
    summary = {
        "schema": "compact_v9_entry_tls.v1",
        "generated_at": utc_now(),
        "created": True,
        "tls_id": DEFAULT_TLS_ID,
        "input_net": rel(source_net),
        "output_net": rel(MERGE_TLS_NET),
        "merge_edge": merge_edge,
        "ev_depart_edge": ev_depart_edge,
        "controlled_connection_count": len(controlled),
        "ev_uncontrolled_connection_count": len(ev_uncontrolled),
        "controlled_connections": controlled,
        "ev_uncontrolled_connections": ev_uncontrolled,
        "green_state": "".join(normal),
        "yellow_state": "".join(yellow),
        "red_state": "".join(red),
    }
    write_json(MERGE_TLS_SUMMARY, summary)
    return summary


def load_net_meta(net_file: Path) -> tuple[dict[str, dict[str, Any]], set[tuple[str, str]]]:
    edges: dict[str, dict[str, Any]] = {}
    pairs: set[tuple[str, str]] = set()
    for _event, elem in ET.iterparse(net_file, events=("end",)):
        if elem.tag == "edge" and not elem.get("function"):
            lanes = elem.findall("lane")
            if lanes:
                edge_id = elem.get("id", "")
                edges[edge_id] = {"lane_count": len(lanes), "length_m": max(safe_float(lane.get("length")) for lane in lanes)}
        elif elem.tag == "connection" and elem.get("from") and elem.get("to"):
            pairs.add((elem.get("from", ""), elem.get("to", "")))
        elem.clear()
    return edges, pairs


def passenger_weak_connectivity_summary(net_file: Path) -> dict[str, Any]:
    sumo_net = read_sumo_net(net_file)
    edges = [edge for edge in sumo_net.getEdges() if not edge.isSpecial() and edge.allows("passenger")]
    edge_ids = {edge.getID() for edge in edges}
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        edge_id = edge.getID()
        for outgoing in edge.getOutgoing():
            outgoing_id = outgoing.getID()
            if outgoing_id in edge_ids:
                adjacency[edge_id].add(outgoing_id)
                adjacency[outgoing_id].add(edge_id)
    seen: set[str] = set()
    components: list[int] = []
    for edge_id in edge_ids:
        if edge_id in seen:
            continue
        queue = deque([edge_id])
        seen.add(edge_id)
        count = 0
        while queue:
            current = queue.popleft()
            count += 1
            for other in adjacency[current]:
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
        components.append(count)
    components.sort(reverse=True)
    largest = components[0] if components else 0
    return {
        "passenger_edge_count": len(edge_ids),
        "weak_component_count": len(components),
        "largest_component_edge_count": largest,
        "largest_component_ratio": round(largest / len(edge_ids), 6) if edge_ids else 0.0,
        "small_component_count": sum(1 for count in components[1:] if count < 10),
        "top_component_sizes": components[:10],
    }


def largest_passenger_component_edges(net_file: Path) -> set[str]:
    sumo_net = read_sumo_net(net_file)
    edges = [edge for edge in sumo_net.getEdges() if not edge.isSpecial() and edge.allows("passenger")]
    edge_ids = {edge.getID() for edge in edges}
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        edge_id = edge.getID()
        for outgoing in edge.getOutgoing():
            outgoing_id = outgoing.getID()
            if outgoing_id in edge_ids:
                adjacency[edge_id].add(outgoing_id)
                adjacency[outgoing_id].add(edge_id)
    seen: set[str] = set()
    components: list[set[str]] = []
    for edge_id in edge_ids:
        if edge_id in seen:
            continue
        component: set[str] = set()
        queue = deque([edge_id])
        seen.add(edge_id)
        while queue:
            current = queue.popleft()
            component.add(current)
            for other in adjacency[current]:
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
        components.append(component)
    return max(components, key=len) if components else set()


def prune_to_largest_connected_component() -> dict[str, Any]:
    source_net = MERGE_TLS_NET if MERGE_TLS_NET.is_file() else lane_base_net()
    before = passenger_weak_connectivity_summary(source_net)
    keep_edges = largest_passenger_component_edges(source_net)
    keep_file = DATA_ROOT / "net/connected_keep_edges.txt"
    keep_file.parent.mkdir(parents=True, exist_ok=True)
    keep_file.write_text("\n".join(sorted(keep_edges)) + "\n", encoding="utf-8")
    command = [
        find_executable("netconvert"),
        "--sumo-net-file", str(source_net),
        "--keep-edges.input-file", str(keep_file),
        "--output-file", str(CONNECTED_NET),
        "--remove-edges.isolated", "true",
        "--no-turnarounds", "true",
    ]
    completed = run_netconvert(command)
    if completed.returncode != 0:
        raise CompactV9Error(f"connected_component_prune_failed:{completed.stderr[-3000:]}")
    after = passenger_weak_connectivity_summary(CONNECTED_NET)
    summary = {
        "schema": "compact_v9_connected_component_prune.v1",
        "generated_at": utc_now(),
        "input_net": rel(source_net),
        "output_net": rel(CONNECTED_NET),
        "keep_edges_file": rel(keep_file),
        "before": before,
        "after": after,
        "removed_passenger_edge_count": int(before["passenger_edge_count"]) - int(after["passenger_edge_count"]),
        "status": "PASS" if after["weak_component_count"] == 1 else "WARN",
        "netconvert_command": command,
        "stderr_tail": completed.stderr[-2000:],
    }
    write_json(CONNECTED_SUMMARY, summary)
    return summary


def road_integrity_audit() -> dict[str, Any]:
    if not MAPPING_CSV.is_file():
        build_mapping()
    if not FIRETRUCK_ROUTE_CSV.is_file():
        build_firetruck_route()
    net_file = CONNECTED_NET if CONNECTED_NET.is_file() else (MERGE_TLS_NET if MERGE_TLS_NET.is_file() else LANE_REPAIRED_NET)
    edge_meta, connections = load_net_meta(net_file)
    mapping_rows = read_csv(MAPPING_CSV)
    main_edges = {row["edge_id"] for row in mapping_rows if row.get("edge_id")}
    route_edges = read_csv(FIRETRUCK_ROUTE_CSV)[0]["route_edges"].split()
    route_edge_set = set(route_edges)
    edge_rows = []
    for edge_id in sorted(main_edges | route_edge_set):
        meta = edge_meta.get(edge_id, {})
        lanes = int(meta.get("lane_count", 0))
        roles = []
        if edge_id in main_edges:
            roles.append("toegye_mainroad")
        if edge_id in route_edge_set:
            roles.append("firetruck_route")
        issues = []
        if lanes == 0:
            issues.append("missing_edge")
        elif edge_id in main_edges and lanes <= 1:
            issues.append("hard_mainroad_one_lane_forbidden")
        edge_rows.append({
            "edge_id": edge_id, "roles": " ".join(roles), "lane_count": lanes,
            "length_m": round(float(meta.get("length_m", 0.0)), 3), "issues": ";".join(issues),
        })
    pair_rows = []
    all_connected_pairs = sorted(connections)
    for from_edge, to_edge in all_connected_pairs:
        from_lanes = int(edge_meta.get(from_edge, {}).get("lane_count", 0))
        to_lanes = int(edge_meta.get(to_edge, {}).get("lane_count", 0))
        issue = ""
        severity = ""
        if from_lanes >= 3 and to_lanes <= 1:
            issue = "hard_global_3_to_1_lane_drop_forbidden"
            severity = "hard"
        elif from_lanes >= 3 and to_lanes == 2:
            issue = "warn_3_to_2_lane_reduction"
            severity = "warn"
        elif from_lanes == 2 and to_lanes <= 1:
            issue = "warn_2_to_1_lane_reduction"
            severity = "warn"
        if issue:
            pair_rows.append({
                "from_edge": from_edge, "to_edge": to_edge, "from_lanes": from_lanes,
                "to_lanes": to_lanes, "transition": f"{from_lanes}->{to_lanes}",
                "severity": severity, "issue": issue,
                "roles": " ".join(filter(None, [
                    "toegye" if from_edge in main_edges or to_edge in main_edges else "",
                    "firetruck" if from_edge in route_edge_set or to_edge in route_edge_set else "",
                ])),
            })
    hard_edges = sum(1 for row in edge_rows if "hard_" in row["issues"])
    hard_pairs = sum(1 for row in pair_rows if row["severity"] == "hard")
    connectivity = passenger_weak_connectivity_summary(net_file)
    write_csv(ROAD_AUDIT_EDGES, edge_rows, ["edge_id", "roles", "lane_count", "length_m", "issues"])
    write_csv(ROAD_AUDIT_PAIRS, pair_rows, ["from_edge", "to_edge", "from_lanes", "to_lanes", "transition", "severity", "issue", "roles"])
    summary = {
        "schema": "compact_v9_road_integrity.v1",
        "generated_at": utc_now(),
        "net_file": rel(net_file),
        "mainroad_edge_count": len(main_edges),
        "firetruck_route_edge_count": len(route_edges),
        "mainroad_one_lane_hard_count": hard_edges,
        "global_3_to_1_hard_count": hard_pairs,
        "warn_3_to_2_count": sum(1 for row in pair_rows if row["issue"] == "warn_3_to_2_lane_reduction"),
        "warn_2_to_1_count": sum(1 for row in pair_rows if row["issue"] == "warn_2_to_1_lane_reduction"),
        "passenger_connectivity": connectivity,
        "status": "PASS" if hard_edges == 0 and hard_pairs == 0 else "FAIL",
        "edge_csv": rel(ROAD_AUDIT_EDGES),
        "pair_csv": rel(ROAD_AUDIT_PAIRS),
    }
    write_json(ROAD_AUDIT_SUMMARY, summary)
    return summary


def signal_integrity_audit() -> dict[str, Any]:
    net_file = CONNECTED_NET if CONNECTED_NET.is_file() else (MERGE_TLS_NET if MERGE_TLS_NET.is_file() else LANE_REPAIRED_NET)
    root = ET.parse(net_file).getroot()
    tl_logic: dict[str, ET.Element] = {logic.get("id", ""): logic for logic in root.findall("tlLogic") if logic.get("id")}
    max_link_index: dict[str, int] = defaultdict(lambda: -1)
    connection_rows: list[dict[str, Any]] = []
    missing_link_index = 0
    missing_logic = 0
    for connection in root.findall("connection"):
        tls_id = connection.get("tl")
        if not tls_id:
            continue
        link_text = connection.get("linkIndex")
        issue = ""
        severity = ""
        link_index = -1
        if link_text is None:
            missing_link_index += 1
            issue = "missing_linkIndex"
            severity = "fail"
        else:
            try:
                link_index = int(link_text)
                max_link_index[tls_id] = max(max_link_index[tls_id], link_index)
            except ValueError:
                missing_link_index += 1
                issue = "invalid_linkIndex"
                severity = "fail"
        if tls_id not in tl_logic:
            missing_logic += 1
            issue = ";".join(filter(None, [issue, "missing_tlLogic"]))
            severity = "fail"
        connection_rows.append({
            "tls_id": tls_id,
            "from_edge": connection.get("from", ""),
            "to_edge": connection.get("to", ""),
            "fromLane": connection.get("fromLane", ""),
            "toLane": connection.get("toLane", ""),
            "linkIndex": link_text or "",
            "issue": issue,
            "severity": severity,
        })

    tls_rows: list[dict[str, Any]] = []
    fail_count = 0
    warn_count = 0
    short_green_warn = 0
    long_cycle_warn = 0
    zero_duration_fail = 0
    phase_state_fail = 0
    for tls_id, logic in sorted(tl_logic.items()):
        phases = logic.findall("phase")
        durations = [safe_float(phase.get("duration")) for phase in phases]
        states = [phase.get("state", "") for phase in phases]
        cycle = sum(durations)
        required_len = max_link_index.get(tls_id, -1) + 1
        issues: list[str] = []
        severity = ""
        if not phases:
            issues.append("missing_phases")
            severity = "fail"
        if any(duration <= 0 for duration in durations):
            zero_duration_fail += 1
            issues.append("non_positive_phase_duration")
            severity = "fail"
        if required_len > 0 and any(len(state) < required_len for state in states):
            phase_state_fail += 1
            issues.append("phase_state_shorter_than_max_linkIndex")
            severity = "fail"
        if cycle > 180:
            long_cycle_warn += 1
            issues.append("warn_long_cycle_gt_180s")
            severity = severity or "warn"
        if states:
            min_green = min(
                (
                    sum(duration for duration, state in zip(durations, states, strict=False) if index < len(state) and state[index] in {"G", "g"})
                    for index in range(required_len)
                ),
                default=0.0,
            )
            if required_len > 0 and min_green < 6.0:
                short_green_warn += 1
                issues.append("warn_short_green_lt_6s")
                severity = severity or "warn"
        else:
            min_green = 0.0
        if severity == "fail":
            fail_count += 1
        elif severity == "warn":
            warn_count += 1
        tls_rows.append({
            "tls_id": tls_id,
            "phase_count": len(phases),
            "cycle_sec": round(cycle, 3),
            "max_link_index": max_link_index.get(tls_id, ""),
            "required_state_len": required_len,
            "state_lengths": " ".join(str(len(state)) for state in states),
            "min_green_sec": round(min_green, 3),
            "issue": ";".join(issues),
            "severity": severity,
        })

    entry = read_json(MERGE_TLS_SUMMARY) if MERGE_TLS_SUMMARY.is_file() else {}
    entry_status = "PASS"
    entry_issues: list[str] = []
    if not entry.get("created"):
        entry_status = "WARN"
        entry_issues.append(str(entry.get("reason", "entry_tls_not_created")))
    if entry.get("created") and int(entry.get("controlled_connection_count", 0)) <= 0:
        entry_status = "FAIL"
        entry_issues.append("entry_tls_no_background_controlled_connection")
    if entry.get("created") and int(entry.get("ev_uncontrolled_connection_count", 0)) <= 0:
        entry_status = "FAIL"
        entry_issues.append("entry_tls_no_ev_uncontrolled_connection")

    write_csv(SIGNAL_AUDIT_TLS, tls_rows, [
        "tls_id", "phase_count", "cycle_sec", "max_link_index", "required_state_len",
        "state_lengths", "min_green_sec", "issue", "severity",
    ])
    write_csv(SIGNAL_AUDIT_CONNECTIONS, connection_rows, [
        "tls_id", "from_edge", "to_edge", "fromLane", "toLane", "linkIndex", "issue", "severity",
    ])
    status = "PASS" if fail_count == 0 and missing_link_index == 0 and missing_logic == 0 and entry_status != "FAIL" else "FAIL"
    summary = {
        "schema": "compact_v9_signal_integrity.v1",
        "generated_at": utc_now(),
        "net_file": rel(net_file),
        "status": status,
        "tlLogic_count": len(tl_logic),
        "traffic_light_junction_count": sum(1 for junction in root.findall("junction") if junction.get("type") == "traffic_light"),
        "connection_with_tl_count": len(connection_rows),
        "connection_missing_linkIndex_count": missing_link_index,
        "connection_missing_tlLogic_count": missing_logic,
        "tls_fail_count": fail_count,
        "tls_warn_count": warn_count,
        "short_green_warn_count": short_green_warn,
        "long_cycle_warn_count": long_cycle_warn,
        "zero_duration_fail_count": zero_duration_fail,
        "phase_state_fail_count": phase_state_fail,
        "entry_tls_status": entry_status,
        "entry_tls_issues": entry_issues,
        "tls_csv": rel(SIGNAL_AUDIT_TLS),
        "connection_csv": rel(SIGNAL_AUDIT_CONNECTIONS),
    }
    write_json(SIGNAL_AUDIT_SUMMARY, summary)
    return summary


def route_connectivity_summary() -> dict[str, Any]:
    net_file = CONNECTED_NET if CONNECTED_NET.is_file() else (MERGE_TLS_NET if MERGE_TLS_NET.is_file() else LANE_REPAIRED_NET)
    return route_connectivity_summary_for_net(net_file)


def route_connectivity_summary_for_net(net_file: Path) -> dict[str, Any]:
    connections = set()
    for _event, elem in ET.iterparse(net_file, events=("end",)):
        if elem.tag == "connection" and elem.get("from") and elem.get("to"):
            connections.add((elem.get("from", ""), elem.get("to", "")))
        elem.clear()
    rows = read_csv(FIRETRUCK_ROUTE_CSV) if FIRETRUCK_ROUTE_CSV.is_file() else []
    route_edges = rows[0].get("route_edges", "").split() if rows else []
    bad_pairs = [
        {"from_edge": a_edge, "to_edge": b_edge}
        for a_edge, b_edge in zip(route_edges, route_edges[1:], strict=False)
        if (a_edge, b_edge) not in connections
    ]
    return {
        "schema": "compact_v9_firetruck_route_connectivity.v1",
        "net_file": rel(net_file),
        "route_csv": rel(FIRETRUCK_ROUTE_CSV),
        "route_edge_count": len(route_edges),
        "bad_pair_count": len(bad_pairs),
        "bad_pairs": bad_pairs[:20],
        "status": "PASS" if not bad_pairs and len(route_edges) >= 2 else "FAIL",
    }


def firetruck_smoke_test(timeout_sec: int = 180) -> dict[str, Any]:
    net_file = CONNECTED_NET if CONNECTED_NET.is_file() else (MERGE_TLS_NET if MERGE_TLS_NET.is_file() else LANE_REPAIRED_NET)
    return firetruck_smoke_test_for_net(net_file, FIRETRUCK_SMOKE_DIR, timeout_sec=timeout_sec)


def firetruck_smoke_test_for_net(net_file: Path, output_dir: Path, timeout_sec: int = 180) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_output = output_dir / "summary.xml"
    tripinfo_output = output_dir / "tripinfo.xml"
    fcd_output = output_dir / "fcd.xml"
    sumo = shutil.which("sumo")
    if not sumo:
        summary = {"schema": "compact_v9_firetruck_smoke.v1", "status": "WARN", "reason": "sumo_not_found"}
        write_json(output_dir / "compact_v9_firetruck_smoke_summary.json", summary)
        return summary
    command = [
        sumo,
        "--net-file", str(net_file),
        "--route-files", str(FIRETRUCK_ROUTE_XML),
        "--begin", "0",
        "--end", "1800",
        "--time-to-teleport", "1200",
        "--tripinfo-output", str(tripinfo_output),
        "--fcd-output", str(fcd_output),
        "--summary-output", str(summary_output),
        "--no-step-log", "true",
        "--no-warnings", "true",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout_sec)
    inserted = False
    arrived = False
    teleport = False
    depart_delay = 0.0
    travel_time = 0.0
    target_reached = False
    route_rows = read_csv(FIRETRUCK_ROUTE_CSV) if FIRETRUCK_ROUTE_CSV.is_file() else []
    target_edge = route_rows[0].get("target_edge_id", "") if route_rows else ""
    if FIRETRUCK_TRIPINFO := tripinfo_output if tripinfo_output.is_file() else None:
        try:
            root = ET.parse(FIRETRUCK_TRIPINFO).getroot()
            for trip in root.findall("tripinfo"):
                if trip.get("id") == "emergency_0":
                    inserted = True
                    arrived = trip.get("arrival") not in {"", None, "-1"}
                    depart_delay = safe_float(trip.get("departDelay"))
                    travel_time = safe_float(trip.get("duration"))
                    if trip.find("emissions") is not None or arrived:
                        target_reached = arrived
                    break
        except ET.ParseError:
            pass
    if fcd_output.is_file():
        try:
            for _event, elem in ET.iterparse(fcd_output, events=("end",)):
                if elem.tag == "vehicle" and elem.get("id") == "emergency_0":
                    inserted = True
                    if elem.get("edge") == target_edge:
                        target_reached = True
                elem.clear()
        except ET.ParseError:
            pass
    stderr_text = completed.stderr or ""
    teleport = "teleport" in stderr_text.lower()
    route_error = int(completed.returncode != 0 or "Error:" in stderr_text)
    if completed.returncode != 0:
        failure_reason = "sumo_exit_nonzero"
    elif route_error:
        failure_reason = "route_disconnected_runtime"
    elif teleport:
        failure_reason = "teleport"
    elif not inserted:
        failure_reason = "insert_failed"
    elif not arrived and not target_reached:
        failure_reason = "not_arrived_within_timeout"
    else:
        failure_reason = ""
    status = "PASS" if completed.returncode == 0 and inserted and (arrived or target_reached) and not teleport and route_error == 0 else "FAIL"
    summary = {
        "schema": "compact_v9_firetruck_smoke.v1",
        "generated_at": utc_now(),
        "status": status,
        "active_net": rel(net_file),
        "route_xml": rel(FIRETRUCK_ROUTE_XML),
        "target_edge": target_edge,
        "sumo_exit_code": completed.returncode,
        "route_error": route_error,
        "emergency_inserted": inserted,
        "emergency_arrived": arrived,
        "target_edge_seen_in_fcd": target_reached,
        "emergency_teleport": teleport,
        "departDelay": round(depart_delay, 3),
        "travel_time": round(travel_time, 3),
        "failure_reason": failure_reason,
        "tripinfo": rel(tripinfo_output),
        "fcd": rel(fcd_output),
        "command": command,
        "stderr_tail": stderr_text[-2000:],
    }
    summary_path = output_dir / "compact_v9_firetruck_smoke_summary.json"
    write_json(summary_path, summary)
    if output_dir == FIRETRUCK_SMOKE_DIR:
        write_json(FIRETRUCK_SMOKE_SUMMARY, summary)
    return summary


def sumo_load_check(net_file: Path) -> dict[str, Any]:
    sumo = shutil.which("sumo")
    if not sumo:
        return {"status": "WARN", "reason": "sumo_not_found"}
    completed = subprocess.run(
        [sumo, "--net-file", str(net_file), "--begin", "0", "--end", "1", "--no-step-log", "true", "--no-warnings", "true"],
        check=False, capture_output=True, text=True, timeout=60,
    )
    return {"status": "PASS" if completed.returncode == 0 else "FAIL", "returncode": completed.returncode, "stderr_tail": completed.stderr[-1000:]}


def tls_link_green_seconds(logic: ET.Element, link_index: int) -> float:
    return sum(
        safe_float(phase.get("duration"))
        for phase in logic.findall("phase")
        if link_index < len(phase.get("state", "")) and phase.get("state", "")[link_index] in {"G", "g"}
    )


def tls_link_yellow_seconds(logic: ET.Element, link_index: int) -> float:
    return sum(
        safe_float(phase.get("duration"))
        for phase in logic.findall("phase")
        if link_index < len(phase.get("state", "")) and phase.get("state", "")[link_index] in {"y", "Y"}
    )


def phase_is_green_phase(phase: ET.Element) -> bool:
    state = phase.get("state", "")
    return any(char in {"G", "g"} for char in state) and not any(char in {"y", "Y"} for char in state)


def linked_movements(root: ET.Element, tls_id: str) -> list[dict[str, Any]]:
    rows = []
    for connection in root.findall("connection"):
        if connection.get("tl") != tls_id:
            continue
        link_text = connection.get("linkIndex", "")
        link_index = safe_int(link_text, -1)
        rows.append({
            "linkIndex": link_index,
            "from_edge": connection.get("from", ""),
            "to_edge": connection.get("to", ""),
            "fromLane": connection.get("fromLane", ""),
            "toLane": connection.get("toLane", ""),
            "is_target": (
                link_index == MAINLINE_GREEN_LINK_INDEX
                and connection.get("from") == MAINLINE_GREEN_FROM_EDGE
                and connection.get("to") == MAINLINE_GREEN_TO_EDGE
            ),
            "is_target_phase_green": "",
        })
    return sorted(rows, key=lambda row: (row["linkIndex"], row["from_edge"], row["to_edge"], row["fromLane"]))


def compact_signal_integrity_for_net(net_file: Path) -> dict[str, Any]:
    root = ET.parse(net_file).getroot()
    tl_logic: dict[str, ET.Element] = {logic.get("id", ""): logic for logic in root.findall("tlLogic") if logic.get("id")}
    max_link_index: dict[str, int] = defaultdict(lambda: -1)
    missing_link_index = 0
    missing_logic = 0
    for connection in root.findall("connection"):
        tls_id = connection.get("tl")
        if not tls_id:
            continue
        link_text = connection.get("linkIndex")
        if link_text is None:
            missing_link_index += 1
        else:
            try:
                max_link_index[tls_id] = max(max_link_index[tls_id], int(link_text))
            except ValueError:
                missing_link_index += 1
        if tls_id not in tl_logic:
            missing_logic += 1
    fail_count = 0
    warn_count = 0
    for tls_id, logic in tl_logic.items():
        phases = logic.findall("phase")
        durations = [safe_float(phase.get("duration")) for phase in phases]
        states = [phase.get("state", "") for phase in phases]
        required_len = max_link_index.get(tls_id, -1) + 1
        failed = False
        warned = False
        if not phases or any(duration <= 0 for duration in durations):
            failed = True
        if required_len > 0 and any(len(state) < required_len for state in states):
            failed = True
        if sum(durations) > 180:
            warned = True
        if failed:
            fail_count += 1
        elif warned:
            warn_count += 1
    entry = read_json(MERGE_TLS_SUMMARY) if MERGE_TLS_SUMMARY.is_file() else {}
    entry_status = "PASS"
    if entry.get("created") and int(entry.get("controlled_connection_count", 0)) <= 0:
        entry_status = "FAIL"
    if entry.get("created") and int(entry.get("ev_uncontrolled_connection_count", 0)) <= 0:
        entry_status = "FAIL"
    return {
        "schema": "compact_v9_candidate_signal_integrity.v1",
        "net_file": rel(net_file),
        "status": "PASS" if fail_count == 0 and missing_link_index == 0 and missing_logic == 0 and entry_status != "FAIL" else "FAIL",
        "tlLogic_count": len(tl_logic),
        "connection_missing_linkIndex_count": missing_link_index,
        "connection_missing_tlLogic_count": missing_logic,
        "tls_fail_count": fail_count,
        "tls_warn_count": warn_count,
        "entry_tls_status": entry_status,
    }


def rebalance_mainline_green(tree: ET.ElementTree, target_green_sec: float) -> dict[str, Any]:
    root = tree.getroot()
    logic = next((item for item in root.findall("tlLogic") if item.get("id") == MAINLINE_GREEN_TLS_ID), None)
    if logic is None:
        raise CompactV9Error(f"missing_target_tls:{MAINLINE_GREEN_TLS_ID}")
    phases = logic.findall("phase")
    if MAINLINE_GREEN_PHASE_INDEX >= len(phases):
        raise CompactV9Error(f"missing_target_phase:{MAINLINE_GREEN_PHASE_INDEX}")
    target_phase = phases[MAINLINE_GREEN_PHASE_INDEX]
    target_state = target_phase.get("state", "")
    if MAINLINE_GREEN_LINK_INDEX >= len(target_state) or target_state[MAINLINE_GREEN_LINK_INDEX] not in {"G", "g"}:
        raise CompactV9Error("target_link_not_green_in_target_phase")
    before_durations = [safe_float(phase.get("duration")) for phase in phases]
    before_cycle = sum(before_durations)
    before_green = tls_link_green_seconds(logic, MAINLINE_GREEN_LINK_INDEX)
    if target_green_sec < before_green:
        raise CompactV9Error("target_green_must_not_decrease")
    delta = target_green_sec - before_green
    remaining = delta
    donor_records: list[dict[str, Any]] = []
    if remaining > 0:
        target_phase.set("duration", f"{safe_float(target_phase.get('duration')) + delta:g}")
        donor_candidates = []
        for index, phase in enumerate(phases):
            if index == MAINLINE_GREEN_PHASE_INDEX:
                continue
            duration = safe_float(phase.get("duration"))
            state = phase.get("state", "")
            if duration <= 6.0 or not phase_is_green_phase(phase):
                continue
            if MAINLINE_GREEN_LINK_INDEX < len(state) and state[MAINLINE_GREEN_LINK_INDEX] in {"G", "g"}:
                continue
            donor_candidates.append((duration, index, phase))
        donor_candidates.sort(reverse=True, key=lambda item: (item[0], -item[1]))
        for _duration, index, phase in donor_candidates:
            if remaining <= 1e-9:
                break
            duration = safe_float(phase.get("duration"))
            take = min(duration - 6.0, remaining)
            if take <= 0:
                continue
            phase.set("duration", f"{duration - take:g}")
            donor_records.append({
                "phase_index": index,
                "old_duration": duration,
                "new_duration": duration - take,
                "subtracted_sec": take,
                "state": phase.get("state", ""),
            })
            remaining -= take
    if remaining > 1e-6:
        raise CompactV9Error(f"insufficient_donor_green:{remaining}")
    after_durations = [safe_float(phase.get("duration")) for phase in phases]
    after_cycle = sum(after_durations)
    movement_rows = linked_movements(root, MAINLINE_GREEN_TLS_ID)
    for row in movement_rows:
        index = int(row["linkIndex"])
        if index >= 0 and index < len(target_phase.get("state", "")):
            row["is_target_phase_green"] = target_phase.get("state", "")[index] in {"G", "g"}
            row["green_sec_before"] = before_green if index == MAINLINE_GREEN_LINK_INDEX else ""
            row["green_sec_after"] = tls_link_green_seconds(logic, index)
    return {
        "tls_id": MAINLINE_GREEN_TLS_ID,
        "target_from_edge": MAINLINE_GREEN_FROM_EDGE,
        "target_to_edge": MAINLINE_GREEN_TO_EDGE,
        "target_link_index": MAINLINE_GREEN_LINK_INDEX,
        "target_phase_index": MAINLINE_GREEN_PHASE_INDEX,
        "before_cycle_sec": before_cycle,
        "after_cycle_sec": after_cycle,
        "before_target_green_sec": before_green,
        "after_target_green_sec": tls_link_green_seconds(logic, MAINLINE_GREEN_LINK_INDEX),
        "before_target_yellow_sec": tls_link_yellow_seconds(logic, MAINLINE_GREEN_LINK_INDEX),
        "after_target_yellow_sec": tls_link_yellow_seconds(logic, MAINLINE_GREEN_LINK_INDEX),
        "phase_duration_before": before_durations,
        "phase_duration_after": after_durations,
        "target_phase_state": target_phase.get("state", ""),
        "donor_phases": donor_records,
        "same_phase_movements": [row for row in movement_rows if row.get("is_target_phase_green")],
    }


def mainline_green_candidate_net_path(name: str) -> Path:
    return DATA_ROOT / "net" / f"jungbu_compact_v9_ellipse_lanes_repaired_entry_tls_connected_mainline_{name}.net.xml"


def build_mainline_green_candidates() -> dict[str, Any]:
    if not CONNECTED_NET.is_file():
        raise CompactV9Error(f"missing_connected_net:{rel(CONNECTED_NET)}")
    if not FIRETRUCK_ROUTE_XML.is_file():
        build_firetruck_route()
    candidate_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for name, target_green in MAINLINE_GREEN_TARGETS.items():
        tree = ET.parse(CONNECTED_NET)
        change = rebalance_mainline_green(tree, target_green)
        output_net = mainline_green_candidate_net_path(name)
        output_net.parent.mkdir(parents=True, exist_ok=True)
        ET.indent(tree, space="    ")
        tree.write(output_net, encoding="utf-8", xml_declaration=True)
        sumo_check = sumo_load_check(output_net)
        signal_check = compact_signal_integrity_for_net(output_net)
        route_check = route_connectivity_summary_for_net(output_net)
        smoke = firetruck_smoke_test_for_net(output_net, METRICS_ROOT / "signal_green" / name / "firetruck_smoke")
        status = "PASS" if (
            sumo_check.get("status") == "PASS"
            and signal_check.get("status") == "PASS"
            and route_check.get("status") == "PASS"
            and smoke.get("status") == "PASS"
            and abs(float(change["after_cycle_sec"]) - 90.0) < 1e-6
            and abs(float(change["after_target_green_sec"]) - target_green) < 1e-6
        ) else "FAIL"
        candidate = {
            "name": name,
            "status": status,
            "output_net": rel(output_net),
            "output_net_abs": str(output_net.resolve()),
            "target_green_sec": target_green,
            "change": change,
            "sumo_load": sumo_check,
            "signal_integrity": signal_check,
            "route_connectivity": route_check,
            "firetruck_smoke": smoke,
            "selected_default": name == "green18",
        }
        candidates.append(candidate)
        candidate_rows.append({
            "candidate": name,
            "status": status,
            "output_net": rel(output_net),
            "target_green_sec": target_green,
            "before_target_green_sec": change["before_target_green_sec"],
            "after_target_green_sec": change["after_target_green_sec"],
            "before_cycle_sec": change["before_cycle_sec"],
            "after_cycle_sec": change["after_cycle_sec"],
            "sumo_load_status": sumo_check.get("status"),
            "signal_integrity_status": signal_check.get("status"),
            "route_bad_pair_count": route_check.get("bad_pair_count"),
            "firetruck_smoke_status": smoke.get("status"),
            "travel_time": smoke.get("travel_time", ""),
            "selected_default": name == "green18",
        })
    summary = {
        "schema": "compact_v9_mainline_green_candidates.v1",
        "generated_at": utc_now(),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "base_net": rel(CONNECTED_NET),
        "target_tls_id": MAINLINE_GREEN_TLS_ID,
        "target_movement": {
            "from_edge": MAINLINE_GREEN_FROM_EDGE,
            "to_edge": MAINLINE_GREEN_TO_EDGE,
            "linkIndex": MAINLINE_GREEN_LINK_INDEX,
            "phase_index": MAINLINE_GREEN_PHASE_INDEX,
            "ev_route": True,
            "toegye_mainroad": True,
        },
        "cycle_policy": "Keep 90s cycle; increase target green and subtract the same seconds from non-yellow competing green phases with duration > 6s.",
        "default_candidate": "green18",
        "candidates": candidates,
        "status": "PASS" if all(row["status"] == "PASS" for row in candidate_rows) else "FAIL",
    }
    write_json(MAINLINE_GREEN_SUMMARY, summary)
    write_csv(MAINLINE_GREEN_SUMMARY.with_suffix(".csv"), candidate_rows, [
        "candidate", "status", "output_net", "target_green_sec", "before_target_green_sec",
        "after_target_green_sec", "before_cycle_sec", "after_cycle_sec", "sumo_load_status",
        "signal_integrity_status", "route_bad_pair_count", "firetruck_smoke_status",
        "travel_time", "selected_default",
    ])
    write_signal_green_review_html(summary)
    return summary


def write_signal_green_review_html(summary: dict[str, Any]) -> None:
    candidates = summary.get("candidates", [])
    cards = []
    rows = []
    movement_rows = []
    for candidate in candidates:
        change = candidate.get("change", {})
        cards.append(f"""
        <div class="card">
          <div class="label">{html.escape(candidate.get('name', ''))}</div>
          <div class="num {'pass' if candidate.get('status') == 'PASS' else 'fail'}">{html.escape(candidate.get('status', ''))}</div>
          <p>green {change.get('before_target_green_sec')}s → {change.get('after_target_green_sec')}s / cycle {change.get('after_cycle_sec')}s</p>
          <p>smoke {html.escape(candidate.get('firetruck_smoke', {}).get('status', 'NA'))}, travel {candidate.get('firetruck_smoke', {}).get('travel_time', '')}s</p>
        </div>
        """)
        rows.append(f"""
        <tr>
          <td>{html.escape(candidate.get('name', ''))}</td>
          <td>{html.escape(candidate.get('status', ''))}</td>
          <td>{change.get('before_target_green_sec')}</td>
          <td>{change.get('after_target_green_sec')}</td>
          <td>{change.get('after_cycle_sec')}</td>
          <td>{html.escape(candidate.get('sumo_load', {}).get('status', ''))}</td>
          <td>{html.escape(candidate.get('signal_integrity', {}).get('status', ''))}</td>
          <td>{candidate.get('route_connectivity', {}).get('bad_pair_count', '')}</td>
          <td>{html.escape(candidate.get('firetruck_smoke', {}).get('status', ''))}</td>
          <td>{candidate.get('firetruck_smoke', {}).get('travel_time', '')}</td>
          <td><code>{html.escape(candidate.get('output_net', ''))}</code></td>
        </tr>
        """)
    if candidates:
        for row in candidates[0].get("change", {}).get("same_phase_movements", []):
            movement_rows.append(f"""
            <tr>
              <td>{row.get('linkIndex')}</td>
              <td><code>{html.escape(row.get('from_edge', ''))}</code></td>
              <td><code>{html.escape(row.get('to_edge', ''))}</code></td>
              <td>{'yes' if row.get('is_target') else ''}</td>
            </tr>
            """)
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    MAINLINE_GREEN_HTML.parent.mkdir(parents=True, exist_ok=True)
    MAINLINE_GREEN_HTML.write_text(f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Compact V9 Mainline Green Review</title>
  <style>
    body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin:0; color:#172033; }}
    header {{ padding:22px; border-bottom:1px solid #d8dee9; }}
    .panel {{ padding:18px 22px; display:grid; grid-template-columns:repeat(3,minmax(220px,1fr)); gap:12px; }}
    .card {{ border:1px solid #d8dee9; border-radius:8px; padding:12px; }}
    .label {{ color:#667085; font-size:12px; }}
    .num {{ font-size:24px; font-weight:700; }}
    .pass {{ color:#067647; }} .fail {{ color:#b42318; }}
    table {{ margin:0 22px 22px; border-collapse:collapse; width:calc(100% - 44px); font-size:13px; }}
    th, td {{ border:1px solid #d8dee9; padding:8px; text-align:left; vertical-align:top; }}
    th {{ background:#f8fafc; }}
    pre {{ margin:0 22px 24px; padding:14px; border:1px solid #d8dee9; background:#f8fafc; overflow:auto; max-height:420px; }}
  </style>
</head>
<body>
<header>
  <h1>Compact V9 S15/S16 Mainline Green 후보 검증</h1>
  <p><code>{html.escape(MAINLINE_GREEN_FROM_EDGE)}</code> → <code>{html.escape(MAINLINE_GREEN_TO_EDGE)}</code>, linkIndex {MAINLINE_GREEN_LINK_INDEX}. 기존 6초 green을 12/18/24초 후보로 늘리고 cycle 90초를 유지했습니다.</p>
</header>
<section class="panel">{''.join(cards)}</section>
<table>
  <thead><tr><th>candidate</th><th>status</th><th>green before</th><th>green after</th><th>cycle</th><th>SUMO</th><th>signal</th><th>bad pair</th><th>smoke</th><th>travel</th><th>net</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
<table>
  <thead><tr><th>linkIndex</th><th>from</th><th>to</th><th>target</th></tr></thead>
  <tbody>{''.join(movement_rows)}</tbody>
</table>
<pre>{html.escape(payload)}</pre>
</body>
</html>
""", encoding="utf-8")


def write_map_review_html() -> dict[str, Any]:
    area = read_json(AREA_META)
    manifest = read_json(MAP_MANIFEST) if MAP_MANIFEST.is_file() else {}
    lane_report = read_json(LANE_REPAIR_REPORT) if LANE_REPAIR_REPORT.is_file() else {}
    route_summary = read_json(FIRETRUCK_ROUTE_CSV.with_suffix(".summary.json")) if FIRETRUCK_ROUTE_CSV.with_suffix(".summary.json").is_file() else {}
    tls_summary = read_json(MERGE_TLS_SUMMARY) if MERGE_TLS_SUMMARY.is_file() else {}
    connected_summary = read_json(CONNECTED_SUMMARY) if CONNECTED_SUMMARY.is_file() else {}
    audit = read_json(ROAD_AUDIT_SUMMARY) if ROAD_AUDIT_SUMMARY.is_file() else {}
    signal_audit = read_json(SIGNAL_AUDIT_SUMMARY) if SIGNAL_AUDIT_SUMMARY.is_file() else {}
    smoke_summary = read_json(FIRETRUCK_SMOKE_SUMMARY) if FIRETRUCK_SMOKE_SUMMARY.is_file() else {}
    route_connectivity = route_connectivity_summary() if FIRETRUCK_ROUTE_CSV.is_file() else {}
    final_net = CONNECTED_NET if CONNECTED_NET.is_file() else (MERGE_TLS_NET if MERGE_TLS_NET.is_file() else LANE_REPAIRED_NET)
    sumo_check = sumo_load_check(final_net)
    edge_features = read_json(BASE_EDGES_GEOJSON).get("features", []) if BASE_EDGES_GEOJSON.is_file() else []
    ellipse_coords = read_json(ELLIPSE_GEOJSON)["features"][0]["geometry"]["coordinates"][0]
    route_coords = []
    if route_summary:
        sumo_net = read_sumo_net(final_net)
        for edge_id in route_summary.get("route_edges", "").split():
            try:
                edge = sumo_net.getEdge(edge_id)
                shape = edge.getShape() or edge.getLanes()[0].getShape()
                route_coords.extend([[lat, lon] for lon, lat in (sumo_net.convertXY2LonLat(x, y) for x, y in shape)])
            except Exception:
                continue
    payload = {
        "area": area,
        "map_manifest": manifest,
        "lane_repair_report": lane_report,
        "route_summary": route_summary,
        "entry_tls_summary": tls_summary,
        "connected_component_prune": connected_summary,
        "road_integrity": audit,
        "signal_integrity": signal_audit,
        "firetruck_route_connectivity": route_connectivity,
        "firetruck_smoke": smoke_summary,
        "sumo_load_check": sumo_check,
    }
    write_json(DATA_ROOT / "acceptance/compact_v9_map_review_payload.json", payload)
    edge_js = json.dumps(edge_features, ensure_ascii=False)
    ellipse_js = json.dumps([[lat, lon] for lon, lat in ellipse_coords], ensure_ascii=False)
    route_js = json.dumps(route_coords, ensure_ascii=False)
    payload_js = json.dumps(payload, ensure_ascii=False, indent=2)
    MAP_REVIEW_HTML.parent.mkdir(parents=True, exist_ok=True)
    MAP_REVIEW_HTML.write_text(f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Compact V9 Map Review</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#172033; }}
    header {{ padding:18px 22px; border-bottom:1px solid #d8dee9; }}
    #map {{ height:68vh; }}
    .panel {{ padding:18px 22px; display:grid; grid-template-columns: repeat(4, minmax(160px,1fr)); gap:12px; }}
    .card {{ border:1px solid #d8dee9; border-radius:8px; padding:12px; background:white; }}
    .label {{ color:#667085; font-size:12px; }}
    .num {{ font-size:22px; font-weight:700; margin-top:4px; }}
    pre {{ margin:0 22px 24px; padding:14px; border:1px solid #d8dee9; background:#f8fafc; overflow:auto; max-height:360px; }}
    .pass {{ color:#067647; }} .fail {{ color:#b42318; }} .warn {{ color:#b54708; }}
  </style>
</head>
<body>
<header>
  <h1>Compact V9 타원형 맵 Review</h1>
  <p>서울역과 중부소방서를 두 초점으로 하는 최소 타원형 corridor 맵입니다. 이 화면에서 도로망, 소방서 진입 경로, 진입 신호기, 차로 검증 결과를 확인하고 accept 여부를 결정합니다.</p>
</header>
<div id="map"></div>
<section class="panel">
  <div class="card"><div class="label">Ellipse Area</div><div class="num">{area.get('ellipse_area_km2')} km²</div></div>
  <div class="card"><div class="label">Edges</div><div class="num">{manifest.get('net_counts', {}).get('edge_count', '')}</div></div>
  <div class="card"><div class="label">Road Integrity</div><div class="num {'pass' if audit.get('status') == 'PASS' else 'fail'}">{audit.get('status', 'NA')}</div></div>
  <div class="card"><div class="label">SUMO Load</div><div class="num {'pass' if sumo_check.get('status') == 'PASS' else 'warn'}">{sumo_check.get('status')}</div></div>
  <div class="card"><div class="label">Mainroad 1-lane</div><div class="num">{audit.get('mainroad_one_lane_hard_count', '')}</div></div>
  <div class="card"><div class="label">Global 3→1</div><div class="num">{audit.get('global_3_to_1_hard_count', '')}</div></div>
  <div class="card"><div class="label">3→2 WARN</div><div class="num">{audit.get('warn_3_to_2_count', '')}</div></div>
  <div class="card"><div class="label">Entry TLS</div><div class="num {'pass' if tls_summary.get('created') else 'warn'}">{'created' if tls_summary.get('created') else tls_summary.get('reason', 'NA')}</div></div>
  <div class="card"><div class="label">Connected Components</div><div class="num">{audit.get('passenger_connectivity', {}).get('weak_component_count', '')}</div></div>
  <div class="card"><div class="label">Signal Integrity</div><div class="num {'pass' if signal_audit.get('status') == 'PASS' else 'fail'}">{signal_audit.get('status', 'NA')}</div></div>
  <div class="card"><div class="label">Route Bad Pairs</div><div class="num">{route_connectivity.get('bad_pair_count', '')}</div></div>
  <div class="card"><div class="label">Firetruck Smoke</div><div class="num {'pass' if smoke_summary.get('status') == 'PASS' else 'fail'}">{smoke_summary.get('status', 'NA')}</div></div>
  <div class="card"><div class="label">Travel Time</div><div class="num">{smoke_summary.get('travel_time', '')}s</div></div>
</section>
<pre>{html.escape(payload_js)}</pre>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const edgeFeatures = {edge_js};
const ellipse = {ellipse_js};
const route = {route_js};
const map = L.map('map', {{ preferCanvas: true }}).setView([37.5617, 126.9934], 14);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 19, attribution: '&copy; OpenStreetMap' }}).addTo(map);
const edgeLayer = L.geoJSON({{type:'FeatureCollection', features: edgeFeatures}}, {{
  style: f => {{
    const lanes = Number(f.properties && f.properties.lanes || 1);
    return {{ color: lanes >= 3 ? '#2563eb' : lanes === 2 ? '#16a34a' : '#f97316', weight: Math.max(1, Math.min(5, lanes + 1)), opacity: 0.55 }};
  }}
}}).addTo(map);
L.polygon(ellipse, {{ color:'#dc2626', weight:3, fill:false, dashArray:'8 6' }}).addTo(map).bindTooltip('Compact V9 ellipse');
if (route.length > 1) {{
  L.polyline(route, {{ color:'#ef4444', weight:7, opacity:0.9 }}).addTo(map).bindTooltip('소방차 경로');
}}
L.circleMarker([{JUNGBU_FIRE_STATION['lat']}, {JUNGBU_FIRE_STATION['lon']}], {{ radius:8, color:'#dc2626', fillColor:'#dc2626', fillOpacity:1 }}).addTo(map).bindTooltip('중부소방서');
L.circleMarker([{SEOUL_STATION_REFERENCE['lat']}, {SEOUL_STATION_REFERENCE['lon']}], {{ radius:8, color:'#111827', fillColor:'#111827', fillOpacity:1 }}).addTo(map).bindTooltip('서울역 기준점');
map.fitBounds(L.polygon(ellipse).getBounds(), {{ padding:[30,30] }});
</script>
</body>
</html>
""", encoding="utf-8")
    return {**payload, "html": rel(MAP_REVIEW_HTML), "review_payload": rel(DATA_ROOT / "acceptance/compact_v9_map_review_payload.json")}


def build_all() -> dict[str, Any]:
    area = define_ellipse_area()
    net = build_net()
    mapping = build_mapping()
    lane = repair_lanes()
    global_lane_drop = fix_global_3_to_1_lane_drops()
    route = build_firetruck_route()
    tls = build_entry_tls()
    connected = prune_to_largest_connected_component()
    audit = road_integrity_audit()
    signal_audit = signal_integrity_audit()
    route_connectivity = route_connectivity_summary()
    smoke = firetruck_smoke_test()
    review = write_map_review_html()
    manifest = {
        "schema": "compact_v9_b0_manifest.v1",
        "generated_at": utc_now(),
        "active_net": rel(CONNECTED_NET if CONNECTED_NET.is_file() else (MERGE_TLS_NET if MERGE_TLS_NET.is_file() else LANE_REPAIRED_NET)),
        "base_net": rel(BASE_NET),
        "lane_repaired_net": rel(LANE_REPAIRED_NET),
        "global_3to1_fixed_net": rel(GLOBAL_LANE_DROP_FIXED_NET),
        "connected_net": rel(CONNECTED_NET),
        "firetruck_route_xml": rel(FIRETRUCK_ROUTE_XML),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "map_acceptance_required": True,
        "map_acceptance_json": rel(MAP_ACCEPTANCE_JSON),
        "map_review_html": rel(MAP_REVIEW_HTML),
        "area": area,
        "net": net,
        "mapping": mapping,
        "lane_repair": lane,
        "global_3to1_lane_drop_fix": global_lane_drop,
        "firetruck_route": route,
        "entry_tls": tls,
        "connected_component_prune": connected,
        "road_integrity": audit,
        "signal_integrity": signal_audit,
        "firetruck_route_connectivity": route_connectivity,
        "firetruck_smoke": smoke,
    }
    write_json(CONFIG_PATH, manifest)
    return {**manifest, "review": review}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compact V9 ellipse-corridor map pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("area")
    sub.add_parser("net")
    sub.add_parser("mapping")
    sub.add_parser("lanes")
    sub.add_parser("route")
    sub.add_parser("entry-tls")
    sub.add_parser("audit")
    sub.add_parser("signal-audit")
    sub.add_parser("firetruck-smoke")
    sub.add_parser("mainline-green-candidates")
    sub.add_parser("review")
    sub.add_parser("all")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "area":
        result = define_ellipse_area()
    elif args.command == "net":
        result = build_net()
    elif args.command == "mapping":
        result = build_mapping()
    elif args.command == "lanes":
        result = repair_lanes()
    elif args.command == "route":
        result = build_firetruck_route()
    elif args.command == "entry-tls":
        result = build_entry_tls()
    elif args.command == "audit":
        result = road_integrity_audit()
    elif args.command == "signal-audit":
        result = signal_integrity_audit()
    elif args.command == "firetruck-smoke":
        result = firetruck_smoke_test()
    elif args.command == "mainline-green-candidates":
        result = build_mainline_green_candidates()
    elif args.command == "review":
        result = write_map_review_html()
    elif args.command == "all":
        result = build_all()
    else:
        raise CompactV9Error(f"unknown_command:{args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
