#!/usr/bin/env python3
"""Calibrate u130-derived demand to 15 km/h for regular Toegye-ro traffic.

This keeps the u130 total demand scale and only redistributes the synthetic
``target15_u`` clone vehicles in time and, optionally, across existing route
families.  The calibration target is not EV travel speed.  It is the B04
edgeData speed over the EV-route Toegye movements, weighted by movement entered
count so arrived-only tripinfo bias does not dominate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
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
DEFAULT_BASE_DEMAND = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml"
DEFAULT_FINAL_DEMAND = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130_toegye15.rou.xml"
DEFAULT_STAGE1_DIR = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1_s1forced"
DEFAULT_OUTPUT_DIR = PIPELINE_DIR / "tdata_signal/u130_toegye_general15"
DEFAULT_METRICS_ROOT = PROJECT_ROOT / "results/metrics/compact_v9_B4_u130_toegye_general15"
DEFAULT_RUN_ROOT = PROJECT_ROOT / "runs/compact_v9_B4_u130_toegye_general15"

RESULT_FIELDS = [
    "label",
    "variant_kind",
    "time_profile",
    "spatial_shift",
    "vehicle_count",
    "clone_count",
    "route_changed_count",
    "time_changed_count",
    "run_id",
    "final_status",
    "failure_reason",
    "T_actual_EMV_sec",
    "background_departed",
    "background_arrived",
    "background_arrived_ratio",
    "general_mean_delay_sec",
    "toegye_entered_weighted_speed_kmh",
    "toegye_sampled_weighted_speed_kmh",
    "toegye_movement_simple_mean_kmh",
    "toegye_movement_median_kmh",
    "toegye_slow_movement_count_le10",
    "toegye_slow_movement_count_le15",
    "toegye_fast_movement_count_gt60",
    "toegye_entered_total",
    "toegye_sampled_seconds_total",
    "score_abs_error",
    "demand_file",
    "metrics_csv",
    "run_dir",
]

HOTSPOT_SEGMENTS = {"S6", "S7", "S8", "S9", "S10", "S14", "S15"}
COOL_SEGMENTS = {"S2", "S3", "S5", "S11", "S12", "S13", "S16", "S17", "S18", "S19", "S20", "S21"}


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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def stable_unit(value: str, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / float(16**16 - 1)


def stable_index(value: str, seed: int, modulo: int) -> int:
    if modulo <= 0:
        return 0
    return int(stable_unit(value, seed) * modulo) % modulo


def vehicle_depart(vehicle: ET.Element) -> float:
    return safe_float(vehicle.get("depart"), 0.0)


def clone_vehicle_attrs(vehicle: ET.Element) -> bool:
    return "_target15_u" in str(vehicle.get("id", ""))


def route_direction(route_id: str) -> str:
    if "upbound" in route_id or "_up_" in route_id:
        return "upbound"
    if "downbound" in route_id or "_down_" in route_id:
        return "downbound"
    return "other"


def route_segment(route_id: str) -> str:
    marker = "_S"
    if marker not in route_id:
        return ""
    suffix = route_id.rsplit(marker, 1)[-1]
    digits = []
    for char in suffix:
        if char.isdigit():
            digits.append(char)
        else:
            break
    return f"S{''.join(digits)}" if digits else ""


def route_prefix(route_id: str) -> str:
    return route_id.split("_", 1)[0] if route_id else "unknown"


def bounded_depart(value: float, begin: float = 120.0, end: float = 3900.0) -> float:
    return max(begin, min(end, value))


def profiled_depart(index: int, total: int, profile: str, seed: int) -> float:
    unit = (index + 0.5) / max(total, 1)
    jitter = (stable_unit(f"{profile}:{index}", seed) - 0.5) * 24.0
    if profile == "keep":
        raise ValueError("keep profile should not call profiled_depart")
    if profile == "even":
        return bounded_depart(120.0 + unit * (3900.0 - 120.0) + jitter)
    if profile == "peak_soft":
        return bounded_depart(420.0 + unit * (1800.0 - 420.0) + jitter)
    if profile == "shoulder":
        # Push part of clone pressure away from the 600-1200 EV approach window
        # without reducing the u130 vehicle count.
        if unit < 0.35:
            local = unit / 0.35
            return bounded_depart(120.0 + local * (600.0 - 120.0) + jitter)
        local = (unit - 0.35) / 0.65
        return bounded_depart(1500.0 + local * (3900.0 - 1500.0) + jitter)
    if profile == "late_broad":
        return bounded_depart(600.0 + unit * (3900.0 - 600.0) + jitter)
    raise ValueError(f"unknown_time_profile:{profile}")


def partial_time_profile(profile: str) -> tuple[str, float] | None:
    if "_p" not in profile:
        return None
    base, pct_text = profile.rsplit("_p", 1)
    if base not in {"shoulder", "late_broad", "peak_soft", "even"}:
        return None
    try:
        fraction = float(pct_text) / 100.0
    except ValueError:
        return None
    return base, max(0.0, min(1.0, fraction))


def parse_routes(root: ET.Element) -> dict[str, list[str]]:
    routes: dict[str, list[str]] = {}
    for route in root.findall("route"):
        route_id = route.get("id", "")
        if route_id:
            routes[route_id] = [edge for edge in str(route.get("edges", "")).split() if edge]
    return routes


def route_pools(routes: dict[str, list[str]], vehicles: list[ET.Element]) -> dict[str, list[str]]:
    used_routes = {str(vehicle.get("route", "")) for vehicle in vehicles}
    available = sorted(route_id for route_id in used_routes if route_id in routes)
    pools: dict[str, list[str]] = {
        "upbound_cool": [],
        "downbound_cool": [],
        "upbound_any": [],
        "downbound_any": [],
        "mainline_any": [],
        "midcorridor_any": [],
    }
    for route_id in available:
        direction = route_direction(route_id)
        segment = route_segment(route_id)
        if route_id.startswith("mainline_through_"):
            pools["mainline_any"].append(route_id)
        if route_id.startswith("midcorridor_"):
            pools["midcorridor_any"].append(route_id)
        if direction in {"upbound", "downbound"}:
            pools[f"{direction}_any"].append(route_id)
            if route_id.startswith("mainline_through_") or route_id.startswith("midcorridor_") or segment in COOL_SEGMENTS:
                pools[f"{direction}_cool"].append(route_id)
    return pools


def choose_spatial_route(route_id: str, pools: dict[str, list[str]], seed_key: str, seed: int) -> str:
    direction = route_direction(route_id)
    segment = route_segment(route_id)
    if direction in {"upbound", "downbound"} and segment in HOTSPOT_SEGMENTS and pools.get(f"{direction}_cool"):
        pool = pools[f"{direction}_cool"]
    elif direction in {"upbound", "downbound"} and pools.get(f"{direction}_any"):
        pool = pools[f"{direction}_any"]
    else:
        pool = pools.get("mainline_any") or pools.get("midcorridor_any") or [route_id]
    return pool[stable_index(seed_key, seed, len(pool))]


def build_variant(
    base_demand: Path,
    output_demand: Path,
    *,
    label: str,
    time_profile: str,
    spatial_shift: float,
    seed: int,
) -> dict[str, Any]:
    tree = ET.parse(base_demand)
    root = tree.getroot()
    vehicles = list(root.findall("vehicle"))
    clones = [vehicle for vehicle in vehicles if clone_vehicle_attrs(vehicle)]
    pools = route_pools(parse_routes(root), vehicles)
    route_changed = 0
    time_changed = 0
    clone_count = len(clones)
    partial_profile = partial_time_profile(time_profile)
    for index, vehicle in enumerate(clones):
        vehicle_id = str(vehicle.get("id", ""))
        if spatial_shift > 0 and stable_unit(f"spatial:{vehicle_id}", seed) < spatial_shift:
            old_route = str(vehicle.get("route", ""))
            new_route = choose_spatial_route(old_route, pools, f"route:{vehicle_id}", seed)
            if new_route and new_route != old_route:
                vehicle.set("route", new_route)
                route_changed += 1
        effective_time_profile = time_profile
        if partial_profile is not None:
            base_profile, fraction = partial_profile
            if stable_unit(f"time_partial:{vehicle_id}", seed) >= fraction:
                effective_time_profile = "keep"
            else:
                effective_time_profile = base_profile
        if effective_time_profile != "keep":
            old_depart = vehicle_depart(vehicle)
            new_depart = profiled_depart(index, clone_count, effective_time_profile, seed)
            if abs(new_depart - old_depart) > 0.05:
                vehicle.set("depart", f"{new_depart:.2f}")
                time_changed += 1

    vehicles.sort(key=lambda item: (vehicle_depart(item), str(item.get("id", ""))))
    for vehicle in list(root.findall("vehicle")):
        root.remove(vehicle)
    for vehicle in vehicles:
        root.append(vehicle)
    output_demand.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_demand, encoding="UTF-8", xml_declaration=True)

    bins = Counter(int(vehicle_depart(vehicle) // 300) * 300 for vehicle in vehicles)
    prefixes = Counter(route_prefix(str(vehicle.get("route", ""))) for vehicle in vehicles)
    directions = Counter(route_direction(str(vehicle.get("route", ""))) for vehicle in vehicles)
    summary = {
        "schema": "compact_v9_u130_toegye_general15_variant.v1",
        "generated_at": utc_now(),
        "label": label,
        "base_demand": rel(base_demand),
        "output_demand": rel(output_demand),
        "vehicle_count": len(vehicles),
        "clone_count": clone_count,
        "route_changed_count": route_changed,
        "time_changed_count": time_changed,
        "time_profile": time_profile,
        "spatial_shift": spatial_shift,
        "depart_300s_bins": {str(key): bins[key] for key in sorted(bins)},
        "route_prefix_counts": dict(prefixes.most_common()),
        "route_direction_counts": dict(directions.most_common()),
    }
    write_json(output_demand.with_suffix(".summary.json"), summary)
    return summary


def stage1_movement_edges(stage1_dir: Path) -> list[dict[str, Any]]:
    rows = read_csv(stage1_dir / "b4_approach_storage_link_plan.csv")
    movements: list[dict[str, Any]] = []
    for row in rows:
        edges = [edge for edge in str(row.get("corridor_storage_edges", "")).split() if edge]
        if not edges:
            continue
        movements.append({
            "movement_id": row.get("movement_id", ""),
            "segment_id": row.get("mapped_S_segment", ""),
            "route_order_index": safe_float(row.get("route_order_index"), 0.0),
            "edges": edges,
        })
    return movements


def parse_edge_data(edge_data: Path, movements: list[dict[str, Any]], begin_sec: float) -> dict[str, Any]:
    edge_to_movements: dict[str, list[str]] = {}
    for movement in movements:
        for edge_id in movement["edges"]:
            edge_to_movements.setdefault(edge_id, []).append(movement["movement_id"])
    acc: dict[str, dict[str, float]] = {
        movement["movement_id"]: {
            "speed_weighted": 0.0,
            "sampled": 0.0,
            "entered": 0.0,
            "left": 0.0,
            "waiting": 0.0,
        }
        for movement in movements
    }
    current_begin = 0.0
    for event, elem in ET.iterparse(edge_data, events=("start", "end")):
        if event == "start" and elem.tag == "interval":
            current_begin = safe_float(elem.get("begin"), 0.0)
        elif event == "end" and elem.tag == "edge":
            if current_begin < begin_sec:
                elem.clear()
                continue
            edge_id = elem.get("id", "")
            movement_ids = edge_to_movements.get(edge_id, [])
            if not movement_ids:
                elem.clear()
                continue
            sampled = safe_float(elem.get("sampledSeconds"), 0.0)
            speed_kmh = safe_float(elem.get("speed"), 0.0) * 3.6
            entered = safe_float(elem.get("entered"), 0.0)
            left = safe_float(elem.get("left"), 0.0)
            waiting = safe_float(elem.get("waitingTime"), 0.0)
            for movement_id in movement_ids:
                item = acc[movement_id]
                item["sampled"] += sampled
                item["speed_weighted"] += speed_kmh * sampled
                item["entered"] += entered
                item["left"] += left
                item["waiting"] += waiting
            elem.clear()
    movement_rows: list[dict[str, Any]] = []
    for movement in movements:
        item = acc[movement["movement_id"]]
        sampled = item["sampled"]
        speed = item["speed_weighted"] / sampled if sampled > 0.0 else math.nan
        movement_rows.append({
            "movement_id": movement["movement_id"],
            "segment_id": movement["segment_id"],
            "route_order_index": movement["route_order_index"],
            "speed_kmh": round(speed, 6) if not math.isnan(speed) else "",
            "sampled_seconds": round(sampled, 6),
            "entered": round(item["entered"], 6),
            "left": round(item["left"], 6),
            "waiting_sec": round(item["waiting"], 6),
        })
    speeds = [safe_float(row["speed_kmh"], math.nan) for row in movement_rows if row["speed_kmh"] != ""]
    entered_den = sum(safe_float(row["entered"], 0.0) for row in movement_rows if row["speed_kmh"] != "")
    sampled_den = sum(safe_float(row["sampled_seconds"], 0.0) for row in movement_rows if row["speed_kmh"] != "")
    entered_speed = (
        sum(safe_float(row["speed_kmh"], 0.0) * safe_float(row["entered"], 0.0) for row in movement_rows) / entered_den
        if entered_den > 0.0
        else ""
    )
    sampled_speed = (
        sum(safe_float(row["speed_kmh"], 0.0) * safe_float(row["sampled_seconds"], 0.0) for row in movement_rows) / sampled_den
        if sampled_den > 0.0
        else ""
    )
    return {
        "movement_rows": movement_rows,
        "toegye_entered_weighted_speed_kmh": round(entered_speed, 6) if entered_speed != "" else "",
        "toegye_sampled_weighted_speed_kmh": round(sampled_speed, 6) if sampled_speed != "" else "",
        "toegye_movement_simple_mean_kmh": round(sum(speeds) / len(speeds), 6) if speeds else "",
        "toegye_movement_median_kmh": round(sorted(speeds)[len(speeds) // 2], 6) if speeds else "",
        "toegye_slow_movement_count_le10": sum(speed <= 10.0 for speed in speeds),
        "toegye_slow_movement_count_le15": sum(speed <= 15.0 for speed in speeds),
        "toegye_fast_movement_count_gt60": sum(speed > 60.0 for speed in speeds),
        "toegye_entered_total": round(entered_den, 6),
        "toegye_sampled_seconds_total": round(sampled_den, 6),
    }


def demand_path_for_label(output_dir: Path, label: str) -> Path:
    return output_dir / f"background_routes_compact_v9_B04_{label}.rou.xml"


def run_b04_candidate(args: argparse.Namespace, label: str, demand: Path, variant_summary: dict[str, Any]) -> dict[str, Any]:
    run_id = f"{args.run_id}_{label}"
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
    metrics_dir = args.metrics_root / run_id
    metrics_dir.mkdir(parents=True, exist_ok=True)
    result_csv = metrics_dir / "b04_result_row.csv"
    write_csv(result_csv, [row], list(row.keys()))
    write_json(metrics_dir / "b04_result_row.json", row)
    metric = parse_edge_data(run_dir / "edgeData.xml", stage1_movement_edges(args.stage1_dir), args.metric_begin_sec)
    movement_csv = metrics_dir / "toegye_movement_speed_metrics.csv"
    write_csv(
        movement_csv,
        metric["movement_rows"],
        ["movement_id", "segment_id", "route_order_index", "speed_kmh", "sampled_seconds", "entered", "left", "waiting_sec"],
    )
    result = {
        "label": label,
        "variant_kind": "u130_clone_time_spatial_redistribution",
        "time_profile": variant_summary["time_profile"],
        "spatial_shift": variant_summary["spatial_shift"],
        "vehicle_count": variant_summary["vehicle_count"],
        "clone_count": variant_summary["clone_count"],
        "route_changed_count": variant_summary["route_changed_count"],
        "time_changed_count": variant_summary["time_changed_count"],
        "run_id": run_id,
        "final_status": row.get("final_status", ""),
        "failure_reason": row.get("failure_reason", ""),
        "T_actual_EMV_sec": row.get("T_actual_EMV_sec", ""),
        "background_departed": row.get("background_departed", ""),
        "background_arrived": row.get("background_arrived", ""),
        "background_arrived_ratio": row.get("background_arrived_ratio", ""),
        "general_mean_delay_sec": row.get("general_mean_delay_sec", ""),
        **{key: value for key, value in metric.items() if key != "movement_rows"},
        "score_abs_error": abs(safe_float(metric.get("toegye_entered_weighted_speed_kmh"), 0.0) - args.target_speed_kmh),
        "demand_file": rel(demand),
        "metrics_csv": rel(result_csv),
        "run_dir": rel(run_dir),
        "movement_metrics_csv": rel(movement_csv),
    }
    write_json(metrics_dir / "toegye_general15_metrics.json", result)
    return result


def variant_specs(args: argparse.Namespace) -> list[tuple[str, str, float]]:
    if args.variant:
        specs: list[tuple[str, str, float]] = []
        for raw in args.variant:
            parts = raw.split(":")
            if len(parts) != 3:
                raise ValueError(f"variant must be label:time_profile:spatial_shift, got {raw}")
            specs.append((parts[0], parts[1], float(parts[2])))
        return specs
    return [
        ("u130_keep", "keep", 0.0),
        ("u130_peak_soft", "peak_soft", 0.0),
        ("u130_even", "even", 0.0),
        ("u130_shoulder", "shoulder", 0.0),
        ("u130_spatial_mild", "keep", 0.15),
        ("u130_spatial_peak_mild", "peak_soft", 0.15),
    ]


def choose_best(rows: list[dict[str, Any]], target_speed: float) -> dict[str, Any]:
    pass_rows = [row for row in rows if row.get("final_status") == "PASS" and row.get("toegye_entered_weighted_speed_kmh") != ""]
    candidates = pass_rows or rows
    return min(candidates, key=lambda row: abs(safe_float(row.get("toegye_entered_weighted_speed_kmh"), 0.0) - target_speed))


def copy_best_demand(best: dict[str, Any], final_demand: Path) -> None:
    source = PROJECT_ROOT / str(best["demand_file"])
    tree = ET.parse(source)
    final_demand.parent.mkdir(parents=True, exist_ok=True)
    tree.write(final_demand, encoding="UTF-8", xml_declaration=True)
    source_summary = source.with_suffix(".summary.json")
    payload = json.loads(source_summary.read_text(encoding="utf-8")) if source_summary.is_file() else {}
    payload.update({
        "schema": "compact_v9_u130_toegye_general15_final_demand.v1",
        "generated_at": utc_now(),
        "source_variant_demand": rel(source),
        "output_demand": rel(final_demand),
        "selected_run": best,
    })
    write_json(final_demand.with_suffix(".summary.json"), payload)


def calibrate(args: argparse.Namespace) -> dict[str, Any]:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for label, time_profile, spatial_shift in variant_specs(args):
        demand = demand_path_for_label(args.output_dir, label)
        summary = build_variant(
            args.base_demand,
            demand,
            label=label,
            time_profile=time_profile,
            spatial_shift=spatial_shift,
            seed=args.seed,
        )
        rows.append(run_b04_candidate(args, label, demand, summary))
        write_csv(args.output_dir / "u130_toegye_general15_results.partial.csv", rows, RESULT_FIELDS)
    best = choose_best(rows, args.target_speed_kmh)
    copy_best_demand(best, args.final_demand)
    result_csv = args.output_dir / "u130_toegye_general15_results.csv"
    result_json = args.output_dir / "u130_toegye_general15_summary.json"
    write_csv(result_csv, rows, RESULT_FIELDS)
    payload = {
        "schema": "compact_v9_u130_toegye_general15_calibration.v1",
        "generated_at": utc_now(),
        "target_speed_kmh": args.target_speed_kmh,
        "target_metric": "B04 Toegye movement speed weighted by movement entered count; edgeData begin >= metric_begin_sec",
        "metric_begin_sec": args.metric_begin_sec,
        "net_file": rel(args.net_file),
        "base_demand": rel(args.base_demand),
        "final_demand": rel(args.final_demand),
        "stage1_dir": rel(args.stage1_dir),
        "best": best,
        "rows": rows,
        "result_csv": rel(result_csv),
    }
    write_json(result_json, payload)
    return {**payload, "result_json": rel(result_json)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate u130 demand to regular Toegye-ro 15 km/h.")
    parser.add_argument("--net-file", type=Path, default=DEFAULT_NET)
    parser.add_argument("--base-demand", type=Path, default=DEFAULT_BASE_DEMAND)
    parser.add_argument("--final-demand", type=Path, default=DEFAULT_FINAL_DEMAND)
    parser.add_argument("--stage1-dir", type=Path, default=DEFAULT_STAGE1_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--metrics-root", type=Path, default=DEFAULT_METRICS_ROOT)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--run-id", default="u130_toegye_general15")
    parser.add_argument("--phase", default="bo-smoke")
    parser.add_argument("--hard-max-sim-time", type=float, default=4000.0)
    parser.add_argument("--metric-begin-sec", type=float, default=600.0)
    parser.add_argument("--target-speed-kmh", type=float, default=15.0)
    parser.add_argument("--seed", type=int, default=20260606)
    parser.add_argument("--variant", action="append", default=[], help="label:time_profile:spatial_shift")
    parser.add_argument("--sumo-binary", default=None)
    args = parser.parse_args(argv)
    args.net_file = args.net_file.resolve()
    args.base_demand = args.base_demand.resolve()
    args.final_demand = args.final_demand.resolve()
    args.stage1_dir = args.stage1_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.metrics_root = args.metrics_root.resolve()
    args.run_root = args.run_root.resolve()
    for path in [args.net_file, args.base_demand]:
        if not path.is_file():
            raise FileNotFoundError(path)
    if not args.stage1_dir.is_dir():
        raise FileNotFoundError(args.stage1_dir)
    return args


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(calibrate(parse_args(argv)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
