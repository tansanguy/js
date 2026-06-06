#!/usr/bin/env python3
"""OFAT sensitivity for B4 fixed structure parameters.

The screened decision variables stay fixed.  This script varies structure
parameters that came from B0/runtime calibration so we can verify that they are
actually behaviorally active before treating them as constants.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from b4_runtime import B04_MODE, B4_MODE, B4ThetaParams, safe_float, write_csv  # noqa: E402
from run_b0_b4_signal_pipeline import B4RunTask, run_b4_task  # noqa: E402
from run_b4_theta_bo import (  # noqa: E402
    ALL_VALUE_FIELDS,
    DEFAULT_TAU_NUMERATOR_GAMMA,
    FAILURE_PENALTY_SEC,
    prepare_real_context,
    score_for_row,
    write_json,
)


DEFAULT_OUTPUT_PREFIX = "compact_v9_B4_fixed_param_sensitivity"
DEFAULT_METRICS_ROOT = PROJECT_ROOT / "results/metrics" / DEFAULT_OUTPUT_PREFIX
DEFAULT_RUN_ROOT = PROJECT_ROOT / "runs" / DEFAULT_OUTPUT_PREFIX
DEFAULT_NET = PIPELINE_DIR / "tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml"
DEFAULT_ROUTE = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130_toegye15.rou.xml"
DEFAULT_STAGE1_DIR = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1_u130_toegye15"
DEFAULT_OUTPUT_DIR = PIPELINE_DIR / "tdata_signal/u130_toegye15_fixed_param_sensitivity"

BASE_DECISION = {
    "alpha": 1.15,
    "t_lead": 21.0,
    "delta_T_thr": 80.0,
    "G_ext": 32.0,
    "Q_trig": 0.0,
}
BASE_STRUCTURE = {
    "tau": 0.75,
    "hold_max": 14.0,
    "d_up": 1,
    "tau_scale": 0.85,
    "tau_numerator_gamma": 5.0,
}
OFAT_VALUES = {
    "tau": [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90],
    "tau_scale": [0.70, 0.80, 0.85, 0.90, 1.00],
    "tau_numerator_gamma": [1.0, 3.0, 5.0, 7.0],
    "hold_max": [7.0, 14.0, 24.0, 33.0],
    "d_up": [1, 2, 3],
}
STRUCTURE_FIELDS = list(BASE_STRUCTURE.keys())
DECISION_FIELDS = list(BASE_DECISION.keys())
SIGNAL_BURDEN_MAX_WORSENING = 1.20
MIN_SCORE_IMPROVEMENT_RATIO = 0.01
MIN_EV_IMPROVEMENT_SEC = 30.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_rows(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def value_label(value: float | int) -> str:
    if isinstance(value, float) and not value.is_integer():
        return str(value).replace(".", "p")
    return str(int(value))


def build_candidates(only_variable: str = "") -> list[dict[str, Any]]:
    baseline = {
        "parameter_id": "fixed_base_tau075_scale085_gamma5_hold14_dup1",
        "changed_variable": "baseline",
        "changed_value": "",
        **BASE_DECISION,
        **BASE_STRUCTURE,
    }
    candidates = [baseline]
    for variable, values in OFAT_VALUES.items():
        if only_variable and variable != only_variable:
            continue
        base_value = BASE_STRUCTURE[variable]
        for value in values:
            if float(value) == float(base_value):
                continue
            row = dict(baseline)
            row[variable] = value
            row["changed_variable"] = variable
            row["changed_value"] = value
            row["parameter_id"] = f"fixed_{variable}_{value_label(float(value))}"
            candidates.append(row)
    return candidates


def combined_parameter_id(structure: dict[str, Any]) -> str:
    return (
        "fixed_combined_lock"
        f"_tau{value_label(float(structure['tau']))}"
        f"_scale{value_label(float(structure['tau_scale']))}"
        f"_gamma{value_label(float(structure['tau_numerator_gamma']))}"
        f"_hold{value_label(float(structure['hold_max']))}"
        f"_dup{value_label(float(structure['d_up']))}"
    )


def build_combined_candidate(structure: dict[str, Any]) -> dict[str, Any]:
    return {
        "parameter_id": combined_parameter_id(structure),
        "changed_variable": "combined_lock",
        "changed_value": "combined_lock",
        **BASE_DECISION,
        **structure,
    }


def normalized_structure_value(variable: str, value: Any) -> float | int:
    if variable == "d_up":
        return int(round(safe_float(value, BASE_STRUCTURE[variable])))
    return float(safe_float(value, BASE_STRUCTURE[variable]))


def bool_cell(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def row_status(row: dict[str, Any]) -> str:
    if row.get("final_status") not in {"PASS", "WARNING"}:
        return "FAIL"
    if not bool_cell(row.get("emergency_arrived")) or bool_cell(row.get("emergency_teleport")):
        return "FAIL"
    if safe_float(row.get("failure_penalty_sec")) >= FAILURE_PENALTY_SEC:
        return "FAIL"
    return "PASS"


def candidate_rollups(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("parameter_id", ""))].append(row)
    rollups: dict[str, dict[str, Any]] = {}
    for parameter_id, group in groups.items():
        first = group[0]
        statuses = [row_status(row) for row in group]
        numeric_fields = [
            "T_actual_EMV_sec",
            "general_mean_travel_time_sec",
            "bo_score_sec",
            "signal_burden_sec",
            "stage3_preemption_count",
            "bottleneck_mode_count",
            "case_b_active_count",
            "case_b_original_tau_count",
            "original_tau_trigger_count",
            "case_b_runtime_segment_count",
            "case_b_runtime_movement_count",
            "original_tau_hit_0p75",
        ]
        out = dict(first)
        out["parameter_id"] = parameter_id
        out["repeat_count"] = len(group)
        out["rollup_status"] = "PASS" if all(status == "PASS" for status in statuses) else "FAIL"
        for field in numeric_fields:
            values = [safe_float(row.get(field)) for row in group if row.get(field) not in {"", None}]
            if values:
                out[field] = round(sum(values) / len(values), 6)
        rollups[parameter_id] = out
    return rollups


def signal_burden_allowed(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    baseline_signal = safe_float(baseline.get("signal_burden_sec"), 0.0)
    candidate_signal = safe_float(candidate.get("signal_burden_sec"), 0.0)
    if baseline_signal <= 0.0 or candidate_signal <= 0.0:
        return True
    return candidate_signal <= baseline_signal * SIGNAL_BURDEN_MAX_WORSENING


def best_candidate(rows: list[dict[str, Any]], baseline: dict[str, Any]) -> dict[str, Any] | None:
    eligible = [
        row for row in rows
        if row.get("rollup_status") == "PASS" and signal_burden_allowed(row, baseline)
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda row: (
            safe_float(row.get("bo_score_sec"), FAILURE_PENALTY_SEC * 10),
            safe_float(row.get("T_actual_EMV_sec"), FAILURE_PENALTY_SEC * 10),
            safe_float(row.get("signal_burden_sec"), FAILURE_PENALTY_SEC * 10),
            str(row.get("parameter_id", "")),
        ),
    )


def best_passing_candidate_unfiltered(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [row for row in rows if row.get("rollup_status") == "PASS"]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda row: (
            safe_float(row.get("bo_score_sec"), FAILURE_PENALTY_SEC * 10),
            safe_float(row.get("T_actual_EMV_sec"), FAILURE_PENALTY_SEC * 10),
            safe_float(row.get("signal_burden_sec"), FAILURE_PENALTY_SEC * 10),
            str(row.get("parameter_id", "")),
        ),
    )


def activation_count(row: dict[str, Any]) -> float:
    return max(
        safe_float(row.get("case_b_original_tau_count")),
        safe_float(row.get("original_tau_trigger_count")),
        safe_float(row.get("case_b_runtime_segment_count")),
        safe_float(row.get("case_b_runtime_movement_count")),
    )


def diagnostic_candidate(variable: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    active = [row for row in rows if activation_count(row) > 0.0]
    if not active:
        return None
    counts = {activation_count(row) for row in rows}
    if len(counts) <= 1 and variable in {"tau_scale", "tau_numerator_gamma", "d_up"}:
        return None
    if variable == "tau":
        return max(
            active,
            key=lambda row: (
                safe_float(row.get("tau")),
                activation_count(row),
                -safe_float(row.get("signal_burden_sec"), FAILURE_PENALTY_SEC),
                str(row.get("parameter_id", "")),
            ),
        )
    if variable == "tau_scale":
        return min(
            active,
            key=lambda row: (
                safe_float(row.get("tau_scale"), 1.0),
                safe_float(row.get("signal_burden_sec"), FAILURE_PENALTY_SEC),
                str(row.get("parameter_id", "")),
            ),
        )
    if variable == "tau_numerator_gamma":
        return max(
            active,
            key=lambda row: (
                safe_float(row.get("tau_numerator_gamma")),
                activation_count(row),
                -safe_float(row.get("signal_burden_sec"), FAILURE_PENALTY_SEC),
                str(row.get("parameter_id", "")),
            ),
        )
    if variable == "d_up":
        return max(
            active,
            key=lambda row: (
                activation_count(row),
                safe_float(row.get("d_up")),
                -safe_float(row.get("signal_burden_sec"), FAILURE_PENALTY_SEC),
                str(row.get("parameter_id", "")),
            ),
        )
    return max(
        active,
        key=lambda row: (
            activation_count(row),
            safe_float(row.get("stage3_preemption_count")),
            -safe_float(row.get("signal_burden_sec"), FAILURE_PENALTY_SEC),
            str(row.get("parameter_id", "")),
        ),
    )


def variable_sensitivity_label(rows: list[dict[str, Any]]) -> str:
    passed = [row for row in rows if row.get("rollup_status") == "PASS"]
    failed = [row for row in rows if row.get("rollup_status") != "PASS"]
    ev_values = [safe_float(row.get("T_actual_EMV_sec")) for row in passed if row.get("T_actual_EMV_sec") not in {"", None}]
    if failed or (len(ev_values) >= 2 and max(ev_values) - min(ev_values) >= 120.0):
        return "high"
    if len(ev_values) >= 2 and max(ev_values) - min(ev_values) >= 45.0:
        return "medium"
    return "low"


def structure_lock_summary(rows: list[dict[str, Any]], combined_row: dict[str, Any] | None = None) -> dict[str, Any]:
    rollups = candidate_rollups(rows)
    baseline = next(row for row in rollups.values() if row.get("changed_variable") == "baseline")
    baseline_pass = baseline.get("rollup_status") == "PASS"
    baseline_score = safe_float(baseline.get("bo_score_sec"), FAILURE_PENALTY_SEC * 10)
    baseline_ev = safe_float(baseline.get("T_actual_EMV_sec"), FAILURE_PENALTY_SEC * 10)
    candidate_structure = dict(BASE_STRUCTURE)
    variables: dict[str, dict[str, Any]] = {}

    for variable in OFAT_VALUES:
        subset = [row for row in rollups.values() if row.get("changed_variable") == variable]
        pass_count = sum(row.get("rollup_status") == "PASS" for row in subset)
        fail_count = len(subset) - pass_count
        selected = best_candidate(subset, baseline)
        selected_value = BASE_STRUCTURE[variable]
        status = "blocked_no_pass"
        reason = "no_passing_candidate_after_signal_burden_gate"
        best_row_id = ""
        if selected is not None:
            selected_score = safe_float(selected.get("bo_score_sec"), FAILURE_PENALTY_SEC * 10)
            selected_ev = safe_float(selected.get("T_actual_EMV_sec"), FAILURE_PENALTY_SEC * 10)
            score_improvement = baseline_score - selected_score
            ev_improvement = baseline_ev - selected_ev
            selected_value = normalized_structure_value(variable, selected.get(variable, selected.get("changed_value", BASE_STRUCTURE[variable])))
            best_row_id = str(selected.get("parameter_id", ""))
            if baseline_pass:
                if (
                    score_improvement >= baseline_score * MIN_SCORE_IMPROVEMENT_RATIO
                    or ev_improvement >= MIN_EV_IMPROVEMENT_SEC
                ):
                    status = "locked"
                    reason = "passing_candidate_improves_baseline"
                    candidate_structure[variable] = selected_value
                else:
                    status = "retained_baseline"
                    reason = "improvement_below_lock_threshold"
                    selected_value = BASE_STRUCTURE[variable]
            else:
                status = "provisional"
                reason = "baseline_failed_best_passing_candidate"
                candidate_structure[variable] = selected_value
        elif not baseline_pass:
            tradeoff = best_passing_candidate_unfiltered(subset)
            if tradeoff is not None:
                selected_value = normalized_structure_value(variable, tradeoff.get(variable, tradeoff.get("changed_value", BASE_STRUCTURE[variable])))
                best_row_id = str(tradeoff.get("parameter_id", ""))
                status = "provisional_pass_signal_tradeoff"
                reason = "baseline_failed_passing_candidate_exceeds_signal_burden_gate"
                candidate_structure[variable] = selected_value
            else:
                diagnostic = diagnostic_candidate(variable, subset)
                if diagnostic is not None:
                    selected_value = normalized_structure_value(variable, diagnostic.get(variable, diagnostic.get("changed_value", BASE_STRUCTURE[variable])))
                    best_row_id = str(diagnostic.get("parameter_id", ""))
                    status = "provisional_diagnostic"
                    reason = "baseline_failed_selected_by_runtime_original_tau_activation"
                    candidate_structure[variable] = selected_value
        variables[variable] = {
            "variable": variable,
            "status": status,
            "reason": reason,
            "baseline_value": BASE_STRUCTURE[variable],
            "selected_value": selected_value,
            "best_row_id": best_row_id,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "candidate_count": len(subset),
            "sensitivity_label": variable_sensitivity_label(subset),
        }

    changed = {
        key: value for key, value in candidate_structure.items()
        if float(value) != float(BASE_STRUCTURE[key])
    }
    combined_confirmation = {
        "status": "not_run",
        "reason": "no_structure_change_selected",
        "parameter_id": "",
        "final_status": "",
        "T_actual_EMV_sec": "",
        "bo_score_sec": "",
    }
    selected_structure = dict(BASE_STRUCTURE)
    lock_status = "NO_CHANGE"
    if changed:
        lock_status = "PENDING_COMBINED_CONFIRMATION"
        combined_confirmation["reason"] = "combined_candidate_not_evaluated"
    if combined_row is not None:
        combined_status = row_status(combined_row)
        combined_confirmation = {
            "status": combined_status,
            "reason": "combined_lock_passed" if combined_status == "PASS" else "combined_lock_failed",
            "parameter_id": combined_row.get("parameter_id", ""),
            "final_status": combined_row.get("final_status", ""),
            "failure_reason": combined_row.get("failure_reason", ""),
            "T_actual_EMV_sec": combined_row.get("T_actual_EMV_sec", ""),
            "bo_score_sec": combined_row.get("bo_score_sec", ""),
            "signal_burden_sec": combined_row.get("signal_burden_sec", ""),
        }
        if changed and combined_status == "PASS":
            selected_structure = dict(candidate_structure)
            lock_status = "LOCKED"
        elif changed and not baseline_pass:
            selected_structure = dict(candidate_structure)
            lock_status = "DIAGNOSTIC_LOCKED_BASELINE_FAIL"
        elif changed:
            lock_status = "PARTIAL_CANDIDATES_ONLY"

    return {
        "baseline_structure": dict(BASE_STRUCTURE),
        "candidate_structure": candidate_structure,
        "selected_structure": selected_structure,
        "lock_status": lock_status,
        "variables": variables,
        "combined_confirmation": combined_confirmation,
    }


def signal_event_summary(signal_events: Path) -> dict[str, Any]:
    rows = read_csv(signal_events)
    case_b_counts = Counter(row.get("case_b_source", "") for row in rows if row.get("case_b_source"))
    action_counts = Counter(row.get("action_type", "") for row in rows if row.get("action_type"))
    active_case_b_count = 0
    original_tau_case_b_count = 0
    original_tau_trigger_count = 0
    for row in rows:
        source = row.get("case_b_source", "")
        reason = row.get("trigger_reason", "")
        if source and source != "not_case_b":
            active_case_b_count += 1
        if source.startswith("original_tau_") or source == "original_tau_segment":
            original_tau_case_b_count += 1
        if "original_tau_segment" in reason:
            original_tau_trigger_count += 1
    tau_rows = [
        row for row in rows
        if row.get("original_tau_segment_id") or safe_float(row.get("original_tau_raw_fill"), 0.0) > 0.0
    ]
    raw_values = [safe_float(row.get("original_tau_raw_fill")) for row in tau_rows]
    effective_values = [safe_float(row.get("original_tau_effective_fill"), safe_float(row.get("original_tau_fill"))) for row in tau_rows]
    segment_hits: dict[str, Counter[str]] = defaultdict(Counter)
    for row in tau_rows:
        segment = row.get("original_tau_segment_id") or "unknown"
        effective = safe_float(row.get("original_tau_effective_fill"), safe_float(row.get("original_tau_fill")))
        for threshold in (0.65, 0.75, 0.85):
            if effective >= threshold:
                segment_hits[segment][f"hit_{threshold:.2f}"] += 1
        segment_hits[segment]["samples"] += 1
    summary: dict[str, Any] = {
        "signal_event_count": len(rows),
        "phase_change_count": action_counts.get("phase_change_target_green", 0),
        "trigger_eval_count": action_counts.get("trigger_evaluation", 0),
        "case_b_active_count": active_case_b_count,
        "case_b_not_case_b_count": case_b_counts.get("not_case_b", 0),
        "case_b_runtime_segment_count": case_b_counts.get("runtime_tau_segment", 0),
        "case_b_runtime_movement_count": case_b_counts.get("runtime_tau_movement", 0),
        "case_b_original_tau_count": original_tau_case_b_count,
        "case_b_b0_prior_count": case_b_counts.get("b0_prior", 0),
        "original_tau_trigger_count": original_tau_trigger_count,
        "original_tau_sample_count": len(tau_rows),
        "original_tau_raw_max": round(max(raw_values), 6) if raw_values else 0.0,
        "original_tau_effective_max": round(max(effective_values), 6) if effective_values else 0.0,
        "original_tau_hit_0p65": sum(value >= 0.65 for value in effective_values),
        "original_tau_hit_0p75": sum(value >= 0.75 for value in effective_values),
        "original_tau_hit_0p85": sum(value >= 0.85 for value in effective_values),
    }
    summary["segment_hits"] = {
        segment: dict(counter)
        for segment, counter in sorted(segment_hits.items())
    }
    return summary


def evaluate_candidate(run_id: str, candidate: dict[str, Any], repeat_id: int, args: argparse.Namespace, context: dict[str, Any]) -> dict[str, Any]:
    task = B4RunTask(
        run_id,
        B4_MODE,
        candidate["parameter_id"],
        repeat_id,
        args.seed + repeat_id - 1,
        args.run_root / run_id / B4_MODE / candidate["parameter_id"] / f"repeat_{repeat_id:03d}",
        net_file=args.net_file,
        background_route=args.background_route,
    )
    params = B4ThetaParams.from_row(candidate)
    row = run_b4_task(
        task,
        context["stage1"],
        context["phase_config"],
        context["free_reference"],
        context["free_rows_by_id"],
        args.sumo_binary,
        args.emit_fcd,
        params,
    )
    score, penalty, bo_score = score_for_row(row, args.w_emv, args.w_veh)
    events = signal_event_summary(task.run_dir / "signal_events.csv")
    row.update({
        "changed_variable": candidate.get("changed_variable", ""),
        "changed_value": candidate.get("changed_value", ""),
        "score_sec": f"{score:.2f}",
        "failure_penalty_sec": f"{penalty:.2f}",
        "bo_score_sec": f"{bo_score:.2f}",
        **{field: candidate.get(field, "") for field in [*BASE_DECISION.keys(), *BASE_STRUCTURE.keys()]},
        **{field: value for field, value in events.items() if field != "segment_hits"},
    })
    row["segment_hits_json"] = json.dumps(events.get("segment_hits", {}), ensure_ascii=False, sort_keys=True)
    return row


def aggregate_variable_rows(summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variable in OFAT_VALUES:
        subset = [row for row in summary_rows if row.get("changed_variable") == variable]
        passed = [row for row in subset if row_status(row) == "PASS"]
        failed = [row for row in subset if row_status(row) != "PASS"]
        ev_values = [safe_float(row.get("T_actual_EMV_sec")) for row in passed if row.get("T_actual_EMV_sec") not in {"", None}]
        score_values = [safe_float(row.get("bo_score_sec")) for row in passed if row.get("bo_score_sec") not in {"", None}]
        preemptions = [safe_float(row.get("stage3_preemption_count")) for row in passed]
        tau_hits = [safe_float(row.get("original_tau_hit_0p75")) for row in passed]
        sensitivity = "low"
        if failed or (len(ev_values) >= 2 and max(ev_values) - min(ev_values) >= 120):
            sensitivity = "high"
        elif len(ev_values) >= 2 and max(ev_values) - min(ev_values) >= 45:
            sensitivity = "medium"
        rows.append({
            "variable": variable,
            "candidate_count": len(subset),
            "pass_count": len(passed),
            "fail_count": len(failed),
            "best_EV_sec": round(min(ev_values), 3) if ev_values else "",
            "worst_EV_sec": round(max(ev_values), 3) if ev_values else "",
            "EV_range_sec": round(max(ev_values) - min(ev_values), 3) if len(ev_values) >= 2 else "",
            "best_score_sec": round(min(score_values), 3) if score_values else "",
            "worst_score_sec": round(max(score_values), 3) if score_values else "",
            "preemption_range": round(max(preemptions) - min(preemptions), 3) if len(preemptions) >= 2 else "",
            "tau_hit_0p75_range": round(max(tau_hits) - min(tau_hits), 3) if len(tau_hits) >= 2 else "",
            "sensitivity_label": sensitivity,
        })
    return rows


def write_report(path: Path, payload: dict[str, Any]) -> None:
    baseline = payload["baseline"]
    lock = payload.get("structure_lock", {})
    lines = [
        "# B4 Fixed Parameter Sensitivity",
        "",
        f"- generated_at: {utc_now()}",
        f"- run_id: `{payload['run_id']}`",
        f"- net: `{payload['net_file']}`",
        f"- demand: `{payload['background_route']}`",
        "- fixed screened decision variables: "
        + ", ".join(f"{key}={value}" for key, value in BASE_DECISION.items()),
        "",
        "## Baseline",
        "",
        f"- status={baseline.get('final_status')}, EV={baseline.get('T_actual_EMV_sec')} sec, "
        f"general={baseline.get('general_mean_travel_time_sec')} sec, score={baseline.get('bo_score_sec')}",
        f"- B04 baseline speed={payload.get('b04_baseline_speed_kmh')} km/h "
        f"(target15_check={'required' if payload.get('target15_baseline_required') else 'diagnostic_bypassed'})",
        f"- stage3_preemption_count={baseline.get('stage3_preemption_count')}, "
        f"original_tau_caseB={baseline.get('case_b_original_tau_count')}, "
        f"original_tau_hit_0p75={baseline.get('original_tau_hit_0p75')}",
        "",
        "## Variable Sensitivity",
        "",
    ]
    for row in payload["variable_summary"]:
        lines.append(
            f"- {row['variable']}: {row['sensitivity_label']}, PASS {row['pass_count']}/{row['candidate_count']}, "
            f"EV_range={row['EV_range_sec']} sec, tau_hit_0p75_range={row['tau_hit_0p75_range']}"
        )
    lines.extend(["", "## Structure Preconfirmation Lock", ""])
    lines.append(f"- lock_status: `{lock.get('lock_status', '')}`")
    lines.append(f"- baseline_structure: `{json.dumps(lock.get('baseline_structure', {}), ensure_ascii=False, sort_keys=True)}`")
    lines.append(f"- candidate_structure: `{json.dumps(lock.get('candidate_structure', {}), ensure_ascii=False, sort_keys=True)}`")
    lines.append(f"- selected_structure: `{json.dumps(lock.get('selected_structure', {}), ensure_ascii=False, sort_keys=True)}`")
    combined = lock.get("combined_confirmation", {})
    lines.append(
        f"- combined_confirmation: status={combined.get('status', '')}, "
        f"reason={combined.get('reason', '')}, EV={combined.get('T_actual_EMV_sec', '')}, score={combined.get('bo_score_sec', '')}"
    )
    for variable, row in lock.get("variables", {}).items():
        lines.append(
            f"- {variable}: {row.get('status', '')}, selected={row.get('selected_value', '')}, "
            f"PASS {row.get('pass_count', '')}/{row.get('candidate_count', '')}, "
            f"sensitivity={row.get('sensitivity_label', '')}, reason={row.get('reason', '')}"
        )
    lines.extend(["", "## Best Passing Candidates", ""])
    for row in payload["summary"][:10]:
        lines.append(
            f"- {row['parameter_id']}: status={row['final_status']}, changed={row['changed_variable']}={row['changed_value']}, "
            f"EV={row['T_actual_EMV_sec']}, score={row['bo_score_sec']}, "
            f"caseB_segment={row['case_b_runtime_segment_count']}, caseB_movement={row['case_b_runtime_movement_count']}, "
            f"original_tau_caseB={row.get('case_b_original_tau_count', '')}, "
            f"tau_hit_0p75={row['original_tau_hit_0p75']}"
        )
    lines.extend([
        "",
        "## Outputs",
        "",
        f"- all values: `{payload['all_values_csv']}`",
        f"- summary: `{payload['summary_csv']}`",
        f"- variable summary: `{payload['variable_summary_csv']}`",
        f"- structure lock json: `{payload['structure_lock_json']}`",
        f"- structure lock csv: `{payload['structure_lock_csv']}`",
        f"- structure preconfirm report: `{payload['structure_preconfirm_report_md']}`",
        f"- next BO command: `{payload['next_bo_command']}`",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def structure_lock_csv_rows(lock: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for variable, item in lock.get("variables", {}).items():
        rows.append({
            "variable": variable,
            "status": item.get("status", ""),
            "reason": item.get("reason", ""),
            "baseline_value": item.get("baseline_value", ""),
            "selected_value": item.get("selected_value", ""),
            "best_row_id": item.get("best_row_id", ""),
            "pass_count": item.get("pass_count", ""),
            "fail_count": item.get("fail_count", ""),
            "candidate_count": item.get("candidate_count", ""),
            "sensitivity_label": item.get("sensitivity_label", ""),
        })
    return rows


def next_bo_command(lock_json: Path, args: argparse.Namespace) -> str:
    return (
        "python3 '09 Compact Corridor Baseline/run_b4_theta_bo.py' "
        f"--structure-lock-json '{rel(lock_json)}' "
        f"--net-file '{rel(args.net_file)}' "
        f"--background-route '{rel(args.background_route)}' "
        f"--stage1-dir '{rel(args.stage1_dir) if args.stage1_dir else ''}'"
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or "fixed_param_sensitivity_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_metrics_dir = args.metrics_root / run_id
    run_metrics_dir.mkdir(parents=True, exist_ok=True)
    candidates = build_candidates(args.only_variable)
    candidate_fields = ["parameter_id", "changed_variable", "changed_value", *BASE_DECISION.keys(), *BASE_STRUCTURE.keys()]
    extra_result_fields = list(dict.fromkeys([
        *ALL_VALUE_FIELDS,
        "changed_variable",
        "changed_value",
        *BASE_DECISION.keys(),
        *BASE_STRUCTURE.keys(),
        "score_sec",
        "bo_score_sec",
        "failure_penalty_sec",
        "case_b_active_count",
        "case_b_runtime_segment_count",
        "case_b_runtime_movement_count",
        "case_b_original_tau_count",
        "case_b_b0_prior_count",
        "original_tau_trigger_count",
        "original_tau_hit_0p65",
        "original_tau_hit_0p75",
        "original_tau_hit_0p85",
        "segment_hits_json",
    ]))
    write_rows(run_metrics_dir / "fixed_param_candidates.csv", candidates, candidate_fields)
    context = prepare_real_context(run_id, args)
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        for repeat in range(1, args.repeats + 1):
            rows.append(evaluate_candidate(run_id, candidate, repeat, args, context))
            write_csv(run_metrics_dir / "fixed_param_all_values.partial.csv", rows, extra_result_fields)
    lock_without_combined = structure_lock_summary(rows)
    combined_row: dict[str, Any] | None = None
    if args.confirm_combined_lock and lock_without_combined["lock_status"] == "PENDING_COMBINED_CONFIRMATION":
        combined_candidate = build_combined_candidate(lock_without_combined["candidate_structure"])
        candidates.append(combined_candidate)
        for repeat in range(1, args.repeats + 1):
            row = evaluate_candidate(run_id, combined_candidate, repeat, args, context)
            rows.append(row)
            if repeat == 1:
                combined_row = row
            write_csv(run_metrics_dir / "fixed_param_all_values.partial.csv", rows, extra_result_fields)
    structure_lock = structure_lock_summary(rows, combined_row)
    all_fields = extra_result_fields
    all_values = run_metrics_dir / "fixed_param_all_values.csv"
    write_csv(all_values, rows, all_fields)
    write_rows(run_metrics_dir / "fixed_param_candidates.csv", candidates, candidate_fields)
    summary = sorted(rows, key=lambda row: (safe_float(row.get("bo_score_sec"), FAILURE_PENALTY_SEC * 10), str(row.get("parameter_id", ""))))
    summary_fields = [
        "parameter_id", "changed_variable", "changed_value", "final_status", "failure_reason",
        *BASE_DECISION.keys(), *BASE_STRUCTURE.keys(),
        "T_actual_EMV_sec", "general_mean_travel_time_sec", "bo_score_sec",
        "stage3_preemption_count", "bottleneck_mode_count", "signal_burden_sec",
        "case_b_active_count", "case_b_runtime_segment_count", "case_b_runtime_movement_count",
        "case_b_original_tau_count", "case_b_b0_prior_count", "original_tau_trigger_count",
        "original_tau_sample_count", "original_tau_raw_max", "original_tau_effective_max",
        "original_tau_hit_0p65", "original_tau_hit_0p75", "original_tau_hit_0p85",
        "segment_hits_json", "signal_events_csv",
    ]
    summary_csv = args.output_dir / "fixed_param_sensitivity_summary.csv"
    write_rows(summary_csv, summary, summary_fields)
    variable_summary = aggregate_variable_rows(rows)
    variable_csv = args.output_dir / "fixed_param_variable_sensitivity.csv"
    write_rows(variable_csv, variable_summary, [
        "variable", "candidate_count", "pass_count", "fail_count",
        "best_EV_sec", "worst_EV_sec", "EV_range_sec", "best_score_sec", "worst_score_sec",
        "preemption_range", "tau_hit_0p75_range", "sensitivity_label",
    ])
    baseline = next(row for row in rows if row.get("changed_variable") == "baseline")
    structure_lock_json = args.output_dir / "structure_param_lock_summary.json"
    structure_lock_csv = args.output_dir / "structure_param_lock.csv"
    structure_preconfirm_report = args.output_dir / "structure_param_preconfirm_report.md"
    lock_payload = {
        "schema": "compact_v9_B4_structure_param_lock.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "net_file": rel(args.net_file),
        "background_route": rel(args.background_route),
        "stage1_dir": rel(args.stage1_dir) if args.stage1_dir else "",
        "decision_variables_fixed": dict(BASE_DECISION),
        **structure_lock,
    }
    write_json(structure_lock_json, lock_payload)
    write_rows(structure_lock_csv, structure_lock_csv_rows(structure_lock), [
        "variable", "status", "reason", "baseline_value", "selected_value", "best_row_id",
        "pass_count", "fail_count", "candidate_count", "sensitivity_label",
    ])
    payload = {
        "schema": "compact_v9_B4_fixed_param_sensitivity.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "net_file": rel(args.net_file),
        "background_route": rel(args.background_route),
        "stage1_dir": rel(args.stage1_dir) if args.stage1_dir else "",
        "baseline": baseline,
        "b04_baseline_speed_kmh": context["b04_baseline"].get("b04_ev_speed_kmh"),
        "target15_baseline_required": args.require_target15_baseline,
        "summary": summary,
        "variable_summary": variable_summary,
        "structure_lock": structure_lock,
        "all_values_csv": rel(all_values),
        "summary_csv": rel(summary_csv),
        "variable_summary_csv": rel(variable_csv),
        "structure_lock_json": rel(structure_lock_json),
        "structure_lock_csv": rel(structure_lock_csv),
        "structure_preconfirm_report_md": rel(structure_preconfirm_report),
        "next_bo_command": next_bo_command(structure_lock_json, args),
    }
    summary_json = args.output_dir / "fixed_param_sensitivity_summary.json"
    report_md = args.output_dir / "fixed_param_sensitivity_report.md"
    write_json(summary_json, payload)
    write_report(report_md, {**payload, "report_md": rel(report_md)})
    write_report(structure_preconfirm_report, {**payload, "report_md": rel(structure_preconfirm_report)})
    latest = {
        "schema": "compact_v9_B4_fixed_param_sensitivity_latest.v1",
        "run_id": run_id,
        "summary_json": rel(summary_json),
        "report_md": rel(report_md),
        "summary_csv": rel(summary_csv),
        "variable_summary_csv": rel(variable_csv),
        "structure_lock_json": rel(structure_lock_json),
        "structure_preconfirm_report_md": rel(structure_preconfirm_report),
    }
    write_json(args.metrics_root / "latest.json", latest)
    return {**latest, "candidate_count": len(candidates), "completed_rows": len(rows)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run fixed-parameter B4 sensitivity checks.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--metrics-root", type=Path, default=DEFAULT_METRICS_ROOT)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--net-file", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_ROUTE)
    parser.add_argument("--stage1-dir", type=Path, default=DEFAULT_STAGE1_DIR)
    parser.add_argument("--phase", default="bo-smoke")
    parser.add_argument("--hard-max-sim-time", type=float, default=4000.0)
    parser.add_argument("--seed", type=int, default=20260605)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--only-variable", default="")
    parser.add_argument("--w-emv", type=float, default=10.0)
    parser.add_argument("--w-veh", type=float, default=1.0)
    parser.add_argument("--tau-scale", type=float, default=BASE_STRUCTURE["tau_scale"])
    parser.add_argument("--tau-numerator-gamma", type=float, default=DEFAULT_TAU_NUMERATOR_GAMMA)
    parser.add_argument("--sumo-binary", default=None)
    parser.add_argument("--emit-fcd", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--require-target15-baseline", action="store_true")
    parser.add_argument("--skip-confirm-combined-lock", dest="confirm_combined_lock", action="store_false")
    parser.set_defaults(confirm_combined_lock=True)
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    args.metrics_root = args.metrics_root.resolve()
    args.run_root = args.run_root.resolve()
    args.output_dir = args.output_dir.resolve()
    args.net_file = args.net_file.resolve()
    args.background_route = args.background_route.resolve()
    args.stage1_dir = args.stage1_dir.resolve() if args.stage1_dir else None
    args.mock_eval = False
    args.allow_baseline_speed_out_of_target = not args.require_target15_baseline
    if not args.net_file.is_file():
        raise FileNotFoundError(f"missing_net_file:{args.net_file}")
    if not args.background_route.is_file():
        raise FileNotFoundError(f"missing_background_route:{args.background_route}")
    if args.stage1_dir is not None and not args.stage1_dir.is_dir():
        raise FileNotFoundError(f"missing_stage1_dir:{args.stage1_dir}")
    if args.only_variable and args.only_variable not in OFAT_VALUES:
        raise ValueError(f"unknown_only_variable:{args.only_variable}")
    if args.repeats < 1:
        raise ValueError("repeats_must_be_positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_args(args)
        result = run(args)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {type(exc).__name__}:{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
