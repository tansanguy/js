#!/usr/bin/env python3
"""Run Step 10 B0 baseline smoke for all spine-v2 emergency routes."""

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
DEFAULT_BACKGROUND_ROUTE = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml"
DEFAULT_EMERGENCY_ROUTES = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
DEFAULT_SPINE_EDGES = PROJECT_ROOT / "data_prepared/routes/corridor_spine_edges.csv"
DEFAULT_RUN_ROOT = PROJECT_ROOT / "runs/b0_baseline_19route_smoke"
SUMMARY_CSV = PROJECT_ROOT / "results/metrics/b0_baseline_19route_smoke_summary.csv"
SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b0_baseline_19route_smoke_summary.json"
SPEED_BY_ROUTE_CSV = PROJECT_ROOT / "results/metrics/b0_baseline_19route_speed_by_route.csv"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step10_b0_19route_baseline_smoke.log"
STEP10_DOC = PROJECT_ROOT / "docs/Step10.md"
TARGET_SPEED_KMH = 20.0
DEFAULT_TIMEOUT_SEC = 1200


class BatchSmokeError(RuntimeError):
    """Expected batch smoke failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run B0 19-route baseline smoke.")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--emergency-routes", type=Path, default=DEFAULT_EMERGENCY_ROUTES)
    parser.add_argument("--spine-edges", type=Path, default=DEFAULT_SPINE_EDGES)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--time-to-teleport", type=int, default=1200)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--emergency-depart", type=float, default=0.0)
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


def csv_fields(rows: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    return fields


def count_vehicles(route_file: Path) -> int:
    count = 0
    for _event, elem in ET.iterparse(route_file, events=("end",)):
        if elem.tag == "vehicle":
            count += 1
        elem.clear()
    return count


def validate_route_edges(sumo_net: Any, edge_ids: list[str]) -> list[str]:
    failures = []
    for edge_id in edge_ids:
        try:
            sumo_net.getEdge(edge_id)
        except KeyError:
            failures.append(f"missing_edge:{edge_id}")
    if failures:
        return failures
    for from_id, to_id in zip(edge_ids, edge_ids[1:], strict=False):
        if sumo_net.getEdge(to_id) not in sumo_net.getEdge(from_id).getOutgoing():
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


def write_sumo_files(
    run_dir: Path,
    net_path: Path,
    background_route: Path,
    emergency_route_xml: Path,
    time_to_teleport: int,
    collision_action: str,
) -> dict[str, Path]:
    paths = {
        "additional": run_dir / "edge_data.add.xml",
        "edge_data": run_dir / "edgeData.xml",
        "sumocfg": run_dir / "scenario.sumocfg",
        "tripinfo": run_dir / "tripinfo.xml",
        "summary": run_dir / "summary.xml",
        "stdout": run_dir / "sumo_stdout.log",
        "stderr": run_dir / "sumo_stderr.log",
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    additional = ET.Element("additional")
    ET.SubElement(
        additional,
        "edgeData",
        {
            "id": "b0_19route_edge_speed",
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
        raise BatchSmokeError(f"summary-output has no steps: {rel(path)}")
    mean_speed_mps = speed_num / speed_den if speed_den else float(last_step.get("meanSpeed", "0") or 0)
    return {
        "departed_count_total": int(float(last_step.get("inserted", "0"))),
        "arrived_count_total": int(float(last_step.get("arrived", "0"))),
        "teleport_count": max_teleports,
        "sim_end_time": float(last_step.get("time", "0")),
        "network_avg_speed_kmh": mean_speed_mps * 3.6,
        "network_summary_mean_speed_mps": mean_speed_mps,
    }


def parse_tripinfo(path: Path, vehicle_id: str) -> dict[str, Any]:
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
    return {row["edge_id"] for row in read_csv(path) if row.get("is_spine_edge") == "True"}


def route_error_count(stderr: str) -> int:
    lower = stderr.lower()
    return lower.count("route error") + lower.count("has no valid route") + lower.count("is not connected")


def emergency_teleport_lines(stderr: str, vehicle_id: str) -> list[str]:
    return [line for line in stderr.splitlines() if vehicle_id in line and "teleport" in line.lower()]


def run_one_route(
    sumo: str,
    sumo_net: Any,
    args: argparse.Namespace,
    route_row: dict[str, str],
    background_vehicle_count: int,
    spine_edges: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    route_id = route_row["route_id"]
    vehicle_id = f"emergency_{route_id}"
    route_edges = route_row["route_edges"].split()
    run_dir = args.run_root / route_id
    emergency_route_xml = run_dir / f"{vehicle_id}.rou.xml"
    log_lines = [f"route_id={route_id}"]
    validation_failures = validate_route_edges(sumo_net, route_edges)
    if validation_failures:
        row = {
            "route_id": route_id,
            "target_edge_id": route_row.get("target_edge_id", ""),
            "sumo_exit_code": "",
            "emergency_departed": False,
            "emergency_arrived": False,
            "emergency_teleport": False,
            "emergency_travel_time": "",
            "route_error_count": len(validation_failures),
            "background_departed": "",
            "background_arrived": "",
            "background_teleported": "",
            "background_teleport_ratio": "",
            "network_avg_speed_kmh": "",
            "spine_avg_speed_kmh": "",
            "emergency_corridor_avg_speed_kmh": "",
            "final_status": "FAIL",
            "failure_reason": ";".join(validation_failures[:10]),
            "run_dir": rel(run_dir),
        }
        return row, [], [*log_lines, f"validation=FAIL {row['failure_reason']}"]

    write_emergency_route_xml(emergency_route_xml, route_row, vehicle_id, args.emergency_depart)
    paths = write_sumo_files(run_dir, args.net, args.background_route, emergency_route_xml, args.time_to_teleport, args.collision_action)
    completed = subprocess.run([sumo, "-c", str(paths["sumocfg"])], cwd=PROJECT_ROOT, check=False, capture_output=True, text=True, timeout=args.timeout_sec)
    paths["stdout"].write_text(completed.stdout, encoding="utf-8")
    paths["stderr"].write_text(completed.stderr, encoding="utf-8")

    summary_metrics = parse_summary_output(paths["summary"])
    trip = parse_tripinfo(paths["tripinfo"], vehicle_id)
    edge_data = parse_edge_data(paths["edge_data"])
    spine_speed, spine_weighting, spine_weight = weighted_speed_kmh(edge_data, spine_edges)
    emergency_speed, emergency_weighting, emergency_weight = weighted_speed_kmh(edge_data, set(route_edges))
    route_errors = route_error_count(completed.stderr)
    emergency_tp = emergency_teleport_lines(completed.stderr, vehicle_id)
    emergency_arrived = bool(trip["emergency_arrived"])
    emergency_departed = summary_metrics["departed_count_total"] > background_vehicle_count or emergency_arrived
    emergency_teleport = bool(emergency_tp)
    background_departed = max(int(summary_metrics["departed_count_total"]) - (1 if emergency_departed else 0), 0)
    background_arrived = max(int(summary_metrics["arrived_count_total"]) - (1 if emergency_arrived else 0), 0)
    background_teleported = max(int(summary_metrics["teleport_count"]) - (1 if emergency_teleport else 0), 0)
    background_teleport_ratio = background_teleported / background_departed if background_departed else 0.0

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
    if background_teleported > 0:
        warnings.append("background_teleports_present")
    final_status = "FAIL" if failures else "WARNING" if warnings else "PASS"
    row = {
        "route_id": route_id,
        "target_edge_id": route_row.get("target_edge_id", ""),
        "sumo_exit_code": completed.returncode,
        "emergency_departed": emergency_departed,
        "emergency_arrived": emergency_arrived,
        "emergency_teleport": emergency_teleport,
        "emergency_travel_time": trip["emergency_travel_time"],
        "route_error_count": route_errors,
        "background_departed": background_departed,
        "background_arrived": background_arrived,
        "background_teleported": background_teleported,
        "background_teleport_ratio": round(background_teleport_ratio, 6),
        "network_avg_speed_kmh": round(float(summary_metrics["network_avg_speed_kmh"]), 6),
        "spine_avg_speed_kmh": round(spine_speed, 6) if spine_speed is not None else "",
        "emergency_corridor_avg_speed_kmh": round(emergency_speed, 6) if emergency_speed is not None else "",
        "network_summary_mean_speed_mps": round(float(summary_metrics["network_summary_mean_speed_mps"]), 6),
        "spine_speed_weighting": spine_weighting,
        "spine_speed_weight_sum": round(spine_weight, 6),
        "emergency_corridor_speed_weighting": emergency_weighting,
        "emergency_corridor_speed_weight_sum": round(emergency_weight, 6),
        "time_to_teleport": args.time_to_teleport,
        "collision_action": args.collision_action,
        "emergency_depart": args.emergency_depart,
        "sim_end_time": summary_metrics["sim_end_time"],
        "sumo_end_policy": "no_explicit_end_run_until_all_vehicles_finished",
        "final_status": final_status,
        "failure_reason": ";".join(failures),
        "warning_reason": ";".join(warnings),
        "emergency_teleport_evidence": emergency_tp,
        "run_dir": rel(run_dir),
        "sumocfg": rel(paths["sumocfg"]),
        "tripinfo": rel(paths["tripinfo"]),
        "summary_output": rel(paths["summary"]),
        "edgeData_output": rel(paths["edge_data"]),
        "stderr_log": rel(paths["stderr"]),
    }
    speed_rows = [
        {
            "route_id": route_id,
            "category": "network",
            "mean_speed_kmh": row["network_avg_speed_kmh"],
            "speed_delta_to_20kmh": round(float(row["network_avg_speed_kmh"]) - TARGET_SPEED_KMH, 6),
            "weighting": "summary_running_vehicle_seconds",
            "weight_sum": "",
        },
        {
            "route_id": route_id,
            "category": "spine",
            "mean_speed_kmh": row["spine_avg_speed_kmh"],
            "speed_delta_to_20kmh": round(float(row["spine_avg_speed_kmh"]) - TARGET_SPEED_KMH, 6) if row["spine_avg_speed_kmh"] != "" else "",
            "weighting": spine_weighting,
            "weight_sum": round(spine_weight, 6),
        },
        {
            "route_id": route_id,
            "category": "emergency_route_corridor",
            "mean_speed_kmh": row["emergency_corridor_avg_speed_kmh"],
            "speed_delta_to_20kmh": round(float(row["emergency_corridor_avg_speed_kmh"]) - TARGET_SPEED_KMH, 6)
            if row["emergency_corridor_avg_speed_kmh"] != ""
            else "",
            "weighting": emergency_weighting,
            "weight_sum": round(emergency_weight, 6),
        },
    ]
    log_lines.extend(
        [
            f"sumo_exit_code={completed.returncode}",
            f"emergency_arrived={emergency_arrived}",
            f"emergency_teleport={emergency_teleport}",
            f"background_teleport_ratio={background_teleport_ratio:.6f}",
            f"final_status={final_status}",
        ]
    )
    return row, speed_rows, log_lines


def append_step10_doc(summary: dict[str, Any]) -> None:
    marker = "## 19-Route B0 Baseline Smoke"
    current = STEP10_DOC.read_text(encoding="utf-8") if STEP10_DOC.is_file() else "# Step 10\n"
    base = current.split(marker)[0].rstrip()
    text = f"""{base}

{marker}

0.15x imputed background demand를 고정 입력으로 사용해 spine-v2 emergency route 19개 전체를 B0 no-control 조건에서 route별 1회씩 실행했다.

- final status: `{summary['final_status']}`
- background route: `{summary['background_route']}`
- route count: `{summary['route_count']}`
- PASS/WARNING/FAIL: `{summary['status_counts']}`
- emergency teleport route count: `{summary['emergency_teleport_route_count']}`
- route error route count: `{summary['route_error_route_count']}`
- Step 11B allowed: `{summary['step11b_allowed']}`
- summary CSV: `results/metrics/b0_baseline_19route_smoke_summary.csv`
- summary JSON: `results/metrics/b0_baseline_19route_smoke_summary.json`
- speed CSV: `results/metrics/b0_baseline_19route_speed_by_route.csv`
"""
    STEP10_DOC.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    generated_at = utc_now()
    lines = ["Step 10 B0 19-route baseline smoke", "====================================", f"generated_at: {generated_at}"]
    try:
        args.net = args.net.resolve()
        args.background_route = args.background_route.resolve()
        args.emergency_routes = args.emergency_routes.resolve()
        args.spine_edges = args.spine_edges.resolve()
        args.run_root = args.run_root.resolve()
        for path in [args.net, args.background_route, args.emergency_routes, args.spine_edges]:
            if not path.is_file():
                raise BatchSmokeError(f"missing_file: {path}")
        sumo = shutil.which("sumo")
        if sumo is None:
            raise BatchSmokeError("missing_executable: sumo")
        route_rows = read_csv(args.emergency_routes)
        if len(route_rows) != 19:
            raise BatchSmokeError(f"expected 19 emergency routes, found {len(route_rows)}")
        background_vehicle_count = count_vehicles(args.background_route)
        spine_edges = read_spine_edges(args.spine_edges)
        sumo_net = read_sumo_net(str(args.net))
        lines.append(f"background_vehicle_count: {background_vehicle_count}")
        result_rows: list[dict[str, Any]] = []
        speed_rows: list[dict[str, Any]] = []
        for route_row in route_rows:
            row, route_speed_rows, route_lines = run_one_route(sumo, sumo_net, args, route_row, background_vehicle_count, spine_edges)
            result_rows.append(row)
            speed_rows.extend(route_speed_rows)
            lines.extend(route_lines)
        status_counts = {status: sum(1 for row in result_rows if row["final_status"] == status) for status in ["PASS", "WARNING", "FAIL"]}
        emergency_teleport_route_count = sum(1 for row in result_rows if row.get("emergency_teleport") is True)
        route_error_route_count = sum(1 for row in result_rows if int(row.get("route_error_count") or 0) > 0)
        severe_failure_count = emergency_teleport_route_count + route_error_route_count
        step11b_allowed = severe_failure_count < 2
        final_status = "FAIL" if status_counts["FAIL"] else "WARNING" if status_counts["WARNING"] else "PASS"
        summary = {
            "generated_at": generated_at,
            "final_status": final_status,
            "active_net": rel(args.net),
            "background_route": rel(args.background_route),
            "background_vehicle_count": background_vehicle_count,
            "emergency_routes": rel(args.emergency_routes),
            "route_count": len(result_rows),
            "status_counts": status_counts,
            "emergency_teleport_route_count": emergency_teleport_route_count,
            "route_error_route_count": route_error_route_count,
            "step11b_allowed": step11b_allowed,
            "step11b_blocker": "" if step11b_allowed else "multiple emergency teleport/route-error failures in Step 10 B0 batch",
            "time_to_teleport": args.time_to_teleport,
            "collision_action": args.collision_action,
            "sumo_end_policy": "no_explicit_end_run_until_all_vehicles_finished",
            "results": result_rows,
            "outputs": [rel(SUMMARY_CSV), rel(SUMMARY_JSON), rel(SPEED_BY_ROUTE_CSV), rel(LOG_PATH), rel(STEP10_DOC)],
        }
        write_csv(SUMMARY_CSV, result_rows, csv_fields(result_rows))
        write_csv(SPEED_BY_ROUTE_CSV, speed_rows, csv_fields(speed_rows))
        write_json(SUMMARY_JSON, summary)
        append_step10_doc(summary)
        lines.extend(
            [
                f"status_counts: {status_counts}",
                f"emergency_teleport_route_count: {emergency_teleport_route_count}",
                f"route_error_route_count: {route_error_route_count}",
                f"step11b_allowed: {step11b_allowed}",
                f"final_status: {final_status}",
                f"summary_json: {rel(SUMMARY_JSON)}",
            ]
        )
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if final_status in {"PASS", "WARNING"} else 1
    except (BatchSmokeError, OSError, ET.ParseError, subprocess.TimeoutExpired, ValueError, RuntimeError, ImportError) as exc:
        lines.extend(["final_status: FAIL", f"blocker: {exc}"])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
