#!/usr/bin/env python3
"""Fixed-budget B4 optimizer comparison for the S1-forced B04/B4 inputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
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
DEFAULT_N = 15
DEFAULT_M = 50
DEFAULT_BO_INITIAL = 10
DEFAULT_WORKERS = 1
FAILURE_PENALTY_SEC = 1_000_000.0
METHODS = ["Random Search", "CMA-ES", "BO"]
THETA_FIELDS = ["parameter_id", "t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]
EVALUATION_FIELDS = [
    "method",
    "seed",
    "round",
    *THETA_FIELDS,
    "delay_A",
    "delay_N",
    "score",
    "best_so_far",
    "penalty",
    "final_status",
    "surrogate_mean",
    "surrogate_ci_low",
    "surrogate_ci_high",
    "acquisition",
    "essi_max",
    "essi_log_max_ewma",
    "essi_spc_status",
]
BO_SURROGATE_FIELDS = [
    "method",
    "seed",
    "round",
    *THETA_FIELDS,
    "observed_score",
    "best_so_far",
    "surrogate_mean",
    "surrogate_ci_low",
    "surrogate_ci_high",
    "acquisition",
    "essi_max",
    "essi_log_max_ewma",
    "essi_spc_status",
]
PARETO_FIELDS = ["weight_ratio", *THETA_FIELDS, "delay_A", "delay_N", "score", "is_knee"]
NOISE_FIELDS = ["repeat", *THETA_FIELDS, "delay_A", "delay_N", "score", "final_status"]


class B4OptimizationError(RuntimeError):
    """Expected optimizer comparison setup or runtime failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_run_id() -> str:
    return "b4_optimization_s1forced_" + datetime.now().strftime("%Y%m%d_%H%M%S")


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


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


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


def score_delay_row(row: dict[str, Any], w_emv: float, w_veh: float) -> tuple[float, float, float, float, float]:
    delay_a = safe_float(row.get("d_EMV_sec"), safe_float(row.get("T_actual_EMV_sec"), 0.0))
    delay_n = safe_float(row.get("d_veh_sec"), safe_float(row.get("general_mean_travel_time_sec"), 0.0))
    score = w_emv * delay_a + w_veh * delay_n
    failed = (
        row.get("final_status") not in {"PASS", "WARNING"}
        or not bool_cell(row.get("emergency_arrived"))
        or bool_cell(row.get("emergency_teleport"))
        or bool_cell(row.get("failed"))
    )
    penalty = FAILURE_PENALTY_SEC if failed else 0.0
    return round(delay_a, 6), round(delay_n, 6), round(score, 6), penalty, round(score + penalty, 6)


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
        "raw": raw,
    }


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


def random_search_thetas(bounds: dict[str, Any], m: int, seed: int) -> list[dict[str, Any]]:
    rows = theta_bo.random_theta_samples(bounds, m, seed, "rs")
    for index, row in enumerate(rows, start=1):
        row["parameter_id"] = theta_bo.theta_id(f"rs_r{index:02d}", 1, row)
    return rows


def cma_es_thetas(
    bounds: dict[str, Any],
    m: int,
    seed: int,
    evaluate: Any,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    dim = 5
    mean = [0.5] * dim
    sigma = [0.32] * dim
    selected: set[tuple[float, float, float, float, float]] = set()
    history: list[tuple[float, list[float]]] = []
    rows: list[dict[str, Any]] = []
    for round_index in range(1, m + 1):
        vector: list[float] = []
        theta: dict[str, Any] = {}
        for attempt in range(500):
            vector = [max(0.0, min(1.0, mean[i] + rng.gauss(0.0, sigma[i]))) for i in range(dim)]
            theta = theta_from_vector(vector, bounds, theta_bo.theta_id(f"cma_r{round_index:02d}", 1, {"t_lead": 0, "delta_T_thr": 0, "G_ext": 0, "Q_ratio": 0, "tau": 0.75}))
            theta["parameter_id"] = theta_bo.theta_id(f"cma_r{round_index:02d}", 1, theta)
            if theta_key(theta) not in selected:
                break
            if attempt == 499:
                theta = theta_bo.random_theta_samples(bounds, 1, seed + round_index * 1009, "cma_fill", selected)[0]
                vector = vector_from_theta(theta, bounds)
        selected.add(theta_key(theta))
        score = safe_float(evaluate(theta, round_index).get("score"), float("inf"))
        history.append((score, vector))
        rows.append(theta)
        if round_index >= 5:
            elite = sorted(history, key=lambda item: item[0])[: max(2, min(5, len(history) // 3))]
            weights = [len(elite) - index for index in range(len(elite))]
            weight_sum = float(sum(weights))
            mean = [
                sum(weights[j] * elite[j][1][i] for j in range(len(elite))) / weight_sum
                for i in range(dim)
            ]
            for i in range(dim):
                variance = sum(weights[j] * (elite[j][1][i] - mean[i]) ** 2 for j in range(len(elite))) / weight_sum
                sigma[i] = max(0.035, min(0.45, 0.82 * sigma[i] + 0.18 * math.sqrt(max(variance, 1.0e-6))))
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
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    round_rows: list[dict[str, Any]] = []
    existing: set[tuple[float, float, float, float, float]] = set()

    def evaluate_and_record(theta: dict[str, Any], round_index: int, acquisition: Any = "") -> dict[str, Any]:
        prediction = surrogate_prediction(observations, theta, bounds) if method == "BO" else {"surrogate_mean": "", "surrogate_ci_low": "", "surrogate_ci_high": ""}
        row = evaluate_theta(run_id, method, seed, round_index, theta, args, eval_args, real_context, w_emv, w_veh)
        row.update(prediction)
        row["acquisition"] = sec(acquisition) if acquisition not in {"", None} else ""
        observations.append({
            "mode": B4_MODE,
            **{field: row.get(field, "") for field in THETA_FIELDS},
            "score_sec": row["score"],
            "bo_score_sec": row["score"],
            "score": row["score"],
        })
        if method == "BO":
            best = min(observations, key=lambda item: safe_float(item.get("bo_score_sec"), float("inf")))
            try:
                essi = theta_bo.essi_round_fields(observations, bounds, stage1, seed + round_index, round_rows, best, eval_args)
            except Exception:
                essi = {}
            row["essi_max"] = essi.get("essi_max", "")
            row["essi_log_max_ewma"] = essi.get("essi_log_max_ewma", "")
            row["essi_spc_status"] = essi.get("essi_spc_status", "")
            round_rows.append({
                "round": round_index,
                "phase": "bo" if round_index > args.bo_initial else "initial",
                "best_bo_score_sec": best.get("bo_score_sec", ""),
                "essi_log_max": essi.get("essi_log_max", ""),
                "essi_log_max_ewma": essi.get("essi_log_max_ewma", ""),
            })
        rows.append(row)
        return row

    if method == "Random Search":
        for round_index, theta in enumerate(random_search_thetas(bounds, args.m, seed), start=1):
            evaluate_and_record(theta, round_index)
    elif method == "CMA-ES":
        cache: dict[tuple[float, float, float, float, float], dict[str, Any]] = {}

        def cma_evaluate(theta: dict[str, Any], round_index: int) -> dict[str, Any]:
            row = evaluate_and_record(theta, round_index)
            cache[theta_key(theta)] = row
            return row

        cma_es_thetas(bounds, args.m, seed, cma_evaluate)
    elif method == "BO":
        for round_index in range(1, args.m + 1):
            if round_index <= args.bo_initial or len(observations) < 2:
                theta = theta_bo.random_theta_samples(bounds, 1, seed + round_index * 31, "bo_init", existing)[0]
                acquisition = ""
            else:
                ranked = theta_bo.expected_improvement_candidates(observations, bounds, seed + round_index, existing, args.ei_candidate_count)
                theta = theta_bo.clamp_theta(ranked[0], bounds) if ranked else theta_bo.random_theta_samples(bounds, 1, seed + round_index * 37, "bo_fill", existing)[0]
                theta["parameter_id"] = theta_bo.theta_id(f"bo_r{round_index:02d}", 1, theta)
                acquisition = ranked[0].get("acquisition", "") if ranked else ""
            existing.add(theta_key(theta))
            evaluate_and_record(theta, round_index, acquisition)
    else:
        raise B4OptimizationError(f"unknown_method:{method}")

    update_best_so_far(rows)
    return rows


def build_best_so_far_table(rows: list[dict[str, Any]], m: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row["method"]), int(row["seed"])), []).append(row)
    table: list[dict[str, Any]] = []
    for (method, seed), group in sorted(grouped.items()):
        group = sorted(group, key=lambda item: int(item["round"]))
        out: dict[str, Any] = {"method": method, "seed": seed}
        for index in range(1, m + 1):
            out[f"R{index}"] = group[index - 1]["best_so_far"] if index <= len(group) else ""
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
            **{field: row.get(field, "") for field in THETA_FIELDS},
            "observed_score": row.get("score", ""),
            "best_so_far": row.get("best_so_far", ""),
            "surrogate_mean": row.get("surrogate_mean", ""),
            "surrogate_ci_low": row.get("surrogate_ci_low", ""),
            "surrogate_ci_high": row.get("surrogate_ci_high", ""),
            "acquisition": row.get("acquisition", ""),
            "essi_max": row.get("essi_max", ""),
            "essi_log_max_ewma": row.get("essi_log_max_ewma", ""),
            "essi_spc_status": row.get("essi_spc_status", ""),
        })
    return out


def normalize_pareto_weights(weight_ratio: str) -> tuple[float, float]:
    left, right = weight_ratio.split(":")
    return float(left), float(right)


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
) -> list[dict[str, Any]]:
    pareto_rows: list[dict[str, Any]] = []
    for weight_ratio in ["1:1", "5:1", "10:1", "15:1", "20:1"]:
        w_emv, w_veh = normalize_pareto_weights(weight_ratio)
        rows = run_method("BO", args.seed_base, f"{run_id}_pareto_{weight_ratio.replace(':', '_')}", bounds, args, eval_args, real_context, w_emv, w_veh, stage1)
        best = min(rows, key=lambda row: safe_float(row.get("score"), float("inf")))
        pareto_rows.append({
            "weight_ratio": weight_ratio,
            **{field: best.get(field, "") for field in THETA_FIELDS},
            "delay_A": best.get("delay_A", ""),
            "delay_N": best.get("delay_N", ""),
            "score": best.get("score", ""),
            "is_knee": "False",
        })
    pareto_rows.sort(key=lambda row: safe_float(row["delay_N"]))
    if pareto_rows:
        pareto_rows[knee_index(pareto_rows)]["is_knee"] = "True"
    return pareto_rows


def run_noise_check(
    run_id: str,
    bounds: dict[str, Any],
    args: argparse.Namespace,
    eval_args: argparse.Namespace,
    real_context: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    theta = theta_bo.clamp_theta(B4ThetaParams().as_result_fields(), bounds, parameter_id="noise_reference_theta")
    rows: list[dict[str, Any]] = []
    for repeat in range(1, 6):
        row = evaluate_theta(run_id, "Noise Check", args.seed_base, repeat, theta, args, eval_args, real_context, args.w_emv, args.w_veh, repeat_id=repeat)
        rows.append({
            "repeat": repeat,
            **{field: theta.get(field, "") for field in THETA_FIELDS},
            "delay_A": row["delay_A"],
            "delay_N": row["delay_N"],
            "score": row["score"],
            "final_status": row["final_status"],
        })
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run S1-forced B4 fixed-budget optimizer comparison.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--m", type=int, default=DEFAULT_M)
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
    parser.add_argument("--ei-candidate-count", type=int, default=theta_bo.DEFAULT_EI_CANDIDATE_COUNT)
    parser.add_argument("--spc-window", type=int, default=theta_bo.DEFAULT_SPC_WINDOW)
    parser.add_argument("--spc-alpha", type=float, default=theta_bo.DEFAULT_SPC_ALPHA)
    parser.add_argument("--spc-min-rounds", type=int, default=theta_bo.DEFAULT_SPC_MIN_ROUNDS)
    parser.add_argument("--spc-min-improvement-sec", type=float, default=theta_bo.DEFAULT_SPC_MIN_IMPROVEMENT_SEC)
    parser.add_argument("--mock-eval", action="store_true")
    parser.add_argument("--emit-fcd", action="store_true")
    parser.add_argument("--skip-pareto", action="store_true")
    parser.add_argument("--skip-noise-check", action="store_true")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.n < 1:
        raise B4OptimizationError("n_must_be_positive")
    if args.m < 2:
        raise B4OptimizationError("m_must_be_at_least_2")
    if not 2 <= args.bo_initial < args.m:
        raise B4OptimizationError("bo_initial_must_be_between_2_and_m_minus_1")
    if args.workers < 1:
        raise B4OptimizationError("workers_must_be_positive")
    if args.w_emv < 0.0 or args.w_veh < 0.0:
        raise B4OptimizationError("weights_must_be_nonnegative")
    if args.ei_candidate_count < 2:
        raise B4OptimizationError("ei_candidate_count_must_be_at_least_2")
    args.output_dir = args.output_dir.resolve()
    args.run_root = args.run_root.resolve()
    args.active_inputs = args.active_inputs.resolve()
    args.net_file = args.net_file.resolve()
    args.background_route = args.background_route.resolve()
    args.stage1_dir = args.stage1_dir.resolve()


def run_experiment(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or default_run_id()
    output_dir = args.output_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
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
        "active_inputs": preflight_payload["active_inputs"],
    })

    eval_args = build_eval_args(args, args.seed_base, args.run_root, output_dir)
    real_context = prepare_real_context_once(run_id, eval_args)
    all_rows: list[dict[str, Any]] = []
    seeds = [args.seed_base + index for index in range(args.n)]
    for method in METHODS:
        for seed in seeds:
            method_rows = run_method(method, seed, f"{run_id}_{method.lower().replace(' ', '_').replace('-', '_')}_{seed}", bounds, args, build_eval_args(args, seed, args.run_root, output_dir), real_context, args.w_emv, args.w_veh, stage1)
            all_rows.extend(method_rows)

    best_table = build_best_so_far_table(all_rows, args.m)
    bo_table = build_bo_surrogate_table(all_rows)
    all_rows_public = [{field: row.get(field, "") for field in EVALUATION_FIELDS} for row in all_rows]
    write_csv(output_dir / "all_evaluations.csv", all_rows_public, EVALUATION_FIELDS)
    write_csv(output_dir / "table1_best_so_far.csv", best_table, ["method", "seed", *[f"R{index}" for index in range(1, args.m + 1)]])
    write_csv(output_dir / "table2_bo_surrogate.csv", bo_table, BO_SURROGATE_FIELDS)

    pareto_rows: list[dict[str, Any]] = []
    if not args.skip_pareto:
        pareto_rows = run_pareto(run_id, bounds, args, eval_args, real_context, stage1)
        write_csv(output_dir / "table3_pareto.csv", pareto_rows, PARETO_FIELDS)

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

    outputs = {
        "all_evaluations_csv": rel(output_dir / "all_evaluations.csv"),
        "table1_best_so_far_csv": rel(output_dir / "table1_best_so_far.csv"),
        "table2_bo_surrogate_csv": rel(output_dir / "table2_bo_surrogate.csv"),
        "table3_pareto_csv": rel(output_dir / "table3_pareto.csv") if pareto_rows else "",
        "noise_check_csv": rel(output_dir / "noise_check_5repeat.csv") if noise_rows else "",
        "figure1_png": rel(output_dir / "figure1_best_so_far.png"),
        "figure2_png": rel(output_dir / "figure2_bo_surrogate.png"),
        "figure3_png": rel(output_dir / "figure3_pareto.png") if pareto_rows else "",
    }
    summary = {
        "schema": "compact_v9_B4_s1forced_optimizer_comparison.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "mock_eval": args.mock_eval,
        "n": args.n,
        "m": args.m,
        "methods": METHODS,
        "seeds": seeds,
        "objective": {"score": "w_emv * delay_A + w_veh * delay_N", "w_emv": args.w_emv, "w_veh": args.w_veh},
        "fixed_budget_policy": "Each round is one evaluated theta; best-so-far records cumulative minimum score.",
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
        result = run_experiment(args)
    except (B4OptimizationError, theta_bo.B4ThetaBoError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
