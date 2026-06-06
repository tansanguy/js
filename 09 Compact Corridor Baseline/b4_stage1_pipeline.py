#!/usr/bin/env python3
"""Build Compact V9 B4 Stage 1 static signal-control inputs.

B4 Stage 1 is an offline preparation step.  It reads the existing B04
baseline demand/queue audit artifacts and writes a B4-only static plan for
human review.  It does not run SUMO, BO, FCD, or the future B4 runtime.
"""

from __future__ import annotations

import argparse
import csv
import html
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / "09 Compact Corridor Baseline"
DATA_ROOT = PROJECT_ROOT / "data_prepared/compact_v9"
STAGE1_DIR = DATA_ROOT / "b4_stage1"
METRICS_ROOT = PROJECT_ROOT / "results/metrics/compact_v9_B04"
QUEUE_AUDIT_DIR = METRICS_ROOT / "queue_audit"
HTML_ROOT = PROJECT_ROOT / "results/html"

B04_PIPELINE = PIPELINE_DIR / "b04_baseline_pipeline.py"
B04_MANIFEST = PROJECT_ROOT / "configs/compact_v9_B04_b0_manifest.json"
B04_NET = DATA_ROOT / "net/jungbu_compact_v9_B04_green18.net.xml"
B04_FIRETRUCK_ROUTE_XML = DATA_ROOT / "routes/firetruck_to_seoul_station_front.rou.xml"
B04_FIRETRUCK_ROUTE_CSV = DATA_ROOT / "routes/firetruck_route.csv"
ENTRY_TLS_SUMMARY = DATA_ROOT / "net/entry_tls_summary.json"
B04_TRAFFIC_DEMAND_REVIEW = QUEUE_AUDIT_DIR / "b04_traffic_demand_review.json"
B04_QUEUE_DEFINITION_AUDIT = QUEUE_AUDIT_DIR / "b04_queue_definition_audit.json"
B04_QUEUE_PROXY_BY_SEGMENT = QUEUE_AUDIT_DIR / "b04_queue_proxy_by_segment.csv"
B04_MEASUREMENT_DIAGNOSTICS = QUEUE_AUDIT_DIR / "b4_queue_measurement_diagnostics.csv"
B04_SEGMENT_EDGE_MAPPING = DATA_ROOT / "map/B04_toegye_segment_edge_mapping.csv"
B04_CSV_SIGNAL_CANDIDATES = DATA_ROOT / "net/B04_csv_signal_candidates.csv"
B4_PRIMARY_CANDIDATE = "B04_ad_variance_smoothed"
B4_PRIMARY_RUN_SUMMARY = METRICS_ROOT / B4_PRIMARY_CANDIDATE / "b0_run_summary.json"
B4_PRIMARY_SPEED_RECALL = METRICS_ROOT / B4_PRIMARY_CANDIDATE / "B04_segment_speed_recall.csv"

B4_ROUTE_MOVEMENT_PLAN = STAGE1_DIR / "b4_route_movement_plan.json"
B4_INTERSECTIONS_CSV = STAGE1_DIR / "b4_intersections.csv"
B4_APPROACH_STORAGE_LINK_PLAN_CSV = STAGE1_DIR / "b4_approach_storage_link_plan.csv"
B4_MERGE_ZONE = STAGE1_DIR / "b4_merge_zone.json"
B4_DEPARTURE_FLOW_PLAN = STAGE1_DIR / "b4_departure_flow_plan.json"
B4_BOTTLENECK_QUEUE_READINESS_CSV = STAGE1_DIR / "b4_bottleneck_queue_readiness.csv"
B4_CASE_B_CANDIDATES_CSV = STAGE1_DIR / "b4_case_b_candidates.csv"
B4_CASE_B_CANDIDATES_JSON = STAGE1_DIR / "b4_case_b_candidates.json"
B4_CONTROL_QUEUE_THRESHOLD_PROPOSAL = STAGE1_DIR / "b4_control_queue_threshold_proposal.json"
B4_B0_MEASURED_SIGNAL_PARAMS_CSV = STAGE1_DIR / "b4_b0_measured_signal_params.csv"
B4_TA_PROXY_POLICY = STAGE1_DIR / "b4_ta_proxy_policy.json"
B4_STAGE2_B0_MERGE_HOLD_PARAMS_JSON = STAGE1_DIR / "b4_stage2_b0_merge_hold_params.json"
B4_STAGE2_B0_MERGE_HOLD_PARAMS_CSV = STAGE1_DIR / "b4_stage2_b0_merge_hold_params.csv"
B4_RUNTIME_INDEX = STAGE1_DIR / "b4_runtime_index.json"
B4_STAGE1_SUMMARY = STAGE1_DIR / "b4_stage1_summary.json"
B4_STAGE1_REVIEW_HTML = HTML_ROOT / "b4_stage1_review.html"
MAINSTREAM_SEGMENT_SKELETON = PROJECT_ROOT / "mainstream_segment_skeleton.csv"

ENTRY_TLS_ID = "COMPACT_V9_FIRE_STATION_ENTRY_TLS"
EV_ID = "emergency_0"
STOPLINE_LOCAL_STORAGE_M = 100.0
LOCAL_STORAGE_COMPARISON_M = [80.0, 100.0, 120.0]
CORRIDOR_STORAGE_MAX_M = 250.0
LOCAL_FILL_TRIGGER = 0.50
SPEED_TRIGGER_KMH = 15.0
TRAFFIC_PRESSURE_LOCAL_FILL = 0.20
BOTTLENECK_LOCAL_FILL = 0.70
BOTTLENECK_CORRIDOR_FILL = 0.50
DISPATCH_LEAD_TIME_SEC = 35.0
DISPATCH_LEAD_TIME_RANGE_SEC = [30.0, 40.0]
MAX_ACTIVE_MOVEMENTS = 3
TA_EV_SPEED_MPS = 13.9
TA_HEADWAY_M = 6.5
TA_SATURATION_FLOW_VPH_PER_LANE = 1800.0
TA_DIRECT_SWITCH_BUFFER_SEC = 5.0
STAGE2_MERGE_DESIGN_LENGTH_M = 50.0
STAGE2_N_NEED_PROXY_VEH = 2.0
STAGE2_MEASUREMENT_SOURCE = "SUMO_B04_AD_B0_laneData_edgeData_proxy"
B4_PRIMARY_EDGE_LANE_SOURCE = "SUMO_B04_AD_B0_edge_lane_data"
B4_PRIMARY_B0_MEASURED_PROXY = "SUMO_B04_AD_B0_measured_proxy"
CASE_B_SEGMENT_IDS = ("S7", "S10", "S11")
CASE_B_DEFAULT_TAU = 0.75
QUEUE_CALIBRATION_MIN = 0.5
QUEUE_CALIBRATION_MAX = 2.0

EVENT_SCHEMA = [
    "time",
    "stage",
    "action_type",
    "tls_id",
    "movement_id",
    "from_edge",
    "to_edge",
    "linkIndex",
    "local_fill_100m",
    "corridor_fill_250m",
    "approach_speed_kmh",
    "density",
    "occupancy",
    "waiting",
    "timeLoss",
    "trigger_reason",
    "target_phase",
    "previous_phase",
    "ev_distance_m",
    "control_mode",
    "safety_status",
    "tE_sec",
    "tS_sec",
    "tQ_sec",
    "TA_proxy_sec",
    "b0_q_avg_proxy_veh",
    "b0_q_max_proxy_veh",
    "b0_tQ_hist_sec",
    "b0_lambda_vph",
    "ta_triggered",
    "ta_formula",
    "ta_input_source",
    "queue_source",
    "case_b_source",
    "tS_source",
    "TA_case",
    "TA_upstream_sec",
    "TA_bottleneck_sec",
    "case_b_mapping_status",
    "case_b_segment_id",
    "case_b_segment_queue_m_proxy",
    "case_b_segment_fill",
    "case_b_same_tls_policy",
    "D_merge_m",
    "tE_merge_sec",
    "L_merge_m",
    "Lq_merge_m",
    "C_merge_proxy_veh",
    "n_need_proxy_veh",
    "n_occ_runtime_veh",
    "n_excess_proxy_veh",
    "t_clear_proxy_sec",
    "T_hold_proxy_sec",
    "b0_merge_n_occ_mean_proxy_veh",
    "b0_merge_n_occ_max_proxy_veh",
    "b0_background_inflow_lambda_vph",
    "stage2_formula",
    "stage2_measurement_source",
    "runtime_or_b0_fallback",
]

B4_DECISION_VARIABLES = ["alpha", "t_lead", "delta_T_thr", "G_ext", "Q_trig"]


def decision_variable_screening_payload() -> dict[str, Any]:
    return {
        "schema": "compact_v9_B4_decision_variable_screening.v1",
        "model_form": "Y = f(X, Z)",
        "decision_variables_X": B4_DECISION_VARIABLES,
        "screening_filters": [
            "controllability",
            "state_variable_exclusion",
            "safety_constraint_exclusion",
            "objective_sensitivity",
            "structure_and_scenario_factor_exclusion",
            "algorithm_registration",
        ],
        "state_variables_S": ["Lq", "n_occ", "elapsed_green", "TA_proxy_sec", "T_hold_proxy_sec"],
        "environment_variables_Z": [
            "network_geometry",
            "vehicle_physics",
            "simulation_begin_end_step_seed",
            "demand_scale_scenario",
        ],
        "safety_constraints": ["phase_order", "yellow_clearance", "all_red_clearance", "minimum_pedestrian_crossing_time"],
        "fixed_structure_params": ["tau", "beta", "hold_max", "d_up", "tau_scale", "tau_numerator_gamma"],
        "concept_only_or_unimplemented": ["green_split", "offset", "cycle_length", "green_wave_intersection_set"],
        "notes": [
            "tau is retained as a fixed Case B bottleneck threshold, not a screened optimization variable.",
            "seed and demand scale remain experiment design or robustness factors, not theta variables.",
        ],
    }


class B4Stage1Error(RuntimeError):
    """Expected B4 Stage 1 failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def require_input(path: Path, label: str) -> Path:
    if not path.is_file():
        raise B4Stage1Error(f"missing_b4_stage1_input:{label}:{rel(path)}")
    return path


def read_json(path: Path) -> dict[str, Any]:
    require_input(path, path.name)
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise B4Stage1Error(f"json_root_not_object:{rel(path)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    require_input(path, path.name)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {"", None}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in {"", None}:
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def load_b04_pipeline() -> Any:
    spec = importlib.util.spec_from_file_location("compact_v9_b04_pipeline_for_b4_stage1", B04_PIPELINE)
    if spec is None or spec.loader is None:
        raise B4Stage1Error(f"module_load_failed:{rel(B04_PIPELINE)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def route_metadata() -> dict[str, Any]:
    rows = read_csv(B04_FIRETRUCK_ROUTE_CSV)
    row = rows[0] if rows else {}
    route_root = ET.parse(require_input(B04_FIRETRUCK_ROUTE_XML, "firetruck_route_xml")).getroot()
    route = route_root.find("route")
    vehicle = route_root.find("vehicle")
    edges = str(route.get("edges") if route is not None else row.get("route_edges", "")).split()
    if not edges:
        raise B4Stage1Error("missing_b4_firetruck_route_edges")
    return {
        "route_id": route.get("id") if route is not None else row.get("route_id", ""),
        "vehicle_id": vehicle.get("id") if vehicle is not None else EV_ID,
        "depart_sec": safe_float(vehicle.get("depart") if vehicle is not None else 600.0, 600.0),
        "target_edge_id": row.get("target_edge_id", edges[-1]),
        "selected_policy": row.get("selected_policy", ""),
        "route_edges": edges,
        "route_edge_count": len(edges),
        "route_length_m": safe_float(row.get("route_length_m")),
        "start_edge_id": row.get("start_edge_id", edges[0]),
        "merge_edge_id": row.get("merge_edge_id", edges[1] if len(edges) > 1 else ""),
    }


def tl_logic_details(net_file: Path) -> dict[str, list[dict[str, Any]]]:
    phases: dict[str, list[dict[str, Any]]] = {}
    root = ET.parse(require_input(net_file, "b04_net")).getroot()
    for tl in root.findall("tlLogic"):
        tls_id = tl.get("id", "")
        phases[tls_id] = [
            {
                "phase_index": index,
                "duration": safe_float(phase.get("duration")),
                "state": phase.get("state", ""),
                "name": phase.get("name", ""),
            }
            for index, phase in enumerate(tl.findall("phase"))
        ]
    return phases


def selected_red_phase(phases: list[dict[str, Any]], link_indices: list[int]) -> int | str:
    if not phases or not link_indices:
        return ""
    for phase in phases:
        state = str(phase.get("state", ""))
        if all(index < len(state) and state[index] == "r" for index in link_indices):
            return int(phase["phase_index"])
    for phase in phases:
        state = str(phase.get("state", ""))
        if all(index < len(state) and state[index] not in {"G", "g", "y"} for index in link_indices):
            return int(phase["phase_index"])
    return ""


def created_tls_phase_counts() -> dict[str, int]:
    if not B04_CSV_SIGNAL_CANDIDATES.is_file():
        return {}
    return {
        row.get("tls_id", ""): safe_int(row.get("phase_count"), 0)
        for row in read_csv(B04_CSV_SIGNAL_CANDIDATES)
        if row.get("action") == "created_tls" and row.get("tls_id")
    }


def selected_partial_green_phase(phases: list[dict[str, Any]], link_indices: list[int]) -> int | str:
    if not phases or not link_indices:
        return ""
    best_phase: int | str = ""
    best_score = 0
    for phase in phases:
        state = str(phase.get("state", ""))
        score = sum(1 for index in link_indices if index < len(state) and state[index] in {"G", "g"})
        if score > best_score:
            best_phase = int(phase["phase_index"])
            best_score = score
    return best_phase if best_score > 0 else ""


def controlled_connection_index(net_file: Path) -> dict[str, list[dict[str, Any]]]:
    by_from_edge: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for _event, elem in ET.iterparse(require_input(net_file, "b04_net"), events=("end",)):
        if elem.tag != "connection":
            elem.clear()
            continue
        tls_id = elem.get("tl", "")
        link_index = elem.get("linkIndex", "")
        from_edge = elem.get("from", "")
        if tls_id and link_index not in {"", None} and from_edge:
            by_from_edge[from_edge].append({
                "from": from_edge,
                "to": elem.get("to", ""),
                "fromLane": elem.get("fromLane", ""),
                "toLane": elem.get("toLane", ""),
                "tl": tls_id,
                "linkIndex": safe_int(link_index),
                "dir": elem.get("dir", ""),
                "state": elem.get("state", ""),
                "via": elem.get("via", ""),
            })
        elem.clear()
    for rows in by_from_edge.values():
        rows.sort(key=lambda row: (str(row["tl"]), safe_int(row["linkIndex"])))
    return by_from_edge


def movement_signal_context(
    phases: list[dict[str, Any]],
    connections_by_from_edge: dict[str, list[dict[str, Any]]],
    movement: dict[str, Any],
) -> dict[str, Any]:
    tls_id = str(movement["tls_id"])
    from_edge = str(movement["from_edge"])
    to_edge = str(movement["to_edge"])
    ev_route_link_indices = sorted(safe_int(index) for index in movement["link_indices"])
    from_edge_connections = [
        row
        for row in connections_by_from_edge.get(from_edge, [])
        if row["tl"] == tls_id
    ]
    route_connections = [
        row
        for row in from_edge_connections
        if row["to"] == to_edge and row["linkIndex"] in ev_route_link_indices
    ]
    ev_source_lanes = sorted({str(row["fromLane"]) for row in route_connections if str(row["fromLane"]) != ""})
    parallel_through_link_indices = sorted({
        safe_int(row["linkIndex"])
        for row in from_edge_connections
        if str(row.get("dir", "")).lower() == "s"
    })
    same_lane_blocking_link_indices = sorted({
        safe_int(row["linkIndex"])
        for row in from_edge_connections
        if str(row.get("fromLane", "")) in ev_source_lanes
        and safe_int(row["linkIndex"]) not in ev_route_link_indices
    })
    same_lane_blocking_to_edges = sorted({
        str(row["to"])
        for row in from_edge_connections
        if str(row.get("fromLane", "")) in ev_source_lanes
        and safe_int(row["linkIndex"]) in same_lane_blocking_link_indices
    })
    full_through_phase = b04_selected_green_phase(phases, parallel_through_link_indices)
    ev_route_phase = b04_selected_green_phase(phases, ev_route_link_indices)
    selected_green_phase_value = full_through_phase if full_through_phase != "" else ev_route_phase
    control_link_indices = parallel_through_link_indices if full_through_phase != "" else ev_route_link_indices
    selected_flush_phase = b04_selected_green_phase(phases, same_lane_blocking_link_indices)
    if selected_flush_phase == "":
        selected_flush_phase = selected_partial_green_phase(phases, same_lane_blocking_link_indices)
    same_lane_blocker_flush_available = (
        bool(same_lane_blocking_link_indices)
        and selected_flush_phase != ""
        and selected_flush_phase != selected_green_phase_value
    )
    control_strategy = "parallel_through_green"
    if full_through_phase == "" and same_lane_blocker_flush_available:
        control_strategy = "route_green_with_same_lane_blocker_flush"
    elif full_through_phase == "":
        control_strategy = "route_green_only_no_full_through_phase"
    return {
        "ev_route_link_indices": ev_route_link_indices,
        "ev_route_from_lanes": ev_source_lanes,
        "parallel_through_link_indices": parallel_through_link_indices,
        "same_lane_blocking_link_indices": same_lane_blocking_link_indices,
        "same_lane_blocking_to_edges": same_lane_blocking_to_edges,
        "control_link_indices": control_link_indices,
        "flush_link_indices": same_lane_blocking_link_indices,
        "full_through_phase": full_through_phase,
        "ev_route_phase": ev_route_phase,
        "selected_green_phase": selected_green_phase_value,
        "selected_flush_phase": selected_flush_phase,
        "full_through_phase_available": full_through_phase != "",
        "same_lane_blocker_flush_available": same_lane_blocker_flush_available,
        "control_strategy": control_strategy,
    }


def b04_selected_green_phase(phases: list[dict[str, Any]], link_indices: list[int]) -> int | str:
    if not phases or not link_indices:
        return ""
    for phase in phases:
        state = str(phase.get("state", ""))
        if all(index < len(state) and state[index] in {"G", "g"} for index in link_indices):
            return int(phase["phase_index"])
    return ""


def lane_ids_for_edges(b04: Any, sumo_net: Any, edge_ids: list[str]) -> list[str]:
    lanes: list[str] = []
    for edge_id in edge_ids:
        lanes.extend(b04.edge_lanes(sumo_net, edge_id))
    return lanes


def storage_window_for_route(
    b04: Any,
    route_edges: list[str],
    sumo_net: Any,
    approach_pair_index: int,
    max_length_m: float,
    upstream_pair_index: int | None = None,
) -> dict[str, Any]:
    min_pair_index = 0 if upstream_pair_index is None else min(upstream_pair_index + 1, approach_pair_index)
    downstream_to_upstream: list[dict[str, Any]] = []
    total_length = 0.0
    for edge_id in reversed(route_edges[min_pair_index:approach_pair_index + 1]):
        edge_length_m, lane_count = b04.edge_length_and_lane_count(sumo_net, edge_id)
        if edge_length_m <= 0:
            continue
        used_length_m = min(edge_length_m, max(max_length_m - total_length, 0.0))
        if used_length_m <= 0:
            break
        downstream_to_upstream.append({
            "edge_id": edge_id,
            "length_m": round(used_length_m, 3),
            "raw_edge_length_m": round(edge_length_m, 3),
            "lane_count": lane_count,
            "lanes": b04.edge_lanes(sumo_net, edge_id),
        })
        total_length += used_length_m
        if total_length >= max_length_m:
            break
    segments = list(reversed(downstream_to_upstream))
    return {
        "storage_edges": [segment["edge_id"] for segment in segments],
        "storage_lanes": [lane for segment in segments for lane in segment["lanes"]],
        "segments": segments,
        "storage_length_m": round(min(total_length, max_length_m), 3),
        "storage_raw_length_m": round(total_length, 3),
        "storage_length_cap_m": max_length_m,
    }


def queue_estimates_for_storage(
    b04: Any,
    edge_data: dict[str, list[dict[str, float]]],
    lane_data: dict[str, list[dict[str, float]]],
    segments: list[dict[str, Any]],
) -> list[float]:
    estimates: list[float] = []
    for segment in segments:
        edge_id = str(segment.get("edge_id", ""))
        length_m = safe_float(segment.get("length_m"))
        lane_count = max(safe_int(segment.get("lane_count"), 1), 1)
        for row in edge_data.get(edge_id, []):
            estimates.append(b04.estimate_queue_m_from_sample(row, length_m, lane_count))
        for row in lane_data.get(edge_id, []):
            estimates.append(b04.estimate_queue_m_from_sample(row, length_m, 1))
    return estimates


def storage_evidence_for_edges(
    b04: Any,
    edge_data: dict[str, list[dict[str, float]]],
    lane_data: dict[str, list[dict[str, float]]],
    edge_ids: list[str],
) -> dict[str, Any]:
    evidence = b04.storage_queue_evidence_for_edges(edge_data, lane_data, edge_ids)
    return {
        "density": safe_float(evidence.get("storage_density_max")),
        "occupancy": safe_float(evidence.get("storage_occupancy_max")),
        "waiting": safe_float(evidence.get("storage_waiting_max_s")),
        "timeLoss": safe_float(evidence.get("storage_timeLoss_max_s")),
        "low_speed_sample_count": safe_int(evidence.get("storage_low_speed_sample_count")),
        "sample_count": safe_int(evidence.get("storage_evidence_sample_count")),
    }


def max_queue_proxy(
    b04: Any,
    edge_data: dict[str, list[dict[str, float]]],
    lane_data: dict[str, list[dict[str, float]]],
    storage: dict[str, Any],
) -> float:
    estimates = queue_estimates_for_storage(b04, edge_data, lane_data, storage["segments"])
    return round(max(estimates) if estimates else 0.0, 3)


def lane_flow_samples_by_lane(path: Path) -> dict[str, list[float]]:
    flows: dict[str, list[float]] = defaultdict(list)
    for _event, elem in ET.iterparse(require_input(path, "laneData"), events=("end",)):
        if elem.tag == "lane":
            lane_id = elem.get("id", "")
            flow = safe_float(elem.get("flow"))
            if lane_id and flow > 0:
                flows[lane_id].append(flow)
        elem.clear()
    return flows


def lane_samples_by_lane(path: Path) -> dict[str, list[dict[str, float]]]:
    samples: dict[str, list[dict[str, float]]] = defaultdict(list)
    for _event, elem in ET.iterparse(require_input(path, "laneData"), events=("end",)):
        if elem.tag == "lane":
            lane_id = elem.get("id", "")
            if lane_id:
                samples[lane_id].append({
                    "speed": safe_float(elem.get("speed")),
                    "density": safe_float(elem.get("density")),
                    "occupancy": safe_float(elem.get("occupancy")),
                    "waitingTime": safe_float(elem.get("waitingTime")),
                    "timeLoss": safe_float(elem.get("timeLoss")),
                    "sampledSeconds": safe_float(elem.get("sampledSeconds")),
                    "flow": safe_float(elem.get("flow")),
                })
        elem.clear()
    return samples


def estimate_lane_vehicle_count(sample: dict[str, float], lane_length_m: float) -> float:
    density = safe_float(sample.get("density"))
    occupancy = safe_float(sample.get("occupancy"))
    length_m = max(lane_length_m, 1.0)
    vehicle_count = density * length_m / 1000.0
    if vehicle_count <= 0.0 and occupancy > 0.0:
        vehicle_count = occupancy / 100.0 * length_m / TA_HEADWAY_M
    return max(vehicle_count, 0.0)


def build_stage2_b0_merge_hold_params(
    b04: Any,
    departure_plan: dict[str, Any],
    lane_samples: dict[str, list[dict[str, float]]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sumo_net = b04.read_sumo_net(B04_NET)
    phases = tl_logic_details(B04_NET).get(ENTRY_TLS_ID, [])
    yellow_duration = next((safe_float(phase.get("duration")) for phase in phases if phase.get("name") == "entry_yellow"), 3.0)
    open_duration = next((safe_float(phase.get("duration")) for phase in phases if phase.get("name") == "entry_open"), 45.0)
    direct_switch_buffer = TA_DIRECT_SWITCH_BUFFER_SEC
    d_merge_m = safe_float(departure_plan.get("merge_zone_length_m"), 0.0)
    l_merge_m = STAGE2_MERGE_DESIGN_LENGTH_M
    tE_merge_sec = d_merge_m / TA_EV_SPEED_MPS if d_merge_m > 0.0 else 0.0
    c_merge_proxy_veh = l_merge_m / TA_HEADWAY_M
    tS_merge_sec = max(yellow_duration, direct_switch_buffer)
    q_a_proxy_veh = max(
        0.0,
        (tE_merge_sec - (yellow_duration + 0.0 + open_duration + direct_switch_buffer))
        * TA_SATURATION_FLOW_VPH_PER_LANE
        / 3600.0,
    )
    merge_lanes = [lane for lane in departure_plan.get("merge_zone_lanes", []) if str(lane)]
    background_lanes = [lane for lane in departure_plan.get("background_inflow_lanes", []) if str(lane)]
    lane_lengths: dict[str, float] = {}
    for lane_id in set(merge_lanes + background_lanes):
        edge_id = str(lane_id).rsplit("_", 1)[0]
        edge_length_m, _lane_count = b04.edge_length_and_lane_count(sumo_net, edge_id)
        lane_lengths[lane_id] = min(edge_length_m if edge_length_m > 0.0 else l_merge_m, l_merge_m)

    merge_occ_values: list[float] = []
    waiting_values: list[float] = []
    halting_proxy_values: list[float] = []
    for lane_id in merge_lanes:
        length_m = lane_lengths.get(lane_id, l_merge_m)
        for sample in lane_samples.get(lane_id, []):
            occ = estimate_lane_vehicle_count(sample, length_m)
            merge_occ_values.append(occ)
            waiting_values.append(max(safe_float(sample.get("waitingTime")), safe_float(sample.get("timeLoss"))))
            speed_kmh = safe_float(sample.get("speed")) * 3.6
            halting_proxy_values.append(occ if speed_kmh <= 1.0 and occ > 0.0 else 0.0)
    background_flow_values = [
        safe_float(sample.get("flow"))
        for lane_id in background_lanes
        for sample in lane_samples.get(lane_id, [])
        if safe_float(sample.get("flow")) > 0.0
    ]
    b0_merge_occ_mean = sum(merge_occ_values) / len(merge_occ_values) if merge_occ_values else 0.0
    b0_merge_occ_max = max(merge_occ_values) if merge_occ_values else 0.0
    b0_merge_support_status = "weak_runtime_required" if q_a_proxy_veh <= 0.0 and b0_merge_occ_max < STAGE2_N_NEED_PROXY_VEH else "usable_b0_proxy"
    runtime_control_note = (
        "Runtime TraCI merge lane snapshot is primary. B0 mean/max is documented only as fallback/provenance, "
        "and weak B0 support requires runtime n_occ/Lq before deciding Q_trig and T_hold."
        if b0_merge_support_status == "weak_runtime_required"
        else "Runtime TraCI merge lane snapshot remains primary; B0 mean/max is fallback/provenance."
    )
    row = {
        "stage2_param_id": "B4_STAGE2_MERGE_HOLD_B0_PROXY",
        "merge_control_tls": departure_plan.get("merge_control_tls", ""),
        "D_merge_m": round(d_merge_m, 6),
        "L_merge_m": l_merge_m,
        "tE_merge_sec": round(tE_merge_sec, 6),
        "C_merge_proxy_veh": round(c_merge_proxy_veh, 6),
        "n_need_proxy_veh": STAGE2_N_NEED_PROXY_VEH,
        "tS_merge_sec": round(tS_merge_sec, 6),
        "entry_yellow_sec": round(yellow_duration, 6),
        "entry_open_green_sec": round(open_duration, 6),
        "direct_switch_buffer_sec": direct_switch_buffer,
        "q_A_proxy_veh": round(q_a_proxy_veh, 6),
        "b0_merge_n_occ_mean_proxy_veh": round(b0_merge_occ_mean, 6),
        "b0_merge_n_occ_max_proxy_veh": round(b0_merge_occ_max, 6),
        "b0_background_inflow_lambda_vph": round(sum(background_flow_values) / len(background_flow_values), 6) if background_flow_values else 0.0,
        "b0_merge_waiting_max_sec": round(max(waiting_values), 6) if waiting_values else 0.0,
        "b0_merge_halting_proxy_max": round(max(halting_proxy_values), 6) if halting_proxy_values else 0.0,
        "b0_merge_support_status": b0_merge_support_status,
        "runtime_dependency": "runtime_n_occ_and_Lq_merge_primary",
        "merge_zone_lanes": " ".join(merge_lanes),
        "background_inflow_lanes": " ".join(background_lanes),
        "measurement_source": STAGE2_MEASUREMENT_SOURCE,
        "field_measurement_claim": "false",
        "runtime_control_note": runtime_control_note,
    }
    payload = {
        "schema": "compact_v9_B4_stage2_b0_merge_hold_params.v1",
        "generated_at": utc_now(),
        "algorithm": "B4",
        "primary_candidate": B4_PRIMARY_CANDIDATE,
        "merge_hold_policy": "T_hold_proxy_direct_inside_dispatch_window",
        "stage2_formula": "T_hold_proxy_sec = tE_merge_sec - t_clear_proxy_sec - tS_merge_sec; t_clear_proxy_sec = n_excess_proxy_veh * 3600 / (1800 * merge_lane_count)",
        "q_A_formula": "q_A_proxy_veh = max(0, (tE_merge_sec - (Y + R + Gm + O)) * s / 3600)",
        "runtime_hold_condition": "if T_hold_proxy_sec > 0, now >= ev_depart_sec - min(T_hold_proxy_sec, dispatch_lead_time_sec); if T_hold_proxy_sec <= 0, hold at dispatch-window open; EV not merged; merge hold TLS available",
        "runtime_control_uses_formula_directly": True,
        "measurement_source": STAGE2_MEASUREMENT_SOURCE,
        "b0_merge_support_status": b0_merge_support_status,
        "runtime_dependency": "runtime_n_occ_and_Lq_merge_primary",
        "field_measurement_claim": False,
        "params": row,
    }
    return payload, [row]


def b0_measured_signal_params(
    b04: Any,
    plan_rows: list[dict[str, Any]],
    edge_data: dict[str, list[dict[str, float]]],
    lane_data: dict[str, list[dict[str, float]]],
    lane_flow_samples: dict[str, list[float]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sumo_net = b04.read_sumo_net(B04_NET)
    for row in plan_rows:
        local_lanes = str(row.get("local_storage_lanes", "")).split()
        storage_length_m = safe_float(row.get("stopline_local_storage_m"), STOPLINE_LOCAL_STORAGE_M)
        lane_count = max(safe_int(row.get("lane_count"), len(local_lanes) or 1), 1)
        local_edges = str(row.get("local_storage_edges", "")).split()
        estimates_m: list[float] = []
        for edge_id in local_edges:
            edge_length_m, edge_lane_count = b04.edge_length_and_lane_count(sumo_net, edge_id)
            used_length_m = min(edge_length_m if edge_length_m > 0 else storage_length_m, storage_length_m)
            for sample in edge_data.get(edge_id, []):
                estimates_m.append(b04.estimate_queue_m_from_sample(sample, used_length_m, max(edge_lane_count, 1)))
            for sample in lane_data.get(edge_id, []):
                estimates_m.append(b04.estimate_queue_m_from_sample(sample, used_length_m, 1))
        q_values = [estimate / TA_HEADWAY_M for estimate in estimates_m if estimate >= 0]
        q_avg = sum(q_values) / len(q_values) if q_values else 0.0
        q_max = max(q_values) if q_values else 0.0
        flow_samples = [flow for lane in local_lanes for flow in lane_flow_samples.get(lane, [])]
        lambda_vph = sum(flow_samples) / len(flow_samples) if flow_samples else 0.0
        t_q_hist = q_max * 3600.0 / max(TA_SATURATION_FLOW_VPH_PER_LANE * lane_count, 1.0)
        rows.append({
            "movement_id": row["movement_id"],
            "tls_id": row["tls_id"],
            "from_edge": row["from_edge"],
            "to_edge": row["to_edge"],
            "route_order_index": row["route_order_index"],
            "q_avg_b0_proxy_veh": round(q_avg, 6),
            "q_max_b0_proxy_veh": round(q_max, 6),
            "tQ_hist_b0_sec": round(t_q_hist, 6),
            "lambda_b0_vph": round(lambda_vph, 6),
            "L_local_m": STOPLINE_LOCAL_STORAGE_M,
            "L_corridor_m": min(safe_float(row.get("corridor_storage_length_m"), CORRIDOR_STORAGE_MAX_M), CORRIDOR_STORAGE_MAX_M),
            "C_local_proxy_veh": round(STOPLINE_LOCAL_STORAGE_M * lane_count / TA_HEADWAY_M, 6),
            "lane_count": lane_count,
            "measurement_source": B4_PRIMARY_EDGE_LANE_SOURCE,
            "field_queue_claim": "false",
            "measurement_note": (
                f"B0 measured means SUMO {B4_PRIMARY_CANDIDATE} no-control edge/lane data proxy, "
                "not field-observed queue length."
            ),
        })
    return rows


def queue_calibration_factor(raw_factor: float) -> float:
    if raw_factor <= 0.0:
        return 1.0
    return max(QUEUE_CALIBRATION_MIN, min(QUEUE_CALIBRATION_MAX, raw_factor))


def add_queue_calibration_factors(
    b0_measured_rows: list[dict[str, Any]],
    readiness_rows: list[dict[str, Any]],
) -> None:
    readiness_by_id = {str(row.get("movement_id", "")): row for row in readiness_rows}
    for row in b0_measured_rows:
        readiness = readiness_by_id.get(str(row.get("movement_id", "")), {})
        readiness_queue_m = safe_float(
            readiness.get("stopline_local_queue_m_proxy"),
            safe_float(readiness.get("local_queue_m_proxy_100m"), safe_float(readiness.get("queue_m_proxy"), 0.0)),
        )
        measured_queue_m = safe_float(row.get("q_avg_b0_proxy_veh")) * TA_HEADWAY_M
        if measured_queue_m <= 0.0:
            measured_queue_m = safe_float(row.get("q_max_b0_proxy_veh")) * TA_HEADWAY_M
        raw_factor = readiness_queue_m / measured_queue_m if readiness_queue_m > 0.0 and measured_queue_m > 0.0 else 1.0
        row["queue_calibration_reference_m"] = round(readiness_queue_m, 6)
        row["queue_calibration_measured_m"] = round(measured_queue_m, 6)
        row["queue_calibration_factor"] = round(raw_factor, 6)
        row["queue_calibration_factor_applied"] = round(queue_calibration_factor(raw_factor), 6)
        row["queue_calibration_source"] = "b4_bottleneck_queue_readiness.csv/b4_b0_measured_signal_params.csv"


def speed_rows_by_segment(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {f"{row.get('segment_id', '')}:{row.get('direction', '')}": row for row in rows}


def best_diagnostic_by_segment(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("mapped_S_segment", ""): row for row in rows if row.get("mapped_S_segment")}


def trigger_reason(local_fill: float, approach_speed: float) -> str:
    reasons = []
    if local_fill >= LOCAL_FILL_TRIGGER:
        reasons.append("local_fill_ge_0p50")
    if 0.0 < approach_speed <= SPEED_TRIGGER_KMH:
        reasons.append("speed_le_15kmh")
    return "+".join(reasons) if reasons else "below_default_trigger"


def build_approach_storage_plan(
    b04: Any,
    route: dict[str, Any],
    edge_data: dict[str, list[dict[str, float]]],
    lane_data: dict[str, list[dict[str, float]]],
    speed_rows: list[dict[str, str]],
    diagnostics_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sumo_net = b04.read_sumo_net(B04_NET)
    phases = tl_logic_details(B04_NET)
    edge_segments = b04.best_segment_by_edge()
    speed_by_segment = speed_rows_by_segment(speed_rows)
    diagnostics_by_segment = best_diagnostic_by_segment(diagnostics_rows)
    route_edges = route["route_edges"]
    movements = b04.route_tls_movements(B04_NET, route_edges)
    connections_by_from_edge = controlled_connection_index(B04_NET)
    csv_phase_count_by_tls = created_tls_phase_counts()
    plan_rows: list[dict[str, Any]] = []
    readiness_rows: list[dict[str, Any]] = []
    for movement in movements:
        previous_diff = next((row for row in reversed(plan_rows) if row["tls_id"] != movement["tls_id"]), None)
        route_pair_index = int(movement["route_pair_index"])
        local_by_length = {
            int(length_m): storage_window_for_route(
                b04,
                route_edges,
                sumo_net,
                route_pair_index,
                length_m,
            )
            for length_m in LOCAL_STORAGE_COMPARISON_M
        }
        local = local_by_length[int(STOPLINE_LOCAL_STORAGE_M)]
        corridor = storage_window_for_route(
            b04,
            route_edges,
            sumo_net,
            route_pair_index,
            CORRIDOR_STORAGE_MAX_M,
            safe_int(previous_diff["route_order_index"]) if previous_diff else None,
        )
        tls_phases = phases.get(str(movement["tls_id"]), [])
        signal_context = movement_signal_context(tls_phases, connections_by_from_edge, movement)
        link_indices = list(signal_context["control_link_indices"])
        ev_route_link_indices = list(signal_context["ev_route_link_indices"])
        green_phase = signal_context["selected_green_phase"]
        red_phase = selected_red_phase(tls_phases, link_indices)
        csv_single_phase_tls = csv_phase_count_by_tls.get(str(movement["tls_id"])) == 1
        if csv_single_phase_tls:
            red_phase = ""
        red_phase_available = red_phase != ""
        green_only_no_red_phase = bool((csv_single_phase_tls or len(tls_phases) == 1) and not red_phase_available)
        from_edge_lanes = b04.edge_lanes(sumo_net, str(movement["from_edge"]))
        approach_lanes = from_edge_lanes or list(movement["approach_lanes"])
        from_lane_count = len(from_edge_lanes)
        to_lane_count = len(b04.edge_lanes(sumo_net, str(movement["to_edge"])))
        segment = edge_segments.get(str(movement["from_edge"]), {})
        mapped = str(segment.get("mapped_S_segment", ""))
        speed_row = speed_by_segment.get(mapped, {})
        diagnostic = diagnostics_by_segment.get(mapped, {})
        local_queue_by_length = {
            length_m: max_queue_proxy(b04, edge_data, lane_data, storage)
            for length_m, storage in local_by_length.items()
        }
        local_queue_m = local_queue_by_length[int(STOPLINE_LOCAL_STORAGE_M)]
        corridor_queue_m = max_queue_proxy(b04, edge_data, lane_data, corridor)
        local_fill_by_length = {
            length_m: round(min(queue_m / max(float(length_m), 1.0), 1.0), 6)
            for length_m, queue_m in local_queue_by_length.items()
        }
        local_fill = local_fill_by_length[int(STOPLINE_LOCAL_STORAGE_M)]
        corridor_fill = round(min(corridor_queue_m / max(safe_float(corridor["storage_length_m"]), 1.0), 1.0), 6)
        local_evidence = storage_evidence_for_edges(b04, edge_data, lane_data, local["storage_edges"])
        corridor_evidence = storage_evidence_for_edges(b04, edge_data, lane_data, corridor["storage_edges"])
        density = max(local_evidence["density"], corridor_evidence["density"], safe_float(speed_row.get("runtime_density_max")))
        occupancy = max(local_evidence["occupancy"], corridor_evidence["occupancy"], safe_float(speed_row.get("runtime_occupancy_max")))
        waiting = max(local_evidence["waiting"], corridor_evidence["waiting"], safe_float(speed_row.get("runtime_waiting_or_timeloss_max")))
        time_loss = max(local_evidence["timeLoss"], corridor_evidence["timeLoss"])
        low_speed_interval_count = max(
            local_evidence["low_speed_sample_count"],
            corridor_evidence["low_speed_sample_count"],
            safe_int(speed_row.get("low_speed_interval_count")),
        )
        approach_speed = safe_float(speed_row.get("simulated_speed_kmh"))
        fast_dense_flow = str(diagnostic.get("queue_state") or speed_row.get("queue_state")) == "fast_dense_flow"
        signal_only_delay = str(diagnostic.get("queue_state") or speed_row.get("queue_state")) == "signal_only_delay"
        control_candidate = local_fill >= LOCAL_FILL_TRIGGER or (0.0 < approach_speed <= SPEED_TRIGGER_KMH)
        traffic_pressure = local_fill >= TRAFFIC_PRESSURE_LOCAL_FILL or fast_dense_flow
        operational_queue = control_candidate
        movement_id = f"B4_MOVEMENT_{len(plan_rows):02d}"
        controllable = bool(ev_route_link_indices and green_phase != "" and local["storage_edges"])
        plan_rows.append({
            "movement_id": movement_id,
            "tls_id": movement["tls_id"],
            "from_edge": movement["from_edge"],
            "to_edge": movement["to_edge"],
            "linkIndex": " ".join(str(index) for index in link_indices),
            "control_linkIndex": " ".join(str(index) for index in link_indices),
            "ev_route_linkIndex": " ".join(str(index) for index in ev_route_link_indices),
            "parallel_through_linkIndex": " ".join(str(index) for index in signal_context["parallel_through_link_indices"]),
            "same_lane_blocking_linkIndex": " ".join(str(index) for index in signal_context["same_lane_blocking_link_indices"]),
            "same_lane_blocking_to_edges": " ".join(str(edge) for edge in signal_context["same_lane_blocking_to_edges"]),
            "flush_linkIndex": " ".join(str(index) for index in signal_context["flush_link_indices"]),
            "selected_flush_phase": signal_context["selected_flush_phase"],
            "full_through_phase": signal_context["full_through_phase"],
            "ev_route_phase": signal_context["ev_route_phase"],
            "full_through_phase_available": signal_context["full_through_phase_available"],
            "same_lane_blocker_flush_available": signal_context["same_lane_blocker_flush_available"],
            "control_strategy": signal_context["control_strategy"],
            "approach_lanes": " ".join(approach_lanes),
            "storage_edges": " ".join(local["storage_edges"]),
            "storage_lanes": " ".join(local["storage_lanes"]),
            "local_storage_edges": " ".join(local["storage_edges"]),
            "local_storage_lanes": " ".join(local["storage_lanes"]),
            "local_storage_80m_edges": " ".join(local_by_length[80]["storage_edges"]),
            "local_storage_120m_edges": " ".join(local_by_length[120]["storage_edges"]),
            "corridor_storage_edges": " ".join(corridor["storage_edges"]),
            "corridor_storage_lanes": " ".join(corridor["storage_lanes"]),
            "stopline_local_storage_m": STOPLINE_LOCAL_STORAGE_M,
            "stopline_local_actual_length_m": local["storage_length_m"],
            "corridor_storage_length_m": corridor["storage_length_m"],
            "corridor_storage_raw_length_m": corridor["storage_raw_length_m"],
            "lane_count": len(approach_lanes),
            "from_edge_lane_count": from_lane_count,
            "to_edge_lane_count": to_lane_count,
            "lane_drop_delta": from_lane_count - to_lane_count,
            "selected_green_phase": green_phase,
            "selected_red_phase": red_phase,
            "red_phase_available": red_phase_available,
            "green_only_no_red_phase": green_only_no_red_phase,
            "mapped_S_segment": mapped,
            "mapped_segment_id": segment.get("segment_id", ""),
            "mapped_direction": segment.get("direction", ""),
            "route_order_index": route_pair_index,
            "controllable": controllable,
            "storage_definition": "stopline_local_100m_primary; corridor_250m_auxiliary",
            "linkIndex_note": "SUMO TLS movement index, not physical storage length.",
        })
        readiness_rows.append({
            "candidate": "",
            "movement_id": movement_id,
            "tls_id": movement["tls_id"],
            "from_edge": movement["from_edge"],
            "to_edge": movement["to_edge"],
            "linkIndex": " ".join(str(index) for index in link_indices),
            "control_linkIndex": " ".join(str(index) for index in link_indices),
            "ev_route_linkIndex": " ".join(str(index) for index in ev_route_link_indices),
            "parallel_through_linkIndex": " ".join(str(index) for index in signal_context["parallel_through_link_indices"]),
            "same_lane_blocking_linkIndex": " ".join(str(index) for index in signal_context["same_lane_blocking_link_indices"]),
            "flush_linkIndex": " ".join(str(index) for index in signal_context["flush_link_indices"]),
            "selected_flush_phase": signal_context["selected_flush_phase"],
            "full_through_phase_available": signal_context["full_through_phase_available"],
            "same_lane_blocker_flush_available": signal_context["same_lane_blocker_flush_available"],
            "control_strategy": signal_context["control_strategy"],
            "mapped_S_segment": mapped,
            "route_order_index": route_pair_index,
            "stopline_local_queue_m_proxy": local_queue_m,
            "local_queue_m_proxy_80m": local_queue_by_length[80],
            "local_queue_m_proxy_100m": local_queue_by_length[100],
            "local_queue_m_proxy_120m": local_queue_by_length[120],
            "corridor_queue_m_proxy": corridor_queue_m,
            "local_fill_80m": local_fill_by_length[80],
            "local_fill_100m": local_fill_by_length[100],
            "local_fill_120m": local_fill_by_length[120],
            "stopline_local_fill_100m": local_fill,
            "corridor_fill_250m": corridor_fill,
            "approach_speed_kmh": round(approach_speed, 3),
            "density": round(density, 3),
            "occupancy": round(occupancy, 3),
            "waiting": round(waiting, 3),
            "timeLoss": round(time_loss, 3),
            "low_speed_interval_count": low_speed_interval_count,
            "fast_dense_flow": fast_dense_flow,
            "signal_only_delay": signal_only_delay,
            "control_candidate": control_candidate,
            "trigger_reason": trigger_reason(local_fill, approach_speed),
            "traffic_pressure": traffic_pressure,
            "operational_queue": operational_queue,
            "downstream_blockage_evidence": False,
            "bottleneck_risk": local_fill >= BOTTLENECK_LOCAL_FILL or corridor_fill >= BOTTLENECK_CORRIDOR_FILL,
            "control_mode": "bottleneck_first" if local_fill >= BOTTLENECK_LOCAL_FILL or corridor_fill >= BOTTLENECK_CORRIDOR_FILL else "normal_preemptive",
            "queue_evidence_source": "SUMO B04 B0 edgeData/laneData proxy only; no field-observed queue length.",
        })
    for index, row in enumerate(readiness_rows[:-1]):
        downstream = readiness_rows[index + 1]
        downstream_blocked = (
            safe_float(downstream["stopline_local_fill_100m"]) >= BOTTLENECK_LOCAL_FILL
            or safe_float(downstream["corridor_fill_250m"]) >= BOTTLENECK_CORRIDOR_FILL
        )
        row["downstream_blockage_evidence"] = downstream_blocked
        if downstream_blocked:
            row["bottleneck_risk"] = True
            row["control_mode"] = "bottleneck_first"
    return plan_rows, readiness_rows


def parse_connections(net_file: Path, pairs: set[tuple[str, str]] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _event, elem in ET.iterparse(require_input(net_file, "b04_net"), events=("end",)):
        if elem.tag != "connection":
            elem.clear()
            continue
        from_edge = elem.get("from", "")
        to_edge = elem.get("to", "")
        if pairs is None or (from_edge, to_edge) in pairs:
            rows.append({
                "from": from_edge,
                "to": to_edge,
                "fromLane": elem.get("fromLane", ""),
                "toLane": elem.get("toLane", ""),
                "tl": elem.get("tl", ""),
                "linkIndex": elem.get("linkIndex", ""),
                "dir": elem.get("dir", ""),
                "state": elem.get("state", ""),
                "via": elem.get("via", ""),
            })
        elem.clear()
    return rows


def edge_length(b04: Any, sumo_net: Any, edge_id: str) -> float:
    length_m, _lane_count = b04.edge_length_and_lane_count(sumo_net, edge_id)
    return round(length_m, 3)


def phase_name_index(phases: list[dict[str, Any]], name: str) -> int | str:
    for phase in phases:
        if phase.get("name") == name:
            return int(phase["phase_index"])
    return ""


def build_departure_flow_plan(b04: Any, route: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    sumo_net = b04.read_sumo_net(B04_NET)
    entry = read_json(ENTRY_TLS_SUMMARY)
    phases = tl_logic_details(B04_NET).get(ENTRY_TLS_ID, [])
    start_edge = route["start_edge_id"]
    merge_edge = route["merge_edge_id"]
    start_lanes = b04.edge_lanes(sumo_net, start_edge)
    merge_lanes = b04.edge_lanes(sumo_net, merge_edge)
    route_edges = route["route_edges"]
    merge_zone_edges = route_edges[:2]
    merge_zone_lanes = lane_ids_for_edges(b04, sumo_net, merge_zone_edges)
    merge_zone_length = round(sum(edge_length(b04, sumo_net, edge_id) for edge_id in merge_zone_edges), 3)
    background_connections = entry.get("controlled_connections", []) if entry.get("created") else []
    ev_connections = entry.get("ev_uncontrolled_connections", []) if entry.get("created") else []
    background_link_indices = sorted({safe_int(connection.get("linkIndex")) for connection in background_connections})
    ev_link_indices = sorted({safe_int(connection.get("linkIndex")) for connection in ev_connections})
    background_edges = sorted({str(connection.get("from", "")) for connection in background_connections if connection.get("from")})
    background_lanes = [
        f"{connection.get('from')}_{connection.get('fromLane')}"
        for connection in background_connections
        if connection.get("from") and connection.get("fromLane") not in {"", None}
    ]
    entry_hold_phase = phase_name_index(phases, "entry_hold")
    entry_open_phase = phase_name_index(phases, "entry_open")
    merge_hold_available = bool(entry.get("created") and background_link_indices and entry_hold_phase != "")
    ev_release_status = "uncontrolled_by_merge_tls" if ev_connections else "missing_ev_merge_connection"
    validation_status = "WARN" if merge_hold_available and ev_release_status == "uncontrolled_by_merge_tls" else "FAIL"
    if merge_hold_available and ev_release_status != "uncontrolled_by_merge_tls":
        validation_status = "FAIL"
    plan = {
        "schema": "compact_v9_B4_departure_flow_plan.v1",
        "generated_at": utc_now(),
        "fire_station_start_edge": start_edge,
        "fire_station_start_lane": " ".join(start_lanes),
        "departure_edge": start_edge,
        "departure_lane": " ".join(start_lanes),
        "merge_approach_edge": start_edge,
        "merge_approach_lane": " ".join(start_lanes),
        "mainline_target_edge": merge_edge,
        "mainline_target_lane": " ".join(merge_lanes),
        "ev_turn_movement": {
            "from_edge": start_edge,
            "to_edge": merge_edge,
            "linkIndex": " ".join(str(index) for index in ev_link_indices),
            "control_status": ev_release_status,
        },
        "background_inflow_edges": background_edges,
        "background_inflow_lanes": background_lanes,
        "merge_control_tls": ENTRY_TLS_ID if entry.get("created") else "",
        "merge_control_linkIndex": " ".join(str(index) for index in background_link_indices),
        "ev_connection_linkIndex": " ".join(str(index) for index in ev_link_indices),
        "ev_release_green_phase": "uncontrolled_by_merge_tls" if ev_release_status == "uncontrolled_by_merge_tls" else "",
        "ev_release_control_status": ev_release_status,
        "background_inflow_red_hold_phase": entry_hold_phase,
        "background_inflow_open_phase": entry_open_phase,
        "merge_zone_edges": merge_zone_edges,
        "merge_zone_lanes": merge_zone_lanes,
        "merge_zone_length_m": merge_zone_length,
        "dispatch_lead_time_sec": DISPATCH_LEAD_TIME_SEC,
        "dispatch_lead_time_range_sec": DISPATCH_LEAD_TIME_RANGE_SEC,
        "validation_status": validation_status,
        "validation": {
            "merge_control_tls_available": "PASS" if entry.get("created") else "FAIL",
            "background_inflow_hold_available": "PASS" if merge_hold_available else "FAIL",
            "ev_release_control": "WARN" if ev_release_status == "uncontrolled_by_merge_tls" else "FAIL",
        },
        "stage2_policy": "Before EV reaches the merge zone, hold only the precomputed background inflow movements and release after EV passes the merge point.",
    }
    merge_zone = {
        "schema": "compact_v9_B4_merge_zone.v1",
        "generated_at": utc_now(),
        "merge_zone_id": "B4_MERGE_ZONE_00",
        "start_edge": start_edge,
        "merge_edge": merge_edge,
        "merge_zone_edges": merge_zone_edges,
        "merge_zone_lanes": merge_zone_lanes,
        "merge_zone_length_m": merge_zone_length,
        "merge_control_tls_id": plan["merge_control_tls"],
        "merge_control_background_link_indices": background_link_indices,
        "ev_uncontrolled_link_indices": ev_link_indices,
        "merge_control_incoming_edges": background_edges,
        "merge_control_incoming_lanes": background_lanes,
        "control_available": merge_hold_available,
        "control_strategy": "entry_merge_background_hold",
        "validation_status": validation_status,
        "measurement_policy": "Stage 2 must inspect only these precomputed lanes/movements, not scan all vehicles.",
    }
    return plan, merge_zone


def build_intersection_rows(plan_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in plan_rows:
        grouped[str(row["tls_id"])].append(row)
    rows: list[dict[str, Any]] = []
    phases = tl_logic_details(B04_NET)
    for tls_id, items in sorted(grouped.items(), key=lambda item: min(safe_int(row["route_order_index"]) for row in item[1])):
        rows.append({
            "tls_id": tls_id,
            "route_order_min": min(safe_int(row["route_order_index"]) for row in items),
            "route_order_max": max(safe_int(row["route_order_index"]) for row in items),
            "movement_ids": " ".join(str(row["movement_id"]) for row in items),
            "mapped_S_segments": " ".join(sorted({str(row["mapped_S_segment"]) for row in items if row.get("mapped_S_segment")})),
            "movement_count": len(items),
            "controllable_count": sum(1 for row in items if truthy(row.get("controllable"))),
            "phase_count": len(phases.get(tls_id, [])),
            "selected_green_phases": " ".join(str(row["selected_green_phase"]) for row in items),
            "selected_red_phases": " ".join(str(row["selected_red_phase"]) for row in items),
            "red_phase_available_count": sum(1 for row in items if truthy(row.get("red_phase_available"))),
            "green_only_no_red_phase_count": sum(1 for row in items if truthy(row.get("green_only_no_red_phase"))),
            "linkIndex": " ".join(str(row["linkIndex"]) for row in items),
            "control_linkIndex": " ".join(str(row["control_linkIndex"]) for row in items),
            "ev_route_linkIndex": " ".join(str(row["ev_route_linkIndex"]) for row in items),
            "same_lane_blocking_linkIndex": " ".join(str(row["same_lane_blocking_linkIndex"]) for row in items),
            "control_strategies": " ".join(str(row["control_strategy"]) for row in items),
            "from_edges": " ".join(str(row["from_edge"]) for row in items),
            "to_edges": " ".join(str(row["to_edge"]) for row in items),
        })
    return rows


def build_threshold_proposal(primary_candidate: str, readiness_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "compact_v9_B4_control_queue_threshold_proposal.v1",
        "generated_at": utc_now(),
        "candidate": primary_candidate,
        "decision_variables": B4_DECISION_VARIABLES,
        "decision_variable_screening": decision_variable_screening_payload(),
        "primary_control_fill_metric": "stopline_local_fill_100m",
        "local_fill_comparison_metrics": [
            "local_fill_80m",
            "local_fill_100m",
            "local_fill_120m",
        ],
        "control_candidate_expression": "stopline_local_fill_100m >= 0.50 OR approach_speed_kmh <= 15",
        "runtime_preemption_expression": "(stopline_local_fill_100m >= 0.50 OR approach_speed_kmh <= 15) AND TA_proxy_sec <= 0",
        "thresholds": {
            "local_fill_trigger": LOCAL_FILL_TRIGGER,
            "speed_trigger_kmh": SPEED_TRIGGER_KMH,
            "traffic_pressure_local_fill_100m": TRAFFIC_PRESSURE_LOCAL_FILL,
            "bottleneck_local_fill_100m": BOTTLENECK_LOCAL_FILL,
            "bottleneck_corridor_fill_250m": BOTTLENECK_CORRIDOR_FILL,
        },
        "queue_levels": {
            "traffic_pressure": "local_fill_100m >= 0.20 OR fast_dense_flow evidence",
            "operational_queue": "local_fill_100m >= 0.50 OR approach_speed_kmh <= 15km/h",
            "bottleneck_risk": "local_fill_100m >= 0.70 OR corridor_fill_250m >= 0.50 OR downstream_blockage evidence",
        },
        "measurement_policy": {
            "stopline_local_fill_100m": "queue_m_proxy / 100m; primary B4 control trigger denominator",
            "local_fill_80m": "comparison-only local fill using an 80m denominator",
            "local_fill_100m": "comparison alias for stopline_local_fill_100m and the default trigger metric",
            "local_fill_120m": "comparison-only local fill using a 120m denominator",
            "corridor_fill_250m": "queue_m_proxy / corridor_storage_length_m capped at 250m; auxiliary spillback/bottleneck evidence",
            "queue_length_note": "No field queue length exists in the reference CSV; all queue length values are SUMO edgeData/laneData proxies.",
            "linkIndex_note": "linkIndex is a SUMO TLS movement index, not a physical length or storage value.",
        },
        "b0_source_policy": b0_source_policy_rows(),
        "evidence_recorded_with_summary": [
            "density",
            "occupancy",
            "waiting",
            "timeLoss",
            "low_speed_interval",
            "fast_dense_flow",
            "signal_only_delay",
        ],
        "readiness_counts": {
            "movement_count": len(readiness_rows),
            "control_candidate_count": sum(1 for row in readiness_rows if truthy(row.get("control_candidate"))),
            "traffic_pressure_count": sum(1 for row in readiness_rows if truthy(row.get("traffic_pressure"))),
            "operational_queue_count": sum(1 for row in readiness_rows if truthy(row.get("operational_queue"))),
            "bottleneck_risk_count": sum(1 for row in readiness_rows if truthy(row.get("bottleneck_risk"))),
        },
        "event_schema": EVENT_SCHEMA,
    }


def build_ta_proxy_policy() -> dict[str, Any]:
    return {
        "schema": "compact_v9_B4_ta_proxy_policy.v1",
        "generated_at": utc_now(),
        "algorithm": "B4",
        "ta_formula": "TA_proxy_sec = tE_sec - tS_sec - tQ_sec",
        "ta_control_policy": "(local_fill_100m >= 0.50 OR approach_speed_kmh <= 15) AND TA_proxy_sec <= 0",
        "tE_sec": "EV_to_stopline_distance_m / 13.9mps",
        "tS_sec": "0 if selected target phase is already active, else 5s direct switch safety buffer using existing SUMO phases only",
        "tQ_sec": "(queue_m_proxy / 6.5m) * 3600 / (1800 veh/h/lane * lane_count)",
        "b0_measured_params_source": f"SUMO {B4_PRIMARY_CANDIDATE} no-control edgeData/laneData/tripinfo outputs",
        "field_queue_claim": False,
        "notes": [
            "B0 measured values are simulation-internal proxies, not field-observed queue lengths.",
            "TA is used with the existing B4 local fill / low speed safety filter, not as a standalone trigger.",
            "B4 only switches to phases that already exist in the SUMO net.",
            "When runtime queue is stale or empty, B0 tQ_hist may be used as a fallback and is logged with queue_source=b0_fallback.",
        ],
        "b0_source_policy": b0_source_policy_rows(),
        "constants": {
            "v_E_mps": TA_EV_SPEED_MPS,
            "headway_m_per_vehicle": TA_HEADWAY_M,
            "saturation_flow_vph_per_lane": TA_SATURATION_FLOW_VPH_PER_LANE,
            "direct_switch_buffer_sec": TA_DIRECT_SWITCH_BUFFER_SEC,
        },
    }


def runtime_movement_rows(
    plan_rows: list[dict[str, Any]],
    readiness_rows: list[dict[str, Any]],
    b0_measured_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    readiness_by_id = {row["movement_id"]: row for row in readiness_rows}
    b0_by_id = {row["movement_id"]: row for row in (b0_measured_rows or [])}
    rows: list[dict[str, Any]] = []
    for row in plan_rows:
        readiness = readiness_by_id.get(row["movement_id"], {})
        b0 = b0_by_id.get(row["movement_id"], {})
        rows.append({
            "movement_id": row["movement_id"],
            "tls_id": row["tls_id"],
            "from_edge": row["from_edge"],
            "to_edge": row["to_edge"],
            "link_indices": [safe_int(value) for value in str(row["control_linkIndex"]).split() if value != ""],
            "control_link_indices": [safe_int(value) for value in str(row["control_linkIndex"]).split() if value != ""],
            "ev_route_link_indices": [safe_int(value) for value in str(row["ev_route_linkIndex"]).split() if value != ""],
            "parallel_through_link_indices": [safe_int(value) for value in str(row["parallel_through_linkIndex"]).split() if value != ""],
            "same_lane_blocking_link_indices": [safe_int(value) for value in str(row["same_lane_blocking_linkIndex"]).split() if value != ""],
            "flush_link_indices": [safe_int(value) for value in str(row["flush_linkIndex"]).split() if value != ""],
            "approach_lanes": str(row["approach_lanes"]).split(),
            "local_storage_lanes": str(row["local_storage_lanes"]).split(),
            "corridor_storage_lanes": str(row["corridor_storage_lanes"]).split(),
            "selected_green_phase": row["selected_green_phase"],
            "selected_red_phase": row["selected_red_phase"],
            "red_phase_available": truthy(row["red_phase_available"]),
            "green_only_no_red_phase": truthy(row["green_only_no_red_phase"]),
            "selected_flush_phase": row["selected_flush_phase"],
            "full_through_phase": row["full_through_phase"],
            "ev_route_phase": row["ev_route_phase"],
            "full_through_phase_available": truthy(row["full_through_phase_available"]),
            "same_lane_blocker_flush_available": truthy(row["same_lane_blocker_flush_available"]),
            "control_strategy": row["control_strategy"],
            "route_order_index": row["route_order_index"],
            "mapped_S_segment": row["mapped_S_segment"],
            "controllable": truthy(row["controllable"]),
            "default_control_candidate": truthy(readiness.get("control_candidate")),
            "default_trigger_reason": readiness.get("trigger_reason", ""),
            "q_avg_b0_proxy_veh": safe_float(b0.get("q_avg_b0_proxy_veh")),
            "q_max_b0_proxy_veh": safe_float(b0.get("q_max_b0_proxy_veh")),
            "tQ_hist_b0_sec": safe_float(b0.get("tQ_hist_b0_sec")),
            "lambda_b0_vph": safe_float(b0.get("lambda_b0_vph")),
            "C_local_proxy_veh": safe_float(b0.get("C_local_proxy_veh")),
            "b0_measurement_source": b0.get("measurement_source", ""),
        })
    return rows


def segment_id(value: Any) -> str:
    return str(value or "").split(":", 1)[0]


def unique_preserve(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def route_edge_index(route_edges: list[str]) -> dict[str, int]:
    return {edge_id: index for index, edge_id in enumerate(route_edges)}


def case_b_segment_mapping_rows(route_edges: list[str]) -> dict[str, list[dict[str, Any]]]:
    edge_index = route_edge_index(route_edges)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_csv(B04_SEGMENT_EDGE_MAPPING):
        if row.get("segment_id") not in CASE_B_SEGMENT_IDS or row.get("direction") != "upbound":
            continue
        edge_id = row.get("edge_id", "")
        if edge_id not in edge_index:
            continue
        item = dict(row)
        item["route_index"] = edge_index[edge_id]
        grouped[str(row["segment_id"])].append(item)
    for rows in grouped.values():
        rows.sort(key=lambda item: safe_int(item.get("route_index")))
    return grouped


def segment_storage_segments(b04: Any, sumo_net: Any, mapping_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for row in mapping_rows:
        edge_id = str(row.get("edge_id", ""))
        edge_length_m, lane_count = b04.edge_length_and_lane_count(sumo_net, edge_id)
        matched_length_m = safe_float(row.get("matched_length_m"), edge_length_m)
        used_length_m = min(edge_length_m if edge_length_m > 0 else matched_length_m, matched_length_m if matched_length_m > 0 else edge_length_m)
        if used_length_m <= 0:
            continue
        segments.append({
            "edge_id": edge_id,
            "length_m": round(used_length_m, 3),
            "raw_edge_length_m": round(edge_length_m, 3),
            "lane_count": max(lane_count, 1),
            "lanes": b04.edge_lanes(sumo_net, edge_id),
        })
    return segments


def segment_b0_prior(
    b04: Any,
    edge_data: dict[str, list[dict[str, float]]],
    lane_data: dict[str, list[dict[str, float]]],
    lane_flow_samples: dict[str, list[float]],
    segments: list[dict[str, Any]],
    segment_length_m: float,
) -> dict[str, Any]:
    estimates_m = queue_estimates_for_storage(b04, edge_data, lane_data, segments)
    q_values = [estimate / TA_HEADWAY_M for estimate in estimates_m if estimate >= 0.0]
    q_avg = sum(q_values) / len(q_values) if q_values else 0.0
    q_max = max(q_values) if q_values else 0.0
    lanes = unique_preserve([lane for segment in segments for lane in segment.get("lanes", [])])
    flow_samples = [flow for lane in lanes for flow in lane_flow_samples.get(lane, []) if flow > 0.0]
    lambda_vph = sum(flow_samples) / len(flow_samples) if flow_samples else 0.0
    speed_samples: list[float] = []
    for segment in segments:
        edge_id = str(segment.get("edge_id", ""))
        for sample in lane_data.get(edge_id, []):
            speed_mps = safe_float(sample.get("speed"))
            if speed_mps > 0.0:
                speed_samples.append(speed_mps * 3.6)
    speed_kmh = sum(speed_samples) / len(speed_samples) if speed_samples else 0.0
    lane_count = max((safe_int(segment.get("lane_count"), 1) for segment in segments), default=1)
    t_q_hist = q_max * 3600.0 / max(TA_SATURATION_FLOW_VPH_PER_LANE * lane_count, 1.0)
    max_queue_m = max(estimates_m) if estimates_m else 0.0
    return {
        "segment_q_avg_B0": round(q_avg, 6),
        "segment_q_max_B0": round(q_max, 6),
        "segment_tQ_hist_B0": round(t_q_hist, 6),
        "segment_lambda_B0": round(lambda_vph, 6),
        "segment_fill_B0": round(max_queue_m / max(segment_length_m, 1.0), 6),
        "segment_speed_B0": round(speed_kmh, 6),
    }


def route_span_proxy_pair(
    segment_rows: list[dict[str, Any]],
    ordered_controllable: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], int, int]:
    if not segment_rows:
        return {}, {}, 0, 0
    route_start = min(safe_int(row.get("route_index")) for row in segment_rows)
    route_end = max(safe_int(row.get("route_index")) for row in segment_rows)
    in_span = [
        row for row in ordered_controllable
        if route_start <= safe_int(row.get("route_order_index")) <= route_end
    ]
    bottleneck = next((row for row in reversed(in_span) if safe_int(row.get("lane_drop_delta")) > 0), {})
    if not bottleneck and in_span:
        bottleneck = in_span[-1]
    if not bottleneck:
        bottleneck = next(
            (row for row in ordered_controllable if safe_int(row.get("route_order_index")) > route_end),
            {},
        )
    upstream = {}
    if bottleneck:
        bottleneck_order = safe_int(bottleneck.get("route_order_index"))
        upstream = next(
            (row for row in reversed(ordered_controllable) if safe_int(row.get("route_order_index")) < bottleneck_order),
            {},
        )
    return bottleneck, upstream, route_start, route_end


def b0_source_policy_rows() -> list[dict[str, str]]:
    return [
        {
            "field_group": "stage1_signal_params",
            "primary_source": "b4_b0_measured_signal_params.csv",
            "fallback_source": "none",
            "policy": "SUMO B04 no-control B0 measured proxy fills q_avg/q_max/tQ_hist/lambda.",
        },
        {
            "field_group": "stage2_merge",
            "primary_source": "runtime TraCI lane snapshot",
            "fallback_source": "b4_stage2_b0_merge_hold_params.json",
            "policy": "Runtime n_occ wins; B0 mean/max is fallback only when merge lanes are unavailable.",
        },
        {
            "field_group": "stage3_tQ",
            "primary_source": "runtime TraCI queue proxy",
            "fallback_source": "b4_b0_measured_signal_params.csv",
            "policy": "Runtime queue wins; stale or empty runtime queue may use B0 tQ_hist.",
        },
        {
            "field_group": "case_b",
            "primary_source": "runtime queue/L tau check",
            "fallback_source": "b4_case_b_candidates.csv B0 prior",
            "policy": "Runtime tau wins; mapped B0 prior can mark Case B when runtime queue is not decisive.",
        },
    ]


def build_case_b_candidates(
    b04: Any,
    route: dict[str, Any],
    plan_rows: list[dict[str, Any]],
    readiness_rows: list[dict[str, Any]],
    b0_measured_rows: list[dict[str, Any]],
    edge_data: dict[str, list[dict[str, float]]],
    lane_data: dict[str, list[dict[str, float]]],
    lane_flow_samples: dict[str, list[float]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sumo_net = b04.read_sumo_net(B04_NET)
    route_edges = list(route.get("route_edges", []))
    segment_mapping = case_b_segment_mapping_rows(route_edges)
    segment_rows = {
        row["segment_id"]: row
        for row in read_csv(MAINSTREAM_SEGMENT_SKELETON)
        if row.get("segment_id") in CASE_B_SEGMENT_IDS
    }
    plan_by_segment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in plan_rows:
        plan_by_segment[segment_id(row.get("mapped_S_segment"))].append(row)
    readiness_by_id = {row["movement_id"]: row for row in readiness_rows}
    b0_by_id = {row["movement_id"]: row for row in b0_measured_rows}
    ordered_controllable = [
        row for row in sorted(plan_rows, key=lambda item: safe_int(item.get("route_order_index")))
        if truthy(row.get("controllable"))
    ]
    rows: list[dict[str, Any]] = []
    for segment in CASE_B_SEGMENT_IDS:
        skeleton = segment_rows.get(segment, {})
        segment_map_rows = segment_mapping.get(segment, [])
        route_start = min((safe_int(row.get("route_index")) for row in segment_map_rows), default=-1)
        route_end = max((safe_int(row.get("route_index")) for row in segment_map_rows), default=-1)
        segment_edges = unique_preserve([str(row.get("edge_id", "")) for row in segment_map_rows])
        segment_storage = segment_storage_segments(b04, sumo_net, segment_map_rows)
        segment_lanes = unique_preserve([lane for item in segment_storage for lane in item.get("lanes", [])])
        segment_length_m = safe_float(skeleton.get("segment_length_m"))
        segment_prior = segment_b0_prior(
            b04,
            edge_data,
            lane_data,
            lane_flow_samples,
            segment_storage,
            segment_length_m,
        )
        exact = [
            row for row in plan_by_segment.get(segment, [])
            if truthy(row.get("controllable"))
        ]
        bottleneck = exact[-1] if exact else {}
        upstream = {}
        mapping_status = "unmapped"
        if bottleneck:
            mapping_status = "mapped_exact"
            bottleneck_order = safe_int(bottleneck.get("route_order_index"))
            upstream = next(
                (
                    row for row in reversed(ordered_controllable)
                    if safe_int(row.get("route_order_index")) < bottleneck_order
                    and row.get("tls_id") != bottleneck.get("tls_id")
                ),
                {},
            )
        else:
            bottleneck, upstream, proxy_start, proxy_end = route_span_proxy_pair(segment_map_rows, ordered_controllable)
            route_start = proxy_start if proxy_start or route_start < 0 else route_start
            route_end = proxy_end if proxy_end or route_end < 0 else route_end
            if bottleneck and upstream:
                mapping_status = "mapped_route_span_proxy"
        readiness = readiness_by_id.get(str(bottleneck.get("movement_id", "")), {})
        b0 = b0_by_id.get(str(bottleneck.get("movement_id", "")), {})
        skeleton_lane_drop = max(
            0,
            safe_int(skeleton.get("down_lanes_toward_seongdong_high_school"))
            - safe_int(skeleton.get("up_lanes_toward_seoul_station")),
        )
        segment_lane_drop = max(
            [skeleton_lane_drop]
            + [
                max(0, safe_int(row.get("target_lanes")) - safe_int(row.get("current_lanes")))
                for row in segment_map_rows
            ]
        )
        lane_drop_delta = safe_int(
            bottleneck.get("lane_drop_delta"),
            segment_lane_drop,
        )
        q_avg = safe_float(b0.get("q_avg_b0_proxy_veh"))
        q_max = safe_float(b0.get("q_max_b0_proxy_veh"))
        t_q_hist = safe_float(b0.get("tQ_hist_b0_sec"))
        lambda_vph = safe_float(b0.get("lambda_b0_vph"))
        fill_b0 = max(
            safe_float(readiness.get("stopline_local_fill_100m")),
            safe_float(readiness.get("corridor_fill_250m")),
        )
        speed_b0 = safe_float(readiness.get("approach_speed_kmh"))
        mapped = bool(bottleneck and upstream)
        case_b_runtime_enabled = mapped and bool(segment_lanes)
        segment_fill_b0 = safe_float(segment_prior.get("segment_fill_B0"))
        segment_speed_b0 = safe_float(segment_prior.get("segment_speed_B0"))
        prior_risk = mapped and (
            lane_drop_delta > 0
            or max(fill_b0, segment_fill_b0) >= CASE_B_DEFAULT_TAU
            or (0.0 < speed_b0 <= SPEED_TRIGGER_KMH)
            or (0.0 < segment_speed_b0 <= SPEED_TRIGGER_KMH)
        )
        upstream_order = safe_int(upstream.get("route_order_index"), -1)
        bottleneck_order = safe_int(bottleneck.get("route_order_index"), -1)
        proxy_edge_gap_upstream = 0 if upstream_order < 0 or route_start < 0 else max(route_start - upstream_order, 0)
        proxy_edge_gap_bottleneck = 0 if bottleneck_order < 0 or route_end < 0 else max(bottleneck_order - route_end, 0)
        same_tls_chain = bool(mapped and upstream.get("tls_id") == bottleneck.get("tls_id"))
        row = {
            "segment_id": segment,
            "bottleneck_movement_id": bottleneck.get("movement_id", ""),
            "upstream_movement_id": upstream.get("movement_id", ""),
            "L_b0_m": round(segment_length_m, 6),
            "lane_drop_delta": lane_drop_delta,
            "q_avg_B0": round(q_avg, 6),
            "q_max_B0": round(q_max, 6),
            "tQ_hist_B0": round(t_q_hist, 6),
            "lambda_B0": round(lambda_vph, 6),
            "fill_B0": round(max(fill_b0, segment_fill_b0), 6),
            "speed_B0": round(segment_speed_b0 if segment_speed_b0 > 0.0 else speed_b0, 6),
            "segment_q_avg_B0": segment_prior["segment_q_avg_B0"],
            "segment_q_max_B0": segment_prior["segment_q_max_B0"],
            "segment_tQ_hist_B0": segment_prior["segment_tQ_hist_B0"],
            "segment_lambda_B0": segment_prior["segment_lambda_B0"],
            "segment_fill_B0": segment_prior["segment_fill_B0"],
            "segment_speed_B0": segment_prior["segment_speed_B0"],
            "mapping_status": mapping_status if mapped else "unmapped",
            "segment_edges": " ".join(segment_edges),
            "segment_lanes": " ".join(segment_lanes),
            "segment_route_start_index": route_start if route_start >= 0 else "",
            "segment_route_end_index": route_end if route_end >= 0 else "",
            "proxy_edge_gap_upstream": proxy_edge_gap_upstream,
            "proxy_edge_gap_bottleneck": proxy_edge_gap_bottleneck,
            "same_tls_chain": same_tls_chain,
            "case_b_runtime_enabled": case_b_runtime_enabled,
            "tau_default": CASE_B_DEFAULT_TAU,
            "case_b_prior_risk": prior_risk,
            "b0_source": B4_PRIMARY_B0_MEASURED_PROXY,
            "mapping_note": mapping_status if mapped else "no route-span controllable movement pair for this CSV segment",
        }
        rows.append(row)
    payload = {
        "schema": "compact_v9_B4_case_b_candidates.v1",
        "generated_at": utc_now(),
        "candidate_segments": list(CASE_B_SEGMENT_IDS),
        "tau_default": CASE_B_DEFAULT_TAU,
        "measurement_source": "SUMO B04 no-control B0 measured proxy",
        "mapping_policy": "Exact mapped controllable SUMO movements are preferred; otherwise S7/S10/S11 use conservative route-span proxy mapping to existing controllable movements.",
        "source_policy": b0_source_policy_rows(),
        "mapped_count": sum(1 for row in rows if str(row["mapping_status"]).startswith("mapped")),
        "runtime_enabled_count": sum(1 for row in rows if truthy(row["case_b_runtime_enabled"])),
        "rows": rows,
    }
    return payload, rows


def primary_candidate_metrics(traffic_review: dict[str, Any]) -> dict[str, Any]:
    candidate_summary = next(
        (row for row in traffic_review.get("candidate_summaries", []) if row.get("candidate") == B4_PRIMARY_CANDIDATE),
        {},
    )
    growth_summary = next(
        (row for row in traffic_review.get("demand_growth_candidate_summary", []) if row.get("candidate") == B4_PRIMARY_CANDIDATE),
        {},
    )
    return {
        "candidate": B4_PRIMARY_CANDIDATE,
        "vehicles": safe_int(growth_summary.get("vehicle_count")),
        "main_through_flow": safe_int(growth_summary.get("main_through_flow")),
        "terminal_sink_flow": safe_int(growth_summary.get("terminal_sink_flow")),
        "top_sink_share": safe_float(growth_summary.get("top_sink_share")),
        "speed_mae_kmh": safe_float(candidate_summary.get("speed_mae_kmh")),
        "free_count": safe_int(candidate_summary.get("free_count")),
        "od_undercovered": safe_int(candidate_summary.get("od_undercovered_free_count")),
        "queue_not_forming": safe_int(candidate_summary.get("queue_not_forming_free_count")),
        "teleport": safe_int(candidate_summary.get("background_teleported")),
        "arrived_ratio": safe_float(candidate_summary.get("background_arrived_ratio")),
    }


def write_review_html(
    summary: dict[str, Any],
    readiness_rows: list[dict[str, Any]],
    plan_rows: list[dict[str, Any]],
    b0_measured_rows: list[dict[str, Any]] | None = None,
    stage2_b0_rows: list[dict[str, Any]] | None = None,
) -> None:
    def table(rows: list[dict[str, Any]], fields: list[str]) -> str:
        body = []
        for row in rows:
            body.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(field, '')))}</td>" for field in fields) + "</tr>")
        return (
            "<table><thead><tr>"
            + "".join(f"<th>{html.escape(field)}</th>" for field in fields)
            + "</tr></thead><tbody>"
            + "".join(body)
            + "</tbody></table>"
        )

    readiness_fields = [
        "movement_id", "mapped_S_segment", "tls_id", "local_fill_80m",
        "local_fill_100m", "local_fill_120m", "stopline_local_fill_100m",
        "corridor_fill_250m", "approach_speed_kmh", "trigger_reason",
        "traffic_pressure", "operational_queue", "bottleneck_risk", "control_mode",
    ]
    plan_fields = [
        "movement_id", "route_order_index", "from_edge", "to_edge", "linkIndex",
        "ev_route_linkIndex", "parallel_through_linkIndex", "same_lane_blocking_linkIndex",
        "selected_flush_phase", "control_strategy",
        "stopline_local_storage_m", "corridor_storage_length_m",
        "selected_green_phase", "selected_red_phase", "controllable",
    ]
    b0_fields = [
        "movement_id", "q_avg_b0_proxy_veh", "q_max_b0_proxy_veh", "tQ_hist_b0_sec",
        "lambda_b0_vph", "L_local_m", "L_corridor_m", "C_local_proxy_veh",
        "measurement_source",
    ]
    stage2_fields = [
        "stage2_param_id", "merge_control_tls", "D_merge_m", "L_merge_m",
        "tE_merge_sec", "C_merge_proxy_veh", "n_need_proxy_veh", "tS_merge_sec",
        "q_A_proxy_veh", "b0_merge_n_occ_mean_proxy_veh", "b0_merge_n_occ_max_proxy_veh",
        "b0_background_inflow_lambda_vph", "b0_merge_waiting_max_sec",
        "b0_merge_halting_proxy_max", "measurement_source",
    ]
    B4_STAGE1_REVIEW_HTML.parent.mkdir(parents=True, exist_ok=True)
    B4_STAGE1_REVIEW_HTML.write_text(f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Compact V9 B4 Stage 1 Review</title>
  <style>
    body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin:0; color:#172033; }}
    header {{ padding:22px; border-bottom:1px solid #d8dee9; }}
    section {{ padding:18px 22px; }}
    table {{ border-collapse:collapse; width:100%; font-size:12px; margin-bottom:22px; }}
    th,td {{ border:1px solid #d8dee9; padding:6px; text-align:left; vertical-align:top; }}
    th {{ background:#f8fafc; }}
    code {{ background:#f2f4f7; padding:1px 4px; border-radius:4px; }}
    pre {{ padding:14px; border:1px solid #d8dee9; background:#f8fafc; overflow:auto; max-height:360px; }}
  </style>
</head>
<body>
<header>
  <h1>Compact V9 B4 Stage 1</h1>
  <p>B4 Stage 1은 B04 baseline 산출물을 입력으로 읽는 정적 신호제어 준비 단계입니다.</p>
  <p>기본 후보 filter: <code>stopline_local_fill_100m &gt;= 0.50 OR approach_speed_kmh &lt;= 15</code></p>
  <p>실제 B4 선점 조건: <code>(local fill OR low speed) AND TA_proxy_sec &lt;= 0</code></p>
  <p>현실 CSV에는 queue length가 없으므로 모든 queue length와 B0 측정값은 SUMO edgeData/laneData proxy입니다.</p>
</header>
<section>
  <h2>Validation</h2>
  <pre>{html.escape(json.dumps(summary.get("validation", {}), ensure_ascii=False, indent=2))}</pre>
  <h2>B0 Measured Proxy Parameters</h2>
  {table(b0_measured_rows or [], b0_fields)}
  <h2>Stage 2 B0 Merge Hold Proxy</h2>
  <p>Stage 2 실제 제어는 35초 dispatch window 안에서 <code>T_hold_proxy_sec</code>를 직접 사용합니다. <code>T_hold_proxy_sec &lt;= 0</code>이면 시간이 부족하다는 뜻이므로 dispatch window가 열리자마자 hold합니다.</p>
  {table(stage2_b0_rows or [], stage2_fields)}
  <h2>Queue Readiness</h2>
  {table(readiness_rows, readiness_fields)}
  <h2>Approach Storage Link Plan</h2>
  {table(plan_rows, plan_fields)}
  <h2>Stage 1 Summary</h2>
  <pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>
</section>
</body>
</html>
""", encoding="utf-8")


def build_b4_stage1() -> dict[str, Any]:
    b04 = load_b04_pipeline()
    require_input(B04_MANIFEST, "b04_manifest")
    require_input(B04_NET, "b04_net")
    require_input(B04_FIRETRUCK_ROUTE_XML, "firetruck_route_xml")
    require_input(B04_FIRETRUCK_ROUTE_CSV, "firetruck_route_csv")
    require_input(ENTRY_TLS_SUMMARY, "entry_tls_summary")
    traffic_review = read_json(B04_TRAFFIC_DEMAND_REVIEW)
    queue_audit = read_json(B04_QUEUE_DEFINITION_AUDIT)
    manifest = read_json(B04_MANIFEST)
    route = route_metadata()
    run_summary = read_json(B4_PRIMARY_RUN_SUMMARY)
    edge_data_path = project_path(str(run_summary.get("edgeData", "")))
    lane_data_path = project_path(str(run_summary.get("laneData", "")))
    speed_csv_path = B4_PRIMARY_SPEED_RECALL
    require_input(edge_data_path, "edgeData")
    require_input(lane_data_path, "laneData")
    require_input(speed_csv_path, "segment_speed_recall")
    edge_data = b04.edge_data_by_edge(edge_data_path)
    lane_data = b04.lane_data_by_edge(lane_data_path)
    lane_flow_samples = lane_flow_samples_by_lane(lane_data_path)
    lane_samples = lane_samples_by_lane(lane_data_path)
    speed_rows = read_csv(speed_csv_path)
    diagnostics_rows = read_csv(B04_MEASUREMENT_DIAGNOSTICS)
    primary_candidate = B4_PRIMARY_CANDIDATE
    primary_metrics = primary_candidate_metrics(traffic_review)
    plan_rows, readiness_rows = build_approach_storage_plan(b04, route, edge_data, lane_data, speed_rows, diagnostics_rows)
    for row in readiness_rows:
        row["candidate"] = primary_candidate
    b0_measured_rows = b0_measured_signal_params(b04, plan_rows, edge_data, lane_data, lane_flow_samples)
    add_queue_calibration_factors(b0_measured_rows, readiness_rows)
    case_b_payload, case_b_rows = build_case_b_candidates(
        b04,
        route,
        plan_rows,
        readiness_rows,
        b0_measured_rows,
        edge_data,
        lane_data,
        lane_flow_samples,
    )
    departure_plan, merge_zone = build_departure_flow_plan(b04, route)
    stage2_b0_payload, stage2_b0_rows = build_stage2_b0_merge_hold_params(b04, departure_plan, lane_samples)
    intersection_rows = build_intersection_rows(plan_rows)
    threshold_proposal = build_threshold_proposal(primary_candidate, readiness_rows)
    ta_proxy_policy = build_ta_proxy_policy()
    route_movement_plan = {
        "schema": "compact_v9_B4_route_movement_plan.v1",
        "generated_at": utc_now(),
        "algorithm": "B4",
        "decision_variable_screening": decision_variable_screening_payload(),
        "route": {
            "route_id": route["route_id"],
            "vehicle_id": route["vehicle_id"],
            "depart_sec": route["depart_sec"],
            "start_edge_id": route["start_edge_id"],
            "merge_edge_id": route["merge_edge_id"],
            "target_edge_id": route["target_edge_id"],
            "route_edge_count": route["route_edge_count"],
            "route_length_m": route["route_length_m"],
            "route_edges": route["route_edges"],
        },
        "ordered_movements": runtime_movement_rows(plan_rows, readiness_rows, b0_measured_rows),
        "stage_policy": {
            "stage1": "dispatch capture and static route/movement/storage precompute",
            "stage2": "departure/merge zone space control before EV joins mainline",
            "stage3": "preemptive control of controllable movements ahead of the EV",
            "bottleneck_order": "open mapped downstream bottleneck movement first, upstream movement later",
        },
        "case_b_candidates": case_b_payload,
    }
    runtime_index = {
        "schema": "compact_v9_B4_runtime_index.v1",
        "generated_at": utc_now(),
        "algorithm": "B4",
        "runtime_status": "stage1_static_index_consumed_by_b4_runtime",
        "decision_variables": B4_DECISION_VARIABLES,
        "decision_variable_screening": decision_variable_screening_payload(),
        "max_active_movements": MAX_ACTIVE_MOVEMENTS,
        "thresholds": threshold_proposal["thresholds"],
        "ta_proxy_policy": ta_proxy_policy,
        "event_schema": EVENT_SCHEMA,
        "departure_flow": {
            "merge_control_tls": departure_plan["merge_control_tls"],
            "background_inflow_lanes": departure_plan["background_inflow_lanes"],
            "merge_zone_lanes": departure_plan["merge_zone_lanes"],
            "background_inflow_red_hold_phase": departure_plan["background_inflow_red_hold_phase"],
            "background_inflow_open_phase": departure_plan["background_inflow_open_phase"],
            "ev_release_control_status": departure_plan["ev_release_control_status"],
            "dispatch_lead_time_sec": DISPATCH_LEAD_TIME_SEC,
            "stage2_b0_merge_hold_params": stage2_b0_payload,
        },
        "case_b_candidates": case_b_payload,
        "ordered_movements": runtime_movement_rows(plan_rows, readiness_rows, b0_measured_rows),
        "scan_policy": "Use only precomputed Stage 1 lanes/movements; do not scan all vehicles.",
    }
    validation = {
        "overall": "WARN" if departure_plan["validation_status"] == "WARN" else "FAIL",
        "b04_inputs_available": "PASS",
        "sumo_not_run": "PASS",
        "bo_not_run": "PASS",
        "fcd_not_enabled": "PASS" if not bool(queue_audit.get("fcd_enabled")) else "WARN",
        "merge_control_tls_available": departure_plan["validation"]["merge_control_tls_available"],
        "background_inflow_hold_available": departure_plan["validation"]["background_inflow_hold_available"],
        "ev_release_control": departure_plan["validation"]["ev_release_control"],
        "ev_release_control_status": departure_plan["ev_release_control_status"],
    }
    outputs = {
        "b4_route_movement_plan_json": rel(B4_ROUTE_MOVEMENT_PLAN),
        "b4_intersections_csv": rel(B4_INTERSECTIONS_CSV),
        "b4_approach_storage_link_plan_csv": rel(B4_APPROACH_STORAGE_LINK_PLAN_CSV),
        "b4_merge_zone_json": rel(B4_MERGE_ZONE),
        "b4_departure_flow_plan_json": rel(B4_DEPARTURE_FLOW_PLAN),
        "b4_bottleneck_queue_readiness_csv": rel(B4_BOTTLENECK_QUEUE_READINESS_CSV),
        "b4_case_b_candidates_csv": rel(B4_CASE_B_CANDIDATES_CSV),
        "b4_case_b_candidates_json": rel(B4_CASE_B_CANDIDATES_JSON),
        "b4_control_queue_threshold_proposal_json": rel(B4_CONTROL_QUEUE_THRESHOLD_PROPOSAL),
        "b4_b0_measured_signal_params_csv": rel(B4_B0_MEASURED_SIGNAL_PARAMS_CSV),
        "b4_ta_proxy_policy_json": rel(B4_TA_PROXY_POLICY),
        "b4_stage2_b0_merge_hold_params_json": rel(B4_STAGE2_B0_MERGE_HOLD_PARAMS_JSON),
        "b4_stage2_b0_merge_hold_params_csv": rel(B4_STAGE2_B0_MERGE_HOLD_PARAMS_CSV),
        "b4_runtime_index_json": rel(B4_RUNTIME_INDEX),
        "b4_stage1_summary_json": rel(B4_STAGE1_SUMMARY),
        "b4_stage1_review_html": rel(B4_STAGE1_REVIEW_HTML),
    }
    summary = {
        "schema": "compact_v9_B4_stage1_summary.v1",
        "generated_at": utc_now(),
        "algorithm": "B4",
        "status": validation["overall"],
        "primary_candidate": primary_candidate,
        "manifest_selected_candidate": manifest.get("selected_candidate", ""),
        "manifest_selected_candidate_role": "primary_selected",
        "decision_variable_screening": decision_variable_screening_payload(),
        "primary_candidate_lock": {
            "primary_candidate": B4_PRIMARY_CANDIDATE,
            "manifest_selected_candidate": manifest.get("selected_candidate", ""),
            "manifest_selected_candidate_role": "primary_selected",
            "reason": "AD is the current variance-smoothed main-through B04 input and the manifest-selected B04 candidate for B4 Stage 1.",
            "metrics": primary_metrics,
        },
        "mode": "Stage1 static preparation only",
        "runtime_implemented": False,
        "control_candidate_expression": threshold_proposal["control_candidate_expression"],
        "ta_proxy_policy": {
            "ta_formula": ta_proxy_policy["ta_formula"],
            "ta_control_policy": ta_proxy_policy["ta_control_policy"],
            "b0_measured_params_source": ta_proxy_policy["b0_measured_params_source"],
            "field_queue_claim": False,
        },
        "movement_summary": {
            "movement_count": len(plan_rows),
            "controllable_movement_count": sum(1 for row in plan_rows if truthy(row.get("controllable"))),
            "control_candidate_count": threshold_proposal["readiness_counts"]["control_candidate_count"],
            "bottleneck_risk_count": threshold_proposal["readiness_counts"]["bottleneck_risk_count"],
            "case_b_candidate_count": len(case_b_rows),
            "case_b_mapped_count": case_b_payload["mapped_count"],
        },
        "departure_summary": {
            "fire_station_start_edge": departure_plan["fire_station_start_edge"],
            "mainline_target_edge": departure_plan["mainline_target_edge"],
            "merge_control_tls": departure_plan["merge_control_tls"],
            "ev_release_control_status": departure_plan["ev_release_control_status"],
            "dispatch_lead_time_sec": DISPATCH_LEAD_TIME_SEC,
            "stage2_b0_formula": stage2_b0_payload["stage2_formula"],
            "stage2_b0_measurement_source": stage2_b0_payload["measurement_source"],
            "stage2_runtime_control_uses_formula_directly": stage2_b0_payload["runtime_control_uses_formula_directly"],
            "stage2_b0_merge_support_status": stage2_b0_payload["b0_merge_support_status"],
            "stage2_runtime_dependency": stage2_b0_payload["runtime_dependency"],
        },
        "validation": validation,
        "input_artifacts": {
            "b04_manifest": rel(B04_MANIFEST),
            "b04_net": rel(B04_NET),
            "firetruck_route_xml": rel(B04_FIRETRUCK_ROUTE_XML),
            "firetruck_route_csv": rel(B04_FIRETRUCK_ROUTE_CSV),
            "entry_tls_summary": rel(ENTRY_TLS_SUMMARY),
            "b04_traffic_demand_review": rel(B04_TRAFFIC_DEMAND_REVIEW),
            "b04_queue_definition_audit": rel(B04_QUEUE_DEFINITION_AUDIT),
            "b04_queue_proxy_by_segment": rel(B04_QUEUE_PROXY_BY_SEGMENT),
            "b4_queue_measurement_diagnostics": rel(B04_MEASUREMENT_DIAGNOSTICS),
            "primary_run_summary": rel(B4_PRIMARY_RUN_SUMMARY),
            "edgeData": rel(edge_data_path),
            "laneData": rel(lane_data_path),
            "tripinfo": rel(project_path(str(run_summary.get("tripinfo", "")))) if run_summary.get("tripinfo") else "",
            "segment_speed_recall": rel(speed_csv_path),
        },
        "outputs": outputs,
        "policy_notes": [
            "B4 Stage 1 reads existing B04 B0/no-control artifacts only.",
            "No field queue length exists in the reference CSV; B4 queue length values are SUMO stopline/local storage proxies.",
            f"B0 measured signal parameters are SUMO {B4_PRIMARY_CANDIDATE} no-control measurements, not field-observed queue lengths.",
            f"Stage 2 B0 merge hold parameters are SUMO {B4_PRIMARY_CANDIDATE} no-control laneData/edgeData proxy values, not field-observed occupancy.",
            "Stage 2 runtime uses T_hold_proxy_sec directly inside the 35s dispatch window; nonpositive T_hold means immediate hold after dispatch capture.",
            "TA_proxy_sec is used with the B4 local fill / low speed safety filter.",
            "stopline_local_fill_100m is the primary trigger denominator; corridor_fill_250m is auxiliary bottleneck/spillback evidence.",
            "Case B candidates come from S7/S10/S11 CSV segments; exact mapped movements are preferred, otherwise route-span proxy maps to existing controllable SUMO movements.",
            "linkIndex is a SUMO TLS movement index, not physical storage length.",
            "Full B4 runtime is intentionally not implemented until this Stage 1 review is accepted.",
        ],
    }
    write_json(B4_ROUTE_MOVEMENT_PLAN, route_movement_plan)
    write_csv(B4_INTERSECTIONS_CSV, intersection_rows, [
        "tls_id", "route_order_min", "route_order_max", "movement_ids", "mapped_S_segments",
        "movement_count", "controllable_count", "phase_count", "selected_green_phases",
        "selected_red_phases", "red_phase_available_count", "green_only_no_red_phase_count",
        "linkIndex", "control_linkIndex", "ev_route_linkIndex",
        "same_lane_blocking_linkIndex", "control_strategies", "from_edges", "to_edges",
    ])
    write_csv(B4_APPROACH_STORAGE_LINK_PLAN_CSV, plan_rows, [
        "movement_id", "tls_id", "from_edge", "to_edge", "linkIndex", "control_linkIndex",
        "ev_route_linkIndex", "parallel_through_linkIndex", "same_lane_blocking_linkIndex",
        "same_lane_blocking_to_edges", "flush_linkIndex", "selected_flush_phase", "full_through_phase",
        "ev_route_phase", "full_through_phase_available", "same_lane_blocker_flush_available",
        "control_strategy", "approach_lanes",
        "storage_edges", "storage_lanes", "local_storage_edges", "local_storage_lanes",
        "local_storage_80m_edges", "local_storage_120m_edges",
        "corridor_storage_edges", "corridor_storage_lanes", "stopline_local_storage_m",
        "stopline_local_actual_length_m", "corridor_storage_length_m", "corridor_storage_raw_length_m",
        "lane_count", "from_edge_lane_count", "to_edge_lane_count", "lane_drop_delta",
        "selected_green_phase", "selected_red_phase", "red_phase_available", "green_only_no_red_phase",
        "mapped_S_segment", "mapped_segment_id",
        "mapped_direction", "route_order_index", "controllable", "storage_definition", "linkIndex_note",
    ])
    write_json(B4_MERGE_ZONE, merge_zone)
    write_json(B4_DEPARTURE_FLOW_PLAN, departure_plan)
    write_csv(B4_BOTTLENECK_QUEUE_READINESS_CSV, readiness_rows, [
        "candidate", "movement_id", "tls_id", "from_edge", "to_edge", "linkIndex",
        "control_linkIndex", "ev_route_linkIndex", "parallel_through_linkIndex",
        "same_lane_blocking_linkIndex", "flush_linkIndex", "selected_flush_phase",
        "full_through_phase_available", "same_lane_blocker_flush_available", "control_strategy",
        "mapped_S_segment", "route_order_index", "stopline_local_queue_m_proxy",
        "local_queue_m_proxy_80m", "local_queue_m_proxy_100m", "local_queue_m_proxy_120m",
        "corridor_queue_m_proxy", "local_fill_80m", "local_fill_100m", "local_fill_120m",
        "stopline_local_fill_100m", "corridor_fill_250m",
        "approach_speed_kmh", "density", "occupancy", "waiting", "timeLoss",
        "low_speed_interval_count", "fast_dense_flow", "signal_only_delay",
        "control_candidate", "trigger_reason", "traffic_pressure", "operational_queue",
        "downstream_blockage_evidence", "bottleneck_risk", "control_mode", "queue_evidence_source",
    ])
    write_json(B4_CASE_B_CANDIDATES_JSON, case_b_payload)
    write_csv(B4_CASE_B_CANDIDATES_CSV, case_b_rows, [
        "segment_id", "bottleneck_movement_id", "upstream_movement_id", "L_b0_m",
        "lane_drop_delta", "q_avg_B0", "q_max_B0", "tQ_hist_B0", "lambda_B0",
        "fill_B0", "speed_B0", "segment_q_avg_B0", "segment_q_max_B0",
        "segment_tQ_hist_B0", "segment_lambda_B0", "segment_fill_B0",
        "segment_speed_B0", "mapping_status", "segment_edges", "segment_lanes",
        "segment_route_start_index", "segment_route_end_index", "proxy_edge_gap_upstream",
        "proxy_edge_gap_bottleneck", "same_tls_chain", "case_b_runtime_enabled",
        "tau_default", "case_b_prior_risk",
        "b0_source", "mapping_note",
    ])
    write_json(B4_CONTROL_QUEUE_THRESHOLD_PROPOSAL, threshold_proposal)
    write_csv(B4_B0_MEASURED_SIGNAL_PARAMS_CSV, b0_measured_rows, [
        "movement_id", "tls_id", "from_edge", "to_edge", "route_order_index",
        "q_avg_b0_proxy_veh", "q_max_b0_proxy_veh", "tQ_hist_b0_sec", "lambda_b0_vph",
        "L_local_m", "L_corridor_m", "C_local_proxy_veh", "lane_count",
        "queue_calibration_reference_m", "queue_calibration_measured_m",
        "queue_calibration_factor", "queue_calibration_factor_applied", "queue_calibration_source",
        "measurement_source", "field_queue_claim", "measurement_note",
    ])
    write_json(B4_TA_PROXY_POLICY, ta_proxy_policy)
    write_json(B4_STAGE2_B0_MERGE_HOLD_PARAMS_JSON, stage2_b0_payload)
    write_csv(B4_STAGE2_B0_MERGE_HOLD_PARAMS_CSV, stage2_b0_rows, [
        "stage2_param_id", "merge_control_tls", "D_merge_m", "L_merge_m",
        "tE_merge_sec", "C_merge_proxy_veh", "n_need_proxy_veh", "tS_merge_sec",
        "entry_yellow_sec", "entry_open_green_sec", "direct_switch_buffer_sec",
        "q_A_proxy_veh", "b0_merge_n_occ_mean_proxy_veh", "b0_merge_n_occ_max_proxy_veh",
        "b0_background_inflow_lambda_vph", "b0_merge_waiting_max_sec",
        "b0_merge_halting_proxy_max", "merge_zone_lanes", "background_inflow_lanes",
        "b0_merge_support_status", "runtime_dependency",
        "measurement_source", "field_measurement_claim", "runtime_control_note",
    ])
    write_json(B4_RUNTIME_INDEX, runtime_index)
    write_json(B4_STAGE1_SUMMARY, summary)
    write_review_html(summary, readiness_rows, plan_rows, b0_measured_rows, stage2_b0_rows)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Compact V9 B4 Stage 1 static inputs")
    parser.add_argument("command", nargs="?", default="b4-stage1", choices=["b4-stage1"])
    return parser.parse_args()


def main() -> int:
    parse_args()
    result = build_b4_stage1()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
