#!/usr/bin/env python3
"""Compact V9 B4 runtime MVP controller.

This module is intentionally B4-only.  It reads the accepted B4 Stage 1
artifacts and exposes testable trigger/controller logic plus a TraCI runtime
loop entrypoint.  It does not run BO, create demand, enable FCD, or update the
B04 manifest.
"""

from __future__ import annotations

import csv
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data_prepared/compact_v9"
STAGE1_DIR = DATA_ROOT / "b4_stage1"
B04_MANIFEST = PROJECT_ROOT / "configs/compact_v9_B04_b0_manifest.json"
B04_NET = DATA_ROOT / "net/jungbu_compact_v9_B04_green18.net.xml"
B04_AA_BACKGROUND_ROUTE = DATA_ROOT / "demand/background_routes_compact_v9_B04_aa_balanced_growth.rou.xml"
B04_FIRETRUCK_ROUTE_XML = DATA_ROOT / "routes/firetruck_to_seoul_station_front.rou.xml"

B4_PRIMARY_CANDIDATE = "B04_aa_balanced_growth"
B4_MANIFEST_SELECTED_CANDIDATE = B4_PRIMARY_CANDIDATE
B4_MANIFEST_SELECTED_ROLE = "primary_selected"
B4_PARAMETER_ID = "B4_MVP_DEFAULT"
EV_ID = "emergency_0"
B004_MODE = "B004"
B04_MODE = "B04"
B4_MODE = "B4"

HEADWAY_M = 7.5
TA_HEADWAY_M = 6.5
TA_EV_SPEED_MPS = 13.9
TA_SATURATION_FLOW_VPH_PER_LANE = 1800.0
LOW_SPEED_MPS = 2.0
HALTING_SPEED_MPS = 0.1
DEFAULT_STEP_SEC = 1.0
DEFAULT_STAGE2_HOLD_REFRESH_SEC = 5.0
DEFAULT_PHASE_BUFFER_SEC = 5.0
DEFAULT_MAX_HOLD_SEC = 30.0
DEFAULT_NEAR_HOLD_DISTANCE_M = 250.0
DEFAULT_STAGE3_CONTROL_DISTANCE_M = 250.0
DEFAULT_MIN_TLS_ACTION_INTERVAL_SEC = 2.0
DEFAULT_SAME_LANE_BLOCKER_FLUSH_SEC = 10.0
EMPTY_APPROACH_SPEED_KMH = 999.0
FREE_FLOW_SPEED_KMH = 50.0
W_EMV = 20.0
W_VEH = 1.0
B4_DEFAULT_PHASE = "bo-smoke"
B4_EV_DEPARTURE_POLICY = "fixed"
B4_EV_DEPART_RANDOMIZED = False
B4_FINAL_VALIDATION_RANDOM_DEPARTURE_IMPLEMENTED = False

REQUIRED_STAGE1_EVENT_FIELDS = [
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
]

RUNTIME_EVENT_FIELDS = REQUIRED_STAGE1_EVENT_FIELDS + [
    "run_id",
    "mode",
    "parameter_id",
    "repeat_id",
    "vehicle_id",
    "ev_edge",
    "ev_lane",
    "ev_route_index",
    "ev_speed_kmh",
    "local_fill_80m",
    "local_fill_120m",
    "queue_m_proxy",
    "corridor_queue_m_proxy",
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
    "D_merge_m",
    "tE_merge_sec",
    "L_merge_m",
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
    "low_speed_count",
    "halting_count",
    "fast_dense_flow",
    "signal_only_delay",
    "active_movement_count",
    "phase_duration_sec",
    "stage2_hold_status",
    "monitor_local_fill_mean",
    "monitor_speed_mean_kmh",
    "monitor_waiting_mean",
    "monitor_halting_count",
    "termination_reason",
    "ev_route_linkIndex",
    "parallel_through_linkIndex",
    "same_lane_blocking_linkIndex",
    "flush_linkIndex",
    "selected_flush_phase",
    "control_strategy",
]

EXPERIMENT_RESULT_FIELDS = [
    "run_id",
    "mode",
    "scenario_name",
    "parameter_id",
    "seed",
    "repeat_id",
    "primary_candidate",
    "stage1_dir",
    "net_file",
    "background_route_file",
    "ev_route_file",
    "free_time_method",
    "delta_T_thr",
    "Q_trig",
    "t_lead",
    "alpha",
    "G_ext",
    "phase",
    "ev_departure_policy",
    "ev_depart_sec",
    "ev_depart_randomized",
    "final_validation_random_departure_implemented",
    "local_fill_trigger",
    "speed_trigger_kmh",
    "max_active_movements",
    "stage2_dispatch_lead_sec",
    "termination_reason",
    "termination_time_sec",
    "hard_max_sim_time",
    "max_recovery_wait_sec",
    "recovery_detected",
    "recovery_time_sec",
    "post_ev_recovery_duration_sec",
    "pre_ev_congestion_reference_window",
    "recovery_threshold_policy",
    "objective_includes_recovery",
    "pre_ev_local_fill_mean",
    "pre_ev_speed_mean_kmh",
    "pre_ev_waiting_mean",
    "pre_ev_halting_count",
    "emergency_seen_by_controller",
    "emergency_seen_first_time",
    "emergency_last_seen_time",
    "emergency_last_edge",
    "emergency_last_route_index",
    "emergency_last_speed_kmh",
    "emergency_stuck_duration_sec",
    "emergency_tripinfo_found",
    "T_actual_EMV_sec",
    "T_free_EMV_sec",
    "d_EMV_sec",
    "veh_eval_count",
    "veh_actual_mean_sec",
    "veh_free_mean_sec",
    "d_veh_sec",
    "b0_T_actual_EMV_sec",
    "b4_T_actual_EMV_sec",
    "b4_minus_b0_EMV_sec",
    "w_EMV",
    "w_veh",
    "objective_score",
    "score_formula",
    "final_status",
    "sumo_exit_code",
    "emergency_departed",
    "emergency_arrived",
    "emergency_teleport",
    "background_departed",
    "background_arrived",
    "background_teleported",
    "background_arrived_ratio",
    "general_vehicle_count",
    "general_mean_travel_time_sec",
    "general_mean_delay_sec",
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
    "b0_emergency_travel_time_sec",
    "b4_emergency_travel_delta_sec",
    "b4_performance_status",
    "failed",
    "failure_reason",
    "wall_time_sec",
    "signal_events_csv",
    "route_visualization_html",
]


class B4RuntimeError(RuntimeError):
    """Expected B4 runtime setup or validation failure."""


@dataclass(frozen=True)
class B4MvpParams:
    """Single default B4 MVP theta; future BO will vary these."""

    parameter_id: str = B4_PARAMETER_ID
    delta_T_thr: float = 0.0
    Q_trig: float = 0.50
    t_lead: float = 35.0
    alpha: float = 5.0
    G_ext: float = 30.0

    def as_result_fields(self) -> dict[str, Any]:
        return {
            "parameter_id": self.parameter_id,
            "delta_T_thr": self.delta_T_thr,
            "Q_trig": self.Q_trig,
            "t_lead": self.t_lead,
            "alpha": self.alpha,
            "G_ext": self.G_ext,
        }


@dataclass(frozen=True)
class B4RuntimePhaseConfig:
    """Runtime phase settings for fast smoke diagnosis."""

    phase: str = B4_DEFAULT_PHASE
    ev_departure_policy: str = B4_EV_DEPARTURE_POLICY
    ev_depart_sec: float = 600.0
    ev_depart_randomized: bool = B4_EV_DEPART_RANDOMIZED
    final_validation_random_departure_implemented: bool = B4_FINAL_VALIDATION_RANDOM_DEPARTURE_IMPLEMENTED
    pre_ev_reference_window: tuple[float, float] = (540.0, 600.0)
    recovery_sample_interval_sec: float = 10.0
    recovery_consecutive_samples: int = 3
    max_recovery_wait_sec: float = 300.0
    hard_max_sim_time: float = 1800.0
    ev_stuck_speed_kmh: float = 1.0
    ev_stuck_duration_sec: float = 120.0
    objective_includes_recovery: bool = False

    @classmethod
    def bo_smoke(cls) -> "B4RuntimePhaseConfig":
        return cls()

    @classmethod
    def from_phase(cls, phase: str) -> "B4RuntimePhaseConfig":
        if phase != B4_DEFAULT_PHASE:
            raise B4RuntimeError(f"unsupported_runtime_phase:{phase}")
        return cls.bo_smoke()

    @property
    def pre_ev_window_text(self) -> str:
        return f"{round_float(self.pre_ev_reference_window[0])}-{round_float(self.pre_ev_reference_window[1])}"

    @property
    def recovery_threshold_policy(self) -> str:
        return "local_fill_mean <= pre_ev_local_fill_mean + 0.05 AND speed_mean_kmh >= pre_ev_speed_mean_kmh - 3 for 3 consecutive samples"

    def as_result_fields(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "ev_departure_policy": self.ev_departure_policy,
            "ev_depart_sec": round_float(self.ev_depart_sec),
            "ev_depart_randomized": self.ev_depart_randomized,
            "final_validation_random_departure_implemented": self.final_validation_random_departure_implemented,
            "hard_max_sim_time": round_float(self.hard_max_sim_time),
            "max_recovery_wait_sec": round_float(self.max_recovery_wait_sec),
            "pre_ev_congestion_reference_window": self.pre_ev_window_text,
            "recovery_threshold_policy": self.recovery_threshold_policy,
            "objective_includes_recovery": self.objective_includes_recovery,
        }


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise B4RuntimeError(f"json_root_not_object:{rel(path)}")
    return payload


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


def split_tokens(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [token for token in str(value or "").split() if token]


def parse_link_indices(value: Any) -> list[int]:
    if isinstance(value, list):
        return [safe_int(item) for item in value]
    return [safe_int(item) for item in split_tokens(value)]


def parse_optional_phase(value: Any) -> int | None:
    if value in {"", None}:
        return None
    return safe_int(value)


def round_float(value: float, digits: int = 6) -> float:
    if not math.isfinite(value):
        return 0.0
    return round(value, digits)


def load_firetruck_route(route_xml: Path = B04_FIRETRUCK_ROUTE_XML) -> dict[str, Any]:
    root = ET.parse(route_xml).getroot()
    route = root.find("route")
    vehicle = root.find("vehicle")
    if route is None:
        raise B4RuntimeError(f"missing_firetruck_route:{rel(route_xml)}")
    edges = split_tokens(route.get("edges", ""))
    if not edges:
        raise B4RuntimeError(f"empty_firetruck_route:{rel(route_xml)}")
    return {
        "route_id": route.get("id", ""),
        "route_edges": edges,
        "vehicle_id": vehicle.get("id", EV_ID) if vehicle is not None else EV_ID,
        "depart_sec": safe_float(vehicle.get("depart") if vehicle is not None else 600.0, 600.0),
    }


def load_edge_lengths(net_file: Path = B04_NET) -> dict[str, float]:
    lengths: dict[str, float] = {}
    root = ET.parse(net_file).getroot()
    for elem in root.findall("edge"):
        if elem.get("function") == "internal":
            continue
        edge_id = elem.get("id", "")
        edge_length = 0.0
        for lane in elem.findall("lane"):
            edge_length = max(edge_length, safe_float(lane.get("length")))
        if edge_id and edge_length > 0:
            lengths[edge_id] = edge_length
    return lengths


def load_edge_lanes(net_file: Path = B04_NET) -> dict[str, tuple[str, ...]]:
    lanes: dict[str, tuple[str, ...]] = {}
    root = ET.parse(net_file).getroot()
    for elem in root.findall("edge"):
        if elem.get("function") == "internal":
            continue
        edge_id = elem.get("id", "")
        lane_ids = tuple(lane.get("id", "") for lane in elem.findall("lane") if lane.get("id"))
        if edge_id and lane_ids:
            lanes[edge_id] = lane_ids
    return lanes


@dataclass(frozen=True)
class B4Thresholds:
    local_fill_trigger: float = 0.50
    speed_trigger_kmh: float = 15.0
    traffic_pressure_local_fill_100m: float = 0.20
    bottleneck_local_fill_100m: float = 0.70
    bottleneck_corridor_fill_250m: float = 0.50

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "B4Thresholds":
        return cls(
            local_fill_trigger=safe_float(payload.get("local_fill_trigger"), 0.50),
            speed_trigger_kmh=safe_float(payload.get("speed_trigger_kmh"), 15.0),
            traffic_pressure_local_fill_100m=safe_float(payload.get("traffic_pressure_local_fill_100m"), 0.20),
            bottleneck_local_fill_100m=safe_float(payload.get("bottleneck_local_fill_100m"), 0.70),
            bottleneck_corridor_fill_250m=safe_float(payload.get("bottleneck_corridor_fill_250m"), 0.50),
        )


@dataclass(frozen=True)
class B4Movement:
    movement_id: str
    tls_id: str
    from_edge: str
    to_edge: str
    link_indices: tuple[int, ...]
    approach_lanes: tuple[str, ...]
    local_storage_lanes: tuple[str, ...]
    corridor_storage_lanes: tuple[str, ...]
    selected_green_phase: int
    selected_red_phase: int
    route_order_index: int
    mapped_s_segment: str
    controllable: bool
    stopline_local_storage_m: float = 100.0
    corridor_storage_length_m: float = 250.0
    lane_count: int = 1
    control_link_indices: tuple[int, ...] = tuple()
    ev_route_link_indices: tuple[int, ...] = tuple()
    parallel_through_link_indices: tuple[int, ...] = tuple()
    same_lane_blocking_link_indices: tuple[int, ...] = tuple()
    flush_link_indices: tuple[int, ...] = tuple()
    selected_flush_phase: int | None = None
    full_through_phase: int | None = None
    ev_route_phase: int | None = None
    full_through_phase_available: bool = False
    same_lane_blocker_flush_available: bool = False
    control_strategy: str = ""
    q_avg_b0_proxy_veh: float = 0.0
    q_max_b0_proxy_veh: float = 0.0
    tQ_hist_b0_sec: float = 0.0
    lambda_b0_vph: float = 0.0
    C_local_proxy_veh: float = 0.0
    b0_measurement_source: str = ""

    @property
    def link_index_text(self) -> str:
        return " ".join(str(index) for index in self.link_indices)

    @property
    def ev_route_link_index_text(self) -> str:
        return " ".join(str(index) for index in self.ev_route_link_indices)

    @property
    def parallel_through_link_index_text(self) -> str:
        return " ".join(str(index) for index in self.parallel_through_link_indices)

    @property
    def same_lane_blocking_link_index_text(self) -> str:
        return " ".join(str(index) for index in self.same_lane_blocking_link_indices)

    @property
    def flush_link_index_text(self) -> str:
        return " ".join(str(index) for index in self.flush_link_indices)


@dataclass(frozen=True)
class B4DepartureFlow:
    merge_control_tls: str
    background_inflow_lanes: tuple[str, ...]
    merge_zone_lanes: tuple[str, ...]
    background_inflow_red_hold_phase: int
    background_inflow_open_phase: int
    ev_release_control_status: str
    dispatch_lead_time_sec: float
    dispatch_lead_time_range_sec: tuple[float, float]
    mainline_target_edge: str
    merge_zone_length_m: float

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "B4DepartureFlow":
        return cls(
            merge_control_tls=str(payload.get("merge_control_tls", "")),
            background_inflow_lanes=tuple(split_tokens(payload.get("background_inflow_lanes", []))),
            merge_zone_lanes=tuple(split_tokens(payload.get("merge_zone_lanes", []))),
            background_inflow_red_hold_phase=safe_int(payload.get("background_inflow_red_hold_phase"), 2),
            background_inflow_open_phase=safe_int(payload.get("background_inflow_open_phase"), 0),
            ev_release_control_status=str(payload.get("ev_release_control_status", "")),
            dispatch_lead_time_sec=safe_float(payload.get("dispatch_lead_time_sec"), 35.0),
            dispatch_lead_time_range_sec=(
                safe_float((payload.get("dispatch_lead_time_range_sec") or [30.0, 40.0])[0], 30.0),
                safe_float((payload.get("dispatch_lead_time_range_sec") or [30.0, 40.0])[1], 40.0),
            ),
            mainline_target_edge=str(payload.get("mainline_target_edge", "")),
            merge_zone_length_m=safe_float(payload.get("merge_zone_length_m"), 0.0),
        )


@dataclass(frozen=True)
class B4Stage2MergeHoldParams:
    D_merge_m: float = 0.0
    L_merge_m: float = 50.0
    tE_merge_sec: float = 0.0
    C_merge_proxy_veh: float = 0.0
    n_need_proxy_veh: float = 2.0
    tS_merge_sec: float = DEFAULT_PHASE_BUFFER_SEC
    b0_merge_n_occ_mean_proxy_veh: float = 0.0
    b0_merge_n_occ_max_proxy_veh: float = 0.0
    b0_background_inflow_lambda_vph: float = 0.0
    b0_merge_waiting_max_sec: float = 0.0
    b0_merge_halting_proxy_max: float = 0.0
    measurement_source: str = "SUMO_B04_AA_B0_laneData_edgeData_proxy"
    stage2_formula: str = "T_hold_proxy_sec = tE_merge_sec - t_clear_proxy_sec - tS_merge_sec"
    runtime_control_uses_formula_directly: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "B4Stage2MergeHoldParams":
        params = payload.get("params", payload)
        if not isinstance(params, dict):
            params = {}
        return cls(
            D_merge_m=safe_float(params.get("D_merge_m"), safe_float(payload.get("D_merge_m"))),
            L_merge_m=safe_float(params.get("L_merge_m"), 50.0),
            tE_merge_sec=safe_float(params.get("tE_merge_sec"), 0.0),
            C_merge_proxy_veh=safe_float(params.get("C_merge_proxy_veh"), 0.0),
            n_need_proxy_veh=safe_float(params.get("n_need_proxy_veh"), 2.0),
            tS_merge_sec=safe_float(params.get("tS_merge_sec"), DEFAULT_PHASE_BUFFER_SEC),
            b0_merge_n_occ_mean_proxy_veh=safe_float(params.get("b0_merge_n_occ_mean_proxy_veh"), 0.0),
            b0_merge_n_occ_max_proxy_veh=safe_float(params.get("b0_merge_n_occ_max_proxy_veh"), 0.0),
            b0_background_inflow_lambda_vph=safe_float(params.get("b0_background_inflow_lambda_vph"), 0.0),
            b0_merge_waiting_max_sec=safe_float(params.get("b0_merge_waiting_max_sec"), 0.0),
            b0_merge_halting_proxy_max=safe_float(params.get("b0_merge_halting_proxy_max"), 0.0),
            measurement_source=str(params.get("measurement_source", payload.get("measurement_source", "SUMO_B04_AA_B0_laneData_edgeData_proxy"))),
            stage2_formula=str(payload.get("stage2_formula", "T_hold_proxy_sec = tE_merge_sec - t_clear_proxy_sec - tS_merge_sec")),
            runtime_control_uses_formula_directly=truthy(payload.get("runtime_control_uses_formula_directly", False)),
        )


@dataclass(frozen=True)
class B4Stage1Inputs:
    stage1_dir: Path
    runtime_index: dict[str, Any]
    departure_payload: dict[str, Any]
    stage2_merge_hold_payload: dict[str, Any]
    threshold_payload: dict[str, Any]
    summary: dict[str, Any]
    thresholds: B4Thresholds
    departure: B4DepartureFlow
    stage2_merge_hold: B4Stage2MergeHoldParams
    movements: tuple[B4Movement, ...]
    event_schema: tuple[str, ...]
    route_edges: tuple[str, ...]
    ev_id: str
    ev_depart_sec: float
    max_active_movements: int
    primary_candidate: str
    manifest_selected_candidate: str
    manifest_selected_candidate_role: str

    @classmethod
    def load(cls, stage1_dir: Path = STAGE1_DIR) -> "B4Stage1Inputs":
        runtime_index = read_json(stage1_dir / "b4_runtime_index.json")
        departure_payload = read_json(stage1_dir / "b4_departure_flow_plan.json")
        stage2_merge_hold_path = stage1_dir / "b4_stage2_b0_merge_hold_params.json"
        stage2_merge_hold_payload = read_json(stage2_merge_hold_path) if stage2_merge_hold_path.is_file() else {}
        threshold_payload = read_json(stage1_dir / "b4_control_queue_threshold_proposal.json")
        summary = read_json(stage1_dir / "b4_stage1_summary.json")
        approach_rows = {
            row["movement_id"]: row
            for row in read_csv(stage1_dir / "b4_approach_storage_link_plan.csv")
            if row.get("movement_id")
        }
        b0_measured_rows = {
            row["movement_id"]: row
            for row in read_csv(stage1_dir / "b4_b0_measured_signal_params.csv")
            if row.get("movement_id")
        } if (stage1_dir / "b4_b0_measured_signal_params.csv").is_file() else {}
        route_meta = load_firetruck_route()
        thresholds = B4Thresholds.from_payload(runtime_index.get("thresholds", {}))
        movements = tuple(
            _movement_from_stage1(
                item,
                {**approach_rows.get(str(item.get("movement_id")), {}), **b0_measured_rows.get(str(item.get("movement_id")), {})},
            )
            for item in runtime_index.get("ordered_movements", [])
        )
        event_schema = tuple(runtime_index.get("event_schema", []))
        stage1 = cls(
            stage1_dir=stage1_dir,
            runtime_index=runtime_index,
            departure_payload=departure_payload,
            stage2_merge_hold_payload=stage2_merge_hold_payload,
            threshold_payload=threshold_payload,
            summary=summary,
            thresholds=thresholds,
            departure=B4DepartureFlow.from_payload(departure_payload),
            stage2_merge_hold=B4Stage2MergeHoldParams.from_payload(stage2_merge_hold_payload),
            movements=movements,
            event_schema=event_schema,
            route_edges=tuple(route_meta["route_edges"]),
            ev_id=str(route_meta["vehicle_id"] or EV_ID),
            ev_depart_sec=safe_float(route_meta["depart_sec"], 600.0),
            max_active_movements=safe_int(runtime_index.get("max_active_movements"), 3),
            primary_candidate=str(summary.get("primary_candidate", "")),
            manifest_selected_candidate=str(summary.get("manifest_selected_candidate", "")),
            manifest_selected_candidate_role=str(summary.get("manifest_selected_candidate_role", "")),
        )
        stage1.validate()
        return stage1

    def validate(self) -> None:
        if self.runtime_index.get("algorithm") != "B4":
            raise B4RuntimeError("runtime_index_algorithm_must_be_B4")
        if self.primary_candidate != B4_PRIMARY_CANDIDATE:
            raise B4RuntimeError(f"b4_primary_candidate_must_be_AA:{self.primary_candidate}")
        if self.manifest_selected_candidate != B4_MANIFEST_SELECTED_CANDIDATE:
            raise B4RuntimeError(f"manifest_selected_candidate_must_be_AA:{self.manifest_selected_candidate}")
        if self.manifest_selected_candidate_role != B4_MANIFEST_SELECTED_ROLE:
            raise B4RuntimeError(f"manifest_selected_candidate_role_must_be_primary_selected:{self.manifest_selected_candidate_role}")
        if self.departure.merge_control_tls != "COMPACT_V9_FIRE_STATION_ENTRY_TLS":
            raise B4RuntimeError(f"unexpected_merge_control_tls:{self.departure.merge_control_tls}")
        if self.departure.ev_release_control_status != "uncontrolled_by_merge_tls":
            raise B4RuntimeError(f"unexpected_ev_release_control_status:{self.departure.ev_release_control_status}")
        if self.stage2_merge_hold.measurement_source and self.stage2_merge_hold.measurement_source != "SUMO_B04_AA_B0_laneData_edgeData_proxy":
            raise B4RuntimeError(f"unexpected_stage2_measurement_source:{self.stage2_merge_hold.measurement_source}")
        missing_event_fields = [field for field in REQUIRED_STAGE1_EVENT_FIELDS if field not in self.event_schema]
        if missing_event_fields:
            raise B4RuntimeError(f"stage1_event_schema_missing:{','.join(missing_event_fields)}")
        if not self.movements:
            raise B4RuntimeError("missing_b4_movements")
        if self.max_active_movements != 3:
            raise B4RuntimeError(f"unexpected_max_active_movements:{self.max_active_movements}")


def _movement_from_stage1(item: dict[str, Any], approach_row: dict[str, str]) -> B4Movement:
    control_link_indices = tuple(parse_link_indices(item.get("control_link_indices", approach_row.get("control_linkIndex", item.get("link_indices", approach_row.get("linkIndex", ""))))))
    ev_route_link_indices = tuple(parse_link_indices(item.get("ev_route_link_indices", approach_row.get("ev_route_linkIndex", item.get("link_indices", approach_row.get("linkIndex", ""))))))
    parallel_through_link_indices = tuple(parse_link_indices(item.get("parallel_through_link_indices", approach_row.get("parallel_through_linkIndex", ""))))
    same_lane_blocking_link_indices = tuple(parse_link_indices(item.get("same_lane_blocking_link_indices", approach_row.get("same_lane_blocking_linkIndex", ""))))
    flush_link_indices = tuple(parse_link_indices(item.get("flush_link_indices", approach_row.get("flush_linkIndex", ""))))
    return B4Movement(
        movement_id=str(item.get("movement_id", "")),
        tls_id=str(item.get("tls_id", "")),
        from_edge=str(item.get("from_edge", "")),
        to_edge=str(item.get("to_edge", "")),
        link_indices=control_link_indices or tuple(parse_link_indices(item.get("link_indices", approach_row.get("linkIndex", "")))),
        approach_lanes=tuple(split_tokens(item.get("approach_lanes", approach_row.get("approach_lanes", "")))),
        local_storage_lanes=tuple(split_tokens(item.get("local_storage_lanes", approach_row.get("local_storage_lanes", "")))),
        corridor_storage_lanes=tuple(split_tokens(item.get("corridor_storage_lanes", approach_row.get("corridor_storage_lanes", "")))),
        selected_green_phase=safe_int(item.get("selected_green_phase", approach_row.get("selected_green_phase")), 0),
        selected_red_phase=safe_int(item.get("selected_red_phase", approach_row.get("selected_red_phase")), 0),
        route_order_index=safe_int(item.get("route_order_index", approach_row.get("route_order_index")), 0),
        mapped_s_segment=str(item.get("mapped_S_segment", approach_row.get("mapped_S_segment", ""))),
        controllable=truthy(item.get("controllable", approach_row.get("controllable", True))),
        stopline_local_storage_m=safe_float(approach_row.get("stopline_local_storage_m"), 100.0),
        corridor_storage_length_m=min(safe_float(approach_row.get("corridor_storage_length_m"), 250.0), 250.0),
        lane_count=max(safe_int(approach_row.get("lane_count"), len(split_tokens(item.get("approach_lanes", []))) or 1), 1),
        control_link_indices=control_link_indices,
        ev_route_link_indices=ev_route_link_indices,
        parallel_through_link_indices=parallel_through_link_indices,
        same_lane_blocking_link_indices=same_lane_blocking_link_indices,
        flush_link_indices=flush_link_indices,
        selected_flush_phase=parse_optional_phase(item.get("selected_flush_phase", approach_row.get("selected_flush_phase", ""))),
        full_through_phase=parse_optional_phase(item.get("full_through_phase", approach_row.get("full_through_phase", ""))),
        ev_route_phase=parse_optional_phase(item.get("ev_route_phase", approach_row.get("ev_route_phase", ""))),
        full_through_phase_available=truthy(item.get("full_through_phase_available", approach_row.get("full_through_phase_available", False))),
        same_lane_blocker_flush_available=truthy(item.get("same_lane_blocker_flush_available", approach_row.get("same_lane_blocker_flush_available", False))),
        control_strategy=str(item.get("control_strategy", approach_row.get("control_strategy", ""))),
        q_avg_b0_proxy_veh=safe_float(item.get("q_avg_b0_proxy_veh", approach_row.get("q_avg_b0_proxy_veh", 0.0))),
        q_max_b0_proxy_veh=safe_float(item.get("q_max_b0_proxy_veh", approach_row.get("q_max_b0_proxy_veh", 0.0))),
        tQ_hist_b0_sec=safe_float(item.get("tQ_hist_b0_sec", approach_row.get("tQ_hist_b0_sec", 0.0))),
        lambda_b0_vph=safe_float(item.get("lambda_b0_vph", approach_row.get("lambda_b0_vph", 0.0))),
        C_local_proxy_veh=safe_float(item.get("C_local_proxy_veh", approach_row.get("C_local_proxy_veh", 0.0))),
        b0_measurement_source=str(item.get("b0_measurement_source", approach_row.get("measurement_source", ""))),
    )


@dataclass(frozen=True)
class LaneStorageMetrics:
    queue_m_proxy: float = 0.0
    vehicle_count: int = 0
    halting_count: int = 0
    low_speed_count: int = 0
    mean_speed_kmh: float = EMPTY_APPROACH_SPEED_KMH
    speed_observed: bool = False
    density: float = 0.0
    occupancy: float = 0.0
    waiting: float = 0.0
    time_loss: float = 0.0


@dataclass(frozen=True)
class MovementRuntimeMetrics:
    movement: B4Movement
    queue_m_proxy: float
    corridor_queue_m_proxy: float
    local_fill_80m: float
    local_fill_100m: float
    local_fill_120m: float
    stopline_local_fill_100m: float
    corridor_fill_250m: float
    approach_speed_kmh: float
    speed_observed: bool
    density: float
    occupancy: float
    waiting: float
    time_loss: float
    low_speed_count: int
    halting_count: int
    fast_dense_flow: bool
    signal_only_delay: bool
    control_candidate: bool
    trigger_reason: str
    traffic_pressure: bool
    operational_queue: bool
    bottleneck_risk: bool
    control_mode: str


@dataclass(frozen=True)
class TAProxyMetrics:
    tE_sec: float
    tS_sec: float
    tQ_sec: float
    TA_proxy_sec: float
    ta_triggered: bool


@dataclass(frozen=True)
class EVState:
    present: bool
    departed: bool
    arrived: bool
    vehicle_id: str
    edge_id: str = ""
    lane_id: str = ""
    route_index: int = -1
    lane_position_m: float = 0.0
    speed_mps: float = 0.0
    speed_kmh: float = 0.0


@dataclass
class ActiveControl:
    movement_id: str
    tls_id: str
    previous_phase: int
    target_phase: int
    started_at: float
    deadline: float
    route_order_index: int
    flushing_downstream: bool = False
    flushing_same_lane_blockers: bool = False


@dataclass
class B4ControllerStats:
    signal_event_count: int = 0
    stage2_hold_count: int = 0
    stage2_hold_total_sec: float = 0.0
    stage2_release_count: int = 0
    stage3_preemption_count: int = 0
    stage3_restore_count: int = 0
    trigger_local_fill_count: int = 0
    trigger_speed_count: int = 0
    bottleneck_mode_count: int = 0
    max_active_movement_count: int = 0
    signal_burden_sec: float = 0.0

    def as_result_fields(self) -> dict[str, Any]:
        return {
            "signal_event_count": self.signal_event_count,
            "stage2_hold_count": self.stage2_hold_count,
            "stage2_hold_total_sec": round_float(self.stage2_hold_total_sec),
            "stage2_release_count": self.stage2_release_count,
            "stage3_preemption_count": self.stage3_preemption_count,
            "stage3_restore_count": self.stage3_restore_count,
            "trigger_local_fill_count": self.trigger_local_fill_count,
            "trigger_speed_count": self.trigger_speed_count,
            "bottleneck_mode_count": self.bottleneck_mode_count,
            "max_active_movement_count": self.max_active_movement_count,
            "signal_burden_sec": round_float(self.signal_burden_sec),
        }


def compute_fill_metrics(queue_m_proxy: float, corridor_queue_m_proxy: float, corridor_storage_length_m: float) -> dict[str, float]:
    corridor_denominator = max(min(corridor_storage_length_m, 250.0), 1.0)
    return {
        "local_fill_80m": round_float(queue_m_proxy / 80.0),
        "local_fill_100m": round_float(queue_m_proxy / 100.0),
        "local_fill_120m": round_float(queue_m_proxy / 120.0),
        "stopline_local_fill_100m": round_float(queue_m_proxy / 100.0),
        "corridor_fill_250m": round_float(corridor_queue_m_proxy / corridor_denominator),
    }


def normalized_trigger_reason(local_trigger: bool, speed_trigger: bool) -> str:
    if local_trigger and speed_trigger:
        return "local_fill_and_low_speed"
    if local_trigger:
        return "local_fill"
    if speed_trigger:
        return "low_speed"
    return "not_triggered"


def evaluate_queue_levels(
    local_fill_100m: float,
    corridor_fill_250m: float,
    approach_speed_kmh: float,
    thresholds: B4Thresholds,
    *,
    speed_observed: bool = True,
    fast_dense_flow: bool = False,
    downstream_blockage: bool = False,
) -> dict[str, Any]:
    local_trigger = local_fill_100m >= thresholds.local_fill_trigger
    speed_trigger = speed_observed and approach_speed_kmh <= thresholds.speed_trigger_kmh
    control_candidate = local_trigger or speed_trigger
    traffic_pressure = local_fill_100m >= thresholds.traffic_pressure_local_fill_100m or fast_dense_flow
    operational_queue = control_candidate
    bottleneck_risk = (
        local_fill_100m >= thresholds.bottleneck_local_fill_100m
        or corridor_fill_250m >= thresholds.bottleneck_corridor_fill_250m
        or downstream_blockage
    )
    return {
        "control_candidate": control_candidate,
        "trigger_reason": normalized_trigger_reason(local_trigger, speed_trigger),
        "traffic_pressure": traffic_pressure,
        "operational_queue": operational_queue,
        "bottleneck_risk": bottleneck_risk,
        "control_mode": "bottleneck_downstream_first" if bottleneck_risk else "normal_preemptive",
    }


def compute_tQ_sec(queue_m_proxy: float, lane_count: int) -> float:
    queue_vehicles = queue_m_proxy / TA_HEADWAY_M
    discharge_vps = (TA_SATURATION_FLOW_VPH_PER_LANE * max(lane_count, 1)) / 3600.0
    return round_float(queue_vehicles / max(discharge_vps, 0.001))


def compute_ta_proxy(
    *,
    ev_distance_m: float,
    queue_m_proxy: float,
    lane_count: int,
    previous_phase: int,
    target_phase: int,
) -> TAProxyMetrics:
    t_e = max(ev_distance_m, 0.0) / TA_EV_SPEED_MPS
    t_s = 0.0 if previous_phase == target_phase else DEFAULT_PHASE_BUFFER_SEC
    t_q = compute_tQ_sec(queue_m_proxy, lane_count)
    ta_proxy = t_e - t_s - t_q
    return TAProxyMetrics(
        tE_sec=round_float(t_e),
        tS_sec=round_float(t_s),
        tQ_sec=round_float(t_q),
        TA_proxy_sec=round_float(ta_proxy),
        ta_triggered=ta_proxy <= 0.0,
    )


def lane_storage_metrics(traci: Any, lanes: tuple[str, ...] | list[str], storage_length_m: float) -> LaneStorageMetrics:
    lane_count = max(len(lanes), 1)
    vehicle_ids: list[str] = []
    lane_speeds: list[tuple[float, int]] = []
    occupancies: list[float] = []
    halting_count = 0
    slow_count = 0
    tail_distance = 0.0
    waiting = 0.0
    time_loss = 0.0

    for lane_id in lanes:
        try:
            ids = list(traci.lane.getLastStepVehicleIDs(lane_id))
        except Exception:
            ids = []
        vehicle_ids.extend(ids)
        count = len(ids)
        try:
            lane_speed = float(traci.lane.getLastStepMeanSpeed(lane_id))
        except Exception:
            lane_speed = 0.0
        if count > 0:
            lane_speeds.append((lane_speed, count))
        try:
            halting_count += int(traci.lane.getLastStepHaltingNumber(lane_id))
        except Exception:
            pass
        try:
            occupancies.append(float(traci.lane.getLastStepOccupancy(lane_id)))
        except Exception:
            pass
        try:
            lane_length = float(traci.lane.getLength(lane_id))
        except Exception:
            lane_length = storage_length_m
        for vehicle_id in ids:
            try:
                speed = float(traci.vehicle.getSpeed(vehicle_id))
            except Exception:
                speed = lane_speed
            if speed <= LOW_SPEED_MPS:
                slow_count += 1
                try:
                    position = float(traci.vehicle.getLanePosition(vehicle_id))
                    tail_distance = max(tail_distance, max(lane_length - position, 0.0))
                except Exception:
                    pass
            if speed <= HALTING_SPEED_MPS:
                halting_count += 1
            try:
                waiting = max(waiting, float(traci.vehicle.getWaitingTime(vehicle_id)))
            except Exception:
                pass
            try:
                time_loss = max(time_loss, float(traci.vehicle.getTimeLoss(vehicle_id)))
            except Exception:
                pass

    weighted_speed_samples = sum(weight for _speed, weight in lane_speeds)
    if weighted_speed_samples > 0:
        mean_speed_mps = sum(speed * weight for speed, weight in lane_speeds) / weighted_speed_samples
        speed_observed = True
        mean_speed_kmh = mean_speed_mps * 3.6
    else:
        speed_observed = False
        mean_speed_kmh = EMPTY_APPROACH_SPEED_KMH

    queue_m_proxy = max(slow_count * HEADWAY_M, halting_count * HEADWAY_M, min(tail_distance, storage_length_m))
    density = len(vehicle_ids) / max(lane_count * (storage_length_m / 1000.0), 0.001)
    occupancy = sum(occupancies) / len(occupancies) if occupancies else 0.0
    return LaneStorageMetrics(
        queue_m_proxy=round_float(queue_m_proxy),
        vehicle_count=len(vehicle_ids),
        halting_count=halting_count,
        low_speed_count=slow_count,
        mean_speed_kmh=round_float(mean_speed_kmh),
        speed_observed=speed_observed,
        density=round_float(density),
        occupancy=round_float(occupancy),
        waiting=round_float(waiting),
        time_loss=round_float(time_loss),
    )


def movement_runtime_metrics(traci: Any, movement: B4Movement, thresholds: B4Thresholds) -> MovementRuntimeMetrics:
    local = lane_storage_metrics(traci, movement.local_storage_lanes, movement.stopline_local_storage_m)
    corridor = lane_storage_metrics(traci, movement.corridor_storage_lanes, movement.corridor_storage_length_m)
    approach = lane_storage_metrics(traci, movement.approach_lanes, movement.stopline_local_storage_m)
    fill = compute_fill_metrics(local.queue_m_proxy, corridor.queue_m_proxy, movement.corridor_storage_length_m)
    approach_speed = approach.mean_speed_kmh
    fast_dense_flow = approach_speed > 30.0 and max(local.density, corridor.density) >= 25.0
    signal_only_delay = approach.speed_observed and approach_speed <= thresholds.speed_trigger_kmh and local.density < 10.0 and local.waiting >= 30.0
    levels = evaluate_queue_levels(
        fill["local_fill_100m"],
        fill["corridor_fill_250m"],
        approach_speed,
        thresholds,
        speed_observed=approach.speed_observed,
        fast_dense_flow=fast_dense_flow,
    )
    return MovementRuntimeMetrics(
        movement=movement,
        queue_m_proxy=local.queue_m_proxy,
        corridor_queue_m_proxy=corridor.queue_m_proxy,
        local_fill_80m=fill["local_fill_80m"],
        local_fill_100m=fill["local_fill_100m"],
        local_fill_120m=fill["local_fill_120m"],
        stopline_local_fill_100m=fill["stopline_local_fill_100m"],
        corridor_fill_250m=fill["corridor_fill_250m"],
        approach_speed_kmh=approach_speed,
        speed_observed=approach.speed_observed,
        density=max(local.density, corridor.density),
        occupancy=max(local.occupancy, corridor.occupancy),
        waiting=max(local.waiting, corridor.waiting),
        time_loss=max(local.time_loss, corridor.time_loss),
        low_speed_count=local.low_speed_count,
        halting_count=local.halting_count,
        fast_dense_flow=fast_dense_flow,
        signal_only_delay=signal_only_delay,
        control_candidate=bool(levels["control_candidate"]),
        trigger_reason=str(levels["trigger_reason"]),
        traffic_pressure=bool(levels["traffic_pressure"]),
        operational_queue=bool(levels["operational_queue"]),
        bottleneck_risk=bool(levels["bottleneck_risk"]),
        control_mode=str(levels["control_mode"]),
    )


def ev_state_from_traci(traci: Any, stage1: B4Stage1Inputs) -> EVState:
    vehicle_id = stage1.ev_id
    try:
        arrived = vehicle_id in set(traci.simulation.getArrivedIDList())
    except Exception:
        arrived = False
    try:
        departed = vehicle_id in set(traci.simulation.getDepartedIDList())
    except Exception:
        departed = False
    try:
        vehicle_ids = set(traci.vehicle.getIDList())
    except Exception:
        vehicle_ids = set()
    if vehicle_id not in vehicle_ids:
        return EVState(False, departed, arrived, vehicle_id)
    try:
        edge_id = str(traci.vehicle.getRoadID(vehicle_id))
    except Exception:
        edge_id = ""
    try:
        lane_id = str(traci.vehicle.getLaneID(vehicle_id))
    except Exception:
        lane_id = ""
    try:
        route_index = int(traci.vehicle.getRouteIndex(vehicle_id))
    except Exception:
        try:
            route_index = list(stage1.route_edges).index(edge_id)
        except ValueError:
            route_index = -1
    try:
        lane_position = float(traci.vehicle.getLanePosition(vehicle_id))
    except Exception:
        lane_position = 0.0
    try:
        speed_mps = float(traci.vehicle.getSpeed(vehicle_id))
    except Exception:
        speed_mps = 0.0
    return EVState(True, True, arrived, vehicle_id, edge_id, lane_id, route_index, lane_position, speed_mps, speed_mps * 3.6)


def monitor_lanes_for_stage1(stage1: B4Stage1Inputs, net_file: Path = B04_NET) -> tuple[str, ...]:
    lanes: set[str] = set()
    for movement in stage1.movements:
        if not movement.controllable:
            continue
        lanes.update(movement.approach_lanes)
        lanes.update(movement.local_storage_lanes)
        lanes.update(movement.corridor_storage_lanes)
    lanes.update(stage1.departure.background_inflow_lanes)
    lanes.update(stage1.departure.merge_zone_lanes)
    edge_lanes = load_edge_lanes(net_file)
    for edge_id in stage1.route_edges:
        lanes.update(edge_lanes.get(edge_id, ()))
    return tuple(sorted(lane for lane in lanes if lane))


def monitor_snapshot(traci: Any, lanes: tuple[str, ...]) -> dict[str, Any]:
    metrics = lane_storage_metrics(traci, lanes, 100.0)
    return {
        "local_fill_mean": round_float(metrics.queue_m_proxy / 100.0),
        "speed_mean_kmh": metrics.mean_speed_kmh,
        "waiting_mean": metrics.waiting,
        "halting_count": metrics.halting_count,
    }


def stage2_merge_hold_proxy_snapshot(traci: Any, stage1: B4Stage1Inputs) -> dict[str, Any]:
    params = stage1.stage2_merge_hold
    merge_lanes = stage1.departure.merge_zone_lanes
    metrics = lane_storage_metrics(traci, merge_lanes, params.L_merge_m)
    merge_lane_count = max(len(merge_lanes), 1)
    n_occ = float(metrics.vehicle_count)
    n_excess = max(0.0, n_occ - (params.C_merge_proxy_veh - params.n_need_proxy_veh))
    t_clear = n_excess * 3600.0 / max(TA_SATURATION_FLOW_VPH_PER_LANE * merge_lane_count, 1.0)
    t_hold = params.tE_merge_sec - t_clear - params.tS_merge_sec
    return {
        "D_merge_m": round_float(params.D_merge_m),
        "tE_merge_sec": round_float(params.tE_merge_sec),
        "L_merge_m": round_float(params.L_merge_m),
        "C_merge_proxy_veh": round_float(params.C_merge_proxy_veh),
        "n_need_proxy_veh": round_float(params.n_need_proxy_veh),
        "n_occ_runtime_veh": round_float(n_occ),
        "n_excess_proxy_veh": round_float(n_excess),
        "t_clear_proxy_sec": round_float(t_clear),
        "T_hold_proxy_sec": round_float(t_hold),
        "b0_merge_n_occ_mean_proxy_veh": round_float(params.b0_merge_n_occ_mean_proxy_veh),
        "b0_merge_n_occ_max_proxy_veh": round_float(params.b0_merge_n_occ_max_proxy_veh),
        "b0_background_inflow_lambda_vph": round_float(params.b0_background_inflow_lambda_vph),
        "stage2_formula": params.stage2_formula,
        "stage2_measurement_source": params.measurement_source,
    }


@dataclass
class B4RuntimeMonitor:
    traci: Any
    stage1: B4Stage1Inputs
    config: B4RuntimePhaseConfig
    run_id: str = ""
    repeat_id: int = 1
    mode: str = B4_MODE
    monitor_lanes: tuple[str, ...] = field(default_factory=tuple)
    pre_samples: list[dict[str, Any]] = field(default_factory=list)
    recovery_consecutive: int = 0
    ev_arrival_time: float | None = None
    next_pre_sample_time: float | None = None
    next_recovery_sample_time: float | None = None
    recovery_detected: bool = False
    recovery_time_sec: float | str = ""
    termination_reason: str = ""
    termination_time_sec: float | str = ""
    emergency_seen_by_controller: bool = False
    emergency_seen_first_time: float | str = ""
    emergency_last_seen_time: float | str = ""
    emergency_last_edge: str = ""
    emergency_last_route_index: int | str = ""
    emergency_last_speed_kmh: float | str = ""
    emergency_stuck_duration_sec: float = 0.0
    stuck_key: tuple[str, int] | None = None
    stuck_start_time: float | None = None
    last_signal_event: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.monitor_lanes:
            self.monitor_lanes = monitor_lanes_for_stage1(self.stage1)
        self.next_pre_sample_time = self.config.pre_ev_reference_window[0]

    def update_signal_context(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            if event.get("target_phase") != "" or event.get("previous_phase") != "":
                self.last_signal_event = event

    def observe(self, now: float, ev_state: EVState) -> tuple[list[dict[str, Any]], bool]:
        events: list[dict[str, Any]] = []
        self.update_emergency_state(now, ev_state)
        events.extend(self.maybe_pre_ev_sample(now, ev_state))

        if self.mode == B04_MODE and ev_state.arrived:
            events.append(self.diagnostic_event(now, "early_termination", ev_state, termination_reason="ev_arrived_min_summary"))
            self.set_termination("ev_arrived_min_summary", now)
            return events, True

        if self.mode == B4_MODE:
            stuck = self.update_stuck_state(now, ev_state)
            if stuck:
                events.append(self.diagnostic_event(now, "emergency_stuck", ev_state, termination_reason="emergency_stuck"))
                events.append(self.diagnostic_event(now, "early_termination", ev_state, termination_reason="emergency_stuck"))
                self.set_termination("emergency_stuck", now)
                return events, True
            if ev_state.arrived and self.ev_arrival_time is None:
                self.ev_arrival_time = now
                self.next_recovery_sample_time = now
            if self.ev_arrival_time is not None:
                events.extend(self.maybe_recovery_sample(now, ev_state))
                if self.recovery_detected:
                    events.append(self.diagnostic_event(now, "early_termination", ev_state, termination_reason="recovery_detected"))
                    self.set_termination("recovery_detected", now)
                    return events, True
                if now - self.ev_arrival_time >= self.config.max_recovery_wait_sec:
                    events.append(self.diagnostic_event(now, "early_termination", ev_state, termination_reason="recovery_timeout"))
                    self.set_termination("recovery_timeout", now)
                    return events, True
            if now >= self.config.hard_max_sim_time:
                events.append(self.diagnostic_event(now, "early_termination", ev_state, termination_reason="hard_max_sim_time"))
                self.set_termination("hard_max_sim_time", now)
                return events, True
        return events, False

    def update_emergency_state(self, now: float, ev_state: EVState) -> None:
        if not ev_state.present:
            return
        self.emergency_seen_by_controller = True
        if self.emergency_seen_first_time == "":
            self.emergency_seen_first_time = round_float(now)
        self.emergency_last_seen_time = round_float(now)
        self.emergency_last_edge = ev_state.edge_id
        self.emergency_last_route_index = ev_state.route_index
        self.emergency_last_speed_kmh = round_float(ev_state.speed_kmh)

    def update_stuck_state(self, now: float, ev_state: EVState) -> bool:
        if not ev_state.present or ev_state.speed_kmh > self.config.ev_stuck_speed_kmh:
            self.stuck_key = None
            self.stuck_start_time = None
            self.emergency_stuck_duration_sec = 0.0
            return False
        key = (ev_state.edge_id, ev_state.route_index)
        if self.stuck_key != key:
            self.stuck_key = key
            self.stuck_start_time = now
            self.emergency_stuck_duration_sec = 0.0
            return False
        self.emergency_stuck_duration_sec = max(now - (self.stuck_start_time or now), 0.0)
        return self.emergency_stuck_duration_sec >= self.config.ev_stuck_duration_sec

    def maybe_pre_ev_sample(self, now: float, ev_state: EVState) -> list[dict[str, Any]]:
        start, end = self.config.pre_ev_reference_window
        if self.next_pre_sample_time is None or now < start or now > end or now < self.next_pre_sample_time:
            return []
        snapshot = monitor_snapshot(self.traci, self.monitor_lanes)
        self.pre_samples.append(snapshot)
        self.next_pre_sample_time += self.config.recovery_sample_interval_sec
        return [self.diagnostic_event(now, "pre_ev_reference_sample", ev_state, snapshot=snapshot)]

    def maybe_recovery_sample(self, now: float, ev_state: EVState) -> list[dict[str, Any]]:
        if self.next_recovery_sample_time is None or now < self.next_recovery_sample_time:
            return []
        snapshot = monitor_snapshot(self.traci, self.monitor_lanes)
        reference = self.pre_reference()
        local_ok = snapshot["local_fill_mean"] <= safe_float(reference.get("pre_ev_local_fill_mean")) + 0.05
        speed_ok = snapshot["speed_mean_kmh"] >= safe_float(reference.get("pre_ev_speed_mean_kmh")) - 3.0
        if local_ok and speed_ok:
            self.recovery_consecutive += 1
        else:
            self.recovery_consecutive = 0
        self.next_recovery_sample_time += self.config.recovery_sample_interval_sec
        events = [self.diagnostic_event(now, "recovery_sample", ev_state, snapshot=snapshot)]
        if self.recovery_consecutive >= self.config.recovery_consecutive_samples:
            self.recovery_detected = True
            self.recovery_time_sec = round_float(now)
            events.append(self.diagnostic_event(now, "recovery_detected", ev_state, snapshot=snapshot, termination_reason="recovery_detected"))
        return events

    def pre_reference(self) -> dict[str, Any]:
        if not self.pre_samples:
            return {
                "pre_ev_local_fill_mean": "",
                "pre_ev_speed_mean_kmh": "",
                "pre_ev_waiting_mean": "",
                "pre_ev_halting_count": "",
            }
        return {
            "pre_ev_local_fill_mean": round_float(sum(sample["local_fill_mean"] for sample in self.pre_samples) / len(self.pre_samples)),
            "pre_ev_speed_mean_kmh": round_float(sum(sample["speed_mean_kmh"] for sample in self.pre_samples) / len(self.pre_samples)),
            "pre_ev_waiting_mean": round_float(sum(sample["waiting_mean"] for sample in self.pre_samples) / len(self.pre_samples)),
            "pre_ev_halting_count": int(round(sum(sample["halting_count"] for sample in self.pre_samples) / len(self.pre_samples))),
        }

    def diagnostic_event(
        self,
        now: float,
        action_type: str,
        ev_state: EVState,
        *,
        snapshot: dict[str, Any] | None = None,
        termination_reason: str = "",
    ) -> dict[str, Any]:
        snapshot = snapshot or {}
        row = event_row(
            time=now,
            stage="monitor",
            action_type=action_type,
            ev_state=ev_state,
            run_id=self.run_id,
            mode=self.mode,
            repeat_id=self.repeat_id,
            monitor_local_fill_mean=snapshot.get("local_fill_mean", ""),
            monitor_speed_mean_kmh=snapshot.get("speed_mean_kmh", ""),
            monitor_waiting_mean=snapshot.get("waiting_mean", ""),
            monitor_halting_count=snapshot.get("halting_count", ""),
            termination_reason=termination_reason,
            trigger_reason=termination_reason,
        )
        for field in [
            "tls_id",
            "movement_id",
            "from_edge",
            "to_edge",
            "linkIndex",
            "target_phase",
            "previous_phase",
            "control_mode",
            "safety_status",
            "active_movement_count",
        ]:
            if row.get(field, "") == "" and self.last_signal_event.get(field, "") != "":
                row[field] = self.last_signal_event[field]
        return row

    def set_termination(self, reason: str, now: float) -> None:
        if not self.termination_reason:
            self.termination_reason = reason
            self.termination_time_sec = round_float(now)

    def as_result_fields(self, *, emergency_tripinfo_found: bool = False) -> dict[str, Any]:
        reference = self.pre_reference()
        post_duration = ""
        if self.ev_arrival_time is not None and self.recovery_time_sec != "":
            post_duration = round_float(safe_float(self.recovery_time_sec) - self.ev_arrival_time)
        return {
            **self.config.as_result_fields(),
            "termination_reason": self.termination_reason,
            "termination_time_sec": self.termination_time_sec,
            "recovery_detected": self.recovery_detected,
            "recovery_time_sec": self.recovery_time_sec,
            "post_ev_recovery_duration_sec": post_duration,
            **reference,
            "emergency_seen_by_controller": self.emergency_seen_by_controller,
            "emergency_seen_first_time": self.emergency_seen_first_time,
            "emergency_last_seen_time": self.emergency_last_seen_time,
            "emergency_last_edge": self.emergency_last_edge,
            "emergency_last_route_index": self.emergency_last_route_index,
            "emergency_last_speed_kmh": self.emergency_last_speed_kmh,
            "emergency_stuck_duration_sec": round_float(self.emergency_stuck_duration_sec),
            "emergency_tripinfo_found": emergency_tripinfo_found,
        }


def order_stage3_candidates(metrics: list[MovementRuntimeMetrics], current_route_index: int, max_active: int) -> list[MovementRuntimeMetrics]:
    ahead = [
        metric
        for metric in metrics
        if metric.movement.controllable
        and metric.movement.route_order_index >= current_route_index
        and metric.control_candidate
    ]
    current = [metric for metric in ahead if metric.movement.route_order_index == current_route_index]
    remaining = [metric for metric in ahead if metric.movement.route_order_index != current_route_index]
    remaining.sort(key=lambda metric: metric.movement.route_order_index)
    return (current + remaining)[:max_active]


def event_row(
    *,
    time: float,
    stage: str,
    action_type: str,
    tls_id: str = "",
    movement: B4Movement | None = None,
    metrics: MovementRuntimeMetrics | None = None,
    target_phase: int | str = "",
    previous_phase: int | str = "",
    ev_state: EVState | None = None,
    ev_distance_m: float | str = "",
    control_mode: str = "",
    safety_status: str = "",
    trigger_reason: str = "",
    phase_duration_sec: float | str = "",
    active_movement_count: int | str = "",
    stage2_hold_status: str = "",
    run_id: str = "",
    mode: str = "B4",
    parameter_id: str = B4_PARAMETER_ID,
    repeat_id: int | str = 1,
    tE_sec: float | str = "",
    tS_sec: float | str = "",
    tQ_sec: float | str = "",
    TA_proxy_sec: float | str = "",
    ta_triggered: bool | str = "",
    stage2_proxy: dict[str, Any] | None = None,
    monitor_local_fill_mean: float | str = "",
    monitor_speed_mean_kmh: float | str = "",
    monitor_waiting_mean: float | str = "",
    monitor_halting_count: int | str = "",
    termination_reason: str = "",
) -> dict[str, Any]:
    movement = movement or B4Movement("", "", "", "", tuple(), tuple(), tuple(), tuple(), 0, 0, 0, "", False)
    ev_state = ev_state or EVState(False, False, False, EV_ID)
    stage2_proxy = stage2_proxy or {}
    row = {
        "time": round_float(time),
        "stage": stage,
        "action_type": action_type,
        "tls_id": tls_id or movement.tls_id,
        "movement_id": movement.movement_id,
        "from_edge": movement.from_edge,
        "to_edge": movement.to_edge,
        "linkIndex": movement.link_index_text,
        "local_fill_100m": metrics.local_fill_100m if metrics else "",
        "corridor_fill_250m": metrics.corridor_fill_250m if metrics else "",
        "approach_speed_kmh": metrics.approach_speed_kmh if metrics else "",
        "density": metrics.density if metrics else "",
        "occupancy": metrics.occupancy if metrics else "",
        "waiting": metrics.waiting if metrics else "",
        "timeLoss": metrics.time_loss if metrics else "",
        "trigger_reason": trigger_reason or (metrics.trigger_reason if metrics else ""),
        "target_phase": target_phase,
        "previous_phase": previous_phase,
        "ev_distance_m": ev_distance_m,
        "control_mode": control_mode or (metrics.control_mode if metrics else ""),
        "safety_status": safety_status,
        "run_id": run_id,
        "mode": mode,
        "parameter_id": parameter_id,
        "repeat_id": repeat_id,
        "vehicle_id": ev_state.vehicle_id,
        "ev_edge": ev_state.edge_id,
        "ev_lane": ev_state.lane_id,
        "ev_route_index": ev_state.route_index,
        "ev_speed_kmh": round_float(ev_state.speed_kmh),
        "local_fill_80m": metrics.local_fill_80m if metrics else "",
        "local_fill_120m": metrics.local_fill_120m if metrics else "",
        "queue_m_proxy": metrics.queue_m_proxy if metrics else "",
        "corridor_queue_m_proxy": metrics.corridor_queue_m_proxy if metrics else "",
        "tE_sec": tE_sec,
        "tS_sec": tS_sec,
        "tQ_sec": tQ_sec,
        "TA_proxy_sec": TA_proxy_sec,
        "b0_q_avg_proxy_veh": round_float(movement.q_avg_b0_proxy_veh),
        "b0_q_max_proxy_veh": round_float(movement.q_max_b0_proxy_veh),
        "b0_tQ_hist_sec": round_float(movement.tQ_hist_b0_sec),
        "b0_lambda_vph": round_float(movement.lambda_b0_vph),
        "ta_triggered": ta_triggered,
        "ta_formula": "TA_proxy_sec = tE_sec - tS_sec - tQ_sec",
        "ta_input_source": movement.b0_measurement_source or "SUMO_B04_AA_B0_edge_lane_data",
        "D_merge_m": stage2_proxy.get("D_merge_m", ""),
        "tE_merge_sec": stage2_proxy.get("tE_merge_sec", ""),
        "L_merge_m": stage2_proxy.get("L_merge_m", ""),
        "C_merge_proxy_veh": stage2_proxy.get("C_merge_proxy_veh", ""),
        "n_need_proxy_veh": stage2_proxy.get("n_need_proxy_veh", ""),
        "n_occ_runtime_veh": stage2_proxy.get("n_occ_runtime_veh", ""),
        "n_excess_proxy_veh": stage2_proxy.get("n_excess_proxy_veh", ""),
        "t_clear_proxy_sec": stage2_proxy.get("t_clear_proxy_sec", ""),
        "T_hold_proxy_sec": stage2_proxy.get("T_hold_proxy_sec", ""),
        "b0_merge_n_occ_mean_proxy_veh": stage2_proxy.get("b0_merge_n_occ_mean_proxy_veh", ""),
        "b0_merge_n_occ_max_proxy_veh": stage2_proxy.get("b0_merge_n_occ_max_proxy_veh", ""),
        "b0_background_inflow_lambda_vph": stage2_proxy.get("b0_background_inflow_lambda_vph", ""),
        "stage2_formula": stage2_proxy.get("stage2_formula", ""),
        "stage2_measurement_source": stage2_proxy.get("stage2_measurement_source", ""),
        "low_speed_count": metrics.low_speed_count if metrics else "",
        "halting_count": metrics.halting_count if metrics else "",
        "fast_dense_flow": metrics.fast_dense_flow if metrics else "",
        "signal_only_delay": metrics.signal_only_delay if metrics else "",
        "active_movement_count": active_movement_count,
        "phase_duration_sec": phase_duration_sec,
        "stage2_hold_status": stage2_hold_status,
        "monitor_local_fill_mean": monitor_local_fill_mean,
        "monitor_speed_mean_kmh": monitor_speed_mean_kmh,
        "monitor_waiting_mean": monitor_waiting_mean,
        "monitor_halting_count": monitor_halting_count,
        "termination_reason": termination_reason,
        "ev_route_linkIndex": movement.ev_route_link_index_text,
        "parallel_through_linkIndex": movement.parallel_through_link_index_text,
        "same_lane_blocking_linkIndex": movement.same_lane_blocking_link_index_text,
        "flush_linkIndex": movement.flush_link_index_text,
        "selected_flush_phase": "" if movement.selected_flush_phase is None else movement.selected_flush_phase,
        "control_strategy": movement.control_strategy,
    }
    return {field: row.get(field, "") for field in RUNTIME_EVENT_FIELDS}


@dataclass
class B4RuntimeController:
    traci: Any
    stage1: B4Stage1Inputs
    params: B4MvpParams = field(default_factory=B4MvpParams)
    run_id: str = ""
    repeat_id: int = 1
    edge_lengths: dict[str, float] = field(default_factory=load_edge_lengths)
    events: list[dict[str, Any]] = field(default_factory=list)
    stats: B4ControllerStats = field(default_factory=B4ControllerStats)
    stage2_hold_active: bool = False
    stage2_completed: bool = False
    stage2_hold_start: float | None = None
    stage2_previous_phase: int | None = None
    active_controls: dict[str, ActiveControl] = field(default_factory=dict)
    last_tls_action_at: dict[str, float] = field(default_factory=dict)

    def step(self) -> list[dict[str, Any]]:
        now = float(self.traci.simulation.getTime())
        ev_state = self.ev_state()
        new_events = []
        new_events.extend(self.handle_stage2(now, ev_state))
        new_events.extend(self.handle_stage3(now, ev_state))
        for event in new_events:
            self.stats.signal_event_count += 1
            self.events.append(event)
        self.stats.max_active_movement_count = max(self.stats.max_active_movement_count, len(self.active_controls))
        return new_events

    def ev_state(self) -> EVState:
        return ev_state_from_traci(self.traci, self.stage1)

    def route_index_for_edge(self, edge_id: str) -> int:
        try:
            return list(self.stage1.route_edges).index(edge_id)
        except ValueError:
            return -1

    def ev_has_merged(self, ev_state: EVState) -> bool:
        merge_index = self.route_index_for_edge(self.stage1.departure.mainline_target_edge)
        if merge_index < 0:
            return False
        if ev_state.present and ev_state.route_index >= merge_index:
            return True
        return ev_state.edge_id == self.stage1.departure.mainline_target_edge

    def should_start_stage2_hold(self, now: float, stage2_proxy: dict[str, Any]) -> bool:
        departure = self.stage1.departure
        earliest_control_time = self.stage1.ev_depart_sec - departure.dispatch_lead_time_sec
        if now < earliest_control_time:
            return False
        if not self.stage1.stage2_merge_hold.runtime_control_uses_formula_directly:
            return True

        t_hold = safe_float(
            stage2_proxy.get("T_hold_proxy_sec"),
            self.stage1.stage2_merge_hold.tE_merge_sec - self.stage1.stage2_merge_hold.tS_merge_sec,
        )
        if t_hold <= 0.0:
            return True

        formula_control_lead = min(t_hold, departure.dispatch_lead_time_sec)
        return now >= self.stage1.ev_depart_sec - formula_control_lead

    def handle_stage2(self, now: float, ev_state: EVState) -> list[dict[str, Any]]:
        departure = self.stage1.departure
        if self.stage2_completed:
            return []
        if ev_state.arrived:
            self.stage2_completed = True
            return []

        should_watch_merge = now >= self.stage1.ev_depart_sec - departure.dispatch_lead_time_sec
        merged = self.ev_has_merged(ev_state)
        if merged and not self.stage2_hold_active:
            self.stage2_completed = True
            return []

        stage2_proxy = stage2_merge_hold_proxy_snapshot(self.traci, self.stage1) if should_watch_merge or self.stage2_hold_active else {}
        should_start_hold = (
            should_watch_merge
            and not merged
            and not self.stage2_hold_active
            and self.should_start_stage2_hold(now, stage2_proxy)
        )
        events = []
        if should_start_hold:
            previous_phase = self.get_tls_phase(departure.merge_control_tls)
            self.set_tls_phase(departure.merge_control_tls, departure.background_inflow_red_hold_phase, DEFAULT_STAGE2_HOLD_REFRESH_SEC)
            self.stage2_hold_active = True
            self.stage2_hold_start = now
            self.stage2_previous_phase = previous_phase
            self.stats.stage2_hold_count += 1
            self.stats.signal_burden_sec += DEFAULT_STAGE2_HOLD_REFRESH_SEC
            events.append(event_row(
                time=now,
                stage="stage2",
                action_type="entry_hold",
                tls_id=departure.merge_control_tls,
                target_phase=departure.background_inflow_red_hold_phase,
                previous_phase=previous_phase,
                ev_state=ev_state,
                control_mode="departure_merge_hold",
                safety_status="ev_release_uncontrolled_warn",
                trigger_reason="T_hold_proxy_direct" if self.stage1.stage2_merge_hold.runtime_control_uses_formula_directly else "dispatch_lead_time_or_ev_premerge",
                phase_duration_sec=DEFAULT_STAGE2_HOLD_REFRESH_SEC,
                stage2_hold_status="hold_active",
                stage2_proxy=stage2_proxy,
                run_id=self.run_id,
                repeat_id=self.repeat_id,
            ))
        elif self.stage2_hold_active and not merged:
            self.set_tls_duration(departure.merge_control_tls, DEFAULT_STAGE2_HOLD_REFRESH_SEC)
        elif self.stage2_hold_active and merged:
            previous_phase = self.get_tls_phase(departure.merge_control_tls)
            self.set_tls_phase(departure.merge_control_tls, departure.background_inflow_open_phase, DEFAULT_STAGE2_HOLD_REFRESH_SEC)
            hold_total = max(now - (self.stage2_hold_start or now), 0.0)
            self.stage2_hold_active = False
            self.stage2_completed = True
            self.stage2_hold_start = None
            self.stage2_previous_phase = None
            self.stats.stage2_release_count += 1
            self.stats.stage2_hold_total_sec += hold_total
            events.append(event_row(
                time=now,
                stage="stage2",
                action_type="entry_hold_release",
                tls_id=departure.merge_control_tls,
                target_phase=departure.background_inflow_open_phase,
                previous_phase=previous_phase,
                ev_state=ev_state,
                control_mode="departure_merge_hold",
                safety_status="ev_release_uncontrolled_warn",
                trigger_reason="ev_passed_merge",
                phase_duration_sec=DEFAULT_STAGE2_HOLD_REFRESH_SEC,
                stage2_hold_status="released",
                stage2_proxy=stage2_proxy,
                run_id=self.run_id,
                repeat_id=self.repeat_id,
            ))
        return events

    def handle_stage3(self, now: float, ev_state: EVState) -> list[dict[str, Any]]:
        if not ev_state.present or not self.ev_has_merged(ev_state):
            return []
        events: list[dict[str, Any]] = []
        events.extend(self.restore_passed_or_expired_controls(now, ev_state))
        movement_metrics = [movement_runtime_metrics(self.traci, movement, self.stage1.thresholds) for movement in self.stage1.movements]
        selected = order_stage3_candidates(movement_metrics, ev_state.route_index, self.stage1.max_active_movements)
        for metric in selected:
            movement = metric.movement
            if len(self.active_controls) >= self.stage1.max_active_movements:
                break
            if movement.tls_id == self.stage1.departure.merge_control_tls and self.stage2_hold_active:
                continue
            if movement.movement_id in self.active_controls:
                continue
            if not self.can_act_on_tls(movement.tls_id, now):
                continue
            previous_phase = self.get_tls_phase(movement.tls_id)
            ev_distance = self.ev_distance_to_movement(ev_state, movement)
            ta = compute_ta_proxy(
                ev_distance_m=float(ev_distance) if ev_distance != "" else 0.0,
                queue_m_proxy=metric.queue_m_proxy,
                lane_count=movement.lane_count,
                previous_phase=previous_phase,
                target_phase=movement.selected_green_phase,
            )
            active_count = len(self.active_controls)
            events.append(event_row(
                time=now,
                stage="stage3",
                action_type="trigger_evaluation",
                movement=movement,
                metrics=metric,
                target_phase=movement.selected_green_phase,
                previous_phase=previous_phase,
                ev_state=ev_state,
                ev_distance_m=round_float(ev_distance),
                control_mode=metric.control_mode,
                safety_status="ta_ready" if ta.ta_triggered else "ta_not_due",
                trigger_reason=metric.trigger_reason if ta.ta_triggered else f"{metric.trigger_reason}+TA_proxy_gt_0",
                active_movement_count=active_count,
                run_id=self.run_id,
                repeat_id=self.repeat_id,
                tE_sec=ta.tE_sec,
                tS_sec=ta.tS_sec,
                tQ_sec=ta.tQ_sec,
                TA_proxy_sec=ta.TA_proxy_sec,
                ta_triggered=ta.ta_triggered,
            ))
            if isinstance(ev_distance, (int, float)) and ev_distance > DEFAULT_STAGE3_CONTROL_DISTANCE_M:
                continue
            if not ta.ta_triggered:
                continue
            duration = self.target_phase_duration(ta.tE_sec)
            self.set_tls_phase(movement.tls_id, movement.selected_green_phase, duration)
            self.last_tls_action_at[movement.tls_id] = now
            self.active_controls[movement.movement_id] = ActiveControl(
                movement_id=movement.movement_id,
                tls_id=movement.tls_id,
                previous_phase=previous_phase,
                target_phase=movement.selected_green_phase,
                started_at=now,
                deadline=now + DEFAULT_MAX_HOLD_SEC,
                route_order_index=movement.route_order_index,
            )
            self.stats.stage3_preemption_count += 1
            self.stats.signal_burden_sec += duration
            if metric.trigger_reason in {"local_fill", "local_fill_and_low_speed"}:
                self.stats.trigger_local_fill_count += 1
            if metric.trigger_reason in {"low_speed", "local_fill_and_low_speed"}:
                self.stats.trigger_speed_count += 1
            if metric.bottleneck_risk:
                self.stats.bottleneck_mode_count += 1
            events.append(event_row(
                time=now,
                stage="stage3",
                action_type="phase_change_target_green",
                movement=movement,
                metrics=metric,
                target_phase=movement.selected_green_phase,
                previous_phase=previous_phase,
                ev_state=ev_state,
                ev_distance_m=round_float(ev_distance),
                control_mode=metric.control_mode,
                safety_status="stage1_selected_phase_mvp",
                trigger_reason=metric.trigger_reason,
                phase_duration_sec=round_float(duration),
                active_movement_count=len(self.active_controls),
                run_id=self.run_id,
                repeat_id=self.repeat_id,
                tE_sec=ta.tE_sec,
                tS_sec=ta.tS_sec,
                tQ_sec=ta.tQ_sec,
                TA_proxy_sec=ta.TA_proxy_sec,
                ta_triggered=ta.ta_triggered,
            ))
        return events

    def restore_passed_or_expired_controls(self, now: float, ev_state: EVState) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for movement_id, control in list(self.active_controls.items()):
            passed = ev_state.route_index > control.route_order_index
            expired = now >= control.deadline
            if not passed and not expired:
                continue
            movement = next((item for item in self.stage1.movements if item.movement_id == movement_id), None)
            if movement is None:
                del self.active_controls[movement_id]
                continue
            if expired and not passed:
                ev_distance = self.ev_distance_to_movement(ev_state, movement)
                if isinstance(ev_distance, (int, float)) and ev_distance > DEFAULT_NEAR_HOLD_DISTANCE_M:
                    previous_phase = self.get_tls_phase(control.tls_id)
                    self.set_tls_phase(control.tls_id, control.previous_phase, DEFAULT_STAGE2_HOLD_REFRESH_SEC)
                    self.last_tls_action_at[control.tls_id] = now
                    self.stats.stage3_restore_count += 1
                    events.append(event_row(
                        time=now,
                        stage="stage3",
                        action_type="restore_previous_phase",
                        movement=movement,
                        target_phase=control.previous_phase,
                        previous_phase=previous_phase,
                        ev_state=ev_state,
                        ev_distance_m=round_float(ev_distance),
                        control_mode="restore_far_ahead_after_max_hold",
                        safety_status="restored_previous_phase",
                        trigger_reason="max_hold_elapsed_far_ahead",
                        phase_duration_sec=DEFAULT_STAGE2_HOLD_REFRESH_SEC,
                        active_movement_count=max(len(self.active_controls) - 1, 0),
                        run_id=self.run_id,
                        repeat_id=self.repeat_id,
                    ))
                    del self.active_controls[movement_id]
                    continue
                ev_stopped_at_movement = ev_state.route_index <= control.route_order_index and ev_state.speed_kmh <= self.stage1.thresholds.speed_trigger_kmh
                if (
                    ev_stopped_at_movement
                    and movement.same_lane_blocker_flush_available
                    and movement.selected_flush_phase is not None
                ):
                    previous_phase = self.get_tls_phase(control.tls_id)
                    if control.flushing_same_lane_blockers:
                        target_phase = control.target_phase
                        action_type = "return_to_target_green"
                        trigger_reason = "same_lane_blocker_flush_return_to_target_green"
                        control.flushing_same_lane_blockers = False
                    else:
                        target_phase = movement.selected_flush_phase
                        action_type = "same_lane_blocker_flush"
                        trigger_reason = "ev_stopped_same_lane_blocker_flush"
                        control.flushing_same_lane_blockers = True
                    duration = DEFAULT_SAME_LANE_BLOCKER_FLUSH_SEC
                    self.set_tls_phase(control.tls_id, target_phase, duration)
                    self.last_tls_action_at[control.tls_id] = now
                    control.deadline = now + duration
                    self.stats.signal_burden_sec += duration
                    events.append(event_row(
                        time=now,
                        stage="stage3",
                        action_type=action_type,
                        movement=movement,
                        target_phase=target_phase,
                        previous_phase=previous_phase,
                        ev_state=ev_state,
                        ev_distance_m=round_float(ev_distance),
                        control_mode="same_lane_blocker_flush_cycle",
                        safety_status="same_lane_blocker_flush",
                        trigger_reason=trigger_reason,
                        phase_duration_sec=duration,
                        active_movement_count=len(self.active_controls),
                        run_id=self.run_id,
                        repeat_id=self.repeat_id,
                    ))
                    continue
                next_same_tls = self.next_same_tls_route_movement(control)
                if next_same_tls is not None and ev_stopped_at_movement:
                    target_phase = control.target_phase if control.flushing_downstream else next_same_tls.selected_green_phase
                    action_type = "return_to_target_green" if control.flushing_downstream else "downstream_flush_same_tls"
                    trigger_reason = "ev_stopped_return_to_target_green" if control.flushing_downstream else "ev_stopped_downstream_flush_same_tls"
                    control_mode = "same_tls_current_downstream_cycle"
                    control.flushing_downstream = not control.flushing_downstream
                else:
                    target_phase = control.target_phase
                    action_type = "extend_target_green"
                    trigger_reason = "ev_not_passed_extend_target_green"
                    control_mode = "extend_until_ev_pass"
                self.set_tls_phase(control.tls_id, target_phase, self.params.G_ext)
                control.deadline = now + self.params.G_ext
                self.stats.signal_burden_sec += self.params.G_ext
                events.append(event_row(
                    time=now,
                    stage="stage3",
                    action_type=action_type,
                    movement=next_same_tls if action_type == "downstream_flush_same_tls" and next_same_tls is not None else movement,
                    target_phase=target_phase,
                    previous_phase=self.get_tls_phase(control.tls_id),
                    ev_state=ev_state,
                    ev_distance_m=round_float(ev_distance),
                    control_mode=control_mode,
                    safety_status="stage1_selected_phase_mvp",
                    trigger_reason=trigger_reason,
                    phase_duration_sec=self.params.G_ext,
                    active_movement_count=len(self.active_controls),
                    run_id=self.run_id,
                    repeat_id=self.repeat_id,
                ))
                continue
            previous_phase = self.get_tls_phase(control.tls_id)
            self.set_tls_phase(control.tls_id, control.previous_phase, DEFAULT_STAGE2_HOLD_REFRESH_SEC)
            self.last_tls_action_at[control.tls_id] = now
            self.stats.stage3_restore_count += 1
            events.append(event_row(
                time=now,
                stage="stage3",
                action_type="restore_previous_phase",
                movement=movement,
                target_phase=control.previous_phase,
                previous_phase=previous_phase,
                ev_state=ev_state,
                ev_distance_m=0.0 if passed else "",
                control_mode="restore_after_ev_pass" if passed else "restore_after_max_hold",
                safety_status="restored_previous_phase",
                trigger_reason="ev_passed_movement" if passed else "max_hold_elapsed",
                phase_duration_sec=DEFAULT_STAGE2_HOLD_REFRESH_SEC,
                active_movement_count=max(len(self.active_controls) - 1, 0),
                run_id=self.run_id,
                repeat_id=self.repeat_id,
            ))
            del self.active_controls[movement_id]
        return events

    def next_same_tls_route_movement(self, control: ActiveControl) -> B4Movement | None:
        candidates = [
            movement
            for movement in self.stage1.movements
            if movement.controllable
            and movement.tls_id == control.tls_id
            and movement.route_order_index > control.route_order_index
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda movement: movement.route_order_index)

    def ev_distance_to_movement(self, ev_state: EVState, movement: B4Movement) -> float:
        current_index = ev_state.route_index
        if current_index < 0:
            current_index = self.route_index_for_edge(ev_state.edge_id)
        if current_index < 0 or movement.route_order_index <= current_index:
            return 0.0
        current_edge = self.stage1.route_edges[current_index]
        current_length = self.edge_lengths.get(current_edge, 0.0)
        distance = max(current_length - ev_state.lane_position_m, 0.0)
        for edge_id in self.stage1.route_edges[current_index + 1 : movement.route_order_index]:
            distance += self.edge_lengths.get(edge_id, 0.0)
        return round_float(distance)

    def target_phase_duration(self, t_e: float | str) -> float:
        if t_e == "":
            return DEFAULT_PHASE_BUFFER_SEC
        return min(max(float(t_e) + self.params.alpha, DEFAULT_PHASE_BUFFER_SEC), self.params.G_ext)

    def can_act_on_tls(self, tls_id: str, now: float) -> bool:
        return now - self.last_tls_action_at.get(tls_id, -9999.0) >= DEFAULT_MIN_TLS_ACTION_INTERVAL_SEC

    def get_tls_phase(self, tls_id: str) -> int:
        try:
            return int(self.traci.trafficlight.getPhase(tls_id))
        except Exception:
            return -1

    def set_tls_phase(self, tls_id: str, phase: int, duration: float) -> None:
        try:
            self.traci.trafficlight.setPhase(tls_id, int(phase))
            self.traci.trafficlight.setPhaseDuration(tls_id, float(duration))
        except Exception as exc:
            raise B4RuntimeError(f"tls_phase_set_failed:{tls_id}:{phase}") from exc

    def set_tls_duration(self, tls_id: str, duration: float) -> None:
        try:
            self.traci.trafficlight.setPhaseDuration(tls_id, float(duration))
        except Exception as exc:
            raise B4RuntimeError(f"tls_duration_set_failed:{tls_id}") from exc


def run_b4_traci_loop(
    traci: Any,
    stage1: B4Stage1Inputs | None = None,
    run_id: str = "",
    repeat_id: int = 1,
    params: B4MvpParams | None = None,
    phase_config: B4RuntimePhaseConfig | None = None,
) -> tuple[list[dict[str, Any]], B4ControllerStats, B4RuntimeMonitor]:
    stage1 = stage1 or B4Stage1Inputs.load()
    phase_config = phase_config or B4RuntimePhaseConfig.bo_smoke()
    controller = B4RuntimeController(traci=traci, stage1=stage1, params=params or B4MvpParams(), run_id=run_id, repeat_id=repeat_id)
    monitor = B4RuntimeMonitor(traci=traci, stage1=stage1, config=phase_config, run_id=run_id, repeat_id=repeat_id, mode=B4_MODE)
    while traci.simulation.getMinExpectedNumber() > 0:
        traci.simulationStep()
        events = controller.step()
        monitor.update_signal_context(events)
        monitor_events, should_stop = monitor.observe(float(traci.simulation.getTime()), controller.ev_state())
        for event in monitor_events:
            controller.events.append(event)
            controller.stats.signal_event_count += 1
        if should_stop:
            break
    return controller.events, controller.stats, monitor


def run_b04_traci_loop(
    traci: Any,
    stage1: B4Stage1Inputs | None = None,
    run_id: str = "",
    repeat_id: int = 1,
    phase_config: B4RuntimePhaseConfig | None = None,
) -> tuple[list[dict[str, Any]], B4RuntimeMonitor]:
    stage1 = stage1 or B4Stage1Inputs.load()
    phase_config = phase_config or B4RuntimePhaseConfig.bo_smoke()
    monitor = B4RuntimeMonitor(traci=traci, stage1=stage1, config=phase_config, run_id=run_id, repeat_id=repeat_id, mode=B04_MODE)
    events: list[dict[str, Any]] = []
    while traci.simulation.getMinExpectedNumber() > 0:
        traci.simulationStep()
        now = float(traci.simulation.getTime())
        monitor_events, should_stop = monitor.observe(now, ev_state_from_traci(traci, stage1))
        events.extend(monitor_events)
        if should_stop:
            break
        if now >= phase_config.hard_max_sim_time:
            monitor.set_termination("hard_max_sim_time", now)
            events.append(monitor.diagnostic_event(now, "early_termination", ev_state_from_traci(traci, stage1), termination_reason="hard_max_sim_time"))
            break
    return events, monitor
