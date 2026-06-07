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
    "delay_A",
    "delay_N",
    "score",
    "best_so_far",
    "penalty",
    "final_status",
    "failure_reason",
    "termination_reason",
    "emergency_arrived",
    "emergency_teleport",
    "background_teleported",
    "signal_event_count",
    "stage2_hold_count",
    "stage3_preemption_count",
    "surrogate_mean",
    "surrogate_ci_low",
    "surrogate_ci_high",
    "acquisition",
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
    "acquisition",
    *ESSI_FIELDS,
]
PARETO_FIELDS = [
    "weight_ratio",
    *THETA_FIELDS,
    "delay_A",
    "delay_N",
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
NOISE_FIELDS = ["repeat", *THETA_FIELDS, "delay_A", "delay_N", "score", "final_status"]
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
    "output_delay_A_sec",
    "output_delay_N_sec",
    "weight_A",
    "weight_N",
    "weight_ratio",
    "score",
    "measured_T_free_EMV_sec",
    "measured_T_actual_EMV_sec",
    "measured_d_EMV_sec",
    "measured_d_veh_sec",
    "measured_general_mean_travel_time_sec",
    "stage2_on_count",
    "stage3_on_count",
]


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
) -> dict[str, Any]:
    activations = essi_activation_values(theta, bounds, subspaces)
    while len(activations) < theta_bo.DEFAULT_SUBSPACE_COUNT:
        activations.append(0.0)
    essi_values = [max(0.0, gp_improvement) * value for value in activations[: theta_bo.DEFAULT_SUBSPACE_COUNT]]
    spatial_activation = max(activations) if activations else 0.0
    dominant_index = (max(range(len(activations)), key=lambda idx: activations[idx]) + 1) if activations else ""
    essi_max = max(essi_values) if essi_values else 0.0
    essi_mean = sum(essi_values) / len(essi_values) if essi_values else 0.0
    fields: dict[str, Any] = {
        "essi_acquisition": sec(essi_max),
        "essi_max": sec(essi_max),
        "essi_mean": sec(essi_mean),
        "essi_log_max": f"{math.log(essi_max + theta_bo.ESSI_EPS):.8f}",
        "dominant_essi_subspace": dominant_index,
        "spatial_activation_score": f"{spatial_activation:.6f}",
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
        gp_improvement = safe_float(item.get("acquisition"), 0.0)
        essi = essi_fields_for_candidate(theta, bounds, subspaces, gp_improvement)
        out.append({**theta, **essi, "acquisition": essi["essi_acquisition"]})
    if not out:
        raise B4OptimizationError("gp_essi_unavailable:no_unique_candidates")
    out.sort(key=lambda row: (-safe_float(row.get("essi_acquisition")), theta_key(row)))
    return out


def normalize_objective_weights(w_emv: float, w_veh: float) -> tuple[float, float]:
    total = float(w_emv) + float(w_veh)
    if total <= 0.0:
        raise B4OptimizationError("objective_weight_sum_must_be_positive")
    return float(w_emv) / total, float(w_veh) / total


def score_delay_row(row: dict[str, Any], w_emv: float, w_veh: float) -> tuple[float, float, float, float, float]:
    delay_a = safe_float(row.get("d_EMV_sec"), safe_float(row.get("T_actual_EMV_sec"), 0.0))
    delay_n = safe_float(row.get("d_veh_sec"), safe_float(row.get("general_mean_travel_time_sec"), 0.0))
    w_a, w_n = normalize_objective_weights(w_emv, w_veh)
    score = w_a * delay_a + w_n * delay_n
    failed = (
        row.get("final_status") not in {"PASS", "WARNING"}
        or not bool_cell(row.get("emergency_arrived"))
        or bool_cell(row.get("emergency_teleport"))
        or bool_cell(row.get("failed"))
    )
    penalty = FAILURE_PENALTY_SEC if failed else 0.0
    return round(delay_a, 6), round(delay_n, 6), round(score, 6), penalty, round(score + penalty, 6)


def weight_ratio_label(w_emv: float, w_veh: float) -> str:
    return f"{w_emv:g}:{w_veh:g}"


def clean_final_result_row(row: dict[str, Any], w_emv: float, w_veh: float, weight_ratio: str | None = None) -> dict[str, Any]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    source = raw if raw else row
    label = weight_ratio or weight_ratio_label(w_emv, w_veh)
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
        "output_delay_A_sec": row.get("delay_A", ""),
        "output_delay_N_sec": row.get("delay_N", ""),
        "weight_A": sec(w_emv),
        "weight_N": sec(w_veh),
        "weight_ratio": label,
        "score": row.get("raw_score", row.get("score", "")),
        "measured_T_free_EMV_sec": source.get("T_free_EMV_sec", ""),
        "measured_T_actual_EMV_sec": source.get("T_actual_EMV_sec", ""),
        "measured_d_EMV_sec": source.get("d_EMV_sec", row.get("delay_A", "")),
        "measured_d_veh_sec": source.get("d_veh_sec", row.get("delay_N", "")),
        "measured_general_mean_travel_time_sec": source.get("general_mean_travel_time_sec", ""),
        "stage2_on_count": source.get("stage2_hold_count", row.get("stage2_hold_count", "")),
        "stage3_on_count": source.get("stage3_preemption_count", row.get("stage3_preemption_count", "")),
    }


def existing_method_rows(
    output_dir: Path,
    methods_to_run: list[str],
    append_existing: bool,
    w_emv: float,
    w_veh: float,
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
        final_rows = [clean_final_result_row(row, w_emv, w_veh) for row in existing_rows]
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

    active_inputs: dict[str, Any] = {}
    if args.active_inputs.is_file():
        active_inputs = read_json(args.active_inputs)
        expected = {
            "net_file": rel(args.net_file),
            "background_route": rel(args.background_route),
            "stage1_dir": rel(args.stage1_dir),
        }
        for key, value in expected.items():
            if active_inputs.get(key) != value:
                raise B4OptimizationError(f"active_inputs_{key}_mismatch:{active_inputs.get(key)} != {value}")
        for key in ["signal_profile_csv", "signal_mapping_csv"]:
            source = active_inputs.get(key)
            if source and not (PROJECT_ROOT / str(source)).is_file():
                raise B4OptimizationError(f"missing_active_inputs_{key}:{source}")

    return {
        "stage1": stage1,
        "bounds": bounds,
        "active_inputs": active_inputs,
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
        w_emv=args.w_emv,
        w_veh=args.w_veh,
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
    w_emv: float,
    w_veh: float,
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
    delay_a, delay_n, score, penalty, penalized_score = score_delay_row(raw, w_emv, w_veh)
    return {
        "method": method,
        "seed": seed,
        "round": round_index,
        **{field: theta.get(field, "") for field in THETA_FIELDS},
        "delay_A": sec(delay_a),
        "delay_N": sec(delay_n),
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
        float(job["w_emv"]),
        float(job["w_veh"]),
    )
    row["round_theta_index"] = int(job["round_theta_index"])
    row["theta_per_round"] = int(job["theta_per_round"])
    row.update(dict(job.get("prediction") or {}))
    bo_fields = dict(job.get("bo_fields") or {})
    if bo_fields:
        row.update(bo_fields)
        row["acquisition"] = bo_fields.get("essi_acquisition", bo_fields.get("acquisition", ""))
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
    kernel = ConstantKernel(1.0, constant_value_bounds="fixed") * Matern(nu=2.5) + WhiteKernel(noise_level=1.0e-6, noise_level_bounds="fixed")
    try:
        gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, random_state=0, alpha=1.0e-8, n_restarts_optimizer=0)
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
    w_emv: float,
    w_veh: float,
    stage1: B4Stage1Inputs,
    stop_on_spc: bool = False,
    prior_rows: list[dict[str, Any]] | None = None,
    checkpoint_callback: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    round_rows: list[dict[str, Any]] = []
    existing: set[tuple[float, float, float, float, float]] = set()
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
        row = evaluate_theta(run_id, method, seed, round_index, theta, args, eval_args, real_context, w_emv, w_veh)
        row["round_theta_index"] = round_theta_index
        row["theta_per_round"] = args.theta_per_round
        row.update(prediction)
        if bo_fields:
            row.update(bo_fields)
            row["acquisition"] = bo_fields.get("essi_acquisition", bo_fields.get("acquisition", ""))
        else:
            row["acquisition"] = ""
        return row

    def record_row(row: dict[str, Any], round_index: int, round_theta_index: int) -> dict[str, Any]:
        observations.append({
            "mode": B4_MODE,
            **{field: row.get(field, "") for field in THETA_FIELDS},
            "score_sec": row["score"],
            "bo_score_sec": row["score"],
            "score": row["score"],
        })
        if method == "BO":
            best = min(observations, key=lambda item: safe_float(item.get("bo_score_sec"), float("inf")))
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
            checkpoint_callback([*rows, *completed_rows])

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
                                "w_emv": w_emv,
                                "w_veh": w_veh,
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
            checkpoint_callback(rows)
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
            else:
                ranked = essi_improvement_candidates(observations, bounds, stage1, seed + round_index, existing, args.ei_candidate_count)
                batch = [theta_bo.clamp_theta(item, bounds) for item in ranked[: args.theta_per_round]]
                batch_fields = [{field: item.get(field, "") for field in ESSI_FIELDS} for item in ranked[: args.theta_per_round]]
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
        checkpoint_callback(rows)
    return rows


def run_method_with_checkpoint(
    method: str,
    seed: int,
    run_id: str,
    bounds: dict[str, Any],
    args: argparse.Namespace,
    real_context: dict[str, Any] | None,
    w_emv: float,
    w_veh: float,
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
        w_emv,
        w_veh,
        stage1,
        prior_rows=prior_rows,
        checkpoint_callback=checkpoint,
    )


def run_method_seed_grid(
    run_id: str,
    bounds: dict[str, Any],
    args: argparse.Namespace,
    real_context: dict[str, Any] | None,
    w_emv: float,
    w_veh: float,
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
            completed.extend(run_method_with_checkpoint(method, seed, run_id, bounds, args, real_context, w_emv, w_veh, stage1, prior_rows))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {
                executor.submit(run_method_with_checkpoint, method, seed, run_id, bounds, args, real_context, w_emv, w_veh, stage1, prior_rows): (method, seed)
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
            "acquisition": row.get("acquisition", ""),
            **{field: row.get(field, "") for field in ESSI_FIELDS},
        })
    return out


def parse_weight_ratio(weight_ratio: str) -> tuple[float, float]:
    left, right = weight_ratio.split(":")
    return float(left), float(right)


def normalize_pareto_weights(weight_ratio: str) -> tuple[float, float]:
    return normalize_objective_weights(*parse_weight_ratio(weight_ratio))


def knee_index(rows: list[dict[str, Any]]) -> int:
    if len(rows) <= 2:
        return max(0, len(rows) // 2)
    points = [(safe_float(row["delay_N"]), safe_float(row["delay_A"])) for row in rows]
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
    for weight_ratio in ["1:1", "5:1", "10:1", "15:1", "20:1"]:
        w_emv, w_veh = normalize_pareto_weights(weight_ratio)
        raw_w_emv, raw_w_veh = parse_weight_ratio(weight_ratio)
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
            w_emv,
            w_veh,
            stage1,
            stop_on_spc=args.pareto_spc_stop,
            prior_rows=[dict(row) for row in prior_rows],
            checkpoint_callback=lambda current_rows, path=checkpoint: write_checkpoint_rows(path, current_rows),
        )
        best = min(rows, key=lambda row: safe_float(row.get("score"), float("inf")))
        final_rows.append(clean_final_result_row(best, raw_w_emv, raw_w_veh, weight_ratio))
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
            "delay_A": best.get("delay_A", ""),
            "delay_N": best.get("delay_N", ""),
            "score": best.get("score", ""),
            "rounds_completed": len(rows),
            "essi_max": spc_source.get("essi_max", ""),
            "essi_log_max": spc_source.get("essi_log_max", ""),
            "essi_log_max_ewma": spc_source.get("essi_log_max_ewma", ""),
            "spc_status": spc_source.get("essi_spc_status", ""),
            "spc_stop_recommended": "True" if stop_rows else "False",
            "spc_stop_round": stop_rows[0].get("round", "") if stop_rows else "",
            "is_knee": "False",
        })
    pareto_rows.sort(key=lambda row: safe_float(row["delay_N"]))
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
        row = evaluate_theta(run_id, "Noise Check", args.seed_base, repeat, theta, args, eval_args, real_context, args.w_emv, args.w_veh, repeat_id=repeat)
        rows.append({
            "repeat": repeat,
            **{field: theta.get(field, "") for field in THETA_FIELDS},
            "delay_A": row["delay_A"],
            "delay_N": row["delay_N"],
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


def plot_best_so_far(table: list[dict[str, Any]], m: int, output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore
    import numpy as np  # type: ignore

    output.parent.mkdir(parents=True, exist_ok=True)
    rounds = np.arange(1, m + 1)
    plt.figure(figsize=(9, 5.2))
    for method in METHODS:
        series = [
            [safe_float(row.get(f"R{index}")) for index in range(1, m + 1)]
            for row in table
            if row.get("method") == method
        ]
        if not series:
            continue
        values = np.array(series, dtype=float)
        mean = values.mean(axis=0)
        ci = 1.96 * values.std(axis=0, ddof=1) / math.sqrt(values.shape[0]) if values.shape[0] > 1 else np.zeros(m)
        plt.plot(rounds, mean, label=method, linewidth=2)
        plt.fill_between(rounds, mean - ci, mean + ci, alpha=0.18)
    plt.xlabel("Round")
    plt.ylabel("Best-so-far Score")
    plt.title("Fixed-budget Optimizer Comparison")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output, dpi=180)
    plt.close()


def plot_bo_surrogate(table: list[dict[str, Any]], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore

    output.parent.mkdir(parents=True, exist_ok=True)
    if not table:
        raise B4OptimizationError("bo_surrogate_table_empty")
    seed = table[0]["seed"]
    rows = [row for row in table if row.get("seed") == seed]
    rounds = [int(row["round"]) for row in rows]
    observed = [safe_float(row["observed_score"]) for row in rows]
    best = [safe_float(row["best_so_far"]) for row in rows]
    surrogate = [safe_float(row["surrogate_mean"], float("nan")) for row in rows]
    low = [safe_float(row["surrogate_ci_low"], float("nan")) for row in rows]
    high = [safe_float(row["surrogate_ci_high"], float("nan")) for row in rows]
    plt.figure(figsize=(9, 5.2))
    plt.scatter(rounds, observed, s=26, label="Observed", color="#2b6cb0")
    plt.plot(rounds, best, label="Best-so-far", color="#222222", linewidth=2)
    plt.plot(rounds, surrogate, label="Surrogate mean", color="#c05621", linewidth=1.8)
    plt.fill_between(rounds, low, high, color="#c05621", alpha=0.16, label="95% CI")
    plt.xlabel("Round")
    plt.ylabel("Score")
    plt.title(f"BO Surrogate Trace (seed={seed})")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output, dpi=180)
    plt.close()


def plot_pareto(rows: list[dict[str, Any]], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore

    output.parent.mkdir(parents=True, exist_ok=True)
    x = [safe_float(row["delay_N"]) for row in rows]
    y = [safe_float(row["delay_A"]) for row in rows]
    plt.figure(figsize=(7.2, 5.2))
    plt.plot(x, y, color="#4a5568", linewidth=1.2, alpha=0.7)
    for row in rows:
        color = "#c53030" if row.get("is_knee") == "True" else "#2b6cb0"
        size = 82 if row.get("is_knee") == "True" else 54
        plt.scatter([safe_float(row["delay_N"])], [safe_float(row["delay_A"])], color=color, s=size, zorder=3)
        plt.annotate(row["weight_ratio"], (safe_float(row["delay_N"]), safe_float(row["delay_A"])), textcoords="offset points", xytext=(5, 5), fontsize=9)
    plt.xlabel("General Vehicle Delay")
    plt.ylabel("Emergency Vehicle Delay")
    plt.title("Weight Sweep Pareto Candidates")
    plt.grid(True, alpha=0.25)
    plt.tight_layout()
    plt.savefig(output, dpi=180)
    plt.close()


def plot_sensitivity_spc(rows: list[dict[str, Any]], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore

    output.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(9, 5.2))
    for weight_ratio in ["1:1", "5:1", "10:1", "15:1", "20:1"]:
        group = [row for row in rows if row.get("weight_ratio") == weight_ratio]
        if not group:
            continue
        x = [int(safe_float(row.get("round"), 0.0)) for row in group]
        y = [safe_float(row.get("essi_log_max_ewma"), float("nan")) for row in group]
        plt.plot(x, y, marker="o", linewidth=1.6, label=weight_ratio)
    plt.xlabel("BO Round")
    plt.ylabel("ESSI log-max EWMA")
    plt.title("Sensitivity Sweep ESSI/SPC Trace")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output, dpi=180)
    plt.close()


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
    parser.add_argument("--w-emv", "--w1", dest="w_emv", type=float, default=10.0)
    parser.add_argument("--w-veh", "--w2", dest="w_veh", type=float, default=1.0)
    parser.add_argument("--ei-candidate-count", "--essi-candidate-count", dest="ei_candidate_count", type=int, default=theta_bo.DEFAULT_EI_CANDIDATE_COUNT)
    parser.add_argument("--spc-window", type=int, default=theta_bo.DEFAULT_SPC_WINDOW)
    parser.add_argument("--spc-alpha", type=float, default=theta_bo.DEFAULT_SPC_ALPHA)
    parser.add_argument("--spc-min-rounds", type=int, default=theta_bo.DEFAULT_SPC_MIN_ROUNDS)
    parser.add_argument("--spc-min-improvement-sec", type=float, default=theta_bo.DEFAULT_SPC_MIN_IMPROVEMENT_SEC)
    parser.add_argument("--methods", nargs="+", default=None, help="Run only selected methods. Accepts BO, CMA-ES/cma, Random Search/random/rs.")
    parser.add_argument("--bo-first", action="store_true", help="Run BO before the other selected methods in a single invocation.")
    parser.add_argument("--append-existing", action="store_true", help="Merge selected method results into an existing run-id instead of starting from an empty comparison.")
    parser.add_argument("--resume", "--bo-resume", dest="resume", action="store_true", help="Resume an interrupted run-id from per-method/seed checkpoints and existing all_evaluations.csv rows.")
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
    if not 2 <= args.bo_initial < args.m:
        raise B4OptimizationError("bo_initial_must_be_between_2_and_m_minus_1")
    if args.workers < 1:
        raise B4OptimizationError("workers_must_be_positive")
    if args.materialize_visualization_logs and args.mock_eval:
        raise B4OptimizationError("materialize_visualization_logs_requires_real_eval")
    if args.w_emv < 0.0 or args.w_veh < 0.0:
        raise B4OptimizationError("weights_must_be_nonnegative")
    if args.w_emv + args.w_veh <= 0.0:
        raise B4OptimizationError("weight_sum_must_be_positive")
    if args.ei_candidate_count < 2:
        raise B4OptimizationError("ei_candidate_count_must_be_at_least_2")
    if args.ei_candidate_count < args.theta_per_round:
        raise B4OptimizationError("ei_candidate_count_must_be_at_least_theta_per_round")
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
    args.visualization_output = args.visualization_output.resolve() if args.visualization_output else None


def run_experiment(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or default_run_id()
    output_dir = args.output_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    methods_to_run = selected_methods(args)
    preflight_payload = preflight(args)
    stage1: B4Stage1Inputs = preflight_payload["stage1"]
    bounds = preflight_payload["bounds"]
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
        "active_inputs": preflight_payload["active_inputs"],
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
        existing_rows, _existing_final_rows = existing_method_rows(output_dir, methods_to_run, args.append_existing, args.w_emv, args.w_veh)
    seeds = [args.seed_base + index for index in range(args.n)]
    new_rows = run_method_seed_grid(run_id, bounds, args, real_context, args.w_emv, args.w_veh, stage1, methods_to_run, seeds)
    all_rows: list[dict[str, Any]] = dedupe_evaluation_rows([*existing_rows, *new_rows])

    best_table = build_best_so_far_table(all_rows, args.m)
    bo_table = build_bo_surrogate_table(all_rows)
    all_rows_public = [{field: row.get(field, "") for field in EVALUATION_FIELDS} for row in all_rows]
    final_method_rows = [clean_final_result_row(row, args.w_emv, args.w_veh) for row in all_rows]
    write_csv(output_dir / "all_evaluations.csv", all_rows_public, EVALUATION_FIELDS)
    write_csv(output_dir / "final_method_comparison_results.csv", final_method_rows, FINAL_RESULT_FIELDS)
    write_csv(output_dir / "table1_best_so_far.csv", best_table, ["method", "seed", *[f"R{index}" for index in range(1, args.m + 1)]])
    write_csv(output_dir / "table2_bo_surrogate.csv", bo_table, BO_SURROGATE_FIELDS)

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
    plot_bo_surrogate(bo_table, output_dir / "figure2_bo_surrogate.png")
    if pareto_rows:
        plot_pareto(pareto_rows, output_dir / "figure3_pareto.png")
    if sensitivity_spc_rows:
        plot_sensitivity_spc(sensitivity_spc_rows, output_dir / "figure4_sensitivity_spc.png")

    outputs = {
        "all_evaluations_csv": rel(output_dir / "all_evaluations.csv"),
        "final_method_comparison_results_csv": rel(output_dir / "final_method_comparison_results.csv"),
        "table1_best_so_far_csv": rel(output_dir / "table1_best_so_far.csv"),
        "table2_bo_surrogate_csv": rel(output_dir / "table2_bo_surrogate.csv"),
        "table3_pareto_csv": rel(output_dir / "table3_pareto.csv") if pareto_rows else "",
        "table4_sensitivity_spc_csv": rel(output_dir / "table4_sensitivity_spc.csv") if sensitivity_spc_rows else "",
        "final_sensitivity_results_csv": rel(output_dir / "final_sensitivity_results.csv") if final_sensitivity_rows else "",
        "bo_spatial_subspaces_json": rel(output_dir / "bo_spatial_subspaces.json"),
        "noise_check_csv": rel(output_dir / "noise_check_5repeat.csv") if noise_rows else "",
        "figure1_png": rel(output_dir / "figure1_best_so_far.png"),
        "figure2_png": rel(output_dir / "figure2_bo_surrogate.png"),
        "figure3_png": rel(output_dir / "figure3_pareto.png") if pareto_rows else "",
        "figure4_png": rel(output_dir / "figure4_sensitivity_spc.png") if sensitivity_spc_rows else "",
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
        "objective": {"score": "(w_emv / (w_emv + w_veh)) * delay_A + (w_veh / (w_emv + w_veh)) * delay_N", "w_emv": args.w_emv, "w_veh": args.w_veh},
        "fixed_budget_policy": "m is the number of optimization rounds; each round evaluates theta_per_round theta candidates, and best-so-far records the cumulative minimum after the full round.",
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
