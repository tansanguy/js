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


def signal_event_summary(signal_events: Path) -> dict[str, Any]:
    rows = read_csv(signal_events)
    case_b_counts = Counter(row.get("case_b_source", "") for row in rows if row.get("case_b_source"))
    action_counts = Counter(row.get("action_type", "") for row in rows if row.get("action_type"))
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
        "case_b_not_case_b_count": case_b_counts.get("not_case_b", 0),
        "case_b_runtime_segment_count": case_b_counts.get("runtime_tau_segment", 0),
        "case_b_runtime_movement_count": case_b_counts.get("runtime_tau_movement", 0),
        "case_b_b0_prior_count": case_b_counts.get("b0_prior", 0),
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
    lines.extend(["", "## Best Passing Candidates", ""])
    for row in payload["summary"][:10]:
        lines.append(
            f"- {row['parameter_id']}: status={row['final_status']}, changed={row['changed_variable']}={row['changed_value']}, "
            f"EV={row['T_actual_EMV_sec']}, score={row['bo_score_sec']}, "
            f"caseB_segment={row['case_b_runtime_segment_count']}, caseB_movement={row['case_b_runtime_movement_count']}, "
            f"tau_hit_0p75={row['original_tau_hit_0p75']}"
        )
    lines.extend([
        "",
        "## Outputs",
        "",
        f"- all values: `{payload['all_values_csv']}`",
        f"- summary: `{payload['summary_csv']}`",
        f"- variable summary: `{payload['variable_summary_csv']}`",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or "fixed_param_sensitivity_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_metrics_dir = args.metrics_root / run_id
    run_metrics_dir.mkdir(parents=True, exist_ok=True)
    candidates = build_candidates(args.only_variable)
    candidate_fields = ["parameter_id", "changed_variable", "changed_value", *BASE_DECISION.keys(), *BASE_STRUCTURE.keys()]
    write_rows(run_metrics_dir / "fixed_param_candidates.csv", candidates, candidate_fields)
    context = prepare_real_context(run_id, args)
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        for repeat in range(1, args.repeats + 1):
            rows.append(evaluate_candidate(run_id, candidate, repeat, args, context))
            write_csv(run_metrics_dir / "fixed_param_all_values.partial.csv", rows, list(dict.fromkeys([*ALL_VALUE_FIELDS, "changed_variable", "changed_value", *BASE_DECISION.keys(), *BASE_STRUCTURE.keys(), "score_sec", "bo_score_sec", "failure_penalty_sec", "case_b_runtime_segment_count", "case_b_runtime_movement_count", "case_b_b0_prior_count", "original_tau_hit_0p65", "original_tau_hit_0p75", "original_tau_hit_0p85", "segment_hits_json"])))
    all_fields = list(dict.fromkeys([*ALL_VALUE_FIELDS, "changed_variable", "changed_value", *BASE_DECISION.keys(), *BASE_STRUCTURE.keys(), "score_sec", "bo_score_sec", "failure_penalty_sec", "case_b_runtime_segment_count", "case_b_runtime_movement_count", "case_b_b0_prior_count", "original_tau_hit_0p65", "original_tau_hit_0p75", "original_tau_hit_0p85", "segment_hits_json"]))
    all_values = run_metrics_dir / "fixed_param_all_values.csv"
    write_csv(all_values, rows, all_fields)
    summary = sorted(rows, key=lambda row: (safe_float(row.get("bo_score_sec"), FAILURE_PENALTY_SEC * 10), str(row.get("parameter_id", ""))))
    summary_fields = [
        "parameter_id", "changed_variable", "changed_value", "final_status", "failure_reason",
        *BASE_DECISION.keys(), *BASE_STRUCTURE.keys(),
        "T_actual_EMV_sec", "general_mean_travel_time_sec", "bo_score_sec",
        "stage3_preemption_count", "bottleneck_mode_count", "signal_burden_sec",
        "case_b_runtime_segment_count", "case_b_runtime_movement_count", "case_b_b0_prior_count",
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
        "all_values_csv": rel(all_values),
        "summary_csv": rel(summary_csv),
        "variable_summary_csv": rel(variable_csv),
    }
    summary_json = args.output_dir / "fixed_param_sensitivity_summary.json"
    report_md = args.output_dir / "fixed_param_sensitivity_report.md"
    write_json(summary_json, payload)
    write_report(report_md, {**payload, "report_md": rel(report_md)})
    latest = {
        "schema": "compact_v9_B4_fixed_param_sensitivity_latest.v1",
        "run_id": run_id,
        "summary_json": rel(summary_json),
        "report_md": rel(report_md),
        "summary_csv": rel(summary_csv),
        "variable_summary_csv": rel(variable_csv),
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
