#!/usr/bin/env python3
"""Shared helpers for the validated lane and demand calibration pipeline."""

from __future__ import annotations

import argparse
import copy
import csv
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = PROJECT_ROOT / "01-1 Validation/validate_b0_reality_recall.py"
FINAL_MANIFEST = PROJECT_ROOT / "configs/final_experiment_manifest.json"
DEFAULT_REFERENCE_CSV = PROJECT_ROOT / "toegye_ro_mainstream_segments_english.csv"
DEFAULT_BASE_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml"
DEFAULT_BASE_DEMAND = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19.rou.xml"
DEFAULT_MAPPING_CSV = PROJECT_ROOT / "data_prepared/validated/map/toegye_segment_edge_mapping.csv"
DEFAULT_LANE_OVERRIDES_CSV = PROJECT_ROOT / "data_prepared/validated/map/lane_overrides.csv"
DEFAULT_REPAIRED_NET = PROJECT_ROOT / "data_prepared/validated/net/jungbu_ellipse_passenger_speed50_lanes_repaired.net.xml"
DEFAULT_VALIDATED_MANIFEST = PROJECT_ROOT / "configs/validated_experiment_manifest.json"
DEFAULT_DEMAND_DIR = PROJECT_ROOT / "data_prepared/validated/demand"
DEFAULT_SCALE_SUMMARY = PROJECT_ROOT / "results/metrics/validated_demand_scale_variants/variant_summary.csv"
DEFAULT_SWEEP_SUMMARY = PROJECT_ROOT / "results/metrics/validated_b0_scale_sweep/sweep_summary.csv"

SCALE_GRID: list[tuple[float, float]] = [
    (0.15, 0.05),
    (0.25, 0.10),
    (0.40, 0.15),
    (0.60, 0.25),
    (0.80, 0.35),
    (1.00, 0.50),
]
BASELINE_S15_S22_OVER_OPEN_EDGE_COUNT = 51
S15_S22_CLEAR_REDUCTION_RATIO = 0.80


class ValidatedPipelineError(RuntimeError):
    """Expected validated-pipeline failure."""


def load_module(module_name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValidatedPipelineError(f"failed_to_load_module_spec:{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validator_module() -> Any:
    return load_module("validated_b0_reality_validator", VALIDATOR_PATH)


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def project_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else PROJECT_ROOT / value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValidatedPipelineError(f"json_root_not_object:{rel(path)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def bool_cell(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def scale_label(value: float) -> str:
    return f"{value:g}".replace(".", "p")


def warm_sustain_label(warmup_scale: float, sustain_scale: float) -> str:
    return f"warm{scale_label(warmup_scale)}_sustain{scale_label(sustain_scale)}"


def edge_axis_position(start: tuple[float, float], end: tuple[float, float], points: list[tuple[float, float]]) -> float:
    if not points:
        return 0.0
    midpoint = (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denom = dx * dx + dy * dy
    if denom == 0.0:
        return 0.0
    return max(0.0, min(1.0, ((midpoint[0] - start[0]) * dx + (midpoint[1] - start[1]) * dy) / denom))


def matched_sample_length_m(validator: Any, samples: list[tuple[float, float]], edge_points: list[tuple[float, float]], segment_length_m: float) -> float:
    if not samples:
        return 0.0
    hits = sum(1 for sample in samples if validator.point_polyline_distance_m(sample, edge_points) <= validator.MAP_COVER_DISTANCE_M)
    return segment_length_m * hits / len(samples)


def target_lanes_for(segment: Any, direction: str) -> int:
    if direction == "upbound":
        return int(round(float(segment.upbound_lanes)))
    if direction == "downbound":
        return int(round(float(segment.downbound_lanes)))
    raise ValidatedPipelineError(f"unknown_direction:{direction}")


def build_toegye_edge_mapping(reference_csv: Path, net_file: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    validator = validator_module()
    segments = validator.load_reference_segments(reference_csv)
    sumo_net, edges = validator.load_edge_features(net_file)
    rows: list[dict[str, Any]] = []
    for segment in segments:
        for direction in ["upbound", "downbound"]:
            start, end = validator.segment_points(sumo_net, segment, direction)
            samples = validator.sample_line_points(start, end)
            matched = validator.matched_edges_for_direction(sumo_net, segment, direction, edges)
            target_lanes = target_lanes_for(segment, direction)
            scored: list[tuple[float, dict[str, Any]]] = []
            for edge in matched:
                matched_length = matched_sample_length_m(validator, samples, edge.points, float(segment.length_m))
                position = edge_axis_position(start, end, edge.points)
                repair_target = edge.lane_count >= 2 and matched_length >= min(15.0, float(segment.length_m) * 0.5)
                scored.append(
                    (
                        position,
                        {
                            "segment_id": segment.segment_id,
                            "direction": direction,
                            "edge_id": edge.edge_id,
                            "edge_order": 0,
                            "axis_position": round(position, 6),
                            "matched_length_m": round(matched_length, 6),
                            "segment_length_m": round(float(segment.length_m), 6),
                            "match_ratio": round(matched_length / float(segment.length_m), 6) if float(segment.length_m) else 0.0,
                            "current_lanes": edge.lane_count,
                            "target_lanes": target_lanes,
                            "lane_delta": target_lanes - edge.lane_count,
                            "repair_target": repair_target,
                            "repair_reason": "mainline_overlap_lane_repair" if repair_target else "excluded_connector_or_low_overlap",
                        },
                    )
                )
            for index, (_position, row) in enumerate(sorted(scored, key=lambda item: (item[0], item[1]["edge_id"])), start=1):
                row["edge_order"] = index
                rows.append(row)
    summary = {
        "schema": "toegye_segment_edge_mapping.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(reference_csv),
        "net_file": rel(net_file),
        "row_count": len(rows),
        "repair_target_count": sum(1 for row in rows if row["repair_target"]),
        "segment_direction_count": len({(row["segment_id"], row["direction"]) for row in rows}),
    }
    return rows, summary


def build_lane_overrides(mapping_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in mapping_rows:
        if not bool_cell(row.get("repair_target")):
            continue
        edge_id = str(row["edge_id"])
        current = grouped.setdefault(
            edge_id,
            {
                "edge_id": edge_id,
                "target_lanes": 0,
                "current_lanes": int(float(row["current_lanes"])),
                "source_segment_ids": set(),
                "source_directions": set(),
                "source_rows": 0,
            },
        )
        current["target_lanes"] = max(int(current["target_lanes"]), int(float(row["target_lanes"])))
        current["current_lanes"] = max(int(current["current_lanes"]), int(float(row["current_lanes"])))
        current["source_segment_ids"].add(str(row["segment_id"]))
        current["source_directions"].add(str(row["direction"]))
        current["source_rows"] += 1
    overrides: list[dict[str, Any]] = []
    for edge_id, value in sorted(grouped.items()):
        target = int(value["target_lanes"])
        current_lanes = int(value["current_lanes"])
        overrides.append(
            {
                "edge_id": edge_id,
                "target_lanes": target,
                "current_lanes": current_lanes,
                "lane_delta": target - current_lanes,
                "source_segment_ids": " ".join(sorted(value["source_segment_ids"], key=lambda item: int(item[1:]) if item.startswith("S") and item[1:].isdigit() else 10**9)),
                "source_directions": " ".join(sorted(value["source_directions"])),
                "source_row_count": value["source_rows"],
                "repair_reason": "validated_toegye_mainline_lane_override",
            }
        )
    summary = {
        "schema": "lane_overrides.v1",
        "generated_at": utc_now(),
        "override_count": len(overrides),
        "changed_override_count": sum(1 for row in overrides if int(row["lane_delta"]) != 0),
    }
    return overrides, summary


def rewrite_plain_edge_lanes(edge_xml: Path, overrides_csv: Path, output_xml: Path | None = None) -> dict[str, Any]:
    overrides = {row["edge_id"]: int(float(row["target_lanes"])) for row in read_csv(overrides_csv)}
    tree = ET.parse(edge_xml)
    root = tree.getroot()
    changed_rows: list[dict[str, Any]] = []
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if not edge_id or edge_id not in overrides:
            continue
        old_value = int(float(edge.get("numLanes") or 1))
        new_value = overrides[edge_id]
        if old_value != new_value:
            edge.set("numLanes", str(new_value))
            changed_rows.append({"edge_id": edge_id, "old_numLanes": old_value, "new_numLanes": new_value})
    target = output_xml or edge_xml
    target.parent.mkdir(parents=True, exist_ok=True)
    tree.write(target, encoding="utf-8", xml_declaration=True)
    return {
        "edge_xml": rel(edge_xml),
        "output_xml": rel(target),
        "override_count": len(overrides),
        "changed_count": len(changed_rows),
        "changed_rows": changed_rows,
    }


def netconvert_path() -> str:
    path = shutil.which("netconvert")
    if not path:
        raise ValidatedPipelineError("netconvert_not_found_in_PATH")
    return path


def run_command(command: list[str], cwd: Path = PROJECT_ROOT, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True, timeout=timeout)


def rebuild_lane_repaired_net(base_net: Path, overrides_csv: Path, output_net: Path, work_dir: Path) -> dict[str, Any]:
    plain_dir = work_dir / "plain"
    plain_dir.mkdir(parents=True, exist_ok=True)
    prefix = plain_dir / "validated_plain"
    export_command = [netconvert_path(), "--sumo-net-file", str(base_net), "--plain-output-prefix", str(prefix)]
    export_completed = run_command(export_command)
    if export_completed.returncode != 0:
        raise ValidatedPipelineError(f"plain_export_failed:{export_completed.stderr[-2000:]}")
    node_file = prefix.with_suffix(".nod.xml")
    edge_file = prefix.with_suffix(".edg.xml")
    con_file = prefix.with_suffix(".con.xml")
    tll_file = prefix.with_suffix(".tll.xml")
    if not edge_file.is_file() or not node_file.is_file():
        raise ValidatedPipelineError(f"plain_export_missing_files:{plain_dir}")
    repaired_edge_file = plain_dir / "validated_plain_lanes_repaired.edg.xml"
    rewrite_summary = rewrite_plain_edge_lanes(edge_file, overrides_csv, repaired_edge_file)
    output_net.parent.mkdir(parents=True, exist_ok=True)
    primary_rebuild_command = [
        netconvert_path(),
        "--node-files",
        str(node_file),
        "--edge-files",
        str(repaired_edge_file),
        "--output-file",
        str(output_net),
        "--no-turnarounds",
        "true",
    ]
    if con_file.is_file():
        primary_rebuild_command.extend(["--connection-files", str(con_file)])
    if tll_file.is_file():
        primary_rebuild_command.extend(["--tllogic-files", str(tll_file)])
    rebuild_command = primary_rebuild_command
    rebuild_completed = run_command(primary_rebuild_command)
    primary_rebuild_stderr = rebuild_completed.stderr[-4000:]
    fallback_rebuild_command: list[str] = []
    fallback_rebuild_stderr = ""
    fallback_used = False
    if rebuild_completed.returncode != 0:
        fallback_rebuild_command = [
            netconvert_path(),
            "--node-files",
            str(node_file),
            "--edge-files",
            str(repaired_edge_file),
            "--output-file",
            str(output_net),
            "--no-turnarounds",
            "true",
            "--tls.rebuild",
            "true",
        ]
        fallback_completed = run_command(fallback_rebuild_command)
        fallback_rebuild_stderr = fallback_completed.stderr[-4000:]
        if fallback_completed.returncode != 0:
            raise ValidatedPipelineError(
                "net_rebuild_failed:"
                f"primary={rebuild_completed.stderr[-2000:]}"
                f"\nfallback={fallback_completed.stderr[-2000:]}"
            )
        rebuild_command = fallback_rebuild_command
        rebuild_completed = fallback_completed
        fallback_used = True
    return {
        "schema": "validated_lane_repair_report.v1",
        "generated_at": utc_now(),
        "base_net": rel(base_net),
        "output_net": rel(output_net),
        "overrides_csv": rel(overrides_csv),
        "work_dir": rel(work_dir),
        "plain_export_command": export_command,
        "plain_export_stderr": export_completed.stderr[-4000:],
        "rewrite_summary": rewrite_summary,
        "rebuild_command": rebuild_command,
        "primary_rebuild_command": primary_rebuild_command,
        "primary_rebuild_stderr": primary_rebuild_stderr,
        "fallback_rebuild_command": fallback_rebuild_command,
        "fallback_rebuild_stderr": fallback_rebuild_stderr,
        "fallback_used": fallback_used,
        "rebuild_stderr": rebuild_completed.stderr[-4000:],
    }


def validate_repaired_map(reference_csv: Path, net_file: Path) -> dict[str, Any]:
    validator = validator_module()
    segments = validator.load_reference_segments(reference_csv)
    sumo_net, edges = validator.load_edge_features(net_file)
    lane_rows, lane_status, lane_summary = validator.build_lane_rows(sumo_net, segments, edges)
    route_status = "PASS"
    route_reason = ""
    try:
        final_runner = load_module("validated_final_runner", PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py")
        route = final_runner.synthetic_seoul_station_route(net_file)
        route_edge_ids = route["route_edges"].split()
    except Exception as exc:  # noqa: BLE001 - map validation should report route failure explicitly.
        route_status = "FAIL"
        route_reason = f"{type(exc).__name__}:{exc}"
        route_edge_ids = []
    root = ET.parse(net_file).getroot()
    tl_logic_count = len(root.findall("tlLogic"))
    tls_connection_count = sum(1 for connection in root.findall("connection") if connection.get("tl"))
    overall_status = "PASS" if lane_status == "PASS" and route_status == "PASS" and tl_logic_count > 0 and tls_connection_count > 0 else "FAIL"
    return {
        "schema": "validated_repaired_map_validation.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(reference_csv),
        "net_file": rel(net_file),
        "overall_status": overall_status,
        "lane_status": lane_status,
        "lane": lane_summary,
        "route_status": route_status,
        "route_reason": route_reason,
        "route_edge_count": len(route_edge_ids),
        "tl_logic_count": tl_logic_count,
        "tls_connection_count": tls_connection_count,
        "lane_rows": lane_rows,
    }


def build_demand_variants(base_route: Path, output_dir: Path, scales: list[tuple[float, float]], sampling_seed: str = "validated_scale_grid_v1") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    demand_module = load_module("validated_sustained_demand", PROJECT_ROOT / "01_prepare/05_demand/step09c_build_sustained_background_demand.py")
    rows: list[dict[str, Any]] = []
    for warmup_scale, sustain_scale in scales:
        label = warm_sustain_label(warmup_scale, sustain_scale)
        output = output_dir / f"background_routes_validated_{label}.rou.xml"
        summary = demand_module.build_sustained(
            base_route,
            output,
            period_sec=600.0,
            duration_sec=3600.0,
            warmup_scale=warmup_scale,
            sustain_scale=sustain_scale,
            sampling_seed=sampling_seed,
        )
        rows.append(
            {
                "scale_label": label,
                "warmup_scale": warmup_scale,
                "sustain_scale": sustain_scale,
                "route_file": rel(output),
                "vehicle_count": summary["vehicle_count"],
                "vehicle_counts_by_cycle": " ".join(str(value) for value in summary["vehicle_counts_by_cycle"]),
            }
        )
    return rows, {"schema": "validated_demand_scale_variants.v1", "generated_at": utc_now(), "variant_count": len(rows)}


def validated_manifest_payload(net_file: Path, demand_file: Path, warmup_scale: float, sustain_scale: float, notes: str = "") -> dict[str, Any]:
    base = read_json(FINAL_MANIFEST)
    payload = copy.deepcopy(base)
    payload["schema"] = "validated_experiment_manifest.v1"
    payload["active_net"] = rel(net_file)
    payload["background_route"] = rel(demand_file)
    design = dict(payload.get("background_demand_design", {}))
    design.update(
        {
            "source_pattern": rel(DEFAULT_BASE_DEMAND),
            "method": "validated_lane_repair_repeat_600s_topis_pattern_with_split_scale",
            "warmup_scale": warmup_scale,
            "sustain_scale": sustain_scale,
            "sampling_seed": "validated_scale_grid_v1",
        }
    )
    payload["background_demand_design"] = design
    payload["final_background_required_substring"] = Path(demand_file).name
    payload["validated_inputs"] = {
        "base_manifest": rel(FINAL_MANIFEST),
        "lane_repaired_net": rel(net_file),
        "selected_demand": rel(demand_file),
    }
    payload["notes"] = notes or "Validated lane-repaired B0 calibration manifest. Original final manifest remains unchanged."
    return payload


def focus_over_open_count(edge_speed_csv: Path, start_segment: int = 15, end_segment: int = 22) -> int:
    if not edge_speed_csv.is_file():
        return 10**9
    focus = {f"S{index}" for index in range(start_segment, end_segment + 1)}
    return sum(1 for row in read_csv(edge_speed_csv) if row.get("segment_id") in focus and row.get("anomaly_type") == "over_open_speed")


def selection_score(row: dict[str, str]) -> float:
    penalty = 0.0
    if row.get("lane_status") != "PASS":
        penalty += 10000.0
    if row.get("runner_returncode") not in {"", "0", 0}:
        penalty += 5000.0
    if row.get("sumo_exit_code") not in {"", "0", 0}:
        penalty += 5000.0
    if row.get("route_error_count") not in {"", "0", 0}:
        penalty += 2000.0
    try:
        penalty += float(row.get("background_teleported") or 0.0) * 100.0
    except ValueError:
        penalty += 1000.0
    if row.get("demand_status") == "FAIL":
        penalty += 1000.0
    if row.get("speed_status") == "FAIL":
        penalty += 1000.0
    if row.get("edge_speed_status") == "FAIL":
        penalty += 1000.0
    try:
        penalty += float(row.get("speed_mae_kmh") or 999.0)
    except ValueError:
        penalty += 999.0
    try:
        penalty += float(row.get("s15_s22_over_open_edge_count") or 999.0) * 0.5
    except ValueError:
        penalty += 999.0
    try:
        penalty += abs(1.0 - float(row.get("median_scaled_recall") or 1.0)) * 10.0
    except ValueError:
        pass
    return penalty


def needs_downstream_or_tls_calibration(row: dict[str, str]) -> bool:
    if row.get("speed_status") == "FAIL" or row.get("edge_speed_status") == "FAIL":
        return True
    try:
        focus_count = int(float(row.get("s15_s22_over_open_edge_count") or 10**9))
    except ValueError:
        return True
    clear_reduction_threshold = math.floor(BASELINE_S15_S22_OVER_OPEN_EDGE_COUNT * S15_S22_CLEAR_REDUCTION_RATIO)
    if focus_count >= clear_reduction_threshold:
        return True
    try:
        return float(row.get("background_teleported") or 0.0) > 0.0 or float(row.get("route_error_count") or 0.0) > 0.0
    except ValueError:
        return True


def parse_scale_pairs(values: list[str] | None) -> list[tuple[float, float]]:
    if not values:
        return SCALE_GRID
    pairs: list[tuple[float, float]] = []
    for value in values:
        if "/" not in value:
            raise argparse.ArgumentTypeError(f"scale pair must be warmup/sustain: {value}")
        warm, sustain = value.split("/", 1)
        pairs.append((float(warm), float(sustain)))
    return pairs
