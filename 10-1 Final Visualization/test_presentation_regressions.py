#!/usr/bin/env python3
"""Regression tests for the 10-1 EV presentation visualization.

These tests intentionally check the visual contract, not raw SUMO fidelity.
They cover the repeated presentation failures: wrong route shape, fake queue
labels, missing downstream vehicles, red-light violations, EV being overtaken,
vehicle pop-in/warp/reverse, and TLS display glitches.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


THIS_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = THIS_DIR / "seoul_station_fire_station_presentation_data.json"
DEFAULT_REPORT = THIS_DIR / "seoul_station_fire_station_presentation_validation_report.json"
DEFAULT_HTML = THIS_DIR / "seoul_station_fire_station_presentation.html"
QUEUE_WORDS_FRONT = ("앞 큐", "앞 구간 정체", "앞차", "과잉", "막힌")
QUEUE_WORDS_REAR = ("뒤 흐름 정체", "뒤 차량")
QUEUE_WORDS_ANY = ("큐", "정체", "대기", "막힌")
FORBIDDEN_TLS_PATTERNS = (
    "green_yellow_green",
    "red_yellow_red",
    "yellow_red_yellow",
    "red_yellow_red_yellow",
)


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * 111_320.0
    dlon = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dlat, dlon)


def route_turns(points: list[dict[str, Any]], threshold_deg: float) -> list[dict[str, Any]]:
    turns = []
    for idx in range(1, len(points) - 1):
        a, b, c = points[idx - 1], points[idx], points[idx + 1]
        scale = 111_320.0 * math.cos(math.radians(float(b["lat"])))
        v1 = ((float(b["lon"]) - float(a["lon"])) * scale, (float(b["lat"]) - float(a["lat"])) * 111_320.0)
        v2 = ((float(c["lon"]) - float(b["lon"])) * scale, (float(c["lat"]) - float(b["lat"])) * 111_320.0)
        n1 = math.hypot(*v1)
        n2 = math.hypot(*v2)
        if n1 < 1.0 or n2 < 1.0:
            continue
        dot = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        angle = math.degrees(math.acos(dot))
        if angle > threshold_deg:
            turns.append({
                "idx": idx,
                "s": round(float(b["s"]), 2),
                "lat": float(b["lat"]),
                "lon": float(b["lon"]),
                "angle_deg": round(angle, 1),
                "prev_len_m": round(n1, 1),
                "next_len_m": round(n2, 1),
            })
    return turns


def state_at(timeline: list[list[Any]], t: float) -> str:
    state = str(timeline[0][1]) if timeline else "green"
    for ts, value in timeline:
        if float(ts) <= t:
            state = str(value)
        else:
            break
    return state


def sample_at(samples: list[dict[str, Any]], t: float) -> dict[str, Any]:
    best = samples[0] if samples else {}
    for sample in samples:
        if float(sample.get("t", 0.0)) <= t:
            best = sample
        else:
            break
    return best


def visible_front(frame: dict[str, Any], ev_s: float) -> bool:
    vehicles = frame.get("vehicles", [])
    bands = frame.get("bands", [])
    return any(
        vehicle.get("kind") == "route"
        and (str(vehicle.get("role", "")).startswith("ahead") or vehicle.get("role") == "blocked_visible")
        and 18.0 <= float(vehicle.get("s", 0.0)) - ev_s <= 220.0
        for vehicle in vehicles
    ) or any(
        band.get("kind") in {"front", "front_blocked"}
        and float(band.get("b", 0.0)) >= ev_s + 24.0
        and float(band.get("a", 0.0)) <= ev_s + 220.0
        for band in bands
    )


def visible_rear(frame: dict[str, Any], ev_s: float) -> bool:
    vehicles = frame.get("vehicles", [])
    bands = frame.get("bands", [])
    return any(
        vehicle.get("kind") == "route"
        and str(vehicle.get("role", "")).startswith("behind")
        and -240.0 <= float(vehicle.get("s", 0.0)) - ev_s <= -18.0
        for vehicle in vehicles
    ) or any(
        band.get("kind") == "rear"
        and ev_s - 240.0 <= float(band.get("b", 0.0)) <= ev_s - 18.0
        for band in bands
    )


def fail(failures: list[str], name: str, detail: Any = "") -> None:
    failures.append(f"{name}: {detail}" if detail else name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 10-1 presentation regression tests.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("--route-turn-threshold", type=float, default=120.0)
    args = parser.parse_args()

    data = json.loads(args.data.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    html = args.html.read_text(encoding="utf-8") if args.html.is_file() else ""
    failures: list[str] = []
    checks: list[str] = []

    def check(name: str, condition: bool, detail: Any = "") -> None:
        checks.append(name)
        if not condition:
            fail(failures, name, detail)

    check("validation_report_ok", bool(report.get("ok")), report)
    check("priority_signal_backlight_renderer_present", "priorityActive" in html and "rgba(34,197,94,.36)" in html, "")
    check("priority_signal_extension_badge_present", "GREEN EXT" in html, "")
    check("priority_signal_no_yellow_outline", "#fde047" not in html, "")
    check("active_signal_position_not_offset", "next?18" not in html and "stage?-54:0" in html, "")

    tls = report.get("tls", {})
    check("tls_yellow_present", int(tls.get("missing_yellow", 0)) == 0, tls)
    check("tls_forbidden_patterns_zero", all(int(tls.get(key, 0)) == 0 for key in FORBIDDEN_TLS_PATTERNS), tls)
    check("tls_close_display_signal_pairs_zero", int(tls.get("close_display_signal_pairs", 0)) == 0, tls)
    check("tls_short_yellow_zero", int(tls.get("short_yellow", 0)) == 0, tls)

    display = report.get("display", {})
    for key in (
        "ev_next_signal_non_green_pass",
        "vehicle_stopline_non_green_pass",
        "ev_front_queue_overtake",
        "green_empty_stopped",
        "green_blocked_moving",
        "behind_vehicle_ahead_of_ev",
        "green_blocked_without_visible_front",
        "queue_label_without_visible_queue",
        "arrived_with_queue_label",
        "downstream_label_without_moving_vehicle",
    ):
        check(f"display_{key}_zero", int(display.get(key, 0)) == 0, display)

    trajectory = report.get("trajectory", {})
    for key in (
        "tracked_vehicle_pop_in",
        "tracked_vehicle_disappear",
        "flow_vehicle_pop_in_near_camera",
        "flow_vehicle_disappear_near_camera",
        "flow_vehicle_large_jump",
        "vehicle_reverse_steps",
    ):
        check(f"trajectory_{key}_zero", int(trajectory.get(key, 0)) == 0, trajectory)

    visual = report.get("visual_regression", {})
    check("visual_regression_ok", bool(visual.get("ok")), visual)
    check("single_general_vehicle_color", int(visual.get("vehicle_color_palette_size", 99)) <= 1, visual)
    check("vehicle_color_not_changing", int(visual.get("vehicle_color_changes", 99)) == 0, visual)
    check("front_blocked_band_exists", int(visual.get("front_blocked_band_frames", 0)) > 0, visual)
    check("front_moving_band_exists", int(visual.get("front_moving_band_frames", 0)) > 0, visual)

    geometry = report.get("geometry", {})
    check("geometry_regression_ok", bool(geometry.get("ok")), geometry)
    check("geometry_vehicle_lane_distance_max_le_6m", float(geometry.get("vehicle_lane_distance_max_m", 999.0)) <= 6.0, geometry)
    check("geometry_vehicle_lane_distance_over_6m_zero", int(geometry.get("vehicle_lane_distance_over_6m", 99)) == 0, geometry)
    check("geometry_stage2_no_fallback_manual", int(geometry.get("stage2_fallback_manual", 99)) == 0, geometry)
    check("geometry_stage2_stopline_target_ok", int(geometry.get("stage2_stopline_target_fail", 99)) == 0, geometry)
    check("geometry_stage2_stopline_not_firetruck_exit", int(geometry.get("stage2_stopline_exit_fail", 99)) == 0, geometry)
    check("geometry_signal_distance_over_90m_zero", int(geometry.get("signal_distance_over_90m", 99)) == 0, geometry)

    algorithm = report.get("algorithm", {})
    check("algorithm_stage1_present", bool(algorithm.get("stage1_present")), algorithm)
    check("algorithm_stage2_present", int(algorithm.get("stage2_events", 0)) >= 3, algorithm)
    check("algorithm_case_a_present", int(algorithm.get("case_a_events", 0)) > 0, algorithm)
    check("algorithm_case_b_present", int(algorithm.get("case_b_events", 0)) > 0, algorithm)

    modes = data.get("modes", {})
    if "B04" in modes and "B4" in modes:
        travel_times = {mode: float(doc.get("travel_time_sec", 9999.0)) for mode, doc in modes.items()}
        check("b4_priority_signal_beats_b04_baseline", travel_times["B4"] < travel_times["B04"], travel_times)
        profile_sources = {str(modes[mode].get("traffic_profile_source", "")) for mode in ("B04", "B4")}
        check("b04_b4_share_presentation_traffic_profile", len(profile_sources) == 1 and "" not in profile_sources, sorted(profile_sources))
        b4_speeds = [
            float(sample.get("speed_kmh", 0.0))
            for sample in modes["B4"].get("display_samples", [])
            if sample.get("reason") != "arrived"
        ]
        check("b4_speed_profile_not_constant", len({round(speed) for speed in b4_speeds}) >= 5, sorted({round(speed) for speed in b4_speeds}))
        check("b4_priority_rolls_without_stopping", min(b4_speeds or [0.0]) >= 24.0, {"min": min(b4_speeds or [0.0]), "max": max(b4_speeds or [0.0])})
        check("b4_speed_graph_has_visible_range", (max(b4_speeds or [0.0]) - min(b4_speeds or [0.0])) >= 5.0, {"min": min(b4_speeds or [0.0]), "max": max(b4_speeds or [0.0])})
    demand_policy = data.get("presentation_inputs", {}).get("presentation_demand_policy", {})
    mode_split_demand = {
        key: value
        for key, value in demand_policy.items()
        if isinstance(value, dict) and ("B04" in value or "B4" in value)
    }
    check("presentation_demand_policy_not_split_by_mode", not mode_split_demand, mode_split_demand)

    route = data.get("route", {})
    points = route.get("points", [])
    destination = data.get("presentation_destination", {})
    path_edges = destination.get("path_edges", [])
    check("route_has_points", len(points) > 10, route)
    check("route_uses_dongho_waypoint", destination.get("required_waypoint_edge_id") in path_edges, destination)
    check("route_tail_uses_dongho_axis", all(edge in path_edges for edge in ("-1455512070", "-913006754#9", "-913006754#8", "-913006754#7")), destination)
    check("route_target_road_offset_reasonable", float(destination.get("road_offset_m", 999.0)) <= 35.0, destination)
    suspicious_turns = [
        turn
        for turn in route_turns(points, args.route_turn_threshold)
        if float(turn["s"]) > 250.0
    ]
    check("route_no_midroute_u_turns", not suspicious_turns, suspicious_turns[:5])
    dongho_v_turns = [
        turn
        for turn in route_turns(points, 80.0)
        if 1180.0 <= float(turn["s"]) <= 1380.0
    ]
    check("route_dongho_straight_recovery_no_v_turns", not dongho_v_turns, dongho_v_turns[:5])
    stage2_approaches = data.get("stage2_block_approaches", [])
    check("stage2_large_intersection_approaches_present", len(stage2_approaches) >= 3, stage2_approaches)
    firetruck_exit = [37.565126, 127.015716]
    toegye_dasan = [37.565319, 127.016594]
    expected_stage2_lane_prefix = {
        "north": "218684408#1",
        "south": "37399924#1",
        "east": "218684411#1",
    }
    for idx, approach in enumerate(stage2_approaches):
        line = approach.get("line", [])
        stop = line[0] if line else [0.0, 999.0]
        expected_prefix = expected_stage2_lane_prefix.get(str(approach.get("approach")))
        if expected_prefix:
            check(
                f"stage2_{approach.get('approach')}_uses_fixed_4way_arm",
                str(approach.get("lane_id", "")).startswith(expected_prefix),
                {"expected_prefix": expected_prefix, "approach": approach},
            )
        exit_gap = meters_between(float(stop[0]), float(stop[1]), firetruck_exit[0], firetruck_exit[1])
        target_gap = meters_between(float(stop[0]), float(stop[1]), toegye_dasan[0], toegye_dasan[1])
        check(
            f"stage2_approach_{idx}_uses_toegye_dasan_intersection",
            target_gap <= 80.0,
            {"stop": stop, "toegye_dasan": toegye_dasan, "gap_m": round(target_gap, 1), "approach": approach},
        )
        check(
            f"stage2_approach_{idx}_not_firetruck_exit_intersection",
            exit_gap >= 35.0,
            {"stop": stop, "firetruck_exit": firetruck_exit, "gap_m": round(exit_gap, 1), "approach": approach},
        )
        if approach.get("approach") == "south":
            check(
                "stage2_south_stopline_at_pink_lower_arm",
                target_gap <= 55.0 and float(stop[0]) <= toegye_dasan[0],
                {"stop": stop, "toegye_dasan": toegye_dasan, "gap_m": round(target_gap, 1), "approach": approach},
            )
        if approach.get("approach") == "north":
            check(
                "stage2_north_stopline_at_pink_upper_arm",
                target_gap <= 65.0 and float(stop[0]) >= toegye_dasan[0] and float(stop[1]) <= 127.01635,
                {
                    "stop": stop,
                    "toegye_dasan": toegye_dasan,
                    "gap_m": round(target_gap, 1),
                    "approach": approach,
                },
            )
        if approach.get("approach") == "east":
            check(
                "stage2_east_stopline_at_pink_right_arm",
                target_gap <= 25.0 and float(stop[1]) >= 127.01645,
                {"stop": stop, "toegye_dasan": toegye_dasan, "gap_m": round(target_gap, 1), "approach": approach},
            )

    for sig in data.get("signals", []):
        windows = sig.get("priority_windows", {}).get("B4", [])
        check(f"signal_{sig.get('id')}_has_b4_priority_window", bool(windows), sig)
        min_dist = min(
            meters_between(float(sig["lat"]), float(sig["lon"]), float(point["lat"]), float(point["lon"]))
            for point in points
        )
        check(f"signal_{sig.get('id')}_near_route", min_dist <= 90.0, {"signal": sig, "distance_m": round(min_dist, 1)})

    for mode, doc in data.get("modes", {}).items():
        samples = doc.get("display_samples", [])
        frames = doc.get("display_traffic") or doc.get("frames", [])
        timelines = data.get("timelines", {}).get(mode, {})
        check(f"{mode}_has_display_samples", bool(samples), "")
        check(f"{mode}_has_display_traffic", bool(frames), "")
        stage2_all_red_seen = mode != "B4"
        prev_by_id: dict[str, dict[str, Any]] = {}
        for frame in frames:
            t = float(frame.get("t", 0.0))
            sample = sample_at(samples, t)
            ev_s = float(frame.get("ev_s", sample.get("s", 0.0)))
            label = str(frame.get("summary", {}).get("label", ""))
            fake_signals = frame.get("fake_signals", [])
            if fake_signals:
                check(f"{mode}_stage2_fake_signal_single_t{t:.1f}", len(fake_signals) == 1, fake_signals)
                if mode == "B4" and t <= 12.0:
                    stage2_all_red_seen = stage2_all_red_seen or (
                        str(fake_signals[0].get("label")) == "ALL RED"
                        and str(fake_signals[0].get("signal_state")) == "allred"
                    )
            if str(sample.get("reason")) == "arrived":
                check(f"{mode}_arrived_label_no_queue_words_t{t:.1f}", not any(word in label for word in QUEUE_WORDS_ANY), label)
            if any(word in label for word in QUEUE_WORDS_FRONT):
                check(f"{mode}_front_label_has_front_visual_t{t:.1f}", visible_front(frame, ev_s), label)
            if any(word in label for word in QUEUE_WORDS_REAR):
                check(f"{mode}_rear_label_has_rear_visual_t{t:.1f}", visible_rear(frame, ev_s), label)
            for band in frame.get("bands", []):
                if band.get("kind") == "front_moving":
                    a = float(band.get("a", 0.0))
                    b = float(band.get("b", 0.0))
                    moving = [
                        vehicle
                        for vehicle in frame.get("vehicles", [])
                        if vehicle.get("role") == "downstream_moving"
                        and a <= float(vehicle.get("s", 0.0)) <= b
                        and float(vehicle.get("speed_kmh", 0.0)) >= 8.0
                    ]
                    check(f"{mode}_downstream_band_has_moving_vehicles_t{t:.1f}", bool(moving), {"band": band})
                if band.get("kind") == "front_blocked":
                    a = float(band.get("a", 0.0))
                    b = float(band.get("b", 0.0))
                    stopped = [
                        vehicle
                        for vehicle in frame.get("vehicles", [])
                        if (str(vehicle.get("role", "")).startswith("ahead") or vehicle.get("role") == "blocked_visible")
                        and a <= float(vehicle.get("s", 0.0)) <= b
                        and float(vehicle.get("speed_kmh", 0.0)) <= 1.0
                    ]
                    check(f"{mode}_blocked_band_has_stopped_vehicles_t{t:.1f}", bool(stopped), {"band": band})
                    check(f"{mode}_blocked_band_has_many_visible_stopped_vehicles_t{t:.1f}", len(stopped) >= 3, {"band": band, "stopped_count": len(stopped)})
            if mode == "B04" and 0.0 <= t <= 65.0:
                queue_opacities = [
                    float(vehicle.get("opacity", 0.0))
                    for vehicle in frame.get("vehicles", [])
                    if str(vehicle.get("id", "")).startswith("B04_aheadq_")
                ]
                if queue_opacities:
                    check(
                        f"{mode}_front_queue_vehicle_group_persistent_t{t:.1f}",
                        len(queue_opacities) >= 8,
                        {"t": t, "count": len(queue_opacities), "queue_opacities": queue_opacities[:8]},
                    )
                    check(
                        f"{mode}_front_queue_no_opacity_animation_t{t:.1f}",
                        min(queue_opacities) >= 0.84 and (max(queue_opacities) - min(queue_opacities)) <= 0.02,
                        {"t": t, "max_opacity": max(queue_opacities), "queue_opacities": queue_opacities[:8]},
                    )
            for vehicle in frame.get("vehicles", []):
                if vehicle.get("role") == "stage2_blocked" and t <= 12.0:
                    nearest_stop_gap = min(
                        meters_between(float(vehicle.get("lat", 0.0)), float(vehicle.get("lon", 0.0)), float((approach.get("line") or [[0.0, 0.0]])[0][0]), float((approach.get("line") or [[0.0, 0.0]])[0][1]))
                        for approach in stage2_approaches
                        if approach.get("line")
                    )
                    check(
                        f"{mode}_{vehicle.get('id')}_stage2_vehicle_set_back_from_stopline_t{t:.1f}",
                        nearest_stop_gap >= 12.0,
                        {"gap_m": round(nearest_stop_gap, 1), "vehicle": vehicle},
                    )
                if vehicle.get("kind") != "route" or vehicle.get("s") is None:
                    continue
                role = str(vehicle.get("role", ""))
                s = float(vehicle.get("s", 0.0))
                if role.startswith("behind"):
                    check(f"{mode}_{vehicle.get('id')}_behind_stays_behind_t{t:.1f}", s <= ev_s - 18.0, {"ev_s": ev_s, "vehicle": vehicle})
                if role.startswith("ahead"):
                    check(f"{mode}_{vehicle.get('id')}_ahead_stays_ahead_t{t:.1f}", s >= ev_s + 15.0 or str(sample.get("reason")) == "arrived", {"ev_s": ev_s, "vehicle": vehicle})
                prev = prev_by_id.get(str(vehicle.get("id")))
                if prev and prev.get("s") is not None and role != "downstream_moving":
                    check(f"{mode}_{vehicle.get('id')}_not_reverse_t{t:.1f}", s + 0.25 >= float(prev["s"]), {"prev_s": prev["s"], "s": s})
                prev_by_id[str(vehicle.get("id"))] = vehicle
            for sig in data.get("signals", []):
                sig_id = sig.get("id")
                if sig_id not in timelines:
                    continue
                state = state_at(timelines[sig_id], t)
                if state not in {"red", "yellow", "allred"}:
                    continue
                stop_s = float(sig["s"]) - 18.0
                for vehicle in frame.get("vehicles", []):
                    if vehicle.get("kind") != "route" or vehicle.get("s") is None:
                        continue
                    s = float(vehicle["s"])
                    allowed_clearance = "clearance" in str(vehicle.get("reason", ""))
                    check(
                        f"{mode}_{vehicle.get('id')}_respects_non_green_{sig_id}_t{t:.1f}",
                        not (stop_s < s < float(sig["s"]) + 42.0 and not allowed_clearance),
                        {"state": state, "signal": sig, "vehicle": vehicle},
                    )
        check(f"{mode}_stage2_all_red_label_seen", stage2_all_red_seen, "")

    print(f"presentation regression checks: {len(checks)}")
    if failures:
        print("FAILED")
        for item in failures[:80]:
            print(f"- {item}")
        if len(failures) > 80:
            print(f"... {len(failures) - 80} more failures")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
