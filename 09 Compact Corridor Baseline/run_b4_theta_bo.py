#!/usr/bin/env python3
"""Independent 5-variable Bayesian optimization runner for Compact V9 B4."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
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
    B04_MODE,
    B4_MODE,
    B4RuntimeError,
    B4RuntimePhaseConfig,
    B4Stage1Inputs,
    B4ThetaParams,
    EXPERIMENT_RESULT_FIELDS,
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

THETA_FIELDS = ["parameter_id", "t_lead", "tau", "ext_max", "hold_max", "d_up"]
SCORE_FIELDS = [
    "run_id",
    "round",
    "parameter_id",
    "seed",
    "repeat_id",
    "t_lead",
    "tau",
    "ext_max",
    "hold_max",
    "d_up",
    "T_actual_EMV_sec",
    "d_EMV_sec",
    "general_mean_travel_time_sec",
    "d_veh_sec",
    "score_sec",
    "bo_score_sec",
    "final_status",
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


def bool_cell(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def sec(value: Any) -> str:
    if value in {"", None}:
        return ""
    return f"{float(value):.2f}"


def theta_key(row: dict[str, Any]) -> tuple[float, float, float, float, int]:
    return (
        round(safe_float(row.get("t_lead")), 0),
        round(safe_float(row.get("tau")), 2),
        round(safe_float(row.get("ext_max")), 0),
        round(safe_float(row.get("hold_max")), 0),
        int(round(safe_float(row.get("d_up"), 1))),
    )


def theta_id(prefix: str, rank: int, theta: dict[str, Any]) -> str:
    return (
        f"{prefix}_{rank:03d}"
        f"_tl{int(round(safe_float(theta['t_lead'])))}"
        f"_ta{int(round(safe_float(theta['tau']) * 100))}"
        f"_ex{int(round(safe_float(theta['ext_max'])))}"
        f"_ho{int(round(safe_float(theta['hold_max'])))}"
        f"_du{int(round(safe_float(theta['d_up'])))}"
    )


def clamp_theta(theta: dict[str, Any], bounds: dict[str, Any], parameter_id: str | None = None) -> dict[str, Any]:
    clamped = {
        "t_lead": int(round(max(bounds["t_lead"]["lower"], min(bounds["t_lead"]["upper"], safe_float(theta.get("t_lead")))))),
        "tau": min(bounds["tau"]["values"], key=lambda value: abs(float(value) - safe_float(theta.get("tau"), 0.75))),
        "ext_max": int(round(max(bounds["ext_max"]["lower"], min(bounds["ext_max"]["upper"], safe_float(theta.get("ext_max")))))),
        "hold_max": int(round(max(bounds["hold_max"]["lower"], min(bounds["hold_max"]["upper"], safe_float(theta.get("hold_max")))))),
        "d_up": int(min(bounds["d_up"]["values"], key=lambda value: abs(int(value) - safe_float(theta.get("d_up"), 1)))),
    }
    clamped["parameter_id"] = parameter_id or str(theta.get("parameter_id") or theta_id("theta", 1, clamped))
    return clamped


def random_theta_samples(bounds: dict[str, Any], count: int, seed: int, prefix: str, existing: set[tuple[float, float, float, float, int]] | None = None) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    existing = set(existing or set())
    samples: list[dict[str, Any]] = []
    attempts = 0
    while len(samples) < count and attempts < count * 200:
        attempts += 1
        row = {
            "t_lead": rng.randint(int(bounds["t_lead"]["lower"]), int(bounds["t_lead"]["upper"])),
            "tau": rng.choice(bounds["tau"]["values"]),
            "ext_max": rng.randint(int(bounds["ext_max"]["lower"]), int(bounds["ext_max"]["upper"])),
            "hold_max": rng.randint(int(bounds["hold_max"]["lower"]), int(bounds["hold_max"]["upper"])),
            "d_up": rng.choice(bounds["d_up"]["values"]),
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


def score_for_row(row: dict[str, Any]) -> tuple[float, float, float]:
    d_emv = safe_float(row.get("d_EMV_sec"), 0.0)
    d_veh = safe_float(row.get("d_veh_sec"), 0.0)
    score = W_EMV_THETA * d_emv + W_VEH_THETA * d_veh
    failed = (
        row.get("final_status") not in {"PASS", "WARNING"}
        or not bool_cell(row.get("emergency_arrived"))
        or bool_cell(row.get("emergency_teleport"))
        or bool_cell(row.get("failed"))
    )
    penalty = FAILURE_PENALTY_SEC if failed else 0.0
    return round(score, 6), penalty, round(score + penalty, 6)


def append_scores(row: dict[str, Any], theta: dict[str, Any], round_index: int, phase: str) -> dict[str, Any]:
    score, penalty, bo_score = score_for_row(row)
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
        "tau": row.get("tau", ""),
        "ext_max": row.get("ext_max", ""),
        "hold_max": row.get("hold_max", ""),
        "d_up": row.get("d_up", ""),
        "T_actual_EMV_sec": row.get("T_actual_EMV_sec", ""),
        "d_EMV_sec": row.get("d_EMV_sec", ""),
        "general_mean_travel_time_sec": row.get("general_mean_travel_time_sec", ""),
        "d_veh_sec": row.get("d_veh_sec", ""),
        "score_sec": row.get("score_sec", ""),
        "bo_score_sec": row.get("bo_score_sec", ""),
        "final_status": row.get("final_status", ""),
    }


def aggregate_observations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[float, float, float, float, int], dict[str, Any]] = {}
    for row in rows:
        if row.get("mode") != B4_MODE:
            continue
        key = theta_key(row)
        entry = grouped.setdefault(key, {
            "parameter_id": row.get("parameter_id", ""),
            "t_lead": key[0],
            "tau": key[1],
            "ext_max": key[2],
            "hold_max": key[3],
            "d_up": key[4],
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


def skopt_dimensions(bounds: dict[str, Any]) -> list[Any]:
    try:
        from skopt.space import Categorical, Integer  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise B4ThetaBoError(f"skopt_unavailable:{type(exc).__name__}:{exc}") from exc
    return [
        Integer(int(bounds["t_lead"]["lower"]), int(bounds["t_lead"]["upper"]), name="t_lead"),
        Categorical([float(value) for value in bounds["tau"]["values"]], name="tau"),
        Integer(int(bounds["ext_max"]["lower"]), int(bounds["ext_max"]["upper"]), name="ext_max"),
        Integer(int(bounds["hold_max"]["lower"]), int(bounds["hold_max"]["upper"]), name="hold_max"),
        Categorical([int(value) for value in bounds["d_up"]["values"]], name="d_up"),
    ]


def recommend_bo_batch(observations: list[dict[str, Any]], bounds: dict[str, Any], batch_size: int, seed: int, existing: set[tuple[float, float, float, float, int]]) -> list[dict[str, Any]]:
    aggregated = aggregate_observations(observations)
    if len(aggregated) < 2:
        return random_theta_samples(bounds, batch_size, seed, "bo_fallback", existing)
    try:
        from skopt import Optimizer  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return random_theta_samples(bounds, batch_size, seed + 31, "bo_random_fallback", existing)
    optimizer = Optimizer(
        dimensions=skopt_dimensions(bounds),
        base_estimator="GP",
        acq_func="EI",
        random_state=seed,
    )
    optimizer.tell(
        [[row["t_lead"], row["tau"], row["ext_max"], row["hold_max"], row["d_up"]] for row in aggregated],
        [float(row["bo_score_sec"]) for row in aggregated],
    )
    recommendations: list[dict[str, Any]] = []
    selected = set(existing)
    for values in optimizer.ask(n_points=max(batch_size * 4, batch_size), strategy="cl_min"):
        raw = {"t_lead": values[0], "tau": values[1], "ext_max": values[2], "hold_max": values[3], "d_up": values[4]}
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


def mock_eval_row(run_id: str, theta: dict[str, Any], seed: int, repeat_id: int) -> dict[str, Any]:
    t_lead = safe_float(theta["t_lead"])
    tau = safe_float(theta["tau"])
    ext_max = safe_float(theta["ext_max"])
    hold_max = safe_float(theta["hold_max"])
    d_up = safe_float(theta["d_up"])
    noise = random.Random(f"{seed}:{repeat_id}:{theta_key(theta)}").uniform(-2.5, 2.5)
    d_emv = 420 - 5.5 * t_lead - 4.0 * ext_max - 2.2 * hold_max - 12.0 * d_up + 260.0 * abs(tau - 0.75) + noise
    d_veh = 95 + 0.35 * ext_max + 0.55 * hold_max + 3.0 * max(0.0, 0.75 - tau) + noise / 4.0
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
        "tau": theta["tau"],
        "ext_max": theta["ext_max"],
        "hold_max": theta["hold_max"],
        "d_up": theta["d_up"],
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
        "queue_max_m": sec(100.0 * tau),
        "queue_p95_m": sec(85.0 * tau),
        "tls_queue_max_m": sec(120.0 * tau),
        "queue_local_fill_80m_max": sec((100.0 * tau) / 80.0),
        "queue_local_fill_100m_max": sec(tau),
        "queue_local_fill_120m_max": sec((100.0 * tau) / 120.0),
        "queue_corridor_fill_250m_max": sec(tau / 2.0),
        "queue_trigger_count": int(max(0, round((0.9 - tau) * 10))),
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
        "tau": theta["tau"],
        "ext_max": theta["ext_max"],
        "hold_max": theta["hold_max"],
        "d_up": theta["d_up"],
        "final_status": "FAIL",
        "failed": True,
        "failure_reason": f"worker_exception:{type(exc).__name__}:{exc}",
        "emergency_arrived": False,
        "emergency_teleport": False,
    }


def prepare_real_context(run_id: str, args: argparse.Namespace) -> dict[str, Any]:
    stage1 = validate_static_inputs()
    phase_config = B4RuntimePhaseConfig.from_phase(args.phase)
    free_reference = build_b004_free_reference(stage1)
    free_rows = read_free_vehicle_rows()
    free_rows_by_id = {row["vehicle_id"]: row for row in free_rows}
    baseline_dir = args.run_root / run_id / B04_MODE / "no_control" / "repeat_001"
    baseline_json = args.metrics_root / run_id / "b04_baseline_row.json"
    if baseline_json.is_file() and args.resume:
        b04_baseline = read_json(baseline_json)
    else:
        task = B4RunTask(run_id, B04_MODE, "no_control", 1, args.seed, baseline_dir)
        b04_baseline = run_b04_task(task, stage1, phase_config, free_reference, free_rows_by_id, args.sumo_binary, args.emit_fcd)
        write_json(baseline_json, b04_baseline)
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
    )
    return run_b4_task(
        task,
        real_context["stage1"],
        real_context["phase_config"],
        real_context["free_reference"],
        real_context["free_rows_by_id"],
        args.sumo_binary,
        args.emit_fcd,
        B4ThetaParams.from_row(theta),
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
            rows.append(append_scores(raw, theta, round_index, "initial" if round_index == 0 else "bo"))
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
                rows.append(append_scores(raw, theta, round_index, "initial" if round_index == 0 else "bo"))
    rows.sort(key=lambda row: (str(row.get("parameter_id", "")), int(safe_float(row.get("repeat_id"), 0))))
    return rows


def write_bo_outputs(run_dir: Path, rows: list[dict[str, Any]], round_rows: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, str]:
    all_values = run_dir / "bo_all_values.csv"
    score_summary = run_dir / "bo_score_summary.csv"
    observations = run_dir / "bo_observations.csv"
    penalized = run_dir / "bo_excluded_or_penalized.csv"
    rounds = run_dir / "bo_rounds.csv"
    summary = run_dir / "bo_loop_summary.json"
    write_csv(all_values, rows, ALL_VALUE_FIELDS)
    write_csv(score_summary, [score_summary_row(row) for row in rows], SCORE_FIELDS)
    aggregated = aggregate_observations(rows)
    write_csv(observations, aggregated, ["parameter_id", "t_lead", "tau", "ext_max", "hold_max", "d_up", "score_sec", "bo_score_sec", "repeat_count"])
    write_csv(penalized, [row for row in rows if safe_float(row.get("failure_penalty_sec")) > 0.0], ALL_VALUE_FIELDS)
    write_csv(rounds, round_rows, ["round", "phase", "recommendation_csv", "result_count", "best_parameter_id", "best_bo_score_sec"])
    best = aggregated[0] if aggregated else {}
    write_json(summary, {
        "schema": "compact_v9_B4_theta_bo_summary.v1",
        "generated_at": utc_now(),
        "status": state.get("status", ""),
        "completed_round": state.get("completed_round", 0),
        "output_prefix": state.get("output_prefix", DEFAULT_OUTPUT_PREFIX),
        "workers": state.get("workers", 1),
        "weights": {"w_emv": W_EMV_THETA, "w_veh": W_VEH_THETA},
        "best": best,
        "outputs": {
            "bo_all_values_csv": rel(all_values),
            "bo_score_summary_csv": rel(score_summary),
            "bo_observations_csv": rel(observations),
            "bo_excluded_or_penalized_csv": rel(penalized),
            "bo_rounds_csv": rel(rounds),
        },
    })
    return {
        "bo_all_values_csv": rel(all_values),
        "bo_score_summary_csv": rel(score_summary),
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
    bounds = theta_bounds_from_stage1(stage1)
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
        })
        completed_round = 0
        state = {"status": "RUNNING", "completed_round": completed_round, "run_id": run_id, "output_prefix": args.output_prefix, "workers": args.workers}
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
        })
        completed_round = round_index
        state = {"status": "RUNNING", "completed_round": completed_round, "run_id": run_id, "output_prefix": args.output_prefix, "workers": args.workers}
        write_json(state_path, state)
        write_bo_outputs(run_dir, rows, round_rows, state)

    state = {"status": "COMPLETE", "completed_round": completed_round, "run_id": run_id, "output_prefix": args.output_prefix, "workers": args.workers}
    write_json(state_path, state)
    outputs = write_bo_outputs(run_dir, rows, round_rows, state)
    write_json(latest_path, {"schema": "compact_v9_B4_theta_bo_latest.v1", "run_id": run_id, "output_prefix": args.output_prefix, "workers": args.workers, **outputs})
    return {
        "schema": "compact_v9_B4_theta_bo_run.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "mock_eval": args.mock_eval,
        "completed_round": completed_round,
        "output_prefix": args.output_prefix,
        "workers": args.workers,
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
