#!/usr/bin/env python3
"""Shared helpers for the validated lane and demand calibration pipeline."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
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
DEFAULT_MANUAL_LANE_OVERRIDES_CSV = PROJECT_ROOT / "01-2 Validated/validated_route_strict_lane_overrides.csv"
DEFAULT_REPAIRED_NET = PROJECT_ROOT / "data_prepared/validated/net/jungbu_ellipse_passenger_speed50_lanes_repaired.net.xml"
DEFAULT_VALIDATED_MANIFEST = PROJECT_ROOT / "configs/validated_experiment_manifest.json"
DEFAULT_DEMAND_DIR = PROJECT_ROOT / "data_prepared/validated/demand"
DEFAULT_SCALE_SUMMARY = PROJECT_ROOT / "results/metrics/validated_demand_scale_variants/variant_summary.csv"
DEFAULT_REFERENCE_SCREENLINE_VARIANT_SUMMARY = PROJECT_ROOT / "results/metrics/validated_reference_screenline_demand/variant_summary.csv"
DEFAULT_REFERENCE_DISTRIBUTED_VARIANT_SUMMARY = PROJECT_ROOT / "results/metrics/validated_reference_distributed_demand/variant_summary.csv"
DEFAULT_SWEEP_SUMMARY = PROJECT_ROOT / "results/metrics/validated_b0_scale_sweep/sweep_summary.csv"
DEFAULT_REFERENCE_DISTRIBUTED_V2_VARIANT_SUMMARY = PROJECT_ROOT / "results/metrics/validated_reference_distributed_demand_v2/variant_summary.csv"
DEFAULT_TLS_BOUNDARY_CANDIDATE_SUMMARY = PROJECT_ROOT / "results/metrics/validated_tls_boundary_candidates/candidate_summary.csv"

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
MAINLINE_REPAIR_MIN_MATCH_RATIO = 0.35
MAINLINE_REPAIR_MIN_MATCH_M = 20.0
REFERENCE_SCREENLINE_LABEL = "reference_screenline_bidirectional"
REFERENCE_DISTRIBUTED_LABEL = "reference_distributed_od"
REFERENCE_DISTRIBUTED_V2_LABEL = "reference_distributed_od_v2"
REFERENCE_SCREENLINE_UP_FULL_TARGET_EDGE = "1206223945"
REFERENCE_SCREENLINE_UP_PREFIX_TARGET_EDGE = "785130600#2"
REFERENCE_SCREENLINE_DOWN_PREFIX_START_EDGE = "1455512069"
CALIBRATED_NET_DIR = PROJECT_ROOT / "data_prepared/validated/calibrated/net"
CALIBRATED_DEMAND_DIR = PROJECT_ROOT / "data_prepared/validated/calibrated/demand"
CALIBRATED_MANIFEST = PROJECT_ROOT / "configs/validated_calibrated_b0_manifest.json"


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


def is_repair_target(match_ratio: float, matched_length_m: float, segment_length_m: float) -> bool:
    min_match_m = min(MAINLINE_REPAIR_MIN_MATCH_M, segment_length_m * 0.5)
    return match_ratio >= MAINLINE_REPAIR_MIN_MATCH_RATIO and matched_length_m >= min_match_m


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
                match_ratio = matched_length / float(segment.length_m) if float(segment.length_m) else 0.0
                position = edge_axis_position(start, end, edge.points)
                repair_target = is_repair_target(match_ratio, matched_length, float(segment.length_m))
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
                            "match_ratio": round(match_ratio, 6),
                            "current_lanes": edge.lane_count,
                            "target_lanes": target_lanes,
                            "lane_delta": target_lanes - edge.lane_count,
                            "repair_target": repair_target,
                            "repair_reason": "dominant_mainline_overlap_lane_repair" if repair_target else "excluded_low_overlap",
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


def load_manual_lane_overrides(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.is_file():
        return []
    rows = read_csv(path)
    required = {"edge_id", "target_lanes", "source_segment_ids", "source_directions"}
    missing = sorted(required - set(rows[0].keys() if rows else []))
    if missing:
        raise ValidatedPipelineError(f"manual_lane_overrides_missing_columns:{rel(path)}:{','.join(missing)}")
    return rows


def build_lane_overrides(mapping_rows: list[dict[str, Any]], manual_rows: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in mapping_rows:
        if not bool_cell(row.get("repair_target")):
            continue
        edge_id = str(row["edge_id"])
        grouped.setdefault(edge_id, []).append(row)
    mapping_by_edge: dict[str, list[dict[str, Any]]] = {}
    for row in mapping_rows:
        mapping_by_edge.setdefault(str(row["edge_id"]), []).append(row)
    overrides: list[dict[str, Any]] = []
    for edge_id, rows in sorted(grouped.items()):
        best_ratio = max(float(row["match_ratio"]) for row in rows)
        dominant_rows = [row for row in rows if float(row["match_ratio"]) == best_ratio]
        if len(dominant_rows) > 1:
            best_length = max(float(row["matched_length_m"]) for row in dominant_rows)
            dominant_rows = [row for row in dominant_rows if float(row["matched_length_m"]) == best_length]
        target = max(int(float(row["target_lanes"])) for row in dominant_rows)
        current_lanes = max(int(float(row["current_lanes"])) for row in rows)
        source_segment_ids = {str(row["segment_id"]) for row in rows}
        source_directions = {str(row["direction"]) for row in rows}
        dominant_segment_ids = {str(row["segment_id"]) for row in dominant_rows}
        dominant_directions = {str(row["direction"]) for row in dominant_rows}
        overrides.append(
            {
                "edge_id": edge_id,
                "target_lanes": target,
                "current_lanes": current_lanes,
                "lane_delta": target - current_lanes,
                "source_segment_ids": " ".join(sorted(source_segment_ids, key=lambda item: int(item[1:]) if item.startswith("S") and item[1:].isdigit() else 10**9)),
                "source_directions": " ".join(sorted(source_directions)),
                "source_row_count": len(rows),
                "dominant_segment_ids": " ".join(sorted(dominant_segment_ids, key=lambda item: int(item[1:]) if item.startswith("S") and item[1:].isdigit() else 10**9)),
                "dominant_directions": " ".join(sorted(dominant_directions)),
                "dominant_match_ratio": round(best_ratio, 6),
                "repair_reason": "validated_toegye_dominant_mainline_lane_override",
            }
        )
    by_edge = {str(row["edge_id"]): row for row in overrides}
    for manual in manual_rows or []:
        edge_id = str(manual["edge_id"]).strip()
        if not edge_id:
            continue
        target = int(float(manual["target_lanes"]))
        related_rows = mapping_by_edge.get(edge_id, [])
        existing = by_edge.get(edge_id)
        if existing is not None:
            current_lanes = int(float(existing["current_lanes"]))
        elif related_rows:
            current_lanes = max(int(float(row["current_lanes"])) for row in related_rows)
        elif manual.get("current_lanes"):
            current_lanes = int(float(manual["current_lanes"]))
        else:
            raise ValidatedPipelineError(f"manual_lane_override_without_current_lanes:{edge_id}")
        source_segment_ids = str(manual["source_segment_ids"]).strip()
        source_directions = str(manual["source_directions"]).strip()
        manual_row = {
            "edge_id": edge_id,
            "target_lanes": target,
            "current_lanes": current_lanes,
            "lane_delta": target - current_lanes,
            "source_segment_ids": source_segment_ids,
            "source_directions": source_directions,
            "source_row_count": len(related_rows) if related_rows else 1,
            "dominant_segment_ids": source_segment_ids,
            "dominant_directions": source_directions,
            "dominant_match_ratio": manual.get("dominant_match_ratio", "manual"),
            "repair_reason": manual.get("repair_reason") or "route_strict_lane_override",
        }
        by_edge[edge_id] = manual_row
    overrides = [by_edge[edge_id] for edge_id in sorted(by_edge)]
    summary = {
        "schema": "lane_overrides.v1",
        "generated_at": utc_now(),
        "override_count": len(overrides),
        "changed_override_count": sum(1 for row in overrides if int(row["lane_delta"]) != 0),
        "manual_override_count": len(manual_rows or []),
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


def reference_segment_number(segment_id: str) -> int:
    if not segment_id.startswith("S"):
        raise ValidatedPipelineError(f"invalid_segment_id:{segment_id}")
    try:
        return int(segment_id[1:])
    except ValueError as exc:
        raise ValidatedPipelineError(f"invalid_segment_id:{segment_id}") from exc


def reference_screenline_volumes(reference_csv: Path) -> list[dict[str, Any]]:
    rows = read_csv(reference_csv)
    required = {"segment_id", "peak_hour_volume_veh_per_h_reference"}
    missing = sorted(required - set(rows[0].keys() if rows else []))
    if missing:
        raise ValidatedPipelineError(f"reference_csv_missing_columns:{','.join(missing)}")
    parsed: list[dict[str, Any]] = []
    for row in rows:
        segment_id = row["segment_id"].strip()
        volume = float(row["peak_hour_volume_veh_per_h_reference"])
        if volume <= 0:
            raise ValidatedPipelineError(f"nonpositive_reference_volume:{segment_id}")
        parsed.append({"segment_id": segment_id, "segment_number": reference_segment_number(segment_id), "volume_vph": volume})
    parsed.sort(key=lambda row: row["segment_number"])
    if len(parsed) != 22:
        raise ValidatedPipelineError(f"reference_csv_expected_22_segments_got:{len(parsed)}")
    return parsed


def evenly_spaced_departures(vph: float, duration_sec: float, offset_sec: float) -> list[float]:
    if vph <= 0:
        return []
    count = round(vph * duration_sec / 3600.0)
    if count <= 0:
        return []
    step = duration_sec / count
    return [offset_sec + index * step for index in range(count)]


def segment_direction_key(segment_id: str, direction: str) -> str:
    return f"{segment_id}:{direction}"


def target_counts_from_reference(reference_csv: Path, duration_sec: float) -> dict[str, float]:
    return {
        segment_direction_key(row["segment_id"], direction): row["volume_vph"] * duration_sec / 3600.0
        for row in reference_screenline_volumes(reference_csv)
        for direction in ("upbound", "downbound")
    }


def edge_segment_direction_index(mapping_csv: Path, repair_targets_only: bool = True) -> dict[str, set[str]]:
    rows = read_csv(mapping_csv)
    index: dict[str, set[str]] = {}
    for row in rows:
        if repair_targets_only and not bool_cell(row.get("repair_target", "")):
            continue
        edge_id = row.get("edge_id", "").strip()
        segment_id = row.get("segment_id", "").strip()
        direction = row.get("direction", "").strip()
        if edge_id and segment_id and direction:
            index.setdefault(edge_id, set()).add(segment_direction_key(segment_id, direction))
    return index


def vehicle_route_edges(vehicle: ET.Element) -> list[str]:
    route = vehicle.find("route")
    if route is None:
        return []
    return [edge for edge in (route.get("edges") or "").split() if edge]


def route_coverage_keys(route_edges: list[str], edge_index: dict[str, set[str]]) -> set[str]:
    coverage: set[str] = set()
    for edge_id in route_edges:
        coverage.update(edge_index.get(edge_id, set()))
    return coverage


def stable_hash_int(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:16], 16)


def greedy_select_distributed_templates(
    templates: list[dict[str, Any]],
    targets: dict[str, float],
    max_iterations: int,
    max_overfill_ratio: float = 0.30,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    remaining = dict(targets)
    selected: list[dict[str, Any]] = []
    usage: dict[str, int] = {template["template_id"]: 0 for template in templates}
    for iteration in range(max_iterations):
        best: dict[str, Any] | None = None
        best_score = 0.0
        best_tiebreak: tuple[int, int] = (10**9, 10**18)
        for template in templates:
            coverage = template["coverage_keys"]
            if any(remaining.get(key, 0.0) <= -targets.get(key, 1.0) * max_overfill_ratio for key in coverage if key in targets):
                continue
            deficit_score = sum(max(remaining.get(key, 0.0), 0.0) / max(targets.get(key, 1.0), 1.0) for key in coverage if key in targets)
            if deficit_score <= 0.0:
                continue
            overfill_penalty = sum(max(-remaining.get(key, 0.0), 0.0) / max(targets.get(key, 1.0), 1.0) for key in coverage if key in targets)
            score = deficit_score - 2.0 * overfill_penalty
            if score <= 0.0:
                continue
            tiebreak = (usage[template["template_id"]], stable_hash_int(f"{iteration}:{template['template_id']}"))
            if score > best_score or (math.isclose(score, best_score) and tiebreak < best_tiebreak):
                best = template
                best_score = score
                best_tiebreak = tiebreak
        if best is None:
            break
        selected.append(best)
        usage[best["template_id"]] += 1
        for key in best["coverage_keys"]:
            if key in remaining:
                remaining[key] -= 1.0
        if all(value < 1.0 for value in remaining.values()):
            break
    return selected, remaining


def route_from_shortest(runner: Any, sumo_net: Any, start_edge: str, target_edge: str) -> list[str]:
    route = runner.S07.shortest_route(sumo_net, start_edge, target_edge)
    runner.validate_route_transitions(sumo_net, route)
    return route


def build_reference_screenline_demand(
    reference_csv: Path,
    net_file: Path,
    output_route: Path,
    duration_sec: float = 7200.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if duration_sec <= 0:
        raise ValidatedPipelineError("duration_sec_must_be_positive")
    volumes = reference_screenline_volumes(reference_csv)
    base_vph = min(row["volume_vph"] for row in volumes)
    max_vph = max(row["volume_vph"] for row in volumes)
    high_prefix = [row for row in volumes if row["volume_vph"] > base_vph]
    high_prefix_ids = [row["segment_id"] for row in high_prefix]
    if high_prefix_ids and high_prefix_ids != [f"S{index}" for index in range(1, len(high_prefix_ids) + 1)]:
        raise ValidatedPipelineError("reference_screenline_only_supports_leading_extra_volume")
    extra_vph = max(0.0, max_vph - base_vph)

    runner = load_module("validated_b0_runner_for_reference_demand", PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py")
    runner.configure_runtime_environment()
    sumo_net = runner.S07.read_sumo_net(net_file)
    start_edge = runner.SEOUL_STATION_START_EDGE
    full_target = REFERENCE_SCREENLINE_UP_FULL_TARGET_EDGE
    up_full = route_from_shortest(runner, sumo_net, start_edge, full_target)
    down_full = route_from_shortest(runner, sumo_net, full_target, start_edge)

    flows: list[dict[str, Any]] = [
        {
            "flow_id": "ref_up_full",
            "direction": "upbound",
            "segment_scope": "S1-S22",
            "vph": base_vph,
            "route_edges": up_full,
            "depart_offset_sec": 0.0,
        },
        {
            "flow_id": "ref_down_full",
            "direction": "downbound",
            "segment_scope": "S1-S22",
            "vph": base_vph,
            "route_edges": down_full,
            "depart_offset_sec": 0.5,
        },
    ]
    if extra_vph > 0:
        prefix_scope = f"S1-S{len(high_prefix_ids)}"
        flows.extend(
            [
                {
                    "flow_id": "ref_up_prefix_extra",
                    "direction": "upbound",
                    "segment_scope": prefix_scope,
                    "vph": extra_vph,
                    "route_edges": route_from_shortest(runner, sumo_net, start_edge, REFERENCE_SCREENLINE_UP_PREFIX_TARGET_EDGE),
                    "depart_offset_sec": 1.0,
                },
                {
                    "flow_id": "ref_down_prefix_extra",
                    "direction": "downbound",
                    "segment_scope": prefix_scope,
                    "vph": extra_vph,
                    "route_edges": route_from_shortest(runner, sumo_net, REFERENCE_SCREENLINE_DOWN_PREFIX_START_EDGE, start_edge),
                    "depart_offset_sec": 1.5,
                },
            ]
        )

    output_root = ET.Element("routes")
    output_root.append(
        ET.Comment(
            "validated reference screenline demand generated from "
            f"{rel(reference_csv)} for duration_sec={duration_sec:g}; "
            "segment volumes are decomposed into through flow plus leading-prefix extra flow"
        )
    )
    vehicle_rows: list[tuple[float, str, list[str]]] = []
    flow_rows: list[dict[str, Any]] = []
    for flow in flows:
        departures = evenly_spaced_departures(float(flow["vph"]), duration_sec, float(flow["depart_offset_sec"]))
        flow_rows.append(
            {
                "flow_id": flow["flow_id"],
                "direction": flow["direction"],
                "segment_scope": flow["segment_scope"],
                "vph": float(flow["vph"]),
                "vehicle_count": len(departures),
                "route_edge_count": len(flow["route_edges"]),
                "start_edge": flow["route_edges"][0],
                "target_edge": flow["route_edges"][-1],
            }
        )
        for index, depart in enumerate(departures):
            vehicle_id = f"{flow['flow_id']}_{index:05d}"
            vehicle_rows.append((depart, vehicle_id, flow["route_edges"]))
    for depart, vehicle_id, route_edges in sorted(vehicle_rows, key=lambda item: (item[0], item[1])):
        vehicle = ET.SubElement(output_root, "vehicle", {"id": vehicle_id, "depart": f"{depart:.2f}"})
        ET.SubElement(vehicle, "route", {"edges": " ".join(route_edges)})
    output_route.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(output_root).write(output_route, encoding="utf-8", xml_declaration=True)
    summary = {
        "schema": "validated_reference_screenline_demand.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(reference_csv),
        "net_file": rel(net_file),
        "route_file": rel(output_route),
        "duration_sec": duration_sec,
        "base_through_vph": base_vph,
        "leading_prefix_extra_vph": extra_vph,
        "leading_prefix_segments": " ".join(high_prefix_ids),
        "vehicle_count": len(vehicle_rows),
        "flow_count": len(flow_rows),
        "method": "screenline_decomposition_bidirectional_through_plus_leading_prefix_extra",
    }
    return flow_rows, summary


def build_reference_distributed_demand(
    reference_csv: Path,
    mapping_csv: Path,
    base_route: Path,
    output_route: Path,
    duration_sec: float = 7200.0,
    max_vehicles: int = 10000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if duration_sec <= 0:
        raise ValidatedPipelineError("duration_sec_must_be_positive")
    if max_vehicles <= 0:
        raise ValidatedPipelineError("max_vehicles_must_be_positive")
    targets = target_counts_from_reference(reference_csv, duration_sec)
    edge_index = edge_segment_direction_index(mapping_csv, repair_targets_only=False)
    root = ET.parse(base_route).getroot()
    templates: list[dict[str, Any]] = []
    seen_routes: set[str] = set()
    for vehicle in root.findall("vehicle"):
        route_edges = vehicle_route_edges(vehicle)
        if not route_edges:
            continue
        coverage = route_coverage_keys(route_edges, edge_index)
        if not coverage:
            continue
        route_key = " ".join(route_edges)
        if route_key in seen_routes:
            continue
        seen_routes.add(route_key)
        templates.append(
            {
                "template_id": vehicle.get("id", f"template_{len(templates)}"),
                "route_edges": route_edges,
                "coverage_keys": coverage,
            }
        )
    if not templates:
        raise ValidatedPipelineError("no_base_route_templates_cover_reference_segments")
    selected, remaining = greedy_select_distributed_templates(templates, targets, max_vehicles)
    output_root = ET.Element(root.tag, root.attrib)
    output_root.append(
        ET.Comment(
            "validated distributed reference demand generated from existing OD route templates; "
            f"reference={rel(reference_csv)} mapping={rel(mapping_csv)} duration_sec={duration_sec:g}"
        )
    )
    for child in list(root):
        if child.tag != "vehicle":
            output_root.append(copy.deepcopy(child))
    total = len(selected)
    coverage_counts = {key: 0 for key in targets}
    for index, template in enumerate(selected):
        depart = (index + 0.5) * duration_sec / max(total, 1)
        vehicle = ET.SubElement(output_root, "vehicle", {"id": f"ref_dist_{index:05d}", "depart": f"{depart:.2f}"})
        ET.SubElement(vehicle, "route", {"edges": " ".join(template["route_edges"])})
        for key in template["coverage_keys"]:
            if key in coverage_counts:
                coverage_counts[key] += 1
    output_route.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(output_root).write(output_route, encoding="utf-8", xml_declaration=True)
    segment_rows: list[dict[str, Any]] = []
    for key in sorted(targets, key=lambda value: (reference_segment_number(value.split(":")[0]), value.split(":")[1])):
        segment_id, direction = key.split(":", 1)
        target = targets[key]
        generated = coverage_counts[key]
        segment_rows.append(
            {
                "segment_id": segment_id,
                "direction": direction,
                "target_count": round(target, 6),
                "generated_template_count": generated,
                "generated_recall": round(generated / target, 6) if target else "",
                "remaining_count": round(remaining.get(key, 0.0), 6),
            }
        )
    summary = {
        "schema": "validated_reference_distributed_demand.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(reference_csv),
        "mapping_csv": rel(mapping_csv),
        "base_route": rel(base_route),
        "route_file": rel(output_route),
        "duration_sec": duration_sec,
        "candidate_template_count": len(templates),
        "vehicle_count": total,
        "mean_generated_recall": round(sum(float(row["generated_recall"]) for row in segment_rows) / len(segment_rows), 6),
        "min_generated_recall": min(float(row["generated_recall"]) for row in segment_rows),
        "max_generated_recall": max(float(row["generated_recall"]) for row in segment_rows),
        "method": "greedy_existing_od_template_selection_to_reference_segment_targets",
    }
    return segment_rows, summary


def passenger_outgoing_edge_ids(runner: Any, edge: Any) -> list[str]:
    outgoing = []
    for candidate in edge.getOutgoing().keys():
        edge_id = candidate.getID()
        try:
            allows = candidate.allows("passenger")
        except Exception:
            allows = True
        if edge_id and allows:
            outgoing.append(edge_id)
    return sorted(outgoing)


def extend_route_edges(runner: Any, sumo_net: Any, route_edges: list[str], extension_steps: int = 3) -> list[str]:
    if extension_steps <= 0 or not route_edges:
        return list(route_edges)
    extended = list(route_edges)
    seen = set(extended)
    for _ in range(extension_steps):
        edge = runner.S07.edge_from_net(sumo_net, extended[-1])
        if edge is None:
            break
        candidates = [edge_id for edge_id in passenger_outgoing_edge_ids(runner, edge) if edge_id not in seen]
        if not candidates:
            break
        selected = candidates[0]
        extended.append(selected)
        seen.add(selected)
    try:
        runner.validate_route_transitions(sumo_net, extended)
    except Exception:
        return list(route_edges)
    return extended


def build_reference_distributed_demand_v2(
    reference_csv: Path,
    mapping_csv: Path,
    base_route: Path,
    net_file: Path,
    output_route: Path,
    duration_sec: float = 7200.0,
    max_vehicles: int = 10000,
    extension_steps: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    targets = target_counts_from_reference(reference_csv, duration_sec)
    edge_index = edge_segment_direction_index(mapping_csv, repair_targets_only=False)
    base_root = ET.parse(base_route).getroot()
    runner = load_module("validated_b0_runner_for_distributed_v2", PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py")
    runner.configure_runtime_environment()
    sumo_net = runner.S07.read_sumo_net(net_file)
    templates: list[dict[str, Any]] = []
    seen_routes: set[str] = set()
    for vehicle in base_root.findall("vehicle"):
        route_edges = vehicle_route_edges(vehicle)
        coverage = route_coverage_keys(route_edges, edge_index)
        if not route_edges or not coverage:
            continue
        route_key = " ".join(route_edges)
        if route_key in seen_routes:
            continue
        seen_routes.add(route_key)
        templates.append(
            {
                "template_id": vehicle.get("id", f"template_{len(templates)}"),
                "route_edges": route_edges,
                "coverage_keys": coverage,
                "start_edge": route_edges[0],
                "sink_edge": route_edges[-1],
            }
        )
    if not templates:
        raise ValidatedPipelineError("no_base_route_templates_cover_reference_segments")
    selected, remaining = greedy_select_distributed_templates(templates, targets, max_vehicles)
    output_root = ET.Element(base_root.tag, base_root.attrib)
    output_root.append(
        ET.Comment(
            "validated distributed reference demand v2; existing OD templates, insertion attrs, route sink extension; "
            f"reference={rel(reference_csv)} mapping={rel(mapping_csv)} duration_sec={duration_sec:g}"
        )
    )
    for child in list(base_root):
        if child.tag != "vehicle":
            output_root.append(copy.deepcopy(child))
    total = len(selected)
    coverage_counts = {key: 0 for key in targets}
    source_counts: dict[str, int] = {}
    sink_counts: dict[str, int] = {}
    extended_count = 0
    for index, template in enumerate(selected):
        depart = (index + 0.5) * duration_sec / max(total, 1)
        route_edges = extend_route_edges(runner, sumo_net, list(template["route_edges"]), extension_steps)
        if len(route_edges) > len(template["route_edges"]):
            extended_count += 1
        attrs = {
            "id": f"ref_dist_v2_{index:05d}",
            "depart": f"{depart:.2f}",
            "departLane": "best",
            "departPos": "random_free",
            "departSpeed": "max",
        }
        vehicle = ET.SubElement(output_root, "vehicle", attrs)
        ET.SubElement(vehicle, "route", {"edges": " ".join(route_edges)})
        source_counts[route_edges[0]] = source_counts.get(route_edges[0], 0) + 1
        sink_counts[route_edges[-1]] = sink_counts.get(route_edges[-1], 0) + 1
        for key in template["coverage_keys"]:
            if key in coverage_counts:
                coverage_counts[key] += 1
    output_route.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(output_root).write(output_route, encoding="utf-8", xml_declaration=True)
    segment_rows: list[dict[str, Any]] = []
    for key in sorted(targets, key=lambda value: (reference_segment_number(value.split(":")[0]), value.split(":")[1])):
        segment_id, direction = key.split(":", 1)
        target = targets[key]
        generated = coverage_counts[key]
        segment_rows.append(
            {
                "segment_id": segment_id,
                "direction": direction,
                "target_count": round(target, 6),
                "generated_template_count": generated,
                "generated_recall": round(generated / target, 6) if target else "",
                "remaining_count": round(remaining.get(key, 0.0), 6),
            }
        )
    recall_values = [float(row["generated_recall"]) for row in segment_rows]
    summary = {
        "schema": "validated_reference_distributed_demand_v2.v1",
        "generated_at": utc_now(),
        "reference_csv": rel(reference_csv),
        "mapping_csv": rel(mapping_csv),
        "base_route": rel(base_route),
        "net_file": rel(net_file),
        "route_file": rel(output_route),
        "duration_sec": duration_sec,
        "candidate_template_count": len(templates),
        "vehicle_count": total,
        "extended_vehicle_count": extended_count,
        "depart_lane": "best",
        "depart_pos": "random_free",
        "depart_speed": "max",
        "mean_generated_recall": round(sum(recall_values) / len(recall_values), 6),
        "min_generated_recall": min(recall_values),
        "max_generated_recall": max(recall_values),
        "source_edge_count": len(source_counts),
        "sink_edge_count": len(sink_counts),
        "top_source_edges": " ".join(f"{edge}:{count}" for edge, count in sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))[:10]),
        "top_sink_edges": " ".join(f"{edge}:{count}" for edge, count in sorted(sink_counts.items(), key=lambda item: (-item[1], item[0]))[:10]),
        "method": "greedy_existing_od_template_selection_with_insertion_attrs_and_sink_extension",
    }
    return segment_rows, summary


def phase_is_protected(state: str) -> bool:
    return "G" not in state and "g" not in state


def phase_is_green(state: str) -> bool:
    return "G" in state or "g" in state


def parse_int_list(value: str) -> list[int]:
    return [int(item) for item in str(value).split() if str(item).lstrip("-").isdigit()]


def tls_up_green_indices_from_audit(tls_audit: Path) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    if not tls_audit.is_file():
        return result
    for row in read_csv(tls_audit):
        tls_id = row.get("tls_id", "").strip()
        if not tls_id:
            continue
        result.setdefault(tls_id, set()).update(parse_int_list(row.get("green_phase_indices", "")))
    return result


def redistribute_phase_durations(
    states: list[str],
    durations: list[float],
    up_indices: set[int],
    up_delta: float,
    down_delta: float,
    min_green_sec: float = 5.0,
) -> list[int]:
    original_cycle = round(sum(durations))
    adjusted = list(durations)
    green_indices = [index for index, state in enumerate(states) if phase_is_green(state)]
    up_green = [index for index in green_indices if index in up_indices]
    down_green = [index for index in green_indices if index not in up_indices]
    min_allowed = {index: min(float(durations[index]), min_green_sec) for index in green_indices}
    for index in up_green:
        adjusted[index] = max(min_allowed[index], adjusted[index] + up_delta)
    for index in down_green:
        adjusted[index] = max(min_allowed[index], adjusted[index] + down_delta)
    diff = sum(adjusted) - original_cycle
    if abs(diff) > 1e-6:
        preferred = down_green if diff > 0 and down_green else up_green if diff < 0 and up_green else green_indices
        if diff > 0:
            remaining = diff
            for index in sorted(preferred, key=lambda item: adjusted[item], reverse=True):
                capacity = max(adjusted[index] - min_allowed.get(index, min_green_sec), 0.0)
                delta = min(capacity, remaining)
                adjusted[index] -= delta
                remaining -= delta
                if remaining <= 1e-6:
                    break
        else:
            add = -diff / max(len(preferred), 1)
            for index in preferred:
                adjusted[index] += add
    rounded = [max(1, int(round(value))) for value in adjusted]
    residual = original_cycle - sum(rounded)
    candidates = [index for index in green_indices if rounded[index] + residual >= min_allowed.get(index, min_green_sec)]
    if candidates and residual:
        rounded[candidates[0]] += residual
    return rounded


def rewrite_tllogic_for_candidate(
    input_tll: Path,
    output_tll: Path,
    tls_audit: Path,
    up_delta_sec: float,
    down_delta_sec: float,
    offset_sec: float,
) -> dict[str, Any]:
    tree = ET.parse(input_tll)
    root = tree.getroot()
    up_indices_by_tls = tls_up_green_indices_from_audit(tls_audit)
    changed = 0
    for logic in root.findall("tlLogic"):
        tls_id = logic.get("id", "")
        phases = logic.findall("phase")
        states = [phase.get("state", "") for phase in phases]
        durations = [float(phase.get("duration", "0") or 0.0) for phase in phases]
        if not phases or not any(phase_is_green(state) for state in states):
            continue
        new_durations = redistribute_phase_durations(states, durations, up_indices_by_tls.get(tls_id, set()), up_delta_sec, down_delta_sec)
        for phase, old_duration, new_duration in zip(phases, durations, new_durations, strict=False):
            if int(round(old_duration)) != int(new_duration):
                changed += 1
            phase.set("duration", str(int(new_duration)))
        logic.set("offset", str(int(offset_sec)))
    output_tll.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_tll, encoding="utf-8", xml_declaration=True)
    return {
        "schema": "validated_tls_timing_rewrite.v1",
        "input_tll": rel(input_tll),
        "output_tll": rel(output_tll),
        "tls_audit": rel(tls_audit),
        "up_delta_sec": up_delta_sec,
        "down_delta_sec": down_delta_sec,
        "offset_sec": offset_sec,
        "changed_phase_count": changed,
    }


def boundary_speed_for_level(level: str) -> float | None:
    if level == "none":
        return None
    if level == "mild":
        return 8.33
    if level == "medium":
        return 5.56
    raise ValidatedPipelineError(f"unknown_boundary_metering:{level}")


def boundary_metering_edge_ids(mapping_csv: Path, start_segment: int = 15, end_segment: int = 22) -> set[str]:
    edge_ids: set[str] = set()
    for row in read_csv(mapping_csv):
        segment_id = row.get("segment_id", "")
        direction = row.get("direction", "")
        if direction != "downbound":
            continue
        try:
            segment_number = reference_segment_number(segment_id)
        except ValidatedPipelineError:
            continue
        if start_segment <= segment_number <= end_segment and row.get("edge_id"):
            edge_ids.add(row["edge_id"])
    return edge_ids


def rewrite_plain_edge_speeds(input_edge_xml: Path, output_edge_xml: Path, edge_ids: set[str], speed_mps: float | None) -> dict[str, Any]:
    tree = ET.parse(input_edge_xml)
    root = tree.getroot()
    changed = 0
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        if speed_mps is None or edge_id not in edge_ids:
            continue
        old_speed = float(edge.get("speed", "0") or 0.0)
        if old_speed > speed_mps:
            edge.set("speed", f"{speed_mps:.2f}")
            changed += 1
    output_edge_xml.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_edge_xml, encoding="utf-8", xml_declaration=True)
    return {
        "schema": "validated_boundary_metering_rewrite.v1",
        "input_edge_xml": rel(input_edge_xml),
        "output_edge_xml": rel(output_edge_xml),
        "metered_edge_count": changed,
        "target_edge_count": len(edge_ids),
        "speed_mps": "" if speed_mps is None else speed_mps,
    }


def tls_boundary_label(up_delta: float, down_delta: float, offset: float, metering: str) -> str:
    def signed(value: float) -> str:
        prefix = "p" if value >= 0 else "m"
        return f"{prefix}{scale_label(abs(value))}"

    return f"tls_u{signed(up_delta)}_d{signed(down_delta)}_off{scale_label(offset)}_meter_{metering}"


def build_tls_boundary_candidate_net(
    base_net: Path,
    output_net: Path,
    work_dir: Path,
    tls_audit: Path,
    mapping_csv: Path,
    up_delta_sec: float,
    down_delta_sec: float,
    offset_sec: float,
    boundary_metering: str,
) -> dict[str, Any]:
    plain_dir = work_dir / "plain"
    plain_prefix = plain_dir / "candidate_plain"
    plain_dir.mkdir(parents=True, exist_ok=True)
    export_command = [netconvert_path(), "--sumo-net-file", str(base_net), "--plain-output-prefix", str(plain_prefix)]
    export_completed = run_command(export_command)
    if export_completed.returncode != 0:
        raise ValidatedPipelineError(f"candidate_plain_export_failed:{export_completed.stderr[-2000:]}")
    node_file = plain_prefix.with_suffix(".nod.xml")
    edge_file = plain_prefix.with_suffix(".edg.xml")
    con_file = plain_prefix.with_suffix(".con.xml")
    tll_file = plain_prefix.with_suffix(".tll.xml")
    candidate_tll = plain_dir / "candidate_calibrated.tll.xml"
    candidate_edge = plain_dir / "candidate_metered.edg.xml"
    tls_summary = rewrite_tllogic_for_candidate(tll_file, candidate_tll, tls_audit, up_delta_sec, down_delta_sec, offset_sec)
    metering_speed = boundary_speed_for_level(boundary_metering)
    metering_edges = boundary_metering_edge_ids(mapping_csv)
    edge_summary = rewrite_plain_edge_speeds(edge_file, candidate_edge, metering_edges, metering_speed)
    output_net.parent.mkdir(parents=True, exist_ok=True)
    rebuild_command = [
        netconvert_path(),
        "--node-files",
        str(node_file),
        "--edge-files",
        str(candidate_edge),
        "--connection-files",
        str(con_file),
        "--tllogic-files",
        str(candidate_tll),
        "--output-file",
        str(output_net),
        "--no-turnarounds",
        "true",
    ]
    rebuild_completed = run_command(rebuild_command)
    if rebuild_completed.returncode != 0:
        raise ValidatedPipelineError(f"candidate_net_rebuild_failed:{rebuild_completed.stderr[-2000:]}")
    return {
        "schema": "validated_tls_boundary_candidate_net.v1",
        "generated_at": utc_now(),
        "base_net": rel(base_net),
        "output_net": rel(output_net),
        "work_dir": rel(work_dir),
        "up_delta_sec": up_delta_sec,
        "down_delta_sec": down_delta_sec,
        "offset_sec": offset_sec,
        "boundary_metering": boundary_metering,
        "tls_summary": tls_summary,
        "edge_summary": edge_summary,
        "export_stderr": export_completed.stderr[-2000:],
        "rebuild_stderr": rebuild_completed.stderr[-2000:],
    }


def validated_manifest_payload(net_file: Path, demand_file: Path, warmup_scale: float, sustain_scale: float, notes: str = "") -> dict[str, Any]:
    base = read_json(FINAL_MANIFEST)
    payload = copy.deepcopy(base)
    payload["schema"] = "validated_experiment_manifest.v1"
    payload["active_net"] = rel(net_file)
    payload["background_route"] = rel(demand_file)
    design = dict(payload.get("background_demand_design", {}))
    demand_name = Path(demand_file).name
    if REFERENCE_DISTRIBUTED_V2_LABEL in demand_name:
        design.update(
            {
                "source_pattern": rel(DEFAULT_BASE_DEMAND),
                "method": "validated_reference_distributed_od_demand_v2",
                "reference_csv": rel(DEFAULT_REFERENCE_CSV),
                "warmup_scale": warmup_scale,
                "sustain_scale": sustain_scale,
                "sampling_seed": "greedy_existing_od_template_selection_v2",
            }
        )
    elif REFERENCE_DISTRIBUTED_LABEL in demand_name:
        design.update(
            {
                "source_pattern": rel(DEFAULT_BASE_DEMAND),
                "method": "validated_reference_distributed_od_demand",
                "reference_csv": rel(DEFAULT_REFERENCE_CSV),
                "warmup_scale": warmup_scale,
                "sustain_scale": sustain_scale,
                "sampling_seed": "greedy_existing_od_template_selection",
            }
        )
    elif REFERENCE_SCREENLINE_LABEL in demand_name:
        design.update(
            {
                "source_pattern": rel(DEFAULT_REFERENCE_CSV),
                "method": "validated_reference_screenline_demand_bidirectional",
                "warmup_scale": warmup_scale,
                "sustain_scale": sustain_scale,
                "sampling_seed": "",
            }
        )
    else:
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
