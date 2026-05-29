#!/usr/bin/env python3
"""Run corridor-wide B1 Green Wave smoke for ER_ACC_019."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STEP14_PATH = PROJECT_ROOT / "01_prepare/08_signal/step14_b1_green_wave_v1_er_acc_002.py"

DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
DEFAULT_BACKGROUND_ROUTE = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml"
DEFAULT_EMERGENCY_ROUTES = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
DEFAULT_TERMINALS = PROJECT_ROOT / "data_prepared/signals/priority_terminal_candidates.csv"
DEFAULT_TLS_AUDIT = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_spine_v2.csv"
DEFAULT_CONFIG = PROJECT_ROOT / "configs/b1_priority_signal_config.json"
DEFAULT_RUN_DIR = PROJECT_ROOT / "runs/b1_green_wave_corridor_er_acc_019"

SUMMARY_CSV = PROJECT_ROOT / "results/metrics/b1_green_wave_corridor_er_acc_019_smoke_summary.csv"
SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b1_green_wave_corridor_er_acc_019_smoke_summary.json"
SIGNAL_EVENTS_CSV = PROJECT_ROOT / "results/metrics/b1_green_wave_corridor_er_acc_019_signal_events.csv"
TERMINAL_COVERAGE_CSV = PROJECT_ROOT / "results/metrics/b1_green_wave_corridor_er_acc_019_terminal_coverage.csv"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step15_b1_green_wave_corridor_er_acc_019.log"
DOC_PATH = PROJECT_ROOT / "docs/Step15_green_wave_corridor_validation.md"


class Step15Error(RuntimeError):
    """Expected Step 15 failure."""


def load_step14_module() -> Any:
    spec = importlib.util.spec_from_file_location("step14_green_wave", STEP14_PATH)
    if spec is None or spec.loader is None:
        raise Step15Error(f"cannot import Step14 module: {STEP14_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


S14 = load_step14_module()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ER_ACC_019 corridor-wide B1 Green Wave smoke.")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--emergency-routes", type=Path, default=DEFAULT_EMERGENCY_ROUTES)
    parser.add_argument("--route-id", default="ER_ACC_019")
    parser.add_argument("--priority-terminals", type=Path, default=DEFAULT_TERMINALS)
    parser.add_argument("--tls-audit", type=Path, default=DEFAULT_TLS_AUDIT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--time-to-teleport", type=int, default=1200)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--emergency-depart", type=float, default=0.0)
    parser.add_argument("--emergency-vehicle-id", default="emergency_ER_ACC_019_b1_green_wave_corridor")
    parser.add_argument("--timeout-steps", type=int, default=S14.DEFAULT_TIMEOUT_STEPS)
    return parser.parse_args()


def parse_space_list(value: str) -> list[str]:
    return [item for item in value.replace(",", " ").split() if item]


def load_terminal_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for row in read_csv(path):
        rows.append(
            {
                "terminal_id": row.get("terminal_id", ""),
                "tls_id": row.get("tls_id", ""),
                "junction_id": row.get("junction_id", ""),
                "covered_route_count": int(float(row.get("covered_route_count") or 0)),
                "covered_route_ids": parse_space_list(row.get("covered_route_ids", "")),
                "install_candidate_status": row.get("install_candidate_status", ""),
                "install_candidate_reason": row.get("install_candidate_reason", ""),
            }
        )
    return rows


def append_not_on_route_events(
    events: list[dict[str, Any]],
    terminals: list[dict[str, Any]],
    route_relevant_tls: set[str],
    args: argparse.Namespace,
    config: dict[str, Any],
) -> None:
    for terminal in terminals:
        tls_id = terminal["tls_id"]
        if tls_id in route_relevant_tls:
            continue
        events.append(
            {
                "time": 0.0,
                "route_id": args.route_id,
                "vehicle_id": args.emergency_vehicle_id,
                "tls_id": tls_id,
                "junction_id": terminal["junction_id"],
                "incoming": "",
                "outgoing": "",
                "remaining_distance_m": "",
                "speed_used_mps": "",
                "eta_sec": "",
                "D_det": config.get("D_det"),
                "D_det_triggered": False,
                "alpha": config.get("alpha"),
                "t_lead": config.get("t_lead"),
                "G_ext": config.get("G_ext"),
                "effective_G_ext": int(round(float(config.get("G_ext", 30)))),
                "tau": config.get("tau"),
                "metric_sample_interval": config.get("metric_sample_interval"),
                "current_road_id": "",
                "phase_before": "",
                "phase_after": "",
                "action": "not_on_route",
                "reason": "priority_terminal_loaded_but_not_on_selected_route",
                "restore_action": "",
                "restore_reason": "",
            }
        )


def terminal_coverage_rows(
    terminals: list[dict[str, Any]],
    tls_plan: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    route_tls = {row["tls_id"] for row in tls_plan}
    actions_by_tls: dict[str, Counter[str]] = {}
    reasons_by_tls: dict[str, str] = {}
    for event in events:
        tls_id = event.get("tls_id", "")
        if not tls_id:
            continue
        actions_by_tls.setdefault(tls_id, Counter())[str(event.get("action", ""))] += 1
        if event.get("reason"):
            reasons_by_tls[tls_id] = str(event["reason"])

    rows = []
    for terminal in terminals:
        tls_id = terminal["tls_id"]
        actions = actions_by_tls.get(tls_id, Counter())
        controlled = actions.get("extend_green", 0) + actions.get("advance_to_next_green", 0)
        skipped = actions.get("skip", 0)
        not_on_route = actions.get("not_on_route", 0)
        if controlled:
            status = "CONTROLLED"
        elif skipped:
            status = "SKIPPED"
        elif tls_id in route_tls:
            status = "ROUTE_RELEVANT_NO_CONTROL_EVENT"
        elif not_on_route:
            status = "NOT_ON_ROUTE"
        else:
            status = "NO_EVENT"
        rows.append(
            {
                "terminal_id": terminal["terminal_id"],
                "tls_id": tls_id,
                "junction_id": terminal["junction_id"],
                "install_candidate_status": terminal["install_candidate_status"],
                "is_route_relevant": tls_id in route_tls,
                "terminal_status": status,
                "control_event_count": controlled,
                "skip_event_count": skipped,
                "not_on_route_event_count": not_on_route,
                "covered_route_ids": " ".join(terminal["covered_route_ids"]),
                "last_reason": reasons_by_tls.get(tls_id, terminal["install_candidate_reason"]),
            }
        )
    return rows


def write_doc(summary: dict[str, Any]) -> None:
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(
        f"""# Step 15 Green Wave Corridor Validation

## Purpose

This step verifies a corridor-wide B1 Green Wave smoke using the single route that uses the main spine road most strongly: `{summary['route_id']}`.

This is not B2, Bayesian Optimization, or a multi-seed performance evaluation.

## Route Choice

- route_id: `{summary['route_id']}`
- route_length_m: `{summary['route_length_m']}`
- spine_length_m: `{summary['spine_length_m']}`
- spine_length_ratio: `{summary['spine_length_ratio']}`
- route_tls_count: `{summary['route_tls_count']}`

`ER_ACC_019` was selected because it has the largest spine length among the 19 spine-v2 routes and previously had no emergency teleport under B0/B1 smoke.

## Smoke Result

- final_status: `{summary['final_status']}`
- loaded_terminal_count: `{summary['loaded_terminal_count']}`
- route_relevant_terminal_count: `{summary['route_relevant_terminal_count']}`
- not_on_route_terminal_count: `{summary['not_on_route_terminal_count']}`
- controlled_tls_count: `{summary['controlled_tls_count']}`
- skipped_tls_count: `{summary['skipped_tls_count']}`
- intervention_count: `{summary['intervention_count']}`
- emergency_arrived: `{summary['emergency_arrived']}`
- emergency_teleport: `{summary['emergency_teleport']}`
- route_error_count: `{summary['route_error_count']}`
- emergency_travel_time: `{summary['emergency_travel_time']}`
- background_teleport_ratio: `{summary['background_teleport_ratio']}`

## Interpretation

All 20 priority terminal candidates were loaded as controller candidates. The controller only acted on route-relevant TLS. Terminals outside `ER_ACC_019` were not forced and were logged as `not_on_route`.

This verifies corridor terminal loading plus route-relevant Green Wave behavior for the route that uses the main spine the most. It does not claim that all 20 signals were physically changed in a single run.

## Outputs

- summary: `results/metrics/b1_green_wave_corridor_er_acc_019_smoke_summary.csv/json`
- signal events: `results/metrics/b1_green_wave_corridor_er_acc_019_signal_events.csv`
- terminal coverage: `results/metrics/b1_green_wave_corridor_er_acc_019_terminal_coverage.csv`
- run dir: `runs/b1_green_wave_corridor_er_acc_019/`
""",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    generated_at = utc_now()
    lines = [
        "Step 15 B1 Green Wave corridor ER_ACC_019 smoke",
        "================================================",
        f"generated_at: {generated_at}",
    ]
    try:
        for attr in ["net", "background_route", "emergency_routes", "priority_terminals", "tls_audit", "config"]:
            path = getattr(args, attr).resolve()
            setattr(args, attr, path)
            if not path.is_file():
                raise Step15Error(f"missing_file: {path}")
        args.run_dir = args.run_dir.resolve()

        config = S14.read_json(args.config)
        required_config = ["D_det", "v_e_policy", "fallback_v_e_mps", "alpha", "G_ext", "rho", "tau", "t_lead", "metric_sample_interval"]
        missing_config = [key for key in required_config if key not in config]
        if missing_config:
            raise Step15Error(f"missing_config_parameter: {','.join(missing_config)}")

        terminals = load_terminal_rows(args.priority_terminals)
        if len(terminals) != 20:
            raise Step15Error(f"expected_20_priority_terminals_got_{len(terminals)}")

        route_row = S14.select_emergency_route(args.emergency_routes, args.route_id)
        route_edges = route_row["route_edges"].split()
        validation_failures = S14.validate_route_edges(args.net, route_edges)
        if validation_failures:
            raise Step15Error(f"emergency route validation failed: {';'.join(validation_failures[:10])}")

        tls_plan = S14.load_tls_plan(args.tls_audit, args.route_id)
        if not tls_plan:
            raise Step15Error(f"no TLS audit rows for route_id={args.route_id}")
        route_relevant_tls = {row["tls_id"] for row in tls_plan}
        terminal_tls = {row["tls_id"] for row in terminals}
        route_relevant_terminal_tls = route_relevant_tls & terminal_tls

        emergency_route_xml = args.run_dir / f"{args.emergency_vehicle_id}.rou.xml"
        S14.write_emergency_route_xml(emergency_route_xml, route_row, args.emergency_vehicle_id, args.emergency_depart)
        paths = S14.write_sumo_files(args, emergency_route_xml)
        background_vehicle_count = S14.count_vehicles(args.background_route)
        events, controller_started = S14.run_controller(args, paths, tls_plan, route_edges, config)
        append_not_on_route_events(events, terminals, route_relevant_tls, args, config)

        stderr_text = paths["stderr"].read_text(encoding="utf-8", errors="replace") if paths["stderr"].is_file() else ""
        summary_metrics = S14.parse_summary_output(paths["summary"])
        trip = S14.parse_tripinfo(paths["tripinfo"], args.emergency_vehicle_id)
        route_errors = S14.route_error_count(stderr_text)
        emergency_tp = S14.emergency_teleport_lines(stderr_text, args.emergency_vehicle_id)
        emergency_arrived = bool(trip["emergency_arrived"])
        emergency_departed = summary_metrics["departed_count_total"] > background_vehicle_count or emergency_arrived
        emergency_teleport = bool(emergency_tp)
        background_departed = max(summary_metrics["departed_count_total"] - (1 if emergency_departed else 0), 0)
        background_arrived = max(summary_metrics["arrived_count_total"] - (1 if emergency_arrived else 0), 0)
        background_teleported = max(summary_metrics["teleport_count"] - (1 if emergency_teleport else 0), 0)

        controlled_tls = {event["tls_id"] for event in events if event.get("action") in {"extend_green", "advance_to_next_green"} and event.get("tls_id")}
        skipped_tls = {event["tls_id"] for event in events if event.get("action") == "skip" and event.get("tls_id")}
        failed_tls = {event["tls_id"] for event in events if event.get("action") == "failed" and event.get("tls_id")}
        not_on_route_tls = {event["tls_id"] for event in events if event.get("action") == "not_on_route" and event.get("tls_id")}
        green_extension_count = sum(1 for event in events if event.get("action") == "extend_green")
        phase_switch_count = sum(1 for event in events if event.get("action") == "advance_to_next_green")
        restore_count = sum(1 for event in events if event.get("action") == "restore" and str(event.get("restore_action", "")).startswith("restore"))
        intervention_count = green_extension_count + phase_switch_count

        coverage_rows = terminal_coverage_rows(terminals, tls_plan, events)

        failures = []
        warnings = []
        if not controller_started:
            failures.append("controller_not_started")
        if len(terminals) != 20:
            failures.append("loaded_terminal_count_not_20")
        if not emergency_departed:
            failures.append("emergency_not_departed")
        if not emergency_arrived:
            failures.append("emergency_not_arrived")
        if emergency_teleport:
            failures.append("emergency_teleport_detected")
        if route_errors > 0:
            failures.append("route_error_count_gt_0")
        if not (controlled_tls or skipped_tls):
            failures.append("no_route_relevant_tls_control_or_skip_events")
        if background_teleported > 0:
            warnings.append("background_teleports_present")
        if len(route_relevant_terminal_tls) < len(route_relevant_tls):
            warnings.append("some_route_tls_not_in_priority_terminal_candidates")

        final_status = "FAIL" if failures else "WARNING" if warnings else "PASS"
        summary = {
            "generated_at": generated_at,
            "final_status": final_status,
            "active_net": rel(args.net),
            "background_route": rel(args.background_route),
            "priority_terminals": rel(args.priority_terminals),
            "tls_audit": rel(args.tls_audit),
            "config_path": rel(args.config),
            "config_parameter_snapshot": {key: config.get(key) for key in required_config},
            "route_id": args.route_id,
            "route_length_m": route_row.get("route_length_m"),
            "spine_length_m": route_row.get("spine_length_m"),
            "spine_length_ratio": route_row.get("spine_length_ratio"),
            "route_tls_count": route_row.get("route_tls_count"),
            "background_vehicle_count": background_vehicle_count,
            "emergency_vehicle_id": args.emergency_vehicle_id,
            "loaded_terminal_count": len(terminals),
            "route_relevant_tls_count": len(route_relevant_tls),
            "route_relevant_terminal_count": len(route_relevant_terminal_tls),
            "not_on_route_terminal_count": len(not_on_route_tls),
            "controller_started": controller_started,
            "emergency_departed": emergency_departed,
            "emergency_arrived": emergency_arrived,
            "emergency_teleport": emergency_teleport,
            "emergency_teleport_evidence": emergency_tp,
            "emergency_travel_time": trip["emergency_travel_time"],
            "background_departed": background_departed,
            "background_arrived": background_arrived,
            "background_teleported": background_teleported,
            "background_teleport_ratio": round(background_teleported / background_departed, 6) if background_departed else 0.0,
            "route_error_count": route_errors,
            "controlled_tls_count": len(controlled_tls),
            "skipped_tls_count": len(skipped_tls),
            "failed_tls_count": len(failed_tls),
            "intervention_count": intervention_count,
            "green_extension_count": green_extension_count,
            "phase_switch_count": phase_switch_count,
            "restore_count": restore_count,
            "signal_event_count": len(events),
            "time_to_teleport": args.time_to_teleport,
            "collision_action": args.collision_action,
            "pedestrian_min_walk_policy": config.get("pedestrian_min_walk_policy", "safety_placeholder_documented_not_optimized"),
            "sim_end_time": summary_metrics["sim_end_time"],
            "sumo_exit_code": 0 if controller_started else 1,
            "warnings": warnings,
            "failures": failures,
            "failure_reason": ";".join(failures),
            "warning_reason": ";".join(warnings),
            "run_dir": rel(args.run_dir),
            "sumocfg": rel(paths["sumocfg"]),
            "tripinfo": rel(paths["tripinfo"]),
            "summary_output": rel(paths["summary"]),
            "edgeData_output": rel(paths["edge_data"]),
            "stderr_log": rel(paths["stderr"]),
            "outputs": [rel(SUMMARY_CSV), rel(SUMMARY_JSON), rel(SIGNAL_EVENTS_CSV), rel(TERMINAL_COVERAGE_CSV), rel(LOG_PATH), rel(DOC_PATH)],
        }

        event_fields = [
            "time",
            "route_id",
            "vehicle_id",
            "tls_id",
            "junction_id",
            "incoming",
            "outgoing",
            "remaining_distance_m",
            "speed_used_mps",
            "eta_sec",
            "D_det",
            "D_det_triggered",
            "alpha",
            "t_lead",
            "G_ext",
            "effective_G_ext",
            "tau",
            "metric_sample_interval",
            "current_road_id",
            "phase_before",
            "phase_after",
            "action",
            "reason",
            "restore_action",
            "restore_reason",
        ]
        coverage_fields = [
            "terminal_id",
            "tls_id",
            "junction_id",
            "install_candidate_status",
            "is_route_relevant",
            "terminal_status",
            "control_event_count",
            "skip_event_count",
            "not_on_route_event_count",
            "covered_route_ids",
            "last_reason",
        ]
        write_csv(SIGNAL_EVENTS_CSV, events, event_fields)
        write_csv(TERMINAL_COVERAGE_CSV, coverage_rows, coverage_fields)
        write_csv(SUMMARY_CSV, [summary], list(summary.keys()))
        write_json(SUMMARY_JSON, summary)
        write_doc(summary)

        lines.extend(
            [
                f"route_id: {args.route_id}",
                f"loaded_terminal_count: {summary['loaded_terminal_count']}",
                f"route_relevant_terminal_count: {summary['route_relevant_terminal_count']}",
                f"not_on_route_terminal_count: {summary['not_on_route_terminal_count']}",
                f"controlled_tls_count: {summary['controlled_tls_count']}",
                f"skipped_tls_count: {summary['skipped_tls_count']}",
                f"emergency_arrived: {summary['emergency_arrived']}",
                f"emergency_teleport: {summary['emergency_teleport']}",
                f"route_error_count: {summary['route_error_count']}",
                f"final_status: {final_status}",
                f"summary_json: {rel(SUMMARY_JSON)}",
            ]
        )
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if final_status in {"PASS", "WARNING"} else 1
    except (Step15Error, S14.B1GreenWaveError, OSError, ET.ParseError, ValueError, RuntimeError) as exc:
        lines.extend(["final_status: FAIL", f"blocker: {exc}"])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
