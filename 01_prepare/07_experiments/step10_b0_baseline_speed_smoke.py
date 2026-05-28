#!/usr/bin/env python3
"""Run one Step 10 B0 baseline speed smoke with background demand and one emergency route."""

from __future__ import annotations

import argparse
import csv
import json
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
DEFAULT_BACKGROUND_ROUTE = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19.rou.xml"
DEFAULT_EMERGENCY_ROUTES = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
DEFAULT_SPINE_EDGES = PROJECT_ROOT / "data_prepared/routes/corridor_spine_edges.csv"
DEFAULT_RUN_DIR = PROJECT_ROOT / "runs/b0_baseline_speed_smoke"
SUMMARY_CSV = PROJECT_ROOT / "results/metrics/b0_baseline_speed_smoke_summary.csv"
SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b0_baseline_speed_smoke_summary.json"
EDGE_SPEED_CSV = PROJECT_ROOT / "results/metrics/b0_baseline_edge_speed.csv"
SPINE_SPEED_CSV = PROJECT_ROOT / "results/metrics/b0_baseline_spine_speed_summary.csv"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step10_b0_baseline_speed_smoke.log"
STEP10_DOC = PROJECT_ROOT / "docs/Step10.md"
TARGET_SPEED_KMH = 20.0
DEFAULT_TIMEOUT_SEC = 1200


class Step10Error(RuntimeError):
    """Expected Step 10 failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run B0 baseline speed smoke.")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--emergency-routes", type=Path, default=DEFAULT_EMERGENCY_ROUTES)
    parser.add_argument("--route-id", default="ER_ACC_002")
    parser.add_argument("--spine-edges", type=Path, default=DEFAULT_SPINE_EDGES)
    parser.add_argument("--time-to-teleport", type=int, default=900)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--emergency-vehicle-id", default="emergency_ER_ACC_002")
    parser.add_argument("--emergency-depart", type=float, default=0.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RUN_DIR)
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


def count_vehicles(route_file: Path) -> int:
    count = 0
    for _event, elem in ET.iterparse(route_file, events=("end",)):
        if elem.tag == "vehicle":
            count += 1
        elem.clear()
    return count


def select_emergency_route(routes_csv: Path, route_id: str) -> dict[str, str]:
    rows = read_csv(routes_csv)
    matches = [row for row in rows if row.get("route_id") == route_id]
    if not matches:
        raise Step10Error(f"Emergency route not found: {route_id}")
    row = matches[0]
    if row.get("review_status") != "PASS" or row.get("needs_manual_review") != "False":
        raise Step10Error(
            f"Emergency route is not stable enough: route_id={route_id}, "
            f"review_status={row.get('review_status')}, needs_manual_review={row.get('needs_manual_review')}"
        )
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
        from_edge = sumo_net.getEdge(from_id)
        to_edge = sumo_net.getEdge(to_id)
        if to_edge not in from_edge.getOutgoing():
            failures.append(f"disconnected_transition:{from_id}->{to_id}")
    return failures


def write_emergency_route_xml(path: Path, route_row: dict[str, str], vehicle_id: str, depart: float) -> None:
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
            "depart": f"{depart:g}",
            "departLane": "best",
            "departSpeed": "max",
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_additional_xml(path: Path, edge_data_path: Path) -> None:
    root = ET.Element("additional")
    ET.SubElement(
        root,
        "edgeData",
        {
            "id": "b0_baseline_edge_speed",
            "file": str(edge_data_path),
            "begin": "0",
            "end": "86400",
            "freq": "86400",
            "excludeEmpty": "false",
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_sumocfg(
    path: Path,
    net_path: Path,
    background_route: Path,
    emergency_route: Path,
    additional_xml: Path,
    tripinfo: Path,
    summary: Path,
    time_to_teleport: int,
    collision_action: str,
) -> None:
    root = ET.Element("configuration")
    input_elem = ET.SubElement(root, "input")
    ET.SubElement(input_elem, "net-file", {"value": str(net_path)})
    ET.SubElement(input_elem, "route-files", {"value": f"{background_route},{emergency_route}"})
    ET.SubElement(input_elem, "additional-files", {"value": str(additional_xml)})
    output_elem = ET.SubElement(root, "output")
    ET.SubElement(output_elem, "tripinfo-output", {"value": str(tripinfo)})
    ET.SubElement(output_elem, "summary-output", {"value": str(summary)})
    time_elem = ET.SubElement(root, "time")
    ET.SubElement(time_elem, "begin", {"value": "0"})
    processing_elem = ET.SubElement(root, "processing")
    ET.SubElement(processing_elem, "time-to-teleport", {"value": str(time_to_teleport)})
    ET.SubElement(processing_elem, "collision.action", {"value": collision_action})
    report_elem = ET.SubElement(root, "report")
    ET.SubElement(report_elem, "no-step-log", {"value": "true"})
    ET.SubElement(report_elem, "duration-log.disable", {"value": "true"})
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def parse_summary_output(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise Step10Error(f"summary-output missing: {rel(path)}")
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
        raise Step10Error(f"summary-output has no steps: {rel(path)}")
    mean_speed_mps = speed_num / speed_den if speed_den else float(last_step.get("meanSpeed", "0") or 0)
    return {
        "departed_count_total": int(float(last_step.get("inserted", "0"))),
        "arrived_count_total": int(float(last_step.get("arrived", "0"))),
        "teleport_count": max_teleports,
        "sim_end_time": float(last_step.get("time", "0")),
        "network_summary_mean_speed_mps": mean_speed_mps,
        "network_mean_speed_kmh": mean_speed_mps * 3.6,
        "network_summary_speed_weighting": "running_vehicle_seconds",
    }


def parse_tripinfo(path: Path, vehicle_id: str) -> dict[str, Any]:
    if not path.is_file():
        raise Step10Error(f"tripinfo-output missing: {rel(path)}")
    root = ET.parse(path).getroot()
    for tripinfo in root.findall("tripinfo"):
        if tripinfo.get("id") == vehicle_id:
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
    if not path.is_file():
        raise Step10Error(f"edgeData output missing: {rel(path)}")
    root = ET.parse(path).getroot()
    rows: dict[str, dict[str, float]] = {}
    for edge in root.findall(".//edge"):
        edge_id = edge.get("id")
        if not edge_id:
            continue
        rows[edge_id] = {
            "speed_mps": float(edge.get("speed") or 0.0),
            "entered": float(edge.get("entered") or 0.0),
            "left": float(edge.get("left") or 0.0),
            "sampled_seconds": float(edge.get("sampledSeconds") or 0.0),
        }
    return rows


def weighted_speed_kmh(edge_data: dict[str, dict[str, float]], edge_ids: set[str] | None = None) -> tuple[float | None, str, float]:
    subset = edge_data.items() if edge_ids is None else ((edge_id, edge_data[edge_id]) for edge_id in edge_ids if edge_id in edge_data)
    rows = [(edge_id, values) for edge_id, values in subset if values.get("speed_mps", 0.0) > 0]
    if not rows:
        return None, "sampledSeconds", 0.0
    sampled_weight = sum(values.get("sampled_seconds", 0.0) for _edge_id, values in rows)
    weighting = "sampledSeconds" if sampled_weight > 0 else "entered"
    num = 0.0
    den = 0.0
    for _edge_id, values in rows:
        weight = values.get("sampled_seconds", 0.0) if weighting == "sampledSeconds" else values.get("entered", 0.0)
        if weight <= 0:
            continue
        num += values["speed_mps"] * weight
        den += weight
    if den <= 0:
        return None, weighting, 0.0
    return (num / den) * 3.6, weighting, den


def read_spine_edges(path: Path) -> set[str]:
    rows = read_csv(path)
    return {row["edge_id"] for row in rows if row.get("is_spine_edge") == "True"}


def write_edge_speed_csv(edge_data: dict[str, dict[str, float]], spine_edges: set[str], emergency_edges: set[str]) -> None:
    rows = []
    for edge_id, values in sorted(edge_data.items()):
        rows.append(
            {
                "edge_id": edge_id,
                "speed_mps": round(values["speed_mps"], 6),
                "speed_kmh": round(values["speed_mps"] * 3.6, 6),
                "entered": round(values["entered"], 6),
                "left": round(values["left"], 6),
                "sampled_seconds": round(values["sampled_seconds"], 6),
                "is_spine_edge": edge_id in spine_edges,
                "is_emergency_route_edge": edge_id in emergency_edges,
            }
        )
    write_csv(
        EDGE_SPEED_CSV,
        rows,
        ["edge_id", "speed_mps", "speed_kmh", "entered", "left", "sampled_seconds", "is_spine_edge", "is_emergency_route_edge"],
    )


def route_error_count(stderr: str) -> int:
    lower = stderr.lower()
    return lower.count("route error") + lower.count("has no valid route") + lower.count("is not connected")


def emergency_teleport_lines(stderr: str, vehicle_id: str) -> list[str]:
    return [line for line in stderr.splitlines() if vehicle_id in line and "teleport" in line.lower()]


def emergency_collision_lines(stderr: str, vehicle_id: str) -> list[str]:
    return [line for line in stderr.splitlines() if vehicle_id in line and "collision" in line.lower()]


def write_step10_doc(summary: dict[str, Any]) -> None:
    text = f"""# Step 10 B0 Baseline Speed Smoke

## 목표

신호제어 없는 B0 조건에서 imputed A-17/A-19 background demand와 응급차 `ER_ACC_002` 1대를 함께 실행하고 평균속도와 teleport를 측정한다.

## 입력

- active net: `{summary['active_net']}`
- background route: `{summary['background_route']}`
- emergency route: `{summary['emergency_route_id']}`
- emergency vehicle id: `{summary['emergency_vehicle_id']}`
- emergency depart: `{summary['emergency_depart']}`
- time-to-teleport: `{summary['time_to_teleport']}`
- collision action: `{summary['collision_action']}`
- SUMO end policy: `{summary['sumo_end_policy']}`

## 결과

- final status: `{summary['final_status']}`
- SUMO exit code: `{summary['sumo_exit_code']}`
- emergency departed/arrived/teleport: `{summary['emergency_departed']}` / `{summary['emergency_arrived']}` / `{summary['emergency_teleport']}`
- emergency travel time: `{summary['emergency_travel_time']}`
- background departed/arrived: `{summary['background_departed_count']}` / `{summary['background_arrived_count']}`
- general vehicle teleport count/ratio: `{summary['general_teleport_count']}` / `{summary['general_teleport_ratio']}`
- route error count: `{summary['route_error_count']}`
- network mean speed: `{summary['network_mean_speed_kmh']}` km/h
- spine mean speed: `{summary['spine_mean_speed_kmh']}` km/h
- emergency-route-corridor mean speed: `{summary['emergency_route_corridor_mean_speed_kmh']}` km/h
- demand scale-down recommended: `{summary['demand_scale_down_recommended']}`

## 산출물

- `runs/b0_baseline_speed_smoke/`
- `results/metrics/b0_baseline_speed_smoke_summary.csv`
- `results/metrics/b0_baseline_speed_smoke_summary.json`
- `results/metrics/b0_baseline_edge_speed.csv`
- `results/metrics/b0_baseline_spine_speed_summary.csv`
- `outputs/logs/step10_b0_baseline_speed_smoke.log`
"""
    STEP10_DOC.parent.mkdir(parents=True, exist_ok=True)
    STEP10_DOC.write_text(text, encoding="utf-8")


def main() -> int:
    args = parse_args()
    generated_at = utc_now()
    lines = ["Step 10 B0 baseline speed smoke", "=================================", f"generated_at: {generated_at}"]
    try:
        args.net = args.net.resolve()
        args.background_route = args.background_route.resolve()
        args.emergency_routes = args.emergency_routes.resolve()
        args.spine_edges = args.spine_edges.resolve()
        args.output_dir = args.output_dir.resolve()
        for path in [args.net, args.background_route, args.emergency_routes, args.spine_edges]:
            if not path.is_file():
                raise Step10Error(f"missing_file: {path}")
        sumo = shutil.which("sumo")
        if sumo is None:
            raise Step10Error("missing_executable: sumo")

        route_row = select_emergency_route(args.emergency_routes, args.route_id)
        route_edges = route_row["route_edges"].split()
        validation_failures = validate_route_edges(args.net, route_edges)
        if validation_failures:
            raise Step10Error(f"emergency route validation failed: {';'.join(validation_failures[:10])}")

        emergency_route_xml = args.output_dir / "emergency_ER_ACC_002.rou.xml"
        additional_xml = args.output_dir / "edge_data.add.xml"
        edge_data_xml = args.output_dir / "edgeData.xml"
        sumocfg = args.output_dir / "scenario.sumocfg"
        tripinfo = args.output_dir / "tripinfo.xml"
        summary_output = args.output_dir / "summary.xml"
        stdout_log = args.output_dir / "sumo_stdout.log"
        stderr_log = args.output_dir / "sumo_stderr.log"
        write_emergency_route_xml(emergency_route_xml, route_row, args.emergency_vehicle_id, args.emergency_depart)
        write_additional_xml(additional_xml, edge_data_xml)
        write_sumocfg(
            sumocfg,
            args.net,
            args.background_route,
            emergency_route_xml,
            additional_xml,
            tripinfo,
            summary_output,
            args.time_to_teleport,
            args.collision_action,
        )

        background_vehicle_count = count_vehicles(args.background_route)
        completed = subprocess.run([sumo, "-c", str(sumocfg)], cwd=PROJECT_ROOT, check=False, capture_output=True, text=True, timeout=args.timeout_sec)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        stdout_log.write_text(completed.stdout, encoding="utf-8")
        stderr_log.write_text(completed.stderr, encoding="utf-8")

        summary_metrics = parse_summary_output(summary_output)
        trip = parse_tripinfo(tripinfo, args.emergency_vehicle_id)
        edge_data = parse_edge_data(edge_data_xml)
        spine_edges = read_spine_edges(args.spine_edges)
        emergency_edges = set(route_edges)
        write_edge_speed_csv(edge_data, spine_edges, emergency_edges)

        network_edge_speed, edge_weighting, network_edge_weight = weighted_speed_kmh(edge_data)
        spine_speed, spine_weighting, spine_weight = weighted_speed_kmh(edge_data, spine_edges)
        emergency_route_speed, emergency_weighting, emergency_weight = weighted_speed_kmh(edge_data, emergency_edges)
        write_csv(
            SPINE_SPEED_CSV,
            [
                {
                    "category": "network_edgeData",
                    "mean_speed_kmh": round(network_edge_speed, 6) if network_edge_speed is not None else "",
                    "speed_delta_to_20kmh": round((network_edge_speed or 0) - TARGET_SPEED_KMH, 6) if network_edge_speed is not None else "",
                    "weighting": edge_weighting,
                    "weight_sum": round(network_edge_weight, 6),
                    "edge_count": len(edge_data),
                },
                {
                    "category": "spine",
                    "mean_speed_kmh": round(spine_speed, 6) if spine_speed is not None else "",
                    "speed_delta_to_20kmh": round((spine_speed or 0) - TARGET_SPEED_KMH, 6) if spine_speed is not None else "",
                    "weighting": spine_weighting,
                    "weight_sum": round(spine_weight, 6),
                    "edge_count": len(spine_edges),
                },
                {
                    "category": "emergency_route_corridor",
                    "mean_speed_kmh": round(emergency_route_speed, 6) if emergency_route_speed is not None else "",
                    "speed_delta_to_20kmh": round((emergency_route_speed or 0) - TARGET_SPEED_KMH, 6) if emergency_route_speed is not None else "",
                    "weighting": emergency_weighting,
                    "weight_sum": round(emergency_weight, 6),
                    "edge_count": len(emergency_edges),
                },
            ],
            ["category", "mean_speed_kmh", "speed_delta_to_20kmh", "weighting", "weight_sum", "edge_count"],
        )

        route_errors = route_error_count(completed.stderr)
        emergency_tp_lines = emergency_teleport_lines(completed.stderr, args.emergency_vehicle_id)
        emergency_col_lines = emergency_collision_lines(completed.stderr, args.emergency_vehicle_id)
        emergency_arrived = bool(trip["emergency_arrived"])
        total_departed = int(summary_metrics["departed_count_total"])
        total_arrived = int(summary_metrics["arrived_count_total"])
        emergency_departed = total_departed > background_vehicle_count or emergency_arrived
        emergency_teleport = bool(emergency_tp_lines)
        general_teleports = int(summary_metrics["teleport_count"]) - (1 if emergency_teleport else 0)
        general_teleports = max(general_teleports, 0)
        background_departed = max(total_departed - (1 if emergency_departed else 0), 0)
        background_arrived = max(total_arrived - (1 if emergency_arrived else 0), 0)
        general_teleport_ratio = general_teleports / background_departed if background_departed else 0.0
        network_mean_speed_kmh = float(summary_metrics["network_mean_speed_kmh"])
        speed_values = [value for value in [network_mean_speed_kmh, spine_speed, emergency_route_speed] if value is not None]
        demand_scale_down = general_teleport_ratio > 0.20 and any(value < TARGET_SPEED_KMH for value in speed_values)

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
        if emergency_col_lines:
            warnings.append("emergency_collision_warning_present")
        if demand_scale_down:
            warnings.append("demand_scale_down_recommended")
        final_status = "FAIL" if failures else "WARNING" if warnings else "PASS"

        summary = {
            "generated_at": generated_at,
            "final_status": final_status,
            "active_net": rel(args.net),
            "background_route": rel(args.background_route),
            "emergency_routes_csv": rel(args.emergency_routes),
            "emergency_route_id": args.route_id,
            "emergency_vehicle_id": args.emergency_vehicle_id,
            "emergency_depart": args.emergency_depart,
            "emergency_target_edge_id": route_row["target_edge_id"],
            "emergency_route_length_m": float(route_row["route_length_m"]),
            "emergency_route_edge_count": len(route_edges),
            "time_to_teleport": args.time_to_teleport,
            "collision_action": args.collision_action,
            "sumo_end_policy": "no_explicit_end_run_until_all_vehicles_finished",
            "configured_end_time": None,
            "sim_end_time": summary_metrics["sim_end_time"],
            "sumo_exit_code": completed.returncode,
            "emergency_departed": emergency_departed,
            "emergency_arrived": emergency_arrived,
            "emergency_travel_time": trip["emergency_travel_time"],
            "emergency_depart_time_observed": trip["emergency_depart_time_observed"],
            "emergency_arrival_time": trip["emergency_arrival_time"],
            "emergency_teleport": emergency_teleport,
            "emergency_teleport_evidence": emergency_tp_lines,
            "emergency_collision_warning_count": len(emergency_col_lines),
            "emergency_collision_evidence": emergency_col_lines,
            "background_vehicle_count_input": background_vehicle_count,
            "background_departed_count": background_departed,
            "background_arrived_count": background_arrived,
            "teleport_count": int(summary_metrics["teleport_count"]),
            "general_teleport_count": general_teleports,
            "general_teleport_ratio": round(general_teleport_ratio, 6),
            "route_error_count": route_errors,
            "network_mean_speed_kmh": round(network_mean_speed_kmh, 6),
            "network_summary_mean_speed_mps": round(float(summary_metrics["network_summary_mean_speed_mps"]), 6),
            "network_summary_speed_weighting": summary_metrics["network_summary_speed_weighting"],
            "network_edgeData_mean_speed_kmh": round(network_edge_speed, 6) if network_edge_speed is not None else None,
            "spine_mean_speed_kmh": round(spine_speed, 6) if spine_speed is not None else None,
            "emergency_route_corridor_mean_speed_kmh": round(emergency_route_speed, 6) if emergency_route_speed is not None else None,
            "network_speed_delta_to_20kmh": round(network_mean_speed_kmh - TARGET_SPEED_KMH, 6),
            "spine_speed_delta_to_20kmh": round((spine_speed or 0) - TARGET_SPEED_KMH, 6) if spine_speed is not None else None,
            "emergency_route_corridor_speed_delta_to_20kmh": round((emergency_route_speed or 0) - TARGET_SPEED_KMH, 6) if emergency_route_speed is not None else None,
            "edge_speed_weighting": edge_weighting,
            "spine_speed_weighting": spine_weighting,
            "emergency_route_corridor_speed_weighting": emergency_weighting,
            "demand_scale_down_recommended": demand_scale_down,
            "warnings": warnings,
            "failures": failures,
            "run_dir": rel(args.output_dir),
            "sumocfg": rel(sumocfg),
            "tripinfo": rel(tripinfo),
            "summary_output": rel(summary_output),
            "edgeData_output": rel(edge_data_xml),
            "stdout_log": rel(stdout_log),
            "stderr_log": rel(stderr_log),
            "outputs": [
                rel(SUMMARY_CSV),
                rel(SUMMARY_JSON),
                rel(EDGE_SPEED_CSV),
                rel(SPINE_SPEED_CSV),
                rel(LOG_PATH),
                rel(STEP10_DOC),
            ],
        }
        write_json(SUMMARY_JSON, summary)
        write_csv(SUMMARY_CSV, [summary], list(summary.keys()))
        write_step10_doc(summary)

        lines.extend(
            [
                f"route_id: {args.route_id}",
                f"target_edge_id: {route_row['target_edge_id']}",
                f"route_length_m: {route_row['route_length_m']}",
                f"review_status: {route_row['review_status']}",
                f"needs_manual_review: {route_row['needs_manual_review']}",
                "emergency_route_validation: PASS",
                f"sumo_exit_code: {completed.returncode}",
                f"emergency_departed: {emergency_departed}",
                f"emergency_arrived: {emergency_arrived}",
                f"emergency_teleport: {emergency_teleport}",
                f"background_departed_count: {background_departed}",
                f"general_teleport_count: {general_teleports}",
                f"general_teleport_ratio: {general_teleport_ratio:.6f}",
                f"network_mean_speed_kmh: {network_mean_speed_kmh:.6f}",
                f"spine_mean_speed_kmh: {spine_speed if spine_speed is not None else ''}",
                f"emergency_route_corridor_mean_speed_kmh: {emergency_route_speed if emergency_route_speed is not None else ''}",
                f"final_status: {final_status}",
                f"summary_json: {rel(SUMMARY_JSON)}",
            ]
        )
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if final_status in {"PASS", "WARNING"} else 1
    except (Step10Error, OSError, ET.ParseError, subprocess.TimeoutExpired, ValueError, RuntimeError, ImportError) as exc:
        lines.extend(["final_status: FAIL", f"blocker: {exc}"])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
