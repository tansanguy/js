#!/usr/bin/env python3
"""Fixed-budget B4 optimizer comparison for the S1-forced B04/B4 inputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / "09 Compact Corridor Baseline"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from b4_runtime import (  # noqa: E402
    B4_MODE,
    B4Stage1Inputs,
    B4ThetaParams,
    safe_float,
    theta_bounds_from_stage1,
    write_csv,
)
import run_b4_theta_bo as theta_bo  # noqa: E402


RUNNER_DIR = Path(__file__).resolve().parent
DEFAULT_NET = PIPELINE_DIR / "tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
DEFAULT_BACKGROUND_ROUTE = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml"
DEFAULT_STAGE1_DIR = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1_s1forced"
DEFAULT_ACTIVE_INPUTS = PROJECT_ROOT / "configs/compact_v9_B04_B4_active_inputs.json"
DEFAULT_OUTPUT_DIR = RUNNER_DIR / "outputs"
DEFAULT_RUN_ROOT = PROJECT_ROOT / "runs/compact_v9_B4_optimization_s1forced"
DEFAULT_SEED_BASE = 20260607
DEFAULT_N = 1
DEFAULT_M = 50
DEFAULT_BO_INITIAL = 10
DEFAULT_THETA_PER_ROUND = 6
DEFAULT_WORKERS = 6
FAILURE_PENALTY_SEC = 1_000_000.0
ESSI_ACTIVATION_FLOOR = 0.65
ESSI_BLEND_WEIGHT = 0.05
BO_BATCH_MIN_DISTANCE = 0.055
METHODS = ["Random Search", "CMA-ES", "BO"]
METHOD_ALIASES = {
    "random": "Random Search",
    "random-search": "Random Search",
    "random_search": "Random Search",
    "rs": "Random Search",
    "cma": "CMA-ES",
    "cma-es": "CMA-ES",
    "cma_es": "CMA-ES",
    "bo": "BO",
}
THETA_FIELDS = ["parameter_id", "t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]
ESSI_FIELDS = [
    "essi_acquisition",
    "essi_1",
    "essi_2",
    "essi_3",
    "essi_4",
    "essi_5",
    "essi_6",
    "essi_max",
    "essi_mean",
    "essi_log_max",
    "dominant_essi_subspace",
    "spatial_activation_score",
]
EVALUATION_FIELDS = [
    "method",
    "seed",
    "round",
    "round_theta_index",
    "theta_per_round",
    *THETA_FIELDS,
    "D_E_sec",
    "D_G_sec",
    "score",
    "best_so_far",
    "penalty",
    "final_status",
    "failure_reason",
    "termination_reason",
    "emergency_arrived",
    "emergency_teleport",
    "background_teleported",
    "sumo_summary_teleports",
    "sumo_summary_collisions",
    "signal_event_count",
    "stage2_hold_count",
    "stage3_preemption_count",
    "surrogate_mean",
    "surrogate_ci_low",
    "surrogate_ci_high",
    "raw_ei_acquisition",
    "acquisition",
    "bo_selection_strategy",
    "bo_candidate_source",
    "bo_plateau_mode",
    "bo_batch_slot",
    "hold_feasibility",
    *ESSI_FIELDS,
]
CHECKPOINT_FIELDS = [
    *EVALUATION_FIELDS,
    "raw_score",
    "essi_log_max_ewma",
    "essi_spc_status",
    "essi_stop_recommended",
]
BO_SURROGATE_FIELDS = [
    "method",
    "seed",
    "round",
    "round_theta_index",
    "theta_per_round",
    *THETA_FIELDS,
    "observed_score",
    "best_so_far",
    "surrogate_mean",
    "surrogate_ci_low",
    "surrogate_ci_high",
    "raw_ei_acquisition",
    "acquisition",
    "bo_selection_strategy",
    "bo_candidate_source",
    "bo_plateau_mode",
    *ESSI_FIELDS,
]
BO_GP_SLICE_FIELDS = [
    "row_type",
    "slice_parameter",
    "slice_value",
    *THETA_FIELDS,
    "observed_score",
    "gp_mean",
    "gp_ci_low",
    "gp_ci_high",
    "is_best_observed",
    "note",
]
PARETO_FIELDS = [
    "weight_ratio",
    *THETA_FIELDS,
    "D_E_sec",
    "D_G_sec",
    "score",
    "rounds_completed",
    "essi_max",
    "essi_log_max",
    "essi_log_max_ewma",
    "spc_status",
    "spc_stop_recommended",
    "spc_stop_round",
    "is_knee",
]
SENSITIVITY_SPC_FIELDS = [
    "weight_ratio",
    "round",
    "essi_max",
    "essi_log_max",
    "essi_log_max_ewma",
    "spc_status",
    "spc_stop_recommended",
]
NOISE_FIELDS = ["repeat", *THETA_FIELDS, "D_E_sec", "D_G_sec", "score", "final_status"]
FINAL_RESULT_FIELDS = [
    "input_method",
    "input_seed",
    "input_round",
    "input_parameter_id",
    "input_t_lead",
    "input_delta_T_thr",
    "input_G_ext",
    "input_Q_ratio",
    "input_tau",
    "output_D_E_sec",
    "output_D_G_sec",
    "weight_E",
    "weight_G",
    "weight_ratio",
    "score",
    "measured_T_free_EMV_sec",
    "measured_T_actual_EMV_sec",
    "measured_D_E_sec",
    "measured_D_G_sec",
    "measured_general_mean_travel_time_sec",
    "stage2_on_count",
    "stage3_on_count",
]
PARETO_WEIGHT_RATIOS = ["1:1", "5:1", "10:1", "15:1", "20:1"]


class B4OptimizationError(RuntimeError):
    """Expected optimizer comparison setup or runtime failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_run_id() -> str:
    return "b4_optimization_s1forced_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def normalize_method_name(value: str) -> str:
    cleaned = value.strip()
    if cleaned in METHODS:
        return cleaned
    key = cleaned.lower()
    if key in METHOD_ALIASES:
        return METHOD_ALIASES[key]
    raise B4OptimizationError(f"unknown_method_option:{value}")


def selected_methods(args: argparse.Namespace) -> list[str]:
    raw_methods = args.methods if args.methods else list(METHODS)
    methods: list[str] = []
    for value in raw_methods:
        method = normalize_method_name(value)
        if method not in methods:
            methods.append(method)
    if args.bo_first and "BO" in methods:
        methods = ["BO", *[method for method in methods if method != "BO"]]
    return methods


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise B4OptimizationError(f"json_root_not_object:{path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def validate_active_inputs(args: argparse.Namespace) -> dict[str, Any]:
    if not args.active_inputs.is_file():
        return {"status": "SKIP", "reason": "active_inputs_missing"}
    strict = not (getattr(args, "mock_eval", False) or getattr(args, "collect_visualization_info", False))
    warnings: list[str] = []

    def audit_error(message: str) -> None:
        if strict:
            raise B4OptimizationError(message)
        warnings.append(message)

    active_inputs = read_json(args.active_inputs)
    expected = {
        "net_file": rel(args.net_file),
        "background_route": rel(args.background_route),
        "stage1_dir": rel(args.stage1_dir),
    }
    for key, value in expected.items():
        if active_inputs.get(key) != value:
            audit_error(f"active_inputs_{key}_mismatch:{active_inputs.get(key)} != {value}")
    for key in ["signal_profile_csv", "signal_mapping_csv"]:
        source = active_inputs.get(key)
        if source and not project_path(str(source)).is_file():
            audit_error(f"missing_active_inputs_{key}:{source}")
    hash_audit: dict[str, str] = {}
    for path_key, hash_key in {
        "net_file": "net_file_sha256",
        "background_route": "background_route_sha256",
        "firetruck_route": "firetruck_route_sha256",
    }.items():
        manifest_hash = str(active_inputs.get(hash_key, ""))
        manifest_path = active_inputs.get(path_key)
        if not manifest_hash or not manifest_path:
            continue
        path = project_path(str(manifest_path))
        if not path.is_file():
            audit_error(f"missing_active_inputs_{path_key}:{manifest_path}")
            continue
        actual_hash = sha256_file(path)
        hash_audit[hash_key] = actual_hash
        if actual_hash != manifest_hash:
            audit_error(f"active_inputs_{hash_key}_mismatch:{actual_hash} != {manifest_hash}")
    return {
        "status": "PASS" if not warnings else "WARN",
        "active_inputs": rel(args.active_inputs),
        "hashes": hash_audit,
        "warnings": warnings,
    }


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def method_seed_slug(method: str, seed: int) -> str:
    cleaned = method.lower().replace(" ", "_").replace("-", "_")
    return f"{cleaned}_{seed}"


def checkpoint_path(output_dir: Path, method: str, seed: int) -> Path:
    return output_dir / "checkpoints" / f"{method_seed_slug(method, seed)}.csv"


def evaluation_key(method: str, seed: int, round_index: int, round_theta_index: int, parameter_id: str) -> tuple[str, int, int, int, str]:
    return (method, int(seed), int(round_index), int(round_theta_index), str(parameter_id))


def row_evaluation_key(row: dict[str, Any]) -> tuple[str, int, int, int, str]:
    return evaluation_key(
        str(row.get("method", "")),
        int(safe_float(row.get("seed"), 0.0)),
        int(safe_float(row.get("round"), 0.0)),
        int(safe_float(row.get("round_theta_index"), 1.0)),
        str(row.get("parameter_id", "")),
    )


def dedupe_evaluation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed: dict[tuple[str, int, int, int, str], dict[str, Any]] = {}
    for row in rows:
        if row.get("method") in {"", None} or row.get("parameter_id") in {"", None}:
            continue
        keyed[row_evaluation_key(row)] = dict(row)
    return sorted(
        keyed.values(),
        key=lambda row: (
            METHODS.index(str(row.get("method"))) if row.get("method") in METHODS else len(METHODS),
            int(safe_float(row.get("seed"), 0.0)),
            int(safe_float(row.get("round"), 0.0)),
            int(safe_float(row.get("round_theta_index"), 1.0)),
            str(row.get("parameter_id", "")),
        ),
    )


def write_checkpoint_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    update_best_so_far(rows)
    write_csv(path, [{field: row.get(field, "") for field in CHECKPOINT_FIELDS} for row in rows], CHECKPOINT_FIELDS)


def checkpoint_method_rows(output_dir: Path, method: str, seed: int, rows: list[dict[str, Any]]) -> None:
    write_checkpoint_rows(checkpoint_path(output_dir, method, seed), rows)


def load_task_prior_rows(output_dir: Path, method: str, seed: int) -> list[dict[str, Any]]:
    all_rows = [
        dict(row)
        for row in read_csv_rows(output_dir / "all_evaluations.csv")
        if row.get("method") == method and int(safe_float(row.get("seed"), -1.0)) == seed
    ]
    checkpoint_rows = [dict(row) for row in read_csv_rows(checkpoint_path(output_dir, method, seed))]
    return dedupe_evaluation_rows([*all_rows, *checkpoint_rows])


def load_warm_start_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in getattr(args, "warm_start_csv", []) or []:
        rows.extend(dict(row) for row in read_csv_rows(path))
    return rows


def warm_start_observations(args: argparse.Namespace) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen: set[tuple[float, float, float, float, float]] = set()
    for row in load_warm_start_rows(args):
        if any(row.get(field, "") in {"", None} for field in THETA_FIELDS):
            continue
        observation = bo_learning_observation(row)
        if observation is None:
            continue
        key = theta_key(observation)
        if key in seen:
            continue
        seen.add(key)
        observations.append(observation)
    return observations


def existing_rows_outside_methods(output_dir: Path, methods_to_run: list[str]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in read_csv_rows(output_dir / "all_evaluations.csv")
        if str(row.get("method", "")) not in set(methods_to_run)
    ]


def sec(value: Any) -> str:
    if value in {"", None}:
        return ""
    return f"{float(value):.2f}"


def bool_cell(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def sumo_summary_safety_failed(row: dict[str, Any]) -> bool:
    return (
        safe_float(row.get("sumo_summary_teleports"), 0.0) > 0.0
        or safe_float(row.get("sumo_summary_collisions"), 0.0) > 0.0
    )


def theta_key(row: dict[str, Any]) -> tuple[float, float, float, float, float]:
    return theta_bo.theta_key(row)


def vector_from_theta(theta: dict[str, Any], bounds: dict[str, Any]) -> list[float]:
    vector: list[float] = []
    for field in ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]:
        lower = safe_float(bounds[field]["lower"])
        upper = safe_float(bounds[field]["upper"])
        vector.append((safe_float(theta.get(field), lower) - lower) / max(upper - lower, 1.0))
    return vector


def theta_from_vector(vector: list[float], bounds: dict[str, Any], parameter_id: str) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for index, field in enumerate(["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]):
        lower = safe_float(bounds[field]["lower"])
        upper = safe_float(bounds[field]["upper"])
        raw[field] = lower + max(0.0, min(1.0, vector[index])) * (upper - lower)
    return theta_bo.clamp_theta(raw, bounds, parameter_id=parameter_id)


def normalized_theta_value(theta: dict[str, Any], bounds: dict[str, Any], field: str) -> float:
    lower = safe_float(bounds[field]["lower"])
    upper = safe_float(bounds[field]["upper"])
    return max(0.0, min(1.0, (safe_float(theta.get(field), lower) - lower) / max(upper - lower, 1.0e-9)))


def bo_spatial_subspaces(stage1: B4Stage1Inputs) -> list[dict[str, Any]]:
    subspaces = theta_bo.default_route_subspaces(stage1)
    normalized: list[dict[str, Any]] = []
    max_weight = max((safe_float(row.get("weight"), 0.0) for row in subspaces), default=1.0)
    for index, row in enumerate(subspaces[: theta_bo.DEFAULT_SUBSPACE_COUNT], start=1):
        weight = safe_float(row.get("weight"), 0.0) / max(max_weight, 1.0e-9)
        movement_count = int(safe_float(row.get("movement_count"), 0.0))
        case_b_count = int(safe_float(row.get("case_b_count"), 0.0))
        normalized.append({
            "subspace": index,
            "route_order_min": row.get("route_order_min", ""),
            "route_order_max": row.get("route_order_max", ""),
            "movement_count": movement_count,
            "case_b_count": case_b_count,
            "has_control_candidate": movement_count > 0,
            "has_bottleneck_candidate": case_b_count > 0,
            "weight": round(max(0.0, min(1.0, weight)), 6),
        })
    while len(normalized) < theta_bo.DEFAULT_SUBSPACE_COUNT:
        index = len(normalized) + 1
        normalized.append({
            "subspace": index,
            "route_order_min": "",
            "route_order_max": "",
            "movement_count": 0,
            "case_b_count": 0,
            "has_control_candidate": False,
            "has_bottleneck_candidate": False,
            "weight": 0.0,
        })
    return normalized


def essi_activation_values(theta: dict[str, Any], bounds: dict[str, Any], subspaces: list[dict[str, Any]]) -> list[float]:
    lead = normalized_theta_value(theta, bounds, "t_lead")
    green = normalized_theta_value(theta, bounds, "G_ext")
    queue = normalized_theta_value(theta, bounds, "Q_ratio")
    tau = normalized_theta_value(theta, bounds, "tau")
    delta_urgency = 1.0 - normalized_theta_value(theta, bounds, "delta_T_thr")
    control_intensity = max(0.0, min(1.0, 0.25 * lead + 0.25 * green + 0.20 * queue + 0.20 * tau + 0.10 * delta_urgency))
    bottleneck_pressure = max(0.0, min(1.0, 0.55 * queue + 0.45 * tau))
    values: list[float] = []
    denom = max(len(subspaces) - 1, 1)
    for index, subspace in enumerate(subspaces):
        route_position = index / denom
        route_bias = (1.0 - route_position) * lead + route_position * bottleneck_pressure
        candidate_bonus = 0.10 if subspace.get("has_control_candidate") else 0.0
        bottleneck_bonus = 0.15 if subspace.get("has_bottleneck_candidate") else 0.0
        activation = safe_float(subspace.get("weight")) * (0.65 * control_intensity + 0.25 * route_bias + candidate_bonus + bottleneck_bonus)
        values.append(max(0.0, min(1.0, activation)))
    return values[: theta_bo.DEFAULT_SUBSPACE_COUNT]


def essi_fields_for_candidate(
    theta: dict[str, Any],
    bounds: dict[str, Any],
    subspaces: list[dict[str, Any]],
    gp_improvement: float,
    selection_improvement: float | None = None,
) -> dict[str, Any]:
    activations = essi_activation_values(theta, bounds, subspaces)
    while len(activations) < theta_bo.DEFAULT_SUBSPACE_COUNT:
        activations.append(0.0)
    raw_improvement = max(0.0, gp_improvement)
    selection_base = max(0.0, selection_improvement if selection_improvement is not None else gp_improvement)
    essi_values = [selection_base * value for value in activations[: theta_bo.DEFAULT_SUBSPACE_COUNT]]
    spatial_activation = max(activations) if activations else 0.0
    dominant_index = (max(range(len(activations)), key=lambda idx: activations[idx]) + 1) if activations else ""
    essi_max = max(essi_values) if essi_values else 0.0
    essi_mean = sum(essi_values) / len(essi_values) if essi_values else 0.0
    selection_acquisition = selection_base * (
        (1.0 - ESSI_BLEND_WEIGHT) + ESSI_BLEND_WEIGHT * (ESSI_ACTIVATION_FLOOR + (1.0 - ESSI_ACTIVATION_FLOOR) * spatial_activation)
    )
    fields: dict[str, Any] = {
        "raw_ei_acquisition": sec(raw_improvement),
        "acquisition": sec(selection_acquisition),
        "essi_acquisition": sec(essi_max),
        "essi_max": sec(essi_max),
        "essi_mean": sec(essi_mean),
        "essi_log_max": f"{math.log(essi_max + theta_bo.ESSI_EPS):.8f}",
        "dominant_essi_subspace": dominant_index,
        "spatial_activation_score": f"{spatial_activation:.6f}",
        "_essi_acquisition_value": selection_acquisition,
    }
    for index, value in enumerate(essi_values[: theta_bo.DEFAULT_SUBSPACE_COUNT], start=1):
        fields[f"essi_{index}"] = sec(value)
    return fields


def essi_improvement_candidates(
    observations: list[dict[str, Any]],
    bounds: dict[str, Any],
    stage1: B4Stage1Inputs,
    seed: int,
    existing: set[tuple[float, float, float, float, float]],
    candidate_count: int,
) -> list[dict[str, Any]]:
    ranked = theta_bo.expected_improvement_candidates(observations, bounds, seed, existing, candidate_count)
    if not ranked:
        raise B4OptimizationError("gp_essi_unavailable:no_candidates")
    subspaces = bo_spatial_subspaces(stage1)
    out: list[dict[str, Any]] = []
    seen: set[tuple[float, float, float, float, float]] = set(existing)
    for item in ranked:
        theta = theta_bo.clamp_theta(item, bounds)
        key = theta_key(theta)
        if key in seen:
            continue
        seen.add(key)
        raw_gp_improvement = safe_float(item.get("raw_acquisition"), safe_float(item.get("acquisition"), 0.0))
        selection_improvement = safe_float(item.get("acquisition"), raw_gp_improvement)
        essi = essi_fields_for_candidate(theta, bounds, subspaces, raw_gp_improvement, selection_improvement)
        out.append({
            **theta,
            "bo_selection_strategy": item.get("bo_selection_strategy", "ei"),
            "bo_candidate_source": item.get("bo_candidate_source", ""),
            "bo_plateau_mode": item.get("bo_plateau_mode", ""),
            "bo_batch_slot": item.get("bo_batch_slot", ""),
            "hold_feasibility": item.get("hold_feasibility", ""),
            "surrogate_acquisition": item.get("surrogate_acquisition", ""),
            **essi,
        })
    if not out:
        raise B4OptimizationError("gp_essi_unavailable:no_unique_candidates")
    out.sort(key=lambda row: (-safe_float(row.get("_essi_acquisition_value")), theta_key(row)))
    return out


def theta_distance(left: dict[str, Any], right: dict[str, Any], bounds: dict[str, Any]) -> float:
    left_vector = theta_bo.theta_feature_vector(left, bounds)
    right_vector = theta_bo.theta_feature_vector(right, bounds)
    return math.sqrt(sum((lhs - rhs) ** 2 for lhs, rhs in zip(left_vector, right_vector)))


def diverse_bo_batch(
    ranked: list[dict[str, Any]],
    bounds: dict[str, Any],
    batch_size: int,
    min_distance: float = BO_BATCH_MIN_DISTANCE,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[float, float, float, float, float]] = set()

    def slot_row(row: dict[str, Any], slot: str) -> dict[str, Any]:
        out = dict(row)
        out["bo_batch_slot"] = slot
        return out

    def append_ranked(predicate: Any, quota: int, slot: str, threshold: float = 0.0) -> None:
        if quota <= 0 or len(selected) >= batch_size:
            return
        thresholds = [threshold, threshold * 0.65, threshold * 0.35, 0.0] if threshold > 0.0 else [0.0]
        for current_threshold in thresholds:
            for row in ranked:
                if not predicate(row):
                    continue
                key = theta_key(row)
                if key in seen:
                    continue
                if current_threshold > 0.0 and any(theta_distance(row, item, bounds) < current_threshold for item in selected):
                    continue
                selected.append(slot_row(row, slot))
                seen.add(key)
                if len(selected) >= batch_size or sum(1 for item in selected if item.get("bo_batch_slot") == slot) >= quota:
                    return

    def append_space_filling(quota: int) -> None:
        for _index in range(max(0, quota)):
            remaining = [row for row in ranked if theta_key(row) not in seen]
            if not remaining or len(selected) >= batch_size:
                return
            if not selected:
                choice = remaining[0]
            else:
                choice = max(
                    remaining,
                    key=lambda row: (
                        min(theta_distance(row, item, bounds) for item in selected),
                        safe_float(row.get("_essi_acquisition_value"), safe_float(row.get("acquisition"), 0.0)),
                    ),
                )
            selected.append(slot_row(choice, "space_filling"))
            seen.add(theta_key(choice))

    if batch_size >= 6:
        stable_quota, local_quota, global_quota = 2, 2, 1
    elif batch_size >= 4:
        stable_quota, local_quota, global_quota = 1, 2, 1
    elif batch_size >= 3:
        stable_quota, local_quota, global_quota = 1, 1, 1
    else:
        stable_quota, local_quota, global_quota = 0, 0, 0

    append_ranked(
        lambda row: row.get("bo_selection_strategy") in {"incumbent_exploitation", "stable_success_lattice"}
        or row.get("bo_candidate_source") == "stable_success",
        stable_quota,
        "stable",
        threshold=min_distance * 0.35,
    )
    append_ranked(
        lambda row: row.get("bo_candidate_source") in {"trust_region", "local"},
        local_quota,
        "local_constrained",
        threshold=min_distance,
    )
    append_ranked(
        lambda row: row.get("bo_candidate_source") == "global",
        global_quota,
        "global_ei",
        threshold=min_distance,
    )
    append_space_filling(batch_size - len(selected) if batch_size >= 6 else 0)
    if len(selected) >= batch_size:
        return selected[:batch_size]

    thresholds = [min_distance, min_distance * 0.65, min_distance * 0.35, 0.0]
    for threshold in thresholds:
        for row in ranked:
            key = theta_key(row)
            if key in seen:
                continue
            if threshold > 0.0 and any(theta_distance(row, item, bounds) < threshold for item in selected):
                continue
            selected.append(slot_row(row, row.get("bo_batch_slot", "") or "ranked_fallback"))
            seen.add(key)
            if len(selected) >= batch_size:
                return selected
    return selected[:batch_size]


def pass_focus_bo_batch(
    observations: list[dict[str, Any]],
    bounds: dict[str, Any],
    batch_size: int,
    seed: int,
    existing: set[tuple[float, float, float, float, float]],
    min_feasibility: float = 0.70,
) -> list[dict[str, Any]]:
    aggregated = theta_bo.aggregate_observations(observations)
    successes = theta_bo.successful_bo_observations(aggregated)
    if batch_size <= 0 or not successes:
        return []

    candidate_pool: list[tuple[dict[str, Any], str, int]] = []
    stable_count = max(batch_size * 24, 60)
    local_count = max(batch_size * 12, 36)
    stable = theta_bo.stable_success_lattice_candidates(bounds, aggregated, stable_count, existing)
    candidate_pool.extend((row, "pass_focus_stable", 0) for row in stable)

    used = set(existing)
    used.update(theta_key(row) for row in stable)
    trust = theta_bo.trust_region_theta_candidates(bounds, successes, local_count, seed + 17, used)
    candidate_pool.extend((row, "pass_focus_trust", 1) for row in trust)

    used.update(theta_key(row) for row in trust)
    local = theta_bo.local_theta_candidates(bounds, successes, local_count, seed + 29, used)
    candidate_pool.extend((row, "pass_focus_local", 2) for row in local)

    def nearest_success(candidate: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        nearest = min(
            successes,
            key=lambda row: (
                theta_bo.nearest_theta_distance(candidate, [row], bounds),
                safe_float(row.get("bo_score_sec"), float("inf")),
            ),
        )
        return theta_bo.nearest_theta_distance(candidate, [nearest], bounds), nearest

    selected: list[dict[str, Any]] = []
    selected_keys = set(existing)
    thresholds = [max(0.0, min_feasibility), max(0.0, min_feasibility * 0.8), 0.0]
    for threshold in thresholds:
        ranked: list[tuple[float, dict[str, Any]]] = []
        for raw, source, source_rank in candidate_pool:
            candidate = theta_bo.clamp_theta(raw, bounds)
            key = theta_key(candidate)
            if key in selected_keys:
                continue
            safety = theta_bo.safety_feasibility_multiplier(candidate, aggregated, bounds)
            hold = theta_bo.hold_feasibility_multiplier(candidate, aggregated, bounds)
            feasibility = min(safety, hold)
            if feasibility < threshold:
                continue
            distance, anchor = nearest_success(candidate)
            anchor_score = safe_float(anchor.get("bo_score_sec"), float("inf"))
            acquisition = max(0.0, 1_000.0 - anchor_score - distance * 100.0 + feasibility * 10.0)
            row = {
                **candidate,
                "raw_ei_acquisition": sec(acquisition),
                "acquisition": sec(acquisition),
                "bo_selection_strategy": "pass_focus_success_lattice",
                "bo_candidate_source": source,
                "bo_plateau_mode": "True",
                "bo_batch_slot": "pass_focus",
                "hold_feasibility": f"{feasibility:.6f}",
            }
            ranked.append((source_rank * 10_000.0 + anchor_score + distance * 100.0 - feasibility, row))
        for _rank_key, row in sorted(ranked, key=lambda item: (item[0], theta_key(item[1]))):
            key = theta_key(row)
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
            if len(selected) >= batch_size:
                return selected
    return selected


def normalize_objective_weights(w_E: float, w_G: float) -> tuple[float, float]:
    total = float(w_E) + float(w_G)
    if total <= 0.0:
        raise B4OptimizationError("objective_weight_sum_must_be_positive")
    return float(w_E) / total, float(w_G) / total


def score_delay_row(row: dict[str, Any], w_E: float, w_G: float) -> tuple[float, float, float, float, float]:
    D_E_sec = safe_float(row.get("D_E_sec"), safe_float(row.get("T_actual_EMV_sec"), 0.0))
    D_G_sec = safe_float(row.get("D_G_sec"))
    w_E_norm, w_G_norm = normalize_objective_weights(w_E, w_G)
    score = w_E_norm * D_E_sec + w_G_norm * D_G_sec
    failed = (
        row.get("final_status") not in {"PASS", "WARNING"}
        or not bool_cell(row.get("emergency_arrived"))
        or bool_cell(row.get("emergency_teleport"))
        or bool_cell(row.get("failed"))
        or sumo_summary_safety_failed(row)
    )
    penalty = FAILURE_PENALTY_SEC if failed else 0.0
    return round(D_E_sec, 6), round(D_G_sec, 6), round(score, 6), penalty, round(score + penalty, 6)


def weight_ratio_label(w_E: float, w_G: float) -> str:
    return f"{w_E:g}:{w_G:g}"


def clean_final_result_row(row: dict[str, Any], w_E: float, w_G: float, weight_ratio: str | None = None) -> dict[str, Any]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    source = raw if raw else row
    label = weight_ratio or weight_ratio_label(w_E, w_G)
    w_E_norm, w_G_norm = normalize_objective_weights(w_E, w_G)
    return {
        "input_method": row.get("method", ""),
        "input_seed": row.get("seed", ""),
        "input_round": row.get("round", ""),
        "input_parameter_id": row.get("parameter_id", ""),
        "input_t_lead": row.get("t_lead", ""),
        "input_delta_T_thr": row.get("delta_T_thr", ""),
        "input_G_ext": row.get("G_ext", ""),
        "input_Q_ratio": row.get("Q_ratio", ""),
        "input_tau": row.get("tau", ""),
        "output_D_E_sec": row.get("D_E_sec", ""),
        "output_D_G_sec": row.get("D_G_sec", ""),
        "weight_E": sec(w_E_norm),
        "weight_G": sec(w_G_norm),
        "weight_ratio": label,
        "score": row.get("raw_score", row.get("score", "")),
        "measured_T_free_EMV_sec": source.get("T_free_EMV_sec", ""),
        "measured_T_actual_EMV_sec": source.get("T_actual_EMV_sec", ""),
        "measured_D_E_sec": source.get("D_E_sec", row.get("D_E_sec", "")),
        "measured_D_G_sec": source.get("D_G_sec", row.get("D_G_sec", "")),
        "measured_general_mean_travel_time_sec": source.get("general_mean_travel_time_sec", ""),
        "stage2_on_count": source.get("stage2_hold_count", row.get("stage2_hold_count", "")),
        "stage3_on_count": source.get("stage3_preemption_count", row.get("stage3_preemption_count", "")),
    }


def existing_method_rows(
    output_dir: Path,
    methods_to_run: list[str],
    append_existing: bool,
    w_E: float,
    w_G: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not append_existing:
        return [], []
    evaluations_path = output_dir / "all_evaluations.csv"
    if not evaluations_path.is_file():
        raise B4OptimizationError(f"append_existing_missing_all_evaluations:{rel(evaluations_path)}")
    existing_rows: list[dict[str, Any]] = [dict(row) for row in read_csv_rows(evaluations_path)]
    existing_methods = {str(row.get("method", "")) for row in existing_rows}
    duplicate_methods = sorted(existing_methods & set(methods_to_run))
    if duplicate_methods:
        raise B4OptimizationError(f"append_existing_method_already_present:{','.join(duplicate_methods)}")
    final_path = output_dir / "final_method_comparison_results.csv"
    final_rows: list[dict[str, Any]] = [dict(row) for row in read_csv_rows(final_path)] if final_path.is_file() else []
    if not final_rows:
        final_rows = [clean_final_result_row(row, w_E, w_G) for row in existing_rows]
    return existing_rows, final_rows


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    for path, label in [
        (args.net_file, "net_file"),
        (args.background_route, "background_route"),
        (args.stage1_dir, "stage1_dir"),
    ]:
        if not path.exists():
            raise B4OptimizationError(f"missing_{label}:{path}")

    stage1 = B4Stage1Inputs.load(args.stage1_dir)
    bounds = theta_bounds_from_stage1(stage1, args.net_file)
    if bounds.get("decision_variables") != ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]:
        raise B4OptimizationError("unexpected_theta_decision_variables")

    runtime_index = read_json(args.stage1_dir / "b4_runtime_index.json")
    if runtime_index.get("decision_variables") != ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]:
        raise B4OptimizationError("stage1_runtime_index_not_current_five_variable_schema")

    active_inputs = read_json(args.active_inputs) if args.active_inputs.is_file() else {}
    active_inputs_audit = validate_active_inputs(args)

    return {
        "stage1": stage1,
        "bounds": bounds,
        "active_inputs": active_inputs,
        "active_inputs_audit": active_inputs_audit,
    }


def build_eval_args(args: argparse.Namespace, seed: int, run_dir: Path, metrics_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        mock_eval=args.mock_eval,
        seed=seed,
        repeats=1,
        workers=args.workers,
        run_root=run_dir,
        metrics_root=metrics_dir,
        net_file=args.net_file,
        background_route=args.background_route,
        stage1_dir=args.stage1_dir,
        phase=args.phase,
        hard_max_sim_time=args.hard_max_sim_time,
        sumo_binary=args.sumo_binary,
        emit_fcd=args.emit_fcd,
        emit_tls_states=args.emit_tls_states,
        resume=False,
        hold_max=B4ThetaParams.hold_max,
        d_up=B4ThetaParams.d_up,
        w_E=args.w_E,
        w_G=args.w_G,
        ei_candidate_count=args.ei_candidate_count,
        spc_alpha=args.spc_alpha,
        spc_window=args.spc_window,
        spc_min_rounds=args.spc_min_rounds,
        spc_min_improvement_sec=args.spc_min_improvement_sec,
    )


def update_spc_row(row: dict[str, Any], round_rows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    if row.get("essi_log_max") in {"", None}:
        row["essi_log_max_ewma"] = ""
        row["essi_spc_status"] = ""
        row["essi_stop_recommended"] = "False"
        return
    essi_log_max = safe_float(row.get("essi_log_max"))
    previous_ewma = next(
        (
            safe_float(item.get("essi_log_max_ewma"))
            for item in reversed(round_rows)
            if item.get("essi_log_max_ewma") not in {"", None}
        ),
        essi_log_max,
    )
    ewma = args.spc_alpha * essi_log_max + (1.0 - args.spc_alpha) * previous_ewma
    prior_logs = [
        safe_float(item.get("essi_log_max"))
        for item in round_rows
        if item.get("essi_log_max") not in {"", None}
    ]
    window_logs = (prior_logs + [essi_log_max])[-args.spc_window :]
    bo_round_count = len(prior_logs) + 1
    if len(window_logs) < args.spc_window or bo_round_count < args.spc_min_rounds:
        status = "warmup"
        stop_recommended = False
    else:
        center = sum(window_logs) / len(window_logs)
        variance = sum((value - center) ** 2 for value in window_logs) / max(len(window_logs) - 1, 1)
        sigma = math.sqrt(max(variance, 0.0))
        status = "stable" if center - 3.0 * sigma <= ewma <= center + 3.0 * sigma else "active"
        stop_recommended = status == "stable"
    row["essi_log_max_ewma"] = f"{ewma:.8f}"
    row["essi_spc_status"] = status
    row["essi_stop_recommended"] = str(stop_recommended)


def prepare_real_context_once(run_id: str, eval_args: argparse.Namespace) -> dict[str, Any] | None:
    if eval_args.mock_eval:
        return None
    eval_args.allow_baseline_speed_out_of_target = True
    return theta_bo.prepare_real_context(run_id, eval_args)


def evaluate_theta(
    run_id: str,
    method: str,
    seed: int,
    round_index: int,
    theta: dict[str, Any],
    args: argparse.Namespace,
    eval_args: argparse.Namespace,
    real_context: dict[str, Any] | None,
    w_E: float,
    w_G: float,
    repeat_id: int = 1,
) -> dict[str, Any]:
    job = {
        "run_id": run_id,
        "theta": theta,
        "seed": seed,
        "repeat": repeat_id,
        "args": eval_args,
        "real_context": real_context,
    }
    try:
        raw = theta_bo.evaluate_theta_repeat(job)
    except Exception as exc:  # noqa: BLE001
        raw = theta_bo.failure_row_for_worker(run_id, theta, seed, repeat_id, exc)
    D_E_sec, D_G_sec, score, penalty, penalized_score = score_delay_row(raw, w_E, w_G)
    return {
        "method": method,
        "seed": seed,
        "round": round_index,
        **{field: theta.get(field, "") for field in THETA_FIELDS},
        "D_E_sec": sec(D_E_sec),
        "D_G_sec": sec(D_G_sec),
        "score": sec(penalized_score),
        "raw_score": sec(score),
        "best_so_far": "",
        "penalty": sec(penalty),
        "final_status": raw.get("final_status", ""),
        "failure_reason": raw.get("failure_reason", ""),
        "termination_reason": raw.get("termination_reason", ""),
        "emergency_arrived": raw.get("emergency_arrived", ""),
        "emergency_teleport": raw.get("emergency_teleport", ""),
        "background_teleported": raw.get("background_teleported", ""),
        "sumo_summary_teleports": raw.get("sumo_summary_teleports", ""),
        "sumo_summary_collisions": raw.get("sumo_summary_collisions", ""),
        "signal_event_count": raw.get("signal_event_count", ""),
        "stage2_hold_count": raw.get("stage2_hold_count", ""),
        "stage3_preemption_count": raw.get("stage3_preemption_count", ""),
        "raw": raw,
    }


def materialize_new_row_worker(job: dict[str, Any]) -> dict[str, Any]:
    row = evaluate_theta(
        str(job["run_id"]),
        str(job["method"]),
        int(job["seed"]),
        int(job["round_index"]),
        dict(job["theta"]),
        job["args"],
        job["eval_args"],
        job["real_context"],
        float(job["w_E"]),
        float(job["w_G"]),
    )
    row["round_theta_index"] = int(job["round_theta_index"])
    row["theta_per_round"] = int(job["theta_per_round"])
    row.update(dict(job.get("prediction") or {}))
    bo_fields = dict(job.get("bo_fields") or {})
    if bo_fields:
        row.update(bo_fields)
        row["acquisition"] = bo_fields.get("acquisition", bo_fields.get("essi_acquisition", ""))
    else:
        row["acquisition"] = ""
    return row


def surrogate_prediction(observations: list[dict[str, Any]], theta: dict[str, Any], bounds: dict[str, Any]) -> dict[str, str]:
    if len(observations) < 2:
        return {"surrogate_mean": "", "surrogate_ci_low": "", "surrogate_ci_high": ""}
    try:
        import numpy as np  # type: ignore
        from sklearn.gaussian_process import GaussianProcessRegressor  # type: ignore
        from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel  # type: ignore
    except Exception:
        return {"surrogate_mean": "", "surrogate_ci_low": "", "surrogate_ci_high": ""}

    x_train = np.array([vector_from_theta(row, bounds) for row in observations], dtype=float)
    y_train = np.array([safe_float(row.get("score")) for row in observations], dtype=float)
    kernel = ConstantKernel(1.0, constant_value_bounds="fixed") * Matern(
        length_scale=[0.35] * x_train.shape[1],
        length_scale_bounds=theta_bo.GP_LENGTH_SCALE_BOUNDS,
        nu=2.5,
    ) + WhiteKernel(noise_level=theta_bo.GP_NOISE_LEVEL, noise_level_bounds="fixed")
    try:
        gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, random_state=0, alpha=theta_bo.GP_NOISE_LEVEL, n_restarts_optimizer=0)
        gp.fit(x_train, y_train)
        mu, std = gp.predict(np.array([vector_from_theta(theta, bounds)], dtype=float), return_std=True)
    except Exception:
        return {"surrogate_mean": "", "surrogate_ci_low": "", "surrogate_ci_high": ""}
    mean = float(mu[0])
    half = 1.96 * float(std[0])
    return {
        "surrogate_mean": sec(mean),
        "surrogate_ci_low": sec(mean - half),
        "surrogate_ci_high": sec(mean + half),
    }


def bo_learning_observation(row: dict[str, Any]) -> dict[str, Any] | None:
    score = safe_float(row.get("score"), float("inf"))
    if not math.isfinite(score):
        return None
    failed = (
        row.get("final_status") not in {"PASS", "WARNING"}
        or not bool_cell(row.get("emergency_arrived"))
        or bool_cell(row.get("emergency_teleport"))
        or sumo_summary_safety_failed(row)
        or score >= FAILURE_PENALTY_SEC
    )
    return {
        "mode": B4_MODE,
        "round": row.get("round", ""),
        **{field: row.get(field, "") for field in THETA_FIELDS},
        "score_sec": row["score"],
        "bo_score_sec": row["score"],
        "score": row["score"],
        "D_G_sec": row.get("D_G_sec", ""),
        "stage2_hold_count": row.get("stage2_hold_count", ""),
        "final_status": row.get("final_status", ""),
        "failure_reason": row.get("failure_reason", ""),
        "bo_failed": str(failed),
    }


def update_best_so_far(rows: list[dict[str, Any]]) -> None:
    best = float("inf")
    for row in rows:
        best = min(best, safe_float(row.get("score"), float("inf")))
        row["best_so_far"] = sec(best)


def random_search_thetas(bounds: dict[str, Any], m: int, theta_per_round: int, seed: int) -> list[dict[str, Any]]:
    rows = theta_bo.random_theta_samples(bounds, m * theta_per_round, seed, "rs")
    for index, row in enumerate(rows, start=1):
        round_index = ((index - 1) // theta_per_round) + 1
        round_theta_index = ((index - 1) % theta_per_round) + 1
        row["parameter_id"] = theta_bo.theta_id(f"rs_r{round_index:02d}", round_theta_index, row)
    return rows


def cma_es_thetas(
    bounds: dict[str, Any],
    m: int,
    theta_per_round: int,
    seed: int,
    evaluate_batch: Any,
) -> list[dict[str, Any]]:
    try:
        import cma  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise B4OptimizationError("cma_package_required_for_cma_es_method") from exc

    dim = 5
    rng = random.Random(seed)
    options = {
        "bounds": [0.0, 1.0],
        "seed": seed,
        "popsize": max(theta_per_round, min(8, max(4, dim + 1))),
        "verbose": -9,
    }
    strategy = cma.CMAEvolutionStrategy([0.5] * dim, 0.32, options)
    selected: set[tuple[float, float, float, float, float]] = set()
    rows: list[dict[str, Any]] = []
    eval_index = 1
    total_evaluations = m * theta_per_round
    while eval_index <= total_evaluations:
        generation_items: list[tuple[dict[str, Any], int, int, list[float]]] = []
        generation_vectors = strategy.ask()
        for raw_vector in generation_vectors:
            if eval_index > total_evaluations:
                break
            round_index = ((eval_index - 1) // theta_per_round) + 1
            round_theta_index = ((eval_index - 1) % theta_per_round) + 1
            vector = [max(0.0, min(1.0, float(value))) for value in raw_vector]
            theta = theta_from_vector(vector, bounds, theta_bo.theta_id(f"cma_r{round_index:02d}", round_theta_index, {"t_lead": 0, "delta_T_thr": 0, "G_ext": 0, "Q_ratio": 0, "tau": 0.75}))
            theta["parameter_id"] = theta_bo.theta_id(f"cma_r{round_index:02d}", round_theta_index, theta)
            if theta_key(theta) in selected:
                theta = theta_bo.random_theta_samples(bounds, 1, seed + eval_index * 1009 + rng.randint(0, 999), "cma_fill", selected)[0]
                theta["parameter_id"] = theta_bo.theta_id(f"cma_r{round_index:02d}", round_theta_index, theta)
                vector = vector_from_theta(theta, bounds)
            selected.add(theta_key(theta))
            generation_items.append((theta, round_index, round_theta_index, vector))
            rows.append(theta)
            eval_index += 1
        result_rows = evaluate_batch([(theta, round_index, round_theta_index) for theta, round_index, round_theta_index, _vector in generation_items])
        vectors_to_tell = [vector for _theta, _round_index, _round_theta_index, vector in generation_items]
        scores_to_tell = [safe_float(row.get("score"), float("inf")) for row in result_rows]
        if len(vectors_to_tell) == len(generation_vectors):
            strategy.tell(vectors_to_tell, scores_to_tell)
    return rows


def run_method(
    method: str,
    seed: int,
    run_id: str,
    bounds: dict[str, Any],
    args: argparse.Namespace,
    eval_args: argparse.Namespace,
    real_context: dict[str, Any] | None,
    w_E: float,
    w_G: float,
    stage1: B4Stage1Inputs,
    stop_on_spc: bool = False,
    prior_rows: list[dict[str, Any]] | None = None,
    checkpoint_callback: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = warm_start_observations(args) if method == "BO" else []
    round_rows: list[dict[str, Any]] = []
    existing: set[tuple[float, float, float, float, float]] = {theta_key(row) for row in observations}
    checkpoint_base_rows = [dict(row) for row in (prior_rows or [])]
    prior_by_key = {row_evaluation_key(row): dict(row) for row in (prior_rows or [])}

    def completed_row_for(theta: dict[str, Any], round_index: int, round_theta_index: int) -> dict[str, Any] | None:
        completed = prior_by_key.get(evaluation_key(method, seed, round_index, round_theta_index, str(theta.get("parameter_id", ""))))
        if completed is not None:
            row = dict(completed)
            row["method"] = method
            row["seed"] = seed
            row["round"] = round_index
            row["round_theta_index"] = round_theta_index
            row["theta_per_round"] = row.get("theta_per_round", args.theta_per_round)
            return row
        return None

    def materialize_new_row(
        theta: dict[str, Any],
        round_index: int,
        round_theta_index: int,
        bo_fields: dict[str, Any] | None,
        prediction: dict[str, Any],
    ) -> dict[str, Any]:
        row = evaluate_theta(run_id, method, seed, round_index, theta, args, eval_args, real_context, w_E, w_G)
        row["round_theta_index"] = round_theta_index
        row["theta_per_round"] = args.theta_per_round
        row.update(prediction)
        if bo_fields:
            row.update(bo_fields)
            row["acquisition"] = bo_fields.get("acquisition", bo_fields.get("essi_acquisition", ""))
        else:
            row["acquisition"] = ""
        return row

    def record_row(row: dict[str, Any], round_index: int, round_theta_index: int) -> dict[str, Any]:
        learning_observation = bo_learning_observation(row)
        if learning_observation is not None:
            observations.append(learning_observation)
        if method == "BO":
            best = min(observations, key=lambda item: safe_float(item.get("bo_score_sec"), float("inf"))) if observations else {}
            if stop_on_spc:
                update_spc_row(row, round_rows, args)
            round_rows.append({
                "round": round_index,
                "round_theta_index": round_theta_index,
                "phase": "bo" if round_index > args.bo_initial else "initial",
                "best_bo_score_sec": best.get("bo_score_sec", ""),
                "essi_max": row.get("essi_max", ""),
                "essi_log_max": row.get("essi_log_max", ""),
                "essi_log_max_ewma": row.get("essi_log_max_ewma", ""),
                "essi_spc_status": row.get("essi_spc_status", ""),
                "essi_stop_recommended": row.get("essi_stop_recommended", ""),
            })
        rows.append(row)
        update_best_so_far(rows)
        return row

    def checkpoint_partial(completed_rows: list[dict[str, Any]]) -> None:
        if checkpoint_callback is not None:
            checkpoint_callback(dedupe_evaluation_rows([*checkpoint_base_rows, *rows, *completed_rows]))

    def evaluate_batch_and_record(
        items: list[tuple[dict[str, Any], int, int, dict[str, Any] | None]],
    ) -> list[dict[str, Any]]:
        materialized: dict[int, dict[str, Any]] = {}
        pending: list[tuple[int, dict[str, Any], int, int, dict[str, Any] | None, dict[str, Any]]] = []
        for item_index, (theta, round_index, round_theta_index, bo_fields) in enumerate(items):
            completed = completed_row_for(theta, round_index, round_theta_index)
            if completed is not None:
                materialized[item_index] = completed
                continue
            prediction = surrogate_prediction(observations, theta, bounds) if method == "BO" else {"surrogate_mean": "", "surrogate_ci_low": "", "surrogate_ci_high": ""}
            pending.append((item_index, theta, round_index, round_theta_index, bo_fields, prediction))

        if pending and args.workers > 1:
            completed_new_rows: list[dict[str, Any]] = []
            if args.mock_eval:
                with ThreadPoolExecutor(max_workers=min(args.workers, len(pending))) as executor:
                    future_map = {
                        executor.submit(materialize_new_row, theta, round_index, round_theta_index, bo_fields, prediction): item_index
                        for item_index, theta, round_index, round_theta_index, bo_fields, prediction in pending
                    }
                    for future in as_completed(future_map):
                        row = future.result()
                        materialized[future_map[future]] = row
                        completed_new_rows.append(row)
                        checkpoint_partial(completed_new_rows)
            else:
                with ProcessPoolExecutor(max_workers=min(args.workers, len(pending))) as executor:
                    future_map = {
                        executor.submit(
                            materialize_new_row_worker,
                            {
                                "run_id": run_id,
                                "method": method,
                                "seed": seed,
                                "round_index": round_index,
                                "round_theta_index": round_theta_index,
                                "theta_per_round": args.theta_per_round,
                                "theta": theta,
                                "args": args,
                                "eval_args": eval_args,
                                "real_context": real_context,
                                "w_E": w_E,
                                "w_G": w_G,
                                "bo_fields": bo_fields,
                                "prediction": prediction,
                            },
                        ): item_index
                        for item_index, theta, round_index, round_theta_index, bo_fields, prediction in pending
                    }
                    for future in as_completed(future_map):
                        row = future.result()
                        materialized[future_map[future]] = row
                        completed_new_rows.append(row)
                        checkpoint_partial(completed_new_rows)
        else:
            completed_new_rows = []
            for item_index, theta, round_index, round_theta_index, bo_fields, prediction in pending:
                row = materialize_new_row(theta, round_index, round_theta_index, bo_fields, prediction)
                materialized[item_index] = row
                completed_new_rows.append(row)
                checkpoint_partial(completed_new_rows)

        ordered_rows: list[dict[str, Any]] = []
        for item_index, (_theta, round_index, round_theta_index, _bo_fields) in enumerate(items):
            row = record_row(materialized[item_index], round_index, round_theta_index)
            ordered_rows.append(row)
        if checkpoint_callback is not None:
            checkpoint_callback(dedupe_evaluation_rows([*checkpoint_base_rows, *rows]))
        return ordered_rows

    if method == "Random Search":
        all_thetas = random_search_thetas(bounds, args.m, args.theta_per_round, seed)
        for round_index in range(1, args.m + 1):
            batch = all_thetas[(round_index - 1) * args.theta_per_round : round_index * args.theta_per_round]
            evaluate_batch_and_record([(theta, round_index, round_theta_index, None) for round_theta_index, theta in enumerate(batch, start=1)])
    elif method == "CMA-ES":
        cache: dict[tuple[float, float, float, float, float], dict[str, Any]] = {}

        def cma_evaluate_batch(batch: list[tuple[dict[str, Any], int, int]]) -> list[dict[str, Any]]:
            result_rows = evaluate_batch_and_record([(theta, round_index, round_theta_index, None) for theta, round_index, round_theta_index in batch])
            for theta, row in zip((item[0] for item in batch), result_rows):
                cache[theta_key(theta)] = row
            return result_rows

        cma_es_thetas(bounds, args.m, args.theta_per_round, seed, cma_evaluate_batch)
    elif method == "BO":
        for round_index in range(1, args.m + 1):
            if round_index <= args.bo_initial or len(observations) < 2:
                batch = theta_bo.random_theta_samples(bounds, args.theta_per_round, seed + round_index * 31, "bo_init", existing)
                batch_fields: list[dict[str, Any]] = [{} for _theta in batch]
            elif args.bo_pass_focus_from_round and round_index >= args.bo_pass_focus_from_round:
                selected_ranked = pass_focus_bo_batch(
                    observations,
                    bounds,
                    args.theta_per_round,
                    seed + round_index,
                    existing,
                    args.bo_pass_focus_min_feasibility,
                )
                if len(selected_ranked) < args.theta_per_round:
                    fallback_existing = set(existing)
                    fallback_existing.update(theta_key(item) for item in selected_ranked)
                    ranked = essi_improvement_candidates(observations, bounds, stage1, seed + round_index, fallback_existing, args.ei_candidate_count)
                    selected_ranked.extend(diverse_bo_batch(ranked, bounds, args.theta_per_round - len(selected_ranked)))
                batch = [theta_bo.clamp_theta(item, bounds) for item in selected_ranked]
                batch_fields = [
                    {field: item.get(field, "") for field in ["raw_ei_acquisition", "acquisition", "bo_selection_strategy", "bo_candidate_source", "bo_plateau_mode", "bo_batch_slot", "hold_feasibility", *ESSI_FIELDS]}
                    for item in selected_ranked
                ]
            else:
                ranked = essi_improvement_candidates(observations, bounds, stage1, seed + round_index, existing, args.ei_candidate_count)
                selected_ranked = diverse_bo_batch(ranked, bounds, args.theta_per_round)
                batch = [theta_bo.clamp_theta(item, bounds) for item in selected_ranked]
                batch_fields = [
                    {field: item.get(field, "") for field in ["raw_ei_acquisition", "acquisition", "bo_selection_strategy", "bo_candidate_source", "bo_plateau_mode", "bo_batch_slot", "hold_feasibility", *ESSI_FIELDS]}
                    for item in selected_ranked
                ]
            stop_after_round = False
            for round_theta_index, theta in enumerate(batch, start=1):
                theta["parameter_id"] = theta_bo.theta_id(f"bo_r{round_index:02d}", round_theta_index, theta)
                existing.add(theta_key(theta))
            result_rows = evaluate_batch_and_record([
                (theta, round_index, round_theta_index, batch_fields[round_theta_index - 1])
                for round_theta_index, theta in enumerate(batch, start=1)
            ])
            for row in result_rows:
                if stop_on_spc and str(row.get("essi_stop_recommended", "")).lower() == "true":
                    stop_after_round = True
            if stop_after_round:
                break
    else:
        raise B4OptimizationError(f"unknown_method:{method}")

    update_best_so_far(rows)
    if checkpoint_callback is not None:
        checkpoint_callback(dedupe_evaluation_rows([*checkpoint_base_rows, *rows]))
    return rows


def run_method_with_checkpoint(
    method: str,
    seed: int,
    run_id: str,
    bounds: dict[str, Any],
    args: argparse.Namespace,
    real_context: dict[str, Any] | None,
    w_E: float,
    w_G: float,
    stage1: B4Stage1Inputs,
    prior_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    eval_args = build_eval_args(args, seed, args.run_root, args.output_dir / run_id)
    method_run_id = f"{run_id}_{method.lower().replace(' ', '_').replace('-', '_')}_{seed}"

    def checkpoint(rows: list[dict[str, Any]]) -> None:
        checkpoint_method_rows(args.output_dir / run_id, method, seed, rows)

    return run_method(
        method,
        seed,
        method_run_id,
        bounds,
        args,
        eval_args,
        real_context,
        w_E,
        w_G,
        stage1,
        prior_rows=prior_rows,
        checkpoint_callback=checkpoint,
    )


def run_method_seed_grid(
    run_id: str,
    bounds: dict[str, Any],
    args: argparse.Namespace,
    real_context: dict[str, Any] | None,
    w_E: float,
    w_G: float,
    stage1: B4Stage1Inputs,
    methods_to_run: list[str],
    seeds: list[int],
) -> list[dict[str, Any]]:
    output_dir = args.output_dir / run_id
    jobs = [
        (method, seed, load_task_prior_rows(output_dir, method, seed) if args.resume else [])
        for method in methods_to_run
        for seed in seeds
    ]
    completed: list[dict[str, Any]] = []
    if args.workers == 1 or args.theta_per_round > 1 or len(jobs) <= 1:
        for method, seed, prior_rows in jobs:
            completed.extend(run_method_with_checkpoint(method, seed, run_id, bounds, args, real_context, w_E, w_G, stage1, prior_rows))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {
                executor.submit(run_method_with_checkpoint, method, seed, run_id, bounds, args, real_context, w_E, w_G, stage1, prior_rows): (method, seed)
                for method, seed, prior_rows in jobs
            }
            for future in as_completed(future_map):
                completed.extend(future.result())
    return dedupe_evaluation_rows(completed)


def build_best_so_far_table(rows: list[dict[str, Any]], m: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row["method"]), int(row["seed"])), []).append(row)
    table: list[dict[str, Any]] = []
    for (method, seed), group in sorted(grouped.items()):
        group = sorted(group, key=lambda item: (int(item["round"]), int(item.get("round_theta_index", 1) or 1)))
        out: dict[str, Any] = {"method": method, "seed": seed}
        for index in range(1, m + 1):
            completed = [row for row in group if int(row["round"]) <= index]
            out[f"R{index}"] = completed[-1]["best_so_far"] if completed else ""
        table.append(out)
    return table


def build_bo_surrogate_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("method") != "BO":
            continue
        out.append({
            "method": row.get("method", ""),
            "seed": row.get("seed", ""),
            "round": row.get("round", ""),
            "round_theta_index": row.get("round_theta_index", ""),
            "theta_per_round": row.get("theta_per_round", ""),
            **{field: row.get(field, "") for field in THETA_FIELDS},
            "observed_score": row.get("score", ""),
            "best_so_far": row.get("best_so_far", ""),
            "surrogate_mean": row.get("surrogate_mean", ""),
            "surrogate_ci_low": row.get("surrogate_ci_low", ""),
            "surrogate_ci_high": row.get("surrogate_ci_high", ""),
            "raw_ei_acquisition": row.get("raw_ei_acquisition", ""),
            "acquisition": row.get("acquisition", ""),
            "bo_selection_strategy": row.get("bo_selection_strategy", ""),
            "bo_candidate_source": row.get("bo_candidate_source", ""),
            "bo_plateau_mode": row.get("bo_plateau_mode", ""),
            **{field: row.get(field, "") for field in ESSI_FIELDS},
        })
    return out


def bo_observation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for row in rows:
        if row.get("method") != "BO":
            continue
        if row.get("final_status") not in {"PASS", "WARNING"}:
            continue
        if str(row.get("emergency_arrived", "")).lower() != "true":
            continue
        if str(row.get("emergency_teleport", "")).lower() == "true":
            continue
        if sumo_summary_safety_failed(row):
            continue
        score = safe_float(row.get("score"), float("inf"))
        if not math.isfinite(score) or score >= FAILURE_PENALTY_SEC:
            continue
        observations.append(dict(row))
    return sorted(
        observations,
        key=lambda row: (
            int(safe_float(row.get("seed"), 0.0)),
            int(safe_float(row.get("round"), 0.0)),
            int(safe_float(row.get("round_theta_index"), 1.0)),
        ),
    )


def build_bo_gp_slice_table(rows: list[dict[str, Any]], bounds: dict[str, Any], slice_parameter: str = "t_lead", grid_count: int = 160) -> list[dict[str, Any]]:
    observations = bo_observation_rows(rows)
    if len(observations) < 2:
        return []
    try:
        import numpy as np  # type: ignore
        from sklearn.gaussian_process import GaussianProcessRegressor  # type: ignore
        from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel  # type: ignore
    except Exception:
        return []

    best = min(observations, key=lambda row: safe_float(row.get("score"), float("inf")))
    x_train = np.array([vector_from_theta(row, bounds) for row in observations], dtype=float)
    y_train = np.array([safe_float(row.get("score")) for row in observations], dtype=float)
    kernel = ConstantKernel(1.0, constant_value_bounds="fixed") * Matern(
        length_scale=[0.35] * x_train.shape[1],
        length_scale_bounds=theta_bo.GP_LENGTH_SCALE_BOUNDS,
        nu=2.5,
    ) + WhiteKernel(noise_level=theta_bo.GP_NOISE_LEVEL, noise_level_bounds="fixed")
    try:
        gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, random_state=0, alpha=theta_bo.GP_NOISE_LEVEL, n_restarts_optimizer=0)
        gp.fit(x_train, y_train)
    except Exception:
        return []

    lower = safe_float(bounds[slice_parameter]["lower"])
    upper = safe_float(bounds[slice_parameter]["upper"])
    grid_values = np.linspace(lower, upper, grid_count)
    table: list[dict[str, Any]] = []
    for index, value in enumerate(grid_values, start=1):
        theta = {field: best.get(field, "") for field in THETA_FIELDS}
        theta[slice_parameter] = value
        theta["parameter_id"] = f"gp_slice_{slice_parameter}_{index:03d}"
        theta = theta_bo.clamp_theta(theta, bounds, parameter_id=str(theta["parameter_id"]))
        vector = np.array([vector_from_theta(theta, bounds)], dtype=float)
        mean, std = gp.predict(vector, return_std=True)
        half_width = 1.96 * float(std[0])
        row: dict[str, Any] = {
            "row_type": "gp_slice",
            "slice_parameter": slice_parameter,
            "slice_value": sec(theta[slice_parameter]),
            **{field: theta.get(field, "") for field in THETA_FIELDS},
            "observed_score": "",
            "gp_mean": sec(float(mean[0])),
            "gp_ci_low": sec(float(mean[0]) - half_width),
            "gp_ci_high": sec(float(mean[0]) + half_width),
            "is_best_observed": "False",
            "note": "GP estimate only; objective was not re-evaluated on this slice.",
        }
        table.append(row)

    best_key = row_evaluation_key(best)
    for observation in observations:
        table.append({
            "row_type": "observed",
            "slice_parameter": slice_parameter,
            "slice_value": sec(observation.get(slice_parameter)),
            **{field: observation.get(field, "") for field in THETA_FIELDS},
            "observed_score": observation.get("score", ""),
            "gp_mean": observation.get("surrogate_mean", ""),
            "gp_ci_low": observation.get("surrogate_ci_low", ""),
            "gp_ci_high": observation.get("surrogate_ci_high", ""),
            "is_best_observed": "True" if row_evaluation_key(observation) == best_key else "False",
            "note": "Observed BO evaluation.",
        })
    return table


def parse_weight_ratio(weight_ratio: str) -> tuple[float, float]:
    left, right = weight_ratio.split(":")
    return float(left), float(right)


def normalize_pareto_weights(weight_ratio: str) -> tuple[float, float]:
    return normalize_objective_weights(*parse_weight_ratio(weight_ratio))


def knee_index(rows: list[dict[str, Any]]) -> int:
    if len(rows) <= 2:
        return max(0, len(rows) // 2)
    points = [(safe_float(row["D_G_sec"]), safe_float(row["D_E_sec"])) for row in rows]
    min_x, max_x = min(x for x, _y in points), max(x for x, _y in points)
    min_y, max_y = min(y for _x, y in points), max(y for _x, y in points)

    def norm(point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        return ((x - min_x) / max(max_x - min_x, 1.0e-9), (y - min_y) / max(max_y - min_y, 1.0e-9))

    normalized = [norm(point) for point in points]
    start = normalized[0]
    end = normalized[-1]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denom = math.hypot(dx, dy) or 1.0
    distances = [abs(dy * x - dx * y + end[0] * start[1] - end[1] * start[0]) / denom for x, y in normalized]
    return max(range(len(distances)), key=lambda index: distances[index])


def completed_round_count(rows: list[dict[str, Any]]) -> int:
    rounds = [int(safe_float(row.get("round"), 0.0)) for row in rows if row.get("round") not in {"", None}]
    return max(rounds) if rounds else 0


def pareto_static_inputs(args: argparse.Namespace) -> dict[str, Any]:
    static_inputs: dict[str, Any] = {
        "net_file": rel(args.net_file),
        "net_sha256": sha256_file(args.net_file) if args.net_file.is_file() else "",
        "background_route": rel(args.background_route),
        "background_route_sha256": sha256_file(args.background_route) if args.background_route.is_file() else "",
        "stage1_dir": rel(args.stage1_dir),
        "active_inputs": rel(args.active_inputs) if args.active_inputs.is_file() else "",
    }
    if args.active_inputs.is_file():
        active_inputs = read_json(args.active_inputs)
        firetruck_route = active_inputs.get("firetruck_route", "")
        if firetruck_route:
            firetruck_route_path = PROJECT_ROOT / str(firetruck_route)
            static_inputs["firetruck_route"] = rel(firetruck_route_path)
            static_inputs["firetruck_route_sha256"] = sha256_file(firetruck_route_path) if firetruck_route_path.is_file() else ""
    return static_inputs


def run_pareto(
    run_id: str,
    bounds: dict[str, Any],
    args: argparse.Namespace,
    eval_args: argparse.Namespace,
    real_context: dict[str, Any] | None,
    stage1: B4Stage1Inputs,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    pareto_rows: list[dict[str, Any]] = []
    spc_trace_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []
    for weight_ratio in PARETO_WEIGHT_RATIOS:
        w_E, w_G = normalize_pareto_weights(weight_ratio)
        raw_w_E, raw_w_G = parse_weight_ratio(weight_ratio)
        checkpoint = args.output_dir / run_id / "checkpoints" / f"pareto_{weight_ratio.replace(':', '_')}.csv"
        prior_rows = read_csv_rows(checkpoint) if args.resume else []
        rows = run_method(
            "BO",
            args.seed_base,
            f"{run_id}_pareto_{weight_ratio.replace(':', '_')}",
            bounds,
            args,
            eval_args,
            real_context,
            w_E,
            w_G,
            stage1,
            stop_on_spc=args.pareto_spc_stop,
            prior_rows=[dict(row) for row in prior_rows],
            checkpoint_callback=lambda current_rows, path=checkpoint: write_checkpoint_rows(path, current_rows),
        )
        best = min(rows, key=lambda row: safe_float(row.get("score"), float("inf")))
        final_rows.append(clean_final_result_row(best, raw_w_E, raw_w_G, weight_ratio))
        stop_rows = [row for row in rows if str(row.get("essi_stop_recommended", "")).lower() == "true"]
        for row in rows:
            if row.get("essi_log_max") in {"", None}:
                continue
            spc_trace_rows.append({
                "weight_ratio": weight_ratio,
                "round": row.get("round", ""),
                "essi_max": row.get("essi_max", ""),
                "essi_log_max": row.get("essi_log_max", ""),
                "essi_log_max_ewma": row.get("essi_log_max_ewma", ""),
                "spc_status": row.get("essi_spc_status", ""),
                "spc_stop_recommended": row.get("essi_stop_recommended", ""),
            })
        spc_source = stop_rows[0] if stop_rows else next((row for row in reversed(rows) if row.get("essi_log_max") not in {"", None}), {})
        pareto_rows.append({
            "weight_ratio": weight_ratio,
            **{field: best.get(field, "") for field in THETA_FIELDS},
            "D_E_sec": best.get("D_E_sec", ""),
            "D_G_sec": best.get("D_G_sec", ""),
            "score": best.get("score", ""),
            "rounds_completed": completed_round_count(rows),
            "essi_max": spc_source.get("essi_max", ""),
            "essi_log_max": spc_source.get("essi_log_max", ""),
            "essi_log_max_ewma": spc_source.get("essi_log_max_ewma", ""),
            "spc_status": spc_source.get("essi_spc_status", ""),
            "spc_stop_recommended": "True" if stop_rows else "False",
            "spc_stop_round": stop_rows[0].get("round", "") if stop_rows else "",
            "is_knee": "False",
        })
    pareto_rows.sort(key=lambda row: safe_float(row["D_G_sec"]))
    if pareto_rows:
        pareto_rows[knee_index(pareto_rows)]["is_knee"] = "True"
    return pareto_rows, spc_trace_rows, final_rows


def run_noise_check(
    run_id: str,
    bounds: dict[str, Any],
    args: argparse.Namespace,
    eval_args: argparse.Namespace,
    real_context: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    theta = theta_bo.clamp_theta(B4ThetaParams().as_result_fields(), bounds, parameter_id="noise_reference_theta")
    parent_run_id = run_id.removesuffix("_noise")
    checkpoint = args.output_dir / parent_run_id / "checkpoints" / "noise_check.csv"
    rows: list[dict[str, Any]] = [dict(row) for row in read_csv_rows(checkpoint)] if args.resume else []
    completed_repeats = {int(safe_float(row.get("repeat"), 0.0)) for row in rows}
    for repeat in range(1, 6):
        if repeat in completed_repeats:
            continue
        row = evaluate_theta(run_id, "Noise Check", args.seed_base, repeat, theta, args, eval_args, real_context, args.w_E, args.w_G, repeat_id=repeat)
        rows.append({
            "repeat": repeat,
            **{field: theta.get(field, "") for field in THETA_FIELDS},
            "D_E_sec": row["D_E_sec"],
            "D_G_sec": row["D_G_sec"],
            "score": row["score"],
            "final_status": row["final_status"],
        })
        write_csv(checkpoint, rows, NOISE_FIELDS)
    scores = [safe_float(row["score"]) for row in rows]
    mean = sum(scores) / len(scores) if scores else 0.0
    variance = sum((value - mean) ** 2 for value in scores) / max(len(scores) - 1, 1)
    summary = {
        "schema": "compact_v9_B4_noise_check.v1",
        "theta": theta,
        "repeat_count": len(rows),
        "score_mean": round(mean, 6),
        "score_std": round(math.sqrt(max(variance, 0.0)), 6),
        "score_range": round(max(scores) - min(scores), 6) if scores else 0.0,
        "policy_note": "This artifact records the actual 5-repeat noise check. It must not be described as a 30-repeat experiment.",
    }
    return rows, summary


def safe_slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_") or "value"


def select_visualization_solution(rows: list[dict[str, Any]], solution: str) -> dict[str, Any]:
    if solution.lower() == "best":
        pass_rows = [
            row
            for row in rows
            if row.get("final_status") in {"PASS", "WARNING"}
            and str(row.get("emergency_arrived", "")).lower() == "true"
            and str(row.get("emergency_teleport", "")).lower() != "true"
            and not sumo_summary_safety_failed(row)
        ]
        if not pass_rows:
            raise B4OptimizationError("visualization_no_pass_rows")
        return min(pass_rows, key=lambda row: safe_float(row.get("score"), float("inf")))
    matches = [row for row in rows if row.get("parameter_id") == solution]
    if not matches:
        raise B4OptimizationError(f"visualization_solution_not_found:{solution}")
    return min(matches, key=lambda row: safe_float(row.get("score"), float("inf")))


def visualization_method_run_id(run_id: str, solution: dict[str, Any]) -> str:
    method = str(solution.get("method", ""))
    seed = int(safe_float(solution.get("seed"), DEFAULT_SEED_BASE))
    return f"{run_id}_{method.lower().replace(' ', '_').replace('-', '_')}_{seed}"


def materialize_visualization_logs(args: argparse.Namespace, solution: dict[str, Any]) -> dict[str, Any]:
    if args.mock_eval:
        raise B4OptimizationError("visualization_materialize_requires_real_eval")
    method_run_id = visualization_method_run_id(args.run_id, solution)
    seed = int(safe_float(solution.get("seed"), args.seed_base))
    output_dir = args.output_dir / args.run_id
    eval_args = build_eval_args(args, seed, args.run_root, output_dir)
    eval_args.emit_fcd = True
    eval_args.emit_tls_states = True
    eval_args.workers = 1
    real_context = prepare_real_context_once(args.run_id, eval_args)
    theta = {field: solution.get(field, "") for field in THETA_FIELDS}
    raw = theta_bo.evaluate_theta_repeat({
        "run_id": method_run_id,
        "theta": theta,
        "seed": seed,
        "repeat": 1,
        "args": eval_args,
        "real_context": real_context,
    })
    return {
        "method_run_id": method_run_id,
        "b4_row": raw,
    }


def collect_visualization_info(args: argparse.Namespace) -> dict[str, Any]:
    if not args.run_id:
        raise B4OptimizationError("visualization_run_id_required")
    active_inputs_audit = validate_active_inputs(args)
    output_dir = args.output_dir / args.run_id
    rows = [dict(row) for row in read_csv_rows(output_dir / "all_evaluations.csv")]
    if not rows:
        raise B4OptimizationError(f"visualization_missing_all_evaluations:{rel(output_dir / 'all_evaluations.csv')}")
    solution = select_visualization_solution(rows, args.visualization_solution)
    parameter_id = str(solution.get("parameter_id", ""))
    materialized: dict[str, Any] = {}
    if args.materialize_visualization_logs:
        materialized = materialize_visualization_logs(args, solution)
    method_run_id = str(materialized.get("method_run_id") or visualization_method_run_id(args.run_id, solution))
    b04_repeat_dir = args.run_root / args.run_id / "B04" / "no_control" / "repeat_001"
    b4_repeat_dir = args.run_root / method_run_id / B4_MODE / parameter_id / "repeat_001"
    required = {
        "b04_fcd": b04_repeat_dir / "fcd.xml",
        "b04_tripinfo": b04_repeat_dir / "tripinfo.xml",
        "b04_tls_states": b04_repeat_dir / "tls_states.csv",
        "b4_fcd": b4_repeat_dir / "fcd.xml",
        "b4_tripinfo": b4_repeat_dir / "tripinfo.xml",
        "b4_tls_states": b4_repeat_dir / "tls_states.csv",
        "b4_signal_events": b4_repeat_dir / "signal_events.csv",
    }
    missing = [f"{name}:{rel(path)}" for name, path in required.items() if not path.is_file()]
    if missing:
        raise B4OptimizationError("visualization_missing_required_logs:" + ",".join(missing))
    active_inputs = read_json(args.active_inputs) if args.active_inputs.is_file() else {}
    firetruck_route = active_inputs.get("firetruck_route", "")
    firetruck_route_path = (PROJECT_ROOT / str(firetruck_route)) if firetruck_route else None
    static_inputs: dict[str, Any] = {
        "net_file": rel(args.net_file),
        "net_sha256": sha256_file(args.net_file) if args.net_file.is_file() else "",
        "background_route": rel(args.background_route),
        "background_route_sha256": sha256_file(args.background_route) if args.background_route.is_file() else "",
        "stage1_dir": rel(args.stage1_dir),
        "active_inputs": rel(args.active_inputs) if args.active_inputs.is_file() else "",
    }
    if firetruck_route_path is not None:
        static_inputs["firetruck_route"] = rel(firetruck_route_path)
        static_inputs["firetruck_route_sha256"] = sha256_file(firetruck_route_path) if firetruck_route_path.is_file() else ""
    output_path = args.visualization_output or (output_dir / f"visualization_info_{safe_slug(parameter_id)}.json")
    payload = {
        "schema": "compact_v9_B4_visualization_info.v1",
        "generated_at": utc_now(),
        "run_id": args.run_id,
        "method_run_id": method_run_id,
        "solution_selector": args.visualization_solution,
        "solution": {field: solution.get(field, "") for field in EVALUATION_FIELDS},
        "best_theta": {field: solution.get(field, "") for field in THETA_FIELDS},
        "static_inputs": static_inputs,
        "active_inputs_audit": active_inputs_audit,
        "paths": {name: rel(path) for name, path in required.items()},
        "materialized_logs": bool(args.materialize_visualization_logs),
        "materialized_b4_row": materialized.get("b4_row", {}),
        "notes": [
            "FCD and TLS state logs are required for the visualization bundle.",
            "B04 baseline and B4 theta runs live under different run_id folders in the optimization runner.",
            "Use --materialize-visualization-logs to re-run the selected solution with emit_fcd=True and emit_tls_states=True before writing this manifest.",
        ],
    }
    write_json(output_path, payload)
    return payload


def configure_plot_style() -> None:
    import matplotlib.pyplot as plt  # type: ignore
    from matplotlib import font_manager  # type: ignore

    available_fonts = {font.name for font in font_manager.fontManager.ttflist}
    preferred_fonts = ["Apple SD Gothic Neo", "AppleGothic", "Nanum Gothic", "Arial Unicode MS", "DejaVu Sans"]
    font_family = next((font for font in preferred_fonts if font in available_fonts), "DejaVu Sans")

    plt.rcParams.update({
        "font.family": [font_family],
        "axes.unicode_minus": False,
        "axes.edgecolor": "#d7d7d7",
        "axes.labelcolor": "#666666",
        "xtick.color": "#777777",
        "ytick.color": "#777777",
        "grid.color": "#d9d9d9",
    })


def save_plot(output: Path) -> None:
    import matplotlib.pyplot as plt  # type: ignore

    output.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(output, dpi=220)
    plt.savefig(output.with_suffix(".svg"))
    plt.close()


def plot_best_so_far(table: list[dict[str, Any]], m: int, output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore
    import numpy as np  # type: ignore

    output.parent.mkdir(parents=True, exist_ok=True)
    configure_plot_style()
    rounds = np.arange(1, m + 1)
    plt.figure(figsize=(10.8, 6.2))
    style = {
        "BO": {"color": "#2f80c9", "linestyle": "-", "label": "BO"},
        "CMA-ES": {"color": "#2aa17a", "linestyle": "--", "label": "CMA-ES"},
        "Random Search": {"color": "#d46b3d", "linestyle": ":", "label": "랜덤 서치"},
    }
    max_seed_count = 0
    for method in METHODS:
        series = [
            [safe_float(row.get(f"R{index}")) for index in range(1, m + 1)]
            for row in table
            if row.get("method") == method
        ]
        if not series:
            continue
        values = np.array(series, dtype=float)
        max_seed_count = max(max_seed_count, values.shape[0])
        mean = values.mean(axis=0)
        std = values.std(axis=0, ddof=1) if values.shape[0] > 1 else np.zeros(m)
        params = style[method]
        plt.plot(rounds, mean, label=params["label"], color=params["color"], linestyle=params["linestyle"], linewidth=2.7)
        plt.fill_between(rounds, mean - std, mean + std, color=params["color"], alpha=0.14, linewidth=0)
    plt.xlabel("라운드 (탐색 횟수)", fontsize=12)
    plt.ylabel("현재까지 찾은 최적 Score", fontsize=12)
    subtitle = f"시드 {max_seed_count}판 기준 · 음영 = ±표준편차 · Score 낮을수록 우수"
    plt.title("최적화 방법별 수렴 곡선\n" + subtitle, loc="left", fontsize=15, color="#333333")
    plt.grid(True, alpha=0.58)
    plt.legend(loc="upper left", frameon=False, ncol=3, fontsize=11)
    save_plot(output)


def plot_bo_surrogate(table: list[dict[str, Any]], output: Path, gp_slice_table: list[dict[str, Any]] | None = None) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore
    import numpy as np  # type: ignore

    output.parent.mkdir(parents=True, exist_ok=True)
    if not table:
        raise B4OptimizationError("bo_surrogate_table_empty")
    configure_plot_style()
    plt.figure(figsize=(9.2, 5.4))
    if gp_slice_table:
        slice_rows = [row for row in gp_slice_table if row.get("row_type") == "gp_slice"]
        observed_rows = [row for row in gp_slice_table if row.get("row_type") == "observed"]
        x = np.array([safe_float(row["slice_value"]) for row in slice_rows], dtype=float)
        mean = np.array([safe_float(row["gp_mean"], float("nan")) for row in slice_rows], dtype=float)
        low = np.array([safe_float(row["gp_ci_low"], float("nan")) for row in slice_rows], dtype=float)
        high = np.array([safe_float(row["gp_ci_high"], float("nan")) for row in slice_rows], dtype=float)
        plt.fill_between(x, low, high, color="#9dbbe5", alpha=0.45, label="confidence interval")
        plt.plot(x, mean, color="#1659b7", linewidth=2.5, label="GP mean")
        ox = [safe_float(row["slice_value"]) for row in observed_rows]
        oy = [safe_float(row["observed_score"]) for row in observed_rows]
        plt.scatter(ox, oy, s=34, color="#143b66", edgecolor="white", linewidth=0.5, label="observed values", zorder=4)
        best_rows = [row for row in observed_rows if row.get("is_best_observed") == "True"]
        if best_rows:
            best = best_rows[0]
            bx = safe_float(best["slice_value"])
            by = safe_float(best["observed_score"])
            plt.annotate(
                "best observed value",
                xy=(bx, by),
                xytext=(bx, by + max((float(np.nanmax(high)) - float(np.nanmin(low))) * 0.18, 3.0)),
                arrowprops={"arrowstyle": "-|>", "color": "#111111", "lw": 1.8},
                ha="center",
                fontsize=11,
            )
        plt.xlabel("hyperparameter: t_lead", fontsize=12)
        plt.ylabel("Score", fontsize=12)
        plt.title("BO/GP 추정 함수\nbest BO theta 기준 t_lead 1D slice · 실제 재평가 없음", fontsize=14)
    else:
        seed = table[0]["seed"]
        rows = [row for row in table if row.get("seed") == seed]
        rounds = [int(row["round"]) for row in rows]
        observed = [safe_float(row["observed_score"]) for row in rows]
        best = [safe_float(row["best_so_far"]) for row in rows]
        surrogate = [safe_float(row["surrogate_mean"], float("nan")) for row in rows]
        low = [safe_float(row["surrogate_ci_low"], float("nan")) for row in rows]
        high = [safe_float(row["surrogate_ci_high"], float("nan")) for row in rows]
        plt.scatter(rounds, observed, s=26, label="observed values", color="#143b66")
        plt.plot(rounds, best, label="best observed value", color="#222222", linewidth=2)
        plt.plot(rounds, surrogate, label="GP mean", color="#1659b7", linewidth=1.8)
        plt.fill_between(rounds, low, high, color="#9dbbe5", alpha=0.35, label="confidence interval")
        plt.xlabel("라운드", fontsize=12)
        plt.ylabel("Score", fontsize=12)
        plt.title(f"BO/GP 추정 trace (seed={seed})", fontsize=14)
    plt.grid(True, alpha=0.45)
    plt.legend(frameon=True, facecolor="white", edgecolor="#dddddd", fontsize=10)
    save_plot(output)


def plot_pareto(rows: list[dict[str, Any]], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore

    output.parent.mkdir(parents=True, exist_ok=True)
    configure_plot_style()
    x = [safe_float(row["D_G_sec"]) for row in rows]
    y = [safe_float(row["D_E_sec"]) for row in rows]
    plt.figure(figsize=(8.4, 5.6))
    plt.plot(x, y, color="#86b8e8", linewidth=2.2, alpha=0.9)
    grouped: dict[tuple[float, float], list[dict[str, Any]]] = {}
    for row in rows:
        key = (round(safe_float(row["D_G_sec"]), 6), round(safe_float(row["D_E_sec"]), 6))
        grouped.setdefault(key, []).append(row)
    for (D_G_sec, D_E_sec), group in grouped.items():
        is_knee = any(row.get("is_knee") == "True" for row in group)
        color = "#d85a2f" if is_knee else "#2f80c9"
        size = 130 if is_knee else 72
        label = ", ".join(row["weight_ratio"] for row in group)
        if len(group) > 2:
            label = ", ".join(row["weight_ratio"] for row in group[:3]) + "\n" + ", ".join(row["weight_ratio"] for row in group[3:])
        plt.scatter([D_G_sec], [D_E_sec], color=color, s=size, zorder=3)
        plt.annotate(label, (D_G_sec, D_E_sec), textcoords="offset points", xytext=(8, 8), fontsize=9)
    if len(grouped) == 1 and len(rows) > 1:
        plt.text(
            0.02,
            0.03,
            "All weight ratios selected the same theta; trade-off is not visible in this run.",
            transform=plt.gca().transAxes,
            fontsize=10,
            color="#555555",
        )
    plt.xlabel("일반차 평균 지연 D_G_sec (s)", fontsize=12)
    plt.ylabel("응급차 지연 D_E_sec (s)", fontsize=12)
    plt.title("가중치별 Pareto 후보\n여러 가중치로 얻은 후보를 결정자에게 펼쳐 보여주는 시각화", loc="left", fontsize=14)
    plt.grid(True, alpha=0.45)
    save_plot(output)


def plot_sensitivity_spc(rows: list[dict[str, Any]], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore

    output.parent.mkdir(parents=True, exist_ok=True)
    configure_plot_style()
    plt.figure(figsize=(9.2, 5.4))
    for weight_ratio in PARETO_WEIGHT_RATIOS:
        group = [row for row in rows if row.get("weight_ratio") == weight_ratio]
        if not group:
            continue
        x = [int(safe_float(row.get("round"), 0.0)) for row in group]
        y = [safe_float(row.get("essi_log_max_ewma"), float("nan")) for row in group]
        plt.plot(x, y, marker="o", linewidth=1.6, label=weight_ratio)
    plt.xlabel("BO 라운드", fontsize=12)
    plt.ylabel("ESSI log-max EWMA", fontsize=12)
    plt.title("가중치 민감도 분석\n가중치별 탐색 안정화 trace", loc="left", fontsize=14)
    plt.grid(True, alpha=0.45)
    plt.legend(title="가중치", frameon=False)
    save_plot(output)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run S1-forced B4 fixed-budget optimizer comparison.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--m", type=int, default=DEFAULT_M)
    parser.add_argument(
        "--theta-per-round",
        "--solutions-per-round",
        "--batch-size",
        dest="theta_per_round",
        type=int,
        default=DEFAULT_THETA_PER_ROUND,
        help="Theta candidates per optimization round. Total evaluations per seed/method = m * theta_per_round.",
    )
    parser.add_argument("--seed-base", type=int, default=DEFAULT_SEED_BASE)
    parser.add_argument("--bo-initial", type=int, default=DEFAULT_BO_INITIAL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--active-inputs", type=Path, default=DEFAULT_ACTIVE_INPUTS)
    parser.add_argument("--net-file", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--stage1-dir", type=Path, default=DEFAULT_STAGE1_DIR)
    parser.add_argument("--phase", default="bo-smoke")
    parser.add_argument("--hard-max-sim-time", type=float, default=4000.0)
    parser.add_argument("--sumo-binary", default=None)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--w-E", "--w1", dest="w_E", type=float, default=10.0)
    parser.add_argument("--w-G", "--w2", dest="w_G", type=float, default=1.0)
    parser.add_argument("--ei-candidate-count", "--essi-candidate-count", dest="ei_candidate_count", type=int, default=theta_bo.DEFAULT_EI_CANDIDATE_COUNT)
    parser.add_argument("--bo-pass-focus-from-round", type=int, default=0, help="From this BO round onward, sample only around clean PASS observations instead of using the mixed exploration slots. 0 disables this mode.")
    parser.add_argument("--bo-pass-focus-min-feasibility", type=float, default=0.70, help="Minimum safety/hold feasibility multiplier for pass-focused BO candidates before fallback.")
    parser.add_argument("--spc-window", type=int, default=theta_bo.DEFAULT_SPC_WINDOW)
    parser.add_argument("--spc-alpha", type=float, default=theta_bo.DEFAULT_SPC_ALPHA)
    parser.add_argument("--spc-min-rounds", type=int, default=theta_bo.DEFAULT_SPC_MIN_ROUNDS)
    parser.add_argument("--spc-min-improvement-sec", type=float, default=theta_bo.DEFAULT_SPC_MIN_IMPROVEMENT_SEC)
    parser.add_argument("--methods", nargs="+", default=None, help="Run only selected methods. Accepts BO, CMA-ES/cma, Random Search/random/rs.")
    parser.add_argument("--bo-first", action="store_true", help="Run BO before the other selected methods in a single invocation.")
    parser.add_argument("--append-existing", action="store_true", help="Merge selected method results into an existing run-id instead of starting from an empty comparison.")
    parser.add_argument("--resume", "--bo-resume", dest="resume", action="store_true", help="Resume an interrupted run-id from per-method/seed checkpoints and existing all_evaluations.csv rows.")
    parser.add_argument("--warm-start-csv", nargs="+", type=Path, default=[], help="Use completed evaluation CSV rows as BO observations without copying them into the new run output.")
    parser.add_argument("--collect-visualization-info", action="store_true", help="Write a manifest for a selected solution's B04/B4 FCD and TLS logs without running optimization.")
    parser.add_argument("--materialize-visualization-logs", "--generate-visualization-bundle", dest="materialize_visualization_logs", action="store_true", help="Re-run the selected visualization solution once with FCD and TLS state logging before writing the manifest.")
    parser.add_argument("--visualization-solution", default="best", help="Solution parameter_id to collect for visualization, or 'best' for the best PASS row.")
    parser.add_argument("--visualization-output", type=Path, default=None)
    parser.add_argument("--mock-eval", action="store_true")
    parser.add_argument("--emit-fcd", action="store_true")
    parser.add_argument("--emit-tls-states", dest="emit_tls_states", action="store_true")
    parser.add_argument("--skip-pareto", action="store_true")
    parser.add_argument("--skip-noise-check", action="store_true")
    parser.add_argument("--no-pareto-spc-stop", dest="pareto_spc_stop", action="store_false", help="Disable SPC-based early stop for one-search-per-weight Pareto BO sweeps.")
    parser.set_defaults(pareto_spc_stop=True)
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.n < 1:
        raise B4OptimizationError("n_must_be_positive")
    if args.m < 2:
        raise B4OptimizationError("m_must_be_at_least_2")
    if args.theta_per_round < 1:
        raise B4OptimizationError("theta_per_round_must_be_positive")
    if args.warm_start_csv:
        if not 0 <= args.bo_initial < args.m:
            raise B4OptimizationError("bo_initial_must_be_between_0_and_m_minus_1_with_warm_start")
    elif not 1 <= args.bo_initial < args.m:
        raise B4OptimizationError("bo_initial_must_be_between_1_and_m_minus_1")
    if args.workers < 1:
        raise B4OptimizationError("workers_must_be_positive")
    if args.materialize_visualization_logs and args.mock_eval:
        raise B4OptimizationError("materialize_visualization_logs_requires_real_eval")
    if args.w_E < 0.0 or args.w_G < 0.0:
        raise B4OptimizationError("weights_must_be_nonnegative")
    if args.w_E + args.w_G <= 0.0:
        raise B4OptimizationError("weight_sum_must_be_positive")
    if args.ei_candidate_count < 2:
        raise B4OptimizationError("ei_candidate_count_must_be_at_least_2")
    if args.ei_candidate_count < args.theta_per_round:
        raise B4OptimizationError("ei_candidate_count_must_be_at_least_theta_per_round")
    if args.bo_pass_focus_from_round < 0:
        raise B4OptimizationError("bo_pass_focus_from_round_must_be_nonnegative")
    if not 0.0 <= args.bo_pass_focus_min_feasibility <= 1.0:
        raise B4OptimizationError("bo_pass_focus_min_feasibility_must_be_between_0_and_1")
    methods = selected_methods(args)
    if not methods:
        raise B4OptimizationError("at_least_one_method_required")
    args.methods = methods
    args.output_dir = args.output_dir.resolve()
    args.run_root = args.run_root.resolve()
    args.active_inputs = args.active_inputs.resolve()
    args.net_file = args.net_file.resolve()
    args.background_route = args.background_route.resolve()
    args.stage1_dir = args.stage1_dir.resolve()
    args.warm_start_csv = [path.resolve() for path in (args.warm_start_csv or [])]
    for path in args.warm_start_csv:
        if not path.is_file():
            raise B4OptimizationError(f"missing_warm_start_csv:{path}")
    args.visualization_output = args.visualization_output.resolve() if args.visualization_output else None


def run_experiment(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or default_run_id()
    output_dir = args.output_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    methods_to_run = selected_methods(args)
    preflight_payload = preflight(args)
    stage1: B4Stage1Inputs = preflight_payload["stage1"]
    bounds = preflight_payload["bounds"]
    static_inputs = pareto_static_inputs(args)
    warm_observations = warm_start_observations(args)
    write_json(output_dir / "theta_bounds.json", bounds)
    write_json(output_dir / "preflight_summary.json", {
        "schema": "compact_v9_B4_s1forced_preflight.v1",
        "generated_at": utc_now(),
        "status": "PASS",
        "inputs": {
            "net_file": rel(args.net_file),
            "background_route": rel(args.background_route),
            "stage1_dir": rel(args.stage1_dir),
            "active_inputs": rel(args.active_inputs),
        },
        "decision_variables": bounds["decision_variables"],
        "rounds_per_seed": args.m,
        "theta_per_round": args.theta_per_round,
        "theta_evaluations_per_seed_method": args.m * args.theta_per_round,
        "workers": args.workers,
        "resume": args.resume,
        "bo_pass_focus": {
            "from_round": args.bo_pass_focus_from_round,
            "min_feasibility": args.bo_pass_focus_min_feasibility,
        },
        "active_inputs": preflight_payload["active_inputs"],
        "active_inputs_audit": preflight_payload["active_inputs_audit"],
        "controlled_static_inputs": static_inputs,
        "warm_start": {
            "csvs": [rel(path) for path in args.warm_start_csv],
            "observation_count": len(warm_observations),
            "unique_theta_count": len({theta_key(row) for row in warm_observations}),
            "bo_initial": args.bo_initial,
        },
    })
    spatial_subspaces = bo_spatial_subspaces(stage1)
    write_json(output_dir / "bo_spatial_subspaces.json", {
        "schema": "compact_v9_B4_bo_essi_spatial_subspaces.v1",
        "generated_at": utc_now(),
        "definition": "Stage1 route movements split by route_order; weights combine controllable movement density and Case B/bottleneck presence.",
        "subspace_count": len(spatial_subspaces),
        "subspaces": spatial_subspaces,
    })

    eval_args = build_eval_args(args, args.seed_base, args.run_root, output_dir)
    real_context = prepare_real_context_once(run_id, eval_args)
    if args.resume:
        existing_rows = existing_rows_outside_methods(output_dir, methods_to_run)
    else:
        existing_rows, _existing_final_rows = existing_method_rows(output_dir, methods_to_run, args.append_existing, args.w_E, args.w_G)
    seeds = [args.seed_base + index for index in range(args.n)]
    new_rows = run_method_seed_grid(run_id, bounds, args, real_context, args.w_E, args.w_G, stage1, methods_to_run, seeds)
    all_rows: list[dict[str, Any]] = dedupe_evaluation_rows([*existing_rows, *new_rows])

    best_table = build_best_so_far_table(all_rows, args.m)
    bo_table = build_bo_surrogate_table(all_rows)
    bo_gp_slice_table = build_bo_gp_slice_table(all_rows, bounds)
    all_rows_public = [{field: row.get(field, "") for field in EVALUATION_FIELDS} for row in all_rows]
    final_method_rows = [clean_final_result_row(row, args.w_E, args.w_G) for row in all_rows]
    write_csv(output_dir / "all_evaluations.csv", all_rows_public, EVALUATION_FIELDS)
    write_csv(output_dir / "final_method_comparison_results.csv", final_method_rows, FINAL_RESULT_FIELDS)
    write_csv(output_dir / "table1_best_so_far.csv", best_table, ["method", "seed", *[f"R{index}" for index in range(1, args.m + 1)]])
    write_csv(output_dir / "table2_bo_surrogate.csv", bo_table, BO_SURROGATE_FIELDS)
    if bo_gp_slice_table:
        write_csv(output_dir / "table2_bo_gp_slice.csv", bo_gp_slice_table, BO_GP_SLICE_FIELDS)

    pareto_rows: list[dict[str, Any]] = []
    sensitivity_spc_rows: list[dict[str, Any]] = []
    final_sensitivity_rows: list[dict[str, Any]] = []
    if not args.skip_pareto:
        pareto_rows, sensitivity_spc_rows, final_sensitivity_rows = run_pareto(run_id, bounds, args, eval_args, real_context, stage1)
        write_csv(output_dir / "table3_pareto.csv", pareto_rows, PARETO_FIELDS)
        write_csv(output_dir / "table4_sensitivity_spc.csv", sensitivity_spc_rows, SENSITIVITY_SPC_FIELDS)
        write_csv(output_dir / "final_sensitivity_results.csv", final_sensitivity_rows, FINAL_RESULT_FIELDS)

    noise_rows: list[dict[str, Any]] = []
    noise_summary: dict[str, Any] = {}
    if not args.skip_noise_check:
        noise_rows, noise_summary = run_noise_check(f"{run_id}_noise", bounds, args, eval_args, real_context)
        write_csv(output_dir / "noise_check_5repeat.csv", noise_rows, NOISE_FIELDS)
        write_json(output_dir / "noise_check_summary.json", noise_summary)

    plot_best_so_far(best_table, args.m, output_dir / "figure1_best_so_far.png")
    plot_bo_surrogate(bo_table, output_dir / "figure2_bo_surrogate.png", bo_gp_slice_table)
    if pareto_rows:
        plot_pareto(pareto_rows, output_dir / "figure3_pareto.png")
    if sensitivity_spc_rows:
        plot_sensitivity_spc(sensitivity_spc_rows, output_dir / "figure4_sensitivity_spc.png")

    outputs = {
        "all_evaluations_csv": rel(output_dir / "all_evaluations.csv"),
        "final_method_comparison_results_csv": rel(output_dir / "final_method_comparison_results.csv"),
        "table1_best_so_far_csv": rel(output_dir / "table1_best_so_far.csv"),
        "table2_bo_surrogate_csv": rel(output_dir / "table2_bo_surrogate.csv"),
        "table2_bo_gp_slice_csv": rel(output_dir / "table2_bo_gp_slice.csv") if bo_gp_slice_table else "",
        "table3_pareto_csv": rel(output_dir / "table3_pareto.csv") if pareto_rows else "",
        "table4_sensitivity_spc_csv": rel(output_dir / "table4_sensitivity_spc.csv") if sensitivity_spc_rows else "",
        "final_sensitivity_results_csv": rel(output_dir / "final_sensitivity_results.csv") if final_sensitivity_rows else "",
        "bo_spatial_subspaces_json": rel(output_dir / "bo_spatial_subspaces.json"),
        "noise_check_csv": rel(output_dir / "noise_check_5repeat.csv") if noise_rows else "",
        "figure1_png": rel(output_dir / "figure1_best_so_far.png"),
        "figure1_svg": rel(output_dir / "figure1_best_so_far.svg"),
        "figure2_png": rel(output_dir / "figure2_bo_surrogate.png"),
        "figure2_svg": rel(output_dir / "figure2_bo_surrogate.svg"),
        "figure3_png": rel(output_dir / "figure3_pareto.png") if pareto_rows else "",
        "figure3_svg": rel(output_dir / "figure3_pareto.svg") if pareto_rows else "",
        "figure4_png": rel(output_dir / "figure4_sensitivity_spc.png") if sensitivity_spc_rows else "",
        "figure4_svg": rel(output_dir / "figure4_sensitivity_spc.svg") if sensitivity_spc_rows else "",
    }
    summary = {
        "schema": "compact_v9_B4_s1forced_optimizer_comparison.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "mock_eval": args.mock_eval,
        "n": args.n,
        "m": args.m,
        "theta_per_round": args.theta_per_round,
        "theta_evaluations_per_seed_method": args.m * args.theta_per_round,
        "workers": args.workers,
        "methods": sorted({str(row.get("method", "")) for row in all_rows if row.get("method")}, key=METHODS.index),
        "methods_run_this_invocation": methods_to_run,
        "append_existing": args.append_existing,
        "resume": args.resume,
        "seeds": seeds,
        "bo_algorithm": "GP+ESSI",
        "objective": {"score": "(w_E / (w_E + w_G)) * D_E_sec + (w_G / (w_E + w_G)) * D_G_sec", "w_E": args.w_E, "w_G": args.w_G},
        "fixed_budget_policy": "m is the number of optimization rounds; each round evaluates theta_per_round theta candidates, and best-so-far records the cumulative minimum after the full round.",
        "pareto_protocol": {
            "purpose": "Present weight-sweep Pareto candidates to decision makers; do not choose the policy weight inside the analysis.",
            "weight_ratios": PARETO_WEIGHT_RATIOS,
            "search_runs_per_weight": 1,
            "repeat_policy": "Do not repeat the same weight unless an outlier or failed run requires verification.",
            "spc_stop": bool(args.pareto_spc_stop),
            "spc_note": "SPC early stop uses the ESSI log-max EWMA trace to stop once the search stabilizes.",
            "controlled_static_inputs": static_inputs,
        },
        "presentation_notes": {
            "figure1_band": "Shaded band is +/- standard deviation across seeds. With n=1, the band width is zero by design.",
            "figure2_slice": "GP estimate uses a 1D t_lead slice through the best BO theta. The objective is not re-evaluated on the slice grid.",
            "figure3_policy": "The orange marker is the computed knee candidate, not a final policy decision.",
        },
        "outputs": outputs,
        "noise_check": noise_summary,
    }
    write_json(output_dir / "experiment_summary.json", summary)
    write_json(args.output_dir / "latest.json", {"schema": "compact_v9_B4_s1forced_optimizer_latest.v1", "run_id": run_id, "output_dir": rel(output_dir), **outputs})
    return summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_args(args)
        result = collect_visualization_info(args) if args.collect_visualization_info else run_experiment(args)
    except (B4OptimizationError, theta_bo.B4ThetaBoError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
