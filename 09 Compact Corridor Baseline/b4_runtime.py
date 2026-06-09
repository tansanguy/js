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
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data_prepared/compact_v9"
STAGE1_DIR = DATA_ROOT / "b4_stage1_s1forced"
B4_CASE_B_CANDIDATES_CSV = "b4_case_b_candidates.csv"
B04_MANIFEST = PROJECT_ROOT / "configs/compact_v9_B04_b0_manifest.json"
B04_NET = PROJECT_ROOT / "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
B04_FIRETRUCK_ROUTE_XML = DATA_ROOT / "routes/firetruck_to_seoul_station_front.rou.xml"

B4_PRIMARY_CANDIDATE = "B04_ad_stage23_trigger"
B04_AA_BACKGROUND_ROUTE = DATA_ROOT / f"demand/background_routes_compact_v9_{B4_PRIMARY_CANDIDATE}.rou.xml"
B04_REALITY_BACKGROUND_ROUTE = DATA_ROOT / "demand/background_routes_compact_v9_B04_reality_4000_sustained_s1forced.rou.xml"
B4_MANIFEST_SELECTED_CANDIDATE = B4_PRIMARY_CANDIDATE
B4_MANIFEST_SELECTED_ROLE = "primary_selected"
B4_PRIMARY_B0_MEASURED_PROXY = "SUMO_B04_AD_B0_measured_proxy"
B4_PRIMARY_LANE_DATA_SOURCE = "SUMO_B04_AD_B0_laneData_edgeData_proxy"
B4_PRIMARY_EDGE_LANE_SOURCE = "SUMO_B04_AD_B0_edge_lane_data"
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
DEFAULT_STAGE2_GATE_HISTORY_SEC = 3.0
DEFAULT_PHASE_BUFFER_SEC = 5.0
DEFAULT_MAX_HOLD_SEC = 14.0
DEFAULT_NEAR_HOLD_DISTANCE_M = 250.0
DEFAULT_STAGE3_CONTROL_DISTANCE_M = 250.0
DEFAULT_STAGE3_MIN_CONTROL_DISTANCE_M = 80.0
DEFAULT_STAGE3_MAX_CONTROL_DISTANCE_M = 1000.0
DEFAULT_STAGE3_PRE_DEPART_MARGIN_SEC = 60.0
DEFAULT_MIN_TLS_ACTION_INTERVAL_SEC = 2.0
DEFAULT_SAME_LANE_BLOCKER_FLUSH_SEC = 10.0
DEFAULT_STAGE2_MEASUREMENT_SCALE = 1.10
DEFAULT_STAGE3_MEASUREMENT_SCALE = 1.65
EMPTY_APPROACH_SPEED_KMH = 999.0
FREE_FLOW_SPEED_KMH = 50.0
TAU_SPEED_FREEFLOW_KMH = FREE_FLOW_SPEED_KMH
QUEUE_PROXY_CONFIDENCE = 0.65
QUEUE_CALIBRATED_CONFIDENCE = 0.70
QUEUE_EXACT_CONFIDENCE = 0.85
QUEUE_STALE_CONFIDENCE = 0.25
QUEUE_CALIBRATION_MIN = 0.5
QUEUE_CALIBRATION_MAX = 2.0
QUEUE_LOCAL_EXACT_FILL_TRIGGER = 0.30
QUEUE_RUNTIME_CALL_MODE = "unique_lane_snapshot"
QUEUE_CALIBRATION_SOURCE = "b4_bottleneck_queue_readiness.csv/b4_b0_measured_signal_params.csv"
W_E = 10.0
W_G = 1.0
B4_DEFAULT_PHASE = "bo-smoke"
B4_EV_DEPARTURE_POLICY = "fixed"
B4_EV_DEPART_RANDOMIZED = False
B4_FINAL_VALIDATION_RANDOM_DEPARTURE_IMPLEMENTED = False
B4_DECISION_VARIABLES = ("t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau")
EVTSP_DISPATCH_DELAY_SEC = 45.0
EVTSP_T_E_MERGE_SEC = 10.0
EVTSP_EV_LENGTH_M = TA_HEADWAY_M
EVTSP_DEFAULT_Q_RATIO = 0.0
EVTSP_TAU_LOWER = 0.70
EVTSP_TAU_UPPER = 0.90
EVTSP_DEFAULT_TAU = 0.75
B4_FIXED_STRUCTURE_PARAMS = {
    "hold_max": 14.0,
    "d_up": 1,
}

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
    "step",
    "ev_depart_sec",
    "t_rel_depart_sec",
    "time_until_depart_sec",
    "ev_status",
    "EV_NotDeparted",
    "EV_Departed",
    "EV_MergePassed",
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
    "stage2_measurement_scale",
    "scaled_Lq_merge_m",
    "C_merge_proxy_veh",
    "n_need_proxy_veh",
    "n_occ_runtime_veh",
    "scaled_n_occ_runtime_veh",
    "n_excess_proxy_veh",
    "n_queue_from_Lq_proxy_veh",
    "n_blocking_proxy_veh",
    "merge_space_deficit_threshold_m",
    "merge_space_deficit",
    "t_clear_proxy_sec",
    "time_to_merge_sec",
    "time_to_merge_source",
    "dispatch_detect_time_sec",
    "s_vph",
    "tS_merge_sec",
    "HOLD_MAX_sec",
    "current_phase",
    "current_state",
    "ped_state",
    "SafetyGate_result",
    "action",
    "deny_reason",
    "T_hold_proxy_sec",
    "b0_merge_n_occ_mean_proxy_veh",
    "b0_merge_n_occ_max_proxy_veh",
    "b0_background_inflow_lambda_vph",
    "stage2_formula",
    "stage2_time_axis_policy",
    "stage2_measurement_source",
    "runtime_or_b0_fallback",
    "low_speed_count",
    "halting_count",
    "fast_dense_flow",
    "signal_only_delay",
    "active_movement_count",
    "phase_duration_sec",
    "stage2_hold_status",
    "Q_ratio",
    "Q_th_m",
    "Q_th_merge_m",
    "T_hold_sec",
    "hold_elapsed_sec",
    "route_intersection_index",
    "L_m",
    "W_m",
    "intersection_index",
    "junction_id",
    "is_ahead_of_ev",
    "is_i_merge",
    "L",
    "W",
    "Lq",
    "stage3_measurement_scale",
    "scaled_Lq_case_b_m",
    "tau",
    "tau_times_L",
    "case_type",
    "downstream_index",
    "gate_target",
    "tE_gate_target",
    "tS_gate_sec",
    "tE_gate_effective_sec",
    "delta_T_thr",
    "gate_result",
    "ge",
    "tQ",
    "t_lead",
    "G_ext",
    "preemption_state",
    "processing_order",
    "Lq_i",
    "TA_down",
    "tQ_i",
    "Gm_sec",
    "Y_sec",
    "R_sec",
    "green_dur_sec",
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
    "queue_method",
    "queue_m_est",
    "queue_veh_est",
    "queue_proxy_m",
    "queue_confidence",
    "queue_data_age_sec",
    "queue_source_id",
    "tls_queue_m_est",
    "tls_queue_veh_est",
    "tls_queue_max_back_m",
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
    "Q_ratio",
    "t_lead",
    "G_ext",
    "tau",
    "ext_max",
    "hold_max",
    "d_up",
    "phase",
    "ev_departure_policy",
    "ev_depart_sec",
    "ev_depart_randomized",
    "final_validation_random_departure_implemented",
    "local_fill_trigger",
    "speed_trigger_kmh",
    "max_active_movements",
    "stage2_dispatch_lead_sec",
    "stage2_measurement_scale",
    "stage3_measurement_scale",
    "stage2_synthetic_demand",
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
    "D_E_sec",
    "V_G_vehicle_count",
    "V_G_arrived_vehicle_count",
    "V_G_unfinished_vehicle_count",
    "V_G_late_excluded_vehicle_count",
    "V_G_capped_unfinished_vehicle_count",
    "D_G_unfinished_policy",
    "T_G_actual_mean_sec",
    "T_G_free_mean_sec",
    "D_G_sec",
    "b0_T_actual_EMV_sec",
    "b4_T_actual_EMV_sec",
    "b4_minus_b0_EMV_sec",
    "w_E",
    "w_G",
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
]


class B4RuntimeError(RuntimeError):
    """Expected B4 runtime setup or validation failure."""


@dataclass(frozen=True)
class B4MvpParams:
    """Single default B4 MVP parameters."""

    parameter_id: str = B4_PARAMETER_ID
    delta_T_thr: float = 0.0
    Q_ratio: float = EVTSP_DEFAULT_Q_RATIO
    t_lead: float = 35.0
    # Legacy input compatibility only. EVTSP runtime does not use alpha/Q_trig.
    alpha: float = 1.0
    Q_trig: float = 0.0
    G_ext: float = 30.0
    tau: float = EVTSP_TAU_LOWER
    ext_max: float = 30.0
    hold_max: float = DEFAULT_MAX_HOLD_SEC
    d_up: int = 3

    def as_result_fields(self) -> dict[str, Any]:
        return {
            "parameter_id": self.parameter_id,
            "delta_T_thr": self.delta_T_thr,
            "Q_ratio": self.Q_ratio,
            "t_lead": self.t_lead,
            "G_ext": self.G_ext,
            "tau": self.tau,
            "ext_max": self.ext_max,
            "hold_max": self.hold_max,
            "d_up": self.d_up,
        }


@dataclass(frozen=True)
class B4ThetaParams(B4MvpParams):
    """EVTSP screened B4 decision variables plus fixed safety structure."""

    parameter_id: str = B4_PARAMETER_ID
    t_lead: float = 21.0
    delta_T_thr: float = 80.0
    G_ext: float = 32.0
    Q_ratio: float = EVTSP_DEFAULT_Q_RATIO
    tau: float = 0.75
    ext_max: float = 32.0
    hold_max: float = B4_FIXED_STRUCTURE_PARAMS["hold_max"]
    d_up: int = B4_FIXED_STRUCTURE_PARAMS["d_up"]
    alpha: float = 1.0
    Q_trig: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "t_lead", round_float(max(float(self.t_lead), 0.0), 0))
        object.__setattr__(self, "delta_T_thr", round_float(max(float(self.delta_T_thr), 0.0), 0))
        object.__setattr__(self, "G_ext", round_float(max(float(self.G_ext), 0.0), 0))
        object.__setattr__(self, "Q_ratio", round_float(clamp_float(float(self.Q_ratio), 0.0, 1.0), 2))
        object.__setattr__(self, "tau", round_float(clamp_float(float(self.tau), EVTSP_TAU_LOWER, EVTSP_TAU_UPPER), 2))
        object.__setattr__(self, "ext_max", self.G_ext)
        object.__setattr__(self, "hold_max", round_float(max(float(self.hold_max), 0.0), 0))
        object.__setattr__(self, "d_up", max(1, min(int(self.d_up), 3)))
        object.__setattr__(self, "alpha", round_float(max(float(self.alpha), 1.0), 2))
        object.__setattr__(self, "Q_trig", round_float(max(float(self.Q_trig), 0.0), 0))

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "B4ThetaParams":
        legacy_q_ratio = safe_float(row.get("Q_trig"), 0.0) / 50.0 if row.get("Q_trig") not in {"", None} else cls.Q_ratio
        return cls(
            parameter_id=str(row.get("parameter_id", cls.parameter_id)),
            t_lead=safe_float(row.get("t_lead"), cls.t_lead),
            delta_T_thr=safe_float(row.get("delta_T_thr", row.get("delta_T_th")), cls.delta_T_thr),
            G_ext=safe_float(row.get("G_ext", row.get("ext_max")), cls.G_ext),
            Q_ratio=safe_float(row.get("Q_ratio"), legacy_q_ratio),
            tau=safe_float(row.get("tau"), cls.tau),
            hold_max=safe_float(row.get("hold_max"), cls.hold_max),
            d_up=safe_int(row.get("d_up"), cls.d_up),
            alpha=safe_float(row.get("alpha"), cls.alpha),
            Q_trig=safe_float(row.get("Q_trig"), cls.Q_trig),
        )


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
    stage2_measurement_scale: float = DEFAULT_STAGE2_MEASUREMENT_SCALE
    stage3_measurement_scale: float = DEFAULT_STAGE3_MEASUREMENT_SCALE
    stage2_synthetic_demand: bool = False

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
            "stage2_measurement_scale": round_float(self.stage2_measurement_scale),
            "stage3_measurement_scale": round_float(self.stage3_measurement_scale),
            "stage2_synthetic_demand": self.stage2_synthetic_demand,
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


def clamp_float(value: float, lower: float, upper: float) -> float:
    if not math.isfinite(value):
        return lower
    return max(lower, min(upper, value))


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * clamp_float(pct, 0.0, 1.0)
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return round_float(ordered[lower])
    weight = index - lower
    return round_float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


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


def tl_logic_details(net_file: Path = B04_NET) -> dict[str, list[dict[str, Any]]]:
    phases: dict[str, list[dict[str, Any]]] = {}
    root = ET.parse(net_file).getroot()
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


def movement_signal_switch_bound_sec(movement: "B4Movement", phases: list[dict[str, Any]]) -> float:
    if not phases or movement.selected_green_phase is None:
        return DEFAULT_PHASE_BUFFER_SEC
    target_phase = next((phase for phase in phases if safe_int(phase.get("phase_index"), -1) == movement.selected_green_phase), {})
    green_main = safe_float(target_phase.get("duration"), DEFAULT_PHASE_BUFFER_SEC)
    yellow = max(
        (
            safe_float(phase.get("duration"))
            for phase in phases
            if "y" in str(phase.get("state", "")).lower() or "yellow" in str(phase.get("name", "")).lower()
        ),
        default=0.0,
    )
    all_red = max(
        (
            safe_float(phase.get("duration"))
            for phase in phases
            if str(phase.get("state", ""))
            and not any(token in str(phase.get("state", "")) for token in ("G", "g", "y"))
        ),
        default=0.0,
    )
    # Stage 1 has no runtime ge measurement, so use the conservative ge=0 upper bound.
    return max(DEFAULT_PHASE_BUFFER_SEC, yellow + all_red + max(0.0, green_main))


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
    selected_red_phase: int | None
    route_order_index: int
    mapped_s_segment: str
    controllable: bool
    route_intersection_index: int = 0
    L_m: float = 100.0
    W_m: float = 10.5
    Gm_sec: float = DEFAULT_PHASE_BUFFER_SEC
    Y_sec: float = 3.0
    R_sec: float = 2.0
    is_merge: bool = False
    Q_th_formula: str = "Q_ratio * L_m"
    Q_th_default_m: float = 0.0
    stage_owner: str = "stage3"
    ped_min_green_sec: float = 17.0
    ped_min_green_source: str = ""
    ped_safety_margin_sec: float = 3.0
    local_storage_edges: tuple[str, ...] = tuple()
    corridor_storage_edges: tuple[str, ...] = tuple()
    stopline_local_storage_m: float = 100.0
    corridor_storage_length_m: float = 250.0
    lane_count: int = 1
    control_link_indices: tuple[int, ...] = tuple()
    ev_route_link_indices: tuple[int, ...] = tuple()
    parallel_through_link_indices: tuple[int, ...] = tuple()
    same_lane_blocking_link_indices: tuple[int, ...] = tuple()
    flush_link_indices: tuple[int, ...] = tuple()
    selected_flush_phase: int | None = None
    red_phase_available: bool = False
    green_only_no_red_phase: bool = False
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
class QueueCalibrationPrior:
    movement_id: str
    tls_id: str
    source: str
    reference_queue_m: float
    runtime_baseline_queue_m: float
    calibration_factor: float


@dataclass(frozen=True)
class B4CaseBCandidate:
    segment_id: str
    bottleneck_movement_id: str
    upstream_movement_id: str
    L_b0_m: float
    lane_drop_delta: int
    q_avg_B0: float
    q_max_B0: float
    tQ_hist_B0: float
    lambda_B0: float
    fill_B0: float
    speed_B0: float
    mapping_status: str
    tau_default: float = 0.75
    case_b_prior_risk: bool = False
    b0_source: str = B4_PRIMARY_B0_MEASURED_PROXY
    segment_edges: tuple[str, ...] = tuple()
    segment_lanes: tuple[str, ...] = tuple()
    segment_route_start_index: int = -1
    segment_route_end_index: int = -1
    proxy_edge_gap_upstream: int = 0
    proxy_edge_gap_bottleneck: int = 0
    same_tls_chain: bool = False
    case_b_runtime_enabled: bool = True
    segment_q_avg_B0: float = 0.0
    segment_q_max_B0: float = 0.0
    segment_tQ_hist_B0: float = 0.0
    segment_lambda_B0: float = 0.0
    segment_fill_B0: float = 0.0
    segment_speed_B0: float = 0.0

    @property
    def mapped(self) -> bool:
        return (
            self.mapping_status in {"mapped", "mapped_exact", "mapped_route_span_proxy"}
            and self.case_b_runtime_enabled
            and bool(self.bottleneck_movement_id and self.upstream_movement_id)
        )


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
    tE_merge_sec: float = EVTSP_T_E_MERGE_SEC
    t_dispatch_delay_sec: float = EVTSP_DISPATCH_DELAY_SEC
    C_merge_proxy_veh: float = 0.0
    n_need_proxy_veh: float = 2.0
    len_E_m: float = 6.5
    len_E_source: str = ""
    tS_merge_sec: float = DEFAULT_PHASE_BUFFER_SEC
    Q_th_merge_default_m: float = 0.0
    HOLD_MAX_formula: str = ""
    HOLD_MAX_sec: float = B4_FIXED_STRUCTURE_PARAMS["hold_max"]
    ped_min_green_sec: float = 17.0
    ped_min_green_source: str = ""
    ped_safety_margin_sec: float = 3.0
    b0_merge_n_occ_mean_proxy_veh: float = 0.0
    b0_merge_n_occ_max_proxy_veh: float = 0.0
    b0_background_inflow_lambda_vph: float = 0.0
    b0_merge_waiting_max_sec: float = 0.0
    b0_merge_halting_proxy_max: float = 0.0
    measurement_source: str = B4_PRIMARY_LANE_DATA_SOURCE
    stage2_formula: str = "T_hold_sec = time_to_merge_sec - t_clear_sec - tS_merge_sec; pre-depart time_to_merge_sec = (ev_depart_sec - now) + tE_merge_sec"
    runtime_control_uses_formula_directly: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "B4Stage2MergeHoldParams":
        params = payload.get("params", payload)
        if not isinstance(params, dict):
            params = {}
        return cls(
            D_merge_m=safe_float(params.get("D_merge_m"), safe_float(payload.get("D_merge_m"))),
            L_merge_m=safe_float(params.get("L_merge_m"), 50.0),
            tE_merge_sec=safe_float(params.get("tE_merge_sec"), EVTSP_T_E_MERGE_SEC),
            t_dispatch_delay_sec=safe_float(params.get("t_dispatch_delay_sec", payload.get("t_dispatch_delay_sec")), EVTSP_DISPATCH_DELAY_SEC),
            C_merge_proxy_veh=safe_float(params.get("C_merge_proxy_veh"), 0.0),
            n_need_proxy_veh=safe_float(params.get("n_need_proxy_veh"), 2.0),
            len_E_m=safe_float(params.get("len_E_m", payload.get("len_E_m")), 6.5),
            len_E_source=str(params.get("len_E_source", payload.get("len_E_source", ""))),
            tS_merge_sec=safe_float(params.get("tS_merge_sec"), DEFAULT_PHASE_BUFFER_SEC),
            Q_th_merge_default_m=safe_float(params.get("Q_th_merge_default_m", payload.get("Q_th_merge_default_m")), 0.0),
            HOLD_MAX_formula=str(params.get("HOLD_MAX_formula", payload.get("HOLD_MAX_formula", ""))),
            HOLD_MAX_sec=safe_float(params.get("HOLD_MAX_sec", payload.get("HOLD_MAX_sec")), B4_FIXED_STRUCTURE_PARAMS["hold_max"]),
            ped_min_green_sec=safe_float(params.get("ped_min_green_sec", payload.get("ped_min_green_sec")), 17.0),
            ped_min_green_source=str(params.get("ped_min_green_source", payload.get("ped_min_green_source", ""))),
            ped_safety_margin_sec=safe_float(params.get("ped_safety_margin_sec", payload.get("ped_safety_margin_sec")), 3.0),
            b0_merge_n_occ_mean_proxy_veh=safe_float(params.get("b0_merge_n_occ_mean_proxy_veh"), 0.0),
            b0_merge_n_occ_max_proxy_veh=safe_float(params.get("b0_merge_n_occ_max_proxy_veh"), 0.0),
            b0_background_inflow_lambda_vph=safe_float(params.get("b0_background_inflow_lambda_vph"), 0.0),
            b0_merge_waiting_max_sec=safe_float(params.get("b0_merge_waiting_max_sec"), 0.0),
            b0_merge_halting_proxy_max=safe_float(params.get("b0_merge_halting_proxy_max"), 0.0),
            measurement_source=str(params.get("measurement_source", payload.get("measurement_source", B4_PRIMARY_LANE_DATA_SOURCE))),
            stage2_formula=str(payload.get("stage2_formula", "T_hold_sec = time_to_merge_sec - t_clear_sec - tS_merge_sec; pre-depart time_to_merge_sec = (ev_depart_sec - now) + tE_merge_sec")),
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
    stage1_audit: dict[str, Any]
    thresholds: B4Thresholds
    departure: B4DepartureFlow
    stage2_merge_hold: B4Stage2MergeHoldParams
    movements: tuple[B4Movement, ...]
    queue_calibration_priors: dict[str, QueueCalibrationPrior]
    case_b_candidates: tuple[B4CaseBCandidate, ...]
    event_schema: tuple[str, ...]
    route_edges: tuple[str, ...]
    ev_id: str
    ev_depart_sec: float
    i_merge: int
    max_active_movements: int
    primary_candidate: str
    manifest_selected_candidate: str
    manifest_selected_candidate_role: str

    @classmethod
    def load(cls, stage1_dir: Path = STAGE1_DIR, route_xml: Path = B04_FIRETRUCK_ROUTE_XML) -> "B4Stage1Inputs":
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
        queue_readiness_rows = {
            row["movement_id"]: row
            for row in read_csv(stage1_dir / "b4_bottleneck_queue_readiness.csv")
            if row.get("movement_id")
        } if (stage1_dir / "b4_bottleneck_queue_readiness.csv").is_file() else {}
        case_b_rows = read_csv(stage1_dir / B4_CASE_B_CANDIDATES_CSV) if (stage1_dir / B4_CASE_B_CANDIDATES_CSV).is_file() else []
        route_meta = load_firetruck_route(route_xml)
        thresholds = B4Thresholds.from_payload(runtime_index.get("thresholds", {}))
        stage1_audit = (
            runtime_index.get("stage1_audit")
            or summary.get("stage1_audit")
            or {}
        )
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
            stage1_audit=stage1_audit if isinstance(stage1_audit, dict) else {},
            thresholds=thresholds,
            departure=B4DepartureFlow.from_payload(departure_payload),
            stage2_merge_hold=B4Stage2MergeHoldParams.from_payload(stage2_merge_hold_payload),
            movements=movements,
            queue_calibration_priors=queue_calibration_priors_from_stage1(queue_readiness_rows, b0_measured_rows, approach_rows),
            case_b_candidates=case_b_candidates_from_rows(case_b_rows),
            event_schema=event_schema,
            route_edges=tuple(route_meta["route_edges"]),
            ev_id=str(route_meta["vehicle_id"] or EV_ID),
            ev_depart_sec=safe_float(route_meta["depart_sec"], 600.0),
            i_merge=safe_int(runtime_index.get("i_merge", (stage1_audit or {}).get("i_merge") if isinstance(stage1_audit, dict) else 0), 0),
            max_active_movements=safe_int(runtime_index.get("max_active_movements"), 3),
            primary_candidate=str(summary.get("primary_candidate", "")),
            manifest_selected_candidate=str(summary.get("manifest_selected_candidate", "")),
            manifest_selected_candidate_role=str(summary.get("manifest_selected_candidate_role", "")),
        )
        stage1.validate()
        return stage1

    def validate(self) -> None:
        allow_input_override = truthy(self.summary.get("allow_runtime_input_override")) or bool(self.summary.get("runtime_input_provenance"))
        if self.runtime_index.get("algorithm") != "B4":
            raise B4RuntimeError("runtime_index_algorithm_must_be_B4")
        if not allow_input_override and self.primary_candidate != B4_PRIMARY_CANDIDATE:
            raise B4RuntimeError(f"b4_primary_candidate_mismatch:{self.primary_candidate}")
        if not allow_input_override and self.manifest_selected_candidate != B4_MANIFEST_SELECTED_CANDIDATE:
            raise B4RuntimeError(f"manifest_selected_candidate_mismatch:{self.manifest_selected_candidate}")
        if not allow_input_override and self.manifest_selected_candidate_role != B4_MANIFEST_SELECTED_ROLE:
            raise B4RuntimeError(f"manifest_selected_candidate_role_must_be_primary_selected:{self.manifest_selected_candidate_role}")
        if self.departure.merge_control_tls != "COMPACT_V9_FIRE_STATION_ENTRY_TLS":
            raise B4RuntimeError(f"unexpected_merge_control_tls:{self.departure.merge_control_tls}")
        if self.departure.ev_release_control_status != "uncontrolled_by_merge_tls":
            raise B4RuntimeError(f"unexpected_ev_release_control_status:{self.departure.ev_release_control_status}")
        if not allow_input_override and self.stage2_merge_hold.measurement_source and self.stage2_merge_hold.measurement_source != B4_PRIMARY_LANE_DATA_SOURCE:
            raise B4RuntimeError(f"unexpected_stage2_measurement_source:{self.stage2_merge_hold.measurement_source}")
        missing_event_fields = [field for field in REQUIRED_STAGE1_EVENT_FIELDS if field not in self.event_schema]
        if missing_event_fields:
            raise B4RuntimeError(f"stage1_event_schema_missing:{','.join(missing_event_fields)}")
        if not self.movements:
            raise B4RuntimeError("missing_b4_movements")
        if self.max_active_movements != 3:
            raise B4RuntimeError(f"unexpected_max_active_movements:{self.max_active_movements}")
        merge_count = sum(1 for movement in self.movements if movement.is_merge)
        if self.i_merge and merge_count != 1:
            raise B4RuntimeError(f"stage1_i_merge_requires_one_merge_movement:{merge_count}")
        if self.i_merge and not any(movement.route_intersection_index == self.i_merge and movement.is_merge for movement in self.movements):
            raise B4RuntimeError(f"stage1_i_merge_row_missing:{self.i_merge}")

    def pedestrian_min_green_by_tls(self) -> dict[str, float]:
        ped_by_tls: dict[str, float] = {}
        for movement in self.movements:
            if not movement.tls_id:
                continue
            ped_min = safe_float(movement.ped_min_green_sec, 0.0)
            if ped_min <= 0.0:
                continue
            ped_by_tls[movement.tls_id] = max(ped_by_tls.get(movement.tls_id, 0.0), ped_min)
        return ped_by_tls


def queue_calibration_priors_from_stage1(
    queue_readiness_rows: dict[str, dict[str, str]],
    b0_measured_rows: dict[str, dict[str, str]],
    approach_rows: dict[str, dict[str, str]],
) -> dict[str, QueueCalibrationPrior]:
    priors: dict[str, QueueCalibrationPrior] = {}
    for movement_id, approach_row in approach_rows.items():
        readiness = queue_readiness_rows.get(movement_id, {})
        measured = b0_measured_rows.get(movement_id, {})
        reference_queue_m = safe_float(
            readiness.get("stopline_local_queue_m_proxy"),
            safe_float(readiness.get("local_queue_m_proxy_100m"), safe_float(readiness.get("queue_m_proxy"), 0.0)),
        )
        baseline_queue_m = safe_float(measured.get("q_avg_b0_proxy_veh"), 0.0) * TA_HEADWAY_M
        if baseline_queue_m <= 0.0:
            baseline_queue_m = safe_float(measured.get("q_max_b0_proxy_veh"), 0.0) * TA_HEADWAY_M
        factor = 1.0
        stored_factor = safe_float(measured.get("queue_calibration_factor_applied"), 0.0)
        if stored_factor > 0.0:
            factor = clamp_float(stored_factor, QUEUE_CALIBRATION_MIN, QUEUE_CALIBRATION_MAX)
        elif reference_queue_m > 0.0 and baseline_queue_m > 0.0:
            factor = clamp_float(reference_queue_m / baseline_queue_m, QUEUE_CALIBRATION_MIN, QUEUE_CALIBRATION_MAX)
        priors[movement_id] = QueueCalibrationPrior(
            movement_id=movement_id,
            tls_id=str(approach_row.get("tls_id", "")),
            source=str(measured.get("queue_calibration_source", QUEUE_CALIBRATION_SOURCE)) if reference_queue_m > 0.0 else "none",
            reference_queue_m=round_float(reference_queue_m),
            runtime_baseline_queue_m=round_float(baseline_queue_m),
            calibration_factor=round_float(factor),
        )
    return priors


def case_b_candidates_from_rows(rows: list[dict[str, str]]) -> tuple[B4CaseBCandidate, ...]:
    candidates: list[B4CaseBCandidate] = []
    for row in rows:
        candidates.append(B4CaseBCandidate(
            segment_id=str(row.get("segment_id", "")),
            bottleneck_movement_id=str(row.get("bottleneck_movement_id", "")),
            upstream_movement_id=str(row.get("upstream_movement_id", "")),
            L_b0_m=safe_float(row.get("L_b0_m"), 0.0),
            lane_drop_delta=safe_int(row.get("lane_drop_delta"), 0),
            q_avg_B0=safe_float(row.get("q_avg_B0"), 0.0),
            q_max_B0=safe_float(row.get("q_max_B0"), 0.0),
            tQ_hist_B0=safe_float(row.get("tQ_hist_B0"), 0.0),
            lambda_B0=safe_float(row.get("lambda_B0"), 0.0),
            fill_B0=safe_float(row.get("fill_B0"), 0.0),
            speed_B0=safe_float(row.get("speed_B0"), 0.0),
            mapping_status=str(row.get("mapping_status", "")),
            tau_default=safe_float(row.get("tau_default"), 0.75),
            case_b_prior_risk=truthy(row.get("case_b_prior_risk")),
            b0_source=str(row.get("b0_source", B4_PRIMARY_B0_MEASURED_PROXY)),
            segment_edges=tuple(split_tokens(row.get("segment_edges", ""))),
            segment_lanes=tuple(split_tokens(row.get("segment_lanes", ""))),
            segment_route_start_index=safe_int(row.get("segment_route_start_index"), -1),
            segment_route_end_index=safe_int(row.get("segment_route_end_index"), -1),
            proxy_edge_gap_upstream=safe_int(row.get("proxy_edge_gap_upstream"), 0),
            proxy_edge_gap_bottleneck=safe_int(row.get("proxy_edge_gap_bottleneck"), 0),
            same_tls_chain=truthy(row.get("same_tls_chain")),
            case_b_runtime_enabled=truthy(row.get("case_b_runtime_enabled", True)),
            segment_q_avg_B0=safe_float(row.get("segment_q_avg_B0"), 0.0),
            segment_q_max_B0=safe_float(row.get("segment_q_max_B0"), 0.0),
            segment_tQ_hist_B0=safe_float(row.get("segment_tQ_hist_B0"), 0.0),
            segment_lambda_B0=safe_float(row.get("segment_lambda_B0"), 0.0),
            segment_fill_B0=safe_float(row.get("segment_fill_B0"), 0.0),
            segment_speed_B0=safe_float(row.get("segment_speed_B0"), 0.0),
        ))
    return tuple(candidates)


def _movement_from_stage1(item: dict[str, Any], approach_row: dict[str, str]) -> B4Movement:
    control_link_indices = tuple(parse_link_indices(item.get("control_link_indices", approach_row.get("control_linkIndex", item.get("link_indices", approach_row.get("linkIndex", ""))))))
    ev_route_link_indices = tuple(parse_link_indices(item.get("ev_route_link_indices", approach_row.get("ev_route_linkIndex", item.get("link_indices", approach_row.get("linkIndex", ""))))))
    parallel_through_link_indices = tuple(parse_link_indices(item.get("parallel_through_link_indices", approach_row.get("parallel_through_linkIndex", ""))))
    same_lane_blocking_link_indices = tuple(parse_link_indices(item.get("same_lane_blocking_link_indices", approach_row.get("same_lane_blocking_linkIndex", ""))))
    flush_link_indices = tuple(parse_link_indices(item.get("flush_link_indices", approach_row.get("flush_linkIndex", ""))))
    selected_red = parse_optional_phase(item.get("selected_red_phase", approach_row.get("selected_red_phase", "")))
    red_available = truthy(item.get("red_phase_available", approach_row.get("red_phase_available", selected_red is not None))) or selected_red is not None
    green_only_no_red = truthy(item.get("green_only_no_red_phase", approach_row.get("green_only_no_red_phase", False))) or (
        selected_red is None and str(item.get("tls_id", "")).startswith("CSV_TLS_")
    )
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
        selected_red_phase=selected_red,
        route_order_index=safe_int(item.get("route_order_index", approach_row.get("route_order_index")), 0),
        mapped_s_segment=str(item.get("mapped_S_segment", approach_row.get("mapped_S_segment", ""))),
        controllable=truthy(item.get("controllable", approach_row.get("controllable", True))),
        route_intersection_index=safe_int(
            item.get("route_intersection_index", approach_row.get("route_intersection_index", item.get("route_order_index", approach_row.get("route_order_index")))),
            0,
        ),
        L_m=safe_float(item.get("L_m", approach_row.get("L_m", approach_row.get("corridor_storage_length_m"))), 100.0),
        W_m=safe_float(item.get("W_m", approach_row.get("W_m")), 10.5),
        Gm_sec=safe_float(item.get("Gm_sec", approach_row.get("Gm_sec")), DEFAULT_PHASE_BUFFER_SEC),
        Y_sec=safe_float(item.get("Y_sec", approach_row.get("Y_sec")), 3.0),
        R_sec=safe_float(item.get("R_sec", approach_row.get("R_sec")), 2.0),
        is_merge=truthy(item.get("is_merge", approach_row.get("is_merge", False))),
        Q_th_formula=str(item.get("Q_th_formula", approach_row.get("Q_th_formula", "Q_ratio * L_m"))),
        Q_th_default_m=safe_float(item.get("Q_th_default_m", approach_row.get("Q_th_default_m", 0.0)), 0.0),
        stage_owner=str(item.get("stage_owner", approach_row.get("stage_owner", "stage2_merge" if truthy(item.get("is_merge", approach_row.get("is_merge", False))) else "stage3"))),
        ped_min_green_sec=safe_float(item.get("ped_min_green_sec", approach_row.get("ped_min_green_sec")), 17.0),
        ped_min_green_source=str(item.get("ped_min_green_source", approach_row.get("ped_min_green_source", ""))),
        ped_safety_margin_sec=safe_float(item.get("ped_safety_margin_sec", approach_row.get("ped_safety_margin_sec")), 3.0),
        local_storage_edges=tuple(split_tokens(item.get("local_storage_edges", approach_row.get("local_storage_edges", approach_row.get("storage_edges", ""))))),
        corridor_storage_edges=tuple(split_tokens(item.get("corridor_storage_edges", approach_row.get("corridor_storage_edges", "")))),
        stopline_local_storage_m=safe_float(approach_row.get("stopline_local_storage_m"), 100.0),
        corridor_storage_length_m=min(safe_float(approach_row.get("corridor_storage_length_m"), 250.0), 250.0),
        lane_count=max(safe_int(approach_row.get("lane_count"), len(split_tokens(item.get("approach_lanes", []))) or 1), 1),
        control_link_indices=control_link_indices,
        ev_route_link_indices=ev_route_link_indices,
        parallel_through_link_indices=parallel_through_link_indices,
        same_lane_blocking_link_indices=same_lane_blocking_link_indices,
        flush_link_indices=flush_link_indices,
        selected_flush_phase=parse_optional_phase(item.get("selected_flush_phase", approach_row.get("selected_flush_phase", ""))),
        red_phase_available=red_available,
        green_only_no_red_phase=green_only_no_red,
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
class LaneSnapshot:
    lane_id: str
    sample_t: float
    vehicle_ids: tuple[str, ...] = tuple()
    vehicle_count: int = 0
    halting_count: int = 0
    mean_speed_mps: float = 0.0
    speed_observed: bool = False
    occupancy: float = 0.0
    length_m: float = 0.0
    waiting_max: float = 0.0
    time_loss_max: float = 0.0


@dataclass(frozen=True)
class LaneSetQueueMetrics:
    queue_m_proxy: float = 0.0
    queue_veh_proxy: int = 0
    vehicle_count: int = 0
    halting_count: int = 0
    low_speed_count: int = 0
    mean_speed_kmh: float = EMPTY_APPROACH_SPEED_KMH
    speed_observed: bool = False
    density: float = 0.0
    occupancy: float = 0.0
    waiting: float = 0.0
    time_loss: float = 0.0
    observed_lane_count: int = 0
    missing_lane_count: int = 0
    max_lane_queue_m: float = 0.0


@dataclass(frozen=True)
class QueueEstimate:
    movement_id: str
    tls_id: str
    method: str
    queue_m_est: float
    queue_veh_est: int
    queue_proxy_m: float
    fill_80: float
    fill_100: float
    fill_120: float
    fill_250: float
    mean_speed_kmh: float
    occupancy: float
    halting: int
    confidence: float
    data_age_sec: float
    source_id: str


@dataclass(frozen=True)
class TlsQueueEstimate:
    tls_id: str
    queue_m_est: float = 0.0
    queue_veh_est: int = 0
    queue_max_back_m: float = 0.0
    unique_lane_count: int = 0
    source_id: str = ""


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
    queue_method: str = "lane_proxy"
    queue_m_est: float = 0.0
    queue_veh_est: int = 0
    queue_proxy_m: float = 0.0
    queue_confidence: float = 0.0
    queue_data_age_sec: float = 0.0
    queue_source_id: str = ""
    tls_queue_m_est: float = 0.0
    tls_queue_veh_est: int = 0
    tls_queue_max_back_m: float = 0.0
    case_b_queue_m_proxy: float = 0.0


@dataclass(frozen=True)
class TAProxyMetrics:
    tE_sec: float
    tS_sec: float
    tQ_sec: float
    TA_proxy_sec: float
    ta_triggered: bool
    queue_source: str = "runtime_proxy"
    tS_source: str = "default_buffer"
    queue_m_used: float = 0.0


@dataclass(frozen=True)
class CaseBEvaluation:
    case_b_source: str = "not_case_b"
    TA_case: str = "caseA"
    TA_upstream_sec: float | str = ""
    TA_bottleneck_sec: float | str = ""
    effective_TA_proxy_sec: float | None = None
    case_b_mapping_status: str = ""
    case_b_segment_id: str = ""
    case_b_segment_queue_m_proxy: float | str = ""
    case_b_segment_fill: float | str = ""
    case_b_same_tls_policy: str = ""


@dataclass(frozen=True)
class Stage3CasePlan:
    case_type: str
    source_metric: MovementRuntimeMetrics
    processing_metrics: tuple[MovementRuntimeMetrics, ...]
    gate_metric: MovementRuntimeMetrics
    downstream_index: int | str = ""
    processing_order: str = ""


@dataclass(frozen=True)
class CaseBSegmentRuntimeMetrics:
    segment_id: str
    queue_m_proxy: float = 0.0
    fill: float = 0.0
    queue_confidence: float = QUEUE_STALE_CONFIDENCE
    observed_lane_count: int = 0
    missing_lane_count: int = 0


@dataclass(frozen=True)
class OriginalTauRuntimeMetrics:
    movement_id: str
    segment_id: str
    queue_m_proxy: float = 0.0
    fill: float = 0.0
    denominator_m: float = 0.0
    source: str = ""
    observed_edge_count: int = 0
    missing_edge_count: int = 0


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
    queue_samples_m: list[float] = field(default_factory=list)
    tls_queue_samples_m: list[float] = field(default_factory=list)
    local_fill_80_samples: list[float] = field(default_factory=list)
    local_fill_100_samples: list[float] = field(default_factory=list)
    local_fill_120_samples: list[float] = field(default_factory=list)
    corridor_fill_250_samples: list[float] = field(default_factory=list)
    queue_method_counts: dict[str, int] = field(default_factory=dict)
    queue_trigger_count: int = 0
    queue_runtime_lane_count: int = 0
    queue_runtime_call_mode: str = ""
    queue_calibration_source: str = ""

    def observe_queue_metrics(self, metrics: list[MovementRuntimeMetrics], tls_estimates: dict[str, TlsQueueEstimate]) -> None:
        for metric in metrics:
            self.queue_samples_m.append(float(metric.queue_m_est))
            self.local_fill_80_samples.append(float(metric.local_fill_80m))
            self.local_fill_100_samples.append(float(metric.local_fill_100m))
            self.local_fill_120_samples.append(float(metric.local_fill_120m))
            self.corridor_fill_250_samples.append(float(metric.corridor_fill_250m))
            method = metric.queue_method or "lane_proxy"
            self.queue_method_counts[method] = self.queue_method_counts.get(method, 0) + 1
            if metric.control_candidate:
                self.queue_trigger_count += 1
            if "calibrated" in method:
                self.queue_calibration_source = QUEUE_CALIBRATION_SOURCE
        for estimate in tls_estimates.values():
            self.tls_queue_samples_m.append(float(estimate.queue_m_est))

    def primary_queue_method(self) -> str:
        if not self.queue_method_counts:
            return ""
        return max(self.queue_method_counts.items(), key=lambda item: (item[1], item[0]))[0]

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
            "queue_method_primary": self.primary_queue_method(),
            "queue_max_m": round_float(max(self.queue_samples_m) if self.queue_samples_m else 0.0),
            "queue_p95_m": percentile(self.queue_samples_m, 0.95),
            "tls_queue_max_m": round_float(max(self.tls_queue_samples_m) if self.tls_queue_samples_m else 0.0),
            "queue_local_fill_80m_max": round_float(max(self.local_fill_80_samples) if self.local_fill_80_samples else 0.0),
            "queue_local_fill_100m_max": round_float(max(self.local_fill_100_samples) if self.local_fill_100_samples else 0.0),
            "queue_local_fill_120m_max": round_float(max(self.local_fill_120_samples) if self.local_fill_120_samples else 0.0),
            "queue_corridor_fill_250m_max": round_float(max(self.corridor_fill_250_samples) if self.corridor_fill_250_samples else 0.0),
            "queue_trigger_count": self.queue_trigger_count,
            "queue_sampling_period_sec": DEFAULT_STEP_SEC,
            "queue_runtime_lane_count": self.queue_runtime_lane_count,
            "queue_runtime_call_mode": self.queue_runtime_call_mode,
            "queue_calibration_source": self.queue_calibration_source,
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


def theta_runtime_thresholds(base: B4Thresholds, params: B4MvpParams) -> B4Thresholds:
    return B4Thresholds(
        local_fill_trigger=base.local_fill_trigger,
        speed_trigger_kmh=base.speed_trigger_kmh,
        traffic_pressure_local_fill_100m=base.traffic_pressure_local_fill_100m,
        bottleneck_local_fill_100m=base.bottleneck_local_fill_100m,
        bottleneck_corridor_fill_250m=base.bottleneck_corridor_fill_250m,
    )


def theta_ta_lead_sec(params: B4MvpParams) -> float:
    return safe_float(getattr(params, "t_lead", 0.0), 0.0)


def theta_bounds_from_stage1(stage1: B4Stage1Inputs, net_file: Path = B04_NET) -> dict[str, Any]:
    phases_by_tls = tl_logic_details(net_file) if net_file.is_file() else {}
    max_signal_switch = max(
        (
            movement_signal_switch_bound_sec(movement, phases_by_tls.get(movement.tls_id, []))
            for movement in stage1.movements
            if movement.controllable
        ),
        default=DEFAULT_PHASE_BUFFER_SEC,
    )
    t_lead_ub = max(DEFAULT_PHASE_BUFFER_SEC, math.ceil(max_signal_switch))
    ext_ub = max(DEFAULT_STAGE2_HOLD_REFRESH_SEC, math.ceil(stage1.departure.dispatch_lead_time_range_sec[1] + DEFAULT_PHASE_BUFFER_SEC))
    delta_ub = max(120, int(math.ceil(t_lead_ub * 2)))
    return {
        "schema": "compact_v9_B4_evtsp_theta_bounds.v1",
        "source": "B4Stage1Inputs+B04_signal_program_proxy",
        "decision_variables": list(B4_DECISION_VARIABLES),
        "screening_policy": "EVTSP optimizes t_lead, delta_T_thr, G_ext, Q_ratio, and tau. Legacy alpha/Q_trig are accepted only as input aliases and are not runtime decision variables.",
        "fixed_structure_params": dict(B4_FIXED_STRUCTURE_PARAMS),
        "t_lead": {"type": "continuous_quantized_1s", "lower": 0, "upper": int(t_lead_ub)},
        "delta_T_thr": {"type": "continuous_quantized_1s", "lower": 0, "upper": int(delta_ub)},
        "G_ext": {"type": "continuous_quantized_1s", "lower": 0, "upper": int(ext_ub)},
        "Q_ratio": {"type": "continuous_quantized_0p01", "lower": 0.0, "upper": 1.0, "step": 0.01},
        "tau": {"type": "continuous_quantized_0p01", "lower": EVTSP_TAU_LOWER, "upper": EVTSP_TAU_UPPER, "step": 0.01},
        "constraints": [
            "delta_T_thr is the Stage 3 ETA gate; 0 disables the gate for legacy smoke runs.",
            "Q_ratio is converted to absolute thresholds as Q_th = Q_ratio * L for movement links and merge zone.",
            f"tau is bounded to [{EVTSP_TAU_LOWER:.2f}, {EVTSP_TAU_UPPER:.2f}] for Case B spillback classification.",
        ],
    }


def compute_tQ_sec(queue_m_proxy: float, lane_count: int) -> float:
    queue_vehicles = queue_m_proxy / TA_HEADWAY_M
    discharge_vps = (TA_SATURATION_FLOW_VPH_PER_LANE * max(lane_count, 1)) / 3600.0
    return round_float(queue_vehicles / max(discharge_vps, 0.001))


def queue_source_from_method(method: str, confidence: float, used_b0_fallback: bool = False) -> str:
    if used_b0_fallback:
        return "b0_fallback"
    if "exact" in method:
        return "runtime_exact"
    if "calibrated" in method:
        return "b0_calibrated"
    return "runtime_proxy"


def compute_ta_proxy(
    *,
    ev_distance_m: float,
    queue_m_proxy: float,
    lane_count: int,
    previous_phase: int,
    target_phase: int,
    queue_confidence: float = QUEUE_PROXY_CONFIDENCE,
    queue_method: str = "lane_proxy",
    b0_tQ_hist_sec: float = 0.0,
    b0_queue_veh: float = 0.0,
    eta_buffer_alpha: float = 1.0,
) -> TAProxyMetrics:
    _ = eta_buffer_alpha
    t_e = max(ev_distance_m, 0.0) / TA_EV_SPEED_MPS
    if previous_phase == target_phase:
        t_s = 0.0
        t_s_source = "current_phase_direct"
    elif previous_phase >= 0 and target_phase >= 0:
        t_s = DEFAULT_PHASE_BUFFER_SEC
        t_s_source = "b0_phase_proxy"
    else:
        t_s = DEFAULT_PHASE_BUFFER_SEC
        t_s_source = "default_buffer"
    runtime_queue_available = queue_m_proxy > 0.0 and queue_confidence > QUEUE_STALE_CONFIDENCE
    used_b0_fallback = False
    queue_m_used = queue_m_proxy
    if not runtime_queue_available and b0_tQ_hist_sec > 0.0:
        t_q = b0_tQ_hist_sec
        used_b0_fallback = True
        queue_m_used = b0_queue_veh * TA_HEADWAY_M if b0_queue_veh > 0.0 else queue_m_proxy
    else:
        t_q = compute_tQ_sec(queue_m_proxy, lane_count)
    ta_proxy = t_e - t_s - t_q
    return TAProxyMetrics(
        tE_sec=round_float(t_e),
        tS_sec=round_float(t_s),
        tQ_sec=round_float(t_q),
        TA_proxy_sec=round_float(ta_proxy),
        ta_triggered=ta_proxy <= 0.0,
        queue_source=queue_source_from_method(queue_method, queue_confidence, used_b0_fallback),
        tS_source=t_s_source,
        queue_m_used=round_float(queue_m_used),
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




def unique_queue_lanes_for_movements(movements: tuple[B4Movement, ...] | list[B4Movement]) -> tuple[str, ...]:
    lanes: set[str] = set()
    for movement in movements:
        if not movement.controllable:
            continue
        lanes.update(movement.approach_lanes)
        lanes.update(movement.local_storage_lanes)
        lanes.update(movement.corridor_storage_lanes)
    return tuple(sorted(lane_id for lane_id in lanes if lane_id))


def sample_lane_snapshots(traci: Any, lanes: tuple[str, ...] | list[str], sample_t: float) -> dict[str, LaneSnapshot]:
    snapshots: dict[str, LaneSnapshot] = {}
    for lane_id in lanes:
        try:
            vehicle_ids = tuple(str(vehicle_id) for vehicle_id in traci.lane.getLastStepVehicleIDs(lane_id))
        except Exception:
            vehicle_ids = tuple()
        try:
            vehicle_count = int(traci.lane.getLastStepVehicleNumber(lane_id))
        except Exception:
            vehicle_count = len(vehicle_ids)
        try:
            mean_speed_mps = float(traci.lane.getLastStepMeanSpeed(lane_id))
        except Exception:
            mean_speed_mps = 0.0
        try:
            halting_count = int(traci.lane.getLastStepHaltingNumber(lane_id))
        except Exception:
            halting_count = 0
        try:
            occupancy = float(traci.lane.getLastStepOccupancy(lane_id))
        except Exception:
            occupancy = 0.0
        try:
            length_m = float(traci.lane.getLength(lane_id))
        except Exception:
            length_m = 100.0
        try:
            waiting_max = float(traci.lane.getWaitingTime(lane_id))
        except Exception:
            waiting_max = 0.0
        try:
            time_loss_max = float(traci.lane.getLastStepTimeLoss(lane_id))
        except Exception:
            time_loss_max = 0.0
        snapshots[lane_id] = LaneSnapshot(
            lane_id=lane_id,
            sample_t=sample_t,
            vehicle_ids=vehicle_ids,
            vehicle_count=vehicle_count,
            halting_count=halting_count,
            mean_speed_mps=mean_speed_mps,
            speed_observed=vehicle_count > 0,
            occupancy=occupancy,
            length_m=length_m,
            waiting_max=waiting_max,
            time_loss_max=time_loss_max,
        )
    return snapshots


def lane_queue_proxy_from_snapshot(snapshot: LaneSnapshot, window_m: float) -> tuple[float, int, int]:
    lane_window = max(min(window_m, snapshot.length_m), 0.0)
    occupancy_len_m = lane_window * clamp_float(snapshot.occupancy, 0.0, 100.0) / 100.0
    low_speed_count = snapshot.vehicle_count if snapshot.speed_observed and snapshot.mean_speed_mps <= LOW_SPEED_MPS else snapshot.halting_count
    halting_len_m = snapshot.halting_count * HEADWAY_M
    slow_len_m = low_speed_count * HEADWAY_M
    queue_m = min(lane_window, max(occupancy_len_m, halting_len_m, slow_len_m))
    queue_veh = max(snapshot.halting_count, low_speed_count, int(math.ceil(queue_m / HEADWAY_M)) if queue_m > 0 else 0)
    return round_float(queue_m), int(queue_veh), int(low_speed_count)


def exact_lane_queue_tail(traci: Any, snapshot: LaneSnapshot, exact_cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if snapshot.lane_id in exact_cache:
        return exact_cache[snapshot.lane_id]
    tail_m = 0.0
    queue_veh = 0
    waiting = snapshot.waiting_max
    time_loss = snapshot.time_loss_max
    for vehicle_id in snapshot.vehicle_ids:
        try:
            speed = float(traci.vehicle.getSpeed(vehicle_id))
        except Exception:
            speed = snapshot.mean_speed_mps
        if speed <= LOW_SPEED_MPS:
            queue_veh += 1
            try:
                pos = float(traci.vehicle.getLanePosition(vehicle_id))
                tail_m = max(tail_m, max(snapshot.length_m - pos, 0.0))
            except Exception:
                tail_m = max(tail_m, queue_veh * HEADWAY_M)
        try:
            waiting = max(waiting, float(traci.vehicle.getWaitingTime(vehicle_id)))
        except Exception:
            pass
        try:
            time_loss = max(time_loss, float(traci.vehicle.getTimeLoss(vehicle_id)))
        except Exception:
            pass
    result = {"tail_m": round_float(tail_m), "queue_veh": queue_veh, "waiting": round_float(waiting), "time_loss": round_float(time_loss)}
    exact_cache[snapshot.lane_id] = result
    return result


def estimate_lane_set_queue_from_snapshots(
    snapshots: dict[str, LaneSnapshot],
    lanes: tuple[str, ...] | list[str],
    storage_length_m: float,
    sample_t: float,
) -> LaneSetQueueMetrics:
    lane_count = max(len(lanes), 1)
    queue_m_values: list[float] = []
    vehicle_count = 0
    queue_veh_proxy = 0
    halting_count = 0
    low_speed_count = 0
    speed_weight = 0
    speed_sum = 0.0
    occupancies: list[float] = []
    waiting = 0.0
    time_loss = 0.0
    missing = 0
    for lane_id in lanes:
        snapshot = snapshots.get(lane_id)
        if snapshot is None:
            missing += 1
            continue
        lane_queue_m, lane_queue_veh, lane_low_speed = lane_queue_proxy_from_snapshot(snapshot, storage_length_m)
        queue_m_values.append(lane_queue_m)
        queue_veh_proxy += lane_queue_veh
        vehicle_count += snapshot.vehicle_count
        halting_count += snapshot.halting_count
        low_speed_count += lane_low_speed
        if snapshot.speed_observed:
            speed_weight += max(snapshot.vehicle_count, 1)
            speed_sum += snapshot.mean_speed_mps * max(snapshot.vehicle_count, 1)
        occupancies.append(snapshot.occupancy)
        waiting = max(waiting, snapshot.waiting_max)
        time_loss = max(time_loss, snapshot.time_loss_max)
    if speed_weight > 0:
        mean_speed_kmh = (speed_sum / speed_weight) * 3.6
        speed_observed = True
    else:
        mean_speed_kmh = EMPTY_APPROACH_SPEED_KMH
        speed_observed = False
    density = vehicle_count / max(lane_count * (storage_length_m / 1000.0), 0.001)
    occupancy = sum(occupancies) / len(occupancies) if occupancies else 0.0
    queue_m_proxy = max(queue_m_values) if queue_m_values else 0.0
    return LaneSetQueueMetrics(
        queue_m_proxy=round_float(queue_m_proxy),
        queue_veh_proxy=queue_veh_proxy,
        vehicle_count=vehicle_count,
        halting_count=halting_count,
        low_speed_count=low_speed_count,
        mean_speed_kmh=round_float(mean_speed_kmh),
        speed_observed=speed_observed,
        density=round_float(density),
        occupancy=round_float(occupancy),
        waiting=round_float(waiting),
        time_loss=round_float(time_loss),
        observed_lane_count=len(lanes) - missing,
        missing_lane_count=missing,
        max_lane_queue_m=round_float(queue_m_proxy),
    )


def lane_edge_id(lane_id: str) -> str:
    if "_" not in lane_id:
        return lane_id
    return lane_id.rsplit("_", 1)[0]


def edge_queue_proxy_from_snapshots(
    snapshots: dict[str, LaneSnapshot],
    edge_id: str,
) -> tuple[float, int, float]:
    edge_lanes = [snapshot for lane_id, snapshot in snapshots.items() if lane_edge_id(lane_id) == edge_id]
    if not edge_lanes:
        return 0.0, 0, 0.0
    queue_m_values: list[float] = []
    speed_weight = 0
    speed_sum = 0.0
    for snapshot in edge_lanes:
        lane_queue_m, _lane_queue_veh, _slow = lane_queue_proxy_from_snapshot(snapshot, snapshot.length_m)
        queue_m_values.append(lane_queue_m)
        if snapshot.speed_observed:
            speed_weight += max(snapshot.vehicle_count, 1)
            speed_sum += snapshot.mean_speed_mps * max(snapshot.vehicle_count, 1)
    mean_speed_kmh = (speed_sum / speed_weight) * 3.6 if speed_weight > 0 else EMPTY_APPROACH_SPEED_KMH
    return round_float(max(queue_m_values) if queue_m_values else 0.0), len(edge_lanes), round_float(mean_speed_kmh)


def route_span_queue_proxy_from_snapshots(
    snapshots: dict[str, LaneSnapshot],
    edge_ids: tuple[str, ...] | list[str],
    storage_length_m: float,
) -> tuple[float, int, int]:
    """Estimate queue over an ordered route span by accumulating per-edge queues."""
    total_queue_m = 0.0
    observed_lane_count = 0
    missing_edge_count = 0
    for edge_id in edge_ids:
        edge_lanes = [snapshot for lane_id, snapshot in snapshots.items() if lane_edge_id(lane_id) == edge_id]
        if not edge_lanes:
            missing_edge_count += 1
            continue
        observed_lane_count += len(edge_lanes)
        edge_queue_m = 0.0
        for snapshot in edge_lanes:
            lane_queue_m, _lane_queue_veh, _slow = lane_queue_proxy_from_snapshot(snapshot, snapshot.length_m)
            storage_fill_m = min(snapshot.length_m, snapshot.vehicle_count * HEADWAY_M)
            edge_queue_m = max(edge_queue_m, lane_queue_m, storage_fill_m)
        total_queue_m += edge_queue_m
    return (
        round_float(min(max(storage_length_m, 0.0), total_queue_m) if storage_length_m > 0.0 else total_queue_m),
        observed_lane_count,
        missing_edge_count,
    )


def estimate_movement_queue_from_snapshots(
    traci: Any,
    movement: B4Movement,
    snapshots: dict[str, LaneSnapshot],
    sample_t: float,
    *,
    ev_distance_m: float | str = "",
    calibration_prior: QueueCalibrationPrior | None = None,
    exact_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[QueueEstimate, LaneSetQueueMetrics, LaneSetQueueMetrics, LaneSetQueueMetrics]:
    exact_cache = exact_cache if exact_cache is not None else {}
    local = estimate_lane_set_queue_from_snapshots(snapshots, movement.local_storage_lanes, movement.stopline_local_storage_m, sample_t)
    corridor = estimate_lane_set_queue_from_snapshots(snapshots, movement.corridor_storage_lanes, movement.corridor_storage_length_m, sample_t)
    approach = estimate_lane_set_queue_from_snapshots(snapshots, movement.approach_lanes, movement.stopline_local_storage_m, sample_t)
    case_b_edges = movement.corridor_storage_edges or movement.local_storage_edges or (movement.from_edge,)
    case_b_queue_m, _case_b_observed_lanes, _case_b_missing_edges = route_span_queue_proxy_from_snapshots(
        snapshots,
        case_b_edges,
        movement.L_m,
    )
    factor = calibration_prior.calibration_factor if calibration_prior is not None else 1.0
    raw_proxy_m = local.queue_m_proxy
    calibrated_proxy_m = min(movement.stopline_local_storage_m, raw_proxy_m * factor)
    method = "calibrated_proxy" if abs(factor - 1.0) > 1e-6 and raw_proxy_m > 0.0 else "lane_proxy"
    confidence = QUEUE_CALIBRATED_CONFIDENCE if method == "calibrated_proxy" else QUEUE_PROXY_CONFIDENCE
    queue_m_est = calibrated_proxy_m
    queue_veh_est = local.queue_veh_proxy
    try_exact = False
    if isinstance(ev_distance_m, (int, float)) and float(ev_distance_m) <= DEFAULT_STAGE3_CONTROL_DISTANCE_M:
        try_exact = True
    if movement.stopline_local_storage_m > 0 and calibrated_proxy_m / movement.stopline_local_storage_m >= QUEUE_LOCAL_EXACT_FILL_TRIGGER:
        try_exact = True
    if try_exact:
        exact_tail = 0.0
        exact_veh = 0
        exact_waiting = local.waiting
        exact_time_loss = local.time_loss
        for lane_id in movement.local_storage_lanes:
            snapshot = snapshots.get(lane_id)
            if snapshot is None:
                continue
            lane_exact = exact_lane_queue_tail(traci, snapshot, exact_cache)
            exact_tail = max(exact_tail, safe_float(lane_exact.get("tail_m")))
            exact_veh += safe_int(lane_exact.get("queue_veh"))
            exact_waiting = max(exact_waiting, safe_float(lane_exact.get("waiting")))
            exact_time_loss = max(exact_time_loss, safe_float(lane_exact.get("time_loss")))
        if exact_tail > 0.0 or exact_veh > 0:
            queue_m_est = min(movement.stopline_local_storage_m, max(calibrated_proxy_m, exact_tail))
            queue_veh_est = max(queue_veh_est, exact_veh)
            local = LaneSetQueueMetrics(
                queue_m_proxy=local.queue_m_proxy,
                queue_veh_proxy=max(local.queue_veh_proxy, exact_veh),
                vehicle_count=local.vehicle_count,
                halting_count=local.halting_count,
                low_speed_count=max(local.low_speed_count, exact_veh),
                mean_speed_kmh=local.mean_speed_kmh,
                speed_observed=local.speed_observed,
                density=local.density,
                occupancy=local.occupancy,
                waiting=round_float(exact_waiting),
                time_loss=round_float(exact_time_loss),
                observed_lane_count=local.observed_lane_count,
                missing_lane_count=local.missing_lane_count,
                max_lane_queue_m=max(local.max_lane_queue_m, round_float(exact_tail)),
            )
            method = "local_exact_calibrated" if method == "calibrated_proxy" else "local_exact"
            confidence = QUEUE_EXACT_CONFIDENCE
    if local.observed_lane_count == 0:
        confidence = QUEUE_STALE_CONFIDENCE
    fill = compute_fill_metrics(queue_m_est, corridor.queue_m_proxy, movement.corridor_storage_length_m)
    estimate = QueueEstimate(
        movement_id=movement.movement_id,
        tls_id=movement.tls_id,
        method=method,
        queue_m_est=round_float(queue_m_est),
        queue_veh_est=int(queue_veh_est),
        queue_proxy_m=round_float(raw_proxy_m),
        fill_80=fill["local_fill_80m"],
        fill_100=fill["local_fill_100m"],
        fill_120=fill["local_fill_120m"],
        fill_250=fill["corridor_fill_250m"],
        mean_speed_kmh=approach.mean_speed_kmh,
        occupancy=max(local.occupancy, corridor.occupancy),
        halting=local.halting_count,
        confidence=round_float(confidence),
        data_age_sec=0.0,
        source_id=(calibration_prior.source if calibration_prior is not None and calibration_prior.source != "none" else QUEUE_RUNTIME_CALL_MODE),
    )
    return estimate, local, corridor, approach, case_b_queue_m


def tls_queue_estimates_from_snapshots(
    movements: tuple[B4Movement, ...] | list[B4Movement],
    snapshots: dict[str, LaneSnapshot],
    sample_t: float,
) -> dict[str, TlsQueueEstimate]:
    tls_lanes: dict[str, set[str]] = {}
    for movement in movements:
        if not movement.controllable:
            continue
        tls_lanes.setdefault(movement.tls_id, set()).update(movement.approach_lanes)
        tls_lanes.setdefault(movement.tls_id, set()).update(movement.local_storage_lanes)
    estimates: dict[str, TlsQueueEstimate] = {}
    for tls_id, lanes in tls_lanes.items():
        queue_sum = 0.0
        queue_veh = 0
        max_back = 0.0
        for lane_id in lanes:
            snapshot = snapshots.get(lane_id)
            if snapshot is None:
                continue
            lane_queue_m, lane_queue_veh, _slow = lane_queue_proxy_from_snapshot(snapshot, min(snapshot.length_m, 100.0))
            queue_sum += lane_queue_m
            queue_veh += lane_queue_veh
            max_back = max(max_back, lane_queue_m)
        estimates[tls_id] = TlsQueueEstimate(
            tls_id=tls_id,
            queue_m_est=round_float(queue_sum),
            queue_veh_est=queue_veh,
            queue_max_back_m=round_float(max_back),
            unique_lane_count=len(lanes),
            source_id=f"tls:{tls_id}:unique_lanes={len(lanes)}",
        )
    return estimates


def movement_runtime_metrics_from_snapshots(
    traci: Any,
    movement: B4Movement,
    thresholds: B4Thresholds,
    snapshots: dict[str, LaneSnapshot],
    sample_t: float,
    *,
    ev_distance_m: float | str = "",
    calibration_prior: QueueCalibrationPrior | None = None,
    exact_cache: dict[str, dict[str, Any]] | None = None,
    tls_estimate: TlsQueueEstimate | None = None,
) -> MovementRuntimeMetrics:
    queue, local, corridor, approach, case_b_queue_m = estimate_movement_queue_from_snapshots(
        traci,
        movement,
        snapshots,
        sample_t,
        ev_distance_m=ev_distance_m,
        calibration_prior=calibration_prior,
        exact_cache=exact_cache,
    )
    approach_speed = approach.mean_speed_kmh
    fast_dense_flow = approach_speed > 30.0 and max(local.density, corridor.density) >= 25.0
    signal_only_delay = approach.speed_observed and approach_speed <= thresholds.speed_trigger_kmh and local.density < 10.0 and local.waiting >= 30.0
    levels = evaluate_queue_levels(
        queue.fill_100,
        queue.fill_250,
        approach_speed,
        thresholds,
        speed_observed=approach.speed_observed,
        fast_dense_flow=fast_dense_flow,
    )
    if bool(levels["bottleneck_risk"]) and queue.fill_250 >= thresholds.bottleneck_corridor_fill_250m and queue.method == "lane_proxy":
        queue_method = "shockwave_pending"
    else:
        queue_method = queue.method
    tls_estimate = tls_estimate or TlsQueueEstimate(tls_id=movement.tls_id)
    return MovementRuntimeMetrics(
        movement=movement,
        queue_m_proxy=queue.queue_m_est,
        corridor_queue_m_proxy=corridor.queue_m_proxy,
        local_fill_80m=queue.fill_80,
        local_fill_100m=queue.fill_100,
        local_fill_120m=queue.fill_120,
        stopline_local_fill_100m=queue.fill_100,
        corridor_fill_250m=queue.fill_250,
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
        queue_method=queue_method,
        queue_m_est=queue.queue_m_est,
        queue_veh_est=queue.queue_veh_est,
        queue_proxy_m=queue.queue_proxy_m,
        queue_confidence=queue.confidence,
        queue_data_age_sec=queue.data_age_sec,
        queue_source_id=queue.source_id,
        tls_queue_m_est=tls_estimate.queue_m_est,
        tls_queue_veh_est=tls_estimate.queue_veh_est,
        tls_queue_max_back_m=tls_estimate.queue_max_back_m,
        case_b_queue_m_proxy=round_float(case_b_queue_m),
    )


def movement_runtime_metrics(traci: Any, movement: B4Movement, thresholds: B4Thresholds) -> MovementRuntimeMetrics:
    sample_t = 0.0
    try:
        sample_t = float(traci.simulation.getTime())
    except Exception:
        pass
    lanes = unique_queue_lanes_for_movements((movement,))
    snapshots = sample_lane_snapshots(traci, lanes, sample_t)
    tls_estimates = tls_queue_estimates_from_snapshots((movement,), snapshots, sample_t)
    return movement_runtime_metrics_from_snapshots(
        traci,
        movement,
        thresholds,
        snapshots,
        sample_t,
        tls_estimate=tls_estimates.get(movement.tls_id),
    )


DEFAULT_MOVEMENT_RUNTIME_METRICS = movement_runtime_metrics


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


def stage2_time_to_merge(
    *,
    now: float | None,
    stage1: B4Stage1Inputs,
    ev_state: EVState | None = None,
    distance_to_merge_m: float | None = None,
) -> tuple[float, str]:
    params = stage1.stage2_merge_hold
    if now is None:
        return params.tE_merge_sec, "fallback_tE_merge_no_time"
    if ev_state is None:
        if now < stage1.ev_depart_sec:
            return max(stage1.ev_depart_sec - now, 0.0) + params.tE_merge_sec, "depart_relative_tE_merge_pre_departure"
        return params.tE_merge_sec, "fallback_tE_merge_no_ev_state"
    if not ev_state.present and now < stage1.ev_depart_sec:
        return max(stage1.ev_depart_sec - now, 0.0) + params.tE_merge_sec, "depart_relative_tE_merge_pre_departure"
    if ev_state.arrived:
        return 0.0, "ev_arrived"
    if ev_state.present and distance_to_merge_m is not None:
        return max(distance_to_merge_m, 0.0) / max(TA_EV_SPEED_MPS, 0.1), "ev_position_distance_over_v_E"
    return params.tE_merge_sec, "fallback_tE_merge_no_ev_distance"


def stage2_merge_hold_proxy_snapshot(
    traci: Any,
    stage1: B4Stage1Inputs,
    *,
    now: float | None = None,
    ev_state: EVState | None = None,
    distance_to_merge_m: float | None = None,
    merged: bool | None = None,
    measurement_scale: float = 1.0,
) -> dict[str, Any]:
    params = stage1.stage2_merge_hold
    merge_lanes = tuple(dict.fromkeys((*stage1.departure.merge_zone_lanes, *stage1.departure.background_inflow_lanes)))
    metrics = lane_storage_metrics(traci, merge_lanes, params.L_merge_m)
    merge_lane_count = max(len(stage1.departure.merge_zone_lanes), 1)
    fallback_label = "runtime"
    scale = max(safe_float(measurement_scale, 1.0), 0.0)
    raw_queue_m = float(metrics.queue_m_proxy)
    raw_n_occ = float(metrics.vehicle_count)
    n_occ = raw_n_occ
    valid_merge_lane_count = 0
    for lane_id in merge_lanes:
        try:
            traci.lane.getLength(lane_id)
            valid_merge_lane_count += 1
        except Exception:
            pass
    if not merge_lanes or valid_merge_lane_count == 0:
        n_occ = max(params.b0_merge_n_occ_max_proxy_veh, params.b0_merge_n_occ_mean_proxy_veh)
        raw_n_occ = n_occ
        fallback_label = "b0_fallback_no_merge_lanes" if not merge_lanes else "b0_fallback_unreadable_merge_lanes"
    scaled_queue_m = raw_queue_m * scale
    scaled_n_occ = n_occ * scale
    n_excess = max(0.0, n_occ - (params.C_merge_proxy_veh - params.n_need_proxy_veh))
    scaled_n_excess = max(0.0, scaled_n_occ - (params.C_merge_proxy_veh - params.n_need_proxy_veh))
    s_vph = TA_SATURATION_FLOW_VPH_PER_LANE * merge_lane_count
    t_clear = scaled_n_excess * 3600.0 / max(s_vph, 1.0)
    time_to_merge, time_to_merge_source = stage2_time_to_merge(
        now=now,
        stage1=stage1,
        ev_state=ev_state,
        distance_to_merge_m=distance_to_merge_m,
    )
    t_hold = time_to_merge - t_clear - params.tS_merge_sec
    ev_depart_sec = round_float(stage1.ev_depart_sec)
    t_rel_depart_sec = round_float(float(now) - stage1.ev_depart_sec) if now is not None else ""
    time_until_depart_sec = round_float(max(stage1.ev_depart_sec - float(now), 0.0)) if now is not None else ""
    dispatch_detect_time_sec = round_float(stage1.ev_depart_sec - params.t_dispatch_delay_sec)
    ev_departed = bool(ev_state.present or ev_state.departed) if ev_state is not None else False
    ev_arrived = bool(ev_state.arrived) if ev_state is not None else False
    ev_merged = bool(merged) if merged is not None else False
    ev_status = "merge_passed" if ev_merged else ("arrived" if ev_arrived else ("departed" if ev_departed else "not_departed"))
    return {
        "D_merge_m": round_float(params.D_merge_m),
        "tE_merge_sec": round_float(params.tE_merge_sec),
        "L_merge_m": round_float(params.L_merge_m),
        "Lq_merge_m": round_float(scaled_queue_m),
        "stage2_measurement_scale": round_float(scale),
        "scaled_Lq_merge_m": round_float(scaled_queue_m),
        "C_merge_proxy_veh": round_float(params.C_merge_proxy_veh),
        "n_need_proxy_veh": round_float(params.n_need_proxy_veh),
        "n_occ_runtime_veh": round_float(scaled_n_occ),
        "scaled_n_occ_runtime_veh": round_float(scaled_n_occ),
        "n_excess_proxy_veh": round_float(scaled_n_excess),
        "t_clear_proxy_sec": round_float(t_clear),
        "time_to_merge_sec": round_float(time_to_merge),
        "time_to_merge_source": time_to_merge_source,
        "ev_depart_sec": ev_depart_sec,
        "t_rel_depart_sec": t_rel_depart_sec,
        "time_until_depart_sec": time_until_depart_sec,
        "dispatch_detect_time_sec": dispatch_detect_time_sec,
        "stage2_time_axis_policy": "depart_relative_pre_depart_time_to_merge",
        "s_vph": round_float(s_vph),
        "tS_merge_sec": round_float(params.tS_merge_sec),
        "HOLD_MAX_sec": round_float(params.HOLD_MAX_sec),
        "T_hold_proxy_sec": round_float(t_hold),
        "EV_NotDeparted": not ev_departed and not ev_arrived,
        "EV_Departed": ev_departed,
        "EV_MergePassed": ev_merged,
        "ev_status": ev_status,
        "b0_merge_n_occ_mean_proxy_veh": round_float(params.b0_merge_n_occ_mean_proxy_veh),
        "b0_merge_n_occ_max_proxy_veh": round_float(params.b0_merge_n_occ_max_proxy_veh),
        "b0_background_inflow_lambda_vph": round_float(params.b0_background_inflow_lambda_vph),
        "stage2_formula": params.stage2_formula,
        "stage2_measurement_source": params.measurement_source,
        "runtime_or_b0_fallback": fallback_label,
        "stage2_scale_status": "ZERO_MEASUREMENT_CANNOT_SCALE" if scale > 1.0 and raw_queue_m <= 0.0 and raw_n_occ <= 0.0 else "SCALED",
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


def order_stage3_candidates(
    metrics: list[MovementRuntimeMetrics],
    current_route_index: int,
    max_active: int,
    d_up: int = 3,
) -> list[MovementRuntimeMetrics]:
    ahead = [
        metric
        for metric in metrics
        if metric.movement.controllable
        and metric.movement.route_order_index >= current_route_index
    ]
    current = [metric for metric in ahead if metric.movement.route_order_index == current_route_index]
    remaining = [metric for metric in ahead if metric.movement.route_order_index != current_route_index]
    remaining.sort(key=lambda metric: metric.movement.route_order_index)
    limit = max_active
    if any(metric.bottleneck_risk for metric in ahead):
        limit = min(max_active, max(1, int(d_up)))
    return (current + remaining)[:limit]


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
    stage2_action: str = "",
    deny_reason: str = "",
    q_ratio: float | str = "",
    q_th_m: float | str = "",
    q_th_merge_m: float | str = "",
    t_hold_sec: float | str = "",
    hold_elapsed_sec: float | str = "",
    green_dur_sec: float | str = "",
    run_id: str = "",
    mode: str = "B4",
    parameter_id: str = B4_PARAMETER_ID,
    repeat_id: int | str = 1,
    tE_sec: float | str = "",
    tS_sec: float | str = "",
    tQ_sec: float | str = "",
    TA_proxy_sec: float | str = "",
    ta_triggered: bool | str = "",
    queue_source: str = "",
    case_b_source: str = "not_case_b",
    tS_source: str = "",
    TA_case: str = "caseA",
    TA_upstream_sec: float | str = "",
    TA_bottleneck_sec: float | str = "",
    case_b_mapping_status: str = "",
    case_b_segment_id: str = "",
    case_b_segment_queue_m_proxy: float | str = "",
    case_b_segment_fill: float | str = "",
    case_b_same_tls_policy: str = "",
    stage2_proxy: dict[str, Any] | None = None,
    stage3_context: dict[str, Any] | None = None,
    monitor_local_fill_mean: float | str = "",
    monitor_speed_mean_kmh: float | str = "",
    monitor_waiting_mean: float | str = "",
    monitor_halting_count: int | str = "",
    termination_reason: str = "",
    step: int | str = "",
) -> dict[str, Any]:
    movement = movement or B4Movement("", "", "", "", tuple(), tuple(), tuple(), tuple(), 0, 0, 0, "", False)
    ev_state = ev_state or EVState(False, False, False, EV_ID)
    stage2_proxy = stage2_proxy or {}
    stage3_context = stage3_context or {}
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
        "step": step if step != "" else safe_int(time),
        "ev_depart_sec": stage2_proxy.get("ev_depart_sec", stage3_context.get("ev_depart_sec", "")),
        "t_rel_depart_sec": stage2_proxy.get("t_rel_depart_sec", stage3_context.get("t_rel_depart_sec", "")),
        "time_until_depart_sec": stage2_proxy.get("time_until_depart_sec", stage3_context.get("time_until_depart_sec", "")),
        "ev_status": stage2_proxy.get("ev_status", "arrived" if ev_state.arrived else ("departed" if ev_state.departed or ev_state.present else "not_departed")),
        "EV_NotDeparted": stage2_proxy.get("EV_NotDeparted", not (ev_state.departed or ev_state.present or ev_state.arrived)),
        "EV_Departed": stage2_proxy.get("EV_Departed", ev_state.departed or ev_state.present),
        "EV_MergePassed": stage2_proxy.get("EV_MergePassed", ""),
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
        "ta_input_source": movement.b0_measurement_source or B4_PRIMARY_EDGE_LANE_SOURCE,
        "queue_source": queue_source,
        "case_b_source": case_b_source,
        "tS_source": tS_source,
        "TA_case": TA_case,
        "TA_upstream_sec": TA_upstream_sec,
        "TA_bottleneck_sec": TA_bottleneck_sec,
        "case_b_mapping_status": case_b_mapping_status,
        "case_b_segment_id": case_b_segment_id,
        "case_b_segment_queue_m_proxy": case_b_segment_queue_m_proxy,
        "case_b_segment_fill": case_b_segment_fill,
        "case_b_same_tls_policy": case_b_same_tls_policy,
        "D_merge_m": stage2_proxy.get("D_merge_m", ""),
        "tE_merge_sec": stage2_proxy.get("tE_merge_sec", ""),
        "L_merge_m": stage2_proxy.get("L_merge_m", ""),
        "Lq_merge_m": stage2_proxy.get("Lq_merge_m", ""),
        "stage2_measurement_scale": stage2_proxy.get("stage2_measurement_scale", ""),
        "scaled_Lq_merge_m": stage2_proxy.get("scaled_Lq_merge_m", ""),
        "C_merge_proxy_veh": stage2_proxy.get("C_merge_proxy_veh", ""),
        "n_need_proxy_veh": stage2_proxy.get("n_need_proxy_veh", ""),
        "n_occ_runtime_veh": stage2_proxy.get("n_occ_runtime_veh", ""),
        "scaled_n_occ_runtime_veh": stage2_proxy.get("scaled_n_occ_runtime_veh", ""),
        "n_excess_proxy_veh": stage2_proxy.get("n_excess_proxy_veh", ""),
        "n_queue_from_Lq_proxy_veh": stage2_proxy.get("n_queue_from_Lq_proxy_veh", ""),
        "n_blocking_proxy_veh": stage2_proxy.get("n_blocking_proxy_veh", ""),
        "merge_space_deficit_threshold_m": stage2_proxy.get("merge_space_deficit_threshold_m", ""),
        "merge_space_deficit": stage2_proxy.get("merge_space_deficit", ""),
        "t_clear_proxy_sec": stage2_proxy.get("t_clear_proxy_sec", ""),
        "time_to_merge_sec": stage2_proxy.get("time_to_merge_sec", ""),
        "time_to_merge_source": stage2_proxy.get("time_to_merge_source", ""),
        "dispatch_detect_time_sec": stage2_proxy.get("dispatch_detect_time_sec", ""),
        "s_vph": stage2_proxy.get("s_vph", ""),
        "tS_merge_sec": stage2_proxy.get("tS_merge_sec", ""),
        "HOLD_MAX_sec": stage2_proxy.get("HOLD_MAX_sec", ""),
        "current_phase": stage2_proxy.get("current_phase", ""),
        "current_state": stage2_proxy.get("current_state", ""),
        "ped_state": stage2_proxy.get("ped_state", ""),
        "SafetyGate_result": stage2_proxy.get("SafetyGate_result", stage3_context.get("SafetyGate_result", safety_status)),
        "action": stage2_proxy.get("action", stage3_context.get("action", stage2_action)),
        "deny_reason": stage2_proxy.get(
            "deny_reason",
            stage3_context.get("deny_reason", deny_reason or (safety_status if str(safety_status).startswith("DENY") else "")),
        ),
        "T_hold_proxy_sec": stage2_proxy.get("T_hold_proxy_sec", ""),
        "b0_merge_n_occ_mean_proxy_veh": stage2_proxy.get("b0_merge_n_occ_mean_proxy_veh", ""),
        "b0_merge_n_occ_max_proxy_veh": stage2_proxy.get("b0_merge_n_occ_max_proxy_veh", ""),
        "b0_background_inflow_lambda_vph": stage2_proxy.get("b0_background_inflow_lambda_vph", ""),
        "stage2_formula": stage2_proxy.get("stage2_formula", ""),
        "stage2_time_axis_policy": stage2_proxy.get("stage2_time_axis_policy", ""),
        "stage2_measurement_source": stage2_proxy.get("stage2_measurement_source", ""),
        "runtime_or_b0_fallback": stage2_proxy.get("runtime_or_b0_fallback", ""),
        "low_speed_count": metrics.low_speed_count if metrics else "",
        "halting_count": metrics.halting_count if metrics else "",
        "fast_dense_flow": metrics.fast_dense_flow if metrics else "",
        "signal_only_delay": metrics.signal_only_delay if metrics else "",
        "active_movement_count": active_movement_count,
        "phase_duration_sec": phase_duration_sec,
        "stage2_hold_status": stage2_hold_status,
        "Q_ratio": q_ratio,
        "Q_th_m": q_th_m,
        "Q_th_merge_m": q_th_merge_m,
        "T_hold_sec": t_hold_sec,
        "hold_elapsed_sec": hold_elapsed_sec,
        "route_intersection_index": movement.route_intersection_index,
        "L_m": round_float(movement.L_m),
        "W_m": round_float(movement.W_m),
        "intersection_index": stage3_context.get("intersection_index", movement.route_intersection_index),
        "junction_id": stage3_context.get("junction_id", movement.tls_id),
        "is_ahead_of_ev": stage3_context.get("is_ahead_of_ev", ""),
        "is_i_merge": stage3_context.get("is_i_merge", movement.is_merge),
        "L": stage3_context.get("L", round_float(movement.L_m)),
        "W": stage3_context.get("W", round_float(movement.W_m)),
        "Lq": stage3_context.get("Lq", metrics.queue_m_proxy if metrics else ""),
        "stage3_measurement_scale": stage3_context.get("stage3_measurement_scale", ""),
        "scaled_Lq_case_b_m": stage3_context.get("scaled_Lq_case_b_m", ""),
        "tau": stage3_context.get("tau", ""),
        "tau_times_L": stage3_context.get("tau_times_L", ""),
        "case_type": stage3_context.get("case_type", ""),
        "downstream_index": stage3_context.get("downstream_index", ""),
        "gate_target": stage3_context.get("gate_target", ""),
        "tE_gate_target": stage3_context.get("tE_gate_target", ""),
        "tS_gate_sec": stage3_context.get("tS_gate_sec", ""),
        "tE_gate_effective_sec": stage3_context.get("tE_gate_effective_sec", ""),
        "delta_T_thr": stage3_context.get("delta_T_thr", ""),
        "gate_result": stage3_context.get("gate_result", ""),
        "ge": stage3_context.get("ge", ""),
        "tQ": stage3_context.get("tQ", tQ_sec),
        "t_lead": stage3_context.get("t_lead", ""),
        "G_ext": stage3_context.get("G_ext", ""),
        "preemption_state": stage3_context.get("preemption_state", ""),
        "processing_order": stage3_context.get("processing_order", ""),
        "Lq_i": stage3_context.get("Lq_i", ""),
        "TA_down": stage3_context.get("TA_down", ""),
        "tQ_i": stage3_context.get("tQ_i", ""),
        "Gm_sec": round_float(movement.Gm_sec),
        "Y_sec": round_float(movement.Y_sec),
        "R_sec": round_float(movement.R_sec),
        "green_dur_sec": green_dur_sec,
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
    if metrics is not None:
        row.update({
            "queue_method": metrics.queue_method,
            "queue_m_est": round_float(metrics.queue_m_est),
            "queue_veh_est": metrics.queue_veh_est,
            "queue_proxy_m": round_float(metrics.queue_proxy_m),
            "queue_confidence": round_float(metrics.queue_confidence),
            "queue_data_age_sec": round_float(metrics.queue_data_age_sec),
            "queue_source_id": metrics.queue_source_id,
            "tls_queue_m_est": round_float(metrics.tls_queue_m_est),
            "tls_queue_veh_est": metrics.tls_queue_veh_est,
            "tls_queue_max_back_m": round_float(metrics.tls_queue_max_back_m),
        })
    return {field: row.get(field, "") for field in RUNTIME_EVENT_FIELDS}


@dataclass
class B4RuntimeController:
    traci: Any
    stage1: B4Stage1Inputs
    params: B4MvpParams = field(default_factory=B4ThetaParams)
    run_id: str = ""
    repeat_id: int = 1
    stage2_measurement_scale: float = DEFAULT_STAGE2_MEASUREMENT_SCALE
    stage3_measurement_scale: float = DEFAULT_STAGE3_MEASUREMENT_SCALE
    edge_lengths: dict[str, float] = field(default_factory=load_edge_lengths)
    events: list[dict[str, Any]] = field(default_factory=list)
    stats: B4ControllerStats = field(default_factory=B4ControllerStats)
    phases_by_tls: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: tl_logic_details(B04_NET))
    stage2_hold_active: bool = False
    stage2_completed: bool = False
    stage2_hold_clearance_pending: bool = False
    stage2_release_clearance_pending: bool = False
    stage2_hold_start: float | None = None
    stage2_hold_clearance_start: float | None = None
    stage2_previous_phase: int | None = None
    stage2_gate_history: list[dict[str, float]] = field(default_factory=list)
    active_controls: dict[str, ActiveControl] = field(default_factory=dict)
    pending_stage3_requests: dict[str, float] = field(default_factory=dict)
    last_tls_action_at: dict[str, float] = field(default_factory=dict)
    queue_unique_lanes: tuple[str, ...] = field(default_factory=tuple)
    pedestrian_min_green_by_tls: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.pedestrian_min_green_by_tls:
            self.pedestrian_min_green_by_tls = self.stage1.pedestrian_min_green_by_tls()

    def stage3_queue_lanes(self) -> tuple[str, ...]:
        if not self.queue_unique_lanes:
            lanes = list(unique_queue_lanes_for_movements(self.stage1.movements))
            for candidate in self.stage1.case_b_candidates:
                if candidate.mapped:
                    lanes.extend(candidate.segment_lanes)
            self.queue_unique_lanes = tuple(sorted({lane_id for lane_id in lanes if lane_id}))
        return self.queue_unique_lanes

    def case_b_tau(self, candidate: B4CaseBCandidate) -> float:
        param_tau = safe_float(getattr(self.params, "tau", candidate.tau_default), candidate.tau_default)
        return clamp_float(param_tau, EVTSP_TAU_LOWER, EVTSP_TAU_UPPER)

    def phase_green_link_count(self, tls_id: str, phase_index: int, link_indices: tuple[int, ...]) -> int:
        if not link_indices:
            return 0
        state = self.phase_state(tls_id, phase_index)
        return sum(
            1
            for link_index in link_indices
            if 0 <= link_index < len(state) and state[link_index] in {"G", "g"}
        )

    def phase_covers_green_links(self, tls_id: str, phase_index: int, link_indices: tuple[int, ...]) -> bool:
        return bool(link_indices) and self.phase_green_link_count(tls_id, phase_index, link_indices) == len(link_indices)

    def scaled_stage3_case_b_queue_m(self, metric: MovementRuntimeMetrics) -> float:
        scale = max(safe_float(self.stage3_measurement_scale, 1.0), 0.0)
        case_b_queue_m = safe_float(metric.case_b_queue_m_proxy, 0.0)
        if case_b_queue_m <= 0.0:
            case_b_queue_m = metric.queue_m_proxy
        return max(case_b_queue_m, metric.queue_m_proxy) * scale

    def case_b_segment_metrics_from_snapshots(
        self,
        lane_snapshots: dict[str, LaneSnapshot],
        now: float,
    ) -> dict[str, CaseBSegmentRuntimeMetrics]:
        result: dict[str, CaseBSegmentRuntimeMetrics] = {}
        for candidate in self.stage1.case_b_candidates:
            if not candidate.mapped:
                continue
            segment_queue = estimate_lane_set_queue_from_snapshots(lane_snapshots, candidate.segment_lanes, candidate.L_b0_m, now)
            span_queue_m, span_observed_lanes, span_missing_edges = route_span_queue_proxy_from_snapshots(
                lane_snapshots,
                candidate.segment_edges,
                candidate.L_b0_m,
            )
            queue_m_proxy = max(span_queue_m, segment_queue.queue_m_proxy) if span_observed_lanes > 0 else segment_queue.queue_m_proxy
            observed_count = span_observed_lanes if span_observed_lanes > 0 else segment_queue.observed_lane_count
            missing_count = span_missing_edges if span_observed_lanes > 0 else segment_queue.missing_lane_count
            confidence = QUEUE_PROXY_CONFIDENCE if observed_count > 0 else QUEUE_STALE_CONFIDENCE
            result[candidate.segment_id] = CaseBSegmentRuntimeMetrics(
                segment_id=candidate.segment_id,
                queue_m_proxy=round_float(queue_m_proxy),
                fill=round_float(queue_m_proxy / max(candidate.L_b0_m, 0.001)),
                queue_confidence=confidence,
                observed_lane_count=observed_count,
                missing_lane_count=missing_count,
            )
        return result

    def case_b_source_for_metric(
        self,
        metric: MovementRuntimeMetrics,
        candidate: B4CaseBCandidate,
        segment_metric: CaseBSegmentRuntimeMetrics | None = None,
    ) -> str:
        if not candidate.mapped or candidate.L_b0_m <= 0.0:
            return "not_case_b"
        if metric.movement.is_merge:
            return "not_case_b"
        scale = max(safe_float(self.stage3_measurement_scale, 1.0), 0.0)
        if segment_metric is not None and segment_metric.queue_confidence > QUEUE_STALE_CONFIDENCE and segment_metric.queue_m_proxy > 0.0:
            return "runtime_tau_segment" if segment_metric.fill * scale >= self.case_b_tau(candidate) else "not_case_b"
        runtime_queue_available = metric.queue_confidence > QUEUE_STALE_CONFIDENCE and metric.queue_m_proxy > 0.0
        if runtime_queue_available:
            fill_ratio = max(safe_float(metric.case_b_queue_m_proxy, 0.0), metric.queue_m_proxy) * scale / max(candidate.L_b0_m, 0.001)
            return "runtime_tau_movement" if fill_ratio >= self.case_b_tau(candidate) else "not_case_b"
        return "not_case_b"

    def case_b_duplicate_score(
        self,
        candidate: B4CaseBCandidate,
        bottleneck_metric: MovementRuntimeMetrics,
        segment_metric: CaseBSegmentRuntimeMetrics | None = None,
    ) -> tuple[float, float, float, str]:
        if segment_metric is not None and segment_metric.queue_confidence > QUEUE_STALE_CONFIDENCE:
            live_fill = segment_metric.fill * max(safe_float(self.stage3_measurement_scale, 1.0), 0.0)
        elif bottleneck_metric.queue_confidence > QUEUE_STALE_CONFIDENCE and bottleneck_metric.queue_m_proxy > 0.0:
            case_b_queue_m = max(safe_float(bottleneck_metric.case_b_queue_m_proxy, 0.0), bottleneck_metric.queue_m_proxy)
            live_fill = case_b_queue_m * max(safe_float(self.stage3_measurement_scale, 1.0), 0.0) / max(candidate.L_b0_m, 0.001)
        else:
            live_fill = -1.0
        b0_fill = max(candidate.segment_fill_B0, candidate.fill_B0)
        return (round_float(live_fill), round_float(b0_fill), round_float(candidate.L_b0_m), candidate.segment_id)

    def active_case_b_candidate(
        self,
        metric: MovementRuntimeMetrics,
        metrics_by_id: dict[str, MovementRuntimeMetrics],
        segment_metrics_by_id: dict[str, CaseBSegmentRuntimeMetrics] | None = None,
    ) -> tuple[B4CaseBCandidate | None, str]:
        segment_metrics_by_id = segment_metrics_by_id or {}
        matches: list[tuple[tuple[float, float, float, str], B4CaseBCandidate, str]] = []
        for candidate in self.stage1.case_b_candidates:
            if not candidate.mapped:
                continue
            if metric.movement.movement_id not in {candidate.bottleneck_movement_id, candidate.upstream_movement_id}:
                continue
            bottleneck_metric = metrics_by_id.get(candidate.bottleneck_movement_id)
            if bottleneck_metric is None:
                continue
            source = self.case_b_source_for_metric(
                bottleneck_metric,
                candidate,
                segment_metrics_by_id.get(candidate.segment_id),
            )
            if source != "not_case_b":
                matches.append((
                    self.case_b_duplicate_score(candidate, bottleneck_metric, segment_metrics_by_id.get(candidate.segment_id)),
                    candidate,
                    source,
                ))
        if matches:
            _score, candidate, source = max(matches, key=lambda item: item[0])
            return candidate, source
        return None, "not_case_b"

    def order_case_b_candidates(
        self,
        selected: list[MovementRuntimeMetrics],
        metrics_by_id: dict[str, MovementRuntimeMetrics],
        current_route_index: int,
        max_active: int,
        segment_metrics_by_id: dict[str, CaseBSegmentRuntimeMetrics] | None = None,
    ) -> list[MovementRuntimeMetrics]:
        segment_metrics_by_id = segment_metrics_by_id or {}
        ordered: list[MovementRuntimeMetrics] = []
        seen: set[str] = set()
        active_candidates: list[tuple[tuple[float, float, float, str], B4CaseBCandidate]] = []
        for candidate in self.stage1.case_b_candidates:
            if not candidate.mapped:
                continue
            bottleneck_metric = metrics_by_id.get(candidate.bottleneck_movement_id)
            upstream_metric = metrics_by_id.get(candidate.upstream_movement_id)
            if bottleneck_metric is None or upstream_metric is None:
                continue
            if self.case_b_source_for_metric(
                bottleneck_metric,
                candidate,
                segment_metrics_by_id.get(candidate.segment_id),
            ) == "not_case_b":
                continue
            active_candidates.append((
                self.case_b_duplicate_score(candidate, bottleneck_metric, segment_metrics_by_id.get(candidate.segment_id)),
                candidate,
            ))
        for _score, candidate in sorted(active_candidates, key=lambda item: item[0], reverse=True):
            bottleneck_metric = metrics_by_id[candidate.bottleneck_movement_id]
            upstream_metric = metrics_by_id[candidate.upstream_movement_id]
            for metric in (bottleneck_metric, upstream_metric):
                movement = metric.movement
                if not movement.controllable or movement.route_order_index < current_route_index:
                    continue
                if movement.movement_id in seen:
                    continue
                ordered.append(metric)
                seen.add(movement.movement_id)
        for metric in selected:
            movement_id = metric.movement.movement_id
            if movement_id in seen:
                continue
            ordered.append(metric)
            seen.add(movement_id)
        return ordered[:max_active]

    def stage3_ahead_metrics(self, metrics: list[MovementRuntimeMetrics], ev_state: EVState) -> list[MovementRuntimeMetrics]:
        return sorted(
            (
                metric
                for metric in metrics
                if metric.movement.controllable
                and metric.movement.route_order_index >= ev_state.route_index
                and not metric.movement.is_merge
                and metric.movement.route_intersection_index != self.stage1.i_merge
            ),
            key=lambda metric: metric.movement.route_intersection_index,
        )

    def stage3_case_plan(
        self,
        metric: MovementRuntimeMetrics,
        metrics_by_id: dict[str, MovementRuntimeMetrics],
    ) -> Stage3CasePlan:
        movement = metric.movement
        tau = clamp_float(safe_float(getattr(self.params, "tau", EVTSP_DEFAULT_TAU), EVTSP_DEFAULT_TAU), EVTSP_TAU_LOWER, EVTSP_TAU_UPPER)
        is_case_b = (
            not movement.is_merge
            and movement.route_intersection_index != self.stage1.i_merge
            and self.scaled_stage3_case_b_queue_m(metric) >= tau * movement.L_m
        )
        if not is_case_b:
            return Stage3CasePlan(
                case_type="caseA",
                source_metric=metric,
                processing_metrics=(metric,),
                gate_metric=metric,
                processing_order=str(movement.route_intersection_index),
            )
        downstream_index = movement.route_intersection_index + 1
        downstream_metric = next(
            (
                candidate
                for candidate in metrics_by_id.values()
                if candidate.movement.route_intersection_index == downstream_index
                and candidate.movement.controllable
                and not candidate.movement.is_merge
                and candidate.movement.route_intersection_index != self.stage1.i_merge
            ),
            None,
        )
        if downstream_metric is None:
            return Stage3CasePlan(
                case_type="caseA_boundary",
                source_metric=metric,
                processing_metrics=(metric,),
                gate_metric=metric,
                downstream_index=downstream_index,
                processing_order=str(movement.route_intersection_index),
            )
        return Stage3CasePlan(
            case_type="caseB",
            source_metric=metric,
            processing_metrics=(downstream_metric, metric),
            gate_metric=downstream_metric,
            downstream_index=downstream_index,
            processing_order=f"{downstream_metric.movement.route_intersection_index},{movement.route_intersection_index}",
        )

    def stage3_case_plans(
        self,
        ahead_metrics: list[MovementRuntimeMetrics],
        metrics_by_id: dict[str, MovementRuntimeMetrics],
    ) -> list[Stage3CasePlan]:
        plans: list[Stage3CasePlan] = []
        planned: set[str] = set()
        for metric in ahead_metrics:
            if metric.movement.movement_id in planned:
                continue
            plan = self.stage3_case_plan(metric, metrics_by_id)
            plans.append(plan)
            for processing_metric in plan.processing_metrics:
                planned.add(processing_metric.movement.movement_id)
        return plans

    def stage3_gate_tE(self, plan: Stage3CasePlan, ev_distances: dict[str, float | str]) -> float | str:
        gate_distance = ev_distances.get(plan.gate_metric.movement.movement_id, "")
        if gate_distance == "":
            return ""
        return round_float(max(float(gate_distance), 0.0) / TA_EV_SPEED_MPS)

    def stage3_gate_effective_tE(self, plan: Stage3CasePlan, ev_distances: dict[str, float | str]) -> tuple[float | str, float | str]:
        gate_t_e = self.stage3_gate_tE(plan, ev_distances)
        if gate_t_e == "":
            return "", ""
        gate_movement = plan.gate_metric.movement
        transition_loss = self.stage2_effective_transition_loss_sec(
            self.traci.simulation.getTime(),
            gate_movement.tls_id,
            gate_movement.selected_green_phase,
        )
        effective_t_e = max(safe_float(gate_t_e, 0.0) - transition_loss, 0.0)
        return round_float(effective_t_e), round_float(transition_loss)

    def stage3_ta_for_plan(
        self,
        plan: Stage3CasePlan,
        metric: MovementRuntimeMetrics,
        ev_distances: dict[str, float | str],
        previous_phase: int | None = None,
    ) -> tuple[TAProxyMetrics, CaseBEvaluation, float]:
        base_ta = self.metric_ta(metric, ev_distances, previous_phase)
        if plan.case_type != "caseB":
            return base_ta, CaseBEvaluation(TA_case="caseA"), base_ta.TA_proxy_sec
        upstream_metric = plan.source_metric
        downstream_metric = plan.gate_metric
        downstream_ta = self.metric_ta(downstream_metric, ev_distances)
        upstream_ta = self.metric_ta(upstream_metric, ev_distances)
        effective_ta = downstream_ta.TA_proxy_sec - upstream_ta.tQ_sec
        case_b = CaseBEvaluation(
            case_b_source="runtime_tau_adjacency",
            TA_case="caseB_downstream_first",
            TA_upstream_sec=round_float(upstream_ta.TA_proxy_sec),
            TA_bottleneck_sec=round_float(downstream_ta.TA_proxy_sec),
            effective_TA_proxy_sec=round_float(effective_ta),
            case_b_mapping_status="adjacent_i_plus_1",
            case_b_segment_id=f"i{upstream_metric.movement.route_intersection_index}_down{downstream_metric.movement.route_intersection_index}",
            case_b_segment_queue_m_proxy=round_float(self.scaled_stage3_case_b_queue_m(upstream_metric)),
            case_b_segment_fill=round_float(self.scaled_stage3_case_b_queue_m(upstream_metric) / max(upstream_metric.movement.L_m, 0.001)),
            case_b_same_tls_policy="downstream_first",
        )
        return base_ta, case_b, round_float(effective_ta)

    def stage3_log_context(
        self,
        *,
        now: float,
        plan: Stage3CasePlan,
        metric: MovementRuntimeMetrics,
        ev_state: EVState,
        gate_tE: float | str,
        gate_effective_tE: float | str,
        gate_tS: float | str,
        gate_result: str,
        previous_phase: int | None,
        ta: TAProxyMetrics,
        case_b: CaseBEvaluation,
        ta_proxy_sec: float | str,
        preemption_state: str,
        action: str,
        safety_status: str = "",
        deny_reason: str = "",
    ) -> dict[str, Any]:
        movement = metric.movement
        tau = clamp_float(safe_float(getattr(self.params, "tau", EVTSP_DEFAULT_TAU), EVTSP_DEFAULT_TAU), EVTSP_TAU_LOWER, EVTSP_TAU_UPPER)
        elapsed_green = self.elapsed_green_sec(movement.tls_id) if previous_phase == movement.selected_green_phase else 0.0
        upstream_metric = plan.source_metric
        downstream_metric = plan.gate_metric
        t_rel_depart_sec = float(now) - self.stage1.ev_depart_sec
        return {
            "ev_depart_sec": round_float(self.stage1.ev_depart_sec),
            "t_rel_depart_sec": round_float(t_rel_depart_sec),
            "time_until_depart_sec": round_float(max(-t_rel_depart_sec, 0.0)),
            "intersection_index": movement.route_intersection_index,
            "junction_id": movement.tls_id,
            "is_ahead_of_ev": movement.route_order_index >= ev_state.route_index,
            "is_i_merge": movement.is_merge or movement.route_intersection_index == self.stage1.i_merge,
            "L": round_float(movement.L_m),
            "W": round_float(movement.W_m),
            "Lq": round_float(metric.queue_m_proxy),
            "stage3_measurement_scale": round_float(max(safe_float(self.stage3_measurement_scale, 1.0), 0.0)),
            "scaled_Lq_case_b_m": round_float(self.scaled_stage3_case_b_queue_m(metric)),
            "tau": round_float(tau),
            "tau_times_L": round_float(tau * movement.L_m),
            "case_type": plan.case_type,
            "downstream_index": plan.downstream_index,
            "gate_target": plan.gate_metric.movement.route_intersection_index,
            "tE_gate_target": gate_tE,
            "tS_gate_sec": gate_tS,
            "tE_gate_effective_sec": gate_effective_tE,
            "delta_T_thr": safe_float(getattr(self.params, "delta_T_thr", 0.0), 0.0),
            "gate_result": gate_result,
            "ge": round_float(elapsed_green),
            "tQ": ta.tQ_sec,
            "t_lead": theta_ta_lead_sec(self.params),
            "G_ext": safe_float(getattr(self.params, "G_ext", 0.0), 0.0),
            "SafetyGate_result": safety_status,
            "deny_reason": deny_reason,
            "preemption_state": preemption_state,
            "action": action,
            "processing_order": plan.processing_order,
            "Lq_i": round_float(upstream_metric.queue_m_proxy) if plan.case_type == "caseB" else "",
            "TA_down": case_b.TA_bottleneck_sec if plan.case_type == "caseB" else "",
            "tQ_i": self.metric_ta(upstream_metric, {upstream_metric.movement.movement_id: 0.0}).tQ_sec if plan.case_type == "caseB" else "",
            "TA_formula": "TA = TA_down - tQ_i" if plan.case_type == "caseB" else "TA = tE - tS - tQ",
            "TA": ta_proxy_sec,
        }

    def metric_ta(
        self,
        metric: MovementRuntimeMetrics,
        ev_distances: dict[str, float | str],
        previous_phase: int | None = None,
    ) -> TAProxyMetrics:
        movement = metric.movement
        if previous_phase is None:
            previous_phase = self.get_tls_phase(movement.tls_id)
        ev_distance = ev_distances.get(movement.movement_id, "")
        t_e = max(float(ev_distance) if ev_distance != "" else 0.0, 0.0) / TA_EV_SPEED_MPS
        elapsed_green = self.elapsed_green_sec(movement.tls_id) if previous_phase == movement.selected_green_phase else 0.0
        t_s = movement.Y_sec + movement.R_sec + max(0.0, movement.Gm_sec - elapsed_green)
        runtime_queue_available = metric.queue_m_proxy > 0.0 and metric.queue_confidence > QUEUE_STALE_CONFIDENCE
        used_b0_fallback = False
        if not runtime_queue_available and movement.tQ_hist_b0_sec > 0.0:
            t_q = movement.tQ_hist_b0_sec
            used_b0_fallback = True
        else:
            t_q = compute_tQ_sec(metric.queue_m_proxy, movement.lane_count)
        ta_proxy = t_e - t_s - t_q
        return TAProxyMetrics(
            tE_sec=round_float(t_e),
            tS_sec=round_float(t_s),
            tQ_sec=round_float(t_q),
            TA_proxy_sec=round_float(ta_proxy),
            ta_triggered=ta_proxy <= theta_ta_lead_sec(self.params),
            queue_source=queue_source_from_method(metric.queue_method or "lane_proxy", metric.queue_confidence, used_b0_fallback),
            tS_source="evtsp_safety_clearance",
            queue_m_used=round_float(metric.queue_m_proxy),
        )

    def case_b_evaluation(
        self,
        metric: MovementRuntimeMetrics,
        metrics_by_id: dict[str, MovementRuntimeMetrics],
        ev_distances: dict[str, float | str],
        base_ta: TAProxyMetrics,
        segment_metrics_by_id: dict[str, CaseBSegmentRuntimeMetrics] | None = None,
    ) -> CaseBEvaluation:
        segment_metrics_by_id = segment_metrics_by_id or {}
        candidate, source = self.active_case_b_candidate(metric, metrics_by_id, segment_metrics_by_id)
        if candidate is None:
            return CaseBEvaluation()
        upstream_metric = metrics_by_id.get(candidate.upstream_movement_id)
        bottleneck_metric = metrics_by_id.get(candidate.bottleneck_movement_id)
        if upstream_metric is None or bottleneck_metric is None:
            return CaseBEvaluation()
        if metric.movement.movement_id == candidate.upstream_movement_id:
            upstream_ta = base_ta
            bottleneck_ta = self.metric_ta(bottleneck_metric, ev_distances)
            ta_case = "caseB_upstream"
            effective_ta = bottleneck_ta.TA_proxy_sec - upstream_ta.tQ_sec
        elif metric.movement.movement_id == candidate.bottleneck_movement_id:
            upstream_ta = self.metric_ta(upstream_metric, ev_distances)
            bottleneck_ta = base_ta
            ta_case = "caseB_downstream"
            effective_ta = bottleneck_ta.TA_proxy_sec
        else:
            return CaseBEvaluation()
        ta_bottleneck = bottleneck_ta.TA_proxy_sec
        segment_metric = segment_metrics_by_id.get(candidate.segment_id)
        same_tls_policy = ""
        if candidate.same_tls_chain and upstream_metric.movement.selected_green_phase != bottleneck_metric.movement.selected_green_phase:
            same_tls_policy = "bottleneck_first_defer_upstream_same_tls"
        elif candidate.same_tls_chain:
            same_tls_policy = "same_tls_same_phase"
        else:
            same_tls_policy = "different_tls_or_same_phase"
        return CaseBEvaluation(
            case_b_source=source,
            TA_case=ta_case,
            TA_upstream_sec=round_float(upstream_ta.TA_proxy_sec),
            TA_bottleneck_sec=round_float(ta_bottleneck),
            effective_TA_proxy_sec=round_float(effective_ta),
            case_b_mapping_status=candidate.mapping_status,
            case_b_segment_id=candidate.segment_id,
            case_b_segment_queue_m_proxy=round_float(segment_metric.queue_m_proxy) if segment_metric is not None else "",
            case_b_segment_fill=round_float(segment_metric.fill) if segment_metric is not None else "",
            case_b_same_tls_policy=same_tls_policy,
        )

    def event_case_b_source(self, metric: MovementRuntimeMetrics, case_b: CaseBEvaluation) -> str:
        _ = metric
        return case_b.case_b_source

    def same_tls_case_b_deferred(
        self,
        metric: MovementRuntimeMetrics,
        metrics_by_id: dict[str, MovementRuntimeMetrics],
        segment_metrics_by_id: dict[str, CaseBSegmentRuntimeMetrics],
    ) -> bool:
        for candidate in self.stage1.case_b_candidates:
            if (
                not candidate.mapped
                or not candidate.same_tls_chain
                or metric.movement.movement_id != candidate.upstream_movement_id
                or candidate.bottleneck_movement_id not in self.active_controls
            ):
                continue
            bottleneck_metric = metrics_by_id.get(candidate.bottleneck_movement_id)
            if bottleneck_metric is None:
                continue
            if metric.movement.selected_green_phase == bottleneck_metric.movement.selected_green_phase:
                continue
            source = self.case_b_source_for_metric(
                bottleneck_metric,
                candidate,
                segment_metrics_by_id.get(candidate.segment_id),
            )
            if source != "not_case_b":
                return True
        return False

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

    def distance_to_merge_m(self, ev_state: EVState) -> float | None:
        if not ev_state.present:
            return None
        merge_index = self.route_index_for_edge(self.stage1.departure.mainline_target_edge)
        if merge_index < 0:
            return None
        current_index = ev_state.route_index if ev_state.route_index >= 0 else self.route_index_for_edge(ev_state.edge_id)
        if current_index < 0:
            return None
        if current_index >= merge_index:
            return 0.0
        current_edge = self.stage1.route_edges[current_index]
        current_length = safe_float(self.edge_lengths.get(current_edge), 0.0)
        if current_length <= 0.0:
            return None
        distance = max(current_length - ev_state.lane_position_m, 0.0)
        for edge_id in self.stage1.route_edges[current_index + 1 : merge_index]:
            distance += max(safe_float(self.edge_lengths.get(edge_id), 0.0), 0.0)
        return round_float(distance)

    def tls_state(self, tls_id: str) -> str:
        try:
            return str(self.traci.trafficlight.getRedYellowGreenState(tls_id))
        except Exception:
            return self.phase_state(tls_id, self.get_tls_phase(tls_id))

    def stage2_proxy_with_signal_context(
        self,
        stage2_proxy: dict[str, Any],
        tls_id: str,
        *,
        action: str = "",
        safety_status: str = "",
        deny_reason: str = "",
    ) -> dict[str, Any]:
        proxy = dict(stage2_proxy)
        current_phase = self.get_tls_phase(tls_id)
        proxy.update({
            "current_phase": current_phase,
            "current_state": self.tls_state(tls_id),
            "ped_state": "configured_min_green" if safe_float(self.pedestrian_min_green_by_tls.get(tls_id), 0.0) > 0.0 else "no_ped_min_green_configured",
            "SafetyGate_result": safety_status,
            "action": action,
            "deny_reason": deny_reason or (safety_status if str(safety_status).startswith("DENY") else ""),
        })
        return proxy

    def should_start_stage2_hold(self, now: float, stage2_proxy: dict[str, Any]) -> bool:
        params = self.stage1.stage2_merge_hold
        dispatch_detect_time = self.stage1.ev_depart_sec - params.t_dispatch_delay_sec
        if now < dispatch_detect_time:
            return False
        if now >= self.stage1.ev_depart_sec:
            return False
        if bool(stage2_proxy.get("EV_MergePassed", False)):
            return False
        if not (bool(stage2_proxy.get("EV_NotDeparted", False)) or bool(stage2_proxy.get("EV_Departed", False))):
            return False
        q_ratio = safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO)
        q_th_merge = q_ratio * params.L_merge_m
        if safe_float(stage2_proxy.get("Lq_merge_m"), 0.0) < q_th_merge:
            return False
        t_hold = safe_float(
            stage2_proxy.get("T_hold_proxy_sec"),
            params.tE_merge_sec - params.tS_merge_sec,
        )
        return t_hold <= 0.0 or bool(stage2_proxy.get("merge_space_deficit", False))

    def stage2_effective_transition_loss_sec(self, now: float, tls_id: str, target_phase: int) -> float:
        params = self.stage1.stage2_merge_hold
        base_loss = safe_float(params.tS_merge_sec, DEFAULT_PHASE_BUFFER_SEC)
        if not tls_id:
            return base_loss
        current_phase = self.get_tls_phase(tls_id)
        if current_phase == target_phase:
            return base_loss
        elapsed = self.elapsed_green_sec(tls_id)
        action_wait = max(DEFAULT_MIN_TLS_ACTION_INTERVAL_SEC - (now - self.last_tls_action_at.get(tls_id, -9999.0)), 0.0)
        ped_wait = 0.0
        if self.phase_has_green(tls_id, current_phase):
            ped_min_green = safe_float(self.pedestrian_min_green_by_tls.get(tls_id), 0.0)
            ped_wait = max(ped_min_green - elapsed, 0.0)
        clearance_wait = 0.0
        if self.phase_is_clearance(tls_id, current_phase):
            clearance_wait = max(self.phase_duration(tls_id, current_phase) - elapsed, 0.0)
        elif self.phase_has_green(tls_id, current_phase):
            clearance_phase = self.find_clearance_phase(tls_id, current_phase, target_phase)
            if clearance_phase is not None:
                clearance_wait = self.phase_duration(tls_id, clearance_phase)
        return round_float(base_loss + action_wait + ped_wait + clearance_wait)

    def stage2_proxy_with_effective_gate_inputs(self, now: float, stage2_proxy: dict[str, Any]) -> dict[str, Any]:
        if not stage2_proxy:
            return stage2_proxy
        proxy = dict(stage2_proxy)
        params = self.stage1.stage2_merge_hold
        self.stage2_gate_history.append({
            "time": float(now),
            "Lq_merge_m": safe_float(proxy.get("Lq_merge_m"), 0.0),
            "n_occ_runtime_veh": safe_float(proxy.get("n_occ_runtime_veh"), 0.0),
        })
        window_start = float(now) - DEFAULT_STAGE2_GATE_HISTORY_SEC
        self.stage2_gate_history = [item for item in self.stage2_gate_history if item["time"] >= window_start]
        if self.stage2_gate_history:
            proxy["Lq_merge_m"] = round_float(max(item["Lq_merge_m"] for item in self.stage2_gate_history))
            proxy["scaled_Lq_merge_m"] = proxy["Lq_merge_m"]
            proxy["n_occ_runtime_veh"] = round_float(max(item["n_occ_runtime_veh"] for item in self.stage2_gate_history))
            proxy["scaled_n_occ_runtime_veh"] = proxy["n_occ_runtime_veh"]
        lq_merge_m = safe_float(proxy.get("Lq_merge_m"), 0.0)
        n_occ = safe_float(proxy.get("n_occ_runtime_veh"), 0.0)
        merge_capacity_without_ev = max(params.C_merge_proxy_veh - params.n_need_proxy_veh, 0.0)
        n_queue_from_lq = lq_merge_m / max(TA_HEADWAY_M, 0.1)
        n_blocking = max(n_occ, n_queue_from_lq)
        n_excess = max(0.0, n_blocking - merge_capacity_without_ev)
        merge_space_deficit_threshold_m = max(params.L_merge_m - params.n_need_proxy_veh * TA_HEADWAY_M, 0.0)
        merge_space_deficit = lq_merge_m >= merge_space_deficit_threshold_m
        s_vph = safe_float(proxy.get("s_vph"), TA_SATURATION_FLOW_VPH_PER_LANE)
        t_clear = n_excess * 3600.0 / max(s_vph, 1.0)
        t_s_eff = self.stage2_effective_transition_loss_sec(
            now,
            self.stage1.departure.merge_control_tls,
            self.stage1.departure.background_inflow_red_hold_phase,
        )
        time_to_merge = safe_float(proxy.get("time_to_merge_sec"), params.tE_merge_sec)
        proxy["n_excess_proxy_veh"] = round_float(n_excess)
        proxy["n_queue_from_Lq_proxy_veh"] = round_float(n_queue_from_lq)
        proxy["n_blocking_proxy_veh"] = round_float(n_blocking)
        proxy["merge_space_deficit_threshold_m"] = round_float(merge_space_deficit_threshold_m)
        proxy["merge_space_deficit"] = merge_space_deficit
        proxy["t_clear_proxy_sec"] = round_float(t_clear)
        proxy["tS_merge_sec"] = round_float(t_s_eff)
        proxy["T_hold_proxy_sec"] = round_float(time_to_merge - t_clear - t_s_eff)
        proxy["stage2_measurement_source"] = f"{proxy.get('stage2_measurement_source', B4_PRIMARY_LANE_DATA_SOURCE)};rolling_max_{int(DEFAULT_STAGE2_GATE_HISTORY_SEC)}s;tS_eff;Lq_space_deficit"
        return proxy

    def should_release_stage2_hold_by_max(self, now: float) -> bool:
        if not self.stage2_hold_active or self.stage2_hold_start is None:
            return False
        hold_max = safe_float(getattr(self.params, "hold_max", self.stage1.stage2_merge_hold.HOLD_MAX_sec), self.stage1.stage2_merge_hold.HOLD_MAX_sec)
        return hold_max > 0.0 and now - self.stage2_hold_start >= hold_max

    def handle_stage2(self, now: float, ev_state: EVState) -> list[dict[str, Any]]:
        departure = self.stage1.departure
        if self.stage2_completed:
            return []
        if ev_state.arrived:
            self.stage2_completed = True
            return []

        dispatch_detect_time = self.stage1.ev_depart_sec - self.stage1.stage2_merge_hold.t_dispatch_delay_sec
        should_watch_merge = now >= dispatch_detect_time
        merged = self.ev_has_merged(ev_state)
        if merged and not self.stage2_hold_active and not self.stage2_hold_clearance_pending and not self.stage2_release_clearance_pending:
            self.stage2_completed = True
            return []

        distance_to_merge_m = self.distance_to_merge_m(ev_state)
        stage2_proxy = (
            stage2_merge_hold_proxy_snapshot(
                self.traci,
                self.stage1,
                now=now,
                ev_state=ev_state,
                distance_to_merge_m=distance_to_merge_m,
                merged=merged,
                measurement_scale=self.stage2_measurement_scale,
            )
            if should_watch_merge or self.stage2_hold_active or self.stage2_hold_clearance_pending or self.stage2_release_clearance_pending
            else {}
        )
        stage2_proxy = self.stage2_proxy_with_effective_gate_inputs(now, stage2_proxy)
        events = []
        if os.environ.get("B4_DEBUG_STAGE2_GATE") and should_watch_merge:
            params = self.stage1.stage2_merge_hold
            q_ratio = safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO)
            q_th_merge = q_ratio * params.L_merge_m
            t_hold = safe_float(stage2_proxy.get("T_hold_proxy_sec"), params.tE_merge_sec - params.tS_merge_sec)
            if bool(stage2_proxy.get("EV_MergePassed", False)):
                gate_reason = "blocked_merge_passed"
            elif not (bool(stage2_proxy.get("EV_NotDeparted", False)) or bool(stage2_proxy.get("EV_Departed", False))):
                gate_reason = "blocked_ev_state_not_eligible"
            elif safe_float(stage2_proxy.get("Lq_merge_m"), 0.0) < q_th_merge:
                gate_reason = "blocked_queue_below_threshold"
            elif t_hold > 0.0 and not bool(stage2_proxy.get("merge_space_deficit", False)):
                gate_reason = "blocked_t_hold_positive"
            else:
                gate_reason = "would_start_hold"
            events.append(event_row(
                time=now,
                stage="stage2",
                action_type="stage2_gate_debug",
                tls_id=departure.merge_control_tls,
                ev_state=ev_state,
                control_mode="departure_merge_hold",
                trigger_reason=gate_reason,
                q_ratio=q_ratio,
                q_th_merge_m=round_float(q_th_merge),
                t_hold_sec=round_float(t_hold),
                stage2_hold_status="debug",
                stage2_action="GATE_CHECK",
                stage2_proxy=stage2_proxy,
                run_id=self.run_id,
                parameter_id=self.params.parameter_id,
                repeat_id=self.repeat_id,
            ))

        if self.stage2_release_clearance_pending:
            previous_phase = self.get_tls_phase(departure.merge_control_tls)
            stage2_proxy = self.stage2_proxy_with_signal_context(stage2_proxy, departure.merge_control_tls, action="RELEASE_REQUEST")
            applied, safety_status, applied_phase, applied_duration = self.apply_tls_request(
                departure.merge_control_tls,
                departure.background_inflow_open_phase,
                DEFAULT_STAGE2_HOLD_REFRESH_SEC,
                "GREEN",
                now,
            )
            stage2_proxy = {
                **stage2_proxy,
                "SafetyGate_result": safety_status,
                "action": "RELEASE" if applied else "RELEASE_REQUEST",
                "deny_reason": safety_status if str(safety_status).startswith("DENY") else "",
            }
            if applied:
                hold_total = max(now - (self.stage2_hold_start or now), 0.0)
                self.stage2_hold_active = False
                self.stage2_release_clearance_pending = False
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
                    target_phase=applied_phase if applied_phase is not None else departure.background_inflow_open_phase,
                    previous_phase=previous_phase,
                    ev_state=ev_state,
                    control_mode="departure_merge_hold",
                    safety_status=safety_status,
                    trigger_reason="release_clearance_completed",
                    phase_duration_sec=applied_duration,
                    stage2_hold_status="released",
                    stage2_action="RELEASE",
                    q_ratio=safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO),
                    q_th_merge_m=round_float(safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO) * self.stage1.stage2_merge_hold.L_merge_m),
                    t_hold_sec=stage2_proxy.get("T_hold_proxy_sec", ""),
                    hold_elapsed_sec=hold_total,
                    stage2_proxy=stage2_proxy,
                    run_id=self.run_id,
                    parameter_id=self.params.parameter_id,
                    repeat_id=self.repeat_id,
                ))
                return events
            if safety_status == "REQUIRE_CLEARANCE":
                self.stats.signal_burden_sec += applied_duration
                events.append(event_row(
                    time=now,
                    stage="stage2",
                    action_type="entry_hold_release_clearance",
                    tls_id=departure.merge_control_tls,
                    target_phase=applied_phase if applied_phase is not None else departure.background_inflow_open_phase,
                    previous_phase=previous_phase,
                    ev_state=ev_state,
                    control_mode="departure_merge_hold",
                    safety_status=safety_status,
                    trigger_reason="release_requires_clearance",
                    phase_duration_sec=applied_duration,
                    stage2_hold_status="release_clearance_pending",
                    stage2_action="RELEASE_REQUEST",
                    q_ratio=safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO),
                    q_th_merge_m=round_float(safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO) * self.stage1.stage2_merge_hold.L_merge_m),
                    t_hold_sec=stage2_proxy.get("T_hold_proxy_sec", ""),
                    hold_elapsed_sec=max(now - (self.stage2_hold_start or now), 0.0),
                    stage2_proxy=stage2_proxy,
                    run_id=self.run_id,
                    parameter_id=self.params.parameter_id,
                    repeat_id=self.repeat_id,
                ))
                return events
            if safety_status in {"DENY_MIN_ACTION_INTERVAL", "DENY_CLEARANCE_INCOMPLETE"}:
                return []
            events.append(event_row(
                time=now,
                stage="stage2",
                action_type="entry_hold_release_denied",
                tls_id=departure.merge_control_tls,
                target_phase=departure.background_inflow_open_phase,
                previous_phase=previous_phase,
                ev_state=ev_state,
                control_mode="departure_merge_hold",
                safety_status=safety_status,
                trigger_reason="release_safety_denied",
                stage2_hold_status="release_clearance_pending",
                stage2_action="RELEASE_REQUEST",
                deny_reason=safety_status,
                q_ratio=safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO),
                q_th_merge_m=round_float(safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO) * self.stage1.stage2_merge_hold.L_merge_m),
                t_hold_sec=stage2_proxy.get("T_hold_proxy_sec", ""),
                hold_elapsed_sec=max(now - (self.stage2_hold_start or now), 0.0),
                stage2_proxy=stage2_proxy,
                run_id=self.run_id,
                parameter_id=self.params.parameter_id,
                repeat_id=self.repeat_id,
            ))
            return events

        if self.stage2_hold_clearance_pending:
            if merged:
                self.stage2_hold_clearance_pending = False
                self.stage2_hold_clearance_start = None
                self.stage2_completed = True
                return []
            hold_clearance_elapsed = max(now - (self.stage2_hold_clearance_start or now), 0.0)
            hold_max = safe_float(getattr(self.params, "hold_max", self.stage1.stage2_merge_hold.HOLD_MAX_sec), self.stage1.stage2_merge_hold.HOLD_MAX_sec)
            if not should_watch_merge or (hold_max > 0.0 and hold_clearance_elapsed >= hold_max):
                self.stage2_hold_clearance_pending = False
                self.stage2_hold_clearance_start = None
                previous_phase = self.get_tls_phase(departure.merge_control_tls)
                stage2_proxy = self.stage2_proxy_with_signal_context(stage2_proxy, departure.merge_control_tls, action="CANCEL_HOLD")
                events.append(event_row(
                    time=now,
                    stage="stage2",
                    action_type="entry_hold_cancelled",
                    tls_id=departure.merge_control_tls,
                    target_phase=departure.background_inflow_red_hold_phase,
                    previous_phase=previous_phase,
                    ev_state=ev_state,
                    control_mode="departure_merge_hold",
                    safety_status="CANCEL_CONDITION_CLEARED",
                    trigger_reason="hold_clearance_timeout" if hold_max > 0.0 and hold_clearance_elapsed >= hold_max else "hold_condition_cleared_during_clearance",
                    stage2_hold_status="cancelled",
                    stage2_action="CANCEL_HOLD",
                    q_ratio=safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO),
                    q_th_merge_m=round_float(safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO) * self.stage1.stage2_merge_hold.L_merge_m),
                    t_hold_sec=stage2_proxy.get("T_hold_proxy_sec", ""),
                    hold_elapsed_sec=hold_clearance_elapsed,
                    stage2_proxy=stage2_proxy,
                    run_id=self.run_id,
                    parameter_id=self.params.parameter_id,
                    repeat_id=self.repeat_id,
                ))
                return events
            previous_phase = self.get_tls_phase(departure.merge_control_tls)
            stage2_proxy = self.stage2_proxy_with_signal_context(stage2_proxy, departure.merge_control_tls, action="RED_HOLD_REQUEST")
            applied, safety_status, applied_phase, applied_duration = self.apply_tls_request(
                departure.merge_control_tls,
                departure.background_inflow_red_hold_phase,
                DEFAULT_STAGE2_HOLD_REFRESH_SEC,
                "RED_HOLD",
                now,
            )
            stage2_proxy = {
                **stage2_proxy,
                "SafetyGate_result": safety_status,
                "action": "RED_HOLD" if applied else "RED_HOLD_REQUEST",
                "deny_reason": safety_status if str(safety_status).startswith("DENY") else "",
            }
            if applied:
                self.stage2_hold_clearance_pending = False
                self.stage2_hold_clearance_start = None
                self.stage2_hold_active = True
                self.stage2_hold_start = now
                self.stats.stage2_hold_count += 1
                self.stats.signal_burden_sec += applied_duration
                events.append(event_row(
                    time=now,
                    stage="stage2",
                    action_type="entry_hold",
                    tls_id=departure.merge_control_tls,
                    target_phase=applied_phase if applied_phase is not None else departure.background_inflow_red_hold_phase,
                    previous_phase=previous_phase,
                    ev_state=ev_state,
                    control_mode="departure_merge_hold",
                    safety_status=safety_status,
                    trigger_reason="hold_clearance_completed",
                    phase_duration_sec=applied_duration,
                    stage2_hold_status="hold_active",
                    stage2_action="RED_HOLD",
                    q_ratio=safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO),
                    q_th_merge_m=round_float(safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO) * self.stage1.stage2_merge_hold.L_merge_m),
                    t_hold_sec=stage2_proxy.get("T_hold_proxy_sec", ""),
                    hold_elapsed_sec=0.0,
                    stage2_proxy=stage2_proxy,
                    run_id=self.run_id,
                    parameter_id=self.params.parameter_id,
                    repeat_id=self.repeat_id,
                ))
                return events
            if safety_status == "REQUIRE_CLEARANCE":
                self.stats.signal_burden_sec += applied_duration
                events.append(event_row(
                    time=now,
                    stage="stage2",
                    action_type="entry_hold_clearance",
                    tls_id=departure.merge_control_tls,
                    target_phase=applied_phase if applied_phase is not None else departure.background_inflow_red_hold_phase,
                    previous_phase=previous_phase,
                    ev_state=ev_state,
                    control_mode="departure_merge_hold",
                    safety_status=safety_status,
                    trigger_reason="hold_requires_clearance",
                    phase_duration_sec=applied_duration,
                    stage2_hold_status="hold_clearance_pending",
                    stage2_action="RED_HOLD_REQUEST",
                    q_ratio=safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO),
                    q_th_merge_m=round_float(safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO) * self.stage1.stage2_merge_hold.L_merge_m),
                    t_hold_sec=stage2_proxy.get("T_hold_proxy_sec", ""),
                    stage2_proxy=stage2_proxy,
                    run_id=self.run_id,
                    parameter_id=self.params.parameter_id,
                    repeat_id=self.repeat_id,
                ))
                return events
            if safety_status in {"DENY_MIN_ACTION_INTERVAL", "DENY_CLEARANCE_INCOMPLETE"}:
                return []
            self.stage2_hold_clearance_pending = False
            self.stage2_hold_clearance_start = None
            events.append(event_row(
                time=now,
                stage="stage2",
                action_type="entry_hold_denied",
                tls_id=departure.merge_control_tls,
                target_phase=departure.background_inflow_red_hold_phase,
                previous_phase=previous_phase,
                ev_state=ev_state,
                control_mode="departure_merge_hold",
                safety_status=safety_status,
                trigger_reason="hold_safety_denied",
                stage2_hold_status="denied",
                stage2_action="DENIED_BY_SAFETY",
                deny_reason=safety_status,
                q_ratio=safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO),
                q_th_merge_m=round_float(safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO) * self.stage1.stage2_merge_hold.L_merge_m),
                t_hold_sec=stage2_proxy.get("T_hold_proxy_sec", ""),
                stage2_proxy=stage2_proxy,
                run_id=self.run_id,
                parameter_id=self.params.parameter_id,
                repeat_id=self.repeat_id,
            ))
            return events

        should_start_hold = (
            should_watch_merge
            and not merged
            and not self.stage2_hold_active
            and not self.stage2_hold_clearance_pending
            and self.should_start_stage2_hold(now, stage2_proxy)
        )
        if should_start_hold:
            previous_phase = self.get_tls_phase(departure.merge_control_tls)
            stage2_proxy = self.stage2_proxy_with_signal_context(stage2_proxy, departure.merge_control_tls, action="RED_HOLD_REQUEST")
            applied, safety_status, applied_phase, applied_duration = self.apply_tls_request(
                departure.merge_control_tls,
                departure.background_inflow_red_hold_phase,
                DEFAULT_STAGE2_HOLD_REFRESH_SEC,
                "RED_HOLD",
                now,
            )
            stage2_proxy = {
                **stage2_proxy,
                "SafetyGate_result": safety_status,
                "action": "RED_HOLD" if applied else ("RED_HOLD_REQUEST" if safety_status == "REQUIRE_CLEARANCE" else "DENIED_BY_SAFETY"),
                "deny_reason": safety_status if str(safety_status).startswith("DENY") else "",
            }
            if not applied and safety_status != "REQUIRE_CLEARANCE":
                events.append(event_row(
                    time=now,
                    stage="stage2",
                    action_type="entry_hold_denied",
                    tls_id=departure.merge_control_tls,
                    target_phase=departure.background_inflow_red_hold_phase,
                    previous_phase=previous_phase,
                    ev_state=ev_state,
                    control_mode="departure_merge_hold",
                    safety_status=safety_status,
                    trigger_reason="evtsp_merge_hold",
                    stage2_hold_status="denied",
                    stage2_action="DENIED_BY_SAFETY",
                    deny_reason=safety_status,
                    q_ratio=safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO),
                    q_th_merge_m=round_float(safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO) * self.stage1.stage2_merge_hold.L_merge_m),
                    t_hold_sec=stage2_proxy.get("T_hold_proxy_sec", ""),
                    stage2_proxy=stage2_proxy,
                    run_id=self.run_id,
                    parameter_id=self.params.parameter_id,
                    repeat_id=self.repeat_id,
                ))
                return events
            if applied:
                self.stage2_hold_active = True
                self.stage2_hold_start = now
                self.stage2_previous_phase = previous_phase
                self.stats.stage2_hold_count += 1
                self.stats.signal_burden_sec += applied_duration
            else:
                self.stage2_hold_clearance_pending = True
                self.stage2_hold_clearance_start = now
                self.stage2_previous_phase = previous_phase
                self.stats.signal_burden_sec += applied_duration
            events.append(event_row(
                time=now,
                stage="stage2",
                action_type="entry_hold" if applied else "entry_hold_clearance",
                tls_id=departure.merge_control_tls,
                target_phase=applied_phase if applied_phase is not None else departure.background_inflow_red_hold_phase,
                previous_phase=previous_phase,
                ev_state=ev_state,
                control_mode="departure_merge_hold",
                safety_status=safety_status,
                trigger_reason="evtsp_merge_hold",
                phase_duration_sec=applied_duration,
                stage2_hold_status="hold_active" if applied else "hold_clearance_pending",
                stage2_action="RED_HOLD" if applied else "RED_HOLD_REQUEST",
                q_ratio=safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO),
                q_th_merge_m=round_float(safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO) * self.stage1.stage2_merge_hold.L_merge_m),
                t_hold_sec=stage2_proxy.get("T_hold_proxy_sec", ""),
                hold_elapsed_sec=0.0 if applied else "",
                stage2_proxy=stage2_proxy,
                run_id=self.run_id,
                parameter_id=self.params.parameter_id,
                repeat_id=self.repeat_id,
            ))
        elif self.stage2_hold_active and not merged and not self.should_release_stage2_hold_by_max(now):
            hold_max = safe_float(getattr(self.params, "hold_max", self.stage1.stage2_merge_hold.HOLD_MAX_sec), self.stage1.stage2_merge_hold.HOLD_MAX_sec)
            elapsed = max(now - (self.stage2_hold_start or now), 0.0)
            remaining = max(hold_max - elapsed, DEFAULT_STEP_SEC) if hold_max > 0.0 else DEFAULT_STAGE2_HOLD_REFRESH_SEC
            self.set_tls_duration(departure.merge_control_tls, min(DEFAULT_STAGE2_HOLD_REFRESH_SEC, remaining))
        elif self.stage2_hold_active and (merged or self.should_release_stage2_hold_by_max(now)):
            previous_phase = self.get_tls_phase(departure.merge_control_tls)
            stage2_proxy = self.stage2_proxy_with_signal_context(stage2_proxy, departure.merge_control_tls, action="RELEASE_REQUEST")
            applied, safety_status, applied_phase, applied_duration = self.apply_tls_request(
                departure.merge_control_tls,
                departure.background_inflow_open_phase,
                DEFAULT_STAGE2_HOLD_REFRESH_SEC,
                "GREEN",
                now,
            )
            stage2_proxy = {
                **stage2_proxy,
                "SafetyGate_result": safety_status,
                "action": "RELEASE" if applied else ("RELEASE_REQUEST" if safety_status == "REQUIRE_CLEARANCE" else "DENIED_BY_SAFETY"),
                "deny_reason": safety_status if str(safety_status).startswith("DENY") else "",
            }
            hold_total = max(now - (self.stage2_hold_start or now), 0.0)
            trigger_reason = "ev_passed_merge" if merged else "hold_max_elapsed"
            if applied:
                self.stage2_hold_active = False
                self.stage2_completed = True
                self.stage2_hold_start = None
                self.stage2_previous_phase = None
                self.stats.stage2_release_count += 1
                self.stats.stage2_hold_total_sec += hold_total
            elif safety_status == "REQUIRE_CLEARANCE":
                self.stage2_release_clearance_pending = True
                self.stats.signal_burden_sec += applied_duration
            elif safety_status in {"DENY_MIN_ACTION_INTERVAL", "DENY_CLEARANCE_INCOMPLETE"}:
                return []
            events.append(event_row(
                time=now,
                stage="stage2",
                action_type="entry_hold_release" if applied else "entry_hold_release_clearance" if safety_status == "REQUIRE_CLEARANCE" else "entry_hold_release_denied",
                tls_id=departure.merge_control_tls,
                target_phase=applied_phase if applied_phase is not None else departure.background_inflow_open_phase,
                previous_phase=previous_phase,
                ev_state=ev_state,
                control_mode="departure_merge_hold",
                safety_status=safety_status if applied or safety_status == "REQUIRE_CLEARANCE" else f"release_{safety_status}",
                trigger_reason=trigger_reason,
                phase_duration_sec=applied_duration,
                stage2_hold_status="released" if applied else "release_clearance_pending" if safety_status == "REQUIRE_CLEARANCE" else "release_denied",
                stage2_action="RELEASE" if applied else "RELEASE_REQUEST" if safety_status == "REQUIRE_CLEARANCE" else "DENIED_BY_SAFETY",
                deny_reason=safety_status if str(safety_status).startswith("DENY") else "",
                q_ratio=safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO),
                q_th_merge_m=round_float(safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO) * self.stage1.stage2_merge_hold.L_merge_m),
                t_hold_sec=stage2_proxy.get("T_hold_proxy_sec", ""),
                hold_elapsed_sec=hold_total,
                stage2_proxy=stage2_proxy,
                run_id=self.run_id,
                parameter_id=self.params.parameter_id,
                repeat_id=self.repeat_id,
            ))
        return events

    def stage3_control_ev_state(self, now: float, ev_state: EVState) -> EVState:
        if ev_state.present:
            return ev_state
        if ev_state.arrived or now >= self.stage1.ev_depart_sec or not isinstance(self.params, B4ThetaParams):
            return ev_state
        start_edge = self.stage1.route_edges[0] if self.stage1.route_edges else ""
        return EVState(
            present=False,
            departed=False,
            arrived=False,
            vehicle_id=self.stage1.ev_id,
            edge_id=start_edge,
            lane_id="",
            route_index=0,
            lane_position_m=0.0,
            speed_mps=TA_EV_SPEED_MPS,
            speed_kmh=TA_EV_SPEED_MPS * 3.6,
        )

    def stage3_ev_distances(self, now: float, ev_state: EVState) -> dict[str, float]:
        depart_wait_m = max(self.stage1.ev_depart_sec - now, 0.0) * TA_EV_SPEED_MPS if not ev_state.present else 0.0
        return {
            movement.movement_id: round_float(self.ev_distance_to_movement(ev_state, movement) + depart_wait_m)
            for movement in self.stage1.movements
        }

    def stage3_pre_depart_window_sec(self) -> float:
        if not isinstance(self.params, B4ThetaParams):
            return 0.0
        return max(
            safe_float(getattr(self.params, "delta_T_thr", 0.0), 0.0),
            theta_ta_lead_sec(self.params),
        ) + DEFAULT_STAGE3_PRE_DEPART_MARGIN_SEC

    def handle_stage3(self, now: float, ev_state: EVState) -> list[dict[str, Any]]:
        if ev_state.arrived:
            return []
        if now < self.stage1.ev_depart_sec and self.stage1.ev_depart_sec - now > self.stage3_pre_depart_window_sec():
            return []
        stage3_ev_state = self.stage3_control_ev_state(now, ev_state)
        if not stage3_ev_state.present and stage3_ev_state.route_index < 0:
            return []
        if now < self.stage1.ev_depart_sec and not isinstance(self.params, B4ThetaParams):
            return []
        events: list[dict[str, Any]] = []
        events.extend(self.restore_passed_or_expired_controls(now, stage3_ev_state))
        self.pending_stage3_requests = {
            movement_id: requested_at
            for movement_id, requested_at in self.pending_stage3_requests.items()
            if now - requested_at < DEFAULT_MIN_TLS_ACTION_INTERVAL_SEC
        }
        runtime_thresholds = theta_runtime_thresholds(self.stage1.thresholds, self.params)
        if movement_runtime_metrics is not DEFAULT_MOVEMENT_RUNTIME_METRICS:
            movement_metrics = [
                movement_runtime_metrics(self.traci, movement, runtime_thresholds)
                for movement in self.stage1.movements
            ]
            tls_estimates: dict[str, TlsQueueEstimate] = {}
            case_b_segment_metrics: dict[str, CaseBSegmentRuntimeMetrics] = {}
            ev_distances = self.stage3_ev_distances(now, stage3_ev_state)
        else:
            queue_lanes = self.stage3_queue_lanes()
            lane_snapshots = sample_lane_snapshots(self.traci, queue_lanes, now)
            tls_estimates = tls_queue_estimates_from_snapshots(self.stage1.movements, lane_snapshots, now)
            case_b_segment_metrics = self.case_b_segment_metrics_from_snapshots(lane_snapshots, now)
            exact_cache: dict[str, dict[str, Any]] = {}
            ev_distances = self.stage3_ev_distances(now, stage3_ev_state)
            movement_metrics = [
                movement_runtime_metrics_from_snapshots(
                    self.traci,
                    movement,
                    runtime_thresholds,
                    lane_snapshots,
                    now,
                    ev_distance_m=ev_distances.get(movement.movement_id, ""),
                    calibration_prior=self.stage1.queue_calibration_priors.get(movement.movement_id),
                    exact_cache=exact_cache,
                    tls_estimate=tls_estimates.get(movement.tls_id),
                )
                for movement in self.stage1.movements
            ]
            self.stats.queue_runtime_lane_count = len(queue_lanes)
            self.stats.queue_runtime_call_mode = QUEUE_RUNTIME_CALL_MODE
            if self.stage1.queue_calibration_priors:
                self.stats.queue_calibration_source = QUEUE_CALIBRATION_SOURCE
        self.stats.observe_queue_metrics(movement_metrics, tls_estimates)
        metrics_by_id = {metric.movement.movement_id: metric for metric in movement_metrics}
        selected = self.stage3_ahead_metrics(movement_metrics, stage3_ev_state)
        plans = self.stage3_case_plans(selected, metrics_by_id)
        new_stage3_action_count = 0
        max_new_stage3_actions = self.stage3_max_new_actions_per_step()
        active_tls_ids = {control.tls_id for control in self.active_controls.values()}
        for plan in plans:
            gate_tE = self.stage3_gate_tE(plan, ev_distances)
            gate_effective_tE, gate_tS = self.stage3_gate_effective_tE(plan, ev_distances)
            delta_gate_open = self.stage3_delta_gate_open(gate_effective_tE)
            for metric in plan.processing_metrics:
                movement = metric.movement
                if len(self.active_controls) >= self.stage1.max_active_movements:
                    break
                if new_stage3_action_count >= max_new_stage3_actions:
                    break
                if movement.is_merge or movement.route_intersection_index == self.stage1.i_merge:
                    continue
                if movement.tls_id == self.stage1.departure.merge_control_tls and self.stage2_hold_active:
                    continue
                if movement.movement_id in self.active_controls or movement.movement_id in self.pending_stage3_requests:
                    continue
                if movement.tls_id in active_tls_ids:
                    continue
                if not self.can_act_on_tls(movement.tls_id, now):
                    continue
                previous_phase = self.get_tls_phase(movement.tls_id)
                ev_distance = ev_distances.get(movement.movement_id, self.ev_distance_to_movement(stage3_ev_state, movement))
                ta, case_b, ta_proxy_sec = self.stage3_ta_for_plan(plan, metric, ev_distances, previous_phase)
                event_case_b_source = self.event_case_b_source(metric, case_b)
                theta_ta_triggered = delta_gate_open and ta_proxy_sec <= theta_ta_lead_sec(self.params)
                q_ratio = safe_float(getattr(self.params, "Q_ratio", EVTSP_DEFAULT_Q_RATIO), EVTSP_DEFAULT_Q_RATIO)
                q_th_m = q_ratio * movement.L_m
                gate_result = "PASS" if delta_gate_open else "CONTINUE_TOO_FAR"
                selection_action = "CASE_B_SELECTED" if plan.case_type == "caseB" else "CASE_A_SELECTED"
                stage3_context = self.stage3_log_context(
                    now=now,
                    plan=plan,
                    metric=metric,
                    ev_state=stage3_ev_state,
                    gate_tE=gate_tE,
                    gate_effective_tE=gate_effective_tE,
                    gate_tS=gate_tS,
                    gate_result=gate_result,
                    previous_phase=previous_phase,
                    ta=ta,
                    case_b=case_b,
                    ta_proxy_sec=ta_proxy_sec,
                    preemption_state="IDLE",
                    action="CONTINUE_TOO_FAR" if not delta_gate_open else selection_action,
                )
                events.append(event_row(
                    time=now,
                    stage="stage3",
                    action_type="trigger_evaluation",
                    movement=movement,
                    metrics=metric,
                    target_phase=movement.selected_green_phase,
                    previous_phase=previous_phase,
                    ev_state=stage3_ev_state,
                    ev_distance_m=round_float(ev_distance),
                    control_mode="case_b_downstream_first" if plan.case_type == "caseB" else "case_a_preemption",
                    safety_status=(
                        "ta_ready"
                        if theta_ta_triggered
                        else "delta_T_gate_closed"
                        if not delta_gate_open
                        else "ta_not_due"
                    ),
                    trigger_reason=(
                        selection_action
                        if theta_ta_triggered
                        else "CONTINUE_TOO_FAR"
                        if not delta_gate_open
                        else "TA_proxy_gt_t_lead"
                    ),
                    active_movement_count=len(self.active_controls),
                    run_id=self.run_id,
                    parameter_id=self.params.parameter_id,
                    repeat_id=self.repeat_id,
                    tE_sec=ta.tE_sec,
                    tS_sec=ta.tS_sec,
                    tQ_sec=ta.tQ_sec,
                    TA_proxy_sec=ta_proxy_sec,
                    ta_triggered=theta_ta_triggered,
                    queue_source=ta.queue_source,
                    case_b_source=event_case_b_source,
                    tS_source=ta.tS_source,
                    TA_case=case_b.TA_case,
                    TA_upstream_sec=case_b.TA_upstream_sec,
                    TA_bottleneck_sec=case_b.TA_bottleneck_sec,
                    case_b_mapping_status=case_b.case_b_mapping_status,
                    case_b_segment_id=case_b.case_b_segment_id,
                    case_b_segment_queue_m_proxy=case_b.case_b_segment_queue_m_proxy,
                    case_b_segment_fill=case_b.case_b_segment_fill,
                    case_b_same_tls_policy=case_b.case_b_same_tls_policy,
                    q_ratio=q_ratio,
                    q_th_m=round_float(q_th_m),
                    stage3_context=stage3_context,
                ))
                if not delta_gate_open or not theta_ta_triggered:
                    continue
                duration = self.target_phase_duration(movement, ta.tS_sec, ev_distance)
                if duration <= 0.0:
                    continue
                stage3_hold_budget = self.stage3_hold_budget_sec()
                if self.stage3_hold_budget_enforced() and stage3_hold_budget <= 0.0:
                    continue
                if self.stage3_hold_budget_enforced():
                    first_deadline_sec = max(DEFAULT_MIN_TLS_ACTION_INTERVAL_SEC, min(duration, stage3_hold_budget))
                else:
                    first_deadline_sec = DEFAULT_MAX_HOLD_SEC
                applied, safety_status, applied_phase, applied_duration = self.apply_tls_request(
                    movement.tls_id,
                    movement.selected_green_phase,
                    duration,
                    "GREEN",
                    now,
                )
                if not applied:
                    preemption_state = "REQUESTED" if safety_status == "REQUIRE_CLEARANCE" else "IDLE"
                    if safety_status == "REQUIRE_CLEARANCE":
                        self.pending_stage3_requests[movement.movement_id] = now
                    stage3_context = self.stage3_log_context(
                        now=now,
                        plan=plan,
                        metric=metric,
                        ev_state=stage3_ev_state,
                        gate_tE=gate_tE,
                        gate_effective_tE=gate_effective_tE,
                        gate_tS=gate_tS,
                        gate_result=gate_result,
                        previous_phase=previous_phase,
                        ta=ta,
                        case_b=case_b,
                        ta_proxy_sec=ta_proxy_sec,
                        preemption_state=preemption_state,
                        action="GREEN_REQUEST" if safety_status == "REQUIRE_CLEARANCE" else "DENIED_BY_SAFETY",
                        safety_status=safety_status,
                        deny_reason=safety_status if str(safety_status).startswith("DENY") else "",
                    )
                    events.append(event_row(
                        time=now,
                        stage="stage3",
                        action_type="phase_change_target_green_deferred" if safety_status == "REQUIRE_CLEARANCE" else "phase_change_target_green_denied",
                        movement=movement,
                        metrics=metric,
                        target_phase=applied_phase if applied_phase is not None else movement.selected_green_phase,
                        previous_phase=previous_phase,
                        ev_state=stage3_ev_state,
                        ev_distance_m=round_float(ev_distance),
                        control_mode="case_b_downstream_first" if plan.case_type == "caseB" else "case_a_preemption",
                        safety_status=safety_status,
                        trigger_reason=metric.trigger_reason or selection_action,
                        phase_duration_sec=applied_duration,
                        active_movement_count=len(self.active_controls),
                        run_id=self.run_id,
                        parameter_id=self.params.parameter_id,
                        repeat_id=self.repeat_id,
                        tE_sec=ta.tE_sec,
                        tS_sec=ta.tS_sec,
                        tQ_sec=ta.tQ_sec,
                        TA_proxy_sec=ta_proxy_sec,
                        ta_triggered=theta_ta_triggered,
                        queue_source=ta.queue_source,
                        case_b_source=event_case_b_source,
                        tS_source=ta.tS_source,
                        TA_case=case_b.TA_case,
                        TA_upstream_sec=case_b.TA_upstream_sec,
                        TA_bottleneck_sec=case_b.TA_bottleneck_sec,
                        case_b_mapping_status=case_b.case_b_mapping_status,
                        case_b_segment_id=case_b.case_b_segment_id,
                        case_b_segment_queue_m_proxy=case_b.case_b_segment_queue_m_proxy,
                        case_b_segment_fill=case_b.case_b_segment_fill,
                        case_b_same_tls_policy=case_b.case_b_same_tls_policy,
                        q_ratio=q_ratio,
                        q_th_m=round_float(q_th_m),
                        green_dur_sec=duration,
                        stage3_context=stage3_context,
                    ))
                    if safety_status == "REQUIRE_CLEARANCE":
                        self.stats.signal_burden_sec += applied_duration
                    continue
                self.active_controls[movement.movement_id] = ActiveControl(
                    movement_id=movement.movement_id,
                    tls_id=movement.tls_id,
                    previous_phase=previous_phase,
                    target_phase=movement.selected_green_phase,
                    started_at=now,
                    deadline=now + first_deadline_sec,
                    route_order_index=movement.route_order_index,
                )
                self.pending_stage3_requests.pop(movement.movement_id, None)
                new_stage3_action_count += 1
                active_tls_ids.add(movement.tls_id)
                self.stats.stage3_preemption_count += 1
                self.stats.signal_burden_sec += applied_duration
                if metric.trigger_reason in {"local_fill", "local_fill_and_low_speed"}:
                    self.stats.trigger_local_fill_count += 1
                if metric.trigger_reason in {"low_speed", "local_fill_and_low_speed"}:
                    self.stats.trigger_speed_count += 1
                if plan.case_type == "caseB":
                    self.stats.bottleneck_mode_count += 1
                stage3_context = self.stage3_log_context(
                    now=now,
                    plan=plan,
                    metric=metric,
                    ev_state=stage3_ev_state,
                    gate_tE=gate_tE,
                    gate_effective_tE=gate_effective_tE,
                    gate_tS=gate_tS,
                    gate_result=gate_result,
                    previous_phase=previous_phase,
                    ta=ta,
                    case_b=case_b,
                    ta_proxy_sec=ta_proxy_sec,
                    preemption_state="ACTIVE",
                    action="GREEN_ACTIVE",
                    safety_status=safety_status,
                )
                events.append(event_row(
                    time=now,
                    stage="stage3",
                    action_type="phase_change_target_green",
                    movement=movement,
                    metrics=metric,
                    target_phase=movement.selected_green_phase,
                    previous_phase=previous_phase,
                    ev_state=stage3_ev_state,
                    ev_distance_m=round_float(ev_distance),
                    control_mode="case_b_downstream_first" if plan.case_type == "caseB" else "case_a_preemption",
                    safety_status=safety_status,
                    trigger_reason=metric.trigger_reason or selection_action,
                    phase_duration_sec=round_float(applied_duration),
                    active_movement_count=len(self.active_controls),
                    run_id=self.run_id,
                    parameter_id=self.params.parameter_id,
                    repeat_id=self.repeat_id,
                    tE_sec=ta.tE_sec,
                    tS_sec=ta.tS_sec,
                    tQ_sec=ta.tQ_sec,
                    TA_proxy_sec=ta_proxy_sec,
                    ta_triggered=theta_ta_triggered,
                    queue_source=ta.queue_source,
                    case_b_source=event_case_b_source,
                    tS_source=ta.tS_source,
                    TA_case=case_b.TA_case,
                    TA_upstream_sec=case_b.TA_upstream_sec,
                    TA_bottleneck_sec=case_b.TA_bottleneck_sec,
                    case_b_mapping_status=case_b.case_b_mapping_status,
                    case_b_segment_id=case_b.case_b_segment_id,
                    case_b_segment_queue_m_proxy=case_b.case_b_segment_queue_m_proxy,
                    case_b_segment_fill=case_b.case_b_segment_fill,
                    case_b_same_tls_policy=case_b.case_b_same_tls_policy,
                    q_ratio=q_ratio,
                    q_th_m=round_float(q_th_m),
                    green_dur_sec=duration,
                    stage3_context=stage3_context,
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
                elapsed = max(now - control.started_at, 0.0)
                hold_budget = self.stage3_hold_budget_sec()
                if self.stage3_hold_budget_enforced() and (hold_budget <= 0.0 or elapsed >= hold_budget):
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
                        control_mode="restore_after_stage3_hold_max",
                        safety_status="restored_previous_phase",
                        trigger_reason="stage3_hold_max_elapsed",
                        phase_duration_sec=DEFAULT_STAGE2_HOLD_REFRESH_SEC,
                        active_movement_count=max(len(self.active_controls) - 1, 0),
                        run_id=self.run_id,
                        parameter_id=self.params.parameter_id,
                        repeat_id=self.repeat_id,
                    ))
                    del self.active_controls[movement_id]
                    continue
                if isinstance(ev_distance, (int, float)) and ev_distance > self.stage3_near_hold_distance_m():
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
                        parameter_id=self.params.parameter_id,
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
                    if self.stage3_hold_budget_enforced():
                        remaining_budget = max(hold_budget - elapsed, 0.0)
                        duration = min(duration, remaining_budget)
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
                        parameter_id=self.params.parameter_id,
                        repeat_id=self.repeat_id,
                    ))
                    continue
                target_phase = control.target_phase
                action_type = "extend_target_green"
                trigger_reason = "ev_not_passed_extend_target_green"
                control_mode = "extend_until_ev_pass"
                if self.stage3_hold_budget_enforced():
                    remaining_budget = max(hold_budget - elapsed, 0.0)
                    extension_duration = min(self.params.G_ext, remaining_budget)
                else:
                    extension_duration = self.params.G_ext
                self.set_tls_phase(control.tls_id, target_phase, extension_duration)
                control.deadline = now + extension_duration
                self.stats.signal_burden_sec += extension_duration
                events.append(event_row(
                    time=now,
                    stage="stage3",
                    action_type=action_type,
                    movement=movement,
                    target_phase=target_phase,
                    previous_phase=self.get_tls_phase(control.tls_id),
                    ev_state=ev_state,
                    ev_distance_m=round_float(ev_distance),
                    control_mode=control_mode,
                    safety_status="stage1_selected_phase_mvp",
                    trigger_reason=trigger_reason,
                    phase_duration_sec=extension_duration,
                    active_movement_count=len(self.active_controls),
                    run_id=self.run_id,
                    parameter_id=self.params.parameter_id,
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
                parameter_id=self.params.parameter_id,
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

    def stage3_control_distance_m(self) -> float:
        if isinstance(self.params, B4ThetaParams):
            lead_sec = theta_ta_lead_sec(self.params)
            if lead_sec <= 0.0:
                return DEFAULT_STAGE3_MIN_CONTROL_DISTANCE_M
            d_up = max(1, safe_int(getattr(self.params, "d_up", 1), 1))
            lookahead_multiplier = 1.0 + 0.25 * (d_up - 1)
            return round_float(clamp_float(
                TA_EV_SPEED_MPS * lead_sec * lookahead_multiplier,
                DEFAULT_STAGE3_MIN_CONTROL_DISTANCE_M,
                DEFAULT_STAGE3_MAX_CONTROL_DISTANCE_M,
            ))
        return DEFAULT_STAGE3_CONTROL_DISTANCE_M

    def stage3_near_hold_distance_m(self) -> float:
        return max(DEFAULT_NEAR_HOLD_DISTANCE_M, self.stage3_control_distance_m())

    def stage3_max_new_actions_per_step(self) -> int:
        if not isinstance(self.params, B4ThetaParams):
            return self.stage1.max_active_movements
        return min(max(1, safe_int(getattr(self.params, "d_up", 1), 1)), 2)

    def stage3_hold_budget_sec(self) -> float:
        if isinstance(self.params, B4ThetaParams):
            return safe_float(getattr(self.params, "G_ext", 0.0), 0.0) + safe_float(
                getattr(self.params, "hold_max", DEFAULT_MAX_HOLD_SEC),
                DEFAULT_MAX_HOLD_SEC,
            )
        return DEFAULT_MAX_HOLD_SEC

    def stage3_hold_budget_enforced(self) -> bool:
        return isinstance(self.params, B4ThetaParams)

    def stage3_delta_gate_open(self, t_e_eff: float | str) -> bool:
        delta_t = safe_float(getattr(self.params, "delta_T_thr", 0.0), 0.0)
        if delta_t <= 0.0 or t_e_eff == "":
            return True
        return safe_float(t_e_eff, float("inf")) <= delta_t

    def target_phase_duration(self, movement: B4Movement, t_s: float | str, ev_distance: float | str) -> float:
        distance = float(ev_distance) if ev_distance != "" else 0.0
        t_pass = (max(distance, 0.0) + movement.W_m + EVTSP_EV_LENGTH_M) / TA_EV_SPEED_MPS
        duration = safe_float(t_s, DEFAULT_PHASE_BUFFER_SEC) + t_pass + safe_float(getattr(self.params, "G_ext", 0.0), 0.0)
        return int(round(max(movement.Gm_sec, duration)))

    def can_act_on_tls(self, tls_id: str, now: float) -> bool:
        return now - self.last_tls_action_at.get(tls_id, -9999.0) >= DEFAULT_MIN_TLS_ACTION_INTERVAL_SEC

    def get_tls_phase(self, tls_id: str) -> int:
        try:
            return int(self.traci.trafficlight.getPhase(tls_id))
        except Exception:
            return -1

    def elapsed_green_sec(self, tls_id: str) -> float:
        for method_name in ("getSpentDuration", "getPhaseDuration"):
            try:
                method = getattr(self.traci.trafficlight, method_name)
                return max(float(method(tls_id)), 0.0)
            except Exception:
                continue
        return 0.0

    def phase_state(self, tls_id: str, phase_index: int) -> str:
        phases = self.phases_by_tls.get(tls_id, [])
        for phase in phases:
            if safe_int(phase.get("phase_index"), -1) == phase_index:
                return str(phase.get("state", ""))
        return ""

    def phase_duration(self, tls_id: str, phase_index: int, default: float = DEFAULT_PHASE_BUFFER_SEC) -> float:
        phases = self.phases_by_tls.get(tls_id, [])
        for phase in phases:
            if safe_int(phase.get("phase_index"), -1) == phase_index:
                return max(safe_float(phase.get("duration"), default), DEFAULT_STEP_SEC)
        return default

    def phase_has_green(self, tls_id: str, phase_index: int) -> bool:
        state = self.phase_state(tls_id, phase_index)
        return any(token in state for token in ("G", "g"))

    def phase_is_clearance(self, tls_id: str, phase_index: int) -> bool:
        state = self.phase_state(tls_id, phase_index)
        if not state:
            return False
        return "y" in state.lower() or not any(token in state for token in ("G", "g"))

    def find_clearance_phase(self, tls_id: str, current_phase: int, target_phase: int) -> int | None:
        phases = self.phases_by_tls.get(tls_id, [])
        if not phases:
            return None
        phase_indices = [safe_int(phase.get("phase_index"), index) for index, phase in enumerate(phases)]
        if current_phase not in phase_indices or target_phase not in phase_indices:
            candidates = phase_indices
        else:
            start = phase_indices.index(current_phase)
            stop = phase_indices.index(target_phase)
            if start < stop:
                candidates = phase_indices[start + 1 : stop + 1]
            else:
                candidates = phase_indices[start + 1 :] + phase_indices[: stop + 1]
        for phase_index in candidates:
            if phase_index != target_phase and self.phase_is_clearance(tls_id, phase_index):
                return phase_index
        for phase_index in phase_indices:
            if phase_index != target_phase and self.phase_is_clearance(tls_id, phase_index):
                return phase_index
        return None

    def safety_gate(self, tls_id: str, target_phase: int, action: str, now: float) -> dict[str, Any]:
        if not tls_id:
            return {"status": "DENY_MISSING_TLS"}
        if not self.can_act_on_tls(tls_id, now):
            return {"status": "DENY_MIN_ACTION_INTERVAL"}
        current_phase = self.get_tls_phase(tls_id)
        if current_phase == target_phase:
            return {"status": "ALLOW", "phase": target_phase}
        elapsed = self.elapsed_green_sec(tls_id)
        ped_min_green = safe_float(self.pedestrian_min_green_by_tls.get(tls_id), 0.0)
        if action in {"GREEN", "RED_HOLD"} and ped_min_green > 0.0 and self.phase_has_green(tls_id, current_phase) and elapsed < ped_min_green:
            return {"status": "DENY_PEDESTRIAN_MIN_GREEN"}
        if self.phase_is_clearance(tls_id, current_phase) and elapsed < self.phase_duration(tls_id, current_phase):
            return {"status": "DENY_CLEARANCE_INCOMPLETE"}
        if self.phase_is_clearance(tls_id, current_phase):
            return {"status": "ALLOW", "phase": target_phase}
        if action in {"GREEN", "RED_HOLD"} and self.phase_has_green(tls_id, current_phase):
            clearance_phase = self.find_clearance_phase(tls_id, current_phase, target_phase)
            if clearance_phase is None:
                return {"status": "DENY_CLEARANCE_UNAVAILABLE"}
            return {
                "status": "REQUIRE_CLEARANCE",
                "phase": clearance_phase,
                "duration": self.phase_duration(tls_id, clearance_phase),
            }
        return {"status": "ALLOW", "phase": target_phase}

    def apply_tls_request(self, tls_id: str, target_phase: int, duration: float, action: str, now: float) -> tuple[bool, str, int | None, float]:
        gate = self.safety_gate(tls_id, target_phase, action, now)
        status = str(gate.get("status", "DENY"))
        if status == "ALLOW":
            phase = safe_int(gate.get("phase"), target_phase)
            self.set_tls_phase(tls_id, phase, duration)
            self.last_tls_action_at[tls_id] = now
            return True, status, phase, duration
        if status == "REQUIRE_CLEARANCE":
            phase = safe_int(gate.get("phase"), -1)
            clearance_duration = safe_float(gate.get("duration"), DEFAULT_PHASE_BUFFER_SEC)
            if phase < 0:
                return False, "DENY_CLEARANCE_UNAVAILABLE", None, 0.0
            self.set_tls_phase(tls_id, phase, clearance_duration)
            self.last_tls_action_at[tls_id] = now
            return False, status, phase, clearance_duration
        return False, status, None, 0.0

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


def ev_signal_link_map(
    net_file: Path = B04_NET,
    route_xml: Path = B04_FIRETRUCK_ROUTE_XML,
) -> dict[str, list[dict[str, Any]]]:
    """Map each on-route TLS to the EV movements it controls, with stop-line geo.

    Returns ``{tls_id: [{"link": int, "lat": float, "lon": float}, ...]}`` — one
    entry per consecutive route edge pair the EV traverses under that TLS. One
    runtime TLS can control SEVERAL on-route stop-lines (e.g. a ``joinedS_…``
    junction the route passes through twice); recording each link + its stop-line
    position lets the dumper emit a per-movement colour and the visualization
    match each geojson light to the right movement instead of dropping it.
    """
    import sumolib  # local import; only needed for the dump path

    net = sumolib.net.readNet(str(net_file))
    root = ET.parse(str(route_xml)).getroot()
    route_el = root.find(".//route")
    edges = route_el.get("edges").split() if route_el is not None else []
    link_map: dict[str, list[dict[str, Any]]] = {}
    for i in range(len(edges) - 1):
        try:
            e_from = net.getEdge(edges[i])
        except Exception:
            continue
        to_id = edges[i + 1]
        conn = None
        conn_lane = None
        for lane in e_from.getLanes():
            for c in lane.getOutgoing():
                if c.getTo().getID() == to_id:
                    conn, conn_lane = c, lane
                    break
            if conn:
                break
        if conn is None:
            continue
        tls_id = conn.getTLSID()
        link = conn.getTLLinkIndex()
        if not tls_id or link is None or link < 0:
            continue
        x, y = conn_lane.getShape()[-1]  # stop-line end of the EV's lane
        lon, lat = net.convertXY2LonLat(x, y)
        entries = link_map.setdefault(tls_id, [])
        if not any(e["link"] == link for e in entries):
            entries.append({"link": link, "lat": round(lat, 6), "lon": round(lon, 6)})
    return link_map


# SUMO RYG state char -> visualization state. Yellow is its own colour; the EV
# faces 'G'/'g' (green, with/without priority) or 'r'/'R' (red).
def _ryg_to_state(ch: str) -> str:
    if ch in ("G", "g"):
        return "green"
    if ch in ("y", "Y"):
        return "yellow"
    return "red"  # 'r', 'R', 'o', anything else => not-go


class TlsStateDumper:
    """Record the EV-facing signal colour of every on-route MOVEMENT each step.

    Writes change-compressed rows ``time,tls_id,link_index,lat,lon,ryg_char,
    state`` so the visualization can replay the real per-light timeline. The unit
    is (tls_id, link_index), not just tls_id: one runtime TLS can control several
    on-route stop-lines, and each must drive its own geojson icon (otherwise the
    extra stop-lines, e.g. a joinedS_ junction the route crosses twice, show no
    signal at all). A row is emitted only when that movement's colour flips.
    """

    def __init__(self, traci: Any, link_map: dict[str, list[dict[str, Any]]], out_path: Path):
        self.traci = traci
        self.out_path = Path(out_path)
        self._last: dict[tuple[str, int], str] = {}
        self._rows: list[tuple[float, str, int, float, float, str, str]] = []
        # Resolve which TLS ids actually exist in this run once.
        try:
            live = set(traci.trafficlight.getIDList())
        except Exception:
            live = set()
        self._active = {tid: entries for tid, entries in link_map.items() if tid in live}

    def step(self, now: float) -> None:
        for tid, entries in self._active.items():
            try:
                state = self.traci.trafficlight.getRedYellowGreenState(tid)
            except Exception:
                continue
            for e in entries:
                link = e["link"]
                if link >= len(state):
                    continue
                ch = state[link]
                viz = _ryg_to_state(ch)
                key = (tid, link)
                if self._last.get(key) != viz:
                    self._rows.append((round(now, 1), tid, link, e["lat"], e["lon"], ch, viz))
                    self._last[key] = viz

    def write(self) -> None:
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        with self.out_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["time", "tls_id", "link_index", "lat", "lon", "ryg_char", "state"])
            writer.writerows(self._rows)


def run_b4_traci_loop(
    traci: Any,
    stage1: B4Stage1Inputs | None = None,
    run_id: str = "",
    repeat_id: int = 1,
    params: B4MvpParams | None = None,
    phase_config: B4RuntimePhaseConfig | None = None,
    tls_dump_path: Path | None = None,
) -> tuple[list[dict[str, Any]], B4ControllerStats, B4RuntimeMonitor]:
    stage1 = stage1 or B4Stage1Inputs.load()
    phase_config = phase_config or B4RuntimePhaseConfig.bo_smoke()
    controller = B4RuntimeController(
        traci=traci,
        stage1=stage1,
        params=params or B4MvpParams(),
        run_id=run_id,
        repeat_id=repeat_id,
        stage2_measurement_scale=phase_config.stage2_measurement_scale,
        stage3_measurement_scale=phase_config.stage3_measurement_scale,
    )
    monitor = B4RuntimeMonitor(traci=traci, stage1=stage1, config=phase_config, run_id=run_id, repeat_id=repeat_id, mode=B4_MODE)
    dumper = TlsStateDumper(traci, ev_signal_link_map(), tls_dump_path) if tls_dump_path else None
    while traci.simulation.getMinExpectedNumber() > 0:
        traci.simulationStep()
        events = controller.step()
        monitor.update_signal_context(events)
        now = float(traci.simulation.getTime())
        if dumper is not None:
            dumper.step(now)
        monitor_events, should_stop = monitor.observe(now, controller.ev_state())
        for event in monitor_events:
            controller.events.append(event)
            controller.stats.signal_event_count += 1
        if should_stop:
            break
    if dumper is not None:
        dumper.write()
    return controller.events, controller.stats, monitor


def run_b04_traci_loop(
    traci: Any,
    stage1: B4Stage1Inputs | None = None,
    run_id: str = "",
    repeat_id: int = 1,
    phase_config: B4RuntimePhaseConfig | None = None,
    tls_dump_path: Path | None = None,
) -> tuple[list[dict[str, Any]], B4RuntimeMonitor]:
    stage1 = stage1 or B4Stage1Inputs.load()
    phase_config = phase_config or B4RuntimePhaseConfig.bo_smoke()
    monitor = B4RuntimeMonitor(traci=traci, stage1=stage1, config=phase_config, run_id=run_id, repeat_id=repeat_id, mode=B04_MODE)
    dumper = TlsStateDumper(traci, ev_signal_link_map(), tls_dump_path) if tls_dump_path else None
    events: list[dict[str, Any]] = []
    while traci.simulation.getMinExpectedNumber() > 0:
        traci.simulationStep()
        now = float(traci.simulation.getTime())
        if dumper is not None:
            dumper.step(now)
        monitor_events, should_stop = monitor.observe(now, ev_state_from_traci(traci, stage1))
        events.extend(monitor_events)
        if should_stop:
            break
        if now >= phase_config.hard_max_sim_time:
            monitor.set_termination("hard_max_sim_time", now)
            events.append(monitor.diagnostic_event(now, "early_termination", ev_state_from_traci(traci, stage1), termination_reason="hard_max_sim_time"))
            break
    if dumper is not None:
        dumper.write()
    return events, monitor
