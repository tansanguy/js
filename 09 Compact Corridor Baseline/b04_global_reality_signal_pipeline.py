#!/usr/bin/env python3
"""Apply A008/API-informed plausible timing to every TLS in the B04 net."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sumolib
from pyproj import Transformer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = Path(__file__).resolve().parent
TDATA_ROOT = PIPELINE_DIR / "tdata_signal"
NET_DIR = TDATA_ROOT / "nets"
SUMMARY_DIR = TDATA_ROOT / "summaries"

A008_CSV = PROJECT_ROOT / "A008_P.csv"
INPUT_NET = NET_DIR / "jungbu_compact_v9_B04_location_matched_reality_repaired.net.xml"
OUTPUT_NET = NET_DIR / "jungbu_compact_v9_B04_global_reality.net.xml"
MAIN_PROFILES_CSV = TDATA_ROOT / "reality_repaired_signal_profiles.csv"
TIMING_SNAPSHOTS = [
    TDATA_ROOT / "api_snapshots/timing_location_matched_20260605_210517.jsonl",
    TDATA_ROOT / "api_snapshots/timing_location_matched_20260605_210751.jsonl",
    TDATA_ROOT / "api_snapshots/timing_location_matched_20260605_210833.jsonl",
]

GLOBAL_MAPPING_CSV = TDATA_ROOT / "global_tls_a008_itst_mapping.csv"
GLOBAL_PROFILES_CSV = TDATA_ROOT / "global_reality_signal_profiles.csv"
GLOBAL_APPLIED_CSV = TDATA_ROOT / "global_reality_applied_signal_profiles.csv"
SUMMARY_JSON = SUMMARY_DIR / "b04_global_reality_signal_summary.json"

TDATA_HELPER_PATH = PIPELINE_DIR / "tdata_plausible_signal_pipeline.py"


class GlobalRealitySignalError(RuntimeError):
    """Expected global signal pipeline failure."""


@dataclass(frozen=True)
class A008Point:
    itst_id: str
    name: str
    lat: float
    lon: float
    x: float
    y: float


@dataclass(frozen=True)
class TlsPoint:
    tls_id: str
    x: float
    y: float
    lat: float
    lon: float
    coord_source: str
    link_count: int
    phase_count: int


def load_tdata_helper() -> Any:
    spec = importlib.util.spec_from_file_location("tdata_global_reality_helper", TDATA_HELPER_PATH)
    if spec is None or spec.loader is None:
        raise GlobalRealitySignalError(f"cannot_load_helper:{TDATA_HELPER_PATH}")
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


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return math.hypot((lat1 - lat2) * 111000.0, (lon1 - lon2) * 88000.0)


def round_to_5(value: float) -> int:
    return int(max(1, round(value / 5.0) * 5))


def clamp_int(value: float, low: int, high: int) -> int:
    return int(max(low, min(high, round(value))))


def load_a008_points() -> list[A008Point]:
    transformer = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
    points: list[A008Point] = []
    for row in read_csv(A008_CSV):
        x = safe_float(row.get("XCE"), math.nan)
        y = safe_float(row.get("YCE"), math.nan)
        if math.isnan(x) or math.isnan(y):
            continue
        lon, lat = transformer.transform(x, y)
        points.append(A008Point(
            itst_id=str(row.get("교차로번호", "")).strip(),
            name=str(row.get("교차로명", "")).strip(),
            lat=lat,
            lon=lon,
            x=x,
            y=y,
        ))
    return points


def load_snapshots(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    records.extend(json.loads(line).get("records", []))
    return records


def latest_timing_by_itst(paths: list[Path]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for row in load_snapshots(paths):
        record = tdp.api_record_from_row(row)
        if record is None:
            continue
        previous = latest.get(record.itst_id)
        if previous is None or record.utc_ms >= previous.utc_ms:
            latest[record.itst_id] = record
    return latest


def logic_phase_count(input_net: Path) -> dict[str, int]:
    root = ET.parse(input_net).getroot()
    return {str(logic.get("id")): len(logic.findall("phase")) for logic in root.findall("tlLogic") if logic.get("id")}


def logic_link_count(input_net: Path) -> dict[str, int]:
    root = ET.parse(input_net).getroot()
    counts: dict[str, int] = {}
    for logic in root.findall("tlLogic"):
        tls_id = str(logic.get("id", ""))
        states = [len(phase.get("state", "")) for phase in logic.findall("phase")]
        counts[tls_id] = max(states) if states else 0
    return counts


def required_state_lengths(root: ET.Element) -> dict[str, int]:
    required: dict[str, int] = {}
    for connection in root.findall("connection"):
        tls_id = connection.get("tl")
        link_index = connection.get("linkIndex")
        if not tls_id or link_index is None:
            continue
        try:
            index = int(link_index)
        except ValueError:
            continue
        required[tls_id] = max(required.get(tls_id, 0), index + 1)
    return required


def normalize_tls_phase_state_lengths(root: ET.Element) -> dict[str, Any]:
    required = required_state_lengths(root)
    normalized_rows: list[dict[str, Any]] = []
    normalized_phase_count = 0
    for logic in root.findall("tlLogic"):
        tls_id = str(logic.get("id", ""))
        required_len = required.get(tls_id, 0)
        if required_len <= 0:
            continue
        for phase_index, phase in enumerate(logic.findall("phase")):
            state = phase.get("state", "")
            before_len = len(state)
            if before_len == required_len:
                continue
            if before_len > required_len:
                phase.set("state", state[:required_len])
                action = "truncate"
            else:
                phase.set("state", state + "r" * (required_len - before_len))
                action = "pad_red"
            normalized_phase_count += 1
            normalized_rows.append({
                "tls_id": tls_id,
                "programID": logic.get("programID", ""),
                "phase_index": phase_index,
                "action": action,
                "before_len": before_len,
                "after_len": required_len,
                "required_len": required_len,
            })
    return {
        "normalized_phase_state_count": normalized_phase_count,
        "normalized_tls_count": len({row["tls_id"] for row in normalized_rows}),
        "normalized_rows": normalized_rows,
    }


def tls_xy_from_connections(tls: Any) -> tuple[float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for connection in tls.getConnections():
        for lane in connection[:2]:
            shape = lane.getShape()
            if not shape:
                continue
            for x, y in (shape[0], shape[-1]):
                xs.append(float(x))
                ys.append(float(y))
    if not xs:
        return None
    return sum(xs) / len(xs), sum(ys) / len(ys)


def tls_points(input_net: Path) -> list[TlsPoint]:
    net = sumolib.net.readNet(str(input_net))
    phase_counts = logic_phase_count(input_net)
    link_counts = logic_link_count(input_net)
    node_ids = {node.getID() for node in net.getNodes()}
    points: list[TlsPoint] = []
    for tls in net.getTrafficLights():
        tls_id = tls.getID()
        coord_source = "junction"
        if tls_id in node_ids:
            x, y = net.getNode(tls_id).getCoord()
        else:
            xy = tls_xy_from_connections(tls)
            if xy is None:
                continue
            x, y = xy
            coord_source = "connection_centroid"
        lon, lat = net.convertXY2LonLat(x, y)
        points.append(TlsPoint(
            tls_id=tls_id,
            x=float(x),
            y=float(y),
            lat=float(lat),
            lon=float(lon),
            coord_source=coord_source,
            link_count=link_counts.get(tls_id, 0),
            phase_count=phase_counts.get(tls_id, 0),
        ))
    return points


def nearest_a008(tls: TlsPoint, points: list[A008Point]) -> tuple[A008Point, float]:
    best = min(points, key=lambda point: distance_m(tls.lat, tls.lon, point.lat, point.lon))
    return best, distance_m(tls.lat, tls.lon, best.lat, best.lon)


def main_profile_objects() -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    if not MAIN_PROFILES_CSV.is_file():
        return profiles
    for row in read_csv(MAIN_PROFILES_CSV):
        profile = tdp.SignalProfile(
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
            confidence=safe_float(row.get("confidence"), 0.55),
            dominant_api_field=str(row.get("dominant_api_field", "")),
            dominant_remaining_sec=safe_float(row.get("dominant_remaining_sec"), 0.0),
            inference_reason=str(row.get("inference_reason", "")),
        )
        profiles[profile.tls_id] = profile
    return profiles


def profile_from_timing(tls: TlsPoint, itst_id: str, a008_name: str, timing: Any, nearest_rank: float) -> Any:
    dominant = timing.dominant_remaining_sec
    median = timing.median_remaining_sec
    cycle = clamp_int(round_to_5(max(85.0, dominant + 12.0, median * 1.2)), 80, 130)
    yellow = 3
    main_green = clamp_int(round_to_5(max(0.58 * cycle, min(cycle - 12.0, dominant * 0.54 + 16.0))), 22, cycle - 12)
    side_green = max(6, cycle - main_green - 2 * yellow)
    offset = int((tls.x * 0.018 + tls.y * 0.011 + dominant * 0.2) % cycle)
    confidence = min(0.9, 0.5 + timing.vehicle_field_count * 0.035 + max(0.0, 1.0 - nearest_rank / 160.0) * 0.12)
    return tdp.SignalProfile(
        tls_id=tls.tls_id,
        profile_role="global_location_direct",
        source="A008_global_TData_SPAT_direct",
        source_itst_id=itst_id,
        source_eqmn_id=timing.eqmn_id,
        source_tls_id=tls.tls_id,
        movement_ids="",
        mapped_segments="",
        route_order_min=0.0,
        cycle_sec=cycle,
        main_green_sec=main_green,
        side_green_sec=side_green,
        yellow_sec=yellow,
        offset_sec=offset,
        confidence=confidence,
        dominant_api_field=timing.dominant_field,
        dominant_remaining_sec=dominant,
        inference_reason=f"nearest A008={a008_name}; direct timing hit for itstId={itst_id}",
    )


def profile_from_source(tls: TlsPoint, source: Any, a008: A008Point, dist_to_a008: float, dist_to_source_tls: float) -> Any:
    cycle = int(source.cycle_sec)
    main_green = clamp_int(source.main_green_sec * 0.86, 18, cycle - 14)
    side_green = max(6, cycle - main_green - 2 * int(source.yellow_sec))
    offset = int((source.offset_sec + dist_to_source_tls / 9.0 + tls.link_count * 2.0) % cycle)
    return tdp.SignalProfile(
        tls_id=tls.tls_id,
        profile_role="global_location_fallback",
        source="nearest_similar_A008_or_API_signal_profile",
        source_itst_id=a008.itst_id,
        source_eqmn_id=source.source_eqmn_id,
        source_tls_id=source.tls_id,
        movement_ids="",
        mapped_segments="",
        route_order_min=0.0,
        cycle_sec=cycle,
        main_green_sec=main_green,
        side_green_sec=side_green,
        yellow_sec=int(source.yellow_sec),
        offset_sec=offset,
        confidence=max(0.35, min(0.62, source.confidence - 0.1)),
        dominant_api_field=source.dominant_api_field,
        dominant_remaining_sec=source.dominant_remaining_sec,
        inference_reason=(
            f"nearest A008={a008.name} ({dist_to_a008:.1f}m), no direct timing hit; "
            f"borrowed source_tls={source.tls_id} at {dist_to_source_tls:.1f}m"
        ),
    )


def average_profile(tls: TlsPoint, a008: A008Point, dist_to_a008: float, averages: dict[str, float]) -> Any:
    cycle = int(averages["cycle_sec"])
    main_green = int(averages["main_green_sec"])
    yellow = int(averages["yellow_sec"])
    side_green = max(6, cycle - main_green - 2 * yellow)
    offset = int((tls.x * 0.015 + tls.y * 0.009 + tls.link_count * 3.0) % cycle)
    return tdp.SignalProfile(
        tls_id=tls.tls_id,
        profile_role="global_average_fallback",
        source="realistic_API_and_field_average_fallback",
        source_itst_id=a008.itst_id,
        source_eqmn_id="",
        source_tls_id="average",
        movement_ids="",
        mapped_segments="",
        route_order_min=0.0,
        cycle_sec=cycle,
        main_green_sec=main_green,
        side_green_sec=side_green,
        yellow_sec=yellow,
        offset_sec=offset,
        confidence=0.32,
        dominant_api_field="",
        dominant_remaining_sec=0.0,
        inference_reason=f"nearest A008={a008.name} ({dist_to_a008:.1f}m), average fallback",
    )


def profile_averages(profiles: list[Any]) -> dict[str, float]:
    if not profiles:
        return {"cycle_sec": 100.0, "main_green_sec": 70.0, "yellow_sec": 3.0}
    return {
        "cycle_sec": round(sum(p.cycle_sec for p in profiles) / len(profiles)),
        "main_green_sec": round(sum(p.main_green_sec for p in profiles) / len(profiles)),
        "yellow_sec": round(sum(p.yellow_sec for p in profiles) / len(profiles)),
    }


def replace_single_phase(logic: ET.Element, profile: Any) -> dict[str, Any]:
    phases = logic.findall("phase")
    if len(phases) != 1:
        raise GlobalRealitySignalError("replace_single_phase_called_for_multiphase")
    before_cycle = sum(safe_float(phase.get("duration")) for phase in phases)
    link_count = max(1, len(phases[0].get("state", "")))
    for phase in phases:
        logic.remove(phase)
    yellow = max(1, int(profile.yellow_sec))
    main_green = max(5, int(profile.main_green_sec))
    red = max(5, int(profile.cycle_sec) - main_green - 2 * yellow)
    specs = [
        (main_green, "G" * link_count, "global_main_green"),
        (yellow, "y" * link_count, "global_main_yellow"),
        (red, "r" * link_count, "global_cross_red_surrogate"),
        (yellow, "r" * link_count, "global_all_red_clearance"),
    ]
    for duration, state, name in specs:
        ET.SubElement(logic, "phase", {"duration": str(duration), "state": state, "name": name})
    logic.set("type", "static")
    logic.set("programID", "GLOBAL_REALITY")
    logic.set("offset", str(int(profile.offset_sec)))
    return {
        "status": "APPLIED_SINGLE_PHASE_REPAIRED",
        "before_cycle_sec": round(before_cycle, 3),
        "after_cycle_sec": sum(item[0] for item in specs),
        "phase_count": 4,
        "main_phase_indices": "0",
        "yellow_phase_count": 1,
    }


def apply_profile_preserve_phase_ratios(logic: ET.Element, profile: Any) -> dict[str, Any]:
    phases = logic.findall("phase")
    if not phases:
        return {"status": "SKIP", "reason": "no_phase"}
    before = [safe_float(phase.get("duration"), 1.0) for phase in phases]
    before_cycle = sum(before)
    yellow_indices = [index for index, phase in enumerate(phases) if tdp.is_yellow_phase(phase)]
    target = int(profile.cycle_sec)
    new_durations = [1 for _ in phases]
    if yellow_indices:
        yellow_total = min(len(yellow_indices) * int(profile.yellow_sec), max(0, target - (len(phases) - len(yellow_indices))))
        for index, duration in zip(yellow_indices, tdp.distribute(yellow_total, len(yellow_indices), minimum=1), strict=False):
            new_durations[index] = duration
        non_yellow = [index for index in range(len(phases)) if index not in yellow_indices]
        non_yellow_total = target - sum(new_durations[index] for index in yellow_indices)
        base = sum(before[index] for index in non_yellow) or len(non_yellow)
        assigned = 0
        for index in non_yellow[:-1]:
            duration = max(1, int(round(non_yellow_total * before[index] / base)))
            new_durations[index] = duration
            assigned += duration
        if non_yellow:
            new_durations[non_yellow[-1]] = max(1, non_yellow_total - assigned)
    else:
        base = before_cycle or len(phases)
        assigned = 0
        for index in range(len(phases) - 1):
            duration = max(1, int(round(target * before[index] / base)))
            new_durations[index] = duration
            assigned += duration
        new_durations[-1] = max(1, target - assigned)
    delta = target - sum(new_durations)
    if new_durations:
        new_durations[-1] = max(1, new_durations[-1] + delta)
    for phase, duration in zip(phases, new_durations, strict=True):
        phase.set("duration", str(int(duration)))
    logic.set("type", "static")
    logic.set("programID", "GLOBAL_REALITY")
    logic.set("offset", str(int(profile.offset_sec)))
    return {
        "status": "APPLIED_RATIO_PRESERVED",
        "before_cycle_sec": round(before_cycle, 3),
        "after_cycle_sec": sum(new_durations),
        "phase_count": len(phases),
        "main_phase_indices": "",
        "yellow_phase_count": len(yellow_indices),
    }


def build_profiles(input_net: Path, timing_snapshots: list[Path]) -> tuple[list[Any], list[dict[str, Any]], dict[str, Any]]:
    tls_list = tls_points(input_net)
    a008_points = load_a008_points()
    timing_by_itst = latest_timing_by_itst(timing_snapshots)
    main_profiles = main_profile_objects()
    direct_profiles: list[Any] = []
    mapping_rows: list[dict[str, Any]] = []
    profiles_by_tls: dict[str, Any] = {}

    for tls in tls_list:
        a008, dist = nearest_a008(tls, a008_points)
        timing = timing_by_itst.get(a008.itst_id)
        if tls.tls_id in main_profiles:
            profile = main_profiles[tls.tls_id]
            source_kind = "mainroad_preserved"
        elif timing is not None:
            profile = profile_from_timing(tls, a008.itst_id, a008.name, timing, dist)
            direct_profiles.append(profile)
            source_kind = "direct_api"
        else:
            profile = None
            source_kind = "pending_fallback"
        if profile is not None:
            profiles_by_tls[tls.tls_id] = profile
        mapping_rows.append({
            "tls_id": tls.tls_id,
            "tls_lat": round(tls.lat, 8),
            "tls_lon": round(tls.lon, 8),
            "coord_source": tls.coord_source,
            "link_count": tls.link_count,
            "phase_count": tls.phase_count,
            "itst_id": a008.itst_id,
            "a008_name": a008.name,
            "a008_lat": round(a008.lat, 8),
            "a008_lon": round(a008.lon, 8),
            "distance_m": round(dist, 3),
            "timing_hit": timing is not None,
            "source_kind": source_kind,
        })

    source_profiles = list(profiles_by_tls.values())
    averages = profile_averages(source_profiles)
    tls_by_id = {tls.tls_id: tls for tls in tls_list}
    for row in mapping_rows:
        if row["tls_id"] in profiles_by_tls:
            continue
        tls = tls_by_id[str(row["tls_id"])]
        a008 = A008Point(
            itst_id=str(row["itst_id"]),
            name=str(row["a008_name"]),
            lat=safe_float(row["a008_lat"]),
            lon=safe_float(row["a008_lon"]),
            x=0.0,
            y=0.0,
        )
        dist_to_a008 = safe_float(row["distance_m"])
        if source_profiles:
            source = min(
                source_profiles,
                key=lambda profile: distance_m(tls.lat, tls.lon, tls_by_id.get(profile.tls_id, tls).lat, tls_by_id.get(profile.tls_id, tls).lon)
                + abs(tls.phase_count - tls_by_id.get(profile.tls_id, tls).phase_count) * 15.0
                + abs(tls.link_count - tls_by_id.get(profile.tls_id, tls).link_count) * 3.0,
            )
            source_tls = tls_by_id.get(source.tls_id, tls)
            dist_to_source = distance_m(tls.lat, tls.lon, source_tls.lat, source_tls.lon)
            profile = profile_from_source(tls, source, a008, dist_to_a008, dist_to_source)
            row["source_kind"] = "nearest_source_fallback"
        else:
            profile = average_profile(tls, a008, dist_to_a008, averages)
            row["source_kind"] = "average_fallback"
        profiles_by_tls[tls.tls_id] = profile

    profiles = [profiles_by_tls[tls.tls_id] for tls in tls_list if tls.tls_id in profiles_by_tls]
    stats = {
        "tls_count": len(tls_list),
        "mainroad_preserved_count": sum(1 for row in mapping_rows if row["source_kind"] == "mainroad_preserved"),
        "direct_api_count": sum(1 for row in mapping_rows if row["source_kind"] == "direct_api"),
        "nearest_fallback_count": sum(1 for row in mapping_rows if row["source_kind"] == "nearest_source_fallback"),
        "average_fallback_count": sum(1 for row in mapping_rows if row["source_kind"] == "average_fallback"),
        "timing_record_itst_count": len(timing_by_itst),
        "profile_average": averages,
    }
    return profiles, mapping_rows, stats


def apply_profiles(input_net: Path, output_net: Path, profiles: list[Any]) -> dict[str, Any]:
    tree = ET.parse(input_net)
    root = tree.getroot()
    by_tls = {profile.tls_id: profile for profile in profiles}
    applied_rows: list[dict[str, Any]] = []
    applied_count = 0
    single_repaired = 0
    for logic in root.findall("tlLogic"):
        tls_id = str(logic.get("id", ""))
        profile = by_tls.get(tls_id)
        if profile is None:
            continue
        if profile.profile_role == "location_matched_mainroad":
            result = {
                "status": "PRESERVED_MAINROAD",
                "before_cycle_sec": sum(safe_float(phase.get("duration")) for phase in logic.findall("phase")),
                "after_cycle_sec": sum(safe_float(phase.get("duration")) for phase in logic.findall("phase")),
                "phase_count": len(logic.findall("phase")),
                "main_phase_indices": "",
                "yellow_phase_count": sum(1 for phase in logic.findall("phase") if tdp.is_yellow_phase(phase)),
            }
        elif len(logic.findall("phase")) == 1:
            result = replace_single_phase(logic, profile)
            single_repaired += 1
        else:
            result = apply_profile_preserve_phase_ratios(logic, profile)
        if str(result.get("status", "")).startswith("APPLIED") or result.get("status") == "PRESERVED_MAINROAD":
            applied_count += 1
        applied_rows.append(profile.as_row() | result)
    normalize_stats = normalize_tls_phase_state_lengths(root)
    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="UTF-8", xml_declaration=True)
    write_csv(GLOBAL_APPLIED_CSV, applied_rows, list(applied_rows[0].keys()) if applied_rows else [])
    normalized_rows = normalize_stats.pop("normalized_rows")
    normalized_csv = TDATA_ROOT / "b04_global_reality_tls_state_normalization.csv"
    write_csv(normalized_csv, normalized_rows, list(normalized_rows[0].keys()) if normalized_rows else [
        "tls_id", "programID", "phase_index", "action", "before_len", "after_len", "required_len",
    ])
    return {
        "input_net": rel(input_net),
        "output_net": rel(output_net),
        "applied_tls_count": applied_count,
        "single_phase_repaired_count": single_repaired,
        "applied_csv": rel(GLOBAL_APPLIED_CSV),
        "tls_state_normalization_csv": rel(normalized_csv),
        **normalize_stats,
    }


def normalize_existing_net(input_net: Path, output_net: Path) -> dict[str, Any]:
    tree = ET.parse(input_net)
    root = tree.getroot()
    normalize_stats = normalize_tls_phase_state_lengths(root)
    output_net.parent.mkdir(parents=True, exist_ok=True)
    temp = output_net.with_suffix(output_net.suffix + ".tmp")
    tree.write(temp, encoding="UTF-8", xml_declaration=True)
    temp.replace(output_net)
    normalized_rows = normalize_stats.pop("normalized_rows")
    normalized_csv = TDATA_ROOT / f"{output_net.stem}_tls_state_normalization.csv"
    write_csv(normalized_csv, normalized_rows, list(normalized_rows[0].keys()) if normalized_rows else [
        "tls_id", "programID", "phase_index", "action", "before_len", "after_len", "required_len",
    ])
    return {
        "schema": "compact_v9_b04_tls_state_normalization.v1",
        "generated_at": utc_now(),
        "input_net": rel(input_net),
        "output_net": rel(output_net),
        "tls_state_normalization_csv": rel(normalized_csv),
        **normalize_stats,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    snapshots = args.timing_snapshot or TIMING_SNAPSHOTS
    profiles, mapping_rows, profile_stats = build_profiles(args.input_net, snapshots)
    write_csv(GLOBAL_MAPPING_CSV, mapping_rows, list(mapping_rows[0].keys()) if mapping_rows else [])
    write_csv(GLOBAL_PROFILES_CSV, [profile.as_row() for profile in profiles], list(profiles[0].as_row().keys()) if profiles else [])
    apply_stats = apply_profiles(args.input_net, args.output_net, profiles)
    summary = {
        "schema": "compact_v9_b04_global_reality_signal.v1",
        "generated_at": utc_now(),
        "claim_scope": "all TLS get A008/API-informed plausible timing; not exact field reproduction",
        "timing_snapshots": [rel(path) for path in snapshots],
        "mapping_csv": rel(GLOBAL_MAPPING_CSV),
        "profiles_csv": rel(GLOBAL_PROFILES_CSV),
        **profile_stats,
        **apply_stats,
    }
    write_json(SUMMARY_JSON, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply global A008/API-informed signal profiles to every TLS.")
    parser.add_argument("--input-net", type=Path, default=INPUT_NET)
    parser.add_argument("--output-net", type=Path, default=OUTPUT_NET)
    parser.add_argument("--timing-snapshot", type=Path, action="append", default=None)
    parser.add_argument("--normalize-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = normalize_existing_net(args.input_net, args.output_net) if args.normalize_only else run(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
