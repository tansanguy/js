#!/usr/bin/env python3
"""One-factor-at-a-time sensitivity run around the u130 Toegye-15 B4 theta."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from b4_runtime import safe_float, write_csv  # noqa: E402
from postprocess_u130_init20 import candidate_edge_metrics  # noqa: E402
from run_b4_theta_bo import (  # noqa: E402
    ALL_VALUE_FIELDS,
    FAILURE_PENALTY_SEC,
    SCORE_FIELDS,
    apply_structure_params,
    evaluate_theta_batch,
    prepare_real_context,
    structure_inputs,
    write_json,
)


DEFAULT_OUTPUT_PREFIX = "compact_v9_B4_theta_u130_toegye15_ofat"
DEFAULT_METRICS_ROOT = PROJECT_ROOT / "results/metrics" / DEFAULT_OUTPUT_PREFIX
DEFAULT_RUN_ROOT = PROJECT_ROOT / "runs" / DEFAULT_OUTPUT_PREFIX
DEFAULT_NET = PIPELINE_DIR / "tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml"
DEFAULT_ROUTE = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130_toegye15.rou.xml"
DEFAULT_STAGE1_DIR = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1_u130_toegye15"
DEFAULT_TAU_NUMERATOR_GAMMA = 5.0
DEFAULT_BASELINE = {
    "parameter_id": "baseline_al115_tl21_dt80_ge32_q0",
    "alpha": 1.15,
    "t_lead": 21,
    "delta_T_thr": 80,
    "G_ext": 32,
    "Q_trig": 0,
}
OFAT_VALUES = {
    "alpha": [1.00, 1.05, 1.15, 1.25, 1.40, 1.60],
    "t_lead": [0, 10, 21, 35, 50, 65, 95],
    "delta_T_thr": [0, 40, 60, 80, 120, 160],
    "G_ext": [0, 10, 20, 32, 40, 45],
    "Q_trig": [0, 5, 10, 20, 35, 50],
}
FOCUS_SEGMENTS = {"S6:upbound", "S7:upbound", "S9:upbound", "S15:upbound"}


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


def stage1_movements(stage1_dir: Path) -> list[dict[str, Any]]:
    rows = read_csv(stage1_dir / "b4_approach_storage_link_plan.csv")
    return [
        {
            "movement_id": row.get("movement_id", ""),
            "segment_id": row.get("mapped_S_segment", ""),
            "route_order_index": safe_float(row.get("route_order_index")),
            "tls_id": row.get("tls_id", ""),
            "edges": [edge for edge in row.get("corridor_storage_edges", "").split() if edge],
        }
        for row in rows
    ]


def theta_label(value: float | int) -> str:
    if isinstance(value, float) and not value.is_integer():
        return str(value).replace(".", "p")
    return str(int(value))


def build_candidates(only_variable: str = "") -> list[dict[str, Any]]:
    rows = [dict(DEFAULT_BASELINE, changed_variable="baseline", changed_value="")]
    for variable, values in OFAT_VALUES.items():
        if only_variable and variable != only_variable:
            continue
        baseline_value = DEFAULT_BASELINE[variable]
        for value in values:
            if float(value) == float(baseline_value):
                continue
            theta = dict(DEFAULT_BASELINE)
            theta[variable] = value
            theta["parameter_id"] = f"ofat_{variable}_{theta_label(float(value))}"
            theta["changed_variable"] = variable
            theta["changed_value"] = value
            rows.append(theta)
    return rows


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


def summary_rows(rows: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidate_by_id = {row["parameter_id"]: row for row in candidates}
    baseline = next(row for row in rows if row.get("parameter_id") == DEFAULT_BASELINE["parameter_id"])
    baseline_ev = safe_float(baseline.get("T_actual_EMV_sec"))
    baseline_general = safe_float(baseline.get("general_mean_travel_time_sec"))
    baseline_score = safe_float(baseline.get("bo_score_sec"))
    out = []
    for row in sorted(rows, key=lambda item: safe_float(item.get("bo_score_sec"), FAILURE_PENALTY_SEC * 10)):
        candidate = candidate_by_id.get(row.get("parameter_id", ""), {})
        ev = safe_float(row.get("T_actual_EMV_sec"))
        general = safe_float(row.get("general_mean_travel_time_sec"))
        score = safe_float(row.get("bo_score_sec"))
        out.append({
            "parameter_id": row.get("parameter_id", ""),
            "changed_variable": candidate.get("changed_variable", ""),
            "changed_value": candidate.get("changed_value", ""),
            "final_status": row.get("final_status", ""),
            "failure_reason": row.get("failure_reason", ""),
            "emergency_arrived": row.get("emergency_arrived", ""),
            "emergency_teleport": row.get("emergency_teleport", ""),
            "emergency_stuck_duration_sec": row.get("emergency_stuck_duration_sec", ""),
            "T_actual_EMV_sec": row.get("T_actual_EMV_sec", ""),
            "general_mean_travel_time_sec": row.get("general_mean_travel_time_sec", ""),
            "bo_score_sec": row.get("bo_score_sec", ""),
            "signal_burden_sec": row.get("signal_burden_sec", ""),
            "general_mean_delay_sec": row.get("general_mean_delay_sec", ""),
            "background_arrived_ratio": row.get("background_arrived_ratio", ""),
            "delta_EV_sec": round(ev - baseline_ev, 3) if ev else "",
            "delta_general_sec": round(general - baseline_general, 3) if general else "",
            "delta_score_sec": round(score - baseline_score, 3) if score else "",
            "alpha": row.get("alpha", ""),
            "t_lead": row.get("t_lead", ""),
            "delta_T_thr": row.get("delta_T_thr", ""),
            "G_ext": row.get("G_ext", ""),
            "Q_trig": row.get("Q_trig", ""),
        })
    return out


def variable_rows(summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for variable in OFAT_VALUES:
        subset = [row for row in summary if row.get("changed_variable") == variable]
        passed = [row for row in subset if row_status(row) == "PASS" and row.get("T_actual_EMV_sec") not in {"", None}]
        failed = [row for row in subset if row_status(row) != "PASS"]
        ev_values = [safe_float(row.get("T_actual_EMV_sec")) for row in passed]
        score_values = [safe_float(row.get("bo_score_sec")) for row in passed]
        abs_delta_ev = [abs(safe_float(row.get("delta_EV_sec"))) for row in passed]
        sensitivity = "low"
        if failed or (abs_delta_ev and max(abs_delta_ev) >= 120):
            sensitivity = "high"
        elif abs_delta_ev and max(abs_delta_ev) >= 45:
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
            "mean_abs_delta_EV_sec": round(sum(abs_delta_ev) / len(abs_delta_ev), 3) if abs_delta_ev else "",
            "max_abs_delta_EV_sec": round(max(abs_delta_ev), 3) if abs_delta_ev else "",
            "sensitivity_label": sensitivity,
        })
    return rows


def focus_segment_rows(run_id: str, rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    movements = stage1_movements(args.stage1_dir)
    movement_by_id = {movement["movement_id"]: movement for movement in movements}
    out = []
    for row in rows:
        parameter_id = row.get("parameter_id", "")
        candidate_dir = args.run_root / run_id / "B4" / parameter_id / "repeat_001"
        metrics = candidate_edge_metrics(candidate_dir / "edgeData.xml", movements)
        for movement_id, item in metrics.items():
            movement = movement_by_id[movement_id]
            if movement["segment_id"] not in FOCUS_SEGMENTS:
                continue
            out.append({
                "parameter_id": parameter_id,
                "changed_variable": row.get("changed_variable", ""),
                "changed_value": row.get("changed_value", ""),
                "final_status": row.get("final_status", ""),
                "movement_id": movement_id,
                "segment_id": movement["segment_id"],
                "route_order_index": int(movement["route_order_index"]),
                "speed_kmh": item.get("speed_kmh", ""),
                "low_lt10_ratio": item.get("low_lt10_ratio", ""),
                "waiting_sec": item.get("waiting_sec", ""),
                "density": item.get("density", ""),
            })
    return out


def write_report(path: Path, payload: dict[str, Any]) -> None:
    baseline = payload["baseline"]
    lines = [
        "# B4 Theta OFAT Sensitivity",
        "",
        f"- generated_at: {utc_now()}",
        f"- run_id: `{payload['run_id']}`",
        f"- net: `{payload['net_file']}`",
        f"- demand: `{payload['background_route']}`",
        f"- tau: `{payload['tau']}`",
        f"- hold_max: `{payload['hold_max']}`",
        f"- d_up: `{payload['d_up']}`",
        f"- tau_scale: `{payload['tau_scale']}`",
        f"- tau_numerator_gamma: `{payload['tau_numerator_gamma']}`",
        "",
        "## Baseline",
        "",
        f"- theta: alpha={baseline['alpha']}, t_lead={baseline['t_lead']}, delta_T_thr={baseline['delta_T_thr']}, G_ext={baseline['G_ext']}, Q_trig={baseline['Q_trig']}",
        f"- result: status={baseline['final_status']}, EV={baseline['T_actual_EMV_sec']} sec, general={baseline['general_mean_travel_time_sec']} sec, score={baseline['bo_score_sec']}",
        "",
        "## Variable Sensitivity",
        "",
    ]
    for row in payload["variable_summary"]:
        lines.append(
            f"- {row['variable']}: {row['sensitivity_label']} sensitivity, "
            f"PASS {row['pass_count']}/{row['candidate_count']}, "
            f"EV range={row['EV_range_sec']} sec, max |delta EV|={row['max_abs_delta_EV_sec']} sec"
        )
    lines.extend(["", "## Best Single Changes", ""])
    for row in payload["summary"][:8]:
        lines.append(
            f"- {row['parameter_id']}: status={row['final_status']}, "
            f"changed={row['changed_variable']}={row['changed_value']}, "
            f"EV={row['T_actual_EMV_sec']}, general={row['general_mean_travel_time_sec']}, "
            f"score={row['bo_score_sec']}, delta_EV={row['delta_EV_sec']}"
        )
    lines.extend([
        "",
        "## Outputs",
        "",
        f"- summary: `{payload['summary_csv']}`",
        f"- variable summary: `{payload['variable_summary_csv']}`",
        f"- focus segment summary: `{payload['focus_segment_csv']}`",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id
    run_metrics_dir = args.metrics_root / run_id
    run_metrics_dir.mkdir(parents=True, exist_ok=True)
    candidates = build_candidates(args.only_variable)
    candidate_csv = run_metrics_dir / "ofat_candidates.csv"
    write_rows(candidate_csv, candidates, ["parameter_id", "changed_variable", "changed_value", "alpha", "t_lead", "delta_T_thr", "G_ext", "Q_trig"])

    real_context = prepare_real_context(run_id, args)
    rows = evaluate_theta_batch(run_id, candidates, 0, args, real_context)
    write_csv(run_metrics_dir / "ofat_all_values.csv", rows, ALL_VALUE_FIELDS)
    score_rows = [
        {field: row.get(field, "") for field in SCORE_FIELDS}
        for row in rows
    ]
    write_rows(run_metrics_dir / "ofat_score_summary.csv", score_rows, SCORE_FIELDS)

    summary = summary_rows(rows, candidates)
    variable_summary = variable_rows(summary)
    focus = focus_segment_rows(run_id, summary, args)
    out_dir = args.output_dir
    summary_csv = out_dir / "ofat_sensitivity_summary.csv"
    variable_csv = out_dir / "ofat_variable_sensitivity.csv"
    focus_csv = out_dir / "ofat_focus_segment_speed_summary.csv"
    report_md = out_dir / "ofat_sensitivity_report.md"
    write_rows(summary_csv, summary, [
        "parameter_id",
        "changed_variable",
        "changed_value",
        "final_status",
        "failure_reason",
        "emergency_arrived",
        "emergency_teleport",
        "emergency_stuck_duration_sec",
        "T_actual_EMV_sec",
        "general_mean_travel_time_sec",
        "bo_score_sec",
        "signal_burden_sec",
        "general_mean_delay_sec",
        "background_arrived_ratio",
        "delta_EV_sec",
        "delta_general_sec",
        "delta_score_sec",
        "alpha",
        "t_lead",
        "delta_T_thr",
        "G_ext",
        "Q_trig",
    ])
    write_rows(variable_csv, variable_summary, [
        "variable",
        "candidate_count",
        "pass_count",
        "fail_count",
        "best_EV_sec",
        "worst_EV_sec",
        "EV_range_sec",
        "best_score_sec",
        "worst_score_sec",
        "mean_abs_delta_EV_sec",
        "max_abs_delta_EV_sec",
        "sensitivity_label",
    ])
    write_rows(focus_csv, focus, [
        "parameter_id",
        "changed_variable",
        "changed_value",
        "final_status",
        "movement_id",
        "segment_id",
        "route_order_index",
        "speed_kmh",
        "low_lt10_ratio",
        "waiting_sec",
        "density",
    ])
    baseline = next(row for row in summary if row.get("parameter_id") == DEFAULT_BASELINE["parameter_id"])
    payload = {
        "schema": "compact_v9_b4_theta_ofat_sensitivity.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "net_file": rel(args.net_file),
        "background_route": rel(args.background_route),
        "stage1_dir": rel(args.stage1_dir) if args.stage1_dir else "",
        "hard_max_sim_time": args.hard_max_sim_time,
        **structure_inputs(args),
        "candidate_count": len(candidates),
        "baseline": baseline,
        "summary": summary,
        "variable_summary": variable_summary,
        "candidate_csv": rel(candidate_csv),
        "summary_csv": rel(summary_csv),
        "variable_summary_csv": rel(variable_csv),
        "focus_segment_csv": rel(focus_csv),
        "report_md": rel(report_md),
    }
    write_report(report_md, payload)
    write_json(out_dir / "ofat_sensitivity_summary.json", payload)
    write_json(run_metrics_dir / "ofat_sensitivity_summary.json", payload)
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OFAT sensitivity around the active target15 B4 theta.")
    parser.add_argument("--run-id", default="u130_toegye15_ofat_screened5_20260606")
    parser.add_argument("--metrics-root", type=Path, default=DEFAULT_METRICS_ROOT)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--output-dir", type=Path, default=PIPELINE_DIR / "tdata_signal/u130_toegye15_ofat_sensitivity")
    parser.add_argument("--net-file", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_ROUTE)
    parser.add_argument("--stage1-dir", type=Path, default=DEFAULT_STAGE1_DIR)
    parser.add_argument("--hard-max-sim-time", type=float, default=4000.0)
    parser.add_argument("--require-target15-baseline", action="store_true")
    parser.add_argument("--structure-lock-json", type=Path, default=None)
    parser.add_argument("--tau", type=float, default=None)
    parser.add_argument("--hold-max", dest="hold_max", type=float, default=None)
    parser.add_argument("--d-up", dest="d_up", type=int, default=None)
    parser.add_argument("--tau-scale", type=float, default=None)
    parser.add_argument("--tau-numerator-gamma", type=float, default=None)
    parser.add_argument("--phase", default="bo-smoke")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--w-emv", type=float, default=10.0)
    parser.add_argument("--w-veh", type=float, default=1.0)
    parser.add_argument("--sumo-binary", default=None)
    parser.add_argument("--emit-fcd", action="store_true")
    parser.add_argument("--mock-eval", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--only-variable", choices=["", *OFAT_VALUES.keys()], default="")
    args = parser.parse_args(argv)
    args.net_file = args.net_file.resolve()
    args.background_route = args.background_route.resolve()
    args.stage1_dir = args.stage1_dir.resolve() if args.stage1_dir else None
    args.metrics_root = args.metrics_root.resolve()
    args.run_root = args.run_root.resolve()
    args.output_dir = args.output_dir.resolve()
    args.allow_baseline_speed_out_of_target = not args.require_target15_baseline
    if args.tau_scale is not None and not 0.0 <= args.tau_scale <= 1.0:
        raise ValueError("tau_scale_must_be_between_0_and_1")
    if args.tau_numerator_gamma is not None and args.tau_numerator_gamma < 0.1:
        raise ValueError("tau_numerator_gamma_must_be_at_least_0p1")
    apply_structure_params(args)
    if args.stage1_dir is not None and not args.stage1_dir.is_dir():
        raise FileNotFoundError(f"missing_stage1_dir:{args.stage1_dir}")
    return args


def main(argv: list[str] | None = None) -> int:
    payload = run(parse_args(argv))
    print(json.dumps({
        "run_id": payload["run_id"],
        "candidate_count": payload["candidate_count"],
        "summary_csv": payload["summary_csv"],
        "variable_summary_csv": payload["variable_summary_csv"],
        "report_md": payload["report_md"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
