#!/usr/bin/env python3
"""Strengthen Compact V9 B04 signals and demand for congestion reproduction.

This is a pragmatic follow-up to the A008 location-matched T-Data work:

* keep API-hit signal profiles as-is,
* replace no-hit fallback profiles with measured API-profile averages plus
  +/-3s green/red transfer,
* turn single-phase mainline-open TLS into cyclic green/yellow/red plans, and
* generate a sustained 4000-second background demand from the skeleton's
  one-hour traffic volumes.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = Path(__file__).resolve().parent
TDATA_ROOT = PIPELINE_DIR / "tdata_signal"
NET_DIR = TDATA_ROOT / "nets"
DEMAND_DIR = PROJECT_ROOT / "data_prepared/compact_v9/demand"
SUMMARY_DIR = TDATA_ROOT / "summaries"

INPUT_NET = TDATA_ROOT / "nets/jungbu_compact_v9_B04_location_matched_s1forced.net.xml"
OUTPUT_NET = NET_DIR / "jungbu_compact_v9_B04_location_matched_reality_repaired_s1forced.net.xml"
ACTIVE_NET = PROJECT_ROOT / "data_prepared/compact_v9/net/jungbu_compact_v9_B04_green18.net.xml"
ACTIVE_BACKUP = NET_DIR / "jungbu_compact_v9_B04_green18.before_reality_repaired.net.xml"

BASE_DEMAND = DEMAND_DIR / "background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml"
OUTPUT_DEMAND = DEMAND_DIR / "background_routes_compact_v9_B04_reality_4000_sustained_s1forced.rou.xml"
SKELETON_CSV = PROJECT_ROOT / "mainstream_segment_skeleton.csv"
TLS_MAPPING_CSV = TDATA_ROOT / "a008_tls_itst_mapping.csv"
PROFILES_CSV = TDATA_ROOT / "location_matched_signal_profiles.csv"
OUTPUT_PROFILES_CSV = TDATA_ROOT / "reality_repaired_signal_profiles.csv"
APPLIED_CSV = TDATA_ROOT / "reality_repaired_applied_signal_profiles.csv"
SUMMARY_JSON = SUMMARY_DIR / "b04_reality_congestion_summary.json"

TDATA_HELPER_PATH = PIPELINE_DIR / "tdata_plausible_signal_pipeline.py"


class RealityCongestionError(RuntimeError):
    """Expected reality congestion pipeline failure."""


def load_tdata_helper() -> Any:
    spec = importlib.util.spec_from_file_location("tdata_reality_signal_helper", TDATA_HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RealityCongestionError(f"cannot_load_helper:{TDATA_HELPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tdp = load_tdata_helper()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


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


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def measured_average_profile(rows: list[dict[str, Any]]) -> dict[str, int]:
    api_profiles = [row for row in rows if "TData_SPAT" in str(row.get("source", ""))]
    if not api_profiles:
        return {"cycle_sec": 90, "main_green_sec": 60, "yellow_sec": 3}
    return {
        "cycle_sec": round(sum(safe_int(row.get("cycle_sec"), 90) for row in api_profiles) / len(api_profiles)),
        "main_green_sec": round(sum(safe_int(row.get("main_green_sec"), 60) for row in api_profiles) / len(api_profiles)),
        "yellow_sec": round(sum(safe_int(row.get("yellow_sec"), 3) for row in api_profiles) / len(api_profiles)),
    }


def fallback_timing_family(row: dict[str, Any], averages: dict[str, int]) -> dict[str, int]:
    route_order = safe_float(row.get("route_order_min"), 0.0)
    yellow = int(averages["yellow_sec"])
    family_index = int(route_order) % 5
    cycle = max(80, min(105, int(averages["cycle_sec"]) + [-5, 0, 5, 10, -10][family_index]))
    main_green = clamp_main_green(
        int(averages["main_green_sec"]) + [-14, -9, -4, 1, -6][family_index],
        cycle,
        yellow,
    )
    return {"cycle_sec": cycle, "main_green_sec": main_green, "yellow_sec": yellow}


def load_signal_profiles() -> list[dict[str, Any]]:
    profiles = [dict(row) for row in read_csv(PROFILES_CSV)]
    api_profiles = [row for row in profiles if "TData_SPAT" in str(row.get("source", ""))]
    if not api_profiles:
        return profiles

    averages = measured_average_profile(profiles)
    enhanced: list[dict[str, Any]] = []
    for row in profiles:
        row = dict(row)
        if "TData_SPAT" not in str(row.get("source", "")):
            timing = fallback_timing_family(row, averages)
            cycle = timing["cycle_sec"]
            yellow = timing["yellow_sec"]
            main_green = timing["main_green_sec"]
            row["cycle_sec"] = str(cycle)
            row["main_green_sec"] = str(main_green)
            row["side_green_sec"] = str(max(6, cycle - main_green - 2 * yellow))
            row["yellow_sec"] = str(yellow)
            row["source"] = "A008_location_matched_TData_measured_route_family_fallback"
            row["source_eqmn_id"] = ""
            row["confidence"] = max(safe_float(row.get("confidence"), 0.0), 0.55)
            row["inference_reason"] = (
                "no direct API timing hit; used direct T-Data profile average route-family timing"
            )
        enhanced.append(row)
    write_csv(OUTPUT_PROFILES_CSV, enhanced, list(enhanced[0].keys()) if enhanced else [])
    return enhanced


def clamp_main_green(value: int, cycle: int, yellow: int) -> int:
    return max(24, min(cycle - 2 * yellow - 16, value))


def profile_object(row: dict[str, Any]) -> Any:
    return tdp.SignalProfile(
        tls_id=str(row.get("tls_id", "")),
        profile_role=str(row.get("profile_role", "location_matched_mainroad")),
        source=str(row.get("source", "")),
        source_itst_id=str(row.get("source_itst_id", "")),
        source_eqmn_id=str(row.get("source_eqmn_id", "")),
        source_tls_id=str(row.get("source_tls_id", "")),
        movement_ids=str(row.get("movement_ids", "")),
        mapped_segments=str(row.get("mapped_segments", "")),
        route_order_min=safe_float(row.get("route_order_min"), 0.0),
        cycle_sec=safe_int(row.get("cycle_sec"), 100),
        main_green_sec=safe_int(row.get("main_green_sec"), 70),
        side_green_sec=safe_int(row.get("side_green_sec"), 24),
        yellow_sec=safe_int(row.get("yellow_sec"), 3),
        offset_sec=safe_int(row.get("offset_sec"), 0),
        confidence=safe_float(row.get("confidence"), 0.5),
        dominant_api_field=str(row.get("dominant_api_field", "")),
        dominant_remaining_sec=safe_float(row.get("dominant_remaining_sec"), 0.0),
        inference_reason=str(row.get("inference_reason", "")),
    )


def replace_single_phase(logic: ET.Element, profile: Any) -> dict[str, Any]:
    phases = logic.findall("phase")
    if len(phases) != 1:
        raise RealityCongestionError("replace_single_phase_called_for_multiphase")
    before_cycle = safe_float(phases[0].get("duration"), 0.0)
    link_count = max(1, len(phases[0].get("state", "")))
    for phase in phases:
        logic.remove(phase)
    yellow = max(1, int(profile.yellow_sec))
    main_green = max(5, int(profile.main_green_sec))
    side_red = max(5, int(profile.cycle_sec) - main_green - 2 * yellow)
    phase_specs = [
        (main_green, "G" * link_count, "reality_main_green"),
        (yellow, "y" * link_count, "reality_main_yellow"),
        (side_red, "r" * link_count, "reality_side_surrogate_red"),
        (yellow, "r" * link_count, "reality_all_red_clearance"),
    ]
    for duration, state, name in phase_specs:
        ET.SubElement(logic, "phase", {"duration": str(duration), "state": state, "name": name})
    logic.set("type", "static")
    logic.set("programID", "TDATA_REALITY_REPAIRED")
    logic.set("offset", str(int(profile.offset_sec)))
    return {
        "status": "APPLIED_SINGLE_PHASE_REPAIRED",
        "before_cycle_sec": round(before_cycle, 3),
        "after_cycle_sec": sum(item[0] for item in phase_specs),
        "phase_count": 4,
        "main_phase_indices": "0",
        "yellow_phase_count": 1,
        "single_phase_repaired": True,
    }


def build_repaired_net(input_net: Path, output_net: Path, overwrite_active: bool) -> dict[str, Any]:
    profiles = load_signal_profiles()
    tls_rows = {row["tls_id"]: row for row in read_csv(TLS_MAPPING_CSV)}
    profiles_by_tls = {row["tls_id"]: profile_object(row) for row in profiles}
    tree = ET.parse(input_net)
    root = tree.getroot()
    applied_rows: list[dict[str, Any]] = []
    repaired_count = 0
    applied_count = 0
    for logic in root.findall("tlLogic"):
        tls_id = str(logic.get("id", ""))
        profile = profiles_by_tls.get(tls_id)
        if profile is None:
            continue
        if len(logic.findall("phase")) == 1:
            result = replace_single_phase(logic, profile)
            repaired_count += 1
        else:
            main_indices = tdp.parse_index_list(tls_rows.get(tls_id, {}).get("selected_green_phases", ""))
            result = tdp.apply_profile_to_logic(logic, profile, main_indices)
        if str(result.get("status", "")).startswith("APPLIED"):
            applied_count += 1
        applied_rows.append(profile.as_row() | result)
    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="UTF-8", xml_declaration=True)
    active_overwritten = False
    if overwrite_active:
        if not ACTIVE_BACKUP.is_file():
            import shutil

            shutil.copy2(ACTIVE_NET, ACTIVE_BACKUP)
        import shutil

        shutil.copy2(output_net, ACTIVE_NET)
        active_overwritten = True
    write_csv(APPLIED_CSV, applied_rows, list(applied_rows[0].keys()) if applied_rows else [])
    return {
        "input_net": rel(input_net),
        "output_net": rel(output_net),
        "active_net": rel(ACTIVE_NET),
        "active_net_overwritten": active_overwritten,
        "active_backup": rel(ACTIVE_BACKUP) if ACTIVE_BACKUP.is_file() else "",
        "applied_tls_count": applied_count,
        "single_phase_repaired_count": repaired_count,
        "profiles_csv": rel(OUTPUT_PROFILES_CSV),
        "applied_profiles_csv": rel(APPLIED_CSV),
    }


def append_vehicle(vehicles: list[dict[str, str]], veh_id: str, route_id: str, depart: float) -> None:
    vehicles.append({
        "id": veh_id,
        "type": "b04_passenger",
        "route": route_id,
        "depart": f"{depart:.2f}",
        "departLane": "best",
        "departPos": "random_free",
        "departSpeed": "max",
    })


def spread_departures(count: int, begin: float, end: float, phase: float = 0.0) -> list[float]:
    if count <= 0:
        return []
    span = max(1.0, end - begin)
    step = span / count
    times = []
    for index in range(count):
        wave = 0.12 * step * math.sin((index + phase) * 1.618)
        times.append(begin + (index + 0.5) * step + wave)
    return times


def existing_routes(root: ET.Element) -> set[str]:
    return {route.get("id", "") for route in root.findall("route") if route.get("id")}


def route_variants(route_ids: set[str], prefix: str) -> list[str]:
    return sorted(route_id for route_id in route_ids if route_id == prefix or route_id.startswith(prefix))


def generate_reality_demand(base_demand: Path, output_demand: Path, begin: float, end: float, feeder_scale: float, sideflow_count: int) -> dict[str, Any]:
    root = ET.parse(base_demand).getroot()
    for vehicle in list(root.findall("vehicle")):
        root.remove(vehicle)
    vtype = root.find("vType")
    if vtype is not None:
        vtype.set("speedFactor", "0.820")
        vtype.set("speedDev", "0.020")
    route_ids = existing_routes(root)
    up_routes = route_variants(route_ids, "mainline_through_upbound_balanced") or route_variants(route_ids, "mainline_through_upbound")
    down_routes = route_variants(route_ids, "mainline_through_downbound_balanced") or route_variants(route_ids, "mainline_through_downbound")
    mid_up_routes = route_variants(route_ids, "midcorridor_local_upbound")
    mid_down_routes = route_variants(route_ids, "midcorridor_local_downbound")
    side_routes = route_variants(route_ids, "sideflow_background")

    skeleton = read_csv(SKELETON_CSV)
    duration = end - begin
    hour_factor = duration / 3600.0
    up_vph = [safe_float(row.get("peak_hour_volume_up_veh_per_h")) for row in skeleton]
    down_vph = [safe_float(row.get("peak_hour_volume_down_veh_per_h")) for row in skeleton]
    main_up_count = int(round(sum(up_vph) / len(up_vph) * hour_factor))
    main_down_count = int(round(sum(down_vph) / len(down_vph) * hour_factor))
    vehicles: list[dict[str, str]] = []

    for index, depart in enumerate(spread_departures(main_up_count, begin, end, phase=0.5)):
        append_vehicle(vehicles, f"B04_reality4000_main_up_{index:05d}", up_routes[index % len(up_routes)], depart)
    for index, depart in enumerate(spread_departures(main_down_count, begin, end, phase=1.5)):
        append_vehicle(vehicles, f"B04_reality4000_main_down_{index:05d}", down_routes[index % len(down_routes)], depart)

    feeder_count = 0
    for row in skeleton:
        segment = row["segment_id"]
        for direction, volume in [("upbound", safe_float(row.get("peak_hour_volume_up_veh_per_h"))), ("downbound", safe_float(row.get("peak_hour_volume_down_veh_per_h")))]:
            route_id = f"segment_feeder_{direction}_{segment}"
            if route_id not in route_ids:
                continue
            count = int(round(volume * hour_factor * feeder_scale))
            segment_begin = begin + (safe_int(segment[1:], 1) % 7) * 4.0
            for index, depart in enumerate(spread_departures(count, segment_begin, end, phase=feeder_count + 0.25)):
                append_vehicle(vehicles, f"B04_reality4000_feeder_{direction}_{segment}_{index:04d}", route_id, depart)
            feeder_count += count

    mid_count_each = int(round((main_up_count + main_down_count) * 0.045))
    for route_group, label, count in [(mid_up_routes, "mid_up", mid_count_each), (mid_down_routes, "mid_down", mid_count_each)]:
        if not route_group:
            continue
        for index, depart in enumerate(spread_departures(count, begin + 120.0, end, phase=2.5)):
            append_vehicle(vehicles, f"B04_reality4000_{label}_{index:04d}", route_group[index % len(route_group)], depart)
    if side_routes:
        for index, depart in enumerate(spread_departures(sideflow_count, begin + 180.0, end, phase=4.5)):
            append_vehicle(vehicles, f"B04_reality4000_side_{index:04d}", side_routes[index % len(side_routes)], depart)

    vehicles.sort(key=lambda item: safe_float(item["depart"]))
    for vehicle in vehicles:
        ET.SubElement(root, "vehicle", vehicle)
    output_demand.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_demand, encoding="UTF-8", xml_declaration=True)
    return {
        "base_demand": rel(base_demand),
        "output_demand": rel(output_demand),
        "begin_sec": begin,
        "end_sec": end,
        "duration_sec": duration,
        "hour_factor": round(hour_factor, 6),
        "main_up_count": main_up_count,
        "main_down_count": main_down_count,
        "feeder_count": feeder_count,
        "mid_count_each_direction": mid_count_each,
        "sideflow_count": sideflow_count if side_routes else 0,
        "vehicle_count": len(vehicles),
        "peak_hour_avg_up_vph": round(sum(up_vph) / len(up_vph), 3),
        "peak_hour_avg_down_vph": round(sum(down_vph) / len(down_vph), 3),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    net_summary = build_repaired_net(args.input_net, args.output_net, args.overwrite_active_net)
    demand_summary = generate_reality_demand(
        args.base_demand,
        args.output_demand,
        args.demand_begin,
        args.demand_end,
        args.feeder_scale,
        args.sideflow_count,
    )
    summary = {
        "schema": "compact_v9_b04_reality_congestion.v1",
        "generated_at": utc_now(),
        "signal": net_summary,
        "demand": demand_summary,
    }
    write_json(SUMMARY_JSON, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair B04 reality signals and generate 4000-second sustained demand.")
    parser.add_argument("--input-net", type=Path, default=INPUT_NET)
    parser.add_argument("--output-net", type=Path, default=OUTPUT_NET)
    parser.add_argument("--overwrite-active-net", action="store_true")
    parser.add_argument("--base-demand", type=Path, default=BASE_DEMAND)
    parser.add_argument("--output-demand", type=Path, default=OUTPUT_DEMAND)
    parser.add_argument("--demand-begin", type=float, default=180.0)
    parser.add_argument("--demand-end", type=float, default=3900.0)
    parser.add_argument("--feeder-scale", type=float, default=0.025)
    parser.add_argument("--sideflow-count", type=int, default=180)
    args = parser.parse_args(argv)
    try:
        summary = run(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
