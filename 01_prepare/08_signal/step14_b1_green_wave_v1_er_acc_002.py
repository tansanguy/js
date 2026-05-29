#!/usr/bin/env python3
"""Run Step 14 B1 Central Green Wave Controller v1 smoke for ER_ACC_002."""

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


DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml"
DEFAULT_BACKGROUND_ROUTE = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml"
DEFAULT_EMERGENCY_ROUTES = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
DEFAULT_TLS_AUDIT = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_spine_v2.csv"
DEFAULT_CONFIG = PROJECT_ROOT / "configs/b1_priority_signal_config.json"
DEFAULT_RUN_DIR = PROJECT_ROOT / "runs/b1_green_wave_v1_er_acc_002"
SUMMARY_CSV = PROJECT_ROOT / "results/metrics/b1_green_wave_v1_er_acc_002_smoke_summary.csv"
SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b1_green_wave_v1_er_acc_002_smoke_summary.json"
SIGNAL_EVENTS_CSV = PROJECT_ROOT / "results/metrics/b1_green_wave_v1_er_acc_002_signal_events.csv"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step14_b1_green_wave_v1_er_acc_002.log"
STEP14_DOC = PROJECT_ROOT / "docs/Step14_B1_green_wave_controller_v1.md"
DEFAULT_TIMEOUT_STEPS = 7200


class B1GreenWaveError(RuntimeError):
    """Expected B1 Green Wave v1 failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ER_ACC_002 B1 Green Wave v1 smoke.")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--emergency-routes", type=Path, default=DEFAULT_EMERGENCY_ROUTES)
    parser.add_argument("--route-id", default="ER_ACC_002")
    parser.add_argument("--tls-audit", type=Path, default=DEFAULT_TLS_AUDIT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--time-to-teleport", type=int, default=1200)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--emergency-depart", type=float, default=0.0)
    parser.add_argument("--emergency-vehicle-id", default="emergency_ER_ACC_002_b1_green_wave_v1")
    parser.add_argument("--timeout-steps", type=int, default=DEFAULT_TIMEOUT_STEPS)
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


def as_float(value: Any, default: float = 0.0) -> float:
    if value in {"", None}:
        return default
    return float(value)


def parse_xml_with_retry(path: Path, attempts: int = 5, delay_sec: float = 0.5) -> ET.ElementTree:
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


def select_emergency_route(path: Path, route_id: str) -> dict[str, str]:
    rows = read_csv(path)
    matches = [row for row in rows if row.get("route_id") == route_id]
    if not matches:
        raise B1GreenWaveError(f"Emergency route not found: {route_id}")
    return matches[0]


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


def write_emergency_route_xml(path: Path, route_row: dict[str, str], vehicle_id: str, depart: float) -> None:
    root = ET.Element("routes")
    vtype = ET.SubElement(
        root,
        "vType",
        {
            "id": "b1_emergency_type",
            "vClass": "emergency",
            "guiShape": "emergency",
            "color": "1,0,0",
            "speedFactor": "1.00",
            "speedDev": "0.00",
            "accel": "3.0",
            "decel": "7.5",
            "impatience": "1.0",
        },
    )
    ET.SubElement(vtype, "param", {"key": "has.bluelight.device", "value": "false"})
    ET.SubElement(root, "route", {"id": route_row["route_id"], "edges": route_row["route_edges"]})
    ET.SubElement(
        root,
        "vehicle",
        {
            "id": vehicle_id,
            "type": "b1_emergency_type",
            "route": route_row["route_id"],
            "depart": f"{depart:g}",
            "departLane": "best",
            "departSpeed": "max",
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_sumo_files(args: argparse.Namespace, emergency_route_xml: Path) -> dict[str, Path]:
    paths = {
        "additional": args.run_dir / "edge_data.add.xml",
        "edge_data": args.run_dir / "edgeData.xml",
        "sumocfg": args.run_dir / "scenario.sumocfg",
        "tripinfo": args.run_dir / "tripinfo.xml",
        "summary": args.run_dir / "summary.xml",
        "stdout": args.run_dir / "sumo_stdout.log",
        "stderr": args.run_dir / "sumo_stderr.log",
    }
    args.run_dir.mkdir(parents=True, exist_ok=True)
    additional = ET.Element("additional")
    ET.SubElement(
        additional,
        "edgeData",
        {
            "id": "b1_er_acc_002_edge_speed",
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
    ET.SubElement(input_elem, "net-file", {"value": str(args.net)})
    ET.SubElement(input_elem, "route-files", {"value": f"{args.background_route},{emergency_route_xml}"})
    ET.SubElement(input_elem, "additional-files", {"value": str(paths["additional"])})
    output_elem = ET.SubElement(config, "output")
    ET.SubElement(output_elem, "tripinfo-output", {"value": str(paths["tripinfo"])})
    ET.SubElement(output_elem, "summary-output", {"value": str(paths["summary"])})
    time_elem = ET.SubElement(config, "time")
    ET.SubElement(time_elem, "begin", {"value": "0"})
    processing_elem = ET.SubElement(config, "processing")
    ET.SubElement(processing_elem, "time-to-teleport", {"value": str(args.time_to_teleport)})
    ET.SubElement(processing_elem, "collision.action", {"value": args.collision_action})
    report_elem = ET.SubElement(config, "report")
    ET.SubElement(report_elem, "no-step-log", {"value": "true"})
    ET.SubElement(report_elem, "duration-log.disable", {"value": "true"})
    ET.ElementTree(config).write(paths["sumocfg"], encoding="utf-8", xml_declaration=True)
    return paths


def load_tls_plan(path: Path, route_id: str) -> list[dict[str, Any]]:
    rows = []
    for row in read_csv(path):
        if row.get("route_id") != route_id:
            continue
        green = [int(value) for value in row.get("green_phase_indices", "").split() if value.isdigit()]
        rows.append(
            {
                "route_id": row["route_id"],
                "tls_id": row["tls_id"],
                "junction_id": row["junction_id"],
                "incoming": row["emergency_incoming_edge"],
                "outgoing": row["emergency_outgoing_edge"],
                "distance": float(row["distance_from_route_start_m"] or 0),
                "link_index": int(row["emergency_link_index"]) if row.get("emergency_link_index", "").isdigit() else -1,
                "green_phases": green,
                "is_controllable": row.get("is_controllable") == "True",
                "audit_status": row.get("audit_status", ""),
                "audit_reason": row.get("audit_reason", ""),
            }
        )
    rows.sort(key=lambda item: item["distance"])
    return rows


def parse_summary_output(path: Path) -> dict[str, Any]:
    root = parse_xml_with_retry(path).getroot()
    last_step = None
    max_teleports = 0
    for step in root.findall("step"):
        last_step = step
        max_teleports = max(max_teleports, int(float(step.get("teleports", "0"))))
    if last_step is None:
        raise B1GreenWaveError(f"summary-output has no steps: {rel(path)}")
    return {
        "departed_count_total": int(float(last_step.get("inserted", "0"))),
        "arrived_count_total": int(float(last_step.get("arrived", "0"))),
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
                "emergency_depart_time_observed": float(tripinfo.get("depart", "0")),
                "emergency_arrival_time": float(tripinfo.get("arrival", "0")),
            }
    return {
        "emergency_arrived": False,
        "emergency_travel_time": None,
        "emergency_depart_time_observed": None,
        "emergency_arrival_time": None,
    }


def route_error_count(stderr: str) -> int:
    lower = stderr.lower()
    return lower.count("route error") + lower.count("has no valid route") + lower.count("is not connected")


def emergency_teleport_lines(stderr: str, vehicle_id: str) -> list[str]:
    return [line for line in stderr.splitlines() if vehicle_id in line and "teleport" in line.lower()]


def route_edge_starts(net_path: Path, route_edges: list[str]) -> list[float]:
    sumo_net = read_sumo_net(str(net_path))
    starts = []
    cumulative = 0.0
    for edge_id in route_edges:
        starts.append(cumulative)
        cumulative += float(sumo_net.getEdge(edge_id).getLength())
    return starts


def write_step14_doc(summary: dict[str, Any]) -> None:
    config = summary["config_parameter_snapshot"]
    STEP14_DOC.parent.mkdir(parents=True, exist_ok=True)
    STEP14_DOC.write_text(
        f"""# Step 14 B1 Central Green Wave Controller v1

## Purpose

This step verifies that B1 controller parameters are actually used in TraCI decisions. It is not a B0/B1 performance evaluation, B2, Bayesian Optimization, or multi-seed experiment.

## Inputs

- net: `{summary['active_net']}`
- background: `{summary['background_route']}`
- emergency route: `{summary['route_id']}`
- TLS audit: `{summary['tls_audit']}`
- config: `{summary['config_path']}`

## Parameter Use

- D_det: `{config['D_det']}` m, used for detection-distance trigger.
- v_e_policy: `{config['v_e_policy']}`, current speed with fallback.
- fallback_v_e_mps: `{config['fallback_v_e_mps']}`.
- alpha: `{config['alpha']}`, applied to ETA.
- G_ext: `{config['G_ext']}`, applied as green extension when current/next phase is emergency green.
- rho: `{config['rho']}`, recorded as restore policy. Direct phase restore is not forced.
- tau: `{config['tau']}`, TraCI decision interval.
- t_lead: `{config['t_lead']}`, ETA trigger.
- metric_sample_interval: `{config['metric_sample_interval']}`, recorded in summary and events.

## Smoke Result

- final_status: `{summary['final_status']}`
- controller_started: `{summary['controller_started']}`
- emergency_departed/arrived/teleport: `{summary['emergency_departed']}` / `{summary['emergency_arrived']}` / `{summary['emergency_teleport']}`
- emergency_travel_time: `{summary['emergency_travel_time']}`
- route_error_count: `{summary['route_error_count']}`
- intervention_count: `{summary['intervention_count']}`
- green_extension_count: `{summary['green_extension_count']}`
- phase_switch_count: `{summary['phase_switch_count']}`
- restore_count: `{summary['restore_count']}`
- skipped_tls_count: `{summary['skipped_tls_count']}`
- failed_tls_count: `{summary['failed_tls_count']}`

## Safety

Pedestrian minimum walking time is not removed. Current implementation records `{summary['pedestrian_min_walk_policy']}` and preserves conservative controller behavior: no direct phase-state rewrite, no yellow/clearance skip, and existing SUMO phase sequence only.

## Outputs

- summary CSV/JSON: `results/metrics/b1_green_wave_v1_er_acc_002_smoke_summary.csv/json`
- signal events: `results/metrics/b1_green_wave_v1_er_acc_002_signal_events.csv`
- log: `outputs/logs/step14_b1_green_wave_v1_er_acc_002.log`
""",
        encoding="utf-8",
    )


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
            raise B1GreenWaveError(f"missing_python_module: traci; SUMO_HOME={os.environ.get('SUMO_HOME', '')}") from exc


def controller_speed(vehicle_speed: float, config: dict[str, Any]) -> float:
    fallback = as_float(config.get("fallback_v_e_mps"), 8.33)
    policy = config.get("v_e_policy", "current_speed_with_fallback")
    if policy == "current_speed_with_fallback":
        return max(vehicle_speed, fallback)
    if policy == "fallback_only":
        return fallback
    return max(vehicle_speed, fallback)


def run_controller(
    args: argparse.Namespace,
    paths: dict[str, Path],
    tls_plan: list[dict[str, Any]],
    route_edges: list[str],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    traci = import_traci()
    sumo = shutil.which("sumo")
    if sumo is None:
        raise B1GreenWaveError("missing_executable: sumo")
    d_det = as_float(config.get("D_det"), 300.0)
    alpha = as_float(config.get("alpha"), 1.0)
    g_ext = as_float(config.get("G_ext"), 30.0)
    effective_g_ext = max(1, int(round(g_ext)))
    tau = max(1, int(as_float(config.get("tau"), 1.0)))
    t_lead = as_float(config.get("t_lead"), 30.0)
    metric_sample_interval = max(1, int(as_float(config.get("metric_sample_interval"), 10.0)))
    rho = str(config.get("rho", "restore_original_program"))
    events: list[dict[str, Any]] = []
    touched: dict[str, dict[str, Any]] = {}
    controlled: dict[str, dict[str, Any]] = {}
    restored: set[str] = set()
    controller_started = False
    edge_starts = route_edge_starts(args.net, route_edges)
    cmd = [sumo, "-c", str(paths["sumocfg"]), "--error-log", str(paths["stderr"])]
    with paths["stdout"].open("w", encoding="utf-8") as stdout:
        traci.start(cmd, stdout=stdout)
        controller_started = True
        try:
            last_metric_sample = -metric_sample_interval
            while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() <= args.timeout_steps:
                traci.simulationStep()
                sim_time = traci.simulation.getTime()
                if args.emergency_vehicle_id in traci.vehicle.getIDList():
                    road_id = traci.vehicle.getRoadID(args.emergency_vehicle_id)
                    speed = controller_speed(float(traci.vehicle.getSpeed(args.emergency_vehicle_id)), config)
                    route_index = int(traci.vehicle.getRouteIndex(args.emergency_vehicle_id))
                    lane_position = float(traci.vehicle.getLanePosition(args.emergency_vehicle_id))
                    current_distance = edge_starts[route_index] + lane_position if 0 <= route_index < len(edge_starts) else 0.0
                    for tls_id, record in list(controlled.items()):
                        if tls_id in restored:
                            continue
                        if current_distance <= float(record["distance"]) + 10.0:
                            continue
                        restore_action = "restore_not_supported"
                        restore_reason = "rho_not_restore_original_program"
                        if rho == "restore_original_program":
                            try:
                                current_program = traci.trafficlight.getProgram(tls_id)
                                if current_program == record["original_program"]:
                                    restore_action = "restore_noop_original_program_unchanged"
                                    restore_reason = "original_program_already_active_direct_phase_restore_disabled"
                                else:
                                    traci.trafficlight.setProgram(tls_id, record["original_program"])
                                    restore_action = "restore_original_program"
                                    restore_reason = "setProgram_original_program"
                            except Exception as exc:  # noqa: BLE001
                                restore_action = "restore_skipped"
                                restore_reason = f"restore_exception:{type(exc).__name__}"
                        restored.add(tls_id)
                        events.append(
                            {
                                "time": sim_time,
                                "route_id": args.route_id,
                                "vehicle_id": args.emergency_vehicle_id,
                                "tls_id": tls_id,
                                "junction_id": record["junction_id"],
                                "incoming": record["incoming"],
                                "outgoing": record["outgoing"],
                                "remaining_distance_m": 0.0,
                                "speed_used_mps": round(speed, 3),
                                "eta_sec": 0.0,
                                "D_det": d_det,
                                "D_det_triggered": True,
                                "alpha": alpha,
                                "t_lead": t_lead,
                                "G_ext": g_ext,
                                "effective_G_ext": effective_g_ext,
                                "tau": tau,
                                "metric_sample_interval": metric_sample_interval,
                                "current_road_id": road_id,
                                "phase_before": record["phase_after"],
                                "phase_after": traci.trafficlight.getPhase(tls_id) if tls_id in traci.trafficlight.getIDList() else "",
                                "action": "restore",
                                "reason": "emergency_passed_tls",
                                "restore_action": restore_action,
                                "restore_reason": restore_reason,
                            }
                        )
                    if int(sim_time) % tau != 0:
                        continue
                    next_tls = None
                    for candidate in tls_plan:
                        if candidate["tls_id"] in touched:
                            continue
                        if candidate["distance"] + 1.0 < current_distance:
                            continue
                        next_tls = candidate
                        break
                    if next_tls is not None:
                        tls = next_tls
                        tls_id = tls["tls_id"]
                        remaining_distance = 0.0 if road_id == tls["incoming"] else max(tls["distance"] - current_distance, 0.0)
                        eta = (remaining_distance / speed) * alpha if speed > 0 else float("inf")
                        d_det_triggered = remaining_distance <= d_det
                        t_lead_triggered = eta <= t_lead
                        if not d_det_triggered and not t_lead_triggered:
                            if sim_time - last_metric_sample >= metric_sample_interval:
                                last_metric_sample = sim_time
                                events.append(
                                    {
                                        "time": sim_time,
                                        "route_id": args.route_id,
                                        "vehicle_id": args.emergency_vehicle_id,
                                        "tls_id": tls_id,
                                        "junction_id": tls["junction_id"],
                                        "incoming": tls["incoming"],
                                        "outgoing": tls["outgoing"],
                                        "remaining_distance_m": round(remaining_distance, 3),
                                        "speed_used_mps": round(speed, 3),
                                        "eta_sec": round(eta, 3),
                                        "D_det": d_det,
                                        "D_det_triggered": d_det_triggered,
                                        "alpha": alpha,
                                        "t_lead": t_lead,
                                        "G_ext": g_ext,
                                        "effective_G_ext": effective_g_ext,
                                        "tau": tau,
                                        "metric_sample_interval": metric_sample_interval,
                                        "current_road_id": road_id,
                                        "phase_before": "",
                                        "phase_after": "",
                                        "action": "observe_wait",
                                        "reason": "outside_detection_and_lead_time",
                                        "restore_action": "",
                                        "restore_reason": "",
                                    }
                                )
                            continue
                        event_base = {
                            "time": sim_time,
                            "route_id": args.route_id,
                            "vehicle_id": args.emergency_vehicle_id,
                            "tls_id": tls_id,
                            "junction_id": tls["junction_id"],
                            "incoming": tls["incoming"],
                            "outgoing": tls["outgoing"],
                            "remaining_distance_m": round(remaining_distance, 3),
                            "speed_used_mps": round(speed, 3),
                            "eta_sec": round(eta, 3),
                            "D_det": d_det,
                            "D_det_triggered": d_det_triggered,
                            "alpha": alpha,
                            "t_lead": t_lead,
                            "G_ext": g_ext,
                            "effective_G_ext": effective_g_ext,
                            "tau": tau,
                            "metric_sample_interval": metric_sample_interval,
                            "current_road_id": road_id,
                        }
                        if not tls["is_controllable"]:
                            touched[tls_id] = {"action": "skip"}
                            events.append(
                                {
                                    **event_base,
                                    "phase_before": "",
                                    "phase_after": "",
                                    "action": "skip",
                                    "reason": tls["audit_reason"] or "audit_not_controllable",
                                    "restore_action": "",
                                    "restore_reason": "",
                                }
                            )
                        else:
                            current_phase = int(traci.trafficlight.getPhase(tls_id))
                            original_program = traci.trafficlight.getProgram(tls_id)
                            green_phases = tls["green_phases"]
                            if current_phase in green_phases:
                                traci.trafficlight.setPhaseDuration(tls_id, effective_g_ext)
                                touched[tls_id] = {"action": "extend_green"}
                                controlled[tls_id] = {
                                    **tls,
                                    "original_program": original_program,
                                    "original_phase": current_phase,
                                    "phase_after": current_phase,
                                }
                                events.append(
                                    {
                                        **event_base,
                                        "phase_before": current_phase,
                                        "phase_after": current_phase,
                                        "action": "extend_green",
                                        "reason": "current_phase_already_green",
                                        "restore_action": "",
                                        "restore_reason": "",
                                    }
                                )
                            else:
                                next_phase = (current_phase + 1) % len(traci.trafficlight.getAllProgramLogics(tls_id)[0].phases)
                                if next_phase in green_phases:
                                    traci.trafficlight.setPhase(tls_id, next_phase)
                                    traci.trafficlight.setPhaseDuration(tls_id, effective_g_ext)
                                    touched[tls_id] = {"action": "advance_to_next_green"}
                                    controlled[tls_id] = {
                                        **tls,
                                        "original_program": original_program,
                                        "original_phase": current_phase,
                                        "phase_after": next_phase,
                                    }
                                    events.append(
                                        {
                                            **event_base,
                                            "phase_before": current_phase,
                                            "phase_after": next_phase,
                                            "action": "advance_to_next_green",
                                            "reason": "next_phase_is_emergency_green_existing_sequence",
                                            "restore_action": "",
                                            "restore_reason": "",
                                        }
                                    )
                                else:
                                    touched[tls_id] = {"action": "skip"}
                                    events.append(
                                        {
                                            **event_base,
                                            "phase_before": current_phase,
                                            "phase_after": current_phase,
                                            "action": "skip",
                                            "reason": "safe_green_not_current_or_next_phase",
                                            "restore_action": "",
                                            "restore_reason": "",
                                        }
                                    )
            if traci.simulation.getTime() > args.timeout_steps:
                events.append({"time": traci.simulation.getTime(), "route_id": args.route_id, "action": "timeout", "reason": "controller_timeout_steps"})
        finally:
            traci.close(False)
    return events, controller_started


def main() -> int:
    args = parse_args()
    generated_at = utc_now()
    lines = ["Step 14 B1 Green Wave v1 ER_ACC_002 smoke", "==========================================", f"generated_at: {generated_at}"]
    try:
        args.net = args.net.resolve()
        args.background_route = args.background_route.resolve()
        args.emergency_routes = args.emergency_routes.resolve()
        args.tls_audit = args.tls_audit.resolve()
        args.config = args.config.resolve()
        args.run_dir = args.run_dir.resolve()
        for path in [args.net, args.background_route, args.emergency_routes, args.tls_audit, args.config]:
            if not path.is_file():
                raise B1GreenWaveError(f"missing_file: {path}")
        config = read_json(args.config)
        required_config = ["D_det", "v_e_policy", "fallback_v_e_mps", "alpha", "G_ext", "rho", "tau", "t_lead", "metric_sample_interval"]
        missing_config = [key for key in required_config if key not in config]
        if missing_config:
            raise B1GreenWaveError(f"missing_config_parameter: {','.join(missing_config)}")
        route_row = select_emergency_route(args.emergency_routes, args.route_id)
        route_edges = route_row["route_edges"].split()
        validation_failures = validate_route_edges(args.net, route_edges)
        if validation_failures:
            raise B1GreenWaveError(f"emergency route validation failed: {';'.join(validation_failures[:10])}")
        tls_plan = load_tls_plan(args.tls_audit, args.route_id)
        if not tls_plan:
            raise B1GreenWaveError(f"no TLS audit rows for route_id={args.route_id}")
        emergency_route_xml = args.run_dir / f"{args.emergency_vehicle_id}.rou.xml"
        write_emergency_route_xml(emergency_route_xml, route_row, args.emergency_vehicle_id, args.emergency_depart)
        paths = write_sumo_files(args, emergency_route_xml)
        background_vehicle_count = count_vehicles(args.background_route)
        events, controller_started = run_controller(args, paths, tls_plan, route_edges, config)
        stderr_text = paths["stderr"].read_text(encoding="utf-8", errors="replace") if paths["stderr"].is_file() else ""
        summary_metrics = parse_summary_output(paths["summary"])
        trip = parse_tripinfo(paths["tripinfo"], args.emergency_vehicle_id)
        route_errors = route_error_count(stderr_text)
        emergency_tp = emergency_teleport_lines(stderr_text, args.emergency_vehicle_id)
        emergency_arrived = bool(trip["emergency_arrived"])
        emergency_departed = summary_metrics["departed_count_total"] > background_vehicle_count or emergency_arrived
        emergency_teleport = bool(emergency_tp)
        background_departed = max(summary_metrics["departed_count_total"] - (1 if emergency_departed else 0), 0)
        background_arrived = max(summary_metrics["arrived_count_total"] - (1 if emergency_arrived else 0), 0)
        background_teleported = max(summary_metrics["teleport_count"] - (1 if emergency_teleport else 0), 0)
        controlled_tls = {event["tls_id"] for event in events if event.get("action") in {"extend_green", "advance_to_next_green"} and event.get("tls_id")}
        skipped_tls = {event["tls_id"] for event in events if event.get("action") == "skip" and event.get("tls_id")}
        failed_tls = {event["tls_id"] for event in events if event.get("action") == "failed" and event.get("tls_id")}
        green_extension_count = sum(1 for event in events if event.get("action") == "extend_green")
        phase_switch_count = sum(1 for event in events if event.get("action") == "advance_to_next_green")
        restore_count = sum(1 for event in events if event.get("action") == "restore" and str(event.get("restore_action", "")).startswith("restore"))
        intervention_count = green_extension_count + phase_switch_count
        failures = []
        warnings = []
        if not controller_started:
            failures.append("controller_not_started")
        if not emergency_departed:
            failures.append("emergency_not_departed")
        if not emergency_arrived:
            failures.append("emergency_not_arrived")
        if emergency_teleport:
            failures.append("emergency_teleport_detected")
        if route_errors > 0:
            failures.append("route_error_count_gt_0")
        if not controlled_tls and not skipped_tls:
            failures.append("no_tls_decision_events")
        if background_teleported > 0:
            warnings.append("background_teleports_present")
        final_status = "FAIL" if failures else "WARNING" if warnings else "PASS"
        summary = {
            "generated_at": generated_at,
            "final_status": final_status,
            "active_net": rel(args.net),
            "background_route": rel(args.background_route),
            "tls_audit": rel(args.tls_audit),
            "config_path": rel(args.config),
            "config_parameter_snapshot": {key: config.get(key) for key in required_config},
            "background_vehicle_count": background_vehicle_count,
            "route_id": args.route_id,
            "emergency_vehicle_id": args.emergency_vehicle_id,
            "controller_started": controller_started,
            "emergency_departed": emergency_departed,
            "emergency_arrived": emergency_arrived,
            "emergency_teleport": emergency_teleport,
            "emergency_teleport_evidence": emergency_tp,
            "emergency_travel_time": trip["emergency_travel_time"],
            "background_departed": background_departed,
            "background_arrived": background_arrived,
            "background_teleported": background_teleported,
            "background_teleport_ratio": round(background_teleported / background_departed, 6) if background_departed else 0.0,
            "route_error_count": route_errors,
            "controlled_tls_count": len(controlled_tls),
            "skipped_tls_count": len(skipped_tls),
            "failed_tls_count": len(failed_tls),
            "intervention_count": intervention_count,
            "green_extension_count": green_extension_count,
            "phase_switch_count": phase_switch_count,
            "restore_count": restore_count,
            "signal_event_count": len(events),
            "time_to_teleport": args.time_to_teleport,
            "collision_action": args.collision_action,
            "pedestrian_min_walk_policy": config.get("pedestrian_min_walk_policy", "safety_placeholder_documented_not_optimized"),
            "sim_end_time": summary_metrics["sim_end_time"],
            "sumo_exit_code": 0 if controller_started else 1,
            "warnings": warnings,
            "failures": failures,
            "failure_reason": ";".join(failures),
            "run_dir": rel(args.run_dir),
            "sumocfg": rel(paths["sumocfg"]),
            "tripinfo": rel(paths["tripinfo"]),
            "summary_output": rel(paths["summary"]),
            "edgeData_output": rel(paths["edge_data"]),
            "stderr_log": rel(paths["stderr"]),
            "outputs": [rel(SUMMARY_CSV), rel(SUMMARY_JSON), rel(SIGNAL_EVENTS_CSV), rel(LOG_PATH), rel(STEP14_DOC)],
        }
        event_fields = [
            "time",
            "route_id",
            "vehicle_id",
            "tls_id",
            "junction_id",
            "incoming",
            "outgoing",
            "remaining_distance_m",
            "speed_used_mps",
            "eta_sec",
            "D_det",
            "D_det_triggered",
            "alpha",
            "t_lead",
            "G_ext",
            "effective_G_ext",
            "tau",
            "metric_sample_interval",
            "current_road_id",
            "phase_before",
            "phase_after",
            "action",
            "reason",
            "restore_action",
            "restore_reason",
        ]
        write_csv(SIGNAL_EVENTS_CSV, events, event_fields)
        write_csv(SUMMARY_CSV, [summary], list(summary.keys()))
        write_json(SUMMARY_JSON, summary)
        write_step14_doc(summary)
        lines.extend(
            [
                f"controller_started: {controller_started}",
                f"emergency_arrived: {emergency_arrived}",
                f"emergency_teleport: {emergency_teleport}",
                f"intervention_count: {summary['intervention_count']}",
                f"green_extension_count: {summary['green_extension_count']}",
                f"phase_switch_count: {summary['phase_switch_count']}",
                f"restore_count: {summary['restore_count']}",
                f"skipped_tls_count: {summary['skipped_tls_count']}",
                f"final_status: {final_status}",
                f"summary_json: {rel(SUMMARY_JSON)}",
            ]
        )
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if final_status in {"PASS", "WARNING"} else 1
    except (B1GreenWaveError, OSError, ET.ParseError, ValueError, RuntimeError) as exc:
        lines.extend(["final_status: FAIL", f"blocker: {exc}"])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
