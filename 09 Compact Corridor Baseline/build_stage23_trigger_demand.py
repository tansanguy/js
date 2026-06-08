#!/usr/bin/env python3
"""Build a B04 demand variant that makes EVTSP Stage2/Stage3 observable.

The original B04 demand is preserved. This script appends a small number of
background passenger vehicles around the dispatch/Case-B windows and writes a
separate route file that can be selected with --background-route.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / "09 Compact Corridor Baseline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from b4_runtime import B4Stage1Inputs, DATA_ROOT, rel  # noqa: E402


DEFAULT_OUTPUT = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml"
DEFAULT_BASE_DEMAND = DATA_ROOT / "demand/background_routes_compact_v9_B04_ad_variance_smoothed.rou.xml"
DEFAULT_STAGE2_COUNT = 24
DEFAULT_STAGE2_HEADWAY_SEC = 2.0
DEFAULT_STAGE3_COUNT = 8
DEFAULT_STAGE3_HEADWAY_SEC = 6.0
DEFAULT_STAGE3_ROUTE_LENGTH = 8
DEFAULT_STAGE2_QUEUE_PREBUILD_SEC = 30.0
DEFAULT_STAGE3_QUEUE_PREBUILD_SEC = 45.0
DEFAULT_STAGE2_ROUTE_RANK = 0
DEFAULT_STAGE3_ROUTE_RANK = 0
DEFAULT_S1FORCED_BOTTLENECK_EDGE = "347237859#0"
DEFAULT_S1FORCED_BOTTLENECK_KEEP_SHARE = 0.0
DEFAULT_S1FORCED_BOTTLENECK_STRATEGY = "remove"
DEFAULT_S1FORCED_POST_BOTTLENECK_DEPART_DELAY_SEC = 900.0
DEFAULT_EV_SPEED_MPS = 13.9
FALLBACK_EDGE_LENGTH_M = 50.0
FALLBACK_ROUTE_SPEED_MPS = 10.0
CANONICAL_REFERENCE_VEHICLE_COUNT = 1688
DIRECTION_BALANCE_MIN_SHARE = 0.45
DIRECTION_BALANCE_MAX_SHARE = 0.55
SPEED_BAND_TUNING_REMOVE_ROUTE_IDS = {
    "segment_feeder_upbound_S9",
    "segment_feeder_upbound_S15",
    "segment_feeder_upbound_S16",
    "segment_feeder_upbound_S17",
    "od_queue_upbound_S16_00",
    "od_queue_upbound_S16_01",
    "od_queue_upbound_S16_02",
    "midcorridor_local_upbound",
}
SPEED_BAND_TUNING_ADD_PLAN = {
    "segment_feeder_upbound_S6": 24,
    "segment_feeder_upbound_S7": 48,
    "segment_feeder_upbound_S8": 24,
    "segment_feeder_upbound_S18": 30,
    "segment_feeder_upbound_S20": 33,
    "segment_feeder_upbound_S21": 33,
    "segment_feeder_upbound_S22": 48,
    "segment_feeder_downbound_S12": 62,
    "segment_feeder_downbound_S20": 33,
    "segment_feeder_downbound_S21": 37,
    "segment_feeder_downbound_S22": 28,
    "od_repair_downbound_S20_00": 5,
    "od_repair_downbound_S20_01": 5,
}
SPEED_BAND_TUNING_LATE_ADD_PLAN = {
    "band_s15_local_upbound": {
        "count": 15,
        "begin_sec": 2300.0,
        "headway_sec": 80.0,
    },
    "od_repair_upbound_S11_01": {
        "count": 24,
        "begin_sec": 2260.0,
        "headway_sec": 38.0,
    },
    "od_queue_upbound_S18_00": {
        "count": 34,
        "begin_sec": 1760.0,
        "headway_sec": 32.0,
    },
}
SPEED_BAND_TUNING_CUSTOM_ROUTES = {
    "band_s15_local_upbound": ["347237859#4", "347237859#5", "781985787#0", "913372757"],
}
SPEED_BAND_TUNING_BEGIN_SEC = 420.0
SPEED_BAND_TUNING_END_SEC = 1620.0


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def edge_length_and_speed(net: Any | None, edge_id: str) -> tuple[float, float]:
    if net is None:
        return FALLBACK_EDGE_LENGTH_M, FALLBACK_ROUTE_SPEED_MPS
    try:
        edge = net.getEdge(edge_id)
        speed = max((lane.getSpeed() for lane in edge.getLanes()), default=FALLBACK_ROUTE_SPEED_MPS)
        return float(edge.getLength()), max(float(speed), 0.1)
    except Exception:
        return FALLBACK_EDGE_LENGTH_M, FALLBACK_ROUTE_SPEED_MPS


def read_sumo_net_optional(net_file: Path | None) -> Any | None:
    if net_file is None or not net_file.is_file():
        return None
    import sumolib  # noqa: PLC0415

    return sumolib.net.readNet(str(net_file))


def route_slice(route_edges: tuple[str, ...], start_edge: str, length: int) -> list[str]:
    if start_edge not in route_edges:
        raise ValueError(f"edge_not_in_stage1_route:{start_edge}")
    start = route_edges.index(start_edge)
    return list(route_edges[start : min(start + length, len(route_edges))])


def ensure_route(root: ET.Element, route_id: str, edges: list[str]) -> None:
    existing = root.find(f"./route[@id='{route_id}']")
    if existing is not None:
        existing.set("edges", " ".join(edges))
        return
    ET.SubElement(root, "route", {"id": route_id, "edges": " ".join(edges)})


def vehicle_count_by_route(root: ET.Element) -> dict[str, int]:
    counts: dict[str, int] = {}
    for vehicle in root.findall("vehicle"):
        route_id = vehicle.get("route", "")
        if route_id:
            counts[route_id] = counts.get(route_id, 0) + 1
    return counts


def natural_route_candidates(
    root: ET.Element,
    *,
    target_edge: str,
    min_upstream_edges: int,
    min_downstream_edges: int,
    excluded_start_edges: set[str],
) -> list[dict[str, Any]]:
    counts = vehicle_count_by_route(root)
    candidates: list[dict[str, Any]] = []
    for route in root.findall("route"):
        route_id = route.get("id", "")
        edges = route.get("edges", "").split()
        if not route_id or target_edge not in edges:
            continue
        target_index = edges.index(target_edge)
        downstream_count = len(edges) - target_index - 1
        if target_index < min_upstream_edges:
            continue
        if downstream_count < min_downstream_edges:
            continue
        if edges[0] in excluded_start_edges:
            continue
        candidates.append({
            "route_id": route_id,
            "edges": edges,
            "target_edge": target_edge,
            "target_index": target_index,
            "downstream_count": downstream_count,
            "base_vehicle_count": counts.get(route_id, 0),
        })
    return sorted(
        candidates,
        key=lambda item: (
            -int(item["base_vehicle_count"]),
            abs(int(item["target_index"]) - 4),
            len(item["edges"]),
            str(item["route_id"]),
        ),
    )


def select_natural_route(
    candidates: list[dict[str, Any]],
    *,
    rank: int,
    explicit_route_id: str = "",
) -> dict[str, Any]:
    if explicit_route_id:
        for candidate in candidates:
            if candidate["route_id"] == explicit_route_id:
                return candidate
        raise ValueError(f"stage23_route_id_not_available:{explicit_route_id}")
    if not candidates:
        raise ValueError("stage23_no_natural_route_candidate")
    index = max(0, min(int(rank), len(candidates) - 1))
    return candidates[index]


def freeflow_time_to_edge(net: Any | None, edges: list[str] | tuple[str, ...], target_edge: str) -> float:
    if target_edge not in edges:
        raise ValueError(f"target_edge_not_in_route:{target_edge}")
    total = 0.0
    for edge_id in list(edges)[: list(edges).index(target_edge)]:
        length, speed = edge_length_and_speed(net, edge_id)
        total += length / max(speed, 0.1)
    return total


def trigger_depart_base(
    *,
    net: Any | None,
    ev_route_edges: tuple[str, ...],
    ev_depart_sec: float,
    target_edge: str,
    trigger_route_edges: list[str],
    queue_prebuild_sec: float,
) -> dict[str, float]:
    ev_target_arrival = ev_depart_sec + freeflow_time_to_edge(net, ev_route_edges, target_edge)
    trigger_time_to_target = freeflow_time_to_edge(net, trigger_route_edges, target_edge)
    depart = max(0.0, ev_target_arrival - trigger_time_to_target - queue_prebuild_sec)
    return {
        "ev_target_arrival_sec": round(ev_target_arrival, 3),
        "trigger_time_to_target_sec": round(trigger_time_to_target, 3),
        "queue_prebuild_sec": round(queue_prebuild_sec, 3),
        "depart_base_sec": round(depart, 3),
    }


def append_vehicle(
    root: ET.Element,
    *,
    vehicle_id: str,
    route_id: str,
    depart: float,
    vehicle_type: str = "b04_passenger",
) -> None:
    ET.SubElement(
        root,
        "vehicle",
        {
            "id": vehicle_id,
            "type": vehicle_type,
            "route": route_id,
            "depart": f"{depart:.2f}",
            "departLane": "best",
            "departPos": "random_free",
            "departSpeed": "max",
        },
    )


def sort_vehicle_elements_by_depart(root: ET.Element) -> None:
    non_vehicles = [child for child in list(root) if child.tag != "vehicle"]
    vehicles = [child for child in list(root) if child.tag == "vehicle"]
    vehicles.sort(key=lambda item: (float(item.get("depart", "0") or 0.0), item.get("id", "")))
    root[:] = [*non_vehicles, *vehicles]


def stagger_duplicate_route_departures(root: ET.Element, min_headway_sec: float = 0.8) -> dict[str, Any]:
    buckets: dict[tuple[str, str], list[ET.Element]] = {}
    for vehicle in root.findall("vehicle"):
        route_id = str(vehicle.get("route", ""))
        depart = f"{safe_float(vehicle.get('depart')):.2f}"
        buckets.setdefault((route_id, depart), []).append(vehicle)
    changed = 0
    max_shift = 0.0
    for (_route_id, depart), vehicles in buckets.items():
        if len(vehicles) < 2:
            continue
        base_depart = safe_float(depart)
        for index, vehicle in enumerate(sorted(vehicles, key=lambda item: str(item.get("id", "")))):
            if index == 0:
                continue
            shift = index * min_headway_sec
            vehicle.set("depart", f"{base_depart + shift:.2f}")
            changed += 1
            max_shift = max(max_shift, shift)
    return {
        "enabled": True,
        "duplicate_bucket_count": sum(1 for vehicles in buckets.values() if len(vehicles) > 1),
        "changed_vehicle_count": changed,
        "min_headway_sec": min_headway_sec,
        "max_shift_sec": round(max_shift, 3),
        "policy": "stagger vehicles that share the same route and exact depart time to avoid deterministic junction-collision teleports",
    }


def route_direction(route_id: str) -> str:
    if "upbound" in route_id:
        return "upbound"
    if "downbound" in route_id:
        return "downbound"
    if "sideflow" in route_id:
        return "sideflow"
    return "other"


def direction_vehicle_counts(root: ET.Element) -> dict[str, int]:
    counts = {"upbound": 0, "downbound": 0, "sideflow": 0, "other": 0}
    for vehicle in root.findall("vehicle"):
        direction = route_direction(str(vehicle.get("route", "")))
        counts[direction] = counts.get(direction, 0) + 1
    return counts


def direction_balance_status(counts: dict[str, int]) -> dict[str, Any]:
    upbound = counts.get("upbound", 0)
    downbound = counts.get("downbound", 0)
    directional_total = upbound + downbound
    upbound_share = upbound / directional_total if directional_total else 0.0
    downbound_share = downbound / directional_total if directional_total else 0.0
    balanced = (
        directional_total == 0
        or (
            DIRECTION_BALANCE_MIN_SHARE <= upbound_share <= DIRECTION_BALANCE_MAX_SHARE
            and DIRECTION_BALANCE_MIN_SHARE <= downbound_share <= DIRECTION_BALANCE_MAX_SHARE
        )
    )
    return {
        "status": "PASS" if balanced else "FAIL",
        "directional_total": directional_total,
        "upbound_share": round(upbound_share, 6),
        "downbound_share": round(downbound_share, 6),
        "min_share": DIRECTION_BALANCE_MIN_SHARE,
        "max_share": DIRECTION_BALANCE_MAX_SHARE,
    }


def route_priority(route_id: str, direction: str) -> tuple[int, str]:
    if route_id.startswith(f"mainline_through_{direction}_balanced"):
        return (0, route_id)
    if route_id == f"mainline_through_{direction}" or route_id.startswith(f"mainline_through_{direction}_alt"):
        return (1, route_id)
    if route_id.startswith(f"segment_feeder_{direction}_"):
        return (2, route_id)
    if route_id.startswith(f"midcorridor_local_{direction}"):
        return (3, route_id)
    if route_id.startswith(f"od_repair_{direction}_") or route_id.startswith(f"od_queue_{direction}_"):
        return (4, route_id)
    return (9, route_id)


def route_candidates_by_direction(root: ET.Element, direction: str) -> list[str]:
    route_ids = [
        str(route.get("id", ""))
        for route in root.findall("route")
        if route_direction(str(route.get("id", ""))) == direction
        and str(route.get("id", "")) not in SPEED_BAND_TUNING_REMOVE_ROUTE_IDS
    ]
    return sorted(route_ids, key=lambda route_id: route_priority(route_id, direction))


def rebalance_direction_vehicle_counts(root: ET.Element) -> dict[str, Any]:
    before = direction_vehicle_counts(root)
    status_before = direction_balance_status(before)
    directional_total = before["upbound"] + before["downbound"]
    if status_before["status"] == "PASS" or directional_total == 0:
        return {
            "enabled": True,
            "changed_vehicle_count": 0,
            "before": before,
            "after": before,
            "before_status": status_before,
            "after_status": status_before,
            "policy": "no rebalance needed",
        }

    under_direction = "upbound" if before["upbound"] < before["downbound"] else "downbound"
    over_direction = "downbound" if under_direction == "upbound" else "upbound"
    target_under_count = math.ceil(directional_total * DIRECTION_BALANCE_MIN_SHARE)
    move_count = max(0, target_under_count - before[under_direction])
    target_routes = route_candidates_by_direction(root, under_direction)
    donor_vehicles = [
        vehicle
        for vehicle in root.findall("vehicle")
        if not str(vehicle.get("id", "")).startswith("stage23_")
        and route_direction(str(vehicle.get("route", ""))) == over_direction
    ]
    changed = 0
    if target_routes:
        for vehicle in donor_vehicles[:move_count]:
            vehicle.set("route", target_routes[changed % len(target_routes)])
            changed += 1
    after = direction_vehicle_counts(root)
    return {
        "enabled": True,
        "changed_vehicle_count": changed,
        "under_direction": under_direction,
        "over_direction": over_direction,
        "target_routes_used": target_routes[:12],
        "before": before,
        "after": after,
        "before_status": status_before,
        "after_status": direction_balance_status(after),
        "policy": "reassign non-stage23 background vehicles from overrepresented direction only until the underrepresented direction reaches the 45:55 balance window",
    }


def route_edges_by_id(root: ET.Element) -> dict[str, list[str]]:
    return {str(route.get("id", "")): str(route.get("edges", "")).split() for route in root.findall("route")}


def mainline_terminal_sinks(route_edges: dict[str, list[str]]) -> dict[str, str]:
    return {
        direction: route_edges.get(f"mainline_through_{direction}", [""])[-1]
        for direction in ("upbound", "downbound")
        if route_edges.get(f"mainline_through_{direction}")
    }


def mainline_terminal_sink_fail_count(root: ET.Element) -> int:
    route_edges = route_edges_by_id(root)
    sinks = mainline_terminal_sinks(route_edges)
    fail_count = 0
    for vehicle in root.findall("vehicle"):
        route_id = str(vehicle.get("route", ""))
        direction = route_direction(route_id)
        if direction not in sinks or not route_id.startswith(f"mainline_through_{direction}"):
            continue
        edges = route_edges.get(route_id, [])
        if not edges or edges[-1] != sinks[direction]:
            fail_count += 1
    return fail_count


def apply_speed_band_tuning(root: ET.Element) -> dict[str, Any]:
    existing_route_ids = {route.get("id", "") for route in root.findall("route")}
    real_b04_tuning_routes = set(SPEED_BAND_TUNING_ADD_PLAN) | (
        set(SPEED_BAND_TUNING_LATE_ADD_PLAN) - set(SPEED_BAND_TUNING_CUSTOM_ROUTES)
    )
    if not existing_route_ids.intersection(real_b04_tuning_routes):
        return {
            "enabled": False,
            "policy": "skip speed-band tuning when B04 calibration route templates are absent",
            "removed_vehicle_count": 0,
            "added_vehicle_count": 0,
            "remove_route_ids": sorted(SPEED_BAND_TUNING_REMOVE_ROUTE_IDS),
            "custom_routes": {},
            "add_plan": {},
            "late_add_plan": {},
            "depart_window_sec": [SPEED_BAND_TUNING_BEGIN_SEC, SPEED_BAND_TUNING_END_SEC],
            "added_vehicle_preview": [],
            "late_added_vehicle_preview": [],
        }
    for route_id, edges in SPEED_BAND_TUNING_CUSTOM_ROUTES.items():
        ensure_route(root, route_id, edges)
    route_ids = {route.get("id", "") for route in root.findall("route")}
    removed = 0
    for vehicle in list(root.findall("vehicle")):
        if vehicle.get("id", "").startswith("stage23_"):
            continue
        if vehicle.get("route", "") in SPEED_BAND_TUNING_REMOVE_ROUTE_IDS:
            root.remove(vehicle)
            removed += 1
    added = 0
    span = max(SPEED_BAND_TUNING_END_SEC - SPEED_BAND_TUNING_BEGIN_SEC, 1.0)
    add_rows: list[dict[str, Any]] = []
    for route_id, count in SPEED_BAND_TUNING_ADD_PLAN.items():
        if route_id not in route_ids:
            continue
        for index in range(max(0, int(count))):
            depart = SPEED_BAND_TUNING_BEGIN_SEC + span * ((index + 0.5) / max(count, 1)) + (added % 7) * 1.7
            append_vehicle(
                root,
                vehicle_id=f"stage23_band_tune_{added:04d}",
                route_id=route_id,
                depart=depart,
            )
            add_rows.append({
                "vehicle_id": f"stage23_band_tune_{added:04d}",
                "route_id": route_id,
                "depart": round(depart, 2),
            })
            added += 1
    late_add_rows: list[dict[str, Any]] = []
    for route_id, plan in SPEED_BAND_TUNING_LATE_ADD_PLAN.items():
        if route_id not in route_ids:
            continue
        count = max(0, int(plan["count"]))
        begin_sec = float(plan["begin_sec"])
        headway_sec = float(plan["headway_sec"])
        for index in range(count):
            depart = begin_sec + index * headway_sec + (added % 7) * 1.3
            append_vehicle(
                root,
                vehicle_id=f"stage23_late_band_tune_{added:04d}",
                route_id=route_id,
                depart=depart,
            )
            late_add_rows.append({
                "vehicle_id": f"stage23_late_band_tune_{added:04d}",
                "route_id": route_id,
                "depart": round(depart, 2),
            })
            added += 1
    return {
        "enabled": True,
        "policy": "remove unstable sparse bottleneck routes, add bidirectional natural-route samples, then rely on final route-id direction rebalance",
        "removed_vehicle_count": removed,
        "added_vehicle_count": added,
        "remove_route_ids": sorted(SPEED_BAND_TUNING_REMOVE_ROUTE_IDS),
        "custom_routes": SPEED_BAND_TUNING_CUSTOM_ROUTES,
        "add_plan": SPEED_BAND_TUNING_ADD_PLAN,
        "late_add_plan": SPEED_BAND_TUNING_LATE_ADD_PLAN,
        "depart_window_sec": [SPEED_BAND_TUNING_BEGIN_SEC, SPEED_BAND_TUNING_END_SEC],
        "added_vehicle_preview": add_rows[:10],
        "late_added_vehicle_preview": late_add_rows[:10],
    }


def cap_non_stage23_bottleneck_demand(
    root: ET.Element,
    *,
    bottleneck_edge: str,
    keep_share: float,
    strategy: str = "remove",
    post_depart_delay_sec: float = DEFAULT_S1FORCED_POST_BOTTLENECK_DEPART_DELAY_SEC,
) -> dict[str, Any]:
    if not bottleneck_edge or keep_share >= 1.0:
        return {
            "enabled": False,
            "bottleneck_edge": bottleneck_edge,
            "keep_share": keep_share,
            "strategy": strategy,
            "post_depart_delay_sec": post_depart_delay_sec,
            "total_bottleneck_vehicle_count": 0,
            "stage23_bottleneck_vehicle_count": 0,
            "kept_non_stage23_bottleneck_vehicle_count": 0,
            "removed_non_stage23_bottleneck_vehicle_count": 0,
            "split_pre_bottleneck_vehicle_count": 0,
            "split_post_bottleneck_vehicle_count": 0,
            "split_dropped_bottleneck_vehicle_count": 0,
        }
    route_edges = {route.get("id", ""): route.get("edges", "").split() for route in root.findall("route")}
    vehicles = list(root.findall("vehicle"))
    total_bottleneck = 0
    stage23_bottleneck = 0
    kept_non_stage23 = 0
    removed_non_stage23 = 0
    split_pre = 0
    split_post = 0
    split_dropped = 0
    keep_share = max(0.0, min(1.0, keep_share))
    strategy = strategy if strategy in {"remove", "split"} else "remove"
    for index, vehicle in enumerate(vehicles):
        route_id = vehicle.get("route", "")
        edges = route_edges.get(route_id, [])
        if bottleneck_edge not in edges:
            continue
        total_bottleneck += 1
        if vehicle.get("id", "").startswith("stage23_"):
            stage23_bottleneck += 1
            continue
        bucket = ((index * 1103515245 + 12345) % 10000) / 10000.0
        if bucket >= keep_share:
            if strategy == "split":
                bottleneck_index = edges.index(bottleneck_edge)
                pre_edges = edges[:bottleneck_index]
                post_edges = edges[bottleneck_index + 1 :]
                kept_part = False
                if len(pre_edges) >= 2:
                    pre_route_id = f"{route_id}__s1pre_bn"
                    ensure_route(root, pre_route_id, pre_edges)
                    route_edges[pre_route_id] = pre_edges
                    vehicle.set("route", pre_route_id)
                    split_pre += 1
                    kept_part = True
                else:
                    root.remove(vehicle)
                if len(post_edges) >= 2:
                    post_route_id = f"{route_id}__s1post_bn"
                    ensure_route(root, post_route_id, post_edges)
                    route_edges[post_route_id] = post_edges
                    post_attrib = dict(vehicle.attrib)
                    post_attrib["id"] = f"{vehicle.get('id', '')}__post_bn"
                    post_attrib["route"] = post_route_id
                    post_attrib["depart"] = f"{safe_float(vehicle.get('depart')) + post_depart_delay_sec:.2f}"
                    ET.SubElement(root, "vehicle", post_attrib)
                    split_post += 1
                    kept_part = True
                if not kept_part:
                    split_dropped += 1
                removed_non_stage23 += 1
            else:
                root.remove(vehicle)
                removed_non_stage23 += 1
        else:
            kept_non_stage23 += 1
    return {
        "enabled": True,
        "bottleneck_edge": bottleneck_edge,
        "keep_share": keep_share,
        "strategy": strategy,
        "post_depart_delay_sec": post_depart_delay_sec,
        "total_bottleneck_vehicle_count": total_bottleneck,
        "stage23_bottleneck_vehicle_count": stage23_bottleneck,
        "kept_non_stage23_bottleneck_vehicle_count": kept_non_stage23,
        "removed_non_stage23_bottleneck_vehicle_count": removed_non_stage23,
        "split_pre_bottleneck_vehicle_count": split_pre,
        "split_post_bottleneck_vehicle_count": split_post,
        "split_dropped_bottleneck_vehicle_count": split_dropped,
    }


def bottleneck_vehicle_counts(root: ET.Element, *, bottleneck_edge: str) -> dict[str, int]:
    if not bottleneck_edge:
        return {
            "final_bottleneck_vehicle_count": 0,
            "final_stage23_bottleneck_vehicle_count": 0,
            "final_non_stage23_bottleneck_vehicle_count": 0,
        }
    route_edges = {route.get("id", ""): route.get("edges", "").split() for route in root.findall("route")}
    total = 0
    stage23 = 0
    for vehicle in root.findall("vehicle"):
        route_id = vehicle.get("route", "")
        if bottleneck_edge not in route_edges.get(route_id, []):
            continue
        total += 1
        if vehicle.get("id", "").startswith("stage23_"):
            stage23 += 1
    return {
        "final_bottleneck_vehicle_count": total,
        "final_stage23_bottleneck_vehicle_count": stage23,
        "final_non_stage23_bottleneck_vehicle_count": total - stage23,
    }


def build_stage23_trigger_demand(
    *,
    base_demand: Path,
    output: Path,
    stage2_count: int,
    stage2_headway_sec: float,
    stage3_count: int,
    stage3_headway_sec: float,
    stage3_route_length: int,
    stage2_route_rank: int = DEFAULT_STAGE2_ROUTE_RANK,
    stage3_route_rank: int = DEFAULT_STAGE3_ROUTE_RANK,
    stage2_route_id: str = "",
    stage3_route_id: str = "",
    stage2_queue_prebuild_sec: float = DEFAULT_STAGE2_QUEUE_PREBUILD_SEC,
    stage3_queue_prebuild_sec: float = DEFAULT_STAGE3_QUEUE_PREBUILD_SEC,
    s1forced_bottleneck_edge: str = DEFAULT_S1FORCED_BOTTLENECK_EDGE,
    s1forced_bottleneck_keep_share: float = DEFAULT_S1FORCED_BOTTLENECK_KEEP_SHARE,
    s1forced_bottleneck_strategy: str = DEFAULT_S1FORCED_BOTTLENECK_STRATEGY,
    s1forced_post_bottleneck_depart_delay_sec: float = DEFAULT_S1FORCED_POST_BOTTLENECK_DEPART_DELAY_SEC,
    net_file: Path | None = None,
    stage1: B4Stage1Inputs,
) -> dict[str, Any]:
    tree = ET.parse(base_demand)
    root = tree.getroot()
    if root.tag != "routes":
        raise ValueError(f"unexpected_route_root:{root.tag}")

    caseb_movement = next((movement for movement in stage1.movements if movement.movement_id == "B4_MOVEMENT_09"), None)
    if caseb_movement is None:
        raise ValueError("missing_B4_MOVEMENT_09")
    net = read_sumo_net_optional(net_file)
    route_start_exclusions = set(stage1.route_edges)
    stage2_target_edge = stage1.departure.mainline_target_edge
    stage3_target_edge = caseb_movement.from_edge
    stage2_candidates = natural_route_candidates(
        root,
        target_edge=stage2_target_edge,
        min_upstream_edges=2,
        min_downstream_edges=3,
        excluded_start_edges=route_start_exclusions,
    )
    stage3_candidates = natural_route_candidates(
        root,
        target_edge=stage3_target_edge,
        min_upstream_edges=3,
        min_downstream_edges=max(stage3_route_length - 1, 1),
        excluded_start_edges=route_start_exclusions,
    )
    stage2_route = select_natural_route(stage2_candidates, rank=stage2_route_rank, explicit_route_id=stage2_route_id)
    stage3_route = select_natural_route(stage3_candidates, rank=stage3_route_rank, explicit_route_id=stage3_route_id)
    bottleneck_cap_summary = cap_non_stage23_bottleneck_demand(
        root,
        bottleneck_edge=s1forced_bottleneck_edge,
        keep_share=s1forced_bottleneck_keep_share,
        strategy=s1forced_bottleneck_strategy,
        post_depart_delay_sec=s1forced_post_bottleneck_depart_delay_sec,
    )
    stage2_depart = trigger_depart_base(
        net=net,
        ev_route_edges=stage1.route_edges,
        ev_depart_sec=stage1.ev_depart_sec,
        target_edge=stage2_target_edge,
        trigger_route_edges=stage2_route["edges"],
        queue_prebuild_sec=stage2_queue_prebuild_sec,
    )
    stage3_depart = trigger_depart_base(
        net=net,
        ev_route_edges=stage1.route_edges,
        ev_depart_sec=stage1.ev_depart_sec,
        target_edge=stage3_target_edge,
        trigger_route_edges=stage3_route["edges"],
        queue_prebuild_sec=stage3_queue_prebuild_sec,
    )

    for idx in range(stage2_count):
        append_vehicle(
            root,
            vehicle_id=f"stage23_merge_trigger_{idx:03d}",
            route_id=stage2_route["route_id"],
            depart=stage2_depart["depart_base_sec"] + idx * stage2_headway_sec,
        )
    for idx in range(stage3_count):
        append_vehicle(
            root,
            vehicle_id=f"stage23_caseb_m09_trigger_{idx:03d}",
            route_id=stage3_route["route_id"],
            depart=stage3_depart["depart_base_sec"] + idx * stage3_headway_sec,
        )

    speed_band_tuning = apply_speed_band_tuning(root)
    direction_balance = rebalance_direction_vehicle_counts(root)
    duplicate_depart_stagger = stagger_duplicate_route_departures(root)
    sort_vehicle_elements_by_depart(root)
    bottleneck_cap_summary.update(bottleneck_vehicle_counts(root, bottleneck_edge=s1forced_bottleneck_edge))
    final_vehicle_count = sum(1 for child in root if child.tag == "vehicle")
    final_direction_counts = direction_vehicle_counts(root)
    terminal_sink_fail_count = mainline_terminal_sink_fail_count(root)
    total_vehicle_delta_pct = (
        (final_vehicle_count - CANONICAL_REFERENCE_VEHICLE_COUNT) / CANONICAL_REFERENCE_VEHICLE_COUNT * 100.0
        if CANONICAL_REFERENCE_VEHICLE_COUNT
        else 0.0
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output, encoding="utf-8", xml_declaration=True)
    summary = {
        "schema": "compact_v9_B04_stage23_trigger_demand.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_demand": rel(base_demand),
        "output_demand": rel(output),
        "net_file": rel(net_file) if net_file else "",
        "route_selection_policy": "natural_base_route_template_only_with_s1forced_bottleneck_cap",
        "s1forced_bottleneck_cap": bottleneck_cap_summary,
        "speed_band_tuning": speed_band_tuning,
        "direction_balance": direction_balance,
        "duplicate_route_depart_stagger": duplicate_depart_stagger,
        "direction_vehicle_counts": final_direction_counts,
        "direction_balance_status": direction_balance_status(final_direction_counts),
        "mainline_terminal_sink_fail_count": terminal_sink_fail_count,
        "canonical_reference_vehicle_count": CANONICAL_REFERENCE_VEHICLE_COUNT,
        "total_vehicle_delta_pct": round(total_vehicle_delta_pct, 6),
        "vehicle_count": final_vehicle_count,
        "stage23_added_count": stage2_count + stage3_count,
        "stage2_route_id": stage2_route["route_id"],
        "stage2_edges": stage2_route["edges"],
        "stage2_target_edge": stage2_target_edge,
        "stage2_route_rank": stage2_route_rank,
        "stage2_target_index": stage2_route["target_index"],
        "stage2_base_vehicle_count": stage2_route["base_vehicle_count"],
        "stage2_route_candidate_count": len(stage2_candidates),
        "stage2_vehicle_count": stage2_count,
        "stage2_headway_sec": stage2_headway_sec,
        **{f"stage2_{key}": value for key, value in stage2_depart.items()},
        "stage2_depart_lane": "best",
        "stage2_depart_pos": "random_free",
        "stage2_depart_speed": "max",
        "stage2_depart_window_sec": [
            stage2_depart["depart_base_sec"],
            stage2_depart["depart_base_sec"] + max(stage2_count - 1, 0) * stage2_headway_sec,
        ],
        "stage3_route_id": stage3_route["route_id"],
        "stage3_edges": stage3_route["edges"],
        "stage3_target_edge": stage3_target_edge,
        "stage3_route_rank": stage3_route_rank,
        "stage3_target_index": stage3_route["target_index"],
        "stage3_base_vehicle_count": stage3_route["base_vehicle_count"],
        "stage3_route_candidate_count": len(stage3_candidates),
        "stage3_vehicle_count": stage3_count,
        "stage3_headway_sec": stage3_headway_sec,
        "stage3_route_length": stage3_route_length,
        **{f"stage3_{key}": value for key, value in stage3_depart.items()},
        "stage3_depart_lane": "best",
        "stage3_depart_pos": "random_free",
        "stage3_depart_speed": "max",
        "stage3_depart_window_sec": [
            stage3_depart["depart_base_sec"],
            stage3_depart["depart_base_sec"] + max(stage3_count - 1, 0) * stage3_headway_sec,
        ],
        "stage2_route_candidates_top": [
            {key: candidate[key] for key in ("route_id", "target_index", "downstream_count", "base_vehicle_count")}
            for candidate in stage2_candidates[:5]
        ],
        "stage3_route_candidates_top": [
            {key: candidate[key] for key in ("route_id", "target_index", "downstream_count", "base_vehicle_count")}
            for candidate in stage3_candidates[:5]
        ],
        "notes": "Original demand is not modified; Stage2/Stage3 trigger vehicles reuse natural base route templates and computed depart windows. Non-Stage23 background demand through the S1-forced downstream bottleneck is capped so the canonical baseline can discharge without hard-max gridlock.",
    }
    output.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Stage2/Stage3 trigger demand variant.")
    parser.add_argument("--base-demand", type=Path, default=DEFAULT_BASE_DEMAND)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stage2-count", type=int, default=DEFAULT_STAGE2_COUNT)
    parser.add_argument("--stage2-headway-sec", type=float, default=DEFAULT_STAGE2_HEADWAY_SEC)
    parser.add_argument("--stage3-count", type=int, default=DEFAULT_STAGE3_COUNT)
    parser.add_argument("--stage3-headway-sec", type=float, default=DEFAULT_STAGE3_HEADWAY_SEC)
    parser.add_argument("--stage3-route-length", type=int, default=DEFAULT_STAGE3_ROUTE_LENGTH)
    parser.add_argument("--stage2-route-rank", type=int, default=DEFAULT_STAGE2_ROUTE_RANK)
    parser.add_argument("--stage3-route-rank", type=int, default=DEFAULT_STAGE3_ROUTE_RANK)
    parser.add_argument("--stage2-route-id", default="")
    parser.add_argument("--stage3-route-id", default="")
    parser.add_argument("--stage2-queue-prebuild-sec", type=float, default=DEFAULT_STAGE2_QUEUE_PREBUILD_SEC)
    parser.add_argument("--stage3-queue-prebuild-sec", type=float, default=DEFAULT_STAGE3_QUEUE_PREBUILD_SEC)
    parser.add_argument("--s1forced-bottleneck-edge", default=DEFAULT_S1FORCED_BOTTLENECK_EDGE)
    parser.add_argument("--s1forced-bottleneck-keep-share", type=float, default=DEFAULT_S1FORCED_BOTTLENECK_KEEP_SHARE)
    parser.add_argument("--s1forced-bottleneck-strategy", choices=["remove", "split"], default=DEFAULT_S1FORCED_BOTTLENECK_STRATEGY)
    parser.add_argument("--s1forced-post-bottleneck-depart-delay-sec", type=float, default=DEFAULT_S1FORCED_POST_BOTTLENECK_DEPART_DELAY_SEC)
    parser.add_argument("--net-file", type=Path, default=None)
    args = parser.parse_args(argv)
    base_demand = args.base_demand if args.base_demand.is_absolute() else PROJECT_ROOT / args.base_demand
    output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    net_file = args.net_file if args.net_file is None or args.net_file.is_absolute() else PROJECT_ROOT / args.net_file
    summary = build_stage23_trigger_demand(
        base_demand=base_demand,
        output=output,
        stage2_count=max(args.stage2_count, 0),
        stage2_headway_sec=max(args.stage2_headway_sec, 0.1),
        stage3_count=max(args.stage3_count, 0),
        stage3_headway_sec=max(args.stage3_headway_sec, 0.1),
        stage3_route_length=max(args.stage3_route_length, 1),
        stage2_route_rank=max(args.stage2_route_rank, 0),
        stage3_route_rank=max(args.stage3_route_rank, 0),
        stage2_route_id=args.stage2_route_id,
        stage3_route_id=args.stage3_route_id,
        stage2_queue_prebuild_sec=max(args.stage2_queue_prebuild_sec, 0.0),
        stage3_queue_prebuild_sec=max(args.stage3_queue_prebuild_sec, 0.0),
        s1forced_bottleneck_edge=args.s1forced_bottleneck_edge,
        s1forced_bottleneck_keep_share=max(0.0, min(1.0, args.s1forced_bottleneck_keep_share)),
        s1forced_bottleneck_strategy=args.s1forced_bottleneck_strategy,
        s1forced_post_bottleneck_depart_delay_sec=max(0.0, args.s1forced_post_bottleneck_depart_delay_sec),
        net_file=net_file,
        stage1=B4Stage1Inputs.load(),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
