#!/usr/bin/env python3
"""Run Compact V9 B0/B4 Runtime MVP experiments.

The runner compares a no-control B0 run with the B4 MVP controller on the
manifest-selected B04 demand.  It never updates the B04 manifest, never creates new
demand, never runs BO, and does not enable FCD by default.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / "09 Compact Corridor Baseline"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from common.net_utils import find_executable  # noqa: E402
from b4_runtime import (  # noqa: E402
    B04_AA_BACKGROUND_ROUTE,
    B04_FIRETRUCK_ROUTE_XML,
    B04_MANIFEST,
    B04_NET,
    B004_MODE,
    B04_MODE,
    B4_MODE,
    B4MvpParams,
    B4ThetaParams,
    B4_PARAMETER_ID,
    B4_PRIMARY_CANDIDATE,
    B4RuntimePhaseConfig,
    EV_ID,
    EXPERIMENT_RESULT_FIELDS,
    FREE_FLOW_SPEED_KMH,
    RUNTIME_EVENT_FIELDS,
    W_E,
    W_G,
    B4RuntimeError,
    B4Stage1Inputs,
    load_edge_lengths,
    load_firetruck_route,
    rel,
    run_b04_traci_loop,
    run_b4_traci_loop,
    safe_float,
    theta_runtime_thresholds,
    unique_queue_lanes_for_movements,
    write_csv,
)


RUN_ROOT = PROJECT_ROOT / "runs/compact_v9_B4"
METRICS_ROOT = PROJECT_ROOT / "results/metrics/compact_v9_B4"
HTML_ROOT = PROJECT_ROOT / "results/html"
B004_FREE_REFERENCE_JSON = METRICS_ROOT / "b004_free_time_reference.json"
B004_VEHICLE_FREE_TIMES_CSV = METRICS_ROOT / "b004_vehicle_free_times.csv"
B004_B04_B4_COMPARISON_CSV = METRICS_ROOT / "b004_b04_b4_comparison.csv"
ROUTE_VISUALIZATION_HTML = HTML_ROOT / "compact_v9_b004_b04_b4_route_comparison.html"
ROUTE_VISUALIZATION_JSON = HTML_ROOT / "compact_v9_b004_b04_b4_route_comparison.json"
B4_TA_B0_MEASUREMENT_REVIEW_HTML = HTML_ROOT / "b4_ta_b0_measurement_review.html"
SIM_END_SEC = 4200
B4_RUNTIME_END_SEC = 7200
EDGE_DATA_FREQ_SEC = 60
FCD_PERIOD_SEC = 1
FCD_BEGIN_SEC = 600
DEFAULT_SEED = 1
DEFAULT_REPEAT_ID = 1
VALID_MODES = {B004_MODE, B04_MODE, B4_MODE, "B0"}
FREE_TIME_METHOD = "analytic_50kmh"
VEHICLE_FREE_TIME_METHOD = "analytic_50kmh_V_G_route_based_mainstream_plus_incoming"
SCORE_FORMULA = "(10/11) * D_E_sec + (1/11) * D_G_sec"
V_G_UNFINISHED_DELAY_CAP_SEC = 600.0
V_G_UNFINISHED_ELIGIBILITY_GRACE_SEC = 60.0
V_G_DEFINITION = "V_G = mainline route edges + all SUMO net incoming edges at mainline TLS; vehicles are included when their route touches any V_G edge."


class B4RunnerError(RuntimeError):
    """Expected B4 runner failure."""


@dataclass(frozen=True)
class B4RunTask:
    run_id: str
    mode: str
    parameter_id: str
    repeat_id: int
    seed: int
    run_dir: Path
    net_file: Path = B04_NET
    background_route: Path = B04_AA_BACKGROUND_ROUTE
    firetruck_route: Path = B04_FIRETRUCK_ROUTE_XML

    @property
    def is_analytic(self) -> bool:
        return self.mode == B004_MODE


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise B4RunnerError(f"json_root_not_object:{rel(path)}")
    return payload


def parse_modes(value: str) -> tuple[str, ...]:
    raw_modes = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    modes = tuple(B04_MODE if mode == "B0" else mode for mode in raw_modes)
    unknown = [mode for mode in modes if mode not in VALID_MODES]
    if unknown:
        raise B4RunnerError(f"unknown_modes:{','.join(unknown)}")
    return modes or (B004_MODE, B04_MODE, B4_MODE)


def default_run_id() -> str:
    return "b4_mvp_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def validate_b4_stage1_audit(stage1: B4Stage1Inputs) -> None:
    audit = stage1.stage1_audit
    if not isinstance(audit, dict) or audit.get("schema") != "compact_v9_B4_evtsp_stage1_audit.v1":
        raise B4RunnerError("stage1_audit_schema_missing")
    expected = {
        "dispatch_time_abs_sec": 61200.0,
        "sim_begin_abs_sec": 61200.0,
        "sim_end_abs_sec": 64800.0,
        "step_length_sec": 1.0,
        "t_dispatch_delay_sec": 45.0,
        "tE_merge_sec": 10.0,
        "t_merge_abs_rel_sec": 55.0,
    }
    for field, expected_value in expected.items():
        if abs(safe_float(audit.get(field), -1.0) - expected_value) > 1e-6:
            raise B4RunnerError(f"stage1_audit_field_mismatch:{field}")
    if safe_float(audit.get("Q_ratio_default"), -1.0) < 0.0 or safe_float(audit.get("Q_ratio_default"), -1.0) > 1.0:
        raise B4RunnerError("stage1_q_ratio_default_out_of_range")
    tau_default = safe_float(audit.get("tau_default"), -1.0)
    if tau_default < 0.70 or tau_default > 0.90:
        raise B4RunnerError("stage1_tau_default_out_of_range")
    n_movements = len(stage1.movements)
    i_merge = int(round(safe_float(audit.get("i_merge"), 0.0)))
    if safe_float(audit.get("N"), -1.0) != float(n_movements):
        raise B4RunnerError("stage1_audit_N_mismatch")
    if not (1 <= i_merge <= n_movements):
        raise B4RunnerError(f"stage1_i_merge_out_of_range:{i_merge}")
    merge_rows = [movement for movement in stage1.movements if movement.is_merge]
    if len(merge_rows) != 1:
        raise B4RunnerError(f"stage1_merge_row_count_mismatch:{len(merge_rows)}")
    if merge_rows[0].route_intersection_index != i_merge or merge_rows[0].stage_owner != "stage2_merge":
        raise B4RunnerError("stage1_merge_owner_mismatch")


def validate_static_inputs(
    stage1_dir: Path | None = None,
    net_file: Path = B04_NET,
    background_route: Path = B04_AA_BACKGROUND_ROUTE,
    firetruck_route: Path = B04_FIRETRUCK_ROUTE_XML,
) -> B4Stage1Inputs:
    stage1 = B4Stage1Inputs.load(stage1_dir, route_xml=firetruck_route) if stage1_dir is not None else B4Stage1Inputs.load(route_xml=firetruck_route)
    for path in [net_file, background_route, firetruck_route, B04_MANIFEST]:
        if not path.is_file():
            raise B4RunnerError(f"missing_required_input:{rel(path)}")
    manifest = read_json(B04_MANIFEST)
    allow_input_override = bool(stage1.summary.get("allow_runtime_input_override") or stage1.summary.get("runtime_input_provenance"))
    if not allow_input_override and manifest.get("selected_candidate") != B4_PRIMARY_CANDIDATE:
        raise B4RunnerError(f"unexpected_manifest_selected_candidate:{manifest.get('selected_candidate')}")
    validate_b4_stage1_audit(stage1)
    return stage1


def build_tasks(
    *,
    run_id: str | None = None,
    modes: tuple[str, ...] = (B004_MODE, B04_MODE, B4_MODE),
    seed: int = DEFAULT_SEED,
    repeat_id: int = DEFAULT_REPEAT_ID,
    run_root: Path = RUN_ROOT,
    net_file: Path = B04_NET,
    background_route: Path = B04_AA_BACKGROUND_ROUTE,
    firetruck_route: Path = B04_FIRETRUCK_ROUTE_XML,
) -> list[B4RunTask]:
    if repeat_id != DEFAULT_REPEAT_ID:
        raise B4RunnerError("B4 Runtime MVP supports repeat_id=1 only")
    if seed != DEFAULT_SEED:
        raise B4RunnerError("B4 Runtime MVP supports seed=1 only")
    run_id = run_id or default_run_id()
    tasks = []
    for mode in modes:
        mode = B04_MODE if mode == "B0" else mode
        if mode not in VALID_MODES:
            raise B4RunnerError(f"unknown_mode:{mode}")
        if mode == B004_MODE:
            leaf = "free_emv_analytic_50kmh"
            task_background_route = Path("")
        elif mode == B04_MODE:
            leaf = "no_control"
            task_background_route = background_route
        else:
            leaf = B4_PARAMETER_ID
            task_background_route = background_route
        run_dir = run_root / run_id / mode / leaf / f"repeat_{repeat_id:03d}"
        tasks.append(B4RunTask(run_id, mode, leaf, repeat_id, seed, run_dir, net_file=net_file, background_route=task_background_route, firetruck_route=firetruck_route))
    return tasks


def write_stage2_synthetic_demand_route(task: B4RunTask, stage1: B4Stage1Inputs) -> Path:
    """Inject a small verification-only inflow near the merge window.

    This does not change constants or theta. It only creates nonzero merge-zone
    measurements so multiplication-based Stage2 verification can actually fire.
    """
    route_path = task.run_dir / "stage2_synthetic_merge_verify.rou.xml"
    target_edge = stage1.departure.mainline_target_edge
    route_edges = [target_edge]
    if target_edge in stage1.route_edges:
        start = stage1.route_edges.index(target_edge)
        route_edges = list(stage1.route_edges[start : min(start + 4, len(stage1.route_edges))])
    elif stage1.route_edges:
        route_edges = list(stage1.route_edges[: min(4, len(stage1.route_edges))])
    stop_lanes = [lane for lane in stage1.departure.merge_zone_lanes if lane.startswith(f"{target_edge}_")] or [f"{target_edge}_0"]
    stop_slots = (10.0, 16.0, 22.0, 28.0, 34.0, 40.0)
    vehicles = []
    for idx in range(48):
        depart = 555 + idx * 0.5
        stop_lane = stop_lanes[idx % len(stop_lanes)]
        stop_pos = stop_slots[(idx // max(len(stop_lanes), 1)) % len(stop_slots)]
        vehicles.append(
            f'  <vehicle id="stage2_verify_{idx:03d}" type="stage2_verify_passenger" '
            f'route="stage2_verify_merge_route" depart="{depart:.1f}" departLane="best" '
            'departPos="base" departSpeed="0">'
            f'<stop lane="{stop_lane}" endPos="{stop_pos:.1f}" until="625.0"/></vehicle>'
        )
    route_path.write_text(
        "\n".join([
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<routes>",
            '  <vType id="stage2_verify_passenger" vClass="passenger" length="5.0" minGap="2.5" accel="2.0" decel="4.5" maxSpeed="13.9"/>',
            f'  <route id="stage2_verify_merge_route" edges="{" ".join(route_edges)}"/>',
            *vehicles,
            "</routes>",
            "",
        ]),
        encoding="utf-8",
    )
    return route_path


def write_sumo_config(
    task: B4RunTask,
    phase_config: B4RuntimePhaseConfig | None = None,
    *,
    emit_fcd: bool = False,
    emit_e2: bool = False,
    stage1: B4Stage1Inputs | None = None,
) -> dict[str, Path]:
    if task.is_analytic:
        raise B4RunnerError("B004 is analytic_50kmh and must not create a SUMO config")
    phase_config = phase_config or B4RuntimePhaseConfig.bo_smoke()
    sim_end_sec = phase_config.hard_max_sim_time
    task.run_dir.mkdir(parents=True, exist_ok=True)
    additional = task.run_dir / "b0_b4_additional.add.xml"
    edge_data = task.run_dir / "edgeData.xml"
    lane_data = task.run_dir / "laneData.xml"
    tripinfo = task.run_dir / "tripinfo.xml"
    summary = task.run_dir / "summary.xml"
    fcd = task.run_dir / "fcd.xml"
    e2_output = task.run_dir / "e2_queue.xml"
    sumocfg = task.run_dir / "scenario.sumocfg"
    route_files = [task.background_route, task.firetruck_route]
    if task.mode == B4_MODE and phase_config.stage2_synthetic_demand:
        synthetic_stage1 = stage1 or B4Stage1Inputs.load()
        route_files.append(write_stage2_synthetic_demand_route(task, synthetic_stage1))
    output_lines = [
        "  <output>",
        f'    <tripinfo-output value="{tripinfo.as_posix()}"/>',
        f'    <summary-output value="{summary.as_posix()}"/>',
    ]
    if emit_fcd:
        output_lines.extend([
            f'    <fcd-output value="{fcd.as_posix()}"/>',
            '    <fcd-output.geo value="true"/>',
            '    <fcd-output.distance value="true"/>',
            f'    <device.fcd.period value="{FCD_PERIOD_SEC}"/>',
            f'    <device.fcd.begin value="{FCD_BEGIN_SEC}"/>',
        ])
    output_lines.append("  </output>")

    additional_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<additional>",
        f'  <edgeData id="b4_edge_data" freq="{EDGE_DATA_FREQ_SEC}" file="{edge_data.as_posix()}"/>',
        f'  <laneData id="b4_lane_data" freq="{EDGE_DATA_FREQ_SEC}" file="{lane_data.as_posix()}"/>',
    ]
    if emit_e2:
        stage1_for_e2 = stage1 or B4Stage1Inputs.load()
        additional_lines.extend(e2_detector_lines(stage1_for_e2, e2_output))
    additional_lines.extend(["</additional>", ""])
    additional.write_text("\n".join(additional_lines), encoding="utf-8")
    sumocfg.write_text(
        "\n".join([
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<configuration>",
            "  <input>",
            f'    <net-file value="{task.net_file.as_posix()}"/>',
            f'    <route-files value="{",".join(path.as_posix() for path in route_files)}"/>',
            f'    <additional-files value="{additional.as_posix()}"/>',
            "  </input>",
            "  <time>",
            '    <begin value="0"/>',
            f'    <end value="{sim_end_sec}"/>',
            "  </time>",
            "  <processing>",
            '    <time-to-teleport value="-1"/>',
            "  </processing>",
            *output_lines,
            "  <report>",
            '    <verbose value="false"/>',
            '    <no-step-log value="true"/>',
            "  </report>",
            "</configuration>",
            "",
        ]),
        encoding="utf-8",
    )
    return {
        "sumocfg": sumocfg,
        "additional": additional,
        "edgeData": edge_data,
        "laneData": lane_data,
        "tripinfo": tripinfo,
        "summary": summary,
        "fcd": fcd,
        "e2": e2_output,
    }


def e2_detector_lines(stage1: B4Stage1Inputs, output_file: Path, max_movements: int = 3) -> list[str]:
    lines: list[str] = []
    ranked = sorted(
        (movement for movement in stage1.movements if movement.controllable),
        key=lambda movement: safe_float(getattr(stage1.queue_calibration_priors.get(movement.movement_id), "reference_queue_m", 0.0)),
        reverse=True,
    )
    for movement in ranked[:max_movements]:
        lanes = " ".join(movement.local_storage_lanes or movement.approach_lanes)
        if not lanes:
            continue
        detector_id = f"b4_e2_{movement.movement_id}"
        length_m = max(min(movement.stopline_local_storage_m, 120.0), 1.0)
        lines.append(
            f'  <laneAreaDetector id="{detector_id}" lanes="{lanes}" pos="0" length="{length_m}" freq="1" file="{output_file.as_posix()}"/>'
        )
    return lines


def build_sumo_command(
    task: B4RunTask,
    sumo_binary: str | None = None,
    phase_config: B4RuntimePhaseConfig | None = None,
    *,
    emit_fcd: bool = False,
    emit_e2: bool = False,
    stage1: B4Stage1Inputs | None = None,
) -> list[str]:
    paths = write_sumo_config(task, phase_config, emit_fcd=emit_fcd, emit_e2=emit_e2, stage1=stage1)
    binary = sumo_binary or find_executable("sumo")
    ensure_sumo_home(binary)
    return [
        binary,
        "-c",
        str(paths["sumocfg"]),
        "--seed",
        str(task.seed),
        "--no-step-log",
        "true",
    ]


def valid_sumo_home(path: Path) -> bool:
    return (path / "data/xsd/net_file.xsd").is_file() or (path / "data/typemap/osmNetconvert.typ.xml").is_file()


def infer_sumo_home(sumo_binary: str) -> Path | None:
    binary = Path(sumo_binary).resolve()
    candidates = [
        binary.parents[1] / "share/sumo",
        binary.parents[0].parent / "share/sumo",
    ]
    for candidate in candidates:
        if valid_sumo_home(candidate):
            return candidate
    return None


def ensure_sumo_home(sumo_binary: str) -> None:
    current = os.environ.get("SUMO_HOME")
    if current and valid_sumo_home(Path(current)):
        return
    inferred = infer_sumo_home(sumo_binary)
    if inferred is not None:
        os.environ["SUMO_HOME"] = str(inferred)


def count_background_departures(route_file: Path) -> int:
    count = 0
    for _event, elem in ET.iterparse(route_file, events=("end",)):
        if elem.tag == "vehicle" and elem.get("id") != EV_ID:
            count += 1
        elem.clear()
    return count


def parse_tripinfo(path: Path) -> dict[str, Any]:
    result = {
        "emergency": None,
        "background": [],
    }
    if not path.is_file():
        return result
    try:
        for _event, elem in ET.iterparse(path, events=("end",)):
            if elem.tag == "tripinfo":
                row = dict(elem.attrib)
                if row.get("id") == EV_ID:
                    result["emergency"] = row
                else:
                    result["background"].append(row)
            elem.clear()
    except ET.ParseError:
        # Early TraCI smoke termination can leave the root unclosed.  Complete
        # tripinfo elements are still valid per-line and enough for MVP metrics.
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped.startswith("<tripinfo ") or not stripped.endswith("/>"):
                continue
            try:
                row = dict(ET.fromstring(stripped).attrib)
            except ET.ParseError:
                continue
            if row.get("id") == EV_ID:
                result["emergency"] = row
            else:
                result["background"].append(row)
    return result


def edge_id_from_lane(lane_id: str) -> str:
    return str(lane_id).rsplit("_", 1)[0]


def route_length_m(edges: list[str] | tuple[str, ...], edge_lengths: dict[str, float]) -> float:
    return sum(edge_lengths.get(edge_id, 0.0) for edge_id in edges)


def free_time_sec(edges: list[str] | tuple[str, ...], edge_lengths: dict[str, float]) -> float:
    speed_mps = FREE_FLOW_SPEED_KMH / 3.6
    return round(route_length_m(edges, edge_lengths) / speed_mps, 6)


def parse_route_file(route_file: Path) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    routes: dict[str, list[str]] = {}
    vehicles: list[dict[str, Any]] = []
    root = ET.parse(route_file).getroot()
    for route in root.findall("route"):
        route_id = route.get("id", "")
        if route_id:
            routes[route_id] = [edge for edge in str(route.get("edges", "")).split() if edge]
    for vehicle in root.findall("vehicle"):
        vehicles.append(dict(vehicle.attrib))
    return routes, vehicles


def mainline_tls_ids(stage1: B4Stage1Inputs) -> set[str]:
    return {movement.tls_id for movement in stage1.movements if movement.tls_id}


def v_g_edge_sets(stage1: B4Stage1Inputs, net_file: Path) -> dict[str, set[str]]:
    mainline_edges = {edge for edge in stage1.route_edges if edge}
    incoming_edges: set[str] = set()
    tls_ids = mainline_tls_ids(stage1)
    root = ET.parse(net_file).getroot()
    for connection in root.findall("connection"):
        if connection.get("tl") not in tls_ids:
            continue
        from_edge = connection.get("from", "")
        if from_edge and not from_edge.startswith(":"):
            incoming_edges.add(from_edge)
    return {
        "mainline_edges": mainline_edges,
        "incoming_edges": incoming_edges,
        "tributary_edges": incoming_edges - mainline_edges,
        "v_g_edges": mainline_edges | incoming_edges,
    }


def controllable_v_edges(stage1: B4Stage1Inputs) -> set[str]:
    edges: set[str] = set()
    for movement in stage1.movements:
        if not movement.controllable:
            continue
        edges.add(movement.from_edge)
        edges.add(movement.to_edge)
        for lane_id in [*movement.approach_lanes, *movement.local_storage_lanes, *movement.corridor_storage_lanes]:
            edge_id = edge_id_from_lane(lane_id)
            if edge_id:
                edges.add(edge_id)
    return edges


def vehicle_free_time_rows(stage1: B4Stage1Inputs, edge_lengths: dict[str, float], background_route: Path = B04_AA_BACKGROUND_ROUTE, net_file: Path = B04_NET) -> list[dict[str, Any]]:
    routes, vehicles = parse_route_file(background_route)
    v_g_edges = v_g_edge_sets(stage1, net_file)["v_g_edges"]
    rows: list[dict[str, Any]] = []
    for vehicle in vehicles:
        vehicle_id = str(vehicle.get("id", ""))
        route_id = str(vehicle.get("route", ""))
        edges = routes.get(route_id, [])
        if not vehicle_id or not edges or vehicle_id == EV_ID:
            continue
        overlap_edges = [edge for edge in edges if edge in v_g_edges]
        if not overlap_edges:
            continue
        rows.append({
            "vehicle_id": vehicle_id,
            "route_id": route_id,
            "route_edge_count": len(edges),
            "route_length_m": round(route_length_m(edges, edge_lengths), 6),
            "free_time_sec": free_time_sec(edges, edge_lengths),
            "V_G_overlap_edge_count": len(overlap_edges),
            "V_G_definition": V_G_DEFINITION,
        })
    return rows


def build_b004_free_reference(
    stage1: B4Stage1Inputs,
    net_file: Path = B04_NET,
    background_route: Path = B04_AA_BACKGROUND_ROUTE,
    firetruck_route: Path = B04_FIRETRUCK_ROUTE_XML,
    output_json: Path = B004_FREE_REFERENCE_JSON,
    vehicle_free_times_csv: Path = B004_VEHICLE_FREE_TIMES_CSV,
) -> dict[str, Any]:
    edge_lengths = load_edge_lengths(net_file)
    route_meta = load_firetruck_route(firetruck_route)
    route_edges = list(route_meta["route_edges"])
    route_length = route_length_m(route_edges, edge_lengths)
    emv_free = free_time_sec(route_edges, edge_lengths)
    v_g_sets = v_g_edge_sets(stage1, net_file)
    vehicle_rows = vehicle_free_time_rows(stage1, edge_lengths, background_route, net_file)
    veh_free_values = [safe_float(row.get("free_time_sec")) for row in vehicle_rows]
    reference = {
        "schema": "compact_v9_B004_free_emv_reference.v1",
        "generated_at": utc_now(),
        "mode": B004_MODE,
        "scenario_name": "emv_free_flow_fire_station_to_seoul_station_front",
        "primary_candidate": B4_PRIMARY_CANDIDATE,
        "net_file": rel(net_file),
        "free_time_method": FREE_TIME_METHOD,
        "vehicle_free_time_method": VEHICLE_FREE_TIME_METHOD,
        "V_G_definition": V_G_DEFINITION,
        "V_G_mainline_edge_count": len(v_g_sets["mainline_edges"]),
        "V_G_incoming_edge_count": len(v_g_sets["incoming_edges"]),
        "V_G_tributary_edge_count": len(v_g_sets["tributary_edges"]),
        "V_G_edge_count": len(v_g_sets["v_g_edges"]),
        "free_flow_speed_kmh": FREE_FLOW_SPEED_KMH,
        "start_edge": route_edges[0] if route_edges else "",
        "merge_edge": stage1.departure.mainline_target_edge,
        "target_edge": route_edges[-1] if route_edges else "",
        "route_id": route_meta["route_id"],
        "route_edge_count": len(route_edges),
        "route_length_m": round(route_length, 6),
        "T_free_EMV_sec": emv_free,
        "V_G_vehicle_count": len(vehicle_rows),
        "T_G_free_mean_sec": round(sum(veh_free_values) / len(veh_free_values), 6) if veh_free_values else "",
        "V_G_definition": V_G_DEFINITION,
    }
    write_json(output_json, reference)
    write_csv(
        vehicle_free_times_csv,
        vehicle_rows,
        ["vehicle_id", "route_id", "route_edge_count", "route_length_m", "free_time_sec", "V_G_overlap_edge_count", "V_G_definition"],
    )
    return reference


def read_free_vehicle_rows() -> list[dict[str, Any]]:
    if not B004_VEHICLE_FREE_TIMES_CSV.is_file():
        return []
    with B004_VEHICLE_FREE_TIMES_CSV.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def b004_result_row(task: B4RunTask, stage1: B4Stage1Inputs, reference: dict[str, Any], phase_config: B4RuntimePhaseConfig) -> dict[str, Any]:
    params = B4MvpParams()
    row = base_result_row(task, stage1, params, phase_config)
    row.update({
        "scenario_name": reference["scenario_name"],
        "background_route_file": "",
        "free_time_method": FREE_TIME_METHOD,
        "sumo_exit_code": "",
        "emergency_departed": True,
        "emergency_arrived": True,
        "emergency_teleport": False,
        "termination_reason": "analytic_reference_complete",
        "termination_time_sec": "",
        "recovery_detected": "",
        "objective_includes_recovery": False,
        "emergency_tripinfo_found": False,
        "T_actual_EMV_sec": reference["T_free_EMV_sec"],
        "T_free_EMV_sec": reference["T_free_EMV_sec"],
        "D_E_sec": 0.0,
        "V_G_vehicle_count": 0,
        "T_G_actual_mean_sec": "",
        "T_G_free_mean_sec": "",
        "D_G_sec": "",
        "objective_score": 0.0,
        "final_status": "REFERENCE",
        "failure_reason": "",
    })
    return {field: row.get(field, "") for field in EXPERIMENT_RESULT_FIELDS}


def base_result_row(task: B4RunTask, stage1: B4Stage1Inputs, params: B4MvpParams, phase_config: B4RuntimePhaseConfig) -> dict[str, Any]:
    runtime_thresholds = theta_runtime_thresholds(stage1.thresholds, params)
    return {
        "run_id": task.run_id,
        "mode": task.mode,
        "scenario_name": "emv_free_flow_fire_station_to_seoul_station_front" if task.mode == B004_MODE else "compact_v9_B04_AD_real_demand",
        "seed": task.seed,
        "repeat_id": task.repeat_id,
        "primary_candidate": stage1.primary_candidate or B4_PRIMARY_CANDIDATE,
        "stage1_dir": rel(stage1.stage1_dir),
        "net_file": rel(task.net_file),
        "background_route_file": rel(task.background_route) if str(task.background_route) else "",
        "ev_route_file": rel(task.firetruck_route),
        "free_time_method": FREE_TIME_METHOD,
        "parameter_id": task.parameter_id,
        **params.as_result_fields(),
        **phase_config.as_result_fields(),
        "local_fill_trigger": runtime_thresholds.local_fill_trigger,
        "speed_trigger_kmh": runtime_thresholds.speed_trigger_kmh,
        "max_active_movements": stage1.max_active_movements,
        "stage2_dispatch_lead_sec": stage1.departure.dispatch_lead_time_sec,
        "w_E": W_E,
        "w_G": W_G,
        "score_formula": SCORE_FORMULA,
        "final_status": "",
        "sumo_exit_code": "",
        "failed": "",
        "failure_reason": "",
        "wall_time_sec": "",
        "route_visualization_html": rel(ROUTE_VISUALIZATION_HTML),
    }


def objective_score(D_E_sec: float, D_G_sec: float) -> float:
    total_weight = W_E + W_G
    return round((W_E / total_weight) * D_E_sec + (W_G / total_weight) * D_G_sec, 6)


def actual_v_vehicle_metrics(
    background_tripinfo: list[dict[str, Any]],
    free_rows_by_id: dict[str, dict[str, Any]],
    background_route: Path,
    simulation_end_sec: float,
) -> dict[str, Any]:
    arrived_by_id = {str(row.get("id", "")): row for row in background_tripinfo if row.get("id")}
    _routes, vehicles = parse_route_file(background_route)
    depart_by_id = {
        str(vehicle.get("id", "")): safe_float(vehicle.get("depart"), 0.0)
        for vehicle in vehicles
        if vehicle.get("id") and vehicle.get("id") != EV_ID
    }
    actual_values: list[float] = []
    free_values: list[float] = []
    arrived_count = 0
    unfinished_count = 0
    excluded_late_count = 0
    capped_unfinished_count = 0
    for row in background_tripinfo:
        vehicle_id = row.get("id", "")
        free = free_rows_by_id.get(vehicle_id)
        if not free:
            continue
        duration = safe_float(row.get("duration"), -1.0)
        free_time = safe_float(free.get("free_time_sec"), -1.0)
        if duration >= 0 and free_time >= 0:
            actual_values.append(duration)
            free_values.append(free_time)
            arrived_count += 1
    for vehicle_id, free in free_rows_by_id.items():
        if vehicle_id in arrived_by_id:
            continue
        depart = depart_by_id.get(vehicle_id)
        if depart is None:
            continue
        if depart > float(simulation_end_sec):
            continue
        free_time = safe_float(free.get("free_time_sec"), -1.0)
        if free_time < 0.0:
            continue
        latest_eligible_depart = float(simulation_end_sec) - free_time - V_G_UNFINISHED_ELIGIBILITY_GRACE_SEC
        if depart > latest_eligible_depart:
            excluded_late_count += 1
            continue
        censored_duration = max(float(simulation_end_sec) - depart, 0.0)
        capped_delay = min(max(censored_duration - free_time, 0.0), V_G_UNFINISHED_DELAY_CAP_SEC)
        if censored_duration - free_time > capped_delay:
            capped_unfinished_count += 1
        actual_values.append(free_time + capped_delay)
        free_values.append(free_time)
        unfinished_count += 1
    actual_mean = round(sum(actual_values) / len(actual_values), 6) if actual_values else ""
    free_mean = round(sum(free_values) / len(free_values), 6) if free_values else ""
    D_G_sec = round(actual_mean - free_mean, 6) if actual_values and free_values else ""
    return {
        "V_G_vehicle_count": len(actual_values),
        "T_G_actual_mean_sec": actual_mean,
        "T_G_free_mean_sec": free_mean,
        "D_G_sec": D_G_sec,
        "V_G_arrived_vehicle_count": arrived_count,
        "V_G_unfinished_vehicle_count": unfinished_count,
        "V_G_late_excluded_vehicle_count": excluded_late_count,
        "V_G_capped_unfinished_vehicle_count": capped_unfinished_count,
        "D_G_unfinished_policy": f"eligible_unfinished_censored_delay_capped_at_{int(V_G_UNFINISHED_DELAY_CAP_SEC)}s_excluding_depart_after_eval_end_minus_free_time_minus_{int(V_G_UNFINISHED_ELIGIBILITY_GRACE_SEC)}s",
    }


def net_shapes(net_file: Path) -> dict[str, list[tuple[float, float]]]:
    shapes: dict[str, list[tuple[float, float]]] = {}
    root = ET.parse(net_file).getroot()
    for elem in root.findall("edge"):
        if elem.get("function") == "internal":
            continue
        edge_id = elem.get("id", "")
        lane = elem.find("lane")
        if edge_id and lane is not None and lane.get("shape"):
            points = []
            for point in str(lane.get("shape")).split():
                x, y = point.split(",")[:2]
                points.append((float(x), float(y)))
            if points:
                shapes[edge_id] = points
    return shapes


def shape_center(points: list[tuple[float, float]]) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    return (sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points))


def svg_path(points: list[tuple[float, float]], bounds: tuple[float, float, float, float], width: int, height: int) -> str:
    min_x, min_y, max_x, max_y = bounds
    scale_x = width / max(max_x - min_x, 1.0)
    scale_y = height / max(max_y - min_y, 1.0)

    def project(point: tuple[float, float]) -> tuple[float, float]:
        x = (point[0] - min_x) * scale_x
        y = height - (point[1] - min_y) * scale_y
        return (round(x, 2), round(y, 2))

    projected = [project(point) for point in points]
    if not projected:
        return ""
    head = projected[0]
    tail = " ".join(f"L {x},{y}" for x, y in projected[1:])
    return f"M {head[0]},{head[1]} {tail}".strip()


def write_route_visualization(stage1: B4Stage1Inputs, rows: list[dict[str, Any]]) -> dict[str, str]:
    shapes = net_shapes(B04_NET)
    route_edges = list(stage1.route_edges)
    route_points = [point for edge in route_edges for point in shapes.get(edge, [])]
    if not route_points:
        return {"route_visualization_html": "", "route_visualization_json": ""}
    min_x = min(point[0] for point in route_points)
    min_y = min(point[1] for point in route_points)
    max_x = max(point[0] for point in route_points)
    max_y = max(point[1] for point in route_points)
    pad = 80.0
    bounds = (min_x - pad, min_y - pad, max_x + pad, max_y + pad)
    width, height = 1180, 760
    route_path = svg_path(route_points, bounds, width, height)

    markers = []
    for label, edge_id, css in [
        ("FIRE STATION START", route_edges[0], "start"),
        ("MERGE", stage1.departure.mainline_target_edge, "merge"),
        ("SEOUL STATION FRONT", route_edges[-1], "target"),
    ]:
        center = shape_center(shapes.get(edge_id, []))
        d = svg_path([center], bounds, width, height).replace("M ", "")
        x, y = d.split(",")[:2] if d else ("0", "0")
        markers.append({"label": label, "edge_id": edge_id, "x": x, "y": y, "css": css})

    movement_markers = []
    for movement in stage1.movements:
        center = shape_center(shapes.get(movement.from_edge, []))
        d = svg_path([center], bounds, width, height).replace("M ", "")
        if not d:
            continue
        x, y = d.split(",")[:2]
        movement_markers.append({"movement_id": movement.movement_id, "mapped": movement.mapped_s_segment, "x": x, "y": y})

    payload = {
        "schema": "compact_v9_b004_b04_b4_route_comparison_visualization.v1",
        "generated_at": utc_now(),
        "route_edges": route_edges,
        "start_edge": route_edges[0],
        "merge_edge": stage1.departure.mainline_target_edge,
        "target_edge": route_edges[-1],
        "entry_tls": stage1.departure.merge_control_tls,
        "rows": rows,
        "movement_markers": movement_markers,
    }
    write_json(ROUTE_VISUALIZATION_JSON, payload)

    cards = []
    for row in rows:
        cards.append(
            "<div class='card'>"
            f"<h2>{html.escape(str(row.get('mode', '')))}</h2>"
            f"<b>{html.escape(str(row.get('T_actual_EMV_sec') or row.get('T_free_EMV_sec') or ''))}s</b>"
            f"<span>D_E {html.escape(str(row.get('D_E_sec', '')))}s</span>"
            f"<span>score {html.escape(str(row.get('objective_score', '')))}</span>"
            f"<span>signals {html.escape(str(row.get('signal_event_count', '')))}</span>"
            "</div>"
        )
    marker_svg = "\n".join(
        f"<circle class='{item['css']}' cx='{item['x']}' cy='{item['y']}' r='9'/><text x='{float(item['x']) + 12}' y='{float(item['y']) - 8}'>{html.escape(item['label'])}</text>"
        for item in markers
    )
    movement_svg = "\n".join(
        f"<circle class='movement' cx='{item['x']}' cy='{item['y']}' r='5'><title>{html.escape(item['movement_id'])} {html.escape(item['mapped'])}</title></circle>"
        for item in movement_markers
    )
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compact V9 B004/B04/B4 Route Comparison</title>
<style>
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#172033;background:#f5f7fb}}
header{{padding:16px 20px;background:#fff;border-bottom:1px solid #d8e0ea}}
h1{{margin:0;font-size:22px}} p{{margin:6px 0 0;color:#607086}}
.wrap{{display:grid;grid-template-columns:minmax(0,1fr) 340px;min-height:calc(100vh - 76px)}}
.map{{overflow:auto;background:#dbe5ef}} svg{{background:#eef3f8;min-width:{width}px}}
.route{{fill:none;stroke:#e11d48;stroke-width:6;stroke-linecap:round;stroke-linejoin:round}}
.start{{fill:#0ea5e9;stroke:#075985;stroke-width:3}} .merge{{fill:#f97316;stroke:#7c2d12;stroke-width:3}}
.target{{fill:#ef4444;stroke:#7f1d1d;stroke-width:3}} .movement{{fill:#111827;opacity:.78}}
text{{font-size:13px;font-weight:800;paint-order:stroke;stroke:#fff;stroke-width:4;fill:#111827}}
aside{{background:#fff;border-left:1px solid #d8e0ea;padding:14px;overflow:auto}}
.card{{border:1px solid #d8e0ea;border-radius:8px;padding:12px;margin-bottom:10px;background:#fff}}
.card h2{{margin:0 0 8px;font-size:16px}} .card b{{display:block;font-size:22px}} .card span{{display:block;color:#607086;margin-top:4px}}
code{{background:#edf2f7;border-radius:4px;padding:2px 5px}}
</style>
</head>
<body>
<header><h1>Compact V9 B004/B04/B4 Route Comparison</h1>
<p>B004는 구급차가 중부소방서에서 서울역 앞까지 50km/h 자유류로 주행하는 EMV-only 기준입니다.</p></header>
<div class="wrap">
<main class="map"><svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<path class="route" d="{route_path}"/>
{marker_svg}
{movement_svg}
</svg></main>
<aside>
<div class="card"><h2>Route</h2><span>start <code>{html.escape(route_edges[0])}</code></span><span>merge <code>{html.escape(stage1.departure.mainline_target_edge)}</code></span><span>target <code>{html.escape(route_edges[-1])}</code></span><span>TLS <code>{html.escape(stage1.departure.merge_control_tls)}</code></span></div>
{''.join(cards)}
</aside>
</div>
</body></html>
"""
    ROUTE_VISUALIZATION_HTML.parent.mkdir(parents=True, exist_ok=True)
    ROUTE_VISUALIZATION_HTML.write_text(html_text, encoding="utf-8")
    return {"route_visualization_html": rel(ROUTE_VISUALIZATION_HTML), "route_visualization_json": rel(ROUTE_VISUALIZATION_JSON)}


def summarize_task(
    task: B4RunTask,
    *,
    stage1: B4Stage1Inputs,
    phase_config: B4RuntimePhaseConfig,
    free_reference: dict[str, Any],
    free_rows_by_id: dict[str, dict[str, Any]],
    sumo_exit_code: int,
    wall_time_sec: float,
    events: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
    monitor_fields: dict[str, Any] | None = None,
    failure_reason: str = "",
    emit_fcd: bool = False,
    params: B4MvpParams | None = None,
) -> dict[str, Any]:
    paths = write_sumo_config(task, phase_config, emit_fcd=emit_fcd, stage1=stage1)
    tripinfo = parse_tripinfo(paths["tripinfo"])
    emergency = tripinfo["emergency"] or {}
    background = tripinfo["background"]
    background_departed = count_background_departures(task.background_route)
    background_arrived = len(background)
    durations = [safe_float(row.get("duration")) for row in background if row.get("duration") not in {"", None}]
    delays = [safe_float(row.get("timeLoss")) for row in background if row.get("timeLoss") not in {"", None}]
    stats = stats or {}
    monitor_fields = monitor_fields or {}
    params = params or B4ThetaParams()
    row = base_result_row(task, stage1, params, phase_config)
    emergency_tripinfo_found = bool(emergency)
    monitor_fields["emergency_tripinfo_found"] = emergency_tripinfo_found
    termination_reason = str(monitor_fields.get("termination_reason") or "")
    if not failure_reason and termination_reason in {"emergency_stuck", "hard_max_sim_time"}:
        failure_reason = termination_reason
    if not failure_reason and task.mode == B4_MODE and not emergency_tripinfo_found:
        failure_reason = termination_reason or "missing_emergency_tripinfo"
    t_actual_emv = safe_float(emergency.get("duration"), 0.0) if emergency else 0.0
    t_free_emv = safe_float(free_reference.get("T_free_EMV_sec"), 0.0)
    D_E_sec = round(t_actual_emv - t_free_emv, 6) if emergency else ""
    veh_metrics = actual_v_vehicle_metrics(background, free_rows_by_id, task.background_route, phase_config.hard_max_sim_time)
    D_G_sec = veh_metrics["D_G_sec"] if veh_metrics["D_G_sec"] != "" else 0.0
    score = objective_score(safe_float(D_E_sec), safe_float(D_G_sec)) if emergency else ""
    row.update({
        "sumo_exit_code": sumo_exit_code,
        **monitor_fields,
        "emergency_departed": bool(emergency) or bool(monitor_fields.get("emergency_seen_by_controller")),
        "emergency_arrived": bool(emergency),
        "emergency_teleport": str(emergency.get("vaporized", "false")).lower() == "true" if emergency else False,
        "background_departed": background_departed,
        "background_arrived": background_arrived,
        "background_teleported": 0,
        "background_arrived_ratio": round(background_arrived / background_departed, 6) if background_departed else 0.0,
        "general_vehicle_count": background_arrived,
        "general_mean_travel_time_sec": round(sum(durations) / len(durations), 6) if durations else "",
        "general_mean_delay_sec": round(sum(delays) / len(delays), 6) if delays else "",
        "T_actual_EMV_sec": t_actual_emv if emergency else "",
        "T_free_EMV_sec": t_free_emv,
        "D_E_sec": D_E_sec,
        **veh_metrics,
        "objective_score": score,
        "final_status": "PASS" if sumo_exit_code == 0 and bool(emergency) and failure_reason not in {"emergency_stuck", "hard_max_sim_time", "missing_emergency_tripinfo"} else "FAIL",
        "failed": failure_reason in {"emergency_stuck", "hard_max_sim_time", "missing_emergency_tripinfo"} or sumo_exit_code != 0,
        "failure_reason": failure_reason,
        "wall_time_sec": round(wall_time_sec, 6),
        "signal_events_csv": rel(task.run_dir / "signal_events.csv") if task.mode == B4_MODE else "",
    })
    for field in [
        "signal_event_count",
        "stage2_hold_count",
        "stage2_hold_total_sec",
        "stage2_release_count",
        "stage3_preemption_count",
        "stage3_restore_count",
        "trigger_local_fill_count",
        "trigger_speed_count",
        "bottleneck_mode_count",
        "max_active_movement_count",
        "signal_burden_sec",
        "queue_method_primary",
        "queue_max_m",
        "queue_p95_m",
        "tls_queue_max_m",
        "queue_local_fill_80m_max",
        "queue_local_fill_100m_max",
        "queue_local_fill_120m_max",
        "queue_corridor_fill_250m_max",
        "queue_trigger_count",
        "queue_sampling_period_sec",
        "queue_runtime_lane_count",
        "queue_runtime_call_mode",
        "queue_calibration_source",
    ]:
        row[field] = stats.get(field, 0)
    row["b0_emergency_travel_time_sec"] = ""
    row["b0_T_actual_EMV_sec"] = ""
    row["b4_T_actual_EMV_sec"] = ""
    row["b4_emergency_travel_delta_sec"] = ""
    row["b4_minus_b0_EMV_sec"] = ""
    row["b4_performance_status"] = ""
    if events is not None and task.mode in {B04_MODE, B4_MODE}:
        write_csv(task.run_dir / "signal_events.csv", events, RUNTIME_EVENT_FIELDS)
    return {field: row.get(field, "") for field in EXPERIMENT_RESULT_FIELDS}


def run_b04_task(
    task: B4RunTask,
    stage1: B4Stage1Inputs,
    phase_config: B4RuntimePhaseConfig,
    free_reference: dict[str, Any],
    free_rows_by_id: dict[str, dict[str, Any]],
    sumo_binary: str | None = None,
    emit_fcd: bool = False,
    emit_tls_states: bool = False,
) -> dict[str, Any]:
    start = time.time()
    command = build_sumo_command(task, sumo_binary, phase_config, emit_fcd=emit_fcd, stage1=stage1)
    traci = import_traci()
    events: list[dict[str, Any]] = []
    monitor_fields: dict[str, Any] = {}
    exit_code = 0
    failure_reason = ""
    tls_dump = (task.run_dir / "tls_states.csv") if emit_tls_states else None
    try:
        traci.start(command)
        events, monitor = run_b04_traci_loop(
            traci,
            stage1,
            task.run_id,
            task.repeat_id,
            phase_config,
            tls_dump_path=tls_dump,
        )
        monitor_fields = monitor.as_result_fields()
    except Exception as exc:  # noqa: BLE001 - runner records failure into result schema.
        exit_code = 1
        failure_reason = str(exc)
    finally:
        try:
            traci.close(True)
        except Exception:
            pass
    return summarize_task(
        task,
        stage1=stage1,
        phase_config=phase_config,
        free_reference=free_reference,
        free_rows_by_id=free_rows_by_id,
        sumo_exit_code=exit_code,
        wall_time_sec=time.time() - start,
        events=events,
        monitor_fields=monitor_fields,
        failure_reason=failure_reason,
        emit_fcd=emit_fcd,
    )


def import_traci() -> Any:
    try:
        import traci  # type: ignore

        return traci
    except ModuleNotFoundError:
        sumo_home = os.environ.get("SUMO_HOME")
        if sumo_home:
            tools = Path(sumo_home) / "tools"
            if tools.is_dir() and str(tools) not in sys.path:
                sys.path.append(str(tools))
        import traci  # type: ignore

        return traci


def run_b4_task(
    task: B4RunTask,
    stage1: B4Stage1Inputs,
    phase_config: B4RuntimePhaseConfig,
    free_reference: dict[str, Any],
    free_rows_by_id: dict[str, dict[str, Any]],
    sumo_binary: str | None = None,
    emit_fcd: bool = False,
    params: B4MvpParams | B4ThetaParams | None = None,
    emit_tls_states: bool = False,
) -> dict[str, Any]:
    start = time.time()
    command = build_sumo_command(task, sumo_binary, phase_config, emit_fcd=emit_fcd, stage1=stage1)
    traci = import_traci()
    events: list[dict[str, Any]] = []
    stats: dict[str, Any] = {}
    monitor_fields: dict[str, Any] = {}
    exit_code = 0
    failure_reason = ""
    params = params or B4ThetaParams()
    tls_dump = (task.run_dir / "tls_states.csv") if emit_tls_states else None
    try:
        traci.start(command)
        controller_events, controller_stats, monitor = run_b4_traci_loop(
            traci,
            stage1,
            task.run_id,
            task.repeat_id,
            params,
            phase_config,
            tls_dump_path=tls_dump,
        )
        events = controller_events
        stats = controller_stats.as_result_fields()
        monitor_fields = monitor.as_result_fields()
    except Exception as exc:  # noqa: BLE001 - runner records failure into result schema.
        exit_code = 1
        failure_reason = str(exc)
    finally:
        try:
            traci.close(True)
        except Exception:
            pass
    return summarize_task(
        task,
        stage1=stage1,
        phase_config=phase_config,
        free_reference=free_reference,
        free_rows_by_id=free_rows_by_id,
        sumo_exit_code=exit_code,
        wall_time_sec=time.time() - start,
        events=events,
        stats=stats,
        monitor_fields=monitor_fields,
        failure_reason=failure_reason,
        emit_fcd=emit_fcd,
        params=params,
    )


def attach_b0_b4_comparison(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    b04 = next((row for row in rows if row.get("mode") == B04_MODE), None)
    b4_rows = [row for row in rows if row.get("mode") == B4_MODE]
    if not b04:
        return rows
    b04_time = safe_float(b04.get("T_actual_EMV_sec"), 0.0)
    if b04_time <= 0:
        return rows
    for row in b4_rows:
        b4_time = safe_float(row.get("T_actual_EMV_sec"), 0.0)
        if b4_time <= 0:
            row["b0_emergency_travel_time_sec"] = b04_time
            row["b0_T_actual_EMV_sec"] = b04_time
            row["b4_performance_status"] = "missing_b4_ev_time"
            continue
        delta = round(b4_time - b04_time, 6)
        row["b0_emergency_travel_time_sec"] = b04_time
        row["b0_T_actual_EMV_sec"] = b04_time
        row["b4_T_actual_EMV_sec"] = b4_time
        row["b4_emergency_travel_delta_sec"] = delta
        row["b4_minus_b0_EMV_sec"] = delta
        row["b4_performance_status"] = "faster_than_b0" if delta < 0 else "not_faster_than_b0"
    return rows


VERIFICATION_SUMMARY_FIELDS = [
    "run_id",
    "parameter_id",
    "stage2_measurement_scale",
    "stage3_measurement_scale",
    "stage2_synthetic_demand",
    "stage2_hold_count",
    "stage2_release_count",
    "caseB_rows",
    "green_dur_violation_count",
    "gate_ta_mismatch_count",
    "verification_pass",
    "failure_reasons",
]


def build_verification_summary(rows: list[dict[str, Any]], event_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for row in [item for item in rows if item.get("mode") == B4_MODE]:
        run_id = str(row.get("run_id", ""))
        parameter_id = str(row.get("parameter_id", ""))
        related = [
            event
            for event in event_rows
            if str(event.get("run_id", "")) == run_id and str(event.get("parameter_id", "")) == parameter_id
        ]
        stage2_hold_count = sum(1 for event in related if event.get("stage") == "stage2" and event.get("action") == "RED_HOLD")
        stage2_release_count = sum(1 for event in related if event.get("stage") == "stage2" and event.get("action") == "RELEASE")
        caseb_rows = sum(1 for event in related if event.get("stage") == "stage3" and event.get("case_type") == "caseB")
        green_dur_violation_count = 0
        gate_ta_mismatch_count = 0
        for event in related:
            if event.get("stage") != "stage3":
                continue
            green_dur = safe_float(event.get("green_dur_sec"), 0.0)
            gm = safe_float(event.get("Gm_sec"), 0.0)
            if green_dur > 0.0 and gm > 0.0 and green_dur < gm:
                green_dur_violation_count += 1
            if event.get("action") in {"GREEN_REQUEST", "GREEN_ACTIVE"} and str(event.get("ta_triggered")).lower() not in {"true", "1"}:
                gate_ta_mismatch_count += 1

        reasons: set[str] = set()
        stage2_scale = safe_float(row.get("stage2_measurement_scale"), 1.0)
        stage3_scale = safe_float(row.get("stage3_measurement_scale"), 1.0)
        if stage2_scale > 1.0 and stage2_hold_count == 0:
            stage2_events = [event for event in related if event.get("stage") == "stage2"]
            if not stage2_events or all(
                safe_float(event.get("scaled_Lq_merge_m"), 0.0) <= 0.0
                and safe_float(event.get("scaled_n_occ_runtime_veh"), 0.0) <= 0.0
                for event in stage2_events
            ):
                reasons.add("ZERO_MEASUREMENT_CANNOT_SCALE")
            elif any(str(event.get("SafetyGate_result", "")).startswith("DENY") for event in stage2_events):
                reasons.add("SAFETY_DENIED")
            else:
                reasons.add("SCALED_VALUE_BELOW_THRESHOLD")
        if stage3_scale > 1.0 and caseb_rows == 0:
            stage3_events = [event for event in related if event.get("stage") == "stage3"]
            if any(str(event.get("SafetyGate_result", "")).startswith("DENY") for event in stage3_events):
                reasons.add("SAFETY_DENIED")
            elif any(event.get("gate_result") == "CONTINUE_TOO_FAR" for event in stage3_events):
                reasons.add("DELTA_GATE_CLOSED")
            elif any(event.get("case_type") == "caseA_boundary" for event in stage3_events):
                reasons.add("NO_DOWNSTREAM")
            else:
                reasons.add("SCALED_VALUE_BELOW_THRESHOLD")
        if green_dur_violation_count:
            reasons.add("GREEN_DUR_BELOW_GM")
        if gate_ta_mismatch_count:
            reasons.add("GATE_TA_MISMATCH")
        verification_pass = (
            stage2_hold_count > 0
            and stage2_release_count > 0
            and caseb_rows > 0
            and green_dur_violation_count == 0
            and gate_ta_mismatch_count == 0
        )
        summaries.append({
            "run_id": run_id,
            "parameter_id": parameter_id,
            "stage2_measurement_scale": row.get("stage2_measurement_scale", ""),
            "stage3_measurement_scale": row.get("stage3_measurement_scale", ""),
            "stage2_synthetic_demand": row.get("stage2_synthetic_demand", ""),
            "stage2_hold_count": stage2_hold_count,
            "stage2_release_count": stage2_release_count,
            "caseB_rows": caseb_rows,
            "green_dur_violation_count": green_dur_violation_count,
            "gate_ta_mismatch_count": gate_ta_mismatch_count,
            "verification_pass": verification_pass,
            "failure_reasons": ";".join(sorted(reasons)),
        })
    return summaries


def queue_runtime_summary(stage1: B4Stage1Inputs, rows: list[dict[str, Any]]) -> dict[str, Any]:
    tls_queue_map: dict[str, dict[str, Any]] = {}
    for movement in stage1.movements:
        payload = tls_queue_map.setdefault(movement.tls_id, {"movement_ids": [], "lanes": set()})
        payload["movement_ids"].append(movement.movement_id)
        payload["lanes"].update(movement.approach_lanes)
        payload["lanes"].update(movement.local_storage_lanes)
    serial_tls_map = {
        tls_id: {
            "movement_ids": payload["movement_ids"],
            "unique_lane_count": len(payload["lanes"]),
            "lanes": sorted(payload["lanes"]),
        }
        for tls_id, payload in tls_queue_map.items()
    }
    calibration_map = {
        movement_id: {
            "tls_id": prior.tls_id,
            "source": prior.source,
            "reference_queue_m": prior.reference_queue_m,
            "runtime_baseline_queue_m": prior.runtime_baseline_queue_m,
            "calibration_factor": prior.calibration_factor,
        }
        for movement_id, prior in stage1.queue_calibration_priors.items()
    }
    b4_rows = [row for row in rows if row.get("mode") == B4_MODE]
    return {
        "schema": "compact_v9_B4_runtime_queue_summary.v1",
        "queue_runtime_call_mode": "unique_lane_snapshot",
        "unique_lane_count": len(unique_queue_lanes_for_movements(stage1.movements)),
        "tls_queue_map": serial_tls_map,
        "calibration_map": calibration_map,
        "queue_results": {
            "queue_method_primary": next((row.get("queue_method_primary", "") for row in b4_rows if row.get("queue_method_primary") != ""), ""),
            "queue_max_m": max((safe_float(row.get("queue_max_m")) for row in b4_rows), default=0.0),
            "queue_p95_m": max((safe_float(row.get("queue_p95_m")) for row in b4_rows), default=0.0),
            "tls_queue_max_m": max((safe_float(row.get("tls_queue_max_m")) for row in b4_rows), default=0.0),
            "queue_trigger_count": sum(int(safe_float(row.get("queue_trigger_count"))) for row in b4_rows),
        },
        "e2_enabled": False,
        "shockwave_enabled": False,
        "shockwave_policy": "method=shockwave_pending only when corridor fill indicates spillback; no state-space correction in MVP",
        "stale_data_fallback_policy": "missing lane snapshots keep queue fields present with low confidence; tripinfo is never runtime truth",
    }


def comparison_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "run_id": row.get("run_id"),
            "mode": row.get("mode"),
            "scenario_name": row.get("scenario_name"),
            "phase": row.get("phase"),
            "termination_reason": row.get("termination_reason"),
            "termination_time_sec": row.get("termination_time_sec"),
            "failure_reason": row.get("failure_reason"),
            "final_status": row.get("final_status"),
            "recovery_detected": row.get("recovery_detected"),
            "objective_includes_recovery": row.get("objective_includes_recovery"),
            "emergency_seen_by_controller": row.get("emergency_seen_by_controller"),
            "emergency_last_edge": row.get("emergency_last_edge"),
            "emergency_last_route_index": row.get("emergency_last_route_index"),
            "emergency_stuck_duration_sec": row.get("emergency_stuck_duration_sec"),
            "T_EMV_sec": row.get("T_actual_EMV_sec") or row.get("T_free_EMV_sec"),
            "T_free_EMV_sec": row.get("T_free_EMV_sec"),
            "D_E_sec": row.get("D_E_sec"),
            "V_G_vehicle_count": row.get("V_G_vehicle_count"),
            "T_G_actual_mean_sec": row.get("T_G_actual_mean_sec"),
            "T_G_free_mean_sec": row.get("T_G_free_mean_sec"),
            "D_G_sec": row.get("D_G_sec"),
            "objective_score": row.get("objective_score"),
            "emergency_arrived": row.get("emergency_arrived"),
            "emergency_teleport": row.get("emergency_teleport"),
            "signal_event_count": row.get("signal_event_count"),
            "stage2_hold_count": row.get("stage2_hold_count"),
            "stage3_preemption_count": row.get("stage3_preemption_count"),
            "route_visualization_html": row.get("route_visualization_html"),
        }
        for row in rows
    ]


def read_stage1_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_ta_b0_measurement_review(stage1: B4Stage1Inputs, rows: list[dict[str, Any]]) -> str:
    measured_rows = read_stage1_csv(stage1.stage1_dir / "b4_b0_measured_signal_params.csv")
    stage2_rows = read_stage1_csv(stage1.stage1_dir / "b4_stage2_b0_merge_hold_params.csv")
    policy = read_json(stage1.stage1_dir / "b4_ta_proxy_policy.json") if (stage1.stage1_dir / "b4_ta_proxy_policy.json").is_file() else {}
    stage2_policy = read_json(stage1.stage1_dir / "b4_stage2_b0_merge_hold_params.json") if (stage1.stage1_dir / "b4_stage2_b0_merge_hold_params.json").is_file() else {}

    def table(table_rows: list[dict[str, Any]], fields: list[str]) -> str:
        body = []
        for row in table_rows:
            body.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(field, '')))}</td>" for field in fields) + "</tr>")
        return (
            "<table><thead><tr>"
            + "".join(f"<th>{html.escape(field)}</th>" for field in fields)
            + "</tr></thead><tbody>"
            + "".join(body)
            + "</tbody></table>"
        )

    variable_rows = [
        {"original": "q_avg, q_max", "b4": "q_avg_b0_proxy_veh, q_max_b0_proxy_veh", "meaning": f"{B4_PRIMARY_CANDIDATE} no-control SUMO lane/edge data에서 추정한 평균/최대 queue proxy"},
        {"original": "tQ_hist", "b4": "tQ_hist_b0_sec", "meaning": "B0 q_max proxy를 포화류율로 방출한다고 본 시간"},
        {"original": "lambda", "b4": "lambda_b0_vph", "meaning": "B0 laneData flow 기반 접근부 교통량 proxy"},
        {"original": "D_merge, tE_merge", "b4": "D_merge_m, tE_merge_sec", "meaning": "소방서 출발 edge부터 본선 합류 edge까지 route geometry proxy와 50km/h 기준 도달시간"},
        {"original": "C_merge, n_occ", "b4": "C_merge_proxy_veh, n_occ_runtime_veh", "meaning": "50m 합류공간 용량 proxy와 runtime TraCI merge-zone lane 점유 차량수"},
        {"original": "T_hold", "b4": "T_hold_proxy_sec", "meaning": "tE_merge - t_clear - tS_merge. 실제 hold timing은 안전상 35초 lead 유지"},
        {"original": "TA", "b4": "TA_proxy_sec", "meaning": "tE - tS - tQ. 기존 fill/speed 안전필터를 통과한 경우에만 제어 gate로 사용"},
    ]
    result_fields = [
        "mode", "final_status", "termination_reason", "T_actual_EMV_sec",
        "T_free_EMV_sec", "D_E_sec", "objective_score", "emergency_arrived",
        "emergency_teleport", "signal_event_count",
    ]
    measured_fields = [
        "movement_id", "q_avg_b0_proxy_veh", "q_max_b0_proxy_veh", "tQ_hist_b0_sec",
        "lambda_b0_vph", "L_local_m", "L_corridor_m", "C_local_proxy_veh", "measurement_source",
    ]
    stage2_fields = [
        "stage2_param_id", "merge_control_tls", "D_merge_m", "L_merge_m",
        "tE_merge_sec", "C_merge_proxy_veh", "n_need_proxy_veh", "tS_merge_sec",
        "q_A_proxy_veh", "b0_merge_n_occ_mean_proxy_veh", "b0_merge_n_occ_max_proxy_veh",
        "b0_background_inflow_lambda_vph", "b0_merge_waiting_max_sec",
        "b0_merge_halting_proxy_max", "measurement_source",
    ]
    B4_TA_B0_MEASUREMENT_REVIEW_HTML.parent.mkdir(parents=True, exist_ok=True)
    B4_TA_B0_MEASUREMENT_REVIEW_HTML.write_text(f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>B4 TA Proxy and B0 Measurement Review</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;color:#172033}}
header{{padding:20px;border-bottom:1px solid #d8e0ea;background:#f8fafc}}
section{{padding:18px 22px}} table{{border-collapse:collapse;width:100%;font-size:12px;margin:12px 0 24px}}
th,td{{border:1px solid #d8e0ea;padding:6px;text-align:left;vertical-align:top}} th{{background:#f1f5f9}}
code{{background:#edf2f7;border-radius:4px;padding:2px 5px}} pre{{background:#f8fafc;border:1px solid #d8e0ea;padding:12px;overflow:auto}}
</style>
</head>
<body>
<header>
<h1>B4 TA Proxy + B0 Measurement Review</h1>
<p>B0 측정값은 현장 실측값이 아니라 <code>{html.escape(B4_PRIMARY_CANDIDATE)}</code> no-control SUMO 내부 edge/lane data proxy입니다.</p>
<p>실제 제어 조건: <code>(local_fill_100m &gt;= 0.50 OR speed &lt;= 15) AND TA_proxy_sec &lt;= 0</code></p>
</header>
<section>
<h2>TA Policy</h2>
<pre>{html.escape(json.dumps(policy, ensure_ascii=False, indent=2))}</pre>
<h2>Stage 2 Merge Hold Policy</h2>
<pre>{html.escape(json.dumps(stage2_policy, ensure_ascii=False, indent=2))}</pre>
<h2>1.3 Variables Reflected in Simulation</h2>
{table(variable_rows, ["original", "b4", "meaning"])}
<h2>B0 Measured Proxy Parameters</h2>
{table(measured_rows, measured_fields)}
<h2>Stage 2 B0 Merge Hold Proxy</h2>
<p>Stage 2는 entry TLS background inflow hold만 사용하고 EV release는 <code>uncontrolled_by_merge_tls</code> WARN을 유지합니다. 아래 값은 B0 SUMO proxy와 runtime 기록용 연구식입니다.</p>
{table(stage2_rows, stage2_fields)}
<h2>B04/B4 Result Rows</h2>
{table(rows, result_fields)}
</section>
</body></html>
""", encoding="utf-8")
    return rel(B4_TA_B0_MEASUREMENT_REVIEW_HTML)


def write_metric_outputs(
    rows: list[dict[str, Any]],
    tasks: list[B4RunTask],
    stage1: B4Stage1Inputs,
    metrics_root: Path = METRICS_ROOT,
    *,
    emit_fcd: bool = False,
) -> dict[str, str]:
    metrics_root.mkdir(parents=True, exist_ok=True)
    rows = attach_b0_b4_comparison(rows)
    visualization_outputs = write_route_visualization(stage1, rows)
    ta_review_html = write_ta_b0_measurement_review(stage1, rows)
    for row in rows:
        row["route_visualization_html"] = visualization_outputs.get("route_visualization_html", "")
    experiment_results = metrics_root / "experiment_results.csv"
    signal_events = metrics_root / "signal_events.csv"
    compare = metrics_root / "compare_b0_b4.csv"
    b004_compare = metrics_root / "b004_b04_b4_comparison.csv"
    verification_summary_csv = metrics_root / "verification_summary.csv"
    verification_summary_json = metrics_root / "verification_summary.json"
    summary_json = metrics_root / "experiment_summary.json"
    write_csv(experiment_results, rows, EXPERIMENT_RESULT_FIELDS)

    event_rows: list[dict[str, Any]] = []
    for task in tasks:
        path = task.run_dir / "signal_events.csv"
        if path.is_file():
            with path.open("r", encoding="utf-8", newline="") as file:
                event_rows.extend(csv.DictReader(file))
    write_csv(signal_events, event_rows, RUNTIME_EVENT_FIELDS)
    verification_rows = build_verification_summary(rows, event_rows)
    write_csv(verification_summary_csv, verification_rows, VERIFICATION_SUMMARY_FIELDS)
    write_json(
        verification_summary_json,
        {
            "schema": "compact_v9_B4_stage2_stage3_verification_summary.v1",
            "generated_at": utc_now(),
            "rows": verification_rows,
        },
    )

    compare_rows = [
        {
            "run_id": row.get("run_id"),
            "parameter_id": row.get("parameter_id"),
            "b0_emergency_travel_time_sec": row.get("b0_emergency_travel_time_sec"),
            "b4_emergency_travel_time_sec": row.get("T_actual_EMV_sec"),
            "b4_emergency_travel_delta_sec": row.get("b4_emergency_travel_delta_sec"),
            "b4_performance_status": row.get("b4_performance_status"),
        }
        for row in rows
        if row.get("mode") == B4_MODE
    ]
    write_csv(
        compare,
        compare_rows,
        [
            "run_id",
            "parameter_id",
            "b0_emergency_travel_time_sec",
            "b4_emergency_travel_time_sec",
            "b4_emergency_travel_delta_sec",
            "b4_performance_status",
        ],
    )
    write_csv(
        b004_compare,
        comparison_rows(rows),
        [
            "run_id",
            "mode",
            "scenario_name",
            "phase",
            "termination_reason",
            "termination_time_sec",
            "failure_reason",
            "final_status",
            "recovery_detected",
            "objective_includes_recovery",
            "emergency_seen_by_controller",
            "emergency_last_edge",
            "emergency_last_route_index",
            "emergency_stuck_duration_sec",
            "T_EMV_sec",
            "T_free_EMV_sec",
            "D_E_sec",
            "V_G_vehicle_count",
            "T_G_actual_mean_sec",
            "T_G_free_mean_sec",
            "D_G_sec",
            "objective_score",
            "emergency_arrived",
            "emergency_teleport",
            "signal_event_count",
            "stage2_hold_count",
            "stage3_preemption_count",
            "route_visualization_html",
        ],
    )
    write_json(
        summary_json,
        {
            "schema": "compact_v9_B4_runtime_mvp_experiment_summary.v1",
            "generated_at": utc_now(),
            "primary_candidate": B4_PRIMARY_CANDIDATE,
            "manifest_selected_candidate_role": "primary_selected",
            "parameter_id": B4_PARAMETER_ID,
            "seed": DEFAULT_SEED,
            "repeat_count": 1,
            "phase": rows[0].get("phase", B4RuntimePhaseConfig.bo_smoke().phase) if rows else B4RuntimePhaseConfig.bo_smoke().phase,
            "ev_departure_policy": rows[0].get("ev_departure_policy", "fixed") if rows else "fixed",
            "ev_depart_sec": rows[0].get("ev_depart_sec", 600) if rows else 600,
            "ev_depart_randomized": rows[0].get("ev_depart_randomized", False) if rows else False,
            "objective_includes_recovery": False,
            "recovery_threshold_policy": rows[0].get("recovery_threshold_policy", "") if rows else "",
            "bo_enabled": False,
            "multi_seed_enabled": False,
            "fcd_enabled": emit_fcd,
            "B004_is_analytic_reference": True,
            "free_time_method": FREE_TIME_METHOD,
            "vehicle_free_time_method": VEHICLE_FREE_TIME_METHOD,
            "B004_meaning": "EMV-only free-flow reference from Jungbu fire station to Seoul Station front.",
            "V_definition": "Stage 1 controllable movement edge route-overlap background vehicles; no FCD edge-pass claim.",
            "queue_runtime": queue_runtime_summary(stage1, rows),
            "outputs": {
                "experiment_results_csv": rel(experiment_results),
                "signal_events_csv": rel(signal_events),
                "compare_b0_b4_csv": rel(compare),
                "b004_b04_b4_comparison_csv": rel(b004_compare),
                "verification_summary_csv": rel(verification_summary_csv),
                "verification_summary_json": rel(verification_summary_json),
                "experiment_summary_json": rel(summary_json),
                **visualization_outputs,
                "b4_ta_b0_measurement_review_html": ta_review_html,
                "b004_free_time_reference_json": rel(B004_FREE_REFERENCE_JSON),
                "b004_vehicle_free_times_csv": rel(B004_VEHICLE_FREE_TIMES_CSV),
            },
            "rows": rows,
        },
    )
    return {
        "experiment_results_csv": rel(experiment_results),
        "signal_events_csv": rel(signal_events),
        "compare_b0_b4_csv": rel(compare),
        "b004_b04_b4_comparison_csv": rel(b004_compare),
        "verification_summary_csv": rel(verification_summary_csv),
        "verification_summary_json": rel(verification_summary_json),
        "experiment_summary_json": rel(summary_json),
        **visualization_outputs,
        "b4_ta_b0_measurement_review_html": ta_review_html,
        "b004_free_time_reference_json": rel(B004_FREE_REFERENCE_JSON),
        "b004_vehicle_free_times_csv": rel(B004_VEHICLE_FREE_TIMES_CSV),
    }


def run_pipeline(
    *,
    modes: tuple[str, ...] = (B004_MODE, B04_MODE, B4_MODE),
    phase: str = "bo-smoke",
    run_id: str | None = None,
    run_root: Path = RUN_ROOT,
    metrics_root: Path = METRICS_ROOT,
    sumo_binary: str | None = None,
    dry_run: bool = False,
    emit_fcd: bool = False,
    net_file: Path = B04_NET,
    background_route: Path = B04_AA_BACKGROUND_ROUTE,
    firetruck_route: Path = B04_FIRETRUCK_ROUTE_XML,
    hard_max_sim_time: float | None = None,
    b4_params: B4MvpParams | None = None,
    stage1_dir: Path | None = None,
    b4_stage2_measurement_scale: float = 1.0,
    b4_stage3_measurement_scale: float = 1.5,
    b4_stage2_synthetic_demand: bool = False,
) -> dict[str, Any]:
    net_file = net_file if net_file.is_absolute() else (PROJECT_ROOT / net_file)
    background_route = background_route if background_route.is_absolute() else (PROJECT_ROOT / background_route)
    firetruck_route = firetruck_route if firetruck_route.is_absolute() else (PROJECT_ROOT / firetruck_route)
    stage1_dir = stage1_dir.resolve() if stage1_dir is not None else None
    stage1 = validate_static_inputs(stage1_dir=stage1_dir, net_file=net_file, background_route=background_route, firetruck_route=firetruck_route)
    phase_config = B4RuntimePhaseConfig.from_phase(phase)
    if hard_max_sim_time is not None:
        phase_config = replace(phase_config, hard_max_sim_time=float(hard_max_sim_time))
    phase_config = replace(
        phase_config,
        stage2_measurement_scale=max(float(b4_stage2_measurement_scale), 0.0),
        stage3_measurement_scale=max(float(b4_stage3_measurement_scale), 0.0),
        stage2_synthetic_demand=bool(b4_stage2_synthetic_demand),
    )
    tasks = build_tasks(run_id=run_id, modes=modes, run_root=run_root, net_file=net_file, background_route=background_route, firetruck_route=firetruck_route)
    for task in tasks:
        if not task.is_analytic:
            write_sumo_config(task, phase_config, emit_fcd=emit_fcd, stage1=stage1)
    free_reference = build_b004_free_reference(stage1, net_file=net_file, background_route=background_route, firetruck_route=firetruck_route)
    free_rows = read_free_vehicle_rows()
    free_rows_by_id = {row["vehicle_id"]: row for row in free_rows}
    if dry_run:
        return {
            "schema": "compact_v9_B4_runtime_mvp_dry_run.v1",
            "primary_candidate": B4_PRIMARY_CANDIDATE,
            "free_time_method": FREE_TIME_METHOD,
            "phase": phase_config.phase,
            "emit_fcd": emit_fcd,
            "net_file": rel(net_file),
            "background_route_file": rel(background_route),
            "phase_config": phase_config.as_result_fields(),
            "tasks": [
                task.__dict__ | {
                    "run_dir": rel(task.run_dir),
                    "net_file": rel(task.net_file),
                    "background_route": "" if task.is_analytic else rel(task.background_route),
                    "firetruck_route": rel(task.firetruck_route),
                }
                for task in tasks
            ],
        }
    rows = []
    for task in tasks:
        if task.mode == B004_MODE:
            rows.append(b004_result_row(task, stage1, free_reference, phase_config))
        elif task.mode == B04_MODE:
            rows.append(run_b04_task(task, stage1, phase_config, free_reference, free_rows_by_id, sumo_binary, emit_fcd))
        elif task.mode == B4_MODE:
            rows.append(run_b4_task(task, stage1, phase_config, free_reference, free_rows_by_id, sumo_binary, emit_fcd, params=b4_params))
        else:
            raise B4RuntimeError(f"unsupported_mode:{task.mode}")
    outputs = write_metric_outputs(rows, tasks, stage1, metrics_root, emit_fcd=emit_fcd)
    return {
        "schema": "compact_v9_B4_runtime_mvp_run.v1",
        "generated_at": utc_now(),
        "primary_candidate": B4_PRIMARY_CANDIDATE,
        "net_file": rel(net_file),
        "background_route_file": rel(background_route),
        "firetruck_route_file": rel(firetruck_route),
        "phase": phase_config.phase,
        "run_id": tasks[0].run_id if tasks else "",
        "outputs": outputs,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Compact V9 B0/B4 Runtime MVP.")
    parser.add_argument("--modes", default="B004,B04,B4", help="Comma-separated subset of B004,B04,B4. B0 is accepted as a B04 alias.")
    parser.add_argument("--phase", default="bo-smoke", help="Runtime phase. Currently only bo-smoke is implemented.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--run-root", type=Path, default=RUN_ROOT)
    parser.add_argument("--metrics-root", type=Path, default=METRICS_ROOT)
    parser.add_argument("--sumo-binary", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--emit-fcd", action="store_true", help="Write geo FCD output for visualization runs only.")
    parser.add_argument("--net-file", type=Path, default=B04_NET, help="Optional B04/B4 SUMO net file override.")
    parser.add_argument("--background-route", type=Path, default=B04_AA_BACKGROUND_ROUTE, help="Optional B04/B4 background route file override.")
    parser.add_argument("--firetruck-route", type=Path, default=B04_FIRETRUCK_ROUTE_XML, help="Optional emergency/firetruck route XML override.")
    parser.add_argument("--stage1-dir", type=Path, default=None, help="Optional B4 Stage1 directory generated from the same B04 inputs.")
    parser.add_argument("--hard-max-sim-time", type=float, default=None, help="Optional SUMO/B4 hard max simulation time in seconds.")
    parser.add_argument("--b4-parameter-id", default=B4_PARAMETER_ID)
    parser.add_argument("--b4-alpha", type=float, default=None)
    parser.add_argument("--b4-t-lead", type=float, default=None)
    parser.add_argument("--b4-delta-t-thr", type=float, default=None)
    parser.add_argument("--b4-q-ratio", type=float, default=None)
    parser.add_argument("--b4-q-trig", type=float, default=None)
    parser.add_argument("--b4-g-ext", type=float, default=None)
    parser.add_argument("--b4-ext-max", type=float, default=None, help="Legacy alias for --b4-g-ext.")
    parser.add_argument("--b4-hold-max", type=float, default=None, help="Fixed structure override; not a screened decision variable.")
    parser.add_argument("--b4-tau", type=float, default=None, help="EVTSP Case B tau decision-variable override.")
    parser.add_argument("--b4-d-up", type=int, default=None, help="Fixed lookahead/action-budget override; not a screened decision variable.")
    parser.add_argument("--b4-tau-scale", type=float, default=None, help="Deprecated legacy alias; ignored by EVTSP runtime.")
    parser.add_argument("--b4-tau-numerator-gamma", type=float, default=None, help="Deprecated legacy alias; ignored by EVTSP runtime.")
    parser.add_argument("--b4-stage2-measurement-scale", type=float, default=1.0, help="Verification-only scale applied to Stage2 merge queue/occupancy measurements.")
    parser.add_argument("--b4-stage3-measurement-scale", type=float, default=1.5, help="Scale applied to Stage3 Case B queue measurements.")
    parser.add_argument("--b4-stage2-synthetic-demand", action="store_true", help="Verification-only merge-zone demand used to make Stage2 measurements nonzero.")
    parser.add_argument("--b4-theta", action="store_true", help="Compatibility flag; B4ThetaParams is now the default B4 runtime.")
    args = parser.parse_args(argv)
    b4_g_ext = args.b4_g_ext if args.b4_g_ext is not None else args.b4_ext_max
    legacy_q_ratio = None
    if args.b4_q_trig is not None:
        legacy_q_ratio = args.b4_q_trig / 50.0
    b4_params = B4ThetaParams(
        parameter_id=args.b4_parameter_id,
        alpha=args.b4_alpha if args.b4_alpha is not None else B4ThetaParams.alpha,
        t_lead=args.b4_t_lead if args.b4_t_lead is not None else B4ThetaParams.t_lead,
        delta_T_thr=args.b4_delta_t_thr if args.b4_delta_t_thr is not None else B4ThetaParams.delta_T_thr,
        Q_ratio=(
            args.b4_q_ratio
            if args.b4_q_ratio is not None
            else legacy_q_ratio
            if legacy_q_ratio is not None
            else B4ThetaParams.Q_ratio
        ),
        Q_trig=args.b4_q_trig if args.b4_q_trig is not None else B4ThetaParams.Q_trig,
        G_ext=b4_g_ext if b4_g_ext is not None else B4ThetaParams.G_ext,
        tau=args.b4_tau if args.b4_tau is not None else B4ThetaParams.tau,
        hold_max=args.b4_hold_max if args.b4_hold_max is not None else B4ThetaParams.hold_max,
        d_up=args.b4_d_up if args.b4_d_up is not None else B4ThetaParams.d_up,
    )
    try:
        result = run_pipeline(
            modes=parse_modes(args.modes),
            phase=args.phase,
            run_id=args.run_id,
            run_root=args.run_root,
            metrics_root=args.metrics_root,
            sumo_binary=args.sumo_binary,
            dry_run=args.dry_run,
            emit_fcd=args.emit_fcd,
            net_file=args.net_file,
            background_route=args.background_route,
            firetruck_route=args.firetruck_route,
            hard_max_sim_time=args.hard_max_sim_time,
            b4_params=b4_params,
            stage1_dir=args.stage1_dir,
            b4_stage2_measurement_scale=args.b4_stage2_measurement_scale,
            b4_stage3_measurement_scale=args.b4_stage3_measurement_scale,
            b4_stage2_synthetic_demand=args.b4_stage2_synthetic_demand,
        )
    except (B4RunnerError, B4RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
