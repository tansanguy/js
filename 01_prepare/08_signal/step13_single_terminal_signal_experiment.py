#!/usr/bin/env python3
"""Run B1 single-terminal signal experiments.

The experiment enables exactly one priority terminal at a time. All other
route-relevant TLS decisions are logged as skipped so terminal contribution can
be inspected without B2 or optimization.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import read_sumo_net  # noqa: E402


DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
DEFAULT_BACKGROUND = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml"
DEFAULT_ROUTES = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
DEFAULT_B0_SUMMARY = PROJECT_ROOT / "results/metrics/b0_baseline_19route_smoke_summary.json"
DEFAULT_TERMINALS = PROJECT_ROOT / "data_prepared/signals/priority_terminal_candidates.csv"
DEFAULT_TLS_AUDIT = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_spine_v2.csv"
DEFAULT_B1_CONFIG = PROJECT_ROOT / "configs/b1_priority_signal_config.json"
EXPERIMENT_CONFIG = PROJECT_ROOT / "configs/b1_single_terminal_experiment_config.json"
ER002_SUMMARY_CSV = PROJECT_ROOT / "results/metrics/b1_single_terminal_er_acc_002_summary.csv"
ER002_SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b1_single_terminal_er_acc_002_summary.json"
ER002_EVENTS_CSV = PROJECT_ROOT / "results/metrics/b1_single_terminal_er_acc_002_signal_events.csv"
ROUTE18_SUMMARY_CSV = PROJECT_ROOT / "results/metrics/b1_single_terminal_18route_summary.csv"
ROUTE18_SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b1_single_terminal_18route_summary.json"
ROUTE18_EVENTS_CSV = PROJECT_ROOT / "results/metrics/b1_single_terminal_18route_signal_events.csv"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step13_single_terminal_signal_experiment.log"
STEP13_DOC = PROJECT_ROOT / "docs/Step13_single_terminal_signal_experiment.md"
RUN_ROOT = PROJECT_ROOT / "runs/b1_single_terminal_signal_experiment"
TARGET_SPEED_FALLBACK = 8.33


class Step13Error(RuntimeError):
    """Expected Step 13 failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run single-terminal B1 signal experiment.")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND)
    parser.add_argument("--emergency-routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--b0-summary", type=Path, default=DEFAULT_B0_SUMMARY)
    parser.add_argument("--terminals", type=Path, default=DEFAULT_TERMINALS)
    parser.add_argument("--tls-audit", type=Path, default=DEFAULT_TLS_AUDIT)
    parser.add_argument("--b1-config", type=Path, default=DEFAULT_B1_CONFIG)
    parser.add_argument("--time-to-teleport", type=int, default=1200)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--timeout-steps", type=int, default=7200)
    parser.add_argument("--run-18route-sweep", action="store_true", default=True)
    parser.add_argument("--max-terminals", type=int, default=0, help="Debug limiter. 0 means all PASS terminals.")
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


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


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


def as_int(value: Any) -> int:
    if value in {"", None}:
        return 0
    return int(float(value))


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def parse_xml_with_retry(path: Path, attempts: int = 6, delay_sec: float = 0.5) -> ET.ElementTree:
    last_error: ET.ParseError | None = None
    for attempt in range(attempts):
        try:
            return ET.parse(path)
        except ET.ParseError as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay_sec)
    assert last_error is not None
    raise last_error


def count_vehicles(route_file: Path) -> int:
    count = 0
    for _event, elem in ET.iterparse(route_file, events=("end",)):
        if elem.tag == "vehicle":
            count += 1
        elem.clear()
    return count


def select_route(routes: list[dict[str, str]], route_id: str) -> dict[str, str]:
    matches = [row for row in routes if row.get("route_id") == route_id]
    if not matches:
        raise Step13Error(f"route not found: {route_id}")
    return matches[0]


def route_edge_starts(net_path: Path, route_edges: list[str]) -> list[float]:
    sumo_net = read_sumo_net(str(net_path))
    starts = []
    cumulative = 0.0
    for edge_id in route_edges:
        starts.append(cumulative)
        cumulative += float(sumo_net.getEdge(edge_id).getLength())
    return starts


def validate_route_edges(net_path: Path, edge_ids: list[str]) -> list[str]:
    sumo_net = read_sumo_net(str(net_path))
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


def b0_valid_route_ids(b0_summary: dict[str, Any]) -> list[str]:
    result = []
    for row in b0_summary.get("results", []):
        if row.get("route_id") == "ER_ACC_013":
            continue
        if (
            int(row.get("sumo_exit_code") or 0) == 0
            and row.get("emergency_arrived") is True
            and row.get("emergency_teleport") is False
            and int(row.get("route_error_count") or 0) == 0
        ):
            result.append(row["route_id"])
    return result


def write_emergency_route_xml(path: Path, route_row: dict[str, str], vehicle_id: str) -> None:
    root = ET.Element("routes")
    vtype = ET.SubElement(
        root,
        "vType",
        {
            "id": "b1_single_terminal_emergency_type",
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
            "type": "b1_single_terminal_emergency_type",
            "route": route_row["route_id"],
            "depart": "0",
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
        {"id": "b1_single_terminal_edge_data", "file": str(paths["edge_data"]), "begin": "0", "end": "86400", "freq": "86400", "excludeEmpty": "false"},
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


def parse_summary(path: Path) -> dict[str, Any]:
    root = parse_xml_with_retry(path).getroot()
    last_step = None
    max_teleports = 0
    for step in root.findall("step"):
        last_step = step
        max_teleports = max(max_teleports, int(float(step.get("teleports", "0"))))
    if last_step is None:
        raise Step13Error(f"summary has no step: {rel(path)}")
    return {
        "departed_total": int(float(last_step.get("inserted", "0"))),
        "arrived_total": int(float(last_step.get("arrived", "0"))),
        "teleport_count": max_teleports,
        "sim_end_time": float(last_step.get("time", "0")),
    }


def parse_tripinfo(path: Path, vehicle_id: str) -> dict[str, Any]:
    root = parse_xml_with_retry(path).getroot()
    for tripinfo in root.findall("tripinfo"):
        if tripinfo.get("id") == vehicle_id:
            return {
                "emergency_arrived": True,
                "emergency_travel_time": float(tripinfo.get("duration", "0")),
                "arrival": float(tripinfo.get("arrival", "0") or 0),
            }
    return {"emergency_arrived": False, "emergency_travel_time": None, "arrival": None}


def route_error_count(stderr: str) -> int:
    lower = stderr.lower()
    return lower.count("route error") + lower.count("has no valid route") + lower.count("is not connected")


def emergency_teleport_lines(stderr: str, vehicle_id: str) -> list[str]:
    return [line for line in stderr.splitlines() if vehicle_id in line and "teleport" in line.lower()]


def import_traci() -> Any:
    try:
        import traci  # type: ignore

        return traci
    except ImportError:
        sumo_home = os.environ.get("SUMO_HOME")
        if sumo_home:
            tools = Path(sumo_home) / "tools"
            if tools.is_dir() and str(tools) not in sys.path:
                sys.path.insert(0, str(tools))
        try:
            import traci  # type: ignore

            return traci
        except ImportError as exc:
            raise Step13Error(f"missing_python_module: traci; SUMO_HOME={os.environ.get('SUMO_HOME', '')}") from exc


def load_tls_plan(tls_audit: list[dict[str, str]], route_id: str) -> list[dict[str, Any]]:
    rows = []
    for row in tls_audit:
        if row.get("route_id") != route_id:
            continue
        rows.append(
            {
                "route_id": route_id,
                "tls_id": row["tls_id"],
                "junction_id": row["junction_id"],
                "incoming": row["emergency_incoming_edge"],
                "outgoing": row["emergency_outgoing_edge"],
                "distance": float(row["distance_from_route_start_m"] or 0),
                "green_phases": [int(value) for value in row.get("green_phase_indices", "").split() if value.isdigit()],
                "is_controllable": row.get("is_controllable") == "True",
                "audit_reason": row.get("audit_reason", ""),
            }
        )
    rows.sort(key=lambda item: item["distance"])
    return rows


def run_one(
    args: argparse.Namespace,
    route_row: dict[str, str],
    terminal: dict[str, str],
    tls_audit: list[dict[str, str]],
    b1_config: dict[str, Any],
    b0_by_route: dict[str, dict[str, Any]],
    run_group: str,
    background_count: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    traci = import_traci()
    sumo = shutil.which("sumo")
    if sumo is None:
        raise Step13Error("missing_executable: sumo")
    route_id = route_row["route_id"]
    terminal_id = terminal["terminal_id"]
    tls_id_enabled = terminal["tls_id"]
    experiment_id = f"{run_group}_{route_id}_{terminal_id}"
    run_dir = RUN_ROOT / run_group / route_id / terminal_id
    vehicle_id = f"emergency_{route_id}_{terminal_id}"
    route_edges = route_row["route_edges"].split()
    validation_failures = validate_route_edges(args.net, route_edges)
    if validation_failures:
        row = base_result_row(experiment_id, terminal, route_id, b0_by_route, "FAIL", ";".join(validation_failures[:5]))
        return row, []
    emergency_route_xml = run_dir / f"{vehicle_id}.rou.xml"
    write_emergency_route_xml(emergency_route_xml, route_row, vehicle_id)
    paths = write_sumo_files(run_dir, args.net, args.background_route, emergency_route_xml, args.time_to_teleport, args.collision_action)
    tls_plan = load_tls_plan(tls_audit, route_id)
    edge_starts = route_edge_starts(args.net, route_edges)
    events: list[dict[str, Any]] = []
    touched: dict[str, str] = {}
    controller_started = False
    cmd = [sumo, "-c", str(paths["sumocfg"]), "--error-log", str(paths["stderr"])]
    with paths["stdout"].open("w", encoding="utf-8") as stdout:
        traci.start(cmd, stdout=stdout)
        controller_started = True
        try:
            step = 0
            while traci.simulation.getMinExpectedNumber() > 0 and step <= args.timeout_steps:
                traci.simulationStep()
                sim_time = traci.simulation.getTime()
                if vehicle_id in traci.vehicle.getIDList():
                    road_id = traci.vehicle.getRoadID(vehicle_id)
                    speed = max(float(traci.vehicle.getSpeed(vehicle_id)), float(b1_config.get("fallback_v_e_mps", TARGET_SPEED_FALLBACK)))
                    route_index = int(traci.vehicle.getRouteIndex(vehicle_id))
                    lane_position = float(traci.vehicle.getLanePosition(vehicle_id))
                    current_distance = edge_starts[route_index] + lane_position if 0 <= route_index < len(edge_starts) else 0.0
                    next_tls = None
                    for candidate in tls_plan:
                        if candidate["tls_id"] in touched:
                            continue
                        if candidate["distance"] + 1.0 < current_distance:
                            continue
                        next_tls = candidate
                        break
                    if next_tls is not None:
                        eta = 0.0 if road_id == next_tls["incoming"] else max((next_tls["distance"] - current_distance) / speed, 0.0)
                        if eta <= float(b1_config.get("t_lead", 30)) or road_id == next_tls["incoming"]:
                            event_base = {
                                "experiment_id": experiment_id,
                                "enabled_terminal_id": terminal_id,
                                "enabled_tls_id": tls_id_enabled,
                                "time": sim_time,
                                "route_id": route_id,
                                "vehicle_id": vehicle_id,
                                "tls_id": next_tls["tls_id"],
                                "junction_id": next_tls["junction_id"],
                                "incoming": next_tls["incoming"],
                                "outgoing": next_tls["outgoing"],
                                "eta_sec": round(eta, 3),
                                "current_road_id": road_id,
                            }
                            if next_tls["tls_id"] != tls_id_enabled:
                                touched[next_tls["tls_id"]] = "disabled"
                                events.append({**event_base, "action": "skip", "reason": "disabled_by_single_terminal_experiment"})
                            elif not next_tls["is_controllable"]:
                                touched[next_tls["tls_id"]] = "skip"
                                events.append({**event_base, "action": "skip", "reason": next_tls["audit_reason"] or "audit_not_controllable"})
                            else:
                                current_phase = int(traci.trafficlight.getPhase(next_tls["tls_id"]))
                                green = next_tls["green_phases"]
                                if current_phase in green:
                                    traci.trafficlight.setPhaseDuration(next_tls["tls_id"], min(float(b1_config.get("G_ext", 30)), 5.0))
                                    touched[next_tls["tls_id"]] = "extend_green"
                                    events.append({**event_base, "action": "extend_green", "reason": "current_phase_already_green", "phase": current_phase})
                                else:
                                    next_phase = (current_phase + 1) % len(traci.trafficlight.getAllProgramLogics(next_tls["tls_id"])[0].phases)
                                    if next_phase in green:
                                        traci.trafficlight.setPhase(next_tls["tls_id"], next_phase)
                                        traci.trafficlight.setPhaseDuration(next_tls["tls_id"], min(float(b1_config.get("G_ext", 30)), 5.0))
                                        touched[next_tls["tls_id"]] = "advance_to_next_green"
                                        events.append({**event_base, "action": "advance_to_next_green", "reason": "next_phase_is_emergency_green_existing_sequence", "phase": next_phase})
                                    else:
                                        touched[next_tls["tls_id"]] = "skip"
                                        events.append(
                                            {
                                                **event_base,
                                                "action": "skip",
                                                "reason": "safe_green_not_current_or_next_phase",
                                                "phase": current_phase,
                                                "green_phase_indices": " ".join(map(str, green)),
                                            }
                                        )
                step += int(b1_config.get("tau", 1))
            if step > args.timeout_steps:
                events.append({"experiment_id": experiment_id, "enabled_terminal_id": terminal_id, "route_id": route_id, "vehicle_id": vehicle_id, "action": "timeout", "reason": "controller_timeout_steps"})
        finally:
            traci.close(False)
    stderr = paths["stderr"].read_text(encoding="utf-8", errors="replace") if paths["stderr"].is_file() else ""
    summary = parse_summary(paths["summary"])
    trip = parse_tripinfo(paths["tripinfo"], vehicle_id)
    emergency_tp = emergency_teleport_lines(stderr, vehicle_id)
    emergency_departed = summary["departed_total"] > background_count or trip["emergency_arrived"]
    emergency_arrived = bool(trip["emergency_arrived"])
    route_errors = route_error_count(stderr)
    bg_departed = max(summary["departed_total"] - (1 if emergency_departed else 0), 0)
    bg_tp = max(summary["teleport_count"] - (1 if emergency_tp else 0), 0)
    actions = [event.get("action") for event in events]
    failures = []
    warnings = []
    if not controller_started:
        failures.append("controller_not_started")
    if not emergency_departed:
        failures.append("emergency_not_departed")
    if not emergency_arrived:
        failures.append("emergency_not_arrived")
    if emergency_tp:
        failures.append("emergency_teleport_detected")
    if route_errors > 0:
        failures.append("route_error_count_gt_0")
    if bg_tp > 0:
        warnings.append("background_teleports_present")
    final_status = "FAIL" if failures else "WARNING" if warnings else "PASS"
    b0_travel_time = float(b0_by_route[route_id]["emergency_travel_time"])
    b1_travel_time = trip["emergency_travel_time"]
    delta = (float(b1_travel_time) - b0_travel_time) if b1_travel_time is not None else None
    result = {
        "experiment_id": experiment_id,
        "enabled_terminal_id": terminal_id,
        "tls_id": tls_id_enabled,
        "route_id": route_id,
        "b0_travel_time": b0_travel_time,
        "b1_travel_time": b1_travel_time,
        "travel_time_delta_sec": round(delta, 6) if delta is not None else "",
        "travel_time_improvement_pct": round((-delta / b0_travel_time) * 100.0, 6) if delta is not None and b0_travel_time else "",
        "emergency_departed": emergency_departed,
        "emergency_arrived": emergency_arrived,
        "emergency_teleport": bool(emergency_tp),
        "route_error_count": route_errors,
        "controlled_tls_count": len({event["tls_id"] for event in events if event.get("action") in {"extend_green", "advance_to_next_green"} and event.get("tls_id")}),
        "skipped_tls_count": len({event["tls_id"] for event in events if event.get("action") == "skip" and event.get("tls_id")}),
        "intervention_count": sum(1 for action in actions if action in {"extend_green", "advance_to_next_green"}),
        "green_extension_count": actions.count("extend_green"),
        "phase_switch_count": actions.count("advance_to_next_green"),
        "disabled_tls_count": sum(1 for event in events if event.get("reason") == "disabled_by_single_terminal_experiment"),
        "background_teleport_ratio": round(bg_tp / bg_departed, 6) if bg_departed else 0.0,
        "sumo_exit_code": 0 if controller_started else 1,
        "final_status": final_status,
        "failure_reason": ";".join(failures),
        "warning_reason": ";".join(warnings),
        "run_dir": rel(run_dir),
        "stderr_log": rel(paths["stderr"]),
    }
    return result, events


def base_result_row(experiment_id: str, terminal: dict[str, str], route_id: str, b0_by_route: dict[str, dict[str, Any]], status: str, reason: str) -> dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "enabled_terminal_id": terminal["terminal_id"],
        "tls_id": terminal["tls_id"],
        "route_id": route_id,
        "b0_travel_time": b0_by_route.get(route_id, {}).get("emergency_travel_time", ""),
        "b1_travel_time": "",
        "travel_time_delta_sec": "",
        "travel_time_improvement_pct": "",
        "emergency_departed": False,
        "emergency_arrived": False,
        "emergency_teleport": False,
        "route_error_count": "",
        "controlled_tls_count": 0,
        "skipped_tls_count": 0,
        "intervention_count": 0,
        "green_extension_count": 0,
        "phase_switch_count": 0,
        "disabled_tls_count": 0,
        "background_teleport_ratio": "",
        "sumo_exit_code": "",
        "final_status": status,
        "failure_reason": reason,
    }


def summarize_sweep(rows: list[dict[str, Any]], terminals: list[dict[str, str]], route_ids: list[str], run_status: str) -> dict[str, Any]:
    status_counts = {status: sum(1 for row in rows if row["final_status"] == status) for status in ["PASS", "WARNING", "FAIL"]}
    by_terminal = []
    for terminal in terminals:
        tls_rows = [row for row in rows if row["enabled_terminal_id"] == terminal["terminal_id"]]
        improvements = [float(row["travel_time_improvement_pct"]) for row in tls_rows if row.get("travel_time_improvement_pct") not in {"", None}]
        deltas = [float(row["travel_time_delta_sec"]) for row in tls_rows if row.get("travel_time_delta_sec") not in {"", None}]
        covered_routes = sorted({row["route_id"] for row in tls_rows if as_bool(row.get("emergency_departed"))})
        controlled_routes = sorted({row["route_id"] for row in tls_rows if as_int(row.get("controlled_tls_count")) > 0})
        by_terminal.append(
            {
                "terminal_id": terminal["terminal_id"],
                "tls_id": terminal["tls_id"],
                "run_count": len(tls_rows),
                "route_coverage_count": len(covered_routes),
                "route_coverage_ratio": round(len(covered_routes) / len(route_ids), 6) if route_ids else None,
                "covered_route_ids": covered_routes,
                "controlled_route_count": len(controlled_routes),
                "controlled_route_ids": controlled_routes,
                "pass_count": sum(1 for row in tls_rows if row["final_status"] == "PASS"),
                "warning_count": sum(1 for row in tls_rows if row["final_status"] == "WARNING"),
                "fail_count": sum(1 for row in tls_rows if row["final_status"] == "FAIL"),
                "avg_improvement_pct": round(sum(improvements) / len(improvements), 6) if improvements else None,
                "avg_delta_sec": round(sum(deltas) / len(deltas), 6) if deltas else None,
                "controlled_tls_count": sum(as_int(row.get("controlled_tls_count")) for row in tls_rows),
                "skipped_tls_count": sum(as_int(row.get("skipped_tls_count")) for row in tls_rows),
                "disabled_tls_count": sum(as_int(row.get("disabled_tls_count")) for row in tls_rows),
                "intervention_count": sum(as_int(row.get("intervention_count")) for row in tls_rows),
                "green_extension_count": sum(as_int(row.get("green_extension_count")) for row in tls_rows),
                "phase_switch_count": sum(as_int(row.get("phase_switch_count")) for row in tls_rows),
            }
        )
    comparable = [item for item in by_terminal if item["avg_improvement_pct"] is not None]
    best = max(comparable, key=lambda item: item["avg_improvement_pct"]) if comparable else None
    worst = min(comparable, key=lambda item: item["avg_improvement_pct"]) if comparable else None
    b0_values = [float(row["b0_travel_time"]) for row in rows if row.get("b0_travel_time") not in {"", None}]
    b1_values = [float(row["b1_travel_time"]) for row in rows if row.get("b1_travel_time") not in {"", None}]
    return {
        "generated_at": utc_now(),
        "run_status": run_status,
        "terminal_count": len(terminals),
        "route_count": len(route_ids),
        "row_count": len(rows),
        "status_counts": status_counts,
        "emergency_teleport_routes": sorted({row["route_id"] for row in rows if as_bool(row.get("emergency_teleport"))}),
        "controller_failure_routes": sorted({row["route_id"] for row in rows if "controller" in str(row.get("failure_reason", ""))}),
        "average_b0_travel_time": round(sum(b0_values) / len(b0_values), 6) if b0_values else None,
        "average_b1_travel_time": round(sum(b1_values) / len(b1_values), 6) if b1_values else None,
        "best_terminal": best,
        "worst_terminal": worst,
        "terminal_summaries": by_terminal,
    }


def run_sweep(
    args: argparse.Namespace,
    run_group: str,
    route_ids: list[str],
    terminals: list[dict[str, str]],
    routes_by_id: dict[str, dict[str, str]],
    tls_audit: list[dict[str, str]],
    b1_config: dict[str, Any],
    b0_by_route: dict[str, dict[str, Any]],
    background_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    emergency_tp_routes: set[str] = set()
    for route_id in route_ids:
        for terminal in terminals:
            row, run_events = run_one(args, routes_by_id[route_id], terminal, tls_audit, b1_config, b0_by_route, run_group, background_count)
            rows.append(row)
            events.extend(run_events)
            if row.get("emergency_teleport") is True:
                emergency_tp_routes.add(route_id)
            if len(emergency_tp_routes) >= 2:
                summary = summarize_sweep(rows, terminals, route_ids, "STOPPED_REPEATED_EMERGENCY_TELEPORT")
                return rows, events, summary
    return rows, events, summarize_sweep(rows, terminals, route_ids, "COMPLETE")


def write_experiment_config(terminals: list[dict[str, str]], route_ids: list[str], b1_config: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema": "b1_single_terminal_experiment_config.v1",
        "generated_at": utc_now(),
        "description": "Enable exactly one PASS priority terminal per run; skip all other TLS decisions.",
        "terminal_count": len(terminals),
        "terminal_ids": [row["terminal_id"] for row in terminals],
        "tls_ids": [row["tls_id"] for row in terminals],
        "route_ids": route_ids,
        "base_b1_config": b1_config,
        "control_policy": {
            "single_terminal_only": True,
            "disabled_tls_skip_reason": "disabled_by_single_terminal_experiment",
            "direct_phase_state_rewrite": False,
            "yellow_clearance_skip": False,
            "phase_sequence_policy": "existing_sequence_only",
        },
    }
    write_json(EXPERIMENT_CONFIG, payload)
    return payload


def terminal_markdown_table(summary: dict[str, Any]) -> str:
    rows = summary.get("terminal_summaries") or []
    if not rows:
        return "No terminal rows."
    lines = [
        "| terminal | tls_id | PASS | WARNING | FAIL | coverage | controlled_routes | controlled | skipped | disabled | interventions | avg_delta_sec | avg_improvement_pct |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {terminal_id} | {tls_id} | {pass_count} | {warning_count} | {fail_count} | "
            "{route_coverage_count} | {controlled_route_count} | {controlled_tls_count} | "
            "{skipped_tls_count} | {disabled_tls_count} | {intervention_count} | "
            "{avg_delta_sec} | {avg_improvement_pct} |".format(**row)
        )
    return "\n".join(lines)


def write_docs(er002_summary: dict[str, Any], route18_summary: dict[str, Any] | None) -> None:
    route18_text = "not run"
    if route18_summary:
        route18_text = (
            f"run_status `{route18_summary['run_status']}`, rows `{route18_summary['row_count']}`, "
            f"status `{route18_summary['status_counts']}`, best `{route18_summary.get('best_terminal')}`"
        )
    STEP13_DOC.write_text(
        f"""# Step 13 Single-Terminal Signal Experiment

## Purpose

This diagnostic experiment enables one corridor priority terminal at a time. It decomposes B1 behavior so each TLS contribution can be reviewed independently before selected-terminal B1 or B2 optimization.

## Method

- Source terminals: `data_prepared/signals/priority_terminal_candidates.csv`
- Terminal filter: `install_candidate_status=PASS`
- Background demand: `data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml`
- Excluded route: `ER_ACC_013` because B0 has emergency teleport.
- Safety: no direct phase-state rewrite, no yellow/clearance skip, existing phase sequence only.

## ER_ACC_002 Sweep

- run status: `{er002_summary['run_status']}`
- terminal count: `{er002_summary['terminal_count']}`
- status counts: `{er002_summary['status_counts']}`
- best terminal: `{er002_summary.get('best_terminal')}`
- worst terminal: `{er002_summary.get('worst_terminal')}`

### ER_ACC_002 Terminal Counts

{terminal_markdown_table(er002_summary)}

## 18-Route Sweep

{route18_text}

### 18-Route Terminal Counts

{terminal_markdown_table(route18_summary) if route18_summary and route18_summary.get('terminal_summaries') else 'not run'}

## Next Step

Review best/worst terminals and build a selected-terminal B1 candidate using only terminals with stable positive effect. This is still not B2 or Bayesian Optimization.
""",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    args.net = args.net.resolve()
    args.background_route = args.background_route.resolve()
    args.emergency_routes = args.emergency_routes.resolve()
    args.b0_summary = args.b0_summary.resolve()
    args.terminals = args.terminals.resolve()
    args.tls_audit = args.tls_audit.resolve()
    args.b1_config = args.b1_config.resolve()
    lines = ["Step 13 single-terminal signal experiment", "========================================", f"generated_at: {utc_now()}"]
    for path in [args.net, args.background_route, args.emergency_routes, args.b0_summary, args.terminals, args.tls_audit, args.b1_config]:
        if not path.is_file():
            raise Step13Error(f"missing_file: {path}")
    routes = read_csv(args.emergency_routes)
    routes_by_id = {row["route_id"]: row for row in routes}
    b0_summary = read_json(args.b0_summary)
    b0_by_route = {row["route_id"]: row for row in b0_summary["results"]}
    tls_audit = read_csv(args.tls_audit)
    b1_config = read_json(args.b1_config)
    terminals = [row for row in read_csv(args.terminals) if row.get("install_candidate_status") == "PASS"]
    terminals.sort(key=lambda row: row["terminal_id"])
    if args.max_terminals > 0:
        terminals = terminals[: args.max_terminals]
    if not terminals:
        raise Step13Error("no PASS terminal candidates")
    valid_route_ids = b0_valid_route_ids(b0_summary)
    if "ER_ACC_002" not in valid_route_ids:
        raise Step13Error("ER_ACC_002 is not B0-valid")
    experiment_config = write_experiment_config(terminals, valid_route_ids, b1_config)
    background_count = count_vehicles(args.background_route)
    er002_rows, er002_events, er002_summary = run_sweep(args, "er_acc_002", ["ER_ACC_002"], terminals, routes_by_id, tls_audit, b1_config, b0_by_route, background_count)
    write_csv(ER002_SUMMARY_CSV, er002_rows, csv_fields(er002_rows))
    write_csv(ER002_EVENTS_CSV, er002_events, csv_fields(er002_events) if er002_events else ["experiment_id"])
    write_json(ER002_SUMMARY_JSON, {**er002_summary, "results": er002_rows, "experiment_config": rel(EXPERIMENT_CONFIG)})
    lines.append(f"er_acc_002_status_counts: {er002_summary['status_counts']}")
    route18_summary = None
    if er002_summary["emergency_teleport_routes"] or er002_summary["status_counts"].get("FAIL", 0) > 0:
        route18_summary = {"run_status": "BLOCKED_BY_ER_ACC_002_SWEEP", "blockers": ["ER_ACC_002 sweep had emergency teleport or FAIL"]}
    elif args.run_18route_sweep:
        route18_rows, route18_events, route18_summary = run_sweep(args, "b0_valid_18route", valid_route_ids, terminals, routes_by_id, tls_audit, b1_config, b0_by_route, background_count)
        write_csv(ROUTE18_SUMMARY_CSV, route18_rows, csv_fields(route18_rows))
        write_csv(ROUTE18_EVENTS_CSV, route18_events, csv_fields(route18_events) if route18_events else ["experiment_id"])
        write_json(ROUTE18_SUMMARY_JSON, {**route18_summary, "results": route18_rows, "experiment_config": rel(EXPERIMENT_CONFIG)})
        lines.append(f"18route_status_counts: {route18_summary['status_counts']}")
    else:
        route18_summary = {"run_status": "NOT_RUN", "blockers": ["--run-18route-sweep disabled"]}
    write_docs(er002_summary, route18_summary)
    final_summary = {
        "generated_at": utc_now(),
        "experiment_config": rel(EXPERIMENT_CONFIG),
        "terminal_count": len(terminals),
        "b0_valid_route_count": len(valid_route_ids),
        "er_acc_002_sweep": er002_summary,
        "route18_sweep": route18_summary,
        "outputs": [
            rel(EXPERIMENT_CONFIG),
            rel(ER002_SUMMARY_CSV),
            rel(ER002_SUMMARY_JSON),
            rel(ER002_EVENTS_CSV),
            rel(ROUTE18_SUMMARY_CSV),
            rel(ROUTE18_SUMMARY_JSON),
            rel(ROUTE18_EVENTS_CSV),
            rel(LOG_PATH),
            rel(STEP13_DOC),
        ],
    }
    write_json(PROJECT_ROOT / "results/metrics/b1_single_terminal_experiment_summary.json", final_summary)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines.append(f"terminal_count: {len(terminals)}")
    lines.append(f"b0_valid_route_count: {len(valid_route_ids)}")
    lines.append(f"final_summary: results/metrics/b1_single_terminal_experiment_summary.json")
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(final_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Step13Error, OSError, ET.ParseError, ValueError, RuntimeError) as exc:
        print(f"Step13 failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
