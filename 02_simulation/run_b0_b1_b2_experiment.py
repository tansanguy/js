#!/usr/bin/env python3
"""Run B00/B0/B2 route-level SUMO experiments with process-level parallelism."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STEP07_PATH = PROJECT_ROOT / "01_prepare/04_routes/step07_generate_emergency_routes.py"
STEP14_PATH = PROJECT_ROOT / "01_prepare/08_signal/step14_b1_green_wave_v1_er_acc_002.py"

DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml"
DEFAULT_BACKGROUND_ROUTE = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19_warm0p15_sustain0p05_seed002_sustained_3600.rou.xml"
DEFAULT_TLS_AUDIT = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_spine_v2.csv"
DEFAULT_PRIORITY_TERMINALS = PROJECT_ROOT / "data_prepared/signals/priority_terminal_candidates.csv"
DEFAULT_B2_PARAMS = PROJECT_ROOT / "configs/b2_parameter_sets.csv"
DEFAULT_MANIFEST = PROJECT_ROOT / "configs/final_experiment_manifest.json"
DEFAULT_CORRIDOR_EDGES = PROJECT_ROOT / "data_prepared/routes/corridor_spine_edges.csv"
DEFAULT_SEOUL_STATION_MANUAL_ROUTE = PROJECT_ROOT / "data_prepared/manual/seoul_station_manual_route.json"

LOG_PATH = PROJECT_ROOT / "outputs/logs/b00_b0_b2_experiment.log"


def configure_runtime_environment() -> None:
    sumo_bin = shutil.which("sumo")
    if sumo_bin:
        candidate = Path(sumo_bin).resolve().parent.parent
        sumo_home = candidate / "share/sumo"
        if (sumo_home / "data/xsd").is_dir():
            os.environ["SUMO_HOME"] = str(sumo_home)
    if "PROJ_LIB" not in os.environ or "PROJ_DATA" not in os.environ:
        try:
            import pyproj  # type: ignore

            proj_dir = Path(pyproj.datadir.get_data_dir())
            if (proj_dir / "proj.db").is_file():
                os.environ["PROJ_LIB"] = str(proj_dir)
                os.environ["PROJ_DATA"] = str(proj_dir)
        except Exception:
            pass

DEFAULT_TIMEOUT_STEPS = 7200
DEFAULT_TIMEOUT_SEC = 7200
DEFAULT_RECOVERY_BUFFER_SEC = 300
DEFAULT_EMERGENCY_DEPART_SEC = 600.0
ROLLING_CONGESTION_WINDOW_SEC = 300
CONGESTION_MIN_KMH = 12.0
CONGESTION_MAX_KMH = 35.0
PREFERRED_CONGESTION_MIN_KMH = 15.0
PREFERRED_CONGESTION_MAX_KMH = 30.0
FREE_FLOW_SPEED_CAP_KMH = 50.0
FREE_FLOW_SPEED_CAP_MPS = FREE_FLOW_SPEED_CAP_KMH / 3.6
EMERGENCY_SPEED_FACTOR = 1.4
EMERGENCY_SPEED_CAP_KMH = 70.0
EMERGENCY_BLUELIGHT_ENABLED = False
DEFAULT_T_CHANGE_SEC = 10
CLEARANCE_BEFORE_GREEN_SEC = 3
SCORE_WEIGHT_A = 3.0
SCORE_WEIGHT_N = 1.0
SCORE_WEIGHT_RECOVERY = 1.0
B00_SPEED_POLICY = "speed50_net_emergency_speedFactor_1p40_cap70_no_bluelight_tls_all_off"
SEOUL_STATION_ROUTE_ID = "FIRE_TO_SEOUL_STATION"
SEOUL_STATION_START_EDGE = "-381802881#2"
SEOUL_STATION_TARGET_EDGE = "619147738#0"
SEOUL_STATION_POLICY = "straight_seoul_station_fixed"
CONTROL_ACTIONS = {"extend_green", "alpha_hold_extend", "switch_to_green_after_t_change"}
SCORE_COMPONENT_FIELDS = [
    "generated_at",
    "run_id",
    "pipeline",
    "mode",
    "parameter_id",
    "repeat_id",
    "route_id",
    "D_det",
    "alpha",
    "G_ext",
    "T_change_sec",
    "A_delay_sec",
    "N_delay_sec",
    "T_recovery_sec",
    "score_sec",
    "final_status",
    "warning_reason",
    "failure_reason",
    "emergency_travel_time_sec",
    "b00_emergency_travel_time_sec",
    "background_teleported",
    "background_remaining_count",
    "network_avg_speed_kmh",
    "network_avg_speed_at_analysis_end_kmh",
    "network_running_at_analysis_end",
    "network_speed_pre_emergency_kmh",
    "network_speed_during_response_kmh",
    "network_speed_post_recovery_kmh",
    "active_vehicle_count_pre_emergency",
    "active_vehicle_count_during_response",
    "active_vehicle_count_post_recovery",
    "rolling_congestion_valid",
    "rolling_congestion_reason",
    "rolling_congestion_window_sec",
    "rolling_congestion_min_kmh",
    "rolling_congestion_max_kmh",
    "congestion_valid",
    "congestion_valid_at_analysis_end",
    "intervention_count",
    "t_change_switch_count",
    "run_dir",
]
RESULT_SCORE_FIELDS = [
    "run_id",
    "pipeline",
    "mode",
    "parameter_id",
    "repeat_id",
    "route_id",
    "A_delay_sec",
    "N_delay_sec",
    "T_recovery_sec",
]
EVENT_FIELDS = [
    "time",
    "output_prefix",
    "mode",
    "parameter_id",
    "repeat_id",
    "route_id",
    "vehicle_id",
    "tls_id",
    "junction_id",
    "incoming",
    "outgoing",
    "remaining_distance_m",
    "D_det",
    "alpha",
    "G_ext",
    "T_change_sec",
    "effective_T_change_sec",
    "effective_alpha_sec",
    "effective_G_ext_sec",
    "current_road_id",
    "phase_before",
    "phase_after",
    "action",
    "reason",
    "restore_action",
    "pass_time",
    "recovery_sec",
]
EXPERIMENT_RESULT_FIELDS = [
    "generated_at",
    "run_id",
    "timeout_steps",
    "command_time_to_teleport",
    "pipeline",
    "output_prefix",
    "mode",
    "parameter_id",
    "repeat_id",
    "route_id",
    "route_start_edge",
    "route_target_edge",
    "route_policy",
    "D_det",
    "alpha",
    "G_ext",
    "T_change_sec",
    "w1",
    "w2",
    "w3",
    "effective_T_change_sec",
    "effective_alpha_sec",
    "effective_G_ext_sec",
    "emergency_travel_time_sec",
    "b00_emergency_travel_time_sec",
    "A_delay_sec",
    "N_delay_sec",
    "T_recovery_sec",
    "score_sec",
    "emergency_arrived",
    "emergency_teleport",
    "background_vehicle_count",
    "final_status",
    "warning_reason",
    "failure_reason",
    "run_dir",
    "sumo_exit_code",
    "emergency_departed",
    "emergency_travel_time",
    "emergency_corridor_actual_sec",
    "emergency_corridor_free_flow_sec",
    "general_non_main_actual_sec",
    "general_non_main_free_flow_sec",
    "general_non_main_vehicle_edge_count",
    "N_delay_completed_vehicle_edge_count",
    "N_delay_censored_vehicle_edge_count",
    "N_delay_excluded_active_vehicle_edge_count",
    "N_delay_excluded_ratio",
    "N_delay_censored_ratio",
    "route_error_count",
    "background_departed",
    "background_arrived",
    "background_teleported",
    "background_teleport_ratio",
    "timeout_reached",
    "remaining_vehicle_count",
    "background_remaining_count",
    "background_remaining_at_sim_end",
    "all_vehicles_arrived",
    "network_avg_speed_kmh",
    "network_avg_speed_at_analysis_end_kmh",
    "network_running_at_analysis_end",
    "network_speed_pre_emergency_kmh",
    "network_speed_during_response_kmh",
    "network_speed_post_recovery_kmh",
    "active_vehicle_count_pre_emergency",
    "active_vehicle_count_during_response",
    "active_vehicle_count_post_recovery",
    "rolling_congestion_valid",
    "rolling_congestion_reason",
    "rolling_congestion_window_sec",
    "rolling_congestion_min_kmh",
    "rolling_congestion_max_kmh",
    "congestion_valid",
    "congestion_valid_at_analysis_end",
    "congestion_reason_at_analysis_end",
    "congestion_reason",
    "analysis_end_time_sec",
    "analysis_stop_reason",
    "recovery_buffer_sec",
    "emergency_route_length_m",
    "emergency_route_length_source",
    "emergency_avg_speed_kmh",
    "emergency_speed_factor",
    "emergency_speed_cap_kmh",
    "emergency_bluelight_enabled",
    "b00_speed_policy",
    "controlled_tls_count",
    "skipped_tls_count",
    "failed_tls_count",
    "intervention_count",
    "green_extension_count",
    "green_arrived_before_t_change_extension_count",
    "phase_switch_count",
    "restore_count",
    "signal_event_count",
    "t_change_request_count",
    "t_change_switch_count",
    "green_missed_before_t_change_count",
    "T_recovery_tls_count",
    "T_recovery_max_tls_id",
    "T_recovery_unrecovered_count",
    "queue_recovery_csv",
    "safety_violation_count",
    "emergency_stop_warning_count",
    "emergency_lane_connection_warning_count",
    "emergency_lane_connection_diagnostic_count",
    "signal_events_csv",
    "b0_emergency_travel_time",
    "travel_time_delta_sec",
    "travel_time_improvement_pct",
    "stderr_log",
    "tripinfo",
    "summary_output",
    "edgeData_output",
    "controller_started",
    "elapsed_wall_sec",
    "emergency_vehicle_id",
    "sumocfg",
    "sim_end_time",
    "emergency_last_edge_id",
    "emergency_last_route_index",
    "emergency_last_lane_id",
    "emergency_last_speed_kmh",
    "emergency_last_waiting_time_sec",
    "emergency_route_progress_ratio",
]


class ExperimentError(RuntimeError):
    """Expected experiment runner failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_id_from_generated_at(generated_at: str) -> str:
    return generated_at.replace(":", "").replace("-", "").replace("+", "Z").replace(".", "_")


def rel(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


def load_step14_module() -> Any:
    spec = importlib.util.spec_from_file_location("step14_green_wave", STEP14_PATH)
    if spec is None or spec.loader is None:
        raise ExperimentError(f"cannot import Step14 module: {STEP14_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_step07_module() -> Any:
    spec = importlib.util.spec_from_file_location("step07_routes", STEP07_PATH)
    if spec is None or spec.loader is None:
        raise ExperimentError(f"cannot import Step7 route module: {STEP07_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure_runtime_environment()
S07 = load_step07_module()
S14 = load_step14_module()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_text_with_retry(path: Path, attempts: int = 3, delay_sec: float = 0.2) -> str:
    text = ""
    for attempt in range(attempts):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
        if text or attempt == attempts - 1:
            return text
        time.sleep(delay_sec)
    return text


def default_workers() -> int:
    cpus = os.cpu_count() or 2
    return max(1, min(cpus - 2, 8))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run B00/B0/B2 experiment batches.")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--tls-audit", type=Path, default=DEFAULT_TLS_AUDIT)
    parser.add_argument("--priority-terminals", type=Path, default=DEFAULT_PRIORITY_TERMINALS)
    parser.add_argument("--corridor-edges", type=Path, default=DEFAULT_CORRIDOR_EDGES)
    parser.add_argument("--b2-params", type=Path, default=DEFAULT_B2_PARAMS)
    parser.add_argument("--pipeline", choices=["parameter_input_sim"], default="parameter_input_sim")
    parser.add_argument("--modes", nargs="+", choices=["B00", "B0", "B2"], default=["B00", "B0", "B2"])
    parser.add_argument("--route-set", choices=["seoul_station"], default="seoul_station")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--time-to-teleport", type=int, default=1200)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--emergency-depart", type=float, default=DEFAULT_EMERGENCY_DEPART_SEC)
    parser.add_argument("--timeout-steps", type=int, default=DEFAULT_TIMEOUT_STEPS)
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--recovery-buffer-sec", type=int, default=DEFAULT_RECOVERY_BUFFER_SEC)
    parser.add_argument("--output-prefix", default=None)
    parser.add_argument("--run-root", type=Path, default=PROJECT_ROOT / "runs/final")
    parser.add_argument("--allow-nonfinal-background", action="store_true")
    parser.add_argument("--legacy-output-names", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ExperimentError(f"json_root_not_object: {path}")
    return payload


def project_path(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def apply_manifest(args: argparse.Namespace) -> dict[str, Any]:
    if args.manifest is None:
        return {}
    args.manifest = args.manifest.resolve()
    if not args.manifest.is_file():
        raise ExperimentError(f"missing_manifest: {args.manifest}")
    manifest = load_json(args.manifest)
    if args.net == DEFAULT_NET:
        args.net = project_path(manifest.get("active_net"), args.net)
    if args.background_route == DEFAULT_BACKGROUND_ROUTE:
        args.background_route = project_path(manifest.get("background_route"), args.background_route)
    if args.tls_audit == DEFAULT_TLS_AUDIT:
        args.tls_audit = project_path(manifest.get("tls_audit"), args.tls_audit)
    if args.priority_terminals == DEFAULT_PRIORITY_TERMINALS:
        args.priority_terminals = project_path(manifest.get("priority_terminals"), args.priority_terminals)
    if args.corridor_edges == DEFAULT_CORRIDOR_EDGES:
        args.corridor_edges = project_path(manifest.get("corridor_edges"), args.corridor_edges)
    if args.b2_params == DEFAULT_B2_PARAMS:
        args.b2_params = project_path(manifest.get("b2_parameter_sets"), args.b2_params)
    return manifest


def output_paths(output_prefix: str, legacy: bool, pipeline: str | None = None, run_id: str | None = None) -> dict[str, Path | None]:
    if legacy:
        return {
            "results_csv": PROJECT_ROOT / "results/metrics/experiment_b0_b2_summary.csv",
            "summary_json": PROJECT_ROOT / "results/metrics/experiment_b0_b2_summary.json",
            "events_csv": PROJECT_ROOT / "results/metrics/experiment_signal_events.csv",
            "compare_csv": PROJECT_ROOT / "results/metrics/experiment_compare_by_route.csv",
            "latest_json": None,
        }
    safe_prefix = output_prefix.strip()
    if not safe_prefix:
        raise ExperimentError("output_prefix cannot be blank")
    if not run_id:
        raise ExperimentError("run_id is required for non-legacy metrics output")
    metrics_dir = PROJECT_ROOT / "results/metrics" / safe_prefix / run_id
    return {
        "results_csv": metrics_dir / "experiment_results.csv",
        "score_components_csv": metrics_dir / "score_components.csv",
        "result_score_csv": metrics_dir / "result_score.csv",
        "summary_json": metrics_dir / "experiment_summary.json",
        "latest_json": PROJECT_ROOT / "results/metrics" / safe_prefix / "latest.json",
    }


def write_sumo_files_for_task(
    args: argparse.Namespace,
    emergency_route_xml: Path,
    include_background: bool,
    tls_all_off: bool = False,
) -> dict[str, Path]:
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
            "id": "experiment_edge_data",
            "file": str(paths["edge_data"]),
            "begin": "0",
            "end": "86400",
            "freq": "86400",
            "excludeEmpty": "false",
        },
    )
    ET.ElementTree(additional).write(paths["additional"], encoding="utf-8", xml_declaration=True)
    route_files = [str(emergency_route_xml)]
    if include_background:
        route_files.insert(0, str(args.background_route))
    config = ET.Element("configuration")
    input_elem = ET.SubElement(config, "input")
    ET.SubElement(input_elem, "net-file", {"value": str(args.net)})
    ET.SubElement(input_elem, "route-files", {"value": ",".join(route_files)})
    ET.SubElement(input_elem, "additional-files", {"value": str(paths["additional"])})
    output_elem = ET.SubElement(config, "output")
    ET.SubElement(output_elem, "tripinfo-output", {"value": str(paths["tripinfo"])})
    ET.SubElement(output_elem, "summary-output", {"value": str(paths["summary"])})
    time_elem = ET.SubElement(config, "time")
    ET.SubElement(time_elem, "begin", {"value": "0"})
    processing_elem = ET.SubElement(config, "processing")
    ET.SubElement(processing_elem, "time-to-teleport", {"value": str(args.time_to_teleport)})
    ET.SubElement(processing_elem, "collision.action", {"value": args.collision_action})
    ET.SubElement(processing_elem, "emergency-insert", {"value": "true"})
    if tls_all_off:
        ET.SubElement(processing_elem, "tls.all-off", {"value": "true"})
    report_elem = ET.SubElement(config, "report")
    ET.SubElement(report_elem, "no-step-log", {"value": "true"})
    ET.SubElement(report_elem, "duration-log.disable", {"value": "true"})
    ET.ElementTree(config).write(paths["sumocfg"], encoding="utf-8", xml_declaration=True)
    return paths


def set_congested_emergency_departure(path: Path) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    for vtype in root.findall("vType"):
        vtype.set("speedFactor", f"{EMERGENCY_SPEED_FACTOR:.2f}")
        vtype.set("maxSpeed", f"{EMERGENCY_SPEED_CAP_KMH / 3.6:.6f}")
        vtype.set("lcStrategic", "10.0")
        vtype.set("lcCooperative", "0.0")
        vtype.set("lcSpeedGain", "5.0")
        vtype.set("lcKeepRight", "0.0")
        vtype.set("lcAssertive", "5.0")
        for param in vtype.findall("param"):
            if param.get("key") == "has.bluelight.device":
                param.set("value", "true" if EMERGENCY_BLUELIGHT_ENABLED else "false")
    for vehicle in root.findall("vehicle"):
        vehicle.set("departLane", "free")
        vehicle.set("departPos", "last")
        vehicle.set("departSpeed", "max")
        vehicle.set("insertionChecks", "none")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def remove_vehicle_elements(path: Path) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    for vehicle in list(root.findall("vehicle")):
        root.remove(vehicle)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def configure_dynamic_emergency_departure(task: dict[str, Any], emergency_route_xml: Path) -> dict[str, Any]:
    if task["mode"] == "B00" or float(task["emergency_depart"]) <= 0:
        return task
    remove_vehicle_elements(emergency_route_xml)
    return {**task, "dynamic_emergency_insert": True}


def current_git_commit() -> str:
    try:
        completed = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def stitch_route_waypoints(sumo_net: Any, waypoints: list[str]) -> list[str]:
    if len(waypoints) < 2:
        raise ExperimentError("straight_seoul_station_route_requires_at_least_two_waypoints")
    route_edges: list[str] = []
    for start_edge, target_edge in zip(waypoints, waypoints[1:], strict=False):
        segment = S07.shortest_route(sumo_net, start_edge, target_edge)
        if not route_edges:
            route_edges.extend(segment)
        elif route_edges[-1] == segment[0]:
            route_edges.extend(segment[1:])
        else:
            route_edges.extend(segment)
    return route_edges


def validate_route_transitions(sumo_net: Any, route_edges: list[str]) -> None:
    for from_edge_id, to_edge_id in zip(route_edges, route_edges[1:], strict=False):
        from_edge = S07.edge_from_net(sumo_net, from_edge_id)
        to_edge = S07.edge_from_net(sumo_net, to_edge_id)
        if from_edge is None or to_edge is None:
            raise ExperimentError(f"straight_seoul_station_route_edge_missing:{from_edge_id}->{to_edge_id}")
        if not from_edge.getOutgoing().get(to_edge, []):
            raise ExperimentError(f"straight_seoul_station_route_not_connected:{from_edge_id}->{to_edge_id}")


def load_canonical_seoul_station_route(sumo_net: Any) -> dict[str, Any]:
    if not DEFAULT_SEOUL_STATION_MANUAL_ROUTE.is_file():
        raise ExperimentError(f"missing_straight_seoul_station_route:{DEFAULT_SEOUL_STATION_MANUAL_ROUTE}")
    payload = load_json(DEFAULT_SEOUL_STATION_MANUAL_ROUTE)
    route_id = str(payload.get("route_id") or "").strip()
    start_edge = str(payload.get("start_edge_id") or "").strip()
    target_edge = str(payload.get("target_edge_id") or "").strip()
    if route_id != SEOUL_STATION_ROUTE_ID:
        raise ExperimentError(f"straight_seoul_station_route_id_mismatch:{route_id}")
    if start_edge != SEOUL_STATION_START_EDGE:
        raise ExperimentError(f"straight_seoul_station_start_edge_mismatch:{start_edge}")
    if target_edge != SEOUL_STATION_TARGET_EDGE:
        raise ExperimentError(f"straight_seoul_station_target_edge_mismatch:{target_edge}")
    waypoint_edges = payload.get("manual_edge_ids", payload.get("waypoint_edge_ids", []))
    if not isinstance(waypoint_edges, list) or not all(isinstance(edge_id, str) for edge_id in waypoint_edges):
        raise ExperimentError(f"invalid_straight_seoul_station_route_edges:{DEFAULT_SEOUL_STATION_MANUAL_ROUTE}")
    waypoint_edges = [edge_id.strip() for edge_id in waypoint_edges if edge_id.strip()]
    if not waypoint_edges:
        raise ExperimentError(f"empty_straight_seoul_station_route_edges:{DEFAULT_SEOUL_STATION_MANUAL_ROUTE}")
    waypoints = [SEOUL_STATION_START_EDGE, *waypoint_edges]
    if waypoints[-1] != target_edge:
        waypoints.append(target_edge)
    for edge_id in waypoints:
        edge = S07.edge_from_net(sumo_net, edge_id)
        if edge is None:
            raise ExperimentError(f"straight_seoul_station_route_edge_missing:{edge_id}")
        if not edge.allows("passenger"):
            raise ExperimentError(f"straight_seoul_station_route_edge_not_passenger:{edge_id}")
    route_edges = stitch_route_waypoints(sumo_net, waypoints)
    repeated = sorted({edge_id for edge_id in route_edges if route_edges.count(edge_id) > 1})
    if repeated:
        raise ExperimentError(f"straight_seoul_station_route_repeated_edge:{','.join(repeated)}")
    if not route_edges or route_edges[0] != SEOUL_STATION_START_EDGE or route_edges[-1] != SEOUL_STATION_TARGET_EDGE:
        raise ExperimentError("straight_seoul_station_route_endpoint_mismatch")
    validate_route_transitions(sumo_net, route_edges)
    return {
        "target_edge": target_edge,
        "route_edges": route_edges,
        "waypoint_edge_count": len(waypoint_edges),
    }


def synthetic_seoul_station_route(net_path: Path) -> dict[str, str]:
    try:
        sumo_net = S07.read_sumo_net(net_path)
        props_by_id, coords_by_id = S07.load_edge_geojson(S07.ACTIVE_EDGES_GEOJSON)
        map_config = S07.load_yaml(S07.MAP_CONFIG)
        axis_ctx = S07.axis_context(map_config)
        _spine_rows, spine_ids, spine_metrics = S07.build_spine_edges(sumo_net, props_by_id, coords_by_id, axis_ctx)
        canonical_route = load_canonical_seoul_station_route(sumo_net)
        target_edge = canonical_route["target_edge"]
        route_edges = canonical_route["route_edges"]
        shortest_edges = S07.shortest_route(sumo_net, SEOUL_STATION_START_EDGE, target_edge)
        geometry = S07.route_geometry_diagnostics(route_edges, coords_by_id, axis_ctx)
        selected = S07.route_spine_metrics(
            sumo_net,
            route_edges,
            S07.route_length(sumo_net, shortest_edges),
            spine_ids,
            spine_metrics,
            coords_by_id,
            axis_ctx,
            target_edge,
        )
        route_length = float(selected["route_length_m"])
        if int(geometry["repeated_edge_count"]) != 0:
            raise ExperimentError(f"straight_seoul_station_route_repeated_edge:{geometry['repeated_edge_count']}")
        route_tls_count = len(S07.route_tls_ids(S07.route_objects(sumo_net, route_edges)))
    except ExperimentError:
        raise
    except Exception as exc:
        raise ExperimentError(f"straight_seoul_station_route_failed:{exc}") from exc
    return {
        "route_id": SEOUL_STATION_ROUTE_ID,
        "scenario_id": "SEOUL_STATION",
        "target_edge_id": target_edge,
        "selected_policy": SEOUL_STATION_POLICY,
        "route_edges": " ".join(route_edges),
        "route_length_m": f"{route_length:.2f}",
        "route_edge_count": str(len(route_edges)),
        "route_tls_count": str(route_tls_count),
    }


def load_b2_parameter_sets(path: Path) -> list[dict[str, Any]]:
    rows = read_csv(path)
    required = ["parameter_id", "D_det", "alpha", "G_ext"]
    missing = [key for key in required if rows and key not in rows[0]]
    if missing:
        raise ExperimentError(f"missing_b2_parameter_columns: {','.join(missing)}")
    result = []
    for row in rows:
        parameter_id = row.get("parameter_id", "").strip()
        if not parameter_id:
            raise ExperimentError("b2_parameter_sets contains blank parameter_id")
        t_change_raw = row.get("T_change_sec", row.get("T_change", ""))
        if t_change_raw in {"", None}:
            raise ExperimentError("missing_b2_parameter_columns:T_change_sec")
        result.append(
            {
                "parameter_id": parameter_id,
                "D_det": float(row["D_det"]),
                "alpha": float(row["alpha"]),
                "G_ext": float(row["G_ext"]),
                "T_change_sec": float(t_change_raw),
                "metric_sample_interval": int(float(row.get("metric_sample_interval") or 10)),
                "phase_control_policy": row.get("phase_control_policy") or "distance_trigger_no_eta",
                "yellow_clearance_policy": row.get("yellow_clearance_policy") or "wait_clearance_then_switch",
                "pedestrian_min_walk_policy": row.get("pedestrian_min_walk_policy") or "safety_placeholder_documented_not_optimized",
            }
        )
    if not result:
        raise ExperimentError(f"empty_b2_parameter_file: {path}")
    return result


def parse_summary_steps(path: Path) -> list[dict[str, float]]:
    root = S14.parse_xml_with_retry(path).getroot()
    steps = []
    for step in root.findall("step"):
        mean_speed = max(float(step.get("meanSpeed", "0") or 0), 0.0)
        running = float(step.get("running", "0") or 0)
        waiting = float(step.get("waiting", "0") or 0)
        steps.append(
            {
                "time": float(step.get("time", "0") or 0),
                "inserted": float(step.get("inserted", "0") or 0),
                "arrived": float(step.get("arrived", "0") or 0),
                "running": running,
                "waiting": waiting,
                "teleports": float(step.get("teleports", "0") or 0),
                "mean_speed_mps": mean_speed,
            }
        )
    if not steps:
        raise ExperimentError(f"summary-output has no steps: {path}")
    return steps


def parse_summary_output(path: Path) -> dict[str, Any]:
    steps = parse_summary_steps(path)
    last_step = steps[-1]
    max_teleports = max(int(step["teleports"]) for step in steps)
    speed_num = sum(step["mean_speed_mps"] * step["running"] for step in steps if step["running"] > 0)
    speed_den = sum(step["running"] for step in steps if step["running"] > 0)
    mean_speed_mps = speed_num / speed_den if speed_den else last_step["mean_speed_mps"]
    return {
        "departed_count_total": int(last_step["inserted"]),
        "arrived_count_total": int(last_step["arrived"]),
        "running_count": int(last_step["running"]),
        "waiting_count": int(last_step["waiting"]),
        "teleport_count": max_teleports,
        "sim_end_time": last_step["time"],
        "network_avg_speed_kmh": mean_speed_mps * 3.6,
        "network_avg_speed_at_analysis_end_kmh": last_step["mean_speed_mps"] * 3.6,
        "network_running_at_analysis_end": int(last_step["running"]),
    }


def summary_window_stats(steps: list[dict[str, float]], start: float, end: float) -> dict[str, Any]:
    if end <= start:
        return {"speed_kmh": "", "active_count": "", "running_count": "", "sample_count": 0}
    samples = [step for step in steps if start < step["time"] <= end]
    if not samples:
        return {"speed_kmh": "", "active_count": "", "running_count": "", "sample_count": 0}
    speed_den = sum(step["running"] for step in samples if step["running"] > 0)
    if speed_den:
        speed_kmh: float | str = sum(step["mean_speed_mps"] * step["running"] for step in samples if step["running"] > 0) / speed_den * 3.6
    else:
        speed_kmh = ""
    return {
        "speed_kmh": speed_kmh,
        "active_count": sum(step["running"] + step["waiting"] for step in samples) / len(samples),
        "running_count": sum(step["running"] for step in samples) / len(samples),
        "sample_count": len(samples),
    }


def rolling_congestion_stats(
    steps: list[dict[str, float]],
    start: float,
    end: float,
    window_sec: int = ROLLING_CONGESTION_WINDOW_SEC,
) -> dict[str, Any]:
    if end - start < window_sec:
        return {
            "rolling_congestion_valid": False,
            "rolling_congestion_reason": "insufficient_window",
            "rolling_congestion_min_kmh": "",
            "rolling_congestion_max_kmh": "",
        }
    speeds = []
    sample_times = [step["time"] for step in steps if start + window_sec <= step["time"] <= end]
    stride = max(1, window_sec // 10)
    for idx, time_value in enumerate(sample_times):
        if idx % stride != 0 and time_value != sample_times[-1]:
            continue
        stats = summary_window_stats(steps, time_value - window_sec, time_value)
        speed = stats["speed_kmh"]
        if speed != "":
            speeds.append(float(speed))
    if not speeds:
        return {
            "rolling_congestion_valid": False,
            "rolling_congestion_reason": "no_running_vehicles_in_rolling_windows",
            "rolling_congestion_min_kmh": "",
            "rolling_congestion_max_kmh": "",
        }
    min_speed = min(speeds)
    max_speed = max(speeds)
    valid = CONGESTION_MIN_KMH <= min_speed and max_speed <= CONGESTION_MAX_KMH
    if valid:
        reason = "rolling_speed_12_to_35_kmh"
    elif min_speed < CONGESTION_MIN_KMH:
        reason = "rolling_speed_below_12_kmh"
    else:
        reason = "rolling_speed_above_35_kmh"
    return {
        "rolling_congestion_valid": valid,
        "rolling_congestion_reason": reason,
        "rolling_congestion_min_kmh": min_speed,
        "rolling_congestion_max_kmh": max_speed,
    }


def route_error_count(stderr: str) -> int:
    lower = stderr.lower()
    return lower.count("route error") + lower.count("has no valid route") + lower.count("is not connected")


def emergency_teleport_lines(stderr: str, vehicle_id: str) -> list[str]:
    return [line for line in stderr.splitlines() if vehicle_id in line and "teleport" in line.lower()]


def emergency_warning_count(stderr: str, vehicle_id: str, needles: tuple[str, ...]) -> int:
    count = 0
    for line in stderr.splitlines():
        lower = line.lower()
        if vehicle_id in line and any(needle in lower for needle in needles):
            count += 1
    return count


def tripinfo_float_attr(path: Path, vehicle_id: str, attr: str) -> float | None:
    try:
        root = S14.parse_xml_with_retry(path).getroot()
    except Exception:  # noqa: BLE001 - malformed tripinfo is handled elsewhere.
        return None
    for tripinfo in root.findall("tripinfo"):
        if tripinfo.get("id") == vehicle_id and tripinfo.get(attr) not in {"", None}:
            return float(tripinfo.get(attr, "0"))
    return None


def sec(value: Any) -> str:
    if value in {"", None}:
        return ""
    return f"{float(value):.2f}"


def load_id_set(path: Path, column: str) -> set[str]:
    return {row[column] for row in read_csv(path) if row.get(column)}


def load_corridor_tls_ids(path: Path) -> set[str]:
    return load_id_set(path, "tls_id")


def load_corridor_edge_ids(path: Path) -> set[str]:
    rows = read_csv(path)
    if rows and "is_spine_edge" in rows[0]:
        return {row["edge_id"] for row in rows if row.get("edge_id") and row.get("is_spine_edge") == "True"}
    return {row["edge_id"] for row in rows if row.get("edge_id")}


def edge_free_flow_seconds(sumo_net: Any, edge_id: str) -> float:
    edge = sumo_net.getEdge(edge_id)
    speed = max(min(float(edge.getSpeed()), FREE_FLOW_SPEED_CAP_MPS), 0.01)
    return float(edge.getLength()) / speed


def route_free_flow_seconds(net_path: Path, edge_ids: list[str], include_edges: set[str] | None = None) -> float:
    sumo_net = S14.read_sumo_net(str(net_path))
    selected = [edge_id for edge_id in edge_ids if include_edges is None or edge_id in include_edges]
    if not selected and include_edges is not None:
        selected = edge_ids
    return sum(edge_free_flow_seconds(sumo_net, edge_id) for edge_id in selected)


def non_main_free_flow_seconds_by_edge(sumo_net: Any, corridor_edges: set[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for edge in sumo_net.getEdges():
        edge_id = edge.getID()
        if edge_id.startswith(":") or edge_id in corridor_edges:
            continue
        result[edge_id] = edge_free_flow_seconds(sumo_net, edge_id)
    return result


def windowed_edge_delay_record(
    edge_id: str,
    entered_at: float,
    left_at: float,
    free_flow_sec: float,
    window_start: float,
    censored: bool = False,
) -> dict[str, Any] | None:
    if left_at <= window_start:
        return None
    total_actual = max(left_at - entered_at, 0.0)
    overlap_actual = max(left_at - max(entered_at, window_start), 0.0)
    if overlap_actual <= 0:
        return None
    if total_actual > 0 and overlap_actual < total_actual:
        overlap_free = free_flow_sec * (overlap_actual / total_actual)
    else:
        overlap_free = free_flow_sec
    record: dict[str, Any] = {
        "actual_sec": overlap_actual,
        "free_flow_sec": overlap_free,
        "edge_id": edge_id,
    }
    if censored:
        record["censored"] = True
    return record


def tls_incoming_edges(net_path: Path, tls_id: str) -> set[str]:
    incoming: set[str] = set()
    for _event, elem in ET.iterparse(net_path, events=("end",)):
        if elem.tag == "connection" and elem.get("tl") == tls_id:
            from_edge = elem.get("from", "")
            if from_edge and not from_edge.startswith(":"):
                incoming.add(from_edge)
            elem.clear()
    return incoming


def parse_tl_logic(net_path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for _event, elem in ET.iterparse(net_path, events=("end",)):
        if elem.tag == "tlLogic" and elem.get("id"):
            tls_id = elem.get("id", "")
            phases = []
            for phase in elem.findall("phase"):
                phases.append(
                    {
                        "duration": float(phase.get("duration", "0") or 0),
                        "state": phase.get("state", ""),
                    }
                )
            result[tls_id] = {"program_id": elem.get("programID", ""), "phases": phases}
            elem.clear()
    return result


def phase_indices_for_link(phases: list[dict[str, Any]], link_index: int, chars: str) -> list[int]:
    indices = []
    for idx, phase in enumerate(phases):
        state = str(phase.get("state", ""))
        if 0 <= link_index < len(state) and state[link_index] in chars:
            indices.append(idx)
    return indices


def load_tls_plan_for_route(net_path: Path, tls_audit: Path, route_id: str, route_edges: list[str], corridor_tls_ids: set[str]) -> list[dict[str, Any]]:
    rows = []
    for row in read_csv(tls_audit):
        if row.get("route_id") != route_id or row.get("tls_id") not in corridor_tls_ids:
            continue
        green = [int(value) for value in row.get("green_phase_indices", "").split() if value.isdigit()]
        yellow = [int(value) for value in row.get("yellow_phase_indices", "").split() if value.isdigit()]
        clearance = [int(value) for value in row.get("all_red_or_clearance_phase_indices", "").split() if value.isdigit()]
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
                "yellow_phases": yellow,
                "clearance_phases": clearance,
                "is_controllable": row.get("is_controllable") == "True",
                "audit_status": row.get("audit_status", ""),
                "audit_reason": row.get("audit_reason", ""),
            }
        )
    rows.sort(key=lambda item: item["distance"])
    if rows:
        return rows
    sumo_net = S14.read_sumo_net(str(net_path))
    tl_logic = parse_tl_logic(net_path)
    edge_starts = S14.route_edge_starts(net_path, route_edges)
    seen: set[tuple[str, int]] = set()
    plan: list[dict[str, Any]] = []
    for idx, (from_id, to_id) in enumerate(zip(route_edges, route_edges[1:], strict=False)):
        outgoing = sumo_net.getEdge(from_id).getOutgoing().get(sumo_net.getEdge(to_id), [])
        for connection in outgoing:
            tls_id = connection.getTLSID()
            link_index = int(connection.getTLLinkIndex())
            if not tls_id or tls_id not in corridor_tls_ids or link_index < 0 or (tls_id, link_index) in seen:
                continue
            phases = tl_logic.get(tls_id, {}).get("phases", [])
            green = phase_indices_for_link(phases, link_index, "Gg")
            yellow = phase_indices_for_link(phases, link_index, "yY")
            clearance = phase_indices_for_link(phases, link_index, "rR")
            junction = connection.getJunction()
            junction_id = junction.getID() if hasattr(junction, "getID") else str(junction or tls_id)
            seen.add((tls_id, link_index))
            plan.append(
                {
                    "route_id": route_id,
                    "tls_id": tls_id,
                    "junction_id": junction_id,
                    "incoming": from_id,
                    "outgoing": to_id,
                    "distance": edge_starts[idx + 1] if idx + 1 < len(edge_starts) else edge_starts[idx],
                    "link_index": link_index,
                    "green_phases": green,
                    "yellow_phases": yellow,
                    "clearance_phases": clearance,
                    "is_controllable": bool(green),
                    "audit_status": "DYNAMIC",
                    "audit_reason": "generated_from_net_connections",
                }
            )
    plan.sort(key=lambda item: item["distance"])
    return plan


def load_queue_recovery_reference(net_path: Path, tls_audit: Path, corridor_tls_ids: set[str]) -> dict[str, Any]:
    route_row = synthetic_seoul_station_route(net_path)
    route_edges = route_row["route_edges"].split()
    tls_plan = load_tls_plan_for_route(net_path, tls_audit, SEOUL_STATION_ROUTE_ID, route_edges, corridor_tls_ids)
    if not tls_plan:
        return {"tls_id": "", "junction_id": "", "incoming_edges": set()}
    first_tls = tls_plan[0]
    return {
        "tls_id": first_tls["tls_id"],
        "junction_id": first_tls["junction_id"],
        "incoming_edges": tls_incoming_edges(net_path, first_tls["tls_id"]),
    }


def summarize_general_non_main_delay(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "N_delay_sec": 0.0,
            "general_non_main_actual_sec": 0.0,
            "general_non_main_free_flow_sec": 0.0,
            "general_non_main_vehicle_edge_count": 0,
            "N_delay_completed_vehicle_edge_count": 0,
            "N_delay_censored_vehicle_edge_count": 0,
        }
    total_delay = 0.0
    total_actual = 0.0
    total_free = 0.0
    for record in records:
        actual = float(record.get("actual_sec", 0.0) or 0.0)
        free = float(record.get("free_flow_sec", 0.0) or 0.0)
        total_delay += max(actual - free, 0.0)
        total_actual += actual
        total_free += free
    count = len(records)
    censored_count = sum(1 for record in records if record.get("censored"))
    return {
        "N_delay_sec": total_delay / count,
        "general_non_main_actual_sec": total_actual / count,
        "general_non_main_free_flow_sec": total_free / count,
        "general_non_main_vehicle_edge_count": count,
        "N_delay_completed_vehicle_edge_count": count - censored_count,
        "N_delay_censored_vehicle_edge_count": censored_count,
    }


def congestion_diagnostic(network_avg_speed_kmh: float) -> tuple[bool, str]:
    if 10.0 <= network_avg_speed_kmh <= 25.0:
        return True, "congested_10_to_25_kmh"
    if network_avg_speed_kmh > 25.0:
        return False, "weak_congestion_over_25_kmh"
    return False, "possible_gridlock_under_10_kmh"


def analysis_end_congestion_diagnostic(network_speed_kmh: float, running_count: int) -> tuple[bool, str]:
    if running_count <= 0:
        return False, "not_congested_no_running_vehicles"
    if network_speed_kmh <= 25.0:
        return True, "congestion_or_residual_queue_under_25_kmh"
    return False, "weak_congestion_over_25_kmh"


def queue_recovery_seconds(queue_history: list[tuple[float, int]], pass_time: float | None, emergency_depart: float) -> float:
    if pass_time is None or not queue_history:
        return 0.0
    baseline_candidates = [queue for time_value, queue in queue_history if time_value <= emergency_depart]
    baseline_queue = baseline_candidates[-1] if baseline_candidates else queue_history[0][1]
    recovery_time = None
    for time_value, queue in queue_history:
        if time_value >= pass_time and queue <= baseline_queue:
            recovery_time = time_value
            break
    if recovery_time is None:
        recovery_time = queue_history[-1][0]
    return max(float(recovery_time) - float(pass_time), 0.0)


def queue_recovery_detail_rows(
    queue_history_by_tls: dict[str, list[tuple[float, int]]],
    pass_time_by_tls: dict[str, float],
    tls_plan: list[dict[str, Any]],
    emergency_depart: float,
) -> list[dict[str, Any]]:
    rows = []
    for tls in tls_plan:
        tls_id = tls["tls_id"]
        history = queue_history_by_tls.get(tls_id, [])
        pass_time = pass_time_by_tls.get(tls_id)
        if pass_time is None or not history:
            continue
        baseline_candidates = [queue for time_value, queue in history if time_value <= emergency_depart]
        baseline_queue = baseline_candidates[-1] if baseline_candidates else history[0][1]
        recovery_time = None
        max_queue_after_pass = 0
        for time_value, queue in history:
            if time_value >= pass_time:
                max_queue_after_pass = max(max_queue_after_pass, queue)
                if recovery_time is None and queue <= baseline_queue:
                    recovery_time = time_value
        recovered = recovery_time is not None
        if recovery_time is None:
            recovery_time = history[-1][0]
        rows.append(
            {
                "tls_id": tls_id,
                "junction_id": tls.get("junction_id", ""),
                "passed_time_sec": sec(pass_time),
                "baseline_queue": baseline_queue,
                "recovered": recovered,
                "recovered_time_sec": sec(recovery_time) if recovered else "",
                "recovery_sec": sec(max(float(recovery_time) - float(pass_time), 0.0)),
                "max_queue_after_pass": max_queue_after_pass,
            }
        )
    return rows


def route_length_meters(net_path: Path, edge_ids: list[str]) -> float:
    sumo_net = S14.read_sumo_net(str(net_path))
    return sum(float(sumo_net.getEdge(edge_id).getLength()) for edge_id in edge_ids)


def queue_recovery_summary(
    queue_history_by_tls: dict[str, list[tuple[float, int]]],
    pass_time_by_tls: dict[str, float],
    tls_plan: list[dict[str, Any]],
    emergency_depart: float,
) -> dict[str, Any]:
    details = queue_recovery_detail_rows(queue_history_by_tls, pass_time_by_tls, tls_plan, emergency_depart)
    recovery_values = [(row["tls_id"], float(row["recovery_sec"])) for row in details]
    unrecovered = sum(1 for row in details if row["recovered"] is False)
    if not recovery_values:
        return {
            "T_recovery_sec": 0.0,
            "T_recovery_tls_count": 0,
            "T_recovery_max_tls_id": "",
            "T_recovery_unrecovered_count": 0,
        }
    max_tls, max_recovery = max(recovery_values, key=lambda item: item[1])
    return {
        "T_recovery_sec": max_recovery,
        "T_recovery_tls_count": len(recovery_values),
        "T_recovery_max_tls_id": max_tls,
        "T_recovery_unrecovered_count": unrecovered,
    }


def build_tasks(args: argparse.Namespace, route_ids: list[str], b2_params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks = []
    for repeat_idx in range(1, args.repeats + 1):
        repeat_id = f"repeat_{repeat_idx:03d}"
        for route_id in route_ids:
            if "B00" in args.modes:
                tasks.append({"mode": "B00", "parameter_id": "freeflow", "route_id": route_id, "repeat_id": repeat_id, "params": {}, "include_background": False})
            if "B0" in args.modes:
                tasks.append({"mode": "B0", "parameter_id": "no_control", "route_id": route_id, "repeat_id": repeat_id, "params": {}, "include_background": True})
            if "B2" in args.modes:
                for params in b2_params:
                    tasks.append({"mode": "B2", "parameter_id": params["parameter_id"], "route_id": route_id, "repeat_id": repeat_id, "params": params, "include_background": True})
    return tasks


def common_row_base(task: dict[str, Any], run_dir: Path, vehicle_id: str, params: dict[str, Any], elapsed_sec: float) -> dict[str, Any]:
    route_row = task.get("route_row") or {}
    route_edges = str(route_row.get("route_edges", "")).split()
    return {
        "generated_at": task.get("generated_at", ""),
        "run_id": task.get("run_id", ""),
        "timeout_steps": task.get("timeout_steps", ""),
        "command_time_to_teleport": task.get("time_to_teleport", ""),
        "recovery_buffer_sec": sec(task.get("recovery_buffer_sec", DEFAULT_RECOVERY_BUFFER_SEC)),
        "pipeline": task.get("pipeline", ""),
        "output_prefix": task["output_prefix"],
        "mode": task["mode"],
        "parameter_id": task["parameter_id"],
        "repeat_id": task["repeat_id"],
        "route_id": task["route_id"],
        "route_start_edge": route_edges[0] if route_edges else "",
        "route_target_edge": route_row.get("target_edge_id", route_edges[-1] if route_edges else ""),
        "route_policy": route_row.get("selected_policy", ""),
        "emergency_vehicle_id": vehicle_id,
        "run_dir": rel(run_dir),
        "elapsed_wall_sec": sec(elapsed_sec),
        "background_vehicle_count": int(task.get("background_vehicle_count", 0) or 0),
        "D_det": params.get("D_det", ""),
        "alpha": params.get("alpha", ""),
        "G_ext": params.get("G_ext", ""),
        "T_change_sec": sec(params.get("T_change_sec", "")) if task["mode"] == "B2" else "",
        "w1": sec(SCORE_WEIGHT_A),
        "w2": sec(SCORE_WEIGHT_N),
        "w3": sec(SCORE_WEIGHT_RECOVERY),
        "effective_T_change_sec": sec(params.get("_effective_T_change_sec", "")),
        "effective_alpha_sec": sec(params.get("_effective_alpha_sec", "")),
        "effective_G_ext_sec": sec(params.get("_effective_G_ext_sec", "")),
    }


def phase_state_for_link(traci: Any, tls_id: str, phase_index: int, link_index: int) -> str:
    try:
        phases = traci.trafficlight.getAllProgramLogics(tls_id)[0].phases
        state = phases[phase_index].state
    except Exception:  # noqa: BLE001 - TraCI may expose incomplete TLS programs.
        return ""
    if 0 <= link_index < len(state):
        return state[link_index]
    return ""


def integer_seconds(value: Any, field_name: str) -> tuple[int | None, str]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None, f"{field_name}_not_numeric"
    rounded = round(parsed)
    if abs(parsed - rounded) > 1e-9:
        return None, f"{field_name}_must_be_integer_seconds"
    if rounded < 0:
        return None, f"{field_name}_must_be_nonnegative"
    return int(rounded), ""


def phase_remaining_seconds(traci: Any, tls_id: str, sim_time: float) -> float:
    try:
        return max(float(traci.trafficlight.getNextSwitch(tls_id)) - sim_time, 0.0)
    except Exception:  # noqa: BLE001 - TraCI may not expose next switch for malformed TLS.
        return 0.0


def extend_current_green_without_shortening(traci: Any, tls: dict[str, Any], min_remaining_sec: int, sim_time: float) -> tuple[str, int | str, str]:
    tls_id = tls["tls_id"]
    current_phase = int(traci.trafficlight.getPhase(tls_id))
    green_phases = list(tls.get("green_phases") or [])
    if not green_phases:
        return "failed", current_phase, "no_green_phase_for_emergency_link"
    if current_phase in green_phases:
        current_remaining = phase_remaining_seconds(traci, tls_id, sim_time)
        target_remaining = max(int(round(current_remaining)), int(min_remaining_sec))
        traci.trafficlight.setPhaseDuration(tls_id, target_remaining)
        return "extend_green", current_phase, "current_phase_already_green"
    return "wait_for_sequence_green", current_phase, "phase_sequence_preserved_wait_for_green"


def phase_duration_seconds(traci: Any, tls_id: str, phase_index: int, fallback: int) -> int:
    try:
        phases = traci.trafficlight.getAllProgramLogics(tls_id)[0].phases
        return max(1, int(round(float(phases[phase_index].duration))))
    except Exception:  # noqa: BLE001 - TraCI program logic may be incomplete.
        return max(1, int(fallback))


def start_clearance_before_green(traci: Any, tls: dict[str, Any]) -> tuple[str, int | str, str, int]:
    tls_id = tls["tls_id"]
    current_phase = int(traci.trafficlight.getPhase(tls_id))
    clearance_candidates = list(tls.get("yellow_phases") or []) + list(tls.get("clearance_phases") or [])
    if current_phase in clearance_candidates:
        return "clearance_before_green", current_phase, "current_clearance_phase_preserved", 0
    if not clearance_candidates:
        return "failed", current_phase, "no_yellow_or_clearance_phase_for_emergency_link", 0
    clearance_phase = int(clearance_candidates[0])
    duration = phase_duration_seconds(traci, tls_id, clearance_phase, CLEARANCE_BEFORE_GREEN_SEC)
    traci.trafficlight.setPhase(tls_id, clearance_phase)
    traci.trafficlight.setPhaseDuration(tls_id, duration)
    return "clearance_before_green", clearance_phase, "t_change_elapsed_clearance_inserted_before_green", duration


def switch_to_green_after_t_change(traci: Any, tls: dict[str, Any], g_ext: int) -> tuple[str, int | str, str]:
    tls_id = tls["tls_id"]
    green_phases = list(tls.get("green_phases") or [])
    if not green_phases:
        return "failed", traci.trafficlight.getPhase(tls_id), "no_green_phase_for_emergency_link"
    current_phase = int(traci.trafficlight.getPhase(tls_id))
    if current_phase in green_phases:
        action, phase_after, reason = extend_current_green_without_shortening(traci, tls, g_ext, float("inf"))
        return action, phase_after, f"green_arrived_before_t_change_switch:{reason}"
    target_phase = int(green_phases[0])
    traci.trafficlight.setPhase(tls_id, target_phase)
    traci.trafficlight.setPhaseDuration(tls_id, g_ext)
    return "switch_to_green_after_t_change", target_phase, "t_change_elapsed_clearance_completed_then_green"


def actual_upcoming_corridor_tls(traci: Any, vehicle_id: str, corridor_tls_ids: set[str]) -> dict[str, Any] | None:
    try:
        upcoming = traci.vehicle.getNextTLS(vehicle_id)
    except Exception:  # noqa: BLE001 - vehicle may have left the network.
        return None
    for item in upcoming:
        tls_id = str(item[0])
        if tls_id not in corridor_tls_ids:
            continue
        return {
            "tls_id": tls_id,
            "link_index": int(item[1]),
            "distance": float(item[2]),
            "state": str(item[3]),
        }
    return None


def match_tls_plan(tls_plan: list[dict[str, Any]], actual_tls: dict[str, Any], touched: dict[str, dict[str, Any]], pending: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    tls_id = actual_tls["tls_id"]
    link_index = actual_tls["link_index"]
    for candidate in tls_plan:
        if candidate["tls_id"] in touched or candidate["tls_id"] in pending:
            continue
        if candidate["tls_id"] == tls_id and int(candidate.get("link_index", -1)) == link_index:
            return candidate
    for candidate in tls_plan:
        if candidate["tls_id"] in touched or candidate["tls_id"] in pending:
            continue
        if candidate["tls_id"] == tls_id:
            return candidate
    return None


def vehicle_lane_connection_ok(sumo_net: Any, lane_id: str, next_edge_id: str) -> bool | None:
    if not lane_id or lane_id.startswith(":") or not next_edge_id:
        return None
    try:
        lane = sumo_net.getLane(lane_id)
    except Exception:  # noqa: BLE001 - lane may be internal or absent in sumolib.
        return None
    try:
        for connection in lane.getOutgoing():
            to_lane = connection.getToLane()
            if to_lane is not None and to_lane.getEdge().getID() == next_edge_id:
                return True
    except Exception:  # noqa: BLE001 - older sumolib may expose lane links differently.
        return None
    return False


def route_connected_lane_indices(net_path: Path, route_edges: list[str]) -> dict[tuple[str, str], set[int]]:
    route_pairs = set(zip(route_edges, route_edges[1:], strict=False))
    result: dict[tuple[str, str], set[int]] = {pair: set() for pair in route_pairs}
    for _event, elem in ET.iterparse(net_path, events=("end",)):
        if elem.tag == "connection":
            key = (elem.get("from", ""), elem.get("to", ""))
            if key in result and str(elem.get("fromLane", "")).isdigit():
                result[key].add(int(elem.get("fromLane", "0")))
            elem.clear()
    return result


def lane_index_from_id(lane_id: str) -> int | None:
    try:
        return int(lane_id.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return None


def run_traci_experiment(
    task: dict[str, Any],
    paths: dict[str, Path],
    tls_plan: list[dict[str, Any]],
    route_edges: list[str],
    params: dict[str, Any],
    control_enabled: bool,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    traci = S14.import_traci()
    sumo = shutil.which("sumo")
    if sumo is None:
        raise ExperimentError("missing_executable: sumo")
    args = SimpleNamespace(
        net=Path(task["net"]),
        route_id=task["route_id"],
        emergency_vehicle_id=task["vehicle_id"],
        timeout_steps=int(task["timeout_steps"]),
    )
    d_det = float(params.get("D_det", 0.0) or 0.0)
    alpha = int(params.get("_effective_alpha_sec", 0) or 0)
    effective_g_ext = max(1, int(params.get("_effective_G_ext_sec", 0) or 0))
    effective_t_change = max(0, int(params.get("_effective_T_change_sec", DEFAULT_T_CHANGE_SEC) or 0))
    metric_sample_interval = max(1, int(float(params.get("metric_sample_interval") or 10)))
    edge_starts = S14.route_edge_starts(Path(task["net"]), route_edges)
    connected_lanes_by_transition = route_connected_lane_indices(Path(task["net"]), route_edges)
    corridor_edges = load_corridor_edge_ids(Path(task["corridor_edges"]))
    sumo_net = S14.read_sumo_net(str(Path(task["net"])))
    non_main_free_flow = non_main_free_flow_seconds_by_edge(sumo_net, corridor_edges)
    corridor_tls_ids = load_corridor_tls_ids(Path(task["priority_terminals"]))
    recovery_queue_edges_by_tls = {
        tls["tls_id"]: sorted(tls_incoming_edges(Path(task["net"]), tls["tls_id"]))
        for tls in tls_plan
        if tls.get("tls_id")
    }
    events: list[dict[str, Any]] = []
    touched: dict[str, dict[str, Any]] = {}
    controlled: dict[str, dict[str, Any]] = {}
    pending_t_change: dict[str, dict[str, Any]] = {}
    restored: set[str] = set()
    lane_connection_warnings: set[tuple[str, str, str]] = set()
    lane_connection_candidates: dict[tuple[str, str, str], int] = {}
    emergency_lane_guidance_events: set[tuple[str, str, int]] = set()
    edge_time: dict[str, float] = {}
    general_non_main_records: list[dict[str, Any]] = []
    active_general_non_main: dict[str, tuple[str, float]] = {}
    n_delay_window_start = float(task["emergency_depart"])
    dynamic_emergency_insert = bool(task.get("dynamic_emergency_insert"))
    dynamic_emergency_inserted = not dynamic_emergency_insert
    emergency_last_state: dict[str, Any] = {}

    def append_general_non_main_record(edge_id: str, entered_at: float, left_at: float, censored: bool = False) -> bool:
        record = windowed_edge_delay_record(
            edge_id,
            entered_at,
            left_at,
            non_main_free_flow.get(edge_id, 0.0),
            n_delay_window_start,
            censored,
        )
        if record is None:
            return False
        general_non_main_records.append(record)
        return True

    queue_history_by_tls: dict[str, list[tuple[float, int]]] = {tls_id: [] for tls_id in recovery_queue_edges_by_tls}
    recovery_pass_time_by_tls: dict[str, float] = {}
    recovery_time_by_tls: dict[str, float] = {}
    baseline_queue_by_tls: dict[str, int] = {}
    tls_recovery_times: list[float] = []
    recovery_buffer_sec = max(0, int(task.get("recovery_buffer_sec", DEFAULT_RECOVERY_BUFFER_SEC) or 0))
    emergency_seen = False
    emergency_left_time: float | None = None
    latest_queue_recovery_time: float | None = None
    analysis_stop_reason = "simulation_completed"
    controller_started = False
    cmd = [sumo, "-c", str(paths["sumocfg"]), "--error-log", str(paths["stderr"])]
    started_wall = time.time()
    wall_timeout = False
    with paths["stdout"].open("w", encoding="utf-8") as stdout:
        traci.start(cmd, stdout=stdout)
        controller_started = True
        try:
            edge_ids = set(traci.edge.getIDList())
            for tls_id, recovery_queue_edges in recovery_queue_edges_by_tls.items():
                queue = sum(int(traci.edge.getLastStepHaltingNumber(edge_id)) for edge_id in recovery_queue_edges if edge_id in edge_ids)
                baseline_queue_by_tls[tls_id] = queue
                queue_history_by_tls.setdefault(tls_id, []).append((0.0, queue))
            last_metric_sample = -metric_sample_interval
            while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() <= int(task["timeout_steps"]):
                if time.time() - started_wall > int(task["timeout_sec"]):
                    wall_timeout = True
                    break
                traci.simulationStep()
                sim_time = float(traci.simulation.getTime())
                vehicle_ids = set(traci.vehicle.getIDList())
                if dynamic_emergency_insert and not dynamic_emergency_inserted and sim_time >= float(task["emergency_depart"]):
                    try:
                        traci.vehicle.add(
                            args.emergency_vehicle_id,
                            args.route_id,
                            typeID="b1_emergency_type",
                            depart="now",
                            departLane="free",
                            departPos="last",
                            departSpeed="max",
                        )
                        traci.vehicle.updateBestLanes(args.emergency_vehicle_id)
                    except Exception as exc:  # noqa: BLE001 - TraCI exception type differs by SUMO version.
                        events.append(
                            {
                                "time": sec(sim_time),
                                "route_id": args.route_id,
                                "vehicle_id": args.emergency_vehicle_id,
                                "action": "dynamic_emergency_insert_failed",
                                "reason": str(exc),
                            }
                        )
                        raise ExperimentError(f"dynamic_emergency_insert_failed: {exc}") from exc
                    dynamic_emergency_inserted = True
                    events.append(
                        {
                            "time": sec(sim_time),
                            "route_id": args.route_id,
                            "vehicle_id": args.emergency_vehicle_id,
                            "action": "dynamic_emergency_insert",
                            "reason": f"emergency_depart_{sec(task['emergency_depart'])}s",
                        }
                    )
                    vehicle_ids = set(traci.vehicle.getIDList())
                for vehicle_id, (edge_id, entered_at) in list(active_general_non_main.items()):
                    if vehicle_id not in vehicle_ids:
                        append_general_non_main_record(edge_id, entered_at, sim_time)
                        active_general_non_main.pop(vehicle_id, None)
                for vehicle_id in vehicle_ids:
                    if vehicle_id == args.emergency_vehicle_id:
                        continue
                    road = traci.vehicle.getRoadID(vehicle_id)
                    active = active_general_non_main.get(vehicle_id)
                    if road in non_main_free_flow:
                        if active is None:
                            active_general_non_main[vehicle_id] = (road, sim_time)
                        elif active[0] != road:
                            append_general_non_main_record(active[0], active[1], sim_time)
                            active_general_non_main[vehicle_id] = (road, sim_time)
                    elif active is not None:
                        append_general_non_main_record(active[0], active[1], sim_time)
                        active_general_non_main.pop(vehicle_id, None)
                vehicle_present = args.emergency_vehicle_id in vehicle_ids
                if vehicle_present:
                    emergency_seen = True
                elif emergency_seen and emergency_left_time is None:
                    emergency_left_time = sim_time
                current_distance = 0.0
                road_id = ""
                edge_ids = set(traci.edge.getIDList())
                for tls_id, recovery_queue_edges in recovery_queue_edges_by_tls.items():
                    queue = sum(int(traci.edge.getLastStepHaltingNumber(edge_id)) for edge_id in recovery_queue_edges if edge_id in edge_ids)
                    queue_history_by_tls.setdefault(tls_id, []).append((sim_time, queue))
                    if sim_time <= float(task["emergency_depart"]):
                        baseline_queue_by_tls[tls_id] = queue
                    pass_time = recovery_pass_time_by_tls.get(tls_id)
                    if pass_time is not None and tls_id not in recovery_time_by_tls and sim_time >= pass_time and queue <= baseline_queue_by_tls.get(tls_id, 0):
                        recovery_time_by_tls[tls_id] = sim_time
                if vehicle_present:
                    road_id = traci.vehicle.getRoadID(args.emergency_vehicle_id)
                    if road_id and not road_id.startswith(":"):
                        edge_time[road_id] = edge_time.get(road_id, 0.0) + 1.0
                    route_index = int(traci.vehicle.getRouteIndex(args.emergency_vehicle_id))
                    lane_position = float(traci.vehicle.getLanePosition(args.emergency_vehicle_id))
                    emergency_last_state = {
                        "edge_id": road_id,
                        "route_index": route_index,
                        "lane_id": traci.vehicle.getLaneID(args.emergency_vehicle_id),
                        "speed_kmh": float(traci.vehicle.getSpeed(args.emergency_vehicle_id)) * 3.6,
                        "waiting_time_sec": float(traci.vehicle.getWaitingTime(args.emergency_vehicle_id)),
                        "progress_ratio": (route_index / max(len(route_edges) - 1, 1)) if route_index >= 0 else 0.0,
                    }
                    current_distance = edge_starts[route_index] + lane_position if 0 <= route_index < len(edge_starts) else 0.0
                    if 0 <= route_index < len(route_edges) - 1 and road_id and not road_id.startswith(":"):
                        lane_id = traci.vehicle.getLaneID(args.emergency_vehicle_id)
                        next_edge_id = route_edges[route_index + 1]
                        connected_lane_indices = connected_lanes_by_transition.get((road_id, next_edge_id), set())
                        current_lane_index = lane_index_from_id(lane_id)
                        if connected_lane_indices and current_lane_index is not None and current_lane_index not in connected_lane_indices:
                            target_lane_index = min(connected_lane_indices, key=lambda item: abs(item - current_lane_index))
                            try:
                                traci.vehicle.changeLane(args.emergency_vehicle_id, target_lane_index, 20)
                                guidance_key = (road_id, next_edge_id, target_lane_index)
                                if guidance_key not in emergency_lane_guidance_events:
                                    emergency_lane_guidance_events.add(guidance_key)
                                    events.append(
                                        {
                                            "time": sec(sim_time),
                                            "route_id": args.route_id,
                                            "vehicle_id": args.emergency_vehicle_id,
                                            "tls_id": "",
                                            "junction_id": "",
                                            "incoming": road_id,
                                            "outgoing": next_edge_id,
                                            "remaining_distance_m": "0.00",
                                            "D_det": d_det,
                                            "alpha": params.get("alpha", ""),
                                            "G_ext": params.get("G_ext", ""),
                                            "effective_alpha_sec": sec(alpha),
                                            "effective_G_ext_sec": sec(effective_g_ext),
                                            "current_road_id": road_id,
                                            "phase_before": "",
                                            "phase_after": "",
                                            "action": "emergency_lane_guidance",
                                            "reason": f"change_lane_{current_lane_index}_to_{target_lane_index}_for_next_edge:{next_edge_id}",
                                            "restore_action": "",
                                        }
                                    )
                            except Exception as exc:  # noqa: BLE001 - failed guidance is diagnostic only.
                                events.append(
                                    {
                                        "time": sec(sim_time),
                                        "route_id": args.route_id,
                                        "vehicle_id": args.emergency_vehicle_id,
                                        "tls_id": "",
                                        "junction_id": "",
                                        "incoming": road_id,
                                        "outgoing": next_edge_id,
                                        "remaining_distance_m": "0.00",
                                        "D_det": d_det,
                                        "alpha": params.get("alpha", ""),
                                        "G_ext": params.get("G_ext", ""),
                                        "effective_alpha_sec": sec(alpha),
                                        "effective_G_ext_sec": sec(effective_g_ext),
                                        "current_road_id": road_id,
                                        "phase_before": "",
                                        "phase_after": "",
                                        "action": "emergency_lane_guidance_failed",
                                        "reason": str(exc),
                                        "restore_action": "",
                                    }
                                )
                        lane_connection_ok = vehicle_lane_connection_ok(sumo_net, lane_id, next_edge_id)
                        warning_key = (lane_id, road_id, next_edge_id)
                        if lane_connection_ok is False:
                            lane_connection_candidates[warning_key] = lane_connection_candidates.get(warning_key, 0) + 1
                        else:
                            lane_connection_candidates.pop(warning_key, None)
                        if lane_connection_candidates.get(warning_key, 0) >= 2 and warning_key not in lane_connection_warnings:
                            lane_connection_warnings.add(warning_key)
                            events.append(
                                {
                                    "time": sec(sim_time),
                                    "route_id": args.route_id,
                                    "vehicle_id": args.emergency_vehicle_id,
                                    "tls_id": "",
                                    "junction_id": "",
                                    "incoming": road_id,
                                    "outgoing": next_edge_id,
                                    "remaining_distance_m": "0.00",
                                    "D_det": d_det,
                                    "alpha": params.get("alpha", ""),
                                    "G_ext": params.get("G_ext", ""),
                                    "effective_alpha_sec": sec(alpha),
                                    "effective_G_ext_sec": sec(effective_g_ext),
                                    "current_road_id": road_id,
                                    "phase_before": "",
                                    "phase_after": "",
                                    "action": "lane_connection_warning",
                                    "reason": f"current_lane_has_no_connection_to_next_edge:{lane_id}->{next_edge_id}",
                                    "restore_action": "",
                                }
                            )
                    for tls in tls_plan:
                        tls_id = tls["tls_id"]
                        if tls_id not in recovery_pass_time_by_tls and current_distance > float(tls["distance"]) + 10.0:
                            recovery_pass_time_by_tls[tls_id] = sim_time
                    for tls_id, record in list(controlled.items()):
                        if tls_id in restored:
                            continue
                        if current_distance <= float(record["distance"]) + 10.0:
                            continue
                        if "pass_time" not in record:
                            record["pass_time"] = sim_time
                            if alpha > 0:
                                action, phase_after, reason = extend_current_green_without_shortening(traci, record, alpha, sim_time)
                                if action == "extend_green":
                                    action = "alpha_hold_extend"
                                events.append(
                                    {
                                        "time": sec(sim_time),
                                        "route_id": args.route_id,
                                        "vehicle_id": args.emergency_vehicle_id,
                                        "tls_id": tls_id,
                                        "junction_id": record["junction_id"],
                                        "incoming": record["incoming"],
                                        "outgoing": record["outgoing"],
                                        "remaining_distance_m": "0.00",
                                        "D_det": d_det,
                                        "alpha": params.get("alpha", ""),
                                        "G_ext": params.get("G_ext", ""),
                                        "effective_alpha_sec": sec(alpha),
                                        "effective_G_ext_sec": sec(effective_g_ext),
                                        "current_road_id": road_id,
                                        "phase_before": record["phase_after"],
                                        "phase_after": phase_after,
                                        "action": action,
                                        "reason": f"emergency_passed_tls_alpha_hold_{alpha}s:{reason}",
                                        "restore_action": "",
                                        "pass_time": sec(record.get("pass_time", "")),
                                    }
                                )
                        if sim_time < float(record["pass_time"]) + alpha:
                            continue
                        restore_action = "no_program_restore_needed_sequence_preserved"
                        try:
                            phase_after = traci.trafficlight.getPhase(tls_id)
                        except Exception:  # noqa: BLE001
                            phase_after = ""
                        restored.add(tls_id)
                        recovery_sec = max(sim_time - float(record.get("pass_time", sim_time)), 0.0)
                        tls_recovery_times.append(recovery_sec)
                        events.append(
                            {
                                "time": sec(sim_time),
                                "route_id": args.route_id,
                                "vehicle_id": args.emergency_vehicle_id,
                                "tls_id": tls_id,
                                "junction_id": record["junction_id"],
                                "incoming": record["incoming"],
                                "outgoing": record["outgoing"],
                                "remaining_distance_m": "0.00",
                                "D_det": d_det,
                                "alpha": alpha,
                                "G_ext": params.get("G_ext", ""),
                                "effective_alpha_sec": sec(alpha),
                                "effective_G_ext": effective_g_ext,
                                "effective_G_ext_sec": sec(effective_g_ext),
                                "current_road_id": road_id,
                                "phase_before": record["phase_after"],
                                "phase_after": phase_after,
                                "action": "restore",
                                "reason": f"emergency_passed_tls_alpha_hold_{sec(alpha)}s",
                                "restore_action": restore_action,
                                "pass_time": sec(record.get("pass_time", "")),
                                "recovery_sec": sec(recovery_sec),
                            }
                        )
                for tls_id, pending in list(pending_t_change.items()):
                    if not vehicle_present:
                        continue
                    if current_distance > float(pending["distance"]) + 10.0:
                        touched[tls_id] = {"action": "green_missed_before_t_change"}
                        events.append(
                            {
                                **pending["event_base"],
                                "time": sec(sim_time),
                                "phase_before": traci.trafficlight.getPhase(tls_id),
                                "phase_after": traci.trafficlight.getPhase(tls_id),
                                "action": "green_missed_before_t_change",
                                "reason": "vehicle_passed_before_t_change_green_switch",
                                "restore_action": "",
                            }
                        )
                        pending_t_change.pop(tls_id, None)
                        continue
                    current_phase = int(traci.trafficlight.getPhase(tls_id))
                    if current_phase in pending.get("green_phases", []):
                        before = current_phase
                        action, phase_after, reason = extend_current_green_without_shortening(traci, pending, effective_g_ext, sim_time)
                        touched[tls_id] = {"action": action}
                        if action in CONTROL_ACTIONS:
                            controlled[tls_id] = {
                                **pending,
                                "original_program": pending["original_program"],
                                "original_phase": before,
                                "phase_after": phase_after,
                            }
                        events.append(
                            {
                                **pending["event_base"],
                                "time": sec(sim_time),
                                "phase_before": before,
                                "phase_after": phase_after,
                                "action": action,
                                "reason": "green_arrived_before_t_change_then_extended" if action in CONTROL_ACTIONS else reason,
                                "restore_action": "",
                            }
                        )
                        pending_t_change.pop(tls_id, None)
                        continue
                    request_time = float(pending["request_time"])
                    if sim_time < request_time + effective_t_change:
                        if not pending.get("wait_logged"):
                            pending["wait_logged"] = True
                            events.append(
                                {
                                    **pending["event_base"],
                                    "time": sec(sim_time),
                                    "phase_before": current_phase,
                                    "phase_after": current_phase,
                                    "action": "wait_t_change",
                                    "reason": f"waiting_until_t_change_{sec(effective_t_change)}s",
                                    "restore_action": "",
                                }
                            )
                        continue
                    if not pending.get("clearance_started"):
                        action, phase_after, reason, clearance_duration = start_clearance_before_green(traci, pending)
                        pending["clearance_started"] = True
                        pending["clearance_until"] = sim_time + clearance_duration
                        events.append(
                            {
                                **pending["event_base"],
                                "time": sec(sim_time),
                                "phase_before": current_phase,
                                "phase_after": phase_after,
                                "action": action,
                                "reason": reason,
                                "restore_action": "",
                            }
                        )
                        if action == "failed":
                            touched[tls_id] = {"action": action}
                            pending_t_change.pop(tls_id, None)
                        continue
                    if current_phase in pending.get("yellow_phases", []) + pending.get("clearance_phases", []):
                        continue
                    before = current_phase
                    action, phase_after, reason = switch_to_green_after_t_change(traci, pending, effective_g_ext)
                    touched[tls_id] = {"action": action}
                    if action in CONTROL_ACTIONS:
                        controlled[tls_id] = {
                            **pending,
                            "original_program": pending["original_program"],
                            "original_phase": before,
                            "phase_after": phase_after,
                        }
                    events.append(
                        {
                            **pending["event_base"],
                            "time": sec(sim_time),
                            "phase_before": before,
                            "phase_after": phase_after,
                            "action": action,
                            "reason": reason,
                            "restore_action": "",
                        }
                    )
                    pending_t_change.pop(tls_id, None)
                expected_recovery_tls = [tls["tls_id"] for tls in tls_plan if tls.get("tls_id")]
                if emergency_left_time is not None and all(tls_id in recovery_time_by_tls for tls_id in expected_recovery_tls):
                    latest_recovery = max([emergency_left_time, *recovery_time_by_tls.values()])
                    latest_queue_recovery_time = max(recovery_time_by_tls.values()) if recovery_time_by_tls else emergency_left_time
                    if sim_time >= latest_recovery + recovery_buffer_sec:
                        analysis_stop_reason = "emergency_arrived_queue_recovered_buffer_elapsed"
                        events.append(
                            {
                                "time": sec(sim_time),
                                "route_id": args.route_id,
                                "vehicle_id": args.emergency_vehicle_id,
                                "action": "analysis_stop",
                                "reason": analysis_stop_reason,
                                "recovery_sec": sec(max(latest_recovery - emergency_left_time, 0.0)),
                            }
                        )
                        break
                if not control_enabled or not vehicle_present:
                    continue
                if sim_time - last_metric_sample < metric_sample_interval and int(sim_time) % metric_sample_interval != 0:
                    pass
                next_tls = None
                actual_next_tls = actual_upcoming_corridor_tls(traci, args.emergency_vehicle_id, corridor_tls_ids)
                if actual_next_tls is not None:
                    next_tls = match_tls_plan(tls_plan, actual_next_tls, touched, pending_t_change)
                if next_tls is None:
                    continue
                remaining_distance = max(float(actual_next_tls["distance"]), 0.0)
                if remaining_distance > d_det:
                    if sim_time - last_metric_sample >= metric_sample_interval:
                        last_metric_sample = sim_time
                        events.append(
                            {
                                "time": sec(sim_time),
                                "route_id": args.route_id,
                                "vehicle_id": args.emergency_vehicle_id,
                                "tls_id": next_tls["tls_id"],
                                "junction_id": next_tls["junction_id"],
                                "incoming": next_tls["incoming"],
                                "outgoing": next_tls["outgoing"],
                                "remaining_distance_m": sec(remaining_distance),
                                "D_det": d_det,
                                "alpha": alpha,
                                "G_ext": params.get("G_ext", ""),
                                "effective_alpha_sec": sec(alpha),
                                "effective_G_ext": effective_g_ext,
                                "effective_G_ext_sec": sec(effective_g_ext),
                                "current_road_id": road_id,
                                "phase_before": "",
                                "phase_after": "",
                                "action": "observe_wait",
                                "reason": "outside_detection_distance",
                                "restore_action": "",
                            }
                        )
                    continue
                tls_id = next_tls["tls_id"]
                event_base = {
                    "time": sec(sim_time),
                    "route_id": args.route_id,
                    "vehicle_id": args.emergency_vehicle_id,
                    "tls_id": tls_id,
                    "junction_id": next_tls["junction_id"],
                    "incoming": next_tls["incoming"],
                    "outgoing": next_tls["outgoing"],
                    "remaining_distance_m": sec(remaining_distance),
                    "D_det": d_det,
                    "alpha": alpha,
                    "G_ext": params.get("G_ext", ""),
                    "effective_alpha_sec": sec(alpha),
                    "effective_G_ext": effective_g_ext,
                    "effective_G_ext_sec": sec(effective_g_ext),
                    "current_road_id": road_id,
                }
                if not next_tls["is_controllable"]:
                    touched[tls_id] = {"action": "skip"}
                    events.append({**event_base, "phase_before": "", "phase_after": "", "action": "skip", "reason": next_tls["audit_reason"] or "audit_not_controllable", "restore_action": ""})
                    continue
                current_phase = int(traci.trafficlight.getPhase(tls_id))
                original_program = traci.trafficlight.getProgram(tls_id)
                if current_phase not in next_tls.get("green_phases", []):
                    pending_t_change[tls_id] = {**next_tls, "original_program": original_program, "event_base": event_base, "request_time": sim_time}
                    reason = "yellow_or_clearance_active" if current_phase in next_tls.get("yellow_phases", []) or current_phase in next_tls.get("clearance_phases", []) else "red_or_other_phase_active"
                    events.append(
                        {
                            **event_base,
                            "phase_before": current_phase,
                            "phase_after": current_phase,
                            "action": "request_green",
                            "reason": f"{reason};t_change_timer_started",
                            "restore_action": "",
                        }
                    )
                    continue
                before = current_phase
                action, phase_after, reason = extend_current_green_without_shortening(traci, next_tls, effective_g_ext, sim_time)
                touched[tls_id] = {"action": action}
                if action in CONTROL_ACTIONS:
                    controlled[tls_id] = {
                        **next_tls,
                        "original_program": original_program,
                        "original_phase": before,
                        "phase_after": phase_after,
                    }
                events.append({**event_base, "phase_before": before, "phase_after": phase_after, "action": action, "reason": reason, "restore_action": ""})
            if traci.simulation.getTime() > int(task["timeout_steps"]):
                events.append({"time": sec(traci.simulation.getTime()), "route_id": args.route_id, "action": "timeout", "reason": "controller_timeout_steps"})
            if wall_timeout:
                events.append({"time": sec(traci.simulation.getTime()), "route_id": args.route_id, "action": "timeout", "reason": "controller_timeout_sec"})
            analysis_end_time = float(traci.simulation.getTime())
            censored_active_general_non_main_count = 0
            for vehicle_id, (edge_id, entered_at) in list(active_general_non_main.items()):
                if append_general_non_main_record(edge_id, entered_at, analysis_end_time, censored=True):
                    censored_active_general_non_main_count += 1
            excluded_active_general_non_main_count = 0
            active_general_non_main.clear()
        finally:
            traci.close(False)
    return events, controller_started, {
        "edge_time": edge_time,
        "queue_history_by_tls": queue_history_by_tls,
        "recovery_pass_time_by_tls": recovery_pass_time_by_tls,
        "recovery_queue_edges_by_tls": recovery_queue_edges_by_tls,
        "tls_plan": tls_plan,
        "general_non_main_records": general_non_main_records,
        "analysis_end_time": analysis_end_time if "analysis_end_time" in locals() else 0.0,
        "analysis_stop_reason": analysis_stop_reason,
        "dynamic_emergency_inserted": dynamic_emergency_inserted,
        "emergency_seen": emergency_seen,
        "emergency_left_time": emergency_left_time,
        "latest_queue_recovery_time": latest_queue_recovery_time,
        "censored_active_general_non_main_count": censored_active_general_non_main_count if "censored_active_general_non_main_count" in locals() else 0,
        "excluded_active_general_non_main_count": excluded_active_general_non_main_count if "excluded_active_general_non_main_count" in locals() else len(active_general_non_main),
        "tls_recovery_times": tls_recovery_times,
        "lane_connection_warning_count": len(lane_connection_warnings),
        "wall_timeout": wall_timeout,
        "emergency_last_state": emergency_last_state,
    }


def run_b0_task(task: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.time()
    route_row = task["route_row"]
    route_edges = route_row["route_edges"].split()
    validation_failures = S14.validate_route_edges(Path(task["net"]), route_edges)
    run_dir = Path(task["run_dir"])
    vehicle_id = f"emergency_{task['route_id']}_{task['mode']}_{task['repeat_id']}"
    task = {**task, "vehicle_id": vehicle_id}
    base = common_row_base(task, run_dir, vehicle_id, {}, 0)
    if validation_failures:
        base.update(
            {
                "sumo_exit_code": "",
                "final_status": "FAIL",
                "failure_reason": ";".join(validation_failures[:10]),
                "warning_reason": "",
                "emergency_departed": False,
                "emergency_arrived": False,
                "emergency_teleport": False,
                "route_error_count": len(validation_failures),
                "signal_event_count": 0,
            }
        )
        return base, []
    emergency_route_xml = run_dir / f"{vehicle_id}.rou.xml"
    S14.write_emergency_route_xml(emergency_route_xml, route_row, vehicle_id, float(task["emergency_depart"]))
    if float(task["emergency_depart"]) > 0:
        set_congested_emergency_departure(emergency_route_xml)
    task = configure_dynamic_emergency_departure(task, emergency_route_xml)
    args = SimpleNamespace(
        net=Path(task["net"]),
        background_route=Path(task["background_route"]),
        run_dir=run_dir,
        time_to_teleport=int(task["time_to_teleport"]),
        collision_action=task["collision_action"],
    )
    paths = write_sumo_files_for_task(
        args,
        emergency_route_xml,
        bool(task.get("include_background", True)),
        tls_all_off=task["mode"] == "B00",
    )
    corridor_tls_ids = load_corridor_tls_ids(Path(task["priority_terminals"]))
    tls_plan = load_tls_plan_for_route(Path(task["net"]), Path(task["tls_audit"]), task["route_id"], route_edges, corridor_tls_ids)
    events, controller_started, observations = run_traci_experiment(task, paths, tls_plan, route_edges, {}, False)
    row, events = summarize_run(task, run_dir, vehicle_id, paths, 0 if controller_started else 1, events, {}, started, route_edges, observations)
    row["controller_started"] = controller_started
    events_csv = run_dir / "signal_events.csv"
    write_csv(events_csv, events, EVENT_FIELDS)
    row["signal_events_csv"] = rel(events_csv)
    return row, events


def run_control_task(task: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.time()
    route_row = task["route_row"]
    route_edges = route_row["route_edges"].split()
    validation_failures = S14.validate_route_edges(Path(task["net"]), route_edges)
    run_dir = Path(task["run_dir"])
    vehicle_id = f"emergency_{task['route_id']}_{task['mode']}_{task['parameter_id']}_{task['repeat_id']}"
    task = {**task, "vehicle_id": vehicle_id}
    params = dict(task["params"])
    base = common_row_base(task, run_dir, vehicle_id, params, 0)
    effective_alpha, alpha_error = integer_seconds(params.get("alpha"), "alpha")
    effective_g_ext, g_ext_error = integer_seconds(params.get("G_ext"), "G_ext")
    effective_t_change, t_change_error = integer_seconds(params.get("T_change_sec"), "T_change_sec")
    if alpha_error or g_ext_error or t_change_error:
        base.update(
            {
                "sumo_exit_code": "",
                "final_status": "FAIL",
                "failure_reason": ";".join(error for error in [alpha_error, g_ext_error, t_change_error] if error),
                "warning_reason": "",
                "emergency_departed": False,
                "emergency_arrived": False,
                "emergency_teleport": False,
                "route_error_count": 0,
                "signal_event_count": 0,
                "safety_violation_count": 1,
            }
        )
        return base, []
    params["_effective_alpha_sec"] = effective_alpha
    params["_effective_G_ext_sec"] = effective_g_ext
    params["_effective_T_change_sec"] = effective_t_change
    base = common_row_base(task, run_dir, vehicle_id, params, 0)
    if validation_failures:
        base.update(
            {
                "sumo_exit_code": "",
                "final_status": "FAIL",
                "failure_reason": ";".join(validation_failures[:10]),
                "warning_reason": "",
                "emergency_departed": False,
                "emergency_arrived": False,
                "emergency_teleport": False,
                "route_error_count": len(validation_failures),
                "signal_event_count": 0,
            }
        )
        return base, []
    corridor_tls_ids = load_corridor_tls_ids(Path(task["priority_terminals"]))
    tls_plan = load_tls_plan_for_route(Path(task["net"]), Path(task["tls_audit"]), task["route_id"], route_edges, corridor_tls_ids)
    if not tls_plan:
        raise ExperimentError(f"no_corridor_tls_rows: {task['route_id']}")
    emergency_route_xml = run_dir / f"{vehicle_id}.rou.xml"
    S14.write_emergency_route_xml(emergency_route_xml, route_row, vehicle_id, float(task["emergency_depart"]))
    if float(task["emergency_depart"]) > 0:
        set_congested_emergency_departure(emergency_route_xml)
    task = configure_dynamic_emergency_departure(task, emergency_route_xml)
    args = SimpleNamespace(
        net=Path(task["net"]),
        background_route=Path(task["background_route"]),
        run_dir=run_dir,
        time_to_teleport=int(task["time_to_teleport"]),
        collision_action=task["collision_action"],
        emergency_vehicle_id=vehicle_id,
        route_id=task["route_id"],
        timeout_steps=int(task["timeout_steps"]),
    )
    paths = write_sumo_files_for_task(args, emergency_route_xml, bool(task.get("include_background", True)))
    events, controller_started, observations = run_traci_experiment(task, paths, tls_plan, route_edges, params, True)
    row, events = summarize_run(task, run_dir, vehicle_id, paths, 0 if controller_started else 1, events, params, started, route_edges, observations)
    row["controller_started"] = controller_started
    events_csv = run_dir / "signal_events.csv"
    write_csv(events_csv, events, EVENT_FIELDS)
    row["signal_events_csv"] = rel(events_csv)
    return row, events


def summarize_run(
    task: dict[str, Any],
    run_dir: Path,
    vehicle_id: str,
    paths: dict[str, Path],
    returncode: int,
    events: list[dict[str, Any]],
    params: dict[str, Any],
    started: float,
    route_edges: list[str],
    observations: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    background_count = int(task["background_vehicle_count"])
    stderr_text = read_text_with_retry(paths["stderr"])
    summary_steps = parse_summary_steps(paths["summary"])
    summary_metrics = parse_summary_output(paths["summary"])
    trip = S14.parse_tripinfo(paths["tripinfo"], vehicle_id)
    route_errors = route_error_count(stderr_text)
    emergency_tp = emergency_teleport_lines(stderr_text, vehicle_id)
    emergency_stop_warning_count = emergency_warning_count(stderr_text, vehicle_id, ("emergency stop", "emergency braking", "junction collision"))
    stderr_lane_warning_count = emergency_warning_count(stderr_text, vehicle_id, ("no connection", "is not connected"))
    detected_lane_warning_count = int(observations.get("lane_connection_warning_count") or 0)
    emergency_lane_connection_warning_count = stderr_lane_warning_count
    emergency_arrived = bool(trip["emergency_arrived"])
    emergency_departed = bool(observations.get("emergency_seen")) or summary_metrics["departed_count_total"] > background_count or emergency_arrived
    emergency_teleport = bool(emergency_tp)
    background_departed = max(summary_metrics["departed_count_total"] - (1 if emergency_departed else 0), 0)
    background_arrived = max(summary_metrics["arrived_count_total"] - (1 if emergency_arrived else 0), 0)
    background_teleported = max(summary_metrics["teleport_count"] - (1 if emergency_teleport else 0), 0)
    remaining_vehicle_count = int(summary_metrics["running_count"]) + int(summary_metrics["waiting_count"])
    background_remaining_count = max(remaining_vehicle_count - (0 if emergency_arrived else 1), 0)
    timeout_reached = float(summary_metrics["sim_end_time"]) >= float(task["timeout_steps"]) and remaining_vehicle_count > 0
    all_vehicles_arrived = remaining_vehicle_count == 0 and summary_metrics["departed_count_total"] == summary_metrics["arrived_count_total"]
    controlled_tls = {event["tls_id"] for event in events if event.get("action") in CONTROL_ACTIONS and event.get("tls_id")}
    skipped_tls = {event["tls_id"] for event in events if event.get("action") == "skip" and event.get("tls_id")}
    failed_tls = {event["tls_id"] for event in events if event.get("action") == "failed" and event.get("tls_id")}
    failures = []
    warnings = []
    if returncode != 0:
        failures.append(f"sumo_exit_code_{returncode}")
    if not emergency_departed:
        failures.append("emergency_not_departed")
    if not emergency_arrived:
        failures.append("emergency_not_arrived")
    if emergency_teleport:
        failures.append("emergency_teleport_detected")
    if emergency_stop_warning_count > 0:
        failures.append("emergency_stop_warning_detected")
    if emergency_lane_connection_warning_count > 0:
        failures.append("emergency_lane_connection_warning_detected")
    if route_errors > 0:
        failures.append("route_error_count_gt_0")
    if observations.get("wall_timeout"):
        failures.append("timeout_sec_exceeded")
    if background_teleported > 0:
        warnings.append("background_teleports_present")
    if timeout_reached and not emergency_arrived:
        warnings.append("timeout_reached_before_emergency_arrival")
    corridor_edges = load_corridor_edge_ids(Path(task["corridor_edges"]))
    corridor_route_edges = [edge_id for edge_id in route_edges if edge_id in corridor_edges]
    if not corridor_route_edges:
        corridor_route_edges = route_edges
    edge_time = observations.get("edge_time", {})
    emergency_corridor_actual = sum(float(edge_time.get(edge_id, 0.0)) for edge_id in corridor_route_edges)
    if emergency_corridor_actual <= 0 and trip["emergency_travel_time"] not in {"", None}:
        emergency_corridor_actual = float(trip["emergency_travel_time"])
    emergency_corridor_free = route_free_flow_seconds(Path(task["net"]), route_edges, set(corridor_route_edges))
    general_delay = summarize_general_non_main_delay(observations.get("general_non_main_records", []))
    excluded_non_main_count = int(observations.get("excluded_active_general_non_main_count") or 0)
    completed_non_main_count = int(general_delay["N_delay_completed_vehicle_edge_count"])
    censored_non_main_count = int(general_delay["N_delay_censored_vehicle_edge_count"])
    non_main_total_count = completed_non_main_count + censored_non_main_count + excluded_non_main_count
    excluded_non_main_ratio = (excluded_non_main_count / non_main_total_count) if non_main_total_count else 0.0
    censored_non_main_ratio = (censored_non_main_count / non_main_total_count) if non_main_total_count else 0.0
    if task["mode"] in {"B0", "B2"}:
        recovery = queue_recovery_summary(
            observations.get("queue_history_by_tls", {}),
            observations.get("recovery_pass_time_by_tls", {}),
            observations.get("tls_plan", []),
            float(task["emergency_depart"]),
        )
        queue_recovery_rows = queue_recovery_detail_rows(
            observations.get("queue_history_by_tls", {}),
            observations.get("recovery_pass_time_by_tls", {}),
            observations.get("tls_plan", []),
            float(task["emergency_depart"]),
        )
    else:
        recovery = {
            "T_recovery_sec": 0.0,
            "T_recovery_tls_count": 0,
            "T_recovery_max_tls_id": "",
            "T_recovery_unrecovered_count": 0,
        }
        queue_recovery_rows = []
    queue_recovery_csv = run_dir / "queue_recovery_by_tls.csv"
    write_csv(
        queue_recovery_csv,
        queue_recovery_rows,
        ["tls_id", "junction_id", "passed_time_sec", "baseline_queue", "recovered", "recovered_time_sec", "recovery_sec", "max_queue_after_pass"],
    )
    t_recovery = float(recovery["T_recovery_sec"])
    score = SCORE_WEIGHT_N * float(general_delay["N_delay_sec"]) + SCORE_WEIGHT_RECOVERY * t_recovery
    safety_violation_count = sum(1 for event in events if event.get("action") == "safety_violation")
    if safety_violation_count > 0:
        failures.append("safety_violation_detected")
    emergency_route_length = route_length_meters(Path(task["net"]), route_edges)
    emergency_travel_time = trip["emergency_travel_time"]
    emergency_avg_speed = (emergency_route_length / float(emergency_travel_time) * 3.6) if emergency_travel_time not in {"", None} and float(emergency_travel_time) > 0 else ""
    emergency_last_state = observations.get("emergency_last_state", {}) or {}
    network_avg_speed = float(summary_metrics["network_avg_speed_kmh"])
    network_avg_speed_at_analysis_end = float(summary_metrics["network_avg_speed_at_analysis_end_kmh"])
    network_running_at_analysis_end = int(summary_metrics["network_running_at_analysis_end"])
    analysis_end_time = float(observations.get("analysis_end_time", summary_metrics["sim_end_time"]) or summary_metrics["sim_end_time"])
    emergency_depart = float(task["emergency_depart"])
    recovery_buffer_sec = float(task.get("recovery_buffer_sec", DEFAULT_RECOVERY_BUFFER_SEC) or 0)
    emergency_arrival_time = trip.get("emergency_arrival_time")
    during_end_time = float(emergency_arrival_time) if emergency_arrival_time not in {"", None} else analysis_end_time
    recovery_anchor = observations.get("latest_queue_recovery_time") or observations.get("emergency_left_time") or during_end_time
    recovery_anchor = float(recovery_anchor) if recovery_anchor not in {"", None} else during_end_time
    if background_count > 0:
        pre_congestion = summary_window_stats(summary_steps, max(0.0, emergency_depart - ROLLING_CONGESTION_WINDOW_SEC), emergency_depart)
        during_congestion = summary_window_stats(summary_steps, emergency_depart, max(during_end_time, emergency_depart))
        post_congestion = summary_window_stats(summary_steps, recovery_anchor, min(recovery_anchor + recovery_buffer_sec, analysis_end_time))
        rolling_congestion = rolling_congestion_stats(summary_steps, max(0.0, emergency_depart - ROLLING_CONGESTION_WINDOW_SEC), analysis_end_time)
    else:
        pre_congestion = {"speed_kmh": "", "active_count": "", "sample_count": 0}
        during_congestion = {"speed_kmh": "", "active_count": "", "sample_count": 0}
        post_congestion = {"speed_kmh": "", "active_count": "", "sample_count": 0}
        rolling_congestion = {
            "rolling_congestion_valid": False,
            "rolling_congestion_reason": "not_applicable_no_background",
            "rolling_congestion_min_kmh": "",
            "rolling_congestion_max_kmh": "",
        }
    if background_count > 0:
        congestion_valid, congestion_reason = congestion_diagnostic(network_avg_speed)
        congestion_valid_at_analysis_end, congestion_reason_at_analysis_end = analysis_end_congestion_diagnostic(
            network_avg_speed_at_analysis_end,
            network_running_at_analysis_end,
        )
        if not rolling_congestion["rolling_congestion_valid"]:
            warnings.append(f"congestion_not_maintained:{rolling_congestion['rolling_congestion_reason']}")
    else:
        congestion_valid, congestion_reason = False, "not_applicable_no_background"
        congestion_valid_at_analysis_end, congestion_reason_at_analysis_end = False, "not_applicable_no_background"
    if failures:
        final_status = "FAIL"
    elif background_teleported > 0:
        final_status = "WARNING"
    elif warnings:
        final_status = "WARNING"
    elif background_remaining_count > 0:
        final_status = "PASS_WITH_REMAINING_BACKGROUND"
    else:
        final_status = "PASS"
    row = common_row_base(task, run_dir, vehicle_id, params, time.time() - started)
    row.update(
        {
            "sumo_exit_code": returncode,
            "final_status": final_status,
            "failure_reason": ";".join(failures),
            "warning_reason": ";".join(warnings),
            "emergency_departed": emergency_departed,
            "emergency_arrived": emergency_arrived,
            "emergency_teleport": emergency_teleport,
            "emergency_travel_time": sec(trip["emergency_travel_time"]),
            "emergency_travel_time_sec": sec(trip["emergency_travel_time"]),
            "b00_emergency_travel_time_sec": "",
            "A_delay_sec": "",
            "N_delay_sec": sec(general_delay["N_delay_sec"]),
            "T_recovery_sec": sec(t_recovery),
            "score_sec": sec(score),
            "emergency_corridor_actual_sec": sec(emergency_corridor_actual),
            "emergency_corridor_free_flow_sec": sec(emergency_corridor_free),
            "general_non_main_actual_sec": sec(general_delay["general_non_main_actual_sec"]),
            "general_non_main_free_flow_sec": sec(general_delay["general_non_main_free_flow_sec"]),
            "general_non_main_vehicle_edge_count": general_delay["general_non_main_vehicle_edge_count"],
            "N_delay_completed_vehicle_edge_count": completed_non_main_count,
            "N_delay_censored_vehicle_edge_count": censored_non_main_count,
            "N_delay_excluded_active_vehicle_edge_count": excluded_non_main_count,
            "N_delay_excluded_ratio": round(excluded_non_main_ratio, 6),
            "N_delay_censored_ratio": round(censored_non_main_ratio, 6),
            "route_error_count": route_errors,
            "background_departed": background_departed,
            "background_arrived": background_arrived,
            "background_teleported": background_teleported,
            "background_teleport_ratio": round(background_teleported / background_departed, 6) if background_departed else 0.0,
            "timeout_reached": timeout_reached,
            "remaining_vehicle_count": remaining_vehicle_count,
            "background_remaining_count": background_remaining_count,
            "background_remaining_at_sim_end": background_remaining_count,
            "all_vehicles_arrived": all_vehicles_arrived,
            "network_avg_speed_kmh": round(network_avg_speed, 6),
            "network_avg_speed_at_analysis_end_kmh": round(network_avg_speed_at_analysis_end, 6),
            "network_running_at_analysis_end": network_running_at_analysis_end,
            "network_speed_pre_emergency_kmh": sec(pre_congestion["speed_kmh"]),
            "network_speed_during_response_kmh": sec(during_congestion["speed_kmh"]),
            "network_speed_post_recovery_kmh": sec(post_congestion["speed_kmh"]),
            "active_vehicle_count_pre_emergency": sec(pre_congestion["active_count"]),
            "active_vehicle_count_during_response": sec(during_congestion["active_count"]),
            "active_vehicle_count_post_recovery": sec(post_congestion["active_count"]),
            "rolling_congestion_valid": rolling_congestion["rolling_congestion_valid"],
            "rolling_congestion_reason": rolling_congestion["rolling_congestion_reason"],
            "rolling_congestion_window_sec": ROLLING_CONGESTION_WINDOW_SEC,
            "rolling_congestion_min_kmh": sec(rolling_congestion["rolling_congestion_min_kmh"]),
            "rolling_congestion_max_kmh": sec(rolling_congestion["rolling_congestion_max_kmh"]),
            "congestion_valid": congestion_valid,
            "congestion_reason": congestion_reason,
            "congestion_valid_at_analysis_end": congestion_valid_at_analysis_end,
            "congestion_reason_at_analysis_end": congestion_reason_at_analysis_end,
            "analysis_end_time_sec": sec(analysis_end_time),
            "analysis_stop_reason": observations.get("analysis_stop_reason", ""),
            "recovery_buffer_sec": sec(task.get("recovery_buffer_sec", DEFAULT_RECOVERY_BUFFER_SEC)),
            "emergency_route_length_m": sec(emergency_route_length),
            "emergency_route_length_source": "fixed_external_edges",
            "emergency_avg_speed_kmh": sec(emergency_avg_speed),
            "emergency_speed_factor": sec(EMERGENCY_SPEED_FACTOR),
            "emergency_speed_cap_kmh": sec(EMERGENCY_SPEED_CAP_KMH),
            "emergency_bluelight_enabled": EMERGENCY_BLUELIGHT_ENABLED,
            "b00_speed_policy": B00_SPEED_POLICY,
            "sim_end_time": sec(summary_metrics["sim_end_time"]),
            "controlled_tls_count": len(controlled_tls),
            "skipped_tls_count": len(skipped_tls),
            "failed_tls_count": len(failed_tls),
            "intervention_count": sum(1 for event in events if event.get("action") in CONTROL_ACTIONS),
            "green_extension_count": sum(1 for event in events if event.get("action") == "extend_green"),
            "green_arrived_before_t_change_extension_count": sum(
                1
                for event in events
                if event.get("action") == "extend_green" and event.get("reason") == "green_arrived_before_t_change_then_extended"
            ),
            "phase_switch_count": sum(1 for event in events if event.get("action") == "switch_to_green_after_t_change"),
            "restore_count": sum(1 for event in events if event.get("action") == "restore"),
            "signal_event_count": len(events),
            "t_change_request_count": sum(1 for event in events if event.get("action") == "request_green"),
            "t_change_switch_count": sum(1 for event in events if event.get("action") == "switch_to_green_after_t_change"),
            "green_missed_before_t_change_count": sum(1 for event in events if event.get("action") == "green_missed_before_t_change"),
            "T_recovery_tls_count": recovery["T_recovery_tls_count"],
            "T_recovery_max_tls_id": recovery["T_recovery_max_tls_id"],
            "T_recovery_unrecovered_count": recovery["T_recovery_unrecovered_count"],
            "queue_recovery_csv": rel(queue_recovery_csv),
            "safety_violation_count": safety_violation_count,
            "emergency_stop_warning_count": emergency_stop_warning_count,
            "emergency_lane_connection_warning_count": emergency_lane_connection_warning_count,
            "emergency_lane_connection_diagnostic_count": detected_lane_warning_count,
            "signal_events_csv": "",
            "sumocfg": rel(paths["sumocfg"]),
            "tripinfo": rel(paths["tripinfo"]),
            "summary_output": rel(paths["summary"]),
            "edgeData_output": rel(paths["edge_data"]),
            "stderr_log": rel(paths["stderr"]),
            "emergency_last_edge_id": emergency_last_state.get("edge_id", ""),
            "emergency_last_route_index": emergency_last_state.get("route_index", ""),
            "emergency_last_lane_id": emergency_last_state.get("lane_id", ""),
            "emergency_last_speed_kmh": sec(emergency_last_state.get("speed_kmh", "")),
            "emergency_last_waiting_time_sec": sec(emergency_last_state.get("waiting_time_sec", "")),
            "emergency_route_progress_ratio": sec(emergency_last_state.get("progress_ratio", "")),
        }
    )
    for event in events:
        event.update(
            {
                "output_prefix": task["output_prefix"],
                "mode": task["mode"],
                "parameter_id": task["parameter_id"],
                "repeat_id": task["repeat_id"],
            }
        )
        event.setdefault("T_change_sec", sec(params.get("_effective_T_change_sec", params.get("T_change_sec", ""))))
    return row, events


def run_task(task: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if task["mode"] in {"B00", "B0"}:
        return run_b0_task(task)
    return run_control_task(task)


def row_float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def apply_b00_delay_fields(result_rows: list[dict[str, Any]]) -> None:
    b00_by_key: dict[tuple[str, str], float] = {}
    for row in result_rows:
        if row.get("mode") != "B00":
            continue
        travel_time = row_float(row, "emergency_travel_time_sec")
        if travel_time is not None:
            b00_by_key[(row["route_id"], row["repeat_id"])] = travel_time
    for row in result_rows:
        row.setdefault("b00_emergency_travel_time_sec", "")
        base = b00_by_key.get((row.get("route_id", ""), row.get("repeat_id", "")))
        current = row_float(row, "emergency_travel_time_sec")
        n_delay = row_float(row, "N_delay_sec")
        t_recovery = row_float(row, "T_recovery_sec")
        if row.get("mode") == "B00":
            if current is not None:
                row["b00_emergency_travel_time_sec"] = sec(current)
            row["A_delay_sec"] = sec(0.0)
        elif base is not None and current is not None:
            row["b00_emergency_travel_time_sec"] = sec(base)
            row["A_delay_sec"] = sec(current - base)
        a_delay = row_float(row, "A_delay_sec")
        if a_delay is not None and n_delay is not None and t_recovery is not None:
            row["score_sec"] = sec(SCORE_WEIGHT_A * a_delay + SCORE_WEIGHT_N * n_delay + SCORE_WEIGHT_RECOVERY * t_recovery)


def compare_rows(result_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    b0_by_key: dict[tuple[str, str], float] = {}
    for row in result_rows:
        if row.get("mode") == "B0" and row.get("emergency_travel_time") not in ("", None):
            b0_by_key[(row["route_id"], row["repeat_id"])] = float(row["emergency_travel_time"])
    rows = []
    for row in result_rows:
        if row.get("mode") in {"B00", "B0"}:
            continue
        base = b0_by_key.get((row["route_id"], row["repeat_id"]))
        current = row.get("emergency_travel_time")
        if base is None or current in ("", None):
            delta = ""
            improvement = ""
        else:
            delta = sec(float(current) - base)
            improvement = round(((base - float(current)) / base) * 100.0, 6) if base else ""
        rows.append(
            {
                "output_prefix": row.get("output_prefix", ""),
                "route_id": row.get("route_id", ""),
                "repeat_id": row.get("repeat_id", ""),
                "mode": row.get("mode", ""),
                "parameter_id": row.get("parameter_id", ""),
                "b0_emergency_travel_time": base if base is not None else "",
                "mode_emergency_travel_time": current,
                "travel_time_delta_sec": delta,
                "travel_time_improvement_pct": improvement,
                "mode_final_status": row.get("final_status", ""),
                "emergency_teleport": row.get("emergency_teleport", ""),
                "route_error_count": row.get("route_error_count", ""),
                "background_teleport_ratio": row.get("background_teleport_ratio", ""),
                "A_delay_sec": row.get("A_delay_sec", ""),
                "N_delay_sec": row.get("N_delay_sec", ""),
                "T_recovery_sec": row.get("T_recovery_sec", ""),
                "score_sec": row.get("score_sec", ""),
                "intervention_count": row.get("intervention_count", ""),
                "skipped_tls_count": row.get("skipped_tls_count", ""),
            }
        )
    return rows


def add_compare_fields(result_rows: list[dict[str, Any]]) -> None:
    b0_by_key: dict[tuple[str, str], float] = {}
    for row in result_rows:
        row.setdefault("b0_emergency_travel_time", "")
        row.setdefault("travel_time_delta_sec", "")
        row.setdefault("travel_time_improvement_pct", "")
        if row.get("mode") == "B0" and row.get("emergency_travel_time") not in ("", None):
            b0_by_key[(row["route_id"], row["repeat_id"])] = float(row["emergency_travel_time"])
    for row in result_rows:
        if row.get("mode") in {"B00", "B0"}:
            continue
        base = b0_by_key.get((row["route_id"], row["repeat_id"]))
        current = row.get("emergency_travel_time")
        if base is None or current in ("", None):
            continue
        current_value = float(current)
        row["b0_emergency_travel_time"] = base
        row["travel_time_delta_sec"] = sec(current_value - base)
        row["travel_time_improvement_pct"] = round(((base - current_value) / base) * 100.0, 6) if base else ""


def fill_missing_result_fields(result_rows: list[dict[str, Any]]) -> None:
    for row in result_rows:
        for field in EXPERIMENT_RESULT_FIELDS:
            row.setdefault(field, "")
        row.setdefault("background_teleport_ratio", "")


def score_component_rows(result_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in result_rows:
        rows.append({field: row.get(field, "") for field in SCORE_COMPONENT_FIELDS})
    return rows


def result_score_rows(result_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in result_rows:
        rows.append({field: row.get(field, "") for field in RESULT_SCORE_FIELDS})
    return rows


def main() -> int:
    args = parse_args()
    generated_at = utc_now()
    run_id = run_id_from_generated_at(generated_at)
    lines = ["B00/B0/B2 experiment runner", "=======================", f"generated_at: {generated_at}", f"run_id: {run_id}"]
    try:
        manifest = apply_manifest(args)
        if args.pipeline and args.output_prefix is None:
            args.output_prefix = args.pipeline
        if args.output_prefix is None:
            raise ExperimentError("output_prefix is required unless --pipeline is provided")
        for attr in ["net", "background_route", "tls_audit", "priority_terminals", "corridor_edges", "b2_params"]:
            path = getattr(args, attr).resolve()
            setattr(args, attr, path)
            if not path.is_file():
                raise ExperimentError(f"missing_file: {path}")
        if args.repeats < 1:
            raise ExperimentError("repeats must be >= 1")
        if args.workers < 1:
            raise ExperimentError("workers must be >= 1")
        if args.output_prefix.strip() == "experiment" and not args.legacy_output_names:
            raise ExperimentError("reserved_output_prefix: experiment is only allowed with --legacy-output-names")
        if manifest:
            required_substring = str(manifest.get("final_background_required_substring") or "warm0p15_sustain0p05_seed002_sustained_3600")
        else:
            required_substring = "warm0p15_sustain0p05_seed002_sustained_3600"
        if required_substring and required_substring not in args.background_route.name and not args.allow_nonfinal_background:
            raise ExperimentError(
                "nonfinal_background_blocked:"
                f"{rel(args.background_route)} does not contain '{required_substring}'. "
                "Use --allow-nonfinal-background only for explicit diagnostics."
            )
        routes = {SEOUL_STATION_ROUTE_ID: synthetic_seoul_station_route(args.net)}
        route_ids = [SEOUL_STATION_ROUTE_ID]
        b2_params = load_b2_parameter_sets(args.b2_params) if "B2" in args.modes else []
        background_vehicle_count = S14.count_vehicles(args.background_route)
        paths = output_paths(args.output_prefix, args.legacy_output_names, args.pipeline, run_id)
        base_task = {
            "generated_at": generated_at,
            "run_id": run_id,
            "pipeline": args.pipeline or "",
            "net": str(args.net),
            "background_route": str(args.background_route),
            "tls_audit": str(args.tls_audit),
            "priority_terminals": str(args.priority_terminals),
            "corridor_edges": str(args.corridor_edges),
            "background_vehicle_count": background_vehicle_count,
            "time_to_teleport": args.time_to_teleport,
            "collision_action": args.collision_action,
            "emergency_depart": args.emergency_depart,
            "timeout_steps": args.timeout_steps,
            "timeout_sec": args.timeout_sec,
            "recovery_buffer_sec": args.recovery_buffer_sec,
            "output_prefix": args.output_prefix,
        }
        tasks = []
        for task in build_tasks(args, route_ids, b2_params):
            run_dir = args.run_root / args.output_prefix / run_id / task["mode"] / task["parameter_id"] / task["repeat_id"] / task["route_id"]
            task_background_count = background_vehicle_count if task.get("include_background", True) else 0
            tasks.append({**base_task, **task, "background_vehicle_count": task_background_count, "route_row": routes[task["route_id"]], "run_dir": str(run_dir)})
        lines.extend(
            [
                f"route_count: {len(route_ids)}",
                f"routes: {' '.join(route_ids)}",
                f"modes: {' '.join(args.modes)}",
                f"repeats: {args.repeats}",
                f"workers: {args.workers}",
                f"task_count: {len(tasks)}",
                f"background_vehicle_count: {background_vehicle_count}",
            ]
        )
        result_rows: list[dict[str, Any]] = []
        event_rows: list[dict[str, Any]] = []
        if args.workers == 1:
            for task in tasks:
                row, events = run_task(task)
                result_rows.append(row)
                event_rows.extend(events)
        else:
            with ProcessPoolExecutor(max_workers=args.workers) as executor:
                future_map = {executor.submit(run_task, task): task for task in tasks}
                for future in as_completed(future_map):
                    task = future_map[future]
                    try:
                        row, events = future.result()
                    except Exception as exc:  # noqa: BLE001
                        row = {
                            "generated_at": generated_at,
                            "run_id": run_id,
                            "timeout_steps": args.timeout_steps,
                            "command_time_to_teleport": args.time_to_teleport,
                            "recovery_buffer_sec": sec(args.recovery_buffer_sec),
                            "pipeline": args.pipeline or "",
                            "output_prefix": args.output_prefix,
                            "mode": task["mode"],
                            "parameter_id": task["parameter_id"],
                            "repeat_id": task["repeat_id"],
                            "route_id": task["route_id"],
                            "final_status": "FAIL",
                            "failure_reason": f"worker_exception:{type(exc).__name__}:{exc}",
                            "warning_reason": "",
                            "sumo_exit_code": "",
                            "emergency_departed": False,
                            "emergency_arrived": False,
                            "emergency_teleport": False,
                            "route_error_count": "",
                            "run_dir": rel(Path(task["run_dir"])),
                        }
                        events = []
                    result_rows.append(row)
                    event_rows.extend(events)
        result_rows.sort(key=lambda r: (r.get("repeat_id", ""), r.get("route_id", ""), r.get("mode", ""), r.get("parameter_id", "")))
        event_rows.sort(key=lambda r: (r.get("repeat_id", ""), r.get("route_id", ""), r.get("mode", ""), r.get("parameter_id", ""), float(r.get("time") or 0)))
        fill_missing_result_fields(result_rows)
        apply_b00_delay_fields(result_rows)
        add_compare_fields(result_rows)
        compare = compare_rows(result_rows)
        status_counts: dict[str, int] = {}
        for row in result_rows:
            status_counts[row["final_status"]] = status_counts.get(row["final_status"], 0) + 1
        manifest_path = rel(args.manifest) if args.manifest else ""
        emergency_teleport_any = any(bool(row.get("emergency_teleport")) for row in result_rows)
        route_error_count_any = any(int(row.get("route_error_count") or 0) > 0 for row in result_rows)
        summary = {
            "generated_at": generated_at,
            "run_id": run_id,
            "output_prefix": args.output_prefix,
            "manifest": manifest_path,
            "manifest_path": manifest_path,
            "manifest_schema": manifest.get("schema", "") if manifest else "",
            "git_commit": current_git_commit(),
            "active_net": rel(args.net),
            "background_route": rel(args.background_route),
            "priority_terminals": rel(args.priority_terminals),
            "corridor_edges": rel(args.corridor_edges),
            "background_vehicle_count": background_vehicle_count,
            "route_set": args.route_set,
            "route_ids": route_ids,
            "modes": args.modes,
            "b2_parameter_ids": [row["parameter_id"] for row in b2_params],
            "repeats": args.repeats,
            "workers": args.workers,
            "timeout_steps": args.timeout_steps,
            "recovery_buffer_sec": args.recovery_buffer_sec,
            "command_time_to_teleport": args.time_to_teleport,
            "task_count": len(tasks),
            "teleport_policy": manifest.get("teleport_policy", {"emergency": "FAIL", "background": "WARNING"}) if manifest else {"emergency": "FAIL", "background": "WARNING"},
            "allow_nonfinal_background": args.allow_nonfinal_background,
            "emergency_teleport_any": emergency_teleport_any,
            "route_error_count_any": route_error_count_any,
            "status_counts": status_counts,
            "final_status": "FAIL" if status_counts.get("FAIL") else "WARNING" if status_counts.get("WARNING") else "PASS",
            "outputs": [rel(paths["results_csv"]), rel(paths["result_score_csv"]), rel(paths["summary_json"]), rel(paths["latest_json"]), rel(LOG_PATH)]
            if not args.legacy_output_names and paths.get("summary_json") is not None
            else [rel(paths["results_csv"]), rel(paths["score_components_csv"]), rel(paths["result_score_csv"]), rel(LOG_PATH)]
            if not args.legacy_output_names
            else [rel(paths["results_csv"]), rel(paths["summary_json"]), rel(paths["events_csv"]), rel(paths["compare_csv"]), rel(LOG_PATH)],
        }
        write_csv(paths["results_csv"], result_rows, EXPERIMENT_RESULT_FIELDS)
        if not args.legacy_output_names:
            write_csv(paths["score_components_csv"], score_component_rows(result_rows), SCORE_COMPONENT_FIELDS)
            write_csv(paths["result_score_csv"], result_score_rows(result_rows), RESULT_SCORE_FIELDS)
        if args.legacy_output_names:
            write_csv(paths["events_csv"], event_rows)
            write_csv(paths["compare_csv"], compare)
        if paths.get("summary_json") is not None:
            write_json(paths["summary_json"], {**summary, "results": result_rows, "compare": compare, "signal_events": event_rows})
        if paths.get("latest_json") is not None:
            write_json(
                paths["latest_json"],
                {
                    "generated_at": generated_at,
                    "run_id": run_id,
                    "output_prefix": args.output_prefix,
                    "final_status": summary["final_status"],
                    "results_csv": rel(paths["results_csv"]),
                    "score_components_csv": rel(paths["score_components_csv"]),
                    "result_score_csv": rel(paths["result_score_csv"]),
                    "summary_json": rel(paths["summary_json"]),
                },
            )
        lines.extend(
            [
                f"status_counts: {status_counts}",
                f"final_status: {summary['final_status']}",
                f"results_csv: {rel(paths['results_csv'])}",
            ]
        )
        if not args.legacy_output_names:
            lines.append(f"score_components_csv: {rel(paths['score_components_csv'])}")
            lines.append(f"result_score_csv: {rel(paths['result_score_csv'])}")
        if paths.get("summary_json") is not None:
            lines.append(f"summary_json: {rel(paths['summary_json'])}")
        if paths.get("latest_json") is not None:
            lines.append(f"latest_json: {rel(paths['latest_json'])}")
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if summary["final_status"] in {"PASS", "WARNING"} else 1
    except (ExperimentError, OSError, ValueError, ET.ParseError) as exc:
        lines.extend(["final_status: FAIL", f"blocker: {exc}"])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
