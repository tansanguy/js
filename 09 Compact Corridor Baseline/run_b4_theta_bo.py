#!/usr/bin/env python3
"""Independent 5-variable Bayesian optimization runner for Compact V9 B4."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
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

from b4_runtime import (  # noqa: E402
    B04_AA_BACKGROUND_ROUTE,
    B04_MODE,
    B04_NET,
    B4_MODE,
    B4RuntimeError,
    B4RuntimePhaseConfig,
    B4Stage1Inputs,
    B4ThetaParams,
    EVTSP_TAU_LOWER,
    EVTSP_TAU_UPPER,
    EXPERIMENT_RESULT_FIELDS,
    STAGE1_DIR,
    theta_bounds_from_stage1,
    write_csv,
    safe_float,
)
from run_b0_b4_signal_pipeline import (  # noqa: E402
    B4RunTask,
    B4RunnerError,
    build_b004_free_reference,
    read_free_vehicle_rows,
    run_b04_task,
    run_b4_task,
    validate_static_inputs,
)


DEFAULT_OUTPUT_PREFIX = "compact_v9_B4_theta_bo"
RUNS_ROOT = PROJECT_ROOT / "runs"
METRICS_PARENT_ROOT = PROJECT_ROOT / "results/metrics"
RUN_ROOT = RUNS_ROOT / DEFAULT_OUTPUT_PREFIX
METRICS_ROOT = METRICS_PARENT_ROOT / DEFAULT_OUTPUT_PREFIX
DEFAULT_INITIAL_COUNT = 15
DEFAULT_BO_ROUNDS = 8
DEFAULT_BATCH_SIZE = 3
DEFAULT_REPEATS = 1
DEFAULT_SEED = 20260605
W_EMV_THETA = 10.0
W_VEH_THETA = 1.0
FAILURE_PENALTY_SEC = 1_000_000.0
DEFAULT_EI_CANDIDATE_COUNT = 600
DEFAULT_SUBSPACE_COUNT = 6
DEFAULT_SPC_WINDOW = 5
DEFAULT_SPC_ALPHA = 0.30
DEFAULT_SPC_MIN_ROUNDS = 15
DEFAULT_SPC_MIN_IMPROVEMENT_SEC = 1.0
ESSI_EPS = 1.0e-12
DEFAULT_TAU_NUMERATOR_GAMMA = 5.0
STRUCTURE_PARAM_FIELDS = ["hold_max", "d_up"]

THETA_FIELDS = ["parameter_id", "t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]
SCORE_FIELDS = [
    "run_id",
    "round",
    "parameter_id",
    "seed",
    "repeat_id",
    "t_lead",
    "delta_T_thr",
    "G_ext",
    "Q_ratio",
    "tau",
    "T_actual_EMV_sec",
    "d_EMV_sec",
    "general_mean_travel_time_sec",
    "d_veh_sec",
    "score_sec",
    "bo_score_sec",
    "final_status",
]
TOP20_FIELDS = [
    "rank",
    *SCORE_FIELDS,
    "failure_reason",
    "emergency_arrived",
    "emergency_teleport",
    "emergency_stuck_duration_sec",
    "background_arrived_ratio",
    "general_mean_delay_sec",
    "signal_burden_sec",
    "signal_events_csv",
]
ALL_VALUE_FIELDS = list(dict.fromkeys([
    "bo_round",
    "bo_phase",
    *THETA_FIELDS,
    "score_sec",
    "bo_score_sec",
    "failure_penalty_sec",
    *EXPERIMENT_RESULT_FIELDS,
]))


class B4ThetaBoError(RuntimeError):
    """Expected B4 theta BO failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_run_id() -> str:
    return "b4_theta_bo_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def default_workers() -> int:
    cpus = os.cpu_count() or 2
    return max(1, min(cpus - 2, 8))


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def default_structure_params() -> dict[str, Any]:
    return {
        "hold_max": B4ThetaParams.hold_max,
        "d_up": B4ThetaParams.d_up,
    }


def read_structure_lock(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    selected = payload.get("selected_structure")
    if not isinstance(selected, dict):
        raise B4ThetaBoError("structure_lock_missing_selected_structure")
    structure = default_structure_params()
    for field in STRUCTURE_PARAM_FIELDS:
        if field in selected:
            structure[field] = selected[field]
    return {
        "path": rel(path),
        "lock_status": payload.get("lock_status", ""),
        "selected_structure": structure,
    }


def apply_structure_params(args: argparse.Namespace) -> None:
    structure = default_structure_params()
    lock_info: dict[str, Any] = {}
    if getattr(args, "structure_lock_json", None):
        args.structure_lock_json = Path(args.structure_lock_json).resolve()
        if not args.structure_lock_json.is_file():
            raise B4ThetaBoError(f"missing_structure_lock_json:{args.structure_lock_json}")
        lock_info = read_structure_lock(args.structure_lock_json)
        structure.update(lock_info["selected_structure"])

    for field in STRUCTURE_PARAM_FIELDS:
        value = getattr(args, field, None)
        if value is not None:
            structure[field] = value

    normalized = B4ThetaParams.from_row(structure)
    args.hold_max = normalized.hold_max
    args.d_up = normalized.d_up
    args.structure_lock_info = lock_info


def structure_inputs(args: argparse.Namespace) -> dict[str, Any]:
    inputs = {field: getattr(args, field) for field in STRUCTURE_PARAM_FIELDS}
    lock_info = getattr(args, "structure_lock_info", {}) or {}
    if lock_info:
        inputs["structure_lock_json"] = lock_info.get("path", "")
        inputs["structure_lock_status"] = lock_info.get("lock_status", "")
    return inputs


def bool_cell(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def sec(value: Any) -> str:
    if value in {"", None}:
        return ""
    return f"{float(value):.2f}"


def theta_key(row: dict[str, Any]) -> tuple[float, float, float, float, float]:
    return (
        round(safe_float(row.get("t_lead")), 0),
        round(safe_float(row.get("delta_T_thr")), 0),
        round(safe_float(row.get("G_ext")), 0),
        round(safe_float(row.get("Q_ratio"), safe_float(row.get("Q_trig"), 0.0) / 50.0), 2),
        round(safe_float(row.get("tau"), 0.75), 2),
    )


def theta_id(prefix: str, rank: int, theta: dict[str, Any]) -> str:
    return (
        f"{prefix}_{rank:03d}"
        f"_tl{int(round(safe_float(theta['t_lead'])))}"
        f"_dt{int(round(safe_float(theta['delta_T_thr'])))}"
        f"_ge{int(round(safe_float(theta['G_ext'])))}"
        f"_qr{int(round(safe_float(theta['Q_ratio']) * 100))}"
        f"_tau{int(round(safe_float(theta['tau']) * 100))}"
    )


def clamp_theta(theta: dict[str, Any], bounds: dict[str, Any], parameter_id: str | None = None) -> dict[str, Any]:
    clamped = {
        "t_lead": int(round(max(bounds["t_lead"]["lower"], min(bounds["t_lead"]["upper"], safe_float(theta.get("t_lead")))))),
        "delta_T_thr": int(round(max(bounds["delta_T_thr"]["lower"], min(bounds["delta_T_thr"]["upper"], safe_float(theta.get("delta_T_thr")))))),
        "G_ext": int(round(max(bounds["G_ext"]["lower"], min(bounds["G_ext"]["upper"], safe_float(theta.get("G_ext")))))),
        "Q_ratio": round(max(bounds["Q_ratio"]["lower"], min(bounds["Q_ratio"]["upper"], safe_float(theta.get("Q_ratio"), safe_float(theta.get("Q_trig"), 0.0) / 50.0))), 2),
        "tau": round(max(bounds["tau"]["lower"], min(bounds["tau"]["upper"], safe_float(theta.get("tau"), 0.75))), 2),
    }
    clamped["parameter_id"] = parameter_id or str(theta.get("parameter_id") or theta_id("theta", 1, clamped))
    return clamped


def random_rounded_real(rng: random.Random, bounds: dict[str, Any], name: str) -> float:
    lower = safe_float(bounds[name]["lower"])
    upper = safe_float(bounds[name]["upper"])
    step = safe_float(bounds[name].get("step"), 0.01)
    steps = int(round((upper - lower) / step))
    return round(lower + rng.randint(0, steps) * step, 2)


def random_theta_samples(bounds: dict[str, Any], count: int, seed: int, prefix: str, existing: set[tuple[float, float, float, float, float]] | None = None) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    existing = set(existing or set())
    samples: list[dict[str, Any]] = []
    attempts = 0
    while len(samples) < count and attempts < count * 200:
        attempts += 1
        row = {
            "t_lead": rng.randint(int(bounds["t_lead"]["lower"]), int(bounds["t_lead"]["upper"])),
            "delta_T_thr": rng.randint(int(bounds["delta_T_thr"]["lower"]), int(bounds["delta_T_thr"]["upper"])),
            "G_ext": rng.randint(int(bounds["G_ext"]["lower"]), int(bounds["G_ext"]["upper"])),
            "Q_ratio": random_rounded_real(rng, bounds, "Q_ratio"),
            "tau": random_rounded_real(rng, bounds, "tau"),
        }
        key = theta_key(row)
        if key in existing:
            continue
        existing.add(key)
        row["parameter_id"] = theta_id(prefix, len(samples) + 1, row)
        samples.append(row)
    if len(samples) < count:
        raise B4ThetaBoError("theta_search_space_exhausted")
    return samples


ROUND_FIELDS = [
    "round",
    "phase",
    "recommendation_csv",
    "result_count",
    "best_parameter_id",
    "best_bo_score_sec",
    "essi_1",
    "essi_2",
    "essi_3",
    "essi_4",
    "essi_5",
    "essi_6",
    "essi_max",
    "essi_mean",
    "essi_log_max",
    "essi_log_max_ewma",
    "essi_spc_status",
    "essi_stop_recommended",
    "essi_best_improvement_sec",
]


def score_for_row(row: dict[str, Any], w_emv: float = W_EMV_THETA, w_veh: float = W_VEH_THETA) -> tuple[float, float, float]:
    delay_a = safe_float(row.get("d_EMV_sec"), safe_float(row.get("T_actual_EMV_sec"), 0.0))
    delay_n = safe_float(row.get("d_veh_sec"), safe_float(row.get("general_mean_travel_time_sec"), 0.0))
    total_weight = float(w_emv) + float(w_veh)
    if total_weight <= 0.0:
        raise B4ThetaBoError("objective_weight_sum_must_be_positive")
    score = (float(w_emv) / total_weight) * delay_a + (float(w_veh) / total_weight) * delay_n
    failed = (
        row.get("final_status") not in {"PASS", "WARNING"}
        or not bool_cell(row.get("emergency_arrived"))
        or bool_cell(row.get("emergency_teleport"))
        or bool_cell(row.get("failed"))
    )
    penalty = FAILURE_PENALTY_SEC if failed else 0.0
    return round(score, 6), penalty, round(score + penalty, 6)


def append_scores(
    row: dict[str, Any],
    theta: dict[str, Any],
    round_index: int,
    phase: str,
    w_emv: float = W_EMV_THETA,
    w_veh: float = W_VEH_THETA,
) -> dict[str, Any]:
    score, penalty, bo_score = score_for_row(row, w_emv, w_veh)
    row.update({
        "bo_round": round_index,
        "bo_phase": phase,
        "score_sec": sec(score),
        "bo_score_sec": sec(bo_score),
        "failure_penalty_sec": sec(penalty),
        **{field: theta[field] for field in THETA_FIELDS},
    })
    return row


def score_summary_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": row.get("run_id", ""),
        "round": row.get("bo_round", ""),
        "parameter_id": row.get("parameter_id", ""),
        "seed": row.get("seed", ""),
        "repeat_id": row.get("repeat_id", ""),
        "t_lead": row.get("t_lead", ""),
        "delta_T_thr": row.get("delta_T_thr", ""),
        "G_ext": row.get("G_ext", ""),
        "Q_ratio": row.get("Q_ratio", ""),
        "tau": row.get("tau", ""),
        "T_actual_EMV_sec": row.get("T_actual_EMV_sec", ""),
        "d_EMV_sec": row.get("d_EMV_sec", ""),
        "general_mean_travel_time_sec": row.get("general_mean_travel_time_sec", ""),
        "d_veh_sec": row.get("d_veh_sec", ""),
        "score_sec": row.get("score_sec", ""),
        "bo_score_sec": row.get("bo_score_sec", ""),
        "final_status": row.get("final_status", ""),
    }


def top20_ranked_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [row for row in rows if row.get("mode") == B4_MODE]
    ranked = sorted(
        candidates,
        key=lambda row: (
            safe_float(row.get("bo_score_sec"), float("inf")),
            safe_float(row.get("signal_burden_sec"), float("inf")),
            safe_float(row.get("general_mean_delay_sec"), float("inf")),
            safe_float(row.get("emergency_stuck_duration_sec"), float("inf")),
            str(row.get("parameter_id", "")),
        ),
    )[:20]
    output: list[dict[str, Any]] = []
    for rank, row in enumerate(ranked, start=1):
        output.append({
            "rank": rank,
            **score_summary_row(row),
            "failure_reason": row.get("failure_reason", ""),
            "emergency_arrived": row.get("emergency_arrived", ""),
            "emergency_teleport": row.get("emergency_teleport", ""),
            "emergency_stuck_duration_sec": row.get("emergency_stuck_duration_sec", ""),
            "background_arrived_ratio": row.get("background_arrived_ratio", ""),
            "general_mean_delay_sec": row.get("general_mean_delay_sec", ""),
            "signal_burden_sec": row.get("signal_burden_sec", ""),
            "signal_events_csv": row.get("signal_events_csv", ""),
        })
    return output


def aggregate_observations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[float, float, float, float, float], dict[str, Any]] = {}
    for row in rows:
        if row.get("mode") != B4_MODE:
            continue
        key = theta_key(row)
        entry = grouped.setdefault(key, {
            "parameter_id": row.get("parameter_id", ""),
            "t_lead": key[0],
            "delta_T_thr": key[1],
            "G_ext": key[2],
            "Q_ratio": key[3],
            "tau": key[4],
            "bo_scores": [],
            "score_values": [],
            "repeat_count": 0,
        })
        entry["bo_scores"].append(safe_float(row.get("bo_score_sec")))
        entry["score_values"].append(safe_float(row.get("score_sec")))
        entry["repeat_count"] += 1
    aggregated: list[dict[str, Any]] = []
    for entry in grouped.values():
        bo_scores = entry.pop("bo_scores")
        score_values = entry.pop("score_values")
        entry["bo_score_sec"] = sum(bo_scores) / len(bo_scores)
        entry["score_sec"] = sum(score_values) / len(score_values)
        aggregated.append(entry)
    return sorted(aggregated, key=lambda row: (float(row["bo_score_sec"]), theta_key(row)))


def theta_feature_vector(row: dict[str, Any], bounds: dict[str, Any]) -> list[float]:
    def scale_continuous(name: str) -> float:
        lower = safe_float(bounds[name]["lower"])
        upper = safe_float(bounds[name]["upper"])
        width = max(upper - lower, 1.0)
        return (safe_float(row.get(name), lower) - lower) / width

    def scale_category(name: str) -> float:
        values = list(bounds[name]["values"])
        if len(values) <= 1:
            return 0.0
        closest = min(range(len(values)), key=lambda index: abs(float(values[index]) - safe_float(row.get(name), float(values[0]))))
        return closest / float(len(values) - 1)

    return [
        scale_continuous("t_lead"),
        scale_continuous("delta_T_thr"),
        scale_continuous("G_ext"),
        scale_continuous("Q_ratio"),
        scale_continuous("tau"),
    ]


def random_theta_candidates(
    bounds: dict[str, Any],
    count: int,
    seed: int,
    existing: set[tuple[float, float, float, float, float]] | None = None,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    blocked = set(existing or set())
    selected = set(blocked)
    candidates: list[dict[str, Any]] = []
    attempts = 0
    while len(candidates) < count and attempts < max(count * 60, 1000):
        attempts += 1
        row = {
            "t_lead": rng.randint(int(bounds["t_lead"]["lower"]), int(bounds["t_lead"]["upper"])),
            "delta_T_thr": rng.randint(int(bounds["delta_T_thr"]["lower"]), int(bounds["delta_T_thr"]["upper"])),
            "G_ext": rng.randint(int(bounds["G_ext"]["lower"]), int(bounds["G_ext"]["upper"])),
            "Q_ratio": random_rounded_real(rng, bounds, "Q_ratio"),
            "tau": random_rounded_real(rng, bounds, "tau"),
        }
        key = theta_key(row)
        if key in selected:
            continue
        selected.add(key)
        candidates.append(row)
    return candidates


def expected_improvement_candidates(
    observations: list[dict[str, Any]],
    bounds: dict[str, Any],
    seed: int,
    existing: set[tuple[float, float, float, float, float]],
    candidate_count: int = DEFAULT_EI_CANDIDATE_COUNT,
) -> list[dict[str, Any]]:
    aggregated = aggregate_observations(observations)
    if len(aggregated) < 2:
        return []
    try:
        import numpy as np  # type: ignore
        from scipy.stats import norm  # type: ignore
        from sklearn.gaussian_process import GaussianProcessRegressor  # type: ignore
        from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel  # type: ignore
    except Exception:
        return []

    x_train = np.array([theta_feature_vector(row, bounds) for row in aggregated], dtype=float)
    y_raw = np.array([float(row["bo_score_sec"]) for row in aggregated], dtype=float)
    y_mean = float(np.mean(y_raw))
    y_std = float(np.std(y_raw))
    if not math.isfinite(y_std) or y_std < 1.0e-9:
        y_std = 1.0
    y_train = (y_raw - y_mean) / y_std
    kernel = ConstantKernel(1.0, constant_value_bounds="fixed") * Matern(nu=2.5) + WhiteKernel(noise_level=1.0e-6, noise_level_bounds="fixed")
    try:
        gp = GaussianProcessRegressor(kernel=kernel, normalize_y=False, random_state=seed, alpha=1.0e-8, n_restarts_optimizer=0)
        gp.fit(x_train, y_train)
    except Exception:
        return []

    candidates = random_theta_candidates(bounds, candidate_count, seed + 1009, existing)
    if not candidates:
        return []
    x_candidate = np.array([theta_feature_vector(row, bounds) for row in candidates], dtype=float)
    try:
        mu, sigma = gp.predict(x_candidate, return_std=True)
    except Exception:
        return []
    sigma = np.maximum(sigma, 1.0e-9)
    best_scaled = float(np.min(y_train))
    improvement = best_scaled - mu - 0.01
    z = improvement / sigma
    ei_scaled = improvement * norm.cdf(z) + sigma * norm.pdf(z)
    ei_sec = np.maximum(ei_scaled, 0.0) * y_std
    ranked: list[dict[str, Any]] = []
    for row, ei in zip(candidates, ei_sec):
        ranked.append({**row, "acquisition": float(ei)})
    ranked.sort(key=lambda row: (-safe_float(row.get("acquisition")), theta_key(row)))
    return ranked


def recommend_bo_batch_sklearn(
    observations: list[dict[str, Any]],
    bounds: dict[str, Any],
    batch_size: int,
    seed: int,
    existing: set[tuple[float, float, float, float, float]],
) -> list[dict[str, Any]]:
    ranked = expected_improvement_candidates(observations, bounds, seed, existing)
    recommendations: list[dict[str, Any]] = []
    selected = set(existing)
    for row in ranked:
        clamped = clamp_theta(row, bounds)
        key = theta_key(clamped)
        if key in selected:
            continue
        selected.add(key)
        clamped["parameter_id"] = theta_id("bo_sklearn", len(recommendations) + 1, clamped)
        recommendations.append(clamped)
        if len(recommendations) >= batch_size:
            break
    return recommendations


def skopt_dimensions(bounds: dict[str, Any]) -> list[Any]:
    try:
        from skopt.space import Integer, Real  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise B4ThetaBoError(f"skopt_unavailable:{type(exc).__name__}:{exc}") from exc
    return [
        Integer(int(bounds["t_lead"]["lower"]), int(bounds["t_lead"]["upper"]), name="t_lead"),
        Integer(int(bounds["delta_T_thr"]["lower"]), int(bounds["delta_T_thr"]["upper"]), name="delta_T_thr"),
        Integer(int(bounds["G_ext"]["lower"]), int(bounds["G_ext"]["upper"]), name="G_ext"),
        Real(float(bounds["Q_ratio"]["lower"]), float(bounds["Q_ratio"]["upper"]), name="Q_ratio"),
        Real(float(bounds["tau"]["lower"]), float(bounds["tau"]["upper"]), name="tau"),
    ]


def recommend_bo_batch(observations: list[dict[str, Any]], bounds: dict[str, Any], batch_size: int, seed: int, existing: set[tuple[float, float, float, float, float]]) -> list[dict[str, Any]]:
    aggregated = aggregate_observations(observations)
    if len(aggregated) < 2:
        return random_theta_samples(bounds, batch_size, seed, "bo_fallback", existing)
    try:
        from skopt import Optimizer  # type: ignore
    except Exception as exc:  # noqa: BLE001
        fallback = recommend_bo_batch_sklearn(observations, bounds, batch_size, seed + 31, existing)
        if fallback:
            return fallback
        return random_theta_samples(bounds, batch_size, seed + 31, "bo_random_fallback", existing)
    optimizer = Optimizer(
        dimensions=skopt_dimensions(bounds),
        base_estimator="GP",
        acq_func="EI",
        random_state=seed,
    )
    optimizer.tell(
        [[row["t_lead"], row["delta_T_thr"], row["G_ext"], row["Q_ratio"], row["tau"]] for row in aggregated],
        [float(row["bo_score_sec"]) for row in aggregated],
    )
    recommendations: list[dict[str, Any]] = []
    selected = set(existing)
    for values in optimizer.ask(n_points=max(batch_size * 4, batch_size), strategy="cl_min"):
        raw = {"t_lead": values[0], "delta_T_thr": values[1], "G_ext": values[2], "Q_ratio": values[3], "tau": values[4]}
        row = clamp_theta(raw, bounds)
        key = theta_key(row)
        if key in selected:
            continue
        selected.add(key)
        row["parameter_id"] = theta_id("bo_loop", len(recommendations) + 1, row)
        recommendations.append(row)
        if len(recommendations) >= batch_size:
            break
    if len(recommendations) < batch_size:
        recommendations.extend(random_theta_samples(bounds, batch_size - len(recommendations), seed + 17, "bo_fill", selected))
    return recommendations


def default_route_subspaces(stage1: B4Stage1Inputs, count: int = DEFAULT_SUBSPACE_COUNT) -> list[dict[str, Any]]:
    route_orders = sorted({int(safe_float(movement.route_order_index)) for movement in stage1.movements})
    if not route_orders:
        return [{"index": index + 1, "weight": 1.0} for index in range(count)]
    low = min(route_orders)
    high = max(route_orders)
    span = max(high - low + 1, count)
    subspaces: list[dict[str, Any]] = []
    max_movement_count = 1
    for index in range(count):
        start = low + math.floor(index * span / count)
        end = low + math.floor((index + 1) * span / count) - 1
        movements = [
            movement
            for movement in stage1.movements
            if start <= int(safe_float(movement.route_order_index)) <= end and movement.controllable
        ]
        max_movement_count = max(max_movement_count, len(movements))
        subspaces.append({
            "index": index + 1,
            "route_order_min": start,
            "route_order_max": end,
            "movement_count": len(movements),
            "case_b_count": sum(1 for movement in movements if any(candidate.bottleneck_movement_id == movement.movement_id for candidate in stage1.case_b_candidates)),
        })
    for item in subspaces:
        risk = 1.0 + safe_float(item.get("case_b_count"), 0.0)
        density = max(safe_float(item.get("movement_count"), 0.0), 1.0) / max_movement_count
        item["weight"] = density * risk
    max_weight = max((safe_float(item["weight"]) for item in subspaces), default=1.0)
    for item in subspaces:
        item["weight"] = safe_float(item["weight"]) / max(max_weight, 1.0e-9)
    return subspaces


def essi_round_fields(
    observations: list[dict[str, Any]],
    bounds: dict[str, Any],
    stage1: B4Stage1Inputs,
    seed: int,
    round_rows: list[dict[str, Any]],
    best: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    existing = {theta_key(row) for row in observations}
    ranked = expected_improvement_candidates(observations, bounds, seed, existing, args.ei_candidate_count)
    top_ei = max((safe_float(row.get("acquisition")) for row in ranked), default=0.0)
    subspaces = default_route_subspaces(stage1)
    essi_values = [top_ei * safe_float(item.get("weight"), 1.0) for item in subspaces]
    while len(essi_values) < DEFAULT_SUBSPACE_COUNT:
        essi_values.append(0.0)
    essi_values = essi_values[:DEFAULT_SUBSPACE_COUNT]
    essi_max = max(essi_values) if essi_values else 0.0
    essi_mean = sum(essi_values) / len(essi_values) if essi_values else 0.0
    essi_log_max = math.log(essi_max + ESSI_EPS)
    previous_ewma = next(
        (
            safe_float(row.get("essi_log_max_ewma"))
            for row in reversed(round_rows)
            if row.get("essi_log_max_ewma") not in {"", None}
        ),
        essi_log_max,
    )
    ewma = args.spc_alpha * essi_log_max + (1.0 - args.spc_alpha) * previous_ewma
    previous_best = next(
        (
            safe_float(row.get("best_bo_score_sec"), float("inf"))
            for row in reversed(round_rows)
            if row.get("best_bo_score_sec") not in {"", None}
        ),
        float("inf"),
    )
    best_score = safe_float(best.get("bo_score_sec"), float("inf"))
    best_improvement = max(0.0, previous_best - best_score) if math.isfinite(previous_best) else 0.0
    prior_logs = [
        safe_float(row.get("essi_log_max"))
        for row in round_rows
        if row.get("essi_log_max") not in {"", None}
    ]
    window_logs = (prior_logs + [essi_log_max])[-args.spc_window :]
    bo_round_count = sum(1 for row in round_rows if row.get("phase") == "bo") + 1
    if len(window_logs) < args.spc_window or bo_round_count < args.spc_min_rounds:
        spc_status = "warmup"
        stop_recommended = False
    else:
        center = sum(window_logs) / len(window_logs)
        variance = sum((value - center) ** 2 for value in window_logs) / max(len(window_logs) - 1, 1)
        sigma = math.sqrt(max(variance, 0.0))
        lower = center - 3.0 * sigma
        upper = center + 3.0 * sigma
        stable = lower <= ewma <= upper and best_improvement <= args.spc_min_improvement_sec
        spc_status = "stable" if stable else "active"
        stop_recommended = stable
    fields = {
        f"essi_{index + 1}": sec(value)
        for index, value in enumerate(essi_values)
    }
    fields.update({
        "essi_max": sec(essi_max),
        "essi_mean": sec(essi_mean),
        "essi_log_max": f"{essi_log_max:.8f}",
        "essi_log_max_ewma": f"{ewma:.8f}",
        "essi_spc_status": spc_status,
        "essi_stop_recommended": str(stop_recommended),
        "essi_best_improvement_sec": sec(best_improvement),
    })
    return fields


def mock_eval_row(run_id: str, theta: dict[str, Any], seed: int, repeat_id: int) -> dict[str, Any]:
    t_lead = safe_float(theta["t_lead"])
    delta_t = safe_float(theta["delta_T_thr"])
    g_ext = safe_float(theta["G_ext"])
    q_ratio = safe_float(theta["Q_ratio"])
    tau = safe_float(theta["tau"])
    noise = random.Random(f"{seed}:{repeat_id}:{theta_key(theta)}").uniform(-2.5, 2.5)
    gate_penalty = 1.4 * max(0.0, 55.0 - delta_t)
    merge_penalty = 75.0 * max(0.0, q_ratio - 0.35)
    spillback_penalty = 120.0 * abs(tau - 0.80)
    d_emv = 420 - 5.5 * t_lead - 4.0 * g_ext + gate_penalty + merge_penalty + spillback_penalty + noise
    d_veh = 95 + 0.35 * g_ext + 0.18 * t_lead + 18.0 * max(0.0, 0.30 - q_ratio) + 30.0 * max(0.0, 0.78 - tau) + noise / 4.0
    d_emv = max(60.0, d_emv)
    t_free = 218.0
    return {
        "run_id": run_id,
        "mode": B4_MODE,
        "scenario_name": "mock_compact_v9_B4_theta",
        "parameter_id": theta["parameter_id"],
        "seed": seed,
        "repeat_id": repeat_id,
        "t_lead": theta["t_lead"],
        "delta_T_thr": theta["delta_T_thr"],
        "G_ext": theta["G_ext"],
        "Q_ratio": theta["Q_ratio"],
        "tau": theta["tau"],
        "T_actual_EMV_sec": sec(t_free + d_emv),
        "T_free_EMV_sec": sec(t_free),
        "d_EMV_sec": sec(d_emv),
        "d_veh_sec": sec(d_veh),
        "general_mean_travel_time_sec": sec(170.0 + d_veh / 10.0),
        "final_status": "PASS",
        "failed": False,
        "emergency_arrived": True,
        "emergency_teleport": False,
        "queue_method_primary": "mock_local_fill_100m",
        "queue_max_m": sec(max(q_ratio * 50.0, 1.0) * 2.0),
        "queue_p95_m": sec(max(q_ratio * 50.0, 1.0) * 1.7),
        "tls_queue_max_m": sec(max(q_ratio * 50.0, 1.0) * 2.4),
        "queue_local_fill_80m_max": sec(max(q_ratio * 50.0, 1.0) * 2.0 / 80.0),
        "queue_local_fill_100m_max": sec(max(q_ratio * 50.0, 1.0) * 2.0 / 100.0),
        "queue_local_fill_120m_max": sec(max(q_ratio * 50.0, 1.0) * 2.0 / 120.0),
        "queue_corridor_fill_250m_max": sec(max(q_ratio * 50.0, 1.0) * 2.0 / 250.0),
        "queue_trigger_count": int(max(0, round((1.0 - q_ratio) * 5.0))),
    }


def failure_row_for_worker(run_id: str, theta: dict[str, Any], seed: int, repeat_id: int, exc: BaseException) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "mode": B4_MODE,
        "scenario_name": "compact_v9_B4_theta_worker_failure",
        "parameter_id": theta["parameter_id"],
        "seed": seed,
        "repeat_id": repeat_id,
        "t_lead": theta["t_lead"],
        "delta_T_thr": theta["delta_T_thr"],
        "G_ext": theta["G_ext"],
        "Q_ratio": theta["Q_ratio"],
        "tau": theta["tau"],
        "final_status": "FAIL",
        "failed": True,
        "failure_reason": f"worker_exception:{type(exc).__name__}:{exc}",
        "emergency_arrived": False,
        "emergency_teleport": False,
    }


def emergency_route_length_from_tripinfo(path: Path) -> float:
    if not path.is_file():
        return 0.0
    try:
        import xml.etree.ElementTree as ET

        for _event, elem in ET.iterparse(path, events=("end",)):
            if elem.tag == "tripinfo" and elem.get("id") == "emergency_0":
                return safe_float(elem.get("routeLength"))
    except Exception:
        return 0.0
    return 0.0


def prepare_real_context(run_id: str, args: argparse.Namespace) -> dict[str, Any]:
    stage1 = validate_static_inputs(
        stage1_dir=getattr(args, "stage1_dir", None),
        net_file=args.net_file,
        background_route=args.background_route,
    )
    phase_config = B4RuntimePhaseConfig.from_phase(args.phase)
    if args.hard_max_sim_time is not None:
        phase_config = replace(phase_config, hard_max_sim_time=float(args.hard_max_sim_time))
    free_reference = build_b004_free_reference(stage1, net_file=args.net_file, background_route=args.background_route)
    free_rows = read_free_vehicle_rows()
    free_rows_by_id = {row["vehicle_id"]: row for row in free_rows}
    baseline_dir = args.run_root / run_id / B04_MODE / "no_control" / "repeat_001"
    baseline_json = args.metrics_root / run_id / "b04_baseline_row.json"
    if baseline_json.is_file() and args.resume:
        b04_baseline = read_json(baseline_json)
    else:
        task = B4RunTask(
            run_id,
            B04_MODE,
            "no_control",
            1,
            args.seed,
            baseline_dir,
            net_file=args.net_file,
            background_route=args.background_route,
        )
        b04_baseline = run_b04_task(task, stage1, phase_config, free_reference, free_rows_by_id, args.sumo_binary, args.emit_fcd, emit_tls_states=getattr(args, "emit_tls_states", False))
        write_json(baseline_json, b04_baseline)
    if b04_baseline.get("final_status") != "PASS" or str(b04_baseline.get("emergency_teleport", "")).lower() == "true":
        raise B4ThetaBoError("b04_baseline_validation_failed")
    route_length_m = emergency_route_length_from_tripinfo(baseline_dir / "tripinfo.xml")
    if route_length_m <= 0.0:
        route_length_m = safe_float(free_reference.get("route_length_m"))
    ev_time_sec = safe_float(b04_baseline.get("T_actual_EMV_sec"))
    ev_speed_kmh = route_length_m / ev_time_sec * 3.6 if route_length_m > 0 and ev_time_sec > 0 else 0.0
    b04_baseline["b04_ev_speed_kmh"] = round(ev_speed_kmh, 3)
    if not 15.0 <= ev_speed_kmh <= 17.0 and not getattr(args, "allow_baseline_speed_out_of_target", False):
        raise B4ThetaBoError(f"b04_baseline_speed_out_of_target:{ev_speed_kmh:.3f}kmh")
    return {
        "stage1": stage1,
        "phase_config": phase_config,
        "free_reference": free_reference,
        "free_rows_by_id": free_rows_by_id,
        "b04_baseline": b04_baseline,
    }


def evaluate_theta_repeat(job: dict[str, Any]) -> dict[str, Any]:
    run_id = job["run_id"]
    theta = job["theta"]
    seed = int(job["seed"])
    repeat = int(job["repeat"])
    args = job["args"]
    real_context = job.get("real_context")
    if args.mock_eval:
        return mock_eval_row(run_id, theta, seed, repeat)
    assert real_context is not None
    task = B4RunTask(
        run_id,
        B4_MODE,
        theta["parameter_id"],
        repeat,
        seed,
        args.run_root / run_id / B4_MODE / theta["parameter_id"] / f"repeat_{repeat:03d}",
        net_file=args.net_file,
        background_route=args.background_route,
    )
    theta_params = dict(theta)
    theta_params["hold_max"] = args.hold_max
    theta_params["d_up"] = args.d_up
    return run_b4_task(
        task,
        real_context["stage1"],
        real_context["phase_config"],
        real_context["free_reference"],
        real_context["free_rows_by_id"],
        args.sumo_binary,
        args.emit_fcd,
        B4ThetaParams.from_row(theta_params),
    )


def evaluate_theta_batch(
    run_id: str,
    theta_rows: list[dict[str, Any]],
    round_index: int,
    args: argparse.Namespace,
    real_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    jobs = [
        {
            "run_id": run_id,
            "theta": theta,
            "seed": args.seed + repeat - 1,
            "repeat": repeat,
            "args": args,
            "real_context": real_context,
        }
        for theta in theta_rows
        for repeat in range(1, args.repeats + 1)
    ]
    rows: list[dict[str, Any]] = []
    if args.workers == 1:
        for job in jobs:
            theta = job["theta"]
            try:
                raw = evaluate_theta_repeat(job)
            except Exception as exc:  # noqa: BLE001
                raw = failure_row_for_worker(run_id, theta, int(job["seed"]), int(job["repeat"]), exc)
            rows.append(append_scores(raw, theta, round_index, "initial" if round_index == 0 else "bo", args.w_emv, args.w_veh))
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            future_map = {executor.submit(evaluate_theta_repeat, job): job for job in jobs}
            for future in as_completed(future_map):
                job = future_map[future]
                theta = job["theta"]
                try:
                    raw = future.result()
                except Exception as exc:  # noqa: BLE001
                    raw = failure_row_for_worker(run_id, theta, int(job["seed"]), int(job["repeat"]), exc)
                rows.append(append_scores(raw, theta, round_index, "initial" if round_index == 0 else "bo", args.w_emv, args.w_veh))
    rows.sort(key=lambda row: (str(row.get("parameter_id", "")), int(safe_float(row.get("repeat_id"), 0))))
    return rows


def write_bo_outputs(run_dir: Path, rows: list[dict[str, Any]], round_rows: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, str]:
    all_values = run_dir / "bo_all_values.csv"
    score_summary = run_dir / "bo_score_summary.csv"
    top20 = run_dir / "top20_ranked.csv"
    observations = run_dir / "bo_observations.csv"
    penalized = run_dir / "bo_excluded_or_penalized.csv"
    rounds = run_dir / "bo_rounds.csv"
    summary = run_dir / "bo_loop_summary.json"
    write_csv(all_values, rows, ALL_VALUE_FIELDS)
    write_csv(score_summary, [score_summary_row(row) for row in rows], SCORE_FIELDS)
    write_csv(top20, top20_ranked_rows(rows), TOP20_FIELDS)
    aggregated = aggregate_observations(rows)
    write_csv(observations, aggregated, ["parameter_id", "t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau", "score_sec", "bo_score_sec", "repeat_count"])
    write_csv(penalized, [row for row in rows if safe_float(row.get("failure_penalty_sec")) > 0.0], ALL_VALUE_FIELDS)
    write_csv(rounds, round_rows, ROUND_FIELDS)
    best = aggregated[0] if aggregated else {}
    write_json(summary, {
        "schema": "compact_v9_B4_theta_bo_summary.v1",
        "generated_at": utc_now(),
        "status": state.get("status", ""),
        "completed_round": state.get("completed_round", 0),
        "output_prefix": state.get("output_prefix", DEFAULT_OUTPUT_PREFIX),
        "workers": state.get("workers", 1),
        "weights": state.get("weights", {"w_emv": W_EMV_THETA, "w_veh": W_VEH_THETA}),
        "inputs": state.get("inputs", {}),
        "best": best,
        "outputs": {
            "bo_all_values_csv": rel(all_values),
            "bo_score_summary_csv": rel(score_summary),
            "top20_ranked_csv": rel(top20),
            "bo_observations_csv": rel(observations),
            "bo_excluded_or_penalized_csv": rel(penalized),
            "bo_rounds_csv": rel(rounds),
        },
    })
    return {
        "bo_all_values_csv": rel(all_values),
        "bo_score_summary_csv": rel(score_summary),
        "top20_ranked_csv": rel(top20),
        "bo_observations_csv": rel(observations),
        "bo_excluded_or_penalized_csv": rel(penalized),
        "bo_rounds_csv": rel(rounds),
        "bo_loop_summary_json": rel(summary),
    }


def run_bo(args: argparse.Namespace) -> dict[str, Any]:
    run_id = resolve_run_id(args)
    run_dir = args.metrics_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stage1 = B4Stage1Inputs.load()
    bounds = theta_bounds_from_stage1(stage1, args.net_file)
    write_json(run_dir / "theta_bounds.json", bounds)
    real_context = None if args.mock_eval else prepare_real_context(run_id, args)
    state_path = run_dir / "state.json"
    latest_path = args.metrics_root / "latest.json"
    rows: list[dict[str, Any]] = []
    round_rows: list[dict[str, Any]] = []
    completed_round = -1
    if args.resume and state_path.is_file():
        state = read_json(state_path)
        completed_round = int(state.get("completed_round", -1))
        all_values = run_dir / "bo_all_values.csv"
        rounds = run_dir / "bo_rounds.csv"
        if all_values.is_file():
            with all_values.open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
        if rounds.is_file():
            with rounds.open("r", encoding="utf-8", newline="") as file:
                round_rows = list(csv.DictReader(file))

    existing = {theta_key(row) for row in rows}
    if completed_round < 0:
        initial = random_theta_samples(bounds, args.initial_count, args.seed, "init", existing)
        rec_csv = run_dir / "bo_recommendations_round_00.csv"
        write_csv(rec_csv, initial, THETA_FIELDS)
        result_rows = evaluate_theta_batch(run_id, initial, 0, args, real_context)
        rows.extend(result_rows)
        existing.update(theta_key(row) for row in initial)
        best = aggregate_observations(rows)[0]
        round_rows.append({
            "round": 0,
            "phase": "initial",
            "recommendation_csv": rel(rec_csv),
            "result_count": len(result_rows),
            "best_parameter_id": best.get("parameter_id", ""),
            "best_bo_score_sec": sec(best.get("bo_score_sec", "")),
            **essi_round_fields(rows, bounds, stage1, args.seed, round_rows, best, args),
        })
        completed_round = 0
        state = {"status": "RUNNING", "completed_round": completed_round, "run_id": run_id, "output_prefix": args.output_prefix, "workers": args.workers, "weights": {"w_emv": args.w_emv, "w_veh": args.w_veh}, "inputs": {"net_file": rel(args.net_file), "background_route": rel(args.background_route), "stage1_dir": rel(args.stage1_dir) if args.stage1_dir else "", "hard_max_sim_time": args.hard_max_sim_time, **structure_inputs(args)}}
        write_json(state_path, state)
        write_bo_outputs(run_dir, rows, round_rows, state)

    for round_index in range(max(1, completed_round + 1), args.bo_rounds + 1):
        recommendations = recommend_bo_batch(rows, bounds, args.bo_batch_size, args.seed + round_index, existing)
        for idx, row in enumerate(recommendations, start=1):
            row["parameter_id"] = theta_id(f"bo_r{round_index:02d}", idx, row)
        rec_csv = run_dir / f"bo_recommendations_round_{round_index:02d}.csv"
        write_csv(rec_csv, recommendations, THETA_FIELDS)
        result_rows = evaluate_theta_batch(run_id, recommendations, round_index, args, real_context)
        rows.extend(result_rows)
        existing.update(theta_key(row) for row in recommendations)
        best = aggregate_observations(rows)[0]
        round_rows.append({
            "round": round_index,
            "phase": "bo",
            "recommendation_csv": rel(rec_csv),
            "result_count": len(result_rows),
            "best_parameter_id": best.get("parameter_id", ""),
            "best_bo_score_sec": sec(best.get("bo_score_sec", "")),
            **essi_round_fields(rows, bounds, stage1, args.seed + round_index, round_rows, best, args),
        })
        completed_round = round_index
        state = {"status": "RUNNING", "completed_round": completed_round, "run_id": run_id, "output_prefix": args.output_prefix, "workers": args.workers, "weights": {"w_emv": args.w_emv, "w_veh": args.w_veh}, "inputs": {"net_file": rel(args.net_file), "background_route": rel(args.background_route), "stage1_dir": rel(args.stage1_dir) if args.stage1_dir else "", "hard_max_sim_time": args.hard_max_sim_time, **structure_inputs(args)}}
        write_json(state_path, state)
        write_bo_outputs(run_dir, rows, round_rows, state)
        if args.spc_stop and bool_cell(round_rows[-1].get("essi_stop_recommended")):
            break

    state = {"status": "COMPLETE", "completed_round": completed_round, "run_id": run_id, "output_prefix": args.output_prefix, "workers": args.workers, "weights": {"w_emv": args.w_emv, "w_veh": args.w_veh}, "inputs": {"net_file": rel(args.net_file), "background_route": rel(args.background_route), "stage1_dir": rel(args.stage1_dir) if args.stage1_dir else "", "hard_max_sim_time": args.hard_max_sim_time, **structure_inputs(args)}}
    write_json(state_path, state)
    outputs = write_bo_outputs(run_dir, rows, round_rows, state)
    weights = {"w_emv": args.w_emv, "w_veh": args.w_veh}
    inputs = {"net_file": rel(args.net_file), "background_route": rel(args.background_route), "stage1_dir": rel(args.stage1_dir) if args.stage1_dir else "", "hard_max_sim_time": args.hard_max_sim_time, **structure_inputs(args)}
    write_json(latest_path, {"schema": "compact_v9_B4_theta_bo_latest.v1", "run_id": run_id, "output_prefix": args.output_prefix, "workers": args.workers, "weights": weights, "inputs": inputs, **outputs})
    return {
        "schema": "compact_v9_B4_theta_bo_run.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "mock_eval": args.mock_eval,
        "completed_round": completed_round,
        "output_prefix": args.output_prefix,
        "workers": args.workers,
        "weights": weights,
        "inputs": inputs,
        "outputs": outputs,
    }


def latest_path(args: argparse.Namespace) -> Path:
    return args.metrics_root / "latest.json"


def resolve_run_id(args: argparse.Namespace) -> str:
    if args.run_id:
        return args.run_id
    latest = latest_path(args)
    if args.resume and latest.is_file():
        payload = read_json(latest)
        if payload.get("run_id"):
            return str(payload["run_id"])
    return default_run_id()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run B4 five-variable theta Bayesian optimization.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-prefix", "--bo-output-prefix", dest="output_prefix", default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--run-root", type=Path, default=None)
    parser.add_argument("--metrics-root", type=Path, default=None)
    parser.add_argument("--phase", default="bo-smoke")
    parser.add_argument("--initial-count", type=int, default=DEFAULT_INITIAL_COUNT)
    parser.add_argument("--bo-rounds", type=int, default=DEFAULT_BO_ROUNDS)
    parser.add_argument("--bo-batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--w-emv", "--w1", dest="w_emv", type=float, default=W_EMV_THETA)
    parser.add_argument("--w-veh", "--w2", dest="w_veh", type=float, default=W_VEH_THETA)
    parser.add_argument("--ei-candidate-count", type=int, default=DEFAULT_EI_CANDIDATE_COUNT)
    parser.add_argument("--spc-window", type=int, default=DEFAULT_SPC_WINDOW)
    parser.add_argument("--spc-alpha", type=float, default=DEFAULT_SPC_ALPHA)
    parser.add_argument("--spc-min-rounds", type=int, default=DEFAULT_SPC_MIN_ROUNDS)
    parser.add_argument("--spc-min-improvement-sec", type=float, default=DEFAULT_SPC_MIN_IMPROVEMENT_SEC)
    parser.add_argument("--spc-stop", action="store_true")
    parser.add_argument("--net-file", type=Path, default=B04_NET)
    parser.add_argument("--background-route", type=Path, default=B04_AA_BACKGROUND_ROUTE)
    parser.add_argument("--stage1-dir", type=Path, default=STAGE1_DIR)
    parser.add_argument("--hard-max-sim-time", type=float, default=None)
    parser.add_argument("--require-target15-baseline", action="store_true")
    parser.add_argument("--structure-lock-json", type=Path, default=None)
    parser.add_argument("--q-ratio", type=float, default=None)
    parser.add_argument("--tau", type=float, default=None)
    parser.add_argument("--hold-max", dest="hold_max", type=float, default=None)
    parser.add_argument("--d-up", dest="d_up", type=int, default=None)
    parser.add_argument("--tau-scale", type=float, default=None, help="Deprecated legacy alias; ignored by EVTSP BO.")
    parser.add_argument("--tau-numerator-gamma", type=float, default=None, help="Deprecated legacy alias; ignored by EVTSP BO.")
    parser.add_argument("--sumo-binary", default=None)
    parser.add_argument("--mock-eval", action="store_true")
    parser.add_argument("--resume", "--bo-resume", dest="resume", action="store_true")
    parser.add_argument("--emit-fcd", action="store_true")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    output_prefix = str(args.output_prefix or DEFAULT_OUTPUT_PREFIX).strip()
    if not output_prefix:
        raise B4ThetaBoError("output_prefix_must_not_be_blank")
    args.output_prefix = output_prefix
    args.metrics_root = (args.metrics_root or (METRICS_PARENT_ROOT / output_prefix)).resolve()
    args.run_root = (args.run_root or (RUNS_ROOT / output_prefix)).resolve()
    args.net_file = Path(args.net_file).resolve()
    args.background_route = Path(args.background_route).resolve()
    args.stage1_dir = Path(args.stage1_dir).resolve() if args.stage1_dir else None
    args.allow_baseline_speed_out_of_target = not args.require_target15_baseline
    if args.q_ratio is not None and not 0.0 <= args.q_ratio <= 1.0:
        raise B4ThetaBoError("q_ratio_must_be_between_0_and_1")
    if args.tau is not None and not EVTSP_TAU_LOWER <= args.tau <= EVTSP_TAU_UPPER:
        raise B4ThetaBoError("tau_must_be_between_0p70_and_0p90")
    apply_structure_params(args)
    if not args.net_file.is_file():
        raise B4ThetaBoError(f"missing_net_file:{args.net_file}")
    if not args.background_route.is_file():
        raise B4ThetaBoError(f"missing_background_route:{args.background_route}")
    if args.stage1_dir is not None and not args.stage1_dir.is_dir():
        raise B4ThetaBoError(f"missing_stage1_dir:{args.stage1_dir}")
    if args.hard_max_sim_time is not None and args.hard_max_sim_time <= 0:
        raise B4ThetaBoError("hard_max_sim_time_must_be_positive")
    if args.initial_count < 2:
        raise B4ThetaBoError("initial_count_must_be_at_least_2")
    if args.bo_rounds < 0:
        raise B4ThetaBoError("bo_rounds_must_be_nonnegative")
    if not 1 <= args.bo_batch_size <= 5:
        raise B4ThetaBoError("bo_batch_size_must_be_1_to_5")
    if not 1 <= args.repeats <= 3:
        raise B4ThetaBoError("repeats_must_be_1_to_3")
    if args.workers < 1:
        raise B4ThetaBoError("workers_must_be_at_least_1")
    if args.w_emv < 0.0 or args.w_veh < 0.0:
        raise B4ThetaBoError("objective_weights_must_be_nonnegative")
    if args.w_emv + args.w_veh <= 0.0:
        raise B4ThetaBoError("objective_weight_sum_must_be_positive")
    if args.ei_candidate_count < args.bo_batch_size:
        raise B4ThetaBoError("ei_candidate_count_must_cover_bo_batch_size")
    if args.spc_window < 2:
        raise B4ThetaBoError("spc_window_must_be_at_least_2")
    if not 0.0 < args.spc_alpha <= 1.0:
        raise B4ThetaBoError("spc_alpha_must_be_between_0_and_1")
    if args.spc_min_rounds < 1:
        raise B4ThetaBoError("spc_min_rounds_must_be_positive")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_args(args)
        result = run_bo(args)
    except (B4ThetaBoError, B4RunnerError, B4RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
