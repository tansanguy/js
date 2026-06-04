#!/usr/bin/env python3
"""Compact V9 B04 baseline demand and queue-recall workflow.

B04 is the no-control reality baseline built on the Compact V9 green18 map.
It is intentionally isolated from the earlier Expanded V7 and Signal BO assets.
"""

from __future__ import annotations

import argparse
import csv
import html
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import find_executable, read_sumo_net, sha256_file  # noqa: E402


DATA_ROOT = PROJECT_ROOT / "data_prepared/compact_v9"
METRICS_ROOT = PROJECT_ROOT / "results/metrics/compact_v9_B04"
HTML_ROOT = PROJECT_ROOT / "results/html"
REFERENCE_CSV = PROJECT_ROOT / "toegye_ro_mainstream_segments_english.csv"
GREEN18_NET = DATA_ROOT / "net/jungbu_compact_v9_ellipse_lanes_repaired_entry_tls_connected_mainline_green18.net.xml"
B04_NET = DATA_ROOT / "net/jungbu_compact_v9_B04_green18.net.xml"
B04_SPEED50_NET = DATA_ROOT / "net/jungbu_compact_v9_B04_green18_speed50_sanity.net.xml"
B04_MANIFEST = PROJECT_ROOT / "configs/compact_v9_B04_b0_manifest.json"
B04_MAPPING_CSV = DATA_ROOT / "map/B04_toegye_segment_edge_mapping.csv"
B04_TARGET_PROFILE_CSV = DATA_ROOT / "map/B04_target_profile.csv"
B04_FIRETRUCK_ROUTE_XML = DATA_ROOT / "routes/firetruck_to_seoul_station_front.rou.xml"
B04_FIRETRUCK_ROUTE_CSV = DATA_ROOT / "routes/firetruck_route.csv"
B04_DEMAND_DIR = DATA_ROOT / "demand"
B04_SELECTED_DIR = METRICS_ROOT / "selected"
B04_REVIEW_HTML = HTML_ROOT / "compact_v9_B04_demand_validation_review.html"
B04_QUEUE_AUDIT_DIR = METRICS_ROOT / "queue_audit"

VEHICLE_LENGTH_M = 5.0
HEADWAY_M = 7.5
MAX_TEMPLATE_VEHICLES = 800
MAX_SOURCE_VEHICLES = 1200
EV_DEPART_SEC = 600.0
SIM_END_SEC = 4200.0
EDGE_DATA_FREQ_SEC = 60
FCD_MIN_SEGMENT_SAMPLE_COUNT = 3
QUEUE_SAMPLE_BEGIN_SEC = 450.0
QUEUE_SAMPLE_END_SEC = 1200.0
QUEUE_SAMPLE_INTERVAL_SEC = 5
STORAGE_CORRIDOR_MAX_M = 250.0
SPEED50_MPS = 50.0 / 3.6
OD_REPAIR_TARGETS = (("S2", "upbound"), ("S11", "upbound"), ("S12", "upbound"), ("S20", "downbound"))
OD_QUEUE_TUNED_TARGETS = (
    ("S16", "upbound"), ("S17", "upbound"), ("S18", "upbound"),
    ("S13", "downbound"), ("S14", "downbound"), ("S15", "downbound"),
)
B4_CONTROL_TARGET_SEGMENTS = ("S3", "S6", "S9", "S15", "S21")

CANDIDATES = {
    "B04_a_csv_through_only": {
        "through_scale": 0.50,
        "feeder_share": 0.00,
        "sideflow_share": 0.00,
        "pulse_share": 0.00,
    },
    "B04_b_through_plus_feeder": {
        "through_scale": 0.65,
        "feeder_share": 0.16,
        "sideflow_share": 0.00,
        "pulse_share": 0.15,
    },
    "B04_c_queue_window_pulse": {
        "through_scale": 0.78,
        "feeder_share": 0.22,
        "sideflow_share": 0.04,
        "pulse_share": 0.45,
    },
    "B04_d_sideflow_balanced": {
        "through_scale": 0.72,
        "feeder_share": 0.20,
        "sideflow_share": 0.14,
        "pulse_share": 0.30,
    },
    "B04_e_best_mix": {
        "through_scale": 0.90,
        "feeder_share": 0.28,
        "sideflow_share": 0.16,
        "pulse_share": 0.45,
    },
    "B04_f_direction_balance": {
        "through_scale_upbound": 0.18,
        "through_scale_downbound": 1.20,
        "feeder_share_upbound": 0.04,
        "feeder_share_downbound": 0.42,
        "sideflow_share": 0.12,
        "pulse_share": 0.30,
    },
    "B04_g_downbound_density": {
        "through_scale_upbound": 0.10,
        "through_scale_downbound": 1.80,
        "feeder_share_upbound": 0.02,
        "feeder_share_downbound": 0.55,
        "sideflow_share": 0.15,
        "pulse_share": 0.40,
    },
    "B04_h_peak_pulse": {
        "through_scale_upbound": 0.08,
        "through_scale_downbound": 1.90,
        "feeder_share_upbound": 0.02,
        "feeder_share_downbound": 0.70,
        "sideflow_share": 0.12,
        "pulse_share": 0.60,
        "pulse_begin": 600.0,
        "pulse_end": 900.0,
    },
    "B04_i_downbound_feeder": {
        "through_scale_upbound": 0.08,
        "through_scale_downbound": 1.60,
        "feeder_share_upbound": 0.01,
        "feeder_share_downbound": 1.20,
        "sideflow_share": 0.14,
        "pulse_share": 0.40,
        "pulse_begin": 500.0,
        "pulse_end": 950.0,
    },
    "B04_j_balanced_recall": {
        "through_scale_upbound": 0.06,
        "through_scale_downbound": 1.45,
        "feeder_share_upbound": 0.01,
        "feeder_share_downbound": 0.95,
        "sideflow_share": 0.18,
        "pulse_share": 0.45,
        "pulse_begin": 550.0,
        "pulse_end": 950.0,
    },
    "B04_k_city_behavior": {
        "through_scale_upbound": 0.06,
        "through_scale_downbound": 1.45,
        "feeder_share_upbound": 0.01,
        "feeder_share_downbound": 0.95,
        "sideflow_share": 0.18,
        "pulse_share": 0.45,
        "pulse_begin": 550.0,
        "pulse_end": 950.0,
        "speed_factor": 0.82,
        "speed_dev": 0.08,
        "tau": 1.25,
        "min_gap": 3.0,
        "accel": 1.55,
        "decel": 3.8,
        "sigma": 0.65,
        "calibration_note": "B04_j demand with conservative urban passenger behavior.",
    },
    "B04_l_volume_calibrated": {
        "through_scale_upbound": 0.10,
        "through_scale_downbound": 1.65,
        "feeder_share_upbound": 0.025,
        "feeder_share_downbound": 1.15,
        "sideflow_share": 0.20,
        "pulse_share": 0.50,
        "pulse_begin": 520.0,
        "pulse_end": 980.0,
        "segment_factor_mode": "free_under_volume_boost",
        "free_under_volume_boost": 1.70,
        "free_boost": 1.30,
        "stop_cut": 0.45,
        "template_cap": 650,
        "source_cap": 900,
        "calibration_note": "Boost under-volume/free-flow segments and cut non-S22 stop artifacts.",
    },
    "B04_m_hybrid_recall": {
        "through_scale_upbound": 0.09,
        "through_scale_downbound": 1.58,
        "feeder_share_upbound": 0.02,
        "feeder_share_downbound": 1.08,
        "sideflow_share": 0.22,
        "pulse_share": 0.55,
        "pulse_begin": 550.0,
        "pulse_end": 950.0,
        "segment_factor_mode": "free_under_volume_boost",
        "free_under_volume_boost": 1.45,
        "free_boost": 1.20,
        "stop_cut": 0.55,
        "speed_factor": 0.88,
        "speed_dev": 0.08,
        "tau": 1.18,
        "min_gap": 2.8,
        "accel": 1.7,
        "decel": 4.0,
        "sigma": 0.6,
        "template_cap": 700,
        "source_cap": 950,
        "calibration_note": "Moderate urban behavior plus segment volume redistribution.",
    },
    "B04_n_speed50_sanity": {
        "through_scale_upbound": 0.06,
        "through_scale_downbound": 1.45,
        "feeder_share_upbound": 0.01,
        "feeder_share_downbound": 0.95,
        "sideflow_share": 0.18,
        "pulse_share": 0.45,
        "pulse_begin": 550.0,
        "pulse_end": 950.0,
        "net_profile": "speed50_sanity",
        "calibration_note": "B04_j demand on relevant-edge 50km/h sanity net.",
    },
    "B04_o_speedfactor_only": {
        "through_scale_upbound": 0.06,
        "through_scale_downbound": 1.45,
        "feeder_share_upbound": 0.01,
        "feeder_share_downbound": 0.95,
        "sideflow_share": 0.18,
        "pulse_share": 0.45,
        "pulse_begin": 550.0,
        "pulse_end": 950.0,
        "net_profile": "speed50_sanity",
        "speed_factor": 0.88,
        "speed_dev": 0.08,
        "calibration_note": "Speed factor only, no tau/minGap change.",
    },
    "B04_p_k_light": {
        "through_scale_upbound": 0.06,
        "through_scale_downbound": 1.45,
        "feeder_share_upbound": 0.01,
        "feeder_share_downbound": 0.95,
        "sideflow_share": 0.18,
        "pulse_share": 0.45,
        "pulse_begin": 550.0,
        "pulse_end": 950.0,
        "net_profile": "speed50_sanity",
        "speed_factor": 0.90,
        "speed_dev": 0.08,
        "tau": 1.08,
        "min_gap": 2.4,
        "accel": 1.8,
        "decel": 4.2,
        "sigma": 0.55,
        "calibration_note": "Light urban behavior to reduce free flow without recreating gridlock.",
    },
    "B04_q_midcorridor_flow": {
        "through_scale_upbound": 0.08,
        "through_scale_downbound": 1.45,
        "feeder_share_upbound": 0.02,
        "feeder_share_downbound": 0.95,
        "midcorridor_share": 0.18,
        "sideflow_share": 0.18,
        "pulse_share": 0.45,
        "pulse_begin": 550.0,
        "pulse_end": 950.0,
        "net_profile": "speed50_sanity",
        "speed_factor": 0.90,
        "speed_dev": 0.08,
        "calibration_note": "Extra S14-S18 crossing local flow with terminal-safe sinks.",
    },
    "B04_r_exit_relief": {
        "through_scale_upbound": 0.06,
        "through_scale_downbound": 1.35,
        "feeder_share_upbound": 0.01,
        "feeder_share_downbound": 0.85,
        "sideflow_share": 0.18,
        "pulse_share": 0.42,
        "pulse_begin": 550.0,
        "pulse_end": 950.0,
        "net_profile": "speed50_sanity",
        "avoid_terminal_sources": 1.0,
        "template_cap": 600,
        "source_cap": 700,
        "speed_factor": 0.90,
        "speed_dev": 0.08,
        "calibration_note": "Relieve S16-S21 stop by reducing terminal concentration.",
    },
    "B04_s_light_combined": {
        "through_scale_upbound": 0.08,
        "through_scale_downbound": 1.38,
        "feeder_share_upbound": 0.018,
        "feeder_share_downbound": 0.90,
        "midcorridor_share": 0.14,
        "sideflow_share": 0.20,
        "pulse_share": 0.48,
        "pulse_begin": 550.0,
        "pulse_end": 950.0,
        "net_profile": "speed50_sanity",
        "avoid_terminal_sources": 1.0,
        "segment_factor_mode": "free_under_volume_boost",
        "free_under_volume_boost": 1.25,
        "free_boost": 1.12,
        "stop_cut": 0.70,
        "template_cap": 650,
        "source_cap": 750,
        "speed_factor": 0.90,
        "speed_dev": 0.08,
        "tau": 1.06,
        "min_gap": 2.3,
        "accel": 1.85,
        "decel": 4.2,
        "sigma": 0.55,
        "calibration_note": "Speed50 sanity, light behavior, midcorridor flow, and exit relief.",
    },
    "B04_u_speedfactor_exit_relief": {
        "through_scale_upbound": 0.06,
        "through_scale_downbound": 1.35,
        "feeder_share_upbound": 0.01,
        "feeder_share_downbound": 0.85,
        "sideflow_share": 0.18,
        "pulse_share": 0.42,
        "pulse_begin": 550.0,
        "pulse_end": 950.0,
        "net_profile": "speed50_sanity",
        "avoid_terminal_sources": 1.0,
        "template_cap": 600,
        "source_cap": 700,
        "speed_factor": 0.88,
        "speed_dev": 0.08,
        "calibration_note": "B04_o speedFactor combined with B04_r terminal exit relief.",
    },
    "B04_v_queue_overlap_tuned": {
        "through_scale_upbound": 0.07,
        "through_scale_downbound": 1.35,
        "feeder_share_upbound": 0.012,
        "feeder_share_downbound": 0.88,
        "midcorridor_share": 0.08,
        "sideflow_share": 0.18,
        "pulse_share": 0.46,
        "pulse_begin": 540.0,
        "pulse_end": 960.0,
        "net_profile": "speed50_sanity",
        "avoid_terminal_sources": 1.0,
        "segment_factor_mode": "free_under_volume_boost",
        "free_under_volume_boost": 1.18,
        "free_boost": 1.08,
        "stop_cut": 0.78,
        "template_cap": 620,
        "source_cap": 720,
        "speed_factor": 0.88,
        "speed_dev": 0.08,
        "calibration_note": "B04_u plus weak feeder/local flow on target congestion-proxy segments.",
    },
    "B04_w_od_coverage_repair": {
        "through_scale_upbound": 0.07,
        "through_scale_downbound": 1.35,
        "feeder_share_upbound": 0.012,
        "feeder_share_downbound": 0.78,
        "sideflow_share": 0.12,
        "od_repair_share": 0.45,
        "pulse_share": 0.46,
        "pulse_begin": 540.0,
        "pulse_end": 960.0,
        "net_profile": "speed50_sanity",
        "avoid_terminal_sources": 1.0,
        "template_cap": 620,
        "source_cap": 720,
        "speed_factor": 0.88,
        "speed_dev": 0.08,
        "calibration_note": "B04_v with low-coverage free-flow vehicles reassigned to targeted O/D routes.",
    },
    "B04_x_od_queue_tuned": {
        "through_scale_upbound": 0.07,
        "through_scale_downbound": 1.35,
        "feeder_share_upbound": 0.012,
        "feeder_share_downbound": 0.80,
        "sideflow_share": 0.12,
        "od_repair_share": 0.45,
        "od_queue_tuned_share": 0.20,
        "pulse_share": 0.48,
        "pulse_begin": 540.0,
        "pulse_end": 960.0,
        "net_profile": "speed50_sanity",
        "avoid_terminal_sources": 1.0,
        "segment_factor_mode": "free_under_volume_boost",
        "free_under_volume_boost": 1.12,
        "free_boost": 1.05,
        "stop_cut": 0.82,
        "template_cap": 620,
        "source_cap": 720,
        "speed_factor": 0.88,
        "speed_dev": 0.08,
        "calibration_note": "B04_w plus weak O/D pulse for queue-proxy overlap segments.",
    },
    "B04_y_temporal_compression": {
        "through_scale_upbound": 0.07,
        "through_scale_downbound": 1.35,
        "feeder_share_upbound": 0.012,
        "feeder_share_downbound": 0.80,
        "sideflow_share": 0.12,
        "od_repair_share": 0.45,
        "od_queue_tuned_share": 0.20,
        "pulse_share": 0.52,
        "pulse_begin": 540.0,
        "pulse_end": 960.0,
        "target_pulse_mode": "compressed",
        "target_pulse_begin": 600.0,
        "target_pulse_end": 840.0,
        "net_profile": "speed50_sanity",
        "avoid_terminal_sources": 1.0,
        "segment_factor_mode": "free_under_volume_boost",
        "free_under_volume_boost": 1.12,
        "free_boost": 1.05,
        "stop_cut": 0.82,
        "template_cap": 620,
        "source_cap": 720,
        "speed_factor": 0.88,
        "speed_dev": 0.08,
        "calibration_note": "B04_x O/D coverage with target O/D vehicles compressed into 600-840s.",
    },
    "B04_z_signal_queue_pulse": {
        "through_scale_upbound": 0.07,
        "through_scale_downbound": 1.35,
        "feeder_share_upbound": 0.012,
        "feeder_share_downbound": 0.80,
        "sideflow_share": 0.12,
        "od_repair_share": 0.45,
        "od_queue_tuned_share": 0.20,
        "pulse_share": 0.52,
        "pulse_begin": 540.0,
        "pulse_end": 960.0,
        "target_pulse_mode": "two_burst",
        "target_pulse_begin": 620.0,
        "target_pulse_mid": 760.0,
        "target_pulse_end": 900.0,
        "net_profile": "speed50_sanity",
        "avoid_terminal_sources": 1.0,
        "segment_factor_mode": "free_under_volume_boost",
        "free_under_volume_boost": 1.12,
        "free_boost": 1.05,
        "stop_cut": 0.82,
        "template_cap": 620,
        "source_cap": 720,
        "speed_factor": 0.88,
        "speed_dev": 0.08,
        "calibration_note": "B04_y with two burst target O/D arrival windows for stop-line queue formation.",
    },
    "B04_aa_balanced_growth": {
        "through_scale_upbound": 0.16,
        "through_scale_downbound": 0.22,
        "feeder_share_upbound": 0.025,
        "feeder_share_downbound": 0.66,
        "midcorridor_share": 0.10,
        "sideflow_share": 0.15,
        "od_repair_share": 0.38,
        "od_queue_tuned_share": 0.22,
        "pulse_share": 0.55,
        "pulse_begin": 540.0,
        "pulse_end": 960.0,
        "target_pulse_mode": "compressed",
        "target_pulse_begin": 600.0,
        "target_pulse_end": 900.0,
        "net_profile": "speed50_sanity",
        "avoid_terminal_sources": 1.0,
        "avoid_terminal_sinks": 1.0,
        "use_balanced_main_through": 1.0,
        "balanced_main_through_only": 1.0,
        "segment_factor_mode": "free_under_volume_boost",
        "free_under_volume_boost": 1.18,
        "free_boost": 1.08,
        "stop_cut": 0.82,
        "template_cap": 760,
        "source_cap": 840,
        "speed_factor": 0.88,
        "speed_dev": 0.08,
        "calibration_note": "Balanced natural growth: terminal-safe main-through, modest mapwide demand, and B4 approach overlap.",
    },
    "B04_ab_queue_pressure": {
        "through_scale_upbound": 0.24,
        "through_scale_downbound": 0.32,
        "feeder_share_upbound": 0.035,
        "feeder_share_downbound": 0.70,
        "midcorridor_share": 0.12,
        "sideflow_share": 0.16,
        "od_repair_share": 0.45,
        "od_queue_tuned_share": 0.30,
        "pulse_share": 0.62,
        "pulse_begin": 540.0,
        "pulse_end": 960.0,
        "target_pulse_mode": "two_burst",
        "target_pulse_begin": 620.0,
        "target_pulse_mid": 760.0,
        "target_pulse_end": 880.0,
        "net_profile": "speed50_sanity",
        "avoid_terminal_sources": 1.0,
        "avoid_terminal_sinks": 1.0,
        "use_balanced_main_through": 1.0,
        "balanced_main_through_only": 1.0,
        "segment_factor_mode": "free_under_volume_boost",
        "free_under_volume_boost": 1.22,
        "free_boost": 1.10,
        "stop_cut": 0.86,
        "template_cap": 860,
        "source_cap": 920,
        "speed_factor": 0.88,
        "speed_dev": 0.08,
        "calibration_note": "Queue-pressure candidate: terminal-safe through growth and tighter B4 stopline pulse.",
    },
}


class B04Error(RuntimeError):
    """Expected B04 pipeline failure."""


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


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise B04Error(f"json_root_not_object:{rel(path)}")
    return payload


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def resolve_candidate_names(value: str | None) -> list[str]:
    if not value:
        return list(CANDIDATES)
    names = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [name for name in names if name not in CANDIDATES]
    if unknown:
        raise B04Error(f"unknown_candidates:{','.join(unknown)}")
    return names


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


def segment_number(segment_id: str) -> int:
    return int(str(segment_id).strip().lstrip("S") or 0)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise B04Error(f"module_load_failed:{rel(path)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validated_pipeline() -> Any:
    return load_module("b04_validated_pipeline", PROJECT_ROOT / "01-2 Validated/validated_pipeline.py")


def adopt_green18() -> dict[str, Any]:
    if not GREEN18_NET.is_file():
        raise B04Error(f"missing_green18_net:{rel(GREEN18_NET)}")
    B04_NET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(GREEN18_NET, B04_NET)
    manifest = {
        "schema": "compact_v9_B04_b0_manifest.v1",
        "generated_at": utc_now(),
        "baseline_name": "B04",
        "mode": "B0",
        "parameter_id": "no_control",
        "active_net": rel(B04_NET),
        "green18_source_net": rel(GREEN18_NET),
        "active_net_sha256": sha256_file(B04_NET),
        "firetruck_route_xml": rel(B04_FIRETRUCK_ROUTE_XML),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "emergency_depart_sec": EV_DEPART_SEC,
        "background_route": "",
        "policy_ko": "Compact V9 green18 맵을 B04 현실 수요/queue recall용 B0 baseline map으로 채택합니다.",
    }
    write_json(B04_MANIFEST, manifest)
    return manifest


def build_mapping() -> dict[str, Any]:
    if not B04_NET.is_file():
        adopt_green18()
    vp = validated_pipeline()
    rows, summary = vp.build_toegye_edge_mapping(REFERENCE_CSV, B04_NET)
    write_csv(
        B04_MAPPING_CSV,
        rows,
        [
            "segment_id", "direction", "edge_id", "edge_order", "axis_position",
            "matched_length_m", "segment_length_m", "match_ratio", "current_lanes",
            "target_lanes", "lane_delta", "repair_target", "repair_reason",
        ],
    )
    summary = {
        **summary,
        "schema": "compact_v9_B04_segment_edge_mapping.v1",
        "generated_at": utc_now(),
        "net_file": rel(B04_NET),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "output_csv": rel(B04_MAPPING_CSV),
        "mapping_policy": "S1-S22 edge 1:1 perfect recall is not required; grouped segment aggregation is used for short/fragmented edges.",
    }
    write_json(B04_MAPPING_CSV.with_suffix(".summary.json"), summary)
    return summary


def build_target_profile() -> dict[str, Any]:
    rows = []
    for row in read_csv(REFERENCE_CSV):
        length_m = safe_float(row.get("segment_length_m"))
        speed_limit = safe_float(row.get("speed_limit_kmh"), 50.0)
        volume = safe_float(row.get("peak_hour_volume_veh_per_h_reference"))
        for direction in ["upbound", "downbound"]:
            speed = safe_float(row.get(f"avg_speed_kmh_{direction}"))
            travel_time = safe_float(row.get(f"travel_time_s_{direction}"))
            free_time = length_m / max(speed_limit / 3.6, 0.1)
            delay_ratio = travel_time / max(free_time, 1.0)
            speed_deficit = max(0.0, speed_limit - speed)
            rows.append({
                "segment_id": row["segment_id"],
                "direction": direction,
                "segment_length_m": round(length_m, 3),
                "target_speed_kmh": round(speed, 3),
                "target_travel_time_s": round(travel_time, 3),
                "reference_volume_vph": round(volume, 3),
                "speed_limit_kmh": round(speed_limit, 3),
                "free_flow_travel_time_s": round(free_time, 3),
                "travel_time_delay_ratio": round(delay_ratio, 6),
                "speed_deficit_kmh": round(speed_deficit, 3),
                "low_speed_weight": round(max(0.0, min(1.0, (35.0 - speed) / 30.0)), 6),
            })
    write_csv(
        B04_TARGET_PROFILE_CSV,
        rows,
        [
            "segment_id", "direction", "segment_length_m", "target_speed_kmh",
            "target_travel_time_s", "reference_volume_vph", "speed_limit_kmh",
            "free_flow_travel_time_s", "travel_time_delay_ratio", "speed_deficit_kmh",
            "low_speed_weight",
        ],
    )
    summary = {
        "schema": "compact_v9_B04_target_profile.v1",
        "generated_at": utc_now(),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "output_csv": rel(B04_TARGET_PROFILE_CSV),
        "segment_direction_count": len(rows),
        "mean_target_speed_kmh": round(sum(float(row["target_speed_kmh"]) for row in rows) / max(len(rows), 1), 3),
        "mean_reference_volume_vph": round(sum(float(row["reference_volume_vph"]) for row in rows) / max(len(rows), 1), 3),
    }
    write_json(B04_TARGET_PROFILE_CSV.with_suffix(".summary.json"), summary)
    return summary


def mapping_by_segment_direction() -> dict[tuple[str, str], list[str]]:
    if not B04_MAPPING_CSV.is_file():
        build_mapping()
    grouped: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    for row in read_csv(B04_MAPPING_CSV):
        edge_id = row.get("edge_id", "")
        if not edge_id:
            continue
        score = safe_float(row.get("axis_position"), safe_float(row.get("edge_order")))
        grouped[(row.get("segment_id", ""), row.get("direction", ""))].append((score, edge_id))
    result: dict[tuple[str, str], list[str]] = {}
    for key, values in grouped.items():
        seen = []
        for _score, edge_id in sorted(values):
            if edge_id not in seen:
                seen.append(edge_id)
        result[key] = seen
    return result


def shortest_route(sumo_net: Any, from_edge: str, to_edge: str) -> list[str]:
    try:
        path, _cost = sumo_net.getShortestPath(sumo_net.getEdge(from_edge), sumo_net.getEdge(to_edge))
    except Exception:
        return []
    return [edge.getID() for edge in path] if path else []


def route_overlap(route: list[str], edge_set: set[str]) -> int:
    return sum(1 for edge_id in route if edge_id in edge_set)


def direction_main_route(sumo_net: Any, direction: str) -> list[str]:
    grouped = mapping_by_segment_direction()
    ordered_segments = [f"S{i}" for i in range(1, 23)]
    if direction == "upbound":
        segment_order = ordered_segments
    else:
        segment_order = list(reversed(ordered_segments))
    candidates = []
    main_edges = {edge for key, values in grouped.items() if key[1] == direction for edge in values}
    start_pool = []
    end_pool = []
    for segment in segment_order[:4]:
        start_pool.extend(grouped.get((segment, direction), []))
    for segment in segment_order[-4:]:
        end_pool.extend(grouped.get((segment, direction), []))
    for start in start_pool[:16]:
        for end in end_pool[-16:]:
            if start == end:
                continue
            route = shortest_route(sumo_net, start, end)
            if route:
                candidates.append((route_overlap(route, main_edges), -len(route), route))
    if not candidates:
        raise B04Error(f"main_route_not_found:{direction}")
    return sorted(candidates, reverse=True)[0][2]


def route_length_m(sumo_net: Any, route: list[str]) -> float:
    length = 0.0
    for edge_id in route:
        try:
            length += float(sumo_net.getEdge(edge_id).getLength())
        except Exception:
            continue
    return length


def passenger_edges(sumo_net: Any) -> list[str]:
    return [edge.getID() for edge in sumo_net.getEdges() if not edge.isSpecial() and edge.allows("passenger")]


def segment_edges(segment: str, direction: str, limit: int = 8) -> list[str]:
    rows = [
        row for row in read_csv(B04_MAPPING_CSV)
        if row.get("segment_id") == segment and row.get("direction") == direction and row.get("edge_id", "")
    ]
    rows.sort(key=lambda row: safe_float(row.get("matched_length_m"), safe_float(row.get("match_ratio"))), reverse=True)
    result: list[str] = []
    for row in rows:
        edge_id = row["edge_id"]
        if edge_id.startswith(":") or edge_id in result:
            continue
        result.append(edge_id)
        if len(result) >= limit:
            break
    return result


def od_window_segments(segment: str, direction: str, before: int, after: int) -> tuple[list[str], list[str]]:
    number = segment_number(segment)
    if direction == "upbound":
        starts = [f"S{i}" for i in range(max(1, number - before), max(1, number - 1) + 1)]
        ends = [f"S{i}" for i in range(min(22, number + 3), min(22, number + after) + 1)]
    else:
        starts = [f"S{i}" for i in range(min(22, number + before), min(22, number + 1) - 1, -1)]
        ends = [f"S{i}" for i in range(max(1, number - 3), max(1, number - after) - 1, -1)]
    if not starts:
        starts = [segment]
    if not ends:
        ends = [segment]
    return starts, ends


def targeted_od_routes(sumo_net: Any, segment: str, direction: str, max_routes: int = 3) -> list[list[str]]:
    screen_edge = screenline_edges().get((segment, direction))
    if not screen_edge:
        return []
    forbidden = terminal_source_edges()
    start_segments, end_segments = od_window_segments(segment, direction, before=5, after=8)
    starts = [edge for seg in start_segments for edge in segment_edges(seg, direction, limit=4)]
    ends = [edge for seg in end_segments for edge in segment_edges(seg, direction, limit=4)]
    target_edges = [screen_edge] + [edge for edge in segment_edges(segment, direction, limit=4) if edge != screen_edge]
    candidates: list[tuple[int, int, list[str]]] = []
    for target in target_edges:
        for start in starts + [target]:
            if start in forbidden:
                continue
            for end in ends:
                if end in forbidden or start == end:
                    continue
                route = shortest_route(sumo_net, start, end)
                if route and target in route and screen_edge in route and 3 <= len(route) <= 55:
                    score = (0 if route[0] != target else 1, len(route), route)
                    candidates.append(score)
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for _target_start_penalty, _length, route in sorted(candidates, key=lambda item: (item[0], item[1])):
        key = tuple(route)
        if key in seen:
            continue
        seen.add(key)
        unique.append(route)
        if len(unique) >= max_routes:
            break
    return unique


def terminal_safe_main_through_routes(sumo_net: Any, direction: str, max_routes: int = 8) -> list[list[str]]:
    grouped = mapping_by_segment_direction()
    terminal_edges = terminal_source_edges()
    main_edges = {edge for key, values in grouped.items() if key[1] == direction for edge in values}
    target_edges = {
        edge
        for segment in B4_CONTROL_TARGET_SEGMENTS
        for edge in grouped.get((segment, direction), [])
    }
    if direction == "upbound":
        start_segments = [f"S{i}" for i in range(2, 8)]
        end_segments = [f"S{i}" for i in range(15, 22)]
    else:
        start_segments = [f"S{i}" for i in range(21, 14, -1)]
        end_segments = [f"S{i}" for i in range(8, 1, -1)]
    starts = [
        edge
        for segment in start_segments
        for edge in segment_edges(segment, direction, limit=4)
        if edge not in terminal_edges
    ]
    ends = [
        edge
        for segment in end_segments
        for edge in segment_edges(segment, direction, limit=4)
        if edge not in terminal_edges
    ]
    candidates: list[tuple[int, int, int, list[str]]] = []
    for start in starts:
        for end in ends:
            if start == end:
                continue
            route = shortest_route(sumo_net, start, end)
            if not route or route[0] in terminal_edges or route[-1] in terminal_edges:
                continue
            main_overlap = route_overlap(route, main_edges)
            target_overlap = route_overlap(route, target_edges)
            if main_overlap >= 5 and target_overlap >= 1 and 8 <= len(route) <= 70:
                candidates.append((-target_overlap, -main_overlap, len(route), route))
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for _target_score, _main_score, _length, route in sorted(candidates):
        key = tuple(route)
        if key in seen:
            continue
        seen.add(key)
        unique.append(route)
        if len(unique) >= max_routes:
            break
    return unique


def route_templates(sumo_net: Any) -> tuple[dict[str, list[str]], dict[str, Any]]:
    up_route = direction_main_route(sumo_net, "upbound")
    down_route = direction_main_route(sumo_net, "downbound")
    templates = {
        "mainline_through_upbound": up_route,
        "mainline_through_downbound": down_route,
    }
    for direction, main_route in [("upbound", up_route), ("downbound", down_route)]:
        variant_count = 0
        for start_offset in range(1, min(7, max(len(main_route) - 8, 1))):
            for end_offset in range(0, min(5, max(len(main_route) - start_offset - 6, 1))):
                start = main_route[start_offset]
                end = main_route[len(main_route) - 1 - end_offset]
                if start == end:
                    continue
                route = shortest_route(sumo_net, start, end)
                if len(route) >= max(8, int(len(main_route) * 0.65)):
                    templates[f"mainline_through_{direction}_alt{variant_count:02d}"] = route
                    variant_count += 1
                    break
            if variant_count >= 4:
                break
    balanced_template_count = 0
    for direction in ["upbound", "downbound"]:
        for index, route in enumerate(terminal_safe_main_through_routes(sumo_net, direction, max_routes=8)):
            templates[f"mainline_through_{direction}_balanced{index:02d}"] = route
            balanced_template_count += 1
    grouped = mapping_by_segment_direction()
    for direction, main_route in [("upbound", up_route), ("downbound", down_route)]:
        index = {edge_id: idx for idx, edge_id in enumerate(main_route)}
        for segment_no in range(1, 23):
            segment = f"S{segment_no}"
            edge_ids = [edge for edge in grouped.get((segment, direction), []) if edge in index]
            if not edge_ids:
                continue
            lo = max(0, min(index[edge] for edge in edge_ids) - 3)
            hi = min(len(main_route) - 1, max(index[edge] for edge in edge_ids) + 7)
            if hi - lo >= 3:
                templates[f"segment_feeder_{direction}_{segment}"] = main_route[lo : hi + 1]
        mid_edges = [
            edge
            for segment_no in range(14, 19)
            for edge in grouped.get((f"S{segment_no}", direction), [])
            if edge in index
        ]
        if mid_edges:
            lo = max(0, min(index[edge] for edge in mid_edges) - 3)
            hi = min(len(main_route) - 1, max(index[edge] for edge in mid_edges) + 5)
            if hi - lo >= 5:
                templates[f"midcorridor_local_{direction}"] = main_route[lo : hi + 1]
    od_repair_template_count = 0
    od_queue_template_count = 0
    for segment, direction in OD_REPAIR_TARGETS:
        for idx, route in enumerate(targeted_od_routes(sumo_net, segment, direction, max_routes=3)):
            templates[f"od_repair_{direction}_{segment}_{idx:02d}"] = route
            od_repair_template_count += 1
    for segment, direction in OD_QUEUE_TUNED_TARGETS:
        for idx, route in enumerate(targeted_od_routes(sumo_net, segment, direction, max_routes=3)):
            templates[f"od_queue_{direction}_{segment}_{idx:02d}"] = route
            od_queue_template_count += 1
    all_edges = passenger_edges(sumo_net)
    main_set = set(up_route) | set(down_route)
    side_candidates = [edge for edge in all_edges if edge not in main_set and not edge.startswith(":")]
    added = 0
    step = max(1, len(side_candidates) // 80)
    sampled = side_candidates[::step][:80]
    for i, start in enumerate(sampled):
        if added >= 16:
            break
        for target in sampled[-20:]:
            if start == target:
                continue
            route = shortest_route(sumo_net, start, target)
            if 5 <= len(route) <= 35 and route_overlap(route, main_set) <= 2:
                templates[f"sideflow_background_{added:02d}"] = route
                added += 1
                break
    summary = {
        "upbound_route_edge_count": len(up_route),
        "downbound_route_edge_count": len(down_route),
        "template_count": len(templates),
        "sideflow_template_count": added,
        "balanced_main_through_template_count": balanced_template_count,
        "od_repair_template_count": od_repair_template_count,
        "od_queue_template_count": od_queue_template_count,
    }
    return templates, summary


def terminal_source_edges() -> set[str]:
    return {
        "585341903#0", "585341903#1", "585341906#0", "585341906#1",
        "585341906#2", "619147738#0", "619147738#1", "1206223946#0",
        "1206223946#1", "477063271",
    }


def candidate_net(candidate_name: str, templates: dict[str, list[str]] | None = None) -> Path:
    settings = CANDIDATES.get(candidate_name, {})
    if settings.get("net_profile") == "speed50_sanity":
        ensure_speed50_sanity_net(templates or {})
        return B04_SPEED50_NET
    return B04_NET


def speed50_relevant_edges(templates: dict[str, list[str]]) -> set[str]:
    edges: set[str] = set(firetruck_route_edges())
    for row in read_csv(B04_MAPPING_CSV):
        edge_id = row.get("edge_id", "")
        if edge_id and not edge_id.startswith(":"):
            edges.add(edge_id)
    for route_id, route_edges in templates.items():
        if route_id.startswith(("mainline_through_", "segment_feeder_", "midcorridor_local_", "od_repair_", "od_queue_")):
            edges.update(edge for edge in route_edges if edge and not edge.startswith(":"))
    return edges


def ensure_speed50_sanity_net(templates: dict[str, list[str]]) -> dict[str, Any]:
    if not B04_NET.is_file():
        adopt_green18()
    if B04_SPEED50_NET.is_file():
        return {
            "schema": "compact_v9_B04_speed50_sanity_net.v1",
            "output_net": rel(B04_SPEED50_NET),
            "already_exists": True,
        }
    relevant = speed50_relevant_edges(templates)
    tree = ET.parse(B04_NET)
    changed_edges = 0
    changed_lanes = 0
    for edge in tree.getroot().findall("edge"):
        edge_id = edge.get("id", "")
        if edge_id not in relevant:
            continue
        edge_changed = False
        for lane in edge.findall("lane"):
            current = safe_float(lane.get("speed"))
            if current > SPEED50_MPS:
                lane.set("speed", f"{SPEED50_MPS:.6f}")
                changed_lanes += 1
                edge_changed = True
        if edge_changed:
            changed_edges += 1
    B04_SPEED50_NET.parent.mkdir(parents=True, exist_ok=True)
    tree.write(B04_SPEED50_NET, encoding="utf-8", xml_declaration=True)
    summary = {
        "schema": "compact_v9_B04_speed50_sanity_net.v1",
        "generated_at": utc_now(),
        "source_net": rel(B04_NET),
        "output_net": rel(B04_SPEED50_NET),
        "relevant_edge_count": len(relevant),
        "changed_edge_count": changed_edges,
        "changed_lane_count": changed_lanes,
        "max_speed_kmh": 50.0,
        "policy": "Only B04 relevant mapped/firetruck/high-flow template edges are capped to CSV speed_limit_kmh=50.",
    }
    write_json(B04_SPEED50_NET.with_suffix(".summary.json"), summary)
    return summary


def add_vehicle(
    rows: list[dict[str, Any]],
    counts_by_template: dict[str, int],
    counts_by_source: dict[str, int],
    candidate_name: str,
    route_id: str,
    depart: float,
    vehicle_type: str,
    source_edge: str,
    max_template_vehicles: int = MAX_TEMPLATE_VEHICLES,
    max_source_vehicles: int = MAX_SOURCE_VEHICLES,
) -> bool:
    if counts_by_template.get(route_id, 0) >= max_template_vehicles:
        return False
    if counts_by_source.get(source_edge, 0) >= max_source_vehicles:
        return False
    counts_by_template[route_id] = counts_by_template.get(route_id, 0) + 1
    counts_by_source[source_edge] = counts_by_source.get(source_edge, 0) + 1
    rows.append({
        "id": f"{candidate_name}_{len(rows):05d}",
        "type": vehicle_type,
        "route": route_id,
        "depart": round(depart, 2),
    })
    return True


def build_depart_time(
    index: int,
    total: int,
    pulse_share: float,
    phase: int = 0,
    pulse_begin: float = 450.0,
    pulse_end: float = 900.0,
) -> float:
    if total <= 0:
        return 0.0
    use_pulse = pulse_share > 0 and (index % 100) < int(pulse_share * 100)
    if use_pulse:
        span = max(pulse_end - pulse_begin, 1.0)
        base = pulse_begin + span * ((index + 0.5) / max(total * max(pulse_share, 0.01), 1.0))
        return min(pulse_end, base + ((index * 17 + phase) % 31) * 0.21)
    return 10.0 + 3550.0 * ((index + 0.5) / max(total, 1)) + ((index * 11 + phase) % 23) * 0.33


def build_target_depart_time(index: int, total: int, phase: int, settings: dict[str, float]) -> float:
    mode = str(settings.get("target_pulse_mode", ""))
    if mode == "compressed":
        begin = float(settings.get("target_pulse_begin", 600.0))
        end = float(settings.get("target_pulse_end", 840.0))
        span = max(end - begin, 1.0)
        base = begin + span * ((index + 0.5) / max(total, 1))
        return min(end, base + ((index * 13 + phase) % 17) * 0.17)
    if mode == "two_burst":
        begin = float(settings.get("target_pulse_begin", 620.0))
        mid = float(settings.get("target_pulse_mid", 760.0))
        end = float(settings.get("target_pulse_end", 900.0))
        first_count = max(1, int(math.ceil(total * 0.55)))
        if index < first_count:
            span = max(mid - begin, 1.0)
            base = begin + span * ((index + 0.5) / first_count)
        else:
            second_total = max(total - first_count, 1)
            span = max(end - mid, 1.0)
            base = mid + span * ((index - first_count + 0.5) / second_total)
        return min(end, base + ((index * 13 + phase) % 19) * 0.13)
    return build_depart_time(
        index,
        total,
        float(settings["pulse_share"]),
        phase,
        float(settings.get("pulse_begin", 450.0)),
        float(settings.get("pulse_end", 900.0)),
    )


def route_ids_for_prefix(templates: dict[str, list[str]], prefix: str) -> list[str]:
    return sorted(route_id for route_id in templates if route_id == prefix or route_id.startswith(prefix + "_alt"))


def main_through_route_ids(templates: dict[str, list[str]], direction: str, settings: dict[str, float]) -> list[str]:
    prefix = f"mainline_through_{direction}"
    include_balanced = safe_float(settings.get("use_balanced_main_through")) > 0
    balanced_only = safe_float(settings.get("balanced_main_through_only")) > 0
    route_ids = []
    for route_id in sorted(route_id for route_id in templates if route_id == prefix or route_id.startswith(prefix + "_")):
        is_balanced = "_balanced" in route_id
        if is_balanced and not include_balanced:
            continue
        if balanced_only and not is_balanced:
            continue
        if not is_balanced and not (route_id == prefix or route_id.startswith(prefix + "_alt")):
            continue
        route_ids.append(route_id)
    return route_ids


def baseline_segment_recall() -> dict[tuple[str, str], dict[str, Any]]:
    path = METRICS_ROOT / "B04_j_balanced_recall/B04_segment_speed_recall.csv"
    if not path.is_file():
        return {}
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_csv(path):
        result[(row.get("segment_id", ""), row.get("direction", ""))] = row
    return result


def segment_demand_factor(row: dict[str, str], settings: dict[str, float], baseline: dict[tuple[str, str], dict[str, Any]]) -> float:
    if settings.get("segment_factor_mode") != "free_under_volume_boost":
        return 1.0
    key = (row.get("segment_id", ""), row.get("direction", ""))
    recall = baseline.get(key, {})
    cls = str(recall.get("class", ""))
    ref_volume = max(safe_float(row.get("reference_volume_vph")), 1.0)
    observed = safe_float(recall.get("screenline_count"), safe_float(recall.get("observed_count")))
    volume_ratio = observed / ref_volume
    if cls == "stop" and row.get("segment_id") != "S22":
        return float(settings.get("stop_cut", 0.5))
    if cls == "free" and volume_ratio < 0.35:
        return float(settings.get("free_under_volume_boost", 1.5))
    if cls == "free":
        return float(settings.get("free_boost", 1.2))
    if volume_ratio < 0.25:
        return 1.15
    return 1.0


def vehicle_type_attrs(settings: dict[str, float]) -> dict[str, str]:
    attrs = {
        "id": "b04_passenger",
        "vClass": "passenger",
        "length": "5.0",
        "accel": f"{float(settings.get('accel', 2.0)):.3f}",
        "decel": f"{float(settings.get('decel', 4.5)):.3f}",
        "sigma": f"{float(settings.get('sigma', 0.5)):.3f}",
        "departLane": "best",
    }
    for key, xml_key in [
        ("speed_factor", "speedFactor"),
        ("speed_dev", "speedDev"),
        ("tau", "tau"),
        ("min_gap", "minGap"),
    ]:
        if key in settings:
            attrs[xml_key] = f"{float(settings[key]):.3f}"
    return attrs


def build_demand_for_candidate(candidate_name: str, settings: dict[str, float], templates: dict[str, list[str]]) -> dict[str, Any]:
    profile = read_csv(B04_TARGET_PROFILE_CSV)
    root = ET.Element("routes")
    ET.SubElement(root, "vType", vehicle_type_attrs(settings))
    for route_id, edges in sorted(templates.items()):
        ET.SubElement(root, "route", {"id": route_id, "edges": " ".join(edges)})
    vehicle_rows: list[dict[str, Any]] = []
    counts_by_template: dict[str, int] = {}
    counts_by_source: dict[str, int] = {}
    route_source = {route_id: edges[0] for route_id, edges in templates.items() if edges}
    pulse_begin = float(settings.get("pulse_begin", 450.0))
    pulse_end = float(settings.get("pulse_end", 900.0))
    max_template = int(settings.get("template_cap", MAX_TEMPLATE_VEHICLES))
    max_source = int(settings.get("source_cap", MAX_SOURCE_VEHICLES))
    baseline = baseline_segment_recall()
    forbidden_sources = terminal_source_edges() if safe_float(settings.get("avoid_terminal_sources")) > 0 else set()
    forbidden_sinks = terminal_source_edges() if safe_float(settings.get("avoid_terminal_sinks")) > 0 else set()
    route_sink = {route_id: edges[-1] for route_id, edges in templates.items() if edges}

    def route_allowed(route_id: str) -> bool:
        return route_source.get(route_id, "") not in forbidden_sources and route_sink.get(route_id, "") not in forbidden_sinks

    profile_by_key = {(row["segment_id"], row["direction"]): row for row in profile}
    for direction in ["upbound", "downbound"]:
        through_ids = main_through_route_ids(templates, direction, settings)
        reference_vph = sum(safe_float(row.get("reference_volume_vph")) for row in profile if row.get("direction") == direction) / 22.0
        through_scale = float(settings.get(f"through_scale_{direction}", settings.get("through_scale", 0.0)))
        through_count = int(round(reference_vph * through_scale))
        for i in range(through_count):
            if through_ids:
                ordered_ids = through_ids[i % len(through_ids):] + through_ids[:i % len(through_ids)]
                through_id = next((route_id for route_id in ordered_ids if route_allowed(route_id)), through_ids[i % len(through_ids)])
            else:
                through_id = f"mainline_through_{direction}"
            if not route_allowed(through_id):
                continue
            add_vehicle(
                vehicle_rows,
                counts_by_template,
                counts_by_source,
                candidate_name,
                through_id,
                build_depart_time(i, through_count, float(settings["pulse_share"]), 3, pulse_begin, pulse_end),
                "b04_passenger",
                route_source.get(through_id, ""),
                max_template,
                max_source,
            )
    for row in profile:
        direction = str(row["direction"])
        feeder_share = float(settings.get(f"feeder_share_{direction}", settings.get("feeder_share", 0.0)))
        feeder_count = int(round(safe_float(row.get("reference_volume_vph")) * feeder_share * segment_demand_factor(row, settings, baseline) / 22.0))
        route_id = f"segment_feeder_{row['direction']}_{row['segment_id']}"
        if route_id not in templates:
            continue
        if not route_allowed(route_id):
            continue
        for i in range(feeder_count):
            add_vehicle(
                vehicle_rows,
                counts_by_template,
                counts_by_source,
                candidate_name,
                route_id,
                build_depart_time(
                    i,
                    feeder_count,
                    float(settings["pulse_share"]),
                    segment_number(str(row["segment_id"])),
                    pulse_begin,
                    pulse_end,
                ),
                "b04_passenger",
                route_source.get(route_id, ""),
                max_template,
                max_source,
            )
    mid_share = float(settings.get("midcorridor_share", 0.0))
    if mid_share > 0:
        for direction in ["upbound", "downbound"]:
            route_id = f"midcorridor_local_{direction}"
            if route_id not in templates or not route_allowed(route_id):
                continue
            reference_vph = sum(safe_float(row.get("reference_volume_vph")) for row in profile if row.get("direction") == direction) / 22.0
            mid_count = int(round(reference_vph * mid_share))
            for i in range(mid_count):
                add_vehicle(
                    vehicle_rows,
                    counts_by_template,
                    counts_by_source,
                    candidate_name,
                    route_id,
                    build_depart_time(i, mid_count, float(settings["pulse_share"]), 14, pulse_begin, pulse_end),
                    "b04_passenger",
                    route_source.get(route_id, ""),
                    max_template,
                    max_source,
                )
    for share_key, targets, prefix in [
        ("od_repair_share", OD_REPAIR_TARGETS, "od_repair"),
        ("od_queue_tuned_share", OD_QUEUE_TUNED_TARGETS, "od_queue"),
    ]:
        share = float(settings.get(share_key, 0.0))
        if share <= 0:
            continue
        for segment, direction in targets:
            target_routes = sorted(route_id for route_id in templates if route_id.startswith(f"{prefix}_{direction}_{segment}_"))
            if not target_routes:
                continue
            target_row = profile_by_key.get((segment, direction), {})
            reference_vph = safe_float(target_row.get("reference_volume_vph"))
            target_count = int(round(reference_vph * share / 22.0))
            for i in range(target_count):
                route_id = target_routes[i % len(target_routes)]
                if not route_allowed(route_id):
                    continue
                add_vehicle(
                    vehicle_rows,
                    counts_by_template,
                    counts_by_source,
                    candidate_name,
                    route_id,
                    build_target_depart_time(i, target_count, segment_number(segment), settings),
                    "b04_passenger",
                    route_source.get(route_id, ""),
                    max_template,
                    max_source,
                )
    side_templates = [route_id for route_id in templates if route_id.startswith("sideflow_background_")]
    side_count = int(round(sum(safe_float(row.get("reference_volume_vph")) for row in profile) / 44.0 * float(settings["sideflow_share"])))
    for i in range(side_count):
        if not side_templates:
            break
        route_id = side_templates[i % len(side_templates)]
        if not route_allowed(route_id):
            continue
        add_vehicle(
            vehicle_rows,
            counts_by_template,
            counts_by_source,
            candidate_name,
            route_id,
            build_depart_time(i, side_count, 0.15, 7, pulse_begin, pulse_end),
            "b04_passenger",
            route_source.get(route_id, ""),
            max_template,
            max_source,
        )
    vehicle_rows.sort(key=lambda row: (float(row["depart"]), str(row["id"])))
    for row in vehicle_rows:
        ET.SubElement(root, "vehicle", {
            "id": row["id"],
            "type": row["type"],
            "route": row["route"],
            "depart": f"{float(row['depart']):.2f}",
            "departLane": "best",
            "departPos": "random_free",
            "departSpeed": "max",
        })
    output = B04_DEMAND_DIR / f"background_routes_compact_v9_{candidate_name}.rou.xml"
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    summary = {
        "schema": "compact_v9_B04_demand_candidate.v1",
        "generated_at": utc_now(),
        "candidate": candidate_name,
        "output": rel(output),
        "settings": settings,
        "vehicle_count": len(vehicle_rows),
        "route_count": len(templates),
        "unique_source_count": len(counts_by_source),
        "top_sources": sorted(counts_by_source.items(), key=lambda item: (-item[1], item[0]))[:10],
        "top_templates": sorted(counts_by_template.items(), key=lambda item: (-item[1], item[0]))[:10],
        "vehicle_type_attrs": vehicle_type_attrs(settings),
        "depart_window_sec": [0, 3600],
        "pulse_window_sec": [pulse_begin, pulse_end],
        "target_pulse": {
            "mode": settings.get("target_pulse_mode", "default"),
            "begin": settings.get("target_pulse_begin", pulse_begin),
            "mid": settings.get("target_pulse_mid", ""),
            "end": settings.get("target_pulse_end", pulse_end),
        },
    }
    write_json(output.with_suffix(".summary.json"), summary)
    return summary


def build_demand(candidate_names: list[str] | None = None) -> dict[str, Any]:
    if not B04_NET.is_file():
        adopt_green18()
    if not B04_MAPPING_CSV.is_file():
        build_mapping()
    if not B04_TARGET_PROFILE_CSV.is_file():
        build_target_profile()
    sumo_net = read_sumo_net(B04_NET)
    templates, template_summary = route_templates(sumo_net)
    speed50_summary = ensure_speed50_sanity_net(templates)
    selected_names = candidate_names or list(CANDIDATES)
    summaries = []
    for candidate_name in selected_names:
        settings = CANDIDATES[candidate_name]
        summaries.append(build_demand_for_candidate(candidate_name, settings, templates))
    summary = {
        "schema": "compact_v9_B04_demand_sweep.v1",
        "generated_at": utc_now(),
        "net_file": rel(B04_NET),
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "template_summary": template_summary,
        "speed50_sanity_net": speed50_summary,
        "candidate_count": len(summaries),
        "candidate_names": selected_names,
        "candidates": summaries,
    }
    write_json(B04_DEMAND_DIR / "background_routes_compact_v9_B04_sweep.summary.json", summary)
    return summary


def write_sumo_config(candidate_name: str, demand_xml: Path, run_dir: Path) -> dict[str, Path]:
    additional = run_dir / "edge_data.add.xml"
    edge_data = run_dir / "edgeData.xml"
    lane_data = run_dir / "laneData.xml"
    tripinfo = run_dir / "tripinfo.xml"
    fcd = run_dir / "fcd.xml"
    summary = run_dir / "summary.xml"
    sumocfg = run_dir / "scenario.sumocfg"
    add_root = ET.Element("additional")
    ET.SubElement(add_root, "edgeData", {
        "id": "B04_edge_data",
        "file": str(edge_data),
        "begin": "0",
        "end": str(int(SIM_END_SEC)),
        "freq": str(EDGE_DATA_FREQ_SEC),
        "trackVehicles": "true",
        "withInternal": "false",
        "excludeEmpty": "false",
    })
    ET.SubElement(add_root, "laneData", {
        "id": "B04_lane_data",
        "file": str(lane_data),
        "begin": str(int(QUEUE_SAMPLE_BEGIN_SEC)),
        "end": str(int(QUEUE_SAMPLE_END_SEC)),
        "freq": str(EDGE_DATA_FREQ_SEC),
        "excludeEmpty": "false",
    })
    ET.ElementTree(add_root).write(additional, encoding="utf-8", xml_declaration=True)
    net_file = candidate_net(candidate_name)
    cfg = ET.Element("configuration")
    input_elem = ET.SubElement(cfg, "input")
    ET.SubElement(input_elem, "net-file", {"value": str(net_file)})
    ET.SubElement(input_elem, "route-files", {"value": f"{demand_xml},{B04_FIRETRUCK_ROUTE_XML}"})
    ET.SubElement(input_elem, "additional-files", {"value": str(additional)})
    output_elem = ET.SubElement(cfg, "output")
    ET.SubElement(output_elem, "tripinfo-output", {"value": str(tripinfo)})
    ET.SubElement(output_elem, "summary-output", {"value": str(summary)})
    time_elem = ET.SubElement(cfg, "time")
    ET.SubElement(time_elem, "begin", {"value": "0"})
    ET.SubElement(time_elem, "end", {"value": str(int(SIM_END_SEC))})
    processing = ET.SubElement(cfg, "processing")
    ET.SubElement(processing, "time-to-teleport", {"value": "1200"})
    report = ET.SubElement(cfg, "report")
    ET.SubElement(report, "no-step-log", {"value": "true"})
    ET.SubElement(report, "duration-log.disable", {"value": "true"})
    ET.ElementTree(cfg).write(sumocfg, encoding="utf-8", xml_declaration=True)
    return {
        "additional": additional,
        "edgeData": edge_data,
        "laneData": lane_data,
        "tripinfo": tripinfo,
        "fcd": fcd,
        "summary": summary,
        "sumocfg": sumocfg,
        "net": net_file,
    }


def parse_tripinfo(path: Path) -> dict[str, Any]:
    result = {
        "background_departed": 0,
        "background_arrived": 0,
        "background_teleported": 0,
        "emergency_arrived": False,
        "emergency_teleport": False,
        "emergency_duration": "",
    }
    if not path.is_file():
        return result
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        result["tripinfo_parse_error"] = True
        return result
    for trip in root.findall("tripinfo"):
        vehicle_id = trip.get("id", "")
        if vehicle_id == "emergency_0":
            result["emergency_arrived"] = trip.get("arrival") not in {"", None, "-1"}
            result["emergency_duration"] = safe_float(trip.get("duration"))
            if trip.get("arrival") in {"", None, "-1"}:
                result["emergency_teleport"] = True
        else:
            result["background_departed"] += 1
            if trip.get("arrival") not in {"", None, "-1"}:
                result["background_arrived"] += 1
    return result


def count_background_teleports(stderr: str) -> int:
    return sum(1 for line in stderr.splitlines() if "Teleporting vehicle" in line and "emergency_0" not in line)


def run_b0_candidate(candidate_name: str) -> dict[str, Any]:
    demand_xml = B04_DEMAND_DIR / f"background_routes_compact_v9_{candidate_name}.rou.xml"
    if not demand_xml.is_file():
        build_demand()
    run_dir = METRICS_ROOT / candidate_name / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = write_sumo_config(candidate_name, demand_xml, run_dir)
    emit_fcd = bool(CANDIDATES.get(candidate_name, {}).get("emit_fcd")) or os.environ.get("B04_EMIT_FCD") == "1"
    if paths["fcd"].is_file() and not emit_fcd:
        paths["fcd"].unlink()
    cmd = [
        find_executable("sumo"),
        "-c", str(paths["sumocfg"]),
        "--error-log", str(run_dir / "sumo_stderr.log"),
    ]
    if emit_fcd:
        cmd.extend([
            "--fcd-output", str(paths["fcd"]),
            "--fcd-output.geo", "true",
            "--fcd-output.distance", "true",
            "--device.fcd.period", str(QUEUE_SAMPLE_INTERVAL_SEC),
        ])
    timed_out = False
    with (run_dir / "sumo_stdout.log").open("w", encoding="utf-8") as stdout:
        try:
            completed = subprocess.run(cmd, check=False, text=True, stdout=stdout, stderr=subprocess.PIPE, timeout=240)
            return_code = completed.returncode
            stderr_pipe = completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            return_code = 124
            stderr_pipe = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
    trip = parse_tripinfo(paths["tripinfo"])
    stderr = (run_dir / "sumo_stderr.log").read_text(encoding="utf-8") if (run_dir / "sumo_stderr.log").is_file() else stderr_pipe
    trip["background_teleported"] = count_background_teleports(stderr)
    result = {
        "schema": "compact_v9_B04_b0_run.v1",
        "generated_at": utc_now(),
        "candidate": candidate_name,
        "mode": "B0",
        "parameter_id": "no_control",
        "sumo_exit_code": return_code,
        "route_error_count": 1 if "Error:" in stderr or return_code != 0 or timed_out else 0,
        "sumo_timeout": timed_out,
        **trip,
        "run_dir": rel(run_dir),
        "demand_xml": rel(demand_xml),
        "net_file": rel(paths["net"]),
        "edgeData": rel(paths["edgeData"]),
        "laneData": rel(paths["laneData"]),
        "fcd": rel(paths["fcd"]) if emit_fcd else "",
        "fcd_enabled": emit_fcd,
        "measurement_mode": "fcd_debug" if emit_fcd else "lightweight_edge_lane_data",
        "tripinfo": rel(paths["tripinfo"]),
        "stderr_tail": stderr[-2000:],
    }
    write_json(METRICS_ROOT / candidate_name / "b0_run_summary.json", result)
    return result


def edge_data_by_edge(path: Path) -> dict[str, list[dict[str, float]]]:
    data: dict[str, list[dict[str, float]]] = defaultdict(list)
    if not path.is_file():
        return data
    for _event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == "edge" and elem.get("id"):
            data[elem.get("id", "")].append({
                "speed": safe_float(elem.get("speed")),
                "entered": safe_float(elem.get("entered")),
                "left": safe_float(elem.get("left")),
                "departed": safe_float(elem.get("departed")),
                "arrived": safe_float(elem.get("arrived")),
                "density": safe_float(elem.get("density")),
                "occupancy": safe_float(elem.get("occupancy")),
                "traveltime": safe_float(elem.get("traveltime")),
                "waitingTime": safe_float(elem.get("waitingTime")),
                "timeLoss": safe_float(elem.get("timeLoss")),
                "sampledSeconds": safe_float(elem.get("sampledSeconds")),
            })
        elem.clear()
    return data


def lane_data_by_edge(path: Path) -> dict[str, list[dict[str, float]]]:
    data: dict[str, list[dict[str, float]]] = defaultdict(list)
    if not path.is_file():
        return data
    for _event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == "lane" and elem.get("id"):
            edge_id = edge_from_lane(elem.get("id", ""))
            data[edge_id].append({
                "speed": safe_float(elem.get("speed")),
                "density": safe_float(elem.get("density")),
                "occupancy": safe_float(elem.get("occupancy")),
                "traveltime": safe_float(elem.get("traveltime")),
                "waitingTime": safe_float(elem.get("waitingTime")),
                "timeLoss": safe_float(elem.get("timeLoss")),
                "sampledSeconds": safe_float(elem.get("sampledSeconds")),
            })
        elem.clear()
    return data


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * pct))))
    return ordered[index]


def edge_from_lane(lane_id: str) -> str:
    if not lane_id or "_" not in lane_id:
        return lane_id
    return lane_id.rsplit("_", 1)[0]


def screenline_edges() -> dict[tuple[str, str], str]:
    best: dict[tuple[str, str], tuple[float, str]] = {}
    for row in read_csv(B04_MAPPING_CSV):
        key = (row.get("segment_id", ""), row.get("direction", ""))
        edge_id = row.get("edge_id", "")
        if not edge_id or edge_id.startswith(":"):
            continue
        score = safe_float(row.get("matched_length_m"), safe_float(row.get("match_ratio")))
        if key not in best or score > best[key][0]:
            best[key] = (score, edge_id)
    return {key: edge_id for key, (_score, edge_id) in best.items()}


def firetruck_route_edges() -> list[str]:
    if B04_FIRETRUCK_ROUTE_CSV.is_file():
        rows = read_csv(B04_FIRETRUCK_ROUTE_CSV)
        if rows and rows[0].get("route_edges"):
            return str(rows[0]["route_edges"]).split()
    if B04_FIRETRUCK_ROUTE_XML.is_file():
        root = ET.parse(B04_FIRETRUCK_ROUTE_XML).getroot()
        route = root.find("route")
        if route is not None and route.get("edges"):
            return str(route.get("edges")).split()
    return []


def monitored_tls_edges() -> dict[str, dict[str, Any]]:
    protected = set(firetruck_route_edges())
    for edge_ids in mapping_by_segment_direction().values():
        protected.update(edge_ids)
    result: dict[str, dict[str, Any]] = {}
    if not B04_NET.is_file():
        return result
    for _event, elem in ET.iterparse(B04_NET, events=("end",)):
        if elem.tag == "connection":
            from_edge = elem.get("from", "")
            if from_edge in protected and elem.get("tl"):
                result[from_edge] = {
                    "tl": elem.get("tl", ""),
                    "linkIndex": elem.get("linkIndex", ""),
                    "to": elem.get("to", ""),
                }
        elem.clear()
    return result


def lane_stats(sumo_net: Any, edge_id: str) -> tuple[float, int]:
    try:
        edge = sumo_net.getEdge(edge_id)
        lanes = edge.getLanes()
        return float(edge.getLength()), max(len(lanes), 1)
    except Exception:
        return 1.0, 1


def fcd_segment_recall(path: Path) -> dict[str, Any]:
    grouped = mapping_by_segment_direction()
    edge_to_keys: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, edge_ids in grouped.items():
        for edge_id in edge_ids:
            edge_to_keys[edge_id].append(key)
    screenlines = screenline_edges()
    screenline_to_key = {edge_id: key for key, edge_id in screenlines.items()}
    monitors = monitored_tls_edges()
    sumo_net = read_sumo_net(B04_NET)
    monitor_lengths = {edge_id: lane_stats(sumo_net, edge_id) for edge_id in monitors}
    vehicle_times: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(dict)
    screenline_seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    queue_samples: dict[str, dict[str, float]] = defaultdict(lambda: {
        "sample_count": 0,
        "queue_max_m": 0.0,
        "halted_count_max": 0.0,
        "slow_count_max": 0.0,
        "density_max": 0.0,
        "mean_waiting_vehicle_count_max": 0.0,
    })
    if not path.is_file():
        return {"segments": {}, "queue_edges": {}, "summary": {"fcd_missing": True}}
    for _event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag != "timestep":
            continue
        t = safe_float(elem.get("time"))
        per_monitor: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0.0, "halted": 0.0, "slow": 0.0})
        sample_queue = (
            QUEUE_SAMPLE_BEGIN_SEC <= t <= QUEUE_SAMPLE_END_SEC
            and int(round(t)) % QUEUE_SAMPLE_INTERVAL_SEC == 0
        )
        for vehicle in elem.findall("vehicle"):
            vehicle_id = vehicle.get("id", "")
            if vehicle_id == "emergency_0":
                continue
            edge_id = edge_from_lane(vehicle.get("lane", ""))
            if not edge_id or edge_id.startswith(":"):
                continue
            speed = safe_float(vehicle.get("speed"))
            for key in edge_to_keys.get(edge_id, []):
                slot = vehicle_times[key].setdefault(vehicle_id, [t, t])
                slot[1] = t
            if edge_id in screenline_to_key:
                screenline_seen[screenline_to_key[edge_id]].add(vehicle_id)
            if sample_queue and edge_id in monitors:
                per_monitor[edge_id]["count"] += 1.0
                if speed < 0.1:
                    per_monitor[edge_id]["halted"] += 1.0
                if speed < 5.0:
                    per_monitor[edge_id]["slow"] += 1.0
        for edge_id, sample in per_monitor.items():
            length_m, lane_count = monitor_lengths.get(edge_id, (1.0, 1))
            density = sample["count"] / max((length_m / 1000.0) * lane_count, 0.001)
            queue_m = max(sample["halted"], sample["slow"]) * HEADWAY_M / max(lane_count, 1)
            stats = queue_samples[edge_id]
            stats["sample_count"] += 1
            stats["queue_max_m"] = max(stats["queue_max_m"], queue_m)
            stats["halted_count_max"] = max(stats["halted_count_max"], sample["halted"])
            stats["slow_count_max"] = max(stats["slow_count_max"], sample["slow"])
            stats["density_max"] = max(stats["density_max"], density)
            stats["mean_waiting_vehicle_count_max"] = max(stats["mean_waiting_vehicle_count_max"], sample["count"])
        elem.clear()
    target = {(row["segment_id"], row["direction"]): row for row in read_csv(B04_TARGET_PROFILE_CSV)}
    segment_metrics: dict[tuple[str, str], dict[str, Any]] = {}
    for key, by_vehicle in vehicle_times.items():
        durations = [last - first + 1.0 for first, last in by_vehicle.values() if last > first]
        target_row = target.get(key, {})
        length_m = safe_float(target_row.get("segment_length_m"))
        median_tt = percentile(durations, 0.5)
        traversal_speed = (length_m / max(median_tt, 0.1)) * 3.6 if median_tt > 0 else 0.0
        segment_metrics[key] = {
            "fcd_traversal_sample_count": len(durations),
            "fcd_traversal_time_median_s": round(median_tt, 3),
            "fcd_traversal_time_p75_s": round(percentile(durations, 0.75), 3),
            "fcd_traversal_speed_kmh": round(traversal_speed, 3),
            "screenline_edge": screenlines.get(key, ""),
            "screenline_count": len(screenline_seen.get(key, set())),
        }
    segment_queue: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {
        "runtime_queue_max_m": 0.0,
        "runtime_slow_count_max": 0.0,
        "runtime_halted_count_max": 0.0,
        "runtime_density_max": 0.0,
        "runtime_monitor_edge_count": 0,
    })
    for key, edge_ids in grouped.items():
        for edge_id in edge_ids:
            stats = queue_samples.get(edge_id)
            if not stats:
                continue
            q = segment_queue[key]
            q["runtime_queue_max_m"] = max(q["runtime_queue_max_m"], stats["queue_max_m"])
            q["runtime_slow_count_max"] = max(q["runtime_slow_count_max"], stats["slow_count_max"])
            q["runtime_halted_count_max"] = max(q["runtime_halted_count_max"], stats["halted_count_max"])
            q["runtime_density_max"] = max(q["runtime_density_max"], stats["density_max"])
            q["runtime_monitor_edge_count"] += 1
    for key, queue in segment_queue.items():
        segment_metrics.setdefault(key, {}).update({k: round(v, 3) if isinstance(v, float) else v for k, v in queue.items()})
    return {
        "segments": segment_metrics,
        "queue_edges": {edge_id: {**monitors.get(edge_id, {}), **{k: round(v, 3) for k, v in stats.items()}} for edge_id, stats in queue_samples.items()},
        "summary": {
            "fcd_missing": False,
            "screenline_segment_count": len(screenlines),
            "monitored_tls_edge_count": len(monitors),
            "queue_edge_count": len(queue_samples),
            "queue_window_sec": [QUEUE_SAMPLE_BEGIN_SEC, QUEUE_SAMPLE_END_SEC],
            "queue_sample_interval_sec": QUEUE_SAMPLE_INTERVAL_SEC,
        },
    }


def lightweight_segment_metrics(edge_data: dict[str, list[dict[str, float]]], lane_data: dict[str, list[dict[str, float]]]) -> dict[str, Any]:
    grouped = mapping_by_segment_direction()
    screenlines = screenline_edges()
    sumo_net = read_sumo_net(B04_NET)
    segment_metrics: dict[tuple[str, str], dict[str, Any]] = {}
    for key, screen_edge in screenlines.items():
        counts = [
            max(row.get("entered", 0.0), row.get("left", 0.0), row.get("departed", 0.0), row.get("arrived", 0.0))
            for row in edge_data.get(screen_edge, [])
        ]
        segment_metrics[key] = {
            "screenline_edge": screen_edge,
            "screenline_count": round(max(counts) if counts else 0.0, 3),
        }
    for key, edge_ids in grouped.items():
        queue_max_m = 0.0
        slow_count_max = 0.0
        halted_count_max = 0.0
        density_max = 0.0
        occupancy_max = 0.0
        waiting_max = 0.0
        low_speed_interval_count = 0
        monitored_edge_count = 0
        for edge_id in edge_ids:
            length_m, lane_count = lane_stats(sumo_net, edge_id)
            samples = list(edge_data.get(edge_id, [])) + list(lane_data.get(edge_id, []))
            edge_had_queue_sample = False
            for row in samples:
                speed_kmh = safe_float(row.get("speed")) * 3.6
                density = safe_float(row.get("density"))
                occupancy = safe_float(row.get("occupancy"))
                waiting = safe_float(row.get("waitingTime"))
                time_loss = safe_float(row.get("timeLoss"))
                density_max = max(density_max, density)
                occupancy_max = max(occupancy_max, occupancy)
                waiting_max = max(waiting_max, waiting, time_loss)
                if speed_kmh > 0 and speed_kmh < 5.0:
                    low_speed_interval_count += 1
                    estimated_vehicle_count = density * max(length_m / 1000.0, 0.001) * max(lane_count, 1)
                    slow_count_max = max(slow_count_max, estimated_vehicle_count)
                    if waiting > 0 or occupancy >= 10.0:
                        halted_count_max = max(halted_count_max, estimated_vehicle_count)
                    queue_max_m = max(queue_max_m, estimated_vehicle_count * HEADWAY_M / max(lane_count, 1))
                    edge_had_queue_sample = True
            if edge_had_queue_sample:
                monitored_edge_count += 1
        segment_metrics.setdefault(key, {}).update({
            "runtime_queue_max_m": round(queue_max_m, 3),
            "runtime_slow_count_max": round(slow_count_max, 3),
            "runtime_halted_count_max": round(halted_count_max, 3),
            "runtime_density_max": round(density_max, 3),
            "runtime_occupancy_max": round(occupancy_max, 3),
            "runtime_waiting_or_timeloss_max": round(waiting_max, 3),
            "low_speed_interval_count": low_speed_interval_count,
            "runtime_monitor_edge_count": monitored_edge_count,
        })
    return {
        "segments": segment_metrics,
        "summary": {
            "fcd_missing": True,
            "measurement_mode": "lightweight_edge_lane_data",
            "edge_data_freq_sec": EDGE_DATA_FREQ_SEC,
            "screenline_segment_count": len(screenlines),
        },
    }


def segment_speed_rows(edge_data: dict[str, list[dict[str, float]]], fcd_metrics: dict[tuple[str, str], dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    grouped = mapping_by_segment_direction()
    target = {(row["segment_id"], row["direction"]): row for row in read_csv(B04_TARGET_PROFILE_CSV)}
    fcd_metrics = fcd_metrics or {}
    rows: list[dict[str, Any]] = []
    for key, edge_ids in sorted(grouped.items(), key=lambda item: (segment_number(item[0][0]), item[0][1])):
        samples = []
        travel_samples = []
        edge_travel_times = []
        edge_counts = []
        count = 0.0
        density = 0.0
        occupancy = 0.0
        for edge_id in edge_ids:
            per_edge_travel_samples = []
            for row in edge_data.get(edge_id, []):
                entered = max(row["entered"], row["left"], row["departed"], row["arrived"])
                weight = max(row["sampledSeconds"], entered, 1.0)
                if row["speed"] > 0:
                    samples.append((row["speed"] * 3.6, weight))
                if row["traveltime"] > 0:
                    travel_samples.append((row["traveltime"], max(entered, 1.0)))
                    per_edge_travel_samples.append((row["traveltime"], max(entered, 1.0)))
                count += entered
                edge_counts.append(entered)
                density = max(density, row["density"])
                occupancy = max(occupancy, row["occupancy"])
            if per_edge_travel_samples:
                edge_travel = sum(value * weight for value, weight in per_edge_travel_samples) / sum(weight for _value, weight in per_edge_travel_samples)
                edge_travel_times.append(edge_travel)
        simulated_speed = sum(speed * weight for speed, weight in samples) / sum(weight for _speed, weight in samples) if samples else 0.0
        simulated_travel_time = sum(edge_travel_times) if edge_travel_times else 0.0
        if simulated_travel_time <= 0 and travel_samples:
            simulated_travel_time = sum(value * weight for value, weight in travel_samples) / sum(weight for _value, weight in travel_samples)
        target_row = target.get(key, {})
        reference_speed = safe_float(target_row.get("target_speed_kmh"))
        reference_travel_time = safe_float(target_row.get("target_travel_time_s"))
        reference_volume = safe_float(target_row.get("reference_volume_vph"))
        observed_count = sorted(edge_counts)[len(edge_counts) // 2] if edge_counts else 0.0
        fcd = fcd_metrics.get(key, {})
        fcd_sample_count = int(safe_float(fcd.get("fcd_traversal_sample_count")))
        primary_speed = safe_float(fcd.get("fcd_traversal_speed_kmh")) if fcd_sample_count >= FCD_MIN_SEGMENT_SAMPLE_COUNT else simulated_speed
        primary_travel_time = safe_float(fcd.get("fcd_traversal_time_median_s")) if fcd_sample_count >= FCD_MIN_SEGMENT_SAMPLE_COUNT else simulated_travel_time
        primary_count = safe_float(fcd.get("screenline_count"), observed_count)
        cls = "target_like"
        if primary_speed <= 0:
            cls = "missing"
        elif primary_speed > 70.0:
            cls = "metric_invalid"
        elif primary_speed > 60.0:
            cls = "speed_sanity_fail"
        elif primary_speed < 5.0:
            cls = "stop"
        elif primary_speed > 35.0:
            cls = "free"
        elif abs(primary_speed - reference_speed) > 8.0:
            cls = "off_target"
        rows.append({
            "segment_id": key[0],
            "direction": key[1],
            "reference_speed_kmh": round(reference_speed, 3),
            "simulated_speed_kmh": round(primary_speed, 3),
            "speed_error_kmh": round(primary_speed - reference_speed, 3),
            "edgeData_speed_kmh": round(simulated_speed, 3),
            "reference_travel_time_s": round(reference_travel_time, 3),
            "simulated_travel_time_s": round(primary_travel_time, 3),
            "travel_time_error_s": round(primary_travel_time - reference_travel_time, 3),
            "edgeData_travel_time_s": round(simulated_travel_time, 3),
            "reference_volume_vph": round(reference_volume, 3),
            "observed_count": round(primary_count, 3),
            "volume_error": round(primary_count - reference_volume, 3),
            "edgeData_observed_count": round(observed_count, 3),
            "screenline_edge": fcd.get("screenline_edge", ""),
            "screenline_count": round(primary_count, 3),
            "raw_edge_count_sum": round(count, 3),
            "edge_count": len(edge_ids),
            "max_density": round(density, 3),
            "max_occupancy": round(occupancy, 6),
            "fcd_traversal_sample_count": fcd_sample_count,
            "fcd_traversal_time_median_s": fcd.get("fcd_traversal_time_median_s", ""),
            "fcd_traversal_time_p75_s": fcd.get("fcd_traversal_time_p75_s", ""),
            "runtime_queue_max_m": fcd.get("runtime_queue_max_m", ""),
            "runtime_slow_count_max": fcd.get("runtime_slow_count_max", ""),
            "runtime_halted_count_max": fcd.get("runtime_halted_count_max", ""),
            "runtime_density_max": fcd.get("runtime_density_max", ""),
            "runtime_occupancy_max": fcd.get("runtime_occupancy_max", ""),
            "runtime_waiting_or_timeloss_max": fcd.get("runtime_waiting_or_timeloss_max", ""),
            "low_speed_interval_count": fcd.get("low_speed_interval_count", ""),
            "target_queue_proxy": round(safe_float(target_row.get("low_speed_weight")) * safe_float(target_row.get("travel_time_delay_ratio")), 6),
            "sumo_queue_proxy": round(
                (max(0.0, 35.0 - primary_speed) / 35.0)
                * (1.0 + min(max(density, safe_float(fcd.get("runtime_density_max"))) / 100.0, 2.0) + min(max(occupancy, safe_float(fcd.get("runtime_occupancy_max"))) / 100.0, 1.0)),
                6,
            ),
            "class": cls,
        })
    return rows


def queue_audit_from_edges(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def dense(row: dict[str, Any]) -> bool:
        return (
            safe_float(row.get("max_density")) >= 20.0
            or safe_float(row.get("max_occupancy")) >= 12.0
            or safe_float(row.get("runtime_density_max")) >= 20.0
            or safe_float(row.get("runtime_occupancy_max")) >= 12.0
            or safe_float(row.get("runtime_waiting_or_timeloss_max")) >= 30.0
            or safe_float(row.get("runtime_queue_max_m")) >= 40.0
            or safe_float(row.get("runtime_slow_count_max")) >= 8.0
            or safe_float(row.get("low_speed_interval_count")) >= 2.0
        )

    dense_count = sum(1 for row in rows if dense(row))
    low_speed_dense = sum(1 for row in rows if safe_float(row.get("simulated_speed_kmh")) <= 20.0 and dense(row))
    low_speed_sparse = sum(1 for row in rows if safe_float(row.get("simulated_speed_kmh")) <= 20.0 and not dense(row))
    keyed_rows = [row for row in rows if row.get("segment_id") and row.get("direction")]
    target_top = {f"{row['segment_id']}:{row['direction']}" for row in sorted(keyed_rows, key=lambda row: safe_float(row.get("target_queue_proxy")), reverse=True)[:10]}
    sumo_top = {f"{row['segment_id']}:{row['direction']}" for row in sorted(keyed_rows, key=lambda row: safe_float(row.get("sumo_queue_proxy")), reverse=True)[:10]}
    overlap = len(target_top & sumo_top)
    return {
        "dense_segment_direction_count": dense_count,
        "low_speed_dense_count": low_speed_dense,
        "low_speed_sparse_count": low_speed_sparse,
        "target_sumo_queue_top10_overlap": overlap,
        "target_sumo_queue_top10_overlap_ratio": round(overlap / 10.0, 3) if keyed_rows else 0.0,
        "classification": "physical_queue_congestion" if low_speed_dense >= 2 else ("speed_only_delay" if low_speed_sparse >= 2 else "weak_congestion"),
    }


def diagnostic_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def key_name(row: dict[str, Any]) -> str:
        return f"{row['segment_id']}:{row['direction']}"

    speed_top = sorted(rows, key=lambda row: abs(safe_float(row.get("speed_error_kmh"))), reverse=True)[:5]
    travel_top = sorted(rows, key=lambda row: abs(safe_float(row.get("travel_time_error_s"))), reverse=True)[:5]
    volume_top = sorted(rows, key=lambda row: abs(safe_float(row.get("volume_error"))), reverse=True)[:5]
    free_rows = [row for row in rows if row.get("class") == "free"]
    stop_rows = [row for row in rows if row.get("class") == "stop"]
    return {
        "speed_error_top5": [{**row, "segment_key": key_name(row)} for row in speed_top],
        "travel_time_error_top5": [{**row, "segment_key": key_name(row)} for row in travel_top],
        "volume_error_top5": [{**row, "segment_key": key_name(row)} for row in volume_top],
        "free_flow_segment_list": [key_name(row) for row in free_rows],
        "stop_queue_segment_list": [key_name(row) for row in stop_rows],
        "upbound_overcongested": [key_name(row) for row in rows if row.get("direction") == "upbound" and safe_float(row.get("simulated_speed_kmh")) < max(5.0, safe_float(row.get("reference_speed_kmh")) - 8.0)],
        "downbound_freeflow": [key_name(row) for row in rows if row.get("direction") == "downbound" and row.get("class") == "free"],
    }


def demand_route_coverage(candidate_name: str) -> tuple[dict[str, list[str]], dict[str, int]]:
    demand_xml = B04_DEMAND_DIR / f"background_routes_compact_v9_{candidate_name}.rou.xml"
    if not demand_xml.is_file():
        return {}, {}
    root = ET.parse(demand_xml).getroot()
    routes = {route.get("id", ""): route.get("edges", "").split() for route in root.findall("route") if route.get("id")}
    vehicles_by_route: dict[str, int] = defaultdict(int)
    for vehicle in root.findall("vehicle"):
        route_id = vehicle.get("route", "")
        if route_id:
            vehicles_by_route[route_id] += 1
    return routes, dict(vehicles_by_route)


def classify_free_flow_od(route_vehicle_count: int, screenline_count: float, density: float, occupancy: float, low_speed_intervals: float, edge_count: int) -> str:
    if edge_count <= 1 or screenline_count <= 0:
        return "measurement_warn"
    if route_vehicle_count <= 0:
        return "od_missing"
    if route_vehicle_count < 40:
        return "od_undercovered"
    if low_speed_intervals < 2 and (density >= 20.0 or occupancy >= 12.0):
        return "queue_not_forming"
    return "od_undercovered"


def free_flow_od_audit(candidate_name: str, speed_rows: list[dict[str, Any]]) -> dict[str, Any]:
    routes, vehicles_by_route = demand_route_coverage(candidate_name)
    audit_rows: list[dict[str, Any]] = []
    for row in speed_rows:
        if row.get("class") != "free":
            continue
        screenline_edge = str(row.get("screenline_edge", ""))
        crossing_routes = [
            route_id for route_id, edges in routes.items()
            if screenline_edge and screenline_edge in edges
        ]
        route_vehicle_count = sum(vehicles_by_route.get(route_id, 0) for route_id in crossing_routes)
        reason = classify_free_flow_od(
            route_vehicle_count,
            safe_float(row.get("screenline_count")),
            safe_float(row.get("runtime_density_max"), safe_float(row.get("max_density"))),
            safe_float(row.get("runtime_occupancy_max"), safe_float(row.get("max_occupancy"))),
            safe_float(row.get("low_speed_interval_count")),
            int(safe_float(row.get("edge_count"))),
        )
        audit_rows.append({
            "segment_id": row.get("segment_id", ""),
            "direction": row.get("direction", ""),
            "reason": reason,
            "screenline_edge": screenline_edge,
            "route_vehicle_count": route_vehicle_count,
            "crossing_route_count": len(crossing_routes),
            "screenline_count": row.get("screenline_count", ""),
            "raw_edge_count_sum": row.get("raw_edge_count_sum", ""),
            "simulated_speed_kmh": row.get("simulated_speed_kmh", ""),
            "reference_speed_kmh": row.get("reference_speed_kmh", ""),
            "runtime_density_max": row.get("runtime_density_max", ""),
            "runtime_occupancy_max": row.get("runtime_occupancy_max", ""),
            "low_speed_interval_count": row.get("low_speed_interval_count", ""),
            "top_crossing_routes": ";".join(sorted(crossing_routes, key=lambda route_id: vehicles_by_route.get(route_id, 0), reverse=True)[:5]),
        })
    counts: dict[str, int] = defaultdict(int)
    for row in audit_rows:
        counts[str(row["reason"])] += 1
    return {
        "rows": audit_rows,
        "summary": {
            "free_flow_count": len(audit_rows),
            "od_missing_count": counts.get("od_missing", 0),
            "od_undercovered_count": counts.get("od_undercovered", 0),
            "queue_not_forming_count": counts.get("queue_not_forming", 0),
            "measurement_warn_count": counts.get("measurement_warn", 0),
        },
    }


def validate_candidate(candidate_name: str) -> dict[str, Any]:
    run_summary_path = METRICS_ROOT / candidate_name / "b0_run_summary.json"
    run = read_json(run_summary_path) if run_summary_path.is_file() else run_b0_candidate(candidate_name)
    stderr_path = PROJECT_ROOT / run.get("run_dir", "") / "sumo_stderr.log"
    if stderr_path.is_file():
        run["background_teleported"] = count_background_teleports(stderr_path.read_text(encoding="utf-8"))
    if not B04_TARGET_PROFILE_CSV.is_file():
        build_target_profile()
    edge_data = edge_data_by_edge(PROJECT_ROOT / run["edgeData"])
    lane_data = lane_data_by_edge(PROJECT_ROOT / run.get("laneData", ""))
    if run.get("fcd_enabled") and run.get("fcd"):
        measurement = fcd_segment_recall(PROJECT_ROOT / run["fcd"])
    else:
        measurement = lightweight_segment_metrics(edge_data, lane_data)
    speed_rows = segment_speed_rows(edge_data, measurement.get("segments", {}))
    queue = queue_audit_from_edges(speed_rows)
    od_audit = free_flow_od_audit(candidate_name, speed_rows)
    mae = sum(abs(safe_float(row["speed_error_kmh"])) for row in speed_rows) / max(len(speed_rows), 1)
    travel_mae = sum(abs(safe_float(row["travel_time_error_s"])) for row in speed_rows) / max(len(speed_rows), 1)
    stop_count = sum(1 for row in speed_rows if row["class"] == "stop" and row["segment_id"] != "S22")
    free_count = sum(1 for row in speed_rows if row["class"] == "free")
    speed_sanity_fail_count = sum(1 for row in speed_rows if row["class"] == "speed_sanity_fail")
    metric_invalid_count = sum(1 for row in speed_rows if row["class"] == "metric_invalid")
    arrived_ratio = safe_float(run.get("background_arrived")) / max(safe_float(run.get("background_departed")), 1.0)
    status = "PASS" if (
        int(run.get("sumo_exit_code", 1)) == 0
        and int(run.get("route_error_count", 1)) == 0
        and bool(run.get("emergency_arrived"))
        and not bool(run.get("emergency_teleport"))
        and int(run.get("background_teleported", 99)) < 10
        and arrived_ratio >= 0.98
        and metric_invalid_count == 0
        and speed_sanity_fail_count == 0
        and free_count <= 5
        and stop_count <= 2
        and mae <= 8.0
        and travel_mae <= 15.0
        and queue["target_sumo_queue_top10_overlap"] >= 7
        and queue["classification"] == "physical_queue_congestion"
    ) else ("WARN" if (
        int(run.get("sumo_exit_code", 1)) == 0
        and int(run.get("route_error_count", 1)) == 0
        and bool(run.get("emergency_arrived"))
        and not bool(run.get("emergency_teleport"))
        and int(run.get("background_teleported", 99)) < 10
        and arrived_ratio >= 0.98
        and metric_invalid_count == 0
        and speed_sanity_fail_count <= 2
        and free_count <= 10
        and stop_count <= 4
        and mae <= 12.0
        and travel_mae <= 25.0
        and queue["target_sumo_queue_top10_overlap"] >= 5
        and queue["classification"] == "physical_queue_congestion"
    ) else "FAIL")
    out_dir = METRICS_ROOT / candidate_name
    speed_csv = out_dir / "B04_segment_speed_recall.csv"
    write_csv(speed_csv, speed_rows, [
        "segment_id", "direction", "reference_speed_kmh", "simulated_speed_kmh",
        "speed_error_kmh", "edgeData_speed_kmh", "reference_travel_time_s", "simulated_travel_time_s",
        "travel_time_error_s", "edgeData_travel_time_s", "reference_volume_vph", "observed_count", "volume_error",
        "edgeData_observed_count", "screenline_edge", "screenline_count",
        "raw_edge_count_sum", "edge_count", "max_density", "max_occupancy",
        "fcd_traversal_sample_count", "fcd_traversal_time_median_s", "fcd_traversal_time_p75_s",
        "runtime_queue_max_m", "runtime_slow_count_max", "runtime_halted_count_max", "runtime_density_max",
        "runtime_occupancy_max", "runtime_waiting_or_timeloss_max", "low_speed_interval_count",
        "target_queue_proxy", "sumo_queue_proxy", "class",
    ])
    queue_edge_csv = out_dir / "B04_runtime_queue_edges.csv"
    queue_edge_rows = [
        {
            "segment_id": row["segment_id"],
            "direction": row["direction"],
            "runtime_queue_max_m": row.get("runtime_queue_max_m", ""),
            "runtime_slow_count_max": row.get("runtime_slow_count_max", ""),
            "runtime_density_max": row.get("runtime_density_max", ""),
            "runtime_occupancy_max": row.get("runtime_occupancy_max", ""),
            "runtime_waiting_or_timeloss_max": row.get("runtime_waiting_or_timeloss_max", ""),
            "low_speed_interval_count": row.get("low_speed_interval_count", ""),
            "class": row.get("class", ""),
        }
        for row in speed_rows
    ]
    write_csv(queue_edge_csv, queue_edge_rows, [
        "segment_id", "direction", "runtime_queue_max_m", "runtime_slow_count_max",
        "runtime_density_max", "runtime_occupancy_max", "runtime_waiting_or_timeloss_max",
        "low_speed_interval_count", "class",
    ])
    od_audit_csv = out_dir / "B04_free_flow_od_audit.csv"
    write_csv(od_audit_csv, od_audit["rows"], [
        "segment_id", "direction", "reason", "screenline_edge", "route_vehicle_count",
        "crossing_route_count", "screenline_count", "raw_edge_count_sum", "simulated_speed_kmh",
        "reference_speed_kmh", "runtime_density_max", "runtime_occupancy_max",
        "low_speed_interval_count", "top_crossing_routes",
    ])
    summary = {
        "schema": "compact_v9_B04_validation.v1",
        "generated_at": utc_now(),
        "candidate": candidate_name,
        "status": status,
        "speed_mae_kmh": round(mae, 3),
        "travel_time_mae_s": round(travel_mae, 3),
        "stop_count_excluding_s22": stop_count,
        "free_count": free_count,
        "speed_sanity_fail_count": speed_sanity_fail_count,
        "metric_invalid_count": metric_invalid_count,
        "background_arrived_ratio": round(arrived_ratio, 6),
        "queue_audit": queue,
        "free_flow_od_audit": od_audit["summary"],
        "measurement_summary": measurement.get("summary", {}),
        "diagnostics": diagnostic_summary(speed_rows),
        "run_summary": run,
        "speed_csv": rel(speed_csv),
        "runtime_queue_edge_csv": rel(queue_edge_csv),
        "free_flow_od_audit_csv": rel(od_audit_csv),
    }
    write_json(out_dir / "B04_validation_summary.json", summary)
    return summary


def validation_row(candidate_name: str, validation: dict[str, Any]) -> dict[str, Any]:
    run = validation.get("run_summary", {})
    od_audit = validation.get("free_flow_od_audit", {})
    return {
        "candidate": candidate_name,
        "status": validation["status"],
        "speed_mae_kmh": validation["speed_mae_kmh"],
        "travel_time_mae_s": validation["travel_time_mae_s"],
        "stop_count_excluding_s22": validation["stop_count_excluding_s22"],
        "free_count": validation["free_count"],
        "speed_sanity_fail_count": validation["speed_sanity_fail_count"],
        "metric_invalid_count": validation["metric_invalid_count"],
        "queue_classification": validation["queue_audit"]["classification"],
        "queue_top10_overlap": validation["queue_audit"]["target_sumo_queue_top10_overlap"],
        "queue_top10_overlap_ratio": validation["queue_audit"]["target_sumo_queue_top10_overlap_ratio"],
        "dense_segment_direction_count": validation["queue_audit"]["dense_segment_direction_count"],
        "od_missing_free_count": od_audit.get("od_missing_count", ""),
        "od_undercovered_free_count": od_audit.get("od_undercovered_count", ""),
        "queue_not_forming_free_count": od_audit.get("queue_not_forming_count", ""),
        "measurement_warn_free_count": od_audit.get("measurement_warn_count", ""),
        "sumo_exit_code": run.get("sumo_exit_code", ""),
        "emergency_arrived": run.get("emergency_arrived", ""),
        "background_teleported": run.get("background_teleported", ""),
        "background_arrived": run.get("background_arrived", ""),
        "background_departed": run.get("background_departed", ""),
        "background_arrived_ratio": validation["background_arrived_ratio"],
    }


def run_b0_all(candidate_names: list[str] | None = None) -> dict[str, Any]:
    if not (B04_DEMAND_DIR / "background_routes_compact_v9_B04_sweep.summary.json").is_file():
        build_demand(candidate_names)
    run_names = candidate_names or list(CANDIDATES)
    for candidate_name in run_names:
        demand_xml = B04_DEMAND_DIR / f"background_routes_compact_v9_{candidate_name}.rou.xml"
        if not demand_xml.is_file():
            build_demand([candidate_name])
    rows = []
    summaries_by_name: dict[str, dict[str, Any]] = {}
    for candidate_name in run_names:
        run_summary_path = METRICS_ROOT / candidate_name / "b0_run_summary.json"
        if run_summary_path.is_file():
            cached_run = read_json(run_summary_path)
            required = ["edgeData", "laneData", "tripinfo"]
            has_outputs = all((PROJECT_ROOT / cached_run.get(key, "")).is_file() for key in required)
            mode_current = cached_run.get("measurement_mode") == "lightweight_edge_lane_data"
            net_current = cached_run.get("net_file") == rel(candidate_net(candidate_name))
            run = cached_run if has_outputs and mode_current and net_current else run_b0_candidate(candidate_name)
        else:
            run = run_b0_candidate(candidate_name)
        validation = validate_candidate(candidate_name)
        _ = run
        summaries_by_name[candidate_name] = validation
    for candidate_name in CANDIDATES:
        if candidate_name not in summaries_by_name:
            validation_path = METRICS_ROOT / candidate_name / "B04_validation_summary.json"
            if validation_path.is_file():
                summaries_by_name[candidate_name] = read_json(validation_path)
    for candidate_name in CANDIDATES:
        validation = summaries_by_name.get(candidate_name)
        if validation:
            rows.append(validation_row(candidate_name, validation))
    write_csv(METRICS_ROOT / "B04_candidate_comparison.csv", rows, [
        "candidate", "status", "speed_mae_kmh", "stop_count_excluding_s22", "free_count",
        "speed_sanity_fail_count", "metric_invalid_count", "travel_time_mae_s", "queue_classification", "queue_top10_overlap",
        "queue_top10_overlap_ratio", "dense_segment_direction_count", "od_missing_free_count",
        "od_undercovered_free_count", "queue_not_forming_free_count", "measurement_warn_free_count", "sumo_exit_code",
        "emergency_arrived", "background_teleported", "background_arrived", "background_departed",
        "background_arrived_ratio",
    ])
    pass_candidates = [row for row in rows if row["status"] == "PASS"]
    warn_candidates = [row for row in rows if row["status"] == "WARN"]
    status_rank = {"PASS": 0, "WARN": 1, "FAIL": 2}
    if pass_candidates or warn_candidates:
        selection_pool = pass_candidates or warn_candidates
        selected = sorted(
            selection_pool,
            key=lambda row: (
                status_rank.get(str(row.get("status")), 9),
                int(row.get("background_teleported", 99)) >= 10,
                int(row.get("metric_invalid_count", 99)) > 0,
                int(row.get("speed_sanity_fail_count", 99)) > 2,
                int(row.get("sumo_exit_code", 1)) != 0,
                not bool(row.get("emergency_arrived")),
                float(row.get("background_arrived_ratio", 0.0)) < 0.98,
                int(row.get("background_teleported", 99)),
                float(row.get("speed_mae_kmh", 999.0)),
                float(row.get("travel_time_mae_s", 99999.0)),
                -int(row.get("queue_top10_overlap", 0)),
                int(row.get("free_count", 99)),
                int(safe_float(row.get("od_missing_free_count"), 99)),
                int(row.get("stop_count_excluding_s22", 99)),
            ),
        )[0]
    else:
        selected = sorted(
            rows,
            key=lambda row: (
                int(row.get("sumo_exit_code", 1)) != 0,
                not bool(row.get("emergency_arrived")),
                int(row.get("background_teleported", 99)) >= 10,
                int(row.get("metric_invalid_count", 99)) > 0,
                int(row.get("speed_sanity_fail_count", 99)) > 2,
                float(row.get("background_arrived_ratio", 0.0)) < 0.98,
                float(row.get("speed_mae_kmh", 999.0)),
                float(row.get("travel_time_mae_s", 99999.0)),
                int(row.get("free_count", 99)),
                int(safe_float(row.get("od_missing_free_count"), 99)),
                int(row.get("stop_count_excluding_s22", 99)),
                -int(row.get("queue_top10_overlap", 0)),
                int(row.get("background_teleported", 99)),
            ),
        )[0]
    manifest = read_json(B04_MANIFEST) if B04_MANIFEST.is_file() else adopt_green18()
    previous_candidate = manifest.get("selected_candidate", "B04_j_balanced_recall")
    if previous_candidate == "B04_a_csv_through_only" and manifest.get("selection_summary", {}).get("status") == "FAIL":
        previous_candidate = "B04_j_balanced_recall"
    if pass_candidates or warn_candidates:
        manifest["background_route"] = rel(B04_DEMAND_DIR / f"background_routes_compact_v9_{selected['candidate']}.rou.xml")
        manifest["selected_candidate"] = selected["candidate"]
        manifest["selection_summary"] = selected
    else:
        manifest["background_route"] = rel(B04_DEMAND_DIR / f"background_routes_compact_v9_{previous_candidate}.rou.xml")
        manifest["selected_candidate"] = previous_candidate
        manifest["selection_summary"] = {
            "status": "NO_PASS_OR_WARN_RETAINED_PREVIOUS",
            "retained_candidate": previous_candidate,
            "diagnostic_best_candidate": selected["candidate"],
            "diagnostic_best_summary": selected,
        }
    write_json(B04_MANIFEST, manifest)
    selection = {
        "schema": "compact_v9_B04_selection.v3",
        "generated_at": utc_now(),
        "selected": selected,
        "manifest_selected_candidate": manifest["selected_candidate"],
        "manifest_selection_policy": "updated_to_pass_or_warn" if (pass_candidates or warn_candidates) else "retained_previous_because_all_candidates_failed",
        "candidates": rows,
    }
    write_json(B04_SELECTED_DIR / "selection_summary.json", selection)
    return selection


def require_queue_audit_input(path: Path, label: str) -> Path:
    if not path.is_file():
        raise B04Error(f"missing_b04_queue_audit_input:{label}:{rel(path)}")
    return path


def parse_candidate_from_route(path_text: str) -> str:
    name = Path(path_text).name
    prefix = "background_routes_compact_v9_"
    suffix = ".rou.xml"
    if name.startswith(prefix) and name.endswith(suffix):
        return name[len(prefix):-len(suffix)]
    return ""


def resolve_queue_audit_candidate(candidate_name: str | None = None) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    manifest = read_json(require_queue_audit_input(B04_MANIFEST, "manifest"))
    selection_path = B04_SELECTED_DIR / "selection_summary.json"
    selection = read_json(selection_path) if selection_path.is_file() else {}
    diagnostic_best = (
        str((selection.get("selected") or {}).get("candidate") or "")
        or str((manifest.get("selection_summary") or {}).get("diagnostic_best_candidate") or "")
    )
    primary = (
        candidate_name
        or diagnostic_best
        or str(manifest.get("selected_candidate") or "")
        or parse_candidate_from_route(str(manifest.get("background_route") or ""))
    )
    if not primary:
        raise B04Error("missing_b04_queue_audit_primary_candidate")
    if primary not in CANDIDATES:
        raise B04Error(f"unknown_b04_queue_audit_candidate:{primary}")
    return manifest, selection, primary, diagnostic_best


def queue_metric_values(row: dict[str, Any]) -> dict[str, float]:
    density = max(safe_float(row.get("max_density")), safe_float(row.get("runtime_density_max")))
    occupancy = max(safe_float(row.get("max_occupancy")), safe_float(row.get("runtime_occupancy_max")))
    waiting = safe_float(row.get("runtime_waiting_or_timeloss_max"))
    return {
        "speed_kmh": safe_float(row.get("simulated_speed_kmh")),
        "density": density,
        "occupancy": occupancy,
        "waiting_or_timeloss": waiting,
        "runtime_queue_max_m": safe_float(row.get("runtime_queue_max_m")),
        "runtime_slow_count_max": safe_float(row.get("runtime_slow_count_max")),
        "low_speed_interval_count": safe_float(row.get("low_speed_interval_count")),
        "target_queue_proxy": safe_float(row.get("target_queue_proxy")),
        "sumo_queue_proxy": safe_float(row.get("sumo_queue_proxy")),
    }


def queue_evidence_flags(row: dict[str, Any]) -> dict[str, bool]:
    values = queue_metric_values(row)
    return {
        "measurement_invalid": (
            values["speed_kmh"] <= 0.0
            or values["speed_kmh"] > 70.0
            or str(row.get("class", "")) in {"metric_invalid", "speed_sanity_fail", "missing"}
        ),
        "low_speed_evidence": values["speed_kmh"] <= 35.0,
        "density_evidence": values["density"] >= 20.0,
        "occupancy_evidence": values["occupancy"] >= 12.0,
        "waiting_evidence": values["waiting_or_timeloss"] >= 30.0,
        "slow_queue_evidence": (
            values["runtime_queue_max_m"] >= 10.0
            or values["runtime_slow_count_max"] >= 2.0
            or values["low_speed_interval_count"] >= 2.0
        ),
    }


def classify_b04_queue_state(row: dict[str, Any]) -> str:
    values = queue_metric_values(row)
    flags = queue_evidence_flags(row)
    if flags["measurement_invalid"]:
        return "measurement_mismatch"
    dense_or_slow = flags["density_evidence"] or flags["occupancy_evidence"] or flags["slow_queue_evidence"]
    any_evidence = dense_or_slow or flags["waiting_evidence"]
    if values["speed_kmh"] > 35.0:
        return "fast_dense_flow" if any_evidence else "free"
    if dense_or_slow:
        return "physical_queue"
    if flags["waiting_evidence"]:
        return "signal_only_delay"
    return "measurement_mismatch"


def segment_congestion_proxy(row: dict[str, Any]) -> float:
    values = queue_metric_values(row)
    speed_score = max(0.0, min((35.0 - values["speed_kmh"]) / 35.0, 1.0))
    density_score = max(0.0, min(values["density"] / 60.0, 1.0))
    occupancy_score = max(0.0, min(values["occupancy"] / 30.0, 1.0))
    waiting_score = max(0.0, min(values["waiting_or_timeloss"] / 180.0, 1.0))
    slow_score = max(0.0, min(values["low_speed_interval_count"] / 10.0, 1.0))
    proxy = 0.45 * speed_score + 0.25 * density_score + 0.20 * occupancy_score + 0.10 * max(waiting_score, slow_score)
    return round(proxy, 6)


def segment_weight(segment_id: str) -> float:
    return 0.25 if str(segment_id) == "S22" else 1.0


def queue_not_forming_reason(row: dict[str, Any], queue_state: str, proxy: float) -> str:
    values = queue_metric_values(row)
    target_proxy = values["target_queue_proxy"]
    if queue_state == "fast_dense_flow":
        return "fast_dense_flow_separate_from_free"
    if queue_state == "free" and target_proxy >= 0.5:
        return "queue_not_forming_no_density_occupancy_waiting_evidence"
    if queue_state == "measurement_mismatch" and target_proxy >= 0.5 and proxy < 0.35:
        return "measurement_mismatch_not_counted_as_queue_evidence"
    return ""


def build_b04_queue_proxy_rows(speed_rows: list[dict[str, Any]], candidate_name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    queue_not_forming_count = 0
    for row in speed_rows:
        values = queue_metric_values(row)
        flags = queue_evidence_flags(row)
        state = classify_b04_queue_state(row)
        proxy = segment_congestion_proxy(row)
        reason = queue_not_forming_reason(row, state, proxy)
        if reason.startswith("queue_not_forming") or reason.startswith("fast_dense_flow"):
            queue_not_forming_count += 1
        counts[state] += 1
        physical_evidence_count = sum(
            int(flags[key])
            for key in ["density_evidence", "occupancy_evidence", "slow_queue_evidence"]
        )
        rows.append({
            "candidate": candidate_name,
            "segment_id": row.get("segment_id", ""),
            "direction": row.get("direction", ""),
            "segment_weight": segment_weight(str(row.get("segment_id", ""))),
            "reference_speed_kmh": row.get("reference_speed_kmh", ""),
            "simulated_speed_kmh": row.get("simulated_speed_kmh", ""),
            "reference_travel_time_s": row.get("reference_travel_time_s", ""),
            "simulated_travel_time_s": row.get("simulated_travel_time_s", ""),
            "reference_volume_vph": row.get("reference_volume_vph", ""),
            "observed_count": row.get("observed_count", ""),
            "max_density": row.get("max_density", ""),
            "max_occupancy": row.get("max_occupancy", ""),
            "runtime_density_max": row.get("runtime_density_max", ""),
            "runtime_occupancy_max": row.get("runtime_occupancy_max", ""),
            "runtime_waiting_or_timeloss_max": row.get("runtime_waiting_or_timeloss_max", ""),
            "low_speed_interval_count": row.get("low_speed_interval_count", ""),
            "runtime_queue_max_m": row.get("runtime_queue_max_m", ""),
            "runtime_slow_count_max": row.get("runtime_slow_count_max", ""),
            "target_queue_proxy": row.get("target_queue_proxy", ""),
            "sumo_queue_proxy": row.get("sumo_queue_proxy", ""),
            "segment_congestion_proxy": proxy,
            "queue_state": state,
            "physical_queue_count": 1 if state == "physical_queue" else 0,
            "physical_queue_evidence_count": physical_evidence_count,
            "density_evidence": flags["density_evidence"],
            "occupancy_evidence": flags["occupancy_evidence"],
            "waiting_evidence": flags["waiting_evidence"],
            "slow_queue_evidence": flags["slow_queue_evidence"],
            "low_speed_evidence": flags["low_speed_evidence"],
            "measurement_invalid": flags["measurement_invalid"],
            "queue_not_forming_reason": reason,
            "legacy_class": row.get("class", ""),
            "density_value": round(values["density"], 3),
            "occupancy_value": round(values["occupancy"], 3),
            "waiting_or_timeloss_value": round(values["waiting_or_timeloss"], 3),
        })
    summary = {
        "segment_direction_count": len(rows),
        "physical_queue_count": counts.get("physical_queue", 0),
        "fast_dense_flow_count": counts.get("fast_dense_flow", 0),
        "signal_only_delay_count": counts.get("signal_only_delay", 0),
        "free_count": counts.get("free", 0),
        "measurement_mismatch_count": counts.get("measurement_mismatch", 0),
        "queue_not_forming_count": queue_not_forming_count,
        "weighted_segment_congestion_proxy_mean": round(
            sum(safe_float(row["segment_congestion_proxy"]) * safe_float(row["segment_weight"]) for row in rows)
            / max(sum(safe_float(row["segment_weight"]) for row in rows), 1.0),
            6,
        ),
    }
    return rows, summary


def best_segment_by_edge() -> dict[str, dict[str, Any]]:
    require_queue_audit_input(B04_MAPPING_CSV, "segment_mapping")
    best: dict[str, dict[str, Any]] = {}
    for row in read_csv(B04_MAPPING_CSV):
        edge_id = row.get("edge_id", "")
        if not edge_id:
            continue
        score = safe_float(row.get("matched_length_m"), safe_float(row.get("match_ratio")))
        if edge_id not in best or score > safe_float(best[edge_id].get("_score")):
            best[edge_id] = {
                "_score": score,
                "segment_id": row.get("segment_id", ""),
                "direction": row.get("direction", ""),
                "mapped_S_segment": f"{row.get('segment_id', '')}:{row.get('direction', '')}",
            }
    return best


def load_firetruck_route_edges_existing() -> list[str]:
    require_queue_audit_input(B04_FIRETRUCK_ROUTE_XML, "firetruck_route_xml")
    root = ET.parse(B04_FIRETRUCK_ROUTE_XML).getroot()
    route = root.find("route")
    edges = str(route.get("edges") if route is not None else "").split()
    if not edges:
        raise B04Error(f"missing_firetruck_route_edges:{rel(B04_FIRETRUCK_ROUTE_XML)}")
    return edges


def tl_logic_phase_map(net_file: Path) -> dict[str, list[dict[str, Any]]]:
    phases: dict[str, list[dict[str, Any]]] = {}
    root = ET.parse(net_file).getroot()
    for tl in root.findall("tlLogic"):
        tls_id = tl.get("id", "")
        phases[tls_id] = [
            {
                "phase_index": index,
                "duration": safe_float(phase.get("duration")),
                "state": phase.get("state", ""),
            }
            for index, phase in enumerate(tl.findall("phase"))
        ]
    return phases


def route_tls_movements(net_file: Path, route_edges: list[str]) -> list[dict[str, Any]]:
    pair_index_by_edges = {(from_edge, to_edge): index for index, (from_edge, to_edge) in enumerate(zip(route_edges, route_edges[1:]))}
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for _event, elem in ET.iterparse(net_file, events=("end",)):
        if elem.tag != "connection":
            elem.clear()
            continue
        from_edge = elem.get("from", "")
        to_edge = elem.get("to", "")
        tls_id = elem.get("tl", "")
        pair_index = pair_index_by_edges.get((from_edge, to_edge))
        if pair_index is None or not tls_id:
            elem.clear()
            continue
        key = (tls_id, from_edge, to_edge)
        slot = grouped.setdefault(
            key,
            {
                "route_pair_index": pair_index,
                "tls_id": tls_id,
                "from_edge": from_edge,
                "to_edge": to_edge,
                "link_indices": set(),
                "approach_lanes": set(),
            },
        )
        link_index = elem.get("linkIndex")
        if link_index not in {"", None}:
            slot["link_indices"].add(int(link_index))
        from_lane = elem.get("fromLane")
        if from_lane not in {"", None}:
            slot["approach_lanes"].add(f"{from_edge}_{from_lane}")
        elem.clear()
    movements = []
    for slot in grouped.values():
        movements.append({
            **slot,
            "link_indices": sorted(slot["link_indices"]),
            "approach_lanes": sorted(slot["approach_lanes"]),
        })
    movements.sort(key=lambda item: (int(item["route_pair_index"]), str(item["tls_id"]), str(item["from_edge"])))
    return movements


def selected_green_phase(phases: list[dict[str, Any]], link_indices: list[int]) -> int | str:
    if not phases or not link_indices:
        return ""
    for phase in phases:
        state = str(phase.get("state", ""))
        if all(index < len(state) and state[index] in {"G", "g"} for index in link_indices):
            return int(phase["phase_index"])
    return ""


def edge_lanes(sumo_net: Any, edge_id: str) -> list[str]:
    try:
        edge = sumo_net.getEdge(edge_id)
        return [lane.getID() for lane in edge.getLanes()]
    except Exception:
        return []


def edge_length_and_lane_count(sumo_net: Any, edge_id: str) -> tuple[float, int]:
    try:
        edge = sumo_net.getEdge(edge_id)
        lanes = edge.getLanes()
        return float(edge.getLength()), max(len(lanes), 1)
    except Exception:
        return 0.0, 1


def storage_corridor_for_route(
    route_edges: list[str],
    sumo_net: Any,
    approach_pair_index: int,
    upstream_pair_index: int | None,
    max_length_m: float = STORAGE_CORRIDOR_MAX_M,
) -> dict[str, Any]:
    min_pair_index = 0 if upstream_pair_index is None else min(upstream_pair_index + 1, approach_pair_index)
    storage_edges: list[str] = []
    storage_lanes: list[str] = []
    total_length = 0.0
    for edge_id in reversed(route_edges[min_pair_index:approach_pair_index + 1]):
        length_m, _lane_count = edge_length_and_lane_count(sumo_net, edge_id)
        if length_m <= 0:
            continue
        storage_edges.append(edge_id)
        storage_lanes.extend(edge_lanes(sumo_net, edge_id))
        total_length += length_m
        if total_length >= max_length_m:
            break
    storage_edges = list(reversed(storage_edges))
    storage_lane_set = set(storage_lanes)
    ordered_lanes = [lane for edge_id in storage_edges for lane in edge_lanes(sumo_net, edge_id) if lane in storage_lane_set]
    return {
        "storage_edges": storage_edges,
        "storage_lanes": ordered_lanes,
        "storage_length_m": round(min(total_length, max_length_m), 3),
        "storage_raw_length_m": round(total_length, 3),
        "storage_length_cap_m": max_length_m,
    }


def build_b4_approach_storage_link_plan() -> list[dict[str, Any]]:
    require_queue_audit_input(B04_NET, "b04_net")
    route_edges = load_firetruck_route_edges_existing()
    sumo_net = read_sumo_net(B04_NET)
    phases = tl_logic_phase_map(B04_NET)
    edge_segments = best_segment_by_edge()
    rows: list[dict[str, Any]] = []
    movements = route_tls_movements(B04_NET, route_edges)
    for movement in movements:
        previous_diff = next((row for row in reversed(rows) if row["tls_id"] != movement["tls_id"]), None)
        storage = storage_corridor_for_route(
            route_edges,
            sumo_net,
            int(movement["route_pair_index"]),
            int(previous_diff["route_pair_index"]) if previous_diff else None,
        )
        link_indices = list(movement["link_indices"])
        green_phase = selected_green_phase(phases.get(str(movement["tls_id"]), []), link_indices)
        approach_lanes = movement["approach_lanes"] or edge_lanes(sumo_net, str(movement["from_edge"]))
        from_lane_count = len(edge_lanes(sumo_net, str(movement["from_edge"])))
        to_lane_count = len(edge_lanes(sumo_net, str(movement["to_edge"])))
        segment = edge_segments.get(str(movement["from_edge"]), {})
        controllable = bool(link_indices and green_phase != "" and storage["storage_length_m"] > 0)
        rows.append({
            "movement_id": f"B04_TLS_MOVEMENT_{len(rows):02d}",
            "route_pair_index": int(movement["route_pair_index"]),
            "tls_id": movement["tls_id"],
            "from_edge": movement["from_edge"],
            "to_edge": movement["to_edge"],
            "linkIndex": " ".join(str(index) for index in link_indices),
            "approach_lanes": " ".join(approach_lanes),
            "storage_edges": " ".join(storage["storage_edges"]),
            "storage_lanes": " ".join(storage["storage_lanes"]),
            "storage_length_m": storage["storage_length_m"],
            "storage_raw_length_m": storage["storage_raw_length_m"],
            "lane_count": len(approach_lanes),
            "from_edge_lane_count": from_lane_count,
            "to_edge_lane_count": to_lane_count,
            "lane_drop_delta": from_lane_count - to_lane_count,
            "selected_green_phase": green_phase,
            "mapped_S_segment": segment.get("mapped_S_segment", ""),
            "mapped_segment_id": segment.get("segment_id", ""),
            "mapped_direction": segment.get("direction", ""),
            "controllable": controllable,
            "storage_definition": "upstream_signal_to_current_stopline_corridor_capped_250m",
        })
    return rows


def estimate_queue_m_from_sample(row: dict[str, float], length_m: float, lane_count: int) -> float:
    if length_m <= 0:
        return 0.0
    speed_kmh = safe_float(row.get("speed")) * 3.6
    density = safe_float(row.get("density"))
    occupancy = safe_float(row.get("occupancy"))
    waiting = max(safe_float(row.get("waitingTime")), safe_float(row.get("timeLoss")))
    sampled = safe_float(row.get("sampledSeconds"))
    if sampled <= 0.0 and density <= 0.0 and occupancy <= 0.0 and waiting <= 0.0:
        return 0.0
    effective_lanes = max(lane_count, 1)
    vehicle_count = density * max(length_m / 1000.0, 0.001) * effective_lanes
    if vehicle_count <= 0.0 and occupancy > 0.0:
        vehicle_count = (occupancy / 100.0) * length_m * effective_lanes / HEADWAY_M
    dense = density >= 20.0 or occupancy >= 12.0
    if speed_kmh <= 0.0 and (dense or waiting > 0.0):
        queue_factor = 1.0
    elif speed_kmh < 5.0:
        queue_factor = 1.0
    elif speed_kmh < 20.0 and (dense or waiting >= 30.0):
        queue_factor = 0.60
    elif speed_kmh <= 35.0 and (dense or waiting >= 30.0):
        queue_factor = 0.35
    elif dense and waiting >= 30.0:
        queue_factor = 0.20
    else:
        queue_factor = 0.0
    return max(0.0, vehicle_count * HEADWAY_M * queue_factor / effective_lanes)


def queue_sample_estimates_for_edges(
    edge_data: dict[str, list[dict[str, float]]],
    lane_data: dict[str, list[dict[str, float]]],
    sumo_net: Any,
    edge_ids: list[str],
) -> list[float]:
    estimates: list[float] = []
    for edge_id in edge_ids:
        length_m, lane_count = edge_length_and_lane_count(sumo_net, edge_id)
        for row in edge_data.get(edge_id, []):
            estimates.append(estimate_queue_m_from_sample(row, length_m, lane_count))
        for row in lane_data.get(edge_id, []):
            estimates.append(estimate_queue_m_from_sample(row, length_m, 1))
    return estimates


def storage_queue_evidence_for_edges(
    edge_data: dict[str, list[dict[str, float]]],
    lane_data: dict[str, list[dict[str, float]]],
    edge_ids: list[str],
) -> dict[str, Any]:
    density_max = 0.0
    occupancy_max = 0.0
    waiting_max = 0.0
    time_loss_max = 0.0
    low_speed_sample_count = 0
    sample_count = 0
    for edge_id in edge_ids:
        for row in list(edge_data.get(edge_id, [])) + list(lane_data.get(edge_id, [])):
            sample_count += 1
            speed_kmh = safe_float(row.get("speed")) * 3.6
            density_max = max(density_max, safe_float(row.get("density")))
            occupancy_max = max(occupancy_max, safe_float(row.get("occupancy")))
            waiting_max = max(waiting_max, safe_float(row.get("waitingTime")))
            time_loss_max = max(time_loss_max, safe_float(row.get("timeLoss")))
            if 0.0 < speed_kmh < 5.0:
                low_speed_sample_count += 1
    return {
        "storage_density_max": round(density_max, 3),
        "storage_occupancy_max": round(occupancy_max, 3),
        "storage_waiting_max_s": round(waiting_max, 3),
        "storage_timeLoss_max_s": round(time_loss_max, 3),
        "storage_low_speed_sample_count": low_speed_sample_count,
        "storage_evidence_sample_count": sample_count,
        "storage_has_low_speed_evidence": low_speed_sample_count > 0,
        "storage_has_occupancy_evidence": occupancy_max >= 12.0,
        "storage_has_waiting_or_timeLoss_evidence": max(waiting_max, time_loss_max) >= 30.0,
    }


def stopline_fill_rows(
    plan_rows: list[dict[str, Any]],
    edge_data: dict[str, list[dict[str, float]]],
    lane_data: dict[str, list[dict[str, float]]],
    sumo_net: Any,
    candidate_name: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plan in plan_rows:
        if not bool(plan.get("controllable")):
            continue
        storage_edges = str(plan.get("storage_edges", "")).split()
        storage_length = safe_float(plan.get("storage_length_m"))
        estimates = queue_sample_estimates_for_edges(edge_data, lane_data, sumo_net, storage_edges)
        evidence = storage_queue_evidence_for_edges(edge_data, lane_data, storage_edges)
        ratios = [min(queue_m / max(storage_length, 1.0), 1.0) for queue_m in estimates]
        if not ratios:
            ratios = [0.0]
            estimates = [0.0]
        rows.append({
            "candidate": candidate_name,
            "movement_id": plan.get("movement_id", ""),
            "tls_id": plan.get("tls_id", ""),
            "from_edge": plan.get("from_edge", ""),
            "to_edge": plan.get("to_edge", ""),
            "mapped_S_segment": plan.get("mapped_S_segment", ""),
            "controllable": plan.get("controllable", ""),
            "storage_length_m": storage_length,
            "lane_count": plan.get("lane_count", ""),
            "max_stopline_queue_m": round(max(estimates), 3),
            "max_queue_m_proxy": round(max(estimates), 3),
            "mean_stopline_queue_fill_ratio": round(sum(ratios) / max(len(ratios), 1), 6),
            "p75_stopline_queue_fill_ratio": round(percentile(ratios, 0.75), 6),
            "p80_stopline_queue_fill_ratio": round(percentile(ratios, 0.80), 6),
            "max_stopline_queue_fill_ratio": round(max(ratios), 6),
            "queue_sample_count": len(ratios),
            **evidence,
            "queue_evidence_source": "B04 B0 edgeData/laneData stopline storage proxy; not field-observed queue length",
        })
    return rows


def truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def case_b_tau_fill_proposal(fill_rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        safe_float(row.get("max_stopline_queue_fill_ratio"))
        for row in fill_rows
        if truthy(row.get("controllable"))
        and not str(row.get("mapped_S_segment", "")).startswith("S22:")
        and safe_float(row.get("max_stopline_queue_fill_ratio")) > 0.0
    ]
    p75 = percentile(values, 0.75) if values else 0.0
    p80 = percentile(values, 0.80) if values else 0.0
    return {
        "threshold_basis_ko": "B04 B0 baseline 내부 stopline fill ratio 분포 기준",
        "percentile_sample_count": len(values),
        "fill_ratio_p75": round(p75, 6),
        "fill_ratio_p80": round(p80, 6),
        "tau_fill_candidate_p75": round(max(0.45, p75), 6),
        "tau_fill_recommended": round(max(0.50, p80), 6),
        "fallback_policy": "Use 0.50 when no positive controllable non-S22 stopline fill evidence exists.",
    }


def build_case_b_link_audit_rows(
    plan_rows: list[dict[str, Any]],
    fill_rows: list[dict[str, Any]],
    tau_proposal: dict[str, Any],
) -> list[dict[str, Any]]:
    fill_by_movement = {str(row.get("movement_id")): row for row in fill_rows}
    controllable = [row for row in plan_rows if truthy(row.get("controllable"))]
    controllable.sort(key=lambda row: int(safe_float(row.get("route_pair_index"))))
    tau = safe_float(tau_proposal.get("tau_fill_recommended"), 0.50)
    rows: list[dict[str, Any]] = []
    for index in range(1, len(controllable)):
        bottleneck = controllable[index]
        upstream = next(
            (row for row in reversed(controllable[:index]) if row.get("tls_id") != bottleneck.get("tls_id")),
            None,
        )
        if upstream is None:
            continue
        bottleneck_fill = safe_float(fill_by_movement.get(str(bottleneck.get("movement_id")), {}).get("max_stopline_queue_fill_ratio"))
        upstream_fill = safe_float(fill_by_movement.get(str(upstream.get("movement_id")), {}).get("max_stopline_queue_fill_ratio"))
        lane_drop_delta = int(safe_float(bottleneck.get("lane_drop_delta")))
        rows.append({
            "case_b_audit_id": f"B04_CASE_B_AUDIT_{len(rows):02d}",
            "bottleneck_movement_id": bottleneck.get("movement_id", ""),
            "upstream_movement_id": upstream.get("movement_id", ""),
            "bottleneck_tls_id": bottleneck.get("tls_id", ""),
            "upstream_tls_id": upstream.get("tls_id", ""),
            "bottleneck_from_edge": bottleneck.get("from_edge", ""),
            "bottleneck_to_edge": bottleneck.get("to_edge", ""),
            "upstream_from_edge": upstream.get("from_edge", ""),
            "bottleneck_mapped_S_segment": bottleneck.get("mapped_S_segment", ""),
            "upstream_mapped_S_segment": upstream.get("mapped_S_segment", ""),
            "lane_count_upstream": bottleneck.get("from_edge_lane_count", ""),
            "lane_count_downstream": bottleneck.get("to_edge_lane_count", ""),
            "lane_drop_delta": lane_drop_delta,
            "lane_drop_evidence": f"{bottleneck.get('from_edge_lane_count', '')}->{bottleneck.get('to_edge_lane_count', '')}",
            "bottleneck_max_stopline_queue_fill_ratio": round(bottleneck_fill, 6),
            "upstream_max_stopline_queue_fill_ratio": round(upstream_fill, 6),
            "tau_fill_recommended": round(tau, 6),
            "threshold_status": "above_tau_fill" if bottleneck_fill >= tau else "below_tau_fill",
            "case_b_candidate_proxy": bool(lane_drop_delta > 0 and bottleneck_fill >= tau),
            "case_b_term_note": "B4 readiness audit term only; not a B3 runtime implementation.",
            "threshold_basis_ko": tau_proposal["threshold_basis_ko"],
        })
    return rows


def tls_presence_by_segment(net_file: Path) -> dict[tuple[str, str], dict[str, Any]]:
    edge_segments = best_segment_by_edge()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for _event, elem in ET.iterparse(net_file, events=("end",)):
        if elem.tag != "connection":
            elem.clear()
            continue
        tls_id = elem.get("tl", "")
        from_edge = elem.get("from", "")
        segment = edge_segments.get(from_edge, {})
        key = (str(segment.get("segment_id", "")), str(segment.get("direction", "")))
        if tls_id and key[0] and key[1]:
            slot = grouped.setdefault(key, {"tls_ids": set(), "edge_ids": set(), "connection_count": 0})
            slot["tls_ids"].add(tls_id)
            slot["edge_ids"].add(from_edge)
            slot["connection_count"] += 1
        elem.clear()
    return {
        key: {
            "tls_ids": sorted(value["tls_ids"]),
            "edge_ids": sorted(value["edge_ids"]),
            "connection_count": value["connection_count"],
        }
        for key, value in grouped.items()
    }


def fill_by_mapped_segment(fill_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in fill_rows:
        key = str(row.get("mapped_S_segment", ""))
        if not key:
            continue
        if key not in best or safe_float(row.get("max_stopline_queue_fill_ratio")) > safe_float(best[key].get("max_stopline_queue_fill_ratio")):
            best[key] = row
    return best


def signal_presence_audit_rows(
    speed_rows: list[dict[str, Any]],
    plan_rows: list[dict[str, Any]],
    fill_rows: list[dict[str, Any]],
    net_file: Path,
) -> list[dict[str, Any]]:
    tls_by_segment = tls_presence_by_segment(net_file)
    plan_by_segment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in plan_rows:
        if row.get("mapped_S_segment"):
            plan_by_segment[str(row.get("mapped_S_segment"))].append(row)
    fill_by_segment = fill_by_mapped_segment(fill_rows)
    rows: list[dict[str, Any]] = []
    for speed_row in speed_rows:
        segment_id = str(speed_row.get("segment_id", ""))
        direction = str(speed_row.get("direction", ""))
        mapped = f"{segment_id}:{direction}"
        plans = plan_by_segment.get(mapped, [])
        controllable = [row for row in plans if truthy(row.get("controllable"))]
        tls_info = tls_by_segment.get((segment_id, direction), {})
        if segment_id == "S22":
            status = "terminal_segment"
        elif controllable:
            status = "b4_controllable_tls"
        elif plans:
            status = "b4_route_tls_not_controllable"
        elif tls_info.get("tls_ids"):
            status = "tls_present_not_b4_control_target"
        else:
            opposite = "downbound" if direction == "upbound" else "upbound"
            opposite_has_control = bool(plan_by_segment.get(f"{segment_id}:{opposite}"))
            status = "direction_control_mismatch" if opposite_has_control else "no_tls_mapped_on_segment_edges"
        fill = fill_by_segment.get(mapped, {})
        rows.append({
            "segment_id": segment_id,
            "direction": direction,
            "mapped_S_segment": mapped,
            "signal_presence_status": status,
            "net_tls_count": len(tls_info.get("tls_ids", [])),
            "net_tls_ids": " ".join(tls_info.get("tls_ids", [])),
            "net_tls_edge_count": len(tls_info.get("edge_ids", [])),
            "b4_route_movement_count": len(plans),
            "b4_controllable_movement_count": len(controllable),
            "b4_tls_ids": " ".join(sorted({str(row.get("tls_id", "")) for row in plans if row.get("tls_id")})),
            "max_stopline_queue_fill_ratio": fill.get("max_stopline_queue_fill_ratio", 0.0),
            "max_queue_m_proxy": fill.get("max_queue_m_proxy", 0.0),
            "storage_length_m": fill.get("storage_length_m", 0.0),
        })
    return rows


def normalized_density_score(density: float, occupancy: float) -> float:
    return min(1.0, max(density / 40.0, occupancy / 24.0, 0.0))


def b4_queue_measurement_diagnostic_rows(
    speed_rows: list[dict[str, Any]],
    signal_rows: list[dict[str, Any]],
    fill_rows: list[dict[str, Any]],
    candidate_name: str,
) -> list[dict[str, Any]]:
    signal_by_segment = {str(row.get("mapped_S_segment", "")): row for row in signal_rows}
    fill_by_segment = fill_by_mapped_segment(fill_rows)
    rows: list[dict[str, Any]] = []
    for row in speed_rows:
        values = queue_metric_values(row)
        segment_id = str(row.get("segment_id", ""))
        direction = str(row.get("direction", ""))
        mapped = f"{segment_id}:{direction}"
        signal = signal_by_segment.get(mapped, {})
        fill = fill_by_segment.get(mapped, {})
        fill_ratio = safe_float(fill.get("max_stopline_queue_fill_ratio"))
        density_score = normalized_density_score(values["density"], values["occupancy"])
        fast_score = min(max((values["speed_kmh"] - 35.0) / 15.0, 0.0), 1.0)
        waiting_score = min(values["waiting_or_timeloss"] / 90.0, 1.0)
        segment_proxy = segment_congestion_proxy(row)
        target_proxy = values["target_queue_proxy"]
        fast_dense_no_queue_index = density_score * fast_score * (1.0 - min(fill_ratio, 1.0))
        queue_compression_gap = max(0.0, min(max(segment_proxy, target_proxy), 1.0) - fill_ratio)
        signal_too_generous_score = fast_dense_no_queue_index * (1.0 - waiting_score)
        exit_too_easy_score = (
            min(1.0, (segment_number(segment_id) - 19) / 3.0)
            * fast_score
            * (1.0 - min(fill_ratio, 1.0))
            if segment_number(segment_id) >= 20
            else 0.0
        )
        signal_status = str(signal.get("signal_presence_status", ""))
        if signal_status in {"no_tls_mapped_on_segment_edges", "direction_control_mismatch", "tls_present_not_b4_control_target"}:
            diagnosis = signal_status
        elif fill_ratio >= 0.50:
            diagnosis = "queue_ready_fill_ge_0p50"
        elif signal_too_generous_score >= 0.35:
            diagnosis = "signal_too_generous"
        elif exit_too_easy_score >= 0.35:
            diagnosis = "exit_too_easy"
        elif queue_compression_gap >= 0.45 and fill_ratio < 0.25:
            diagnosis = "queue_not_compressed_to_stopline"
        elif classify_b04_queue_state(row) == "fast_dense_flow":
            diagnosis = "fast_dense_flow_without_stopline_queue"
        else:
            diagnosis = "low_queue_pressure_or_measurement_mismatch"
        rows.append({
            "candidate": candidate_name,
            "segment_id": segment_id,
            "direction": direction,
            "mapped_S_segment": mapped,
            "queue_state": classify_b04_queue_state(row),
            "signal_presence_status": signal_status,
            "simulated_speed_kmh": row.get("simulated_speed_kmh", ""),
            "target_congestion_proxy": row.get("target_queue_proxy", ""),
            "segment_congestion_proxy": segment_proxy,
            "max_stopline_queue_fill_ratio": round(fill_ratio, 6),
            "fill_ratio_ge_0p50": fill_ratio >= 0.50,
            "fast_dense_no_queue_index": round(fast_dense_no_queue_index, 6),
            "queue_compression_gap": round(queue_compression_gap, 6),
            "approach_conflict_proxy": round(safe_float(signal.get("b4_controllable_movement_count")) / max(safe_float(signal.get("net_tls_count")), 1.0), 6),
            "signal_too_generous_score": round(signal_too_generous_score, 6),
            "exit_too_easy_score": round(exit_too_easy_score, 6),
            "diagnosis": diagnosis,
            "measurement_note": "SUMO edgeData/laneData proxy only; no field-observed queue length.",
        })
    return rows


def queue_audit_output_paths() -> dict[str, Path]:
    return {
        "queue_definition_audit_json": B04_QUEUE_AUDIT_DIR / "b04_queue_definition_audit.json",
        "queue_proxy_by_segment_csv": B04_QUEUE_AUDIT_DIR / "b04_queue_proxy_by_segment.csv",
        "stopline_queue_fill_ratio_csv": B04_QUEUE_AUDIT_DIR / "b04_stopline_queue_fill_ratio.csv",
        "queue_not_forming_diagnosis_csv": B04_QUEUE_AUDIT_DIR / "b04_queue_not_forming_diagnosis.csv",
        "segment_signal_presence_audit_csv": B04_QUEUE_AUDIT_DIR / "b04_segment_signal_presence_audit.csv",
        "queue_measurement_diagnostics_csv": B04_QUEUE_AUDIT_DIR / "b4_queue_measurement_diagnostics.csv",
        "approach_storage_link_plan_csv": B04_QUEUE_AUDIT_DIR / "b4_approach_storage_link_plan.csv",
        "case_b_queue_readiness_csv": B04_QUEUE_AUDIT_DIR / "b04_case_b_queue_readiness.csv",
        "control_queue_threshold_proposal_json": B04_QUEUE_AUDIT_DIR / "b4_control_queue_threshold_proposal.json",
    }


def traffic_demand_review_output_paths() -> dict[str, Path]:
    return {
        "traffic_demand_review_json": B04_QUEUE_AUDIT_DIR / "b04_traffic_demand_review.json",
        "free_flow_cause_by_segment_csv": B04_QUEUE_AUDIT_DIR / "b04_free_flow_cause_by_segment.csv",
        "main_vs_offmain_demand_audit_csv": B04_QUEUE_AUDIT_DIR / "b04_main_vs_offmain_demand_audit.csv",
        "demand_growth_candidate_summary_csv": B04_QUEUE_AUDIT_DIR / "b04_demand_growth_candidate_summary.csv",
    }


def build_b04_queue_audit(candidate_name: str | None = None) -> dict[str, Any]:
    manifest, selection, primary, diagnostic_best = resolve_queue_audit_candidate(candidate_name)
    require_queue_audit_input(B04_MAPPING_CSV, "segment_mapping")
    require_queue_audit_input(B04_TARGET_PROFILE_CSV, "target_profile")
    require_queue_audit_input(B04_NET, "b04_net")
    metric_dir = METRICS_ROOT / primary
    run_summary_path = require_queue_audit_input(metric_dir / "b0_run_summary.json", "b0_run_summary")
    speed_csv = require_queue_audit_input(metric_dir / "B04_segment_speed_recall.csv", "segment_speed_recall")
    run_summary = read_json(run_summary_path)
    edge_data_path = require_queue_audit_input(PROJECT_ROOT / str(run_summary.get("edgeData", "")), "edgeData")
    lane_data_path = require_queue_audit_input(PROJECT_ROOT / str(run_summary.get("laneData", "")), "laneData")
    edge_data = edge_data_by_edge(edge_data_path)
    lane_data = lane_data_by_edge(lane_data_path)
    speed_rows = read_csv(speed_csv)
    queue_proxy_rows, segment_summary = build_b04_queue_proxy_rows(speed_rows, primary)
    not_forming_rows = [row for row in queue_proxy_rows if row.get("queue_not_forming_reason")]
    plan_rows = build_b4_approach_storage_link_plan()
    sumo_net = read_sumo_net(B04_NET)
    fill_rows = stopline_fill_rows(plan_rows, edge_data, lane_data, sumo_net, primary)
    tau_proposal = case_b_tau_fill_proposal(fill_rows)
    case_b_rows = build_case_b_link_audit_rows(plan_rows, fill_rows, tau_proposal)
    signal_rows = signal_presence_audit_rows(speed_rows, plan_rows, fill_rows, B04_NET)
    measurement_rows = b4_queue_measurement_diagnostic_rows(speed_rows, signal_rows, fill_rows, primary)
    outputs = queue_audit_output_paths()
    write_csv(outputs["queue_proxy_by_segment_csv"], queue_proxy_rows, [
        "candidate", "segment_id", "direction", "segment_weight",
        "reference_speed_kmh", "simulated_speed_kmh", "reference_travel_time_s",
        "simulated_travel_time_s", "reference_volume_vph", "observed_count",
        "max_density", "max_occupancy", "runtime_density_max", "runtime_occupancy_max",
        "runtime_waiting_or_timeloss_max", "low_speed_interval_count", "runtime_queue_max_m",
        "runtime_slow_count_max", "target_queue_proxy", "sumo_queue_proxy",
        "segment_congestion_proxy", "queue_state", "physical_queue_count",
        "physical_queue_evidence_count", "density_evidence", "occupancy_evidence",
        "waiting_evidence", "slow_queue_evidence", "low_speed_evidence",
        "measurement_invalid", "queue_not_forming_reason", "legacy_class",
        "density_value", "occupancy_value", "waiting_or_timeloss_value",
    ])
    write_csv(outputs["queue_not_forming_diagnosis_csv"], not_forming_rows, [
        "candidate", "segment_id", "direction", "segment_weight",
        "reference_speed_kmh", "simulated_speed_kmh", "target_queue_proxy",
        "sumo_queue_proxy", "segment_congestion_proxy", "queue_state",
        "queue_not_forming_reason", "runtime_density_max", "runtime_occupancy_max",
        "runtime_waiting_or_timeloss_max", "low_speed_interval_count", "legacy_class",
    ])
    write_csv(outputs["segment_signal_presence_audit_csv"], signal_rows, [
        "segment_id", "direction", "mapped_S_segment", "signal_presence_status",
        "net_tls_count", "net_tls_ids", "net_tls_edge_count",
        "b4_route_movement_count", "b4_controllable_movement_count", "b4_tls_ids",
        "max_stopline_queue_fill_ratio", "max_queue_m_proxy", "storage_length_m",
    ])
    write_csv(outputs["queue_measurement_diagnostics_csv"], measurement_rows, [
        "candidate", "segment_id", "direction", "mapped_S_segment", "queue_state",
        "signal_presence_status", "simulated_speed_kmh", "target_congestion_proxy",
        "segment_congestion_proxy", "max_stopline_queue_fill_ratio", "fill_ratio_ge_0p50",
        "fast_dense_no_queue_index", "queue_compression_gap", "approach_conflict_proxy",
        "signal_too_generous_score", "exit_too_easy_score", "diagnosis", "measurement_note",
    ])
    write_csv(outputs["approach_storage_link_plan_csv"], plan_rows, [
        "movement_id", "route_pair_index", "tls_id", "from_edge", "to_edge",
        "linkIndex", "approach_lanes", "storage_edges", "storage_lanes",
        "storage_length_m", "storage_raw_length_m", "lane_count", "from_edge_lane_count", "to_edge_lane_count",
        "lane_drop_delta", "selected_green_phase", "mapped_S_segment",
        "mapped_segment_id", "mapped_direction", "controllable", "storage_definition",
    ])
    write_csv(outputs["stopline_queue_fill_ratio_csv"], fill_rows, [
        "candidate", "movement_id", "tls_id", "from_edge", "to_edge",
        "mapped_S_segment", "controllable", "storage_length_m", "lane_count",
        "max_stopline_queue_m", "max_queue_m_proxy", "mean_stopline_queue_fill_ratio",
        "p75_stopline_queue_fill_ratio", "p80_stopline_queue_fill_ratio",
        "max_stopline_queue_fill_ratio", "queue_sample_count", "storage_density_max",
        "storage_occupancy_max", "storage_waiting_max_s", "storage_timeLoss_max_s",
        "storage_low_speed_sample_count", "storage_evidence_sample_count",
        "storage_has_low_speed_evidence", "storage_has_occupancy_evidence",
        "storage_has_waiting_or_timeLoss_evidence", "queue_evidence_source",
    ])
    write_csv(outputs["case_b_queue_readiness_csv"], case_b_rows, [
        "case_b_audit_id", "bottleneck_movement_id", "upstream_movement_id",
        "bottleneck_tls_id", "upstream_tls_id", "bottleneck_from_edge",
        "bottleneck_to_edge", "upstream_from_edge", "bottleneck_mapped_S_segment",
        "upstream_mapped_S_segment", "lane_count_upstream", "lane_count_downstream",
        "lane_drop_delta", "lane_drop_evidence", "bottleneck_max_stopline_queue_fill_ratio",
        "upstream_max_stopline_queue_fill_ratio", "tau_fill_recommended",
        "threshold_status", "case_b_candidate_proxy", "case_b_term_note", "threshold_basis_ko",
    ])
    write_json(outputs["control_queue_threshold_proposal_json"], {
        "schema": "compact_v9_B4_control_queue_threshold_proposal.v1",
        "generated_at": utc_now(),
        "candidate": primary,
        "threshold_policy": tau_proposal,
        "fill_ratio_ge_0p50_count": sum(1 for row in fill_rows if safe_float(row.get("max_stopline_queue_fill_ratio")) >= 0.50),
        "percentile_pool": "controllable_non_S22_tls_movements_only",
        "measurement_note": "stopline fill ratio is a SUMO baseline proxy derived from edgeData/laneData, not a field-observed queue measurement.",
    })
    summary = {
        "schema": "compact_v9_B04_queue_definition_audit.v1",
        "generated_at": utc_now(),
        "primary_candidate": primary,
        "manifest_selected_candidate": manifest.get("selected_candidate", ""),
        "diagnostic_best_candidate": diagnostic_best,
        "selection_policy": selection.get("manifest_selection_policy", ""),
        "mode": "B0",
        "parameter_id": "no_control",
        "measurement_mode": run_summary.get("measurement_mode", ""),
        "fcd_enabled": bool(run_summary.get("fcd_enabled")),
        "queue_lenses": {
            "b04_reality_validation": "S1-S22 segment-level congestion proxy for comparison with the reference CSV.",
            "b4_queue_readiness": "TLS approach-level stopline_queue_fill_ratio proxy for future B4 control trigger design.",
        },
        "classification_thresholds": {
            "density_evidence_min": 20.0,
            "occupancy_evidence_min": 12.0,
            "waiting_or_timeloss_evidence_min": 30.0,
            "low_speed_max_kmh": 35.0,
            "fast_dense_speed_min_kmh": 35.0,
            "s22_weight": 0.25,
        },
        "segment_queue_summary": segment_summary,
        "approach_storage_summary": {
            "movement_count": len(plan_rows),
            "controllable_movement_count": sum(1 for row in plan_rows if truthy(row.get("controllable"))),
            "storage_definition": "upstream_signal_to_current_stopline_corridor_capped_250m",
        },
        "stopline_fill_summary": {
            "row_count": len(fill_rows),
            **tau_proposal,
        },
        "case_b_link_audit_summary": {
            "pair_count": len(case_b_rows),
            "above_tau_fill_count": sum(1 for row in case_b_rows if row.get("threshold_status") == "above_tau_fill"),
            "lane_drop_pair_count": sum(1 for row in case_b_rows if safe_float(row.get("lane_drop_delta")) > 0),
        },
        "queue_measurement_diagnostics_summary": {
            "row_count": len(measurement_rows),
            "fill_ratio_ge_0p50_count": sum(1 for row in measurement_rows if truthy(row.get("fill_ratio_ge_0p50"))),
            "fast_dense_without_stopline_queue_count": sum(1 for row in measurement_rows if row.get("diagnosis") == "fast_dense_flow_without_stopline_queue"),
            "queue_not_compressed_count": sum(1 for row in measurement_rows if row.get("diagnosis") == "queue_not_compressed_to_stopline"),
            "signal_too_generous_count": sum(1 for row in measurement_rows if row.get("diagnosis") == "signal_too_generous"),
            "exit_too_easy_count": sum(1 for row in measurement_rows if row.get("diagnosis") == "exit_too_easy"),
            "no_b4_control_or_tls_gap_count": sum(
                1 for row in measurement_rows
                if row.get("diagnosis") in {"no_tls_mapped_on_segment_edges", "direction_control_mismatch", "tls_present_not_b4_control_target"}
            ),
        },
        "input_artifacts": {
            "manifest": rel(B04_MANIFEST),
            "selection_summary": rel(B04_SELECTED_DIR / "selection_summary.json") if (B04_SELECTED_DIR / "selection_summary.json").is_file() else "",
            "run_summary": rel(run_summary_path),
            "segment_speed_recall": rel(speed_csv),
            "edgeData": rel(edge_data_path),
            "laneData": rel(lane_data_path),
            "net": rel(B04_NET),
            "mapping": rel(B04_MAPPING_CSV),
            "target_profile": rel(B04_TARGET_PROFILE_CSV),
        },
        "outputs": {key: rel(path) for key, path in outputs.items()},
        "policy_notes": [
            "B04 queue proxy remains a segment-level reality validation lens.",
            "stopline_queue_fill_ratio is a SUMO baseline proxy derived from edgeData/laneData, not a field-observed queue measurement.",
            tau_proposal["threshold_basis_ko"],
        ],
    }
    write_json(outputs["queue_definition_audit_json"], summary)
    return summary


def review_candidate_names(candidate_name: str | None = None) -> tuple[str, str, list[str]]:
    manifest, _selection, primary, diagnostic_best = resolve_queue_audit_candidate(candidate_name)
    manifest_selected = str(manifest.get("selected_candidate") or parse_candidate_from_route(str(manifest.get("background_route") or "")))
    growth_candidates = ["B04_aa_balanced_growth", "B04_ab_queue_pressure"]
    names = [
        primary,
        manifest_selected,
        "B04_j_balanced_recall",
        "B04_w_od_coverage_repair",
        "B04_x_od_queue_tuned",
        "B04_y_temporal_compression",
        "B04_z_signal_queue_pulse",
    ]
    for name in growth_candidates:
        has_demand = (B04_DEMAND_DIR / f"background_routes_compact_v9_{name}.rou.summary.json").is_file()
        has_validation = (METRICS_ROOT / name / "B04_validation_summary.json").is_file()
        if has_demand or has_validation:
            names.append(name)
    result: list[str] = []
    for name in names:
        if name and name in CANDIDATES and name not in result:
            result.append(name)
    return primary, diagnostic_best, result


def candidate_comparison_by_name() -> dict[str, dict[str, Any]]:
    path = require_queue_audit_input(METRICS_ROOT / "B04_candidate_comparison.csv", "candidate_comparison")
    return {row.get("candidate", ""): row for row in read_csv(path)}


def demand_summary_for_candidate(candidate_name: str) -> dict[str, Any]:
    path = require_queue_audit_input(
        B04_DEMAND_DIR / f"background_routes_compact_v9_{candidate_name}.rou.summary.json",
        f"demand_summary:{candidate_name}",
    )
    return read_json(path)


def demand_routes_and_counts(candidate_name: str) -> tuple[dict[str, list[str]], dict[str, int]]:
    routes, vehicles_by_route = demand_route_coverage(candidate_name)
    if not routes:
        require_queue_audit_input(B04_DEMAND_DIR / f"background_routes_compact_v9_{candidate_name}.rou.xml", f"demand_xml:{candidate_name}")
    return routes, vehicles_by_route


def route_demand_category(route_id: str) -> str:
    if route_id.startswith("mainline_through_"):
        return "main_through_flow"
    if route_id.startswith(("midcorridor_local_", "od_repair_", "od_queue_")):
        return "main_local_flow"
    if route_id.startswith("segment_feeder_"):
        return "feeder_inflow"
    if route_id.startswith("sideflow_background_"):
        return "off_main_background_flow"
    return "main_local_flow"


def build_main_vs_offmain_demand_rows(candidate_names: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    terminal_edges = terminal_source_edges()
    for candidate_name in candidate_names:
        summary = demand_summary_for_candidate(candidate_name)
        routes, vehicles_by_route = demand_routes_and_counts(candidate_name)
        total = sum(vehicles_by_route.values())
        category_counts: dict[str, int] = defaultdict(int)
        source_counts: dict[str, int] = defaultdict(int)
        sink_counts: dict[str, int] = defaultdict(int)
        terminal_sink_flow = 0
        feeder_outflow = 0
        for route_id, vehicle_count in vehicles_by_route.items():
            edges = routes.get(route_id, [])
            category_counts[route_demand_category(route_id)] += vehicle_count
            if edges:
                source_counts[edges[0]] += vehicle_count
                sink_counts[edges[-1]] += vehicle_count
                if edges[0] in terminal_edges or edges[-1] in terminal_edges:
                    terminal_sink_flow += vehicle_count
                if route_id.startswith("segment_feeder_") and edges[-1] not in set(firetruck_route_edges()):
                    feeder_outflow += vehicle_count
        top_sources = sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        top_sinks = sorted(sink_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        conflict_flow = (
            category_counts.get("feeder_inflow", 0)
            + category_counts.get("main_local_flow", 0)
            + category_counts.get("off_main_background_flow", 0)
        )
        source_bias = (top_sources[0][1] / max(total, 1)) if top_sources else 0.0
        sink_bias = (top_sinks[0][1] / max(total, 1)) if top_sinks else 0.0
        sideflow_count = category_counts.get("off_main_background_flow", 0)
        conflict_share = conflict_flow / max(total, 1)
        rows.append({
            "candidate": candidate_name,
            "vehicle_count": total,
            "route_count": len(routes),
            "unique_source_count": len(source_counts),
            "unique_sink_count": len(sink_counts),
            "main_through_flow": category_counts.get("main_through_flow", 0),
            "main_local_flow": category_counts.get("main_local_flow", 0),
            "feeder_inflow": category_counts.get("feeder_inflow", 0),
            "feeder_outflow": feeder_outflow,
            "terminal_sink_flow": terminal_sink_flow,
            "off_main_background_flow": sideflow_count,
            "off_main_background_share": round(sideflow_count / max(total, 1), 6),
            "conflict_flow_share": round(conflict_share, 6),
            "top_source_share": round(source_bias, 6),
            "top_sink_share": round(sink_bias, 6),
            "source_sink_bias_warning": source_bias >= 0.25 or sink_bias >= 0.25,
            "weak_offmain_conflict_warning": sideflow_count < 50 or conflict_share < 0.25,
            "tls_approach_conflict_proxy": round(conflict_share * (1.0 - max(source_bias, sink_bias)), 6),
            "top_sources": ";".join(f"{edge}:{count}" for edge, count in top_sources),
            "top_sinks": ";".join(f"{edge}:{count}" for edge, count in top_sinks),
            "settings": json.dumps(summary.get("settings", {}), ensure_ascii=False, sort_keys=True),
        })
    return rows


def demand_growth_candidate_summary_rows(demand_rows: list[dict[str, Any]], comparison: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    demand_by_candidate = {row["candidate"]: row for row in demand_rows}
    rows: list[dict[str, Any]] = []
    for name, target_total, target_main_min, target_main_max in [
        ("B04_aa_balanced_growth", "1300-1500", 250, 350),
        ("B04_ab_queue_pressure", "1500-1700", 350, 500),
    ]:
        demand_row = demand_by_candidate.get(name)
        settings = CANDIDATES[name]
        comparison_row = comparison.get(name, {})
        if demand_row:
            main_through = int(safe_float(demand_row.get("main_through_flow")))
            top_sink_share = safe_float(demand_row.get("top_sink_share"))
            terminal_sink_flow = int(safe_float(demand_row.get("terminal_sink_flow")))
            vehicle_count = int(safe_float(demand_row.get("vehicle_count")))
            artifact_status = "ready"
            main_target_status = "within_target" if target_main_min <= main_through <= target_main_max else ("below_target" if main_through < target_main_min else "above_target")
            terminal_status = "ok" if top_sink_share < 0.25 and terminal_sink_flow == 0 else "terminal_or_sink_bias_warning"
        else:
            main_through = 0
            top_sink_share = 0.0
            terminal_sink_flow = 0
            vehicle_count = 0
            artifact_status = "missing_demand_summary"
            main_target_status = "not_evaluated"
            terminal_status = "not_evaluated"
        rows.append({
            "candidate": name,
            "artifact_status": artifact_status,
            "target_vehicle_count": target_total,
            "vehicle_count": vehicle_count,
            "target_main_through_flow": f"{target_main_min}-{target_main_max}",
            "main_through_flow": main_through,
            "main_through_target_status": main_target_status,
            "terminal_sink_flow": terminal_sink_flow,
            "top_sink_share": round(top_sink_share, 6),
            "terminal_sink_status": terminal_status,
            "status": comparison_row.get("status", ""),
            "speed_mae_kmh": comparison_row.get("speed_mae_kmh", ""),
            "travel_time_mae_s": comparison_row.get("travel_time_mae_s", ""),
            "background_teleported": comparison_row.get("background_teleported", ""),
            "background_arrived_ratio": comparison_row.get("background_arrived_ratio", ""),
            "calibration_note": settings.get("calibration_note", ""),
        })
    return rows


def free_flow_od_reason_by_segment(candidate_name: str, speed_rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    audit_path = METRICS_ROOT / candidate_name / "B04_free_flow_od_audit.csv"
    if audit_path.is_file():
        rows = read_csv(audit_path)
    else:
        rows = free_flow_od_audit(candidate_name, speed_rows)["rows"]
    return {(row.get("segment_id", ""), row.get("direction", "")): row for row in rows}


def classify_free_flow_cause(row: dict[str, Any], od_row: dict[str, Any] | None = None) -> str:
    queue_state = classify_b04_queue_state(row)
    if queue_state in {"physical_queue", "fast_dense_flow", "signal_only_delay", "measurement_mismatch"}:
        return queue_state
    od_reason = str((od_row or {}).get("reason", ""))
    if od_reason == "measurement_warn":
        return "measurement_mismatch"
    if od_reason in {"od_missing", "od_undercovered", "queue_not_forming"}:
        return od_reason
    segment_id = str(row.get("segment_id", ""))
    values = queue_metric_values(row)
    proxy = segment_congestion_proxy(row)
    if segment_id in {"S20", "S21", "S22"} and values["speed_kmh"] > 35.0:
        return "exit_too_easy"
    if values["speed_kmh"] > 35.0 and values["target_queue_proxy"] >= 0.5 and proxy < 0.35:
        return "queue_not_forming"
    if values["speed_kmh"] > 35.0 and values["density"] < 20.0 and values["occupancy"] < 12.0:
        return "signal_too_generous"
    return "od_undercovered"


def build_free_flow_cause_rows(candidate_name: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    speed_csv = require_queue_audit_input(METRICS_ROOT / candidate_name / "B04_segment_speed_recall.csv", f"segment_speed_recall:{candidate_name}")
    speed_rows = read_csv(speed_csv)
    od_by_segment = free_flow_od_reason_by_segment(candidate_name, speed_rows)
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    for row in speed_rows:
        key = (row.get("segment_id", ""), row.get("direction", ""))
        od_row = od_by_segment.get(key, {})
        cause = classify_free_flow_cause(row, od_row)
        counts[cause] += 1
        values = queue_metric_values(row)
        rows.append({
            "candidate": candidate_name,
            "segment_id": row.get("segment_id", ""),
            "direction": row.get("direction", ""),
            "free_flow_cause": cause,
            "queue_state": classify_b04_queue_state(row),
            "legacy_class": row.get("class", ""),
            "simulated_speed_kmh": row.get("simulated_speed_kmh", ""),
            "reference_speed_kmh": row.get("reference_speed_kmh", ""),
            "target_congestion_proxy": row.get("target_queue_proxy", ""),
            "segment_congestion_proxy": segment_congestion_proxy(row),
            "sumo_queue_proxy": row.get("sumo_queue_proxy", ""),
            "route_vehicle_count": od_row.get("route_vehicle_count", ""),
            "crossing_route_count": od_row.get("crossing_route_count", ""),
            "screenline_count": row.get("screenline_count", ""),
            "runtime_density_max": row.get("runtime_density_max", ""),
            "runtime_occupancy_max": row.get("runtime_occupancy_max", ""),
            "runtime_waiting_or_timeloss_max": row.get("runtime_waiting_or_timeloss_max", ""),
            "low_speed_interval_count": row.get("low_speed_interval_count", ""),
            "segment_weight": segment_weight(str(row.get("segment_id", ""))),
            "diagnostic_note": (
                "real CSV has speed/travel-time congestion proxy only; no field queue length"
                if values["target_queue_proxy"] > 0
                else ""
            ),
        })
    return rows, dict(counts)


def build_b04_traffic_demand_review(candidate_name: str | None = None) -> dict[str, Any]:
    queue_summary = build_b04_queue_audit(candidate_name)
    primary, diagnostic_best, candidate_names = review_candidate_names(candidate_name)
    comparison = candidate_comparison_by_name()
    free_rows, free_cause_counts = build_free_flow_cause_rows(primary)
    demand_rows = build_main_vs_offmain_demand_rows(candidate_names)
    growth_rows = demand_growth_candidate_summary_rows(demand_rows, comparison)
    outputs = traffic_demand_review_output_paths()
    write_csv(outputs["free_flow_cause_by_segment_csv"], free_rows, [
        "candidate", "segment_id", "direction", "free_flow_cause", "queue_state",
        "legacy_class", "simulated_speed_kmh", "reference_speed_kmh", "target_congestion_proxy",
        "segment_congestion_proxy", "sumo_queue_proxy", "route_vehicle_count",
        "crossing_route_count", "screenline_count", "runtime_density_max",
        "runtime_occupancy_max", "runtime_waiting_or_timeloss_max",
        "low_speed_interval_count", "segment_weight", "diagnostic_note",
    ])
    write_csv(outputs["main_vs_offmain_demand_audit_csv"], demand_rows, [
        "candidate", "vehicle_count", "route_count", "unique_source_count", "unique_sink_count",
        "main_through_flow", "main_local_flow", "feeder_inflow", "feeder_outflow",
        "terminal_sink_flow", "off_main_background_flow", "off_main_background_share",
        "conflict_flow_share", "top_source_share", "top_sink_share",
        "source_sink_bias_warning", "weak_offmain_conflict_warning",
        "tls_approach_conflict_proxy", "top_sources", "top_sinks", "settings",
    ])
    write_csv(outputs["demand_growth_candidate_summary_csv"], growth_rows, [
        "candidate", "artifact_status", "target_vehicle_count", "vehicle_count",
        "target_main_through_flow", "main_through_flow", "main_through_target_status",
        "terminal_sink_flow", "top_sink_share", "terminal_sink_status", "status",
        "speed_mae_kmh", "travel_time_mae_s", "background_teleported",
        "background_arrived_ratio", "calibration_note",
    ])
    candidate_summaries = []
    for name in candidate_names:
        row = comparison.get(name, {})
        candidate_summaries.append({
            "candidate": name,
            "status": row.get("status", ""),
            "speed_mae_kmh": safe_float(row.get("speed_mae_kmh")),
            "travel_time_mae_s": safe_float(row.get("travel_time_mae_s")),
            "free_count": int(safe_float(row.get("free_count"))),
            "od_missing_free_count": int(safe_float(row.get("od_missing_free_count"))),
            "od_undercovered_free_count": int(safe_float(row.get("od_undercovered_free_count"))),
            "queue_not_forming_free_count": int(safe_float(row.get("queue_not_forming_free_count"))),
            "measurement_warn_free_count": int(safe_float(row.get("measurement_warn_free_count"))),
            "background_teleported": int(safe_float(row.get("background_teleported"))),
            "background_arrived_ratio": safe_float(row.get("background_arrived_ratio")),
        })
    review = {
        "schema": "compact_v9_B04_B4_traffic_demand_review.v1",
        "generated_at": utc_now(),
        "primary_candidate": primary,
        "diagnostic_best_candidate": diagnostic_best,
        "review_candidates": candidate_names,
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "queue_length_policy": "No field queue length exists in the reference CSV; use speed/travel-time congestion proxy versus SUMO density/occupancy/waiting/timeLoss proxy.",
        "do_not_run_policy": "This review reads existing B04 B0 edgeData/laneData/tripinfo/demand artifacts only; it does not run B3, BO, SUMO, or FCD.",
        "candidate_summaries": candidate_summaries,
        "primary_free_flow_cause_counts": free_cause_counts,
        "demand_composition_summary": {
            "candidate_count": len(demand_rows),
            "weak_offmain_conflict_count": sum(1 for row in demand_rows if truthy(row.get("weak_offmain_conflict_warning"))),
            "source_sink_bias_warning_count": sum(1 for row in demand_rows if truthy(row.get("source_sink_bias_warning"))),
        },
        "demand_growth_candidate_summary": growth_rows,
        "queue_audit_summary": queue_summary,
        "outputs": {
            **{key: rel(path) for key, path in outputs.items()},
            **queue_summary.get("outputs", {}),
        },
    }
    write_json(outputs["traffic_demand_review_json"], review)
    return review


def write_review_html() -> dict[str, Any]:
    selection_path = B04_SELECTED_DIR / "selection_summary.json"
    selection = read_json(selection_path) if selection_path.is_file() else run_b0_all()
    rows_html = []
    for row in selection["candidates"]:
        rows_html.append(
            "<tr>"
            + "".join(f"<td>{html.escape(str(row.get(field, '')))}</td>" for field in [
                "candidate", "status", "speed_mae_kmh", "travel_time_mae_s",
                "stop_count_excluding_s22", "free_count", "queue_top10_overlap",
                "queue_classification", "dense_segment_direction_count", "od_missing_free_count",
                "od_undercovered_free_count", "queue_not_forming_free_count",
                "emergency_arrived", "background_teleported", "background_arrived",
                "background_departed", "background_arrived_ratio",
            ])
            + "</tr>"
        )
    selected_name = selection["selected"]["candidate"]
    manifest_selected_name = selection.get("manifest_selected_candidate", selected_name)
    selection_policy = selection.get("manifest_selection_policy", "updated_to_pass_or_warn")
    selected_speed_csv = METRICS_ROOT / selected_name / "B04_segment_speed_recall.csv"
    seg_rows = read_csv(selected_speed_csv) if selected_speed_csv.is_file() else []
    segment_html = []
    for row in seg_rows:
        segment_html.append(
            f"<tr><td>{html.escape(row['segment_id'])}</td><td>{html.escape(row['direction'])}</td>"
            f"<td>{row['reference_speed_kmh']}</td><td>{row['simulated_speed_kmh']}</td>"
            f"<td>{row['speed_error_kmh']}</td><td>{row.get('reference_travel_time_s', '')}</td>"
            f"<td>{row.get('simulated_travel_time_s', '')}</td><td>{row.get('travel_time_error_s', '')}</td>"
            f"<td>{row.get('reference_volume_vph', '')}</td><td>{row['observed_count']}</td>"
            f"<td>{row.get('fcd_traversal_sample_count', '')}</td><td>{row.get('screenline_edge', '')}</td>"
            f"<td>{row.get('runtime_queue_max_m', '')}</td><td>{row.get('runtime_slow_count_max', '')}</td>"
            f"<td>{row['max_density']}</td><td>{row['max_occupancy']}</td><td>{html.escape(row['class'])}</td></tr>"
        )
    selected_validation = read_json(METRICS_ROOT / selected_name / "B04_validation_summary.json") if (METRICS_ROOT / selected_name / "B04_validation_summary.json").is_file() else {}
    diagnostics = selected_validation.get("diagnostics", {})
    od_audit_rows = read_csv(METRICS_ROOT / selected_name / "B04_free_flow_od_audit.csv") if (METRICS_ROOT / selected_name / "B04_free_flow_od_audit.csv").is_file() else []
    od_audit_html = []
    for row in od_audit_rows:
        od_audit_html.append(
            f"<tr><td>{html.escape(row.get('segment_id', ''))}</td><td>{html.escape(row.get('direction', ''))}</td>"
            f"<td>{html.escape(row.get('reason', ''))}</td><td>{html.escape(row.get('screenline_edge', ''))}</td>"
            f"<td>{row.get('route_vehicle_count', '')}</td><td>{row.get('crossing_route_count', '')}</td>"
            f"<td>{row.get('screenline_count', '')}</td><td>{row.get('raw_edge_count_sum', '')}</td>"
            f"<td>{row.get('simulated_speed_kmh', '')}</td><td>{row.get('runtime_density_max', '')}</td>"
            f"<td>{row.get('runtime_occupancy_max', '')}</td></tr>"
        )
    diag_html = []
    for title, key in [
        ("Speed Error TOP 5", "speed_error_top5"),
        ("Travel Time Error TOP 5", "travel_time_error_top5"),
        ("Volume Error TOP 5", "volume_error_top5"),
    ]:
        items = diagnostics.get(key, [])
        diag_html.append(f"<h3>{html.escape(title)}</h3><ol>" + "".join(
            f"<li><code>{html.escape(str(item.get('segment_key', '')))}</code> speed {item.get('simulated_speed_kmh', '')}/{item.get('reference_speed_kmh', '')} km/h, travel {item.get('simulated_travel_time_s', '')}/{item.get('reference_travel_time_s', '')} s, volume {item.get('observed_count', '')}/{item.get('reference_volume_vph', '')}</li>"
            for item in items
        ) + "</ol>")
    diag_html.append(f"<p><strong>Free-flow:</strong> {html.escape(', '.join(diagnostics.get('free_flow_segment_list', [])))}</p>")
    diag_html.append(f"<p><strong>Stop/Queue:</strong> {html.escape(', '.join(diagnostics.get('stop_queue_segment_list', [])))}</p>")
    diag_html.append(f"<p><strong>Upbound over-congested:</strong> {html.escape(', '.join(diagnostics.get('upbound_overcongested', [])))}</p>")
    diag_html.append(f"<p><strong>Downbound free-flow:</strong> {html.escape(', '.join(diagnostics.get('downbound_freeflow', [])))}</p>")
    payload = {
        "reference_csv_abs": str(REFERENCE_CSV.resolve()),
        "b04_net": rel(B04_NET),
        "manifest": rel(B04_MANIFEST),
        "selection": selection,
    }
    B04_REVIEW_HTML.parent.mkdir(parents=True, exist_ok=True)
    B04_REVIEW_HTML.write_text(f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Compact V9 B04 Demand Validation</title>
  <style>
    body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin:0; color:#172033; }}
    header {{ padding:22px; border-bottom:1px solid #d8dee9; }}
    section {{ padding:18px 22px; }}
    table {{ border-collapse:collapse; width:100%; font-size:13px; margin-bottom:22px; }}
    th,td {{ border:1px solid #d8dee9; padding:7px; text-align:left; }}
    th {{ background:#f8fafc; }}
    code {{ background:#f2f4f7; padding:1px 4px; border-radius:4px; }}
    pre {{ padding:14px; border:1px solid #d8dee9; background:#f8fafc; overflow:auto; max-height:360px; }}
  </style>
</head>
<body>
<header>
  <h1>Compact V9 B04 현실 수요·Queue Recall</h1>
  <p>B04는 green18 맵 기반 B0/no_control baseline입니다. 기준 CSV: <code>{html.escape(str(REFERENCE_CSV.resolve()))}</code></p>
  <p>진단상 최선 후보: <strong>{html.escape(selected_name)}</strong></p>
  <p>Manifest 유지 후보: <strong>{html.escape(manifest_selected_name)}</strong> / 정책: <code>{html.escape(selection_policy)}</code></p>
</header>
<section>
  <h2>Candidate Summary</h2>
  <table><thead><tr><th>candidate</th><th>status</th><th>speed MAE</th><th>travel MAE</th><th>stop</th><th>free</th><th>queue overlap</th><th>queue</th><th>dense</th><th>OD missing</th><th>OD under</th><th>queue not forming</th><th>EV arrived</th><th>teleport</th><th>arrived</th><th>departed</th><th>arrived ratio</th></tr></thead><tbody>{''.join(rows_html)}</tbody></table>
  <h2>Worst Segment Diagnostics</h2>
  {''.join(diag_html)}
  <h2>Selected Free-flow O/D Audit</h2>
  <table><thead><tr><th>S</th><th>dir</th><th>reason</th><th>screenline</th><th>route veh</th><th>routes</th><th>screen count</th><th>raw count</th><th>sim speed</th><th>density</th><th>occupancy</th></tr></thead><tbody>{''.join(od_audit_html)}</tbody></table>
  <h2>Selected S1-S22 Speed / Queue Proxy</h2>
  <table><thead><tr><th>S</th><th>dir</th><th>ref speed</th><th>sim speed</th><th>err</th><th>ref TT</th><th>sim TT</th><th>TT err</th><th>ref vol</th><th>obs count</th><th>FCD n</th><th>screenline</th><th>runtime q m</th><th>slow max</th><th>density</th><th>occupancy</th><th>class</th></tr></thead><tbody>{''.join(segment_html)}</tbody></table>
  <pre>{html.escape(json.dumps(payload, ensure_ascii=False, indent=2))}</pre>
</section>
</body>
</html>
""", encoding="utf-8")
    return {**payload, "html": rel(B04_REVIEW_HTML)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compact V9 B04 baseline demand and queue-recall workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("b04-adopt-green18")
    sub.add_parser("b04-map-segments")
    sub.add_parser("b04-target-profile")
    build_parser = sub.add_parser("b04-build-demand")
    build_parser.add_argument("--candidates", help="Comma-separated candidate names to build")
    run_parser = sub.add_parser("b04-run-b0")
    run_parser.add_argument("--candidates", help="Comma-separated candidate names to run")
    validate_parser = sub.add_parser("b04-validate")
    validate_parser.add_argument("--candidates", help="Comma-separated candidate names to validate")
    audit_parser = sub.add_parser("b04-queue-audit")
    audit_parser.add_argument("--candidate", help="Override the manifest-selected B04 candidate for audit only")
    traffic_review_parser = sub.add_parser("b04-traffic-demand-review")
    traffic_review_parser.add_argument("--candidate", help="Override the diagnostic B04 candidate for review only")
    sub.add_parser("b04-review")
    sub.add_parser("b04-all")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "b04-adopt-green18":
        result = adopt_green18()
    elif args.command == "b04-map-segments":
        result = build_mapping()
    elif args.command == "b04-target-profile":
        result = build_target_profile()
    elif args.command == "b04-build-demand":
        result = build_demand(resolve_candidate_names(args.candidates))
    elif args.command == "b04-run-b0":
        result = run_b0_all(resolve_candidate_names(args.candidates))
    elif args.command == "b04-validate":
        result = {name: validate_candidate(name) for name in resolve_candidate_names(args.candidates)}
    elif args.command == "b04-queue-audit":
        result = build_b04_queue_audit(args.candidate)
    elif args.command == "b04-traffic-demand-review":
        result = build_b04_traffic_demand_review(args.candidate)
    elif args.command == "b04-review":
        result = write_review_html()
    elif args.command == "b04-all":
        adopt_green18()
        build_mapping()
        build_target_profile()
        build_demand()
        run_b0_all()
        result = write_review_html()
    else:
        raise B04Error(f"unknown_command:{args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
