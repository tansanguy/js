#!/usr/bin/env python3
"""Independent B00/B0/B2 theta validation runner for b0_valid_18 routes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import read_sumo_net  # noqa: E402


DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml"
DEFAULT_BACKGROUND_ROUTE = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19_warm0p15_sustain0p05_seed002_sustained_3600.rou.xml"
DEFAULT_ROUTES_CSV = PROJECT_ROOT / "05_theta_check_simulation/routes/b0_valid_18_routes.csv"
DEFAULT_TLS_AUDIT = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_spine_v2.csv"
DEFAULT_PRIORITY_TERMINALS = PROJECT_ROOT / "data_prepared/signals/priority_terminal_candidates.csv"
DEFAULT_CORRIDOR_EDGES = PROJECT_ROOT / "data_prepared/routes/corridor_spine_edges.csv"
DEFAULT_B2_PARAMS = PROJECT_ROOT / "configs/b2_parameter_sets.csv"
FINAL_OPTIMUM_B2_PARAMS = PROJECT_ROOT / "05_theta_check_simulation/final_optimum_b2_parameter_sets.csv"
DEFAULT_OUTPUT_PREFIX = "parameter_sim"
DEFAULT_SEED = 20260531
DEFAULT_DEPART_MIN = 550.0
DEFAULT_DEPART_MAX = 650.0
DEFAULT_TIMEOUT_STEPS = 7200
DEFAULT_TIMEOUT_SEC = 7200
DEFAULT_RECOVERY_BUFFER_SEC = 300
RECOVERY_PRE_BASELINE_START_SEC = 120.0
RECOVERY_PRE_BASELINE_END_SEC = 30.0
RECOVERY_POST_PEAK_WINDOW_SEC = 300.0
RECOVERY_STABLE_WINDOW_SEC = 30.0
RECOVERY_POST_PEAK_FRACTION = 0.2
RECOVERY_BASELINE_MARGIN_QUEUE = 1.0
PREFERRED_CONGESTION_MIN_KMH = 15.0
EXCLUDED_ROUTE = "ER_ACC_013"
RUN_NAMESPACE = "05_theta_check_simulation"
COMPLETED_STATUSES = {"PASS", "WARNING", "FAIL"}
SCORE_WEIGHT_A = 3.0
SCORE_WEIGHT_N = 1.0
SCORE_WEIGHT_RECOVERY = 1.0
FREE_FLOW_SPEED_CAP_KMH = 50.0
FREE_FLOW_SPEED_CAP_MPS = FREE_FLOW_SPEED_CAP_KMH / 3.6
EMERGENCY_SPEED_FACTOR = 1.4
EMERGENCY_SPEED_CAP_KMH = 70.0
EMERGENCY_BLUELIGHT_ENABLED = False


RESULT_FIELDS = [
    "generated_at",
    "run_id",
    "task_id",
    "mode",
    "parameter_id",
    "repeat_id",
    "route_id",
    "route_target_edge",
    "emergency_depart",
    "D_det",
    "alpha",
    "G_ext",
    "T_change_sec",
    "final_status",
    "failure_reason",
    "warning_reason",
    "sumo_exit_code",
    "emergency_departed",
    "emergency_arrived",
    "emergency_teleport",
    "emergency_travel_time_sec",
    "b00_emergency_travel_time_sec",
    "A_delay_sec",
    "N_delay_sec",
    "T_recovery_sec",
    "T_recovery_queue_sec",
    "T_recovery_speed_penalty_sec",
    "score_sec",
    "B2_vs_B0_travel_time_delta_sec",
    "B2_vs_B0_pct",
    "background_departed",
    "background_arrived",
    "background_teleported",
    "background_teleport_ratio",
    "route_error_count",
    "network_avg_speed_kmh",
    "general_vehicle_avg_speed_kmh",
    "general_vehicle_speed_sample_count",
    "sim_end_time",
    "intervention_count",
    "t_change_switch_count",
    "green_extension_count",
    "post_pass_trim_count",
    "realized_extension_sec",
    "trimmed_green_sec",
    "signal_event_count",
    "run_dir",
    "sumocfg",
    "tripinfo",
    "summary_output",
    "edgeData_output",
    "signal_events_csv",
    "task_status_json",
]

ROUTE_SUMMARY_FIELDS = [
    "route_id",
    "repeat_id",
    "emergency_depart",
    "B00_travel_time_sec",
    "B0_travel_time_sec",
    "B2_best_parameter_id",
    "B2_best_travel_time_sec",
    "B2_vs_B0_travel_time_delta_sec",
    "B2_vs_B0_pct",
    "B2_improved",
    "B2_worsened",
    "status",
]

EVENT_FIELDS = [
    "time",
    "mode",
    "parameter_id",
    "repeat_id",
    "route_id",
    "vehicle_id",
    "tls_id",
    "junction_id",
    "incoming",
    "outgoing",
    "action",
    "reason",
    "distance_to_tls_m",
    "phase_before",
    "phase_after",
    "phase_remaining_before_sec",
    "set_duration_sec",
    "extension_delta_sec",
    "trimmed_green_sec",
]


class ParameterSimError(RuntimeError):
    """Expected theta-check simulation failure."""


def configure_runtime_environment() -> None:
    sumo_bin = shutil.which("sumo")
    if sumo_bin:
        candidate = Path(sumo_bin).resolve().parent.parent
        sumo_home = candidate / "share/sumo"
        if (sumo_home / "data/xsd").is_dir():
            os.environ["SUMO_HOME"] = str(sumo_home)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_id_from_generated_at(generated_at: str) -> str:
    return generated_at.replace(":", "").replace("-", "").replace("+", "Z").replace(".", "_")


def default_workers() -> int:
    cpus = os.cpu_count() or 2
    return max(1, min(cpus - 2, 8))


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def sec(value: Any) -> str:
    if value in {"", None}:
        return ""
    return f"{float(value):.2f}"


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


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
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run independent B00/B0/B2 theta check simulations.")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--routes-csv", type=Path, default=DEFAULT_ROUTES_CSV)
    parser.add_argument("--tls-audit", type=Path, default=DEFAULT_TLS_AUDIT)
    parser.add_argument("--priority-terminals", type=Path, default=DEFAULT_PRIORITY_TERMINALS)
    parser.add_argument("--corridor-edges", type=Path, default=DEFAULT_CORRIDOR_EDGES)
    parser.add_argument("--b2-params", type=Path, default=DEFAULT_B2_PARAMS)
    parser.add_argument("--modes", nargs="+", choices=["B00", "B0", "B2"], default=["B00", "B0", "B2"])
    parser.add_argument("--routes", nargs="+", default=None, help="Optional route_id subset.")
    parser.add_argument("--exclude-routes", nargs="*", default=[EXCLUDED_ROUTE])
    parser.add_argument("--depart-min", type=float, default=DEFAULT_DEPART_MIN)
    parser.add_argument("--depart-max", type=float, default=DEFAULT_DEPART_MAX)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--b00-repeats", type=int, default=None, help="Override repeat count for B00 tasks.")
    parser.add_argument("--b0-repeats", type=int, default=None, help="Override repeat count for B0 tasks.")
    parser.add_argument("--b2-repeats", type=int, default=None, help="Override repeat count for B2 tasks.")
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--run-root", type=Path, default=PROJECT_ROOT / "runs" / RUN_NAMESPACE)
    parser.add_argument("--results-root", type=Path, default=PROJECT_ROOT / "results/metrics" / RUN_NAMESPACE)
    parser.add_argument("--time-to-teleport", type=int, default=1200)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--timeout-steps", type=int, default=DEFAULT_TIMEOUT_STEPS)
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--recovery-buffer-sec", type=int, default=DEFAULT_RECOVERY_BUFFER_SEC)
    parser.add_argument("--allow-nonfinal-background", action="store_true")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    for attr in ["net", "background_route", "routes_csv", "tls_audit", "priority_terminals", "corridor_edges"]:
        path = getattr(args, attr).resolve()
        setattr(args, attr, path)
        if not path.is_file():
            raise ParameterSimError(f"missing_file:{path}")
    args.b2_params = args.b2_params.resolve()
    if "B2" in args.modes and not args.b2_params.is_file():
        raise ParameterSimError(f"missing_file:{args.b2_params}")
    args.run_root = args.run_root.resolve()
    args.results_root = args.results_root.resolve()
    if args.repeats < 1:
        raise ParameterSimError("repeats must be >= 1")
    for attr in ["b00_repeats", "b0_repeats", "b2_repeats"]:
        value = getattr(args, attr)
        if value is not None and value < 1:
            raise ParameterSimError(f"{attr.replace('_', '-')} must be >= 1")
    if args.workers < 1:
        raise ParameterSimError("workers must be >= 1")
    if args.depart_min > args.depart_max:
        raise ParameterSimError("depart-min must be <= depart-max")
    required = "warm0p15_sustain0p05_seed002_sustained_3600"
    if required not in args.background_route.name and not args.allow_nonfinal_background:
        raise ParameterSimError(f"nonfinal_background_blocked:{rel(args.background_route)}")
    if shutil.which("sumo") is None:
        raise ParameterSimError("missing_executable:sumo")


def load_routes(path: Path, include_routes: list[str] | None, exclude_routes: list[str]) -> list[dict[str, str]]:
    rows = read_csv(path)
    if not rows:
        raise ParameterSimError(f"empty_routes_csv:{path}")
    by_id = {row["route_id"]: row for row in rows if row.get("route_id")}
    selected_ids = include_routes if include_routes else [row["route_id"] for row in rows if row.get("route_id")]
    missing = [route_id for route_id in selected_ids if route_id not in by_id]
    if missing:
        raise ParameterSimError(f"route_id_not_found:{','.join(missing)}")
    excluded = set(exclude_routes or [])
    selected = [by_id[route_id] for route_id in selected_ids if route_id not in excluded]
    if not include_routes and EXCLUDED_ROUTE in excluded and len(selected) != 18:
        raise ParameterSimError(f"expected_b0_valid_18_routes_got:{len(selected)}")
    if not selected:
        raise ParameterSimError("selected_route_set_empty")
    return selected


def deterministic_departure(seed: int, route_id: str, repeat_id: str, depart_min: float, depart_max: float) -> float:
    key = f"{seed}:{route_id}:{repeat_id}".encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()
    local_seed = int(digest[:16], 16)
    rng = random.Random(local_seed)
    return round(rng.uniform(depart_min, depart_max), 3)


def load_b2_parameter_sets(path: Path) -> list[dict[str, Any]]:
    rows = read_csv(path)
    if not rows:
        raise ParameterSimError(f"empty_b2_parameter_file:{path}")
    required = ["parameter_id", "D_det", "alpha", "G_ext", "T_change_sec"]
    missing = [key for key in required if key not in rows[0]]
    if missing:
        raise ParameterSimError(f"missing_b2_parameter_columns:{','.join(missing)}")
    params = []
    seen = set()
    for row in rows:
        parameter_id = row.get("parameter_id", "").strip()
        if not parameter_id:
            raise ParameterSimError("blank_b2_parameter_id")
        if parameter_id in seen:
            raise ParameterSimError(f"duplicate_b2_parameter_id:{parameter_id}")
        seen.add(parameter_id)
        params.append(
            {
                "parameter_id": parameter_id,
                "D_det": float(row["D_det"]),
                "alpha": float(row["alpha"]),
                "G_ext": float(row["G_ext"]),
                "T_change_sec": float(row["T_change_sec"]),
            }
        )
    return params


def build_tasks(
    args: argparse.Namespace,
    generated_at: str,
    run_id: str,
    routes: list[dict[str, str]],
    b2_params: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tasks = []
    route_by_id = {row["route_id"]: row for row in routes}
    repeat_counts = {
        "B00": args.b00_repeats or args.repeats,
        "B0": args.b0_repeats or args.repeats,
        "B2": args.b2_repeats or args.repeats,
    }
    for route_id, route_row in route_by_id.items():
        max_repeats = max(repeat_counts[mode] for mode in args.modes)
        repeat_bases = {}
        for repeat_idx in range(1, max_repeats + 1):
            repeat_id = f"repeat_{repeat_idx:03d}"
            depart = deterministic_departure(args.seed, route_id, repeat_id, args.depart_min, args.depart_max)
            repeat_bases[repeat_idx] = {
                "generated_at": generated_at,
                "run_id": run_id,
                "route_id": route_id,
                "repeat_id": repeat_id,
                "route_row": route_row,
                "emergency_depart": depart,
                "net": str(args.net),
                "background_route": str(args.background_route),
                "tls_audit": str(args.tls_audit),
                "priority_terminals": str(args.priority_terminals),
                "corridor_edges": str(args.corridor_edges),
                "time_to_teleport": args.time_to_teleport,
                "collision_action": args.collision_action,
                "timeout_steps": args.timeout_steps,
                "timeout_sec": args.timeout_sec,
                "recovery_buffer_sec": args.recovery_buffer_sec,
                "output_prefix": args.output_prefix,
            }
        if "B00" in args.modes:
            for repeat_idx in range(1, repeat_counts["B00"] + 1):
                tasks.append({**repeat_bases[repeat_idx], "mode": "B00", "parameter_id": "freeflow", "params": {}, "include_background": False})
        if "B0" in args.modes:
            for repeat_idx in range(1, repeat_counts["B0"] + 1):
                tasks.append({**repeat_bases[repeat_idx], "mode": "B0", "parameter_id": "no_control", "params": {}, "include_background": True})
        if "B2" in args.modes:
            for repeat_idx in range(1, repeat_counts["B2"] + 1):
                for params in b2_params:
                    tasks.append({**repeat_bases[repeat_idx], "mode": "B2", "parameter_id": params["parameter_id"], "params": params, "include_background": True})
    for task in tasks:
        task_id = task_identifier(task)
        run_dir = args.run_root / args.output_prefix / run_id / task["mode"] / task["parameter_id"] / task["repeat_id"] / task["route_id"]
        task["task_id"] = task_id
        task["run_dir"] = str(run_dir)
        task["task_status_json"] = str(run_dir / "task_status.json")
    return tasks


def task_identifier(task: dict[str, Any]) -> str:
    return "__".join([task["mode"], task["parameter_id"], task["repeat_id"], task["route_id"]])


def latest_path(args: argparse.Namespace) -> Path:
    return args.results_root / args.output_prefix / "latest.json"


def resolve_run_id(args: argparse.Namespace, generated_at: str) -> str:
    if args.run_id:
        return args.run_id
    latest = latest_path(args)
    if args.resume and latest.is_file():
        payload = read_json(latest)
        if payload.get("run_id"):
            return str(payload["run_id"])
    return run_id_from_generated_at(generated_at)


def output_paths(args: argparse.Namespace, run_id: str) -> dict[str, Path]:
    root = args.results_root / args.output_prefix / run_id
    return {
        "root": root,
        "task_manifest": root / "task_manifest.json",
        "results_csv": root / "experiment_results.csv",
        "score_components_csv": root / "score_components.csv",
        "route_summary_csv": root / "route_summary.csv",
        "summary_json": root / "experiment_summary.json",
        "latest_json": args.results_root / args.output_prefix / "latest.json",
    }


def write_task_manifest(path: Path, tasks: list[dict[str, Any]], args: argparse.Namespace) -> None:
    manifest_rows = []
    for task in tasks:
        params = task.get("params") or {}
        manifest_rows.append(
            {
                "task_id": task["task_id"],
                "mode": task["mode"],
                "parameter_id": task["parameter_id"],
                "repeat_id": task["repeat_id"],
                "route_id": task["route_id"],
                "emergency_depart": task["emergency_depart"],
                "D_det": params.get("D_det", ""),
                "alpha": params.get("alpha", ""),
                "G_ext": params.get("G_ext", ""),
                "T_change_sec": params.get("T_change_sec", ""),
                "run_dir": rel(Path(task["run_dir"])),
                "task_status_json": rel(Path(task["task_status_json"])),
            }
        )
    write_json(
        path,
        {
            "schema": "theta_check_task_manifest.v1",
            "run_id": tasks[0]["run_id"] if tasks else "",
            "output_prefix": args.output_prefix,
            "seed": args.seed,
            "depart_min": args.depart_min,
            "depart_max": args.depart_max,
            "repeats": args.repeats,
            "mode_repeats": {
                "B00": args.b00_repeats or args.repeats,
                "B0": args.b0_repeats or args.repeats,
                "B2": args.b2_repeats or args.repeats,
            },
            "modes": args.modes,
            "exclude_routes": args.exclude_routes,
            "task_count": len(tasks),
            "tasks": manifest_rows,
        },
    )


def completed_task_status(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    status = str(payload.get("status") or "")
    row = payload.get("result_row")
    if status in COMPLETED_STATUSES and isinstance(row, dict):
        return payload
    return None


def write_running_status(task: dict[str, Any]) -> None:
    path = Path(task["task_status_json"])
    write_json(
        path,
        {
            "schema": "theta_check_task_status.v1",
            "task_id": task["task_id"],
            "status": "RUNNING",
            "updated_at": utc_now(),
            "run_dir": rel(Path(task["run_dir"])),
        },
    )


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


def route_edge_starts(net_path: Path, edge_ids: list[str]) -> dict[str, float]:
    sumo_net = read_sumo_net(str(net_path))
    starts = {}
    cumulative = 0.0
    for edge_id in edge_ids:
        starts[edge_id] = cumulative
        cumulative += float(sumo_net.getEdge(edge_id).getLength())
    return starts


def edge_free_flow_seconds(sumo_net: Any, edge_id: str) -> float:
    edge = sumo_net.getEdge(edge_id)
    speed = max(min(float(edge.getSpeed()), FREE_FLOW_SPEED_CAP_MPS), 0.01)
    return float(edge.getLength()) / speed


def load_corridor_edge_ids(path: Path) -> set[str]:
    rows = read_csv(path)
    if rows and "is_spine_edge" in rows[0]:
        return {row["edge_id"] for row in rows if row.get("edge_id") and row.get("is_spine_edge") == "True"}
    return {row["edge_id"] for row in rows if row.get("edge_id")}


def load_corridor_tls_ids(path: Path) -> set[str]:
    return {row["tls_id"] for row in read_csv(path) if row.get("tls_id")}


def write_emergency_route_xml(path: Path, route_row: dict[str, str], vehicle_id: str, depart: float, include_vehicle: bool) -> None:
    root = ET.Element("routes")
    vtype = ET.SubElement(
        root,
        "vType",
        {
            "id": "theta_check_emergency_type",
            "vClass": "emergency",
            "guiShape": "emergency",
            "color": "1,0,0",
            "speedFactor": f"{EMERGENCY_SPEED_FACTOR:.2f}",
            "maxSpeed": f"{EMERGENCY_SPEED_CAP_KMH / 3.6:.6f}",
            "speedDev": "0.00",
            "accel": "3.0",
            "decel": "7.5",
            "impatience": "1.0",
            "lcStrategic": "10.0",
            "lcCooperative": "0.0",
            "lcSpeedGain": "5.0",
            "lcKeepRight": "0.0",
            "lcAssertive": "5.0",
        },
    )
    ET.SubElement(vtype, "param", {"key": "has.bluelight.device", "value": "true" if EMERGENCY_BLUELIGHT_ENABLED else "false"})
    ET.SubElement(root, "route", {"id": route_row["route_id"], "edges": route_row["route_edges"]})
    if include_vehicle:
        ET.SubElement(
            root,
            "vehicle",
            {
                "id": vehicle_id,
                "type": "theta_check_emergency_type",
                "route": route_row["route_id"],
                "depart": f"{depart:g}",
                "departLane": "free",
                "departPos": "last",
                "departSpeed": "max",
                "insertionChecks": "none",
            },
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_sumo_files(task: dict[str, Any], emergency_route_xml: Path, tls_all_off: bool) -> dict[str, Path]:
    run_dir = Path(task["run_dir"])
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
            "id": "theta_check_edge_data",
            "file": str(paths["edge_data"]),
            "begin": "0",
            "end": "86400",
            "freq": "86400",
            "excludeEmpty": "false",
        },
    )
    ET.ElementTree(additional).write(paths["additional"], encoding="utf-8", xml_declaration=True)
    route_files = [str(emergency_route_xml)]
    if task.get("include_background"):
        route_files.insert(0, str(task["background_route"]))
    config = ET.Element("configuration")
    input_elem = ET.SubElement(config, "input")
    ET.SubElement(input_elem, "net-file", {"value": str(task["net"])})
    ET.SubElement(input_elem, "route-files", {"value": ",".join(route_files)})
    ET.SubElement(input_elem, "additional-files", {"value": str(paths["additional"])})
    output_elem = ET.SubElement(config, "output")
    ET.SubElement(output_elem, "tripinfo-output", {"value": str(paths["tripinfo"])})
    ET.SubElement(output_elem, "summary-output", {"value": str(paths["summary"])})
    time_elem = ET.SubElement(config, "time")
    ET.SubElement(time_elem, "begin", {"value": "0"})
    processing_elem = ET.SubElement(config, "processing")
    ET.SubElement(processing_elem, "time-to-teleport", {"value": str(task["time_to_teleport"])})
    ET.SubElement(processing_elem, "collision.action", {"value": str(task["collision_action"])})
    ET.SubElement(processing_elem, "emergency-insert", {"value": "true"})
    if tls_all_off:
        ET.SubElement(processing_elem, "tls.all-off", {"value": "true"})
    report_elem = ET.SubElement(config, "report")
    ET.SubElement(report_elem, "no-step-log", {"value": "true"})
    ET.SubElement(report_elem, "duration-log.disable", {"value": "true"})
    ET.ElementTree(config).write(paths["sumocfg"], encoding="utf-8", xml_declaration=True)
    return paths


def parse_summary_steps(path: Path) -> list[dict[str, float]]:
    root = parse_xml_with_retry(path).getroot()
    steps = []
    for step in root.findall("step"):
        steps.append(
            {
                "time": float(step.get("time", "0") or 0),
                "inserted": float(step.get("inserted", "0") or 0),
                "arrived": float(step.get("arrived", "0") or 0),
                "running": float(step.get("running", "0") or 0),
                "waiting": float(step.get("waiting", "0") or 0),
                "teleports": float(step.get("teleports", "0") or 0),
                "mean_speed_mps": max(float(step.get("meanSpeed", "0") or 0), 0.0),
            }
        )
    if not steps:
        raise ParameterSimError(f"summary-output has no steps:{path}")
    return steps


def parse_summary_output(path: Path) -> dict[str, Any]:
    steps = parse_summary_steps(path)
    last_step = steps[-1]
    speed_num = sum(step["mean_speed_mps"] * step["running"] for step in steps if step["running"] > 0)
    speed_den = sum(step["running"] for step in steps if step["running"] > 0)
    mean_speed_mps = speed_num / speed_den if speed_den else last_step["mean_speed_mps"]
    return {
        "departed_count_total": int(last_step["inserted"]),
        "arrived_count_total": int(last_step["arrived"]),
        "teleport_count": max(int(step["teleports"]) for step in steps),
        "sim_end_time": last_step["time"],
        "network_avg_speed_kmh": mean_speed_mps * 3.6,
    }


def summary_window_stats(steps: list[dict[str, float]], start_time: float, end_time: float) -> dict[str, float | str]:
    samples = [step for step in steps if start_time <= step["time"] <= end_time]
    if not samples:
        return {"speed_kmh": "", "sample_count": 0}
    speed_den = sum(step["running"] for step in samples if step["running"] > 0)
    if not speed_den:
        return {"speed_kmh": "", "sample_count": len(samples)}
    speed_kmh = sum(step["mean_speed_mps"] * step["running"] for step in samples if step["running"] > 0) / speed_den * 3.6
    return {"speed_kmh": speed_kmh, "sample_count": len(samples)}


def parse_tripinfo(path: Path, vehicle_id: str) -> dict[str, Any]:
    root = parse_xml_with_retry(path).getroot()
    for tripinfo in root.findall("tripinfo"):
        if tripinfo.get("id") == vehicle_id:
            return {
                "emergency_arrived": True,
                "emergency_travel_time_sec": float(tripinfo.get("duration", "0") or 0),
                "emergency_depart_time_observed": float(tripinfo.get("depart", "0") or 0),
                "emergency_arrival_time": float(tripinfo.get("arrival", "0") or 0),
            }
    return {
        "emergency_arrived": False,
        "emergency_travel_time_sec": "",
        "emergency_depart_time_observed": "",
        "emergency_arrival_time": "",
    }


def route_error_count(stderr: str) -> int:
    lower = stderr.lower()
    return lower.count("route error") + lower.count("has no valid route") + lower.count("is not connected")


def emergency_teleport_lines(stderr: str, vehicle_id: str) -> list[str]:
    return [line for line in stderr.splitlines() if vehicle_id in line and "teleport" in line.lower()]


def parse_tl_logic(net_path: Path) -> dict[str, list[dict[str, Any]]]:
    result = {}
    for _event, elem in ET.iterparse(net_path, events=("end",)):
        if elem.tag == "tlLogic" and elem.get("id"):
            phases = [{"duration": float(p.get("duration", "0") or 0), "state": p.get("state", "")} for p in elem.findall("phase")]
            result[elem.get("id", "")] = phases
            elem.clear()
    return result


def phase_indices_for_link(phases: list[dict[str, Any]], link_index: int, chars: str) -> list[int]:
    indices = []
    for idx, phase in enumerate(phases):
        state = str(phase.get("state", ""))
        if 0 <= link_index < len(state) and state[link_index] in chars:
            indices.append(idx)
    return indices


def load_tls_plan(task: dict[str, Any], route_edges: list[str]) -> list[dict[str, Any]]:
    route_id = task["route_id"]
    corridor_tls_ids = load_corridor_tls_ids(Path(task["priority_terminals"]))
    rows = []
    for row in read_csv(Path(task["tls_audit"])):
        if row.get("route_id") != route_id or row.get("tls_id") not in corridor_tls_ids:
            continue
        green = [int(v) for v in row.get("green_phase_indices", "").split() if v.isdigit()]
        rows.append(
            {
                "route_id": row["route_id"],
                "tls_id": row["tls_id"],
                "junction_id": row.get("junction_id", ""),
                "incoming": row.get("emergency_incoming_edge", ""),
                "outgoing": row.get("emergency_outgoing_edge", ""),
                "distance": float(row.get("distance_from_route_start_m") or 0),
                "link_index": int(row.get("emergency_link_index") or -1),
                "green_phases": green,
                "is_controllable": row.get("is_controllable") == "True",
            }
        )
    rows.sort(key=lambda item: item["distance"])
    if rows:
        return rows
    sumo_net = read_sumo_net(str(task["net"]))
    edge_starts = route_edge_starts(Path(task["net"]), route_edges)
    phases_by_tls = parse_tl_logic(Path(task["net"]))
    generated = []
    seen = set()
    for idx, (from_edge, to_edge) in enumerate(zip(route_edges, route_edges[1:], strict=False)):
        connections = sumo_net.getEdge(from_edge).getOutgoing().get(sumo_net.getEdge(to_edge), [])
        for connection in connections:
            tls_id = connection.getTLSID()
            link_index = int(connection.getTLLinkIndex())
            if not tls_id or tls_id not in corridor_tls_ids or link_index < 0 or (tls_id, link_index) in seen:
                continue
            green = phase_indices_for_link(phases_by_tls.get(tls_id, []), link_index, "Gg")
            seen.add((tls_id, link_index))
            generated.append(
                {
                    "route_id": route_id,
                    "tls_id": tls_id,
                    "junction_id": tls_id,
                    "incoming": from_edge,
                    "outgoing": to_edge,
                    "distance": edge_starts.get(to_edge, edge_starts.get(from_edge, 0.0)),
                    "link_index": link_index,
                    "green_phases": green,
                    "is_controllable": bool(green),
                }
            )
    generated.sort(key=lambda item: item["distance"])
    return generated


def phase_remaining_seconds(traci: Any, tls_id: str, sim_time: float) -> float:
    try:
        return max(float(traci.trafficlight.getNextSwitch(tls_id)) - sim_time, 0.0)
    except Exception:
        return 0.0


def is_green_phase(traci: Any, tls: dict[str, Any]) -> bool:
    try:
        return int(traci.trafficlight.getPhase(tls["tls_id"])) in set(tls.get("green_phases") or [])
    except Exception:
        return False


def control_tls_if_needed(
    traci: Any,
    task: dict[str, Any],
    tls: dict[str, Any],
    state: dict[str, Any],
    sim_time: float,
    distance_to_tls: float,
    vehicle_id: str,
) -> list[dict[str, Any]]:
    params = task["params"]
    events = []
    tls_id = tls["tls_id"]
    if not tls.get("is_controllable") or not tls.get("green_phases"):
        return events
    current_phase = int(traci.trafficlight.getPhase(tls_id))
    remaining = phase_remaining_seconds(traci, tls_id, sim_time)
    key = tls_id
    if key not in state["requested_tls"]:
        state["requested_tls"][key] = sim_time
        events.append(event_row(task, vehicle_id, tls, sim_time, "request_priority", "within_detection_distance", distance_to_tls, current_phase, current_phase, remaining, "", 0.0, 0.0))
    requested_at = float(state["requested_tls"][key])
    green_phases = list(tls.get("green_phases") or [])
    if current_phase in green_phases:
        target = max(int(round(remaining)), int(round(float(params["G_ext"]))))
        extension = max(float(target) - remaining, 0.0)
        traci.trafficlight.setPhaseDuration(tls_id, target)
        if key not in state["green_extended_tls"]:
            state["green_extended_tls"].add(key)
            state["total_extension_delta_sec"] += extension
            events.append(event_row(task, vehicle_id, tls, sim_time, "extend_green", "current_or_sequence_green", distance_to_tls, current_phase, current_phase, remaining, target, extension, 0.0))
        return events
    if sim_time - requested_at >= float(params["T_change_sec"]) and key not in state["switched_tls"]:
        target_phase = int(green_phases[0])
        duration = int(round(float(params["G_ext"])))
        traci.trafficlight.setPhase(tls_id, target_phase)
        traci.trafficlight.setPhaseDuration(tls_id, duration)
        state["switched_tls"].add(key)
        state["total_extension_delta_sec"] += duration
        events.append(event_row(task, vehicle_id, tls, sim_time, "switch_to_green_after_t_change", "t_change_elapsed", distance_to_tls, current_phase, target_phase, remaining, duration, duration, 0.0))
    return events


def trim_tls_after_pass(
    traci: Any,
    task: dict[str, Any],
    tls: dict[str, Any],
    state: dict[str, Any],
    sim_time: float,
    vehicle_id: str,
) -> list[dict[str, Any]]:
    tls_id = tls["tls_id"]
    if tls_id in state["trimmed_tls"] or not tls.get("green_phases"):
        return []
    current_phase = int(traci.trafficlight.getPhase(tls_id))
    remaining = phase_remaining_seconds(traci, tls_id, sim_time)
    alpha = int(round(float(task["params"]["alpha"])))
    if current_phase in set(tls.get("green_phases") or []) and remaining > alpha:
        trimmed = max(remaining - alpha, 0.0)
        traci.trafficlight.setPhaseDuration(tls_id, alpha)
        state["trimmed_tls"].add(tls_id)
        state["trimmed_green_sec"] += trimmed
        return [event_row(task, vehicle_id, tls, sim_time, "trim_green_after_pass_to_alpha", "emergency_passed_tls_trim_to_alpha", "", current_phase, current_phase, remaining, alpha, 0.0, trimmed)]
    state["trimmed_tls"].add(tls_id)
    return []


def event_row(
    task: dict[str, Any],
    vehicle_id: str,
    tls: dict[str, Any],
    sim_time: float,
    action: str,
    reason: str,
    distance_to_tls: Any,
    phase_before: Any,
    phase_after: Any,
    remaining_before: Any,
    set_duration: Any,
    extension_delta: Any,
    trimmed_green: Any,
) -> dict[str, Any]:
    return {
        "time": sec(sim_time),
        "mode": task["mode"],
        "parameter_id": task["parameter_id"],
        "repeat_id": task["repeat_id"],
        "route_id": task["route_id"],
        "vehicle_id": vehicle_id,
        "tls_id": tls.get("tls_id", ""),
        "junction_id": tls.get("junction_id", ""),
        "incoming": tls.get("incoming", ""),
        "outgoing": tls.get("outgoing", ""),
        "action": action,
        "reason": reason,
        "distance_to_tls_m": sec(distance_to_tls) if distance_to_tls != "" else "",
        "phase_before": phase_before,
        "phase_after": phase_after,
        "phase_remaining_before_sec": sec(remaining_before) if remaining_before != "" else "",
        "set_duration_sec": sec(set_duration) if set_duration != "" else "",
        "extension_delta_sec": sec(extension_delta) if extension_delta != "" else "",
        "trimmed_green_sec": sec(trimmed_green) if trimmed_green != "" else "",
    }


def median_value(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (float(ordered[middle - 1]) + float(ordered[middle])) / 2.0


def queue_recovery_detail(queue_history: list[tuple[float, int]], pass_time: float | None, emergency_depart: float) -> dict[str, Any]:
    if pass_time is None or not queue_history:
        return {"recovered": False, "recovered_time_sec": "", "recovery_sec": 0.0}
    history = sorted((float(time_value), int(queue)) for time_value, queue in queue_history)
    baseline_start = float(emergency_depart) - RECOVERY_PRE_BASELINE_START_SEC
    baseline_end = float(emergency_depart) - RECOVERY_PRE_BASELINE_END_SEC
    baseline_values = [float(queue) for time_value, queue in history if baseline_start <= time_value <= baseline_end]
    if not baseline_values:
        baseline_values = [float(queue) for time_value, queue in history if time_value <= float(emergency_depart)]
    pre_baseline = median_value(baseline_values) if baseline_values else float(history[0][1])
    post_window_end = float(pass_time) + RECOVERY_POST_PEAK_WINDOW_SEC
    post_window = [(time_value, queue) for time_value, queue in history if float(pass_time) <= time_value <= post_window_end]
    post_history = [(time_value, queue) for time_value, queue in history if time_value >= float(pass_time)]
    if not post_window:
        post_window = post_history
    post_peak = max((queue for _, queue in post_window), default=0)
    post_peak_time = next((time_value for time_value, queue in post_window if queue == post_peak), float(pass_time))
    recovery_threshold = max(pre_baseline + RECOVERY_BASELINE_MARGIN_QUEUE, math.ceil(post_peak * RECOVERY_POST_PEAK_FRACTION))
    recovery_time = None
    for idx, (time_value, queue) in enumerate(history):
        if time_value < post_peak_time or queue > recovery_threshold:
            continue
        stable_until = time_value + RECOVERY_STABLE_WINDOW_SEC
        stable_samples = [(sample_time, sample_queue) for sample_time, sample_queue in history[idx:] if sample_time <= stable_until]
        if not stable_samples or stable_samples[-1][0] < stable_until:
            continue
        if all(sample_queue <= recovery_threshold for _, sample_queue in stable_samples):
            recovery_time = time_value
            break
    recovered = recovery_time is not None
    if recovery_time is None:
        recovery_time = history[-1][0]
    return {"recovered": recovered, "recovered_time_sec": recovery_time if recovered else "", "recovery_sec": max(float(recovery_time) - float(pass_time), 0.0)}


def queue_recovery_summary(queue_history_by_tls: dict[str, list[tuple[float, int]]], pass_time_by_tls: dict[str, float], emergency_depart: float) -> dict[str, Any]:
    recoveries = []
    recovered_times = []
    for tls_id, pass_time in pass_time_by_tls.items():
        history = queue_history_by_tls.get(tls_id, [])
        if not history:
            continue
        detail = queue_recovery_detail(history, pass_time, emergency_depart)
        recoveries.append(float(detail["recovery_sec"]))
        if detail["recovered_time_sec"] != "":
            recovered_times.append(float(detail["recovered_time_sec"]))
    return {
        "T_recovery_sec": max(recoveries) if recoveries else 0.0,
        "latest_queue_recovery_time": max(recovered_times) if recovered_times else "",
    }


def recovery_speed_penalty_sec(post_recovery_speed_kmh: float | str, recovery_buffer_sec: float) -> float:
    if post_recovery_speed_kmh in {"", None}:
        return 0.0
    speed = float(post_recovery_speed_kmh)
    if speed >= PREFERRED_CONGESTION_MIN_KMH:
        return 0.0
    return max(0.0, float(recovery_buffer_sec)) * (PREFERRED_CONGESTION_MIN_KMH - speed) / PREFERRED_CONGESTION_MIN_KMH


def summarize_general_non_main_delay(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"N_delay_sec": 0.0}
    total_delay = 0.0
    for record in records:
        total_delay += max(float(record["actual_sec"]) - float(record["free_flow_sec"]), 0.0)
    return {"N_delay_sec": total_delay / len(records)}


def run_traci_loop(task: dict[str, Any], paths: dict[str, Path], vehicle_id: str, route_edges: list[str], tls_plan: list[dict[str, Any]], background_count: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import traci  # type: ignore

    sumo = shutil.which("sumo")
    if sumo is None:
        raise ParameterSimError("missing_executable:sumo")
    cmd = [
        sumo,
        "-c",
        str(paths["sumocfg"]),
        "--message-log",
        str(paths["stdout"]),
        "--error-log",
        str(paths["stderr"]),
    ]
    events: list[dict[str, Any]] = []
    observations: dict[str, Any] = {
        "emergency_seen": False,
        "pass_time_by_tls": {},
        "queue_history_by_tls": {tls["tls_id"]: [] for tls in tls_plan},
        "general_non_main_records": [],
        "total_extension_delta_sec": 0.0,
        "trimmed_green_sec": 0.0,
        "requested_tls": {},
        "green_extended_tls": set(),
        "switched_tls": set(),
        "trimmed_tls": set(),
        "emergency_arrival_time": None,
        "general_vehicle_speed_sum_kmh": 0.0,
        "general_vehicle_speed_sample_count": 0,
    }
    sumo_net = read_sumo_net(str(task["net"]))
    corridor_edges = load_corridor_edge_ids(Path(task["corridor_edges"]))
    non_main_free_flow = {}
    for edge in sumo_net.getEdges():
        edge_id = edge.getID()
        if edge_id.startswith(":") or edge_id in corridor_edges:
            continue
        non_main_free_flow[edge_id] = edge_free_flow_seconds(sumo_net, edge_id)
    starts = route_edge_starts(Path(task["net"]), route_edges)
    vehicle_edge_state: dict[str, tuple[str, float]] = {}
    dynamic_insert = task["mode"] in {"B0", "B2"} and float(task["emergency_depart"]) > 0
    emergency_inserted = not dynamic_insert
    emergency_arrived = False
    with Path(os.devnull).open("w", encoding="utf-8") as devnull:
        traci.start(cmd, stdout=devnull)
        started_at = time.time()
        try:
            while True:
                if time.time() - started_at > int(task["timeout_sec"]):
                    events.append({"time": sec(traci.simulation.getTime()), "route_id": task["route_id"], "action": "timeout", "reason": "wall_timeout"})
                    break
                if traci.simulation.getTime() > int(task["timeout_steps"]):
                    events.append({"time": sec(traci.simulation.getTime()), "route_id": task["route_id"], "action": "timeout", "reason": "timeout_steps"})
                    break
                arrival_time = observations.get("emergency_arrival_time")
                if arrival_time is not None and traci.simulation.getTime() >= float(arrival_time) + float(task["recovery_buffer_sec"]):
                    break
                if traci.simulation.getMinExpectedNumber() <= 0 and emergency_inserted and (emergency_arrived or observations["emergency_seen"]):
                    break
                traci.simulationStep()
                sim_time = float(traci.simulation.getTime())
                if dynamic_insert and not emergency_inserted and sim_time >= float(task["emergency_depart"]):
                    traci.vehicle.addFull(
                        vehID=vehicle_id,
                        routeID=task["route_id"],
                        typeID="theta_check_emergency_type",
                        depart="now",
                        departLane="free",
                        departPos="last",
                        departSpeed="max",
                    )
                    emergency_inserted = True
                    events.append({"time": sec(sim_time), "mode": task["mode"], "parameter_id": task["parameter_id"], "repeat_id": task["repeat_id"], "route_id": task["route_id"], "vehicle_id": vehicle_id, "action": "dynamic_emergency_insert", "reason": f"depart_{sec(task['emergency_depart'])}"})
                vehicle_ids = set(traci.vehicle.getIDList())
                for vid in vehicle_ids:
                    if vid == vehicle_id:
                        continue
                    try:
                        observations["general_vehicle_speed_sum_kmh"] += float(traci.vehicle.getSpeed(vid)) * 3.6
                        observations["general_vehicle_speed_sample_count"] += 1
                    except Exception:
                        continue
                if vehicle_id in vehicle_ids:
                    observations["emergency_seen"] = True
                    road_id = traci.vehicle.getRoadID(vehicle_id)
                    if road_id in starts:
                        distance_along = starts[road_id] + float(traci.vehicle.getLanePosition(vehicle_id))
                        if task["mode"] == "B2":
                            for tls in tls_plan:
                                tls_distance = float(tls["distance"])
                                distance_to_tls = tls_distance - distance_along
                                if tls["tls_id"] not in observations["pass_time_by_tls"] and distance_to_tls < -2.0:
                                    observations["pass_time_by_tls"][tls["tls_id"]] = sim_time
                                    events.extend(trim_tls_after_pass(traci, task, tls, observations, sim_time, vehicle_id))
                                elif 0.0 <= distance_to_tls <= float(task["params"]["D_det"]):
                                    events.extend(control_tls_if_needed(traci, task, tls, observations, sim_time, distance_to_tls, vehicle_id))
                elif observations["emergency_seen"]:
                    emergency_arrived = True
                    if observations.get("emergency_arrival_time") is None:
                        observations["emergency_arrival_time"] = sim_time
                for tls in tls_plan:
                    tls_id = tls["tls_id"]
                    incoming = tls.get("incoming", "")
                    queue = 0
                    if incoming:
                        try:
                            queue = int(traci.edge.getLastStepHaltingNumber(incoming))
                        except Exception:
                            queue = 0
                    observations["queue_history_by_tls"][tls_id].append((sim_time, queue))
                for vid in vehicle_ids:
                    if vid == vehicle_id:
                        continue
                    road_id = traci.vehicle.getRoadID(vid)
                    if vid in vehicle_edge_state:
                        prev_edge, entered_at = vehicle_edge_state[vid]
                        if road_id != prev_edge:
                            free = non_main_free_flow.get(prev_edge)
                            if free is not None and sim_time > float(task["emergency_depart"]):
                                left_at = sim_time
                                overlap = max(left_at - max(entered_at, float(task["emergency_depart"])), 0.0)
                                total = max(left_at - entered_at, 0.001)
                                observations["general_non_main_records"].append({"actual_sec": overlap, "free_flow_sec": free * (overlap / total)})
                            vehicle_edge_state[vid] = (road_id, sim_time)
                    else:
                        vehicle_edge_state[vid] = (road_id, sim_time)
        finally:
            traci.close(False)
    sample_count = int(observations["general_vehicle_speed_sample_count"])
    observations["general_vehicle_avg_speed_kmh"] = (
        float(observations["general_vehicle_speed_sum_kmh"]) / sample_count if sample_count else ""
    )
    return events, observations


def base_result_row(task: dict[str, Any], vehicle_id: str, params: dict[str, Any]) -> dict[str, Any]:
    route_row = task["route_row"]
    return {
        "generated_at": task["generated_at"],
        "run_id": task["run_id"],
        "task_id": task["task_id"],
        "mode": task["mode"],
        "parameter_id": task["parameter_id"],
        "repeat_id": task["repeat_id"],
        "route_id": task["route_id"],
        "route_target_edge": route_row.get("target_edge_id", ""),
        "emergency_depart": sec(task["emergency_depart"]),
        "D_det": params.get("D_det", ""),
        "alpha": params.get("alpha", ""),
        "G_ext": params.get("G_ext", ""),
        "T_change_sec": params.get("T_change_sec", ""),
        "emergency_vehicle_id": vehicle_id,
        "run_dir": rel(Path(task["run_dir"])),
        "task_status_json": rel(Path(task["task_status_json"])),
    }


def run_task(task: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    run_dir = Path(task["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    route_row = task["route_row"]
    route_edges = route_row["route_edges"].split()
    params = task.get("params") or {}
    vehicle_id = f"emergency_{task['route_id']}_{task['mode']}_{task['parameter_id']}_{task['repeat_id']}"
    row = base_result_row(task, vehicle_id, params)
    events: list[dict[str, Any]] = []
    try:
        failures = validate_route_edges(Path(task["net"]), route_edges)
        if failures:
            raise ParameterSimError(";".join(failures[:10]))
        if task["mode"] == "B2":
            for key in ["D_det", "alpha", "G_ext", "T_change_sec"]:
                if key not in params:
                    raise ParameterSimError(f"missing_b2_param:{key}")
                parsed = float(params[key])
                if parsed < 0:
                    raise ParameterSimError(f"negative_b2_param:{key}")
        include_static_vehicle = task["mode"] == "B00" or float(task["emergency_depart"]) <= 0
        emergency_route_xml = run_dir / f"{vehicle_id}.rou.xml"
        write_emergency_route_xml(emergency_route_xml, route_row, vehicle_id, float(task["emergency_depart"]), include_static_vehicle)
        paths = write_sumo_files(task, emergency_route_xml, tls_all_off=task["mode"] == "B00")
        background_count = count_vehicles(Path(task["background_route"])) if task.get("include_background") else 0
        tls_plan = load_tls_plan(task, route_edges)
        if task["mode"] == "B2" and not tls_plan:
            raise ParameterSimError(f"no_tls_plan:{task['route_id']}")
        events, observations = run_traci_loop(task, paths, vehicle_id, route_edges, tls_plan, background_count)
        summary_steps = parse_summary_steps(paths["summary"])
        summary = parse_summary_output(paths["summary"])
        trip = parse_tripinfo(paths["tripinfo"], vehicle_id)
        stderr_text = paths["stderr"].read_text(encoding="utf-8", errors="replace") if paths["stderr"].is_file() else ""
        emergency_tp = emergency_teleport_lines(stderr_text, vehicle_id)
        emergency_arrived = bool(trip["emergency_arrived"])
        emergency_departed = bool(observations.get("emergency_seen")) or emergency_arrived or summary["departed_count_total"] > background_count
        route_errors = route_error_count(stderr_text)
        background_departed = max(int(summary["departed_count_total"]) - (1 if emergency_departed else 0), 0)
        background_arrived = max(int(summary["arrived_count_total"]) - (1 if emergency_arrived else 0), 0)
        background_teleported = max(int(summary["teleport_count"]) - (1 if emergency_tp else 0), 0)
        warnings = []
        failures = []
        if not emergency_departed:
            failures.append("emergency_not_departed")
        if not emergency_arrived:
            failures.append("emergency_not_arrived")
        if emergency_tp:
            failures.append("emergency_teleport_detected")
        if route_errors:
            failures.append("route_error_count_gt_0")
        if background_teleported:
            warnings.append("background_teleports_present")
        queue_summary = queue_recovery_summary(observations["queue_history_by_tls"], observations["pass_time_by_tls"], float(task["emergency_depart"]))
        recovery_anchor = queue_summary.get("latest_queue_recovery_time") or observations.get("emergency_arrival_time") or summary["sim_end_time"]
        post_recovery = summary_window_stats(
            summary_steps,
            float(recovery_anchor),
            min(float(recovery_anchor) + float(task["recovery_buffer_sec"]), float(summary["sim_end_time"])),
        )
        t_recovery_queue = float(queue_summary["T_recovery_sec"])
        t_recovery_speed_penalty = recovery_speed_penalty_sec(post_recovery["speed_kmh"], float(task["recovery_buffer_sec"]))
        t_recovery = t_recovery_queue + t_recovery_speed_penalty
        delay_summary = summarize_general_non_main_delay(observations["general_non_main_records"])
        signal_events_csv = run_dir / "signal_events.csv"
        write_csv(signal_events_csv, events, EVENT_FIELDS)
        total_extension = float(observations.get("total_extension_delta_sec", 0.0) or 0.0)
        trimmed_green = float(observations.get("trimmed_green_sec", 0.0) or 0.0)
        row.update(
            {
                "final_status": "FAIL" if failures else "WARNING" if warnings else "PASS",
                "failure_reason": ";".join(failures),
                "warning_reason": ";".join(warnings),
                "sumo_exit_code": 0,
                "emergency_departed": emergency_departed,
                "emergency_arrived": emergency_arrived,
                "emergency_teleport": bool(emergency_tp),
                "emergency_travel_time_sec": sec(trip["emergency_travel_time_sec"]) if trip["emergency_travel_time_sec"] != "" else "",
                "N_delay_sec": sec(delay_summary["N_delay_sec"]),
                "T_recovery_sec": sec(t_recovery),
                "T_recovery_queue_sec": sec(t_recovery_queue),
                "T_recovery_speed_penalty_sec": sec(t_recovery_speed_penalty),
                "background_departed": background_departed,
                "background_arrived": background_arrived,
                "background_teleported": background_teleported,
                "background_teleport_ratio": round(background_teleported / background_departed, 6) if background_departed else 0.0,
                "route_error_count": route_errors,
                "network_avg_speed_kmh": sec(summary["network_avg_speed_kmh"]),
                "general_vehicle_avg_speed_kmh": sec(observations.get("general_vehicle_avg_speed_kmh", "")),
                "general_vehicle_speed_sample_count": observations.get("general_vehicle_speed_sample_count", 0),
                "sim_end_time": sec(summary["sim_end_time"]),
                "intervention_count": sum(1 for event in events if event.get("action") in {"extend_green", "switch_to_green_after_t_change"}),
                "t_change_switch_count": sum(1 for event in events if event.get("action") == "switch_to_green_after_t_change"),
                "green_extension_count": sum(1 for event in events if event.get("action") == "extend_green"),
                "post_pass_trim_count": sum(1 for event in events if event.get("action") == "trim_green_after_pass_to_alpha"),
                "realized_extension_sec": sec(max(total_extension - trimmed_green, 0.0)),
                "trimmed_green_sec": sec(trimmed_green),
                "signal_event_count": len(events),
                "elapsed_wall_sec": sec(time.time() - started),
                "sumocfg": rel(paths["sumocfg"]),
                "tripinfo": rel(paths["tripinfo"]),
                "summary_output": rel(paths["summary"]),
                "edgeData_output": rel(paths["edge_data"]),
                "signal_events_csv": rel(signal_events_csv),
            }
        )
    except Exception as exc:  # noqa: BLE001
        row.update(
            {
                "final_status": "FAIL",
                "failure_reason": f"{type(exc).__name__}:{exc}",
                "warning_reason": "",
                "sumo_exit_code": "",
                "emergency_departed": False,
                "emergency_arrived": False,
                "emergency_teleport": False,
                "emergency_travel_time_sec": "",
                "N_delay_sec": "",
                "T_recovery_sec": "",
                "route_error_count": "",
                "signal_event_count": len(events),
                "elapsed_wall_sec": sec(time.time() - started),
            }
        )
    status_payload = {
        "schema": "theta_check_task_status.v1",
        "task_id": task["task_id"],
        "status": row["final_status"],
        "updated_at": utc_now(),
        "result_row": row,
    }
    write_json(Path(task["task_status_json"]), status_payload)
    return row


def row_float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def enrich_comparisons(rows: list[dict[str, Any]]) -> None:
    b00 = {}
    b0 = {}
    for row in rows:
        key = (row.get("route_id", ""), row.get("repeat_id", ""))
        travel = row_float(row, "emergency_travel_time_sec")
        if travel is None:
            continue
        if row.get("mode") == "B00":
            b00[key] = travel
        elif row.get("mode") == "B0":
            b0[key] = travel
    for row in rows:
        key = (row.get("route_id", ""), row.get("repeat_id", ""))
        travel = row_float(row, "emergency_travel_time_sec")
        n_delay = row_float(row, "N_delay_sec")
        t_recovery = row_float(row, "T_recovery_sec")
        base_b00 = b00.get(key)
        if row.get("mode") == "B00":
            row["b00_emergency_travel_time_sec"] = sec(travel) if travel is not None else ""
            row["A_delay_sec"] = sec(0.0) if travel is not None else ""
        elif travel is not None and base_b00 is not None:
            row["b00_emergency_travel_time_sec"] = sec(base_b00)
            row["A_delay_sec"] = sec(travel - base_b00)
        if row.get("mode") == "B2" and travel is not None and key in b0:
            delta = travel - b0[key]
            row["B2_vs_B0_travel_time_delta_sec"] = sec(delta)
            row["B2_vs_B0_pct"] = sec((delta / b0[key]) * 100.0) if b0[key] else ""
        if row.get("mode") in {"B0", "B2"}:
            a_delay = row_float(row, "A_delay_sec")
            if a_delay is not None and n_delay is not None and t_recovery is not None:
                row["score_sec"] = sec(SCORE_WEIGHT_A * a_delay + SCORE_WEIGHT_N * n_delay + SCORE_WEIGHT_RECOVERY * t_recovery)
    for row in rows:
        for field in RESULT_FIELDS:
            row.setdefault(field, "")


def collect_completed_rows(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for task in tasks:
        status = completed_task_status(Path(task["task_status_json"]))
        if status and isinstance(status.get("result_row"), dict):
            rows.append(status["result_row"])
    rows.sort(key=lambda row: (row.get("repeat_id", ""), row.get("route_id", ""), row.get("mode", ""), row.get("parameter_id", "")))
    enrich_comparisons(rows)
    return rows


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * pct
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - pos) + ordered[high] * (pos - low)


def build_route_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row.get("route_id", ""), row.get("repeat_id", "")), []).append(row)
    summaries = []
    for (route_id, repeat_id), group in sorted(grouped.items()):
        b00_rows = [row for row in group if row.get("mode") == "B00"]
        b0_rows = [row for row in group if row.get("mode") == "B0"]
        b2_rows = [row for row in group if row.get("mode") == "B2" and row_float(row, "emergency_travel_time_sec") is not None]
        b2_best = min(b2_rows, key=lambda row: row_float(row, "emergency_travel_time_sec") or float("inf")) if b2_rows else None
        b0_time = row_float(b0_rows[0], "emergency_travel_time_sec") if b0_rows else None
        b2_time = row_float(b2_best, "emergency_travel_time_sec") if b2_best else None
        delta = b2_time - b0_time if b0_time is not None and b2_time is not None else None
        statuses = {row.get("final_status") for row in group}
        summaries.append(
            {
                "route_id": route_id,
                "repeat_id": repeat_id,
                "emergency_depart": group[0].get("emergency_depart", "") if group else "",
                "B00_travel_time_sec": b00_rows[0].get("emergency_travel_time_sec", "") if b00_rows else "",
                "B0_travel_time_sec": b0_rows[0].get("emergency_travel_time_sec", "") if b0_rows else "",
                "B2_best_parameter_id": b2_best.get("parameter_id", "") if b2_best else "",
                "B2_best_travel_time_sec": sec(b2_time) if b2_time is not None else "",
                "B2_vs_B0_travel_time_delta_sec": sec(delta) if delta is not None else "",
                "B2_vs_B0_pct": sec((delta / b0_time) * 100.0) if delta is not None and b0_time else "",
                "B2_improved": delta is not None and delta < 0,
                "B2_worsened": delta is not None and delta > 0,
                "status": "FAIL" if "FAIL" in statuses else "WARNING" if "WARNING" in statuses else "PASS",
            }
        )
    return summaries


def build_summary(args: argparse.Namespace, run_id: str, tasks: list[dict[str, Any]], rows: list[dict[str, Any]], route_summary: list[dict[str, Any]], paths: dict[str, Path]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("final_status") or "")
        status_counts[status] = status_counts.get(status, 0) + 1
    deltas = [row_float(row, "B2_vs_B0_travel_time_delta_sec") for row in rows if row.get("mode") == "B2"]
    delta_values = [value for value in deltas if value is not None]
    route_deltas = [row_float(row, "B2_vs_B0_travel_time_delta_sec") for row in route_summary]
    route_delta_values = [value for value in route_deltas if value is not None]
    emergency_problem_routes = sorted({row["route_id"] for row in rows if parse_bool(row.get("emergency_teleport")) or int(row.get("route_error_count") or 0) > 0})
    completed_count = len(rows)
    final_status = "RUNNING"
    if completed_count == len(tasks):
        final_status = "FAIL" if status_counts.get("FAIL") else "WARNING" if status_counts.get("WARNING") else "PASS"
    return {
        "schema": "theta_check_experiment_summary.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "output_prefix": args.output_prefix,
        "active_net": rel(args.net),
        "background_route": rel(args.background_route),
        "routes_csv": rel(args.routes_csv),
        "b2_params": rel(args.b2_params),
        "modes": args.modes,
        "repeats": args.repeats,
        "seed": args.seed,
        "depart_min": args.depart_min,
        "depart_max": args.depart_max,
        "excluded_routes": args.exclude_routes,
        "task_count": len(tasks),
        "completed_task_count": completed_count,
        "remaining_task_count": len(tasks) - completed_count,
        "status_counts": status_counts,
        "final_status": final_status,
        "b2_improved_route_count": sum(1 for row in route_summary if parse_bool(row.get("B2_improved"))),
        "b2_worsened_route_count": sum(1 for row in route_summary if parse_bool(row.get("B2_worsened"))),
        "b2_delta_mean_sec": sec(sum(route_delta_values) / len(route_delta_values)) if route_delta_values else "",
        "b2_delta_median_sec": sec(percentile(route_delta_values, 0.5)) if route_delta_values else "",
        "b2_delta_p90_sec": sec(percentile(route_delta_values, 0.9)) if route_delta_values else "",
        "b2_task_delta_mean_sec": sec(sum(delta_values) / len(delta_values)) if delta_values else "",
        "emergency_problem_routes": emergency_problem_routes,
        "signal_burden": {
            "intervention_count": sum(int(row.get("intervention_count") or 0) for row in rows),
            "realized_extension_sec": sec(sum(row_float(row, "realized_extension_sec") or 0.0 for row in rows)),
            "trimmed_green_sec": sec(sum(row_float(row, "trimmed_green_sec") or 0.0 for row in rows)),
        },
        "outputs": {
            "task_manifest": rel(paths["task_manifest"]),
            "experiment_results_csv": rel(paths["results_csv"]),
            "score_components_csv": rel(paths["score_components_csv"]),
            "route_summary_csv": rel(paths["route_summary_csv"]),
            "summary_json": rel(paths["summary_json"]),
            "latest_json": rel(paths["latest_json"]),
        },
    }


def write_incremental_outputs(args: argparse.Namespace, run_id: str, tasks: list[dict[str, Any]], paths: dict[str, Path]) -> dict[str, Any]:
    rows = collect_completed_rows(tasks)
    route_summary = build_route_summary(rows)
    write_csv(paths["results_csv"], rows, RESULT_FIELDS)
    write_csv(paths["score_components_csv"], rows, RESULT_FIELDS)
    write_csv(paths["route_summary_csv"], route_summary, ROUTE_SUMMARY_FIELDS)
    summary = build_summary(args, run_id, tasks, rows, route_summary, paths)
    write_json(paths["summary_json"], {**summary, "route_summary": route_summary})
    write_json(
        paths["latest_json"],
        {
            "generated_at": utc_now(),
            "run_id": run_id,
            "output_prefix": args.output_prefix,
            "final_status": summary["final_status"],
            "completed_task_count": summary["completed_task_count"],
            "task_count": summary["task_count"],
            "summary_json": rel(paths["summary_json"]),
            "experiment_results_csv": rel(paths["results_csv"]),
            "route_summary_csv": rel(paths["route_summary_csv"]),
        },
    )
    return summary


def tasks_to_run(tasks: list[dict[str, Any]], resume: bool) -> list[dict[str, Any]]:
    pending = []
    for task in tasks:
        status = completed_task_status(Path(task["task_status_json"]))
        if resume and status is not None:
            continue
        pending.append(task)
    return pending


def main() -> int:
    configure_runtime_environment()
    args = parse_args()
    generated_at = utc_now()
    try:
        validate_args(args)
        run_id = resolve_run_id(args, generated_at)
        paths = output_paths(args, run_id)
        routes = load_routes(args.routes_csv, args.routes, args.exclude_routes)
        b2_params = load_b2_parameter_sets(args.b2_params) if "B2" in args.modes else []
        tasks = build_tasks(args, generated_at, run_id, routes, b2_params)
        write_task_manifest(paths["task_manifest"], tasks, args)
        pending = tasks_to_run(tasks, args.resume)
        write_incremental_outputs(args, run_id, tasks, paths)
        if args.workers == 1:
            for task in pending:
                write_running_status(task)
                run_task(task)
                write_incremental_outputs(args, run_id, tasks, paths)
        else:
            for task in pending:
                write_running_status(task)
            with ProcessPoolExecutor(max_workers=args.workers) as executor:
                future_map = {executor.submit(run_task, task): task for task in pending}
                for future in as_completed(future_map):
                    task = future_map[future]
                    try:
                        future.result()
                    except Exception as exc:  # noqa: BLE001
                        row = base_result_row(task, f"emergency_{task['route_id']}_{task['mode']}_{task['parameter_id']}_{task['repeat_id']}", task.get("params") or {})
                        row.update({"final_status": "FAIL", "failure_reason": f"worker_exception:{type(exc).__name__}:{exc}"})
                        write_json(Path(task["task_status_json"]), {"schema": "theta_check_task_status.v1", "task_id": task["task_id"], "status": "FAIL", "updated_at": utc_now(), "result_row": row})
                    write_incremental_outputs(args, run_id, tasks, paths)
        summary = write_incremental_outputs(args, run_id, tasks, paths)
        print(
            "\n".join(
                [
                    "05 theta check simulation",
                    "=========================",
                    f"run_id: {run_id}",
                    f"task_count: {len(tasks)}",
                    f"completed_task_count: {summary['completed_task_count']}",
                    f"remaining_task_count: {summary['remaining_task_count']}",
                    f"final_status: {summary['final_status']}",
                    f"results_csv: {rel(paths['results_csv'])}",
                    f"route_summary_csv: {rel(paths['route_summary_csv'])}",
                    f"summary_json: {rel(paths['summary_json'])}",
                    f"latest_json: {rel(paths['latest_json'])}",
                ]
            )
        )
        return 0 if summary["final_status"] in {"PASS", "WARNING", "RUNNING"} else 1
    except (ParameterSimError, OSError, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
