#!/usr/bin/env python3
"""Build a B4 Stage1 directory from a concrete B04 no-control run."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = Path(__file__).resolve().parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import b04_baseline_pipeline as b04  # noqa: E402
import b4_stage1_pipeline as stage1  # noqa: E402


DEFAULT_STAGE1_DIR = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1_s1forced"
DEFAULT_NET = PIPELINE_DIR / "tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
DEFAULT_DEMAND = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml"
DEFAULT_RUN_DIR = PROJECT_ROOT / "results/metrics/compact_v9_B04/B04_ad_stage23_trigger/run"

SPEED_FIELDS = [
    "segment_id", "direction", "reference_speed_kmh", "simulated_speed_kmh",
    "speed_error_kmh", "edgeData_speed_kmh", "reference_travel_time_s", "simulated_travel_time_s",
    "travel_time_error_s", "edgeData_travel_time_s", "reference_volume_vph", "observed_count", "volume_error",
    "edgeData_observed_count", "screenline_edge", "screenline_count",
    "raw_edge_count_sum", "edge_count", "max_density", "max_occupancy",
    "fcd_traversal_sample_count", "fcd_traversal_time_median_s", "fcd_traversal_time_p75_s",
    "runtime_queue_max_m", "runtime_slow_count_max", "runtime_halted_count_max", "runtime_density_max",
    "runtime_occupancy_max", "runtime_waiting_or_timeloss_max", "low_speed_interval_count",
    "target_queue_proxy", "sumo_queue_proxy", "class",
]

DIAGNOSTIC_FIELDS = [
    "candidate", "segment_id", "direction", "mapped_S_segment", "queue_state",
    "signal_presence_status", "simulated_speed_kmh", "target_congestion_proxy",
    "segment_congestion_proxy", "max_stopline_queue_fill_ratio", "fill_ratio_ge_0p50",
    "fast_dense_no_queue_index", "queue_compression_gap", "approach_conflict_proxy",
    "signal_too_generous_score", "exit_too_easy_score", "diagnosis", "measurement_note",
]

RUN_SUMMARY_FIELDS = [
    "edgeData",
    "laneData",
    "tripinfo",
    "net_file",
    "background_route",
    "source_run_id",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def tripinfo_summary(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "tripinfo": rel(path),
        "vehicle_count": 0,
        "emergency_arrived": False,
        "emergency_duration_sec": 0.0,
        "emergency_route_length_m": 0.0,
        "emergency_speed_kmh": 0.0,
    }
    if not path.is_file():
        return summary
    try:
        for _event, elem in ET.iterparse(path, events=("end",)):
            if elem.tag != "tripinfo":
                elem.clear()
                continue
            summary["vehicle_count"] += 1
            if elem.get("id") == "emergency_0":
                duration = safe_float(elem.get("duration"))
                route_length = safe_float(elem.get("routeLength"))
                summary.update({
                    "emergency_arrived": True,
                    "emergency_duration_sec": round(duration, 6),
                    "emergency_route_length_m": round(route_length, 6),
                    "emergency_speed_kmh": round(route_length / duration * 3.6, 6) if duration > 0.0 else 0.0,
                })
            elem.clear()
    except ET.ParseError:
        summary["parse_error"] = True
    return summary


def queue_state(row: dict[str, Any]) -> str:
    speed = safe_float(row.get("simulated_speed_kmh"))
    density = safe_float(row.get("max_density"), safe_float(row.get("runtime_density_max")))
    occupancy = safe_float(row.get("max_occupancy"), safe_float(row.get("runtime_occupancy_max")))
    waiting = safe_float(row.get("runtime_waiting_or_timeloss_max"))
    if speed <= 0.0:
        return "missing"
    if speed <= 15.0 and (density >= 20.0 or occupancy >= 12.0 or waiting >= 30.0):
        return "physical_queue"
    if speed <= 15.0:
        return "speed_only_runtime_check_required"
    if speed <= 25.0:
        return "traffic_pressure"
    return "free_flow"


def build_speed_and_diagnostics(
    candidate: str,
    edge_data_path: Path,
    lane_data_path: Path,
    speed_csv: Path,
    diagnostics_csv: Path,
) -> dict[str, Any]:
    edge_data = b04.edge_data_by_edge(edge_data_path)
    lane_data = b04.lane_data_by_edge(lane_data_path)
    measurement = b04.lightweight_segment_metrics(edge_data, lane_data)
    speed_rows = b04.segment_speed_rows(edge_data, measurement.get("segments", {}))
    write_csv(speed_csv, speed_rows, SPEED_FIELDS)
    diagnostic_rows = []
    for row in speed_rows:
        state = queue_state(row)
        diagnostic_rows.append({
            "candidate": candidate,
            "segment_id": row.get("segment_id", ""),
            "direction": row.get("direction", ""),
            "mapped_S_segment": f"{row.get('segment_id', '')}:{row.get('direction', '')}",
            "queue_state": state,
            "signal_presence_status": "runtime_stage1_source",
            "simulated_speed_kmh": row.get("simulated_speed_kmh", ""),
            "target_congestion_proxy": row.get("target_queue_proxy", ""),
            "segment_congestion_proxy": row.get("sumo_queue_proxy", ""),
            "max_stopline_queue_fill_ratio": "",
            "fill_ratio_ge_0p50": "",
            "fast_dense_no_queue_index": "",
            "queue_compression_gap": "",
            "approach_conflict_proxy": "",
            "signal_too_generous_score": "",
            "exit_too_easy_score": "",
            "diagnosis": state,
            "measurement_note": "Active B04 run edgeData/laneData proxy; no field-observed queue length.",
        })
    write_csv(diagnostics_csv, diagnostic_rows, DIAGNOSTIC_FIELDS)
    return {
        "speed_csv": rel(speed_csv),
        "diagnostics_csv": rel(diagnostics_csv),
        "segment_count": len(speed_rows),
        "speed_le_15_count": sum(1 for row in speed_rows if 0.0 < safe_float(row.get("simulated_speed_kmh")) <= 15.0),
        "physical_queue_like_count": sum(1 for row in diagnostic_rows if row.get("queue_state") == "physical_queue"),
    }


def configure_stage1_module(
    output_dir: Path,
    candidate: str,
    net_file: Path,
    run_summary: Path,
    speed_csv: Path,
    diagnostics_csv: Path,
) -> None:
    stage1.STAGE1_DIR = output_dir
    stage1.B04_NET = net_file
    stage1.B4_PRIMARY_CANDIDATE = candidate
    stage1.B4_PRIMARY_RUN_SUMMARY = run_summary
    stage1.B4_PRIMARY_SPEED_RECALL = speed_csv
    stage1.B04_MEASUREMENT_DIAGNOSTICS = diagnostics_csv
    stage1.STAGE2_MEASUREMENT_SOURCE = f"SUMO_{candidate}_laneData_edgeData_proxy"
    stage1.B4_PRIMARY_EDGE_LANE_SOURCE = f"SUMO_{candidate}_edge_lane_data"
    stage1.B4_PRIMARY_B0_MEASURED_PROXY = f"SUMO_{candidate}_measured_proxy"
    stage1.B4_ROUTE_MOVEMENT_PLAN = output_dir / "b4_route_movement_plan.json"
    stage1.B4_INTERSECTIONS_CSV = output_dir / "b4_intersections.csv"
    stage1.B4_APPROACH_STORAGE_LINK_PLAN_CSV = output_dir / "b4_approach_storage_link_plan.csv"
    stage1.B4_MERGE_ZONE = output_dir / "b4_merge_zone.json"
    stage1.B4_DEPARTURE_FLOW_PLAN = output_dir / "b4_departure_flow_plan.json"
    stage1.B4_BOTTLENECK_QUEUE_READINESS_CSV = output_dir / "b4_bottleneck_queue_readiness.csv"
    stage1.B4_CASE_B_CANDIDATES_CSV = output_dir / "b4_case_b_candidates.csv"
    stage1.B4_CASE_B_CANDIDATES_JSON = output_dir / "b4_case_b_candidates.json"
    stage1.B4_CONTROL_QUEUE_THRESHOLD_PROPOSAL = output_dir / "b4_control_queue_threshold_proposal.json"
    stage1.B4_B0_MEASURED_SIGNAL_PARAMS_CSV = output_dir / "b4_b0_measured_signal_params.csv"
    stage1.B4_TA_PROXY_POLICY = output_dir / "b4_ta_proxy_policy.json"
    stage1.B4_STAGE2_B0_MERGE_HOLD_PARAMS_JSON = output_dir / "b4_stage2_b0_merge_hold_params.json"
    stage1.B4_STAGE2_B0_MERGE_HOLD_PARAMS_CSV = output_dir / "b4_stage2_b0_merge_hold_params.csv"
    stage1.B4_RUNTIME_INDEX = output_dir / "b4_runtime_index.json"
    stage1.B4_STAGE1_SUMMARY = output_dir / "b4_stage1_summary.json"
    stage1.B4_STAGE1_REVIEW_HTML = output_dir / "b4_stage1_review.html"


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return payload if isinstance(payload, dict) else {}


def update_json(path: Path, extra: dict[str, Any]) -> dict[str, Any]:
    payload = load_json_if_exists(path)
    payload.update(extra)
    write_json(path, payload)
    return payload


def build(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.stage1_dir.resolve()
    source_dir = output_dir / "_active_b04_sources"
    edge_data = args.edge_data.resolve()
    lane_data = args.lane_data.resolve()
    tripinfo = args.tripinfo.resolve()
    net_file = args.net_file.resolve()
    background_route = args.background_route.resolve()
    for path in [edge_data, lane_data, tripinfo, net_file, background_route]:
        if not path.is_file():
            raise FileNotFoundError(path)

    speed_csv = source_dir / "B04_segment_speed_recall.csv"
    diagnostics_csv = source_dir / "b4_queue_measurement_diagnostics.csv"
    run_summary = source_dir / "b0_run_summary.json"
    measurement_summary = build_speed_and_diagnostics(args.primary_candidate, edge_data, lane_data, speed_csv, diagnostics_csv)
    run_payload = {
        "schema": "compact_v9_active_B04_run_summary_for_B4_stage1.v1",
        "generated_at": utc_now(),
        "candidate": args.primary_candidate,
        "source_run_id": args.source_run_id,
        "edgeData": rel(edge_data),
        "laneData": rel(lane_data),
        "tripinfo": rel(tripinfo),
        "net_file": rel(net_file),
        "background_route": rel(background_route),
        "tripinfo_summary": tripinfo_summary(tripinfo),
        "measurement_summary": measurement_summary,
    }
    write_json(run_summary, run_payload)

    configure_stage1_module(output_dir, args.primary_candidate, net_file, run_summary, speed_csv, diagnostics_csv)
    summary = stage1.build_b4_stage1()

    provenance = {
        "source": "active_B04_no_control_run",
        "source_run_id": args.source_run_id,
        "net_file": rel(net_file),
        "background_route": rel(background_route),
        "edgeData": rel(edge_data),
        "laneData": rel(lane_data),
        "tripinfo": rel(tripinfo),
        "source_run_summary": rel(run_summary),
        "segment_speed_recall": rel(speed_csv),
        "queue_measurement_diagnostics": rel(diagnostics_csv),
        "tripinfo_summary": run_payload["tripinfo_summary"],
    }
    override_payload = {
        "allow_runtime_input_override": True,
        "runtime_input_provenance": provenance,
    }
    runtime_index = update_json(output_dir / "b4_runtime_index.json", override_payload)
    stage2 = update_json(output_dir / "b4_stage2_b0_merge_hold_params.json", {
        "runtime_input_provenance": provenance,
        "runtime_dependency": "runtime_n_occ_and_Lq_merge_primary",
    })
    summary_payload = load_json_if_exists(output_dir / "b4_stage1_summary.json")
    input_artifacts = dict(summary_payload.get("input_artifacts", {}))
    input_artifacts.update({
        "b04_net": rel(net_file),
        "background_route": rel(background_route),
        "edgeData": rel(edge_data),
        "laneData": rel(lane_data),
        "tripinfo": rel(tripinfo),
        "primary_run_summary": rel(run_summary),
        "segment_speed_recall": rel(speed_csv),
        "b4_queue_measurement_diagnostics": rel(diagnostics_csv),
    })
    notes = list(summary_payload.get("policy_notes", []))
    notes.append("This Stage1 directory is bound to an active B04 run; use it with matching --net-file and --background-route.")
    notes.append("Stage2 merge B0 values are provenance/fallback only when weak; runtime n_occ/Lq is the primary intervention signal.")
    summary_payload.update({
        **override_payload,
        "primary_candidate": args.primary_candidate,
        "manifest_selected_candidate": args.primary_candidate,
        "manifest_selected_candidate_role": "active_runtime_override",
        "provenance_status": "PASS",
        "provenance_note": "Stage1 is intentionally bound to the active B04 no-control run identified by runtime_input_provenance, not the default manifest-selected candidate.",
        "input_artifacts": input_artifacts,
        "policy_notes": notes,
    })
    lock = dict(summary_payload.get("primary_candidate_lock", {}))
    lock.update({
        "primary_candidate": args.primary_candidate,
        "manifest_selected_candidate": args.primary_candidate,
        "manifest_selected_candidate_role": "active_runtime_override",
        "reason": "Active B04 no-control provenance is locked for this Stage1 directory.",
    })
    summary_payload["primary_candidate_lock"] = lock
    write_json(output_dir / "b4_stage1_summary.json", summary_payload)
    return {
        "schema": "compact_v9_B4_stage1_from_active_B04_run.v1",
        "generated_at": utc_now(),
        "stage1_dir": rel(output_dir),
        "source_run_id": args.source_run_id,
        "provenance": provenance,
        "measurement_summary": measurement_summary,
        "stage2_b0_merge_support_status": stage2.get("b0_merge_support_status", stage2.get("params", {}).get("b0_merge_support_status", "")),
        "runtime_index": rel(output_dir / "b4_runtime_index.json"),
        "runtime_index_algorithm": runtime_index.get("algorithm", ""),
        "summary_json": rel(output_dir / "b4_stage1_summary.json"),
        "stage1_status": summary.get("status", ""),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build B4 Stage1 from an active B04 no-control run.")
    parser.add_argument("--stage1-dir", type=Path, default=DEFAULT_STAGE1_DIR)
    parser.add_argument("--net-file", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_DEMAND)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--edge-data", type=Path, default=None)
    parser.add_argument("--lane-data", type=Path, default=None)
    parser.add_argument("--tripinfo", type=Path, default=None)
    parser.add_argument("--primary-candidate", default="B04_ad_stage23_trigger")
    parser.add_argument("--source-run-id", default="")
    args = parser.parse_args(argv)
    args.run_dir = args.run_dir.resolve()
    args.edge_data = (args.edge_data or (args.run_dir / "edgeData.xml")).resolve()
    args.lane_data = (args.lane_data or (args.run_dir / "laneData.xml")).resolve()
    args.tripinfo = (args.tripinfo or (args.run_dir / "tripinfo.xml")).resolve()
    if not args.source_run_id:
        args.source_run_id = args.run_dir.parent.parent.parent.name if len(args.run_dir.parts) >= 4 else args.primary_candidate
    return args


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(build(parse_args(argv)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
