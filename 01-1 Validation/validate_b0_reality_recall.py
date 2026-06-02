#!/usr/bin/env python3
"""Validate B0 SUMO reality recall against Toegye-ro segment reference data."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import edge_shape_with_lane_fallback, read_sumo_net  # noqa: E402


DEFAULT_REFERENCE_CSV = PROJECT_ROOT / "toegye_ro_mainstream_segments_english.csv"
DEFAULT_MANIFEST = PROJECT_ROOT / "configs/final_experiment_manifest.json"
DEFAULT_RESULTS_ROOT = PROJECT_ROOT / "results/metrics"
OUTPUT_ROOT = PROJECT_ROOT / "results/metrics/validation_b0"

REQUIRED_REFERENCE_COLUMNS = [
    "segment_id",
    "start_intersection",
    "end_intersection",
    "segment_length_m",
    "upbound_lanes_to_seoul_station",
    "downbound_lanes_to_seongdong_high_school",
    "speed_limit_kmh",
    "avg_speed_kmh_upbound",
    "avg_speed_kmh_downbound",
    "travel_time_s_upbound",
    "travel_time_s_downbound",
    "peak_hour_volume_veh_per_h_reference",
    "start_latitude",
    "start_longitude",
    "end_latitude",
    "end_longitude",
]

MAP_SAMPLE_STEP_M = 10.0
MAP_COVER_DISTANCE_M = 35.0
DIRECTION_MATCH_DISTANCE_M = 45.0
DIRECTION_HEADING_TOLERANCE_DEG = 45.0
MAP_PASS_CORRIDOR_RECALL = 0.95
MAP_PASS_MIN_SEGMENT_RECALL = 0.80
DEMAND_PASS_RECALL_MIN = 0.70
DEMAND_PASS_RECALL_MAX = 1.30
DEMAND_WARN_RECALL_MIN = 0.50
DEMAND_WARN_RECALL_MAX = 1.50
DEMAND_GEH_OK_RATIO_MIN = 0.80
SPEED_PASS_MAE_KMH = 5.0
SPEED_WARN_MAE_KMH = 8.0
LANE_RECALL_PASS_RATIO = 0.90


class ValidationError(RuntimeError):
    """Expected validation setup or input failure."""


@dataclass(frozen=True)
class Segment:
    segment_id: str
    start_intersection: str
    end_intersection: str
    length_m: float
    upbound_lanes: float
    downbound_lanes: float
    speed_limit_kmh: float
    avg_speed_kmh_upbound: float
    avg_speed_kmh_downbound: float
    travel_time_s_upbound: float
    travel_time_s_downbound: float
    reference_vph: float
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float


@dataclass(frozen=True)
class EdgeFeature:
    edge_id: str
    points: list[tuple[float, float]]
    length_m: float
    lane_count: int
    speed_mps: float
    heading_deg: float


@dataclass(frozen=True)
class NetProjection:
    net_offset_x: float
    net_offset_y: float
    utm_zone: int
    northern: bool


class CoordinateConverter:
    def __init__(self, sumo_net: Any, projection: NetProjection | None) -> None:
        self.sumo_net = sumo_net
        self.projection = projection

    def convertLonLat2XY(self, lon: float, lat: float) -> tuple[float, float]:
        try:
            x, y = self.sumo_net.convertLonLat2XY(lon, lat)
            return float(x), float(y)
        except RuntimeError as exc:
            if self.projection is None:
                raise exc
            return utm_lonlat_to_sumo_xy(lon, lat, self.projection)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValidationError(f"JSON root must be object: {rel(path)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def parse_float(row: dict[str, str], key: str) -> float:
    value = (row.get(key) or "").strip()
    if value == "":
        raise ValidationError(f"missing numeric value for {key}")
    return float(value)


def load_reference_segments(path: Path) -> list[Segment]:
    if not path.is_file():
        raise ValidationError(f"reference CSV missing: {rel(path)}")
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames or []
        missing = [column for column in REQUIRED_REFERENCE_COLUMNS if column not in fieldnames]
        if missing:
            raise ValidationError(f"reference CSV missing columns: {', '.join(missing)}")
        segments = [
            Segment(
                segment_id=row["segment_id"],
                start_intersection=row["start_intersection"],
                end_intersection=row["end_intersection"],
                length_m=parse_float(row, "segment_length_m"),
                upbound_lanes=parse_float(row, "upbound_lanes_to_seoul_station"),
                downbound_lanes=parse_float(row, "downbound_lanes_to_seongdong_high_school"),
                speed_limit_kmh=parse_float(row, "speed_limit_kmh"),
                avg_speed_kmh_upbound=parse_float(row, "avg_speed_kmh_upbound"),
                avg_speed_kmh_downbound=parse_float(row, "avg_speed_kmh_downbound"),
                travel_time_s_upbound=parse_float(row, "travel_time_s_upbound"),
                travel_time_s_downbound=parse_float(row, "travel_time_s_downbound"),
                reference_vph=parse_float(row, "peak_hour_volume_veh_per_h_reference"),
                start_lat=parse_float(row, "start_latitude"),
                start_lon=parse_float(row, "start_longitude"),
                end_lat=parse_float(row, "end_latitude"),
                end_lon=parse_float(row, "end_longitude"),
            )
            for row in reader
        ]
    if not segments:
        raise ValidationError(f"reference CSV has no rows: {rel(path)}")
    return segments


def heading_deg(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    start = points[0]
    end = points[-1]
    return math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))


def heading_diff_deg(a: float, b: float) -> float:
    return abs((b - a + 180.0) % 360.0 - 180.0)


def parse_net_projection(net_file: Path) -> NetProjection | None:
    root = ET.parse(net_file).getroot()
    location = root.find("location")
    if location is None:
        return None
    proj = location.get("projParameter") or ""
    if "+proj=utm" not in proj:
        return None
    zone = None
    for token in proj.split():
        if token.startswith("+zone="):
            zone = int(token.split("=", 1)[1])
            break
    if zone is None:
        return None
    offset = location.get("netOffset") or "0,0"
    offset_x, offset_y = [float(value) for value in offset.split(",", 1)]
    return NetProjection(
        net_offset_x=offset_x,
        net_offset_y=offset_y,
        utm_zone=zone,
        northern="+south" not in proj,
    )


def utm_lonlat_to_sumo_xy(lon: float, lat: float, projection: NetProjection) -> tuple[float, float]:
    # WGS84 UTM forward projection fallback for venvs without pyproj.
    semi_major_axis = 6378137.0
    flattening = 1 / 298.257223563
    eccentricity_sq = flattening * (2 - flattening)
    second_eccentricity_sq = eccentricity_sq / (1 - eccentricity_sq)
    scale = 0.9996

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon_origin = math.radians((projection.utm_zone - 1) * 6 - 180 + 3)

    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    tan_lat = math.tan(lat_rad)
    n_value = semi_major_axis / math.sqrt(1 - eccentricity_sq * sin_lat * sin_lat)
    t_value = tan_lat * tan_lat
    c_value = second_eccentricity_sq * cos_lat * cos_lat
    a_value = cos_lat * (lon_rad - lon_origin)
    meridional_arc = semi_major_axis * (
        (1 - eccentricity_sq / 4 - 3 * eccentricity_sq**2 / 64 - 5 * eccentricity_sq**3 / 256) * lat_rad
        - (3 * eccentricity_sq / 8 + 3 * eccentricity_sq**2 / 32 + 45 * eccentricity_sq**3 / 1024)
        * math.sin(2 * lat_rad)
        + (15 * eccentricity_sq**2 / 256 + 45 * eccentricity_sq**3 / 1024) * math.sin(4 * lat_rad)
        - (35 * eccentricity_sq**3 / 3072) * math.sin(6 * lat_rad)
    )
    easting = scale * n_value * (
        a_value
        + (1 - t_value + c_value) * a_value**3 / 6
        + (5 - 18 * t_value + t_value**2 + 72 * c_value - 58 * second_eccentricity_sq) * a_value**5 / 120
    ) + 500000.0
    northing = scale * (
        meridional_arc
        + n_value
        * tan_lat
        * (
            a_value**2 / 2
            + (5 - t_value + 9 * c_value + 4 * c_value**2) * a_value**4 / 24
            + (61 - 58 * t_value + t_value**2 + 600 * c_value - 330 * second_eccentricity_sq) * a_value**6 / 720
        )
    )
    if not projection.northern:
        northing += 10000000.0
    return easting + projection.net_offset_x, northing + projection.net_offset_y


def point_segment_distance_m(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    ax, ay = a
    bx, by = b
    px, py = point
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    closest = (ax + t * dx, ay + t * dy)
    return math.hypot(px - closest[0], py - closest[1])


def point_polyline_distance_m(point: tuple[float, float], points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return float("inf")
    return min(point_segment_distance_m(point, a, b) for a, b in zip(points, points[1:], strict=False))


def polyline_min_distance_m(a_points: list[tuple[float, float]], b_points: list[tuple[float, float]]) -> float:
    distances: list[float] = []
    for point in a_points:
        distances.append(point_polyline_distance_m(point, b_points))
    for point in b_points:
        distances.append(point_polyline_distance_m(point, a_points))
    return min(distances) if distances else float("inf")


def sample_line_points(
    start: tuple[float, float],
    end: tuple[float, float],
    step_m: float = MAP_SAMPLE_STEP_M,
) -> list[tuple[float, float]]:
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    sample_count = max(2, int(math.ceil(length / step_m)) + 1)
    return [
        (
            start[0] + (end[0] - start[0]) * index / (sample_count - 1),
            start[1] + (end[1] - start[1]) * index / (sample_count - 1),
        )
        for index in range(sample_count)
    ]


def load_edge_features(net_file: Path) -> tuple[Any, list[EdgeFeature]]:
    if not net_file.is_file():
        raise ValidationError(f"active net missing: {rel(net_file)}")
    sumo_net = read_sumo_net(net_file)
    coordinate_converter = CoordinateConverter(sumo_net, parse_net_projection(net_file))
    features: list[EdgeFeature] = []
    for edge in sumo_net.getEdges():
        edge_id = edge.getID()
        if edge_id.startswith(":") or edge.isSpecial():
            continue
        try:
            if not edge.allows("passenger"):
                continue
        except Exception:  # noqa: BLE001 - sumolib permission APIs can vary by net source.
            continue
        shape = edge_shape_with_lane_fallback(edge)
        if len(shape) < 2:
            continue
        points = [(float(x), float(y)) for x, y in shape]
        features.append(
            EdgeFeature(
                edge_id=edge_id,
                points=points,
                length_m=float(edge.getLength()),
                lane_count=int(edge.getLaneNumber()),
                speed_mps=float(edge.getSpeed()),
                heading_deg=heading_deg(points),
            )
        )
    if not features:
        raise ValidationError(f"active net has no passenger edge features: {rel(net_file)}")
    return coordinate_converter, features


def segment_points(sumo_net: Any, segment: Segment, direction: str) -> tuple[tuple[float, float], tuple[float, float]]:
    start_xy = sumo_net.convertLonLat2XY(segment.start_lon, segment.start_lat)
    end_xy = sumo_net.convertLonLat2XY(segment.end_lon, segment.end_lat)
    if direction == "upbound":
        return (float(start_xy[0]), float(start_xy[1])), (float(end_xy[0]), float(end_xy[1]))
    if direction == "downbound":
        return (float(end_xy[0]), float(end_xy[1])), (float(start_xy[0]), float(start_xy[1]))
    raise ValidationError(f"unknown segment direction: {direction}")


def direction_targets(segment: Segment, direction: str) -> dict[str, float]:
    if direction == "upbound":
        return {
            "reference_lanes": segment.upbound_lanes,
            "reference_speed_kmh": segment.avg_speed_kmh_upbound,
            "reference_travel_time_s": segment.travel_time_s_upbound,
        }
    if direction == "downbound":
        return {
            "reference_lanes": segment.downbound_lanes,
            "reference_speed_kmh": segment.avg_speed_kmh_downbound,
            "reference_travel_time_s": segment.travel_time_s_downbound,
        }
    raise ValidationError(f"unknown segment direction: {direction}")


def map_coverage_for_segment(
    sumo_net: Any,
    segment: Segment,
    edges: list[EdgeFeature],
) -> tuple[float, list[str]]:
    start, end = segment_points(sumo_net, segment, "upbound")
    samples = sample_line_points(start, end)
    covered_count = 0
    covering_edges: set[str] = set()
    for sample in samples:
        best_edge_id = ""
        best_distance = float("inf")
        for edge in edges:
            distance = point_polyline_distance_m(sample, edge.points)
            if distance < best_distance:
                best_distance = distance
                best_edge_id = edge.edge_id
        if best_distance <= MAP_COVER_DISTANCE_M:
            covered_count += 1
            covering_edges.add(best_edge_id)
    return covered_count / len(samples), sorted(covering_edges)


def matched_edges_for_direction(
    sumo_net: Any,
    segment: Segment,
    direction: str,
    edges: list[EdgeFeature],
) -> list[EdgeFeature]:
    start, end = segment_points(sumo_net, segment, direction)
    segment_line = [start, end]
    samples = sample_line_points(start, end)
    segment_heading = heading_deg(segment_line)
    scored: list[tuple[int, float, float, EdgeFeature]] = []
    for edge in edges:
        diff = heading_diff_deg(segment_heading, edge.heading_deg)
        if diff > DIRECTION_HEADING_TOLERANCE_DEG:
            continue
        sample_hits = sum(1 for sample in samples if point_polyline_distance_m(sample, edge.points) <= MAP_COVER_DISTANCE_M)
        if sample_hits == 0:
            continue
        distance = polyline_min_distance_m(segment_line, edge.points)
        if distance > DIRECTION_MATCH_DISTANCE_M:
            continue
        scored.append((-sample_hits, distance, diff, edge))
    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3].edge_id))
    return [item[3] for item in scored]


def representative_edge_id(
    sumo_net: Any,
    segment: Segment,
    direction: str,
    matched_edges: list[EdgeFeature],
) -> str:
    if not matched_edges:
        return ""
    start, end = segment_points(sumo_net, segment, direction)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    return min(
        matched_edges,
        key=lambda edge: (point_polyline_distance_m(midpoint, edge.points), edge.edge_id),
    ).edge_id


def representative_demand_edge_id(
    sumo_net: Any,
    segment: Segment,
    direction: str,
    matched_edges: list[EdgeFeature],
    edge_data: dict[str, dict[str, float]],
) -> str:
    if not matched_edges:
        return ""
    start, end = segment_points(sumo_net, segment, direction)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    return min(
        matched_edges,
        key=lambda edge: (
            -float(edge_data.get(edge.edge_id, {}).get("screenline_count") or 0.0),
            point_polyline_distance_m(midpoint, edge.points),
            edge.edge_id,
        ),
    ).edge_id


def parse_edge_data(path: Path) -> tuple[float, float, dict[str, dict[str, float]]]:
    if not path.is_file():
        raise ValidationError(f"B0 edgeData missing: {rel(path)}")
    root = ET.parse(path).getroot()
    intervals = root.findall(".//interval")
    if not intervals:
        raise ValidationError(f"B0 edgeData has no interval: {rel(path)}")
    begin = min(float(interval.get("begin") or 0.0) for interval in intervals)
    end = max(float(interval.get("end") or 0.0) for interval in intervals)
    edge_data: dict[str, dict[str, float]] = {}
    for edge in root.findall(".//edge"):
        edge_id = edge.get("id")
        if not edge_id:
            continue
        current = edge_data.setdefault(
            edge_id,
            {
                "sampled_seconds": 0.0,
                "speed_weighted_sum_mps": 0.0,
                "entered": 0.0,
                "left": 0.0,
                "departed": 0.0,
                "arrived": 0.0,
            },
        )
        sampled_seconds = float(edge.get("sampledSeconds") or 0.0)
        speed = float(edge.get("speed") or 0.0)
        current["sampled_seconds"] += sampled_seconds
        if sampled_seconds > 0.0 and speed > 0.0:
            current["speed_weighted_sum_mps"] += sampled_seconds * speed
        for key in ["entered", "left", "departed", "arrived"]:
            current[key] += float(edge.get(key) or 0.0)
    for values in edge_data.values():
        sampled_seconds = values["sampled_seconds"]
        values["speed_mps"] = values["speed_weighted_sum_mps"] / sampled_seconds if sampled_seconds > 0.0 else 0.0
        values["screenline_count"] = max(values["entered"], values["left"], values["departed"], values["arrived"])
    return begin, end, edge_data


def overlap_seconds(begin: float, end: float, window_begin: float, window_end: float) -> float:
    return max(0.0, min(end, window_end) - max(begin, window_begin))


def scaled_reference_seconds(begin: float, end: float, warmup_sec: float, warmup_scale: float, sustain_scale: float) -> float:
    warmup_overlap = overlap_seconds(begin, end, 0.0, warmup_sec)
    sustain_overlap = max(0.0, end - max(begin, warmup_sec))
    return warmup_overlap * warmup_scale + sustain_overlap * sustain_scale


def geh_statistic(observed: float, expected: float) -> float:
    if observed < 0.0 or expected < 0.0:
        raise ValidationError("GEH inputs must be non-negative")
    denom = observed + expected
    if denom == 0.0:
        return 0.0
    return math.sqrt(2.0 * (observed - expected) ** 2 / denom)


def geh_status(geh: float) -> str:
    if geh < 5.0:
        return "PASS"
    if geh < 10.0:
        return "WARN"
    return "FAIL"


def status_rank(status: str) -> int:
    return {"PASS": 0, "WARN": 1, "FAIL": 2}.get(status, 2)


def combine_statuses(statuses: list[str]) -> str:
    if not statuses:
        return "FAIL"
    return max(statuses, key=status_rank)


def candidate_results_csvs(results_root: Path = DEFAULT_RESULTS_ROOT) -> list[Path]:
    candidates: list[Path] = []
    latest = results_root / "parameter_input_sim/latest.json"
    if latest.is_file():
        try:
            payload = read_json(latest)
            results_csv = payload.get("results_csv")
            if isinstance(results_csv, str):
                candidates.append(resolve_project_path(results_csv))
        except (OSError, json.JSONDecodeError, ValidationError):
            pass
    candidates.extend(sorted(results_root.glob("*/*/experiment_results.csv"), key=lambda path: path.parent.name, reverse=True))
    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(candidate)
    return unique


def load_results_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def is_nonfailed_status(status: str) -> bool:
    clean = status.strip().upper()
    return bool(clean) and not clean.startswith("FAIL")


def select_b0_row_from_results(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    rows = load_results_rows(path)
    for row in rows:
        if row.get("mode") != "B0":
            continue
        if row.get("parameter_id") != "no_control":
            continue
        if not is_nonfailed_status(row.get("final_status", "")):
            continue
        edge_data_path = resolve_project_path(row.get("edgeData_output", ""))
        if not edge_data_path.is_file():
            continue
        selected = dict(row)
        selected["_results_csv"] = str(path)
        return selected
    return None


def select_b0_row(results_csv_arg: str) -> dict[str, str]:
    if results_csv_arg != "auto":
        path = resolve_project_path(results_csv_arg)
        selected = select_b0_row_from_results(path)
        if selected is None:
            raise ValidationError(f"no usable B0/no_control row found in {rel(path)}")
        return selected
    for candidate in candidate_results_csvs():
        selected = select_b0_row_from_results(candidate)
        if selected is not None:
            return selected
    raise ValidationError("no usable B0/no_control row found under results/metrics")


def weighted_edge_speed_kmh(edge_ids: list[str], edge_lookup: dict[str, EdgeFeature], edge_data: dict[str, dict[str, float]]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for edge_id in edge_ids:
        values = edge_data.get(edge_id, {})
        speed_mps = float(values.get("speed_mps") or 0.0)
        sampled_seconds = float(values.get("sampled_seconds") or 0.0)
        weight = sampled_seconds if sampled_seconds > 0.0 else edge_lookup[edge_id].length_m
        if speed_mps <= 0.0 or weight <= 0.0:
            continue
        numerator += speed_mps * weight
        denominator += weight
    if denominator <= 0.0:
        return None
    return numerator / denominator * 3.6


def build_map_rows(sumo_net: Any, segments: list[Segment], edges: list[EdgeFeature]) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    weighted_recall_sum = 0.0
    length_sum = 0.0
    min_segment_recall = 1.0
    for segment in segments:
        recall, covering_edges = map_coverage_for_segment(sumo_net, segment, edges)
        length_sum += segment.length_m
        weighted_recall_sum += segment.length_m * recall
        min_segment_recall = min(min_segment_recall, recall)
        status = "PASS" if recall >= MAP_PASS_MIN_SEGMENT_RECALL else "FAIL"
        rows.append(
            {
                "segment_id": segment.segment_id,
                "start_intersection": segment.start_intersection,
                "end_intersection": segment.end_intersection,
                "segment_length_m": round(segment.length_m, 3),
                "map_recall": round(recall, 6),
                "covered_edge_count": len(covering_edges),
                "covered_edge_ids": " ".join(covering_edges),
                "status": status,
            }
        )
    corridor_recall = weighted_recall_sum / length_sum if length_sum else 0.0
    status = (
        "PASS"
        if corridor_recall >= MAP_PASS_CORRIDOR_RECALL and min_segment_recall >= MAP_PASS_MIN_SEGMENT_RECALL
        else "FAIL"
    )
    summary = {
        "corridor_map_recall": round(corridor_recall, 6),
        "min_segment_map_recall": round(min_segment_recall, 6),
        "segment_count": len(segments),
    }
    return rows, status, summary


def build_demand_rows(
    sumo_net: Any,
    segments: list[Segment],
    edges: list[EdgeFeature],
    edge_data: dict[str, dict[str, float]],
    begin: float,
    end: float,
    warmup_sec: float,
    warmup_scale: float,
    sustain_scale: float,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scaled_seconds = scaled_reference_seconds(begin, end, warmup_sec, warmup_scale, sustain_scale)
    raw_seconds = max(0.0, end - begin)
    recall_values: list[float] = []
    geh_ok_count = 0
    valid_geh_count = 0
    for segment in segments:
        for direction in ["upbound", "downbound"]:
            matched = matched_edges_for_direction(sumo_net, segment, direction, edges)
            representative = representative_demand_edge_id(sumo_net, segment, direction, matched, edge_data)
            target_scaled = segment.reference_vph * scaled_seconds / 3600.0
            target_raw = segment.reference_vph * raw_seconds / 3600.0
            observed = float(edge_data.get(representative, {}).get("screenline_count") or 0.0) if representative else 0.0
            scaled_recall = observed / target_scaled if target_scaled > 0.0 else None
            raw_recall = observed / target_raw if target_raw > 0.0 else None
            geh = geh_statistic(observed, target_scaled)
            current_geh_status = geh_status(geh)
            valid_geh_count += 1
            if current_geh_status in {"PASS", "WARN"}:
                geh_ok_count += 1
            if scaled_recall is not None:
                recall_values.append(scaled_recall)
            if scaled_recall is None:
                row_status = "FAIL"
            elif DEMAND_PASS_RECALL_MIN <= scaled_recall <= DEMAND_PASS_RECALL_MAX and current_geh_status != "FAIL":
                row_status = "PASS"
            elif DEMAND_WARN_RECALL_MIN <= scaled_recall <= DEMAND_WARN_RECALL_MAX and current_geh_status != "FAIL":
                row_status = "WARN"
            else:
                row_status = "FAIL"
            rows.append(
                {
                    "segment_id": segment.segment_id,
                    "direction": direction,
                    "representative_edge_id": representative,
                    "matched_edge_count": len(matched),
                    "matched_edge_ids": " ".join(edge.edge_id for edge in matched),
                    "reference_volume_veh_per_h_directional": round(segment.reference_vph, 6),
                    "interval_begin_sec": round(begin, 6),
                    "interval_end_sec": round(end, 6),
                    "raw_reference_count": round(target_raw, 6),
                    "scaled_reference_count": round(target_scaled, 6),
                    "observed_count": round(observed, 6),
                    "raw_recall": round(raw_recall, 6) if raw_recall is not None else "",
                    "scaled_recall": round(scaled_recall, 6) if scaled_recall is not None else "",
                    "geh": round(geh, 6),
                    "geh_status": current_geh_status,
                    "status": row_status,
                }
            )
    median_recall = statistics.median(recall_values) if recall_values else None
    geh_ok_ratio = geh_ok_count / valid_geh_count if valid_geh_count else 0.0
    if median_recall is None:
        status = "FAIL"
    elif DEMAND_PASS_RECALL_MIN <= median_recall <= DEMAND_PASS_RECALL_MAX and geh_ok_ratio >= DEMAND_GEH_OK_RATIO_MIN:
        status = "PASS"
    elif DEMAND_WARN_RECALL_MIN <= median_recall <= DEMAND_WARN_RECALL_MAX and geh_ok_ratio >= DEMAND_GEH_OK_RATIO_MIN:
        status = "WARN"
    else:
        status = "FAIL"
    summary = {
        "scaled_reference_seconds": round(scaled_seconds, 6),
        "raw_reference_seconds": round(raw_seconds, 6),
        "median_scaled_recall": round(median_recall, 6) if median_recall is not None else None,
        "geh_pass_warn_ratio": round(geh_ok_ratio, 6),
        "row_count": len(rows),
    }
    return rows, status, summary


def build_speed_rows(
    sumo_net: Any,
    segments: list[Segment],
    edges: list[EdgeFeature],
    edge_data: dict[str, dict[str, float]],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    edge_lookup = {edge.edge_id: edge for edge in edges}
    rows: list[dict[str, Any]] = []
    speed_errors: list[float] = []
    for segment in segments:
        for direction in ["upbound", "downbound"]:
            targets = direction_targets(segment, direction)
            matched = matched_edges_for_direction(sumo_net, segment, direction, edges)
            edge_ids = [edge.edge_id for edge in matched if edge.edge_id in edge_lookup]
            simulated_speed = weighted_edge_speed_kmh(edge_ids, edge_lookup, edge_data) if edge_ids else None
            reference_speed = targets["reference_speed_kmh"]
            reference_travel_time = targets["reference_travel_time_s"]
            if simulated_speed is None or simulated_speed <= 0.0:
                speed_error = None
                simulated_travel_time = None
                row_status = "FAIL"
            else:
                speed_error = simulated_speed - reference_speed
                simulated_travel_time = segment.length_m / (simulated_speed / 3.6)
                speed_errors.append(abs(speed_error))
                row_status = "PASS" if abs(speed_error) <= SPEED_PASS_MAE_KMH else "WARN" if abs(speed_error) <= SPEED_WARN_MAE_KMH else "FAIL"
            rows.append(
                {
                    "segment_id": segment.segment_id,
                    "direction": direction,
                    "matched_edge_count": len(edge_ids),
                    "matched_edge_ids": " ".join(edge_ids),
                    "reference_speed_kmh": round(reference_speed, 6),
                    "simulated_speed_kmh": round(simulated_speed, 6) if simulated_speed is not None else "",
                    "speed_error_kmh": round(speed_error, 6) if speed_error is not None else "",
                    "abs_speed_error_kmh": round(abs(speed_error), 6) if speed_error is not None else "",
                    "reference_travel_time_s": round(reference_travel_time, 6),
                    "simulated_travel_time_s": round(simulated_travel_time, 6) if simulated_travel_time is not None else "",
                    "travel_time_error_s": round((simulated_travel_time or 0.0) - reference_travel_time, 6)
                    if simulated_travel_time is not None
                    else "",
                    "status": row_status,
                }
            )
    speed_mae = sum(speed_errors) / len(speed_errors) if speed_errors else None
    if speed_mae is None:
        status = "FAIL"
    elif speed_mae <= SPEED_PASS_MAE_KMH:
        status = "PASS"
    elif speed_mae <= SPEED_WARN_MAE_KMH:
        status = "WARN"
    else:
        status = "FAIL"
    summary = {
        "speed_mae_kmh": round(speed_mae, 6) if speed_mae is not None else None,
        "valid_speed_row_count": len(speed_errors),
        "row_count": len(rows),
    }
    return rows, status, summary


def lane_match_status(reference_lanes: float, lane_values: list[int]) -> tuple[str, int | str, float | str, int | str]:
    if not lane_values:
        return "FAIL", "", "", ""
    counts: dict[int, int] = {}
    for lane in lane_values:
        counts[lane] = counts.get(lane, 0) + 1
    mode_lane = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    median_lane = statistics.median(lane_values)
    max_lane = max(lane_values)
    representative_matches = mode_lane == reference_lanes or float(median_lane) == reference_lanes
    status = "PASS" if representative_matches else "WARN" if max_lane == reference_lanes else "FAIL"
    return status, mode_lane, median_lane, max_lane


def build_lane_rows(
    sumo_net: Any,
    segments: list[Segment],
    edges: list[EdgeFeature],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pass_count = 0
    pass_warn_count = 0
    for segment in segments:
        for direction in ["upbound", "downbound"]:
            targets = direction_targets(segment, direction)
            reference_lanes = targets["reference_lanes"]
            matched = matched_edges_for_direction(sumo_net, segment, direction, edges)
            lane_values = [edge.lane_count for edge in matched]
            row_status, mode_lane, median_lane, max_lane = lane_match_status(reference_lanes, lane_values)
            if row_status == "PASS":
                pass_count += 1
            if row_status in {"PASS", "WARN"}:
                pass_warn_count += 1
            rows.append(
                {
                    "segment_id": segment.segment_id,
                    "direction": direction,
                    "reference_lanes": round(reference_lanes, 6),
                    "matched_edge_count": len(matched),
                    "matched_edge_ids": " ".join(edge.edge_id for edge in matched),
                    "matched_lane_counts": " ".join(str(edge.lane_count) for edge in matched),
                    "mode_lane_count": mode_lane,
                    "median_lane_count": median_lane,
                    "max_lane_count": max_lane,
                    "status": row_status,
                }
            )
    strict_recall = pass_count / len(rows) if rows else 0.0
    recall = pass_warn_count / len(rows) if rows else 0.0
    status = "PASS" if recall >= LANE_RECALL_PASS_RATIO else "FAIL"
    summary = {
        "lane_recall": round(recall, 6),
        "strict_lane_recall": round(strict_recall, 6),
        "pass_lane_row_count": pass_count,
        "pass_warn_lane_row_count": pass_warn_count,
        "row_count": len(rows),
        "required_lane_recall": LANE_RECALL_PASS_RATIO,
    }
    return rows, status, summary


def edge_speed_row_status(speed_error: float | None) -> tuple[str, str]:
    if speed_error is None:
        return "WARN", "no_observed_speed"
    if speed_error > SPEED_WARN_MAE_KMH:
        return "FAIL", "over_open_speed"
    if speed_error < -SPEED_WARN_MAE_KMH:
        return "FAIL", "under_speed"
    if abs(speed_error) > SPEED_PASS_MAE_KMH:
        return "WARN", "speed_error_warn_range"
    return "PASS", "within_pass_range"


def build_edge_speed_rows(
    sumo_net: Any,
    segments: list[Segment],
    edges: list[EdgeFeature],
    edge_data: dict[str, dict[str, float]],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    speed_errors: list[float] = []
    over_open_count = 0
    failed_count = 0
    warn_count = 0
    for segment in segments:
        for direction in ["upbound", "downbound"]:
            targets = direction_targets(segment, direction)
            reference_speed = targets["reference_speed_kmh"]
            matched = matched_edges_for_direction(sumo_net, segment, direction, edges)
            for edge in matched:
                values = edge_data.get(edge.edge_id, {})
                sampled_seconds = float(values.get("sampled_seconds") or 0.0)
                screenline_count = float(values.get("screenline_count") or 0.0)
                speed_mps = float(values.get("speed_mps") or 0.0)
                simulated_speed = speed_mps * 3.6 if speed_mps > 0.0 else None
                speed_error = simulated_speed - reference_speed if simulated_speed is not None else None
                if speed_error is not None:
                    speed_errors.append(abs(speed_error))
                row_status, anomaly_type = edge_speed_row_status(speed_error)
                if anomaly_type == "over_open_speed":
                    over_open_count += 1
                if row_status == "FAIL":
                    failed_count += 1
                elif row_status == "WARN":
                    warn_count += 1
                rows.append(
                    {
                        "segment_id": segment.segment_id,
                        "direction": direction,
                        "edge_id": edge.edge_id,
                        "edge_length_m": round(edge.length_m, 6),
                        "lane_count": edge.lane_count,
                        "net_speed_limit_kmh": round(edge.speed_mps * 3.6, 6),
                        "reference_segment_speed_kmh": round(reference_speed, 6),
                        "simulated_edge_speed_kmh": round(simulated_speed, 6) if simulated_speed is not None else "",
                        "speed_error_kmh": round(speed_error, 6) if speed_error is not None else "",
                        "abs_speed_error_kmh": round(abs(speed_error), 6) if speed_error is not None else "",
                        "sampled_seconds": round(sampled_seconds, 6),
                        "screenline_count": round(screenline_count, 6),
                        "entered": round(float(values.get("entered") or 0.0), 6),
                        "left": round(float(values.get("left") or 0.0), 6),
                        "departed": round(float(values.get("departed") or 0.0), 6),
                        "arrived": round(float(values.get("arrived") or 0.0), 6),
                        "anomaly_type": anomaly_type,
                        "status": row_status,
                    }
                )
    edge_speed_mae = sum(speed_errors) / len(speed_errors) if speed_errors else None
    if failed_count > 0 or (edge_speed_mae is not None and edge_speed_mae > SPEED_WARN_MAE_KMH):
        status = "FAIL"
    elif warn_count > 0 or (edge_speed_mae is not None and edge_speed_mae > SPEED_PASS_MAE_KMH):
        status = "WARN"
    elif edge_speed_mae is None:
        status = "FAIL"
    else:
        status = "PASS"
    summary = {
        "edge_speed_mae_kmh": round(edge_speed_mae, 6) if edge_speed_mae is not None else None,
        "edge_speed_row_count": len(rows),
        "valid_edge_speed_row_count": len(speed_errors),
        "failed_edge_speed_row_count": failed_count,
        "warn_edge_speed_row_count": warn_count,
        "over_open_edge_count": over_open_count,
    }
    return rows, status, summary


def build_recommendation_rows(
    demand_rows: list[dict[str, Any]],
    demand_status: str,
    overall_status: str,
    warmup_scale: float,
    sustain_scale: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ratios: list[float] = []
    for row in demand_rows:
        expected = float(row["scaled_reference_count"])
        observed = float(row["observed_count"])
        if expected > 0.0 and observed > 0.0:
            ratios.append(expected / observed)
    global_multiplier = statistics.median(ratios) if ratios else None
    recommendation_triggered = demand_status != "PASS" or overall_status != "PASS"
    recommended_warmup = warmup_scale * global_multiplier if global_multiplier is not None else None
    recommended_sustain = sustain_scale * global_multiplier if global_multiplier is not None else None
    rows: list[dict[str, Any]] = [
        {
            "scope": "global",
            "segment_id": "",
            "direction": "",
            "observed_count": "",
            "scaled_reference_count": "",
            "scaled_recall": "",
            "recommended_multiplier": round(global_multiplier, 6) if global_multiplier is not None else "",
            "recommended_warmup_scale": round(recommended_warmup, 6) if recommended_warmup is not None else "",
            "recommended_sustain_scale": round(recommended_sustain, 6) if recommended_sustain is not None else "",
            "action": "adjust_global_scale" if recommendation_triggered else "no_adjustment",
            "reason": "validation_warn_or_fail" if recommendation_triggered else "validation_pass",
            "note": "report_only_no_route_xml_or_config_generated",
        }
    ]
    for row in demand_rows:
        expected = float(row["scaled_reference_count"])
        observed = float(row["observed_count"])
        recall_value = row.get("scaled_recall", "")
        if observed <= 0.0:
            multiplier: float | None = None
            action = "missing_flow_or_mapping"
            reason = "observed_count_zero"
        else:
            multiplier = expected / observed if expected > 0.0 else None
            recall_float = float(recall_value) if recall_value != "" else 0.0
            if recall_float < DEMAND_PASS_RECALL_MIN:
                action = "increase_demand"
                reason = "scaled_recall_below_pass_range"
            elif recall_float > DEMAND_PASS_RECALL_MAX:
                action = "decrease_demand"
                reason = "scaled_recall_above_pass_range"
            else:
                action = "keep"
                reason = "scaled_recall_in_pass_range"
        rows.append(
            {
                "scope": "segment_direction",
                "segment_id": row["segment_id"],
                "direction": row["direction"],
                "observed_count": observed,
                "scaled_reference_count": expected,
                "scaled_recall": recall_value,
                "recommended_multiplier": round(multiplier, 6) if multiplier is not None else "",
                "recommended_warmup_scale": "",
                "recommended_sustain_scale": "",
                "action": action,
                "reason": reason,
                "note": "report_only_no_route_xml_or_config_generated",
            }
        )
    summary = {
        "recommendation_triggered": recommendation_triggered,
        "recommended_global_multiplier": round(global_multiplier, 6) if global_multiplier is not None else None,
        "recommended_warmup_scale": round(recommended_warmup, 6) if recommended_warmup is not None else None,
        "recommended_sustain_scale": round(recommended_sustain, 6) if recommended_sustain is not None else None,
    }
    return rows, summary


def validation_output_paths(run_id: str, output_root: Path = OUTPUT_ROOT) -> dict[str, Path]:
    root = output_root / run_id
    return {
        "root": root,
        "map_csv": root / "b0_map_recall.csv",
        "lane_csv": root / "b0_lane_recall.csv",
        "demand_csv": root / "b0_demand_recall.csv",
        "speed_csv": root / "b0_speed_travel_time_recall.csv",
        "edge_speed_csv": root / "b0_edge_speed_recall.csv",
        "recommendations_csv": root / "b0_demand_adjustment_recommendations.csv",
        "summary_json": root / "validation_summary.json",
        "latest_json": output_root / "latest.json",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate B0 map, demand, and speed recall against Toegye-ro reality data.")
    parser.add_argument("--reference-csv", type=Path, default=DEFAULT_REFERENCE_CSV)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--results-csv", default="auto")
    parser.add_argument("--output-run-id", default="")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    return parser.parse_args()


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    reference_csv = resolve_project_path(args.reference_csv)
    manifest_path = resolve_project_path(args.manifest)
    manifest = read_json(manifest_path)
    b0_row = select_b0_row(args.results_csv)
    run_id = args.output_run_id or b0_row.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = resolve_project_path(args.output_root)
    paths = validation_output_paths(run_id, output_root)

    active_net = resolve_project_path(manifest["active_net"])
    edge_data_path = resolve_project_path(b0_row["edgeData_output"])
    demand_design = manifest.get("background_demand_design", {})
    warmup_sec = float(demand_design.get("warmup_sec", 600.0))
    warmup_scale = float(demand_design.get("warmup_scale", 0.15))
    sustain_scale = float(demand_design.get("sustain_scale", 0.05))

    segments = load_reference_segments(reference_csv)
    sumo_net, edges = load_edge_features(active_net)
    interval_begin, interval_end, edge_data = parse_edge_data(edge_data_path)

    map_rows, map_status, map_summary = build_map_rows(sumo_net, segments, edges)
    lane_rows, lane_status, lane_summary = build_lane_rows(sumo_net, segments, edges)
    demand_rows, demand_status, demand_summary = build_demand_rows(
        sumo_net,
        segments,
        edges,
        edge_data,
        interval_begin,
        interval_end,
        warmup_sec,
        warmup_scale,
        sustain_scale,
    )
    speed_rows, speed_status, speed_summary = build_speed_rows(sumo_net, segments, edges, edge_data)
    edge_speed_rows, edge_speed_status, edge_speed_summary = build_edge_speed_rows(sumo_net, segments, edges, edge_data)
    overall_status = combine_statuses([map_status, lane_status, demand_status, speed_status, edge_speed_status])
    recommendation_rows, recommendation_summary = build_recommendation_rows(
        demand_rows,
        demand_status,
        overall_status,
        warmup_scale,
        sustain_scale,
    )

    write_csv(
        paths["map_csv"],
        map_rows,
        [
            "segment_id",
            "start_intersection",
            "end_intersection",
            "segment_length_m",
            "map_recall",
            "covered_edge_count",
            "covered_edge_ids",
            "status",
        ],
    )
    write_csv(
        paths["lane_csv"],
        lane_rows,
        [
            "segment_id",
            "direction",
            "reference_lanes",
            "matched_edge_count",
            "matched_edge_ids",
            "matched_lane_counts",
            "mode_lane_count",
            "median_lane_count",
            "max_lane_count",
            "status",
        ],
    )
    write_csv(
        paths["demand_csv"],
        demand_rows,
        [
            "segment_id",
            "direction",
            "representative_edge_id",
            "matched_edge_count",
            "matched_edge_ids",
            "reference_volume_veh_per_h_directional",
            "interval_begin_sec",
            "interval_end_sec",
            "raw_reference_count",
            "scaled_reference_count",
            "observed_count",
            "raw_recall",
            "scaled_recall",
            "geh",
            "geh_status",
            "status",
        ],
    )
    write_csv(
        paths["speed_csv"],
        speed_rows,
        [
            "segment_id",
            "direction",
            "matched_edge_count",
            "matched_edge_ids",
            "reference_speed_kmh",
            "simulated_speed_kmh",
            "speed_error_kmh",
            "abs_speed_error_kmh",
            "reference_travel_time_s",
            "simulated_travel_time_s",
            "travel_time_error_s",
            "status",
        ],
    )
    write_csv(
        paths["edge_speed_csv"],
        edge_speed_rows,
        [
            "segment_id",
            "direction",
            "edge_id",
            "edge_length_m",
            "lane_count",
            "net_speed_limit_kmh",
            "reference_segment_speed_kmh",
            "simulated_edge_speed_kmh",
            "speed_error_kmh",
            "abs_speed_error_kmh",
            "sampled_seconds",
            "screenline_count",
            "entered",
            "left",
            "departed",
            "arrived",
            "anomaly_type",
            "status",
        ],
    )
    write_csv(
        paths["recommendations_csv"],
        recommendation_rows,
        [
            "scope",
            "segment_id",
            "direction",
            "observed_count",
            "scaled_reference_count",
            "scaled_recall",
            "recommended_multiplier",
            "recommended_warmup_scale",
            "recommended_sustain_scale",
            "action",
            "reason",
            "note",
        ],
    )

    summary = {
        "schema": "validation_b0_reality_recall.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "reference_csv": rel(reference_csv),
        "manifest": rel(manifest_path),
        "results_csv": rel(resolve_project_path(b0_row["_results_csv"])),
        "active_net": rel(active_net),
        "edgeData_output": rel(edge_data_path),
        "b0_final_status": b0_row.get("final_status", ""),
        "b0_run_dir": b0_row.get("run_dir", ""),
        "interval_begin_sec": interval_begin,
        "interval_end_sec": interval_end,
        "warmup_sec": warmup_sec,
        "warmup_scale": warmup_scale,
        "sustain_scale": sustain_scale,
        "map_status": map_status,
        "lane_status": lane_status,
        "demand_status": demand_status,
        "speed_status": speed_status,
        "edge_speed_status": edge_speed_status,
        "overall_status": overall_status,
        "map": map_summary,
        "lane": lane_summary,
        "demand": demand_summary,
        "speed": speed_summary,
        "edge_speed": edge_speed_summary,
        "recommendations": recommendation_summary,
        "outputs": {
            "map_csv": rel(paths["map_csv"]),
            "lane_csv": rel(paths["lane_csv"]),
            "demand_csv": rel(paths["demand_csv"]),
            "speed_csv": rel(paths["speed_csv"]),
            "edge_speed_csv": rel(paths["edge_speed_csv"]),
            "recommendations_csv": rel(paths["recommendations_csv"]),
            "summary_json": rel(paths["summary_json"]),
        },
    }
    write_json(paths["summary_json"], summary)
    write_json(
        paths["latest_json"],
        {
            "generated_at": summary["generated_at"],
            "run_id": run_id,
            "overall_status": overall_status,
            "summary_json": rel(paths["summary_json"]),
            "map_csv": rel(paths["map_csv"]),
            "lane_csv": rel(paths["lane_csv"]),
            "demand_csv": rel(paths["demand_csv"]),
            "speed_csv": rel(paths["speed_csv"]),
            "edge_speed_csv": rel(paths["edge_speed_csv"]),
            "recommendations_csv": rel(paths["recommendations_csv"]),
        },
    )
    return summary


def main() -> int:
    args = parse_args()
    try:
        summary = run_validation(args)
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"run_id": summary["run_id"], "overall_status": summary["overall_status"], "summary_json": summary["outputs"]["summary_json"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
