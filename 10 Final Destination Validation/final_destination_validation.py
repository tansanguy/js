#!/usr/bin/env python3
"""Final destination validation runner for Compact V9 B004/B04/B4.

This runner never executes Bayesian optimization.  It reads locked B4
parameters, rebuilds candidate firetruck routes from the Compact V9 fire
station start edge, screens presentation-friendly destinations, and executes
B004/B04/B4 validation repeats.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import random
import shutil
import sys
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / "09 Compact Corridor Baseline"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from common.net_utils import read_sumo_net  # noqa: E402
from b4_runtime import (  # noqa: E402
    B004_MODE,
    B04_MODE,
    B4_MODE,
    B4ThetaParams,
    B4RuntimePhaseConfig,
    B4Stage1Inputs,
    EXPERIMENT_RESULT_FIELDS,
    safe_float,
)
from run_b0_b4_signal_pipeline import (  # noqa: E402
    B4RunTask,
    build_b004_free_reference,
    b004_result_row,
    run_b04_task,
    run_b4_task,
)


DEFAULT_ROUTES_CSV = PROJECT_ROOT / "05_theta_check_simulation/routes/b0_valid_18_routes.csv"
DEFAULT_NET = PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml"
DEFAULT_BACKGROUND_ROUTE = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130_toegye15.rou.xml"
DEFAULT_BASE_STAGE1_DIR = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1_u130_toegye15"
DEFAULT_STRUCTURE_LOCK = PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_signal/u130_toegye15_fixed_param_sensitivity/structure_param_lock_summary.json"
DEFAULT_MAINROAD_MAPPING = PROJECT_ROOT / "data_prepared/compact_v9/map/B04_toegye_segment_edge_mapping.csv"
DEFAULT_OUTPUT_PREFIX = "compact_v9_final_destination_validation"
DEFAULT_RUN_ROOT = PROJECT_ROOT / "runs" / DEFAULT_OUTPUT_PREFIX
DEFAULT_METRICS_ROOT = PROJECT_ROOT / "results/metrics" / DEFAULT_OUTPUT_PREFIX
DEFAULT_SEED = 20260606
DEFAULT_DEPART_MIN = 550.0
DEFAULT_DEPART_MAX = 650.0
DEFAULT_REPEATS = 30
DEFAULT_CANDIDATE_LIMIT = 10
DEFAULT_START_EDGE = "420331801#1"
DEFAULT_HARD_MAX_SIM_TIME = 4000.0
EV_ID = "emergency_0"

RUN_FIELDS = list(dict.fromkeys([
    "candidate_rank",
    "route_id",
    "source_route_id",
    "target_edge_id",
    "selected_policy",
    "mainroad_length_ratio",
    "legacy_spine_length_ratio",
    "emergency_depart",
    *EXPERIMENT_RESULT_FIELDS,
]))

AVERAGE_FIELDS = [
    "mode",
    "run_count",
    "T_EMV_mean_sec",
    "T_EMV_std_sec",
    "d_EMV_mean_sec",
    "objective_score_mean",
    "general_mean_travel_time_sec",
    "emergency_arrival_rate",
    "teleport_count",
    "fail_count",
    "stage3_preemption_mean",
    "stage2_hold_mean",
]

CANDIDATE_FIELDS = [
    "candidate_rank",
    "route_id",
    "source_route_id",
    "target_edge_id",
    "route_edge_count",
    "route_length_m",
    "mainroad_length_ratio",
    "legacy_spine_length_ratio",
    "B004_T_EMV_sec",
    "B04_T_EMV_mean_sec",
    "B4_T_EMV_mean_sec",
    "B04_delay_mean_sec",
    "B4_vs_B04_improvement_sec",
    "B4_stage3_preemption_mean",
    "B4_stage2_hold_mean",
    "arrival_rate_min",
    "teleport_count",
    "fail_count",
    "presentation_fit_score",
    "selection_status",
    "selection_reason",
]

TASK_FIELDS = [
    "candidate_rank",
    "route_id",
    "mode",
    "repeat_id",
    "emergency_depart",
    "run_dir",
    "route_xml",
    "stage1_dir",
]


class FinalDestinationValidationError(RuntimeError):
    """Expected final validation failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_run_id() -> str:
    return "final_destination_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


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


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise FinalDestinationValidationError(f"json_root_not_object:{rel(path)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def bool_cell(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes", "y"}


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


def sec(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


def deterministic_departures(
    *,
    seed: int,
    route_id: str,
    repeats: int,
    depart_min: float,
    depart_max: float,
) -> list[float]:
    departures = []
    for repeat_idx in range(1, repeats + 1):
        key = f"{seed}:{route_id}:repeat_{repeat_idx:03d}".encode("utf-8")
        digest = hashlib.sha256(key).hexdigest()
        rng = random.Random(int(digest[:16], 16))
        departures.append(round(rng.uniform(depart_min, depart_max), 3))
    return departures


def planned_task_rows(candidates: list[dict[str, Any]], departures_by_route: dict[str, list[float]], run_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        route_id = str(candidate["route_id"])
        stage1_dir = rel(Path(candidate["stage1_dir"])) if candidate.get("stage1_dir") else ""
        route_xml = rel(Path(candidate["route_xml"])) if candidate.get("route_xml") else ""
        rows.append({
            "candidate_rank": candidate.get("candidate_rank", ""),
            "route_id": route_id,
            "mode": B004_MODE,
            "repeat_id": "reference",
            "emergency_depart": "",
            "run_dir": rel(run_root / route_id / B004_MODE),
            "route_xml": route_xml,
            "stage1_dir": stage1_dir,
        })
        for repeat_idx, depart in enumerate(departures_by_route[route_id], start=1):
            repeat_text = f"repeat_{repeat_idx:03d}"
            for mode in [B04_MODE, B4_MODE]:
                rows.append({
                    "candidate_rank": candidate.get("candidate_rank", ""),
                    "route_id": route_id,
                    "mode": mode,
                    "repeat_id": repeat_text,
                    "emergency_depart": depart,
                    "run_dir": rel(run_root / route_id / mode / repeat_text),
                    "route_xml": route_xml,
                    "stage1_dir": stage1_dir,
                })
    return rows


def load_locked_b4_params(structure_lock: Path, parameter_id: str = "final_validation_locked_bo_result") -> tuple[B4ThetaParams, dict[str, Any]]:
    payload = read_json(structure_lock)
    decision = payload.get("decision_variables_fixed")
    structure = payload.get("selected_structure")
    if not isinstance(decision, dict):
        raise FinalDestinationValidationError(f"missing_decision_variables_fixed:{rel(structure_lock)}")
    if not isinstance(structure, dict):
        raise FinalDestinationValidationError(f"missing_selected_structure:{rel(structure_lock)}")
    required_decision = {"alpha", "t_lead", "delta_T_thr", "G_ext", "Q_trig"}
    required_structure = {"tau", "hold_max", "d_up", "tau_scale", "tau_numerator_gamma"}
    missing = sorted(required_decision - set(decision))
    if missing:
        raise FinalDestinationValidationError(f"locked_decision_variables_missing:{','.join(missing)}")
    missing_structure = sorted(required_structure - set(structure))
    if missing_structure:
        raise FinalDestinationValidationError(f"locked_structure_variables_missing:{','.join(missing_structure)}")
    params = B4ThetaParams.from_row({"parameter_id": parameter_id, **decision, **structure})
    provenance = {
        "structure_lock_json": rel(structure_lock),
        "lock_status": payload.get("lock_status", ""),
        "decision_variables_fixed": decision,
        "selected_structure": structure,
        "bo_enabled": False,
        "bayesian_optimization_executed_by_final_validation": False,
    }
    return params, provenance


def load_stage1_module() -> Any:
    path = PIPELINE_DIR / "b4_stage1_pipeline.py"
    spec = importlib.util.spec_from_file_location("compact_v9_final_destination_stage1", path)
    if spec is None or spec.loader is None:
        raise FinalDestinationValidationError(f"stage1_module_load_failed:{rel(path)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def mainroad_edge_ids(mapping_csv: Path) -> set[str]:
    if not mapping_csv.is_file():
        return set()
    return {row["edge_id"] for row in read_csv(mapping_csv) if row.get("edge_id")}


def route_length(sumo_net: Any, edge_ids: list[str]) -> float:
    return sum(float(sumo_net.getEdge(edge_id).getLength()) for edge_id in edge_ids)


def shortest_route(sumo_net: Any, start_edge_id: str, target_edge_id: str) -> list[str]:
    start = sumo_net.getEdge(start_edge_id)
    target = sumo_net.getEdge(target_edge_id)
    result = sumo_net.getShortestPath(start, target)
    if not result or result[0] is None:
        return []
    return [edge.getID() for edge in result[0]]


def connected_route(sumo_net: Any, edge_ids: list[str]) -> bool:
    if len(edge_ids) < 2:
        return False
    for from_id, to_id in zip(edge_ids, edge_ids[1:], strict=False):
        try:
            outgoing = {edge.getID() for edge in sumo_net.getEdge(from_id).getOutgoing()}
        except Exception:
            return False
        if to_id not in outgoing:
            return False
    return True


def build_candidate_routes(args: argparse.Namespace, input_root: Path) -> list[dict[str, Any]]:
    sumo_net = read_sumo_net(args.net)
    try:
        sumo_net.getEdge(args.start_edge)
    except Exception as exc:
        raise FinalDestinationValidationError(f"missing_start_edge:{args.start_edge}") from exc
    main_edges = mainroad_edge_ids(args.mainroad_mapping)
    rows = read_csv(args.routes_csv)
    candidates: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for row in rows:
        source_route_id = row.get("route_id", "")
        target_edge = row.get("target_edge_id", "")
        if not source_route_id or not target_edge or target_edge in seen_targets:
            continue
        seen_targets.add(target_edge)
        try:
            sumo_net.getEdge(target_edge)
        except Exception:
            continue
        try:
            edges = shortest_route(sumo_net, args.start_edge, target_edge)
        except Exception:
            continue
        if not connected_route(sumo_net, edges):
            continue
        total_len = route_length(sumo_net, edges)
        main_len = route_length(sumo_net, [edge for edge in edges if edge in main_edges]) if main_edges else 0.0
        legacy_spine = safe_float(row.get("spine_length_ratio"), 0.0)
        compact_ratio = main_len / total_len if total_len > 0 else 0.0
        route_id = f"FINAL_DEST_{source_route_id}"
        candidates.append({
            "route_id": route_id,
            "source_route_id": source_route_id,
            "target_edge_id": target_edge,
            "selected_policy": "compact_v9_shortest_from_fire_station_existing18_target",
            "route_edges": edges,
            "route_edge_count": len(edges),
            "route_length_m": round(total_len, 3),
            "start_edge_id": edges[0],
            "merge_edge_id": edges[1] if len(edges) > 1 else "",
            "mainroad_length_ratio": round(compact_ratio, 6),
            "legacy_spine_length_ratio": round(legacy_spine, 6),
            "review_status": row.get("review_status", ""),
            "route_priority_score": round(0.55 * legacy_spine + 0.45 * compact_ratio, 6),
        })
    candidates.sort(
        key=lambda item: (
            item.get("review_status") not in {"PASS", "WARNING"},
            -safe_float(item.get("route_priority_score")),
            -safe_float(item.get("mainroad_length_ratio")),
            safe_float(item.get("route_length_m")),
            str(item.get("source_route_id")),
        )
    )
    selected = candidates[: args.candidate_limit]
    for rank, candidate in enumerate(selected, start=1):
        candidate["candidate_rank"] = rank
        route_root = input_root / candidate["route_id"]
        candidate["route_csv"] = route_root / "firetruck_route.csv"
        candidate["route_xml"] = route_root / "firetruck_route_depart_600.rou.xml"
        candidate["stage1_dir"] = input_root / "stage1" / candidate["route_id"]
    if not selected:
        raise FinalDestinationValidationError("no_compact_v9_reachable_candidates")
    return selected


def write_firetruck_route_artifacts(candidate: dict[str, Any], route_xml: Path, route_csv: Path, depart: float) -> None:
    route_xml.parent.mkdir(parents=True, exist_ok=True)
    route_csv.parent.mkdir(parents=True, exist_ok=True)
    edges_text = " ".join(candidate["route_edges"])
    row = {
        "route_id": candidate["route_id"],
        "scenario_id": candidate["source_route_id"],
        "target_edge_id": candidate["target_edge_id"],
        "selected_policy": candidate["selected_policy"],
        "route_edges": edges_text,
        "route_edge_count": candidate["route_edge_count"],
        "route_length_m": candidate["route_length_m"],
        "start_edge_id": candidate["start_edge_id"],
        "merge_edge_id": candidate["merge_edge_id"],
        "mainroad_length_ratio": candidate["mainroad_length_ratio"],
        "legacy_spine_length_ratio": candidate["legacy_spine_length_ratio"],
    }
    write_csv(route_csv, [row], [
        "route_id", "scenario_id", "target_edge_id", "selected_policy", "route_edges",
        "route_edge_count", "route_length_m", "start_edge_id", "merge_edge_id",
        "mainroad_length_ratio", "legacy_spine_length_ratio",
    ])
    root = ET.Element("routes")
    ET.SubElement(
        root,
        "vType",
        {
            "id": "firetruck",
            "vClass": "emergency",
            "guiShape": "emergency",
            "color": "1,0,0",
            "length": "8.0",
            "width": "2.5",
            "accel": "1.2",
            "decel": "5.0",
            "maxSpeed": "16.67",
            "speedFactor": "1.05",
            "lcAssertive": "1.0",
            "lcCooperative": "0.7",
            "lcStrategic": "3.0",
            "lcSpeedGain": "1.0",
        },
    )
    ET.SubElement(root, "route", {"id": candidate["route_id"], "edges": edges_text})
    ET.SubElement(
        root,
        "vehicle",
        {
            "id": EV_ID,
            "type": "firetruck",
            "route": candidate["route_id"],
            "depart": f"{depart:g}",
            "departLane": "best",
            "departPos": "0",
            "departSpeed": "max",
        },
    )
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(route_xml, encoding="utf-8", xml_declaration=True)


def project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def configure_stage1_source(stage1_module: Any, args: argparse.Namespace) -> dict[str, Any]:
    summary_path = Path(args.base_stage1_dir) / "b4_stage1_summary.json"
    if not summary_path.is_file():
        raise FinalDestinationValidationError(f"missing_base_stage1_summary:{rel(summary_path)}")
    summary = read_json(summary_path)
    artifacts = summary.get("input_artifacts")
    if not isinstance(artifacts, dict):
        raise FinalDestinationValidationError(f"base_stage1_missing_input_artifacts:{rel(summary_path)}")
    required = ["primary_run_summary", "segment_speed_recall", "b4_queue_measurement_diagnostics"]
    missing = [key for key in required if not artifacts.get(key)]
    if missing:
        raise FinalDestinationValidationError(f"base_stage1_artifacts_missing:{','.join(missing)}")
    primary_candidate = str(summary.get("primary_candidate") or "B04_active_target15")
    stage1_module.B04_NET = Path(args.net)
    stage1_module.B4_PRIMARY_CANDIDATE = primary_candidate
    stage1_module.B4_PRIMARY_RUN_SUMMARY = project_path(str(artifacts["primary_run_summary"]))
    stage1_module.B4_PRIMARY_SPEED_RECALL = project_path(str(artifacts["segment_speed_recall"]))
    stage1_module.B04_MEASUREMENT_DIAGNOSTICS = project_path(str(artifacts["b4_queue_measurement_diagnostics"]))
    stage1_module.STAGE2_MEASUREMENT_SOURCE = f"SUMO_{primary_candidate}_laneData_edgeData_proxy"
    stage1_module.B4_PRIMARY_EDGE_LANE_SOURCE = f"SUMO_{primary_candidate}_edge_lane_data"
    stage1_module.B4_PRIMARY_B0_MEASURED_PROXY = f"SUMO_{primary_candidate}_measured_proxy"
    for key in required:
        path = project_path(str(artifacts[key]))
        if not path.is_file():
            raise FinalDestinationValidationError(f"missing_base_stage1_artifact:{key}:{rel(path)}")
    return {
        "base_stage1_summary": rel(summary_path),
        "primary_candidate": primary_candidate,
        "primary_run_summary": rel(stage1_module.B4_PRIMARY_RUN_SUMMARY),
        "segment_speed_recall": rel(stage1_module.B4_PRIMARY_SPEED_RECALL),
        "queue_measurement_diagnostics": rel(stage1_module.B04_MEASUREMENT_DIAGNOSTICS),
    }


def build_route_stage1(args: argparse.Namespace, candidate: dict[str, Any]) -> dict[str, Any]:
    stage1_module = load_stage1_module()
    source_provenance = configure_stage1_source(stage1_module, args)
    write_firetruck_route_artifacts(candidate, Path(candidate["route_xml"]), Path(candidate["route_csv"]), 600.0)
    summary = stage1_module.build_b4_stage1(
        stage1_dir=Path(candidate["stage1_dir"]),
        firetruck_route_xml=Path(candidate["route_xml"]),
        firetruck_route_csv=Path(candidate["route_csv"]),
        review_html=Path(candidate["stage1_dir"]) / "b4_stage1_review.html",
    )
    summary_path = Path(candidate["stage1_dir"]) / "b4_stage1_summary.json"
    runtime_index_path = Path(candidate["stage1_dir"]) / "b4_runtime_index.json"
    for path in [summary_path, runtime_index_path]:
        payload = read_json(path)
        payload["allow_runtime_input_override"] = True
        payload["runtime_input_provenance"] = {
            **source_provenance,
            "net_file": rel(args.net),
            "background_route": rel(args.background_route),
            "route_xml": rel(Path(candidate["route_xml"])),
            "route_csv": rel(Path(candidate["route_csv"])),
        }
        if path == summary_path:
            artifacts = dict(payload.get("input_artifacts", {}))
            artifacts.update({
                "b04_net": rel(args.net),
                "background_route": rel(args.background_route),
                "firetruck_route_xml": rel(Path(candidate["route_xml"])),
                "firetruck_route_csv": rel(Path(candidate["route_csv"])),
                "primary_run_summary": source_provenance["primary_run_summary"],
                "segment_speed_recall": source_provenance["segment_speed_recall"],
                "b4_queue_measurement_diagnostics": source_provenance["queue_measurement_diagnostics"],
            })
            payload["input_artifacts"] = artifacts
        write_json(path, payload)
    return summary


def phase_for_depart(base_phase: B4RuntimePhaseConfig, depart: float) -> B4RuntimePhaseConfig:
    return replace(
        base_phase,
        ev_departure_policy="deterministic_random_550_650",
        ev_depart_sec=float(depart),
        ev_depart_randomized=True,
        final_validation_random_departure_implemented=True,
        pre_ev_reference_window=(max(0.0, float(depart) - 60.0), float(depart)),
    )


def read_free_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8", newline="") as file:
        return {row["vehicle_id"]: row for row in csv.DictReader(file) if row.get("vehicle_id")}


def enrich_row(row: dict[str, Any], candidate: dict[str, Any], emergency_depart: float | str) -> dict[str, Any]:
    row.update({
        "candidate_rank": candidate.get("candidate_rank", ""),
        "route_id": candidate.get("route_id", ""),
        "source_route_id": candidate.get("source_route_id", ""),
        "target_edge_id": candidate.get("target_edge_id", ""),
        "selected_policy": candidate.get("selected_policy", ""),
        "mainroad_length_ratio": candidate.get("mainroad_length_ratio", ""),
        "legacy_spine_length_ratio": candidate.get("legacy_spine_length_ratio", ""),
        "emergency_depart": emergency_depart,
    })
    return row


def run_candidate(
    args: argparse.Namespace,
    candidate: dict[str, Any],
    departures: list[float],
    params: B4ThetaParams,
    run_root: Path,
    metrics_root: Path,
) -> list[dict[str, Any]]:
    stage1_summary = build_route_stage1(args, candidate)
    base_route_xml = Path(candidate["route_xml"])
    stage1_dir = Path(candidate["stage1_dir"])
    base_phase = B4RuntimePhaseConfig.bo_smoke()
    if args.hard_max_sim_time is not None:
        base_phase = replace(base_phase, hard_max_sim_time=float(args.hard_max_sim_time))
    stage1 = B4Stage1Inputs.load(stage1_dir, route_xml=base_route_xml)
    free_json = metrics_root / candidate["route_id"] / "b004_free_time_reference.json"
    free_vehicle_csv = metrics_root / candidate["route_id"] / "b004_vehicle_free_times.csv"
    free_reference = build_b004_free_reference(
        stage1,
        net_file=args.net,
        background_route=args.background_route,
        firetruck_route=base_route_xml,
        output_json=free_json,
        vehicle_free_times_csv=free_vehicle_csv,
    )
    free_rows_by_id = read_free_rows(free_vehicle_csv)
    rows: list[dict[str, Any]] = []
    b004_task = B4RunTask(
        run_id=args.run_id,
        mode=B004_MODE,
        parameter_id="analytic_50kmh",
        repeat_id=1,
        seed=args.seed,
        run_dir=run_root / candidate["route_id"] / B004_MODE / "reference",
        net_file=args.net,
        background_route=Path(""),
        firetruck_route=base_route_xml,
    )
    rows.append(enrich_row(b004_result_row(b004_task, stage1, free_reference, base_phase), candidate, ""))
    for repeat_idx, depart in enumerate(departures, start=1):
        repeat_route_xml = run_root / candidate["route_id"] / "routes" / f"firetruck_depart_{repeat_idx:03d}.rou.xml"
        write_firetruck_route_artifacts(candidate, repeat_route_xml, Path(candidate["route_csv"]), depart)
        repeat_stage1 = B4Stage1Inputs.load(stage1_dir, route_xml=repeat_route_xml)
        phase_config = phase_for_depart(base_phase, depart)
        for mode in [B04_MODE, B4_MODE]:
            leaf = "no_control" if mode == B04_MODE else params.parameter_id
            task = B4RunTask(
                run_id=args.run_id,
                mode=mode,
                parameter_id=leaf,
                repeat_id=repeat_idx,
                seed=args.seed,
                run_dir=run_root / candidate["route_id"] / mode / leaf / f"repeat_{repeat_idx:03d}",
                net_file=args.net,
                background_route=args.background_route,
                firetruck_route=repeat_route_xml,
            )
            if mode == B04_MODE:
                row = run_b04_task(task, repeat_stage1, phase_config, free_reference, free_rows_by_id, args.sumo_binary, args.emit_fcd)
            else:
                row = run_b4_task(task, repeat_stage1, phase_config, free_reference, free_rows_by_id, args.sumo_binary, args.emit_fcd, params=params)
            rows.append(enrich_row(row, candidate, depart))
    stage1_summary_path = metrics_root / candidate["route_id"] / "stage1_summary_snapshot.json"
    write_json(stage1_summary_path, stage1_summary)
    return rows


def t_emv(row: dict[str, Any]) -> float | None:
    value = row.get("T_actual_EMV_sec")
    if row.get("mode") == B004_MODE and value in {"", None}:
        value = row.get("T_free_EMV_sec")
    if value in {"", None}:
        return None
    return safe_float(value)


def average_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for mode in [B004_MODE, B04_MODE, B4_MODE]:
        group = [row for row in rows if row.get("mode") == mode]
        t_values = [value for value in (t_emv(row) for row in group) if value is not None]
        d_values = [safe_float(row.get("d_EMV_sec")) for row in group if row.get("d_EMV_sec") not in {"", None}]
        objective_values = [safe_float(row.get("objective_score")) for row in group if row.get("objective_score") not in {"", None}]
        general_values = [safe_float(row.get("general_mean_travel_time_sec")) for row in group if row.get("general_mean_travel_time_sec") not in {"", None}]
        stage3_values = [safe_float(row.get("stage3_preemption_count")) for row in group if row.get("stage3_preemption_count") not in {"", None}]
        stage2_values = [safe_float(row.get("stage2_hold_count")) for row in group if row.get("stage2_hold_count") not in {"", None}]
        result.append({
            "mode": mode,
            "run_count": len(group),
            "T_EMV_mean_sec": sec(mean(t_values)),
            "T_EMV_std_sec": sec(sample_std(t_values)) if t_values else "",
            "d_EMV_mean_sec": sec(mean(d_values)),
            "objective_score_mean": sec(mean(objective_values)),
            "general_mean_travel_time_sec": sec(mean(general_values)),
            "emergency_arrival_rate": sec(sum(bool_cell(row.get("emergency_arrived")) for row in group) / len(group)) if group else "",
            "teleport_count": sum(bool_cell(row.get("emergency_teleport")) for row in group),
            "fail_count": sum(row.get("final_status") == "FAIL" or bool_cell(row.get("failed")) for row in group),
            "stage3_preemption_mean": sec(mean(stage3_values)),
            "stage2_hold_mean": sec(mean(stage2_values)),
        })
    return result


def summarize_candidate(candidate: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    averages = {row["mode"]: row for row in average_rows(rows)}
    b004_time = safe_float(averages.get(B004_MODE, {}).get("T_EMV_mean_sec"))
    b04_time = safe_float(averages.get(B04_MODE, {}).get("T_EMV_mean_sec"))
    b4_time = safe_float(averages.get(B4_MODE, {}).get("T_EMV_mean_sec"))
    b04_delay = b04_time - b004_time if b04_time and b004_time else 0.0
    improvement = b04_time - b4_time if b04_time and b4_time else 0.0
    b4_stage3 = safe_float(averages.get(B4_MODE, {}).get("stage3_preemption_mean"))
    b4_stage2 = safe_float(averages.get(B4_MODE, {}).get("stage2_hold_mean"))
    arrival_rates = [safe_float(row.get("emergency_arrival_rate"), 0.0) for row in averages.values() if row.get("emergency_arrival_rate") not in {"", None}]
    teleport_count = sum(int(safe_float(row.get("teleport_count"))) for row in averages.values())
    fail_count = sum(int(safe_float(row.get("fail_count"))) for row in averages.values())
    invalid = teleport_count > 0 or fail_count > 0 or (arrival_rates and min(arrival_rates) < 1.0)
    score = (
        0.45 * max(b04_delay, 0.0)
        + 0.35 * max(improvement, 0.0)
        + 8.0 * b4_stage3
        + 4.0 * b4_stage2
        + 120.0 * safe_float(candidate.get("mainroad_length_ratio"))
    )
    if invalid:
        score -= 1_000_000.0
    return {
        "candidate_rank": candidate.get("candidate_rank", ""),
        "route_id": candidate.get("route_id", ""),
        "source_route_id": candidate.get("source_route_id", ""),
        "target_edge_id": candidate.get("target_edge_id", ""),
        "route_edge_count": candidate.get("route_edge_count", ""),
        "route_length_m": candidate.get("route_length_m", ""),
        "mainroad_length_ratio": candidate.get("mainroad_length_ratio", ""),
        "legacy_spine_length_ratio": candidate.get("legacy_spine_length_ratio", ""),
        "B004_T_EMV_sec": sec(b004_time if b004_time else None),
        "B04_T_EMV_mean_sec": sec(b04_time if b04_time else None),
        "B4_T_EMV_mean_sec": sec(b4_time if b4_time else None),
        "B04_delay_mean_sec": sec(b04_delay),
        "B4_vs_B04_improvement_sec": sec(improvement),
        "B4_stage3_preemption_mean": sec(b4_stage3),
        "B4_stage2_hold_mean": sec(b4_stage2),
        "arrival_rate_min": sec(min(arrival_rates) if arrival_rates else None),
        "teleport_count": teleport_count,
        "fail_count": fail_count,
        "presentation_fit_score": sec(score),
        "selection_status": "EXCLUDED" if invalid else "CANDIDATE",
        "selection_reason": "excluded_due_to_failure_or_teleport" if invalid else "congestion_scene_with_b4_intervention_score",
    }


def select_final_candidate(candidate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in candidate_rows if row.get("selection_status") == "CANDIDATE"]
    if eligible:
        return sorted(eligible, key=lambda row: (-safe_float(row.get("presentation_fit_score")), int(safe_float(row.get("candidate_rank"), 9999))))[0]
    if not candidate_rows:
        raise FinalDestinationValidationError("no_candidate_rows_after_validation")
    fallback = dict(sorted(candidate_rows, key=lambda row: (-safe_float(row.get("presentation_fit_score")), int(safe_float(row.get("candidate_rank"), 9999))))[0])
    fallback["selection_status"] = "FALLBACK_NO_ELIGIBLE"
    fallback["selection_reason"] = "all_candidates_failed_or_teleported; selected_best_available_for_diagnostics"
    return fallback


def validate_args(args: argparse.Namespace) -> None:
    for attr in ["routes_csv", "net", "background_route", "base_stage1_dir", "structure_lock", "mainroad_mapping"]:
        value = Path(getattr(args, attr)).resolve()
        setattr(args, attr, value)
        if attr != "mainroad_mapping" and not value.exists():
            raise FinalDestinationValidationError(f"missing_required_input:{rel(value)}")
    args.run_root = Path(args.run_root).resolve()
    args.metrics_root = Path(args.metrics_root).resolve()
    if args.repeats < 1:
        raise FinalDestinationValidationError("repeats_must_be_positive")
    if args.candidate_limit < 1:
        raise FinalDestinationValidationError("candidate_limit_must_be_positive")
    if args.depart_min > args.depart_max:
        raise FinalDestinationValidationError("depart_min_must_be_lte_depart_max")
    if args.workers != 1:
        raise FinalDestinationValidationError("workers_other_than_1_not_supported_for_traci_final_validation")
    if shutil.which(args.sumo_binary or "sumo") is None:
        raise FinalDestinationValidationError("missing_executable:sumo")
    args.run_id = args.run_id or default_run_id()


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    params, params_provenance = load_locked_b4_params(args.structure_lock)
    output_root = args.metrics_root / args.run_id
    run_root = args.run_root / args.run_id
    input_root = run_root / "inputs"
    candidates = build_candidate_routes(args, input_root)
    departures_by_route = {
        candidate["route_id"]: deterministic_departures(
            seed=args.seed,
            route_id=candidate["route_id"],
            repeats=args.repeats,
            depart_min=args.depart_min,
            depart_max=args.depart_max,
        )
        for candidate in candidates
    }
    task_manifest_rows = planned_task_rows(candidates, departures_by_route, run_root)
    write_csv(output_root / "task_manifest.csv", task_manifest_rows, TASK_FIELDS)
    write_json(
        output_root / "task_manifest.json",
        {
            "schema": "compact_v9_final_destination_validation_task_manifest.v1",
            "generated_at": utc_now(),
            "run_id": args.run_id,
            "candidate_limit": args.candidate_limit,
            "repeats": args.repeats,
            "task_count": len(task_manifest_rows),
            "depart_min": args.depart_min,
            "depart_max": args.depart_max,
            "seed": args.seed,
            "bo_enabled": False,
            "b4_params": params.as_result_fields(),
            "b4_params_provenance": params_provenance,
            "tasks": task_manifest_rows,
        },
    )
    if args.dry_run:
        for candidate in candidates:
            write_firetruck_route_artifacts(candidate, Path(candidate["route_xml"]), Path(candidate["route_csv"]), 600.0)
            Path(candidate["stage1_dir"]).mkdir(parents=True, exist_ok=True)
        candidate_rows = [
            {
                "candidate_rank": candidate["candidate_rank"],
                "route_id": candidate["route_id"],
                "source_route_id": candidate["source_route_id"],
                "target_edge_id": candidate["target_edge_id"],
                "route_edge_count": candidate["route_edge_count"],
                "route_length_m": candidate["route_length_m"],
                "mainroad_length_ratio": candidate["mainroad_length_ratio"],
                "legacy_spine_length_ratio": candidate["legacy_spine_length_ratio"],
                "selection_status": "DRY_RUN",
                "selection_reason": "dry_run_no_sumo_execution",
            }
            for candidate in candidates
        ]
        write_csv(output_root / "candidate_selection.csv", candidate_rows, CANDIDATE_FIELDS)
        result = {
            "schema": "compact_v9_final_destination_validation_dry_run.v1",
            "run_id": args.run_id,
            "outputs": {
                "task_manifest_csv": rel(output_root / "task_manifest.csv"),
                "task_manifest_json": rel(output_root / "task_manifest.json"),
                "candidate_selection_csv": rel(output_root / "candidate_selection.csv"),
            },
        }
        write_json(args.metrics_root / "latest.json", result)
        return result

    all_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    rows_by_route: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        route_rows = run_candidate(args, candidate, departures_by_route[candidate["route_id"]], params, run_root, output_root)
        rows_by_route[candidate["route_id"]] = route_rows
        all_rows.extend(route_rows)
        candidate_rows.append(summarize_candidate(candidate, route_rows))
        write_csv(output_root / candidate["route_id"] / "route_runs.csv", route_rows, RUN_FIELDS)
        write_csv(output_root / candidate["route_id"] / "mode_averages.csv", average_rows(route_rows), AVERAGE_FIELDS)
        write_csv(output_root / "all_route_runs.partial.csv", all_rows, RUN_FIELDS)
        write_csv(output_root / "candidate_selection.partial.csv", candidate_rows, CANDIDATE_FIELDS)
    selected = select_final_candidate(candidate_rows)
    selected_route_id = str(selected["route_id"])
    selected_rows = rows_by_route[selected_route_id]
    selected_averages = average_rows(selected_rows)
    write_csv(output_root / "all_route_runs.csv", all_rows, RUN_FIELDS)
    write_csv(output_root / "candidate_selection.csv", candidate_rows, CANDIDATE_FIELDS)
    write_csv(output_root / "selected_route_runs.csv", selected_rows, RUN_FIELDS)
    write_csv(output_root / "selected_mode_averages.csv", selected_averages, AVERAGE_FIELDS)
    selected_candidate = next(candidate for candidate in candidates if candidate["route_id"] == selected_route_id)
    selected_payload = {
        "schema": "compact_v9_final_destination_validation_selected_destination.v1",
        "generated_at": utc_now(),
        "run_id": args.run_id,
        "selection": selected,
        "route": {
            key: selected_candidate[key]
            for key in [
                "route_id",
                "source_route_id",
                "target_edge_id",
                "selected_policy",
                "route_edge_count",
                "route_length_m",
                "start_edge_id",
                "merge_edge_id",
                "mainroad_length_ratio",
                "legacy_spine_length_ratio",
            ]
        } | {"route_edges": selected_candidate["route_edges"]},
        "departures": departures_by_route[selected_route_id],
        "b4_params": params.as_result_fields(),
        "b4_params_provenance": params_provenance,
        "outputs": {
            "all_route_runs_csv": rel(output_root / "all_route_runs.csv"),
            "candidate_selection_csv": rel(output_root / "candidate_selection.csv"),
            "selected_route_runs_csv": rel(output_root / "selected_route_runs.csv"),
            "selected_mode_averages_csv": rel(output_root / "selected_mode_averages.csv"),
        },
    }
    write_json(output_root / "selected_destination.json", selected_payload)
    result = {
        "schema": "compact_v9_final_destination_validation_run.v1",
        "generated_at": utc_now(),
        "run_id": args.run_id,
        "candidate_count": len(candidates),
        "rows_total": len(all_rows),
        "selected_route_id": selected_route_id,
        "outputs": selected_payload["outputs"] | {
            "selected_destination_json": rel(output_root / "selected_destination.json"),
            "task_manifest_json": rel(output_root / "task_manifest.json"),
        },
    }
    write_json(output_root / "experiment_summary.json", result)
    write_json(args.metrics_root / "latest.json", result)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Compact V9 final destination validation.")
    parser.add_argument("--routes-csv", type=Path, default=DEFAULT_ROUTES_CSV)
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--base-stage1-dir", type=Path, default=DEFAULT_BASE_STAGE1_DIR)
    parser.add_argument("--structure-lock", type=Path, default=DEFAULT_STRUCTURE_LOCK)
    parser.add_argument("--mainroad-mapping", type=Path, default=DEFAULT_MAINROAD_MAPPING)
    parser.add_argument("--start-edge", default=DEFAULT_START_EDGE)
    parser.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--depart-min", type=float, default=DEFAULT_DEPART_MIN)
    parser.add_argument("--depart-max", type=float, default=DEFAULT_DEPART_MAX)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--metrics-root", type=Path, default=DEFAULT_METRICS_ROOT)
    parser.add_argument("--hard-max-sim-time", type=float, default=DEFAULT_HARD_MAX_SIM_TIME)
    parser.add_argument("--sumo-binary", default=None)
    parser.add_argument("--emit-fcd", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_validation(args)
    except (FinalDestinationValidationError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
