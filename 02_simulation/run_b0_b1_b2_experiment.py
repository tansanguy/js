#!/usr/bin/env python3
"""Run B0/B1/B2 route-level SUMO experiments with process-level parallelism."""

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
DEFAULT_B1_CONFIG = PROJECT_ROOT / "configs/b1_priority_signal_config.json"
DEFAULT_B2_PARAMS = PROJECT_ROOT / "configs/b2_parameter_sets.csv"
DEFAULT_MANIFEST = PROJECT_ROOT / "configs/final_experiment_manifest.json"
DEFAULT_B0_SUMMARY = PROJECT_ROOT / "results/metrics/b0_baseline_19route_smoke_summary.csv"

LOG_PATH = PROJECT_ROOT / "outputs/logs/b0_b1_b2_experiment.log"

DEFAULT_TIMEOUT_STEPS = 7200
DEFAULT_TIMEOUT_SEC = 1200
CONTROL_ACTIONS = {"extend_green", "advance_to_next_green"}


class ExperimentError(RuntimeError):
    """Expected experiment runner failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    parser = argparse.ArgumentParser(description="Run B0/B1/B2 experiment batches.")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--emergency-routes", type=Path, default=DEFAULT_EMERGENCY_ROUTES)
    parser.add_argument("--tls-audit", type=Path, default=DEFAULT_TLS_AUDIT)
    parser.add_argument("--b1-config", type=Path, default=DEFAULT_B1_CONFIG)
    parser.add_argument("--b2-params", type=Path, default=DEFAULT_B2_PARAMS)
    parser.add_argument("--b0-summary", type=Path, default=DEFAULT_B0_SUMMARY)
    parser.add_argument("--modes", nargs="+", choices=["B0", "B1", "B2"], default=["B0", "B1", "B2"])
    parser.add_argument("--route-set", choices=["b0_valid_18", "all_19"], default="b0_valid_18")
    parser.add_argument("--routes", nargs="*", default=[])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--time-to-teleport", type=int, default=1200)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--emergency-depart", type=float, default=0.0)
    parser.add_argument("--timeout-steps", type=int, default=DEFAULT_TIMEOUT_STEPS)
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--output-prefix", default="experiment")
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
    args.b1_config = project_path(manifest.get("b1_config"), args.b1_config)
    args.b2_params = project_path(manifest.get("b2_parameter_sets"), args.b2_params)
    args.b0_summary = project_path(manifest.get("b0_summary"), args.b0_summary)
    if not args.routes and manifest.get("route_set"):
        args.route_set = str(manifest["route_set"])
    return manifest


def output_paths(output_prefix: str, legacy: bool) -> dict[str, Path]:
    if legacy:
        return {
            "summary_csv": PROJECT_ROOT / "results/metrics/experiment_b0_b1_b2_summary.csv",
            "summary_json": PROJECT_ROOT / "results/metrics/experiment_b0_b1_b2_summary.json",
            "events_csv": PROJECT_ROOT / "results/metrics/experiment_signal_events.csv",
            "compare_csv": PROJECT_ROOT / "results/metrics/experiment_compare_by_route.csv",
        }
    safe_prefix = output_prefix.strip()
    if not safe_prefix:
        raise ExperimentError("output_prefix cannot be blank")
    return {
        "summary_csv": PROJECT_ROOT / f"results/metrics/{safe_prefix}_b0_b1_b2_summary.csv",
        "summary_json": PROJECT_ROOT / f"results/metrics/{safe_prefix}_b0_b1_b2_summary.json",
        "events_csv": PROJECT_ROOT / f"results/metrics/{safe_prefix}_signal_events.csv",
        "compare_csv": PROJECT_ROOT / f"results/metrics/{safe_prefix}_compare_by_route.csv",
    }


def current_git_commit() -> str:
    try:
        completed = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def load_routes(path: Path) -> dict[str, dict[str, str]]:
    return {row["route_id"]: row for row in read_csv(path) if row.get("route_id")}


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
    required = ["parameter_id", "D_det", "alpha", "t_lead", "G_ext", "tau", "fallback_v_e_mps", "rho"]
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
                "t_lead": float(row["t_lead"]),
                "G_ext": float(row["G_ext"]),
                "tau": int(float(row["tau"])),
                "fallback_v_e_mps": float(row["fallback_v_e_mps"]),
                "rho": row["rho"],
                "v_e_policy": row.get("v_e_policy") or "current_speed_with_fallback",
                "metric_sample_interval": int(float(row.get("metric_sample_interval") or 10)),
                "phase_control_policy": row.get("phase_control_policy") or "existing_sequence_only_no_direct_state_rewrite",
                "yellow_clearance_policy": row.get("yellow_clearance_policy") or "do_not_skip",
                "pedestrian_min_walk_policy": row.get("pedestrian_min_walk_policy") or "safety_placeholder_documented_not_optimized",
            }
        )
    if not result:
        raise ExperimentError(f"empty_b2_parameter_file: {path}")
    return result


def b1_parameter_set(config_path: Path) -> dict[str, Any]:
    config = S14.read_json(config_path)
    required = ["D_det", "v_e_policy", "fallback_v_e_mps", "alpha", "G_ext", "rho", "tau", "t_lead", "metric_sample_interval"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ExperimentError(f"missing_b1_config_parameter: {','.join(missing)}")
    return {"parameter_id": "b1_default", **{key: config[key] for key in required}, **{k: config.get(k) for k in ["phase_control_policy", "yellow_clearance_policy", "pedestrian_min_walk_policy"]}}


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
        "teleport_count": max_teleports,
        "sim_end_time": float(last_step.get("time", "0")),
        "network_avg_speed_kmh": mean_speed_mps * 3.6,
    }


def route_error_count(stderr: str) -> int:
    lower = stderr.lower()
    return lower.count("route error") + lower.count("has no valid route") + lower.count("is not connected")


def emergency_teleport_lines(stderr: str, vehicle_id: str) -> list[str]:
    return [line for line in stderr.splitlines() if vehicle_id in line and "teleport" in line.lower()]


def build_tasks(args: argparse.Namespace, route_ids: list[str], b1_params: dict[str, Any], b2_params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks = []
    for repeat_idx in range(1, args.repeats + 1):
        repeat_id = f"repeat_{repeat_idx:03d}"
        for route_id in route_ids:
            if "B0" in args.modes:
                tasks.append({"mode": "B0", "parameter_id": "no_control", "route_id": route_id, "repeat_id": repeat_id, "params": {}})
            if "B1" in args.modes:
                tasks.append({"mode": "B1", "parameter_id": b1_params["parameter_id"], "route_id": route_id, "repeat_id": repeat_id, "params": b1_params})
            if "B2" in args.modes:
                for params in b2_params:
                    tasks.append({"mode": "B2", "parameter_id": params["parameter_id"], "route_id": route_id, "repeat_id": repeat_id, "params": params})
    return tasks


def common_row_base(task: dict[str, Any], run_dir: Path, vehicle_id: str, params: dict[str, Any], elapsed_sec: float) -> dict[str, Any]:
    return {
        "output_prefix": task["output_prefix"],
        "mode": task["mode"],
        "parameter_id": task["parameter_id"],
        "repeat_id": task["repeat_id"],
        "route_id": task["route_id"],
        "emergency_vehicle_id": vehicle_id,
        "run_dir": rel(run_dir),
        "elapsed_wall_sec": round(elapsed_sec, 3),
        "D_det": params.get("D_det", ""),
        "alpha": params.get("alpha", ""),
        "t_lead": params.get("t_lead", ""),
        "G_ext": params.get("G_ext", ""),
        "tau": params.get("tau", ""),
        "rho": params.get("rho", ""),
        "fallback_v_e_mps": params.get("fallback_v_e_mps", ""),
    }


def run_b0_task(task: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.time()
    routes = load_routes(Path(task["emergency_routes"]))
    route_row = routes[task["route_id"]]
    route_edges = route_row["route_edges"].split()
    validation_failures = S14.validate_route_edges(Path(task["net"]), route_edges)
    run_dir = Path(task["run_dir"])
    vehicle_id = f"emergency_{task['route_id']}_{task['mode']}_{task['repeat_id']}"
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
    paths = S14.write_sumo_files(args, emergency_route_xml)
    sumo = shutil.which("sumo")
    if sumo is None:
        raise ExperimentError("missing_executable: sumo")
    completed = subprocess.run(
        [sumo, "-c", str(paths["sumocfg"])],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=int(task["timeout_sec"]),
    )
    paths["stdout"].write_text(completed.stdout, encoding="utf-8")
    paths["stderr"].write_text(completed.stderr, encoding="utf-8")
    return summarize_run(task, run_dir, vehicle_id, paths, completed.returncode, [], {}, started)


def run_control_task(task: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.time()
    routes = load_routes(Path(task["emergency_routes"]))
    route_row = routes[task["route_id"]]
    route_edges = route_row["route_edges"].split()
    validation_failures = S14.validate_route_edges(Path(task["net"]), route_edges)
    run_dir = Path(task["run_dir"])
    vehicle_id = f"emergency_{task['route_id']}_{task['mode']}_{task['parameter_id']}_{task['repeat_id']}"
    params = task["params"]
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
    tls_plan = S14.load_tls_plan(Path(task["tls_audit"]), task["route_id"])
    if not tls_plan:
        raise ExperimentError(f"no_tls_audit_rows: {task['route_id']}")
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
    paths = S14.write_sumo_files(args, emergency_route_xml)
    events, controller_started = S14.run_controller(args, paths, tls_plan, route_edges, params)
    row, events = summarize_run(task, run_dir, vehicle_id, paths, 0 if controller_started else 1, events, params, started)
    row["controller_started"] = controller_started
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
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    background_count = int(task["background_vehicle_count"])
    stderr_text = paths["stderr"].read_text(encoding="utf-8", errors="replace") if paths["stderr"].is_file() else ""
    summary_metrics = parse_summary_output(paths["summary"])
    trip = S14.parse_tripinfo(paths["tripinfo"], vehicle_id)
    route_errors = route_error_count(stderr_text)
    emergency_tp = emergency_teleport_lines(stderr_text, vehicle_id)
    emergency_arrived = bool(trip["emergency_arrived"])
    emergency_departed = summary_metrics["departed_count_total"] > background_count or emergency_arrived
    emergency_teleport = bool(emergency_tp)
    background_departed = max(summary_metrics["departed_count_total"] - (1 if emergency_departed else 0), 0)
    background_arrived = max(summary_metrics["arrived_count_total"] - (1 if emergency_arrived else 0), 0)
    background_teleported = max(summary_metrics["teleport_count"] - (1 if emergency_teleport else 0), 0)
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
    if route_errors > 0:
        failures.append("route_error_count_gt_0")
    if background_teleported > 0:
        warnings.append("background_teleports_present")
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
            "emergency_travel_time": trip["emergency_travel_time"],
            "route_error_count": route_errors,
            "background_departed": background_departed,
            "background_arrived": background_arrived,
            "background_teleported": background_teleported,
            "background_teleport_ratio": round(background_teleported / background_departed, 6) if background_departed else 0.0,
            "network_avg_speed_kmh": round(float(summary_metrics["network_avg_speed_kmh"]), 6),
            "sim_end_time": summary_metrics["sim_end_time"],
            "controlled_tls_count": len(controlled_tls),
            "skipped_tls_count": len(skipped_tls),
            "failed_tls_count": len(failed_tls),
            "intervention_count": sum(1 for event in events if event.get("action") in CONTROL_ACTIONS),
            "green_extension_count": sum(1 for event in events if event.get("action") == "extend_green"),
            "phase_switch_count": sum(1 for event in events if event.get("action") == "advance_to_next_green"),
            "restore_count": sum(1 for event in events if event.get("action") == "restore"),
            "signal_event_count": len(events),
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
    if task["mode"] == "B0":
        return run_b0_task(task)
    return run_control_task(task)


def compare_rows(result_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    b0_by_key: dict[tuple[str, str], float] = {}
    for row in result_rows:
        if row["mode"] == "B0" and row.get("emergency_travel_time") not in ("", None):
            b0_by_key[(row["route_id"], row["repeat_id"])] = float(row["emergency_travel_time"])
    rows = []
    for row in result_rows:
        if row["mode"] == "B0":
            continue
        base = b0_by_key.get((row["route_id"], row["repeat_id"]))
        current = row.get("emergency_travel_time")
        if base is None or current in ("", None):
            delta = ""
            improvement = ""
        else:
            delta = round(float(current) - base, 6)
            improvement = round(((base - float(current)) / base) * 100.0, 6) if base else ""
        rows.append(
            {
                "output_prefix": row["output_prefix"],
                "route_id": row["route_id"],
                "repeat_id": row["repeat_id"],
                "mode": row["mode"],
                "parameter_id": row["parameter_id"],
                "b0_emergency_travel_time": base if base is not None else "",
                "mode_emergency_travel_time": current,
                "travel_time_delta_sec": delta,
                "travel_time_improvement_pct": improvement,
                "mode_final_status": row["final_status"],
                "emergency_teleport": row["emergency_teleport"],
                "route_error_count": row["route_error_count"],
                "background_teleport_ratio": row["background_teleport_ratio"],
                "intervention_count": row.get("intervention_count", ""),
                "skipped_tls_count": row.get("skipped_tls_count", ""),
            }
        )
    return rows


def main() -> int:
    args = parse_args()
    generated_at = utc_now()
    lines = ["B0/B1/B2 experiment runner", "==========================", f"generated_at: {generated_at}"]
    try:
        manifest = apply_manifest(args)
        for attr in ["net", "background_route", "emergency_routes", "tls_audit", "b1_config", "b2_params", "b0_summary"]:
            path = getattr(args, attr).resolve()
            setattr(args, attr, path)
            if not path.is_file():
                raise ExperimentError(f"missing_file: {path}")
        if args.repeats < 1:
            raise ExperimentError("repeats must be >= 1")
        if args.workers < 1:
            raise ExperimentError("workers must be >= 1")
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
        routes = load_routes(args.emergency_routes)
        if args.routes:
            route_ids = args.routes
        elif args.route_set == "b0_valid_18":
            route_ids = load_b0_valid_routes(args.b0_summary)
        else:
            route_ids = sorted(routes)
        missing_routes = [route_id for route_id in route_ids if route_id not in routes]
        if missing_routes:
            raise ExperimentError(f"missing_routes: {','.join(missing_routes)}")
        excluded_routes = set(manifest.get("excluded_routes", ["ER_ACC_013"]) if manifest else ["ER_ACC_013"])
        forbidden = sorted(excluded_routes & set(route_ids))
        if args.route_set == "b0_valid_18" and forbidden:
            raise ExperimentError(f"excluded_routes_present_in_route_set: {','.join(forbidden)}")
        b1_params = b1_parameter_set(args.b1_config)
        b2_params = load_b2_parameter_sets(args.b2_params) if "B2" in args.modes else []
        background_vehicle_count = S14.count_vehicles(args.background_route)
        paths = output_paths(args.output_prefix, args.legacy_output_names)
        base_task = {
            "net": str(args.net),
            "background_route": str(args.background_route),
            "emergency_routes": str(args.emergency_routes),
            "tls_audit": str(args.tls_audit),
            "background_vehicle_count": background_vehicle_count,
            "time_to_teleport": args.time_to_teleport,
            "collision_action": args.collision_action,
            "emergency_depart": args.emergency_depart,
            "timeout_steps": args.timeout_steps,
            "timeout_sec": args.timeout_sec,
            "output_prefix": args.output_prefix,
        }
        tasks = []
        for task in build_tasks(args, route_ids, b1_params, b2_params):
            run_dir = args.run_root / task["mode"] / task["parameter_id"] / task["repeat_id"] / task["route_id"]
            tasks.append({**base_task, **task, "run_dir": str(run_dir)})
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
        compare = compare_rows(result_rows)
        status_counts: dict[str, int] = {}
        for row in result_rows:
            status_counts[row["final_status"]] = status_counts.get(row["final_status"], 0) + 1
        manifest_path = rel(args.manifest) if args.manifest else ""
        emergency_teleport_any = any(bool(row.get("emergency_teleport")) for row in result_rows)
        route_error_count_any = any(int(row.get("route_error_count") or 0) > 0 for row in result_rows)
        summary = {
            "generated_at": generated_at,
            "output_prefix": args.output_prefix,
            "manifest": manifest_path,
            "manifest_path": manifest_path,
            "manifest_schema": manifest.get("schema", "") if manifest else "",
            "git_commit": current_git_commit(),
            "active_net": rel(args.net),
            "background_route": rel(args.background_route),
            "background_vehicle_count": background_vehicle_count,
            "route_set": args.route_set,
            "route_ids": route_ids,
            "excluded_routes": sorted(excluded_routes),
            "modes": args.modes,
            "b2_parameter_ids": [row["parameter_id"] for row in b2_params],
            "repeats": args.repeats,
            "workers": args.workers,
            "task_count": len(tasks),
            "teleport_policy": manifest.get("teleport_policy", {"emergency": "FAIL", "background": "WARNING"}) if manifest else {"emergency": "FAIL", "background": "WARNING"},
            "allow_nonfinal_background": args.allow_nonfinal_background,
            "emergency_teleport_any": emergency_teleport_any,
            "route_error_count_any": route_error_count_any,
            "status_counts": status_counts,
            "final_status": "FAIL" if status_counts.get("FAIL") else "WARNING" if status_counts.get("WARNING") else "PASS",
            "outputs": [rel(paths["summary_csv"]), rel(paths["summary_json"]), rel(paths["events_csv"]), rel(paths["compare_csv"]), rel(LOG_PATH)],
        }
        write_csv(paths["summary_csv"], result_rows)
        write_csv(paths["events_csv"], event_rows)
        write_csv(paths["compare_csv"], compare)
        write_json(paths["summary_json"], {**summary, "results": result_rows, "compare": compare})
        lines.extend([f"status_counts: {status_counts}", f"final_status: {summary['final_status']}", f"summary_json: {rel(paths['summary_json'])}"])
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
