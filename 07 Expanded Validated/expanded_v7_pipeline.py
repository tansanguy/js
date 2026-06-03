#!/usr/bin/env python3
"""Independent expanded-v7 B0 firetruck baseline pipeline helpers."""

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.geo_utils import expand_bbox_m, geojson_feature, haversine_distance_m  # noqa: E402
from common.net_utils import (  # noqa: E402
    count_net_elements,
    download_osm_bbox,
    extract_edge_feature,
    find_executable,
    read_sumo_net,
    run_netconvert,
    sha256_file,
    sumo_version,
    validate_bbox_wgs84,
    validate_osm_xml,
    validate_sumo_net_xml,
)


PIPELINE_DIR = PROJECT_ROOT / "07 Expanded Validated"
DATA_ROOT = PROJECT_ROOT / "data_prepared/expanded_v7"
METRICS_ROOT = PROJECT_ROOT / "results/metrics"
HTML_ROOT = PROJECT_ROOT / "results/html"
REFERENCE_CSV = PROJECT_ROOT / "toegye_ro_mainstream_segments_english.csv"
ANALYSIS_META = PROJECT_ROOT / "data_prepared/geojson/analysis_area_meta.json"
OLD_START_EDGE = "-381802881#2"
SEOUL_STATION_FRONT = {"lat": 37.556152, "lon": 126.973187}
JUNGBU_FIRE_STATION = {"lat": 37.564875, "lon": 127.015376}

EXPANDED_META = DATA_ROOT / "geojson/expanded_area_meta.json"
EXPANDED_BBOX_GEOJSON = DATA_ROOT / "geojson/expanded_bbox.geojson"
EXPANDED_OSM = DATA_ROOT / "osm/jungbu_bbox_expanded_v7.osm.xml"
EXPANDED_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger.net.xml"
EXPANDED_EDGES_GEOJSON = DATA_ROOT / "geojson/expanded_v7_edges.geojson"
EXPANDED_MAP_MANIFEST = DATA_ROOT / "net/expanded_v7_map_manifest.json"
MAPPING_CSV = DATA_ROOT / "map/toegye_segment_edge_mapping.csv"
EDGE_LANE_TARGETS_SIMPLE_CSV = DATA_ROOT / "map/edge_lane_targets_simple.csv"
EDGE_LANE_RECALL_SIMPLE_CSV = DATA_ROOT / "map/edge_lane_recall_simple.csv"
LANE_OVERRIDES_CSV = DATA_ROOT / "map/lane_overrides.csv"
REPAIRED_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger_lanes_repaired.net.xml"
TLS_FIXED_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed.net.xml"
TLS_FIX_SUMMARY_JSON = DATA_ROOT / "net/tls_green_split_fix_summary.json"
SPEEDCAP_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_speedcap.net.xml"
SPEEDCAP_SUMMARY_JSON = DATA_ROOT / "net/mainroad_speedcap_summary.json"
RELEASE_SPEEDCAP_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_release_speedcap.net.xml"
RELEASE_SPEEDCAP_SUMMARY_JSON = DATA_ROOT / "net/release_speedcap_summary.json"
DOWNBOUND_METERING_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_release_downbound_metered.net.xml"
DOWNBOUND_METERING_SUMMARY_JSON = DATA_ROOT / "net/downbound_metering_speedcap_summary.json"
OVEROPEN_METERING_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_release_overopen_metered.net.xml"
OVEROPEN_METERING_SUMMARY_JSON = DATA_ROOT / "net/overopen_metering_speedcap_summary.json"
ROUTE_EDGE_OVEROPEN_METERING_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_release_route_overopen_metered.net.xml"
ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON = DATA_ROOT / "net/route_edge_overopen_metering_speedcap_summary.json"
RELEASE_JUNCTION_FIXED_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_release_route_overopen_metered_release_fixed.net.xml"
RELEASE_JUNCTION_FIXED_SUMMARY_JSON = DATA_ROOT / "net/release_junction_fixed_summary.json"
LANE_DROP_FIXED_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_release_route_overopen_metered_release_fixed_lane_drop_fixed.net.xml"
LANE_DROP_FIXED_SUMMARY_JSON = DATA_ROOT / "net/mainline_lane_drop_fixed_summary.json"
PLAUSIBILITY_OVEROPEN_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_release_route_overopen_metered_release_fixed_lane_drop_fixed_plausibility_overopen.net.xml"
PLAUSIBILITY_OVEROPEN_SUMMARY_JSON = DATA_ROOT / "net/plausibility_overopen_speedcap_summary.json"
MAKE_SENSE_FIXED_NET = DATA_ROOT / "net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_release_route_overopen_metered_release_fixed_lane_drop_fixed_plausibility_overopen_make_sense_fixed.net.xml"
LANE_DROP_FIXED_OVERRIDES_CSV = DATA_ROOT / "net/mainline_lane_drop_fixed_overrides.csv"
MAKE_SENSE_OVERRIDES_CSV = DATA_ROOT / "net/make_sense_net_candidate_overrides.csv"
ROUTE_CANDIDATES_CSV = DATA_ROOT / "routes/firetruck_route_candidates.csv"
ROUTE_ACCEPTANCE_JSON = DATA_ROOT / "routes/firetruck_route_acceptance.json"
ACCEPTED_ROUTES_CSV = DATA_ROOT / "routes/firetruck_accepted_routes.csv"
ACCEPTED_ROUTE_XML = DATA_ROOT / "routes/firetruck_to_seoul_station_front.rou.xml"
CONSERVATIVE_ROUTE_XML = DATA_ROOT / "routes/firetruck_to_seoul_station_front_conservative_b0.rou.xml"
DEMAND_XML = DATA_ROOT / "demand/background_routes_expanded_v7_reference_main_sideflow.rou.xml"
DEMAND_SUMMARY_CSV = DATA_ROOT / "demand/demand_assignment_summary.csv"
DEMAND_PROFILE_SUMMARY_CSV = DATA_ROOT / "demand/demand_profile_summary.csv"
SOURCE_ASSIGNMENT_SUMMARY_CSV = DATA_ROOT / "demand/source_assignment_summary.csv"
SIDEFLOW_SUMMARY_CSV = DATA_ROOT / "demand/sideflow_assignment_summary.csv"
MAPWIDE_SUMMARY_CSV = DATA_ROOT / "demand/mapwide_background_summary.csv"
MANIFEST = PROJECT_ROOT / "configs/expanded_v7_b0_manifest.json"
CONSERVATIVE_MANIFEST = PROJECT_ROOT / "configs/expanded_v7_conservative_b0_manifest.json"

MAP_REVIEW_HTML = HTML_ROOT / "expanded_v7_map_review.html"
ROUTE_REVIEW_HTML = HTML_ROOT / "expanded_v7_firetruck_route_review.html"
B0_REVIEW_HTML = HTML_ROOT / "expanded_v7_b0_result_review.html"
VALIDATION_REVIEW_HTML = HTML_ROOT / "expanded_v7_validation_review.html"

RUN_PREFIX = "expanded_v7_b0"
CONSERVATIVE_RUN_PREFIX = "expanded_v7_conservative_b0"
CONSERVATIVE_CONFLICT_PREFIX = "expanded_v7_conservative_b0_conflict_audit"
VALIDATION_PREFIX = "expanded_v7_validation"
BOTTLENECK_DIAG_PREFIX = "expanded_v7_bottleneck_diagnosis"
BOTTLENECK_DIAG_HTML = HTML_ROOT / "expanded_v7_bottleneck_diagnosis.html"
NETWORK_INTEGRITY_PREFIX = "expanded_v7_network_integrity_audit"
ROAD_INTEGRITY_PREFIX = "expanded_v7_road_integrity_audit"
ROAD_INTEGRITY_HTML = HTML_ROOT / "expanded_v7_road_integrity_audit.html"
MAKE_SENSE_PREFIX = "expanded_v7_make_sense_audit"
MAKE_SENSE_HTML = HTML_ROOT / "expanded_v7_make_sense_audit.html"
MAKE_SENSE_CANDIDATE_SUMMARY_JSON = DATA_ROOT / "net/make_sense_net_candidate_summary.json"
BALANCED_CONGESTION_PREFIX = "expanded_v7_balanced_congestion"
BALANCED_CONGESTION_SUMMARY = METRICS_ROOT / BALANCED_CONGESTION_PREFIX / "balanced_congestion_sweep_summary.json"
V3_CAUSE_REPORT_HTML = HTML_ROOT / "expanded_v7_bottleneck_cause_summary.html"
BASELINE_BALANCED_REMAINING_COUNT = 1249
BASELINE_RELEASE_WAITING_TIME_SEC = 216854.56
DOWNSTREAM_TLS_ID = "joinedS_11203052957_cluster_11203052955_11203052956_11203052960_11203052961_#11more"
DOWNSTREAM_TLS_TARGET_LINK = 18
DOWNSTREAM_TLS_RELATED_LINKS = [17, 18, 19, 20]
BOTTLENECK_SEGMENTS = set(range(9, 18))
BOTTLENECK_EDGE_IDS = {
    "218773869#4", "218773869#5", "218773869#6", "218773869#7", "218773869#8", "218773869#9",
    "219696193#1", "219696193#2", "781985793#0", "781985793#1", "420361196", "180445419",
    "218915133#1", "218915133#2", "218915133#3", "781985787#0",
}
BOTTLENECK_SOURCE_BLOCKLIST = {"180445419", "270279684#1", "198691069#7", "781985787#0", "218773869#6", "218773869#7"}
DOWNSTREAM_SINK_GUARD_EDGE_IDS = {"218915135#3", "218915135#4", "781983104#0", "781983104#1", "333557072#1"}
TERMINAL_SINK_GUARD_EDGE_IDS = {
    "619147738#0", "619147738#1",
    "585341906#0",
    "585341903#0", "585341903#1",
}
PROBLEM_SOURCE_SINK_EDGE_IDS = {
    "585341903#0", "585341906#0",
    "-174870621#5", "-381802847#2",
    "781985787#0", "619147738#0",
}
MAPWIDE_TELEPORT_EDGE_BLOCKLIST = {
    "219053201",
    "37930979#0", "37930979#2",
    "350127794#0", "350127794#1", "350127794#3", "350127794#4", "350127794#5", "350127794#7", "350127794#8", "350127794#9",
    "616037663#0", "616037663#1", "616037663#2",
    "1084408282#1", "1084408282#2",
    "378453707#7", "220058587",
}
RELEASE_CONNECTION_PRIORITY_EDGE_IDS = {
    "-381802847#2", "-174870621#6", "-174870621#5", "-174870621#4",
    "218773869#7", "218773869#8", "218773869#9",
    "180445419",
    "347237859#0", "347237859#1", "347237859#2", "347237859#3", "347237859#4", "347237859#5",
    "781985787#0",
    "333557072#5", "619147738#0",
    "585341903#0", "585341903#1", "585341906#0",
}
RELEASE_SPEEDCAP_EDGE_IDS = {
    "218915133#1", "218915133#2", "218915133#3",
    "218773869#4", "218773869#5",
    "219696193#1", "219696193#2",
    "781985793#0", "781985793#1",
    "347237859#0", "347237859#1",
}
MAINLINE_RELEASE_EDGE_IDS = RELEASE_CONNECTION_PRIORITY_EDGE_IDS | RELEASE_SPEEDCAP_EDGE_IDS | TERMINAL_SINK_GUARD_EDGE_IDS | {
    "218915133#0", "218915133#1", "218915133#2", "218915133#3",
    "218915135#3", "218915135#4",
    "333557072#1", "333557072#2", "333557072#3", "333557072#5",
    "37402371", "477063271", "585341906#2", "585341907#0", "585341907#2",
    "585341908#0", "585341908#1", "615671502", "615671503",
    "1206223939", "1206223943", "1206223945", "1206223946#0", "1206223946#1", "1206223947",
}
BALANCED_CONGESTION_PROFILES = [
    "balanced_congestion_v3_a",
    "balanced_congestion_v3_027",
    "balanced_congestion_v3_tuned",
    "balanced_congestion_v3_up22",
    "balanced_congestion_v3_up20",
    "balanced_congestion_v3",
    "balanced_congestion_v3_c",
    "balanced_congestion_v3_down55",
    "balanced_congestion_v3_down60",
    "balanced_congestion_v3_down65",
    "balanced_congestion_v3_down75",
    "balanced_congestion_v4_smooth_release",
    "balanced_congestion_v5_distributed_boundary",
    "balanced_congestion_v6_boundary_fanout_only",
    "balanced_congestion_v6_release_gap",
    "balanced_congestion_v6_free_feeder",
    "balanced_congestion_v6_boundary_balancer",
    "balanced_congestion_v7_plausibility_first",
    "balanced_congestion_v8_stop_free_cleanup",
]
PLAUSIBILITY_FIRST_PREFIX = "expanded_v7_plausibility_first"
PLAUSIBILITY_FIRST_SUMMARY = METRICS_ROOT / PLAUSIBILITY_FIRST_PREFIX / "plausibility_first_summary.json"
V6_BOUNDARY_BALANCER_PREFIX = "expanded_v7_v6_boundary_balancer"
V6_BOUNDARY_BALANCER_SUMMARY = METRICS_ROOT / V6_BOUNDARY_BALANCER_PREFIX / "v6_boundary_balancer_sweep_summary.json"
V6_BOUNDARY_BALANCER_PROFILES = [
    "balanced_congestion_v6_boundary_fanout_only",
    "balanced_congestion_v6_release_gap",
    "balanced_congestion_v6_free_feeder",
    "balanced_congestion_v6_boundary_balancer",
]
SHORT_EDGE_ARTIFACT_LENGTH_M = 5.0
SHORT_EDGE_WARN_LENGTH_M = 10.0
FLOW_STOP_SPEED_KMH = 5.0
FLOW_FREE_SPEED_KMH = 35.0
FLOW_TARGET_TOLERANCE_KMH = 8.0
DEFAULT_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


class ExpandedV7Error(RuntimeError):
    """Expected expanded-v7 pipeline failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def project_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else PROJECT_ROOT / value


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ExpandedV7Error(f"json_root_not_object:{rel(path)}")
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
        raise ExpandedV7Error(f"module_spec_failed:{rel(path)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validated_pipeline() -> Any:
    return load_module("expanded_v7_validated_pipeline", PROJECT_ROOT / "01-2 Validated/validated_pipeline.py")


def step07_module() -> Any:
    return load_module("expanded_v7_step07_routes", PROJECT_ROOT / "01_prepare/04_routes/step07_generate_emergency_routes.py")


def validator_module() -> Any:
    return load_module("expanded_v7_validator", PROJECT_ROOT / "01-1 Validation/validate_b0_reality_recall.py")


def runner_module() -> Any:
    module = load_module("expanded_v7_runner", PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py")
    if hasattr(module, "configure_runtime_environment"):
        module.configure_runtime_environment()
    return module


def ensure_isolated_output(path: Path) -> None:
    path = path.resolve()
    allowed_roots = [
        PIPELINE_DIR.resolve(),
        DATA_ROOT.resolve(),
        METRICS_ROOT.resolve(),
        HTML_ROOT.resolve(),
    ]
    if path in {MANIFEST.resolve(), CONSERVATIVE_MANIFEST.resolve()}:
        return
    if any(path == root or root in path.parents for root in allowed_roots):
        return
    raise ExpandedV7Error(f"non_isolated_output_path:{path}")


def expanded_bbox_from_meta(meta_path: Path = ANALYSIS_META, buffer_m: float = 100.0) -> dict[str, float]:
    meta = read_json(meta_path)
    bbox = validate_bbox_wgs84(meta["bbox_wgs84"])
    return expand_bbox_m(bbox, buffer_m)


def bbox_geojson_feature(bbox: dict[str, float]) -> dict[str, Any]:
    coords = [[
        [bbox["min_lon"], bbox["min_lat"]],
        [bbox["max_lon"], bbox["min_lat"]],
        [bbox["max_lon"], bbox["max_lat"]],
        [bbox["min_lon"], bbox["max_lat"]],
        [bbox["min_lon"], bbox["min_lat"]],
    ]]
    return geojson_feature("Polygon", coords, {"name": "expanded_v7_bbox"})


def define_expanded_area(meta_path: Path = ANALYSIS_META, output_meta: Path = EXPANDED_META) -> dict[str, Any]:
    old_meta = read_json(meta_path)
    old_bbox = validate_bbox_wgs84(old_meta["bbox_wgs84"])
    new_bbox = expanded_bbox_from_meta(meta_path, 100.0)
    for output in [output_meta, EXPANDED_BBOX_GEOJSON]:
        ensure_isolated_output(output)
    payload = {
        "schema": "expanded_v7_area.v1",
        "generated_at": utc_now(),
        "source_analysis_meta": rel(meta_path),
        "expansion_m_each_direction": 100.0,
        "source_bbox_wgs84": old_bbox,
        "bbox_wgs84": new_bbox,
        "locations": old_meta.get("locations", {}),
    }
    write_json(output_meta, payload)
    write_json(
        EXPANDED_BBOX_GEOJSON,
        {"type": "FeatureCollection", "features": [bbox_geojson_feature(new_bbox)]},
    )
    return payload


def build_netconvert_command(osm_file: Path, net_file: Path) -> list[str]:
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
    ]


def export_edges_geojson(net_file: Path, output_path: Path) -> dict[str, Any]:
    ensure_isolated_output(output_path)
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


def build_expanded_net(force_download: bool = False, timeout_sec: int = 240, overpass_urls: list[str] | None = None) -> dict[str, Any]:
    area = read_json(EXPANDED_META) if EXPANDED_META.is_file() else define_expanded_area()
    bbox = validate_bbox_wgs84(area["bbox_wgs84"])
    for output in [EXPANDED_OSM, EXPANDED_NET, EXPANDED_EDGES_GEOJSON, EXPANDED_MAP_MANIFEST]:
        ensure_isolated_output(output)
    downloaded = False
    download_url = ""
    if force_download or not EXPANDED_OSM.is_file():
        failures = []
        for url in overpass_urls or DEFAULT_OVERPASS_URLS:
            try:
                download_osm_bbox(bbox, EXPANDED_OSM, url, timeout_sec, verify_ssl=True)
                downloaded = True
                download_url = url
                break
            except Exception as exc:
                failures.append(str(exc))
                try:
                    download_osm_bbox(bbox, EXPANDED_OSM, url, timeout_sec, verify_ssl=False)
                    downloaded = True
                    download_url = f"{url} (ssl_verify_disabled)"
                    break
                except Exception as insecure_exc:
                    failures.append(str(insecure_exc))
        if not EXPANDED_OSM.is_file():
            raise ExpandedV7Error("overpass_download_failed:" + " | ".join(failures)[-2000:])
    osm_counts = validate_osm_xml(EXPANDED_OSM)
    command = build_netconvert_command(EXPANDED_OSM, EXPANDED_NET)
    EXPANDED_NET.parent.mkdir(parents=True, exist_ok=True)
    completed = run_netconvert(command)
    if completed.returncode != 0:
        raise ExpandedV7Error(f"netconvert_failed:{completed.stderr[-3000:]}")
    validate_sumo_net_xml(EXPANDED_NET)
    edge_geojson = export_edges_geojson(EXPANDED_NET, EXPANDED_EDGES_GEOJSON)
    manifest = {
        "schema": "expanded_v7_map_manifest.v1",
        "generated_at": utc_now(),
        "bbox_wgs84": bbox,
        "expansion_m_each_direction": 100.0,
        "osm_file": rel(EXPANDED_OSM),
        "osm_file_sha256": sha256_file(EXPANDED_OSM),
        "net_file": rel(EXPANDED_NET),
        "net_file_sha256": sha256_file(EXPANDED_NET),
        "downloaded": downloaded,
        "download_url": download_url,
        "osm_counts": osm_counts,
        "net_counts": count_net_elements(EXPANDED_NET),
        "sumo_version": sumo_version(),
        "netconvert_command": command,
        **edge_geojson,
    }
    write_json(EXPANDED_MAP_MANIFEST, manifest)
    write_map_review_html(MAP_REVIEW_HTML, manifest)
    return manifest


def build_toegye_mapping(reference_csv: Path = REFERENCE_CSV, net_file: Path = EXPANDED_NET) -> dict[str, Any]:
    vp = validated_pipeline()
    rows, summary = vp.build_toegye_edge_mapping(reference_csv, net_file)
    write_csv(MAPPING_CSV, rows, [
        "segment_id", "direction", "edge_id", "edge_order", "axis_position",
        "matched_length_m", "segment_length_m", "match_ratio", "current_lanes",
        "target_lanes", "lane_delta", "repair_target", "repair_reason",
    ])
    write_json(MAPPING_CSV.with_suffix(".summary.json"), summary)
    return summary


def boolish(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def is_mainline_lane_target_candidate(sumo_net: Any, row: dict[str, str]) -> bool:
    edge_id = row.get("edge_id", "")
    try:
        edge = sumo_net.getEdge(edge_id)
    except Exception:
        return False
    if edge.isSpecial() or not edge.allows("passenger"):
        return False
    if edge_id.startswith(":"):
        return False
    length = float(edge.getLength())
    matched = safe_float(row.get("matched_length_m"))
    ratio = safe_float(row.get("match_ratio"))
    # Keep low-overlap corridor edges, but avoid very short connector fragments.
    if length < 10.0 and matched < 8.0:
        return False
    if matched <= 0.5 and ratio <= 0.01:
        return False
    return True


def weighted_lane_target(votes: list[tuple[int, float]]) -> int:
    weights: dict[int, float] = {}
    for lane_count, weight in votes:
        weights[lane_count] = weights.get(lane_count, 0.0) + max(0.0, weight)
    if not weights:
        return 1
    return sorted(weights.items(), key=lambda item: (item[1], item[0]))[-1][0]


def build_simple_edge_lane_targets(mapping_csv: Path = MAPPING_CSV, net_file: Path = EXPANDED_NET) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sumo_net = read_sumo_net(net_file)
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in read_csv(mapping_csv):
        if not is_mainline_lane_target_candidate(sumo_net, row):
            continue
        grouped.setdefault(row["edge_id"], []).append(row)
    rows: list[dict[str, Any]] = []
    for edge_id, matches in grouped.items():
        edge = sumo_net.getEdge(edge_id)
        votes = [
            (safe_int(row.get("target_lanes"), edge.getLaneNumber()), safe_float(row.get("matched_length_m")))
            for row in matches
        ]
        majority = weighted_lane_target(votes)
        max_target = max((lane for lane, _weight in votes), default=majority)
        source_segments = sorted({row.get("segment_id", "") for row in matches if row.get("segment_id")})
        source_directions = sorted({row.get("direction", "") for row in matches if row.get("direction")})
        rows.append({
            "edge_id": edge_id,
            "current_lanes": edge.getLaneNumber(),
            "target_lanes_simple": majority,
            "weighted_majority_lanes": majority,
            "max_target_lanes": max_target,
            "total_matched_length_m": round(sum(safe_float(row.get("matched_length_m")) for row in matches), 6),
            "max_match_ratio": round(max((safe_float(row.get("match_ratio")) for row in matches), default=0.0), 6),
            "edge_length_m": round(float(edge.getLength()), 6),
            "source_segment_ids": " ".join(source_segments),
            "source_directions": " ".join(source_directions),
            "source_row_count": len(matches),
            "min_axis_position": min((safe_float(row.get("axis_position")) for row in matches), default=0.0),
            "smoothing_applied": False,
            "repair_reason": "edge_weighted_majority_lane_target",
        })
    smooth_isolated_lane_targets(rows)
    rows.sort(key=lambda row: (row["source_directions"], row["min_axis_position"], row["edge_id"]))
    summary = {
        "schema": "expanded_v7_edge_lane_targets_simple.v1",
        "generated_at": utc_now(),
        "mapping_csv": rel(mapping_csv),
        "net_file": rel(net_file),
        "edge_target_count": len(rows),
        "changed_edge_count": sum(1 for row in rows if safe_int(row["current_lanes"]) != safe_int(row["target_lanes_simple"])),
        "smoothed_edge_count": sum(1 for row in rows if boolish(row["smoothing_applied"])),
        "method": "one_edge_one_weighted_majority_lane_target_with_isolated_gap_smoothing",
    }
    return rows, summary


def smooth_isolated_lane_targets(rows: list[dict[str, Any]]) -> None:
    by_direction: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        directions = str(row.get("source_directions", "")).split()
        for direction in directions or [""]:
            by_direction.setdefault(direction, []).append(row)
    for direction_rows in by_direction.values():
        direction_rows.sort(key=lambda row: (safe_float(row.get("min_axis_position")), row.get("edge_id", "")))
        for index in range(1, len(direction_rows) - 1):
            prev_row = direction_rows[index - 1]
            row = direction_rows[index]
            next_row = direction_rows[index + 1]
            prev_target = safe_int(prev_row.get("target_lanes_simple"))
            target = safe_int(row.get("target_lanes_simple"))
            next_target = safe_int(next_row.get("target_lanes_simple"))
            if target < prev_target and prev_target == next_target and prev_target >= 3 and safe_float(row.get("edge_length_m")) <= 80.0:
                row["target_lanes_simple"] = prev_target
                row["smoothing_applied"] = True
                row["repair_reason"] = "isolated_lane_gap_smoothing"


def simple_lane_override_rows(target_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overrides = []
    for row in target_rows:
        current = safe_int(row.get("current_lanes"))
        target = safe_int(row.get("target_lanes_simple"), current)
        overrides.append({
            "edge_id": row["edge_id"],
            "target_lanes": target,
            "current_lanes": current,
            "lane_delta": target - current,
            "source_segment_ids": row.get("source_segment_ids", ""),
            "source_directions": row.get("source_directions", ""),
            "source_row_count": row.get("source_row_count", ""),
            "dominant_segment_ids": row.get("source_segment_ids", ""),
            "dominant_directions": row.get("source_directions", ""),
            "dominant_match_ratio": row.get("max_match_ratio", ""),
            "repair_reason": row.get("repair_reason", "edge_weighted_majority_lane_target"),
        })
    return overrides


def validate_simple_edge_lane_recall(target_rows: list[dict[str, Any]], repaired_net: Path = REPAIRED_NET) -> dict[str, Any]:
    sumo_net = read_sumo_net(repaired_net)
    recall_rows = []
    for row in target_rows:
        edge_id = row["edge_id"]
        try:
            actual = int(sumo_net.getEdge(edge_id).getLaneNumber())
        except Exception:
            actual = -1
        target = safe_int(row.get("target_lanes_simple"))
        status = "PASS" if actual == target else "FAIL"
        recall_rows.append({
            "edge_id": edge_id,
            "target_lanes_simple": target,
            "actual_lanes": actual,
            "current_lanes_before_repair": row.get("current_lanes", ""),
            "source_segment_ids": row.get("source_segment_ids", ""),
            "source_directions": row.get("source_directions", ""),
            "smoothing_applied": row.get("smoothing_applied", False),
            "status": status,
        })
    fields = [
        "edge_id", "target_lanes_simple", "actual_lanes", "current_lanes_before_repair",
        "source_segment_ids", "source_directions", "smoothing_applied", "status",
    ]
    write_csv(EDGE_LANE_RECALL_SIMPLE_CSV, recall_rows, fields)
    summary = {
        "schema": "expanded_v7_edge_lane_recall_simple.v1",
        "generated_at": utc_now(),
        "target_csv": rel(EDGE_LANE_TARGETS_SIMPLE_CSV),
        "recall_csv": rel(EDGE_LANE_RECALL_SIMPLE_CSV),
        "edge_target_count": len(recall_rows),
        "pass_edge_count": sum(1 for row in recall_rows if row["status"] == "PASS"),
        "edge_level_lane_recall": (
            sum(1 for row in recall_rows if row["status"] == "PASS") / len(recall_rows)
        ) if recall_rows else 0.0,
        "status": "PASS" if recall_rows and all(row["status"] == "PASS" for row in recall_rows) else "FAIL",
    }
    write_json(EDGE_LANE_RECALL_SIMPLE_CSV.with_suffix(".summary.json"), summary)
    return summary


def repair_lanes(net_file: Path = EXPANDED_NET, mapping_csv: Path = MAPPING_CSV) -> dict[str, Any]:
    vp = validated_pipeline()
    target_rows, target_summary = build_simple_edge_lane_targets(mapping_csv, net_file)
    write_csv(EDGE_LANE_TARGETS_SIMPLE_CSV, target_rows, [
        "edge_id", "current_lanes", "target_lanes_simple", "weighted_majority_lanes",
        "max_target_lanes", "total_matched_length_m", "max_match_ratio", "edge_length_m",
        "source_segment_ids", "source_directions", "source_row_count", "min_axis_position",
        "smoothing_applied", "repair_reason",
    ])
    write_json(EDGE_LANE_TARGETS_SIMPLE_CSV.with_suffix(".summary.json"), target_summary)
    overrides = simple_lane_override_rows(target_rows)
    summary = {
        "schema": "expanded_v7_lane_overrides_simple.v1",
        "generated_at": utc_now(),
        "edge_lane_targets_simple_csv": rel(EDGE_LANE_TARGETS_SIMPLE_CSV),
        **target_summary,
    }
    write_csv(LANE_OVERRIDES_CSV, overrides, [
        "edge_id", "target_lanes", "current_lanes", "lane_delta", "source_segment_ids",
        "source_directions", "source_row_count", "dominant_segment_ids",
        "dominant_directions", "dominant_match_ratio", "repair_reason",
    ])
    write_json(LANE_OVERRIDES_CSV.with_suffix(".summary.json"), summary)
    report = vp.rebuild_lane_repaired_net(net_file, LANE_OVERRIDES_CSV, REPAIRED_NET, DATA_ROOT / "net/plain_work")
    report["simple_edge_lane_recall"] = validate_simple_edge_lane_recall(target_rows, REPAIRED_NET)
    report["edge_lane_targets_simple_csv"] = rel(EDGE_LANE_TARGETS_SIMPLE_CSV)
    write_json(DATA_ROOT / "net/lane_repair_report.json", report)
    return report


def phase_link_green_seconds(phases: list[ET.Element], link_index: int) -> int:
    return int(sum(
        int(float(phase.get("duration", "0") or 0))
        for phase in phases
        if link_index < len(phase.get("state", "")) and phase.get("state", "")[link_index] in {"G", "g"}
    ))


def phase_link_yellow_seconds(phases: list[ET.Element], link_index: int) -> int:
    return int(sum(
        int(float(phase.get("duration", "0") or 0))
        for phase in phases
        if link_index < len(phase.get("state", "")) and phase.get("state", "")[link_index] in {"y", "Y"}
    ))


def active_b0_net() -> Path:
    if PLAUSIBILITY_OVEROPEN_NET.is_file() and PLAUSIBILITY_OVEROPEN_SUMMARY_JSON.is_file():
        plausibility_summary = read_json(PLAUSIBILITY_OVEROPEN_SUMMARY_JSON)
        if plausibility_summary.get("selected_for_manifest") is True:
            return PLAUSIBILITY_OVEROPEN_NET
    if LANE_DROP_FIXED_NET.is_file() and LANE_DROP_FIXED_SUMMARY_JSON.is_file():
        lane_drop_summary = read_json(LANE_DROP_FIXED_SUMMARY_JSON)
        if lane_drop_summary.get("selected_for_manifest") is True:
            return LANE_DROP_FIXED_NET
    return active_b0_net_before_lane_drop()


def active_b0_net_before_lane_drop() -> Path:
    if RELEASE_JUNCTION_FIXED_NET.is_file() and RELEASE_JUNCTION_FIXED_SUMMARY_JSON.is_file():
        release_junction_summary = read_json(RELEASE_JUNCTION_FIXED_SUMMARY_JSON)
        if release_junction_summary.get("selected_for_manifest") is True:
            return RELEASE_JUNCTION_FIXED_NET
    if ROUTE_EDGE_OVEROPEN_METERING_NET.is_file() and ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON.is_file():
        route_edge_summary = read_json(ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON)
        if route_edge_summary.get("selected_for_manifest") is True:
            return ROUTE_EDGE_OVEROPEN_METERING_NET
    if OVEROPEN_METERING_NET.is_file() and OVEROPEN_METERING_SUMMARY_JSON.is_file():
        overopen_summary = read_json(OVEROPEN_METERING_SUMMARY_JSON)
        if overopen_summary.get("selected_for_manifest") is True:
            return OVEROPEN_METERING_NET
    if DOWNBOUND_METERING_NET.is_file() and DOWNBOUND_METERING_SUMMARY_JSON.is_file():
        downbound_summary = read_json(DOWNBOUND_METERING_SUMMARY_JSON)
        if downbound_summary.get("selected_for_manifest") is True:
            return DOWNBOUND_METERING_NET
    if RELEASE_SPEEDCAP_NET.is_file() and RELEASE_SPEEDCAP_SUMMARY_JSON.is_file():
        release_summary = read_json(RELEASE_SPEEDCAP_SUMMARY_JSON)
        if release_summary.get("selected_for_manifest") is True:
            return RELEASE_SPEEDCAP_NET
    if SPEEDCAP_NET.is_file() and SPEEDCAP_SUMMARY_JSON.is_file():
        speedcap_summary = read_json(SPEEDCAP_SUMMARY_JSON)
        if speedcap_summary.get("selected_for_manifest") is True:
            return SPEEDCAP_NET
    return TLS_FIXED_NET if TLS_FIXED_NET.is_file() else REPAIRED_NET


def route_connectivity_on_net(net_file: Path, accepted_routes: Path = ACCEPTED_ROUTES_CSV) -> dict[str, Any]:
    if not accepted_routes.is_file():
        return {"checked": False, "status": "SKIP", "reason": f"missing:{rel(accepted_routes)}"}
    rows = read_csv(accepted_routes)
    if not rows:
        return {"checked": False, "status": "SKIP", "reason": "empty_accepted_routes"}
    edge_ids = rows[0].get("route_edges", "").split()
    if not edge_ids:
        return {"checked": False, "status": "SKIP", "reason": "empty_route_edges"}
    connected = route_connected(read_sumo_net(net_file), edge_ids)
    return {
        "checked": True,
        "status": "PASS" if connected else "FAIL",
        "route_id": rows[0].get("route_id", ""),
        "route_edge_count": len(edge_ids),
        "connected": connected,
    }


def sumo_net_load_check(net_file: Path) -> dict[str, Any]:
    sumo = find_executable("sumo")
    completed = subprocess.run(
        [
            sumo,
            "--net-file",
            str(net_file),
            "--begin",
            "0",
            "--end",
            "1",
            "--no-step-log",
            "true",
            "--no-warnings",
            "true",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "returncode": completed.returncode,
        "stderr_tail": completed.stderr[-1000:],
    }


def mainroad_speed_targets_from_csv(
    mapping_csv: Path = MAPPING_CSV,
    reference_csv: Path = REFERENCE_CSV,
    margin_kmh: float = 25.0,
    min_cap_kmh: float = 35.0,
    max_cap_kmh: float = 50.0,
) -> dict[str, dict[str, Any]]:
    reference = {row["segment_id"]: row for row in reference_volume_rows(reference_csv)}
    accum: dict[str, dict[str, float]] = {}
    for row in read_csv(mapping_csv):
        edge_id = row.get("edge_id", "")
        segment_id = row.get("segment_id", "")
        direction = row.get("direction", "")
        if not edge_id or segment_id not in reference:
            continue
        speed_key = direction_speed_key(direction)
        reference_speed = float(reference[segment_id].get(speed_key) or 0.0)
        if reference_speed <= 0:
            continue
        weight = max(1.0, safe_float(row.get("matched_length_m"), 1.0))
        bucket = accum.setdefault(edge_id, {"weighted_speed": 0.0, "weight": 0.0, "segment_count": 0.0})
        bucket["weighted_speed"] += reference_speed * weight
        bucket["weight"] += weight
        bucket["segment_count"] += 1
    targets: dict[str, dict[str, Any]] = {}
    for edge_id, bucket in accum.items():
        reference_speed = bucket["weighted_speed"] / bucket["weight"] if bucket["weight"] else 0.0
        cap = max(min_cap_kmh, min(max_cap_kmh, reference_speed + margin_kmh))
        targets[edge_id] = {
            "reference_speed_kmh": round(reference_speed, 6),
            "speed_cap_kmh": round(cap, 6),
            "matched_segment_count": int(bucket["segment_count"]),
        }
    return targets


def directional_speed_targets_from_csv(
    mapping_csv: Path = MAPPING_CSV,
    reference_csv: Path = REFERENCE_CSV,
    direction_filter: str = "downbound",
    margin_kmh: float = 8.0,
    min_cap_kmh: float = 22.0,
    max_cap_kmh: float = 30.0,
    excluded_edge_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    reference = {row["segment_id"]: row for row in reference_volume_rows(reference_csv)}
    excluded_edge_ids = excluded_edge_ids or set()
    accum: dict[str, dict[str, Any]] = {}
    for row in read_csv(mapping_csv):
        edge_id = row.get("edge_id", "")
        segment_id = row.get("segment_id", "")
        direction = row.get("direction", "")
        if not edge_id or edge_id in excluded_edge_ids or direction != direction_filter or segment_id not in reference:
            continue
        reference_speed = float(reference[segment_id].get(direction_speed_key(direction)) or 0.0)
        if reference_speed <= 0:
            continue
        weight = max(1.0, safe_float(row.get("matched_length_m"), 1.0))
        bucket = accum.setdefault(edge_id, {
            "weighted_speed": 0.0,
            "weight": 0.0,
            "segment_count": 0,
            "segments": set(),
            "direction": direction,
        })
        bucket["weighted_speed"] += reference_speed * weight
        bucket["weight"] += weight
        bucket["segment_count"] += 1
        bucket["segments"].add(segment_id)
    targets: dict[str, dict[str, Any]] = {}
    for edge_id, bucket in accum.items():
        reference_speed = bucket["weighted_speed"] / bucket["weight"] if bucket["weight"] else 0.0
        cap = max(min_cap_kmh, min(max_cap_kmh, reference_speed + margin_kmh))
        targets[edge_id] = {
            "direction": bucket["direction"],
            "reference_speed_kmh": round(reference_speed, 6),
            "speed_cap_kmh": round(cap, 6),
            "matched_segment_count": int(bucket["segment_count"]),
            "source_segment_ids": " ".join(sorted(bucket["segments"])),
        }
    return targets


def build_mainroad_speedcap_net(
    input_net: Path | None = None,
    output_net: Path = SPEEDCAP_NET,
    mapping_csv: Path = MAPPING_CSV,
    reference_csv: Path = REFERENCE_CSV,
    margin_kmh: float = 25.0,
    min_cap_kmh: float = 35.0,
    max_cap_kmh: float = 50.0,
) -> dict[str, Any]:
    source_net = input_net or (TLS_FIXED_NET if TLS_FIXED_NET.is_file() else REPAIRED_NET)
    ensure_isolated_output(output_net)
    if not source_net.is_file():
        raise ExpandedV7Error(f"missing_speedcap_input_net:{rel(source_net)}")
    targets = mainroad_speed_targets_from_csv(
        mapping_csv,
        reference_csv,
        margin_kmh=margin_kmh,
        min_cap_kmh=min_cap_kmh,
        max_cap_kmh=max_cap_kmh,
    )
    tree = ET.parse(source_net)
    root = tree.getroot()
    rows = []
    changed_lane_count = 0
    changed_edge_count = 0
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        target = targets.get(edge_id)
        if not target:
            continue
        cap_mps = float(target["speed_cap_kmh"]) / 3.6
        edge_changed = False
        old_speeds = []
        for lane in edge.findall("lane"):
            old_speed = safe_float(lane.get("speed"))
            if old_speed <= 0:
                continue
            old_speeds.append(old_speed * 3.6)
            if old_speed > cap_mps:
                lane.set("speed", f"{cap_mps:.6f}")
                changed_lane_count += 1
                edge_changed = True
        if edge_changed:
            changed_edge_count += 1
        if old_speeds:
            rows.append({
                "edge_id": edge_id,
                "matched_segment_count": target["matched_segment_count"],
                "reference_speed_kmh": target["reference_speed_kmh"],
                "speed_cap_kmh": target["speed_cap_kmh"],
                "old_min_speed_kmh": round(min(old_speeds), 6),
                "old_max_speed_kmh": round(max(old_speeds), 6),
                "changed": edge_changed,
            })
    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="utf-8", xml_declaration=True)
    route_check = route_connectivity_on_net(output_net)
    load_check = sumo_net_load_check(output_net)
    csv_path = output_net.with_name("mainroad_speedcap_edges.csv")
    write_csv(csv_path, rows, [
        "edge_id", "matched_segment_count", "reference_speed_kmh", "speed_cap_kmh",
        "old_min_speed_kmh", "old_max_speed_kmh", "changed",
    ])
    summary = {
        "schema": "expanded_v7_mainroad_speedcap_net.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(reference_csv),
        "mapping_csv": rel(mapping_csv),
        "input_net": rel(source_net),
        "output_net": rel(output_net),
        "speedcap_csv": rel(csv_path),
        "target_edge_count": len(targets),
        "written_edge_count": len(rows),
        "changed_edge_count": changed_edge_count,
        "changed_lane_count": changed_lane_count,
        "margin_kmh": margin_kmh,
        "min_cap_kmh": min_cap_kmh,
        "max_cap_kmh": max_cap_kmh,
        "selected_for_manifest": False,
        "sumo_net_load": load_check,
        "route_connectivity": route_check,
        "status": "PASS" if load_check["status"] == "PASS" and route_check.get("status") in {"PASS", "SKIP"} else "FAIL",
        "note": "V7-only main-road speed cap for congestion-flow recall. This avoids free-flow artifacts on Toegye-ro where imported SUMO speed limits are much higher than the real CSV congested speeds.",
    }
    write_json(SPEEDCAP_SUMMARY_JSON, summary)
    return summary


def build_downbound_metering_speedcap_net(
    input_net: Path | None = None,
    output_net: Path = DOWNBOUND_METERING_NET,
    mapping_csv: Path = MAPPING_CSV,
    reference_csv: Path = REFERENCE_CSV,
    margin_kmh: float = 8.0,
    min_cap_kmh: float = 22.0,
    max_cap_kmh: float = 30.0,
    selected_for_manifest: bool = False,
) -> dict[str, Any]:
    source_net = input_net or (RELEASE_SPEEDCAP_NET if RELEASE_SPEEDCAP_NET.is_file() else (TLS_FIXED_NET if TLS_FIXED_NET.is_file() else REPAIRED_NET))
    ensure_isolated_output(output_net)
    if not source_net.is_file():
        raise ExpandedV7Error(f"missing_downbound_metering_input_net:{rel(source_net)}")
    excluded_route_edges: set[str] = set()
    if ACCEPTED_ROUTES_CSV.is_file():
        for row in read_csv(ACCEPTED_ROUTES_CSV):
            excluded_route_edges.update(row.get("route_edges", "").split())
    targets = directional_speed_targets_from_csv(
        mapping_csv,
        reference_csv,
        direction_filter="downbound",
        margin_kmh=margin_kmh,
        min_cap_kmh=min_cap_kmh,
        max_cap_kmh=max_cap_kmh,
        excluded_edge_ids=excluded_route_edges,
    )
    tree = ET.parse(source_net)
    root = tree.getroot()
    rows = []
    changed_lane_count = 0
    changed_edge_count = 0
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        target = targets.get(edge_id)
        if not target:
            continue
        cap_mps = float(target["speed_cap_kmh"]) / 3.6
        old_speeds = []
        edge_changed = False
        for lane in edge.findall("lane"):
            old_speed = safe_float(lane.get("speed"))
            if old_speed <= 0:
                continue
            old_speeds.append(old_speed * 3.6)
            if old_speed > cap_mps:
                lane.set("speed", f"{cap_mps:.6f}")
                changed_lane_count += 1
                edge_changed = True
        if edge_changed:
            changed_edge_count += 1
        if old_speeds:
            rows.append({
                "edge_id": edge_id,
                "direction": target["direction"],
                "source_segment_ids": target["source_segment_ids"],
                "matched_segment_count": target["matched_segment_count"],
                "reference_speed_kmh": target["reference_speed_kmh"],
                "speed_cap_kmh": target["speed_cap_kmh"],
                "old_min_speed_kmh": round(min(old_speeds), 6),
                "old_max_speed_kmh": round(max(old_speeds), 6),
                "changed": edge_changed,
                "excluded_firetruck_route_edge": edge_id in excluded_route_edges,
            })
    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="utf-8", xml_declaration=True)
    route_check = route_connectivity_on_net(output_net)
    load_check = sumo_net_load_check(output_net)
    csv_path = output_net.with_name("downbound_metering_speedcap_edges.csv")
    write_csv(csv_path, rows, [
        "edge_id", "direction", "source_segment_ids", "matched_segment_count",
        "reference_speed_kmh", "speed_cap_kmh", "old_min_speed_kmh",
        "old_max_speed_kmh", "changed", "excluded_firetruck_route_edge",
    ])
    summary = {
        "schema": "expanded_v7_downbound_metering_speedcap_net.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(reference_csv),
        "mapping_csv": rel(mapping_csv),
        "input_net": rel(source_net),
        "output_net": rel(output_net),
        "speedcap_csv": rel(csv_path),
        "direction_filter": "downbound",
        "target_edge_count": len(targets),
        "written_edge_count": len(rows),
        "changed_edge_count": changed_edge_count,
        "changed_lane_count": changed_lane_count,
        "margin_kmh": margin_kmh,
        "min_cap_kmh": min_cap_kmh,
        "max_cap_kmh": max_cap_kmh,
        "excluded_firetruck_route_edge_count": len(excluded_route_edges),
        "selected_for_manifest": selected_for_manifest,
        "sumo_net_load": load_check,
        "route_connectivity": route_check,
        "status": "PASS" if load_check["status"] == "PASS" and route_check.get("status") in {"PASS", "SKIP"} else "FAIL",
        "note": "V7-only downbound metering speed cap. It suppresses unrealistic downbound free-flow while leaving the accepted firetruck route edges untouched.",
    }
    write_json(DOWNBOUND_METERING_SUMMARY_JSON, summary)
    return summary


def overopen_speed_targets_from_validation(
    edge_speed_csv: Path | None = None,
    min_simulated_speed_kmh: float = 60.0,
    min_speed_error_kmh: float = 25.0,
    min_edge_length_m: float = 10.0,
    cap_margin_kmh: float = 20.0,
    min_cap_kmh: float = 35.0,
    max_cap_kmh: float = 45.0,
    excluded_edge_ids: set[str] | None = None,
    include_edge_ids: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], Path]:
    if edge_speed_csv is None:
        summary, _summary_path = latest_validation_summary()
        edge_speed_csv = project_path((summary.get("outputs") or {}).get("edge_speed_csv", ""))
    if not edge_speed_csv or not edge_speed_csv.is_file():
        raise ExpandedV7Error("missing_overopen_edge_speed_csv")
    excluded_edge_ids = excluded_edge_ids or set()
    targets: dict[str, dict[str, Any]] = {}
    for row in read_csv(edge_speed_csv):
        edge_id = row.get("edge_id", "")
        if not edge_id or edge_id in excluded_edge_ids:
            continue
        if include_edge_ids is not None and edge_id not in include_edge_ids:
            continue
        simulated_speed = safe_float(row.get("simulated_edge_speed_kmh"))
        reference_speed = safe_float(row.get("reference_segment_speed_kmh"))
        speed_error = safe_float(row.get("speed_error_kmh"))
        edge_length = safe_float(row.get("edge_length_m"))
        if edge_length < min_edge_length_m:
            continue
        if simulated_speed < min_simulated_speed_kmh or speed_error < min_speed_error_kmh:
            continue
        cap = max(min_cap_kmh, min(max_cap_kmh, reference_speed + cap_margin_kmh))
        existing = targets.get(edge_id)
        if existing:
            existing["source_segment_ids"].add(row.get("segment_id", ""))
            existing["source_directions"].add(row.get("direction", ""))
            existing["observed_row_count"] += 1
            existing["max_simulated_speed_kmh"] = max(existing["max_simulated_speed_kmh"], simulated_speed)
            existing["max_speed_error_kmh"] = max(existing["max_speed_error_kmh"], speed_error)
            existing["speed_cap_kmh"] = min(existing["speed_cap_kmh"], cap)
            continue
        targets[edge_id] = {
            "source_segment_ids": {row.get("segment_id", "")},
            "source_directions": {row.get("direction", "")},
            "reference_speed_kmh": reference_speed,
            "max_simulated_speed_kmh": simulated_speed,
            "max_speed_error_kmh": speed_error,
            "edge_length_m": edge_length,
            "speed_cap_kmh": cap,
            "observed_row_count": 1,
        }
    for target in targets.values():
        target["source_segment_ids"] = " ".join(sorted(item for item in target["source_segment_ids"] if item))
        target["source_directions"] = " ".join(sorted(item for item in target["source_directions"] if item))
    return targets, edge_speed_csv


def build_overopen_metering_speedcap_net(
    input_net: Path | None = None,
    output_net: Path = OVEROPEN_METERING_NET,
    edge_speed_csv: Path | None = None,
    selected_for_manifest: bool = False,
    summary_json: Path = OVEROPEN_METERING_SUMMARY_JSON,
    exclude_firetruck_route_edges: bool = True,
    min_simulated_speed_kmh: float = 60.0,
    min_speed_error_kmh: float = 25.0,
) -> dict[str, Any]:
    source_net = input_net or (RELEASE_SPEEDCAP_NET if RELEASE_SPEEDCAP_NET.is_file() else (TLS_FIXED_NET if TLS_FIXED_NET.is_file() else REPAIRED_NET))
    ensure_isolated_output(output_net)
    if not source_net.is_file():
        raise ExpandedV7Error(f"missing_overopen_metering_input_net:{rel(source_net)}")
    excluded_route_edges: set[str] = set()
    if exclude_firetruck_route_edges and ACCEPTED_ROUTES_CSV.is_file():
        for row in read_csv(ACCEPTED_ROUTES_CSV):
            excluded_route_edges.update(row.get("route_edges", "").split())
    targets, source_edge_speed_csv = overopen_speed_targets_from_validation(
        edge_speed_csv=edge_speed_csv,
        excluded_edge_ids=excluded_route_edges,
        min_simulated_speed_kmh=min_simulated_speed_kmh,
        min_speed_error_kmh=min_speed_error_kmh,
        cap_margin_kmh=15.0,
        min_cap_kmh=30.0,
        max_cap_kmh=FLOW_FREE_SPEED_KMH,
    )
    tree = ET.parse(source_net)
    root = tree.getroot()
    rows = []
    changed_lane_count = 0
    changed_edge_count = 0
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        target = targets.get(edge_id)
        if not target:
            continue
        cap_mps = float(target["speed_cap_kmh"]) / 3.6
        old_speeds = []
        edge_changed = False
        for lane in edge.findall("lane"):
            old_speed = safe_float(lane.get("speed"))
            if old_speed <= 0:
                continue
            old_speeds.append(old_speed * 3.6)
            if old_speed > cap_mps:
                lane.set("speed", f"{cap_mps:.6f}")
                changed_lane_count += 1
                edge_changed = True
        if edge_changed:
            changed_edge_count += 1
        rows.append({
            "edge_id": edge_id,
            "source_segment_ids": target["source_segment_ids"],
            "source_directions": target["source_directions"],
            "observed_row_count": target["observed_row_count"],
            "edge_length_m": round(float(target["edge_length_m"]), 6),
            "reference_speed_kmh": round(float(target["reference_speed_kmh"]), 6),
            "max_simulated_speed_kmh": round(float(target["max_simulated_speed_kmh"]), 6),
            "max_speed_error_kmh": round(float(target["max_speed_error_kmh"]), 6),
            "speed_cap_kmh": round(float(target["speed_cap_kmh"]), 6),
            "old_min_speed_kmh": round(min(old_speeds), 6) if old_speeds else "",
            "old_max_speed_kmh": round(max(old_speeds), 6) if old_speeds else "",
            "changed": edge_changed,
        })
    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="utf-8", xml_declaration=True)
    route_check = route_connectivity_on_net(output_net)
    load_check = sumo_net_load_check(output_net)
    csv_path = output_net.with_name("overopen_metering_speedcap_edges.csv")
    write_csv(csv_path, rows, [
        "edge_id", "source_segment_ids", "source_directions", "observed_row_count",
        "edge_length_m", "reference_speed_kmh", "max_simulated_speed_kmh",
        "max_speed_error_kmh", "speed_cap_kmh", "old_min_speed_kmh",
        "old_max_speed_kmh", "changed",
    ])
    summary = {
        "schema": "expanded_v7_overopen_metering_speedcap_net.v1",
        "generated_at": utc_now(),
        "input_net": rel(source_net),
        "output_net": rel(output_net),
        "source_edge_speed_csv": rel(source_edge_speed_csv),
        "speedcap_csv": rel(csv_path),
        "target_edge_count": len(targets),
        "written_edge_count": len(rows),
        "changed_edge_count": changed_edge_count,
        "changed_lane_count": changed_lane_count,
        "excluded_firetruck_route_edge_count": len(excluded_route_edges),
        "selected_for_manifest": selected_for_manifest,
        "sumo_net_load": load_check,
        "route_connectivity": route_check,
        "status": "PASS" if load_check["status"] == "PASS" and route_check.get("status") in {"PASS", "SKIP"} else "FAIL",
        "note": "V7-only narrow speed cap for edges that were actually observed as over-open in validation. Firetruck route edges are excluded.",
    }
    write_json(summary_json, summary)
    return summary


def build_plausibility_overopen_speedcap_net() -> dict[str, Any]:
    summary = build_overopen_metering_speedcap_net(
        input_net=LANE_DROP_FIXED_NET,
        output_net=PLAUSIBILITY_OVEROPEN_NET,
        selected_for_manifest=True,
        summary_json=PLAUSIBILITY_OVEROPEN_SUMMARY_JSON,
        exclude_firetruck_route_edges=False,
        min_simulated_speed_kmh=FLOW_FREE_SPEED_KMH,
        min_speed_error_kmh=FLOW_TARGET_TOLERANCE_KMH,
    )
    summary["schema"] = "expanded_v7_plausibility_overopen_speedcap_net.v1"
    summary["note"] = "V7 plausibility-first net: only currently over-open non-route edges are capped to reduce free-flow artifacts. This is not a demand recall calibration."
    write_json(PLAUSIBILITY_OVEROPEN_SUMMARY_JSON, summary)
    return summary


def build_route_edge_overopen_metering_speedcap_net(
    input_net: Path | None = None,
    output_net: Path = ROUTE_EDGE_OVEROPEN_METERING_NET,
    edge_speed_csv: Path | None = None,
    selected_for_manifest: bool = True,
) -> dict[str, Any]:
    source_net = input_net or (
        OVEROPEN_METERING_NET
        if OVEROPEN_METERING_NET.is_file()
        else (RELEASE_SPEEDCAP_NET if RELEASE_SPEEDCAP_NET.is_file() else (TLS_FIXED_NET if TLS_FIXED_NET.is_file() else REPAIRED_NET))
    )
    ensure_isolated_output(output_net)
    if not source_net.is_file():
        raise ExpandedV7Error(f"missing_route_edge_overopen_metering_input_net:{rel(source_net)}")
    route_edges = set(accepted_route_edges())
    if not route_edges:
        raise ExpandedV7Error("missing_accepted_route_edges_for_route_edge_overopen_metering")
    targets, source_edge_speed_csv = overopen_speed_targets_from_validation(
        edge_speed_csv=edge_speed_csv,
        min_simulated_speed_kmh=FLOW_FREE_SPEED_KMH,
        min_speed_error_kmh=FLOW_TARGET_TOLERANCE_KMH,
        min_edge_length_m=10.0,
        cap_margin_kmh=15.0,
        min_cap_kmh=30.0,
        max_cap_kmh=FLOW_FREE_SPEED_KMH,
        include_edge_ids=route_edges,
    )
    tree = ET.parse(source_net)
    root = tree.getroot()
    rows = []
    changed_lane_count = 0
    changed_edge_count = 0
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        target = targets.get(edge_id)
        if not target:
            continue
        cap_mps = float(target["speed_cap_kmh"]) / 3.6
        old_speeds = []
        edge_changed = False
        for lane in edge.findall("lane"):
            old_speed = safe_float(lane.get("speed"))
            if old_speed <= 0:
                continue
            old_speeds.append(old_speed * 3.6)
            if old_speed > cap_mps:
                lane.set("speed", f"{cap_mps:.6f}")
                changed_lane_count += 1
                edge_changed = True
        if edge_changed:
            changed_edge_count += 1
        rows.append({
            "edge_id": edge_id,
            "source_segment_ids": target["source_segment_ids"],
            "source_directions": target["source_directions"],
            "observed_row_count": target["observed_row_count"],
            "edge_length_m": round(float(target["edge_length_m"]), 6),
            "reference_speed_kmh": round(float(target["reference_speed_kmh"]), 6),
            "max_simulated_speed_kmh": round(float(target["max_simulated_speed_kmh"]), 6),
            "max_speed_error_kmh": round(float(target["max_speed_error_kmh"]), 6),
            "speed_cap_kmh": round(float(target["speed_cap_kmh"]), 6),
            "old_min_speed_kmh": round(min(old_speeds), 6) if old_speeds else "",
            "old_max_speed_kmh": round(max(old_speeds), 6) if old_speeds else "",
            "changed": edge_changed,
            "reason": "firetruck_route_edge_overopen_35kmh_congestion_recall",
        })
    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="utf-8", xml_declaration=True)
    route_check = route_connectivity_on_net(output_net)
    load_check = sumo_net_load_check(output_net)
    csv_path = output_net.with_name("route_edge_overopen_metering_speedcap_edges.csv")
    write_csv(csv_path, rows, [
        "edge_id", "source_segment_ids", "source_directions", "observed_row_count",
        "edge_length_m", "reference_speed_kmh", "max_simulated_speed_kmh",
        "max_speed_error_kmh", "speed_cap_kmh", "old_min_speed_kmh",
        "old_max_speed_kmh", "changed", "reason",
    ])
    summary = {
        "schema": "expanded_v7_route_edge_overopen_metering_speedcap_net.v1",
        "generated_at": utc_now(),
        "input_net": rel(source_net),
        "output_net": rel(output_net),
        "source_edge_speed_csv": rel(source_edge_speed_csv),
        "speedcap_csv": rel(csv_path),
        "target_edge_count": len(targets),
        "written_edge_count": len(rows),
        "changed_edge_count": changed_edge_count,
        "changed_lane_count": changed_lane_count,
        "accepted_route_edge_count": len(route_edges),
        "free_flow_threshold_kmh": FLOW_FREE_SPEED_KMH,
        "min_cap_kmh": 30.0,
        "max_cap_kmh": FLOW_FREE_SPEED_KMH,
        "cap_margin_kmh": 15.0,
        "selected_for_manifest": selected_for_manifest,
        "sumo_net_load": load_check,
        "route_connectivity": route_check,
        "status": "PASS" if load_check["status"] == "PASS" and route_check.get("status") in {"PASS", "SKIP"} else "FAIL",
        "note": "V7-only B0 reality baseline candidate. Unlike the previous overopen metering, accepted firetruck route edges are included because B0 speed recall is evaluated on that route too.",
    }
    write_json(ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON, summary)
    return summary


def build_release_junction_fixed_net(
    input_net: Path | None = None,
    output_net: Path = RELEASE_JUNCTION_FIXED_NET,
    selected_for_manifest: bool = True,
) -> dict[str, Any]:
    source_net = input_net or (
        ROUTE_EDGE_OVEROPEN_METERING_NET
        if ROUTE_EDGE_OVEROPEN_METERING_NET.is_file()
        else (OVEROPEN_METERING_NET if OVEROPEN_METERING_NET.is_file() else (TLS_FIXED_NET if TLS_FIXED_NET.is_file() else REPAIRED_NET))
    )
    ensure_isolated_output(output_net)
    if not source_net.is_file():
        raise ExpandedV7Error(f"missing_release_junction_fixed_input_net:{rel(source_net)}")
    route_edges = accepted_route_edges()
    route_pairs = set(zip(route_edges, route_edges[1:], strict=False))
    if not route_pairs:
        raise ExpandedV7Error("missing_accepted_route_pairs_for_release_junction_fix")
    tree = ET.parse(source_net)
    root = tree.getroot()
    rows: list[dict[str, Any]] = []
    changed_connection_count = 0
    changed_edge_priority_count = 0
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        if edge_id not in RELEASE_CONNECTION_PRIORITY_EDGE_IDS:
            continue
        old_priority = edge.get("priority", "")
        if safe_float(old_priority, 0.0) < 10.0:
            edge.set("priority", "10")
            changed_edge_priority_count += 1
            rows.append({
                "change_type": "edge_priority",
                "from_edge": edge_id,
                "to_edge": "",
                "tl": "",
                "link_index": "",
                "old_state": "",
                "new_state": "",
                "old_pass": "",
                "new_pass": "",
                "old_keepClear": "",
                "new_keepClear": "",
                "old_priority": old_priority,
                "new_priority": "10",
                "reason": "release_axis_priority_stabilization",
            })
    for connection in root.findall("connection"):
        from_edge = connection.get("from", "")
        to_edge = connection.get("to", "")
        if (from_edge, to_edge) not in route_pairs:
            continue
        if from_edge not in RELEASE_CONNECTION_PRIORITY_EDGE_IDS and to_edge not in RELEASE_CONNECTION_PRIORITY_EDGE_IDS:
            continue
        old_state = connection.get("state", "")
        old_pass = connection.get("pass", "")
        old_keep_clear = connection.get("keepClear", "")
        new_state = old_state
        if connection.get("tl") in {"", None} and old_state in {"m", "o"}:
            new_state = old_state.upper()
            connection.set("state", new_state)
        connection.set("pass", "true")
        connection.set("keepClear", "false")
        changed = (
            old_state != new_state
            or old_pass != "true"
            or old_keep_clear != "false"
        )
        if changed:
            changed_connection_count += 1
        rows.append({
            "change_type": "connection_priority",
            "from_edge": from_edge,
            "to_edge": to_edge,
            "tl": connection.get("tl", ""),
            "link_index": connection.get("linkIndex", ""),
            "old_state": old_state,
            "new_state": new_state,
            "old_pass": old_pass,
            "new_pass": connection.get("pass", ""),
            "old_keepClear": old_keep_clear,
            "new_keepClear": connection.get("keepClear", ""),
            "old_priority": "",
            "new_priority": "",
            "reason": "firetruck_route_release_connection_priority",
        })
    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="utf-8", xml_declaration=True)
    route_check = route_connectivity_on_net(output_net)
    load_check = sumo_net_load_check(output_net)
    csv_path = output_net.with_name("release_junction_fixed_changes.csv")
    write_csv(csv_path, rows, [
        "change_type", "from_edge", "to_edge", "tl", "link_index",
        "old_state", "new_state", "old_pass", "new_pass",
        "old_keepClear", "new_keepClear", "old_priority", "new_priority", "reason",
    ])
    summary = {
        "schema": "expanded_v7_release_junction_fixed_net.v1",
        "generated_at": utc_now(),
        "input_net": rel(source_net),
        "output_net": rel(output_net),
        "changes_csv": rel(csv_path),
        "changed_connection_count": changed_connection_count,
        "changed_edge_priority_count": changed_edge_priority_count,
        "target_edge_count": len(RELEASE_CONNECTION_PRIORITY_EDGE_IDS),
        "selected_for_manifest": selected_for_manifest,
        "sumo_net_load": load_check,
        "route_connectivity": route_check,
        "status": "PASS" if load_check["status"] == "PASS" and route_check.get("status") in {"PASS", "SKIP"} else "FAIL",
        "note": "V7-only release-junction candidate. It does not add more green time; it stabilizes selected firetruck-route release connections and non-TLS priorities where complete stop-flow formed.",
    }
    write_json(RELEASE_JUNCTION_FIXED_SUMMARY_JSON, summary)
    return summary


def build_release_speedcap_net(
    input_net: Path | None = None,
    output_net: Path = RELEASE_SPEEDCAP_NET,
    cap_kmh: float = 35.0,
    selected_for_manifest: bool = True,
) -> dict[str, Any]:
    source_net = input_net or (TLS_FIXED_NET if TLS_FIXED_NET.is_file() else REPAIRED_NET)
    ensure_isolated_output(output_net)
    if not source_net.is_file():
        raise ExpandedV7Error(f"missing_release_speedcap_input_net:{rel(source_net)}")
    tree = ET.parse(source_net)
    root = tree.getroot()
    cap_mps = cap_kmh / 3.6
    rows = []
    changed_edge_count = 0
    changed_lane_count = 0
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        if edge_id not in RELEASE_SPEEDCAP_EDGE_IDS:
            continue
        old_speeds = []
        edge_changed = False
        for lane in edge.findall("lane"):
            old_speed = safe_float(lane.get("speed"))
            if old_speed <= 0:
                continue
            old_speeds.append(old_speed * 3.6)
            if old_speed > cap_mps:
                lane.set("speed", f"{cap_mps:.6f}")
                changed_lane_count += 1
                edge_changed = True
        if edge_changed:
            changed_edge_count += 1
        rows.append({
            "edge_id": edge_id,
            "old_min_speed_kmh": round(min(old_speeds), 6) if old_speeds else "",
            "old_max_speed_kmh": round(max(old_speeds), 6) if old_speeds else "",
            "speed_cap_kmh": cap_kmh,
            "changed": edge_changed,
            "reason": "release_group_over_open_speed_cap",
        })
    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="utf-8", xml_declaration=True)
    route_check = route_connectivity_on_net(output_net)
    load_check = sumo_net_load_check(output_net)
    csv_path = output_net.with_name("release_speedcap_edges.csv")
    write_csv(csv_path, rows, [
        "edge_id", "old_min_speed_kmh", "old_max_speed_kmh", "speed_cap_kmh", "changed", "reason",
    ])
    summary = {
        "schema": "expanded_v7_release_speedcap_net.v1",
        "generated_at": utc_now(),
        "input_net": rel(source_net),
        "output_net": rel(output_net),
        "speedcap_csv": rel(csv_path),
        "cap_kmh": cap_kmh,
        "target_edges": sorted(RELEASE_SPEEDCAP_EDGE_IDS),
        "target_edge_count": len(RELEASE_SPEEDCAP_EDGE_IDS),
        "written_edge_count": len(rows),
        "changed_edge_count": changed_edge_count,
        "changed_lane_count": changed_lane_count,
        "selected_for_manifest": selected_for_manifest,
        "sumo_net_load": load_check,
        "route_connectivity": route_check,
        "status": "PASS" if load_check["status"] == "PASS" and route_check.get("status") in {"PASS", "SKIP"} else "FAIL",
        "note": "V7-only targeted speed cap for over-open bottleneck approach edges. It is narrower than the main-road speedcap and avoids reducing capacity on the already slow downstream release edges.",
    }
    write_json(RELEASE_SPEEDCAP_SUMMARY_JSON, summary)
    return summary


def route_tls_short_green_targets(
    input_net: Path = REPAIRED_NET,
    accepted_routes: Path = ACCEPTED_ROUTES_CSV,
    min_green_sec: int = 18,
    severe_green_sec: int = 24,
) -> list[dict[str, Any]]:
    if not accepted_routes.is_file():
        raise ExpandedV7Error(f"missing_accepted_routes:{rel(accepted_routes)}")
    rows = read_csv(accepted_routes)
    if not rows or not rows[0].get("route_edges"):
        raise ExpandedV7Error("missing_accepted_route_edges")
    route_edges = rows[0]["route_edges"].split()
    sumo_net = read_sumo_net(input_net)
    root = ET.parse(input_net).getroot()
    tls_phases = {
        tls.get("id"): list(tls.findall("phase"))
        for tls in root.findall("tlLogic")
    }
    seen: set[tuple[str, int]] = set()
    targets: list[dict[str, Any]] = []
    for route_index, (from_edge, to_edge) in enumerate(zip(route_edges, route_edges[1:], strict=False)):
        edge = sumo_net.getEdge(from_edge)
        for outgoing in edge.getOutgoing().values():
            connections = outgoing if isinstance(outgoing, list) else [outgoing]
            for connection in connections:
                if connection.getTo().getID() != to_edge:
                    continue
                tls_id = connection.getTLSID()
                link_index = connection.getTLLinkIndex()
                if not tls_id or link_index is None or link_index < 0:
                    continue
                key = (tls_id, int(link_index))
                if key in seen:
                    continue
                seen.add(key)
                green_sec = phase_link_green_seconds(tls_phases.get(tls_id, []), int(link_index))
                if green_sec < min_green_sec:
                    targets.append({
                        "route_index": route_index,
                        "from_edge": from_edge,
                        "to_edge": to_edge,
                        "tls_id": tls_id,
                        "link_index": int(link_index),
                        "current_green_sec": green_sec,
                        "target_green_sec": severe_green_sec if green_sec < 10 else min_green_sec,
                        "direction": connection.getDirection(),
                        "from_lane": connection.getFromLane().getIndex(),
                        "to_lane": connection.getToLane().getIndex(),
                    })
    targets.sort(key=lambda row: (row["tls_id"], row["link_index"], row["route_index"]))
    return targets


def apply_tls_green_targets(
    tls: ET.Element,
    targets: list[dict[str, Any]],
    min_phase_green_sec: int = 6,
) -> dict[str, Any]:
    tls_id = str(tls.get("id"))
    phases = list(tls.findall("phase"))
    if not phases:
        raise ExpandedV7Error(f"tls_has_no_phases:{tls_id}")
    before = [
        {"index": index, "duration": int(float(phase.get("duration", "0") or 0)), "state": phase.get("state", "")}
        for index, phase in enumerate(phases)
    ]
    before_cycle = sum(row["duration"] for row in before)
    target_links = sorted({int(row["link_index"]) for row in targets})
    before_link_green = {str(link): phase_link_green_seconds(phases, link) for link in target_links}
    before_link_yellow = {str(link): phase_link_yellow_seconds(phases, link) for link in target_links}
    adjustments: list[dict[str, Any]] = []
    protected_phase_indices: set[int] = set()
    total_added = 0
    for link in target_links:
        link_target_green_sec = max(
            int(row["target_green_sec"])
            for row in targets
            if int(row["link_index"]) == link
        )
        current_green = phase_link_green_seconds(phases, link)
        if current_green >= link_target_green_sec:
            continue
        green_phase_indices = [
            index for index, phase in enumerate(phases)
            if link < len(phase.get("state", "")) and phase.get("state", "")[link] in {"G", "g"}
        ]
        if not green_phase_indices:
            raise ExpandedV7Error(f"target_link_has_no_green_phase:{tls_id}:{link}")
        phase_index = green_phase_indices[0]
        protected_phase_indices.add(phase_index)
        delta = link_target_green_sec - current_green
        phase = phases[phase_index]
        old_duration = int(float(phase.get("duration", "0") or 0))
        phase.set("duration", str(old_duration + delta))
        total_added += delta
        adjustments.append({
            "phase_index": phase_index,
            "old_duration": old_duration,
            "new_duration": old_duration + delta,
            "delta": delta,
            "reason": f"increase_route_link_{link}_green",
        })
    remaining = total_added
    donor_indices = [
        index for index, phase in enumerate(phases)
        if index not in protected_phase_indices
        and any(char in {"G", "g"} for char in phase.get("state", ""))
        and not any(char in {"y", "Y"} for char in phase.get("state", ""))
    ]
    donor_indices.sort(key=lambda index: int(float(phases[index].get("duration", "0") or 0)), reverse=True)
    for donor_index in donor_indices:
        if remaining <= 0:
            break
        donor = phases[donor_index]
        old_duration = int(float(donor.get("duration", "0") or 0))
        reducible = max(0, old_duration - min_phase_green_sec)
        if reducible <= 0:
            continue
        take = min(reducible, remaining)
        donor.set("duration", str(old_duration - take))
        remaining -= take
        adjustments.append({
            "phase_index": donor_index,
            "old_duration": old_duration,
            "new_duration": old_duration - take,
            "delta": -take,
            "reason": f"donor_non_route_green_min_{min_phase_green_sec}s",
        })
    if remaining:
        raise ExpandedV7Error(f"not_enough_donor_green_seconds:{tls_id}:{remaining}")
    after = [
        {"index": index, "duration": int(float(phase.get("duration", "0") or 0)), "state": phase.get("state", "")}
        for index, phase in enumerate(phases)
    ]
    after_cycle = sum(row["duration"] for row in after)
    if after_cycle != before_cycle:
        raise ExpandedV7Error(f"tls_cycle_changed:{tls_id}:{before_cycle}->{after_cycle}")
    return {
        "tls_id": tls_id,
        "target_links": target_links,
        "target_connections": [
            f"{row['from_edge']} -> {row['to_edge']}@{row['link_index']}"
            for row in targets
        ],
        "before_cycle_sec": before_cycle,
        "after_cycle_sec": after_cycle,
        "before_link_green_sec": before_link_green,
        "after_link_green_sec": {str(link): phase_link_green_seconds(phases, link) for link in target_links},
        "before_link_yellow_sec": before_link_yellow,
        "after_link_yellow_sec": {str(link): phase_link_yellow_seconds(phases, link) for link in target_links},
        "phase_duration_before": before,
        "phase_duration_after": after,
        "phase_adjustments": adjustments,
    }


def fix_downstream_tls_green_split(
    input_net: Path = REPAIRED_NET,
    output_net: Path = TLS_FIXED_NET,
    min_green_sec: int = 18,
    severe_green_sec: int = 24,
) -> dict[str, Any]:
    ensure_isolated_output(output_net)
    if not input_net.is_file():
        raise ExpandedV7Error(f"missing_input_net:{rel(input_net)}")
    tree = ET.parse(input_net)
    root = tree.getroot()
    tls_by_id = {tls.get("id"): tls for tls in root.findall("tlLogic")}
    targets = route_tls_short_green_targets(input_net, min_green_sec=min_green_sec, severe_green_sec=severe_green_sec)
    if not targets:
        raise ExpandedV7Error("no_short_route_tls_green_targets_found")
    targets_by_tls: dict[str, list[dict[str, Any]]] = {}
    for target in targets:
        targets_by_tls.setdefault(str(target["tls_id"]), []).append(target)
    fixed_tls = []
    for tls_id, tls_targets in sorted(targets_by_tls.items()):
        tls = tls_by_id.get(tls_id)
        if tls is None:
            raise ExpandedV7Error(f"tls_not_found:{tls_id}")
        fixed_tls.append(apply_tls_green_targets(tls, tls_targets))
    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="utf-8", xml_declaration=True)
    route_check = route_connectivity_on_net(output_net)
    load_check = sumo_net_load_check(output_net)
    primary = next((row for row in fixed_tls if row["tls_id"] == DOWNSTREAM_TLS_ID), fixed_tls[0])
    summary = {
        "schema": "expanded_v7_route_tls_green_split_fix.v2",
        "generated_at": utc_now(),
        "input_net": rel(input_net),
        "output_net": rel(output_net),
        "min_route_green_sec": min_green_sec,
        "severe_route_green_sec": severe_green_sec,
        "fixed_tls_count": len(fixed_tls),
        "fixed_target_count": len(targets),
        "fixed_tls": fixed_tls,
        "short_green_targets": targets,
        "tls_id": DOWNSTREAM_TLS_ID,
        "target_connection": "781985787#0 -> 218915135#3",
        "target_link_index": DOWNSTREAM_TLS_TARGET_LINK,
        "related_link_indices": DOWNSTREAM_TLS_RELATED_LINKS,
        "before_cycle_sec": primary["before_cycle_sec"],
        "after_cycle_sec": primary["after_cycle_sec"],
        "cycle_status": "PASS" if all(row["before_cycle_sec"] == row["after_cycle_sec"] for row in fixed_tls) else "FAIL",
        "before_link_green_sec": primary["before_link_green_sec"],
        "after_link_green_sec": primary["after_link_green_sec"],
        "before_link_yellow_sec": primary["before_link_yellow_sec"],
        "after_link_yellow_sec": primary["after_link_yellow_sec"],
        "phase_duration_before": primary["phase_duration_before"],
        "phase_duration_after": primary["phase_duration_after"],
        "phase_adjustments": [adjustment for row in fixed_tls for adjustment in row["phase_adjustments"]],
        "sumo_net_load": load_check,
        "route_connectivity": route_check,
        "status": "PASS" if load_check["status"] == "PASS" and route_check.get("status") in {"PASS", "SKIP"} else "FAIL",
    }
    write_json(TLS_FIX_SUMMARY_JSON, summary)
    return summary


def edge_lonlat_shape(sumo_net: Any, edge: Any) -> list[list[float]]:
    shape = edge.getShape() or (edge.getLanes()[0].getShape() if edge.getLanes() else [])
    coords = []
    for x, y in shape:
        lon, lat = sumo_net.convertXY2LonLat(float(x), float(y))
        coords.append([float(lon), float(lat)])
    return coords


def edge_centroid_latlon(sumo_net: Any, edge: Any) -> tuple[float, float]:
    coords = edge_lonlat_shape(sumo_net, edge)
    if not coords:
        x, y = edge.getFromNode().getCoord()
        lon, lat = sumo_net.convertXY2LonLat(float(x), float(y))
        return float(lat), float(lon)
    return sum(point[1] for point in coords) / len(coords), sum(point[0] for point in coords) / len(coords)


def nearest_passenger_edges(sumo_net: Any, lat: float, lon: float, limit: int = 12, radius_m: float = 600.0) -> list[dict[str, Any]]:
    rows = []
    for edge in sumo_net.getEdges():
        if edge.isSpecial() or not edge.allows("passenger"):
            continue
        edge_lat, edge_lon = edge_centroid_latlon(sumo_net, edge)
        distance = haversine_distance_m(lat, lon, edge_lat, edge_lon)
        if distance <= radius_m:
            rows.append({"edge_id": edge.getID(), "lat": edge_lat, "lon": edge_lon, "distance_m": distance, "lane_count": edge.getLaneNumber()})
    rows.sort(key=lambda row: row["distance_m"])
    return rows[:limit]


def route_connected(sumo_net: Any, edge_ids: list[str]) -> bool:
    if not edge_ids:
        return False
    for edge_id in edge_ids:
        try:
            sumo_net.getEdge(edge_id)
        except Exception:
            return False
    for from_id, to_id in zip(edge_ids, edge_ids[1:], strict=False):
        if sumo_net.getEdge(to_id) not in sumo_net.getEdge(from_id).getOutgoing():
            return False
    return True


def route_shape(sumo_net: Any, edge_ids: list[str]) -> list[list[float]]:
    coords: list[list[float]] = []
    for edge_id in edge_ids:
        try:
            edge = sumo_net.getEdge(edge_id)
        except Exception:
            continue
        shape = edge_lonlat_shape(sumo_net, edge)
        if not coords:
            coords.extend([[latlon[1], latlon[0]] for latlon in shape])
        else:
            coords.extend([[latlon[1], latlon[0]] for latlon in shape[1:]])
    return coords


def make_route_row(
    sumo_net: Any,
    start_edge: str,
    target_edge: str,
    policy: str,
    edge_ids: list[str],
    shortest_length: float,
    spine_ids: set[str],
    spine_metrics: dict[str, dict[str, float]],
) -> dict[str, Any]:
    s07 = step07_module()
    metrics = s07.route_spine_metrics(sumo_net, edge_ids, shortest_length, spine_ids, spine_metrics)
    route_length = float(metrics["route_length_m"])
    score = 1000.0 * float(metrics["spine_length_ratio"]) + float(metrics["max_consecutive_spine_length_m"]) - 30.0 * float(metrics["length_increase_ratio"])
    route_id = "FIRETRUCK_TO_SEOUL_STATION_FRONT"
    candidate_id = f"{route_id}_{policy.upper()}"
    return {
        "route_id": route_id,
        "scenario_id": "FIRETRUCK_SEOUL_STATION_FRONT",
        "destination_id": "SEOUL_STATION_FRONT",
        "label_ko": "firetruck_to_seoul_station_front",
        "address": "Seoul Station front road edge",
        "lat": SEOUL_STATION_FRONT["lat"],
        "lon": SEOUL_STATION_FRONT["lon"],
        "start_edge_id": start_edge,
        "target_edge_id": target_edge,
        "candidate_route_id": candidate_id,
        "candidate_policy": policy,
        "route_edges": " ".join(edge_ids),
        "route_edge_count": len(edge_ids),
        "route_length_m": round(route_length, 3),
        "length_increase_ratio": round(float(metrics["length_increase_ratio"]), 6),
        "spine_length_ratio": round(float(metrics["spine_length_ratio"]), 6),
        "max_consecutive_spine_length_m": round(float(metrics["max_consecutive_spine_length_m"]), 3),
        "selection_score": round(score, 6),
        "connected": route_connected(sumo_net, edge_ids),
        "route_shape": json.dumps(route_shape(sumo_net, edge_ids), ensure_ascii=False),
    }


def build_firetruck_route_candidates(net_file: Path = REPAIRED_NET) -> dict[str, Any]:
    s07 = step07_module()
    sumo_net = read_sumo_net(net_file)
    props_by_id = {}
    coords_by_id = {}
    if EXPANDED_EDGES_GEOJSON.is_file():
        props_by_id, coords_by_id = s07.load_edge_geojson(EXPANDED_EDGES_GEOJSON)
    else:
        export_edges_geojson(net_file, EXPANDED_EDGES_GEOJSON)
        props_by_id, coords_by_id = s07.load_edge_geojson(EXPANDED_EDGES_GEOJSON)
    config_like = {"locations": {"jungbu_fire_station": JUNGBU_FIRE_STATION, "seoul_station": SEOUL_STATION_FRONT}}
    axis = s07.bearing_vector(config_like)
    axis_ctx = s07.axis_context(config_like)
    _spine_rows, spine_ids, spine_metrics = s07.build_spine_edges(sumo_net, props_by_id, coords_by_id, axis_ctx)
    start_candidates = nearest_passenger_edges(sumo_net, JUNGBU_FIRE_STATION["lat"], JUNGBU_FIRE_STATION["lon"], limit=14, radius_m=700)
    if OLD_START_EDGE not in [row["edge_id"] for row in start_candidates]:
        try:
            edge = sumo_net.getEdge(OLD_START_EDGE)
            lat, lon = edge_centroid_latlon(sumo_net, edge)
            start_candidates.append({"edge_id": OLD_START_EDGE, "lat": lat, "lon": lon, "distance_m": haversine_distance_m(JUNGBU_FIRE_STATION["lat"], JUNGBU_FIRE_STATION["lon"], lat, lon), "lane_count": edge.getLaneNumber(), "fallback": True})
        except Exception:
            pass
    target_candidates = nearest_passenger_edges(sumo_net, SEOUL_STATION_FRONT["lat"], SEOUL_STATION_FRONT["lon"], limit=8, radius_m=500)
    rows: list[dict[str, Any]] = []
    for start in start_candidates:
        for target in target_candidates:
            start_id = start["edge_id"]
            target_id = target["edge_id"]
            try:
                shortest = s07.shortest_route(sumo_net, start_id, target_id)
                shortest_length = s07.route_length(sumo_net, shortest)
                major = s07.major_route(sumo_net, start_id, target_id, props_by_id, coords_by_id, axis)
                selected = s07.select_spine_route_v2(sumo_net, start_id, target_id, shortest, major, props_by_id, coords_by_id, axis, axis_ctx, spine_ids, spine_metrics)
                candidates = {
                    "shortest": shortest,
                    "major": major,
                    "max_toegye": list(selected["edge_ids"]),
                }
                for policy, edges in candidates.items():
                    row = make_route_row(sumo_net, start_id, target_id, policy, edges, shortest_length, spine_ids, spine_metrics)
                    row["start_distance_m"] = round(float(start["distance_m"]), 3)
                    row["target_distance_m"] = round(float(target["distance_m"]), 3)
                    row["screenshot_like_start_score"] = round(max(0.0, 250.0 - float(start["distance_m"])), 3)
                    rows.append(row)
            except Exception:
                continue
    if not rows:
        raise ExpandedV7Error("no_firetruck_route_candidates")
    rows.sort(key=lambda row: (row["candidate_policy"] != "max_toegye", -float(row["selection_score"]), float(row["start_distance_m"])))
    fields = [
        "route_id", "scenario_id", "destination_id", "label_ko", "address", "lat", "lon",
        "start_edge_id", "target_edge_id", "candidate_route_id", "candidate_policy", "route_edges",
        "route_edge_count", "route_length_m", "length_increase_ratio", "spine_length_ratio",
        "max_consecutive_spine_length_m", "selection_score", "connected", "start_distance_m",
        "target_distance_m", "screenshot_like_start_score", "route_shape",
    ]
    write_csv(ROUTE_CANDIDATES_CSV, rows, fields)
    summary = {
        "schema": "expanded_v7_firetruck_route_candidates.v1",
        "generated_at": utc_now(),
        "net_file": rel(net_file),
        "candidate_count": len(rows),
        "recommended_candidate_route_id": rows[0]["candidate_route_id"],
        "acceptance_json": rel(ROUTE_ACCEPTANCE_JSON),
    }
    write_json(ROUTE_CANDIDATES_CSV.with_suffix(".summary.json"), summary)
    write_route_review_html(ROUTE_REVIEW_HTML, rows, summary)
    return summary


def read_route_acceptance(path: Path = ROUTE_ACCEPTANCE_JSON) -> str:
    return str(read_route_acceptance_payload(path).get("accepted_candidate_route_id", ""))


def read_route_acceptance_payload(path: Path = ROUTE_ACCEPTANCE_JSON) -> dict[str, Any]:
    if not path.is_file():
        raise ExpandedV7Error(f"missing_route_acceptance_json:{rel(path)}")
    payload = read_json(path)
    candidate_id = str(payload.get("accepted_candidate_route_id") or payload.get("candidate_route_id") or "").strip()
    if not candidate_id:
        raise ExpandedV7Error(f"route_acceptance_missing_candidate_id:{rel(path)}")
    payload["accepted_candidate_route_id"] = candidate_id
    return payload


def firetruck_vtype_attrs() -> dict[str, str]:
    return {
        "id": "firetruck_emergency",
        "vClass": "emergency",
        "guiShape": "emergency",
        "color": "1,0,0",
        "length": "8.0",
        "width": "2.5",
        "accel": "1.8",
        "decel": "6.5",
        "speedFactor": "1.30",
        "speedDev": "0.00",
        "maxSpeed": f"{70.0 / 3.6:.6f}",
        "lcStrategic": "10.0",
        "lcCooperative": "0.0",
        "lcSpeedGain": "5.0",
        "lcKeepRight": "0.0",
        "lcAssertive": "5.0",
    }


def conservative_firetruck_vtype_attrs() -> dict[str, str]:
    return {
        "id": "firetruck_emergency_conservative_b0",
        "vClass": "emergency",
        "guiShape": "emergency",
        "color": "1,0,0",
        "length": "8.0",
        "width": "2.5",
        "accel": "1.5",
        "decel": "6.5",
        "speedFactor": "1.05",
        "speedDev": "0.00",
        "maxSpeed": f"{60.0 / 3.6:.6f}",
        "lcStrategic": "3.0",
        "lcCooperative": "0.7",
        "lcSpeedGain": "1.0",
        "lcKeepRight": "0.0",
        "lcAssertive": "1.0",
        "impatience": "0.2",
    }


def accepted_route_fields() -> list[str]:
    return [
        "route_id", "scenario_id", "destination_id", "label_ko", "address", "lat", "lon",
        "target_edge_id", "selected_policy", "source_candidate_route_id", "route_edges",
        "route_edge_count", "route_length_m", "spine_length_ratio", "max_consecutive_spine_length_m",
        "start_depart_pos",
    ]


def apply_firetruck_route_acceptance(candidates_csv: Path = ROUTE_CANDIDATES_CSV, acceptance_json: Path = ROUTE_ACCEPTANCE_JSON) -> dict[str, Any]:
    acceptance = read_route_acceptance_payload(acceptance_json)
    candidate_id = str(acceptance["accepted_candidate_route_id"])
    candidates = read_csv(candidates_csv)
    by_id = {row["candidate_route_id"]: row for row in candidates}
    if candidate_id not in by_id:
        raise ExpandedV7Error(f"accepted_candidate_not_found:{candidate_id}")
    row = by_id[candidate_id]
    route_edges = row["route_edges"].split()
    fixed_start_edge = str(acceptance.get("fixed_start_edge_id") or "").strip()
    start_join_edge = str(acceptance.get("start_join_edge_id") or "").strip()
    if fixed_start_edge and start_join_edge:
        if start_join_edge not in route_edges:
            raise ExpandedV7Error(f"start_join_edge_not_in_route:{start_join_edge}")
        route_edges = [fixed_start_edge] + route_edges[route_edges.index(start_join_edge):]
    trim_after_edge = str(acceptance.get("trimmed_after_edge_id") or acceptance.get("direct_station_target_edge_id") or "").strip()
    if trim_after_edge:
        if trim_after_edge not in route_edges:
            raise ExpandedV7Error(f"trim_after_edge_not_in_route:{trim_after_edge}")
        route_edges = route_edges[: route_edges.index(trim_after_edge) + 1]
    accepted = {
        "route_id": row["route_id"],
        "scenario_id": row["scenario_id"],
        "destination_id": row["destination_id"],
        "label_ko": row["label_ko"],
        "address": row["address"],
        "lat": row["lat"],
        "lon": row["lon"],
        "target_edge_id": trim_after_edge or row["target_edge_id"],
        "selected_policy": row["candidate_policy"] + ("_direct_station_front" if trim_after_edge else ""),
        "source_candidate_route_id": row["candidate_route_id"],
        "route_edges": " ".join(route_edges),
        "route_edge_count": len(route_edges),
        "route_length_m": round(sum_route_length(read_sumo_net(REPAIRED_NET), route_edges), 3),
        "spine_length_ratio": row["spine_length_ratio"],
        "max_consecutive_spine_length_m": row["max_consecutive_spine_length_m"],
        "start_depart_pos": str(acceptance.get("depart_pos") or ("0" if fixed_start_edge else "last")),
    }
    write_csv(ACCEPTED_ROUTES_CSV, [accepted], accepted_route_fields())
    write_firetruck_route_xml(ACCEPTED_ROUTE_XML, accepted)
    summary = {
        "schema": "expanded_v7_firetruck_accepted_route.v1",
        "generated_at": utc_now(),
        "accepted_candidate_route_id": candidate_id,
        "trimmed_after_edge_id": trim_after_edge,
        "fixed_start_edge_id": fixed_start_edge,
        "start_join_edge_id": start_join_edge,
        "accepted_routes_csv": rel(ACCEPTED_ROUTES_CSV),
        "route_xml": rel(ACCEPTED_ROUTE_XML),
        "route_edge_count": accepted["route_edge_count"],
        "route_length_m": accepted["route_length_m"],
    }
    write_json(DATA_ROOT / "routes/firetruck_route_summary.json", summary)
    return summary


def sum_route_length(sumo_net: Any, edge_ids: list[str]) -> float:
    total = 0.0
    for edge_id in edge_ids:
        try:
            total += float(sumo_net.getEdge(edge_id).getLength())
        except Exception:
            continue
    return total


def write_firetruck_route_xml(path: Path, accepted: dict[str, Any], vtype_attrs: dict[str, str] | None = None, conservative: bool = False) -> None:
    attrs = vtype_attrs or firetruck_vtype_attrs()
    root = ET.Element("routes")
    vtype = ET.SubElement(root, "vType", attrs)
    ET.SubElement(vtype, "param", {"key": "has.bluelight.device", "value": "false"})
    ET.SubElement(root, "route", {"id": accepted["route_id"], "edges": accepted["route_edges"]})
    vehicle_attrs = {
        "id": "firetruck_expanded_v7_B0",
        "type": attrs["id"],
        "route": accepted["route_id"],
        "depart": "600",
        "departLane": "best",
        "departPos": str(accepted.get("start_depart_pos") or "last"),
        "departSpeed": "0" if conservative else "max",
    }
    if not conservative:
        vehicle_attrs["type"] = "firetruck_emergency"
    ET.SubElement(
        root,
        "vehicle",
        vehicle_attrs,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def build_conservative_firetruck_route_xml(
    accepted_routes: Path = ACCEPTED_ROUTES_CSV,
    output_xml: Path = CONSERVATIVE_ROUTE_XML,
) -> dict[str, Any]:
    rows = read_csv(accepted_routes) if accepted_routes.is_file() else []
    if not rows:
        raise ExpandedV7Error(f"missing_accepted_route_for_conservative_xml:{rel(accepted_routes)}")
    accepted = rows[0]
    write_firetruck_route_xml(output_xml, accepted, conservative_firetruck_vtype_attrs(), conservative=True)
    payload = {
        "schema": "expanded_v7_conservative_firetruck_route_xml.v1",
        "generated_at": utc_now(),
        "route_xml": rel(output_xml),
        "accepted_routes_csv": rel(accepted_routes),
        "emergency_behavior_profile": "conservative_firetruck_b0",
        "vtype_attrs": conservative_firetruck_vtype_attrs(),
        "insertion_policy": "scheduled_departure_with_sumo_insertion_checks",
        "note_ko": "B0 보수적 baseline: 앞차를 밀지 않고, 막히면 departDelay/waitingTime을 허용합니다.",
    }
    write_json(output_xml.with_suffix(".summary.json"), payload)
    return payload


def reference_direction_targets(reference_csv: Path = REFERENCE_CSV) -> dict[str, float]:
    rows = read_csv(reference_csv)
    targets: dict[str, float] = {}
    for row in rows:
        volume = float(row["peak_hour_volume_veh_per_h_reference"])
        targets[f"{row['segment_id']}:upbound"] = volume
        targets[f"{row['segment_id']}:downbound"] = volume
    return targets


def demand_profile_local_share(profile: str) -> float:
    return float(demand_profile_settings(profile)["local_validation_share"])


def demand_profile_settings(profile: str) -> dict[str, Any]:
    normalized = profile.replace("+", "_")
    if normalized == "through_only":
        return {
            "profile": normalized,
            "main_pass_ratio": 1.0,
            "local_validation_share": 0.0,
            "sideflow_ratio": 0.075,
            "mapwide_background_ratio": 0.05,
            "pulse_active_multiplier": 1.0,
        }
    if normalized == "through_local_25":
        return {
            "profile": normalized,
            "main_pass_ratio": 1.0,
            "local_validation_share": 0.25,
            "sideflow_ratio": 0.075,
            "mapwide_background_ratio": 0.05,
            "pulse_active_multiplier": 1.0,
        }
    if normalized == "through_local_50":
        return {
            "profile": normalized,
            "main_pass_ratio": 1.0,
            "local_validation_share": 0.50,
            "sideflow_ratio": 0.075,
            "mapwide_background_ratio": 0.05,
            "pulse_active_multiplier": 1.0,
        }
    if normalized == "balanced_diversion":
        return {
            "profile": normalized,
            "main_pass_ratio": 0.55,
            "local_validation_share": 0.08,
            "sideflow_ratio": 0.05,
            "mapwide_background_ratio": 0.55,
            "pulse_active_multiplier": 1.35,
            "through_max_variants": 5,
            "avoid_bottleneck_internal_local_source_sink": False,
        }
    if normalized == "bottleneck_aware_diversion":
        return {
            "profile": normalized,
            "main_pass_ratio": 0.55,
            "local_validation_share": 0.08,
            "bottleneck_local_validation_share": 0.03,
            "sideflow_ratio": 0.05,
            "mapwide_background_ratio": 0.62,
            "pulse_active_multiplier": 1.25,
            "through_max_variants": 8,
            "avoid_bottleneck_internal_local_source_sink": True,
        }
    if normalized in {"balanced_congestion_v2", "balanced_congestion_v2_a", "balanced_congestion_v2_b", "balanced_congestion_v2_c"}:
        upbound_through = {
            "balanced_congestion_v2": 0.34,
            "balanced_congestion_v2_a": 0.34,
            "balanced_congestion_v2_b": 0.30,
            "balanced_congestion_v2_c": 0.38,
        }[normalized]
        return {
            "profile": normalized,
            "main_pass_ratio": 0.55,
            "local_validation_share": 0.08,
            "bottleneck_local_validation_share": 0.02,
            "sideflow_ratio": 0.05,
            "mapwide_background_ratio": 0.72,
            "pulse_active_multiplier": 1.15,
            "through_max_variants": 10,
            "avoid_bottleneck_internal_local_source_sink": True,
            "upbound_through_share": upbound_through,
            "downbound_through_share": 0.47,
            "downstream_sink_guard_enabled": True,
        }
    if normalized == "balanced_congestion_v7_plausibility_first":
        return {
            "profile": normalized,
            "main_pass_ratio": 0.42,
            "local_validation_share": 0.06,
            "bottleneck_local_validation_share": 0.005,
            "sideflow_ratio": 0.04,
            "mapwide_background_ratio": 0.46,
            "pulse_active_multiplier": 1.30,
            "through_max_variants": 32,
            "avoid_bottleneck_internal_local_source_sink": True,
            "strict_bottleneck_route_guard": True,
            "local_accounting_guard_enabled": True,
            "upbound_through_share": 0.18,
            "downbound_through_share": 0.36,
            "downstream_sink_guard_enabled": True,
            "terminal_sink_extension_v2_enabled": False,
            "terminal_sink_extension_v3_enabled": True,
            "terminal_sink_extension_v3_limit": 30,
            "distributed_boundary_enabled": True,
            "boundary_extension_limit": 25,
            "route_template_vehicle_cap": 60,
            "mapwide_template_vehicle_cap": 45,
            "mapwide_template_target_count": 70,
            "release_depart_gap_enabled": True,
            "release_depart_gap_sec": 6.0,
            "free_segment_feeder_enabled": True,
            "free_segment_feeder_share": 0.10,
            "short_edge_artifact_length_m": SHORT_EDGE_ARTIFACT_LENGTH_M,
            "short_edge_warn_length_m": SHORT_EDGE_WARN_LENGTH_M,
            "plausibility_first": True,
            "generated_demand_recall_is_report_only": True,
        }
    if normalized == "balanced_congestion_v8_stop_free_cleanup":
        return {
            "profile": normalized,
            "main_pass_ratio": 0.34,
            "local_validation_share": 0.045,
            "bottleneck_local_validation_share": 0.0,
            "sideflow_ratio": 0.035,
            "mapwide_background_ratio": 0.34,
            "pulse_active_multiplier": 1.35,
            "through_max_variants": 36,
            "avoid_bottleneck_internal_local_source_sink": True,
            "strict_bottleneck_route_guard": True,
            "local_accounting_guard_enabled": True,
            "upbound_through_share": 0.10,
            "downbound_through_share": 0.32,
            "downstream_sink_guard_enabled": True,
            "terminal_sink_extension_v2_enabled": False,
            "terminal_sink_extension_v3_enabled": True,
            "terminal_sink_extension_v3_limit": 30,
            "distributed_boundary_enabled": True,
            "boundary_extension_limit": 30,
            "route_template_vehicle_cap": 45,
            "mapwide_template_vehicle_cap": 35,
            "mapwide_template_target_count": 80,
            "release_depart_gap_enabled": True,
            "release_depart_gap_sec": 8.0,
            "free_segment_feeder_enabled": True,
            "free_segment_feeder_share": 0.14,
            "short_edge_artifact_length_m": SHORT_EDGE_ARTIFACT_LENGTH_M,
            "short_edge_warn_length_m": SHORT_EDGE_WARN_LENGTH_M,
            "plausibility_first": True,
            "generated_demand_recall_is_report_only": True,
        }
    if normalized in {"balanced_congestion_v3_a", "balanced_congestion_v3_027", "balanced_congestion_v3_tuned", "balanced_congestion_v3_up22", "balanced_congestion_v3_up20", "balanced_congestion_v3", "balanced_congestion_v3_c", "balanced_congestion_v3_down55", "balanced_congestion_v3_down60", "balanced_congestion_v3_down65", "balanced_congestion_v3_down75", "balanced_congestion_v4_smooth_release", "balanced_congestion_v5_distributed_boundary", "balanced_congestion_v6_boundary_fanout_only", "balanced_congestion_v6_release_gap", "balanced_congestion_v6_free_feeder", "balanced_congestion_v6_boundary_balancer"}:
        upbound_through = {
            "balanced_congestion_v3_a": 0.26,
            "balanced_congestion_v3_027": 0.27,
            "balanced_congestion_v3_tuned": 0.28,
            "balanced_congestion_v3_up22": 0.22,
            "balanced_congestion_v3_up20": 0.20,
            "balanced_congestion_v3": 0.30,
            "balanced_congestion_v3_c": 0.34,
            "balanced_congestion_v3_down55": 0.26,
            "balanced_congestion_v3_down60": 0.26,
            "balanced_congestion_v3_down65": 0.26,
            "balanced_congestion_v3_down75": 0.26,
            "balanced_congestion_v4_smooth_release": 0.26,
            "balanced_congestion_v5_distributed_boundary": 0.26,
            "balanced_congestion_v6_boundary_fanout_only": 0.26,
            "balanced_congestion_v6_release_gap": 0.26,
            "balanced_congestion_v6_free_feeder": 0.26,
            "balanced_congestion_v6_boundary_balancer": 0.26,
        }[normalized]
        downbound_through = {
            "balanced_congestion_v3_down55": 0.55,
            "balanced_congestion_v3_down60": 0.60,
            "balanced_congestion_v3_down65": 0.65,
            "balanced_congestion_v3_down75": 0.75,
        }.get(normalized, 0.47)
        bottleneck_local = {
            "balanced_congestion_v3_a": 0.015,
            "balanced_congestion_v3_027": 0.01,
            "balanced_congestion_v3_tuned": 0.01,
            "balanced_congestion_v3_up22": 0.005,
            "balanced_congestion_v3_up20": 0.0,
            "balanced_congestion_v3": 0.015,
            "balanced_congestion_v3_c": 0.0,
            "balanced_congestion_v3_down55": 0.015,
            "balanced_congestion_v3_down60": 0.015,
            "balanced_congestion_v3_down65": 0.015,
            "balanced_congestion_v3_down75": 0.015,
            "balanced_congestion_v4_smooth_release": 0.015,
            "balanced_congestion_v5_distributed_boundary": 0.015,
            "balanced_congestion_v6_boundary_fanout_only": 0.015,
            "balanced_congestion_v6_release_gap": 0.015,
            "balanced_congestion_v6_free_feeder": 0.015,
            "balanced_congestion_v6_boundary_balancer": 0.015,
        }[normalized]
        is_v6 = normalized.startswith("balanced_congestion_v6_")
        return {
            "profile": normalized,
            "main_pass_ratio": 0.55,
            "local_validation_share": 0.08,
            "bottleneck_local_validation_share": bottleneck_local,
            "sideflow_ratio": 0.05,
            "mapwide_background_ratio": 0.78,
            "pulse_active_multiplier": 1.02 if is_v6 else 1.05,
            "through_max_variants": 24 if is_v6 else (18 if normalized == "balanced_congestion_v5_distributed_boundary" else 12),
            "avoid_bottleneck_internal_local_source_sink": True,
            "strict_bottleneck_route_guard": True,
            "local_accounting_guard_enabled": True,
            "upbound_through_share": upbound_through,
            "downbound_through_share": downbound_through,
            "downstream_sink_guard_enabled": True,
            "terminal_sink_extension_v2_enabled": normalized in {"balanced_congestion_v4_smooth_release", "balanced_congestion_v5_distributed_boundary"},
            "terminal_sink_extension_v3_enabled": is_v6,
            "terminal_sink_extension_v3_limit": 30 if is_v6 else "",
            "distributed_boundary_enabled": normalized == "balanced_congestion_v5_distributed_boundary" or is_v6,
            "boundary_extension_limit": 20 if is_v6 else (10 if normalized == "balanced_congestion_v5_distributed_boundary" else 5),
            "route_template_vehicle_cap": 80 if is_v6 else (120 if normalized == "balanced_congestion_v5_distributed_boundary" else ""),
            "mapwide_template_vehicle_cap": 60 if is_v6 else (80 if normalized == "balanced_congestion_v5_distributed_boundary" else ""),
            "mapwide_template_target_count": 60 if is_v6 else (48 if normalized == "balanced_congestion_v5_distributed_boundary" else ""),
            "release_depart_gap_enabled": normalized in {"balanced_congestion_v6_release_gap", "balanced_congestion_v6_free_feeder", "balanced_congestion_v6_boundary_balancer"},
            "release_depart_gap_sec": 4.0 if normalized in {"balanced_congestion_v6_release_gap", "balanced_congestion_v6_free_feeder", "balanced_congestion_v6_boundary_balancer"} else "",
            "free_segment_feeder_enabled": normalized in {"balanced_congestion_v6_free_feeder", "balanced_congestion_v6_boundary_balancer"},
            "free_segment_feeder_share": 0.035 if normalized in {"balanced_congestion_v6_free_feeder", "balanced_congestion_v6_boundary_balancer"} else "",
            "short_edge_artifact_length_m": SHORT_EDGE_ARTIFACT_LENGTH_M,
            "short_edge_warn_length_m": SHORT_EDGE_WARN_LENGTH_M,
        }
    raise ExpandedV7Error(f"unknown_demand_profile:{profile}")


def build_b0_demand(
    reference_csv: Path = REFERENCE_CSV,
    mapping_csv: Path = MAPPING_CSV,
    net_file: Path | None = None,
    profile: str = "balanced_congestion_v3",
) -> dict[str, Any]:
    settings = demand_profile_settings(profile)
    net_file = net_file or active_b0_net()
    segment_rows, summary = build_expanded_screenline_demand(
        reference_csv,
        mapping_csv,
        net_file,
        DEMAND_XML,
        duration_sec=3600.0,
        profile=profile,
    )
    write_csv(DEMAND_SUMMARY_CSV, segment_rows, [
        "segment_id", "direction", "target_count", "generated_template_count", "generated_recall",
        "mainline_generated_count", "mainline_generated_recall", "through_generated_count",
        "local_generated_count", "feeder_generated_count", "accounted_local_count",
        "route_guarded_local_count", "template_cap_diverted_count", "diversion_assigned_count",
        "local_skipped_by_source_cap", "remaining_count",
    ])
    write_csv(DEMAND_PROFILE_SUMMARY_CSV, summary.get("flow_rows", []), [
        "flow_id", "profile", "flow_type", "direction", "segment_scope", "vph", "vehicle_count",
        "skipped_by_source_cap", "route_edge_count", "start_edge", "target_edge",
        "accounted_only", "route_guard_reason", "route_uses_forbidden_bottleneck",
        "boundary_extension_applied", "boundary_extension_reason",
        "terminal_sink_extension_v2_applied", "terminal_sink_extension_v2_reason",
        "terminal_sink_extension_v3_applied", "terminal_sink_extension_v3_reason",
        "release_depart_gap_applied_count", "template_cap_diverted_count",
        "free_segment_feeder_reason",
        "distribution_variant_index", "distribution_variant_count",
        "reference_speed_kmh", "reference_travel_time_s", "reference_offset_sec",
        "timing_profile", "pulse_cycle_sec", "pulse_active_fraction",
    ])
    write_csv(SOURCE_ASSIGNMENT_SUMMARY_CSV, summary.get("source_rows", []), [
        "source_edge", "source_lanes", "vehicle_count", "source_cap_per_hour", "over_cap",
    ])
    side_rows, side_summary = append_sideflow_demand(DEMAND_XML, net_file, mapping_csv, ratio=float(settings["sideflow_ratio"]))
    write_csv(SIDEFLOW_SUMMARY_CSV, side_rows, ["sideflow_id", "source_edge", "sink_edge", "route_edges", "depart", "reason"])
    mapwide_rows, mapwide_summary = append_mapwide_background_demand(
        DEMAND_XML,
        net_file,
        mapping_csv,
        ratio=float(settings["mapwide_background_ratio"]),
        template_vehicle_cap=safe_int(settings.get("mapwide_template_vehicle_cap"), 0) or None,
        template_target_count=safe_int(settings.get("mapwide_template_target_count"), 0) or None,
        distributed_boundary=bool(settings.get("distributed_boundary_enabled", False)),
    )
    write_csv(MAPWIDE_SUMMARY_CSV, mapwide_rows, [
        "mapwide_id", "source_edge", "sink_edge", "route_edges", "route_edge_count", "depart", "reason",
    ])
    summary = {
        **summary,
        "schema": "expanded_v7_main_sideflow_demand.v1",
        "sideflow": side_summary,
        "mapwide_background": mapwide_summary,
        "demand_summary_csv": rel(DEMAND_SUMMARY_CSV),
        "demand_profile_summary_csv": rel(DEMAND_PROFILE_SUMMARY_CSV),
        "source_assignment_summary_csv": rel(SOURCE_ASSIGNMENT_SUMMARY_CSV),
        "sideflow_summary_csv": rel(SIDEFLOW_SUMMARY_CSV),
        "mapwide_summary_csv": rel(MAPWIDE_SUMMARY_CSV),
    }
    write_json(DEMAND_XML.with_suffix(".summary.json"), summary)
    return summary


def reference_volume_rows(reference_csv: Path = REFERENCE_CSV) -> list[dict[str, Any]]:
    rows = []
    for row in read_csv(reference_csv):
        rows.append({
            "segment_id": row["segment_id"],
            "segment_number": int(row["segment_id"].replace("S", "")),
            "volume_vph": float(row["peak_hour_volume_veh_per_h_reference"]),
            "speed_limit_kmh": float(row.get("speed_limit_kmh") or 50.0),
            "avg_speed_kmh_upbound": float(row.get("avg_speed_kmh_upbound") or 0.0),
            "avg_speed_kmh_downbound": float(row.get("avg_speed_kmh_downbound") or 0.0),
            "travel_time_s_upbound": float(row.get("travel_time_s_upbound") or 0.0),
            "travel_time_s_downbound": float(row.get("travel_time_s_downbound") or 0.0),
        })
    rows.sort(key=lambda item: item["segment_number"])
    return rows


def mapping_chain_edges(mapping_csv: Path, direction: str, segment_numbers: list[int], reverse_segments: bool) -> list[str]:
    rows = read_csv(mapping_csv)
    wanted = set(segment_numbers)
    selected = []
    for row in rows:
        if row.get("direction") != direction:
            continue
        segment_number = int(row["segment_id"].replace("S", ""))
        if segment_number in wanted:
            selected.append(row)
    selected.sort(key=lambda row: (
        -int(row["segment_id"].replace("S", "")) if reverse_segments else int(row["segment_id"].replace("S", "")),
        int(row["edge_order"]),
    ))
    edge_ids: list[str] = []
    for row in selected:
        edge_id = row["edge_id"]
        if edge_id not in edge_ids:
            edge_ids.append(edge_id)
    return edge_ids


def shortest_mapping_route(sumo_net: Any, mapping_csv: Path, direction: str, segment_numbers: list[int], reverse_segments: bool) -> list[str]:
    s07 = step07_module()
    chain = mapping_chain_edges(mapping_csv, direction, segment_numbers, reverse_segments)
    known = []
    for edge_id in chain:
        try:
            edge = sumo_net.getEdge(edge_id)
        except Exception:
            continue
        if not edge.isSpecial() and edge.allows("passenger"):
            known.append(edge_id)
    if len(known) < 2:
        raise ExpandedV7Error(f"insufficient_mapping_edges:{direction}:{segment_numbers}")
    route = s07.shortest_route(sumo_net, known[0], known[-1])
    if not route_connected(sumo_net, route):
        raise ExpandedV7Error(f"mapping_route_not_connected:{direction}:{known[0]}:{known[-1]}")
    return route


def edge_iter(edges: Any) -> list[Any]:
    if isinstance(edges, dict):
        return list(edges.keys())
    return list(edges or [])


def passenger_candidate_edge(edge: Any) -> bool:
    try:
        return (not edge.isSpecial()) and edge.allows("passenger")
    except Exception:
        return False


def prepend_connected_edges(sumo_net: Any, route: list[str], limit: int = 2) -> list[str]:
    result = list(route)
    for _index in range(limit):
        if not result:
            break
        first = sumo_net.getEdge(result[0])
        candidates = [
            edge for edge in edge_iter(getattr(first, "getIncoming")())
            if passenger_candidate_edge(edge) and edge.getID() not in result
        ]
        candidates.sort(key=lambda edge: (-float(edge.getLength()), edge.getID()))
        added = False
        for candidate in candidates:
            candidate_id = candidate.getID()
            if route_connected(sumo_net, [candidate_id, result[0]]):
                result.insert(0, candidate_id)
                added = True
                break
        if not added:
            break
    return result


def append_connected_edges(sumo_net: Any, route: list[str], limit: int = 2) -> list[str]:
    result = list(route)
    for _index in range(limit):
        if not result:
            break
        last = sumo_net.getEdge(result[-1])
        candidates = [
            edge for edge in edge_iter(last.getOutgoing())
            if passenger_candidate_edge(edge) and edge.getID() not in result
        ]
        candidates.sort(key=lambda edge: (-float(edge.getLength()), edge.getID()))
        added = False
        for candidate in candidates:
            candidate_id = candidate.getID()
            if route_connected(sumo_net, [result[-1], candidate_id]):
                result.append(candidate_id)
                added = True
                break
        if not added:
            break
    return result


def bottleneck_edge(edge_id: str) -> bool:
    return edge_id in BOTTLENECK_EDGE_IDS or edge_id.startswith("347237859#")


def blocked_bottleneck_source(edge_id: str) -> bool:
    return edge_id in BOTTLENECK_SOURCE_BLOCKLIST or edge_id.startswith("347237859#")


def guarded_downstream_sink(edge_id: str) -> bool:
    return bottleneck_edge(edge_id) or edge_id in DOWNSTREAM_SINK_GUARD_EDGE_IDS


def terminal_sink_edge(edge_id: str) -> bool:
    return (
        edge_id in TERMINAL_SINK_GUARD_EDGE_IDS
        or edge_id.startswith("12062239")
        or edge_id in {"585341906#2", "585341907#0", "585341907#2", "585341908#0", "585341908#1", "615671502", "615671503", "477063271"}
    )


def mainroad_edge_ids(mapping_csv: Path = MAPPING_CSV) -> set[str]:
    return {row["edge_id"] for row in read_csv(mapping_csv) if row.get("repair_target") in {"True", "true", "1", True}}


def all_mapping_edge_ids(mapping_csv: Path = MAPPING_CSV) -> set[str]:
    return {row["edge_id"] for row in read_csv(mapping_csv) if row.get("edge_id")}


def mapping_target_lanes_by_edge(mapping_csv: Path = MAPPING_CSV) -> dict[str, int]:
    targets: dict[str, int] = {}
    for row in read_csv(mapping_csv):
        edge_id = row.get("edge_id", "")
        if not edge_id:
            continue
        targets[edge_id] = max(targets.get(edge_id, 0), safe_int(row.get("target_lanes"), 0))
    return targets


def protected_mainline_edge_ids(mapping_csv: Path = MAPPING_CSV) -> set[str]:
    return all_mapping_edge_ids(mapping_csv) | set(accepted_route_edges()) | set(MAINLINE_RELEASE_EDGE_IDS)


def mapping_continuation_pairs(sumo_net: Any, mapping_csv: Path = MAPPING_CSV) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    rows = read_csv(mapping_csv)
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        if row.get("segment_id") and row.get("direction") and row.get("edge_id"):
            grouped.setdefault((row["segment_id"], row["direction"]), []).append(row)
    for group_rows in grouped.values():
        group_rows.sort(key=lambda row: int(float(row.get("edge_order") or 0)))
        edge_ids = [row["edge_id"] for row in group_rows]
        for from_edge, to_edge in zip(edge_ids, edge_ids[1:], strict=False):
            if from_edge == to_edge:
                continue
            try:
                if route_connected(sumo_net, [from_edge, to_edge]):
                    pairs.add((from_edge, to_edge))
            except Exception:
                continue
    return pairs


def protected_continuation_pairs(sumo_net: Any, mapping_csv: Path = MAPPING_CSV) -> set[tuple[str, str]]:
    route_edges = accepted_route_edges()
    pairs = set(zip(route_edges, route_edges[1:], strict=False))
    pairs.update(mapping_continuation_pairs(sumo_net, mapping_csv))
    return {(from_edge, to_edge) for from_edge, to_edge in pairs if from_edge and to_edge}


def sumolib_edge_lane_count(sumo_net: Any, edge_id: str) -> int:
    try:
        return int(sumo_net.getEdge(edge_id).getLaneNumber())
    except Exception:
        return 0


def sumolib_edge_speed_kmh(sumo_net: Any, edge_id: str) -> float:
    try:
        return float(sumo_net.getEdge(edge_id).getSpeed()) * 3.6
    except Exception:
        return 0.0


def sumolib_edge_type(edge: Any) -> str:
    try:
        return str(edge.getType())
    except Exception:
        return ""


def forbidden_source_sink_edge(edge_id: str) -> bool:
    return (
        edge_id in PROBLEM_SOURCE_SINK_EDGE_IDS
        or edge_id in TERMINAL_SINK_GUARD_EDGE_IDS
        or guarded_downstream_sink(edge_id)
        or blocked_bottleneck_source(edge_id)
        or edge_id.startswith("347237859#")
    )


def boundary_edge_ok(edge_id: str, main_edges: set[str]) -> bool:
    return edge_id not in main_edges and not forbidden_source_sink_edge(edge_id)


def extend_terminal_sink_v2(sumo_net: Any, route: list[str], limit: int = 10) -> tuple[list[str], bool, str]:
    if not route or not terminal_sink_edge(route[-1]):
        return list(route), False, ""
    extended = extend_route_until(
        sumo_net,
        route,
        prefix_ok=lambda _edge_id: True,
        suffix_ok=lambda edge_id: not terminal_sink_edge(edge_id) and not guarded_downstream_sink(edge_id),
        limit=limit,
    )
    if extended and route_connected(sumo_net, extended) and not terminal_sink_edge(extended[-1]):
        return extended, extended != route, "terminal_sink_extension_v2"
    return list(route), False, "terminal_sink_extension_v2_unavailable"


def extend_terminal_sink_v3(sumo_net: Any, route: list[str], limit: int = 30) -> tuple[list[str], bool, str]:
    if not route or not (terminal_sink_edge(route[-1]) or guarded_downstream_sink(route[-1])):
        return list(route), False, ""
    extended = extend_route_until(
        sumo_net,
        route,
        prefix_ok=lambda _edge_id: True,
        suffix_ok=lambda edge_id: not terminal_sink_edge(edge_id) and not guarded_downstream_sink(edge_id),
        limit=limit,
    )
    if (
        extended
        and route_connected(sumo_net, extended)
        and not terminal_sink_edge(extended[-1])
        and not guarded_downstream_sink(extended[-1])
    ):
        return extended, extended != route, "terminal_sink_extension_v3"
    return list(route), False, "terminal_sink_extension_v3_unavailable"


def extend_route_to_boundary(
    sumo_net: Any,
    route: list[str],
    main_edges: set[str],
    limit: int = 10,
) -> tuple[list[str], bool, str]:
    if not route:
        return [], False, "empty_route"
    extended = extend_route_until(
        sumo_net,
        route,
        prefix_ok=lambda edge_id: boundary_edge_ok(edge_id, main_edges),
        suffix_ok=lambda edge_id: boundary_edge_ok(edge_id, main_edges),
        limit=limit,
    )
    if not extended or not route_connected(sumo_net, extended):
        return list(route), False, "boundary_extension_not_connected"
    changed = extended != route
    if boundary_edge_ok(extended[0], main_edges) and boundary_edge_ok(extended[-1], main_edges):
        return extended, changed, "distributed_boundary_extension" if changed else ""
    return extended, changed, "boundary_extension_incomplete"


def route_uses_forbidden_bottleneck(route_edges: list[str]) -> bool:
    return any(bottleneck_edge(edge_id) for edge_id in route_edges)


def route_guard_reason(flow_type: str, direction: str, route_edges: list[str], strict: bool = False) -> str:
    if not route_edges:
        return "empty_route"
    reasons = []
    if blocked_bottleneck_source(route_edges[0]):
        reasons.append("minor_or_bottleneck_source")
    if guarded_downstream_sink(route_edges[-1]):
        reasons.append("bottleneck_or_guarded_sink")
    uses_bottleneck = route_uses_forbidden_bottleneck(route_edges)
    through_up = flow_type == "through" and direction == "upbound"
    if strict and uses_bottleneck and not through_up:
        reasons.append("non_through_route_uses_upbound_bottleneck")
    if strict and direction != "upbound" and uses_bottleneck:
        reasons.append("direction_conflict_uses_upbound_bottleneck")
    return ";".join(dict.fromkeys(reasons))


def edge_length_m(sumo_net: Any, edge_id: str) -> float:
    try:
        return float(sumo_net.getEdge(edge_id).getLength())
    except Exception:
        return 0.0


def short_edge_artifact_level(sumo_net: Any, edge_id: str) -> str:
    length = edge_length_m(sumo_net, edge_id)
    if 0.0 < length < SHORT_EDGE_ARTIFACT_LENGTH_M:
        return "artifact_lt_5m"
    if 0.0 < length < SHORT_EDGE_WARN_LENGTH_M:
        return "warn_lt_10m"
    return ""


def extend_route_until(
    sumo_net: Any,
    route: list[str],
    prefix_ok: Any,
    suffix_ok: Any,
    limit: int = 5,
) -> list[str]:
    result = list(route)
    for _index in range(limit):
        if not result or prefix_ok(result[0]):
            break
        before = list(result)
        result = prepend_connected_edges(sumo_net, result, 1)
        if result == before:
            break
    for _index in range(limit):
        if not result or suffix_ok(result[-1]):
            break
        before = list(result)
        result = append_connected_edges(sumo_net, result, 1)
        if result == before:
            break
    return result


def connected_route_key(route: list[str]) -> str:
    return " ".join(route)


def incoming_prefix_candidates(sumo_net: Any, route: list[str], limit: int = 4) -> list[list[str]]:
    if not route:
        return []
    first = sumo_net.getEdge(route[0])
    candidates = [
        edge for edge in edge_iter(getattr(first, "getIncoming")())
        if passenger_candidate_edge(edge) and edge.getID() not in route
    ]
    candidates.sort(key=lambda edge: (-float(edge.getLength()), edge.getID()))
    variants: list[list[str]] = []
    for edge in candidates:
        candidate_id = edge.getID()
        one_hop = [candidate_id] + list(route)
        if route_connected(sumo_net, one_hop):
            variants.append(one_hop)
        if len(variants) >= limit:
            break
        second_hop_candidates = [
            incoming for incoming in edge_iter(getattr(edge, "getIncoming")())
            if passenger_candidate_edge(incoming) and incoming.getID() not in one_hop
        ]
        second_hop_candidates.sort(key=lambda item: (-float(item.getLength()), item.getID()))
        for incoming in second_hop_candidates[:2]:
            two_hop = [incoming.getID(), candidate_id] + list(route)
            if route_connected(sumo_net, two_hop):
                variants.append(two_hop)
                break
        if len(variants) >= limit:
            break
    return variants[:limit]


def outgoing_suffix_candidates(sumo_net: Any, route: list[str], limit: int = 3) -> list[list[str]]:
    if not route:
        return []
    last = sumo_net.getEdge(route[-1])
    candidates = [
        edge for edge in edge_iter(last.getOutgoing())
        if passenger_candidate_edge(edge) and edge.getID() not in route
    ]
    candidates.sort(key=lambda edge: (-float(edge.getLength()), edge.getID()))
    variants: list[list[str]] = []
    for edge in candidates:
        candidate_id = edge.getID()
        one_hop = list(route) + [candidate_id]
        if route_connected(sumo_net, one_hop):
            variants.append(one_hop)
        if len(variants) >= limit:
            break
    return variants[:limit]


def distributed_route_variants(sumo_net: Any, route: list[str], max_variants: int = 5) -> list[list[str]]:
    variants: list[list[str]] = []
    seen: set[str] = set()
    for candidate in [route] + incoming_prefix_candidates(sumo_net, route, max_variants) + outgoing_suffix_candidates(sumo_net, route, max_variants):
        key = connected_route_key(candidate)
        if key in seen or not route_connected(sumo_net, candidate):
            continue
        seen.add(key)
        variants.append(candidate)
        if len(variants) >= max_variants:
            break
    return variants


def split_flow_variants(sumo_net: Any, flow: dict[str, Any], max_variants: int = 5) -> list[dict[str, Any]]:
    if flow.get("flow_type") != "through":
        return [flow]
    route_variants = distributed_route_variants(sumo_net, list(flow["route_edges"]), max_variants=max_variants)
    if len(route_variants) <= 1:
        flow["distribution_variant_count"] = 1
        return [flow]
    vph = float(flow["vph"]) / len(route_variants)
    split_flows = []
    for index, route_edges in enumerate(route_variants):
        split = dict(flow)
        split["flow_id"] = f"{flow['flow_id']}_src{index + 1:02d}"
        split["vph"] = vph
        split["route_edges"] = route_edges
        split["distribution_variant_index"] = index + 1
        split["distribution_variant_count"] = len(route_variants)
        split["depart_offset_sec"] = float(flow.get("depart_offset_sec", 0.0)) + index * 3.7
        split_flows.append(split)
    return split_flows


def segment_local_route(sumo_net: Any, mapping_csv: Path, direction: str, segment_number: int, reverse_segments: bool) -> list[str]:
    s07 = step07_module()
    chain = mapping_chain_edges(mapping_csv, direction, [segment_number], reverse_segments)
    known = []
    for edge_id in chain:
        try:
            edge = sumo_net.getEdge(edge_id)
        except Exception:
            continue
        if passenger_candidate_edge(edge) and edge_id not in known:
            known.append(edge_id)
    if not known:
        raise ExpandedV7Error(f"insufficient_local_mapping_edges:{direction}:S{segment_number}")
    if len(known) == 1:
        route = known
    else:
        route = s07.shortest_route(sumo_net, known[0], known[-1])
    route = prepend_connected_edges(sumo_net, route, 2)
    route = append_connected_edges(sumo_net, route, 2)
    if not route_connected(sumo_net, route):
        raise ExpandedV7Error(f"local_route_not_connected:{direction}:S{segment_number}:{route[0]}:{route[-1]}")
    return route


def bottleneck_aware_local_route(sumo_net: Any, mapping_csv: Path, direction: str, segment_number: int, reverse_segments: bool) -> list[str]:
    route = segment_local_route(sumo_net, mapping_csv, direction, segment_number, reverse_segments)
    if direction != "upbound" or segment_number not in BOTTLENECK_SEGMENTS:
        return route
    route = extend_route_until(
        sumo_net,
        route,
        prefix_ok=lambda edge_id: not bottleneck_edge(edge_id),
        suffix_ok=lambda edge_id: not guarded_downstream_sink(edge_id),
        limit=5,
    )
    if route and bottleneck_edge(route[0]):
        wider_segments = [number for number in range(max(1, segment_number - 2), min(22, segment_number + 2) + 1)]
        route = shortest_mapping_route(sumo_net, mapping_csv, direction, wider_segments, reverse_segments)
        route = extend_route_until(
            sumo_net,
            route,
            prefix_ok=lambda edge_id: not bottleneck_edge(edge_id),
            suffix_ok=lambda edge_id: not guarded_downstream_sink(edge_id),
            limit=5,
        )
    if route and bottleneck_edge(route[0]):
        raise ExpandedV7Error(f"bottleneck_local_source_blocked:{direction}:S{segment_number}:{route[0]}")
    if route and guarded_downstream_sink(route[-1]):
        raise ExpandedV7Error(f"bottleneck_local_sink_blocked:{direction}:S{segment_number}:{route[-1]}")
    if not route_connected(sumo_net, route):
        raise ExpandedV7Error(f"bottleneck_local_route_not_connected:{direction}:S{segment_number}:{route[0]}:{route[-1]}")
    return route


def evenly_spaced_departures(vph: float, duration_sec: float, offset_sec: float = 0.0) -> list[float]:
    count = int(round(vph * duration_sec / 3600.0))
    if count <= 0:
        return []
    return [((index + 0.5) * duration_sec / count + offset_sec) % duration_sec for index in range(count)]


def direction_speed_key(direction: str) -> str:
    return "avg_speed_kmh_upbound" if direction == "upbound" else "avg_speed_kmh_downbound"


def direction_travel_time_key(direction: str) -> str:
    return "travel_time_s_upbound" if direction == "upbound" else "travel_time_s_downbound"


def direction_ordered_rows(rows: list[dict[str, Any]], direction: str) -> list[dict[str, Any]]:
    reverse = direction == "downbound"
    return sorted(rows, key=lambda row: int(row["segment_number"]), reverse=reverse)


def flow_reference_timing(
    rows: list[dict[str, Any]],
    direction: str,
    segment_numbers: list[int],
) -> dict[str, float]:
    wanted = set(int(number) for number in segment_numbers)
    ordered = direction_ordered_rows(rows, direction)
    speed_key = direction_speed_key(direction)
    time_key = direction_travel_time_key(direction)
    offset = 0.0
    selected: list[dict[str, Any]] = []
    for row in ordered:
        if int(row["segment_number"]) in wanted:
            selected.append(row)
        elif not selected:
            offset += float(row.get(time_key) or 0.0)
    if not selected:
        return {
            "reference_offset_sec": 0.0,
            "reference_speed_kmh": 25.0,
            "reference_travel_time_s": 120.0,
            "reference_speed_limit_kmh": 50.0,
        }
    weighted_speed_num = 0.0
    weighted_speed_den = 0.0
    travel_time_sum = 0.0
    speed_limit_sum = 0.0
    for row in selected:
        travel_time = max(1.0, float(row.get(time_key) or 0.0))
        speed = max(1.0, float(row.get(speed_key) or 0.0))
        weighted_speed_num += speed * travel_time
        weighted_speed_den += travel_time
        travel_time_sum += travel_time
        speed_limit_sum += float(row.get("speed_limit_kmh") or 50.0)
    return {
        "reference_offset_sec": offset,
        "reference_speed_kmh": weighted_speed_num / weighted_speed_den if weighted_speed_den else 25.0,
        "reference_travel_time_s": travel_time_sum,
        "reference_speed_limit_kmh": speed_limit_sum / len(selected),
    }


def bounded(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def csv_reality_sequential_departures(
    vph: float,
    duration_sec: float,
    offset_sec: float,
    reference_speed_kmh: float,
    reference_speed_limit_kmh: float,
    reference_travel_time_s: float,
    active_multiplier: float = 1.0,
) -> tuple[list[float], dict[str, float]]:
    count = int(round(vph * duration_sec / 3600.0))
    if count <= 0:
        return [], {"pulse_cycle_sec": 0.0, "pulse_active_fraction": 0.0}
    speed_ratio = bounded(reference_speed_kmh / max(1.0, reference_speed_limit_kmh), 0.1, 1.0)
    pulse_cycle_sec = bounded(reference_travel_time_s * 2.0, 120.0, 420.0)
    active_fraction = bounded((0.25 + 0.65 * speed_ratio) * active_multiplier, 0.32, 0.95)
    cycle_count = max(1, int(math.ceil(duration_sec / pulse_cycle_sec)))
    per_cycle = int(math.ceil(count / cycle_count))
    departures = []
    for index in range(count):
        cycle_index = min(index // per_cycle, cycle_count - 1)
        in_cycle_index = index - cycle_index * per_cycle
        active_window = pulse_cycle_sec * active_fraction
        intra_cycle = (in_cycle_index + 0.5) * active_window / per_cycle
        departures.append((offset_sec + cycle_index * pulse_cycle_sec + intra_cycle) % duration_sec)
    departures.sort()
    return departures, {
        "pulse_cycle_sec": round(pulse_cycle_sec, 6),
        "pulse_active_fraction": round(active_fraction, 6),
    }


def route_uses_release_axis(route_edges: list[str]) -> bool:
    return any(edge_id.startswith("347237859#") or edge_id == "781985787#0" for edge_id in route_edges)


def apply_min_depart_gap(departures: list[float], min_gap_sec: float, duration_sec: float) -> tuple[list[float], int]:
    if min_gap_sec <= 0 or len(departures) < 2:
        return sorted(departures), 0
    shifted = []
    shift_count = 0
    previous = -10**9
    for depart in sorted(departures):
        candidate = depart
        if candidate < previous + min_gap_sec:
            candidate = previous + min_gap_sec
            shift_count += 1
        if candidate >= duration_sec:
            candidate = duration_sec - 0.01
            shift_count += 1
        shifted.append(candidate)
        previous = candidate
    return shifted, shift_count


def free_segment_feeder_specs() -> list[dict[str, Any]]:
    return [
        {"segment_number": 8, "direction": "downbound", "reverse": True, "reason": "S8_downbound_free_flow_feeder"},
        {"segment_number": 16, "direction": "upbound", "reverse": False, "reason": "S16_upbound_free_flow_feeder"},
    ]


def build_expanded_screenline_demand(
    reference_csv: Path,
    mapping_csv: Path,
    net_file: Path,
    output_route: Path,
    duration_sec: float = 3600.0,
    profile: str = "balanced_diversion",
    timing_profile: str = "csv_reality_sequential",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    volumes = reference_volume_rows(reference_csv)
    profile = profile.replace("+", "_")
    settings = demand_profile_settings(profile)
    local_share = float(settings["local_validation_share"])
    bottleneck_local_share = float(settings.get("bottleneck_local_validation_share", local_share))
    main_pass_ratio = float(settings["main_pass_ratio"])
    through_share = max(0.0, main_pass_ratio - local_share)
    upbound_through_share = float(settings.get("upbound_through_share", through_share))
    downbound_through_share = float(settings.get("downbound_through_share", through_share))
    pulse_active_multiplier = float(settings["pulse_active_multiplier"])
    through_max_variants = int(settings.get("through_max_variants", 5))
    avoid_bottleneck_internal = bool(settings.get("avoid_bottleneck_internal_local_source_sink", False))
    strict_route_guard = bool(settings.get("strict_bottleneck_route_guard", False))
    local_accounting_guard = bool(settings.get("local_accounting_guard_enabled", False))
    terminal_sink_extension_v2 = bool(settings.get("terminal_sink_extension_v2_enabled", False))
    terminal_sink_extension_v3 = bool(settings.get("terminal_sink_extension_v3_enabled", False))
    terminal_sink_extension_v3_limit = safe_int(settings.get("terminal_sink_extension_v3_limit"), 0) or 30
    distributed_boundary = bool(settings.get("distributed_boundary_enabled", False))
    boundary_extension_limit = int(settings.get("boundary_extension_limit", 5))
    release_depart_gap_enabled = bool(settings.get("release_depart_gap_enabled", False))
    release_depart_gap_sec = safe_float(settings.get("release_depart_gap_sec"), 0.0)
    route_template_vehicle_cap = safe_int(settings.get("route_template_vehicle_cap"), 0)
    free_segment_feeder_enabled = bool(settings.get("free_segment_feeder_enabled", False))
    free_segment_feeder_share = safe_float(settings.get("free_segment_feeder_share"), 0.0)
    if local_share < 0 or main_pass_ratio < 0 or local_share > main_pass_ratio or main_pass_ratio > 1.0:
        raise ExpandedV7Error(f"invalid_demand_profile_ratios:{profile}")
    base_vph = min(row["volume_vph"] for row in volumes)
    max_vph = max(row["volume_vph"] for row in volumes)
    high_segments = [row["segment_number"] for row in volumes if row["volume_vph"] > base_vph]
    expected_prefix = list(range(1, len(high_segments) + 1))
    if high_segments and high_segments != expected_prefix:
        raise ExpandedV7Error("expanded_v7_reference_only_supports_leading_prefix_extra")
    extra_vph = max(0.0, max_vph - base_vph)
    all_segments = [row["segment_number"] for row in volumes]
    sumo_net = read_sumo_net(net_file)
    main_edges = mainroad_edge_ids(mapping_csv)
    flows = [
        {
            "flow_id": "expanded_v7_ref_up_full",
            "flow_type": "through",
            "direction": "upbound",
            "segment_numbers": all_segments,
            "segment_scope": "S1-S22",
                "vph": base_vph * upbound_through_share,
            "route_edges": shortest_mapping_route(sumo_net, mapping_csv, "upbound", all_segments, reverse_segments=False),
            "depart_offset_sec": 0.0,
        },
        {
            "flow_id": "expanded_v7_ref_down_full",
            "flow_type": "through",
            "direction": "downbound",
            "segment_numbers": all_segments,
            "segment_scope": "S1-S22",
                "vph": base_vph * downbound_through_share,
            "route_edges": shortest_mapping_route(sumo_net, mapping_csv, "downbound", all_segments, reverse_segments=True),
            "depart_offset_sec": 0.5,
        },
    ]
    if extra_vph > 0 and high_segments:
        flows.extend([
            {
                "flow_id": "expanded_v7_ref_up_prefix_extra",
                "flow_type": "through",
                "direction": "upbound",
                "segment_numbers": high_segments,
                "segment_scope": f"S1-S{high_segments[-1]}",
                    "vph": extra_vph * upbound_through_share,
                "route_edges": shortest_mapping_route(sumo_net, mapping_csv, "upbound", high_segments, reverse_segments=False),
                "depart_offset_sec": 1.0,
            },
            {
                "flow_id": "expanded_v7_ref_down_prefix_extra",
                "flow_type": "through",
                "direction": "downbound",
                "segment_numbers": high_segments,
                "segment_scope": f"S1-S{high_segments[-1]}",
                    "vph": extra_vph * downbound_through_share,
                "route_edges": shortest_mapping_route(sumo_net, mapping_csv, "downbound", high_segments, reverse_segments=True),
                "depart_offset_sec": 1.5,
            },
        ])
    local_share_by_segment_direction: dict[tuple[str, str], float] = {}
    feeder_share_by_segment_direction: dict[tuple[str, str], float] = {}
    accounted_flow_rows: list[dict[str, Any]] = []
    accounted_local_by_segment_direction: dict[tuple[str, str], float] = {}
    route_guarded_local_by_segment_direction: dict[tuple[str, str], float] = {}
    if local_share > 0:
        for row in volumes:
            segment_number = int(row["segment_number"])
            for direction, reverse, base_offset in [("upbound", False, 7.0), ("downbound", True, 13.0)]:
                effective_local_share = local_share
                if avoid_bottleneck_internal and direction == "upbound" and segment_number in BOTTLENECK_SEGMENTS:
                    effective_local_share = bottleneck_local_share
                if effective_local_share <= 0:
                    local_share_by_segment_direction[(row["segment_id"], direction)] = 0.0
                    continue
                try:
                    if avoid_bottleneck_internal and direction == "upbound" and segment_number in BOTTLENECK_SEGMENTS:
                        route_edges = bottleneck_aware_local_route(sumo_net, mapping_csv, direction, segment_number, reverse)
                    else:
                        route_edges = segment_local_route(sumo_net, mapping_csv, direction, segment_number, reverse)
                except Exception:
                    route_edges = shortest_mapping_route(sumo_net, mapping_csv, direction, [segment_number], reverse)
                    if avoid_bottleneck_internal and direction == "upbound" and segment_number in BOTTLENECK_SEGMENTS:
                        route_edges = extend_route_until(
                            sumo_net,
                            route_edges,
                            prefix_ok=lambda edge_id: not bottleneck_edge(edge_id),
                            suffix_ok=lambda edge_id: not guarded_downstream_sink(edge_id),
                            limit=5,
                        )
                        if bottleneck_edge(route_edges[0]) or guarded_downstream_sink(route_edges[-1]):
                            raise ExpandedV7Error(f"bottleneck_local_fallback_failed:{direction}:S{segment_number}:{route_edges[0]}:{route_edges[-1]}")
                local_share_by_segment_direction[(row["segment_id"], direction)] = effective_local_share
                guard_reason = route_guard_reason("local_validation", direction, route_edges, strict=strict_route_guard)
                if local_accounting_guard and guard_reason:
                    timing = flow_reference_timing(volumes, direction, [segment_number])
                    accounted_count = float(row["volume_vph"]) * effective_local_share * duration_sec / 3600.0
                    key = (row["segment_id"], direction)
                    accounted_local_by_segment_direction[key] = accounted_local_by_segment_direction.get(key, 0.0) + accounted_count
                    route_guarded_local_by_segment_direction[key] = route_guarded_local_by_segment_direction.get(key, 0.0) + accounted_count
                    accounted_flow_rows.append({
                        "flow_id": f"expanded_v7_ref_local_{direction}_S{segment_number:02d}",
                        "profile": profile,
                        "flow_type": "local_validation",
                        "direction": direction,
                        "segment_scope": f"S{segment_number}",
                        "vph": float(row["volume_vph"]) * effective_local_share,
                        "vehicle_count": 0,
                        "skipped_by_source_cap": 0,
                        "route_edge_count": len(route_edges),
                        "start_edge": route_edges[0] if route_edges else "",
                        "target_edge": route_edges[-1] if route_edges else "",
                        "accounted_only": True,
                        "route_guard_reason": guard_reason,
                        "route_uses_forbidden_bottleneck": route_uses_forbidden_bottleneck(route_edges),
                        "distribution_variant_index": 1,
                        "distribution_variant_count": 1,
                        "reference_speed_kmh": round(float(timing["reference_speed_kmh"]), 6),
                        "reference_travel_time_s": round(float(timing["reference_travel_time_s"]), 6),
                        "reference_offset_sec": round(float(timing["reference_offset_sec"]), 6),
                        "timing_profile": timing_profile,
                        "pulse_cycle_sec": 0.0,
                        "pulse_active_fraction": 0.0,
                    })
                    continue
                flows.append({
                    "flow_id": f"expanded_v7_ref_local_{direction}_S{segment_number:02d}",
                    "flow_type": "local_validation",
                    "direction": direction,
                    "segment_numbers": [segment_number],
                    "segment_scope": f"S{segment_number}",
                    "vph": float(row["volume_vph"]) * effective_local_share,
                    "route_edges": route_edges,
                    "depart_offset_sec": base_offset + segment_number * 2.37,
                    "effective_local_share": effective_local_share,
                })
    if free_segment_feeder_enabled and free_segment_feeder_share > 0:
        by_segment_number = {int(row["segment_number"]): row for row in volumes}
        for spec in free_segment_feeder_specs():
            segment_number = int(spec["segment_number"])
            direction = str(spec["direction"])
            source_row = by_segment_number.get(segment_number)
            if not source_row:
                continue
            try:
                route_edges = segment_local_route(sumo_net, mapping_csv, direction, segment_number, bool(spec["reverse"]))
                route_edges = extend_route_until(
                    sumo_net,
                    route_edges,
                    prefix_ok=lambda edge_id: not forbidden_source_sink_edge(edge_id),
                    suffix_ok=lambda edge_id: not forbidden_source_sink_edge(edge_id),
                    limit=8,
                )
                if not route_connected(sumo_net, route_edges):
                    continue
            except Exception:
                continue
            key = (source_row["segment_id"], direction)
            feeder_share_by_segment_direction[key] = feeder_share_by_segment_direction.get(key, 0.0) + free_segment_feeder_share
            flows.append({
                "flow_id": f"expanded_v7_ref_free_feeder_{direction}_S{segment_number:02d}",
                "flow_type": "free_segment_feeder",
                "direction": direction,
                "segment_numbers": [segment_number],
                "segment_scope": f"S{segment_number}",
                "vph": float(source_row["volume_vph"]) * free_segment_feeder_share,
                "route_edges": route_edges,
                "depart_offset_sec": 31.0 + segment_number * 2.91,
                "effective_feeder_share": free_segment_feeder_share,
                "free_segment_feeder_reason": spec["reason"],
            })
    boundary_extension_count = 0
    boundary_extension_incomplete_count = 0
    if distributed_boundary:
        for flow in flows:
            route_edges, changed, reason = extend_route_to_boundary(
                sumo_net,
                list(flow["route_edges"]),
                main_edges,
                limit=boundary_extension_limit,
            )
            if route_edges:
                flow["route_edges"] = route_edges
            if changed:
                boundary_extension_count += 1
            if reason and reason != "distributed_boundary_extension":
                boundary_extension_incomplete_count += 1
            flow["boundary_extension_applied"] = changed
            flow["boundary_extension_reason"] = reason

    terminal_extension_count = 0
    terminal_extension_unavailable_count = 0
    if terminal_sink_extension_v2:
        for flow in flows:
            route_edges, changed, reason = extend_terminal_sink_v2(sumo_net, list(flow["route_edges"]), limit=10)
            if changed:
                flow["route_edges"] = route_edges
                terminal_extension_count += 1
            elif reason:
                terminal_extension_unavailable_count += 1
            flow["terminal_sink_extension_v2_applied"] = changed
            flow["terminal_sink_extension_v2_reason"] = reason

    terminal_v3_extension_count = 0
    terminal_v3_unavailable_count = 0
    terminal_v3_diverted_count = 0
    if terminal_sink_extension_v3:
        kept_flows = []
        for flow in flows:
            route_edges, changed, reason = extend_terminal_sink_v3(sumo_net, list(flow["route_edges"]), limit=terminal_sink_extension_v3_limit)
            if changed:
                flow["route_edges"] = route_edges
                terminal_v3_extension_count += 1
            elif reason:
                terminal_v3_unavailable_count += 1
                if flow.get("flow_type") != "through":
                    timing = flow_reference_timing(volumes, flow["direction"], flow["segment_numbers"])
                    accounted_count = float(flow["vph"]) * duration_sec / 3600.0
                    for segment_number in flow["segment_numbers"]:
                        key = (f"S{segment_number}", flow["direction"])
                        accounted_local_by_segment_direction[key] = accounted_local_by_segment_direction.get(key, 0.0) + accounted_count / max(1, len(flow["segment_numbers"]))
                    terminal_v3_diverted_count += int(round(accounted_count))
                    accounted_flow_rows.append({
                        "flow_id": flow["flow_id"],
                        "profile": profile,
                        "flow_type": flow["flow_type"],
                        "direction": flow["direction"],
                        "segment_scope": flow["segment_scope"],
                        "vph": float(flow["vph"]),
                        "vehicle_count": 0,
                        "skipped_by_source_cap": 0,
                        "route_edge_count": len(flow["route_edges"]),
                        "start_edge": flow["route_edges"][0] if flow["route_edges"] else "",
                        "target_edge": flow["route_edges"][-1] if flow["route_edges"] else "",
                        "accounted_only": True,
                        "route_guard_reason": reason,
                        "route_uses_forbidden_bottleneck": route_uses_forbidden_bottleneck(flow["route_edges"]),
                        "boundary_extension_applied": flow.get("boundary_extension_applied", False),
                        "boundary_extension_reason": flow.get("boundary_extension_reason", ""),
                        "terminal_sink_extension_v2_applied": flow.get("terminal_sink_extension_v2_applied", False),
                        "terminal_sink_extension_v2_reason": flow.get("terminal_sink_extension_v2_reason", ""),
                        "terminal_sink_extension_v3_applied": False,
                        "terminal_sink_extension_v3_reason": reason,
                        "distribution_variant_index": flow.get("distribution_variant_index", 1),
                        "distribution_variant_count": flow.get("distribution_variant_count", 1),
                        "reference_speed_kmh": round(float(timing["reference_speed_kmh"]), 6),
                        "reference_travel_time_s": round(float(timing["reference_travel_time_s"]), 6),
                        "reference_offset_sec": round(float(timing["reference_offset_sec"]), 6),
                        "timing_profile": timing_profile,
                        "pulse_cycle_sec": 0.0,
                        "pulse_active_fraction": 0.0,
                        "release_depart_gap_applied_count": 0,
                        "template_cap_diverted_count": 0,
                        "free_segment_feeder_reason": flow.get("free_segment_feeder_reason", ""),
                    })
                    continue
            flow["terminal_sink_extension_v3_applied"] = changed
            flow["terminal_sink_extension_v3_reason"] = reason
            kept_flows.append(flow)
        flows = kept_flows

    distributed_flows: list[dict[str, Any]] = []
    for flow in flows:
        distributed_flows.extend(split_flow_variants(sumo_net, flow, max_variants=through_max_variants))
    flows = distributed_flows
    for flow in flows:
        timing = flow_reference_timing(volumes, flow["direction"], flow["segment_numbers"])
        flow.update(timing)
        flow["depart_offset_sec"] = float(flow.get("depart_offset_sec", 0.0)) + float(timing["reference_offset_sec"])
    output_root = ET.Element("routes")
    output_root.append(ET.Comment("expanded-v7 screenline/local demand; CSV veh/h decomposed into through flow plus segment-local validation flows; departures are sequenced by CSV speed/travel-time targets"))
    vehicle_rows: list[tuple[float, str, list[str]]] = []
    flow_rows: list[dict[str, Any]] = []
    generated_by_segment: dict[tuple[str, str], dict[str, float]] = {}
    source_counts: dict[str, int] = {}
    source_lanes: dict[str, int] = {}
    for flow in flows:
        if timing_profile == "csv_reality_sequential":
            departures, timing_details = csv_reality_sequential_departures(
                float(flow["vph"]),
                duration_sec,
                float(flow["depart_offset_sec"]),
                float(flow["reference_speed_kmh"]),
                float(flow["reference_speed_limit_kmh"]),
                float(flow["reference_travel_time_s"]),
                active_multiplier=pulse_active_multiplier,
            )
        elif timing_profile == "uniform":
            departures = evenly_spaced_departures(float(flow["vph"]), duration_sec, float(flow["depart_offset_sec"]))
            timing_details = {"pulse_cycle_sec": 0.0, "pulse_active_fraction": 1.0}
        else:
            raise ExpandedV7Error(f"unknown_demand_timing_profile:{timing_profile}")
        release_gap_shifted = 0
        if release_depart_gap_enabled and route_uses_release_axis(flow["route_edges"]):
            departures, release_gap_shifted = apply_min_depart_gap(departures, release_depart_gap_sec, duration_sec)
        template_cap_diverted = 0
        if route_template_vehicle_cap and len(departures) > route_template_vehicle_cap:
            template_cap_diverted = len(departures) - route_template_vehicle_cap
            departures = departures[:route_template_vehicle_cap]
        accepted_departures: list[float] = []
        skipped_by_cap = 0
        source_edge = flow["route_edges"][0]
        try:
            source_lanes[source_edge] = int(sumo_net.getEdge(source_edge).getLaneNumber())
        except Exception:
            source_lanes[source_edge] = 1
        source_cap = max(240, source_lanes[source_edge] * 600)
        for depart in departures:
            if flow["flow_type"] == "local_validation" and source_counts.get(source_edge, 0) >= source_cap:
                skipped_by_cap += 1
                continue
            source_counts[source_edge] = source_counts.get(source_edge, 0) + 1
            accepted_departures.append(depart)
        flow_rows.append({
            "flow_id": flow["flow_id"],
            "profile": profile,
            "flow_type": flow["flow_type"],
            "direction": flow["direction"],
            "segment_scope": flow["segment_scope"],
            "vph": float(flow["vph"]),
            "vehicle_count": len(accepted_departures),
            "skipped_by_source_cap": skipped_by_cap,
            "route_edge_count": len(flow["route_edges"]),
            "start_edge": flow["route_edges"][0],
            "target_edge": flow["route_edges"][-1],
            "accounted_only": False,
            "route_guard_reason": route_guard_reason(flow["flow_type"], flow["direction"], flow["route_edges"], strict=strict_route_guard),
            "route_uses_forbidden_bottleneck": route_uses_forbidden_bottleneck(flow["route_edges"]),
            "boundary_extension_applied": flow.get("boundary_extension_applied", False),
            "boundary_extension_reason": flow.get("boundary_extension_reason", ""),
            "terminal_sink_extension_v2_applied": flow.get("terminal_sink_extension_v2_applied", False),
            "terminal_sink_extension_v2_reason": flow.get("terminal_sink_extension_v2_reason", ""),
            "terminal_sink_extension_v3_applied": flow.get("terminal_sink_extension_v3_applied", False),
            "terminal_sink_extension_v3_reason": flow.get("terminal_sink_extension_v3_reason", ""),
            "distribution_variant_index": flow.get("distribution_variant_index", 1),
            "distribution_variant_count": flow.get("distribution_variant_count", 1),
            "reference_speed_kmh": round(float(flow["reference_speed_kmh"]), 6),
            "reference_travel_time_s": round(float(flow["reference_travel_time_s"]), 6),
            "reference_offset_sec": round(float(flow["reference_offset_sec"]), 6),
            "timing_profile": timing_profile,
            "pulse_cycle_sec": timing_details["pulse_cycle_sec"],
            "pulse_active_fraction": timing_details["pulse_active_fraction"],
            "release_depart_gap_applied_count": release_gap_shifted,
            "template_cap_diverted_count": template_cap_diverted,
            "free_segment_feeder_reason": flow.get("free_segment_feeder_reason", ""),
        })
        for segment_number in flow["segment_numbers"]:
            key = (f"S{segment_number}", flow["direction"])
            bucket = generated_by_segment.setdefault(key, {
                "through": 0.0,
                "local": 0.0,
                "feeder": 0.0,
                "accounted": 0.0,
                "guarded": 0.0,
                "template_cap_diverted": 0.0,
                "diversion": 0.0,
                "skipped": 0.0,
            })
            if flow["flow_type"] == "local_validation":
                bucket["local"] += len(accepted_departures)
            elif flow["flow_type"] == "free_segment_feeder":
                bucket["feeder"] += len(accepted_departures)
            else:
                bucket["through"] += len(accepted_departures)
            bucket["template_cap_diverted"] += template_cap_diverted / max(1, len(flow["segment_numbers"]))
            if flow["flow_type"] == "local_validation":
                bucket["skipped"] += skipped_by_cap
        for index, depart in enumerate(accepted_departures):
            vehicle_rows.append((depart, f"{flow['flow_id']}_{index:05d}", list(flow["route_edges"])))
    for depart, vehicle_id, route_edges in sorted(vehicle_rows, key=lambda item: (item[0], item[1])):
        vehicle = ET.SubElement(output_root, "vehicle", {
            "id": vehicle_id,
            "depart": f"{depart:.2f}",
            "departLane": "best",
            "departPos": "random_free",
            "departSpeed": "max",
        })
        ET.SubElement(vehicle, "route", {"edges": " ".join(route_edges)})
    output_route.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(output_root).write(output_route, encoding="utf-8", xml_declaration=True)
    segment_rows = []
    for row in volumes:
        target = float(row["volume_vph"]) * duration_sec / 3600.0
        for direction in ("upbound", "downbound"):
            key = (row["segment_id"], direction)
            generated = generated_by_segment.get(key, {
                "through": 0.0,
                "local": 0.0,
                "feeder": 0.0,
                "accounted": 0.0,
                "guarded": 0.0,
                "template_cap_diverted": 0.0,
                "diversion": 0.0,
                "skipped": 0.0,
            })
            generated["accounted"] = generated.get("accounted", 0.0) + accounted_local_by_segment_direction.get(key, 0.0)
            generated["guarded"] = generated.get("guarded", 0.0) + route_guarded_local_by_segment_direction.get(key, 0.0)
            effective_local_share = local_share_by_segment_direction.get(key, local_share)
            effective_feeder_share = feeder_share_by_segment_direction.get(key, 0.0)
            effective_through_share = upbound_through_share if direction == "upbound" else downbound_through_share
            effective_diversion_share = max(0.0, 1.0 - effective_through_share - effective_local_share - effective_feeder_share)
            diversion_count = target * effective_diversion_share
            mainline_generated_count = float(generated["through"] + generated["local"] + generated.get("feeder", 0.0))
            generated_count = mainline_generated_count + float(generated.get("accounted", 0.0)) + float(generated.get("template_cap_diverted", 0.0)) + diversion_count
            segment_rows.append({
                "segment_id": row["segment_id"],
                "direction": direction,
                "target_count": round(target, 6),
                "generated_template_count": int(round(generated_count)),
                "generated_recall": round(generated_count / target, 6) if target else "",
                "mainline_generated_count": int(round(mainline_generated_count)),
                "mainline_generated_recall": round(mainline_generated_count / target, 6) if target else "",
                "through_generated_count": int(round(generated["through"])),
                "local_generated_count": int(round(generated["local"])),
                "feeder_generated_count": int(round(generated.get("feeder", 0.0))),
                "accounted_local_count": int(round(generated.get("accounted", 0.0))),
                "route_guarded_local_count": int(round(generated.get("guarded", 0.0))),
                "template_cap_diverted_count": int(round(generated.get("template_cap_diverted", 0.0))),
                "diversion_assigned_count": int(round(diversion_count)),
                "local_skipped_by_source_cap": int(round(generated["skipped"])),
                "remaining_count": 0.0,
            })
    flow_rows.extend(accounted_flow_rows)
    recall_values = [safe_float(row["generated_recall"]) for row in segment_rows if row.get("generated_recall") not in {"", None}]
    mainline_recall_values = [safe_float(row["mainline_generated_recall"]) for row in segment_rows if row.get("mainline_generated_recall") not in {"", None}]
    source_rows = []
    for source_edge, count in sorted(source_counts.items()):
        cap = max(240, source_lanes.get(source_edge, 1) * 600)
        source_rows.append({
            "source_edge": source_edge,
            "source_lanes": source_lanes.get(source_edge, 1),
            "vehicle_count": count,
            "source_cap_per_hour": cap,
            "over_cap": count > cap,
        })
    summary = {
        "schema": "expanded_v7_screenline_demand.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(reference_csv),
        "mapping_csv": rel(mapping_csv),
        "net_file": rel(net_file),
        "route_file": rel(output_route),
        "duration_sec": duration_sec,
        "profile": profile,
        "timing_profile": timing_profile,
        "local_validation_share": local_share,
        "bottleneck_local_validation_share": bottleneck_local_share,
        "main_pass_ratio": main_pass_ratio,
        "through_share": through_share,
        "upbound_through_share": upbound_through_share,
        "downbound_through_share": downbound_through_share,
        "diversion_share": max(0.0, 1.0 - main_pass_ratio),
        "avoid_bottleneck_internal_local_source_sink": avoid_bottleneck_internal,
        "strict_bottleneck_route_guard": strict_route_guard,
        "local_accounting_guard_enabled": local_accounting_guard,
        "downstream_sink_guard_enabled": bool(settings.get("downstream_sink_guard_enabled", False)),
        "distributed_boundary_enabled": distributed_boundary,
        "boundary_extension_limit": boundary_extension_limit if distributed_boundary else "",
        "boundary_extension_applied_count": boundary_extension_count,
        "boundary_extension_incomplete_count": boundary_extension_incomplete_count,
        "terminal_sink_extension_v2_enabled": terminal_sink_extension_v2,
        "terminal_sink_extension_v2_applied_count": terminal_extension_count,
        "terminal_sink_extension_v2_unavailable_count": terminal_extension_unavailable_count,
        "terminal_sink_extension_v3_enabled": terminal_sink_extension_v3,
        "terminal_sink_extension_v3_limit": terminal_sink_extension_v3_limit if terminal_sink_extension_v3 else "",
        "terminal_sink_extension_v3_applied_count": terminal_v3_extension_count,
        "terminal_sink_extension_v3_unavailable_count": terminal_v3_unavailable_count,
        "terminal_sink_extension_v3_diverted_count": terminal_v3_diverted_count,
        "release_depart_gap_enabled": release_depart_gap_enabled,
        "release_depart_gap_sec": release_depart_gap_sec if release_depart_gap_enabled else "",
        "free_segment_feeder_enabled": free_segment_feeder_enabled,
        "free_segment_feeder_share": free_segment_feeder_share if free_segment_feeder_enabled else "",
        "plausibility_first": bool(settings.get("plausibility_first", False)),
        "generated_demand_recall_is_report_only": bool(settings.get("generated_demand_recall_is_report_only", False)),
        "short_edge_artifact_length_m": settings.get("short_edge_artifact_length_m", ""),
        "short_edge_warn_length_m": settings.get("short_edge_warn_length_m", ""),
        "pulse_active_multiplier": pulse_active_multiplier,
        "sideflow_ratio": settings["sideflow_ratio"],
        "mapwide_background_ratio": settings["mapwide_background_ratio"],
        "base_through_vph": base_vph,
        "leading_prefix_extra_vph": extra_vph,
        "leading_prefix_segments": " ".join(f"S{number}" for number in high_segments),
        "vehicle_count": len(vehicle_rows),
        "flow_count": len(flow_rows),
        "accounted_only_flow_count": sum(1 for row in flow_rows if row.get("accounted_only") is True),
        "route_guarded_local_count": int(round(sum(route_guarded_local_by_segment_direction.values()))),
        "through_distribution": {
            "enabled": True,
            "max_variants_per_through_flow": through_max_variants,
            "through_flow_rows": sum(1 for row in flow_rows if row["flow_type"] == "through"),
        },
        "flow_rows": flow_rows,
        "source_rows": source_rows,
        "method": "expanded_v7_balanced_screenline_local_diversion_csv_reality_timed",
        "mean_generated_recall": mean(recall_values),
        "min_generated_recall": min(recall_values) if recall_values else 0.0,
        "max_generated_recall": max(recall_values) if recall_values else 0.0,
        "mean_mainline_generated_recall": mean(mainline_recall_values),
        "min_mainline_generated_recall": min(mainline_recall_values) if mainline_recall_values else 0.0,
        "max_mainline_generated_recall": max(mainline_recall_values) if mainline_recall_values else 0.0,
    }
    return segment_rows, summary


def sanitize_demand_routes(route_xml: Path, net_file: Path) -> dict[str, Any]:
    s07 = step07_module()
    sumo_net = read_sumo_net(net_file)
    tree = ET.parse(route_xml)
    root = tree.getroot()
    rerouted = 0
    removed = 0
    kept = 0
    examples: list[dict[str, Any]] = []
    for vehicle in list(root.findall("vehicle")):
        route = vehicle.find("route")
        edge_text = (route.get("edges") or "") if route is not None else ""
        edges = [edge for edge in edge_text.split() if edge]
        if route is None or not edges:
            root.remove(vehicle)
            removed += 1
            continue
        if route_connected(sumo_net, edges):
            kept += 1
            continue
        known_edges = []
        for edge_id in edges:
            try:
                edge = sumo_net.getEdge(edge_id)
            except Exception:
                continue
            if not edge.isSpecial() and edge.allows("passenger"):
                known_edges.append(edge_id)
        repaired: list[str] = []
        if len(known_edges) >= 2:
            try:
                repaired = s07.shortest_route(sumo_net, known_edges[0], known_edges[-1])
            except Exception:
                repaired = []
        if repaired and route_connected(sumo_net, repaired):
            route.set("edges", " ".join(repaired))
            rerouted += 1
            if len(examples) < 8:
                examples.append({
                    "vehicle_id": vehicle.get("id", ""),
                    "old_edge_count": len(edges),
                    "new_edge_count": len(repaired),
                    "first_edge": repaired[0],
                    "last_edge": repaired[-1],
                })
        else:
            root.remove(vehicle)
            removed += 1
            if len(examples) < 8:
                examples.append({
                    "vehicle_id": vehicle.get("id", ""),
                    "old_edge_count": len(edges),
                    "reason": "no_connected_repair_route",
                })
    tree.write(route_xml, encoding="utf-8", xml_declaration=True)
    return {
        "schema": "expanded_v7_demand_route_sanitization.v1",
        "kept_vehicle_count": kept,
        "rerouted_vehicle_count": rerouted,
        "removed_vehicle_count": removed,
        "remaining_vehicle_count": len(root.findall("vehicle")),
        "examples": examples,
    }


def append_sideflow_demand(route_xml: Path, net_file: Path, mapping_csv: Path, ratio: float = 0.075) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    s07 = step07_module()
    sumo_net = read_sumo_net(net_file)
    mapping = [row for row in read_csv(mapping_csv) if row.get("repair_target") in {"True", "true", "1", True}]
    main_edges = []
    for row in mapping:
        edge_id = row["edge_id"]
        if edge_id not in main_edges:
            main_edges.append(edge_id)
    if not main_edges:
        return [], {"ratio": ratio, "vehicle_count": 0, "reason": "no_main_edges"}
    side_rows: list[dict[str, Any]] = []
    candidate_pairs = []
    for edge_id in main_edges[:: max(1, len(main_edges) // 24)]:
        try:
            edge = sumo_net.getEdge(edge_id)
        except Exception:
            continue
        if guarded_downstream_sink(edge_id):
            continue
        for out_edge in edge.getOutgoing():
            out_id = out_edge.getID()
            if out_id in main_edges or out_edge.isSpecial() or not out_edge.allows("passenger"):
                continue
            if guarded_downstream_sink(out_id):
                continue
            try:
                route = s07.shortest_route(sumo_net, edge_id, out_id)
                route = extend_route_until(
                    sumo_net,
                    route,
                    prefix_ok=lambda source_id: True,
                    suffix_ok=lambda sink_id: not guarded_downstream_sink(sink_id),
                    limit=5,
                )
                if route_guard_reason("sideflow", "mixed", route, strict=True):
                    continue
                if len(route) >= 2 and not guarded_downstream_sink(route[-1]):
                    candidate_pairs.append((edge_id, route[-1], route))
            except Exception:
                continue
            break
    tree = ET.parse(route_xml)
    root = tree.getroot()
    existing_count = len(root.findall("vehicle"))
    side_count = max(1, int(round(existing_count * ratio)))
    if not candidate_pairs:
        tree.write(route_xml, encoding="utf-8", xml_declaration=True)
        return [], {"ratio": ratio, "vehicle_count": 0, "reason": "no_sideflow_pairs"}
    for index in range(side_count):
        source, sink, route = candidate_pairs[index % len(candidate_pairs)]
        depart = 3600.0 * (index + 0.5) / side_count
        vehicle = ET.SubElement(root, "vehicle", {
            "id": f"expanded_v7_sideflow_{index:05d}",
            "depart": f"{depart:.2f}",
            "departLane": "best",
            "departPos": "random_free",
            "departSpeed": "max",
        })
        ET.SubElement(vehicle, "route", {"edges": " ".join(route)})
        side_rows.append({
            "sideflow_id": vehicle.get("id"),
            "source_edge": source,
            "sink_edge": sink,
            "route_edges": " ".join(route),
            "depart": f"{depart:.2f}",
            "reason": "main_road_branch_sideflow",
        })
    sort_vehicle_elements_by_depart(root)
    tree.write(route_xml, encoding="utf-8", xml_declaration=True)
    return side_rows, {"ratio": ratio, "vehicle_count": len(side_rows), "candidate_pair_count": len(candidate_pairs)}


def append_mapwide_background_demand(
    route_xml: Path,
    net_file: Path,
    mapping_csv: Path,
    ratio: float = 0.05,
    template_vehicle_cap: int | None = None,
    template_target_count: int | None = None,
    distributed_boundary: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    s07 = step07_module()
    sumo_net = read_sumo_net(net_file)
    main_edges = {row["edge_id"] for row in read_csv(mapping_csv) if row.get("repair_target") in {"True", "true", "1", True}}
    candidates = []
    for edge in sumo_net.getEdges():
        if not passenger_candidate_edge(edge):
            continue
        edge_id = edge.getID()
        if edge_id in main_edges:
            continue
        if forbidden_source_sink_edge(edge_id):
            continue
        if distributed_boundary and edge_id in MAPWIDE_TELEPORT_EDGE_BLOCKLIST:
            continue
        try:
            length = float(edge.getLength())
            lanes = int(edge.getLaneNumber())
        except Exception:
            continue
        if length < 35.0:
            continue
        candidates.append((edge_id, length, lanes))
    candidates.sort(key=lambda item: (-item[1], item[0]))
    tree = ET.parse(route_xml)
    root = tree.getroot()
    existing_count = len(root.findall("vehicle"))
    vehicle_count = max(1, int(round(existing_count * ratio)))
    if len(candidates) < 4:
        tree.write(route_xml, encoding="utf-8", xml_declaration=True)
        return [], {"ratio": ratio, "vehicle_count": 0, "reason": "insufficient_mapwide_candidate_edges"}
    sample_target = 240 if distributed_boundary else 80
    step = max(1, len(candidates) // sample_target)
    sampled = candidates[::step][:sample_target]
    route_templates: list[tuple[str, str, list[str]]] = []
    seen_templates: set[str] = set()
    offsets = [max(1, len(sampled) // 2), max(1, len(sampled) // 3), max(1, len(sampled) // 4), 17, 31, 47]
    template_limit = template_target_count or 48
    max_route_len = 80 if distributed_boundary else 45
    for offset in offsets:
        for index, source in enumerate(sampled):
            sink = sampled[(index + offset + (index % 11)) % len(sampled)]
            source_id, sink_id = source[0], sink[0]
            if source_id == sink_id:
                continue
            if forbidden_source_sink_edge(source_id) or forbidden_source_sink_edge(sink_id):
                continue
            try:
                route = s07.shortest_route(sumo_net, source_id, sink_id)
            except Exception:
                continue
            if route and (forbidden_source_sink_edge(route[0]) or forbidden_source_sink_edge(route[-1])):
                continue
            if distributed_boundary and any(forbidden_source_sink_edge(edge_id) for edge_id in route):
                continue
            if distributed_boundary and any(edge_id in MAPWIDE_TELEPORT_EDGE_BLOCKLIST for edge_id in route):
                continue
            if any(guarded_downstream_sink(edge_id) for edge_id in route):
                continue
            key = connected_route_key(route)
            if key in seen_templates:
                continue
            if 2 <= len(route) <= max_route_len and route_connected(sumo_net, route):
                seen_templates.add(key)
                route_templates.append((source_id, sink_id, route))
            if len(route_templates) >= template_limit:
                break
        if len(route_templates) >= template_limit:
            break
    if not route_templates:
        tree.write(route_xml, encoding="utf-8", xml_declaration=True)
        return [], {"ratio": ratio, "vehicle_count": 0, "reason": "no_connected_mapwide_routes"}
    rows: list[dict[str, Any]] = []
    template_counts = [0 for _template in route_templates]
    for index in range(vehicle_count):
        template_index = index % len(route_templates)
        if template_vehicle_cap:
            for offset in range(len(route_templates)):
                candidate_index = (index + offset) % len(route_templates)
                if template_counts[candidate_index] < template_vehicle_cap:
                    template_index = candidate_index
                    break
        template_counts[template_index] += 1
        source, sink, route = route_templates[template_index]
        depart = (3600.0 * (index + 0.5) / vehicle_count + template_index * 0.73 + (index % 13) * 0.11) % 3600.0
        vehicle = ET.SubElement(root, "vehicle", {
            "id": f"expanded_v7_mapwide_{index:05d}",
            "depart": f"{depart:.2f}",
            "departLane": "best",
            "departPos": "random_free",
            "departSpeed": "max",
        })
        ET.SubElement(vehicle, "route", {"edges": " ".join(route)})
        rows.append({
            "mapwide_id": vehicle.get("id"),
            "source_edge": source,
            "sink_edge": sink,
            "route_edges": " ".join(route),
            "route_edge_count": len(route),
            "depart": f"{depart:.2f}",
            "reason": "expanded_map_background_distributed_boundary" if distributed_boundary else "expanded_map_background_circulation",
        })
    sort_vehicle_elements_by_depart(root)
    tree.write(route_xml, encoding="utf-8", xml_declaration=True)
    return rows, {
        "ratio": ratio,
        "vehicle_count": len(rows),
        "candidate_edge_count": len(candidates),
        "route_template_count": len(route_templates),
        "template_vehicle_cap": template_vehicle_cap or "",
        "max_template_vehicle_count": max(template_counts) if template_counts else 0,
        "distributed_boundary": distributed_boundary,
        "teleport_edge_blocklist_count": len(MAPWIDE_TELEPORT_EDGE_BLOCKLIST) if distributed_boundary else 0,
    }


def sort_vehicle_elements_by_depart(root: ET.Element) -> None:
    vehicles = list(root.findall("vehicle"))
    if len(vehicles) < 2:
        return
    for vehicle in vehicles:
        root.remove(vehicle)
    vehicles.sort(key=lambda vehicle: (float(vehicle.get("depart", "0") or 0.0), vehicle.get("id", "")))
    for vehicle in vehicles:
        root.append(vehicle)


def manifest_payload() -> dict[str, Any]:
    demand_summary = read_json(DEMAND_XML.with_suffix(".summary.json")) if DEMAND_XML.with_suffix(".summary.json").is_file() else {}
    active_net = active_b0_net()
    tls_summary = read_json(TLS_FIX_SUMMARY_JSON) if TLS_FIX_SUMMARY_JSON.is_file() else {}
    speedcap_summary = read_json(SPEEDCAP_SUMMARY_JSON) if SPEEDCAP_SUMMARY_JSON.is_file() else {}
    release_speedcap_summary = read_json(RELEASE_SPEEDCAP_SUMMARY_JSON) if RELEASE_SPEEDCAP_SUMMARY_JSON.is_file() else {}
    downbound_metering_summary = read_json(DOWNBOUND_METERING_SUMMARY_JSON) if DOWNBOUND_METERING_SUMMARY_JSON.is_file() else {}
    overopen_metering_summary = read_json(OVEROPEN_METERING_SUMMARY_JSON) if OVEROPEN_METERING_SUMMARY_JSON.is_file() else {}
    route_edge_overopen_metering_summary = read_json(ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON) if ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON.is_file() else {}
    release_junction_fixed_summary = read_json(RELEASE_JUNCTION_FIXED_SUMMARY_JSON) if RELEASE_JUNCTION_FIXED_SUMMARY_JSON.is_file() else {}
    lane_drop_fixed_summary = read_json(LANE_DROP_FIXED_SUMMARY_JSON) if LANE_DROP_FIXED_SUMMARY_JSON.is_file() else {}
    plausibility_overopen_summary = read_json(PLAUSIBILITY_OVEROPEN_SUMMARY_JSON) if PLAUSIBILITY_OVEROPEN_SUMMARY_JSON.is_file() else {}
    return {
        "schema": "expanded_v7_b0_manifest.v1",
        "active_net": rel(active_net),
        "background_route": rel(DEMAND_XML),
        "background_demand_design": {
            "method": "expanded_v7_reference_main_local_sideflow",
            "reference_csv": rel(REFERENCE_CSV),
            "warmup_sec": 600.0,
            "warmup_scale": 1.0,
            "sustain_scale": 1.0,
            "sideflow_ratio": demand_summary.get("sideflow", {}).get("ratio", demand_summary.get("sideflow_ratio", 0.05)),
            "mapwide_background_ratio": demand_summary.get("mapwide_background", {}).get("ratio", demand_summary.get("mapwide_background_ratio", 0.55)),
            "profile": demand_summary.get("profile", "balanced_diversion"),
            "timing_profile": demand_summary.get("timing_profile", "csv_reality_sequential"),
            "local_validation_share": demand_summary.get("local_validation_share", 0.08),
            "bottleneck_local_validation_share": demand_summary.get("bottleneck_local_validation_share", ""),
            "main_pass_ratio": demand_summary.get("main_pass_ratio", 0.55),
            "upbound_through_share": demand_summary.get("upbound_through_share", ""),
            "downbound_through_share": demand_summary.get("downbound_through_share", ""),
            "diversion_share": demand_summary.get("diversion_share", 0.45),
            "avoid_bottleneck_internal_local_source_sink": demand_summary.get("avoid_bottleneck_internal_local_source_sink", False),
            "strict_bottleneck_route_guard": demand_summary.get("strict_bottleneck_route_guard", False),
            "local_accounting_guard_enabled": demand_summary.get("local_accounting_guard_enabled", False),
            "downstream_sink_guard_enabled": demand_summary.get("downstream_sink_guard_enabled", False),
            "distributed_boundary_enabled": demand_summary.get("distributed_boundary_enabled", False),
            "boundary_extension_applied_count": demand_summary.get("boundary_extension_applied_count", ""),
            "boundary_extension_incomplete_count": demand_summary.get("boundary_extension_incomplete_count", ""),
            "terminal_sink_extension_v2_enabled": demand_summary.get("terminal_sink_extension_v2_enabled", False),
            "terminal_sink_extension_v2_applied_count": demand_summary.get("terminal_sink_extension_v2_applied_count", ""),
            "terminal_sink_extension_v3_enabled": demand_summary.get("terminal_sink_extension_v3_enabled", False),
            "terminal_sink_extension_v3_limit": demand_summary.get("terminal_sink_extension_v3_limit", ""),
            "terminal_sink_extension_v3_applied_count": demand_summary.get("terminal_sink_extension_v3_applied_count", ""),
            "terminal_sink_extension_v3_unavailable_count": demand_summary.get("terminal_sink_extension_v3_unavailable_count", ""),
            "terminal_sink_extension_v3_diverted_count": demand_summary.get("terminal_sink_extension_v3_diverted_count", ""),
            "release_depart_gap_enabled": demand_summary.get("release_depart_gap_enabled", False),
            "release_depart_gap_sec": demand_summary.get("release_depart_gap_sec", ""),
            "free_segment_feeder_enabled": demand_summary.get("free_segment_feeder_enabled", False),
            "free_segment_feeder_share": demand_summary.get("free_segment_feeder_share", ""),
            "plausibility_first": demand_summary.get("plausibility_first", False),
            "generated_demand_recall_is_report_only": demand_summary.get("generated_demand_recall_is_report_only", False),
            "short_edge_artifact_length_m": demand_summary.get("short_edge_artifact_length_m", SHORT_EDGE_ARTIFACT_LENGTH_M),
            "short_edge_warn_length_m": demand_summary.get("short_edge_warn_length_m", SHORT_EDGE_WARN_LENGTH_M),
            "pulse_active_multiplier": demand_summary.get("pulse_active_multiplier", 1.35),
            "through_distribution": demand_summary.get("through_distribution", {}),
        },
        "tls_fix": {
            "enabled": active_net in {TLS_FIXED_NET, SPEEDCAP_NET, RELEASE_SPEEDCAP_NET, DOWNBOUND_METERING_NET, OVEROPEN_METERING_NET, ROUTE_EDGE_OVEROPEN_METERING_NET, RELEASE_JUNCTION_FIXED_NET, LANE_DROP_FIXED_NET, PLAUSIBILITY_OVEROPEN_NET, MAKE_SENSE_FIXED_NET},
            "summary_json": rel(TLS_FIX_SUMMARY_JSON) if TLS_FIX_SUMMARY_JSON.is_file() else "",
            "tls_id": tls_summary.get("tls_id", DOWNSTREAM_TLS_ID),
            "target_link_index": tls_summary.get("target_link_index", DOWNSTREAM_TLS_TARGET_LINK),
            "target_connection": tls_summary.get("target_connection", "781985787#0 -> 218915135#3"),
            "before_link_green_sec": tls_summary.get("before_link_green_sec", {}),
            "after_link_green_sec": tls_summary.get("after_link_green_sec", {}),
        },
        "speedcap": {
            "enabled": active_net == SPEEDCAP_NET,
            "summary_json": rel(SPEEDCAP_SUMMARY_JSON) if SPEEDCAP_SUMMARY_JSON.is_file() else "",
            "reference_csv": speedcap_summary.get("reference_csv", rel(REFERENCE_CSV)),
            "changed_edge_count": speedcap_summary.get("changed_edge_count", ""),
            "changed_lane_count": speedcap_summary.get("changed_lane_count", ""),
            "min_cap_kmh": speedcap_summary.get("min_cap_kmh", ""),
            "max_cap_kmh": speedcap_summary.get("max_cap_kmh", ""),
        },
        "release_speedcap": {
            "enabled": active_net == RELEASE_SPEEDCAP_NET,
            "summary_json": rel(RELEASE_SPEEDCAP_SUMMARY_JSON) if RELEASE_SPEEDCAP_SUMMARY_JSON.is_file() else "",
            "cap_kmh": release_speedcap_summary.get("cap_kmh", ""),
            "changed_edge_count": release_speedcap_summary.get("changed_edge_count", ""),
            "changed_lane_count": release_speedcap_summary.get("changed_lane_count", ""),
        },
        "downbound_metering": {
            "enabled": active_net == DOWNBOUND_METERING_NET,
            "summary_json": rel(DOWNBOUND_METERING_SUMMARY_JSON) if DOWNBOUND_METERING_SUMMARY_JSON.is_file() else "",
            "reference_csv": downbound_metering_summary.get("reference_csv", rel(REFERENCE_CSV)),
            "changed_edge_count": downbound_metering_summary.get("changed_edge_count", ""),
            "changed_lane_count": downbound_metering_summary.get("changed_lane_count", ""),
            "min_cap_kmh": downbound_metering_summary.get("min_cap_kmh", ""),
            "max_cap_kmh": downbound_metering_summary.get("max_cap_kmh", ""),
            "margin_kmh": downbound_metering_summary.get("margin_kmh", ""),
        },
        "overopen_metering": {
            "enabled": active_net == OVEROPEN_METERING_NET,
            "summary_json": rel(OVEROPEN_METERING_SUMMARY_JSON) if OVEROPEN_METERING_SUMMARY_JSON.is_file() else "",
            "source_edge_speed_csv": overopen_metering_summary.get("source_edge_speed_csv", ""),
            "changed_edge_count": overopen_metering_summary.get("changed_edge_count", ""),
            "changed_lane_count": overopen_metering_summary.get("changed_lane_count", ""),
            "target_edge_count": overopen_metering_summary.get("target_edge_count", ""),
        },
        "route_edge_overopen_metering": {
            "enabled": active_net in {ROUTE_EDGE_OVEROPEN_METERING_NET, RELEASE_JUNCTION_FIXED_NET, LANE_DROP_FIXED_NET, PLAUSIBILITY_OVEROPEN_NET, MAKE_SENSE_FIXED_NET},
            "summary_json": rel(ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON) if ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON.is_file() else "",
            "source_edge_speed_csv": route_edge_overopen_metering_summary.get("source_edge_speed_csv", ""),
            "changed_edge_count": route_edge_overopen_metering_summary.get("changed_edge_count", ""),
            "changed_lane_count": route_edge_overopen_metering_summary.get("changed_lane_count", ""),
            "target_edge_count": route_edge_overopen_metering_summary.get("target_edge_count", ""),
            "free_flow_threshold_kmh": route_edge_overopen_metering_summary.get("free_flow_threshold_kmh", FLOW_FREE_SPEED_KMH),
        },
        "release_junction_fixed": {
            "enabled": active_net in {RELEASE_JUNCTION_FIXED_NET, LANE_DROP_FIXED_NET, PLAUSIBILITY_OVEROPEN_NET, MAKE_SENSE_FIXED_NET},
            "summary_json": rel(RELEASE_JUNCTION_FIXED_SUMMARY_JSON) if RELEASE_JUNCTION_FIXED_SUMMARY_JSON.is_file() else "",
            "changed_connection_count": release_junction_fixed_summary.get("changed_connection_count", ""),
            "changed_edge_priority_count": release_junction_fixed_summary.get("changed_edge_priority_count", ""),
            "target_edge_count": release_junction_fixed_summary.get("target_edge_count", ""),
        },
        "lane_drop_fixed": {
            "enabled": active_net in {LANE_DROP_FIXED_NET, PLAUSIBILITY_OVEROPEN_NET, MAKE_SENSE_FIXED_NET},
            "summary_json": rel(LANE_DROP_FIXED_SUMMARY_JSON) if LANE_DROP_FIXED_SUMMARY_JSON.is_file() else "",
            "changed_edge_count": lane_drop_fixed_summary.get("changed_edge_count", ""),
            "changed_lane_count": lane_drop_fixed_summary.get("changed_lane_count", ""),
            "changed_speed_count": lane_drop_fixed_summary.get("changed_speed_count", ""),
            "added_connection_count": lane_drop_fixed_summary.get("added_connection_count", ""),
            "road_integrity_audit": lane_drop_fixed_summary.get("road_integrity_audit", {}),
        },
        "plausibility_overopen_speedcap": {
            "enabled": active_net in {PLAUSIBILITY_OVEROPEN_NET, MAKE_SENSE_FIXED_NET},
            "summary_json": rel(PLAUSIBILITY_OVEROPEN_SUMMARY_JSON) if PLAUSIBILITY_OVEROPEN_SUMMARY_JSON.is_file() else "",
            "source_edge_speed_csv": plausibility_overopen_summary.get("source_edge_speed_csv", ""),
            "changed_edge_count": plausibility_overopen_summary.get("changed_edge_count", ""),
            "changed_lane_count": plausibility_overopen_summary.get("changed_lane_count", ""),
            "target_edge_count": plausibility_overopen_summary.get("target_edge_count", ""),
            "free_flow_threshold_kmh": FLOW_FREE_SPEED_KMH,
        },
        "route_set": "custom_accepted",
        "custom_routes": rel(ACCEPTED_ROUTES_CSV),
        "firetruck_route_xml": rel(ACCEPTED_ROUTE_XML),
        "emergency_vtype_attrs": firetruck_vtype_attrs(),
        "emergency_depart_sec": 600,
        "final_background_required_substring": "background_routes_expanded_v7_reference_main_sideflow",
        "optimization_route": {
            "route_id": "FIRETRUCK_TO_SEOUL_STATION_FRONT",
            "route_name_ko": "firetruck_to_seoul_station_front",
        },
        "notes": "Expanded-v7 B0-only firetruck baseline. B1/B2 control changes are out of scope.",
    }


def build_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    ensure_isolated_output(path)
    payload = manifest_payload()
    write_json(path, payload)
    return payload


def make_sense_fixed_selection_summary() -> dict[str, Any]:
    if not MAKE_SENSE_FIXED_NET.is_file() or not MAKE_SENSE_CANDIDATE_SUMMARY_JSON.is_file():
        return {
            "enabled": False,
            "reason": "missing_make_sense_fixed_net_or_summary",
            "candidate_net": rel(MAKE_SENSE_FIXED_NET),
            "summary_json": rel(MAKE_SENSE_CANDIDATE_SUMMARY_JSON),
        }
    summary = read_json(MAKE_SENSE_CANDIDATE_SUMMARY_JSON)
    post_audit = summary.get("post_make_sense_audit", {}) or {}
    route_connectivity = summary.get("route_connectivity", {}) or {}
    sumo_net_load = summary.get("sumo_net_load", {}) or {}
    selected = (
        summary.get("status") == "PASS"
        and post_audit.get("status") == "PASS"
        and safe_int(post_audit.get("structural_defect_count")) == 0
        and safe_int(post_audit.get("lane_drop_3_to_1_count")) == 0
        and safe_int(post_audit.get("lane_drop_2_to_1_count")) == 0
        and safe_int(post_audit.get("disconnected_pair_count")) == 0
        and sumo_net_load.get("status") == "PASS"
        and route_connectivity.get("status") == "PASS"
    )
    return {
        "enabled": selected,
        "summary_json": rel(MAKE_SENSE_CANDIDATE_SUMMARY_JSON),
        "candidate_net": rel(MAKE_SENSE_FIXED_NET),
        "reason": "post_audit_structural_defects_zero" if selected else "post_audit_or_load_not_pass",
        "summary_status": summary.get("status", ""),
        "post_audit_status": post_audit.get("status", ""),
        "structural_defect_count": safe_int(post_audit.get("structural_defect_count")),
        "lane_drop_3_to_1_count": safe_int(post_audit.get("lane_drop_3_to_1_count")),
        "lane_drop_2_to_1_count": safe_int(post_audit.get("lane_drop_2_to_1_count")),
        "disconnected_pair_count": safe_int(post_audit.get("disconnected_pair_count")),
        "sumo_net_load": sumo_net_load,
        "route_connectivity": route_connectivity,
    }


def conservative_b0_net() -> Path:
    make_sense_summary = make_sense_fixed_selection_summary()
    if make_sense_summary.get("enabled") is True:
        return MAKE_SENSE_FIXED_NET
    return active_b0_net()


def conservative_manifest_payload() -> dict[str, Any]:
    payload = manifest_payload()
    route_summary = build_conservative_firetruck_route_xml()
    make_sense_summary = make_sense_fixed_selection_summary()
    payload["active_net"] = rel(conservative_b0_net())
    payload["make_sense_fixed"] = make_sense_summary
    payload["schema"] = "expanded_v7_conservative_b0_manifest.v1"
    payload["firetruck_route_xml"] = route_summary["route_xml"]
    payload["emergency_vtype_attrs"] = conservative_firetruck_vtype_attrs()
    payload["emergency_behavior_profile"] = "conservative_firetruck_b0"
    payload["disable_dynamic_emergency_insert"] = True
    payload["emergency_lane_guidance_mode"] = "disabled"
    payload["emergency_depart_policy"] = "scheduled_vehicle_depart_600_with_sumo_insertion_checks"
    payload["baseline_interpretation_ko"] = (
        "첨두시간 현실 B0 baseline: 소방차가 앞차를 밀고 지나가지 않고, 막히면 departDelay/waitingTime을 갖고 기다립니다."
    )
    return payload


def build_conservative_manifest(path: Path = CONSERVATIVE_MANIFEST) -> dict[str, Any]:
    ensure_isolated_output(path)
    payload = conservative_manifest_payload()
    write_json(path, payload)
    return payload


def run_b0(manifest: Path = MANIFEST, accepted_routes: Path = ACCEPTED_ROUTES_CSV, output_prefix: str = RUN_PREFIX) -> dict[str, Any]:
    read_route_acceptance(ROUTE_ACCEPTANCE_JSON)
    if not accepted_routes.is_file():
        raise ExpandedV7Error(f"missing_accepted_routes:{rel(accepted_routes)}")
    command = [
        sys.executable,
        str(PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py"),
        "--manifest",
        str(manifest),
        "--route-set",
        "custom_accepted",
        "--custom-routes",
        str(accepted_routes),
        "--modes",
        "B0",
        "--repeats",
        "1",
        "--workers",
        "1",
        "--emit-fcd",
        "--output-prefix",
        output_prefix,
        "--timeout-steps",
        "3600",
        "--allow-nonfinal-background",
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False, text=True, capture_output=True, timeout=3600)
    latest = METRICS_ROOT / output_prefix / "latest.json"
    summary = {
        "schema": "expanded_v7_b0_run.v1",
        "generated_at": utc_now(),
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-3000:],
        "stderr_tail": completed.stderr[-3000:],
        "latest_json": rel(latest) if latest.is_file() else "",
    }
    write_json(METRICS_ROOT / output_prefix / "run_command_summary.json", summary)
    write_b0_review_html(B0_REVIEW_HTML, summary)
    if completed.returncode != 0:
        raise ExpandedV7Error(f"b0_run_failed:{completed.stderr[-2000:] or completed.stdout[-2000:]}")
    return summary


def run_conservative_b0() -> dict[str, Any]:
    build_conservative_manifest()
    return run_b0(manifest=CONSERVATIVE_MANIFEST, accepted_routes=ACCEPTED_ROUTES_CSV, output_prefix=CONSERVATIVE_RUN_PREFIX)


def validate_expanded(manifest: Path = MANIFEST, results_csv: str = "auto") -> dict[str, Any]:
    validator_path = PROJECT_ROOT / "01-1 Validation/validate_b0_reality_recall.py"
    command = [
        sys.executable,
        str(validator_path),
        "--reference-csv",
        str(REFERENCE_CSV),
        "--manifest",
        str(manifest),
        "--results-csv",
        results_csv,
        "--output-root",
        str(METRICS_ROOT / VALIDATION_PREFIX),
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False, text=True, capture_output=True, timeout=900)
    latest_path = METRICS_ROOT / VALIDATION_PREFIX / "latest.json"
    payload = {
        "schema": "expanded_v7_validation_command.v1",
        "generated_at": utc_now(),
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-3000:],
        "stderr_tail": completed.stderr[-3000:],
        "latest_json": rel(latest_path) if latest_path.is_file() else "",
    }
    if latest_path.is_file():
        latest = read_json(latest_path)
        summary_json = project_path(latest.get("summary_json", ""))
        if summary_json.is_file():
            summary = read_json(summary_json)
            payload["validation_summary"] = summary
            side_summary = summarize_sideflow()
            payload["sideflow"] = side_summary
            write_json(summary_json.with_name("sideflow_demand_summary.json"), side_summary)
            payload["recall_breakdown"] = write_recall_breakdown(summary, side_summary, summary_json.parent)
            payload["flow_plausibility_audit"] = write_flow_plausibility_audit(summary, summary_json.parent)
            payload["congestion_sweep_candidates"] = write_congestion_sweep_candidates(summary, summary_json.parent)
            if summary.get("overall_status") != "PASS":
                payload["report_only_recommendations"] = write_report_only_recommendations(summary, side_summary, summary_json.parent)
    write_json(METRICS_ROOT / VALIDATION_PREFIX / "validation_command_summary.json", payload)
    write_validation_review_html(VALIDATION_REVIEW_HTML, payload)
    if completed.returncode != 0:
        raise ExpandedV7Error(f"validation_failed:{completed.stderr[-2000:] or completed.stdout[-2000:]}")
    return payload


def write_recall_breakdown(summary: dict[str, Any], sideflow: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    outputs = summary.get("outputs", {})
    lane_rows = read_csv(project_path(outputs.get("lane_csv", ""))) if outputs.get("lane_csv") else []
    demand_rows = read_csv(project_path(outputs.get("demand_csv", ""))) if outputs.get("demand_csv") else []
    speed_rows = read_csv(project_path(outputs.get("speed_csv", ""))) if outputs.get("speed_csv") else []
    edge_rows = read_csv(project_path(outputs.get("edge_speed_csv", ""))) if outputs.get("edge_speed_csv") else []
    demand_summary = read_json(DEMAND_XML.with_suffix(".summary.json")) if DEMAND_XML.with_suffix(".summary.json").is_file() else {}
    edge_lane_summary = read_json(EDGE_LANE_RECALL_SIMPLE_CSV.with_suffix(".summary.json")) if EDGE_LANE_RECALL_SIMPLE_CSV.with_suffix(".summary.json").is_file() else {}
    directional = direction_recall_summary(lane_rows, demand_rows, speed_rows, edge_rows)
    rows = []
    for row in directional:
        rows.append({
            "direction": row["direction"],
            "generated_recall_mean": demand_summary.get("mean_generated_recall", ""),
            "realized_demand_median_recall": row["demand_median_recall"],
            "realized_demand_pass_warn_ratio": row["demand_pass_warn_ratio"],
            "speed_mae_kmh": row["speed_mae_kmh"],
            "speed_mean_error_kmh": row["speed_mean_error_kmh"],
            "over_congested_segments": row["over_congested_segments"],
            "over_open_segments": row["over_open_segments"],
            "edge_over_congested_count": row["edge_over_congested_count"],
            "edge_over_open_count": row["edge_over_open_count"],
        })
    csv_path = output_dir / "expanded_v7_recall_breakdown_by_direction.csv"
    write_csv(csv_path, rows, [
        "direction", "generated_recall_mean", "realized_demand_median_recall",
        "realized_demand_pass_warn_ratio", "speed_mae_kmh", "speed_mean_error_kmh",
        "over_congested_segments", "over_open_segments", "edge_over_congested_count",
        "edge_over_open_count",
    ])
    payload = {
        "schema": "expanded_v7_recall_breakdown.v1",
        "generated_at": utc_now(),
        "generated_demand_recall": {
            "profile": demand_summary.get("profile", ""),
            "mean": demand_summary.get("mean_generated_recall", ""),
            "min": demand_summary.get("min_generated_recall", ""),
            "max": demand_summary.get("max_generated_recall", ""),
            "profile_summary_csv": demand_summary.get("demand_profile_summary_csv", rel(DEMAND_PROFILE_SUMMARY_CSV)),
            "source_assignment_summary_csv": demand_summary.get("source_assignment_summary_csv", rel(SOURCE_ASSIGNMENT_SUMMARY_CSV)),
        },
        "realized_demand_recall": summary.get("demand", {}),
        "congestion_recall": {
            "speed": summary.get("speed", {}),
            "edge_speed": summary.get("edge_speed", {}),
            "directional_csv": rel(csv_path),
            "directional": rows,
        },
        "edge_level_lane_recall": edge_lane_summary,
        "sideflow": sideflow,
        "note": "Generated route XML demand, realized edgeData demand, and congestion speed recall are intentionally separated.",
    }
    json_path = output_dir / "expanded_v7_recall_breakdown.json"
    write_json(json_path, payload)
    return {"json": rel(json_path), "csv": rel(csv_path)}


def classify_segment_flow_state(reference_speed_kmh: float, simulated_speed_kmh: float) -> tuple[str, str]:
    if simulated_speed_kmh <= 0:
        return "missing_observation", "No usable simulated speed was observed."
    if simulated_speed_kmh < FLOW_STOP_SPEED_KMH:
        return "stop_flow", f"Near-gridlock speed below {FLOW_STOP_SPEED_KMH:g}km/h."
    if simulated_speed_kmh > FLOW_FREE_SPEED_KMH:
        return "free_flow", f"Speed exceeds the hard {FLOW_FREE_SPEED_KMH:g}km/h free-flow threshold."
    if abs(simulated_speed_kmh - reference_speed_kmh) <= FLOW_TARGET_TOLERANCE_KMH:
        return "target_like_congestion", "Within the V7 practical +/-8km/h congestion tolerance."
    if simulated_speed_kmh < reference_speed_kmh:
        return "slow_congestion", "Still congested, but slower than the CSV reference."
    return "acceptable_over_open", "Open relative to the CSV reference, but not free-flow under the 35km/h gate."


def flow_state_counts(rows: list[dict[str, Any]], state_key: str = "flow_state") -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get(state_key, ""))
        counts[state] = counts.get(state, 0) + 1
    return counts


def flow_gate_summary(rows: list[dict[str, Any]], state_key: str = "flow_state") -> dict[str, Any]:
    counts = flow_state_counts(rows, state_key)
    stop_count = counts.get("stop_flow", 0) + counts.get("missing_observation", 0)
    free_count = counts.get("free_flow", 0)
    non_extreme_count = (
        counts.get("target_like_congestion", 0)
        + counts.get("slow_congestion", 0)
        + counts.get("acceptable_over_open", 0)
    )
    return {
        "status": "PASS" if rows and stop_count == 0 and free_count == 0 else "FAIL",
        "stop_or_missing_count": stop_count,
        "free_flow_count": free_count,
        "non_extreme_congestion_count": non_extreme_count,
        "flow_state_counts": counts,
    }


def weighted_grouped_speed_kmh(edge_rows: list[dict[str, str]]) -> tuple[float, list[dict[str, str]], list[dict[str, str]]]:
    usable = [
        row for row in edge_rows
        if safe_float(row.get("simulated_edge_speed_kmh")) > 0 and safe_float(row.get("edge_length_m")) > 0
    ]
    non_short = [row for row in usable if safe_float(row.get("edge_length_m")) >= SHORT_EDGE_WARN_LENGTH_M]
    used = non_short or usable
    excluded = [row for row in usable if row not in used]
    distance_weighted_m = 0.0
    time_weighted_s = 0.0
    for row in used:
        length = safe_float(row.get("edge_length_m"))
        speed_kmh = safe_float(row.get("simulated_edge_speed_kmh"))
        screenline = max(1.0, safe_float(row.get("screenline_count"), 1.0))
        distance_weighted_m += length * screenline
        time_weighted_s += (length / (speed_kmh / 3.6)) * screenline
    if distance_weighted_m <= 0 or time_weighted_s <= 0:
        return 0.0, used, excluded
    return (distance_weighted_m / time_weighted_s) * 3.6, used, excluded


def grouped_flow_plausibility_rows(speed_rows: list[dict[str, str]], edge_speed_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    by_segment_direction: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in edge_speed_rows:
        by_segment_direction.setdefault((row.get("segment_id", ""), row.get("direction", "")), []).append(row)
    grouped_rows: list[dict[str, Any]] = []
    for row in speed_rows:
        segment_id = row.get("segment_id", "")
        direction = row.get("direction", "")
        reference_speed = safe_float(row.get("reference_speed_kmh"))
        raw_speed = safe_float(row.get("simulated_speed_kmh"))
        raw_state, raw_reason = classify_segment_flow_state(reference_speed, raw_speed)
        grouped_speed, used_edges, excluded_edges = weighted_grouped_speed_kmh(by_segment_direction.get((segment_id, direction), []))
        if grouped_speed <= 0:
            grouped_speed = raw_speed
        grouped_state, grouped_reason = classify_segment_flow_state(reference_speed, grouped_speed)
        grouped_rows.append({
            "segment_id": segment_id,
            "direction": direction,
            "matched_edge_count": row.get("matched_edge_count", ""),
            "reference_speed_kmh": round(reference_speed, 6),
            "raw_simulated_speed_kmh": round(raw_speed, 6),
            "grouped_simulated_speed_kmh": round(grouped_speed, 6),
            "raw_speed_error_kmh": round(raw_speed - reference_speed, 6),
            "grouped_speed_error_kmh": round(grouped_speed - reference_speed, 6),
            "raw_flow_state": raw_state,
            "grouped_flow_state": grouped_state,
            "short_edge_excluded_count": len(excluded_edges),
            "grouped_edge_count": len(used_edges),
            "excluded_short_edge_ids": " ".join(row.get("edge_id", "") for row in excluded_edges),
            "reason": grouped_reason,
            "raw_reason": raw_reason,
            "mapping_policy": "short_edges_lt_10m_grouped_into_adjacent_segment_speed_gate",
        })
    return grouped_rows


def write_flow_plausibility_audit(summary: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    outputs = summary.get("outputs", {})
    speed_rows = read_csv(project_path(outputs.get("speed_csv", ""))) if outputs.get("speed_csv") else []
    edge_speed_rows = read_csv(project_path(outputs.get("edge_speed_csv", ""))) if outputs.get("edge_speed_csv") else []
    rows: list[dict[str, Any]] = []
    for row in speed_rows:
        reference_speed = safe_float(row.get("reference_speed_kmh"))
        simulated_speed = safe_float(row.get("simulated_speed_kmh"))
        state, reason = classify_segment_flow_state(reference_speed, simulated_speed)
        rows.append({
            "segment_id": row.get("segment_id", ""),
            "direction": row.get("direction", ""),
            "matched_edge_count": row.get("matched_edge_count", ""),
            "reference_speed_kmh": round(reference_speed, 6),
            "simulated_speed_kmh": round(simulated_speed, 6),
            "speed_error_kmh": round(simulated_speed - reference_speed, 6),
            "flow_state": state,
            "reason": reason,
            "mapping_policy": "segment_to_edge_approximation_allowed_when_simulation_plausible",
        })
    raw_gate = flow_gate_summary(rows)
    grouped_rows = grouped_flow_plausibility_rows(speed_rows, edge_speed_rows)
    grouped_gate = flow_gate_summary(grouped_rows, "grouped_flow_state")
    status = grouped_gate["status"]
    csv_path = output_dir / "expanded_v7_flow_plausibility_audit.csv"
    grouped_csv_path = output_dir / "expanded_v7_grouped_flow_plausibility_audit.csv"
    write_csv(csv_path, rows, [
        "segment_id", "direction", "matched_edge_count", "reference_speed_kmh",
        "simulated_speed_kmh", "speed_error_kmh", "flow_state", "reason", "mapping_policy",
    ])
    write_csv(grouped_csv_path, grouped_rows, [
        "segment_id", "direction", "matched_edge_count", "reference_speed_kmh",
        "raw_simulated_speed_kmh", "grouped_simulated_speed_kmh",
        "raw_speed_error_kmh", "grouped_speed_error_kmh",
        "raw_flow_state", "grouped_flow_state", "short_edge_excluded_count",
        "grouped_edge_count", "excluded_short_edge_ids", "reason", "raw_reason", "mapping_policy",
    ])
    payload = {
        "schema": "expanded_v7_flow_plausibility_audit.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(REFERENCE_CSV),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "run_id": summary.get("run_id", ""),
        "status": status,
        "primary_gate": "grouped_segment_speed",
        "stop_speed_threshold_kmh": FLOW_STOP_SPEED_KMH,
        "free_flow_threshold_kmh": FLOW_FREE_SPEED_KMH,
        "target_tolerance_kmh": FLOW_TARGET_TOLERANCE_KMH,
        "segment_direction_count": len(rows),
        "stop_or_missing_count": grouped_gate["stop_or_missing_count"],
        "free_flow_count": grouped_gate["free_flow_count"],
        "non_extreme_congestion_count": grouped_gate["non_extreme_congestion_count"],
        "flow_state_counts": grouped_gate["flow_state_counts"],
        "raw_status": raw_gate["status"],
        "raw_stop_or_missing_count": raw_gate["stop_or_missing_count"],
        "raw_free_flow_count": raw_gate["free_flow_count"],
        "raw_flow_state_counts": raw_gate["flow_state_counts"],
        "grouped_status": grouped_gate["status"],
        "grouped_stop_or_missing_count": grouped_gate["stop_or_missing_count"],
        "grouped_free_flow_count": grouped_gate["free_flow_count"],
        "grouped_flow_state_counts": grouped_gate["flow_state_counts"],
        "csv": rel(csv_path),
        "grouped_csv": rel(grouped_csv_path),
        "mapping_policy": "S1/S2 and other imperfect segment-edge matches may be aggregated if the simulated flow remains plausible and recalls the CSV congested speed.",
        "candidate_evidence": {
            "selected_net": summary.get("active_net", ""),
            "release_speedcap": read_json(RELEASE_SPEEDCAP_SUMMARY_JSON) if RELEASE_SPEEDCAP_SUMMARY_JSON.is_file() else {},
            "downbound_metering": read_json(DOWNBOUND_METERING_SUMMARY_JSON) if DOWNBOUND_METERING_SUMMARY_JSON.is_file() else {},
            "mainroad_speedcap": read_json(SPEEDCAP_SUMMARY_JSON) if SPEEDCAP_SUMMARY_JSON.is_file() else {},
            "overopen_metering": read_json(OVEROPEN_METERING_SUMMARY_JSON) if OVEROPEN_METERING_SUMMARY_JSON.is_file() else {},
            "route_edge_overopen_metering": read_json(ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON) if ROUTE_EDGE_OVEROPEN_METERING_SUMMARY_JSON.is_file() else {},
            "release_junction_fixed": read_json(RELEASE_JUNCTION_FIXED_SUMMARY_JSON) if RELEASE_JUNCTION_FIXED_SUMMARY_JSON.is_file() else {},
        },
        "recommendation": (
            "Current V7 is suitable for the firetruck bottleneck baseline if status is FAIL only outside the accepted route, "
            "but full corridor reality recall still needs narrower downstream/TLS/boundary calibration when stop/free-flow counts remain nonzero."
        ),
    }
    json_path = output_dir / "expanded_v7_flow_plausibility_audit.json"
    write_json(json_path, payload)
    return {"json": rel(json_path), "csv": rel(csv_path), "status": status}


def write_congestion_sweep_candidates(summary: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    profiles = ["bottleneck_aware_diversion", "balanced_diversion", "through_only"]
    boundaries = ["none", "mild_downbound_metering", "medium_downbound_metering"]
    tls_cases = ["no_tls", "mild_6to24_12to18", "aggressive_6to30_12to18"]
    rows = []
    index = 0
    for profile in profiles:
        for boundary in boundaries:
            for tls in tls_cases:
                index += 1
                rows.append({
                    "candidate_id": f"expanded_v7_calib_{index:02d}",
                    "demand_profile": profile,
                    "boundary": boundary,
                    "tls_case": tls,
                    "expected_effect": expected_congestion_candidate_effect(profile, boundary, tls),
                    "selection_priority": "no_emergency_teleport;route_error_0;realized_demand;directional_speed_mae;downbound_over_open;upbound_over_congested",
                    "report_only": True,
                })
    csv_path = output_dir / "expanded_v7_congestion_sweep_candidates.csv"
    write_csv(csv_path, rows, [
        "candidate_id", "demand_profile", "boundary", "tls_case",
        "expected_effect", "selection_priority", "report_only",
    ])
    payload = {
        "schema": "expanded_v7_congestion_sweep_candidates.v1",
        "generated_at": utc_now(),
        "current_speed_status": summary.get("speed_status", ""),
        "current_demand_status": summary.get("demand_status", ""),
        "candidate_csv": rel(csv_path),
        "candidate_count": len(rows),
        "note": "Report-only candidate grid. No extra calibrated net or demand files are generated in this validation goal.",
    }
    json_path = output_dir / "expanded_v7_congestion_sweep_candidates.json"
    write_json(json_path, payload)
    return {"json": rel(json_path), "csv": rel(csv_path)}


def expected_congestion_candidate_effect(profile: str, boundary: str, tls_case: str) -> str:
    effects = []
    if profile == "bottleneck_aware_diversion":
        effects.append("remove S9-S17 internal local source/sink pressure before TLS expansion")
    elif profile == "through_local_25":
        effects.append("spread insertion while preserving through demand")
    elif profile == "through_local_50":
        effects.append("maximize local segment realized demand; watch insertion caps")
    elif profile == "balanced_diversion":
        effects.append("keep CSV total demand recall while diverting part of flow off Toegye-ro to reduce upstream gridlock")
    else:
        effects.append("baseline through-flow concentration")
    if "downbound_metering" in boundary:
        effects.append("reduce downbound S15-S22 over-open speed by downstream holding")
    if tls_case == "mild_6to24_12to18":
        effects.append("small route green recovery without aggressive spillback shift")
    elif tls_case == "aggressive_6to30_12to18":
        effects.append("stress test larger route green; reject if teleport/queue shift returns")
    elif tls_case == "no_tls":
        effects.append("separate demand structure effect from signal timing")
    return "; ".join(effects)


def write_report_only_recommendations(summary: dict[str, Any], sideflow: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    recommendations = summary.get("recommendations", {}) or {}
    demand = summary.get("demand", {}) or {}
    speed = summary.get("speed", {}) or {}
    edge_speed = summary.get("edge_speed", {}) or {}
    rows = [
        {
            "recommendation_id": "demand_global_multiplier",
            "category": "demand",
            "status": summary.get("demand_status", ""),
            "recommendation": "Increase main-road demand only after removing insertion bottlenecks; current validation median recall is low.",
            "value": recommendations.get("recommended_global_multiplier", ""),
            "evidence": f"median_scaled_recall={demand.get('median_scaled_recall', '')}; geh_pass_warn_ratio={demand.get('geh_pass_warn_ratio', '')}",
            "report_only": True,
        },
        {
            "recommendation_id": "tls_boundary_calibration",
            "category": "tls_boundary",
            "status": summary.get("speed_status", ""),
            "recommendation": "Test static TLS split/offset and downstream boundary metering before increasing demand aggressively.",
            "value": "",
            "evidence": f"speed_mae_kmh={speed.get('speed_mae_kmh', '')}; edge_speed_mae_kmh={edge_speed.get('edge_speed_mae_kmh', '')}; over_open_edge_count={edge_speed.get('over_open_edge_count', '')}",
            "report_only": True,
        },
        {
            "recommendation_id": "sideflow_distribution_review",
            "category": "sideflow",
            "status": "INFO",
            "recommendation": "Keep side-flow as a small branch demand and review source/sink distribution before calibrated reruns.",
            "value": sideflow.get("sideflow_vehicle_count", ""),
            "evidence": f"source_edge_count={sideflow.get('source_edge_count', '')}; sink_edge_count={sideflow.get('sink_edge_count', '')}",
            "report_only": True,
        },
    ]
    csv_path = output_dir / "expanded_v7_report_only_recommendations.csv"
    json_path = output_dir / "expanded_v7_report_only_recommendations.json"
    fields = ["recommendation_id", "category", "status", "recommendation", "value", "evidence", "report_only"]
    write_csv(csv_path, rows, fields)
    payload = {
        "schema": "expanded_v7_report_only_recommendations.v1",
        "generated_at": utc_now(),
        "overall_status": summary.get("overall_status", ""),
        "recommendations_csv": rel(csv_path),
        "recommendations": rows,
        "note": "Report only. No calibrated net, demand, TLS, or boundary files are generated by expanded-v7 validation.",
    }
    write_json(json_path, payload)
    return {"json": rel(json_path), "csv": rel(csv_path)}


def summarize_sideflow() -> dict[str, Any]:
    rows = read_csv(SIDEFLOW_SUMMARY_CSV) if SIDEFLOW_SUMMARY_CSV.is_file() else []
    return {
        "schema": "expanded_v7_sideflow_summary.v1",
        "sideflow_vehicle_count": len(rows),
        "source_edge_count": len({row.get("source_edge", "") for row in rows if row.get("source_edge")}),
        "sink_edge_count": len({row.get("sink_edge", "") for row in rows if row.get("sink_edge")}),
        "sideflow_summary_csv": rel(SIDEFLOW_SUMMARY_CSV) if SIDEFLOW_SUMMARY_CSV.is_file() else "",
    }


def latest_b0_results_row(results_csv: str = "auto", output_prefix: str = RUN_PREFIX) -> dict[str, str]:
    if results_csv == "auto":
        latest_path = METRICS_ROOT / output_prefix / "latest.json"
        if not latest_path.is_file():
            raise ExpandedV7Error(f"missing_latest_b0:{rel(latest_path)}")
        latest = read_json(latest_path)
        results_csv = latest.get("results_csv", "")
    csv_path = project_path(results_csv)
    if not csv_path.is_file():
        raise ExpandedV7Error(f"missing_results_csv:{rel(csv_path)}")
    rows = [
        row for row in read_csv(csv_path)
        if row.get("mode") == "B0" and row.get("parameter_id") == "no_control"
    ]
    if not rows:
        raise ExpandedV7Error(f"missing_b0_no_control_row:{rel(csv_path)}")
    return rows[-1]


def read_fcd_lonlat(vehicle: ET.Element) -> tuple[float, float] | None:
    lon = vehicle.get("lon") or vehicle.get("x")
    lat = vehicle.get("lat") or vehicle.get("y")
    if lon is None or lat is None:
        return None
    try:
        return float(lat), float(lon)
    except ValueError:
        return None


def same_lane_near_conflict_audit(
    results_csv: str = "auto",
    output_prefix: str = CONSERVATIVE_RUN_PREFIX,
    threshold_m: float = 4.0,
) -> dict[str, Any]:
    row = latest_b0_results_row(results_csv, output_prefix=output_prefix)
    run_dir = project_path(row.get("run_dir", ""))
    run_id = row.get("run_id") or run_dir.name or "unknown_run"
    fcd_xml = run_dir / "fcd.xml"
    if not fcd_xml.is_file():
        raise ExpandedV7Error(f"missing_fcd_for_conflict_audit:{rel(fcd_xml)}")
    output_dir = METRICS_ROOT / CONSERVATIVE_CONFLICT_PREFIX / run_id
    rows: list[dict[str, Any]] = []
    min_distance = float("inf")
    checked_timesteps = 0
    for _event, elem in ET.iterparse(fcd_xml, events=("end",)):
        if elem.tag != "timestep":
            continue
        time_value = safe_float(elem.get("time"))
        vehicles = elem.findall("vehicle")
        emergency = next((vehicle for vehicle in vehicles if vehicle.get("id", "").startswith("emergency_")), None)
        if emergency is None:
            elem.clear()
            continue
        checked_timesteps += 1
        em_coords = read_fcd_lonlat(emergency)
        em_lane = emergency.get("lane", "")
        if em_coords is None or not em_lane:
            elem.clear()
            continue
        for vehicle in vehicles:
            vehicle_id = vehicle.get("id", "")
            if vehicle is emergency or vehicle_id.startswith("emergency_"):
                continue
            if vehicle.get("lane", "") != em_lane:
                continue
            coords = read_fcd_lonlat(vehicle)
            if coords is None:
                continue
            distance = haversine_distance_m(em_coords[0], em_coords[1], coords[0], coords[1])
            min_distance = min(min_distance, distance)
            if distance <= threshold_m:
                rows.append({
                    "time_sec": round(time_value, 2),
                    "emergency_id": emergency.get("id", ""),
                    "vehicle_id": vehicle_id,
                    "lane_id": em_lane,
                    "edge_id": lane_id_to_edge_id(em_lane),
                    "distance_m": round(distance, 3),
                    "emergency_speed_kmh": round(safe_float(emergency.get("speed")) * 3.6, 3),
                    "vehicle_speed_kmh": round(safe_float(vehicle.get("speed")) * 3.6, 3),
                    "emergency_pos": emergency.get("pos", ""),
                    "vehicle_pos": vehicle.get("pos", ""),
                })
        elem.clear()
    csv_path = output_dir / "same_lane_near_conflicts.csv"
    json_path = output_dir / "same_lane_near_conflict_summary.json"
    write_csv(csv_path, rows, [
        "time_sec", "emergency_id", "vehicle_id", "lane_id", "edge_id", "distance_m",
        "emergency_speed_kmh", "vehicle_speed_kmh", "emergency_pos", "vehicle_pos",
    ])
    payload = {
        "schema": "expanded_v7_same_lane_near_conflict_audit.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "results_csv": rel(project_path(results_csv)) if results_csv != "auto" else rel(project_path(read_json(METRICS_ROOT / output_prefix / "latest.json").get("results_csv", ""))),
        "run_dir": rel(run_dir),
        "fcd_xml": rel(fcd_xml),
        "threshold_m": threshold_m,
        "checked_timestep_count": checked_timesteps,
        "near_conflict_count": len(rows),
        "min_same_lane_distance_m": None if min_distance == float("inf") else round(min_distance, 3),
        "status": "PASS" if len(rows) == 0 else "WARN",
        "csv": rel(csv_path),
        "summary_json": rel(json_path),
        "interpretation_ko": "충돌 로그가 0이어도, 같은 lane에서 소방차와 일반차가 지나치게 가까우면 시각적으로 '치고 가는' 느낌이 날 수 있어 별도 점검합니다.",
    }
    write_json(json_path, payload)
    write_json(METRICS_ROOT / CONSERVATIVE_CONFLICT_PREFIX / "latest.json", {
        "summary_json": rel(json_path),
        "csv": rel(csv_path),
        "generated_at": payload["generated_at"],
        "run_id": run_id,
    })
    return payload


def latest_make_sense_results_csv(results_csv: str = "auto") -> str:
    if results_csv != "auto":
        return results_csv
    for prefix in [PLAUSIBILITY_FIRST_PREFIX, RUN_PREFIX]:
        latest_path = METRICS_ROOT / prefix / "latest.json"
        if latest_path.is_file():
            latest = read_json(latest_path)
            candidate = latest.get("results_csv", "")
            if candidate and project_path(candidate).is_file():
                return candidate
    return "auto"


def parse_edge_data_for_edges(edge_data_xml: Path, wanted_edges: set[str]) -> dict[str, dict[str, float]]:
    metrics = {edge_id: {"entered": 0.0, "left": 0.0, "speed_weighted_sum": 0.0, "speed_weight": 0.0, "waitingTime": 0.0, "sampledSeconds": 0.0} for edge_id in wanted_edges}
    if not edge_data_xml.is_file():
        return metrics
    for _event, elem in ET.iterparse(edge_data_xml, events=("end",)):
        if elem.tag == "edge":
            edge_id = elem.get("id", "")
            if edge_id in metrics:
                entered = safe_float(elem.get("entered"))
                left = safe_float(elem.get("left"))
                sampled = safe_float(elem.get("sampledSeconds"))
                speed = safe_float(elem.get("speed"))
                weight = sampled if sampled > 0 else max(entered, left, 1.0)
                metrics[edge_id]["entered"] += entered
                metrics[edge_id]["left"] += left
                metrics[edge_id]["speed_weighted_sum"] += speed * 3.6 * weight
                metrics[edge_id]["speed_weight"] += weight
                metrics[edge_id]["waitingTime"] += safe_float(elem.get("waitingTime"))
                metrics[edge_id]["sampledSeconds"] += sampled
            elem.clear()
    for edge_id, row in metrics.items():
        weight = row.pop("speed_weight", 0.0)
        weighted_sum = row.pop("speed_weighted_sum", 0.0)
        row["observed_count"] = max(row["entered"], row["left"])
        row["speed_kmh"] = weighted_sum / weight if weight else 0.0
    return metrics


def demand_route_load_by_edge(route_xml: Path, wanted_edges: set[str]) -> dict[str, dict[str, Any]]:
    load = {edge_id: {"generated_route_vehicle_count": 0, "flow_type_counts": {}, "source_internal_count": 0, "sink_internal_count": 0} for edge_id in wanted_edges}
    if not route_xml.is_file():
        return load
    for _event, elem in ET.iterparse(route_xml, events=("end",)):
        if elem.tag == "vehicle":
            vehicle_id = elem.get("id", "")
            route = elem.find("route")
            route_edges = (route.get("edges", "").split() if route is not None else [])
            if not route_edges:
                elem.clear()
                continue
            if "local_upbound" in vehicle_id:
                flow_type = "local_upbound"
            elif "local_downbound" in vehicle_id:
                flow_type = "local_downbound"
            elif "_sideflow_" in vehicle_id:
                flow_type = "sideflow"
            elif "_mapwide_" in vehicle_id:
                flow_type = "mapwide"
            elif "_up_" in vehicle_id or "_upbound_" in vehicle_id:
                flow_type = "through_up"
            elif "_down_" in vehicle_id or "_downbound_" in vehicle_id:
                flow_type = "through_down"
            else:
                flow_type = "other"
            starts_internal = bottleneck_edge(route_edges[0])
            sinks_internal = bottleneck_edge(route_edges[-1])
            for edge_id in set(route_edges) & wanted_edges:
                row = load[edge_id]
                row["generated_route_vehicle_count"] += 1
                row["flow_type_counts"][flow_type] = row["flow_type_counts"].get(flow_type, 0) + 1
                if starts_internal:
                    row["source_internal_count"] += 1
                if sinks_internal:
                    row["sink_internal_count"] += 1
            elem.clear()
    return load


def vehicle_flow_category(vehicle_id: str) -> tuple[str, str]:
    if "local_upbound" in vehicle_id:
        return "local_upbound", "upbound"
    if "local_downbound" in vehicle_id:
        return "local_downbound", "downbound"
    if "_sideflow_" in vehicle_id:
        return "sideflow", "mixed"
    if "_mapwide_" in vehicle_id:
        return "mapwide", "mixed"
    if "_up_" in vehicle_id or "_upbound_" in vehicle_id:
        return "through_up", "upbound"
    if "_down_" in vehicle_id or "_downbound_" in vehicle_id:
        return "through_down", "downbound"
    return "other", "mixed"


def write_route_contamination_report(route_xml: Path, output_dir: Path) -> tuple[list[dict[str, Any]], Path, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    checked = 0
    if route_xml.is_file():
        for _event, elem in ET.iterparse(route_xml, events=("end",)):
            if elem.tag == "vehicle":
                checked += 1
                vehicle_id = elem.get("id", "")
                flow_type, direction = vehicle_flow_category(vehicle_id)
                route = elem.find("route")
                route_edges = (route.get("edges", "").split() if route is not None else [])
                guard_type = "through" if flow_type == "through_up" else flow_type
                reason = route_guard_reason(guard_type, direction, route_edges, strict=True)
                forbidden = [edge_id for edge_id in route_edges if bottleneck_edge(edge_id)]
                if reason:
                    rows.append({
                        "vehicle_id": vehicle_id,
                        "flow_type": flow_type,
                        "direction": direction,
                        "start_edge": route_edges[0] if route_edges else "",
                        "target_edge": route_edges[-1] if route_edges else "",
                        "forbidden_bottleneck_edges": " ".join(dict.fromkeys(forbidden)),
                        "forbidden_edge_count": len(set(forbidden)),
                        "route_edge_count": len(route_edges),
                        "route_guard_reason": reason,
                    })
                elem.clear()
    csv_path = output_dir / "route_direction_contamination.csv"
    write_csv(csv_path, rows, [
        "vehicle_id", "flow_type", "direction", "start_edge", "target_edge",
        "forbidden_bottleneck_edges", "forbidden_edge_count", "route_edge_count", "route_guard_reason",
    ])
    summary = {
        "checked_vehicle_count": checked,
        "contaminated_vehicle_count": len(rows),
        "contaminated_non_through_count": sum(1 for row in rows if row["flow_type"] != "through_up"),
        "csv": rel(csv_path),
    }
    return rows, csv_path, summary


def lane_id_to_edge_id(lane_id: str) -> str:
    if not lane_id:
        return ""
    return lane_id.rsplit("_", 1)[0] if "_" in lane_id else lane_id


def parse_teleport_source_report(stderr_log: Path, output_dir: Path) -> tuple[list[dict[str, Any]], Path, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if stderr_log.is_file():
        for line in stderr_log.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "Teleporting vehicle" not in line:
                continue
            vehicle_id = ""
            lane_id = ""
            wait_reason = ""
            time_sec = ""
            if "vehicle '" in line:
                vehicle_id = line.split("vehicle '", 1)[1].split("'", 1)[0]
            if "lane='" in line:
                lane_id = line.split("lane='", 1)[1].split("'", 1)[0]
            if "waited too long (" in line:
                wait_reason = line.split("waited too long (", 1)[1].split(")", 1)[0]
            if "time=" in line:
                time_sec = line.split("time=", 1)[1].split(",", 1)[0].strip()
            flow_type, direction = vehicle_flow_category(vehicle_id)
            edge_id = lane_id_to_edge_id(lane_id)
            rows.append({
                "vehicle_id": vehicle_id,
                "flow_type": flow_type,
                "direction": direction,
                "wait_reason": wait_reason,
                "lane_id": lane_id,
                "edge_id": edge_id,
                "is_bottleneck_edge": bottleneck_edge(edge_id),
                "is_blocked_source_edge": blocked_bottleneck_source(edge_id),
                "time_sec": time_sec,
                "raw_log": line[-500:],
            })
    csv_path = output_dir / "yield_teleport_source_edges.csv"
    write_csv(csv_path, rows, [
        "vehicle_id", "flow_type", "direction", "wait_reason", "lane_id", "edge_id",
        "is_bottleneck_edge", "is_blocked_source_edge", "time_sec", "raw_log",
    ])
    by_edge: dict[str, int] = {}
    for row in rows:
        edge_id = str(row.get("edge_id") or "")
        by_edge[edge_id] = by_edge.get(edge_id, 0) + 1
    summary = {
        "teleport_event_count": len(rows),
        "bottleneck_teleport_event_count": sum(1 for row in rows if row.get("is_bottleneck_edge") is True),
        "top_teleport_edges": sorted(by_edge.items(), key=lambda item: (-item[1], item[0]))[:10],
        "csv": rel(csv_path),
    }
    return rows, csv_path, summary


def write_short_edge_artifact_report(sumo_net: Any, edge_ids: list[str], output_dir: Path) -> tuple[list[dict[str, Any]], Path, dict[str, Any]]:
    rows = []
    for edge_id in edge_ids:
        length = edge_length_m(sumo_net, edge_id)
        level = short_edge_artifact_level(sumo_net, edge_id)
        rows.append({
            "edge_id": edge_id,
            "edge_length_m": round(length, 6),
            "short_edge_artifact": bool(level),
            "artifact_level": level,
            "speed_gate_use": "exclude_or_aggregate" if level else "use",
        })
    csv_path = output_dir / "short_edge_artifacts.csv"
    write_csv(csv_path, rows, [
        "edge_id", "edge_length_m", "short_edge_artifact", "artifact_level", "speed_gate_use",
    ])
    summary = {
        "short_edge_count": sum(1 for row in rows if row["short_edge_artifact"] is True),
        "artifact_lt_5m_count": sum(1 for row in rows if row["artifact_level"] == "artifact_lt_5m"),
        "warn_lt_10m_count": sum(1 for row in rows if row["artifact_level"] == "warn_lt_10m"),
        "csv": rel(csv_path),
    }
    return rows, csv_path, summary


def accepted_route_edges() -> list[str]:
    if not ACCEPTED_ROUTES_CSV.is_file():
        return []
    rows = read_csv(ACCEPTED_ROUTES_CSV)
    if not rows:
        return []
    return rows[0].get("route_edges", "").split()


def latest_grouped_flow_states() -> dict[tuple[str, str], dict[str, str]]:
    latest_path = METRICS_ROOT / VALIDATION_PREFIX / "latest.json"
    if not latest_path.is_file():
        return {}
    latest = read_json(latest_path)
    summary_json = project_path(latest.get("summary_json", ""))
    if not summary_json.is_file():
        return {}
    audit_csv = summary_json.parent / "expanded_v7_grouped_flow_plausibility_audit.csv"
    if not audit_csv.is_file():
        return {}
    states: dict[tuple[str, str], dict[str, str]] = {}
    for row in read_csv(audit_csv):
        states[(row.get("segment_id", ""), row.get("direction", ""))] = row
    return states


def segment_edge_memberships(mapping_csv: Path = MAPPING_CSV) -> dict[str, list[dict[str, str]]]:
    memberships: dict[str, list[dict[str, str]]] = {}
    states = latest_grouped_flow_states()
    for row in read_csv(mapping_csv):
        edge_id = row.get("edge_id", "")
        if not edge_id:
            continue
        state = states.get((row.get("segment_id", ""), row.get("direction", "")), {})
        memberships.setdefault(edge_id, []).append({
            "segment_id": row.get("segment_id", ""),
            "direction": row.get("direction", ""),
            "edge_order": row.get("edge_order", ""),
            "target_lanes": row.get("target_lanes", ""),
            "flow_state": state.get("grouped_flow_state", ""),
            "grouped_speed_kmh": state.get("grouped_simulated_speed_kmh", ""),
        })
    return memberships


def write_road_integrity_html(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]], connection_rows: list[dict[str, Any]]) -> None:
    issue_rows = [row for row in rows if row.get("issue_count", 0)]
    edge_table = "\n".join(
        "<tr>"
        f"<td>{esc(row.get('edge_id'))}</td><td>{esc(row.get('roles'))}</td>"
        f"<td>{esc(row.get('lane_count'))}</td><td>{fmt(row.get('edge_length_m'), 1)}</td>"
        f"<td>{fmt(row.get('speed_limit_kmh'), 1)}</td><td>{esc(row.get('segment_memberships'))}</td>"
        f"<td>{esc(row.get('flow_states'))}</td><td>{esc(row.get('issues'))}</td>"
        "</tr>"
        for row in issue_rows[:160]
    )
    connection_table = "\n".join(
        "<tr>"
        f"<td>{esc(row.get('from_edge'))}</td><td>{esc(row.get('to_edge'))}</td>"
        f"<td>{esc(row.get('from_lanes'))}->{esc(row.get('to_lanes'))}</td>"
        f"<td>{esc(row.get('transition_type'))}</td><td>{esc(row.get('is_protected_continuation'))}</td>"
        f"<td>{esc(row.get('issue'))}</td>"
        "</tr>"
        for row in connection_rows[:200]
    )
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Expanded V7 Road Integrity Audit</title>
  <style>
    body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin:24px; color:#172033; }}
    table {{ border-collapse:collapse; width:100%; font-size:13px; margin:12px 0 28px; }}
    th,td {{ border-bottom:1px solid #d7dde8; padding:7px; text-align:left; vertical-align:top; }}
    th {{ background:#eef2f7; }}
    .cards {{ display:grid; grid-template-columns:repeat(5,minmax(140px,1fr)); gap:12px; }}
    .card {{ border:1px solid #d7dde8; border-radius:6px; padding:12px; }}
    .num {{ font-size:24px; font-weight:700; }}
    code {{ background:#f5f7fb; padding:2px 4px; border-radius:4px; }}
  </style>
</head>
<body>
  <h1>Expanded V7 Road Integrity Audit</h1>
  <p>퇴계로 S1-S22 본선, 소방차 route, release/terminal edge의 차선 급감, 1차선 edge, 초단거리 edge, 100km/h 속도 제한을 점검합니다.</p>
  <p>Reference CSV: <code>{esc(summary.get('reference_csv_abs', ''))}</code></p>
  <div class="cards">
    <div class="card"><div>Protected edges</div><div class="num">{esc(summary.get('protected_edge_count'))}</div></div>
    <div class="card"><div>1-lane protected</div><div class="num">{esc(summary.get('protected_one_lane_edge_count'))}</div></div>
    <div class="card"><div>3→1 transitions</div><div class="num">{esc(summary.get('lane_drop_3_to_1_count'))}</div></div>
    <div class="card"><div>Short &lt;10m</div><div class="num">{esc(summary.get('protected_short_lt_10m_count'))}</div></div>
    <div class="card"><div>Speed &gt;50</div><div class="num">{esc(summary.get('protected_speed_gt_50_count'))}</div></div>
  </div>
  <h2>Issue Edges</h2>
  <table><thead><tr><th>Edge</th><th>Roles</th><th>Lanes</th><th>Length</th><th>Speed</th><th>Segments</th><th>Flow</th><th>Issues</th></tr></thead><tbody>{edge_table}</tbody></table>
  <h2>Lane-drop / Connection Issues</h2>
  <table><thead><tr><th>From</th><th>To</th><th>Lanes</th><th>Type</th><th>Protected continuation</th><th>Issue</th></tr></thead><tbody>{connection_table}</tbody></table>
  <p>CSV: <code>{esc(summary.get('edge_csv'))}</code>, <code>{esc(summary.get('connection_csv'))}</code></p>
</body>
</html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def road_integrity_audit(
    net_file: Path | None = None,
    mapping_csv: Path = MAPPING_CSV,
) -> dict[str, Any]:
    net_path = net_file or active_b0_net()
    sumo_net = read_sumo_net(net_path)
    output_dir = METRICS_ROOT / ROAD_INTEGRITY_PREFIX
    output_dir.mkdir(parents=True, exist_ok=True)
    mapping_edges = all_mapping_edge_ids(mapping_csv)
    route_edges = set(accepted_route_edges())
    release_edges = set(MAINLINE_RELEASE_EDGE_IDS)
    protected_edges = protected_mainline_edge_ids(mapping_csv)
    memberships = segment_edge_memberships(mapping_csv)
    protected_pairs = protected_continuation_pairs(sumo_net, mapping_csv)
    edge_rows: list[dict[str, Any]] = []
    connection_rows: list[dict[str, Any]] = []
    protected_one_lane_count = 0
    protected_short_count = 0
    protected_speed_gt_50_count = 0
    for edge_id in sorted(protected_edges):
        try:
            edge = sumo_net.getEdge(edge_id)
        except Exception:
            continue
        lane_count = int(edge.getLaneNumber())
        length_m = float(edge.getLength())
        speed_kmh = float(edge.getSpeed()) * 3.6
        roles = []
        if edge_id in mapping_edges:
            roles.append("mapping")
        if edge_id in route_edges:
            roles.append("firetruck_route")
        if edge_id in release_edges:
            roles.append("release_or_terminal")
        issues = []
        if lane_count <= 1:
            issues.append("protected_one_lane")
            protected_one_lane_count += 1
        if length_m < SHORT_EDGE_WARN_LENGTH_M:
            issues.append("short_lt_10m")
            protected_short_count += 1
        if speed_kmh > 50.1:
            issues.append("speed_gt_50kmh")
            protected_speed_gt_50_count += 1
        member_rows = memberships.get(edge_id, [])
        edge_rows.append({
            "edge_id": edge_id,
            "roles": " ".join(roles),
            "lane_count": lane_count,
            "edge_length_m": round(length_m, 6),
            "speed_limit_kmh": round(speed_kmh, 6),
            "edge_type": sumolib_edge_type(edge),
            "segment_memberships": " ".join(f"{row['segment_id']}:{row['direction']}" for row in member_rows),
            "flow_states": " ".join(sorted({row.get("flow_state", "") for row in member_rows if row.get("flow_state")})),
            "issues": ";".join(issues),
            "issue_count": len(issues),
        })
    lane_drop_3_to_1 = 0
    lane_drop_3_to_2 = 0
    lane_drop_2_to_1 = 0
    for from_edge, to_edge in sorted(protected_pairs):
        try:
            from_lanes = int(sumo_net.getEdge(from_edge).getLaneNumber())
            to_lanes = int(sumo_net.getEdge(to_edge).getLaneNumber())
        except Exception:
            continue
        transition = f"{from_lanes}->{to_lanes}"
        issue = ""
        if from_lanes >= 3 and to_lanes <= 1:
            lane_drop_3_to_1 += 1
            issue = "mainline_3_to_1_drop"
        elif from_lanes >= 3 and to_lanes == 2:
            lane_drop_3_to_2 += 1
            issue = "mainline_3_to_2_drop"
        elif from_lanes == 2 and to_lanes <= 1:
            lane_drop_2_to_1 += 1
            issue = "mainline_2_to_1_drop"
        if issue:
            connection_rows.append({
                "from_edge": from_edge,
                "to_edge": to_edge,
                "from_lanes": from_lanes,
                "to_lanes": to_lanes,
                "transition_type": transition,
                "is_protected_continuation": True,
                "issue": issue,
            })
    edge_csv = output_dir / "expanded_v7_road_integrity_edges.csv"
    connection_csv = output_dir / "expanded_v7_road_integrity_connections.csv"
    write_csv(edge_csv, edge_rows, [
        "edge_id", "roles", "lane_count", "edge_length_m", "speed_limit_kmh", "edge_type",
        "segment_memberships", "flow_states", "issues", "issue_count",
    ])
    write_csv(connection_csv, connection_rows, [
        "from_edge", "to_edge", "from_lanes", "to_lanes", "transition_type",
        "is_protected_continuation", "issue",
    ])
    summary = {
        "schema": "expanded_v7_road_integrity_audit.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(REFERENCE_CSV),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "net_file": rel(net_path),
        "protected_edge_count": len(edge_rows),
        "protected_one_lane_edge_count": protected_one_lane_count,
        "protected_short_lt_10m_count": protected_short_count,
        "protected_speed_gt_50_count": protected_speed_gt_50_count,
        "lane_drop_3_to_1_count": lane_drop_3_to_1,
        "lane_drop_3_to_2_count": lane_drop_3_to_2,
        "lane_drop_2_to_1_count": lane_drop_2_to_1,
        "connection_issue_count": len(connection_rows),
        "edge_csv": rel(edge_csv),
        "connection_csv": rel(connection_csv),
        "html": rel(ROAD_INTEGRITY_HTML),
        "status": "PASS" if protected_one_lane_count == 0 and lane_drop_3_to_1 == 0 else "WARN",
        "note": "Connectivity PASS only means routes are connected. This audit checks whether protected Toegye-ro/mainline edges have plausible lane continuity and speed limits.",
    }
    summary_json = output_dir / "expanded_v7_road_integrity_audit_summary.json"
    write_json(summary_json, summary)
    write_json(output_dir / "latest.json", {"summary_json": rel(summary_json), "generated_at": summary["generated_at"]})
    write_road_integrity_html(ROAD_INTEGRITY_HTML, summary, edge_rows, connection_rows)
    return {**summary, "summary_json": rel(summary_json)}


def parse_demand_routes(route_xml: Path) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int], dict[str, int]]:
    routes: list[dict[str, Any]] = []
    load_by_edge: dict[str, int] = {}
    source_by_edge: dict[str, int] = {}
    sink_by_edge: dict[str, int] = {}
    if not route_xml.is_file():
        return routes, load_by_edge, source_by_edge, sink_by_edge
    for _event, elem in ET.iterparse(route_xml, events=("end",)):
        if elem.tag == "vehicle":
            vehicle_id = elem.get("id", "")
            route = elem.find("route")
            edge_ids = route.get("edges", "").split() if route is not None else []
            if edge_ids:
                routes.append({"vehicle_id": vehicle_id, "edge_ids": edge_ids})
                source_by_edge[edge_ids[0]] = source_by_edge.get(edge_ids[0], 0) + 1
                sink_by_edge[edge_ids[-1]] = sink_by_edge.get(edge_ids[-1], 0) + 1
                for edge_id in set(edge_ids):
                    load_by_edge[edge_id] = load_by_edge.get(edge_id, 0) + 1
            elem.clear()
    return routes, load_by_edge, source_by_edge, sink_by_edge


def all_edge_data_metrics(edge_data_xml: Path) -> dict[str, dict[str, float]]:
    metrics: dict[str, dict[str, float]] = {}
    if not edge_data_xml.is_file():
        return metrics
    for _event, elem in ET.iterparse(edge_data_xml, events=("end",)):
        if elem.tag == "edge":
            edge_id = elem.get("id", "")
            if not edge_id:
                elem.clear()
                continue
            row = metrics.setdefault(edge_id, {
                "entered": 0.0,
                "left": 0.0,
                "speed_weighted_sum": 0.0,
                "speed_weight": 0.0,
                "waitingTime": 0.0,
                "sampledSeconds": 0.0,
            })
            entered = safe_float(elem.get("entered"))
            left = safe_float(elem.get("left"))
            sampled = safe_float(elem.get("sampledSeconds"))
            speed = safe_float(elem.get("speed"))
            weight = sampled if sampled > 0 else max(entered, left, 1.0)
            row["entered"] += entered
            row["left"] += left
            row["speed_weighted_sum"] += speed * 3.6 * weight
            row["speed_weight"] += weight
            row["waitingTime"] += safe_float(elem.get("waitingTime"))
            row["sampledSeconds"] += sampled
            elem.clear()
    for row in metrics.values():
        weight = row.pop("speed_weight", 0.0)
        weighted_sum = row.pop("speed_weighted_sum", 0.0)
        row["observed_count"] = max(row["entered"], row["left"])
        row["speed_kmh"] = weighted_sum / weight if weight else 0.0
    return metrics


def tls_green_seconds_by_link(net_file: Path) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    if not net_file.is_file():
        return result
    root = ET.parse(net_file).getroot()
    for tl_logic in root.findall("tlLogic"):
        tls_id = tl_logic.get("id", "")
        phases = tl_logic.findall("phase")
        if not phases:
            continue
        max_links = max((len(phase.get("state", "")) for phase in phases), default=0)
        for link_index in range(max_links):
            result[(tls_id, link_index)] = {
                "tls_id": tls_id,
                "link_index": link_index,
                "green_sec": phase_link_green_seconds(phases, link_index),
                "yellow_sec": phase_link_yellow_seconds(phases, link_index),
                "cycle_sec": int(sum(int(float(phase.get("duration", "0") or 0)) for phase in phases)),
            }
    return result


def outgoing_connection_rows(edge: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for conn_list in edge.getOutgoing().values():
        for conn in conn_list:
            to_edge = conn.getTo()
            rows.append({
                "to_edge": to_edge.getID() if to_edge is not None else "",
                "from_lane": conn.getFromLane().getIndex() if conn.getFromLane() is not None else "",
                "to_lane": conn.getToLane().getIndex() if conn.getToLane() is not None else "",
                "tls_id": conn.getTLSID() or "",
                "link_index": conn.getTLLinkIndex() if conn.getTLSID() else "",
                "state": conn.getState() or "",
            })
    return rows


def make_sense_edge_roles(edge_id: str, mapping_edges: set[str], route_edges: set[str], high_flow_edges: set[str]) -> list[str]:
    roles = []
    if edge_id in mapping_edges:
        roles.append("s1_s22_mainline")
    if edge_id in route_edges:
        roles.append("firetruck_route")
    if edge_id in MAINLINE_RELEASE_EDGE_IDS:
        roles.append("release_or_terminal")
    if edge_id in high_flow_edges:
        roles.append("high_flow")
    return roles


def classify_make_sense_edge(
    sumo_net: Any,
    edge_id: str,
    roles: list[str],
    load_count: int,
    source_count: int,
    sink_count: int,
    edge_metrics: dict[str, float],
    tls_by_link: dict[tuple[str, int], dict[str, Any]],
) -> tuple[list[str], str, list[dict[str, Any]]]:
    issues: list[str] = []
    try:
        edge = sumo_net.getEdge(edge_id)
    except Exception:
        return ["missing_edge"], "repair_network_or_drop_route", []
    lane_count = int(edge.getLaneNumber())
    length_m = float(edge.getLength())
    speed_kmh = float(edge.getSpeed()) * 3.6
    observed = safe_float(edge_metrics.get("observed_count"))
    simulated_speed = safe_float(edge_metrics.get("speed_kmh"))
    high_risk_role = bool({"s1_s22_mainline", "firetruck_route", "release_or_terminal", "high_flow"} & set(roles))
    if high_risk_role and lane_count <= 1:
        issues.append("high_risk_one_lane")
    if high_risk_role and speed_kmh > 70.1:
        issues.append("high_risk_speed_gt_70")
    if high_risk_role and length_m < SHORT_EDGE_ARTIFACT_LENGTH_M and load_count >= 50 and simulated_speed < FLOW_STOP_SPEED_KMH:
        issues.append("high_flow_stop_short_edge")
    outgoing = outgoing_connection_rows(edge)
    passenger_outgoing = [
        row for row in outgoing
        if row.get("to_edge") and not str(row.get("to_edge")).startswith(":")
    ]
    if high_risk_role and load_count >= 30 and not passenger_outgoing:
        issues.append("high_flow_no_passenger_outgoing")
    if high_risk_role and observed >= 30 and simulated_speed and simulated_speed < FLOW_STOP_SPEED_KMH and source_count == 0 and sink_count == 0:
        issues.append("effectively_impassable_pass_through")
    short_green_rows = []
    for row in outgoing:
        tls_id = str(row.get("tls_id") or "")
        link_index = row.get("link_index")
        if not tls_id or link_index in {"", None}:
            continue
        green = tls_by_link.get((tls_id, int(link_index)), {})
        green_sec = safe_float(green.get("green_sec"))
        if high_risk_role and load_count >= 30 and 0 < green_sec < 8:
            issues.append("high_flow_short_green_lt_8s")
            short_green_rows.append({
                "tls_id": tls_id,
                "link_index": int(link_index),
                "to_edge": row.get("to_edge", ""),
                "green_sec": green_sec,
                "cycle_sec": green.get("cycle_sec", ""),
            })
    recommendation = "no_repair_needed"
    if "missing_edge" in issues or "high_flow_no_passenger_outgoing" in issues:
        recommendation = "repair_connection_or_route"
    elif "high_risk_one_lane" in issues:
        recommendation = "raise_mainline_continuation_to_min_2_lanes"
    elif "effectively_impassable_pass_through" in issues or "high_flow_short_green_lt_8s" in issues:
        recommendation = "inspect_connection_priority_tls_before_demand_changes"
    elif "high_flow_stop_short_edge" in issues:
        recommendation = "aggregate_short_edge_for_speed_gate_and_check_adjacent_connection"
    return issues, recommendation, short_green_rows


def write_make_sense_html(path: Path, summary: dict[str, Any], edge_rows: list[dict[str, Any]], pair_rows: list[dict[str, Any]]) -> None:
    issue_rows = [row for row in edge_rows if row.get("issue_count", 0)]
    edge_table = "\n".join(
        "<tr>"
        f"<td>{esc(row.get('edge_id'))}</td><td>{esc(row.get('roles'))}</td>"
        f"<td>{esc(row.get('lane_count'))}</td><td>{fmt(row.get('edge_length_m'), 1)}</td>"
        f"<td>{fmt(row.get('generated_load_count'), 0)}</td><td>{fmt(row.get('observed_count'), 0)}</td>"
        f"<td>{fmt(row.get('speed_kmh'), 1)}</td><td>{esc(row.get('issues'))}</td>"
        f"<td>{esc(row.get('recommendation'))}</td>"
        "</tr>"
        for row in issue_rows[:220]
    )
    pair_table = "\n".join(
        "<tr>"
        f"<td>{esc(row.get('from_edge'))}</td><td>{esc(row.get('to_edge'))}</td>"
        f"<td>{esc(row.get('transition'))}</td><td>{esc(row.get('is_connected'))}</td>"
        f"<td>{esc(row.get('issue'))}</td><td>{esc(row.get('recommendation'))}</td>"
        "</tr>"
        for row in pair_rows[:220]
    )
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Expanded V7 Make-Sense Network Audit</title>
  <style>
    body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin:24px; color:#172033; }}
    table {{ border-collapse:collapse; width:100%; font-size:13px; margin:12px 0 28px; }}
    th,td {{ border-bottom:1px solid #d7dde8; padding:7px; text-align:left; vertical-align:top; }}
    th {{ background:#eef2f7; }}
    .cards {{ display:grid; grid-template-columns:repeat(5,minmax(140px,1fr)); gap:12px; }}
    .card {{ border:1px solid #d7dde8; border-radius:6px; padding:12px; }}
    .num {{ font-size:24px; font-weight:700; }}
    code {{ background:#f5f7fb; padding:2px 4px; border-radius:4px; }}
  </style>
</head>
<body>
  <h1>Expanded V7 Make-Sense Network Audit</h1>
  <p>전체 OSM을 미화하지 않고, 퇴계로 본선/소방차 route/high-flow edge에서 실험을 망치는 구조만 고위험으로 분류합니다.</p>
  <p>Reference CSV: <code>{esc(summary.get('reference_csv_abs'))}</code></p>
  <div class="cards">
    <div class="card"><div>Status</div><div class="num">{esc(summary.get('status'))}</div></div>
    <div class="card"><div>High-risk edges</div><div class="num">{esc(summary.get('high_risk_edge_count'))}</div></div>
    <div class="card"><div>Issue edges</div><div class="num">{esc(summary.get('issue_edge_count'))}</div></div>
    <div class="card"><div>3→1 drops</div><div class="num">{esc(summary.get('lane_drop_3_to_1_count'))}</div></div>
    <div class="card"><div>Impassable</div><div class="num">{esc(summary.get('effectively_impassable_count'))}</div></div>
  </div>
  <h2>판단</h2>
  <p>{esc(summary.get('interpretation_ko'))}</p>
  <h2>High-risk Issue Edges</h2>
  <table><thead><tr><th>Edge</th><th>Roles</th><th>Lanes</th><th>Length</th><th>Generated</th><th>Observed</th><th>Speed</th><th>Issues</th><th>Recommendation</th></tr></thead><tbody>{edge_table}</tbody></table>
  <h2>Route / Mainline Pair Issues</h2>
  <table><thead><tr><th>From</th><th>To</th><th>Lanes</th><th>Connected</th><th>Issue</th><th>Recommendation</th></tr></thead><tbody>{pair_table}</tbody></table>
  <p>Outputs: <code>{esc(summary.get('edge_csv'))}</code>, <code>{esc(summary.get('pair_csv'))}</code>, <code>{esc(summary.get('summary_json'))}</code></p>
</body>
</html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def make_sense_network_audit(results_csv: str = "auto", net_file: Path | None = None, demand_xml: Path = DEMAND_XML) -> dict[str, Any]:
    net_path = net_file or active_b0_net()
    resolved_results_csv = latest_make_sense_results_csv(results_csv)
    row = latest_b0_results_row(resolved_results_csv)
    run_dir = project_path(row.get("run_dir", ""))
    run_id = row.get("run_id") or run_dir.name or "unknown_run"
    output_dir = METRICS_ROOT / MAKE_SENSE_PREFIX / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    sumo_net = read_sumo_net(net_path)
    mapping_edges = all_mapping_edge_ids(MAPPING_CSV)
    route_edges = set(accepted_route_edges())
    protected_pairs = protected_continuation_pairs(sumo_net, MAPPING_CSV)
    routes, load_by_edge, source_by_edge, sink_by_edge = parse_demand_routes(demand_xml)
    high_flow_edges = {edge_id for edge_id, count in load_by_edge.items() if count >= 50}
    protected_edges = protected_mainline_edge_ids(MAPPING_CSV)
    edge_metrics = all_edge_data_metrics(run_dir / "edgeData.xml")
    tls_by_link = tls_green_seconds_by_link(net_path)

    checked_edges = sorted(protected_edges | high_flow_edges | set(MAINLINE_RELEASE_EDGE_IDS))
    edge_rows: list[dict[str, Any]] = []
    short_green_rows: list[dict[str, Any]] = []
    for edge_id in checked_edges:
        roles = make_sense_edge_roles(edge_id, mapping_edges, route_edges, high_flow_edges)
        if not roles:
            continue
        metrics = edge_metrics.get(edge_id, {})
        issues, recommendation, green_rows = classify_make_sense_edge(
            sumo_net,
            edge_id,
            roles,
            load_by_edge.get(edge_id, 0),
            source_by_edge.get(edge_id, 0),
            sink_by_edge.get(edge_id, 0),
            metrics,
            tls_by_link,
        )
        for green_row in green_rows:
            short_green_rows.append({"edge_id": edge_id, **green_row})
        try:
            edge = sumo_net.getEdge(edge_id)
            lane_count = int(edge.getLaneNumber())
            length_m = float(edge.getLength())
            speed_limit = float(edge.getSpeed()) * 3.6
            outgoing_count = len([target for target in edge.getOutgoing() if not target.getID().startswith(":")])
        except Exception:
            lane_count = 0
            length_m = 0.0
            speed_limit = 0.0
            outgoing_count = 0
        edge_rows.append({
            "edge_id": edge_id,
            "roles": " ".join(roles),
            "lane_count": lane_count,
            "edge_length_m": round(length_m, 6),
            "speed_limit_kmh": round(speed_limit, 6),
            "generated_load_count": load_by_edge.get(edge_id, 0),
            "source_vehicle_count": source_by_edge.get(edge_id, 0),
            "sink_vehicle_count": sink_by_edge.get(edge_id, 0),
            "observed_count": round(safe_float(metrics.get("observed_count")), 6),
            "speed_kmh": round(safe_float(metrics.get("speed_kmh")), 6),
            "waitingTime": round(safe_float(metrics.get("waitingTime")), 6),
            "outgoing_passenger_edge_count": outgoing_count,
            "issues": ";".join(issues),
            "issue_count": len(issues),
            "recommendation": recommendation,
        })

    pair_rows: list[dict[str, Any]] = []
    for from_edge, to_edge in sorted(protected_pairs):
        from_lanes = sumolib_edge_lane_count(sumo_net, from_edge)
        to_lanes = sumolib_edge_lane_count(sumo_net, to_edge)
        connected = route_connected(sumo_net, [from_edge, to_edge])
        issue = ""
        recommendation = "no_repair_needed"
        if not connected:
            issue = "disconnected_protected_pair"
            recommendation = "repair_connection_or_route_mapping"
        elif from_lanes >= 3 and to_lanes <= 1:
            issue = "high_risk_3_to_1_lane_drop"
            recommendation = "raise_continuation_to_min_2_lanes"
        elif from_lanes == 2 and to_lanes <= 1:
            issue = "high_risk_2_to_1_lane_drop"
            recommendation = "raise_continuation_to_min_2_lanes"
        pair_rows.append({
            "from_edge": from_edge,
            "to_edge": to_edge,
            "from_lanes": from_lanes,
            "to_lanes": to_lanes,
            "transition": f"{from_lanes}->{to_lanes}",
            "is_connected": connected,
            "issue": issue,
            "recommendation": recommendation,
        })

    edge_csv = output_dir / "expanded_v7_make_sense_edges.csv"
    pair_csv = output_dir / "expanded_v7_make_sense_pairs.csv"
    short_green_csv = output_dir / "expanded_v7_make_sense_short_green_links.csv"
    write_csv(edge_csv, edge_rows, [
        "edge_id", "roles", "lane_count", "edge_length_m", "speed_limit_kmh",
        "generated_load_count", "source_vehicle_count", "sink_vehicle_count",
        "observed_count", "speed_kmh", "waitingTime", "outgoing_passenger_edge_count",
        "issues", "issue_count", "recommendation",
    ])
    write_csv(pair_csv, pair_rows, [
        "from_edge", "to_edge", "from_lanes", "to_lanes", "transition",
        "is_connected", "issue", "recommendation",
    ])
    write_csv(short_green_csv, short_green_rows, ["edge_id", "tls_id", "link_index", "to_edge", "green_sec", "cycle_sec"])
    issue_rows = [item for item in edge_rows if safe_int(item.get("issue_count")) > 0]
    lane_drop_3_to_1 = sum(1 for item in pair_rows if item["issue"] == "high_risk_3_to_1_lane_drop")
    lane_drop_2_to_1 = sum(1 for item in pair_rows if item["issue"] == "high_risk_2_to_1_lane_drop")
    disconnected_pairs = sum(1 for item in pair_rows if item["issue"] == "disconnected_protected_pair")
    effectively_impassable = sum(1 for item in edge_rows if "effectively_impassable_pass_through" in item["issues"])
    structural_defects = (
        lane_drop_3_to_1
        + lane_drop_2_to_1
        + disconnected_pairs
        + sum(1 for item in edge_rows if "missing_edge" in item["issues"] or "high_flow_no_passenger_outgoing" in item["issues"] or "high_risk_one_lane" in item["issues"])
    )
    status = "PASS" if structural_defects == 0 else "WARN"
    interpretation = (
        "현재 발견된 저속 edge가 모두 도로망 결함이라는 뜻은 아닙니다. "
        "source/sink가 아닌 pass-through edge에서 저속이 발생하면 connection/TLS/boundary를 우선 점검하고, "
        "3→1 급감·끊김·고위험 1차선이 있을 때만 net 보정 대상으로 봅니다."
    )
    if structural_defects == 0 and effectively_impassable:
        interpretation += " 이번 audit에서는 물리적 끊김보다 통과 가능한 저속 병목이 남아 있어 TLS/boundary calibration 후보로 분류합니다."
    summary_json = output_dir / "expanded_v7_make_sense_audit_summary.json"
    summary = {
        "schema": "expanded_v7_make_sense_network_audit.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(REFERENCE_CSV),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "run_id": run_id,
        "results_csv": rel(project_path(resolved_results_csv)) if resolved_results_csv != "auto" else rel(project_path(read_json(METRICS_ROOT / RUN_PREFIX / "latest.json").get("results_csv", ""))),
        "run_dir": rel(run_dir),
        "active_net": rel(net_path),
        "demand_xml": rel(demand_xml),
        "demand_route_count": len(routes),
        "checked_edge_count": len(edge_rows),
        "high_risk_edge_count": len([row for row in edge_rows if row.get("roles")]),
        "issue_edge_count": len(issue_rows),
        "structural_defect_count": structural_defects,
        "effectively_impassable_count": effectively_impassable,
        "short_green_issue_count": len(short_green_rows),
        "lane_drop_3_to_1_count": lane_drop_3_to_1,
        "lane_drop_2_to_1_count": lane_drop_2_to_1,
        "disconnected_pair_count": disconnected_pairs,
        "edge_csv": rel(edge_csv),
        "pair_csv": rel(pair_csv),
        "short_green_csv": rel(short_green_csv),
        "html": rel(MAKE_SENSE_HTML),
        "summary_json": rel(summary_json),
        "status": status,
        "candidate_policy": "do_not_generate_new_net_when_only_congestion_or_tls_boundary_calibration_remains",
        "interpretation_ko": interpretation,
    }
    write_json(summary_json, summary)
    write_json(output_dir / "latest.json", {"summary_json": rel(summary_json), "generated_at": summary["generated_at"], "run_id": run_id})
    write_json(METRICS_ROOT / MAKE_SENSE_PREFIX / "latest.json", {"summary_json": rel(summary_json), "generated_at": summary["generated_at"], "run_id": run_id})
    write_make_sense_html(MAKE_SENSE_HTML, summary, edge_rows, pair_rows)
    return summary


def build_make_sense_net_candidate() -> dict[str, Any]:
    audit = make_sense_network_audit()
    source_net = active_b0_net()
    sumo_net = read_sumo_net(source_net)
    edge_csv = project_path(audit.get("edge_csv", ""))
    pair_csv = project_path(audit.get("pair_csv", ""))
    targets: dict[str, dict[str, Any]] = {}

    def add_target(edge_id: str, target_lanes: int, reason: str) -> None:
        try:
            edge = sumo_net.getEdge(edge_id)
        except Exception:
            return
        current_lanes = int(edge.getLaneNumber())
        current_speed = float(edge.getSpeed()) * 3.6
        length_m = float(edge.getLength())
        row = targets.setdefault(edge_id, {
            "edge_id": edge_id,
            "current_lanes": current_lanes,
            "target_lanes": current_lanes,
            "old_speed_kmh": round(current_speed, 6),
            "target_speed_kmh": round(min(current_speed, 50.0 if length_m >= SHORT_EDGE_WARN_LENGTH_M else 35.0), 6),
            "edge_length_m": round(length_m, 6),
            "reasons": [],
            "roles": ["make_sense_high_risk"],
        })
        row["target_lanes"] = max(int(row["target_lanes"]), int(target_lanes))
        if reason not in row["reasons"]:
            row["reasons"].append(reason)

    if edge_csv.is_file():
        for row in read_csv(edge_csv):
            issues = set(filter(None, row.get("issues", "").split(";")))
            if "high_risk_one_lane" in issues:
                add_target(row.get("edge_id", ""), 2, "make_sense_high_flow_or_protected_one_lane")
    if pair_csv.is_file():
        for row in read_csv(pair_csv):
            issue = row.get("issue", "")
            if issue in {"high_risk_3_to_1_lane_drop", "high_risk_2_to_1_lane_drop"}:
                add_target(row.get("to_edge", ""), 2, issue)

    structural_defect_count = len(targets)
    if structural_defect_count == 0:
        payload = {
            "schema": "expanded_v7_make_sense_net_candidate.v1",
            "generated_at": utc_now(),
            "status": "PASS",
            "selected_for_manifest": False,
            "active_net": audit.get("active_net", rel(source_net)),
            "candidate_net": "",
            "audit_summary_json": audit.get("summary_json", ""),
            "structural_defect_count": 0,
            "changed_edge_count": 0,
            "changed_lane_count": 0,
            "sumo_net_load": {"status": "SKIP"},
            "route_connectivity": {"status": "SKIP"},
            "recommendation_ko": "고위험 3→1/끊김/1차선 구조 결함이 없어 새 net을 생성하지 않았습니다. 남은 저속은 TLS/boundary calibration 대상으로 봅니다.",
        }
        write_json(MAKE_SENSE_CANDIDATE_SUMMARY_JSON, payload)
        return payload

    ensure_isolated_output(MAKE_SENSE_FIXED_NET)
    work_dir = DATA_ROOT / "net/make_sense_plain_work"
    plain_dir = work_dir / "plain"
    plain_dir.mkdir(parents=True, exist_ok=True)
    prefix = plain_dir / "expanded_v7_make_sense_plain"
    export_command = [find_executable("netconvert"), "--sumo-net-file", str(source_net), "--plain-output-prefix", str(prefix)]
    export_completed = run_netconvert(export_command)
    if export_completed.returncode != 0:
        raise ExpandedV7Error(f"make_sense_plain_export_failed:{export_completed.stderr[-2000:]}")
    node_file = prefix.with_suffix(".nod.xml")
    edge_file = prefix.with_suffix(".edg.xml")
    con_file = prefix.with_suffix(".con.xml")
    tll_file = prefix.with_suffix(".tll.xml")
    fixed_edge_file = plain_dir / "expanded_v7_make_sense_fixed.edg.xml"
    fixed_con_file = plain_dir / "expanded_v7_make_sense_fixed.con.xml"
    protected_pairs = protected_continuation_pairs(sumo_net, MAPPING_CSV)
    edge_rewrite = rewrite_plain_edges_for_lane_drop_fix(edge_file, fixed_edge_file, targets)
    con_rewrite = rewrite_plain_connections_for_lane_drop_fix(con_file, fixed_con_file, targets, protected_pairs) if con_file.is_file() else {
        "added_connection_count": 0,
        "added_rows": [],
    }
    rebuild_command = [
        find_executable("netconvert"),
        "--node-files", str(node_file),
        "--edge-files", str(fixed_edge_file),
        "--output-file", str(MAKE_SENSE_FIXED_NET),
        "--no-turnarounds", "true",
    ]
    if fixed_con_file.is_file():
        rebuild_command.extend(["--connection-files", str(fixed_con_file)])
    if tll_file.is_file():
        rebuild_command.extend(["--tllogic-files", str(tll_file)])
    rebuild_completed = run_netconvert(rebuild_command)
    fallback_used = False
    fallback_command: list[str] = []
    fallback_stderr = ""
    if rebuild_completed.returncode != 0:
        fallback_command = [
            find_executable("netconvert"),
            "--node-files", str(node_file),
            "--edge-files", str(fixed_edge_file),
            "--output-file", str(MAKE_SENSE_FIXED_NET),
            "--no-turnarounds", "true",
            "--tls.rebuild", "true",
        ]
        fallback_completed = run_netconvert(fallback_command)
        fallback_stderr = fallback_completed.stderr[-4000:]
        if fallback_completed.returncode != 0:
            raise ExpandedV7Error(
                "make_sense_net_rebuild_failed:"
                f"primary={rebuild_completed.stderr[-2000:]}"
                f"\nfallback={fallback_completed.stderr[-2000:]}"
            )
        rebuild_completed = fallback_completed
        rebuild_command = fallback_command
        fallback_used = True
    write_csv(MAKE_SENSE_OVERRIDES_CSV, edge_rewrite["rows"], [
        "edge_id", "old_numLanes", "new_numLanes", "old_speed_kmh", "new_speed_kmh",
        "edge_length_m", "roles", "reasons", "changed",
    ])
    route_check = route_connectivity_on_net(MAKE_SENSE_FIXED_NET)
    load_check = sumo_net_load_check(MAKE_SENSE_FIXED_NET)
    post_audit = make_sense_network_audit(net_file=MAKE_SENSE_FIXED_NET)
    payload = {
        "schema": "expanded_v7_make_sense_net_candidate.v1",
        "generated_at": utc_now(),
        "status": "PASS" if load_check["status"] == "PASS" and route_check.get("status") in {"PASS", "SKIP"} else "FAIL",
        "selected_for_manifest": False,
        "active_net": audit.get("active_net", rel(source_net)),
        "candidate_net": rel(MAKE_SENSE_FIXED_NET),
        "overrides_csv": rel(MAKE_SENSE_OVERRIDES_CSV),
        "audit_summary_json": audit.get("summary_json", ""),
        "structural_defect_count": structural_defect_count,
        "target_edge_count": len(targets),
        "changed_edge_count": edge_rewrite["changed_edge_count"],
        "changed_lane_count": edge_rewrite["changed_lane_count"],
        "added_connection_count": con_rewrite.get("added_connection_count", 0),
        "plain_export_command": export_command,
        "rebuild_command": rebuild_command,
        "fallback_used": fallback_used,
        "fallback_command": fallback_command,
        "fallback_stderr": fallback_stderr,
        "sumo_net_load": load_check,
        "route_connectivity": route_check,
        "post_make_sense_audit": post_audit,
        "recommendation_ko": "V7 전용 후보 net을 생성했습니다. manifest에는 자동 채택하지 않았고, B0 재실행 후 stop/free 및 remaining이 개선될 때만 채택해야 합니다.",
    }
    write_json(MAKE_SENSE_CANDIDATE_SUMMARY_JSON, payload)
    return payload


def build_mainline_lane_drop_targets(
    sumo_net: Any,
    mapping_csv: Path = MAPPING_CSV,
) -> tuple[dict[str, dict[str, Any]], set[tuple[str, str]]]:
    mapping_edges = all_mapping_edge_ids(mapping_csv)
    mapping_targets = mapping_target_lanes_by_edge(mapping_csv)
    route_edges = set(accepted_route_edges())
    release_edges = set(MAINLINE_RELEASE_EDGE_IDS)
    protected_pairs = protected_continuation_pairs(sumo_net, mapping_csv)
    targets: dict[str, dict[str, Any]] = {}

    def add_target(edge_id: str, target_lanes: int, reason: str) -> None:
        try:
            edge = sumo_net.getEdge(edge_id)
        except Exception:
            return
        current_lanes = int(edge.getLaneNumber())
        current_speed_kmh = float(edge.getSpeed()) * 3.6
        edge_length = float(edge.getLength())
        speed_cap = 35.0 if edge_length < SHORT_EDGE_WARN_LENGTH_M or edge_id in release_edges else 50.0
        target_lanes = max(current_lanes, target_lanes)
        row = targets.setdefault(edge_id, {
            "edge_id": edge_id,
            "current_lanes": current_lanes,
            "target_lanes": current_lanes,
            "old_speed_kmh": round(current_speed_kmh, 6),
            "target_speed_kmh": round(min(current_speed_kmh, speed_cap), 6),
            "edge_length_m": round(edge_length, 6),
            "reasons": [],
            "roles": [],
        })
        row["target_lanes"] = max(int(row["target_lanes"]), int(target_lanes))
        row["target_speed_kmh"] = min(float(row["target_speed_kmh"]), speed_cap)
        if reason not in row["reasons"]:
            row["reasons"].append(reason)

    for edge_id in sorted(mapping_edges):
        current = sumolib_edge_lane_count(sumo_net, edge_id)
        target = 2 if current <= 1 else current
        add_target(edge_id, target, "s1_s22_mapping_no_one_lane_keep_existing")
    for edge_id in sorted(route_edges):
        current = sumolib_edge_lane_count(sumo_net, edge_id)
        target = 2 if current <= 1 else current
        add_target(edge_id, target, "firetruck_route_no_one_lane_keep_existing")
    for edge_id in sorted(release_edges):
        current = sumolib_edge_lane_count(sumo_net, edge_id)
        if current <= 0:
            continue
        target = 2 if current <= 1 else current
        add_target(edge_id, target, "release_terminal_no_one_lane_keep_existing")
    for from_edge, to_edge in sorted(protected_pairs):
        from_lanes = sumolib_edge_lane_count(sumo_net, from_edge)
        to_lanes = sumolib_edge_lane_count(sumo_net, to_edge)
        if from_lanes >= 3 and to_lanes <= 1:
            add_target(to_edge, 2, f"protected_continuation_3_to_1_from:{from_edge}")
        elif from_lanes == 2 and to_lanes <= 1:
            add_target(to_edge, 2, f"protected_continuation_2_to_1_from:{from_edge}")
    for edge_id, row in targets.items():
        roles = []
        if edge_id in mapping_edges:
            roles.append("mapping")
        if edge_id in route_edges:
            roles.append("firetruck_route")
        if edge_id in release_edges:
            roles.append("release_or_terminal")
        row["roles"] = roles
    return targets, protected_pairs


def rewrite_plain_edges_for_lane_drop_fix(
    edge_xml: Path,
    output_xml: Path,
    targets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tree = ET.parse(edge_xml)
    root = tree.getroot()
    changed_rows: list[dict[str, Any]] = []
    changed_edge_count = 0
    changed_lane_count = 0
    changed_speed_count = 0
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        target = targets.get(edge_id)
        if not target:
            continue
        old_lanes = safe_int(edge.get("numLanes"), 1)
        new_lanes = int(target["target_lanes"])
        old_speed_mps = safe_float(edge.get("speed"))
        new_speed_mps = float(target["target_speed_kmh"]) / 3.6 if safe_float(target.get("target_speed_kmh")) > 0 else old_speed_mps
        changed = False
        if new_lanes > old_lanes:
            edge.set("numLanes", str(new_lanes))
            changed_lane_count += 1
            changed = True
        if old_speed_mps > 0 and new_speed_mps > 0 and old_speed_mps > new_speed_mps:
            edge.set("speed", f"{new_speed_mps:.6f}")
            changed_speed_count += 1
            changed = True
        if changed:
            changed_edge_count += 1
        changed_rows.append({
            "edge_id": edge_id,
            "old_numLanes": old_lanes,
            "new_numLanes": max(old_lanes, new_lanes),
            "old_speed_kmh": round(old_speed_mps * 3.6, 6) if old_speed_mps else "",
            "new_speed_kmh": round(min(old_speed_mps, new_speed_mps) * 3.6, 6) if old_speed_mps and new_speed_mps else "",
            "edge_length_m": target.get("edge_length_m", ""),
            "roles": " ".join(target.get("roles", [])),
            "reasons": ";".join(target.get("reasons", [])),
            "changed": changed,
        })
    output_xml.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_xml, encoding="utf-8", xml_declaration=True)
    return {
        "edge_xml": rel(edge_xml),
        "output_xml": rel(output_xml),
        "target_edge_count": len(targets),
        "written_edge_count": len(changed_rows),
        "changed_edge_count": changed_edge_count,
        "changed_lane_count": changed_lane_count,
        "changed_speed_count": changed_speed_count,
        "rows": changed_rows,
    }


def rewrite_plain_connections_for_lane_drop_fix(
    con_xml: Path,
    output_xml: Path,
    targets: dict[str, dict[str, Any]],
    protected_pairs: set[tuple[str, str]],
) -> dict[str, Any]:
    tree = ET.parse(con_xml)
    root = tree.getroot()
    existing = {
        (connection.get("from", ""), connection.get("to", ""), safe_int(connection.get("fromLane")), safe_int(connection.get("toLane")))
        for connection in root.findall("connection")
    }
    by_pair: dict[tuple[str, str], list[ET.Element]] = {}
    for connection in root.findall("connection"):
        by_pair.setdefault((connection.get("from", ""), connection.get("to", "")), []).append(connection)
    added_rows: list[dict[str, Any]] = []
    for from_edge, to_edge in sorted(protected_pairs):
        from_target = targets.get(from_edge)
        to_target = targets.get(to_edge)
        if not from_target or not to_target:
            continue
        from_lanes = int(from_target["target_lanes"])
        to_lanes = int(to_target["target_lanes"])
        lane_count = min(from_lanes, to_lanes)
        if lane_count <= 1:
            continue
        sample_connections = by_pair.get((from_edge, to_edge), [])
        if not sample_connections:
            continue
        if any(connection.get("tl") for connection in sample_connections):
            continue
        for lane_index in range(lane_count):
            key = (from_edge, to_edge, lane_index, lane_index)
            if key in existing:
                continue
            root.append(ET.Element("connection", {
                "from": from_edge,
                "to": to_edge,
                "fromLane": str(lane_index),
                "toLane": str(lane_index),
            }))
            existing.add(key)
            added_rows.append({
                "from_edge": from_edge,
                "to_edge": to_edge,
                "fromLane": lane_index,
                "toLane": lane_index,
                "reason": "protected_mainline_continuation_lane_coverage",
            })
    output_xml.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_xml, encoding="utf-8", xml_declaration=True)
    return {
        "connection_xml": rel(con_xml),
        "output_xml": rel(output_xml),
        "protected_pair_count": len(protected_pairs),
        "added_connection_count": len(added_rows),
        "added_rows": added_rows,
    }


def build_mainline_lane_drop_fixed_net(
    input_net: Path | None = None,
    output_net: Path = LANE_DROP_FIXED_NET,
    selected_for_manifest: bool = True,
) -> dict[str, Any]:
    source_net = input_net or active_b0_net_before_lane_drop()
    ensure_isolated_output(output_net)
    if not source_net.is_file():
        raise ExpandedV7Error(f"missing_lane_drop_fix_input_net:{rel(source_net)}")
    source_net_obj = read_sumo_net(source_net)
    targets, protected_pairs = build_mainline_lane_drop_targets(source_net_obj)
    work_dir = DATA_ROOT / "net/lane_drop_fixed_plain_work"
    plain_dir = work_dir / "plain"
    plain_dir.mkdir(parents=True, exist_ok=True)
    prefix = plain_dir / "expanded_v7_lane_drop_fixed_plain"
    export_command = [find_executable("netconvert"), "--sumo-net-file", str(source_net), "--plain-output-prefix", str(prefix)]
    export_completed = run_netconvert(export_command)
    if export_completed.returncode != 0:
        raise ExpandedV7Error(f"lane_drop_plain_export_failed:{export_completed.stderr[-2000:]}")
    node_file = prefix.with_suffix(".nod.xml")
    edge_file = prefix.with_suffix(".edg.xml")
    con_file = prefix.with_suffix(".con.xml")
    tll_file = prefix.with_suffix(".tll.xml")
    if not edge_file.is_file() or not node_file.is_file():
        raise ExpandedV7Error(f"lane_drop_plain_export_missing_files:{rel(plain_dir)}")
    fixed_edge_file = plain_dir / "expanded_v7_lane_drop_fixed.edg.xml"
    fixed_con_file = plain_dir / "expanded_v7_lane_drop_fixed.con.xml"
    edge_rewrite = rewrite_plain_edges_for_lane_drop_fix(edge_file, fixed_edge_file, targets)
    con_rewrite = rewrite_plain_connections_for_lane_drop_fix(con_file, fixed_con_file, targets, protected_pairs) if con_file.is_file() else {
        "connection_xml": "",
        "output_xml": "",
        "protected_pair_count": len(protected_pairs),
        "added_connection_count": 0,
        "added_rows": [],
    }
    output_net.parent.mkdir(parents=True, exist_ok=True)
    rebuild_command = [
        find_executable("netconvert"),
        "--node-files", str(node_file),
        "--edge-files", str(fixed_edge_file),
        "--output-file", str(output_net),
        "--no-turnarounds", "true",
    ]
    if fixed_con_file.is_file():
        rebuild_command.extend(["--connection-files", str(fixed_con_file)])
    if tll_file.is_file():
        rebuild_command.extend(["--tllogic-files", str(tll_file)])
    rebuild_completed = run_netconvert(rebuild_command)
    rebuild_stderr = rebuild_completed.stderr[-4000:]
    fallback_command: list[str] = []
    fallback_stderr = ""
    fallback_used = False
    if rebuild_completed.returncode != 0:
        fallback_command = [
            find_executable("netconvert"),
            "--node-files", str(node_file),
            "--edge-files", str(fixed_edge_file),
            "--output-file", str(output_net),
            "--no-turnarounds", "true",
            "--tls.rebuild", "true",
        ]
        fallback_completed = run_netconvert(fallback_command)
        fallback_stderr = fallback_completed.stderr[-4000:]
        if fallback_completed.returncode != 0:
            raise ExpandedV7Error(
                "lane_drop_net_rebuild_failed:"
                f"primary={rebuild_completed.stderr[-2000:]}"
                f"\nfallback={fallback_completed.stderr[-2000:]}"
            )
        rebuild_command = fallback_command
        rebuild_completed = fallback_completed
        fallback_used = True
    write_csv(LANE_DROP_FIXED_OVERRIDES_CSV, edge_rewrite["rows"], [
        "edge_id", "old_numLanes", "new_numLanes", "old_speed_kmh", "new_speed_kmh",
        "edge_length_m", "roles", "reasons", "changed",
    ])
    route_check = route_connectivity_on_net(output_net)
    load_check = sumo_net_load_check(output_net)
    post_audit = road_integrity_audit(net_file=output_net)
    summary = {
        "schema": "expanded_v7_mainline_lane_drop_fixed_net.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(REFERENCE_CSV),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "input_net": rel(source_net),
        "output_net": rel(output_net),
        "overrides_csv": rel(LANE_DROP_FIXED_OVERRIDES_CSV),
        "work_dir": rel(work_dir),
        "plain_export_command": export_command,
        "plain_export_stderr": export_completed.stderr[-4000:],
        "edge_rewrite": {key: value for key, value in edge_rewrite.items() if key != "rows"},
        "connection_rewrite": {key: value for key, value in con_rewrite.items() if key != "added_rows"},
        "rebuild_command": rebuild_command,
        "rebuild_stderr": rebuild_stderr,
        "fallback_command": fallback_command,
        "fallback_stderr": fallback_stderr,
        "fallback_used": fallback_used,
        "changed_edge_count": edge_rewrite["changed_edge_count"],
        "changed_lane_count": edge_rewrite["changed_lane_count"],
        "changed_speed_count": edge_rewrite["changed_speed_count"],
        "added_connection_count": con_rewrite["added_connection_count"],
        "selected_for_manifest": selected_for_manifest,
        "sumo_net_load": load_check,
        "route_connectivity": route_check,
        "road_integrity_audit": post_audit,
        "status": "PASS" if load_check["status"] == "PASS" and route_check.get("status") in {"PASS", "SKIP"} else "FAIL",
        "note": "V7-only road integrity candidate. It fixes protected Toegye-ro/firetruck-route lane drops through plain netconvert round-trip and caps implausible 100km/h protected edges.",
    }
    write_json(LANE_DROP_FIXED_SUMMARY_JSON, summary)
    return summary


def network_integrity_audit(
    net_file: Path | None = None,
    demand_xml: Path = DEMAND_XML,
    accepted_routes: Path = ACCEPTED_ROUTES_CSV,
    mapping_csv: Path = MAPPING_CSV,
) -> dict[str, Any]:
    net_path = net_file or active_b0_net()
    sumo_net = read_sumo_net(net_path)
    output_dir = METRICS_ROOT / NETWORK_INTEGRITY_PREFIX
    output_dir.mkdir(parents=True, exist_ok=True)
    main_edges = mainroad_edge_ids(mapping_csv)
    accepted_rows = read_csv(accepted_routes) if accepted_routes.is_file() else []
    route_edges = accepted_rows[0].get("route_edges", "").split() if accepted_rows else []
    bad_route_pairs = []
    tls_pairs = 0
    non_tls_pairs = 0
    for index, (from_edge, to_edge) in enumerate(zip(route_edges, route_edges[1:], strict=False)):
        if not route_connected(sumo_net, [from_edge, to_edge]):
            bad_route_pairs.append({"route_index": index, "from_edge": from_edge, "to_edge": to_edge})
            continue
        try:
            edge = sumo_net.getEdge(from_edge)
            has_tls = False
            for _outgoing, connections in edge.getOutgoing().items():
                connections = connections if isinstance(connections, list) else [connections]
                for connection in connections:
                    if connection.getTo().getID() == to_edge and getattr(connection, "getTLSID", lambda: None)():
                        has_tls = True
            if has_tls:
                tls_pairs += 1
            else:
                non_tls_pairs += 1
        except Exception:
            non_tls_pairs += 1
    passenger_edges = []
    no_out = []
    no_in = []
    short_edges = []
    for edge in sumo_net.getEdges():
        if not passenger_candidate_edge(edge):
            continue
        edge_id = edge.getID()
        passenger_edges.append(edge_id)
        outgoing = [item for item in edge_iter(edge.getOutgoing()) if passenger_candidate_edge(item)]
        incoming = [item for item in edge_iter(edge.getIncoming()) if passenger_candidate_edge(item)]
        if not outgoing:
            no_out.append(edge_id)
        if not incoming:
            no_in.append(edge_id)
        if float(edge.getLength()) < SHORT_EDGE_WARN_LENGTH_M:
            short_edges.append(edge_id)
    invalid_rows = []
    source_counts: dict[str, int] = {}
    sink_counts: dict[str, int] = {}
    template_counts: dict[tuple[str, str, int], int] = {}
    flow_type_counts: dict[str, int] = {}
    forbidden_source_counts: dict[str, int] = {}
    forbidden_sink_counts: dict[str, int] = {}
    corridor_source_count = 0
    corridor_sink_count = 0
    vehicle_count = 0
    if demand_xml.is_file():
        root = ET.parse(demand_xml).getroot()
        for vehicle in root.findall("vehicle"):
            vehicle_count += 1
            vehicle_id = vehicle.get("id", "")
            route = vehicle.find("route")
            route_edges_for_vehicle = route.get("edges", "").split() if route is not None else []
            if vehicle_id.startswith("expanded_v7_mapwide_"):
                flow_type = "mapwide"
            elif vehicle_id.startswith("expanded_v7_sideflow_"):
                flow_type = "sideflow"
            elif vehicle_id.startswith("expanded_v7_ref_"):
                flow_type = "main"
            else:
                flow_type = "other"
            flow_type_counts[flow_type] = flow_type_counts.get(flow_type, 0) + 1
            if not route_edges_for_vehicle or not route_connected(sumo_net, route_edges_for_vehicle):
                invalid_rows.append({
                    "vehicle_id": vehicle_id,
                    "flow_type": flow_type,
                    "edge_count": len(route_edges_for_vehicle),
                    "first_edge": route_edges_for_vehicle[0] if route_edges_for_vehicle else "",
                    "last_edge": route_edges_for_vehicle[-1] if route_edges_for_vehicle else "",
                })
                continue
            source = route_edges_for_vehicle[0]
            sink = route_edges_for_vehicle[-1]
            source_counts[source] = source_counts.get(source, 0) + 1
            sink_counts[sink] = sink_counts.get(sink, 0) + 1
            template_key = (source, sink, len(route_edges_for_vehicle))
            template_counts[template_key] = template_counts.get(template_key, 0) + 1
            if source in main_edges:
                corridor_source_count += 1
            if sink in main_edges:
                corridor_sink_count += 1
            if forbidden_source_sink_edge(source):
                forbidden_source_counts[source] = forbidden_source_counts.get(source, 0) + 1
            if forbidden_source_sink_edge(sink):
                forbidden_sink_counts[sink] = forbidden_sink_counts.get(sink, 0) + 1
    template_rows = [
        {
            "source_edge": source,
            "sink_edge": sink,
            "route_edge_count": route_edge_count,
            "vehicle_count": count,
            "source_is_corridor": source in main_edges,
            "sink_is_corridor": sink in main_edges,
            "source_forbidden": forbidden_source_sink_edge(source),
            "sink_forbidden": forbidden_source_sink_edge(sink),
        }
        for (source, sink, route_edge_count), count in sorted(template_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    source_sink_rows = []
    for edge_id, count in sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))[:80]:
        source_sink_rows.append({
            "edge_id": edge_id,
            "role": "source",
            "vehicle_count": count,
            "is_corridor": edge_id in main_edges,
            "is_forbidden": forbidden_source_sink_edge(edge_id),
        })
    for edge_id, count in sorted(sink_counts.items(), key=lambda item: (-item[1], item[0]))[:80]:
        source_sink_rows.append({
            "edge_id": edge_id,
            "role": "sink",
            "vehicle_count": count,
            "is_corridor": edge_id in main_edges,
            "is_forbidden": forbidden_source_sink_edge(edge_id),
        })
    invalid_csv = output_dir / "expanded_v7_invalid_demand_routes.csv"
    template_csv = output_dir / "expanded_v7_route_template_concentration.csv"
    source_sink_csv = output_dir / "expanded_v7_source_sink_concentration.csv"
    write_csv(invalid_csv, invalid_rows, ["vehicle_id", "flow_type", "edge_count", "first_edge", "last_edge"])
    write_csv(template_csv, template_rows, [
        "source_edge", "sink_edge", "route_edge_count", "vehicle_count",
        "source_is_corridor", "sink_is_corridor", "source_forbidden", "sink_forbidden",
    ])
    write_csv(source_sink_csv, source_sink_rows, [
        "edge_id", "role", "vehicle_count", "is_corridor", "is_forbidden",
    ])
    max_template_vehicle_count = max((row["vehicle_count"] for row in template_rows), default=0)
    payload = {
        "schema": "expanded_v7_network_integrity_audit.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(REFERENCE_CSV),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "active_net": rel(net_path),
        "demand_xml": rel(demand_xml),
        "accepted_route_edge_count": len(route_edges),
        "accepted_route_bad_pair_count": len(bad_route_pairs),
        "accepted_route_bad_pairs": bad_route_pairs[:20],
        "accepted_route_tls_pairs": tls_pairs,
        "accepted_route_non_tls_pairs": non_tls_pairs,
        "passenger_edge_count": len(passenger_edges),
        "no_out_passenger_edge_count": len(no_out),
        "no_in_passenger_edge_count": len(no_in),
        "short_lt_10m_edge_count": len(short_edges),
        "demand_vehicle_count": vehicle_count,
        "invalid_demand_route_count": len(invalid_rows),
        "flow_type_counts": flow_type_counts,
        "route_template_count": len(template_rows),
        "max_template_vehicle_count": max_template_vehicle_count,
        "corridor_source_vehicle_count": corridor_source_count,
        "corridor_sink_vehicle_count": corridor_sink_count,
        "forbidden_source_vehicle_count": sum(forbidden_source_counts.values()),
        "forbidden_sink_vehicle_count": sum(forbidden_sink_counts.values()),
        "top_forbidden_sources": sorted(forbidden_source_counts.items(), key=lambda item: (-item[1], item[0]))[:20],
        "top_forbidden_sinks": sorted(forbidden_sink_counts.items(), key=lambda item: (-item[1], item[0]))[:20],
        "invalid_routes_csv": rel(invalid_csv),
        "route_template_csv": rel(template_csv),
        "source_sink_csv": rel(source_sink_csv),
        "status": "PASS" if len(bad_route_pairs) == 0 and len(invalid_rows) == 0 else "FAIL",
    }
    summary_json = output_dir / "expanded_v7_network_integrity_audit_summary.json"
    write_json(summary_json, payload)
    write_json(output_dir / "latest.json", {"summary_json": rel(summary_json), "generated_at": payload["generated_at"]})
    return {**payload, "summary_json": rel(summary_json)}


def bottleneck_diagnosis_edges() -> list[str]:
    route_edges = accepted_route_edges()
    route_set = set(route_edges)
    edges = [
        edge_id for edge_id in route_edges
        if bottleneck_edge(edge_id) or edge_id in {"218773869#4", "218773869#5", "218773869#6", "218773869#7", "218773869#8", "781985787#0"}
    ]
    for edge_id in sorted(BOTTLENECK_EDGE_IDS | {f"347237859#{index}" for index in range(6)}):
        if edge_id not in route_set and edge_id not in edges:
            edges.append(edge_id)
    return edges


def write_bottleneck_diagnosis_html(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    table = "\n".join(
        "<tr>"
        f"<td>{esc(row['edge_id'])}</td><td>{esc(row.get('edge_length_m', ''))}</td><td>{esc(row.get('artifact_level', ''))}</td>"
        f"<td>{esc(row['route_index'])}</td><td>{esc(row['generated_route_vehicle_count'])}</td>"
        f"<td>{esc(row['flow_type_counts'])}</td><td>{fmt(row['observed_count'], 0)}</td><td>{fmt(row['speed_kmh'], 1)}</td>"
        f"<td>{fmt(row['waitingTime'], 0)}</td><td>{esc(row['source_internal_count'])}</td><td>{esc(row['sink_internal_count'])}</td>"
        "</tr>"
        for row in rows
    )
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Expanded V7 Bottleneck Diagnosis</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2937; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #d1d5db; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f3f4f6; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); gap: 12px; margin: 16px 0; }}
    .card {{ border: 1px solid #d1d5db; border-radius: 6px; padding: 12px; }}
    .num {{ font-size: 24px; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>Expanded V7 Bottleneck Diagnosis</h1>
  <p>병목 진단은 S9-S17 상행 route 주변 edge의 generated route load, 병목 내부 source/sink, edgeData 통과량/속도/waiting time을 같이 봅니다.</p>
  <div class="cards">
    <div class="card"><div>Run</div><div class="num">{esc(summary.get('run_id', ''))}</div></div>
    <div class="card"><div>Profile</div><div class="num">{esc(summary.get('demand_profile', ''))}</div></div>
    <div class="card"><div>Internal source vehicles</div><div class="num">{esc(summary.get('total_source_internal_count', 0))}</div></div>
    <div class="card"><div>Internal sink vehicles</div><div class="num">{esc(summary.get('total_sink_internal_count', 0))}</div></div>
  </div>
	  <h2>Root Cause</h2>
	  <p>{esc(summary.get('interpretation', ''))}</p>
	  <p>Reference CSV: <code>{esc(summary.get('reference_csv', ''))}</code></p>
	  <p>Additional reports: <code>{esc(summary.get('route_contamination_csv', ''))}</code>,
	  <code>{esc(summary.get('teleport_source_csv', ''))}</code>,
	  <code>{esc(summary.get('short_edge_artifact_csv', ''))}</code></p>
	  <h2>Edge Table</h2>
	  <table>
	    <thead><tr><th>Edge</th><th>Length m</th><th>Short artifact</th><th>Route index</th><th>Generated load</th><th>Flow mix</th><th>Observed</th><th>Speed km/h</th><th>Waiting s</th><th>Internal src</th><th>Internal sink</th></tr></thead>
	    <tbody>{table}</tbody>
	  </table>
  <p>CSV: <code>{esc(summary.get('edge_csv', ''))}</code></p>
</body>
</html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def write_bottleneck_cause_summary_html(
    path: Path,
    summary: dict[str, Any],
    contamination_rows: list[dict[str, Any]],
    teleport_rows: list[dict[str, Any]],
    short_edge_rows: list[dict[str, Any]],
) -> None:
    contamination_table = "\n".join(
        "<tr>"
        f"<td>{esc(row.get('vehicle_id', ''))}</td><td>{esc(row.get('flow_type', ''))}</td>"
        f"<td>{esc(row.get('start_edge', ''))}</td><td>{esc(row.get('target_edge', ''))}</td>"
        f"<td>{esc(row.get('forbidden_bottleneck_edges', ''))}</td><td>{esc(row.get('route_guard_reason', ''))}</td>"
        "</tr>"
        for row in contamination_rows[:80]
    )
    teleport_table = "\n".join(
        "<tr>"
        f"<td>{esc(row.get('vehicle_id', ''))}</td><td>{esc(row.get('flow_type', ''))}</td>"
        f"<td>{esc(row.get('wait_reason', ''))}</td><td>{esc(row.get('edge_id', ''))}</td>"
        f"<td>{esc(row.get('time_sec', ''))}</td>"
        "</tr>"
        for row in teleport_rows[:80]
    )
    short_table = "\n".join(
        "<tr>"
        f"<td>{esc(row.get('edge_id', ''))}</td><td>{esc(row.get('edge_length_m', ''))}</td>"
        f"<td>{esc(row.get('artifact_level', ''))}</td><td>{esc(row.get('speed_gate_use', ''))}</td>"
        "</tr>"
        for row in short_edge_rows if row.get("short_edge_artifact") is True
    )
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Expanded V7 Bottleneck Cause Summary</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 12px 0 28px; }}
    th, td {{ border-bottom: 1px solid #d7dde8; padding: 7px; text-align: left; vertical-align: top; }}
    th {{ background: #eef2f7; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; }}
    .card {{ border: 1px solid #d7dde8; border-radius: 6px; padding: 12px; }}
    .num {{ font-size: 24px; font-weight: 700; }}
    code {{ background: #f5f7fb; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Expanded V7 병목 원인 요약</h1>
  <p>목표는 완벽한 edge strict recall이 아니라, 도로 무결성을 유지하면서 정지류/자유류 양극단을 줄이고 CSV 정체 속도에 가까운 저속 흐름을 만드는 것입니다.</p>
  <p>Reference CSV: <code>{esc(summary.get('reference_csv', ''))}</code></p>
  <div class="cards">
    <div class="card"><div>Route contamination</div><div class="num">{esc(summary.get('route_contamination', {}).get('contaminated_vehicle_count', 0))}</div></div>
    <div class="card"><div>Non-through contamination</div><div class="num">{esc(summary.get('route_contamination', {}).get('contaminated_non_through_count', 0))}</div></div>
    <div class="card"><div>Teleport/yield events</div><div class="num">{esc(summary.get('teleport_sources', {}).get('teleport_event_count', 0))}</div></div>
    <div class="card"><div>Short edges</div><div class="num">{esc(summary.get('short_edge_artifacts', {}).get('short_edge_count', 0))}</div></div>
  </div>
  <h2>판단</h2>
  <p>{esc(summary.get('interpretation', ''))}</p>
  <h2>Route Direction Contamination</h2>
  <table><thead><tr><th>Vehicle</th><th>Type</th><th>Start</th><th>Target</th><th>Forbidden edges</th><th>Reason</th></tr></thead><tbody>{contamination_table}</tbody></table>
  <h2>Yield / Teleport Source</h2>
  <table><thead><tr><th>Vehicle</th><th>Type</th><th>Reason</th><th>Edge</th><th>Time</th></tr></thead><tbody>{teleport_table}</tbody></table>
  <h2>Short Edge Artifacts</h2>
  <table><thead><tr><th>Edge</th><th>Length m</th><th>Level</th><th>Speed gate</th></tr></thead><tbody>{short_table}</tbody></table>
</body>
</html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def diagnose_bottleneck(results_csv: str = "auto") -> dict[str, Any]:
    row = latest_b0_results_row(results_csv)
    run_dir = project_path(row.get("run_dir", ""))
    run_id = row.get("run_id") or run_dir.name or "unknown_run"
    output_dir = METRICS_ROOT / BOTTLENECK_DIAG_PREFIX / run_id
    edge_ids = bottleneck_diagnosis_edges()
    wanted = set(edge_ids)
    route_edges = accepted_route_edges()
    route_index = {edge_id: index for index, edge_id in enumerate(route_edges)}
    sumo_net = read_sumo_net(active_b0_net())
    load = demand_route_load_by_edge(DEMAND_XML, wanted)
    realized = parse_edge_data_for_edges(run_dir / "edgeData.xml", wanted)
    contamination_rows, contamination_csv, contamination_summary = write_route_contamination_report(DEMAND_XML, output_dir)
    stderr_log = project_path(row.get("stderr_log", ""))
    teleport_rows, teleport_csv, teleport_summary = parse_teleport_source_report(stderr_log, output_dir)
    short_edge_rows, short_edge_csv, short_edge_summary = write_short_edge_artifact_report(sumo_net, edge_ids, output_dir)
    short_edge_by_id = {row["edge_id"]: row for row in short_edge_rows}
    rows = []
    for edge_id in edge_ids:
        load_row = load.get(edge_id, {})
        realized_row = realized.get(edge_id, {})
        short_row = short_edge_by_id.get(edge_id, {})
        rows.append({
            "edge_id": edge_id,
            "edge_length_m": short_row.get("edge_length_m", round(edge_length_m(sumo_net, edge_id), 6)),
            "short_edge_artifact": short_row.get("short_edge_artifact", False),
            "artifact_level": short_row.get("artifact_level", ""),
            "route_index": route_index.get(edge_id, ""),
            "is_firetruck_route_edge": edge_id in route_index,
            "is_bottleneck_internal": bottleneck_edge(edge_id),
            "generated_route_vehicle_count": safe_int(load_row.get("generated_route_vehicle_count")),
            "flow_type_counts": json.dumps(load_row.get("flow_type_counts", {}), ensure_ascii=False, sort_keys=True),
            "source_internal_count": safe_int(load_row.get("source_internal_count")),
            "sink_internal_count": safe_int(load_row.get("sink_internal_count")),
            "observed_count": round(safe_float(realized_row.get("observed_count")), 6),
            "entered": round(safe_float(realized_row.get("entered")), 6),
            "left": round(safe_float(realized_row.get("left")), 6),
            "speed_kmh": round(safe_float(realized_row.get("speed_kmh")), 6),
            "waitingTime": round(safe_float(realized_row.get("waitingTime")), 6),
            "sampledSeconds": round(safe_float(realized_row.get("sampledSeconds")), 6),
        })
    rows.sort(key=lambda item: (
        "" if item["route_index"] == "" else f"{int(item['route_index']):04d}",
        -int(item["generated_route_vehicle_count"]),
        item["edge_id"],
    ))
    csv_path = output_dir / "bottleneck_edge_diagnosis.csv"
    json_path = output_dir / "bottleneck_diagnosis_summary.json"
    write_csv(csv_path, rows, [
        "edge_id", "edge_length_m", "short_edge_artifact", "artifact_level",
        "route_index", "is_firetruck_route_edge", "is_bottleneck_internal",
        "generated_route_vehicle_count", "flow_type_counts", "source_internal_count", "sink_internal_count",
        "observed_count", "entered", "left", "speed_kmh", "waitingTime", "sampledSeconds",
    ])
    demand_summary = read_json(DEMAND_XML.with_suffix(".summary.json")) if DEMAND_XML.with_suffix(".summary.json").is_file() else {}
    total_source_internal = sum(safe_int(item["source_internal_count"]) for item in rows)
    total_sink_internal = sum(safe_int(item["sink_internal_count"]) for item in rows)
    overloaded = sorted(rows, key=lambda item: safe_int(item["generated_route_vehicle_count"]), reverse=True)[:5]
    summary = {
        "schema": "expanded_v7_bottleneck_diagnosis.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "results_csv": rel(project_path(results_csv)) if results_csv != "auto" else rel(project_path(read_json(METRICS_ROOT / RUN_PREFIX / "latest.json").get("results_csv", ""))),
        "run_dir": rel(run_dir),
        "edge_csv": rel(csv_path),
        "reference_csv": rel(REFERENCE_CSV),
        "demand_profile": demand_summary.get("profile", ""),
        "demand_route_xml": rel(DEMAND_XML),
        "diagnosed_edge_count": len(rows),
        "total_generated_route_vehicle_count": sum(safe_int(item["generated_route_vehicle_count"]) for item in rows),
        "total_source_internal_count": total_source_internal,
        "total_sink_internal_count": total_sink_internal,
        "route_contamination": contamination_summary,
        "route_contamination_csv": rel(contamination_csv),
        "teleport_sources": teleport_summary,
        "teleport_source_csv": rel(teleport_csv),
        "short_edge_artifacts": short_edge_summary,
        "short_edge_artifact_csv": rel(short_edge_csv),
        "cause_summary_html": rel(V3_CAUSE_REPORT_HTML),
        "top_generated_edges": overloaded,
        "interpretation": "비현실 병목은 단일 release edge만의 문제가 아니라 route 방향 오염, minor/yield source 삽입, 병목 내부 source/sink, short-edge speed artifact, through-up 축 집중이 함께 만든 현상입니다. v3는 non-through/local/mapwide/sideflow가 병목축을 쓰지 않게 하고, short edge는 속도 gate에서 별도 해석합니다.",
    }
    write_json(json_path, summary)
    write_json(METRICS_ROOT / BOTTLENECK_DIAG_PREFIX / "latest.json", {
        "summary_json": rel(json_path),
        "edge_csv": rel(csv_path),
        "route_contamination_csv": rel(contamination_csv),
        "teleport_source_csv": rel(teleport_csv),
        "short_edge_artifact_csv": rel(short_edge_csv),
        "cause_summary_html": rel(V3_CAUSE_REPORT_HTML),
        "run_id": run_id,
    })
    write_bottleneck_diagnosis_html(BOTTLENECK_DIAG_HTML, summary, rows)
    write_bottleneck_cause_summary_html(V3_CAUSE_REPORT_HTML, summary, contamination_rows, teleport_rows, short_edge_rows)
    return {**summary, "summary_json": rel(json_path), "html": rel(BOTTLENECK_DIAG_HTML)}


def release_edge_ids() -> set[str]:
    return {f"347237859#{index}" for index in range(6)} | {"781985787#0"}


def balanced_congestion_metrics(profile: str, results_csv: Path, diagnosis: dict[str, Any]) -> dict[str, Any]:
    row = latest_b0_results_row(str(results_csv))
    edge_rows = read_csv(project_path(diagnosis["edge_csv"])) if diagnosis.get("edge_csv") else []
    release_rows = [item for item in edge_rows if item.get("edge_id") in release_edge_ids()]
    speeds = [safe_float(item.get("speed_kmh")) for item in release_rows if safe_float(item.get("speed_kmh")) > 0]
    release_waiting = sum(safe_float(item.get("waitingTime")) for item in release_rows)
    release_observed = sum(safe_float(item.get("observed_count")) for item in release_rows)
    contaminated_non_through = safe_int((diagnosis.get("route_contamination") or {}).get("contaminated_non_through_count"))
    background_departed = safe_float(row.get("background_departed"))
    background_arrived = safe_float(row.get("background_arrived"))
    completion_rate = background_arrived / background_departed if background_departed else 0.0
    background_teleported = safe_int(row.get("background_teleported"))
    remaining = safe_int(row.get("remaining_vehicle_count"))
    progress = safe_float(row.get("emergency_route_progress_ratio"))
    network_speed = safe_float(row.get("network_avg_speed_kmh"))
    release_speed = mean(speeds)
    balanced_speed = 5.0 <= release_speed <= 20.0
    waiting_reduced = release_waiting <= BASELINE_RELEASE_WAITING_TIME_SEC * 0.5
    balanced_status = "PASS" if (
        row.get("emergency_teleport") == "False"
        and contaminated_non_through == 0
        and background_teleported < 10
        and remaining < BASELINE_BALANCED_REMAINING_COUNT
        and 10.0 <= network_speed <= 25.0
        and (row.get("emergency_arrived") == "True" or progress > 0.95)
        and balanced_speed
        and waiting_reduced
    ) else "FAIL"
    return {
        "profile": profile,
        "run_id": row.get("run_id", ""),
        "results_csv": rel(results_csv),
        "final_status": row.get("final_status", ""),
        "failure_reason": row.get("failure_reason", ""),
        "warning_reason": row.get("warning_reason", ""),
        "emergency_arrived": row.get("emergency_arrived", ""),
        "emergency_teleport": row.get("emergency_teleport", ""),
        "emergency_route_progress_ratio": progress,
        "emergency_last_edge_id": row.get("emergency_last_edge_id", ""),
        "background_departed": int(background_departed),
        "background_arrived": int(background_arrived),
        "completion_rate": round(completion_rate, 6),
        "background_teleported": background_teleported,
        "remaining_vehicle_count": remaining,
        "network_avg_speed_kmh": round(network_speed, 6),
        "release_edge_speed_kmh": round(release_speed, 6),
        "release_edge_waiting_time_sec": round(release_waiting, 6),
        "release_edge_observed_count": round(release_observed, 6),
        "release_waiting_reduction_ratio": round(1.0 - (release_waiting / BASELINE_RELEASE_WAITING_TIME_SEC), 6) if BASELINE_RELEASE_WAITING_TIME_SEC else "",
        "route_contaminated_non_through_count": safe_int((diagnosis.get("route_contamination") or {}).get("contaminated_non_through_count")),
        "teleport_event_count": safe_int((diagnosis.get("teleport_sources") or {}).get("teleport_event_count")),
        "short_edge_artifact_count": safe_int((diagnosis.get("short_edge_artifacts") or {}).get("short_edge_count")),
        "balanced_speed_status": "PASS" if balanced_speed else "FAIL",
        "balanced_congestion_status": balanced_status,
        "diagnosis_json": diagnosis.get("summary_json", ""),
        "diagnosis_csv": diagnosis.get("edge_csv", ""),
        "route_contamination_csv": diagnosis.get("route_contamination_csv", ""),
        "teleport_source_csv": diagnosis.get("teleport_source_csv", ""),
        "short_edge_artifact_csv": diagnosis.get("short_edge_artifact_csv", ""),
        "cause_summary_html": diagnosis.get("cause_summary_html", ""),
    }


def balanced_congestion_selection_key(row: dict[str, Any]) -> tuple[Any, ...]:
    speed = safe_float(row.get("release_edge_speed_kmh"))
    speed_in_range = 5.0 <= speed <= 20.0
    speed_distance = 0.0 if speed_in_range else min(abs(speed - 5.0), abs(speed - 20.0))
    return (
        row.get("balanced_congestion_status") == "PASS",
        row.get("emergency_teleport") == "False",
        row.get("emergency_arrived") == "True",
        safe_float(row.get("emergency_route_progress_ratio")),
        -safe_int(row.get("route_contaminated_non_through_count")),
        -safe_int(row.get("background_teleported")),
        speed_in_range,
        -speed_distance,
        -safe_int(row.get("remaining_vehicle_count")),
        safe_float(row.get("completion_rate")),
    )


def run_balanced_congestion_sweep(profiles: list[str] | None = None) -> dict[str, Any]:
    profiles = profiles or BALANCED_CONGESTION_PROFILES
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for profile in profiles:
        build_b0_demand(profile=profile)
        build_manifest()
        try:
            run_b0(output_prefix=BALANCED_CONGESTION_PREFIX)
        except ExpandedV7Error as exc:
            errors.append({"profile": profile, "error": str(exc)[-2000:]})
        latest_path = METRICS_ROOT / BALANCED_CONGESTION_PREFIX / "latest.json"
        if not latest_path.is_file():
            continue
        latest = read_json(latest_path)
        results_csv = project_path(latest.get("results_csv", ""))
        if not results_csv.is_file():
            continue
        diagnosis = diagnose_bottleneck(results_csv=str(results_csv))
        rows.append(balanced_congestion_metrics(profile, results_csv, diagnosis))
        if rows[-1]["balanced_congestion_status"] == "PASS":
            break
    csv_path = METRICS_ROOT / BALANCED_CONGESTION_PREFIX / "balanced_congestion_sweep_summary.csv"
    fields = [
        "profile", "run_id", "results_csv", "final_status", "failure_reason", "warning_reason",
        "emergency_arrived", "emergency_teleport", "emergency_route_progress_ratio", "emergency_last_edge_id",
        "background_departed", "background_arrived", "completion_rate", "background_teleported",
        "remaining_vehicle_count", "network_avg_speed_kmh", "release_edge_speed_kmh",
        "release_edge_waiting_time_sec", "release_edge_observed_count", "release_waiting_reduction_ratio",
        "route_contaminated_non_through_count", "teleport_event_count", "short_edge_artifact_count",
        "balanced_speed_status", "balanced_congestion_status", "diagnosis_json", "diagnosis_csv",
        "route_contamination_csv", "teleport_source_csv", "short_edge_artifact_csv", "cause_summary_html",
    ]
    write_csv(csv_path, rows, fields)
    selected = max(rows, key=balanced_congestion_selection_key) if rows else {}
    if selected.get("profile"):
        build_b0_demand(profile=str(selected["profile"]))
        build_manifest()
    payload = {
        "schema": "expanded_v7_balanced_congestion_sweep.v1",
        "generated_at": utc_now(),
        "profiles": profiles,
        "baseline_remaining_vehicle_count": BASELINE_BALANCED_REMAINING_COUNT,
        "baseline_release_waiting_time_sec": BASELINE_RELEASE_WAITING_TIME_SEC,
        "candidate_count": len(rows),
        "selected_profile": selected.get("profile", ""),
        "selected_status": selected.get("balanced_congestion_status", ""),
        "summary_csv": rel(csv_path),
        "rows": rows,
        "errors": errors,
        "note": "PASS requires no emergency teleport, <10 background teleports, lower remaining, 10-25km/h network speed, emergency arrival/progress, 5-20km/h release speed, and >50% release waiting reduction.",
    }
    write_json(BALANCED_CONGESTION_SUMMARY, payload)
    return payload


def v6_boundary_balancer_row(profile: str, results_csv: Path, validation_payload: dict[str, Any], integrity_payload: dict[str, Any]) -> dict[str, Any]:
    b0 = latest_b0_results_row(str(results_csv))
    validation_summary = validation_payload.get("validation_summary", {}) or {}
    flow_ref = validation_payload.get("flow_plausibility_audit", {}) or {}
    flow_json = project_path(flow_ref.get("json", ""))
    flow_audit = read_json(flow_json) if flow_json.is_file() else {}
    demand_summary = read_json(DEMAND_XML.with_suffix(".summary.json")) if DEMAND_XML.with_suffix(".summary.json").is_file() else {}
    speed_summary = validation_summary.get("speed", {}) or {}
    edge_speed_summary = validation_summary.get("edge_speed", {}) or {}
    route_error_count = safe_int(b0.get("route_error_count"))
    sumo_exit_code = safe_int(b0.get("sumo_exit_code"))
    emergency_teleport = b0.get("emergency_teleport", "")
    background_teleported = safe_int(b0.get("background_teleported"))
    remaining = safe_int(b0.get("remaining_vehicle_count"))
    hard_status = "PASS" if (
        sumo_exit_code == 0
        and route_error_count == 0
        and emergency_teleport == "False"
        and background_teleported == 0
        and validation_summary.get("map_status") == "PASS"
        and validation_summary.get("lane_status") == "PASS"
        and (integrity_payload.get("status") in {"PASS", "WARN"})
    ) else "FAIL"
    return {
        "profile": profile,
        "run_id": b0.get("run_id", ""),
        "results_csv": rel(results_csv),
        "run_dir": b0.get("run_dir", ""),
        "hard_status": hard_status,
        "sumo_exit_code": sumo_exit_code,
        "route_error_count": route_error_count,
        "emergency_arrived": b0.get("emergency_arrived", ""),
        "emergency_teleport": emergency_teleport,
        "background_teleported": background_teleported,
        "remaining_vehicle_count": remaining,
        "network_avg_speed_kmh": safe_float(b0.get("network_avg_speed_kmh")),
        "map_status": validation_summary.get("map_status", ""),
        "lane_status": validation_summary.get("lane_status", ""),
        "demand_status": validation_summary.get("demand_status", ""),
        "speed_status": validation_summary.get("speed_status", ""),
        "edge_speed_status": validation_summary.get("edge_speed_status", ""),
        "speed_mae_kmh": safe_float(speed_summary.get("speed_mae_kmh")),
        "edge_speed_mae_kmh": safe_float(edge_speed_summary.get("edge_speed_mae_kmh")),
        "grouped_status": flow_audit.get("grouped_status", ""),
        "grouped_stop_or_missing_count": safe_int(flow_audit.get("grouped_stop_or_missing_count")),
        "grouped_free_flow_count": safe_int(flow_audit.get("grouped_free_flow_count")),
        "grouped_non_extreme_congestion_count": safe_int(flow_audit.get("non_extreme_congestion_count")),
        "raw_stop_or_missing_count": safe_int(flow_audit.get("raw_stop_or_missing_count")),
        "raw_free_flow_count": safe_int(flow_audit.get("raw_free_flow_count")),
        "mean_generated_recall": safe_float(demand_summary.get("mean_generated_recall")),
        "min_generated_recall": safe_float(demand_summary.get("min_generated_recall")),
        "max_generated_recall": safe_float(demand_summary.get("max_generated_recall")),
        "terminal_sink_extension_v3_applied_count": safe_int(demand_summary.get("terminal_sink_extension_v3_applied_count")),
        "terminal_sink_extension_v3_diverted_count": safe_int(demand_summary.get("terminal_sink_extension_v3_diverted_count")),
        "release_depart_gap_enabled": demand_summary.get("release_depart_gap_enabled", False),
        "free_segment_feeder_enabled": demand_summary.get("free_segment_feeder_enabled", False),
        "mapwide_route_template_count": safe_int((demand_summary.get("mapwide_background") or {}).get("route_template_count")),
        "mapwide_max_template_vehicle_count": safe_int((demand_summary.get("mapwide_background") or {}).get("max_template_vehicle_count")),
        "validation_summary_json": validation_summary.get("outputs", {}).get("summary_json", ""),
        "flow_plausibility_json": flow_ref.get("json", ""),
        "network_integrity_status": integrity_payload.get("status", ""),
        "network_integrity_summary_json": integrity_payload.get("summary_json", ""),
    }


def v6_boundary_balancer_selection_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("hard_status") == "PASS",
        -safe_int(row.get("grouped_stop_or_missing_count")),
        -safe_int(row.get("grouped_free_flow_count")),
        safe_int(row.get("remaining_vehicle_count")) <= 746,
        -safe_float(row.get("speed_mae_kmh")),
        -safe_int(row.get("remaining_vehicle_count")),
        safe_float(row.get("mean_generated_recall")),
    )


def run_v6_boundary_balancer_sweep(profiles: list[str] | None = None) -> dict[str, Any]:
    profiles = profiles or V6_BOUNDARY_BALANCER_PROFILES
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for profile in profiles:
        try:
            build_b0_demand(profile=profile)
            build_manifest()
            run_b0(output_prefix=V6_BOUNDARY_BALANCER_PREFIX)
            latest = read_json(METRICS_ROOT / V6_BOUNDARY_BALANCER_PREFIX / "latest.json")
            results_csv = project_path(latest.get("results_csv", ""))
            validation_payload = validate_expanded(results_csv=str(results_csv))
            integrity_payload = network_integrity_audit()
            rows.append(v6_boundary_balancer_row(profile, results_csv, validation_payload, integrity_payload))
        except Exception as exc:  # noqa: BLE001
            errors.append({"profile": profile, "error": str(exc)[-2000:]})
    csv_path = METRICS_ROOT / V6_BOUNDARY_BALANCER_PREFIX / "v6_boundary_balancer_sweep_summary.csv"
    fields = [
        "profile", "run_id", "results_csv", "run_dir", "hard_status", "sumo_exit_code",
        "route_error_count", "emergency_arrived", "emergency_teleport", "background_teleported",
        "remaining_vehicle_count", "network_avg_speed_kmh", "map_status", "lane_status",
        "demand_status", "speed_status", "edge_speed_status", "speed_mae_kmh",
        "edge_speed_mae_kmh", "grouped_status", "grouped_stop_or_missing_count",
        "grouped_free_flow_count", "grouped_non_extreme_congestion_count",
        "raw_stop_or_missing_count", "raw_free_flow_count", "mean_generated_recall",
        "min_generated_recall", "max_generated_recall",
        "terminal_sink_extension_v3_applied_count", "terminal_sink_extension_v3_diverted_count",
        "release_depart_gap_enabled", "free_segment_feeder_enabled", "mapwide_route_template_count",
        "mapwide_max_template_vehicle_count", "validation_summary_json", "flow_plausibility_json",
        "network_integrity_status", "network_integrity_summary_json",
    ]
    write_csv(csv_path, rows, fields)
    selected = max(rows, key=v6_boundary_balancer_selection_key) if rows else {}
    if selected.get("profile"):
        build_b0_demand(profile=str(selected["profile"]))
        build_manifest()
    payload = {
        "schema": "expanded_v7_v6_boundary_balancer_sweep.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(REFERENCE_CSV),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "profiles": profiles,
        "baseline_lane_drop_fixed": {
            "grouped_stop_or_missing_count": 8,
            "grouped_free_flow_count": 2,
            "speed_mae_kmh": 11.38,
            "remaining_vehicle_count": 746,
        },
        "candidate_count": len(rows),
        "selected_profile": selected.get("profile", ""),
        "selected_run_id": selected.get("run_id", ""),
        "selected_grouped_stop_or_missing_count": selected.get("grouped_stop_or_missing_count", ""),
        "selected_grouped_free_flow_count": selected.get("grouped_free_flow_count", ""),
        "selected_speed_mae_kmh": selected.get("speed_mae_kmh", ""),
        "selected_remaining_vehicle_count": selected.get("remaining_vehicle_count", ""),
        "summary_csv": rel(csv_path),
        "rows": rows,
        "errors": errors,
        "selection_policy": "Hard gates first, then grouped stop/free reduction, speed MAE, remaining vehicles, generated demand recall.",
        "note_ko": "완벽한 edge 1:1 수요 recall보다 grouped segment speed가 5~35km/h 정체류 안에 들어오는지를 우선합니다.",
    }
    write_json(V6_BOUNDARY_BALANCER_SUMMARY, payload)
    return payload


def run_plausibility_first_check(profile: str = "balanced_congestion_v7_plausibility_first") -> dict[str, Any]:
    build_b0_demand(profile=profile)
    build_manifest()
    run_summary = run_b0(output_prefix=PLAUSIBILITY_FIRST_PREFIX)
    latest = read_json(METRICS_ROOT / PLAUSIBILITY_FIRST_PREFIX / "latest.json")
    results_csv = project_path(latest.get("results_csv", ""))
    validation_payload = validate_expanded(results_csv=str(results_csv))
    integrity_payload = network_integrity_audit()
    row = v6_boundary_balancer_row(profile, results_csv, validation_payload, integrity_payload)
    payload = {
        "schema": "expanded_v7_plausibility_first_check.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(REFERENCE_CSV),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "profile": profile,
        "run_summary": run_summary,
        "row": row,
        "acceptance_goal": {
            "primary": "Remove implausible grouped stop/free states, not maximize generated demand recall.",
            "stop_speed_threshold_kmh": FLOW_STOP_SPEED_KMH,
            "free_flow_threshold_kmh": FLOW_FREE_SPEED_KMH,
            "target_grouped_stop_or_missing_count": 0,
            "target_grouped_free_flow_count": 0,
            "generated_demand_recall": "report_only",
        },
        "status": "PASS" if row.get("grouped_stop_or_missing_count") == 0 and row.get("grouped_free_flow_count") == 0 else "FAIL",
        "summary_csv": "",
        "note_ko": "교통량 100% 구현보다 말이 안 되는 정지류/자유류 제거를 우선하는 단일 후보 검증입니다.",
    }
    write_json(PLAUSIBILITY_FIRST_SUMMARY, payload)
    return payload


def safe_float(value: Any, default: float = 0.0) -> float:
    if value in {"", None}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    if value in {"", None}:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def fmt(value: Any, digits: int = 2) -> str:
    if value in {"", None}:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def status_class(status: str) -> str:
    value = (status or "").upper()
    if value == "PASS":
        return "pass"
    if value == "WARN" or value == "WARNING" or value == "PASS_WITH_REMAINING_BACKGROUND":
        return "warn"
    if value == "FAIL":
        return "fail"
    return "info"


def latest_validation_summary() -> tuple[dict[str, Any], Path]:
    latest_path = METRICS_ROOT / VALIDATION_PREFIX / "latest.json"
    if not latest_path.is_file():
        return {}, Path()
    latest = read_json(latest_path)
    summary_json = project_path(latest.get("summary_json", ""))
    if not summary_json.is_file():
        return {}, Path()
    return read_json(summary_json), summary_json


def latest_b0_row() -> dict[str, str]:
    latest_path = METRICS_ROOT / RUN_PREFIX / "latest.json"
    if not latest_path.is_file():
        return {}
    latest = read_json(latest_path)
    results_csv = project_path(latest.get("results_csv", ""))
    if not results_csv.is_file():
        return {}
    rows = read_csv(results_csv)
    return rows[0] if rows else {}


def group_rows(rows: list[dict[str, str]], key: str) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        result.setdefault(row.get(key, ""), []).append(row)
    return result


def direction_recall_summary(
    lane_rows: list[dict[str, str]],
    demand_rows: list[dict[str, str]],
    speed_rows: list[dict[str, str]],
    edge_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    result = []
    for direction in ["upbound", "downbound"]:
        lanes = [row for row in lane_rows if row.get("direction") == direction]
        demands = [row for row in demand_rows if row.get("direction") == direction]
        speeds = [row for row in speed_rows if row.get("direction") == direction]
        edges = [row for row in edge_rows if row.get("direction") == direction]
        speed_errors = [safe_float(row.get("speed_error_kmh")) for row in speeds if row.get("speed_error_kmh") not in {"", None}]
        abs_speed_errors = [abs(value) for value in speed_errors]
        edge_errors = [safe_float(row.get("speed_error_kmh")) for row in edges if row.get("speed_error_kmh") not in {"", None}]
        demand_recalls = [safe_float(row.get("scaled_recall")) for row in demands if row.get("scaled_recall") not in {"", None}]
        geh_statuses = [row.get("geh_status", "") for row in demands]
        over_open_segments = sum(1 for value in speed_errors if value > 8.0)
        over_congested_segments = sum(1 for value in speed_errors if value < -8.0)
        result.append({
            "direction": direction,
            "lane_pass": sum(1 for row in lanes if row.get("status") == "PASS"),
            "lane_warn": sum(1 for row in lanes if row.get("status") == "WARN"),
            "lane_fail": sum(1 for row in lanes if row.get("status") == "FAIL"),
            "demand_median_recall": median(demand_recalls),
            "demand_pass_warn_ratio": (sum(1 for status in geh_statuses if status in {"PASS", "WARN"}) / len(geh_statuses)) if geh_statuses else 0.0,
            "speed_mae_kmh": mean(abs_speed_errors),
            "speed_mean_error_kmh": mean(speed_errors),
            "simulated_speed_kmh": mean([safe_float(row.get("simulated_speed_kmh")) for row in speeds]),
            "reference_speed_kmh": mean([safe_float(row.get("reference_speed_kmh")) for row in speeds]),
            "over_open_segments": over_open_segments,
            "over_congested_segments": over_congested_segments,
            "edge_over_open_count": sum(1 for value in edge_errors if value > 8.0),
            "edge_over_congested_count": sum(1 for value in edge_errors if value < -8.0),
            "edge_fail_count": sum(1 for row in edges if row.get("status") == "FAIL"),
            "edge_count": len(edges),
        })
    return result


def count_vehicle_prefixes_from_fcd(fcd_xml: Path) -> dict[str, int]:
    if not fcd_xml.is_file():
        return {"sideflow_seen": 0, "main_seen": 0, "emergency_seen": 0}
    seen_sideflow: set[str] = set()
    seen_main: set[str] = set()
    seen_emergency: set[str] = set()
    try:
        for _event, elem in ET.iterparse(fcd_xml, events=("end",)):
            if elem.tag == "vehicle":
                vehicle_id = elem.get("id", "")
                if vehicle_id.startswith("expanded_v7_sideflow_"):
                    seen_sideflow.add(vehicle_id)
                elif vehicle_id.startswith("expanded_v7_ref_"):
                    seen_main.add(vehicle_id)
                elif vehicle_id.startswith("emergency_"):
                    seen_emergency.add(vehicle_id)
            elem.clear()
    except ET.ParseError:
        return {"sideflow_seen": len(seen_sideflow), "main_seen": len(seen_main), "emergency_seen": len(seen_emergency), "parse_error": 1}
    return {"sideflow_seen": len(seen_sideflow), "main_seen": len(seen_main), "emergency_seen": len(seen_emergency)}


def count_vehicle_prefixes_from_tripinfo(tripinfo_xml: Path) -> dict[str, int]:
    if not tripinfo_xml.is_file():
        return {"sideflow_arrived": 0, "main_arrived": 0, "emergency_arrived": 0}
    sideflow = 0
    main = 0
    emergency = 0
    try:
        for _event, elem in ET.iterparse(tripinfo_xml, events=("end",)):
            if elem.tag == "tripinfo":
                vehicle_id = elem.get("id", "")
                if vehicle_id.startswith("expanded_v7_sideflow_"):
                    sideflow += 1
                elif vehicle_id.startswith("expanded_v7_ref_"):
                    main += 1
                elif vehicle_id.startswith("emergency_"):
                    emergency += 1
            elem.clear()
    except ET.ParseError:
        return {"sideflow_arrived": sideflow, "main_arrived": main, "emergency_arrived": emergency, "parse_error": 1}
    return {"sideflow_arrived": sideflow, "main_arrived": main, "emergency_arrived": emergency}


def edge_feature_lookup() -> dict[str, dict[str, Any]]:
    if not EXPANDED_EDGES_GEOJSON.is_file():
        return {}
    payload = read_json(EXPANDED_EDGES_GEOJSON)
    lookup = {}
    for feature in payload.get("features", []):
        props = feature.get("properties", {})
        edge_id = props.get("edge_id")
        if edge_id:
            lookup[edge_id] = feature
    return lookup


def polyline_points(feature: dict[str, Any]) -> list[tuple[float, float]]:
    geometry = feature.get("geometry", {})
    if geometry.get("type") != "LineString":
        return []
    return [(float(lon), float(lat)) for lon, lat in geometry.get("coordinates", [])]


def build_validation_svg(edge_rows: list[dict[str, str]], sideflow_rows: list[dict[str, str]], accepted_route: dict[str, str]) -> str:
    features = edge_feature_lookup()
    selected_edges = {row.get("edge_id", "") for row in edge_rows if row.get("status") == "FAIL"}
    selected_edges.update(edge for edge in accepted_route.get("route_edges", "").split() if edge)
    for row in sideflow_rows[:80]:
        selected_edges.add(row.get("source_edge", ""))
        selected_edges.add(row.get("sink_edge", ""))
    coords: list[tuple[float, float]] = []
    for edge_id in selected_edges:
        feature = features.get(edge_id)
        if feature:
            coords.extend(polyline_points(feature))
    if not coords:
        return "<svg class='map-svg' viewBox='0 0 100 40'><text x='4' y='22'>No geometry available</text></svg>"
    min_lon = min(lon for lon, _lat in coords)
    max_lon = max(lon for lon, _lat in coords)
    min_lat = min(lat for _lon, lat in coords)
    max_lat = max(lat for _lon, lat in coords)
    pad_lon = max((max_lon - min_lon) * 0.06, 0.0001)
    pad_lat = max((max_lat - min_lat) * 0.08, 0.0001)
    min_lon -= pad_lon
    max_lon += pad_lon
    min_lat -= pad_lat
    max_lat += pad_lat
    width = 1100.0
    height = 560.0

    def project(point: tuple[float, float]) -> tuple[float, float]:
        lon, lat = point
        x = (lon - min_lon) / max(max_lon - min_lon, 1e-9) * width
        y = height - (lat - min_lat) / max(max_lat - min_lat, 1e-9) * height
        return x, y

    def path_for(edge_id: str) -> str:
        feature = features.get(edge_id)
        if not feature:
            return ""
        points = [project(point) for point in polyline_points(feature)]
        if len(points) < 2:
            return ""
        first = points[0]
        rest = " ".join(f"L{x:.1f},{y:.1f}" for x, y in points[1:])
        return f"M{first[0]:.1f},{first[1]:.1f} {rest}"

    route_paths = []
    for edge_id in accepted_route.get("route_edges", "").split():
        path_data = path_for(edge_id)
        if path_data:
            route_paths.append(f"<path d='{path_data}' class='route-line' />")
    edge_paths = []
    for row in edge_rows:
        edge_id = row.get("edge_id", "")
        path_data = path_for(edge_id)
        if not path_data:
            continue
        error = safe_float(row.get("speed_error_kmh"))
        css = "edge-ok"
        if error > 8:
            css = "edge-open"
        elif error < -8:
            css = "edge-slow"
        elif row.get("status") == "WARN":
            css = "edge-warn"
        edge_paths.append(f"<path d='{path_data}' class='{css}'><title>{esc(edge_id)} {fmt(error)} km/h</title></path>")
    side_lines = []
    seen_side_pairs: set[tuple[str, str]] = set()
    for row in sideflow_rows:
        pair = (row.get("source_edge", ""), row.get("sink_edge", ""))
        if pair in seen_side_pairs:
            continue
        seen_side_pairs.add(pair)
        source = features.get(row.get("source_edge", ""))
        sink = features.get(row.get("sink_edge", ""))
        if not source or not sink:
            continue
        src_points = polyline_points(source)
        sink_points = polyline_points(sink)
        if not src_points or not sink_points:
            continue
        sx, sy = project(src_points[len(src_points) // 2])
        tx, ty = project(sink_points[len(sink_points) // 2])
        side_lines.append(f"<line x1='{sx:.1f}' y1='{sy:.1f}' x2='{tx:.1f}' y2='{ty:.1f}' class='side-line'><title>{esc(row.get('source_edge'))} to {esc(row.get('sink_edge'))}</title></line>")
    return (
        f"<svg class='map-svg' viewBox='0 0 {width:.0f} {height:.0f}' role='img' aria-label='Expanded V7 validation map'>"
        "<rect width='100%' height='100%' fill='#f8fafc'/>"
        + "".join(edge_paths)
        + "".join(side_lines)
        + "".join(route_paths)
        + "<g class='legend' transform='translate(18 18)'>"
        "<rect x='0' y='0' width='250' height='126' rx='6' fill='rgba(255,255,255,.92)' stroke='#cbd5e1'/>"
        "<line x1='16' y1='24' x2='56' y2='24' class='route-line'/><text x='68' y='29'>Firetruck route</text>"
        "<line x1='16' y1='50' x2='56' y2='50' class='edge-slow'/><text x='68' y='55'>Over-congested</text>"
        "<line x1='16' y1='76' x2='56' y2='76' class='edge-open'/><text x='68' y='81'>Over-open</text>"
        "<line x1='16' y1='102' x2='56' y2='102' class='side-line'/><text x='68' y='107'>Side-flow branch</text>"
        "</g></svg>"
    )


def build_validation_dashboard_payload(seed: dict[str, Any]) -> dict[str, Any]:
    summary = seed.get("validation_summary")
    summary_json = Path()
    if summary:
        summary_json = project_path(summary.get("outputs", {}).get("summary_json", ""))
    else:
        summary, summary_json = latest_validation_summary()
    if not summary:
        return {"summary": {}, "available": False}
    output_dir = summary_json.parent
    outputs = summary.get("outputs", {})
    lane_rows = read_csv(project_path(outputs.get("lane_csv", ""))) if outputs.get("lane_csv") else []
    demand_rows = read_csv(project_path(outputs.get("demand_csv", ""))) if outputs.get("demand_csv") else []
    speed_rows = read_csv(project_path(outputs.get("speed_csv", ""))) if outputs.get("speed_csv") else []
    edge_rows = read_csv(project_path(outputs.get("edge_speed_csv", ""))) if outputs.get("edge_speed_csv") else []
    sideflow_rows = read_csv(SIDEFLOW_SUMMARY_CSV) if SIDEFLOW_SUMMARY_CSV.is_file() else []
    demand_summary = read_json(DEMAND_XML.with_suffix(".summary.json")) if DEMAND_XML.with_suffix(".summary.json").is_file() else {}
    b0_row = latest_b0_row()
    run_dir = project_path(b0_row.get("run_dir", "")) if b0_row else Path()
    fcd_counts = count_vehicle_prefixes_from_fcd(run_dir / "fcd.xml") if run_dir else {}
    trip_counts = count_vehicle_prefixes_from_tripinfo(run_dir / "tripinfo.xml") if run_dir else {}
    accepted_rows = read_csv(ACCEPTED_ROUTES_CSV) if ACCEPTED_ROUTES_CSV.is_file() else []
    accepted_route = accepted_rows[0] if accepted_rows else {}
    directional = direction_recall_summary(lane_rows, demand_rows, speed_rows, edge_rows)
    lane = summary.get("lane", {})
    lane_certainty = "HIGH" if safe_float(lane.get("strict_lane_recall")) >= 0.9 else "PARTIAL"
    fail_edges = sorted(edge_rows, key=lambda row: safe_float(row.get("abs_speed_error_kmh")), reverse=True)[:40]
    return {
        "available": True,
        "summary": summary,
        "summary_json": rel(summary_json),
        "output_dir": rel(output_dir),
        "lane_rows": lane_rows,
        "demand_rows": demand_rows,
        "speed_rows": speed_rows,
        "edge_rows": edge_rows,
        "fail_edges": fail_edges,
        "sideflow_rows": sideflow_rows,
        "sideflow": seed.get("sideflow") or summarize_sideflow(),
        "demand_summary": demand_summary,
        "b0_row": b0_row,
        "run_dir": rel(run_dir) if run_dir else "",
        "fcd_counts": fcd_counts,
        "trip_counts": trip_counts,
        "accepted_route": accepted_route,
        "directional": directional,
        "lane_certainty": lane_certainty,
        "svg_map": build_validation_svg(edge_rows, sideflow_rows, accepted_route),
        "report_only_recommendations": seed.get("report_only_recommendations") or {
            "json": rel(output_dir / "expanded_v7_report_only_recommendations.json"),
            "csv": rel(output_dir / "expanded_v7_report_only_recommendations.csv"),
        },
    }


def write_map_review_html(path: Path, payload: dict[str, Any]) -> None:
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Expanded V7 Map</title></head><body>
<h1>Expanded V7 Map</h1><pre>{json.dumps(payload, ensure_ascii=False, indent=2)}</pre></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def write_route_review_html(path: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    data = {"summary": summary, "routes": rows[:40]}
    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>Expanded V7 Firetruck Route Review</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px}} .card{{border:1px solid #ddd;padding:12px;margin:8px 0;border-radius:6px}}</style></head><body>
<h1>Expanded V7 Firetruck Route Review</h1>
<p>Save acceptance JSON to <code>{rel(ROUTE_ACCEPTANCE_JSON)}</code> before running B0.</p>
<textarea style="width:100%;height:90px">{json.dumps({"accepted_candidate_route_id": summary["recommended_candidate_route_id"]}, ensure_ascii=False, indent=2)}</textarea>
<pre>{json.dumps(data, ensure_ascii=False, indent=2)}</pre></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def write_b0_review_html(path: Path, payload: dict[str, Any]) -> None:
    html = f"<!doctype html><html><head><meta charset='utf-8'><title>Expanded V7 B0</title></head><body><h1>Expanded V7 B0</h1><pre>{json.dumps(payload, ensure_ascii=False, indent=2)}</pre></body></html>"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def write_validation_review_html(path: Path, payload: dict[str, Any]) -> None:
    data = build_validation_dashboard_payload(payload)
    if not data.get("available"):
        html_text = f"<!doctype html><html><head><meta charset='utf-8'><title>Expanded V7 Validation</title></head><body><h1>Expanded V7 Validation</h1><pre>{json.dumps(payload, ensure_ascii=False, indent=2)}</pre></body></html>"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_text, encoding="utf-8")
        return
    summary = data["summary"]
    b0 = data["b0_row"]
    lane = summary.get("lane", {})
    demand = summary.get("demand", {})
    speed = summary.get("speed", {})
    edge_speed = summary.get("edge_speed", {})
    demand_summary = data.get("demand_summary", {})
    sideflow = data.get("sideflow", {})
    fcd_counts = data.get("fcd_counts", {})
    trip_counts = data.get("trip_counts", {})
    sideflow_total = safe_int(sideflow.get("sideflow_vehicle_count"))
    main_total = safe_int(demand_summary.get("vehicle_count"))
    sideflow_seen = safe_int(fcd_counts.get("sideflow_seen"))
    sideflow_arrived = safe_int(trip_counts.get("sideflow_arrived"))
    sideflow_ratio = (sideflow_total / main_total) if main_total else 0.0
    generated_recall = {
        "mean": demand_summary.get("mean_generated_recall", ""),
        "min": demand_summary.get("min_generated_recall", ""),
        "max": demand_summary.get("max_generated_recall", ""),
    }
    lane_cards = "".join(
        f"<tr><td>{esc(row.get('segment_id'))}</td><td>{esc(row.get('direction'))}</td><td>{fmt(row.get('reference_lanes'), 0)}</td><td>{esc(row.get('matched_lane_counts'))}</td><td>{fmt(row.get('mode_lane_count'), 0)}</td><td><span class='pill {status_class(row.get('status', ''))}'>{esc(row.get('status'))}</span></td></tr>"
        for row in data["lane_rows"]
    )
    demand_cards = "".join(
        f"<tr><td>{esc(row.get('segment_id'))}</td><td>{esc(row.get('direction'))}</td><td>{fmt(row.get('scaled_reference_count'), 0)}</td><td>{fmt(row.get('observed_count'), 0)}</td><td>{fmt(row.get('scaled_recall'), 2)}</td><td>{fmt(row.get('geh'), 2)}</td><td><span class='pill {status_class(row.get('status', ''))}'>{esc(row.get('status'))}</span></td></tr>"
        for row in data["demand_rows"]
    )
    speed_cards = "".join(
        f"<tr><td>{esc(row.get('segment_id'))}</td><td>{esc(row.get('direction'))}</td><td>{fmt(row.get('reference_speed_kmh'), 1)}</td><td>{fmt(row.get('simulated_speed_kmh'), 1)}</td><td>{fmt(row.get('speed_error_kmh'), 1)}</td><td>{fmt(row.get('travel_time_error_s'), 1)}</td><td><span class='pill {status_class(row.get('status', ''))}'>{esc(row.get('status'))}</span></td></tr>"
        for row in data["speed_rows"]
    )
    edge_cards = "".join(
        f"<tr><td>{esc(row.get('segment_id'))}</td><td>{esc(row.get('direction'))}</td><td>{esc(row.get('edge_id'))}</td><td>{fmt(row.get('reference_segment_speed_kmh'), 1)}</td><td>{fmt(row.get('simulated_edge_speed_kmh'), 1)}</td><td>{fmt(row.get('speed_error_kmh'), 1)}</td><td>{esc(row.get('anomaly_type'))}</td><td><span class='pill {status_class(row.get('status', ''))}'>{esc(row.get('status'))}</span></td></tr>"
        for row in data["fail_edges"]
    )
    direction_cards = "".join(
        f"""
        <section class="direction-card">
          <div class="direction-head">
            <h3>{esc(row['direction'])}</h3>
            <span class="pill {status_class('FAIL' if row['speed_mae_kmh'] > 8 or row['demand_pass_warn_ratio'] < .8 else 'PASS')}">Recall {'FAIL' if row['speed_mae_kmh'] > 8 or row['demand_pass_warn_ratio'] < .8 else 'PASS'}</span>
          </div>
          <div class="mini-grid">
            <div><b>{fmt(row['demand_median_recall'], 2)}</b><span>demand median recall</span></div>
            <div><b>{fmt(row['speed_mae_kmh'], 1)}</b><span>speed MAE km/h</span></div>
            <div><b>{fmt(row['speed_mean_error_kmh'], 1)}</b><span>mean speed error</span></div>
            <div><b>{row['over_congested_segments']} / {row['over_open_segments']}</b><span>over-congested / over-open segments</span></div>
          </div>
          <p>Edge speed: {row['edge_over_congested_count']} over-congested, {row['edge_over_open_count']} over-open, {row['edge_fail_count']} FAIL of {row['edge_count']} edges.</p>
        </section>
        """
        for row in data["directional"]
    )
    recommendation_json = data.get("report_only_recommendations", {}).get("json", "")
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Expanded V7 Validation Dashboard</title>
  <style>
    :root {{
      --ink:#17202a; --muted:#5f6b7a; --line:#d8dee8; --panel:#ffffff; --bg:#f5f7fb;
      --pass:#16794a; --pass-bg:#e7f6ed; --warn:#966b00; --warn-bg:#fff3c4; --fail:#b42318; --fail-bg:#fde7e5; --info:#315a9f; --info-bg:#e8f0ff;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:28px 32px 18px; background:#fff; border-bottom:1px solid var(--line); }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    h2 {{ margin:30px 0 12px; font-size:20px; }}
    h3 {{ margin:0; font-size:17px; }}
    p {{ color:var(--muted); line-height:1.45; }}
    main {{ padding:24px 32px 40px; max-width:1500px; margin:0 auto; }}
    .grid {{ display:grid; gap:14px; grid-template-columns:repeat(6,minmax(0,1fr)); }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; min-height:118px; }}
    .card.span2 {{ grid-column:span 2; }}
    .card.span3 {{ grid-column:span 3; }}
    .card.span6 {{ grid-column:span 6; }}
    .metric {{ font-size:28px; font-weight:720; margin:8px 0 4px; }}
    .label {{ color:var(--muted); font-size:13px; }}
    .pill {{ display:inline-flex; align-items:center; border-radius:999px; padding:4px 9px; font-size:12px; font-weight:700; }}
    .pass {{ color:var(--pass); background:var(--pass-bg); }}
    .warn {{ color:var(--warn); background:var(--warn-bg); }}
    .fail {{ color:var(--fail); background:var(--fail-bg); }}
    .info {{ color:var(--info); background:var(--info-bg); }}
    .direction-wrap {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
    .direction-card {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .direction-head {{ display:flex; justify-content:space-between; gap:10px; align-items:center; }}
    .mini-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:14px 0; }}
    .mini-grid div {{ border:1px solid var(--line); border-radius:6px; padding:10px; background:#fafbfc; }}
    .mini-grid b {{ display:block; font-size:20px; }}
    .mini-grid span {{ display:block; color:var(--muted); font-size:12px; margin-top:4px; }}
    .map-panel {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:14px; overflow:auto; }}
    .map-svg {{ width:100%; min-width:860px; height:auto; border-radius:6px; border:1px solid var(--line); }}
    .route-line {{ fill:none; stroke:#111827; stroke-width:5; stroke-linecap:round; stroke-linejoin:round; opacity:.85; }}
    .edge-ok {{ fill:none; stroke:#22a06b; stroke-width:2; opacity:.42; }}
    .edge-warn {{ fill:none; stroke:#d29a00; stroke-width:3; opacity:.72; }}
    .edge-slow {{ fill:none; stroke:#2563eb; stroke-width:3.5; opacity:.75; }}
    .edge-open {{ fill:none; stroke:#dc2626; stroke-width:3.5; opacity:.75; }}
    .side-line {{ stroke:#7c3aed; stroke-width:1.6; stroke-dasharray:4 4; opacity:.55; }}
    .legend text {{ font-size:13px; fill:#334155; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
    th,td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; font-size:13px; vertical-align:top; }}
    th {{ background:#eef2f7; font-size:12px; color:#334155; position:sticky; top:0; }}
    .table-wrap {{ max-height:430px; overflow:auto; border-radius:8px; }}
    code {{ background:#eef2f7; padding:2px 5px; border-radius:4px; }}
    @media (max-width:1000px) {{
      .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
      .card.span2,.card.span3,.card.span6 {{ grid-column:span 2; }}
      .direction-wrap {{ grid-template-columns:1fr; }}
      .mini-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
      header, main {{ padding-left:16px; padding-right:16px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Expanded V7 Validation Dashboard</h1>
    <p>B0 firetruck baseline recall for map, lanes, demand, side-flow insertion, speed, edge speed, and bidirectional congestion. Run <code>{esc(summary.get('run_id'))}</code>.</p>
  </header>
  <main>
    <section class="grid">
      <article class="card span2"><span class="pill {status_class(summary.get('overall_status',''))}">{esc(summary.get('overall_status'))}</span><div class="metric">Overall</div><div class="label">Map/lane pass, demand and speed still fail.</div></article>
      <article class="card span2"><span class="pill {status_class(summary.get('lane_status',''))}">{esc(summary.get('lane_status'))}</span><div class="metric">{fmt(lane.get('lane_recall'), 2)}</div><div class="label">Lane recall gate. Strict certainty: <b>{esc(data['lane_certainty'])}</b>, strict {fmt(lane.get('strict_lane_recall'), 2)}.</div></article>
      <article class="card span2"><span class="pill {status_class(summary.get('demand_status',''))}">{esc(summary.get('demand_status'))}</span><div class="metric">{fmt(demand.get('median_scaled_recall'), 2)}</div><div class="label">Observed median demand recall. Generated demand recall mean/min/max: {fmt(generated_recall['mean'], 2)} / {fmt(generated_recall['min'], 2)} / {fmt(generated_recall['max'], 2)}.</div></article>
      <article class="card span2"><span class="pill {status_class(summary.get('speed_status',''))}">{esc(summary.get('speed_status'))}</span><div class="metric">{fmt(speed.get('speed_mae_kmh'), 1)}</div><div class="label">Segment speed MAE km/h.</div></article>
      <article class="card span2"><span class="pill {status_class(summary.get('edge_speed_status',''))}">{esc(summary.get('edge_speed_status'))}</span><div class="metric">{fmt(edge_speed.get('edge_speed_mae_kmh'), 1)}</div><div class="label">Edge speed MAE km/h, over-open edges {fmt(edge_speed.get('over_open_edge_count'), 0)}.</div></article>
      <article class="card span2"><span class="pill warn">Side-flow</span><div class="metric">{sideflow_total}</div><div class="label">Generated side-flow vehicles, ratio {fmt(sideflow_ratio * 100, 1)}%. Arrived {sideflow_arrived}; FCD seen after 600s {sideflow_seen}.</div></article>
    </section>

    <h2>Visual Map</h2>
    <section class="map-panel">
      {data['svg_map']}
    </section>

    <h2>Bidirectional Congestion Recall</h2>
    <section class="direction-wrap">
      {direction_cards}
    </section>

    <h2>B0 Run Evidence</h2>
    <section class="grid">
      <article class="card span2"><div class="metric">{esc(b0.get('sumo_exit_code'))}</div><div class="label">SUMO exit code</div></article>
      <article class="card span2"><div class="metric">{esc(b0.get('route_error_count'))}</div><div class="label">Route errors</div></article>
      <article class="card span2"><div class="metric">{esc(b0.get('emergency_teleport'))}</div><div class="label">Emergency teleport</div></article>
      <article class="card span3"><div class="metric">{fmt(b0.get('emergency_travel_time_sec'), 0)}s</div><div class="label">Firetruck travel time, arrived={esc(b0.get('emergency_arrived'))}, vType firetruck_emergency.</div></article>
      <article class="card span3"><div class="metric">{esc(b0.get('background_teleported'))}</div><div class="label">Background teleports. Remaining background {esc(b0.get('background_remaining_count'))}; this is why B0 final status is {esc(b0.get('final_status'))}.</div></article>
    </section>

    <h2>Lane Recall Table</h2>
    <p>Gate 기준은 PASS/WARN 허용으로 통과했지만, strict 기준은 모든 edge 차선수가 같은지 보기 때문에 S1 등 일부 구간이 WARN으로 남습니다.</p>
    <div class="table-wrap"><table><thead><tr><th>Segment</th><th>Direction</th><th>Ref lanes</th><th>Matched lane counts</th><th>Mode</th><th>Status</th></tr></thead><tbody>{lane_cards}</tbody></table></div>

    <h2>Demand Recall Table</h2>
    <p>Generated demand는 CSV screenline을 맞췄지만, 실제 observed count는 삽입 실패/잔류/병목 영향으로 낮게 측정됩니다.</p>
    <div class="table-wrap"><table><thead><tr><th>Segment</th><th>Direction</th><th>Target</th><th>Observed</th><th>Recall</th><th>GEH</th><th>Status</th></tr></thead><tbody>{demand_cards}</tbody></table></div>

    <h2>Speed Recall Table</h2>
    <div class="table-wrap"><table><thead><tr><th>Segment</th><th>Direction</th><th>Ref km/h</th><th>Sim km/h</th><th>Error</th><th>TT error s</th><th>Status</th></tr></thead><tbody>{speed_cards}</tbody></table></div>

    <h2>Worst Edge Speed Recall</h2>
    <div class="table-wrap"><table><thead><tr><th>Segment</th><th>Direction</th><th>Edge</th><th>Ref km/h</th><th>Sim km/h</th><th>Error</th><th>Anomaly</th><th>Status</th></tr></thead><tbody>{edge_cards}</tbody></table></div>

    <h2>Files</h2>
    <p>Summary: <code>{esc(data['summary_json'])}</code>. Recommendation JSON: <code>{esc(recommendation_json)}</code>. Run dir: <code>{esc(data.get('run_dir'))}</code>.</p>
  </main>
</body>
</html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def run_step(step: str, args: argparse.Namespace) -> dict[str, Any]:
    if step == "area":
        return define_expanded_area()
    if step == "net":
        return build_expanded_net(force_download=args.force_download, timeout_sec=args.timeout_sec)
    if step == "mapping":
        return build_toegye_mapping()
    if step == "lanes":
        return repair_lanes()
    if step == "tls_fix":
        severe_green = 30 if args.tls_case == "aggressive" else 24
        return fix_downstream_tls_green_split(severe_green_sec=severe_green)
    if step == "speedcap":
        return build_mainroad_speedcap_net()
    if step == "release_speedcap":
        return build_release_speedcap_net()
    if step == "downbound_metering":
        return build_downbound_metering_speedcap_net()
    if step == "overopen_metering":
        return build_overopen_metering_speedcap_net()
    if step == "route_edge_overopen_metering":
        return build_route_edge_overopen_metering_speedcap_net()
    if step == "release_junction_fixed":
        return build_release_junction_fixed_net()
    if step == "road_integrity_audit":
        return road_integrity_audit()
    if step == "make_sense_audit":
        return make_sense_network_audit(results_csv=args.results_csv)
    if step == "make_sense_candidate":
        return build_make_sense_net_candidate()
    if step == "lane_drop_fixed":
        return build_mainline_lane_drop_fixed_net()
    if step == "plausibility_overopen_speedcap":
        return build_plausibility_overopen_speedcap_net()
    if step == "route_candidates":
        return build_firetruck_route_candidates()
    if step == "apply_route":
        return apply_firetruck_route_acceptance()
    if step == "demand":
        return build_b0_demand(profile=args.demand_profile)
    if step == "manifest":
        return build_manifest()
    if step == "conservative_manifest":
        return build_conservative_manifest()
    if step == "run_b0":
        return run_b0()
    if step == "run_conservative_b0":
        return run_conservative_b0()
    if step == "validate":
        return validate_expanded(results_csv=args.results_csv)
    if step == "conservative_conflict_audit":
        return same_lane_near_conflict_audit(results_csv=args.results_csv)
    if step == "integrity_audit":
        return network_integrity_audit()
    if step == "bottleneck_diagnosis":
        return diagnose_bottleneck(results_csv=args.results_csv)
    if step == "balanced_congestion_sweep":
        return run_balanced_congestion_sweep()
    if step == "v6_boundary_balancer_sweep":
        return run_v6_boundary_balancer_sweep()
    if step == "plausibility_first_check":
        return run_plausibility_first_check(profile=args.demand_profile)
    if step == "visualize":
        payload = {
            "schema": "expanded_v7_static_review_index.v1",
            "generated_at": utc_now(),
            "map_review_html": rel(MAP_REVIEW_HTML),
            "route_review_html": rel(ROUTE_REVIEW_HTML),
            "b0_review_html": rel(B0_REVIEW_HTML),
            "validation_review_html": rel(VALIDATION_REVIEW_HTML),
            "manifest": rel(MANIFEST) if MANIFEST.is_file() else "",
            "latest_b0_json": rel(METRICS_ROOT / RUN_PREFIX / "latest.json") if (METRICS_ROOT / RUN_PREFIX / "latest.json").is_file() else "",
            "latest_validation_json": rel(METRICS_ROOT / VALIDATION_PREFIX / "latest.json") if (METRICS_ROOT / VALIDATION_PREFIX / "latest.json").is_file() else "",
        }
        write_b0_review_html(B0_REVIEW_HTML, payload)
        write_validation_review_html(VALIDATION_REVIEW_HTML, payload)
        return payload
    if step == "all_pre_acceptance":
        outputs = {}
        for item in ["area", "net", "mapping", "lanes", "tls_fix", "route_candidates"]:
            outputs[item] = run_step(item, args)
        return outputs
    raise ExpandedV7Error(f"unknown_step:{step}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expanded-v7 B0 firetruck pipeline.")
    parser.add_argument("step", choices=[
        "area", "net", "mapping", "lanes", "route_candidates", "apply_route",
        "tls_fix", "speedcap", "release_speedcap", "downbound_metering", "overopen_metering", "route_edge_overopen_metering", "release_junction_fixed", "road_integrity_audit", "make_sense_audit", "make_sense_candidate", "lane_drop_fixed", "plausibility_overopen_speedcap", "demand", "manifest", "conservative_manifest", "run_b0", "run_conservative_b0", "validate", "conservative_conflict_audit", "integrity_audit", "bottleneck_diagnosis", "balanced_congestion_sweep", "visualize", "all_pre_acceptance",
        "v6_boundary_balancer_sweep", "plausibility_first_check",
    ])
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--timeout-sec", type=int, default=240)
    parser.add_argument("--results-csv", default="auto")
    parser.add_argument("--demand-profile", choices=[
        "through_only", "through_local_25", "through_local_50", "balanced_diversion",
        "bottleneck_aware_diversion", "balanced_congestion_v2", "balanced_congestion_v2_a",
        "balanced_congestion_v2_b", "balanced_congestion_v2_c", "balanced_congestion_v3_a",
        "balanced_congestion_v3_027", "balanced_congestion_v3_tuned", "balanced_congestion_v3_up22", "balanced_congestion_v3_up20", "balanced_congestion_v3", "balanced_congestion_v3_c",
        "balanced_congestion_v3_down55", "balanced_congestion_v3_down60", "balanced_congestion_v3_down65", "balanced_congestion_v3_down75",
        "balanced_congestion_v4_smooth_release", "balanced_congestion_v5_distributed_boundary",
        "balanced_congestion_v6_boundary_fanout_only", "balanced_congestion_v6_release_gap",
        "balanced_congestion_v6_free_feeder", "balanced_congestion_v6_boundary_balancer",
        "balanced_congestion_v7_plausibility_first", "balanced_congestion_v8_stop_free_cleanup",
    ], default="balanced_congestion_v3")
    parser.add_argument("--tls-case", choices=["mild", "aggressive"], default="mild")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = run_step(args.step, args)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}")
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
