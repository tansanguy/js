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
INPUT_NET = NET_DIR / "jungbu_compact_v9_B04_location_matched_reality_repaired_s1forced.net.xml"
OUTPUT_NET = NET_DIR / "jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
FIRETRUCK_ROUTE_XML = PROJECT_ROOT / "data_prepared/compact_v9/routes/firetruck_to_seoul_station_front.rou.xml"
DEFAULT_DEMAND_ROUTE_XML = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml"
B0_ROUTE_REFERENCE_NET = PROJECT_ROOT / "data_prepared/expanded_v7/net/jungbu_expanded_v7_v13_b_ref_plus5.net.xml"
B0_ROUTE_REFERENCE_ROUTE_XML = PROJECT_ROOT / "data_prepared/expanded_v7/routes/firetruck_to_seoul_station_front_conservative_b0.rou.xml"
TOEGYE_SEGMENT_REFERENCE_CSV = PROJECT_ROOT / "toegye_ro_mainstream_segments_english.csv"
B04_MAINROAD_MAPPING_CSV = PROJECT_ROOT / "data_prepared/compact_v9/map/B04_toegye_segment_edge_mapping.csv"
MAIN_PROFILES_CSV = TDATA_ROOT / "reality_repaired_signal_profiles.csv"
TIMING_SNAPSHOTS = sorted(TDATA_ROOT.glob("api_snapshots/timing_location_matched_*.jsonl"))
if not TIMING_SNAPSHOTS:
    TIMING_SNAPSHOTS = [
        TDATA_ROOT / "api_snapshots/timing_location_matched_20260605_210517.jsonl",
        TDATA_ROOT / "api_snapshots/timing_location_matched_20260605_210751.jsonl",
        TDATA_ROOT / "api_snapshots/timing_location_matched_20260605_210833.jsonl",
    ]

GLOBAL_MAPPING_CSV = TDATA_ROOT / "global_tls_a008_itst_mapping.csv"
GLOBAL_PROFILES_CSV = TDATA_ROOT / "global_reality_signal_profiles.csv"
GLOBAL_DEMAND_STATS_CSV = TDATA_ROOT / "global_reality_signal_demand_stats.csv"
GLOBAL_APPLIED_CSV = TDATA_ROOT / "global_reality_applied_signal_profiles.csv"
ROUTE_GEOMETRY_RECALL_AUDIT_JSON = TDATA_ROOT / "route_geometry_recall_audit.json"
MAINROAD_LANE_RECALL_AUDIT_CSV = TDATA_ROOT / "mainroad_lane_recall_audit.csv"
ROUTE_INTERNAL_LANE_AUDIT_CSV = TDATA_ROOT / "route_internal_lane_alignment_audit.csv"
ROUTE_TLS_PROJECTION_AUDIT_CSV = TDATA_ROOT / "route_tls_projection_audit.csv"
SUMMARY_JSON = SUMMARY_DIR / "b04_global_reality_signal_summary.json"
VIRTUAL_TLS_IDS = {"COMPACT_V9_FIRE_STATION_ENTRY_TLS"}
CSV_SIGNAL_CANDIDATES_CSV = PROJECT_ROOT / "data_prepared/compact_v9/net/B04_csv_signal_candidates.csv"
GLOBAL_REALITY_MAX_SPEED_MPS = 50.0 / 3.6
MIN_VEHICLE_GREEN_HARD_SEC = 6.0

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


def averaged_timing_by_itst(paths: list[Path]) -> dict[str, Any]:
    return tdp.aggregate_api_records_by_itst(load_snapshots(paths))


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


def connection_link_index(connection: ET.Element) -> int | None:
    link_index = connection.get("linkIndex")
    if link_index is None:
        return None
    try:
        return int(link_index)
    except ValueError:
        return None


def tls_connections_by_index(root: ET.Element) -> dict[str, dict[int, list[ET.Element]]]:
    by_tls: dict[str, dict[int, list[ET.Element]]] = {}
    for connection in root.findall("connection"):
        tls_id = connection.get("tl")
        link_index = connection_link_index(connection)
        if not tls_id or link_index is None:
            continue
        by_tls.setdefault(tls_id, {}).setdefault(link_index, []).append(connection)
    return by_tls


def phase_state(phase: ET.Element, required_len: int) -> str:
    return (phase.get("state", "") + "r" * required_len)[:required_len]


def set_phase_state_char(phase: ET.Element, required_len: int, link_index: int, char: str) -> None:
    state = list(phase_state(phase, required_len))
    if 0 <= link_index < required_len:
        state[link_index] = char
        phase.set("state", "".join(state))


def phase_green_count(phase: ET.Element, required_len: int) -> int:
    return sum(1 for char in phase_state(phase, required_len) if char in {"G", "g"})


def representative_connection(connections: list[ET.Element]) -> ET.Element | None:
    if not connections:
        return None
    return next((connection for connection in connections if connection.get("dir") == "s"), connections[0])


def select_missing_link_anchor_phase(
    phases: list[ET.Element],
    required_len: int,
    link_index: int,
    connections_by_index: dict[int, list[ET.Element]],
    min_anchor_green_sec: float = MIN_VEHICLE_GREEN_HARD_SEC,
) -> int | None:
    target = representative_connection(connections_by_index.get(link_index, []))
    target_to = target.get("to") if target is not None else None
    target_from = target.get("from") if target is not None else None
    target_dir = target.get("dir") if target is not None else None

    candidates: list[tuple[float, int]] = []
    for phase_index, phase in enumerate(phases):
        duration = safe_float(phase.get("duration"))
        if duration < min_anchor_green_sec or tdp.is_yellow_phase(phase):
            continue
        state = phase_state(phase, required_len)
        green_indices = [index for index, char in enumerate(state) if char in {"G", "g"}]
        if not green_indices:
            continue
        same_to = 0
        same_to_dir = 0
        same_from = 0
        for green_index in green_indices:
            for connection in connections_by_index.get(green_index, []):
                if target_to and connection.get("to") == target_to:
                    same_to += 1
                    if target_dir and connection.get("dir") == target_dir:
                        same_to_dir += 1
                if target_from and connection.get("from") == target_from:
                    same_from += 1
        name = str(phase.get("name", ""))
        mainroad_named = 1 if name.startswith("mainroad_green") else 0
        candidates.append((
            float(same_to_dir > 0),
            float(same_to > 0),
            float(same_from > 0),
            float(mainroad_named),
            float(same_to_dir),
            float(same_to),
            float(same_from),
            float(phase_green_count(phase, required_len)),
            duration,
            -float(phase_index),
        ))
    if not candidates:
        return None
    return int(-max(candidates)[-1])


def repair_missing_green_links(root: ET.Element) -> dict[str, Any]:
    required = required_state_lengths(root)
    connections_by_tls = tls_connections_by_index(root)
    repaired_rows: list[dict[str, Any]] = []
    for logic in root.findall("tlLogic"):
        tls_id = str(logic.get("id", ""))
        required_len = required.get(tls_id, 0)
        if required_len <= 0:
            continue
        connections_by_index = connections_by_tls.get(tls_id, {})
        phases = list(logic.findall("phase"))
        if not phases:
            continue
        missing_link_indices: list[int] = []
        for link_index in range(required_len):
            has_green = any(
                link_index < len(phase.get("state", ""))
                and phase.get("state", "")[link_index] in {"G", "g"}
                for phase in phases
            )
            if has_green:
                continue
            missing_link_indices.append(link_index)
        for link_index in missing_link_indices:
            anchor_phase_index = select_missing_link_anchor_phase(
                phases,
                required_len,
                link_index,
                connections_by_index,
            )
            if anchor_phase_index is None:
                green_state = ["r"] * required_len
                yellow_state = ["r"] * required_len
                green_state[link_index] = "g"
                yellow_state[link_index] = "y"
                green_phase = ET.SubElement(logic, "phase", {
                    "duration": "20",
                    "state": "".join(green_state),
                    "name": f"missing_link_{link_index}_fallback_green",
                })
                ET.SubElement(logic, "phase", {
                    "duration": "3",
                    "state": "".join(yellow_state),
                    "name": f"missing_link_{link_index}_fallback_yellow",
                })
                phases = list(logic.findall("phase"))
                repaired_rows.append({
                    "tls_id": tls_id,
                    "programID": logic.get("programID", ""),
                    "linkIndex": link_index,
                    "action": "append_fallback_phase",
                    "anchor_phase_index": len(phases) - 2,
                    "yellow_phase_index": len(phases) - 1,
                    "anchor_duration_sec": 20,
                    "state_before": "missing_green",
                    "state_after": green_phase.get("state", ""),
                })
                continue
            yellow_phase_index = ""
            anchor_phase = phases[anchor_phase_index]
            set_phase_state_char(anchor_phase, required_len, link_index, "g")
            if anchor_phase_index + 1 < len(phases) and tdp.is_yellow_phase(phases[anchor_phase_index + 1]):
                yellow_phase_index = anchor_phase_index + 1
                set_phase_state_char(phases[anchor_phase_index + 1], required_len, link_index, "y")
            repaired_rows.append({
                "tls_id": tls_id,
                "programID": logic.get("programID", ""),
                "linkIndex": link_index,
                "action": "attach_to_existing_phase",
                "anchor_phase_index": anchor_phase_index,
                "yellow_phase_index": yellow_phase_index,
                "anchor_duration_sec": safe_float(anchor_phase.get("duration")),
                "state_before": "missing_green",
                "state_after": phase_state(anchor_phase, required_len),
            })
    return {
        "missing_green_repaired_count": len(repaired_rows),
        "missing_green_repaired_tls_count": len({row["tls_id"] for row in repaired_rows}),
        "missing_green_repair_policy": "attach_missing_vehicle_links_to_existing_long_green_phase",
        "missing_green_rows": repaired_rows,
    }


def max_green_window_for_link(phases: list[ET.Element], required_len: int, link_index: int) -> float:
    max_window = 0.0
    current = 0.0
    for phase in phases:
        duration = safe_float(phase.get("duration"))
        state = phase_state(phase, required_len)
        char = state[link_index] if 0 <= link_index < len(state) else "r"
        if char in {"G", "g"}:
            current += duration
            max_window = max(max_window, current)
        else:
            current = 0.0
    return max_window


def repair_short_green_links(root: ET.Element, min_green_sec: float = MIN_VEHICLE_GREEN_HARD_SEC) -> dict[str, Any]:
    required = required_state_lengths(root)
    connections_by_tls = tls_connections_by_index(root)
    rows: list[dict[str, Any]] = []
    for logic in root.findall("tlLogic"):
        tls_id = str(logic.get("id", ""))
        required_len = required.get(tls_id, 0)
        if required_len <= 0:
            continue
        connections_by_index = connections_by_tls.get(tls_id, {})
        phases = list(logic.findall("phase"))
        if not phases:
            continue
        for link_index in sorted(connections_by_index):
            before_max_green = max_green_window_for_link(phases, required_len, link_index)
            if before_max_green >= min_green_sec:
                continue
            anchor_phase_index = select_missing_link_anchor_phase(
                phases,
                required_len,
                link_index,
                connections_by_index,
            )
            if anchor_phase_index is None:
                continue
            anchor_phase = phases[anchor_phase_index]
            before_state = phase_state(anchor_phase, required_len)
            set_phase_state_char(anchor_phase, required_len, link_index, "g")
            yellow_phase_index = ""
            if anchor_phase_index + 1 < len(phases) and tdp.is_yellow_phase(phases[anchor_phase_index + 1]):
                yellow_phase_index = anchor_phase_index + 1
                set_phase_state_char(phases[anchor_phase_index + 1], required_len, link_index, "y")
            after_max_green = max_green_window_for_link(phases, required_len, link_index)
            if after_max_green <= before_max_green:
                continue
            rows.append({
                "tls_id": tls_id,
                "programID": logic.get("programID", ""),
                "linkIndex": link_index,
                "before_max_green_sec": round(before_max_green, 3),
                "after_max_green_sec": round(after_max_green, 3),
                "anchor_phase_index": anchor_phase_index,
                "yellow_phase_index": yellow_phase_index,
                "anchor_duration_sec": safe_float(anchor_phase.get("duration")),
                "state_before": before_state,
                "state_after": phase_state(anchor_phase, required_len),
            })
    return {
        "short_green_repaired_count": len(rows),
        "short_green_repaired_tls_count": len({row["tls_id"] for row in rows}),
        "short_green_min_sec": min_green_sec,
        "short_green_rows": rows,
    }


def lower_duplicate_major_greens(root: ET.Element) -> dict[str, Any]:
    target_by_tls_index: dict[str, dict[int, str]] = {}
    for connection in root.findall("connection"):
        tls_id = connection.get("tl")
        link_index = connection.get("linkIndex")
        if not tls_id or link_index is None:
            continue
        try:
            index = int(link_index)
        except ValueError:
            continue
        target = f"{connection.get('to', '')}:{connection.get('toLane', '')}"
        target_by_tls_index.setdefault(tls_id, {})[index] = target

    rows: list[dict[str, Any]] = []
    for logic in root.findall("tlLogic"):
        tls_id = str(logic.get("id", ""))
        target_by_index = target_by_tls_index.get(tls_id, {})
        if not target_by_index:
            continue
        for phase_index, phase in enumerate(logic.findall("phase")):
            state = list(phase.get("state", ""))
            major_by_target: dict[str, list[int]] = {}
            for index, char in enumerate(state):
                if char != "G":
                    continue
                target = target_by_index.get(index)
                if target is None:
                    continue
                major_by_target.setdefault(target, []).append(index)
            changed_indices: list[int] = []
            for indices in major_by_target.values():
                for index in indices[1:]:
                    state[index] = "g"
                    changed_indices.append(index)
            if changed_indices:
                before = phase.get("state", "")
                after = "".join(state)
                phase.set("state", after)
                rows.append({
                    "tls_id": tls_id,
                    "programID": logic.get("programID", ""),
                    "phase_index": phase_index,
                    "changed_link_indices": " ".join(str(index) for index in changed_indices),
                    "state_before": before,
                    "state_after": after,
                })
    return {
        "duplicate_major_green_lowered_count": sum(len(str(row["changed_link_indices"]).split()) for row in rows),
        "duplicate_major_green_tls_count": len({row["tls_id"] for row in rows}),
        "duplicate_major_green_rows": rows,
    }


def cap_global_reality_speeds(root: ET.Element, max_speed_mps: float = GLOBAL_REALITY_MAX_SPEED_MPS) -> dict[str, Any]:
    changed_edges = 0
    changed_lanes = 0
    for edge in root.findall("edge"):
        edge_id = str(edge.get("id", ""))
        if edge_id.startswith(":"):
            continue
        edge_changed = False
        for lane in edge.findall("lane"):
            speed = safe_float(lane.get("speed"), 0.0)
            if speed <= max_speed_mps:
                continue
            lane.set("speed", f"{max_speed_mps:.6f}")
            changed_lanes += 1
            edge_changed = True
        if edge_changed:
            changed_edges += 1
    return {
        "speed_cap_policy": "global_reality_all_regular_lanes_capped_50kmh",
        "speed_cap_mps": round(max_speed_mps, 6),
        "speed_cap_changed_edge_count": changed_edges,
        "speed_cap_changed_lane_count": changed_lanes,
    }


def firetruck_route_edges() -> list[str]:
    root = ET.parse(FIRETRUCK_ROUTE_XML).getroot()
    route = root.find(".//route")
    if route is None:
        return []
    return [edge for edge in str(route.get("edges", "")).split() if edge]


def route_edges_from_xml(path: Path) -> list[str]:
    if not path.is_file():
        return []
    root = ET.parse(path).getroot()
    route = root.find(".//route")
    if route is None:
        return []
    return [edge for edge in str(route.get("edges", "")).split() if edge]


def parse_shape(shape: str | None) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for token in str(shape or "").split():
        if "," not in token:
            continue
        try:
            x, y = token.split(",", 1)
            points.append((float(x), float(y)))
        except ValueError:
            continue
    return points


def format_shape(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def transform_shape_between_nets(source_net: Any, target_net: Any, shape: str | None) -> str:
    transformed: list[tuple[float, float]] = []
    for x, y in parse_shape(shape):
        lon, lat = source_net.convertXY2LonLat(x, y)
        target_x, target_y = target_net.convertLonLat2XY(lon, lat)
        transformed.append((float(target_x), float(target_y)))
    return format_shape(transformed)


def edge_by_id(root: ET.Element) -> dict[str, ET.Element]:
    return {
        str(edge.get("id")): edge
        for edge in root.findall("edge")
        if edge.get("id") and str(edge.get("function", "")) != "internal"
    }


def segment_lane_targets() -> dict[tuple[str, str], int]:
    if not TOEGYE_SEGMENT_REFERENCE_CSV.is_file():
        return {}
    targets: dict[tuple[str, str], int] = {}
    for row in read_csv(TOEGYE_SEGMENT_REFERENCE_CSV):
        segment_id = str(row.get("segment_id", "")).strip()
        if not segment_id:
            continue
        targets[(segment_id, "upbound")] = safe_int(row.get("upbound_lanes_to_seoul_station"), 0)
        targets[(segment_id, "downbound")] = safe_int(row.get("downbound_lanes_to_seongdong_high_school"), 0)
    return targets


def mainroad_edge_lane_targets() -> dict[str, int]:
    targets: dict[str, int] = {}
    segment_targets = segment_lane_targets()
    if not B04_MAINROAD_MAPPING_CSV.is_file():
        return targets
    for row in read_csv(B04_MAINROAD_MAPPING_CSV):
        edge_id = str(row.get("edge_id", "")).strip()
        if not edge_id:
            continue
        explicit = safe_int(row.get("target_lanes"), 0)
        segment = str(row.get("segment_id", "")).strip()
        direction = str(row.get("direction", "")).strip()
        reference = segment_targets.get((segment, direction), 0)
        target = max(explicit, reference)
        if target > 0:
            targets[edge_id] = max(targets.get(edge_id, 0), target)
    return targets


def clone_lane_with_shape(source_lane: ET.Element, edge_id: str, index: int, shape: str, fallback_speed: str) -> ET.Element:
    lane = ET.Element("lane", dict(source_lane.attrib))
    lane.set("id", f"{edge_id}_{index}")
    lane.set("index", str(index))
    if fallback_speed:
        lane.set("speed", fallback_speed)
    lane.set("shape", shape)
    points = parse_shape(shape)
    if len(points) >= 2:
        length = sum(math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]) for i in range(len(points) - 1))
        lane.set("length", f"{length:.2f}")
    return lane


def set_regular_edge_lanes(
    edge: ET.Element,
    source_edge: ET.Element | None,
    source_net: Any | None,
    target_net: Any | None,
    target_lanes: int,
) -> dict[str, Any]:
    before_lanes = list(edge.findall("lane"))
    before_count = len(before_lanes)
    if target_lanes <= 0:
        target_lanes = before_count
    target_lanes = max(1, target_lanes)
    fallback_lane = before_lanes[-1] if before_lanes else ET.Element("lane", {"speed": f"{GLOBAL_REALITY_MAX_SPEED_MPS:.6f}"})
    fallback_speed = str(before_lanes[0].get("speed", "")) if before_lanes else f"{GLOBAL_REALITY_MAX_SPEED_MPS:.6f}"
    source_lanes = list(source_edge.findall("lane")) if source_edge is not None else []
    new_lanes: list[ET.Element] = []
    for index in range(target_lanes):
        if source_lanes and source_net is not None and target_net is not None:
            source_lane = source_lanes[min(index, len(source_lanes) - 1)]
            shape = transform_shape_between_nets(source_net, target_net, source_lane.get("shape"))
            lane = clone_lane_with_shape(source_lane, str(edge.get("id")), index, shape, fallback_speed)
        elif index < len(before_lanes):
            lane = ET.Element("lane", dict(before_lanes[index].attrib))
            lane.set("id", f"{edge.get('id')}_{index}")
            lane.set("index", str(index))
        else:
            lane = ET.Element("lane", dict(fallback_lane.attrib))
            lane.set("id", f"{edge.get('id')}_{index}")
            lane.set("index", str(index))
        if safe_float(lane.get("speed"), 0.0) > GLOBAL_REALITY_MAX_SPEED_MPS:
            lane.set("speed", f"{GLOBAL_REALITY_MAX_SPEED_MPS:.6f}")
        new_lanes.append(lane)
    for lane in before_lanes:
        edge.remove(lane)
    for lane in new_lanes:
        edge.append(lane)
    if source_edge is not None and source_net is not None and target_net is not None:
        edge_shape = transform_shape_between_nets(source_net, target_net, source_edge.get("shape"))
        if edge_shape:
            edge.set("shape", edge_shape)
    return {
        "before_lane_count": before_count,
        "after_lane_count": len(new_lanes),
    }


def route_polyline_from_root(root: ET.Element, route_edges: list[str]) -> list[tuple[float, float]]:
    edges = edge_by_id(root)
    points: list[tuple[float, float]] = []
    for edge_id in route_edges:
        edge = edges.get(edge_id)
        if edge is None:
            continue
        lane = edge.find("lane")
        if lane is None:
            continue
        shape = parse_shape(lane.get("shape"))
        if not shape:
            continue
        if points and points[-1] == shape[0]:
            points.extend(shape[1:])
        else:
            points.extend(shape)
    return points


def route_geometry_stats(points: list[tuple[float, float]]) -> dict[str, float]:
    if len(points) < 2:
        return {"point_count": float(len(points)), "length_m": 0.0, "chord_m": 0.0, "extra_length_m": 0.0, "max_lateral_deviation_m": 0.0}
    length = sum(math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]) for i in range(len(points) - 1))
    ax, ay = points[0]
    bx, by = points[-1]
    chord = math.hypot(bx - ax, by - ay)
    den = chord or 1.0
    max_dev = max(abs((by - ay) * x - (bx - ax) * y + bx * ay - by * ax) / den for x, y in points)
    return {
        "point_count": float(len(points)),
        "length_m": round(length, 3),
        "chord_m": round(chord, 3),
        "extra_length_m": round(length - chord, 3),
        "max_lateral_deviation_m": round(max_dev, 3),
    }


def update_junctions_from_regular_edges(root: ET.Element, protected_edge_ids: set[str]) -> dict[str, Any]:
    junction_points: dict[str, list[tuple[float, float]]] = {}
    for edge in root.findall("edge"):
        edge_id = str(edge.get("id", ""))
        if edge_id not in protected_edge_ids or str(edge.get("function", "")) == "internal":
            continue
        lane = edge.find("lane")
        if lane is None:
            continue
        shape = parse_shape(lane.get("shape"))
        if len(shape) < 2:
            continue
        from_id = str(edge.get("from", ""))
        to_id = str(edge.get("to", ""))
        if from_id:
            junction_points.setdefault(from_id, []).append(shape[0])
        if to_id:
            junction_points.setdefault(to_id, []).append(shape[-1])
    moved = 0
    for junction in root.findall("junction"):
        points = junction_points.get(str(junction.get("id", "")))
        if not points:
            continue
        x = sum(point[0] for point in points) / len(points)
        y = sum(point[1] for point in points) / len(points)
        old_x = safe_float(junction.get("x"), x)
        old_y = safe_float(junction.get("y"), y)
        junction.set("x", f"{x:.2f}")
        junction.set("y", f"{y:.2f}")
        shape = parse_shape(junction.get("shape"))
        if shape:
            dx = x - old_x
            dy = y - old_y
            junction.set("shape", format_shape([(sx + dx, sy + dy) for sx, sy in shape]))
        moved += 1
    return {"route_aligned_junction_count": moved}


def internal_lane_by_id(root: ET.Element) -> dict[str, ET.Element]:
    lanes: dict[str, ET.Element] = {}
    for edge in root.findall("edge"):
        edge_id = str(edge.get("id", ""))
        if str(edge.get("function", "")) != "internal" and not edge_id.startswith(":"):
            continue
        for lane in edge.findall("lane"):
            lane_id = str(lane.get("id", ""))
            if lane_id:
                lanes[lane_id] = lane
    return lanes


def align_route_internal_lanes(root: ET.Element, route_edges: list[str]) -> dict[str, Any]:
    regular_edges = edge_by_id(root)
    internal_lanes = internal_lane_by_id(root)
    route_pairs = set(zip(route_edges, route_edges[1:]))
    rows: list[dict[str, Any]] = []
    aligned = 0
    missing = 0
    max_before_offset = 0.0
    for connection in root.findall("connection"):
        from_edge_id = str(connection.get("from", ""))
        to_edge_id = str(connection.get("to", ""))
        if (from_edge_id, to_edge_id) not in route_pairs:
            continue
        via = str(connection.get("via", ""))
        if not via:
            continue
        from_edge = regular_edges.get(from_edge_id)
        to_edge = regular_edges.get(to_edge_id)
        lane = internal_lanes.get(via)
        if from_edge is None or to_edge is None or lane is None:
            missing += 1
            rows.append({
                "from_edge": from_edge_id,
                "to_edge": to_edge_id,
                "via": via,
                "status": "FAIL",
                "before_length_m": "",
                "after_length_m": "",
                "before_midpoint_offset_m": "",
                "reason": "missing_from_to_or_internal_lane",
            })
            continue
        from_lane = from_edge.find(f"./lane[@index='{connection.get('fromLane', '0')}']")
        to_lane = to_edge.find(f"./lane[@index='{connection.get('toLane', '0')}']")
        if from_lane is None:
            from_lane = from_edge.find("lane")
        if to_lane is None:
            to_lane = to_edge.find("lane")
        from_shape = parse_shape(from_lane.get("shape") if from_lane is not None else None)
        to_shape = parse_shape(to_lane.get("shape") if to_lane is not None else None)
        old_shape = parse_shape(lane.get("shape"))
        if len(from_shape) < 2 or len(to_shape) < 2:
            missing += 1
            rows.append({
                "from_edge": from_edge_id,
                "to_edge": to_edge_id,
                "via": via,
                "status": "FAIL",
                "before_length_m": "",
                "after_length_m": "",
                "before_midpoint_offset_m": "",
                "reason": "missing_regular_lane_shape",
            })
            continue
        start = from_shape[-1]
        end = to_shape[0]
        new_shape = [start, end]
        before_length = sum(math.hypot(old_shape[i + 1][0] - old_shape[i][0], old_shape[i + 1][1] - old_shape[i][1]) for i in range(len(old_shape) - 1)) if len(old_shape) >= 2 else 0.0
        after_length = math.hypot(end[0] - start[0], end[1] - start[1])
        before_mid_offset = 0.0
        if old_shape:
            mid = old_shape[len(old_shape) // 2]
            before_mid_offset, _ = point_to_segment_distance(mid, start, end)
            max_before_offset = max(max_before_offset, before_mid_offset)
        lane.set("shape", format_shape(new_shape))
        lane.set("length", f"{after_length:.2f}")
        if safe_float(lane.get("speed"), 0.0) > GLOBAL_REALITY_MAX_SPEED_MPS:
            lane.set("speed", f"{GLOBAL_REALITY_MAX_SPEED_MPS:.6f}")
        aligned += 1
        rows.append({
            "from_edge": from_edge_id,
            "to_edge": to_edge_id,
            "via": via,
            "status": "PASS",
            "before_length_m": round(before_length, 3),
            "after_length_m": round(after_length, 3),
            "before_midpoint_offset_m": round(before_mid_offset, 3),
            "reason": "route_internal_lane_straightened",
        })
    write_csv(ROUTE_INTERNAL_LANE_AUDIT_CSV, rows, [
        "from_edge", "to_edge", "via", "status", "before_length_m", "after_length_m", "before_midpoint_offset_m", "reason",
    ])
    return {
        "route_internal_lane_audit_csv": rel(ROUTE_INTERNAL_LANE_AUDIT_CSV),
        "route_internal_lane_aligned_count": aligned,
        "route_internal_lane_missing_count": missing,
        "route_internal_lane_status": "PASS" if missing == 0 else "FAIL",
        "route_internal_lane_max_before_midpoint_offset_m": round(max_before_offset, 3),
    }


def apply_b0_route_geometry_and_lane_recall(root: ET.Element, input_net: Path) -> dict[str, Any]:
    route_edges = route_edges_from_xml(FIRETRUCK_ROUTE_XML)
    reference_route_edges = route_edges_from_xml(B0_ROUTE_REFERENCE_ROUTE_XML)
    current_edges = edge_by_id(root)
    reference_root = ET.parse(B0_ROUTE_REFERENCE_NET).getroot()
    reference_edges = edge_by_id(reference_root)
    input_root = ET.parse(input_net).getroot()
    source_net = sumolib.net.readNet(str(B0_ROUTE_REFERENCE_NET))
    target_net = sumolib.net.readNet(str(input_net))
    route_edge_set = set(route_edges)
    lane_targets = mainroad_edge_lane_targets()
    for edge_id in route_edges:
        if edge_id in reference_edges:
            lane_targets[edge_id] = max(lane_targets.get(edge_id, 0), len(reference_edges[edge_id].findall("lane")))
    protected_edge_ids = set(lane_targets) | route_edge_set

    before_stats = route_geometry_stats(route_polyline_from_root(input_root, route_edges))
    reference_stats = route_geometry_stats(route_polyline_from_root(reference_root, route_edges))
    missing_route_edges = [edge_id for edge_id in route_edges if edge_id not in current_edges]
    lane_rows: list[dict[str, Any]] = []
    geometry_recalled_edges = 0
    lane_changed_edges = 0
    for edge_id in sorted(protected_edge_ids):
        edge = current_edges.get(edge_id)
        if edge is None:
            lane_rows.append({
                "edge_id": edge_id,
                "edge_role": "route" if edge_id in route_edge_set else "mainroad",
                "current_lanes_before": "",
                "reference_lanes": len(reference_edges[edge_id].findall("lane")) if edge_id in reference_edges else "",
                "target_lanes": lane_targets.get(edge_id, ""),
                "actual_lanes_after": "",
                "status": "FAIL",
                "reason": "edge_missing_in_current_net",
            })
            continue
        before_count = len(edge.findall("lane"))
        reference_edge = reference_edges.get(edge_id)
        reference_count = len(reference_edge.findall("lane")) if reference_edge is not None else 0
        target_count = max(before_count, lane_targets.get(edge_id, 0), reference_count if edge_id in route_edge_set else 0)
        result = set_regular_edge_lanes(edge, reference_edge, source_net if reference_edge is not None else None, target_net if reference_edge is not None else None, target_count)
        after_count = result["after_lane_count"]
        if reference_edge is not None:
            geometry_recalled_edges += 1
        if after_count != before_count:
            lane_changed_edges += 1
        expected = max(lane_targets.get(edge_id, 0), reference_count if edge_id in route_edge_set else 0)
        status = "PASS" if after_count >= expected else "FAIL"
        if expected >= 3 and after_count <= 1:
            status = "FAIL"
        lane_rows.append({
            "edge_id": edge_id,
            "edge_role": "route+mainroad" if edge_id in route_edge_set and edge_id in lane_targets else ("route" if edge_id in route_edge_set else "mainroad"),
            "current_lanes_before": before_count,
            "reference_lanes": reference_count or "",
            "target_lanes": expected,
            "actual_lanes_after": after_count,
            "status": status,
            "reason": "b0_route_geometry_lane_recall" if reference_edge is not None else "mainroad_lane_target_recall",
        })
    junction_stats = update_junctions_from_regular_edges(root, protected_edge_ids)
    internal_stats = align_route_internal_lanes(root, route_edges)
    after_stats = route_geometry_stats(route_polyline_from_root(root, route_edges))
    write_csv(MAINROAD_LANE_RECALL_AUDIT_CSV, lane_rows, [
        "edge_id", "edge_role", "current_lanes_before", "reference_lanes", "target_lanes", "actual_lanes_after", "status", "reason",
    ])
    failed_rows = [row for row in lane_rows if row["status"] != "PASS"]
    geometry_status = (
        "PASS"
        if route_edges == reference_route_edges
        and not missing_route_edges
        and after_stats["extra_length_m"] <= max(reference_stats["extra_length_m"] + 30.0, 260.0)
        else "FAIL"
    )
    lane_status = "PASS" if not failed_rows else "FAIL"
    audit = {
        "schema": "compact_v9_b04_b0_route_geometry_lane_recall.v1",
        "generated_at": utc_now(),
        "reference_net": rel(B0_ROUTE_REFERENCE_NET),
        "reference_route_xml": rel(B0_ROUTE_REFERENCE_ROUTE_XML),
        "current_route_xml": rel(FIRETRUCK_ROUTE_XML),
        "route_edge_count": len(route_edges),
        "route_edges_match_reference": route_edges == reference_route_edges,
        "missing_route_edge_count": len(missing_route_edges),
        "missing_route_edges": missing_route_edges,
        "before_geometry": before_stats,
        "reference_geometry": reference_stats,
        "after_geometry": after_stats,
        "geometry_recalled_edge_count": geometry_recalled_edges,
        "lane_changed_edge_count": lane_changed_edges,
        "protected_edge_count": len(protected_edge_ids),
        "lane_audit_csv": rel(MAINROAD_LANE_RECALL_AUDIT_CSV),
        "lane_fail_count": len(failed_rows),
        "geometry_status": geometry_status,
        "lane_status": lane_status,
        "status": "PASS" if geometry_status == "PASS" and lane_status == "PASS" else "FAIL",
        **junction_stats,
        **internal_stats,
    }
    write_json(ROUTE_GEOMETRY_RECALL_AUDIT_JSON, audit)
    return audit


def point_to_segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
    px, py = point
    ax, ay = start
    bx, by = end
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom <= 0.0:
        return math.hypot(px - ax, py - ay), 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    qx = ax + t * dx
    qy = ay + t * dy
    return math.hypot(px - qx, py - qy), t


def route_pair_stopline_point(root: ET.Element, from_edge_id: str, to_edge_id: str) -> tuple[float, float] | None:
    edges = edge_by_id(root)
    from_edge = edges.get(from_edge_id)
    to_edge = edges.get(to_edge_id)
    if from_edge is None or to_edge is None:
        return None
    from_lane = from_edge.find("lane")
    to_lane = to_edge.find("lane")
    if from_lane is None or to_lane is None:
        return None
    from_shape = parse_shape(from_lane.get("shape"))
    to_shape = parse_shape(to_lane.get("shape"))
    points: list[tuple[float, float]] = []
    if from_shape:
        points.append(from_shape[-1])
    if to_shape:
        points.append(to_shape[0])
    if not points:
        return None
    return sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points)


def tls_control_centroid(root: ET.Element, tls_id: str) -> tuple[float, float] | None:
    points: list[tuple[float, float]] = []
    edges = edge_by_id(root)
    for connection in root.findall("connection"):
        if connection.get("tl") != tls_id:
            continue
        from_edge = edges.get(str(connection.get("from", "")))
        to_edge = edges.get(str(connection.get("to", "")))
        if from_edge is not None:
            lane = from_edge.find(f"./lane[@index='{connection.get('fromLane', '0')}']")
            if lane is None:
                lane = from_edge.find("lane")
            shape = parse_shape(lane.get("shape") if lane is not None else "")
            if shape:
                points.append(shape[-1])
        if to_edge is not None:
            lane = to_edge.find(f"./lane[@index='{connection.get('toLane', '0')}']")
            if lane is None:
                lane = to_edge.find("lane")
            shape = parse_shape(lane.get("shape") if lane is not None else "")
            if shape:
                points.append(shape[0])
    if not points:
        return None
    return sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points)


def write_route_tls_projection_audit(root: ET.Element) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if not CSV_SIGNAL_CANDIDATES_CSV.is_file():
        write_csv(ROUTE_TLS_PROJECTION_AUDIT_CSV, rows, [
            "tls_id", "boundary_id", "route_from_edge", "route_to_edge", "projection_distance_m", "status", "reason",
        ])
        return {
            "route_tls_projection_csv": rel(ROUTE_TLS_PROJECTION_AUDIT_CSV),
            "route_tls_projection_status": "SKIP",
            "route_tls_projection_fail_count": 0,
            "route_tls_projection_row_count": 0,
        }
    for row in read_csv(CSV_SIGNAL_CANDIDATES_CSV):
        tls_id = str(row.get("tls_id", "")).strip()
        from_edge = str(row.get("route_from_edge", "")).strip()
        to_edge = str(row.get("route_to_edge", "")).strip()
        if not tls_id or not from_edge or not to_edge:
            continue
        stopline = route_pair_stopline_point(root, from_edge, to_edge)
        centroid = tls_control_centroid(root, tls_id)
        if stopline is None:
            status = "FAIL"
            distance = ""
            reason = "route_pair_missing"
        elif centroid is None:
            status = "FAIL"
            distance = ""
            reason = "tls_control_centroid_missing"
        else:
            distance_value = math.hypot(centroid[0] - stopline[0], centroid[1] - stopline[1])
            status = "PASS" if distance_value <= 35.0 else "WARN"
            distance = round(distance_value, 3)
            reason = "route_pair_stopline_projection"
        rows.append({
            "tls_id": tls_id,
            "boundary_id": row.get("boundary_id", ""),
            "signal_type": row.get("signal_type", ""),
            "route_from_edge": from_edge,
            "route_to_edge": to_edge,
            "projection_distance_m": distance,
            "status": status,
            "reason": reason,
        })
    write_csv(ROUTE_TLS_PROJECTION_AUDIT_CSV, rows, [
        "tls_id", "boundary_id", "signal_type", "route_from_edge", "route_to_edge", "projection_distance_m", "status", "reason",
    ])
    fail_count = sum(1 for row in rows if row["status"] == "FAIL")
    warn_count = sum(1 for row in rows if row["status"] == "WARN")
    return {
        "route_tls_projection_csv": rel(ROUTE_TLS_PROJECTION_AUDIT_CSV),
        "route_tls_projection_status": "PASS" if fail_count == 0 else "FAIL",
        "route_tls_projection_row_count": len(rows),
        "route_tls_projection_warn_count": warn_count,
        "route_tls_projection_fail_count": fail_count,
    }


def vehicle_weight(element: ET.Element) -> float:
    if element.tag == "vehicle":
        return 1.0
    if element.tag == "flow":
        if element.get("number") not in (None, ""):
            return max(0.0, safe_float(element.get("number"), 0.0))
        begin = safe_float(element.get("begin"), 0.0)
        end = safe_float(element.get("end"), begin)
        period = safe_float(element.get("period"), 0.0)
        if period > 0 and end > begin:
            return max(0.0, (end - begin) / period)
    return 0.0


def route_edges_for_vehicle(element: ET.Element, route_defs: dict[str, list[str]]) -> list[str]:
    child_route = element.find("route")
    if child_route is not None:
        return [edge for edge in str(child_route.get("edges", "")).split() if edge]
    route_id = str(element.get("route", ""))
    return route_defs.get(route_id, [])


def tls_route_demand_counts(input_net: Path, demand_route: Path | None) -> dict[str, Any]:
    if demand_route is None or not demand_route.is_file():
        return {
            "demand_route": rel(demand_route) if demand_route else "",
            "demand_source_status": "missing",
            "tls_counts": {},
            "vehicle_weight_total": 0.0,
            "matched_transition_total": 0.0,
        }
    net_root = ET.parse(input_net).getroot()
    pair_to_tls: dict[tuple[str, str], set[str]] = {}
    for connection in net_root.findall("connection"):
        tls_id = connection.get("tl")
        from_edge = connection.get("from")
        to_edge = connection.get("to")
        if not tls_id or not from_edge or not to_edge:
            continue
        pair_to_tls.setdefault((from_edge, to_edge), set()).add(tls_id)

    route_root = ET.parse(demand_route).getroot()
    route_defs = {
        str(route.get("id")): [edge for edge in str(route.get("edges", "")).split() if edge]
        for route in route_root.findall("route")
        if route.get("id")
    }
    tls_counts: dict[str, float] = {}
    vehicle_weight_total = 0.0
    matched_transition_total = 0.0
    for element in list(route_root.findall("vehicle")) + list(route_root.findall("flow")):
        weight = vehicle_weight(element)
        if weight <= 0.0:
            continue
        edges = route_edges_for_vehicle(element, route_defs)
        if len(edges) < 2:
            continue
        vehicle_weight_total += weight
        touched_tls: set[str] = set()
        for pair in zip(edges, edges[1:]):
            for tls_id in pair_to_tls.get(pair, set()):
                tls_counts[tls_id] = tls_counts.get(tls_id, 0.0) + weight
                touched_tls.add(tls_id)
        matched_transition_total += len(touched_tls) * weight
    return {
        "demand_route": rel(demand_route),
        "demand_source_status": "loaded",
        "tls_counts": tls_counts,
        "vehicle_weight_total": round(vehicle_weight_total, 3),
        "matched_transition_total": round(matched_transition_total, 3),
    }


def demand_percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * pct))))
    return ordered[index]


def demand_signal_factors(tls_list: list[TlsPoint], demand_counts: dict[str, float]) -> dict[str, dict[str, Any]]:
    counts = [float(demand_counts.get(tls.tls_id, 0.0)) for tls in tls_list]
    links = [float(tls.link_count) for tls in tls_list]
    median_count = demand_percentile(counts, 0.50)
    p10_count = demand_percentile(counts, 0.10)
    p90_count = max(demand_percentile(counts, 0.90), median_count + 1.0)
    median_link = demand_percentile(links, 0.50)
    stats: dict[str, dict[str, Any]] = {}
    for tls in tls_list:
        count = float(demand_counts.get(tls.tls_id, 0.0))
        if count >= median_count:
            denom = max(1.0, math.log1p(p90_count) - math.log1p(median_count))
            pressure = (math.log1p(count) - math.log1p(median_count)) / denom
        else:
            denom = max(1.0, math.log1p(median_count) - math.log1p(p10_count))
            pressure = -((math.log1p(median_count) - math.log1p(count)) / denom)
        structure = (float(tls.link_count) - median_link) / 10.0
        factor = max(-1.0, min(1.0, pressure * 0.75 + structure * 0.25))
        cycle_delta = int(round(factor * 10.0 / 5.0) * 5)
        green_delta = int(round(factor * 8.0 / 5.0) * 5)
        stats[tls.tls_id] = {
            "demand_count": round(count, 3),
            "demand_pressure_factor": round(factor, 3),
            "demand_cycle_delta_sec": cycle_delta,
            "demand_main_green_delta_sec": green_delta,
        }
    return stats


def demand_adjusted_profile(profile: Any, demand_stat: dict[str, Any]) -> Any:
    if profile.profile_role == "virtual_merge_control":
        return profile
    yellow = int(profile.yellow_sec)
    cycle = clamp_int(int(profile.cycle_sec) + safe_int(demand_stat.get("demand_cycle_delta_sec")), 70, 140)
    main_green = clamp_int(
        int(profile.main_green_sec) + safe_int(demand_stat.get("demand_main_green_delta_sec")),
        18,
        cycle - 2 * yellow - 12,
    )
    side_green = max(12, cycle - main_green - 2 * yellow)
    return tdp.SignalProfile(
        tls_id=profile.tls_id,
        profile_role=profile.profile_role,
        source=profile.source,
        source_itst_id=profile.source_itst_id,
        source_eqmn_id=profile.source_eqmn_id,
        source_tls_id=profile.source_tls_id,
        movement_ids=profile.movement_ids,
        mapped_segments=profile.mapped_segments,
        route_order_min=profile.route_order_min,
        cycle_sec=cycle,
        main_green_sec=main_green,
        side_green_sec=side_green,
        yellow_sec=yellow,
        offset_sec=int(profile.offset_sec) % cycle,
        confidence=profile.confidence,
        dominant_api_field=profile.dominant_api_field,
        dominant_remaining_sec=profile.dominant_remaining_sec,
        inference_reason=(
            f"{profile.inference_reason}; demand-proportional mapwide adjustment "
            f"count={demand_stat.get('demand_count', 0)}, factor={demand_stat.get('demand_pressure_factor', 0)}"
        ),
    )


def promote_firetruck_route_priority_connections(root: ET.Element) -> dict[str, Any]:
    route_edges = firetruck_route_edges()
    route_pairs = set(zip(route_edges, route_edges[1:]))
    updated: list[dict[str, Any]] = []
    for conn in root.findall("connection"):
        pair = (conn.get("from", ""), conn.get("to", ""))
        if pair not in route_pairs:
            continue
        if conn.get("tl"):
            continue
        if conn.get("state") not in {"m", "o"}:
            continue
        before = conn.get("state", "")
        conn.set("state", "M")
        updated.append({
            "from": pair[0],
            "to": pair[1],
            "fromLane": conn.get("fromLane", ""),
            "toLane": conn.get("toLane", ""),
            "via": conn.get("via", ""),
            "state_before": before,
            "state_after": "M",
        })
    return {
        "policy": "firetruck_route_uncontrolled_minor_connections_promoted",
        "status": "UPDATED" if updated else "NOOP",
        "route_edge_count": len(route_edges),
        "updated_connection_count": len(updated),
        "updated_connections": updated,
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


def csv_signal_position_overrides(net: Any) -> dict[str, tuple[float, float, float, float]]:
    overrides: dict[str, tuple[float, float, float, float]] = {}
    if not CSV_SIGNAL_CANDIDATES_CSV.is_file():
        return overrides
    for row in read_csv(CSV_SIGNAL_CANDIDATES_CSV):
        tls_id = str(row.get("tls_id", "")).strip()
        lat = safe_float(row.get("csv_lat"), 0.0)
        lon = safe_float(row.get("csv_lon"), 0.0)
        if not tls_id or not lat or not lon:
            continue
        try:
            x, y = net.convertLonLat2XY(lon, lat)
        except Exception:
            continue
        overrides[tls_id] = (float(x), float(y), float(lat), float(lon))
    return overrides


def tls_points(input_net: Path) -> list[TlsPoint]:
    net = sumolib.net.readNet(str(input_net))
    phase_counts = logic_phase_count(input_net)
    link_counts = logic_link_count(input_net)
    node_ids = {node.getID() for node in net.getNodes()}
    csv_position_overrides = csv_signal_position_overrides(net)
    points: list[TlsPoint] = []
    for tls in net.getTrafficLights():
        tls_id = tls.getID()
        coord_source = "junction"
        if tls_id in csv_position_overrides:
            x, y, lat, lon = csv_position_overrides[tls_id]
            coord_source = "csv_reference_signal_position"
        elif tls_id in node_ids:
            x, y = net.getNode(tls_id).getCoord()
            lon, lat = net.convertXY2LonLat(x, y)
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


def existing_logic_profile(tls: TlsPoint, logic: ET.Element | None) -> Any:
    phases = list(logic.findall("phase")) if logic is not None else []
    cycle = int(round(sum(safe_float(phase.get("duration")) for phase in phases))) if phases else 60
    yellow = 3
    yellow_total = 0.0
    main_green = 0.0
    for phase in phases:
        duration = safe_float(phase.get("duration"))
        state = str(phase.get("state", ""))
        if "y" in state or "Y" in state:
            yellow_total += duration
        elif state.count("G") * 2 + state.count("g") > 0:
            main_green = max(main_green, duration)
    if main_green <= 0.0:
        main_green = max(1.0, cycle - 2 * yellow)
    side_green = max(0, int(round(cycle - main_green - yellow_total)))
    return tdp.SignalProfile(
        tls_id=tls.tls_id,
        profile_role="virtual_merge_control",
        source="virtual_merge_tls_preserved_no_A008_claim",
        source_itst_id="",
        source_eqmn_id="",
        source_tls_id=tls.tls_id,
        movement_ids="",
        mapped_segments="virtual_merge_control",
        route_order_min=0.0,
        cycle_sec=max(1, cycle),
        main_green_sec=max(1, int(round(main_green))),
        side_green_sec=side_green,
        yellow_sec=yellow,
        offset_sec=safe_int(logic.get("offset") if logic is not None else 0),
        confidence=1.0,
        dominant_api_field="",
        dominant_remaining_sec=0.0,
        inference_reason="Preserved virtual fire-station merge TLS; excluded from physical A008/T-Data mapping.",
    )


def profile_from_timing(tls: TlsPoint, itst_id: str, a008_name: str, timing: Any, nearest_rank: float) -> Any:
    dominant = timing.dominant_remaining_sec
    median = timing.median_remaining_sec
    cycle = clamp_int(round_to_5(max(60.0, median * 2.0, dominant + 6.0)), 60, 140)
    yellow = 3
    main_green = clamp_int(round_to_5(median), 18, cycle - 2 * yellow - 12)
    side_green = max(12, cycle - main_green - 2 * yellow)
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
        inference_reason=(
            f"nearest A008={a008_name}; timing hit for itstId={itst_id}; "
            "G/Y/R inferred from RmdrCs remaining-time statistics, not direct API color durations"
        ),
    )


def profile_from_source(tls: TlsPoint, source: Any, a008: A008Point, dist_to_a008: float, dist_to_source_tls: float) -> Any:
    cycle = int(source.cycle_sec)
    main_green = clamp_int(source.main_green_sec * 0.78, 18, cycle - 20)
    side_green = max(12, cycle - main_green - 2 * int(source.yellow_sec))
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


def stable_index(seed: str) -> int:
    return sum(ord(char) for char in seed)


def fallback_timing_family(tls: TlsPoint, averages: dict[str, float]) -> dict[str, int]:
    yellow = int(averages["yellow_sec"])
    family_index = (stable_index(tls.tls_id) + tls.phase_count + tls.link_count) % 6
    cycle = clamp_int(int(averages["cycle_sec"]) + [-10, -5, 0, 5, 10, 15][family_index], 80, 115)
    main_green = clamp_int(
        int(averages["main_green_sec"]) + [-16, -11, -6, -1, 3, 6][family_index],
        28,
        cycle - 2 * yellow - 16,
    )
    return {"cycle_sec": cycle, "main_green_sec": main_green, "yellow_sec": yellow}


def average_profile(tls: TlsPoint, a008: A008Point, dist_to_a008: float, averages: dict[str, float]) -> Any:
    timing = fallback_timing_family(tls, averages)
    cycle = timing["cycle_sec"]
    yellow = timing["yellow_sec"]
    main_green = timing["main_green_sec"]
    side_green = max(12, cycle - main_green - 2 * yellow)
    offset = int((tls.x * 0.015 + tls.y * 0.009 + tls.link_count * 3.0) % cycle)
    return tdp.SignalProfile(
        tls_id=tls.tls_id,
        profile_role="global_average_fallback",
        source="realistic_API_measured_route_family_fallback",
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
        inference_reason=(
            f"nearest A008={a008.name} ({dist_to_a008:.1f}m), no direct timing hit; "
            "used measured T-Data profile average route-family timing"
        ),
    )


def profile_averages(profiles: list[Any]) -> dict[str, float]:
    if not profiles:
        return {"cycle_sec": 100.0, "main_green_sec": 70.0, "yellow_sec": 3.0}
    return {
        "cycle_sec": round(sum(p.cycle_sec for p in profiles) / len(profiles)),
        "main_green_sec": round(sum(p.main_green_sec for p in profiles) / len(profiles)),
        "yellow_sec": round(sum(p.yellow_sec for p in profiles) / len(profiles)),
    }


def measured_profile_averages(profiles: list[Any]) -> dict[str, float]:
    measured = [
        profile for profile in profiles
        if "TData_SPAT" in str(profile.source) or str(profile.source).endswith("_direct")
    ]
    return profile_averages(measured or profiles)


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


def phase_green_score(phase: ET.Element) -> int:
    state = phase.get("state", "")
    return state.count("G") * 2 + state.count("g")


def yellow_state_from_green(state: str) -> str:
    return "".join("y" if char in {"G", "g", "y", "Y"} else "r" for char in state)


def all_red_state(length: int) -> str:
    return "r" * max(1, length)


def apply_mainroad_semantic_profile(logic: ET.Element, profile: Any, selected_indices: list[int] | None) -> dict[str, Any]:
    phases = list(logic.findall("phase"))
    if not phases:
        return {"status": "SKIP", "reason": "no_phase"}
    before_cycle = sum(safe_float(phase.get("duration")) for phase in phases)
    valid_main = [index for index in (selected_indices or []) if 0 <= index < len(phases)]
    if not valid_main:
        valid_main = tdp.auto_main_phase_indices(phases)
    valid_main = valid_main[:3]
    yellow = max(1, int(profile.yellow_sec))
    target = max(30, int(profile.cycle_sec))
    main_total = clamp_int(int(profile.main_green_sec), 12 * len(valid_main), target - yellow * (len(valid_main) + 1) - 6)
    main_durations = tdp.distribute(main_total, len(valid_main), minimum=8)
    side_green = max(6, target - sum(main_durations) - yellow * (len(valid_main) + 1))
    delta = target - (sum(main_durations) + side_green + yellow * (len(valid_main) + 1))
    side_green += delta

    state_len = max(len(phase.get("state", "")) for phase in phases)
    side_candidates = [
        (phase_green_score(phase), index, phase)
        for index, phase in enumerate(phases)
        if index not in valid_main and not tdp.is_yellow_phase(phase) and phase_green_score(phase) > 0
    ]
    side_state = max(side_candidates)[2].get("state", "") if side_candidates else all_red_state(state_len)
    side_state = (side_state + all_red_state(state_len))[:state_len]
    side_yellow = yellow_state_from_green(side_state)

    specs: list[tuple[int, str, str]] = []
    new_main_indices: list[int] = []
    for order, (phase_index, duration) in enumerate(zip(valid_main, main_durations, strict=True), start=1):
        green_state = (phases[phase_index].get("state", "") + all_red_state(state_len))[:state_len]
        yellow_index = phase_index + 1
        if yellow_index < len(phases) and tdp.is_yellow_phase(phases[yellow_index]):
            yellow_state = (phases[yellow_index].get("state", "") + all_red_state(state_len))[:state_len]
        else:
            yellow_state = yellow_state_from_green(green_state)
        new_main_indices.append(len(specs))
        specs.append((duration, green_state, f"mainroad_green_{order}"))
        specs.append((yellow, yellow_state, f"mainroad_yellow_{order}"))
    specs.append((side_green, side_state, "side_green"))
    specs.append((yellow, side_yellow, "side_yellow"))

    for phase in phases:
        logic.remove(phase)
    for duration, state, name in specs:
        ET.SubElement(logic, "phase", {"duration": str(int(duration)), "state": state, "name": name})
    logic.set("type", "static")
    logic.set("programID", "GLOBAL_REALITY")
    logic.set("offset", str(int(profile.offset_sec)))
    return {
        "status": "APPLIED_MAINROAD_SEMANTIC",
        "before_cycle_sec": round(before_cycle, 3),
        "after_cycle_sec": sum(item[0] for item in specs),
        "phase_count": len(specs),
        "main_phase_indices": " ".join(str(index) for index in new_main_indices),
        "yellow_phase_count": len(valid_main) + 1,
    }


def mainroad_phase_indices() -> dict[str, list[int]]:
    if not (TDATA_ROOT / "a008_tls_itst_mapping.csv").is_file():
        return {}
    return {
        str(row.get("tls_id", "")): tdp.parse_index_list(str(row.get("selected_green_phases", "")))
        for row in read_csv(TDATA_ROOT / "a008_tls_itst_mapping.csv")
        if row.get("tls_id")
    }


def build_profiles(input_net: Path, timing_snapshots: list[Path], demand_route: Path | None) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    tls_list = tls_points(input_net)
    a008_points = load_a008_points()
    timing_by_itst = averaged_timing_by_itst(timing_snapshots)
    main_profiles = main_profile_objects()
    net_root = ET.parse(input_net).getroot()
    logic_by_tls = {str(logic.get("id", "")): logic for logic in net_root.findall("tlLogic") if logic.get("id")}
    direct_profiles: list[Any] = []
    mapping_rows: list[dict[str, Any]] = []
    profiles_by_tls: dict[str, Any] = {}

    for tls in tls_list:
        if tls.tls_id in VIRTUAL_TLS_IDS:
            profile = existing_logic_profile(tls, logic_by_tls.get(tls.tls_id))
            profiles_by_tls[tls.tls_id] = profile
            mapping_rows.append({
                "tls_id": tls.tls_id,
                "tls_lat": round(tls.lat, 8),
                "tls_lon": round(tls.lon, 8),
                "coord_source": tls.coord_source,
                "link_count": tls.link_count,
                "phase_count": tls.phase_count,
                "itst_id": "",
                "a008_name": "virtual_merge_control",
                "a008_lat": "",
                "a008_lon": "",
                "distance_m": "",
                "timing_hit": False,
                "source_kind": "virtual_preserved",
            })
            continue
        a008, dist = nearest_a008(tls, a008_points)
        timing = timing_by_itst.get(a008.itst_id)
        if tls.tls_id in main_profiles:
            profile = main_profiles[tls.tls_id]
            source_kind = "mainroad_profile"
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

    source_profiles = [
        profile for profile in profiles_by_tls.values()
        if profile.profile_role != "virtual_merge_control"
    ]
    averages = measured_profile_averages(source_profiles)
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
        profile = average_profile(tls, a008, dist_to_a008, averages)
        row["source_kind"] = "average_fallback"
        profiles_by_tls[tls.tls_id] = profile

    demand_payload = tls_route_demand_counts(input_net, demand_route)
    demand_stats_by_tls = demand_signal_factors(tls_list, demand_payload["tls_counts"])
    for tls in tls_list:
        if tls.tls_id in profiles_by_tls:
            profiles_by_tls[tls.tls_id] = demand_adjusted_profile(
                profiles_by_tls[tls.tls_id],
                demand_stats_by_tls.get(tls.tls_id, {}),
            )
    for row in mapping_rows:
        row.update(demand_stats_by_tls.get(str(row["tls_id"]), {}))

    demand_rows = [
        {
            "tls_id": tls.tls_id,
            "link_count": tls.link_count,
            "phase_count": tls.phase_count,
            **demand_stats_by_tls.get(tls.tls_id, {}),
        }
        for tls in tls_list
    ]
    profiles = [profiles_by_tls[tls.tls_id] for tls in tls_list if tls.tls_id in profiles_by_tls]
    stats = {
        "tls_count": len(tls_list),
        "mainroad_profile_count": sum(1 for row in mapping_rows if row["source_kind"] == "mainroad_profile"),
        "mainroad_preserved_count": 0,
        "virtual_preserved_count": sum(1 for row in mapping_rows if row["source_kind"] == "virtual_preserved"),
        "direct_api_count": sum(1 for row in mapping_rows if row["source_kind"] == "direct_api"),
        "nearest_fallback_count": sum(1 for row in mapping_rows if row["source_kind"] == "nearest_source_fallback"),
        "average_fallback_count": sum(1 for row in mapping_rows if row["source_kind"] == "average_fallback"),
        "timing_record_itst_count": len(timing_by_itst),
        "profile_average": averages,
        "demand_route": demand_payload["demand_route"],
        "demand_source_status": demand_payload["demand_source_status"],
        "demand_vehicle_weight_total": demand_payload["vehicle_weight_total"],
        "demand_matched_transition_total": demand_payload["matched_transition_total"],
        "demand_adjusted_tls_count": sum(1 for row in demand_rows if safe_float(row.get("demand_count")) > 0.0),
        "demand_profile_policy": "mapwide_tls_route_pair_volume_proportional_cycle_and_main_green_adjustment",
    }
    return profiles, mapping_rows, demand_rows, stats


def apply_profiles(input_net: Path, output_net: Path, profiles: list[Any]) -> dict[str, Any]:
    tree = ET.parse(input_net)
    root = tree.getroot()
    by_tls = {profile.tls_id: profile for profile in profiles}
    applied_rows: list[dict[str, Any]] = []
    applied_count = 0
    single_repaired = 0
    phase_indices_by_tls = mainroad_phase_indices()
    for logic in root.findall("tlLogic"):
        tls_id = str(logic.get("id", ""))
        profile = by_tls.get(tls_id)
        if profile is None:
            continue
        if profile.profile_role == "location_matched_mainroad":
            result = apply_mainroad_semantic_profile(logic, profile, phase_indices_by_tls.get(tls_id))
        elif profile.profile_role == "virtual_merge_control":
            result = {
                "status": "PRESERVED_VIRTUAL",
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
        if (
            str(result.get("status", "")).startswith("APPLIED")
            or result.get("status") in {"PRESERVED_MAINROAD", "PRESERVED_VIRTUAL"}
        ):
            applied_count += 1
        applied_rows.append(profile.as_row() | result)
    normalize_stats = normalize_tls_phase_state_lengths(root)
    missing_green_stats = repair_missing_green_links(root)
    short_green_stats = repair_short_green_links(root)
    duplicate_green_stats = lower_duplicate_major_greens(root)
    route_geometry_recall_stats = apply_b0_route_geometry_and_lane_recall(root, input_net)
    route_tls_projection_stats = write_route_tls_projection_audit(root)
    speed_cap_stats = cap_global_reality_speeds(root)
    firetruck_priority_summary = promote_firetruck_route_priority_connections(root)
    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="UTF-8", xml_declaration=True)
    write_csv(GLOBAL_APPLIED_CSV, applied_rows, list(applied_rows[0].keys()) if applied_rows else [])
    normalized_rows = normalize_stats.pop("normalized_rows")
    normalized_csv = TDATA_ROOT / "b04_global_reality_tls_state_normalization.csv"
    write_csv(normalized_csv, normalized_rows, list(normalized_rows[0].keys()) if normalized_rows else [
        "tls_id", "programID", "phase_index", "action", "before_len", "after_len", "required_len",
    ])
    missing_green_rows = missing_green_stats.pop("missing_green_rows")
    missing_green_csv = TDATA_ROOT / "b04_global_reality_missing_green_repair.csv"
    write_csv(missing_green_csv, missing_green_rows, list(missing_green_rows[0].keys()) if missing_green_rows else [
        "tls_id", "programID", "linkIndex", "action", "anchor_phase_index", "yellow_phase_index",
        "anchor_duration_sec", "state_before", "state_after",
    ])
    short_green_rows = short_green_stats.pop("short_green_rows")
    short_green_csv = TDATA_ROOT / "b04_global_reality_short_green_repair.csv"
    write_csv(short_green_csv, short_green_rows, list(short_green_rows[0].keys()) if short_green_rows else [
        "tls_id", "programID", "linkIndex", "before_max_green_sec", "after_max_green_sec",
        "anchor_phase_index", "yellow_phase_index", "anchor_duration_sec", "state_before", "state_after",
    ])
    duplicate_green_rows = duplicate_green_stats.pop("duplicate_major_green_rows")
    duplicate_green_csv = TDATA_ROOT / "b04_global_reality_duplicate_major_green_normalization.csv"
    write_csv(duplicate_green_csv, duplicate_green_rows, list(duplicate_green_rows[0].keys()) if duplicate_green_rows else [
        "tls_id", "programID", "phase_index", "changed_link_indices", "state_before", "state_after",
    ])
    return {
        "input_net": rel(input_net),
        "output_net": rel(output_net),
        "applied_tls_count": applied_count,
        "single_phase_repaired_count": single_repaired,
        "applied_csv": rel(GLOBAL_APPLIED_CSV),
        "tls_state_normalization_csv": rel(normalized_csv),
        "missing_green_repair_csv": rel(missing_green_csv),
        "short_green_repair_csv": rel(short_green_csv),
        "duplicate_major_green_normalization_csv": rel(duplicate_green_csv),
        "firetruck_route_priority": firetruck_priority_summary,
        "route_geometry_recall": route_geometry_recall_stats,
        **route_tls_projection_stats,
        **normalize_stats,
        **missing_green_stats,
        **short_green_stats,
        **duplicate_green_stats,
        **speed_cap_stats,
    }


def normalize_existing_net(input_net: Path, output_net: Path) -> dict[str, Any]:
    tree = ET.parse(input_net)
    root = tree.getroot()
    normalize_stats = normalize_tls_phase_state_lengths(root)
    missing_green_stats = repair_missing_green_links(root)
    short_green_stats = repair_short_green_links(root)
    duplicate_green_stats = lower_duplicate_major_greens(root)
    route_geometry_recall_stats = apply_b0_route_geometry_and_lane_recall(root, input_net)
    route_tls_projection_stats = write_route_tls_projection_audit(root)
    speed_cap_stats = cap_global_reality_speeds(root)
    firetruck_priority_summary = promote_firetruck_route_priority_connections(root)
    output_net.parent.mkdir(parents=True, exist_ok=True)
    temp = output_net.with_suffix(output_net.suffix + ".tmp")
    tree.write(temp, encoding="UTF-8", xml_declaration=True)
    temp.replace(output_net)
    normalized_rows = normalize_stats.pop("normalized_rows")
    normalized_csv = TDATA_ROOT / f"{output_net.stem}_tls_state_normalization.csv"
    write_csv(normalized_csv, normalized_rows, list(normalized_rows[0].keys()) if normalized_rows else [
        "tls_id", "programID", "phase_index", "action", "before_len", "after_len", "required_len",
    ])
    missing_green_rows = missing_green_stats.pop("missing_green_rows")
    missing_green_csv = TDATA_ROOT / f"{output_net.stem}_missing_green_repair.csv"
    write_csv(missing_green_csv, missing_green_rows, list(missing_green_rows[0].keys()) if missing_green_rows else [
        "tls_id", "programID", "linkIndex", "action", "anchor_phase_index", "yellow_phase_index",
        "anchor_duration_sec", "state_before", "state_after",
    ])
    short_green_rows = short_green_stats.pop("short_green_rows")
    short_green_csv = TDATA_ROOT / f"{output_net.stem}_short_green_repair.csv"
    write_csv(short_green_csv, short_green_rows, list(short_green_rows[0].keys()) if short_green_rows else [
        "tls_id", "programID", "linkIndex", "before_max_green_sec", "after_max_green_sec",
        "anchor_phase_index", "yellow_phase_index", "anchor_duration_sec", "state_before", "state_after",
    ])
    duplicate_green_rows = duplicate_green_stats.pop("duplicate_major_green_rows")
    duplicate_green_csv = TDATA_ROOT / f"{output_net.stem}_duplicate_major_green_normalization.csv"
    write_csv(duplicate_green_csv, duplicate_green_rows, list(duplicate_green_rows[0].keys()) if duplicate_green_rows else [
        "tls_id", "programID", "phase_index", "changed_link_indices", "state_before", "state_after",
    ])
    return {
        "schema": "compact_v9_b04_tls_state_normalization.v1",
        "generated_at": utc_now(),
        "input_net": rel(input_net),
        "output_net": rel(output_net),
        "tls_state_normalization_csv": rel(normalized_csv),
        "missing_green_repair_csv": rel(missing_green_csv),
        "short_green_repair_csv": rel(short_green_csv),
        "duplicate_major_green_normalization_csv": rel(duplicate_green_csv),
        "firetruck_route_priority": firetruck_priority_summary,
        "route_geometry_recall": route_geometry_recall_stats,
        **route_tls_projection_stats,
        **normalize_stats,
        **missing_green_stats,
        **short_green_stats,
        **duplicate_green_stats,
        **speed_cap_stats,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    snapshots = args.timing_snapshot or TIMING_SNAPSHOTS
    profiles, mapping_rows, demand_rows, profile_stats = build_profiles(args.input_net, snapshots, args.demand_route)
    write_csv(GLOBAL_MAPPING_CSV, mapping_rows, list(mapping_rows[0].keys()) if mapping_rows else [])
    profile_rows = [
        profile.as_row() | {
            key: value for key, value in next(
                (row for row in demand_rows if row["tls_id"] == profile.tls_id),
                {},
            ).items()
            if key not in {"tls_id"}
        }
        for profile in profiles
    ]
    write_csv(GLOBAL_PROFILES_CSV, profile_rows, list(profile_rows[0].keys()) if profile_rows else [])
    write_csv(GLOBAL_DEMAND_STATS_CSV, demand_rows, list(demand_rows[0].keys()) if demand_rows else [])
    apply_stats = apply_profiles(args.input_net, args.output_net, profiles)
    summary = {
        "schema": "compact_v9_b04_global_reality_signal.v1",
        "generated_at": utc_now(),
        "claim_scope": "all TLS get A008/API-informed plausible timing; not exact field reproduction",
        "timing_snapshots": [rel(path) for path in snapshots],
        "mapping_csv": rel(GLOBAL_MAPPING_CSV),
        "profiles_csv": rel(GLOBAL_PROFILES_CSV),
        "demand_stats_csv": rel(GLOBAL_DEMAND_STATS_CSV),
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
    parser.add_argument("--demand-route", type=Path, default=DEFAULT_DEMAND_ROUTE_XML)
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
