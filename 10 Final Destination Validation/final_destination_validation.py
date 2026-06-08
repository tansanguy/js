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
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
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
    B04_AA_BACKGROUND_ROUTE,
    B04_NET,
    B4_MODE,
    B4ThetaParams,
    B4RuntimePhaseConfig,
    B4Stage1Inputs,
    EXPERIMENT_RESULT_FIELDS,
    STAGE1_DIR,
    W_E,
    W_G,
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
DEFAULT_ACTIVE_INPUTS = PROJECT_ROOT / "configs/compact_v9_B04_B4_active_inputs.json"
DEFAULT_THETA_LATEST = PROJECT_ROOT / "09-1 B4 Optimization S1forced/outputs/latest.json"
DEFAULT_MAINROAD_MAPPING = PROJECT_ROOT / "data_prepared/compact_v9/map/B04_toegye_segment_edge_mapping.csv"
DEFAULT_OUTPUT_PREFIX = "compact_v9_final_destination_validation"
DEFAULT_RUN_ROOT = PROJECT_ROOT / "runs" / DEFAULT_OUTPUT_PREFIX
DEFAULT_METRICS_ROOT = PROJECT_ROOT / "results/metrics" / DEFAULT_OUTPUT_PREFIX
DEFAULT_SEED = 20260606
DEFAULT_DEPART_MIN = 550.0
DEFAULT_DEPART_MAX = 650.0
DEFAULT_REPEATS = 30
DEFAULT_SCREENING_REPEATS = 1
DEFAULT_PILOT_REPEATS = 30
DEFAULT_RELATIVE_ERROR_TARGET = 0.05
DEFAULT_CONFIDENCE_LEVEL = 0.95
DEFAULT_ADAPTIVE_MAX_REPEATS = 300
DEFAULT_CANDIDATE_LIMIT = 18
DEFAULT_FINAL_SELECTION_COUNT = 3
DEFAULT_START_EDGE = "420331801#1"
DEFAULT_HARD_MAX_SIM_TIME = 4000.0
DEFAULT_WORKERS = 6
EV_ID = "emergency_0"
THETA_FIELDS = ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]
PHASE_SCREENING = "screening"
PHASE_FINAL = "final"
PHASE_ALL = "all"
VALIDATION_MODE_STANDARD = "standard"
VALIDATION_MODE_ROBUST_THETA_SELECTION = "robust-theta-selection"
STAGE1_REBUILD_POLICY = "per_repeat_for_10_final_validation"


def _manifest_path(key: str, fallback: Path) -> Path:
    if not DEFAULT_ACTIVE_INPUTS.is_file():
        return fallback
    with DEFAULT_ACTIVE_INPUTS.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    value = payload.get(key)
    if not value:
        return fallback
    path = Path(str(value))
    return path if path.is_absolute() else PROJECT_ROOT / path


DEFAULT_NET = _manifest_path("net_file", B04_NET)
DEFAULT_BACKGROUND_ROUTE = _manifest_path("background_route", B04_AA_BACKGROUND_ROUTE)
DEFAULT_BASE_STAGE1_DIR = _manifest_path("stage1_dir", STAGE1_DIR)

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
    "route_id",
    "mode",
    "run_count",
    "T_EMV_mean_sec",
    "T_EMV_std_sec",
    "D_E_mean_sec",
    "D_G_mean_sec",
    "objective_score_mean",
    "emergency_arrival_rate",
    "teleport_count",
    "fail_count",
    "stage3_preemption_mean",
    "stage2_hold_mean",
]

SPC_REPEAT_FIELDS = [
    "route_id",
    "metric",
    "repeat_count",
    "mean",
    "std",
    "latest_value",
    "ewma",
    "center",
    "lcl",
    "ucl",
    "spc_status",
    "stable_round",
]

RELATIVE_ERROR_FIELDS = [
    "route_id",
    "metric",
    "pilot_repeat_count",
    "repeat_count",
    "mean",
    "std",
    "confidence_level",
    "z_critical",
    "ci_half_width",
    "target_relative_error",
    "target_half_width",
    "relative_half_width",
    "required_repeats",
    "additional_repeats_required",
    "status",
    "reason",
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
    "B04_D_E_mean_sec",
    "B4_vs_B04_D_E_improvement_sec",
    "B04_D_G_mean_sec",
    "B4_D_G_mean_sec",
    "B4_stage3_preemption_mean",
    "B4_stage2_hold_mean",
    "intervention_mean",
    "arrival_rate_min",
    "teleport_count",
    "fail_count",
    "presentation_fit_score",
    "selection_rank",
    "selection_status",
    "selection_reason",
]

FINAL_SIMULATION_FIELDS = [
    "input_phase",
    "input_route_id",
    "input_source_route_id",
    "input_target_edge_id",
    "input_repeat_id",
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
    "measured_T_G_actual_mean_sec",
    "measured_T_G_free_mean_sec",
    "stage2_on_count",
    "stage3_on_count",
]

TASK_FIELDS = [
    "phase",
    "candidate_rank",
    "route_id",
    "mode",
    "repeat_id",
    "emergency_depart",
    "run_dir",
    "route_xml",
    "stage1_dir",
]

ROBUST_THETA_CANDIDATE_FIELDS = [
    "theta_rank",
    "source_row_index",
    "method",
    "seed",
    "round",
    "round_theta_index",
    "parameter_id",
    *THETA_FIELDS,
    "source_score",
    "source_D_E_sec",
    "source_D_G_sec",
    "source_final_status",
    "source_failure_reason",
    "selection_reason",
    "nearest_selected_distance",
]

ROBUST_THETA_SUMMARY_FIELDS = [
    "theta_rank",
    "parameter_id",
    *THETA_FIELDS,
    "route_id",
    "departures",
    "repeat_count",
    "stuck_count",
    "fail_count",
    "arrival_rate",
    "teleport_count",
    "mean_D_E_sec",
    "mean_D_G_sec",
    "mean_score",
    "mean_B4_vs_B04_D_E_improvement_sec",
    "stage2_hold_mean",
    "stage3_preemption_mean",
    "survivor_status",
    "survivor_reason",
    "mini_batch_output_root",
    "mini_batch_run_root",
]

ROBUST_FINAL_RANKING_FIELDS = [
    "final_rank",
    "theta_rank",
    "parameter_id",
    *THETA_FIELDS,
    "route_id",
    "run_id",
    "selected_route_runs_csv",
    "relative_error_sufficiency_csv",
    "repeat_count",
    "stuck_count",
    "fail_count",
    "arrival_rate",
    "teleport_count",
    "mean_D_E_sec",
    "mean_D_G_sec",
    "mean_score",
    "mean_B4_vs_B04_D_E_improvement_sec",
    "relative_error_pass",
    "relative_error_statuses",
    "selection_status",
    "selection_reason",
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_active_inputs(args: argparse.Namespace) -> dict[str, Any]:
    if not DEFAULT_ACTIVE_INPUTS.is_file():
        return {"status": "SKIP", "reason": "active_inputs_missing"}
    payload = read_json(DEFAULT_ACTIVE_INPUTS)
    expected_paths = {
        "net_file": Path(args.net),
        "background_route": Path(args.background_route),
        "stage1_dir": Path(args.base_stage1_dir),
    }
    for key, path in expected_paths.items():
        manifest_value = payload.get(key)
        if not manifest_value:
            raise FinalDestinationValidationError(f"active_inputs_missing_{key}")
        if rel(project_path(str(manifest_value))) != rel(path):
            raise FinalDestinationValidationError(f"active_inputs_{key}_mismatch:{manifest_value} != {rel(path)}")
    hash_audit: dict[str, str] = {}
    for path_key, hash_key in {
        "net_file": "net_file_sha256",
        "background_route": "background_route_sha256",
    }.items():
        manifest_hash = str(payload.get(hash_key, ""))
        if not manifest_hash:
            continue
        actual_hash = sha256_file(expected_paths[path_key])
        hash_audit[hash_key] = actual_hash
        if actual_hash != manifest_hash:
            raise FinalDestinationValidationError(f"active_inputs_{hash_key}_mismatch:{actual_hash} != {manifest_hash}")
    return {
        "status": "PASS",
        "active_inputs": rel(DEFAULT_ACTIVE_INPUTS),
        "hashes": hash_audit,
    }


def bool_cell(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes", "y"}


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


def spc_metric_row(route_id: str, metric: str, values: list[float], *, window: int = 5, alpha: float = 0.30) -> dict[str, Any]:
    if len(values) < window:
        return {
            "route_id": route_id,
            "metric": metric,
            "repeat_count": len(values),
            "mean": sec(mean(values)),
            "std": sec(sample_std(values)) if values else "",
            "latest_value": sec(values[-1] if values else None),
            "ewma": "",
            "center": "",
            "lcl": "",
            "ucl": "",
            "spc_status": "insufficient",
            "stable_round": "",
        }
    ewma = values[0]
    stable_round = ""
    status = "active"
    center = values[0]
    sigma = 0.0
    for index, value in enumerate(values, start=1):
        ewma = alpha * value + (1.0 - alpha) * ewma
        if index < window:
            continue
        slice_values = values[index - window:index]
        center = sum(slice_values) / len(slice_values)
        sigma = sample_std(slice_values)
        lower = center - 3.0 * sigma
        upper = center + 3.0 * sigma
        if lower <= ewma <= upper:
            status = "stable"
            if not stable_round:
                stable_round = index
        else:
            status = "active"
            stable_round = ""
    return {
        "route_id": route_id,
        "metric": metric,
        "repeat_count": len(values),
        "mean": sec(mean(values)),
        "std": sec(sample_std(values)),
        "latest_value": sec(values[-1]),
        "ewma": sec(ewma),
        "center": sec(center),
        "lcl": sec(center - 3.0 * sigma),
        "ucl": sec(center + 3.0 * sigma),
        "spc_status": status,
        "stable_round": stable_round,
    }


def z_critical(confidence_level: float) -> float:
    return NormalDist().inv_cdf(0.5 + confidence_level / 2.0)


def sec(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


def repeat_count_for_rows(rows: list[dict[str, Any]], mode: str = B4_MODE) -> int:
    return len({
        int(safe_float(row.get("repeat_id"), 0.0))
        for row in rows
        if row.get("mode") == mode and safe_float(row.get("repeat_id"), 0.0) > 0
    })


def paired_repeat_rows(rows: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    b04_by_repeat = {
        int(safe_float(row.get("repeat_id"), 0.0)): row
        for row in rows
        if row.get("mode") == B04_MODE and safe_float(row.get("repeat_id"), 0.0) > 0
    }
    b4_by_repeat = {
        int(safe_float(row.get("repeat_id"), 0.0)): row
        for row in rows
        if row.get("mode") == B4_MODE and safe_float(row.get("repeat_id"), 0.0) > 0
    }
    return [(repeat, b04_by_repeat[repeat], b4_by_repeat[repeat]) for repeat in sorted(set(b04_by_repeat) & set(b4_by_repeat))]


def relative_error_metric_values(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    values: dict[str, list[float]] = {
        "B4_T_EMV_sec": [],
        "B4_D_E_sec": [],
        "B4_D_G_sec": [],
        "B4_vs_B04_D_E_improvement_sec": [],
    }
    for row in rows:
        if row.get("mode") != B4_MODE:
            continue
        value = t_emv(row)
        if value is not None:
            values["B4_T_EMV_sec"].append(value)
        if row.get("D_E_sec") not in {"", None}:
            values["B4_D_E_sec"].append(safe_float(row.get("D_E_sec")))
        if row.get("D_G_sec") not in {"", None}:
            values["B4_D_G_sec"].append(safe_float(row.get("D_G_sec")))
    for _repeat, b04, b4 in paired_repeat_rows(rows):
        b04_time = t_emv(b04)
        b4_time = t_emv(b4)
        if b04_time is not None and b4_time is not None:
            values["B4_vs_B04_D_E_improvement_sec"].append(b04_time - b4_time)
    return values


def relative_error_metric_row(
    route_id: str,
    metric: str,
    values: list[float],
    *,
    pilot_repeat_count: int,
    confidence_level: float,
    relative_error_target: float,
    max_repeats: int,
) -> dict[str, Any]:
    n = len(values)
    z = z_critical(confidence_level)
    avg = mean(values)
    std = sample_std(values) if n >= 2 else 0.0
    target_half_width = relative_error_target * abs(avg) if avg is not None else None
    ci_half_width = z * std / math.sqrt(n) if n > 0 else None
    relative_half_width = (ci_half_width / abs(avg)) if ci_half_width is not None and avg not in {None, 0.0} else None
    required_repeats = n
    status = "PASS"
    reason = "ci_half_width_within_relative_error_target"
    if n < 2 or avg is None or ci_half_width is None or target_half_width is None:
        status = "INSUFFICIENT_DATA"
        reason = "need_at_least_two_valid_repeat_values"
    elif target_half_width == 0.0:
        if ci_half_width == 0.0:
            status = "PASS"
            reason = "zero_mean_and_zero_variance"
        else:
            status = "UNBOUNDED_ZERO_MEAN"
            reason = "relative_error_undefined_for_zero_mean_nonzero_variance"
            required_repeats = max_repeats
    else:
        required_repeats = max(n, math.ceil((z * std / target_half_width) ** 2))
        if ci_half_width <= target_half_width:
            status = "PASS"
        elif required_repeats > max_repeats:
            status = "CAPPED"
            reason = "required_repeats_exceeds_adaptive_max_repeats"
        else:
            status = "NEEDS_MORE"
            reason = "ci_half_width_exceeds_relative_error_target"
    additional = max(0, min(required_repeats, max_repeats) - n)
    return {
        "route_id": route_id,
        "metric": metric,
        "pilot_repeat_count": pilot_repeat_count,
        "repeat_count": n,
        "mean": sec(avg),
        "std": sec(std) if n >= 2 else "",
        "confidence_level": sec(confidence_level),
        "z_critical": sec(z),
        "ci_half_width": sec(ci_half_width),
        "target_relative_error": sec(relative_error_target),
        "target_half_width": sec(target_half_width),
        "relative_half_width": sec(relative_half_width),
        "required_repeats": required_repeats if status != "INSUFFICIENT_DATA" else "",
        "additional_repeats_required": additional,
        "status": status,
        "reason": reason,
    }


def relative_error_rows(
    route_id: str,
    rows: list[dict[str, Any]],
    *,
    pilot_repeat_count: int,
    confidence_level: float,
    relative_error_target: float,
    max_repeats: int,
) -> list[dict[str, Any]]:
    return [
        relative_error_metric_row(
            route_id,
            metric,
            values,
            pilot_repeat_count=pilot_repeat_count,
            confidence_level=confidence_level,
            relative_error_target=relative_error_target,
            max_repeats=max_repeats,
        )
        for metric, values in relative_error_metric_values(rows).items()
    ]


def required_repeats_from_relative_error(rows: list[dict[str, Any]]) -> int:
    required = 0
    for row in rows:
        value = row.get("required_repeats", "")
        if value in {"", None}:
            continue
        required = max(required, int(safe_float(value)))
    return required


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


def repeat_artifact_paths(candidate: dict[str, Any], run_root: Path, repeat_idx: int) -> dict[str, Path]:
    route_id = str(candidate["route_id"])
    repeat_text = f"repeat_{repeat_idx:03d}"
    route_root = run_root / route_id / "routes"
    return {
        "route_xml": route_root / f"firetruck_depart_{repeat_idx:03d}.rou.xml",
        "route_csv": route_root / f"firetruck_depart_{repeat_idx:03d}.csv",
        "stage1_dir": run_root / route_id / "stage1" / repeat_text,
    }


def planned_task_rows(
    candidates: list[dict[str, Any]],
    departures_by_route: dict[str, list[float]],
    run_root: Path,
    *,
    phase: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        route_id = str(candidate["route_id"])
        stage1_dir = rel(Path(candidate["stage1_dir"])) if candidate.get("stage1_dir") else ""
        route_xml = rel(Path(candidate["route_xml"])) if candidate.get("route_xml") else ""
        rows.append({
            "phase": phase,
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
            repeat_paths = repeat_artifact_paths(candidate, run_root, repeat_idx)
            for mode in [B04_MODE, B4_MODE]:
                rows.append({
                    "phase": phase,
                    "candidate_rank": candidate.get("candidate_rank", ""),
                    "route_id": route_id,
                    "mode": mode,
                    "repeat_id": repeat_text,
                    "emergency_depart": depart,
                    "run_dir": rel(run_root / route_id / mode / repeat_text),
                    "route_xml": rel(repeat_paths["route_xml"]),
                    "stage1_dir": rel(repeat_paths["stage1_dir"]),
                })
    return rows


def resolve_theta_evaluations_csv(theta_latest: Path, theta_all_evaluations: Path | None = None) -> tuple[Path, dict[str, Any]]:
    if theta_all_evaluations is not None:
        path = theta_all_evaluations.resolve()
        if not path.is_file():
            raise FinalDestinationValidationError(f"missing_theta_all_evaluations:{rel(path)}")
        return path, {"theta_all_evaluations_csv": rel(path), "theta_latest_json": ""}
    latest = read_json(theta_latest)
    csv_value = latest.get("all_evaluations_csv")
    if not csv_value:
        raise FinalDestinationValidationError(f"theta_latest_missing_all_evaluations_csv:{rel(theta_latest)}")
    path = project_path(str(csv_value)).resolve()
    if not path.is_file():
        raise FinalDestinationValidationError(f"missing_theta_all_evaluations:{rel(path)}")
    return path, {
        "theta_latest_json": rel(theta_latest),
        "theta_output_dir": str(latest.get("output_dir", "")),
        "theta_run_id": str(latest.get("run_id", "")),
        "theta_all_evaluations_csv": rel(path),
    }


def is_smoke_theta_source(provenance: dict[str, Any], rows: list[dict[str, str]]) -> bool:
    run_id = str(provenance.get("theta_run_id", "")).lower()
    if "smoke" in run_id:
        return True
    methods = {(row.get("method", ""), row.get("seed", "")) for row in rows}
    max_round = max([safe_float(row.get("round"), 0.0) for row in rows] or [0.0])
    return len(methods) < 6 or max_round < 50


def load_final_b4_params(
    *,
    theta_latest: Path,
    theta_all_evaluations: Path | None = None,
    theta_method: str = "ALL",
    parameter_id: str = "final_validation_locked_theta",
) -> tuple[B4ThetaParams, dict[str, Any]]:
    csv_path, provenance = resolve_theta_evaluations_csv(theta_latest, theta_all_evaluations)
    rows = read_csv(csv_path)
    method_filter = theta_method.strip()
    candidates: list[dict[str, str]] = []
    for row in rows:
        if row.get("final_status") != "PASS":
            continue
        if method_filter != "ALL" and row.get("method") != method_filter:
            continue
        if any(row.get(field) in {"", None} for field in THETA_FIELDS):
            continue
        candidates.append(row)
    if not candidates:
        raise FinalDestinationValidationError(f"no_passing_theta_for_method:{theta_method}")
    selected = sorted(candidates, key=lambda row: (safe_float(row.get("score"), float("inf")), row.get("method", ""), row.get("seed", ""), safe_float(row.get("round"), 0.0)))[0]
    theta = {field: selected[field] for field in THETA_FIELDS}
    params = B4ThetaParams.from_row({"parameter_id": parameter_id, **theta})
    provenance.update({
        "schema": "compact_v9_final_destination_theta_lock.v1",
        "theta_selection_policy": "minimum_score_among_final_status_PASS_rows",
        "theta_method_filter": method_filter,
        "selected_method": selected.get("method", ""),
        "selected_seed": selected.get("seed", ""),
        "selected_round": selected.get("round", ""),
        "selected_score": selected.get("score", ""),
        "decision_variables_fixed": {field: params.as_result_fields()[field] for field in THETA_FIELDS},
        "legacy_alpha_q_trig_used_as_decision_variables": False,
        "bo_enabled": False,
        "bayesian_optimization_executed_by_final_validation": False,
        "theta_source_smoke_warning": is_smoke_theta_source(provenance, rows),
    })
    return params, provenance


def safe_id(value: Any, fallback: str = "theta") -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
    text = "_".join(part for part in text.split("_") if part)
    return text[:120] or fallback


def clone_args(args: argparse.Namespace, **overrides: Any) -> argparse.Namespace:
    payload = dict(vars(args))
    payload.update(overrides)
    return argparse.Namespace(**payload)


def theta_sort_score(row: dict[str, str]) -> tuple[float, float, float]:
    penalty = safe_float(row.get("penalty"), 0.0)
    return (
        safe_float(row.get("score"), float("inf")) + penalty,
        safe_float(row.get("D_E_sec"), float("inf")),
        safe_float(row.get("D_G_sec"), float("inf")),
    )


def theta_identity(row: dict[str, Any]) -> tuple[float, float, float, float, float]:
    return tuple(round(safe_float(row.get(field)), 6) for field in THETA_FIELDS)  # type: ignore[return-value]


def normalized_theta_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    ranges = {
        "t_lead": (0.0, 122.0),
        "delta_T_thr": (0.0, 244.0),
        "G_ext": (0.0, 50.0),
        "Q_ratio": (0.0, 1.0),
        "tau": (0.70, 0.90),
    }
    total = 0.0
    for field, (lower, upper) in ranges.items():
        width = max(upper - lower, 1.0)
        delta = (safe_float(left.get(field)) - safe_float(right.get(field))) / width
        total += delta * delta
    return math.sqrt(total)


def select_robust_theta_candidates(
    rows: list[dict[str, str]],
    *,
    method_filter: str,
    limit: int,
    diversity_min_distance: float,
) -> list[dict[str, Any]]:
    method_filter = method_filter.strip()
    eligible: list[dict[str, Any]] = []
    seen: set[tuple[float, float, float, float, float]] = set()
    for row_index, row in enumerate(rows, start=1):
        if row.get("final_status") != "PASS":
            continue
        if method_filter != "ALL" and row.get("method") != method_filter:
            continue
        if any(row.get(field) in {"", None} for field in THETA_FIELDS):
            continue
        key = theta_identity(row)
        if key in seen:
            continue
        seen.add(key)
        eligible.append({
            "source_row_index": row_index,
            "method": row.get("method", ""),
            "seed": row.get("seed", ""),
            "round": row.get("round", ""),
            "round_theta_index": row.get("round_theta_index", ""),
            "parameter_id": row.get("parameter_id") or f"theta_row_{row_index:04d}",
            **{field: row.get(field, "") for field in THETA_FIELDS},
            "source_score": row.get("score", ""),
            "source_D_E_sec": row.get("D_E_sec", ""),
            "source_D_G_sec": row.get("D_G_sec", ""),
            "source_final_status": row.get("final_status", ""),
            "source_failure_reason": row.get("failure_reason", ""),
            "_sort_score": theta_sort_score(row),
        })
    eligible.sort(key=lambda row: row["_sort_score"])
    selected: list[dict[str, Any]] = []
    deferred: list[tuple[dict[str, Any], float]] = []
    for row in eligible:
        nearest = min((normalized_theta_distance(row, other) for other in selected), default=float("inf"))
        if nearest >= diversity_min_distance or len(selected) < max(1, limit // 3):
            row = dict(row)
            row["selection_reason"] = "score_ranked_diverse_pass_bo_candidate"
            row["nearest_selected_distance"] = "" if math.isinf(nearest) else sec(nearest)
            selected.append(row)
            if len(selected) >= limit:
                break
        else:
            deferred.append((row, nearest))
    if len(selected) < limit:
        for row, nearest in deferred:
            row = dict(row)
            row["selection_reason"] = "score_ranked_fill_after_diversity_pass"
            row["nearest_selected_distance"] = sec(nearest)
            selected.append(row)
            if len(selected) >= limit:
                break
    for index, row in enumerate(selected, start=1):
        row["theta_rank"] = index
        row.pop("_sort_score", None)
    return selected


def robust_theta_params(row: dict[str, Any]) -> B4ThetaParams:
    return B4ThetaParams.from_row({
        "parameter_id": safe_id(row.get("parameter_id") or f"robust_theta_{row.get('theta_rank', '')}"),
        **{field: row.get(field, "") for field in THETA_FIELDS},
    })


def robust_summary_from_rows(
    theta_row: dict[str, Any],
    route_id: str,
    route_rows: list[dict[str, Any]],
    departures: list[float],
    *,
    output_root: Path,
    run_root: Path,
) -> dict[str, Any]:
    b4_rows = [row for row in route_rows if row.get("mode") == B4_MODE]
    d_e_values = [safe_float(row.get("D_E_sec")) for row in b4_rows if row.get("D_E_sec") not in {"", None}]
    d_g_values = [safe_float(row.get("D_G_sec")) for row in b4_rows if row.get("D_G_sec") not in {"", None}]
    score_values = [
        safe_float(objective_score_from_row(row, W_E, W_G))
        for row in b4_rows
        if objective_score_from_row(row, W_E, W_G) not in {"", None}
    ]
    improvement_values: list[float] = []
    for _repeat, b04, b4 in paired_repeat_rows(route_rows):
        b04_time = t_emv(b04)
        b4_time = t_emv(b4)
        if b04_time is not None and b4_time is not None:
            improvement_values.append(b04_time - b4_time)
    stage2_values = [safe_float(row.get("stage2_hold_count")) for row in b4_rows if row.get("stage2_hold_count") not in {"", None}]
    stage3_values = [safe_float(row.get("stage3_preemption_count")) for row in b4_rows if row.get("stage3_preemption_count") not in {"", None}]
    repeat_count = len(b4_rows)
    stuck_count = sum(str(row.get("failure_reason", "")).strip() == "emergency_stuck" for row in b4_rows)
    fail_count = sum(row.get("final_status") != "PASS" or bool_cell(row.get("failed")) for row in b4_rows)
    teleport_count = sum(bool_cell(row.get("emergency_teleport")) for row in b4_rows)
    arrival_rate = sum(bool_cell(row.get("emergency_arrived")) for row in b4_rows) / repeat_count if repeat_count else 0.0
    survivor = stuck_count == 0 and fail_count == 0 and teleport_count == 0 and arrival_rate == 1.0 and repeat_count == len(departures)
    reason = "stuck_free_full_arrival_mini_batch" if survivor else "mini_batch_failed_stuck_fail_teleport_or_arrival_gate"
    return {
        "theta_rank": theta_row.get("theta_rank", ""),
        "parameter_id": theta_row.get("parameter_id", ""),
        **{field: theta_row.get(field, "") for field in THETA_FIELDS},
        "route_id": route_id,
        "departures": ";".join(str(value) for value in departures),
        "repeat_count": repeat_count,
        "stuck_count": stuck_count,
        "fail_count": fail_count,
        "arrival_rate": sec(arrival_rate),
        "teleport_count": teleport_count,
        "mean_D_E_sec": sec(mean(d_e_values)),
        "mean_D_G_sec": sec(mean(d_g_values)),
        "mean_score": sec(mean(score_values)),
        "mean_B4_vs_B04_D_E_improvement_sec": sec(mean(improvement_values)),
        "stage2_hold_mean": sec(mean(stage2_values)),
        "stage3_preemption_mean": sec(mean(stage3_values)),
        "survivor_status": "SURVIVOR" if survivor else "REJECTED",
        "survivor_reason": reason,
        "mini_batch_output_root": rel(output_root),
        "mini_batch_run_root": rel(run_root),
    }


def robust_summary_sort_key(row: dict[str, Any]) -> tuple[int, int, int, float, float]:
    return (
        0 if row.get("survivor_status") == "SURVIVOR" else 1,
        int(safe_float(row.get("stuck_count"))),
        int(safe_float(row.get("fail_count"))),
        -safe_float(row.get("arrival_rate")),
        safe_float(row.get("mean_score"), float("inf")),
    )


def relative_error_all_pass(rows: list[dict[str, Any]]) -> tuple[bool, str]:
    if not rows:
        return False, ""
    statuses = [str(row.get("status", "")) for row in rows]
    return all(status == "PASS" for status in statuses), ";".join(statuses)


def final_ranking_row(
    final_rank: int,
    theta_row: dict[str, Any],
    route_id: str,
    child_run_id: str,
    output_root: Path,
) -> dict[str, Any]:
    route_rows = read_csv(output_root / "selected_route_runs.csv")
    relative_rows = read_csv(output_root / "relative_error_sufficiency.csv")
    summary = robust_summary_from_rows(
        theta_row,
        route_id,
        route_rows,
        [],
        output_root=output_root,
        run_root=PROJECT_ROOT / "runs",
    )
    relative_pass, relative_statuses = relative_error_all_pass(relative_rows)
    stable = (
        int(safe_float(summary.get("stuck_count"))) == 0
        and int(safe_float(summary.get("fail_count"))) == 0
        and safe_float(summary.get("arrival_rate")) == 1.0
        and int(safe_float(summary.get("teleport_count"))) == 0
        and relative_pass
    )
    return {
        "final_rank": final_rank,
        "theta_rank": theta_row.get("theta_rank", ""),
        "parameter_id": theta_row.get("parameter_id", ""),
        **{field: theta_row.get(field, "") for field in THETA_FIELDS},
        "route_id": route_id,
        "run_id": child_run_id,
        "selected_route_runs_csv": rel(output_root / "selected_route_runs.csv"),
        "relative_error_sufficiency_csv": rel(output_root / "relative_error_sufficiency.csv"),
        "repeat_count": summary["repeat_count"],
        "stuck_count": summary["stuck_count"],
        "fail_count": summary["fail_count"],
        "arrival_rate": summary["arrival_rate"],
        "teleport_count": summary["teleport_count"],
        "mean_D_E_sec": summary["mean_D_E_sec"],
        "mean_D_G_sec": summary["mean_D_G_sec"],
        "mean_score": summary["mean_score"],
        "mean_B4_vs_B04_D_E_improvement_sec": summary["mean_B4_vs_B04_D_E_improvement_sec"],
        "relative_error_pass": str(relative_pass),
        "relative_error_statuses": relative_statuses,
        "selection_status": "FINAL_SELECTED" if stable else "FINAL_DIAGNOSTIC",
        "selection_reason": "stuck_free_relative_error_pass_min_score" if stable else "failed_stability_or_relative_error_gate",
    }


def load_stage1_module() -> Any:
    path = PIPELINE_DIR / "b4_stage1_pipeline.py"
    spec = importlib.util.spec_from_file_location("compact_v9_final_destination_stage1", path)
    if spec is None or spec.loader is None:
        raise FinalDestinationValidationError(f"stage1_module_load_failed:{rel(path)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ensure_worker_imports() -> None:
    load_stage1_module()


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


def assign_candidate_artifact_paths(candidates: list[dict[str, Any]], input_root: Path) -> list[dict[str, Any]]:
    for rank, candidate in enumerate(candidates, start=1):
        candidate["candidate_rank"] = rank
        route_root = input_root / candidate["route_id"]
        candidate["route_csv"] = route_root / "firetruck_route.csv"
        candidate["route_xml"] = route_root / "firetruck_route_depart_600.rou.xml"
        candidate["stage1_dir"] = input_root / "stage1" / candidate["route_id"]
    return candidates


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
        route_id = f"FINAL_DEST_{source_route_id}"
        try:
            sumo_net.getEdge(target_edge)
        except Exception:
            candidates.append({
                "route_id": route_id,
                "source_route_id": source_route_id,
                "target_edge_id": target_edge,
                "selected_policy": "compact_v9_shortest_from_fire_station_existing18_target",
                "route_edges": [],
                "route_edge_count": 0,
                "route_length_m": "",
                "start_edge_id": args.start_edge,
                "merge_edge_id": "",
                "mainroad_length_ratio": 0.0,
                "legacy_spine_length_ratio": round(safe_float(row.get("spine_length_ratio"), 0.0), 6),
                "review_status": row.get("review_status", ""),
                "route_priority_score": -1.0,
                "precheck_status": "EXCLUDED",
                "precheck_reason": "target_edge_missing_in_s1forced_net",
            })
            continue
        try:
            edges = shortest_route(sumo_net, args.start_edge, target_edge)
        except Exception:
            candidates.append({
                "route_id": route_id,
                "source_route_id": source_route_id,
                "target_edge_id": target_edge,
                "selected_policy": "compact_v9_shortest_from_fire_station_existing18_target",
                "route_edges": [],
                "route_edge_count": 0,
                "route_length_m": "",
                "start_edge_id": args.start_edge,
                "merge_edge_id": "",
                "mainroad_length_ratio": 0.0,
                "legacy_spine_length_ratio": round(safe_float(row.get("spine_length_ratio"), 0.0), 6),
                "review_status": row.get("review_status", ""),
                "route_priority_score": -1.0,
                "precheck_status": "EXCLUDED",
                "precheck_reason": "shortest_route_failed",
            })
            continue
        if not connected_route(sumo_net, edges):
            candidates.append({
                "route_id": route_id,
                "source_route_id": source_route_id,
                "target_edge_id": target_edge,
                "selected_policy": "compact_v9_shortest_from_fire_station_existing18_target",
                "route_edges": edges,
                "route_edge_count": len(edges),
                "route_length_m": "",
                "start_edge_id": args.start_edge,
                "merge_edge_id": "",
                "mainroad_length_ratio": 0.0,
                "legacy_spine_length_ratio": round(safe_float(row.get("spine_length_ratio"), 0.0), 6),
                "review_status": row.get("review_status", ""),
                "route_priority_score": -1.0,
                "precheck_status": "EXCLUDED",
                "precheck_reason": "shortest_route_not_connected",
            })
            continue
        total_len = route_length(sumo_net, edges)
        main_len = route_length(sumo_net, [edge for edge in edges if edge in main_edges]) if main_edges else 0.0
        legacy_spine = safe_float(row.get("spine_length_ratio"), 0.0)
        compact_ratio = main_len / total_len if total_len > 0 else 0.0
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
            "precheck_status": "PASS",
            "precheck_reason": "",
        })
    candidates.sort(
        key=lambda item: (
            item.get("precheck_status") != "PASS",
            item.get("review_status") not in {"PASS", "WARNING"},
            -safe_float(item.get("route_priority_score")),
            -safe_float(item.get("mainroad_length_ratio")),
            safe_float(item.get("route_length_m")),
            str(item.get("source_route_id")),
        )
    )
    selected = assign_candidate_artifact_paths(candidates[: args.candidate_limit], input_root)
    if not selected:
        raise FinalDestinationValidationError("no_compact_v9_reachable_candidates")
    return selected


def clone_candidates_for_phase(candidates: list[dict[str, Any]], route_ids: list[str], input_root: Path) -> list[dict[str, Any]]:
    wanted = set(route_ids)
    selected = [
        {key: value for key, value in candidate.items() if key not in {"route_csv", "route_xml", "stage1_dir"}}
        for candidate in candidates
        if candidate.get("route_id") in wanted or candidate.get("source_route_id") in wanted
    ]
    if len(selected) != len(wanted):
        found = {str(candidate.get("route_id")) for candidate in selected} | {str(candidate.get("source_route_id")) for candidate in selected}
        missing = sorted(wanted - found)
        raise FinalDestinationValidationError(f"missing_selected_final_candidates:{','.join(missing)}")
    return assign_candidate_artifact_paths(selected, input_root)


def write_firetruck_route_xml_artifact(candidate: dict[str, Any], route_xml: Path, depart: float) -> None:
    route_xml.parent.mkdir(parents=True, exist_ok=True)
    edges_text = " ".join(candidate["route_edges"])
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


def write_firetruck_route_artifacts(candidate: dict[str, Any], route_xml: Path, route_csv: Path, depart: float) -> None:
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
    write_firetruck_route_xml_artifact(candidate, route_xml, depart)


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
    primary_candidate = str(summary.get("primary_candidate") or "B04_ad_stage23_trigger")
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


def build_route_stage1(
    args: argparse.Namespace,
    candidate: dict[str, Any],
    *,
    route_xml: Path | None = None,
    route_csv: Path | None = None,
    stage1_dir: Path | None = None,
    depart: float = 600.0,
    repeat_id: str | int = "reference",
    phase: str = "",
) -> dict[str, Any]:
    stage1_module = load_stage1_module()
    source_provenance = configure_stage1_source(stage1_module, args)
    route_xml = Path(route_xml or candidate["route_xml"])
    route_csv = Path(route_csv or candidate["route_csv"])
    stage1_dir = Path(stage1_dir or candidate["stage1_dir"])
    write_firetruck_route_artifacts(candidate, route_xml, route_csv, depart)
    summary = stage1_module.build_b4_stage1(
        stage1_dir=stage1_dir,
        firetruck_route_xml=route_xml,
        firetruck_route_csv=route_csv,
        review_html=stage1_dir / "b4_stage1_review.html",
    )
    summary_path = stage1_dir / "b4_stage1_summary.json"
    runtime_index_path = stage1_dir / "b4_runtime_index.json"
    for path in [summary_path, runtime_index_path]:
        payload = read_json(path)
        payload["allow_runtime_input_override"] = True
        payload["stage1_rebuild_policy"] = STAGE1_REBUILD_POLICY
        payload["runtime_input_provenance"] = {
            **source_provenance,
            "net_file": rel(args.net),
            "background_route": rel(args.background_route),
            "route_xml": rel(route_xml),
            "route_csv": rel(route_csv),
            "ev_depart_sec": float(depart),
            "repeat_id": str(repeat_id),
            "phase": phase,
            "stage1_rebuild_policy": STAGE1_REBUILD_POLICY,
        }
        if path == summary_path:
            artifacts = dict(payload.get("input_artifacts", {}))
            artifacts.update({
                "b04_net": rel(args.net),
                "background_route": rel(args.background_route),
                "firetruck_route_xml": rel(route_xml),
                "firetruck_route_csv": rel(route_csv),
                "primary_run_summary": source_provenance["primary_run_summary"],
                "segment_speed_recall": source_provenance["segment_speed_recall"],
                "b4_queue_measurement_diagnostics": source_provenance["queue_measurement_diagnostics"],
            })
            payload["input_artifacts"] = artifacts
        write_json(path, payload)
    return read_json(summary_path) if summary_path.is_file() else summary


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
    *,
    repeat_offset: int = 0,
    include_reference: bool = True,
    repeat_workers: int = 1,
) -> list[dict[str, Any]]:
    phase = run_root.name
    base_route_xml = Path(candidate["route_xml"])
    stage1_dir = Path(candidate["stage1_dir"])
    if include_reference or not (stage1_dir / "b4_runtime_index.json").is_file():
        stage1_summary = build_route_stage1(
            args,
            candidate,
            route_xml=base_route_xml,
            route_csv=Path(candidate["route_csv"]),
            stage1_dir=stage1_dir,
            depart=600.0,
            repeat_id="reference",
            phase=phase,
        )
    else:
        stage1_summary = read_json(stage1_dir / "b4_stage1_summary.json")
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
    if include_reference:
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
        b004_row = b004_result_row(b004_task, stage1, free_reference, base_phase)
        b004_row["stage1_dir"] = rel(stage1_dir)
        rows.append(enrich_row(b004_row, candidate, ""))
    repeat_jobs = [(repeat_idx, depart) for repeat_idx, depart in enumerate(departures, start=repeat_offset + 1)]
    if repeat_workers > 1 and len(repeat_jobs) > 1:
        ensure_worker_imports()
        with ProcessPoolExecutor(max_workers=min(repeat_workers, len(repeat_jobs)), initializer=ensure_worker_imports) as executor:
            futures = [
                executor.submit(
                    run_repeat_pair_worker,
                    (
                        args,
                        candidate,
                        repeat_idx,
                        depart,
                        params,
                        run_root,
                        free_reference,
                        free_rows_by_id,
                        base_phase,
                    ),
                )
                for repeat_idx, depart in repeat_jobs
            ]
            repeat_results = [future.result() for future in as_completed(futures)]
        for repeat_rows in sorted(repeat_results, key=lambda item: int(safe_float(item[0].get("repeat_id"), 0.0)) if item else 0):
            rows.extend(repeat_rows)
    else:
        for repeat_idx, depart in repeat_jobs:
            rows.extend(
                run_repeat_pair(
                    args,
                    candidate,
                    repeat_idx,
                    depart,
                    params,
                    run_root,
                    free_reference,
                    free_rows_by_id,
                    base_phase,
                )
            )
    stage1_summary_path = metrics_root / candidate["route_id"] / "stage1_summary_snapshot.json"
    write_json(stage1_summary_path, stage1_summary)
    return rows


def run_repeat_pair(
    args: argparse.Namespace,
    candidate: dict[str, Any],
    repeat_idx: int,
    depart: float,
    params: B4ThetaParams,
    run_root: Path,
    free_reference: dict[str, Any],
    free_rows_by_id: dict[str, dict[str, str]],
    base_phase: B4RuntimePhaseConfig,
) -> list[dict[str, Any]]:
    repeat_paths = repeat_artifact_paths(candidate, run_root, repeat_idx)
    repeat_route_xml = repeat_paths["route_xml"]
    repeat_route_csv = repeat_paths["route_csv"]
    repeat_stage1_dir = repeat_paths["stage1_dir"]
    build_route_stage1(
        args,
        candidate,
        route_xml=repeat_route_xml,
        route_csv=repeat_route_csv,
        stage1_dir=repeat_stage1_dir,
        depart=depart,
        repeat_id=repeat_idx,
        phase=run_root.name,
    )
    repeat_stage1 = B4Stage1Inputs.load(repeat_stage1_dir, route_xml=repeat_route_xml)
    phase_config = phase_for_depart(base_phase, depart)
    rows: list[dict[str, Any]] = []
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
            row = run_b04_task(
                task,
                repeat_stage1,
                phase_config,
                free_reference,
                free_rows_by_id,
                args.sumo_binary,
                args.emit_fcd,
                emit_tls_states=getattr(args, "emit_tls_states", False),
            )
        else:
            row = run_b4_task(
                task,
                repeat_stage1,
                phase_config,
                free_reference,
                free_rows_by_id,
                args.sumo_binary,
                args.emit_fcd,
                params=params,
                emit_tls_states=getattr(args, "emit_tls_states", False),
            )
        row["stage1_dir"] = rel(repeat_stage1_dir)
        rows.append(enrich_row(row, candidate, depart))
    return rows


def run_repeat_pair_worker(payload: tuple[argparse.Namespace, dict[str, Any], int, float, B4ThetaParams, Path, dict[str, Any], dict[str, dict[str, str]], B4RuntimePhaseConfig]) -> list[dict[str, Any]]:
    args, candidate, repeat_idx, depart, params, run_root, free_reference, free_rows_by_id, base_phase = payload
    return run_repeat_pair(args, candidate, repeat_idx, depart, params, run_root, free_reference, free_rows_by_id, base_phase)


def run_candidate_worker(payload: tuple[int, argparse.Namespace, dict[str, Any], list[float], B4ThetaParams, Path, Path, int, bool, int]) -> dict[str, Any]:
    index, args, candidate, departures, params, run_root, output_root, repeat_offset, include_reference, repeat_workers = payload
    route_rows = run_candidate(
        args,
        candidate,
        departures,
        params,
        run_root,
        output_root,
        repeat_offset=repeat_offset,
        include_reference=include_reference,
        repeat_workers=repeat_workers,
    )
    route_id = str(candidate["route_id"])
    return {
        "index": index,
        "route_id": route_id,
        "route_rows": route_rows,
        "candidate_row": summarize_candidate(candidate, route_rows),
        "average_rows": average_rows(route_rows, route_id),
    }


def run_robust_mini_batch_worker(payload: tuple[int, argparse.Namespace, dict[str, Any], dict[str, Any], list[float], Path, Path, int]) -> dict[str, Any]:
    index, args, candidate, theta_row, departures, run_root, output_root, repeat_workers = payload
    params = robust_theta_params(theta_row)
    result = run_candidate_worker((index, args, candidate, departures, params, run_root, output_root, 0, True, repeat_workers))
    route_rows = result["route_rows"]
    write_csv(output_root / "route_runs.csv", route_rows, RUN_FIELDS)
    write_csv(output_root / "mode_averages.csv", result["average_rows"], AVERAGE_FIELDS)
    summary = robust_summary_from_rows(
        theta_row,
        str(candidate["route_id"]),
        route_rows,
        departures,
        output_root=output_root,
        run_root=run_root,
    )
    write_json(output_root / "mini_batch_summary.json", {"summary": summary, "theta": theta_row})
    return {
        "index": index,
        "theta_row": theta_row,
        "summary": summary,
        "route_rows": route_rows,
    }


def t_emv(row: dict[str, Any]) -> float | None:
    value = row.get("T_actual_EMV_sec")
    if row.get("mode") == B004_MODE and value in {"", None}:
        value = row.get("T_free_EMV_sec")
    if value in {"", None}:
        return None
    return safe_float(value)


def average_rows(rows: list[dict[str, Any]], route_id: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for mode in [B004_MODE, B04_MODE, B4_MODE]:
        group = [row for row in rows if row.get("mode") == mode]
        t_values = [value for value in (t_emv(row) for row in group) if value is not None]
        d_e_values = [safe_float(row.get("D_E_sec")) for row in group if row.get("D_E_sec") not in {"", None}]
        d_g_values = [safe_float(row.get("D_G_sec")) for row in group if row.get("D_G_sec") not in {"", None}]
        objective_values = [safe_float(row.get("objective_score")) for row in group if row.get("objective_score") not in {"", None}]
        stage3_values = [safe_float(row.get("stage3_preemption_count")) for row in group if row.get("stage3_preemption_count") not in {"", None}]
        stage2_values = [safe_float(row.get("stage2_hold_count")) for row in group if row.get("stage2_hold_count") not in {"", None}]
        result.append({
            "route_id": route_id,
            "mode": mode,
            "run_count": len(group),
            "T_EMV_mean_sec": sec(mean(t_values)),
            "T_EMV_std_sec": sec(sample_std(t_values)) if t_values else "",
            "D_E_mean_sec": sec(mean(d_e_values)),
            "D_G_mean_sec": sec(mean(d_g_values)),
            "objective_score_mean": sec(mean(objective_values)),
            "emergency_arrival_rate": sec(sum(bool_cell(row.get("emergency_arrived")) for row in group) / len(group)) if group else "",
            "teleport_count": sum(bool_cell(row.get("emergency_teleport")) for row in group),
            "fail_count": sum(row.get("final_status") == "FAIL" or bool_cell(row.get("failed")) for row in group),
            "stage3_preemption_mean": sec(mean(stage3_values)),
            "stage2_hold_mean": sec(mean(stage2_values)),
        })
    return result


def repeat_stability_rows(route_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    b04_by_repeat = {
        int(safe_float(row.get("repeat_id"), 0.0)): row
        for row in rows
        if row.get("mode") == B04_MODE and safe_float(row.get("repeat_id"), 0.0) > 0
    }
    b4_by_repeat = {
        int(safe_float(row.get("repeat_id"), 0.0)): row
        for row in rows
        if row.get("mode") == B4_MODE and safe_float(row.get("repeat_id"), 0.0) > 0
    }
    improvements: list[float] = []
    b4_d_e_values: list[float] = []
    b4_d_g_values: list[float] = []
    interventions: list[float] = []
    for repeat in sorted(set(b04_by_repeat) & set(b4_by_repeat)):
        b04 = b04_by_repeat[repeat]
        b4 = b4_by_repeat[repeat]
        b04_time = t_emv(b04)
        b4_time = t_emv(b4)
        if b04_time is not None and b4_time is not None:
            improvements.append(b04_time - b4_time)
        if b4.get("D_E_sec") not in {"", None}:
            b4_d_e_values.append(safe_float(b4.get("D_E_sec")))
        if b4.get("D_G_sec") not in {"", None}:
            b4_d_g_values.append(safe_float(b4.get("D_G_sec")))
        interventions.append(safe_float(b4.get("stage2_hold_count")) + safe_float(b4.get("stage3_preemption_count")))
    return [
        spc_metric_row(route_id, "B4_vs_B04_D_E_improvement_sec", improvements),
        spc_metric_row(route_id, "B4_D_E_sec", b4_d_e_values),
        spc_metric_row(route_id, "B4_D_G_sec", b4_d_g_values),
        spc_metric_row(route_id, "B4_intervention_count", interventions),
    ]


def objective_score_from_row(row: dict[str, Any], w_E: float, w_G: float) -> str:
    if row.get("objective_score") not in {"", None}:
        return row.get("objective_score", "")
    total = w_E + w_G
    if total <= 0.0:
        return ""
    D_E_sec = safe_float(row.get("D_E_sec"))
    D_G_sec = safe_float(row.get("D_G_sec"))
    return sec((w_E / total) * D_E_sec + (w_G / total) * D_G_sec)


def normalize_objective_weights(w_E: float, w_G: float) -> tuple[float, float]:
    total = w_E + w_G
    if total <= 0.0:
        return 0.0, 0.0
    return w_E / total, w_G / total


def final_simulation_result_rows(rows: list[dict[str, Any]], params: B4ThetaParams) -> list[dict[str, Any]]:
    final_rows: list[dict[str, Any]] = []
    for row in rows:
        if row.get("mode") != B4_MODE:
            continue
        w_E = safe_float(row.get("w_E"), W_E)
        w_G = safe_float(row.get("w_G"), W_G)
        w_E_norm, w_G_norm = normalize_objective_weights(w_E, w_G)
        D_G_sec = row.get("D_G_sec", "")
        final_rows.append({
            "input_phase": row.get("phase", PHASE_FINAL),
            "input_route_id": row.get("route_id", ""),
            "input_source_route_id": row.get("source_route_id", ""),
            "input_target_edge_id": row.get("target_edge_id", ""),
            "input_repeat_id": row.get("repeat_id", ""),
            "input_parameter_id": row.get("parameter_id", params.parameter_id),
            "input_t_lead": params.t_lead,
            "input_delta_T_thr": params.delta_T_thr,
            "input_G_ext": params.G_ext,
            "input_Q_ratio": params.Q_ratio,
            "input_tau": params.tau,
            "output_D_E_sec": row.get("D_E_sec", ""),
            "output_D_G_sec": D_G_sec,
            "weight_E": sec(w_E_norm),
            "weight_G": sec(w_G_norm),
            "weight_ratio": f"{w_E:g}:{w_G:g}",
            "score": objective_score_from_row(row, w_E, w_G),
            "measured_T_free_EMV_sec": row.get("T_free_EMV_sec", ""),
            "measured_T_actual_EMV_sec": row.get("T_actual_EMV_sec", ""),
            "measured_D_E_sec": row.get("D_E_sec", ""),
            "measured_D_G_sec": D_G_sec,
            "measured_T_G_actual_mean_sec": row.get("T_G_actual_mean_sec", ""),
            "measured_T_G_free_mean_sec": row.get("T_G_free_mean_sec", ""),
            "stage2_on_count": row.get("stage2_hold_count", ""),
            "stage3_on_count": row.get("stage3_preemption_count", ""),
        })
    return final_rows


def summarize_candidate(candidate: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    averages = {row["mode"]: row for row in average_rows(rows)}
    b004_time = safe_float(averages.get(B004_MODE, {}).get("T_EMV_mean_sec"))
    b04_time = safe_float(averages.get(B04_MODE, {}).get("T_EMV_mean_sec"))
    b4_time = safe_float(averages.get(B4_MODE, {}).get("T_EMV_mean_sec"))
    b04_d_e = b04_time - b004_time if b04_time and b004_time else 0.0
    improvement = b04_time - b4_time if b04_time and b4_time else 0.0
    b04_d_g = safe_float(averages.get(B04_MODE, {}).get("D_G_mean_sec"))
    b4_d_g = safe_float(averages.get(B4_MODE, {}).get("D_G_mean_sec"))
    b4_stage3 = safe_float(averages.get(B4_MODE, {}).get("stage3_preemption_mean"))
    b4_stage2 = safe_float(averages.get(B4_MODE, {}).get("stage2_hold_mean"))
    intervention = b4_stage3 + b4_stage2
    arrival_rates = [safe_float(row.get("emergency_arrival_rate"), 0.0) for row in averages.values() if row.get("emergency_arrival_rate") not in {"", None}]
    teleport_count = sum(int(safe_float(row.get("teleport_count"))) for row in averages.values())
    fail_count = sum(int(safe_float(row.get("fail_count"))) for row in averages.values())
    comparable = bool(b004_time and b04_time and b4_time)
    invalid = teleport_count > 0 or fail_count > 0 or (arrival_rates and min(arrival_rates) < 1.0) or not comparable
    if invalid:
        selection_status = "EXCLUDED"
        selection_reason = "excluded_due_to_failure_teleport_arrival_or_comparison_gap"
    elif improvement <= 0.0:
        selection_status = "EXCLUDED"
        selection_reason = "excluded_due_to_no_b4_improvement_over_b04"
    elif intervention <= 0.0:
        selection_status = "EXCLUDED"
        selection_reason = "excluded_due_to_no_b4_stage2_or_stage3_intervention"
    else:
        selection_status = "CANDIDATE"
        selection_reason = "valid_b4_improvement_with_actual_intervention"
    score = (
        10_000.0 * max(improvement, 0.0)
        + 1_000.0 * max(b04_d_e, 0.0)
        + 100.0 * intervention
        + 10.0 * safe_float(candidate.get("mainroad_length_ratio"))
        + 10.0 * safe_float(candidate.get("legacy_spine_length_ratio"))
    )
    if selection_status != "CANDIDATE":
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
        "B04_D_E_mean_sec": sec(b04_d_e),
        "B4_vs_B04_D_E_improvement_sec": sec(improvement),
        "B04_D_G_mean_sec": sec(b04_d_g),
        "B4_D_G_mean_sec": sec(b4_d_g),
        "B4_stage3_preemption_mean": sec(b4_stage3),
        "B4_stage2_hold_mean": sec(b4_stage2),
        "intervention_mean": sec(intervention),
        "arrival_rate_min": sec(min(arrival_rates) if arrival_rates else None),
        "teleport_count": teleport_count,
        "fail_count": fail_count,
        "presentation_fit_score": sec(score),
        "selection_rank": "",
        "selection_status": selection_status,
        "selection_reason": selection_reason,
    }


def candidate_selection_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float, float, int]:
    representativeness = (
        safe_float(row.get("mainroad_length_ratio"))
        + safe_float(row.get("legacy_spine_length_ratio"))
    )
    return (
        -safe_float(row.get("B4_vs_B04_D_E_improvement_sec")),
        -safe_float(row.get("B04_D_E_mean_sec")),
        -safe_float(row.get("intervention_mean")),
        -safe_float(row.get("mainroad_length_ratio")),
        -representativeness,
        int(safe_float(row.get("candidate_rank"), 9999)),
    )


def select_final_candidates(candidate_rows: list[dict[str, Any]], limit: int = DEFAULT_FINAL_SELECTION_COUNT) -> list[dict[str, Any]]:
    eligible = [row for row in candidate_rows if row.get("selection_status") == "CANDIDATE"]
    if eligible:
        selected = [dict(row) for row in sorted(eligible, key=candidate_selection_sort_key)[:limit]]
        for index, row in enumerate(selected, start=1):
            row["selection_rank"] = index
        return selected
    if not candidate_rows:
        raise FinalDestinationValidationError("no_candidate_rows_after_validation")
    fallback = [
        dict(row)
        for row in sorted(candidate_rows, key=lambda row: (-safe_float(row.get("presentation_fit_score")), int(safe_float(row.get("candidate_rank"), 9999))))[:limit]
    ]
    for index, row in enumerate(fallback, start=1):
        row["selection_rank"] = index
        row["selection_status"] = "FALLBACK_NO_ELIGIBLE"
        row["selection_reason"] = "all_candidates_failed_or_excluded; selected_best_available_for_diagnostics"
    return fallback


def select_final_candidate(candidate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return select_final_candidates(candidate_rows, limit=1)[0]


def validate_args(args: argparse.Namespace) -> None:
    for attr in ["routes_csv", "net", "background_route", "base_stage1_dir", "mainroad_mapping"]:
        value = Path(getattr(args, attr)).resolve()
        setattr(args, attr, value)
        if attr != "mainroad_mapping" and not value.exists():
            raise FinalDestinationValidationError(f"missing_required_input:{rel(value)}")
    if args.theta_all_evaluations is not None:
        args.theta_all_evaluations = Path(args.theta_all_evaluations).resolve()
        if not args.theta_all_evaluations.exists():
            raise FinalDestinationValidationError(f"missing_required_input:{rel(args.theta_all_evaluations)}")
    else:
        args.theta_latest = Path(args.theta_latest).resolve()
        if not args.theta_latest.exists():
            raise FinalDestinationValidationError(f"missing_required_input:{rel(args.theta_latest)}")
    args.run_root = Path(args.run_root).resolve()
    args.metrics_root = Path(args.metrics_root).resolve()
    if args.repeats < 1:
        raise FinalDestinationValidationError("repeats_must_be_positive")
    if args.pilot_repeats < 2:
        raise FinalDestinationValidationError("pilot_repeats_must_be_at_least_2")
    if args.adaptive_max_repeats < args.pilot_repeats:
        raise FinalDestinationValidationError("adaptive_max_repeats_must_be_gte_pilot_repeats")
    if not (0.0 < args.relative_error_target < 1.0):
        raise FinalDestinationValidationError("relative_error_target_must_be_between_0_and_1")
    if not (0.0 < args.confidence_level < 1.0):
        raise FinalDestinationValidationError("confidence_level_must_be_between_0_and_1")
    if args.screening_repeats != 1:
        raise FinalDestinationValidationError("screening_repeats_must_be_1_for_final_protocol")
    if args.candidate_limit < 1:
        raise FinalDestinationValidationError("candidate_limit_must_be_positive")
    if args.final_selection_count < 1:
        raise FinalDestinationValidationError("final_selection_count_must_be_positive")
    if args.depart_min > args.depart_max:
        raise FinalDestinationValidationError("depart_min_must_be_lte_depart_max")
    if args.workers < 1:
        raise FinalDestinationValidationError("workers_must_be_positive")
    if args.validation_mode == VALIDATION_MODE_ROBUST_THETA_SELECTION:
        if args.theta_all_evaluations is None:
            raise FinalDestinationValidationError("robust_theta_selection_requires_theta_all_evaluations")
        if args.robust_candidate_count < 1:
            raise FinalDestinationValidationError("robust_candidate_count_must_be_positive")
        if args.robust_mini_batch_repeats < 1:
            raise FinalDestinationValidationError("robust_mini_batch_repeats_must_be_positive")
        if args.robust_survivor_count < 1:
            raise FinalDestinationValidationError("robust_survivor_count_must_be_positive")
        if args.robust_final_top_k < 1:
            raise FinalDestinationValidationError("robust_final_top_k_must_be_positive")
        if args.robust_theta_workers < 1:
            raise FinalDestinationValidationError("robust_theta_workers_must_be_positive")
        if args.robust_repeat_workers < 0:
            raise FinalDestinationValidationError("robust_repeat_workers_must_be_nonnegative")
        if args.robust_theta_workers > 1 and args.robust_repeat_workers > 1:
            raise FinalDestinationValidationError("robust_repeat_workers_must_be_1_when_theta_workers_gt_1")
        if args.robust_repeat_workers > 0 and args.robust_theta_workers * args.robust_repeat_workers > args.workers:
            raise FinalDestinationValidationError("robust_theta_workers_times_repeat_workers_must_not_exceed_workers")
    if not args.dry_run and shutil.which(args.sumo_binary or "sumo") is None:
        raise FinalDestinationValidationError("missing_executable:sumo")
    args.run_id = args.run_id or default_run_id()
    args.active_inputs_audit = validate_active_inputs(args)


def departures_for_candidates(args: argparse.Namespace, candidates: list[dict[str, Any]], repeats: int) -> dict[str, list[float]]:
    return {
        candidate["route_id"]: deterministic_departures(
            seed=args.seed,
            route_id=candidate["route_id"],
            repeats=repeats,
            depart_min=args.depart_min,
            depart_max=args.depart_max,
        )
        for candidate in candidates
    }


def dry_run_candidate_rows(candidates: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    rows = []
    for candidate in candidates:
        precheck_ok = candidate.get("precheck_status", "PASS") == "PASS"
        rows.append({
            "candidate_rank": candidate["candidate_rank"],
            "route_id": candidate["route_id"],
            "source_route_id": candidate["source_route_id"],
            "target_edge_id": candidate["target_edge_id"],
            "route_edge_count": candidate["route_edge_count"],
            "route_length_m": candidate["route_length_m"],
            "mainroad_length_ratio": candidate["mainroad_length_ratio"],
            "legacy_spine_length_ratio": candidate["legacy_spine_length_ratio"],
            "selection_rank": "",
            "selection_status": f"DRY_RUN_{phase.upper()}" if precheck_ok else "EXCLUDED_PRECHECK",
            "selection_reason": "dry_run_no_sumo_execution" if precheck_ok else candidate.get("precheck_reason", "precheck_failed"),
        })
    return rows


def precheck_excluded_candidate_rows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_rank": candidate["candidate_rank"],
            "route_id": candidate["route_id"],
            "source_route_id": candidate["source_route_id"],
            "target_edge_id": candidate["target_edge_id"],
            "route_edge_count": candidate["route_edge_count"],
            "route_length_m": candidate["route_length_m"],
            "mainroad_length_ratio": candidate["mainroad_length_ratio"],
            "legacy_spine_length_ratio": candidate["legacy_spine_length_ratio"],
            "selection_rank": "",
            "selection_status": "EXCLUDED_PRECHECK",
            "selection_reason": candidate.get("precheck_reason", "precheck_failed"),
        }
        for candidate in candidates
        if candidate.get("precheck_status", "PASS") != "PASS"
    ]


def dry_run_selected_rows(candidate_rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected = [dict(row) for row in candidate_rows[:limit]]
    for index, row in enumerate(selected, start=1):
        row["selection_rank"] = index
        row["selection_status"] = "PRELIMINARY_DRY_RUN"
        row["selection_reason"] = "preliminary_route_priority_only; final_selection_requires_screening_results"
    return selected


def mark_selected_candidate_rows(candidate_rows: list[dict[str, Any]], selected_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected_by_route = {str(row.get("route_id")): row for row in selected_rows}
    updated = []
    for row in candidate_rows:
        out = dict(row)
        selected = selected_by_route.get(str(out.get("route_id")))
        if selected is not None:
            out["selection_rank"] = selected.get("selection_rank", "")
            if selected.get("selection_status") == "CANDIDATE":
                out["selection_status"] = "SELECTED"
            else:
                out["selection_status"] = selected.get("selection_status", out.get("selection_status", ""))
                out["selection_reason"] = selected.get("selection_reason", out.get("selection_reason", ""))
        updated.append(out)
    return updated


def markdown_table(rows: list[dict[str, Any]], fields: list[str]) -> str:
    if not rows:
        return "_No rows._"
    header = "| " + " | ".join(fields) + " |"
    divider = "| " + " | ".join("---" for _ in fields) + " |"
    body = [
        "| " + " | ".join(str(row.get(field, "")) for field in fields) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def write_final_report(
    output_root: Path,
    *,
    phase: str,
    selected_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    params: B4ThetaParams,
    params_provenance: dict[str, Any],
    spc_rows: list[dict[str, Any]] | None = None,
    relative_error_rows_: list[dict[str, Any]] | None = None,
    adaptive_repeat_summary: dict[str, Any] | None = None,
) -> Path:
    adaptive_repeat_summary = adaptive_repeat_summary or {}
    report = [
        "# 10 Final Destination Validation Report",
        "",
        f"- phase: `{phase}`",
        "- purpose: selected theta is locked; no BO/CMA-ES/random search is executed here.",
        f"- theta method filter: `{params_provenance.get('theta_method_filter', '')}`",
        f"- theta source: `{params_provenance.get('theta_all_evaluations_csv', '')}`",
        f"- theta smoke warning: `{params_provenance.get('theta_source_smoke_warning', False)}`",
        f"- theta: `{json.dumps(params.as_result_fields(), ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Selected Destinations",
        "",
        "Selection requires valid arrival, no emergency teleport, comparable B004/B04/B4 rows, positive B4 improvement over B04, and at least one B4 Stage2/Stage3 intervention.",
        "",
        markdown_table(
            selected_rows,
            [
                "selection_rank",
                "route_id",
                "source_route_id",
                "target_edge_id",
                "B4_vs_B04_D_E_improvement_sec",
                "B04_D_E_mean_sec",
                "B4_D_G_mean_sec",
                "intervention_mean",
                "mainroad_length_ratio",
                "legacy_spine_length_ratio",
                "selection_reason",
            ],
        ),
        "",
        "## Candidate Screening",
        "",
        markdown_table(
            candidate_rows,
            [
                "candidate_rank",
                "route_id",
                "target_edge_id",
                "selection_status",
                "selection_reason",
                "B4_vs_B04_D_E_improvement_sec",
                "B4_D_G_mean_sec",
                "intervention_mean",
                "teleport_count",
                "fail_count",
            ],
        ),
        "",
        "## Repeat SPC Stability",
        "",
        markdown_table(
            spc_rows or [],
            ["route_id", "metric", "repeat_count", "spc_status", "stable_round", "latest_value", "ewma", "lcl", "ucl"],
        ),
        "",
        "## 95% CI Relative Error Sufficiency",
        "",
        f"- pilot repeats: `{adaptive_repeat_summary.get('pilot_repeats', '')}`",
        f"- target half-width: `{adaptive_repeat_summary.get('relative_error_target', '')}` × KPI mean",
        f"- confidence level: `{adaptive_repeat_summary.get('confidence_level', '')}`",
        f"- adaptive max repeats: `{adaptive_repeat_summary.get('adaptive_max_repeats', '')}`",
        f"- status: `{adaptive_repeat_summary.get('status', '')}`",
        "",
        markdown_table(
            relative_error_rows_ or [],
            ["route_id", "metric", "repeat_count", "mean", "ci_half_width", "target_half_width", "required_repeats", "status"],
        ),
        "",
    ]
    path = output_root / "final_destination_validation_report.md"
    path.write_text("\n".join(report), encoding="utf-8")
    return path


def write_task_manifest(
    output_root: Path,
    *,
    args: argparse.Namespace,
    phase: str,
    candidates: list[dict[str, Any]],
    departures_by_route: dict[str, list[float]],
    task_manifest_rows: list[dict[str, Any]],
    params: B4ThetaParams,
    params_provenance: dict[str, Any],
) -> None:
    write_csv(output_root / "task_manifest.csv", task_manifest_rows, TASK_FIELDS)
    write_json(
        output_root / "task_manifest.json",
        {
            "schema": "compact_v9_final_destination_validation_task_manifest.v3",
            "generated_at": utc_now(),
            "run_id": args.run_id,
            "phase": phase,
            "inputs": {
                "active_inputs": rel(DEFAULT_ACTIVE_INPUTS),
                "net_file": rel(args.net),
                "background_route": rel(args.background_route),
                "base_stage1_dir": rel(args.base_stage1_dir),
                "mainroad_mapping": rel(args.mainroad_mapping),
                "active_inputs_audit": args.active_inputs_audit,
            },
            "candidate_count": len(candidates),
            "runnable_candidate_count": sum(candidate.get("precheck_status", "PASS") == "PASS" for candidate in candidates),
            "candidate_limit": args.candidate_limit,
            "final_selection_count": args.final_selection_count,
            "repeats": max((len(values) for values in departures_by_route.values()), default=0),
            "task_count": len(task_manifest_rows),
            "depart_min": args.depart_min,
            "depart_max": args.depart_max,
            "stage1_rebuild_policy": STAGE1_REBUILD_POLICY,
            "adaptive_repeats": {
                "enabled_for_final_phase": not args.disable_adaptive_repeats,
                "pilot_repeats": args.pilot_repeats,
                "relative_error_target": args.relative_error_target,
                "confidence_level": args.confidence_level,
                "adaptive_max_repeats": args.adaptive_max_repeats,
            },
            "seed": args.seed,
            "bo_enabled": False,
            "b4_params": params.as_result_fields(),
            "b4_params_provenance": params_provenance,
            "tasks": task_manifest_rows,
        },
    )


def adaptive_final_repeats_enabled(args: argparse.Namespace, phase: str, repeats: int) -> bool:
    return (
        phase == PHASE_FINAL
        and not args.disable_adaptive_repeats
        and repeats >= args.pilot_repeats
    )


def run_validation_phase(
    args: argparse.Namespace,
    *,
    phase: str,
    candidates: list[dict[str, Any]],
    repeats: int,
    params: B4ThetaParams,
    params_provenance: dict[str, Any],
    selected_rows_for_report: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    output_root = args.metrics_root / args.run_id / phase
    run_root = args.run_root / args.run_id / phase
    runnable_candidates = [candidate for candidate in candidates if candidate.get("precheck_status", "PASS") == "PASS"]
    departures_by_route = {
        candidate["route_id"]: deterministic_departures(
            seed=args.seed,
            route_id=candidate["route_id"],
            repeats=repeats,
            depart_min=args.depart_min,
            depart_max=args.depart_max,
        )
        for candidate in runnable_candidates
    }
    task_manifest_rows = planned_task_rows(runnable_candidates, departures_by_route, run_root, phase=phase)
    write_task_manifest(
        output_root,
        args=args,
        phase=phase,
        candidates=candidates,
        departures_by_route=departures_by_route,
        task_manifest_rows=task_manifest_rows,
        params=params,
        params_provenance=params_provenance,
    )
    if args.dry_run:
        for candidate in candidates:
            if candidate.get("precheck_status", "PASS") == "PASS":
                write_firetruck_route_artifacts(candidate, Path(candidate["route_xml"]), Path(candidate["route_csv"]), 600.0)
                Path(candidate["stage1_dir"]).mkdir(parents=True, exist_ok=True)
        candidate_rows = dry_run_candidate_rows(candidates, phase)
        report_selected = selected_rows_for_report or dry_run_selected_rows(candidate_rows, min(args.final_selection_count, len(candidate_rows)))
        write_csv(output_root / "candidate_selection.csv", candidate_rows, CANDIDATE_FIELDS)
        report_path = write_final_report(
            output_root,
            phase=phase,
            selected_rows=report_selected,
            candidate_rows=candidate_rows,
            params=params,
            params_provenance=params_provenance,
            adaptive_repeat_summary={
                "status": "dry_run_no_execution",
                "pilot_repeats": args.pilot_repeats,
                "relative_error_target": args.relative_error_target,
                "confidence_level": args.confidence_level,
                "adaptive_max_repeats": args.adaptive_max_repeats,
            },
        )
        result = {
            "schema": "compact_v9_final_destination_validation_phase_dry_run.v2",
            "run_id": args.run_id,
            "phase": phase,
            "candidate_count": len(candidates),
            "runnable_candidate_count": len(runnable_candidates),
            "rows_total": 0,
            "outputs": {
                "task_manifest_csv": rel(output_root / "task_manifest.csv"),
                "task_manifest_json": rel(output_root / "task_manifest.json"),
                "candidate_selection_csv": rel(output_root / "candidate_selection.csv"),
                "final_destination_validation_report_md": rel(report_path),
            },
        }
        return result

    all_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = precheck_excluded_candidate_rows(candidates)
    rows_by_route: dict[str, list[dict[str, Any]]] = {}

    def write_candidate_partials(completed: dict[int, dict[str, Any]]) -> None:
        ordered = [completed[index] for index in sorted(completed)]
        partial_rows = [row for result in ordered for row in result["route_rows"]]
        partial_candidate_rows = [
            *precheck_excluded_candidate_rows(candidates),
            *[result["candidate_row"] for result in ordered],
        ]
        write_csv(output_root / "all_route_runs.partial.csv", partial_rows, RUN_FIELDS)
        write_csv(output_root / "candidate_selection.partial.csv", partial_candidate_rows, CANDIDATE_FIELDS)

    completed_results: dict[int, dict[str, Any]] = {}
    max_workers = min(args.workers, len(runnable_candidates)) if runnable_candidates else 1
    if max_workers == 1:
        for index, candidate in enumerate(runnable_candidates):
            result = run_candidate_worker((index, args, candidate, departures_by_route[candidate["route_id"]], params, run_root, output_root, 0, True, args.workers))
            completed_results[index] = result
            write_csv(output_root / result["route_id"] / "route_runs.csv", result["route_rows"], RUN_FIELDS)
            write_csv(output_root / result["route_id"] / "mode_averages.csv", result["average_rows"], AVERAGE_FIELDS)
            write_candidate_partials(completed_results)
    else:
        ensure_worker_imports()
        with ProcessPoolExecutor(max_workers=max_workers, initializer=ensure_worker_imports) as executor:
            futures = [
                executor.submit(
                    run_candidate_worker,
                    (index, args, candidate, departures_by_route[candidate["route_id"]], params, run_root, output_root, 0, True, 1),
                )
                for index, candidate in enumerate(runnable_candidates)
            ]
            for future in as_completed(futures):
                result = future.result()
                completed_results[int(result["index"])] = result
                write_csv(output_root / result["route_id"] / "route_runs.csv", result["route_rows"], RUN_FIELDS)
                write_csv(output_root / result["route_id"] / "mode_averages.csv", result["average_rows"], AVERAGE_FIELDS)
                write_candidate_partials(completed_results)

    ordered_results = [completed_results[index] for index in sorted(completed_results)]
    for result in ordered_results:
        rows_by_route[result["route_id"]] = result["route_rows"]
        all_rows.extend(result["route_rows"])
        candidate_rows.append(result["candidate_row"])

    adaptive_summary = {
        "enabled": adaptive_final_repeats_enabled(args, phase, repeats),
        "status": "disabled",
        "pilot_repeats": args.pilot_repeats,
        "relative_error_target": args.relative_error_target,
        "confidence_level": args.confidence_level,
        "adaptive_max_repeats": args.adaptive_max_repeats,
        "initial_repeats": repeats,
        "final_repeats_by_route": {route_id: repeat_count_for_rows(rows) for route_id, rows in rows_by_route.items()},
    }
    relative_rows: list[dict[str, Any]] = []
    if adaptive_summary["enabled"]:
        selected_for_precision = select_final_candidates(candidate_rows, args.final_selection_count)
        candidate_by_route = {str(candidate["route_id"]): candidate for candidate in runnable_candidates}
        selected_precision_ids = [str(row["route_id"]) for row in selected_for_precision if str(row["route_id"]) in candidate_by_route]
        index_by_route = {str(result["route_id"]): int(result["index"]) for result in ordered_results}
        adaptive_summary["status"] = "pilot_sufficient" if selected_precision_ids else "no_runnable_selected_routes"
        while True:
            relative_rows = [
                row
                for route_id in selected_precision_ids
                for row in relative_error_rows(
                    route_id,
                    rows_by_route.get(route_id, []),
                    pilot_repeat_count=args.pilot_repeats,
                    confidence_level=args.confidence_level,
                    relative_error_target=args.relative_error_target,
                    max_repeats=args.adaptive_max_repeats,
                )
            ]
            required_by_route = {
                route_id: min(
                    args.adaptive_max_repeats,
                    required_repeats_from_relative_error([row for row in relative_rows if row.get("route_id") == route_id]),
                )
                for route_id in selected_precision_ids
            }
            additions = {
                route_id: required - repeat_count_for_rows(rows_by_route.get(route_id, []))
                for route_id, required in required_by_route.items()
                if required > repeat_count_for_rows(rows_by_route.get(route_id, []))
            }
            if not additions:
                break
            adaptive_summary["status"] = "additional_repeats_executed"
            extra_results: dict[int, dict[str, Any]] = {}
            extra_payloads = []
            for route_id, extra_count in additions.items():
                current_count = repeat_count_for_rows(rows_by_route.get(route_id, []))
                target_count = current_count + extra_count
                departures_by_route[route_id] = deterministic_departures(
                    seed=args.seed,
                    route_id=route_id,
                    repeats=target_count,
                    depart_min=args.depart_min,
                    depart_max=args.depart_max,
                )
                extra_departures = departures_by_route[route_id][current_count:target_count]
                repeat_worker_count = args.workers if len(additions) == 1 else 1
                extra_payloads.append((
                    index_by_route[route_id],
                    args,
                    candidate_by_route[route_id],
                    extra_departures,
                    params,
                    run_root,
                    output_root,
                    current_count,
                    False,
                    repeat_worker_count,
                ))
            extra_workers = min(args.workers, len(extra_payloads)) if extra_payloads else 1
            if extra_workers == 1:
                for payload in extra_payloads:
                    result = run_candidate_worker(payload)
                    extra_results[int(result["index"])] = result
            else:
                ensure_worker_imports()
                with ProcessPoolExecutor(max_workers=extra_workers, initializer=ensure_worker_imports) as executor:
                    futures = [executor.submit(run_candidate_worker, payload) for payload in extra_payloads]
                    for future in as_completed(futures):
                        result = future.result()
                        extra_results[int(result["index"])] = result
            for result in extra_results.values():
                route_id = str(result["route_id"])
                rows_by_route[route_id].extend(result["route_rows"])
                candidate = candidate_by_route[route_id]
                result["route_rows"] = rows_by_route[route_id]
                result["candidate_row"] = summarize_candidate(candidate, rows_by_route[route_id])
                result["average_rows"] = average_rows(rows_by_route[route_id], route_id)
                completed_results[int(result["index"])] = result
                write_csv(output_root / route_id / "route_runs.csv", rows_by_route[route_id], RUN_FIELDS)
                write_csv(output_root / route_id / "mode_averages.csv", result["average_rows"], AVERAGE_FIELDS)
            adaptive_summary["final_repeats_by_route"] = {route_id: repeat_count_for_rows(rows) for route_id, rows in rows_by_route.items()}
            if all(count >= args.adaptive_max_repeats for count in adaptive_summary["final_repeats_by_route"].values() if count):
                break

        relative_rows = [
            row
            for route_id in selected_precision_ids
            for row in relative_error_rows(
                route_id,
                rows_by_route.get(route_id, []),
                pilot_repeat_count=args.pilot_repeats,
                confidence_level=args.confidence_level,
                relative_error_target=args.relative_error_target,
                max_repeats=args.adaptive_max_repeats,
            )
        ]
        if any(row.get("status") in {"NEEDS_MORE", "CAPPED", "UNBOUNDED_ZERO_MEAN", "INSUFFICIENT_DATA"} for row in relative_rows):
            adaptive_summary["status"] = "insufficient_or_capped"
        elif adaptive_summary["status"] not in {"additional_repeats_executed", "no_runnable_selected_routes"}:
            adaptive_summary["status"] = "pilot_sufficient"
        adaptive_summary["final_repeats_by_route"] = {route_id: repeat_count_for_rows(rows) for route_id, rows in rows_by_route.items()}
    elif phase == PHASE_FINAL:
        adaptive_summary["status"] = "disabled_repeats_below_pilot" if repeats < args.pilot_repeats else "disabled_by_flag"

    all_rows = []
    candidate_rows = precheck_excluded_candidate_rows(candidates)
    ordered_results = [completed_results[index] for index in sorted(completed_results)]
    for result in ordered_results:
        rows_by_route[result["route_id"]] = result["route_rows"]
        all_rows.extend(result["route_rows"])
        candidate_rows.append(result["candidate_row"])
    selected_candidates = select_final_candidates(candidate_rows, args.final_selection_count)
    candidate_rows = mark_selected_candidate_rows(candidate_rows, selected_candidates)
    selected_route_ids = [str(row["route_id"]) for row in selected_candidates]
    selected_rows = [row for route_id in selected_route_ids for row in rows_by_route.get(route_id, [])]
    final_simulation_rows = final_simulation_result_rows(selected_rows, params)
    selected_averages = [
        row
        for route_id in selected_route_ids
        for row in average_rows(rows_by_route.get(route_id, []), route_id)
    ]
    spc_rows = [
        row
        for route_id in selected_route_ids
        for row in repeat_stability_rows(route_id, rows_by_route.get(route_id, []))
    ]
    if phase == PHASE_FINAL and not relative_rows:
        relative_rows = [
            row
            for route_id in selected_route_ids
            for row in relative_error_rows(
                route_id,
                rows_by_route.get(route_id, []),
                pilot_repeat_count=args.pilot_repeats,
                confidence_level=args.confidence_level,
                relative_error_target=args.relative_error_target,
                max_repeats=args.adaptive_max_repeats,
            )
        ]
    task_manifest_rows = planned_task_rows(runnable_candidates, departures_by_route, run_root, phase=phase)
    write_task_manifest(
        output_root,
        args=args,
        phase=phase,
        candidates=candidates,
        departures_by_route=departures_by_route,
        task_manifest_rows=task_manifest_rows,
        params=params,
        params_provenance=params_provenance,
    )
    write_csv(output_root / "all_route_runs.csv", all_rows, RUN_FIELDS)
    write_csv(output_root / "candidate_selection.csv", candidate_rows, CANDIDATE_FIELDS)
    write_csv(output_root / "final_simulation_results.csv", final_simulation_rows, FINAL_SIMULATION_FIELDS)
    write_csv(output_root / "selected_route_runs.csv", selected_rows, RUN_FIELDS)
    write_csv(output_root / "selected_mode_averages.csv", selected_averages, AVERAGE_FIELDS)
    write_csv(output_root / "spc_repeat_stability.csv", spc_rows, SPC_REPEAT_FIELDS)
    write_csv(output_root / "relative_error_sufficiency.csv", relative_rows, RELATIVE_ERROR_FIELDS)
    selected_candidate_payload = [
        next(candidate for candidate in candidates if candidate["route_id"] == route_id)
        for route_id in selected_route_ids
    ]
    selected_payload = {
        "schema": "compact_v9_final_destination_validation_selected_destinations.v2",
        "generated_at": utc_now(),
        "run_id": args.run_id,
        "phase": phase,
        "selection": selected_candidates,
        "stage1_rebuild_policy": STAGE1_REBUILD_POLICY,
        "routes": [
            {
                key: candidate[key]
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
            } | {"route_edges": candidate["route_edges"]}
            for candidate in selected_candidate_payload
        ],
        "departures": {route_id: departures_by_route[route_id] for route_id in selected_route_ids},
        "adaptive_repeat_summary": adaptive_summary,
        "relative_error_sufficiency": relative_rows,
        "b4_params": params.as_result_fields(),
        "b4_params_provenance": params_provenance,
        "outputs": {
            "all_route_runs_csv": rel(output_root / "all_route_runs.csv"),
            "candidate_selection_csv": rel(output_root / "candidate_selection.csv"),
            "final_simulation_results_csv": rel(output_root / "final_simulation_results.csv"),
            "selected_route_runs_csv": rel(output_root / "selected_route_runs.csv"),
            "selected_mode_averages_csv": rel(output_root / "selected_mode_averages.csv"),
            "spc_repeat_stability_csv": rel(output_root / "spc_repeat_stability.csv"),
            "relative_error_sufficiency_csv": rel(output_root / "relative_error_sufficiency.csv"),
        },
    }
    write_json(output_root / "selected_destinations.json", selected_payload)
    report_path = write_final_report(
        output_root,
        phase=phase,
        selected_rows=selected_candidates,
        candidate_rows=candidate_rows,
        params=params,
        params_provenance=params_provenance,
        spc_rows=spc_rows,
        relative_error_rows_=relative_rows,
        adaptive_repeat_summary=adaptive_summary,
    )
    result = {
        "schema": "compact_v9_final_destination_validation_phase_run.v2",
        "generated_at": utc_now(),
        "run_id": args.run_id,
        "phase": phase,
        "candidate_count": len(candidates),
        "runnable_candidate_count": len(runnable_candidates),
        "rows_total": len(all_rows),
        "selected_route_ids": selected_route_ids,
        "outputs": selected_payload["outputs"] | {
            "selected_destinations_json": rel(output_root / "selected_destinations.json"),
            "task_manifest_json": rel(output_root / "task_manifest.json"),
            "final_destination_validation_report_md": rel(report_path),
            "spc_repeat_stability_csv": rel(output_root / "spc_repeat_stability.csv"),
            "relative_error_sufficiency_csv": rel(output_root / "relative_error_sufficiency.csv"),
        },
        "adaptive_repeat_summary": adaptive_summary,
    }
    write_json(output_root / "experiment_summary.json", result)
    return result


def selected_route_ids_from_args_or_screening(args: argparse.Namespace) -> list[str]:
    if args.selected_routes:
        return list(args.selected_routes)
    selection_csv = args.screening_selection_csv or (args.metrics_root / args.run_id / PHASE_SCREENING / "candidate_selection.csv")
    selection_csv = Path(selection_csv).resolve()
    if not selection_csv.is_file():
        raise FinalDestinationValidationError(f"missing_screening_selection_csv:{rel(selection_csv)}")
    selected = select_final_candidates(read_csv(selection_csv), args.final_selection_count)
    return [str(row["route_id"]) for row in selected]


def run_robust_theta_selection(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    if args.theta_all_evaluations is None:
        raise FinalDestinationValidationError("robust_theta_selection_requires_theta_all_evaluations")
    output_root = args.metrics_root / args.run_id / "robust_selection"
    run_root = args.run_root / args.run_id / "robust_selection"
    output_root.mkdir(parents=True, exist_ok=True)
    base_run_root = args.run_root / args.run_id
    base_candidates = build_candidate_routes(args, base_run_root / "inputs" / "candidate_catalog")
    selected_route_ids = selected_route_ids_from_args_or_screening(args)
    if not selected_route_ids:
        raise FinalDestinationValidationError("robust_theta_selection_requires_selected_route")
    route_id = selected_route_ids[0]
    candidates = clone_candidates_for_phase(base_candidates, [route_id], base_run_root / "inputs" / "robust_selection")
    if not candidates:
        raise FinalDestinationValidationError(f"robust_route_not_found:{route_id}")
    candidate = candidates[0]
    theta_rows = read_csv(Path(args.theta_all_evaluations))
    theta_candidates = select_robust_theta_candidates(
        theta_rows,
        method_filter=args.theta_method,
        limit=args.robust_candidate_count,
        diversity_min_distance=args.robust_diversity_min_distance,
    )
    if not theta_candidates:
        raise FinalDestinationValidationError("no_robust_theta_candidates_after_filter")
    write_csv(output_root / "robust_theta_candidates.csv", theta_candidates, ROBUST_THETA_CANDIDATE_FIELDS)
    departures = deterministic_departures(
        seed=args.seed,
        route_id=route_id,
        repeats=args.robust_mini_batch_repeats,
        depart_min=args.depart_min,
        depart_max=args.depart_max,
    )
    plan_payload = {
        "schema": "compact_v9_final_destination_robust_theta_plan.v1",
        "generated_at": utc_now(),
        "run_id": args.run_id,
        "route_id": route_id,
        "theta_all_evaluations": rel(Path(args.theta_all_evaluations)),
        "theta_method": args.theta_method,
        "candidate_count": len(theta_candidates),
        "mini_batch_repeats": args.robust_mini_batch_repeats,
        "mini_batch_departures": departures,
        "survivor_count": args.robust_survivor_count,
        "final_top_k": args.robust_final_top_k,
        "workers": args.workers,
        "theta_workers": args.robust_theta_workers,
        "repeat_workers": args.robust_repeat_workers,
        "stage1_rebuild_policy": STAGE1_REBUILD_POLICY,
    }
    write_json(output_root / "robust_theta_plan.json", plan_payload)
    if args.dry_run:
        write_csv(output_root / "mini_batch_theta_summary.csv", [], ROBUST_THETA_SUMMARY_FIELDS)
        result = {
            "schema": "compact_v9_final_destination_robust_theta_selection.v1",
            "generated_at": utc_now(),
            "run_id": args.run_id,
            "status": "dry_run_no_execution",
            "route_id": route_id,
            "outputs": {
                "robust_theta_candidates_csv": rel(output_root / "robust_theta_candidates.csv"),
                "robust_theta_plan_json": rel(output_root / "robust_theta_plan.json"),
            },
        }
        write_json(output_root / "robust_selection_summary.json", result)
        return result

    theta_worker_count = min(max(1, args.robust_theta_workers), len(theta_candidates), max(1, args.workers))
    if args.robust_repeat_workers > 0:
        repeat_workers = max(1, args.robust_repeat_workers)
    elif theta_worker_count > 1:
        repeat_workers = 1
    else:
        repeat_workers = max(1, args.workers)
    mini_payloads = []
    for index, theta_row in enumerate(theta_candidates):
        theta_id = f"theta_{int(theta_row['theta_rank']):03d}_{safe_id(theta_row.get('parameter_id'))}"
        mini_payloads.append((
            index,
            args,
            candidate,
            theta_row,
            departures,
            run_root / "mini_batch" / theta_id,
            output_root / "mini_batch" / theta_id,
            repeat_workers,
        ))
    completed: dict[int, dict[str, Any]] = {}

    def write_mini_partials() -> None:
        ordered = [completed[index] for index in sorted(completed)]
        summary_rows = [item["summary"] for item in ordered]
        write_csv(output_root / "mini_batch_theta_summary.partial.csv", summary_rows, ROBUST_THETA_SUMMARY_FIELDS)

    if theta_worker_count == 1:
        for payload in mini_payloads:
            result = run_robust_mini_batch_worker(payload)
            completed[int(result["index"])] = result
            write_mini_partials()
    else:
        ensure_worker_imports()
        with ProcessPoolExecutor(max_workers=theta_worker_count, initializer=ensure_worker_imports) as executor:
            futures = [executor.submit(run_robust_mini_batch_worker, payload) for payload in mini_payloads]
            for future in as_completed(futures):
                result = future.result()
                completed[int(result["index"])] = result
                write_mini_partials()

    mini_results = [completed[index] for index in sorted(completed)]
    mini_summary_rows = [result["summary"] for result in mini_results]
    write_csv(output_root / "mini_batch_theta_summary.csv", mini_summary_rows, ROBUST_THETA_SUMMARY_FIELDS)
    survivor_rows = [row for row in sorted(mini_summary_rows, key=robust_summary_sort_key) if row.get("survivor_status") == "SURVIVOR"]
    selected_for_final = survivor_rows[:min(args.robust_survivor_count, args.robust_final_top_k)]
    fallback_used = False
    if not selected_for_final:
        fallback_used = True
        selected_for_final = sorted(mini_summary_rows, key=robust_summary_sort_key)[:args.robust_final_top_k]
    theta_by_rank = {int(safe_float(row.get("theta_rank"))): row for row in theta_candidates}
    selected_theta_rows = [theta_by_rank[int(safe_float(row.get("theta_rank")))] for row in selected_for_final]
    write_csv(output_root / "survivor_ranking.csv", sorted(mini_summary_rows, key=robust_summary_sort_key), ROBUST_THETA_SUMMARY_FIELDS)

    final_ranking_rows: list[dict[str, Any]] = []
    final_results: list[dict[str, Any]] = []
    for final_index, theta_row in enumerate(selected_theta_rows, start=1):
        params = robust_theta_params(theta_row)
        child_run_id = f"{args.run_id}/robust_final/top_{final_index:02d}_{safe_id(params.parameter_id)}"
        child_args = clone_args(args, run_id=child_run_id, final_selection_count=1)
        child_base_root = child_args.run_root / child_args.run_id
        child_candidates = clone_candidates_for_phase(
            base_candidates,
            [route_id],
            child_base_root / "inputs" / PHASE_FINAL,
        )
        params_provenance = {
            "schema": "compact_v9_final_destination_robust_theta_lock.v1",
            "theta_all_evaluations_csv": rel(Path(args.theta_all_evaluations)),
            "theta_selection_policy": "robust_mini_batch_survivor_top5",
            "theta_method_filter": args.theta_method,
            "selected_method": theta_row.get("method", ""),
            "selected_seed": theta_row.get("seed", ""),
            "selected_round": theta_row.get("round", ""),
            "selected_score": theta_row.get("source_score", ""),
            "decision_variables_fixed": {field: params.as_result_fields()[field] for field in THETA_FIELDS},
            "bo_enabled": False,
            "bayesian_optimization_executed_by_final_validation": False,
            "robust_parent_run_id": args.run_id,
            "robust_theta_rank": theta_row.get("theta_rank", ""),
            "stage1_rebuild_policy": STAGE1_REBUILD_POLICY,
        }
        final_result = run_validation_phase(
            child_args,
            phase=PHASE_FINAL,
            candidates=child_candidates,
            repeats=args.repeats,
            params=params,
            params_provenance=params_provenance,
        )
        final_results.append(final_result)
        child_output_root = child_args.metrics_root / child_args.run_id / PHASE_FINAL
        final_ranking_rows.append(final_ranking_row(final_index, theta_row, route_id, child_run_id, child_output_root))
        write_csv(output_root / "final_theta_ranking.partial.csv", final_ranking_rows, ROBUST_FINAL_RANKING_FIELDS)

    final_ranking_rows = sorted(
        final_ranking_rows,
        key=lambda row: (
            0 if row.get("selection_status") == "FINAL_SELECTED" else 1,
            int(safe_float(row.get("stuck_count"))),
            int(safe_float(row.get("fail_count"))),
            -safe_float(row.get("arrival_rate")),
            0 if row.get("relative_error_pass") == "True" else 1,
            safe_float(row.get("mean_score"), float("inf")),
            -safe_float(row.get("mean_B4_vs_B04_D_E_improvement_sec")),
            safe_float(row.get("mean_D_G_sec"), float("inf")),
        ),
    )
    for index, row in enumerate(final_ranking_rows, start=1):
        row["final_rank"] = index
    write_csv(output_root / "final_theta_ranking.csv", final_ranking_rows, ROBUST_FINAL_RANKING_FIELDS)
    selected_final = final_ranking_rows[0] if final_ranking_rows else {}
    result = {
        "schema": "compact_v9_final_destination_robust_theta_selection.v1",
        "generated_at": utc_now(),
        "run_id": args.run_id,
        "status": "complete",
        "route_id": route_id,
        "fallback_used_due_to_no_survivor": fallback_used,
        "mini_batch": {
            "theta_candidate_count": len(theta_candidates),
            "repeat_count": args.robust_mini_batch_repeats,
            "departures": departures,
            "survivor_count": len(survivor_rows),
        },
        "final_validation": {
            "top_k": len(final_ranking_rows),
            "repeats": args.repeats,
            "adaptive_max_repeats": args.adaptive_max_repeats,
            "results": final_results,
        },
        "selected_final_theta": selected_final,
        "outputs": {
            "robust_theta_candidates_csv": rel(output_root / "robust_theta_candidates.csv"),
            "robust_theta_plan_json": rel(output_root / "robust_theta_plan.json"),
            "mini_batch_theta_summary_csv": rel(output_root / "mini_batch_theta_summary.csv"),
            "survivor_ranking_csv": rel(output_root / "survivor_ranking.csv"),
            "final_theta_ranking_csv": rel(output_root / "final_theta_ranking.csv"),
            "robust_selection_summary_json": rel(output_root / "robust_selection_summary.json"),
        },
    }
    write_json(output_root / "robust_selection_summary.json", result)
    write_json(args.metrics_root / "latest.json", result)
    return result


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    if args.validation_mode == VALIDATION_MODE_ROBUST_THETA_SELECTION:
        return run_robust_theta_selection(args)
    validate_args(args)
    params, params_provenance = load_final_b4_params(
        theta_latest=args.theta_latest,
        theta_all_evaluations=args.theta_all_evaluations,
        theta_method=args.theta_method,
    )
    base_run_root = args.run_root / args.run_id
    base_candidates = build_candidate_routes(args, base_run_root / "inputs" / "candidate_catalog")
    results: dict[str, Any] = {
        "schema": "compact_v9_final_destination_validation_run.v2",
        "generated_at": utc_now(),
        "run_id": args.run_id,
        "phase": args.phase,
        "inputs": {
            "active_inputs": rel(DEFAULT_ACTIVE_INPUTS),
            "net_file": rel(args.net),
            "background_route": rel(args.background_route),
            "base_stage1_dir": rel(args.base_stage1_dir),
            "mainroad_mapping": rel(args.mainroad_mapping),
            "active_inputs_audit": args.active_inputs_audit,
        },
        "bo_enabled": False,
        "bayesian_optimization_executed_by_final_validation": False,
        "b4_params": params.as_result_fields(),
        "b4_params_provenance": params_provenance,
        "phases": {},
    }
    if args.phase in {PHASE_SCREENING, PHASE_ALL}:
        screening_candidates = clone_candidates_for_phase(
            base_candidates,
            [str(candidate["route_id"]) for candidate in base_candidates],
            base_run_root / "inputs" / PHASE_SCREENING,
        )
        screening_result = run_validation_phase(
            args,
            phase=PHASE_SCREENING,
            candidates=screening_candidates,
            repeats=args.screening_repeats,
            params=params,
            params_provenance=params_provenance,
        )
        results["phases"][PHASE_SCREENING] = screening_result
    if args.phase in {PHASE_FINAL, PHASE_ALL}:
        if args.phase == PHASE_ALL:
            screening_csv = args.metrics_root / args.run_id / PHASE_SCREENING / "candidate_selection.csv"
            if args.dry_run:
                selected_rows = dry_run_selected_rows(read_csv(screening_csv), args.final_selection_count)
                selected_route_ids = [str(row["route_id"]) for row in selected_rows]
            else:
                selected_rows = select_final_candidates(read_csv(screening_csv), args.final_selection_count)
                selected_route_ids = [str(row["route_id"]) for row in selected_rows]
        else:
            selected_route_ids = selected_route_ids_from_args_or_screening(args)
            selected_rows = []
        final_candidates = clone_candidates_for_phase(
            base_candidates,
            selected_route_ids,
            base_run_root / "inputs" / PHASE_FINAL,
        )
        final_result = run_validation_phase(
            args,
            phase=PHASE_FINAL,
            candidates=final_candidates,
            repeats=args.repeats,
            params=params,
            params_provenance=params_provenance,
            selected_rows_for_report=selected_rows or None,
        )
        results["phases"][PHASE_FINAL] = final_result
    write_json(args.metrics_root / "latest.json", results)
    write_json(args.metrics_root / args.run_id / "experiment_summary.json", results)
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Compact V9 final destination validation.")
    parser.add_argument("--validation-mode", choices=[VALIDATION_MODE_STANDARD, VALIDATION_MODE_ROBUST_THETA_SELECTION], default=VALIDATION_MODE_STANDARD)
    parser.add_argument("--phase", choices=[PHASE_SCREENING, PHASE_FINAL, PHASE_ALL], default=PHASE_ALL)
    parser.add_argument("--routes-csv", type=Path, default=DEFAULT_ROUTES_CSV)
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--base-stage1-dir", type=Path, default=DEFAULT_BASE_STAGE1_DIR)
    parser.add_argument("--theta-latest", type=Path, default=DEFAULT_THETA_LATEST)
    parser.add_argument("--theta-all-evaluations", type=Path, default=None)
    parser.add_argument("--theta-method", default="ALL", choices=["ALL", "BO", "CMA-ES", "Random Search"])
    parser.add_argument("--mainroad-mapping", type=Path, default=DEFAULT_MAINROAD_MAPPING)
    parser.add_argument("--start-edge", default=DEFAULT_START_EDGE)
    parser.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    parser.add_argument("--screening-repeats", type=int, default=DEFAULT_SCREENING_REPEATS)
    parser.add_argument("--final-selection-count", type=int, default=DEFAULT_FINAL_SELECTION_COUNT)
    parser.add_argument("--selected-routes", nargs="*", default=None)
    parser.add_argument("--screening-selection-csv", type=Path, default=None)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--pilot-repeats", type=int, default=DEFAULT_PILOT_REPEATS)
    parser.add_argument("--relative-error-target", type=float, default=DEFAULT_RELATIVE_ERROR_TARGET)
    parser.add_argument("--confidence-level", type=float, default=DEFAULT_CONFIDENCE_LEVEL)
    parser.add_argument("--adaptive-max-repeats", type=int, default=DEFAULT_ADAPTIVE_MAX_REPEATS)
    parser.add_argument("--disable-adaptive-repeats", action="store_true")
    parser.add_argument("--depart-min", type=float, default=DEFAULT_DEPART_MIN)
    parser.add_argument("--depart-max", type=float, default=DEFAULT_DEPART_MAX)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--robust-candidate-count", type=int, default=24)
    parser.add_argument("--robust-mini-batch-repeats", type=int, default=6)
    parser.add_argument("--robust-survivor-count", type=int, default=5)
    parser.add_argument("--robust-final-top-k", type=int, default=5)
    parser.add_argument("--robust-diversity-min-distance", type=float, default=0.08)
    parser.add_argument("--robust-theta-workers", type=int, default=2)
    parser.add_argument("--robust-repeat-workers", type=int, default=0, help="Repeat workers per theta in robust mini-batch. 0 auto-splits --workers across theta workers.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--metrics-root", type=Path, default=DEFAULT_METRICS_ROOT)
    parser.add_argument("--hard-max-sim-time", type=float, default=DEFAULT_HARD_MAX_SIM_TIME)
    parser.add_argument("--sumo-binary", default=None)
    parser.add_argument("--emit-fcd", action="store_true")
    parser.add_argument("--emit-tls-states", action="store_true")
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
