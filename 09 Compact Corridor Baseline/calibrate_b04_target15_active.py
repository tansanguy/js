#!/usr/bin/env python3
"""Calibrate active B04 demand to 15 km/h EV speed including stops."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = Path(__file__).resolve().parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from b4_runtime import B04_MODE, safe_float  # noqa: E402
from run_b0_b4_signal_pipeline import (  # noqa: E402
    B4RunTask,
    B4RuntimePhaseConfig,
    build_b004_free_reference,
    read_free_vehicle_rows,
    run_b04_task,
    validate_static_inputs,
)


DEFAULT_NET = PIPELINE_DIR / "tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
DEFAULT_BASE_DEMAND = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml"
DEFAULT_FINAL_DEMAND = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_active15.rou.xml"
DEFAULT_STAGE1_DIR = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1_s1forced"
DEFAULT_OUTPUT_DIR = PIPELINE_DIR / "tdata_signal/active_target15_recalibration"
DEFAULT_METRICS_ROOT = PROJECT_ROOT / "results/metrics/compact_v9_B4_active_target15_recalibration"
DEFAULT_RUN_ROOT = PROJECT_ROOT / "runs/compact_v9_B4_active_target15_recalibration"

RESULT_FIELDS = [
    "label",
    "scale",
    "demand_file",
    "vehicle_count",
    "base_vehicle_count",
    "run_id",
    "final_status",
    "failure_reason",
    "T_actual_EMV_sec",
    "route_length_m",
    "ev_speed_kmh",
    "speed_error_kmh",
    "background_departed",
    "background_arrived",
    "background_arrived_ratio",
    "general_mean_delay_sec",
    "metrics_csv",
    "run_dir",
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


def stable_unit(value: str, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / float(16 ** 16 - 1)


def vehicle_depart(vehicle: ET.Element) -> float:
    return safe_float(vehicle.get("depart"), 0.0)


def route_prefix(vehicle: ET.Element) -> str:
    return str(vehicle.get("route", "")).split("_")[0] or "unknown"


def build_scaled_demand(base_demand: Path, output_demand: Path, scale: float, seed: int) -> dict[str, Any]:
    if not 0.0 < scale <= 1.0:
        raise ValueError(f"scale_must_be_0_to_1:{scale}")
    tree = ET.parse(base_demand)
    root = tree.getroot()
    vehicles = list(root.findall("vehicle"))
    for vehicle in vehicles:
        root.remove(vehicle)
    kept = [
        vehicle
        for vehicle in vehicles
        if stable_unit(str(vehicle.get("id", "")), seed) <= scale
    ]
    kept.sort(key=lambda item: (vehicle_depart(item), str(item.get("id", ""))))
    for vehicle in kept:
        root.append(vehicle)
    output_demand.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_demand, encoding="UTF-8", xml_declaration=True)
    bins = Counter(int(vehicle_depart(vehicle) // 300) * 300 for vehicle in kept)
    prefixes = Counter(route_prefix(vehicle) for vehicle in kept)
    summary = {
        "schema": "compact_v9_B04_active_target15_scaled_demand.v1",
        "generated_at": utc_now(),
        "base_demand": rel(base_demand),
        "output_demand": rel(output_demand),
        "scale": scale,
        "seed": seed,
        "base_vehicle_count": len(vehicles),
        "vehicle_count": len(kept),
        "vehicle_count_ratio": round(len(kept) / max(len(vehicles), 1), 6),
        "depart_300s_bins": {str(key): bins[key] for key in sorted(bins)},
        "route_prefix_counts": dict(prefixes.most_common()),
    }
    write_json(output_demand.with_suffix(".summary.json"), summary)
    return summary


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def emergency_route_length(tripinfo: Path) -> float:
    if not tripinfo.is_file():
        return 0.0
    try:
        for _event, elem in ET.iterparse(tripinfo, events=("end",)):
            if elem.tag == "tripinfo" and elem.get("id") == "emergency_0":
                return safe_float(elem.get("routeLength"))
            elem.clear()
    except ET.ParseError:
        return 0.0
    return 0.0


def run_b04_candidate(args: argparse.Namespace, label: str, demand: Path, summary: dict[str, Any]) -> dict[str, Any]:
    run_id = f"{args.run_id}_{label}"
    metrics_dir = args.metrics_root / run_id
    stage1 = validate_static_inputs(
        stage1_dir=args.stage1_dir,
        net_file=args.net_file,
        background_route=demand,
    )
    phase_config = B4RuntimePhaseConfig.from_phase(args.phase)
    if args.hard_max_sim_time is not None:
        phase_config = replace(phase_config, hard_max_sim_time=float(args.hard_max_sim_time))
    free_reference = build_b004_free_reference(stage1, net_file=args.net_file, background_route=demand)
    free_rows_by_id = {row["vehicle_id"]: row for row in read_free_vehicle_rows()}
    run_dir = args.run_root / run_id / B04_MODE / "no_control" / "repeat_001"
    task = B4RunTask(
        run_id=run_id,
        mode=B04_MODE,
        parameter_id="no_control",
        repeat_id=1,
        seed=args.seed,
        run_dir=run_dir,
        net_file=args.net_file,
        background_route=demand,
    )
    row = run_b04_task(task, stage1, phase_config, free_reference, free_rows_by_id, args.sumo_binary, False)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    metrics_csv = metrics_dir / "b04_result_row.csv"
    write_csv(metrics_csv, [row], list(row.keys()))
    write_json(metrics_dir / "b04_result_row.json", row)
    route_length = emergency_route_length(run_dir / "tripinfo.xml")
    ev_time = safe_float(row.get("T_actual_EMV_sec"))
    ev_speed = route_length / ev_time * 3.6 if route_length > 0.0 and ev_time > 0.0 else 0.0
    return {
        "label": label,
        "scale": summary["scale"],
        "demand_file": rel(demand),
        "vehicle_count": summary["vehicle_count"],
        "base_vehicle_count": summary["base_vehicle_count"],
        "run_id": run_id,
        "final_status": row.get("final_status", ""),
        "failure_reason": row.get("failure_reason", ""),
        "T_actual_EMV_sec": row.get("T_actual_EMV_sec", ""),
        "route_length_m": round(route_length, 6),
        "ev_speed_kmh": round(ev_speed, 6),
        "speed_error_kmh": round(ev_speed - args.target_speed_kmh, 6),
        "background_departed": row.get("background_departed", ""),
        "background_arrived": row.get("background_arrived", ""),
        "background_arrived_ratio": row.get("background_arrived_ratio", ""),
        "general_mean_delay_sec": row.get("general_mean_delay_sec", ""),
        "metrics_csv": rel(metrics_csv),
        "run_dir": rel(run_dir),
    }


def demand_path_for_scale(output_dir: Path, scale: float) -> Path:
    label = f"s{int(round(scale * 1000)):03d}"
    return output_dir / f"background_routes_compact_v9_B04_target15_active_{label}.rou.xml"


def candidate_scales(args: argparse.Namespace) -> list[float]:
    if args.scales:
        values = [float(item) for text in args.scales for item in text.split(",") if item.strip()]
    else:
        values = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00]
    return sorted({round(value, 4) for value in values if 0.0 < value <= 1.0})


def choose_best(rows: list[dict[str, Any]], target_speed: float) -> dict[str, Any]:
    pass_rows = [row for row in rows if row.get("final_status") == "PASS" and safe_float(row.get("ev_speed_kmh")) > 0.0]
    candidates = pass_rows or rows
    return min(candidates, key=lambda row: abs(safe_float(row.get("ev_speed_kmh")) - target_speed))


def copy_best_demand(best: dict[str, Any], final_demand: Path) -> None:
    source = PROJECT_ROOT / str(best["demand_file"])
    tree = ET.parse(source)
    final_demand.parent.mkdir(parents=True, exist_ok=True)
    tree.write(final_demand, encoding="UTF-8", xml_declaration=True)
    source_summary = source.with_suffix(".summary.json")
    if source_summary.is_file():
        payload = json.loads(source_summary.read_text(encoding="utf-8"))
    else:
        payload = {}
    payload.update({
        "schema": "compact_v9_B04_active_target15_final_demand.v1",
        "generated_at": utc_now(),
        "source_scaled_demand": rel(source),
        "output_demand": rel(final_demand),
        "selected_run": best,
    })
    write_json(final_demand.with_suffix(".summary.json"), payload)


def calibrate(args: argparse.Namespace) -> dict[str, Any]:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for scale in candidate_scales(args):
        label = f"s{int(round(scale * 1000)):03d}"
        demand = demand_path_for_scale(args.output_dir, scale)
        summary = build_scaled_demand(args.base_demand, demand, scale, args.seed)
        rows.append(run_b04_candidate(args, label, demand, summary))
        write_csv(args.output_dir / "active_target15_calibration_results.partial.csv", rows, RESULT_FIELDS)
    best = choose_best(rows, args.target_speed_kmh)
    copy_best_demand(best, args.final_demand)
    result_csv = args.output_dir / "active_target15_calibration_results.csv"
    result_json = args.output_dir / "active_target15_calibration_summary.json"
    write_csv(result_csv, rows, RESULT_FIELDS)
    payload = {
        "schema": "compact_v9_B04_active_target15_calibration.v1",
        "generated_at": utc_now(),
        "target_speed_kmh": args.target_speed_kmh,
        "net_file": rel(args.net_file),
        "base_demand": rel(args.base_demand),
        "final_demand": rel(args.final_demand),
        "stage1_dir": rel(args.stage1_dir) if args.stage1_dir else "",
        "best": best,
        "rows": rows,
        "result_csv": rel(result_csv),
    }
    write_json(result_json, payload)
    return {**payload, "result_json": rel(result_json)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate active B04 demand to 15 km/h EV speed.")
    parser.add_argument("--net-file", type=Path, default=DEFAULT_NET)
    parser.add_argument("--base-demand", type=Path, default=DEFAULT_BASE_DEMAND)
    parser.add_argument("--final-demand", type=Path, default=DEFAULT_FINAL_DEMAND)
    parser.add_argument("--stage1-dir", type=Path, default=DEFAULT_STAGE1_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--metrics-root", type=Path, default=DEFAULT_METRICS_ROOT)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--run-id", default="active_target15")
    parser.add_argument("--phase", default="bo-smoke")
    parser.add_argument("--hard-max-sim-time", type=float, default=4000.0)
    parser.add_argument("--target-speed-kmh", type=float, default=15.0)
    parser.add_argument("--seed", type=int, default=20260606)
    parser.add_argument("--scales", action="append", default=[])
    parser.add_argument("--sumo-binary", default=None)
    args = parser.parse_args(argv)
    args.net_file = args.net_file.resolve()
    args.base_demand = args.base_demand.resolve()
    args.final_demand = args.final_demand.resolve()
    args.stage1_dir = args.stage1_dir.resolve() if args.stage1_dir else None
    args.output_dir = args.output_dir.resolve()
    args.metrics_root = args.metrics_root.resolve()
    args.run_root = args.run_root.resolve()
    for path in [args.net_file, args.base_demand]:
        if not path.is_file():
            raise FileNotFoundError(path)
    if args.stage1_dir is not None and not args.stage1_dir.is_dir():
        raise FileNotFoundError(args.stage1_dir)
    return args


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(calibrate(parse_args(argv)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
