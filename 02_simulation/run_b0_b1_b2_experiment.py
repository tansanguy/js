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
STEP14_PATH = PROJECT_ROOT / "01_prepare/08_signal/step14_b1_green_wave_v1_er_acc_002.py"

DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
DEFAULT_BACKGROUND_ROUTE = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml"
DEFAULT_EMERGENCY_ROUTES = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
DEFAULT_TLS_AUDIT = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_spine_v2.csv"
DEFAULT_PRIORITY_TERMINALS = PROJECT_ROOT / "data_prepared/signals/priority_terminal_candidates.csv"
DEFAULT_B2_PARAMS = PROJECT_ROOT / "configs/b2_parameter_sets.csv"
DEFAULT_MANIFEST = PROJECT_ROOT / "configs/final_experiment_manifest.json"
DEFAULT_B0_SUMMARY = PROJECT_ROOT / "results/metrics/b0_baseline_19route_smoke_summary.csv"
DEFAULT_CORRIDOR_EDGES = PROJECT_ROOT / "data_prepared/routes/corridor_spine_edges.csv"

LOG_PATH = PROJECT_ROOT / "outputs/logs/b00_b0_b2_experiment.log"

DEFAULT_TIMEOUT_STEPS = 7200
DEFAULT_TIMEOUT_SEC = 1200
FREE_FLOW_SPEED_CAP_KMH = 50.0
FREE_FLOW_SPEED_CAP_MPS = FREE_FLOW_SPEED_CAP_KMH / 3.6
T_CHANGE_SEC = 30
CLEARANCE_BEFORE_GREEN_SEC = 3
SCORE_WEIGHT_A = 3.0
SCORE_WEIGHT_N = 1.0
SCORE_WEIGHT_RECOVERY = 1.0
B00_SPEED_POLICY = "existing_emergency_vtype_speedFactor_1p30"
SEOUL_STATION_ROUTE_ID = "FIRE_TO_SEOUL_STATION"
SEOUL_STATION_START_EDGE = "-381802881#2"
SEOUL_STATION_TARGET_EDGE = "438360331#2"
CONTROL_ACTIONS = {"extend_green", "alpha_hold_extend", "switch_to_green_after_t_change"}
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
    "D_det",
    "alpha",
    "G_ext",
    "T_change_sec",
    "w1",
    "w2",
    "w3",
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
    "route_error_count",
    "background_departed",
    "background_arrived",
    "background_teleported",
    "background_teleport_ratio",
    "timeout_reached",
    "remaining_vehicle_count",
    "background_remaining_count",
    "all_vehicles_arrived",
    "network_avg_speed_kmh",
    "emergency_route_length_m",
    "emergency_avg_speed_kmh",
    "b00_speed_policy",
    "controlled_tls_count",
    "skipped_tls_count",
    "failed_tls_count",
    "intervention_count",
    "green_extension_count",
    "phase_switch_count",
    "restore_count",
    "signal_event_count",
    "t_change_request_count",
    "t_change_switch_count",
    "green_missed_before_t_change_count",
    "T_recovery_tls_count",
    "T_recovery_max_tls_id",
    "T_recovery_unrecovered_count",
    "safety_violation_count",
    "emergency_stop_warning_count",
    "emergency_lane_connection_warning_count",
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


def default_workers() -> int:
    cpus = os.cpu_count() or 2
    return max(1, min(cpus - 2, 8))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run B00/B0/B2 experiment batches.")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--emergency-routes", type=Path, default=DEFAULT_EMERGENCY_ROUTES)
    parser.add_argument("--tls-audit", type=Path, default=DEFAULT_TLS_AUDIT)
    parser.add_argument("--priority-terminals", type=Path, default=DEFAULT_PRIORITY_TERMINALS)
    parser.add_argument("--corridor-edges", type=Path, default=DEFAULT_CORRIDOR_EDGES)
    parser.add_argument("--b2-params", type=Path, default=DEFAULT_B2_PARAMS)
    parser.add_argument("--b0-summary", type=Path, default=DEFAULT_B0_SUMMARY)
    parser.add_argument("--pipeline", choices=["parameter_input_sim", "final_effect_validation_sim"], default=None)
    parser.add_argument("--modes", nargs="+", choices=["B00", "B0", "B2"], default=["B00", "B0", "B2"])
    parser.add_argument("--route-set", choices=["b0_valid_18", "all_19", "seoul_station"], default=None)
    parser.add_argument("--routes", nargs="*", default=[])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--time-to-teleport", type=int, default=1200)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--emergency-depart", type=float, default=0.0)
    parser.add_argument("--timeout-steps", type=int, default=DEFAULT_TIMEOUT_STEPS)
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
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
    args.net = project_path(manifest.get("active_net"), args.net)
    args.background_route = project_path(manifest.get("background_route"), args.background_route)
    args.emergency_routes = project_path(manifest.get("emergency_routes"), args.emergency_routes)
    args.tls_audit = project_path(manifest.get("tls_audit"), args.tls_audit)
    args.priority_terminals = project_path(manifest.get("priority_terminals"), args.priority_terminals)
    args.corridor_edges = project_path(manifest.get("corridor_edges"), args.corridor_edges)
    args.b2_params = project_path(manifest.get("b2_parameter_sets"), args.b2_params)
    args.b0_summary = project_path(manifest.get("b0_summary"), args.b0_summary)
    if not args.routes and args.route_set is None and args.pipeline != "parameter_input_sim" and manifest.get("route_set"):
        args.route_set = str(manifest["route_set"])
    return manifest


def output_paths(output_prefix: str, legacy: bool, pipeline: str | None = None) -> dict[str, Path | None]:
    if pipeline:
        return {
            "results_csv": PROJECT_ROOT / f"results/metrics/{pipeline}.csv",
            "summary_json": None,
        }
    if legacy:
        return {
            "results_csv": PROJECT_ROOT / "results/metrics/experiment_b0_b2_summary.csv",
            "summary_json": PROJECT_ROOT / "results/metrics/experiment_b0_b2_summary.json",
            "events_csv": PROJECT_ROOT / "results/metrics/experiment_signal_events.csv",
            "compare_csv": PROJECT_ROOT / "results/metrics/experiment_compare_by_route.csv",
        }
    safe_prefix = output_prefix.strip()
    if not safe_prefix:
        raise ExperimentError("output_prefix cannot be blank")
    return {
        "results_csv": PROJECT_ROOT / f"results/metrics/{safe_prefix}_experiment_results.csv",
        "summary_json": PROJECT_ROOT / f"results/metrics/{safe_prefix}_experiment_summary.json",
    }


def write_sumo_files_for_task(args: argparse.Namespace, emergency_route_xml: Path, include_background: bool) -> dict[str, Path]:
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
    report_elem = ET.SubElement(config, "report")
    ET.SubElement(report_elem, "no-step-log", {"value": "true"})
    ET.SubElement(report_elem, "duration-log.disable", {"value": "true"})
    ET.ElementTree(config).write(paths["sumocfg"], encoding="utf-8", xml_declaration=True)
    return paths


def current_git_commit() -> str:
    try:
        completed = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def load_routes(path: Path) -> dict[str, dict[str, str]]:
    return {row["route_id"]: row for row in read_csv(path) if row.get("route_id")}


def synthetic_seoul_station_route(net_path: Path) -> dict[str, str]:
    sumo_net = S14.read_sumo_net(str(net_path))
    try:
        start = sumo_net.getEdge(SEOUL_STATION_START_EDGE)
        target = sumo_net.getEdge(SEOUL_STATION_TARGET_EDGE)
    except KeyError as exc:
        raise ExperimentError(f"missing_seoul_station_route_endpoint: {exc}") from exc
    edges, _cost = sumo_net.getOptimalPath(start, target, vClass="passenger", withInternal=False, includeFromToCost=True)
    if not edges:
        raise ExperimentError(f"no_route_to_seoul_station: {SEOUL_STATION_START_EDGE}->{SEOUL_STATION_TARGET_EDGE}")
    route_edges = [edge.getID() for edge in edges]
    route_length = sum(float(edge.getLength()) for edge in edges)
    return {
        "route_id": SEOUL_STATION_ROUTE_ID,
        "scenario_id": "SEOUL_STATION",
        "target_edge_id": SEOUL_STATION_TARGET_EDGE,
        "selected_policy": "shortest_to_seoul_station",
        "route_edges": " ".join(route_edges),
        "route_length_m": f"{route_length:.2f}",
        "route_edge_count": str(len(route_edges)),
        "route_tls_count": "",
    }


def load_b0_valid_routes(path: Path) -> list[str]:
    rows = read_csv(path)
    route_ids = []
    for row in rows:
        if (
            row.get("sumo_exit_code") == "0"
            and row.get("emergency_departed") == "True"
            and row.get("emergency_arrived") == "True"
            and row.get("emergency_teleport") == "False"
            and str(row.get("route_error_count", "0")) == "0"
        ):
            route_ids.append(row["route_id"])
    return route_ids


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
        result.append(
            {
                "parameter_id": parameter_id,
                "D_det": float(row["D_det"]),
                "alpha": float(row["alpha"]),
                "G_ext": float(row["G_ext"]),
                "metric_sample_interval": int(float(row.get("metric_sample_interval") or 10)),
                "phase_control_policy": row.get("phase_control_policy") or "distance_trigger_no_eta",
                "yellow_clearance_policy": row.get("yellow_clearance_policy") or "wait_clearance_then_switch",
                "pedestrian_min_walk_policy": row.get("pedestrian_min_walk_policy") or "safety_placeholder_documented_not_optimized",
            }
        )
    if not result:
        raise ExperimentError(f"empty_b2_parameter_file: {path}")
    return result


def parse_summary_output(path: Path) -> dict[str, Any]:
    root = S14.parse_xml_with_retry(path).getroot()
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
        raise ExperimentError(f"summary-output has no steps: {path}")
    mean_speed_mps = speed_num / speed_den if speed_den else float(last_step.get("meanSpeed", "0") or 0)
    return {
        "departed_count_total": int(float(last_step.get("inserted", "0"))),
        "arrived_count_total": int(float(last_step.get("arrived", "0"))),
        "running_count": int(float(last_step.get("running", "0"))),
        "waiting_count": int(float(last_step.get("waiting", "0"))),
        "teleport_count": max_teleports,
        "sim_end_time": float(last_step.get("time", "0")),
        "network_avg_speed_kmh": mean_speed_mps * 3.6,
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


def summarize_general_non_main_delay(records: list[dict[str, float]]) -> dict[str, Any]:
    if not records:
        return {
            "N_delay_sec": 0.0,
            "general_non_main_actual_sec": 0.0,
            "general_non_main_free_flow_sec": 0.0,
            "general_non_main_vehicle_edge_count": 0,
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
    return {
        "N_delay_sec": total_delay / count,
        "general_non_main_actual_sec": total_actual / count,
        "general_non_main_free_flow_sec": total_free / count,
        "general_non_main_vehicle_edge_count": count,
    }


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


def route_length_meters(net_path: Path, edge_ids: list[str]) -> float:
    sumo_net = S14.read_sumo_net(str(net_path))
    return sum(float(sumo_net.getEdge(edge_id).getLength()) for edge_id in edge_ids)


def queue_recovery_summary(
    queue_history_by_tls: dict[str, list[tuple[float, int]]],
    pass_time_by_tls: dict[str, float],
    tls_plan: list[dict[str, Any]],
    emergency_depart: float,
) -> dict[str, Any]:
    recovery_values: list[tuple[str, float]] = []
    unrecovered = 0
    for tls in tls_plan:
        tls_id = tls["tls_id"]
        history = queue_history_by_tls.get(tls_id, [])
        pass_time = pass_time_by_tls.get(tls_id)
        if pass_time is None:
            continue
        recovery = queue_recovery_seconds(history, pass_time, emergency_depart)
        if history and recovery > max(history[-1][0] - pass_time, 0.0) - 1e-9:
            baseline_candidates = [queue for time_value, queue in history if time_value <= emergency_depart]
            baseline_queue = baseline_candidates[-1] if baseline_candidates else history[0][1]
            if history[-1][1] > baseline_queue:
                unrecovered += 1
        recovery_values.append((tls_id, recovery))
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
    return {
        "generated_at": task.get("generated_at", ""),
        "run_id": task.get("run_id", ""),
        "timeout_steps": task.get("timeout_steps", ""),
        "command_time_to_teleport": task.get("time_to_teleport", ""),
        "pipeline": task.get("pipeline", ""),
        "output_prefix": task["output_prefix"],
        "mode": task["mode"],
        "parameter_id": task["parameter_id"],
        "repeat_id": task["repeat_id"],
        "route_id": task["route_id"],
        "emergency_vehicle_id": vehicle_id,
        "run_dir": rel(run_dir),
        "elapsed_wall_sec": sec(elapsed_sec),
        "background_vehicle_count": int(task.get("background_vehicle_count", 0) or 0),
        "D_det": params.get("D_det", ""),
        "alpha": params.get("alpha", ""),
        "G_ext": params.get("G_ext", ""),
        "T_change_sec": sec(T_CHANGE_SEC) if task["mode"] == "B2" else "",
        "w1": sec(SCORE_WEIGHT_A),
        "w2": sec(SCORE_WEIGHT_N),
        "w3": sec(SCORE_WEIGHT_RECOVERY),
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
    metric_sample_interval = max(1, int(float(params.get("metric_sample_interval") or 10)))
    edge_starts = S14.route_edge_starts(Path(task["net"]), route_edges)
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
    edge_time: dict[str, float] = {}
    general_non_main_records: list[dict[str, float]] = []
    active_general_non_main: dict[str, tuple[str, float]] = {}
    queue_history_by_tls: dict[str, list[tuple[float, int]]] = {tls_id: [] for tls_id in recovery_queue_edges_by_tls}
    recovery_pass_time_by_tls: dict[str, float] = {}
    tls_recovery_times: list[float] = []
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
                queue_history_by_tls.setdefault(tls_id, []).append((0.0, queue))
            last_metric_sample = -metric_sample_interval
            while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() <= int(task["timeout_steps"]):
                if time.time() - started_wall > int(task["timeout_sec"]):
                    wall_timeout = True
                    break
                traci.simulationStep()
                sim_time = float(traci.simulation.getTime())
                vehicle_ids = set(traci.vehicle.getIDList())
                for vehicle_id, (edge_id, entered_at) in list(active_general_non_main.items()):
                    if vehicle_id not in vehicle_ids:
                        general_non_main_records.append(
                            {
                                "actual_sec": max(sim_time - entered_at, 0.0),
                                "free_flow_sec": non_main_free_flow.get(edge_id, 0.0),
                            }
                        )
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
                            general_non_main_records.append(
                                {
                                    "actual_sec": max(sim_time - active[1], 0.0),
                                    "free_flow_sec": non_main_free_flow.get(active[0], 0.0),
                                }
                            )
                            active_general_non_main[vehicle_id] = (road, sim_time)
                    elif active is not None:
                        general_non_main_records.append(
                            {
                                "actual_sec": max(sim_time - active[1], 0.0),
                                "free_flow_sec": non_main_free_flow.get(active[0], 0.0),
                            }
                        )
                        active_general_non_main.pop(vehicle_id, None)
                vehicle_present = args.emergency_vehicle_id in vehicle_ids
                current_distance = 0.0
                road_id = ""
                edge_ids = set(traci.edge.getIDList())
                for tls_id, recovery_queue_edges in recovery_queue_edges_by_tls.items():
                    queue = sum(int(traci.edge.getLastStepHaltingNumber(edge_id)) for edge_id in recovery_queue_edges if edge_id in edge_ids)
                    queue_history_by_tls.setdefault(tls_id, []).append((sim_time, queue))
                if vehicle_present:
                    road_id = traci.vehicle.getRoadID(args.emergency_vehicle_id)
                    if road_id and not road_id.startswith(":"):
                        edge_time[road_id] = edge_time.get(road_id, 0.0) + 1.0
                    route_index = int(traci.vehicle.getRouteIndex(args.emergency_vehicle_id))
                    lane_position = float(traci.vehicle.getLanePosition(args.emergency_vehicle_id))
                    current_distance = edge_starts[route_index] + lane_position if 0 <= route_index < len(edge_starts) else 0.0
                    if 0 <= route_index < len(route_edges) - 1 and road_id and not road_id.startswith(":"):
                        lane_id = traci.vehicle.getLaneID(args.emergency_vehicle_id)
                        next_edge_id = route_edges[route_index + 1]
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
                    if sim_time < request_time + T_CHANGE_SEC:
                        if not pending.get("wait_logged"):
                            pending["wait_logged"] = True
                            events.append(
                                {
                                    **pending["event_base"],
                                    "time": sec(sim_time),
                                    "phase_before": current_phase,
                                    "phase_after": current_phase,
                                    "action": "wait_t_change",
                                    "reason": f"waiting_until_t_change_{sec(T_CHANGE_SEC)}s",
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
            final_time = float(traci.simulation.getTime())
            for vehicle_id, (edge_id, entered_at) in list(active_general_non_main.items()):
                general_non_main_records.append(
                    {
                        "actual_sec": max(final_time - entered_at, 0.0),
                        "free_flow_sec": non_main_free_flow.get(edge_id, 0.0),
                    }
                )
                active_general_non_main.pop(vehicle_id, None)
        finally:
            traci.close(False)
    return events, controller_started, {
        "edge_time": edge_time,
        "queue_history_by_tls": queue_history_by_tls,
        "recovery_pass_time_by_tls": recovery_pass_time_by_tls,
        "recovery_queue_edges_by_tls": recovery_queue_edges_by_tls,
        "tls_plan": tls_plan,
        "general_non_main_records": general_non_main_records,
        "tls_recovery_times": tls_recovery_times,
        "lane_connection_warning_count": len(lane_connection_warnings),
        "wall_timeout": wall_timeout,
    }


def run_b0_task(task: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.time()
    routes = load_routes(Path(task["emergency_routes"]))
    route_row = task.get("route_row") or routes[task["route_id"]]
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
    args = SimpleNamespace(
        net=Path(task["net"]),
        background_route=Path(task["background_route"]),
        run_dir=run_dir,
        time_to_teleport=int(task["time_to_teleport"]),
        collision_action=task["collision_action"],
    )
    paths = write_sumo_files_for_task(args, emergency_route_xml, bool(task.get("include_background", True)))
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
    routes = load_routes(Path(task["emergency_routes"]))
    route_row = task.get("route_row") or routes[task["route_id"]]
    route_edges = route_row["route_edges"].split()
    validation_failures = S14.validate_route_edges(Path(task["net"]), route_edges)
    run_dir = Path(task["run_dir"])
    vehicle_id = f"emergency_{task['route_id']}_{task['mode']}_{task['parameter_id']}_{task['repeat_id']}"
    task = {**task, "vehicle_id": vehicle_id}
    params = dict(task["params"])
    base = common_row_base(task, run_dir, vehicle_id, params, 0)
    effective_alpha, alpha_error = integer_seconds(params.get("alpha"), "alpha")
    effective_g_ext, g_ext_error = integer_seconds(params.get("G_ext"), "G_ext")
    if alpha_error or g_ext_error:
        base.update(
            {
                "sumo_exit_code": "",
                "final_status": "FAIL",
                "failure_reason": ";".join(error for error in [alpha_error, g_ext_error] if error),
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
    stderr_text = paths["stderr"].read_text(encoding="utf-8", errors="replace") if paths["stderr"].is_file() else ""
    summary_metrics = parse_summary_output(paths["summary"])
    trip = S14.parse_tripinfo(paths["tripinfo"], vehicle_id)
    route_errors = route_error_count(stderr_text)
    emergency_tp = emergency_teleport_lines(stderr_text, vehicle_id)
    emergency_stop_warning_count = emergency_warning_count(stderr_text, vehicle_id, ("emergency stop", "emergency braking"))
    stderr_lane_warning_count = emergency_warning_count(stderr_text, vehicle_id, ("no connection", "is not connected"))
    detected_lane_warning_count = int(observations.get("lane_connection_warning_count") or 0)
    emergency_lane_connection_warning_count = stderr_lane_warning_count + detected_lane_warning_count
    emergency_arrived = bool(trip["emergency_arrived"])
    emergency_departed = summary_metrics["departed_count_total"] > background_count or emergency_arrived
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
    if timeout_reached:
        warnings.append("timeout_reached_with_running_vehicles")
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
    if task["mode"] == "B2":
        recovery = queue_recovery_summary(
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
    t_recovery = float(recovery["T_recovery_sec"])
    score = SCORE_WEIGHT_N * float(general_delay["N_delay_sec"]) + SCORE_WEIGHT_RECOVERY * t_recovery
    safety_violation_count = sum(1 for event in events if event.get("action") == "safety_violation")
    if safety_violation_count > 0:
        failures.append("safety_violation_detected")
    emergency_route_length = tripinfo_float_attr(paths["tripinfo"], vehicle_id, "routeLength")
    if emergency_route_length is None:
        emergency_route_length = route_length_meters(Path(task["net"]), route_edges)
    emergency_travel_time = trip["emergency_travel_time"]
    emergency_avg_speed = (emergency_route_length / float(emergency_travel_time) * 3.6) if emergency_travel_time not in {"", None} and float(emergency_travel_time) > 0 else ""
    row = common_row_base(task, run_dir, vehicle_id, params, time.time() - started)
    row.update(
        {
            "sumo_exit_code": returncode,
            "final_status": "FAIL" if failures else "WARNING" if warnings else "PASS",
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
            "route_error_count": route_errors,
            "background_departed": background_departed,
            "background_arrived": background_arrived,
            "background_teleported": background_teleported,
            "background_teleport_ratio": round(background_teleported / background_departed, 6) if background_departed else 0.0,
            "timeout_reached": timeout_reached,
            "remaining_vehicle_count": remaining_vehicle_count,
            "background_remaining_count": background_remaining_count,
            "all_vehicles_arrived": all_vehicles_arrived,
            "network_avg_speed_kmh": round(float(summary_metrics["network_avg_speed_kmh"]), 6),
            "emergency_route_length_m": sec(emergency_route_length),
            "emergency_avg_speed_kmh": sec(emergency_avg_speed),
            "b00_speed_policy": B00_SPEED_POLICY,
            "sim_end_time": sec(summary_metrics["sim_end_time"]),
            "controlled_tls_count": len(controlled_tls),
            "skipped_tls_count": len(skipped_tls),
            "failed_tls_count": len(failed_tls),
            "intervention_count": sum(1 for event in events if event.get("action") in CONTROL_ACTIONS),
            "green_extension_count": sum(1 for event in events if event.get("action") == "extend_green"),
            "phase_switch_count": sum(1 for event in events if event.get("action") == "switch_to_green_after_t_change"),
            "restore_count": sum(1 for event in events if event.get("action") == "restore"),
            "signal_event_count": len(events),
            "t_change_request_count": sum(1 for event in events if event.get("action") == "request_green"),
            "t_change_switch_count": sum(1 for event in events if event.get("action") == "switch_to_green_after_t_change"),
            "green_missed_before_t_change_count": sum(1 for event in events if event.get("action") == "green_missed_before_t_change"),
            "T_recovery_tls_count": recovery["T_recovery_tls_count"],
            "T_recovery_max_tls_id": recovery["T_recovery_max_tls_id"],
            "T_recovery_unrecovered_count": recovery["T_recovery_unrecovered_count"],
            "safety_violation_count": safety_violation_count,
            "emergency_stop_warning_count": emergency_stop_warning_count,
            "emergency_lane_connection_warning_count": emergency_lane_connection_warning_count,
            "signal_events_csv": "",
            "sumocfg": rel(paths["sumocfg"]),
            "tripinfo": rel(paths["tripinfo"]),
            "summary_output": rel(paths["summary"]),
            "edgeData_output": rel(paths["edge_data"]),
            "stderr_log": rel(paths["stderr"]),
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
        for attr in ["net", "background_route", "emergency_routes", "tls_audit", "priority_terminals", "corridor_edges", "b2_params", "b0_summary"]:
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
            required_substring = str(manifest.get("final_background_required_substring") or "scale_0p15")
        else:
            required_substring = "scale_0p15"
        if required_substring and required_substring not in args.background_route.name and not args.allow_nonfinal_background:
            raise ExperimentError(
                "nonfinal_background_blocked:"
                f"{rel(args.background_route)} does not contain '{required_substring}'. "
                "Use --allow-nonfinal-background only for explicit diagnostics."
            )
        if args.route_set is None:
            args.route_set = "seoul_station" if args.pipeline == "parameter_input_sim" else "b0_valid_18"
        routes = load_routes(args.emergency_routes)
        routes[SEOUL_STATION_ROUTE_ID] = synthetic_seoul_station_route(args.net)
        if args.routes:
            route_ids = args.routes
        elif args.route_set == "seoul_station":
            route_ids = [SEOUL_STATION_ROUTE_ID]
        elif args.route_set == "b0_valid_18":
            route_ids = load_b0_valid_routes(args.b0_summary)
        else:
            route_ids = sorted(route_id for route_id in routes if route_id != SEOUL_STATION_ROUTE_ID)
        missing_routes = [route_id for route_id in route_ids if route_id not in routes]
        if missing_routes:
            raise ExperimentError(f"missing_routes: {','.join(missing_routes)}")
        excluded_routes = set(manifest.get("excluded_routes", ["ER_ACC_013"]) if manifest else ["ER_ACC_013"])
        forbidden = sorted(excluded_routes & set(route_ids))
        if args.route_set == "b0_valid_18" and forbidden:
            raise ExperimentError(f"excluded_routes_present_in_route_set: {','.join(forbidden)}")
        b2_params = load_b2_parameter_sets(args.b2_params) if "B2" in args.modes else []
        background_vehicle_count = S14.count_vehicles(args.background_route)
        paths = output_paths(args.output_prefix, args.legacy_output_names, args.pipeline)
        base_task = {
            "generated_at": generated_at,
            "run_id": run_id,
            "pipeline": args.pipeline or "",
            "net": str(args.net),
            "background_route": str(args.background_route),
            "emergency_routes": str(args.emergency_routes),
            "tls_audit": str(args.tls_audit),
            "priority_terminals": str(args.priority_terminals),
            "corridor_edges": str(args.corridor_edges),
            "background_vehicle_count": background_vehicle_count,
            "time_to_teleport": args.time_to_teleport,
            "collision_action": args.collision_action,
            "emergency_depart": args.emergency_depart,
            "timeout_steps": args.timeout_steps,
            "timeout_sec": args.timeout_sec,
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
            "excluded_routes": sorted(excluded_routes),
            "modes": args.modes,
            "b2_parameter_ids": [row["parameter_id"] for row in b2_params],
            "repeats": args.repeats,
            "workers": args.workers,
            "timeout_steps": args.timeout_steps,
            "command_time_to_teleport": args.time_to_teleport,
            "task_count": len(tasks),
            "teleport_policy": manifest.get("teleport_policy", {"emergency": "FAIL", "background": "WARNING"}) if manifest else {"emergency": "FAIL", "background": "WARNING"},
            "allow_nonfinal_background": args.allow_nonfinal_background,
            "emergency_teleport_any": emergency_teleport_any,
            "route_error_count_any": route_error_count_any,
            "status_counts": status_counts,
            "final_status": "FAIL" if status_counts.get("FAIL") else "WARNING" if status_counts.get("WARNING") else "PASS",
            "outputs": [rel(paths["results_csv"]), rel(paths["summary_json"]), rel(LOG_PATH)]
            if not args.legacy_output_names and paths.get("summary_json") is not None
            else [rel(paths["results_csv"]), rel(LOG_PATH)]
            if not args.legacy_output_names
            else [rel(paths["results_csv"]), rel(paths["summary_json"]), rel(paths["events_csv"]), rel(paths["compare_csv"]), rel(LOG_PATH)],
        }
        write_csv(paths["results_csv"], result_rows, EXPERIMENT_RESULT_FIELDS)
        if args.legacy_output_names:
            write_csv(paths["events_csv"], event_rows)
            write_csv(paths["compare_csv"], compare)
        if paths.get("summary_json") is not None:
            write_json(paths["summary_json"], {**summary, "results": result_rows, "compare": compare, "signal_events": event_rows})
        lines.extend(
            [
                f"status_counts: {status_counts}",
                f"final_status: {summary['final_status']}",
                f"results_csv: {rel(paths['results_csv'])}",
            ]
        )
        if paths.get("summary_json") is not None:
            lines.append(f"summary_json: {rel(paths['summary_json'])}")
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
