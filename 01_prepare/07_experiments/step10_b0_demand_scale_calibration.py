#!/usr/bin/env python3
"""Calibrate Step 10 B0 background demand scales with one emergency route."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import read_sumo_net  # noqa: E402


DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
DEFAULT_BASE_ROUTE = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19.rou.xml"
DEFAULT_EMERGENCY_ROUTES = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
DEFAULT_SPINE_EDGES = PROJECT_ROOT / "data_prepared/routes/corridor_spine_edges.csv"
MAPPING_CSV = PROJECT_ROOT / "data_prepared/demand/detector_to_screenline_mapping_am_imputed_a17_a19.csv"
BASE_STEP10_SUMMARY = PROJECT_ROOT / "results/metrics/b0_baseline_speed_smoke_summary.json"
SUMMARY_CSV = PROJECT_ROOT / "results/metrics/b0_demand_scale_calibration_summary.csv"
SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b0_demand_scale_calibration_summary.json"
ROAD_AXIS_CSV = PROJECT_ROOT / "results/metrics/b0_road_axis_speed_calibration.csv"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step10_b0_demand_scale_calibration.log"
STEP10_DOC = PROJECT_ROOT / "docs/Step10.md"
TARGET_SPEED_KMH = 20.0
DEFAULT_TIMEOUT_SEC = 1200
TARGET_ROAD_AXES = [
    {"road_axis_id": "세종대로", "direction": "NB", "target_speed_kmh": 16.8},
    {"road_axis_id": "서소문로", "direction": "EB", "target_speed_kmh": 14.8},
    {"road_axis_id": "남대문로", "direction": "NB", "target_speed_kmh": 16.8},
    {"road_axis_id": "소공로", "direction": "NB", "target_speed_kmh": 15.6},
    {"road_axis_id": "마른내로", "direction": "EB", "target_speed_kmh": 16.0},
    {"road_axis_id": "을지로", "direction": "EB", "target_speed_kmh": 19.6},
    {"road_axis_id": "퇴계로", "direction": "EB", "target_speed_kmh": 15.3},
    {"road_axis_id": "청계천로", "direction": "EB", "target_speed_kmh": 15.5},
    {"road_axis_id": "삼일대로", "direction": "NB", "target_speed_kmh": 22.6},
]


class CalibrationError(RuntimeError):
    """Expected calibration failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def scale_label(scale: float) -> str:
    return str(scale).replace(".", "p")


def scaled_route_path(scale: float) -> Path:
    return PROJECT_ROOT / f"data_prepared/demand/background_routes_am_imputed_a17_a19_scale_{scale_label(scale)}.rou.xml"


def run_dir(scale: float) -> Path:
    return PROJECT_ROOT / f"runs/b0_baseline_speed_smoke_scale_{scale_label(scale)}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run B0 demand scale calibration.")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--base-background-route", type=Path, default=DEFAULT_BASE_ROUTE)
    parser.add_argument("--emergency-routes", type=Path, default=DEFAULT_EMERGENCY_ROUTES)
    parser.add_argument("--route-id", default="ER_ACC_002")
    parser.add_argument("--spine-edges", type=Path, default=DEFAULT_SPINE_EDGES)
    parser.add_argument("--scales", nargs="+", type=float, default=[0.5, 0.3, 0.2])
    parser.add_argument("--time-to-teleport", type=int, default=1200)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--sampling-seed", default="step10_scale_calibration_v1")
    parser.add_argument("--merge-existing-summary", action="store_true", default=True)
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def vehicle_sort_key(vehicle: ET.Element) -> tuple[int, str]:
    vehicle_id = vehicle.get("id", "")
    suffix = vehicle_id.rsplit("_", 1)[-1]
    return (int(suffix) if suffix.isdigit() else 10**12, vehicle_id)


def deterministic_vehicle_hash(vehicle_id: str, label: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}:{vehicle_id}:{label}".encode("utf-8")).hexdigest()


def count_vehicles(path: Path) -> int:
    count = 0
    for _event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == "vehicle":
            count += 1
        elem.clear()
    return count


def write_scaled_route(base_path: Path, output_path: Path, scale: float, seed: str) -> tuple[int, int]:
    root = ET.parse(base_path).getroot()
    children = list(root)
    vehicles = [child for child in children if child.tag == "vehicle"]
    base_count = len(vehicles)
    keep_count = round(base_count * scale)
    label = scale_label(scale)
    ranked = sorted(vehicles, key=lambda vehicle: (deterministic_vehicle_hash(vehicle.get("id", ""), label, seed), vehicle_sort_key(vehicle)))
    keep_ids = {vehicle.get("id") for vehicle in ranked[:keep_count]}
    scaled_root = ET.Element(root.tag, root.attrib)
    scaled_root.append(ET.Comment(f"scaled from {rel(base_path)} scale={scale} keep_count={keep_count} base_count={base_count} sampling_seed={seed}"))
    for child in children:
        if child.tag == "vehicle" and child.get("id") not in keep_ids:
            continue
        scaled_root.append(copy.deepcopy(child))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(scaled_root).write(output_path, encoding="utf-8", xml_declaration=True)
    actual_count = count_vehicles(output_path)
    if actual_count != keep_count:
        raise CalibrationError(f"Scaled route generation count mismatch: {rel(output_path)} expected={keep_count} actual={actual_count}")
    return base_count, keep_count


def select_emergency_route(routes_csv: Path, route_id: str) -> dict[str, str]:
    rows = read_csv(routes_csv)
    matches = [row for row in rows if row.get("route_id") == route_id]
    if not matches:
        raise CalibrationError(f"Emergency route not found: {route_id}")
    row = matches[0]
    if row.get("review_status") != "PASS" or row.get("needs_manual_review") != "False":
        raise CalibrationError(f"Emergency route not stable: {route_id}")
    return row


def validate_route_edges(net_path: Path, edge_ids: list[str]) -> list[str]:
    sumo_net = read_sumo_net(net_path)
    failures = []
    for edge_id in edge_ids:
        try:
            sumo_net.getEdge(edge_id)
        except KeyError:
            failures.append(f"missing_edge:{edge_id}")
    for from_id, to_id in zip(edge_ids, edge_ids[1:], strict=False):
        if failures:
            break
        if sumo_net.getEdge(to_id) not in sumo_net.getEdge(from_id).getOutgoing():
            failures.append(f"disconnected_transition:{from_id}->{to_id}")
    return failures


def write_emergency_route_xml(path: Path, route_row: dict[str, str], vehicle_id: str) -> None:
    root = ET.Element("routes")
    vtype = ET.SubElement(
        root,
        "vType",
        {
            "id": "b0_emergency_type",
            "vClass": "emergency",
            "guiShape": "emergency",
            "color": "1,0,0",
            "speedFactor": "1.30",
            "speedDev": "0.00",
            "accel": "3.0",
            "decel": "7.5",
            "impatience": "1.0",
        },
    )
    ET.SubElement(vtype, "param", {"key": "has.bluelight.device", "value": "true"})
    ET.SubElement(root, "route", {"id": route_row["route_id"], "edges": route_row["route_edges"]})
    ET.SubElement(
        root,
        "vehicle",
        {
            "id": vehicle_id,
            "type": "b0_emergency_type",
            "route": route_row["route_id"],
            "depart": "0",
            "departLane": "best",
            "departSpeed": "max",
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_sumo_files(
    scale: float,
    net_path: Path,
    background_route: Path,
    emergency_route_xml: Path,
    time_to_teleport: int,
    collision_action: str,
) -> dict[str, Path]:
    directory = run_dir(scale)
    paths = {
        "additional": directory / "edge_data.add.xml",
        "edge_data": directory / "edgeData.xml",
        "sumocfg": directory / "scenario.sumocfg",
        "tripinfo": directory / "tripinfo.xml",
        "summary": directory / "summary.xml",
        "stdout": directory / "sumo_stdout.log",
        "stderr": directory / "sumo_stderr.log",
    }
    directory.mkdir(parents=True, exist_ok=True)
    additional = ET.Element("additional")
    ET.SubElement(
        additional,
        "edgeData",
        {
            "id": "b0_scale_edge_speed",
            "file": str(paths["edge_data"]),
            "begin": "0",
            "end": "86400",
            "freq": "86400",
            "excludeEmpty": "false",
        },
    )
    ET.ElementTree(additional).write(paths["additional"], encoding="utf-8", xml_declaration=True)
    config = ET.Element("configuration")
    input_elem = ET.SubElement(config, "input")
    ET.SubElement(input_elem, "net-file", {"value": str(net_path)})
    ET.SubElement(input_elem, "route-files", {"value": f"{background_route},{emergency_route_xml}"})
    ET.SubElement(input_elem, "additional-files", {"value": str(paths["additional"])})
    output_elem = ET.SubElement(config, "output")
    ET.SubElement(output_elem, "tripinfo-output", {"value": str(paths["tripinfo"])})
    ET.SubElement(output_elem, "summary-output", {"value": str(paths["summary"])})
    time_elem = ET.SubElement(config, "time")
    ET.SubElement(time_elem, "begin", {"value": "0"})
    processing_elem = ET.SubElement(config, "processing")
    ET.SubElement(processing_elem, "time-to-teleport", {"value": str(time_to_teleport)})
    ET.SubElement(processing_elem, "collision.action", {"value": collision_action})
    report_elem = ET.SubElement(config, "report")
    ET.SubElement(report_elem, "no-step-log", {"value": "true"})
    ET.SubElement(report_elem, "duration-log.disable", {"value": "true"})
    ET.ElementTree(config).write(paths["sumocfg"], encoding="utf-8", xml_declaration=True)
    return paths


def parse_summary_output(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    last_step = None
    max_teleports = 0
    speed_num = 0.0
    speed_den = 0.0
    for step in root.findall("step"):
        last_step = step
        max_teleports = max(max_teleports, int(float(step.get("teleports", "0"))))
        mean_speed = float(step.get("meanSpeed", "0") or 0)
        running = float(step.get("running", "0") or 0)
        if running > 0:
            speed_num += mean_speed * running
            speed_den += running
    if last_step is None:
        raise CalibrationError(f"summary-output has no steps: {rel(path)}")
    mean_speed_mps = speed_num / speed_den if speed_den else float(last_step.get("meanSpeed", "0") or 0)
    return {
        "departed_count_total": int(float(last_step.get("inserted", "0"))),
        "arrived_count_total": int(float(last_step.get("arrived", "0"))),
        "teleport_count": max_teleports,
        "sim_end_time": float(last_step.get("time", "0")),
        "network_mean_speed_kmh": mean_speed_mps * 3.6,
        "network_summary_mean_speed_mps": mean_speed_mps,
    }


def parse_tripinfo(path: Path, emergency_vehicle_id: str) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    for tripinfo in root.findall("tripinfo"):
        if tripinfo.get("id") == emergency_vehicle_id:
            return {
                "emergency_arrived": True,
                "emergency_travel_time": float(tripinfo.get("duration", "0")),
                "emergency_depart_time_observed": float(tripinfo.get("depart", "0")),
                "emergency_arrival_time": float(tripinfo.get("arrival", "0")),
            }
    return {
        "emergency_arrived": False,
        "emergency_travel_time": None,
        "emergency_depart_time_observed": None,
        "emergency_arrival_time": None,
    }


def parse_edge_data(path: Path) -> dict[str, dict[str, float]]:
    root = ET.parse(path).getroot()
    result = {}
    for edge in root.findall(".//edge"):
        edge_id = edge.get("id")
        if not edge_id:
            continue
        result[edge_id] = {
            "speed_mps": float(edge.get("speed") or 0.0),
            "entered": float(edge.get("entered") or 0.0),
            "sampled_seconds": float(edge.get("sampledSeconds") or 0.0),
        }
    return result


def weighted_speed_kmh(edge_data: dict[str, dict[str, float]], edge_ids: set[str] | None = None) -> tuple[float | None, str, float]:
    items = edge_data.items() if edge_ids is None else ((edge_id, edge_data[edge_id]) for edge_id in edge_ids if edge_id in edge_data)
    rows = [(edge_id, values) for edge_id, values in items if values.get("speed_mps", 0.0) > 0]
    if not rows:
        return None, "sampledSeconds", 0.0
    sampled_total = sum(values.get("sampled_seconds", 0.0) for _edge_id, values in rows)
    weighting = "sampledSeconds" if sampled_total > 0 else "entered"
    numerator = 0.0
    denominator = 0.0
    for _edge_id, values in rows:
        weight = values.get("sampled_seconds", 0.0) if weighting == "sampledSeconds" else values.get("entered", 0.0)
        if weight <= 0:
            continue
        numerator += values["speed_mps"] * weight
        denominator += weight
    if denominator <= 0:
        return None, weighting, 0.0
    return (numerator / denominator) * 3.6, weighting, denominator


def read_spine_edges(path: Path) -> set[str]:
    return {row["edge_id"] for row in read_csv(path) if row.get("is_spine_edge") == "True"}


def edge_heading_deg(sumo_net: Any, edge_id: str) -> float:
    edge = sumo_net.getEdge(edge_id)
    shape = edge.getShape()
    if len(shape) < 2:
        return 0.0
    (x1, y1), (x2, y2) = shape[0], shape[-1]
    return (math.degrees(math.atan2(y2 - y1, x2 - x1)) + 360.0) % 360.0


def heading_matches(direction: str, heading: float) -> bool:
    if direction == "NB":
        return 45.0 <= heading <= 135.0
    if direction == "EB":
        return heading <= 45.0 or heading >= 315.0
    return False


def road_axis_edge_map(net_path: Path) -> dict[tuple[str, str], set[str]]:
    sumo_net = read_sumo_net(net_path)
    mapping: dict[tuple[str, str], set[str]] = {}
    rows = read_csv(MAPPING_CSV) if MAPPING_CSV.is_file() else []
    for target in TARGET_ROAD_AXES:
        axis = target["road_axis_id"]
        direction = target["direction"]
        edge_ids = set()
        for row in rows:
            if row.get("road_axis_id") != axis:
                continue
            for edge_id in row.get("screenline_edge_ids", "").split():
                try:
                    heading = edge_heading_deg(sumo_net, edge_id)
                except KeyError:
                    continue
                if heading_matches(direction, heading):
                    edge_ids.add(edge_id)
        mapping[(axis, direction)] = edge_ids
    return mapping


def road_axis_rows(scale: float, edge_data: dict[str, dict[str, float]], axis_edges: dict[tuple[str, str], set[str]]) -> list[dict[str, Any]]:
    rows = []
    for target in TARGET_ROAD_AXES:
        axis = target["road_axis_id"]
        direction = target["direction"]
        edges = axis_edges.get((axis, direction), set())
        if not edges:
            rows.append(
                {
                    "scale": scale,
                    "scale_label": scale_label(scale),
                    "road_axis_id": axis,
                    "direction": direction,
                    "target_speed_kmh": target["target_speed_kmh"],
                    "simulated_speed_kmh": "",
                    "speed_error_kmh": "",
                    "abs_speed_error_kmh": "",
                    "edge_count": 0,
                    "edge_ids": "",
                    "weighting": "",
                    "weight_sum": "",
                    "edge_mapping_status": "no_edge_mapping",
                }
            )
            continue
        speed, weighting, weight_sum = weighted_speed_kmh(edge_data, edges)
        status = "mapped" if speed is not None else "no_speed_data"
        rows.append(
            {
                "scale": scale,
                "scale_label": scale_label(scale),
                "road_axis_id": axis,
                "direction": direction,
                "target_speed_kmh": target["target_speed_kmh"],
                "simulated_speed_kmh": round(speed, 6) if speed is not None else "",
                "speed_error_kmh": round((speed or 0) - target["target_speed_kmh"], 6) if speed is not None else "",
                "abs_speed_error_kmh": round(abs((speed or 0) - target["target_speed_kmh"]), 6) if speed is not None else "",
                "edge_count": len(edges),
                "edge_ids": " ".join(sorted(edges)),
                "weighting": weighting,
                "weight_sum": round(weight_sum, 6),
                "edge_mapping_status": status,
            }
        )
    return rows


def parse_general_teleport_diagnostics(stderr_path: Path, emergency_vehicle_id: str) -> dict[str, Any]:
    if not stderr_path.is_file():
        return {"teleport_start_count": 0, "top_reasons": [], "top_lanes": [], "top_end_edges": [], "top_vehicles": [], "notes": "stderr_missing"}
    reason_counts: dict[str, int] = {}
    lane_counts: dict[str, int] = {}
    end_edge_counts: dict[str, int] = {}
    vehicle_counts: dict[str, int] = {}
    start_count = 0
    start_pattern = re.compile(r"Teleporting vehicle '([^']+)';\s*([^,]+)(?:, lane='([^']+)')?")
    end_pattern = re.compile(r"Vehicle '([^']+)' ends teleporting on edge '([^']+)'")
    for line in stderr_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if emergency_vehicle_id in line:
            continue
        start_match = start_pattern.search(line)
        if start_match:
            vehicle_id = start_match.group(1)
            reason = start_match.group(2).strip()
            lane = start_match.group(3) or ""
            start_count += 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if lane:
                lane_counts[lane] = lane_counts.get(lane, 0) + 1
            vehicle_counts[vehicle_id] = vehicle_counts.get(vehicle_id, 0) + 1
            continue
        end_match = end_pattern.search(line)
        if end_match:
            edge_id = end_match.group(2)
            end_edge_counts[edge_id] = end_edge_counts.get(edge_id, 0) + 1

    def top_items(counts: dict[str, int], limit: int = 8) -> list[dict[str, Any]]:
        return [{"item": item, "count": count} for item, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]]

    repeat_vehicles = [{"vehicle_id": vehicle_id, "count": count} for vehicle_id, count in sorted(vehicle_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:8] if count > 1]
    return {
        "teleport_start_count": start_count,
        "top_reasons": top_items(reason_counts),
        "top_lanes": top_items(lane_counts),
        "top_end_edges": top_items(end_edge_counts),
        "top_repeat_vehicles": repeat_vehicles,
        "notes": "stderr_log_based_diagnostic",
    }


def load_existing_results() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result_rows = []
    road_rows = []
    if SUMMARY_JSON.is_file():
        payload = read_json(SUMMARY_JSON)
        result_rows = list(payload.get("results", []))
    if ROAD_AXIS_CSV.is_file():
        road_rows = read_csv(ROAD_AXIS_CSV)
    return result_rows, road_rows


def normalize_result_row(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row.setdefault("background_departed", row.get("background_departed_count"))
    row.setdefault("background_arrived", row.get("background_arrived_count"))
    row.setdefault("background_teleported", row.get("general_teleport_count"))
    row.setdefault("background_teleport_ratio", row.get("general_teleport_ratio"))
    row.setdefault("network_avg_speed_kmh", row.get("network_mean_speed_kmh"))
    row.setdefault("spine_avg_speed_kmh", row.get("spine_mean_speed_kmh"))
    row.setdefault("emergency_corridor_avg_speed_kmh", row.get("emergency_route_corridor_mean_speed_kmh"))
    if not row.get("teleport_diagnostics") and row.get("stderr_log") and row.get("emergency_vehicle_id"):
        row["teleport_diagnostics"] = parse_general_teleport_diagnostics(PROJECT_ROOT / row["stderr_log"], str(row["emergency_vehicle_id"]))
    speed_values = [
        float(row[key]) for key in ["network_mean_speed_kmh", "spine_mean_speed_kmh", "emergency_route_corridor_mean_speed_kmh"]
        if row.get(key) is not None and row.get(key) != ""
    ]
    core_speed_mae = sum(abs(value - TARGET_SPEED_KMH) for value in speed_values) / len(speed_values) if speed_values else None
    row["core_speed_mae_to_20kmh"] = round(core_speed_mae, 6) if core_speed_mae is not None else None
    road_axis_mae = float(row["road_axis_speed_mae_kmh"]) if row.get("road_axis_speed_mae_kmh") not in {None, ""} else None
    road_axis_penalty = (road_axis_mae * 0.25) if road_axis_mae is not None else 2.5
    eligible = bool(row.get("eligible_for_recommendation"))
    general_teleport_ratio = float(row.get("general_teleport_ratio") or 0.0)
    row["recommendation_score"] = round((core_speed_mae or 10**6) + road_axis_penalty + general_teleport_ratio * 10.0, 6) if eligible else None
    return row


def merge_by_scale(existing: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {str(row.get("scale_label") or scale_label(float(row["scale"]))): normalize_result_row(row) for row in existing}
    for row in new_rows:
        merged[str(row["scale_label"])] = normalize_result_row(row)
    return sorted(merged.values(), key=lambda row: float(row["scale"]))


def merge_road_rows(existing: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {
        (str(row.get("scale_label") or scale_label(float(row["scale"]))), row.get("road_axis_id"), row.get("direction")): row
        for row in existing
    }
    for row in new_rows:
        merged[(str(row["scale_label"]), row["road_axis_id"], row["direction"])] = row
    return sorted(merged.values(), key=lambda row: (float(row["scale"]), str(row["road_axis_id"]), str(row["direction"])))


def csv_fields(rows: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    return fields


def route_error_count(stderr: str) -> int:
    lower = stderr.lower()
    return lower.count("route error") + lower.count("has no valid route") + lower.count("is not connected")


def emergency_teleport_lines(stderr: str, vehicle_id: str) -> list[str]:
    return [line for line in stderr.splitlines() if vehicle_id in line and "teleport" in line.lower()]


def run_scale(
    scale: float,
    args: argparse.Namespace,
    route_row: dict[str, str],
    spine_edges: set[str],
    axis_edges: dict[tuple[str, str], set[str]],
    lines: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    label = scale_label(scale)
    scaled_route = scaled_route_path(scale)
    base_count, kept_count = write_scaled_route(args.base_background_route, scaled_route, scale, args.sampling_seed)
    emergency_vehicle_id = f"emergency_{args.route_id}_scale_{label}"
    emergency_route_xml = run_dir(scale) / f"{emergency_vehicle_id}.rou.xml"
    write_emergency_route_xml(emergency_route_xml, route_row, emergency_vehicle_id)
    paths = write_sumo_files(scale, args.net, scaled_route, emergency_route_xml, args.time_to_teleport, args.collision_action)
    sumo = shutil.which("sumo")
    if sumo is None:
        raise CalibrationError("missing_executable: sumo")
    completed = subprocess.run([sumo, "-c", str(paths["sumocfg"])], cwd=PROJECT_ROOT, check=False, capture_output=True, text=True, timeout=args.timeout_sec)
    paths["stdout"].write_text(completed.stdout, encoding="utf-8")
    paths["stderr"].write_text(completed.stderr, encoding="utf-8")
    summary_metrics = parse_summary_output(paths["summary"])
    trip = parse_tripinfo(paths["tripinfo"], emergency_vehicle_id)
    edge_data = parse_edge_data(paths["edge_data"])
    route_edges = set(route_row["route_edges"].split())
    network_edge_speed, edge_weighting, _network_weight = weighted_speed_kmh(edge_data)
    spine_speed, spine_weighting, _spine_weight = weighted_speed_kmh(edge_data, spine_edges)
    emergency_corridor_speed, emergency_weighting, _emergency_weight = weighted_speed_kmh(edge_data, route_edges)
    axis_rows = road_axis_rows(scale, edge_data, axis_edges)
    mapped_axis_errors = [float(row["abs_speed_error_kmh"]) for row in axis_rows if row["edge_mapping_status"] == "mapped" and row["abs_speed_error_kmh"] != ""]
    road_axis_mae = sum(mapped_axis_errors) / len(mapped_axis_errors) if mapped_axis_errors else None
    route_errors = route_error_count(completed.stderr)
    emergency_tp = emergency_teleport_lines(completed.stderr, emergency_vehicle_id)
    emergency_departed = summary_metrics["departed_count_total"] > kept_count or trip["emergency_arrived"]
    emergency_arrived = bool(trip["emergency_arrived"])
    emergency_teleport = bool(emergency_tp)
    background_departed = max(int(summary_metrics["departed_count_total"]) - (1 if emergency_departed else 0), 0)
    background_arrived = max(int(summary_metrics["arrived_count_total"]) - (1 if emergency_arrived else 0), 0)
    general_teleports = max(int(summary_metrics["teleport_count"]) - (1 if emergency_teleport else 0), 0)
    general_teleport_ratio = general_teleports / background_departed if background_departed else 0.0
    teleport_diagnostics = parse_general_teleport_diagnostics(paths["stderr"], emergency_vehicle_id)
    failures = []
    warnings = []
    if completed.returncode != 0:
        failures.append(f"sumo_exit_code_{completed.returncode}")
    if not emergency_departed:
        failures.append("emergency_not_departed")
    if not emergency_arrived:
        failures.append("emergency_not_arrived")
    if emergency_teleport:
        failures.append("emergency_teleport_detected")
    if route_errors > 0:
        failures.append("route_error_count_gt_0")
    if general_teleports > 0:
        warnings.append("general_vehicle_teleports_present")
    eligible = not failures
    status = "FAIL" if failures else "WARNING" if warnings else "PASS"
    speed_values = [value for value in [summary_metrics["network_mean_speed_kmh"], spine_speed, emergency_corridor_speed] if value is not None]
    core_speed_mae = sum(abs(value - TARGET_SPEED_KMH) for value in speed_values) / len(speed_values) if speed_values else None
    road_axis_penalty = (road_axis_mae * 0.25) if road_axis_mae is not None else 2.5
    recommendation_score = None if not eligible else (core_speed_mae or 10**6) + road_axis_penalty + general_teleport_ratio * 10.0
    row = {
        "scale": scale,
        "scale_label": label,
        "scaled_route": rel(scaled_route),
        "base_vehicle_count": base_count,
        "scaled_vehicle_count": kept_count,
        "sumo_exit_code": completed.returncode,
        "final_status": status,
        "eligible_for_recommendation": eligible,
        "emergency_vehicle_id": emergency_vehicle_id,
        "emergency_departed": emergency_departed,
        "emergency_arrived": emergency_arrived,
        "emergency_teleport": emergency_teleport,
        "emergency_teleport_evidence": emergency_tp,
        "emergency_travel_time": trip["emergency_travel_time"],
        "background_departed_count": background_departed,
        "background_arrived_count": background_arrived,
        "background_departed": background_departed,
        "background_arrived": background_arrived,
        "background_teleported": general_teleports,
        "background_teleport_ratio": round(general_teleport_ratio, 6),
        "general_teleport_count": general_teleports,
        "general_teleport_ratio": round(general_teleport_ratio, 6),
        "route_error_count": route_errors,
        "network_mean_speed_kmh": round(float(summary_metrics["network_mean_speed_kmh"]), 6),
        "network_avg_speed_kmh": round(float(summary_metrics["network_mean_speed_kmh"]), 6),
        "network_edgeData_mean_speed_kmh": round(network_edge_speed, 6) if network_edge_speed is not None else None,
        "spine_mean_speed_kmh": round(spine_speed, 6) if spine_speed is not None else None,
        "spine_avg_speed_kmh": round(spine_speed, 6) if spine_speed is not None else None,
        "emergency_route_corridor_mean_speed_kmh": round(emergency_corridor_speed, 6) if emergency_corridor_speed is not None else None,
        "emergency_corridor_avg_speed_kmh": round(emergency_corridor_speed, 6) if emergency_corridor_speed is not None else None,
        "network_speed_delta_to_20kmh": round(float(summary_metrics["network_mean_speed_kmh"]) - TARGET_SPEED_KMH, 6),
        "spine_speed_delta_to_20kmh": round((spine_speed or 0) - TARGET_SPEED_KMH, 6) if spine_speed is not None else None,
        "emergency_route_corridor_speed_delta_to_20kmh": round((emergency_corridor_speed or 0) - TARGET_SPEED_KMH, 6) if emergency_corridor_speed is not None else None,
        "road_axis_speed_mae_kmh": round(road_axis_mae, 6) if road_axis_mae is not None else None,
        "core_speed_mae_to_20kmh": round(core_speed_mae, 6) if core_speed_mae is not None else None,
        "recommendation_score": round(recommendation_score, 6) if recommendation_score is not None else None,
        "edge_speed_weighting": edge_weighting,
        "spine_speed_weighting": spine_weighting,
        "emergency_route_corridor_speed_weighting": emergency_weighting,
        "time_to_teleport": args.time_to_teleport,
        "collision_action": args.collision_action,
        "sampling_seed": args.sampling_seed,
        "sumo_end_policy": "no_explicit_end_run_until_all_vehicles_finished",
        "sim_end_time": summary_metrics["sim_end_time"],
        "teleport_diagnostics": teleport_diagnostics,
        "warnings": warnings,
        "failures": failures,
        "run_dir": rel(run_dir(scale)),
        "sumocfg": rel(paths["sumocfg"]),
        "tripinfo": rel(paths["tripinfo"]),
        "summary_output": rel(paths["summary"]),
        "edgeData_output": rel(paths["edge_data"]),
        "stderr_log": rel(paths["stderr"]),
    }
    lines.append(f"scale_{label}: status={status} eligible={eligible} emergency_teleport={emergency_teleport} network_kmh={row['network_mean_speed_kmh']}")
    return row, axis_rows


def choose_recommended(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [row for row in rows if row["eligible_for_recommendation"]]
    if not eligible:
        return None
    network_in_range = [
        row for row in eligible
        if 15.0 <= float(row["network_mean_speed_kmh"]) <= 22.0
    ]
    if network_in_range:
        return min(network_in_range, key=lambda row: float(row["recommendation_score"]))
    return min(eligible, key=lambda row: float(row["recommendation_score"]))


def teleport_ratio_note(rows: list[dict[str, Any]]) -> str:
    by_label = {str(row.get("scale_label")): row for row in rows}
    row_02 = by_label.get("0p2")
    row_03 = by_label.get("0p3")
    if not row_02 or not row_03:
        return "0.2x_or_0.3x_missing"
    ratio_02 = float(row_02.get("general_teleport_ratio", 0.0))
    ratio_03 = float(row_03.get("general_teleport_ratio", 0.0))
    if ratio_02 <= ratio_03:
        return "0.2x teleport ratio is not higher than 0.3x"
    diag_02 = row_02.get("teleport_diagnostics") or {}
    diag_03 = row_03.get("teleport_diagnostics") or {}
    top_02 = (diag_02.get("top_reasons") or [{}])[0].get("item", "") if isinstance(diag_02, dict) and diag_02.get("top_reasons") else ""
    top_03 = (diag_03.get("top_reasons") or [{}])[0].get("item", "") if isinstance(diag_03, dict) and diag_03.get("top_reasons") else ""
    return (
        f"0.2x ratio {ratio_02:.6f} > 0.3x {ratio_03:.6f}; "
        f"stderr diagnostics show top reasons 0.2x='{top_02}', 0.3x='{top_03}'. "
        "This can happen because deterministic subsets are not nested; lower scale is sampled independently from 1.0x, not from 0.3x."
    )


def append_step10_doc(summary: dict[str, Any]) -> None:
    marker = "## Demand Scale Calibration"
    current = STEP10_DOC.read_text(encoding="utf-8") if STEP10_DOC.is_file() else "# Step 10 B0 Baseline Speed Smoke\n"
    base = current.split(marker)[0].rstrip()
    recommended = summary.get("recommended_scale")
    text = f"""{base}

{marker}

0.5x / 0.3x / 0.2x / 0.15x / 0.12x / 0.10x background demand를 deterministic sampling으로 생성하고 ER_ACC_002 B0 no-control speed smoke를 비교했다.

- final status: `{summary['final_status']}`
- recommended scale: `{recommended}`
- eligible scale count: `{summary['eligible_scale_count']}`
- blocker: `{summary.get('blocker', '')}`
- compared scales: `{summary.get('compared_scales', [])}`
- summary CSV: `results/metrics/b0_demand_scale_calibration_summary.csv`
- road-axis CSV: `results/metrics/b0_road_axis_speed_calibration.csv`
- 0.2x vs 0.3x teleport diagnostic: `{summary.get('teleport_ratio_note', '')}`

추천 기준은 emergency teleport 없음, route error 0, emergency arrived, 평균속도 목표범위 근접성이다. 1.0x imputed demand는 gridlock/stress demand로 보관한다.
"""
    STEP10_DOC.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.net = args.net.resolve()
    args.base_background_route = args.base_background_route.resolve()
    args.emergency_routes = args.emergency_routes.resolve()
    args.spine_edges = args.spine_edges.resolve()
    generated_at = utc_now()
    lines = ["Step 10 B0 demand scale calibration", "=====================================", f"generated_at: {generated_at}"]
    try:
        for path in [args.net, args.base_background_route, args.emergency_routes, args.spine_edges, MAPPING_CSV, BASE_STEP10_SUMMARY]:
            if not path.is_file():
                raise CalibrationError(f"missing input path: {path}")
        base_vehicle_count = count_vehicles(args.base_background_route)
        route_row = select_emergency_route(args.emergency_routes, args.route_id)
        validation_failures = validate_route_edges(args.net, route_row["route_edges"].split())
        if validation_failures:
            raise CalibrationError(f"emergency route validation failed: {';'.join(validation_failures[:10])}")
        base_summary = read_json(BASE_STEP10_SUMMARY)
        lines.extend(
            [
                f"base_vehicle_count: {base_vehicle_count}",
                f"route_id: {route_row['route_id']}",
                f"route_status: {route_row['review_status']}",
                f"route_needs_manual_review: {route_row['needs_manual_review']}",
                f"base_1x_time_to_teleport: {base_summary.get('time_to_teleport')}",
                f"base_1x_collision_action: {base_summary.get('collision_action')}",
                f"base_1x_emergency_teleport_evidence: {base_summary.get('emergency_teleport_evidence')}",
            ]
        )
        spine_edges = read_spine_edges(args.spine_edges)
        axis_edges = road_axis_edge_map(args.net)
        existing_results, existing_road_rows = load_existing_results() if args.merge_existing_summary else ([], [])
        new_result_rows: list[dict[str, Any]] = []
        new_road_rows: list[dict[str, Any]] = []
        for scale in args.scales:
            row, axis_rows = run_scale(scale, args, route_row, spine_edges, axis_edges, lines)
            new_result_rows.append(row)
            new_road_rows.extend(axis_rows)
        result_rows = merge_by_scale(existing_results, new_result_rows)
        road_rows = merge_road_rows(existing_road_rows, new_road_rows)
        recommended = choose_recommended(result_rows)
        final_status = "FAIL" if recommended is None else "WARNING" if any(row["warnings"] for row in result_rows) else "PASS"
        blocker = "No eligible scale for recommendation" if recommended is None else ""
        tp_note = teleport_ratio_note(result_rows)
        summary = {
            "generated_at": generated_at,
            "final_status": final_status,
            "active_net": rel(args.net),
            "base_background_route": rel(args.base_background_route),
            "base_vehicle_count": base_vehicle_count,
            "route_id": args.route_id,
            "time_to_teleport": args.time_to_teleport,
            "collision_action": args.collision_action,
            "requested_scales": args.scales,
            "compared_scales": [row["scale"] for row in result_rows],
            "recommended_scale": recommended["scale"] if recommended else None,
            "recommended_scale_label": recommended["scale_label"] if recommended else None,
            "eligible_scale_count": sum(1 for row in result_rows if row["eligible_for_recommendation"]),
            "blocker": blocker,
            "sampling_seed": args.sampling_seed,
            "merged_existing_summary": args.merge_existing_summary,
            "teleport_ratio_note": tp_note,
            "results": result_rows,
            "outputs": [
                rel(SUMMARY_CSV),
                rel(SUMMARY_JSON),
                rel(ROAD_AXIS_CSV),
                rel(LOG_PATH),
                rel(STEP10_DOC),
            ],
        }
        write_csv(SUMMARY_CSV, result_rows, csv_fields(result_rows))
        write_json(SUMMARY_JSON, summary)
        write_csv(
            ROAD_AXIS_CSV,
            road_rows,
            [
                "scale",
                "scale_label",
                "road_axis_id",
                "direction",
                "target_speed_kmh",
                "simulated_speed_kmh",
                "speed_error_kmh",
                "abs_speed_error_kmh",
                "edge_count",
                "edge_ids",
                "weighting",
                "weight_sum",
                "edge_mapping_status",
            ],
        )
        append_step10_doc(summary)
        lines.extend(
            [
                f"final_status: {final_status}",
                f"recommended_scale: {summary['recommended_scale']}",
                f"eligible_scale_count: {summary['eligible_scale_count']}",
                f"blocker: {blocker}",
                f"summary_json: {rel(SUMMARY_JSON)}",
            ]
        )
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if final_status in {"PASS", "WARNING"} else 1
    except (CalibrationError, OSError, ET.ParseError, subprocess.TimeoutExpired, ValueError, RuntimeError, ImportError) as exc:
        lines.extend(["final_status: FAIL", f"blocker: {exc}"])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
