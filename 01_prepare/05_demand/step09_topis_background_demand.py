#!/usr/bin/env python3
"""Build Step 9 TOPIS AM background demand and run vehicle spawn smoke."""

from __future__ import annotations

import argparse
import csv
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


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import read_sumo_net  # noqa: E402


ACTIVE_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
SOURCE_TOPIS_CSV = PROJECT_ROOT / "peak_volume_summary.csv"
CANONICAL_TOPIS_CSV = PROJECT_ROOT / "data_prepared/demand/peak_volume_summary.csv"
MAPPING_CSV = PROJECT_ROOT / "data_prepared/demand/detector_to_screenline_mapping.csv"
SCREENLINE_COUNTS_CSV = PROJECT_ROOT / "data_prepared/demand/topis_screenline_counts_am.csv"
EDGEDATA_XML = PROJECT_ROOT / "data_prepared/demand/topis_edgedata_am.xml"
BACKGROUND_TRIPS_XML = PROJECT_ROOT / "data_prepared/demand/background_trips_candidate_am.trips.xml"
CANDIDATE_ROUTES_XML = PROJECT_ROOT / "data_prepared/demand/background_routes_candidate_am.rou.xml"
BACKGROUND_ROUTES_XML = PROJECT_ROOT / "data_prepared/demand/background_routes_am.rou.xml"
ROUTESAMPLER_MISMATCH_XML = PROJECT_ROOT / "data_prepared/demand/topis_route_sampler_mismatch_am.xml"
DEMAND_SUMMARY_JSON = PROJECT_ROOT / "data_prepared/demand/background_demand_summary.json"
SMOKE_RUN_DIR = PROJECT_ROOT / "runs/background_vehicle_spawn_smoke_am"
SMOKE_SUMMARY_CSV = PROJECT_ROOT / "results/metrics/background_vehicle_spawn_smoke_summary.csv"
SMOKE_SUMMARY_JSON = PROJECT_ROOT / "results/metrics/background_vehicle_spawn_smoke_summary.json"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step09_topis_background_demand.log"
STEP_DOC = PROJECT_ROOT / "docs/Step9.md"

EXCLUDED_DETECTORS = {
    "A-17": ("excluded_zero_count", "0값 측정 누락"),
    "A-19": ("excluded_abnormally_low_count", "비정상 저값"),
}
SCREENLINE_SEARCH_RADII_M = [120.0, 250.0, 500.0, 900.0]
OPPOSITE_HEADING_MIN_DEG = 135.0
SMOKE_TIMEOUT_SEC = 600


class Step09Error(RuntimeError):
    """Expected Step 9 failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build TOPIS AM background demand for active reduced SUMO net.")
    parser.add_argument("--period", choices=["am"], default="am")
    parser.add_argument("--smoke-seconds", type=int, default=600)
    parser.add_argument("--seed", type=int, default=9009)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise Step09Error(f"JSON root must be object: {rel(path)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_csv(path: Path, encoding: str = "utf-8") -> list[dict[str, str]]:
    with path.open("r", encoding=encoding, newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def sumo_home() -> Path:
    value = os.environ.get("SUMO_HOME")
    if value:
        return Path(value)
    sumo = shutil.which("sumo")
    if sumo:
        candidate = Path(sumo).resolve().parents[1]
        if (candidate / "share/sumo/tools/routeSampler.py").is_file():
            return candidate
    raise Step09Error("SUMO_HOME is not set and SUMO tools path could not be inferred")


def require_tool_paths() -> dict[str, str]:
    home = sumo_home()
    tools = {
        "sumo": shutil.which("sumo") or "",
        "duarouter": shutil.which("duarouter") or "",
        "randomTrips.py": str(home / "share/sumo/tools/randomTrips.py"),
        "routeSampler.py": str(home / "share/sumo/tools/routeSampler.py"),
    }
    missing = [name for name, path in tools.items() if not path or not Path(path).is_file()]
    if missing:
        raise Step09Error(f"Required SUMO tool missing: {', '.join(missing)}; SUMO_HOME={home}; PATH={os.environ.get('PATH', '')}")
    return tools


def canonicalize_topis_csv(lines: list[str]) -> str:
    if not SOURCE_TOPIS_CSV.is_file():
        raise Step09Error(f"Source TOPIS CSV missing: {rel(SOURCE_TOPIS_CSV)}")
    CANONICAL_TOPIS_CSV.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_TOPIS_CSV, CANONICAL_TOPIS_CSV)
    action = "copy"
    lines.append(f"canonical_input_action: {action}")
    lines.append(f"canonical_input: {rel(CANONICAL_TOPIS_CSV)}")
    return action


def road_axis_id(name: str) -> str:
    base = name.split("(", 1)[0].strip()
    return base.replace(" ", "_")


def load_edge_features_from_net(sumo_net: Any) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for edge in sumo_net.getEdges():
        edge_id = edge.getID()
        if edge.isSpecial() or edge_id.startswith(":") or not edge.allows("passenger"):
            continue
        shape = edge.getShape()
        if len(shape) < 2:
            continue
        edges.append(
            {
                "edge_id": edge_id,
                "props": {
                    "length_m": edge.getLength(),
                    "lane_count": edge.getLaneNumber(),
                    "speed_mps": edge.getSpeed(),
                    "priority": edge.getPriority(),
                },
                "points": [(float(x), float(y)) for x, y in shape],
            }
        )
    if not edges:
        raise Step09Error("No passenger edges found in active reduced SUMO net")
    return edges


def point_segment_distance(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    px, py = point
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    closest = (ax + t * dx, ay + t * dy)
    return math.hypot(px - closest[0], py - closest[1])


def point_polyline_distance(point: tuple[float, float], points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return float("inf")
    return min(point_segment_distance(point, a, b) for a, b in zip(points, points[1:], strict=False))


def heading_deg(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    first = points[0]
    last = points[-1]
    return math.degrees(math.atan2(last[1] - first[1], last[0] - first[0]))


def heading_diff(a: float, b: float) -> float:
    return abs((b - a + 180.0) % 360.0 - 180.0)


def edge_score(distance_m: float, props: dict[str, Any]) -> float:
    length = float(props.get("length_m") or 0.0)
    lane_count = float(props.get("lane_count") or 1.0)
    speed = float(props.get("speed_mps") or 0.0)
    priority = float(props.get("priority") or 0.0)
    return distance_m - 3.0 * min(lane_count, 4.0) - 0.4 * min(speed, 30.0) - 1.5 * min(priority, 10.0) - 0.01 * min(length, 300.0)


def candidate_edges(detector: dict[str, str], sumo_net: Any, edge_features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lat = float(detector["위도"])
    lon = float(detector["경도"])
    detector_point = sumo_net.convertLonLat2XY(lon, lat)
    candidates: list[dict[str, Any]] = []
    for edge in edge_features:
        points = edge["points"]
        distance = point_polyline_distance(detector_point, points)
        if not math.isfinite(distance):
            continue
        candidates.append(
            {
                "edge_id": edge["edge_id"],
                "distance_m": distance,
                "heading_deg": heading_deg(points),
                "props": edge["props"],
            }
        )
    candidates.sort(key=lambda item: edge_score(float(item["distance_m"]), item["props"]))
    for radius in SCREENLINE_SEARCH_RADII_M:
        nearby = [item for item in candidates if float(item["distance_m"]) <= radius]
        if nearby:
            return nearby[:12]
    return candidates[:12]


def select_screenline_pair(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    if not candidates:
        return [], "no_candidate"
    primary = candidates[0]
    opposite = None
    for candidate in candidates[1:]:
        if candidate["edge_id"] == primary["edge_id"]:
            continue
        if heading_diff(float(primary["heading_deg"]), float(candidate["heading_deg"])) >= OPPOSITE_HEADING_MIN_DEG:
            opposite = candidate
            break
    if opposite is None:
        return [primary], "single_direction_warning"
    return [primary, opposite], "bidirectional_pair_50_50"


def build_mapping(
    detectors: list[dict[str, str]],
    sumo_net: Any,
    edge_features: list[dict[str, Any]],
    smoke_seconds: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    mapping_rows: list[dict[str, Any]] = []
    count_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for detector in detectors:
        detector_id = detector["지점번호"]
        axis_id = road_axis_id(detector["지점명"])
        am_peak = float(detector["AM_peak_avg"])
        if detector_id in EXCLUDED_DETECTORS:
            reason_code, reason_text = EXCLUDED_DETECTORS[detector_id]
            warnings.append(f"{detector_id}:{reason_code}")
            mapping_rows.append(
                {
                    "detector_id": detector_id,
                    "detector_name": detector["지점명"],
                    "road_axis_id": axis_id,
                    "lat": detector["위도"],
                    "lon": detector["경도"],
                    "am_peak_avg_3h_bidirectional": am_peak,
                    "status": "WARNING",
                    "exclude_from_counts": True,
                    "exclude_reason": reason_code,
                    "screenline_edge_ids": "",
                    "direction_policy": "",
                    "nearest_candidate_distance_m": "",
                    "candidate_edge_count": 0,
                    "notes": reason_text,
                }
            )
            continue
        candidates = candidate_edges(detector, sumo_net, edge_features)
        selected, policy = select_screenline_pair(candidates)
        if not selected:
            warnings.append(f"{detector_id}:no_screenline_candidate")
            mapping_rows.append(
                {
                    "detector_id": detector_id,
                    "detector_name": detector["지점명"],
                    "road_axis_id": axis_id,
                    "lat": detector["위도"],
                    "lon": detector["경도"],
                    "am_peak_avg_3h_bidirectional": am_peak,
                    "status": "FAIL",
                    "exclude_from_counts": True,
                    "exclude_reason": "no_screenline_candidate",
                    "screenline_edge_ids": "",
                    "direction_policy": "",
                    "nearest_candidate_distance_m": "",
                    "candidate_edge_count": 0,
                    "notes": "No nearby passenger edge candidate",
                }
            )
            continue
        if policy == "single_direction_warning":
            warnings.append(f"{detector_id}:single_direction_screenline")
        count_600s = am_peak * smoke_seconds / 10_800.0
        per_edge = count_600s / len(selected)
        for index, edge in enumerate(selected, start=1):
            count_rows.append(
                {
                    "detector_id": detector_id,
                    "road_axis_id": axis_id,
                    "screenline_edge_id": edge["edge_id"],
                    "direction_index": index,
                    "direction_policy": policy,
                    "am_peak_avg_3h_bidirectional": round(am_peak, 3),
                    "count_600s_total": round(count_600s, 6),
                    "count_600s_edge": round(per_edge, 6),
                    "distance_m": round(float(edge["distance_m"]), 3),
                    "heading_deg": round(float(edge["heading_deg"]), 3),
                }
            )
        mapping_rows.append(
            {
                "detector_id": detector_id,
                "detector_name": detector["지점명"],
                "road_axis_id": axis_id,
                "lat": detector["위도"],
                "lon": detector["경도"],
                "am_peak_avg_3h_bidirectional": am_peak,
                "status": "WARNING" if policy == "single_direction_warning" else "PASS",
                "exclude_from_counts": False,
                "exclude_reason": "",
                "screenline_edge_ids": " ".join(edge["edge_id"] for edge in selected),
                "direction_policy": policy,
                "nearest_candidate_distance_m": round(float(selected[0]["distance_m"]), 3),
                "candidate_edge_count": len(candidates),
                "notes": "",
            }
        )
    return mapping_rows, count_rows, warnings


def write_edgedata(path: Path, count_rows: list[dict[str, Any]], smoke_seconds: int) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in count_rows:
        edge_id = str(row["screenline_edge_id"])
        totals[edge_id] = totals.get(edge_id, 0.0) + float(row["count_600s_edge"])
    root = ET.Element("data")
    interval = ET.SubElement(root, "interval", {"begin": "0", "end": str(smoke_seconds)})
    for edge_id, count in sorted(totals.items()):
        ET.SubElement(interval, "edge", {"id": edge_id, "entered": f"{count:.6f}"})
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return totals


def run_command(command: list[str], log_prefix: str, lines: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    lines.append(f"{log_prefix}: {' '.join(command)}")
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False, capture_output=True, text=True, timeout=timeout)
    lines.append(f"{log_prefix}_exit_code: {completed.returncode}")
    if completed.stdout.strip():
        lines.append(f"{log_prefix}_stdout: {completed.stdout.strip()[-4000:]}")
    if completed.stderr.strip():
        lines.append(f"{log_prefix}_stderr: {completed.stderr.strip()[-4000:]}")
    return completed


def generate_candidate_routes(tools: dict[str, str], candidate_count: int, smoke_seconds: int, seed: int, lines: list[str]) -> None:
    period = smoke_seconds / max(candidate_count, 1)
    command = [
        sys.executable,
        tools["randomTrips.py"],
        "-n",
        str(ACTIVE_NET),
        "-r",
        str(CANDIDATE_ROUTES_XML),
        "-o",
        str(BACKGROUND_TRIPS_XML),
        "--vehicle-class",
        "passenger",
        "--edge-permission",
        "passenger",
        "-b",
        "0",
        "-e",
        str(smoke_seconds),
        "-p",
        f"{period:.8f}",
        "--validate",
        "--remove-loops",
        "--random-depart",
        "--seed",
        str(seed),
    ]
    completed = run_command(command, "randomTrips", lines, timeout=600)
    if completed.returncode != 0 or not CANDIDATE_ROUTES_XML.is_file():
        raise Step09Error("randomTrips/duarouter candidate route generation failed")


def run_route_sampler(tools: dict[str, str], smoke_seconds: int, seed: int, lines: list[str]) -> None:
    command = [
        sys.executable,
        tools["routeSampler.py"],
        "-r",
        str(CANDIDATE_ROUTES_XML),
        "-d",
        str(EDGEDATA_XML),
        "--edgedata-attribute",
        "entered",
        "-o",
        str(BACKGROUND_ROUTES_XML),
        "--mismatch-output",
        str(ROUTESAMPLER_MISMATCH_XML),
        "-b",
        "0",
        "-e",
        str(smoke_seconds),
        "-i",
        str(smoke_seconds),
        "--prefix",
        "bg_",
        "--seed",
        str(seed),
        "--weighted",
    ]
    completed = run_command(command, "routeSampler", lines, timeout=600)
    if completed.returncode != 0 or not BACKGROUND_ROUTES_XML.is_file():
        raise Step09Error("routeSampler failed to generate background_routes_am.rou.xml")


def write_smoke_sumocfg(route_file: Path, tripinfo_file: Path, summary_file: Path) -> Path:
    sumocfg = SMOKE_RUN_DIR / "scenario.sumocfg"
    root = ET.Element("configuration")
    input_elem = ET.SubElement(root, "input")
    ET.SubElement(input_elem, "net-file", {"value": str(ACTIVE_NET)})
    ET.SubElement(input_elem, "route-files", {"value": str(route_file)})
    output_elem = ET.SubElement(root, "output")
    ET.SubElement(output_elem, "tripinfo-output", {"value": str(tripinfo_file)})
    ET.SubElement(output_elem, "summary-output", {"value": str(summary_file)})
    time_elem = ET.SubElement(root, "time")
    ET.SubElement(time_elem, "begin", {"value": "0"})
    report_elem = ET.SubElement(root, "report")
    ET.SubElement(report_elem, "no-step-log", {"value": "true"})
    ET.SubElement(report_elem, "duration-log.disable", {"value": "true"})
    SMOKE_RUN_DIR.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(sumocfg, encoding="utf-8", xml_declaration=True)
    return sumocfg


def count_route_vehicles(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    for _event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == "vehicle":
            count += 1
        elem.clear()
    return count


def parse_summary(path: Path) -> dict[str, int]:
    result = {"departed_count": 0, "arrived_count": 0, "teleport_count": 0, "route_error_count": 0}
    if not path.is_file():
        return result
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return result
    last_step = None
    max_teleports = 0
    for step in root.findall("step"):
        last_step = step
        max_teleports = max(max_teleports, int(float(step.get("teleports", "0"))))
    if last_step is not None:
        result["departed_count"] = int(float(last_step.get("inserted", "0")))
        result["arrived_count"] = int(float(last_step.get("arrived", "0")))
    result["teleport_count"] = max_teleports
    return result


def run_smoke(tools: dict[str, str], lines: list[str]) -> dict[str, Any]:
    tripinfo = SMOKE_RUN_DIR / "tripinfo.xml"
    summary = SMOKE_RUN_DIR / "summary.xml"
    stdout_log = SMOKE_RUN_DIR / "sumo_stdout.log"
    stderr_log = SMOKE_RUN_DIR / "sumo_stderr.log"
    sumocfg = write_smoke_sumocfg(BACKGROUND_ROUTES_XML, tripinfo, summary)
    completed = subprocess.run(
        [tools["sumo"], "-c", str(sumocfg)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=SMOKE_TIMEOUT_SEC,
    )
    stdout_log.write_text(completed.stdout, encoding="utf-8")
    stderr_log.write_text(completed.stderr, encoding="utf-8")
    metrics = parse_summary(summary)
    stderr_lower = completed.stderr.lower()
    route_error_count = stderr_lower.count("route error") + stderr_lower.count("has no valid route")
    metrics["route_error_count"] = route_error_count
    metrics["expected_vehicle_count"] = count_route_vehicles(BACKGROUND_ROUTES_XML)
    metrics["exit_code"] = completed.returncode
    metrics["arrival_rate"] = (
        metrics["arrived_count"] / metrics["departed_count"] if metrics["departed_count"] else 0.0
    )
    failure_reason = ""
    if completed.returncode != 0:
        failure_reason = f"sumo_exit_code_{completed.returncode}"
    elif metrics["departed_count"] <= 0:
        failure_reason = "departed_count_zero"
    metrics["failure_reason"] = failure_reason
    metrics["run_dir"] = rel(SMOKE_RUN_DIR)
    metrics["sumocfg"] = rel(sumocfg)
    metrics["tripinfo"] = rel(tripinfo)
    lines.append(f"sumo_smoke_exit_code: {completed.returncode}")
    lines.append(f"sumo_smoke_departed_count: {metrics['departed_count']}")
    lines.append(f"sumo_smoke_arrived_count: {metrics['arrived_count']}")
    lines.append(f"sumo_smoke_teleport_count: {metrics['teleport_count']}")
    if completed.stderr.strip():
        lines.append(f"sumo_smoke_stderr: {completed.stderr.strip()[-4000:]}")
    return metrics


def write_step9_doc(summary: dict[str, Any]) -> None:
    text = f"""# Step 9 TOPIS AM Background Demand

## 목표

TOPIS 검지기 AM peak count를 active reduced SUMO map의 screenline 제약으로 변환하고, routeSampler 기반 background vehicle route를 생성한 뒤 SUMO smoke로 일반 차량 출발/도착을 확인한다.

## 입력

- active net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- canonical TOPIS CSV: `data_prepared/demand/peak_volume_summary.csv`
- period: `am`
- smoke seconds: `{summary['smoke_seconds']}`

root의 `peak_volume_summary.csv`는 canonical input으로 copy하며, Step 9 실행 중에는 `data_prepared/demand/peak_volume_summary.csv`만 읽는다.

## 매핑 정책

- 검지점 좌표를 nearest edge 하나로만 확정하지 않는다.
- 지점명에서 도로명을 추출해 `road_axis_id`를 만들고, 좌표 주변 passenger 가능 edge 후보 중 대표 screenline edge 또는 양방향 edge pair를 선택한다.
- 방향별 실측값이 없으므로 양방향 edge pair는 50:50으로 분배한다.
- A-17은 0값 측정 누락으로 routeSampler count 입력에서 제외한다.
- A-19는 비정상 저값으로 routeSampler count 입력에서 제외한다.
- AM 3시간 count는 `AM_peak_avg * smoke_seconds / 10800`으로 환산한다.

## 산출물

- `data_prepared/demand/detector_to_screenline_mapping.csv`
- `data_prepared/demand/topis_screenline_counts_am.csv`
- `data_prepared/demand/topis_edgedata_am.xml`
- `data_prepared/demand/background_routes_candidate_am.rou.xml`
- `data_prepared/demand/background_routes_am.rou.xml`
- `data_prepared/demand/background_demand_summary.json`
- `results/metrics/background_vehicle_spawn_smoke_summary.csv`
- `results/metrics/background_vehicle_spawn_smoke_summary.json`
- `outputs/logs/step09_topis_background_demand.log`

## 현재 결과

- final status: `{summary['final_status']}`
- TOPIS row count: `{summary['topis_row_count']}`
- valid detector count: `{summary['valid_detector_count']}`
- excluded detector count: `{summary['excluded_detector_count']}`
- expected 600s count: `{summary['expected_600s_count']}`
- routeSampler vehicle count: `{summary['route_sampler_vehicle_count']}`
- smoke departed count: `{summary['smoke']['departed_count']}`
- smoke arrived count: `{summary['smoke']['arrived_count']}`
- smoke arrival rate: `{summary['smoke']['arrival_rate']:.6f}`
- smoke teleports: `{summary['smoke']['teleport_count']}`
- smoke route errors: `{summary['smoke']['route_error_count']}`

## 하지 않는 일

- emergency route 재생성
- spine route 재계산
- TraCI 신호제어
- B1/B2 구현
- Bayesian Optimization
- full batch 실행
- netconvert 실행
- OSM 다운로드
- map 재생성
- legacy full map 기본 입력 사용
"""
    STEP_DOC.write_text(text, encoding="utf-8")


def final_status(warnings: list[str], failures: list[str]) -> str:
    if failures:
        return "FAIL"
    if warnings:
        return "WARNING"
    return "PASS"


def main() -> int:
    args = parse_args()
    generated_at = utc_now()
    lines = ["Step 9 TOPIS AM background demand", "=================================", f"generated_at: {generated_at}"]
    warnings: list[str] = []
    failures: list[str] = []
    try:
        if not ACTIVE_NET.is_file():
            raise Step09Error(f"Active net missing: {rel(ACTIVE_NET)}")
        canonicalize_topis_csv(lines)
        tools = require_tool_paths()
        for name, path in tools.items():
            lines.append(f"tool_{name}: {path}")

        detectors = read_csv(CANONICAL_TOPIS_CSV, encoding="utf-8-sig")
        if len(detectors) != 13:
            raise Step09Error(f"Expected 13 TOPIS rows, got {len(detectors)}")
        sumo_net = read_sumo_net(ACTIVE_NET)
        edge_features = load_edge_features_from_net(sumo_net)
        mapping_rows, count_rows, mapping_warnings = build_mapping(detectors, sumo_net, edge_features, args.smoke_seconds)
        warnings.extend(mapping_warnings)
        failed_mappings = [row["detector_id"] for row in mapping_rows if row.get("status") == "FAIL"]
        if failed_mappings:
            failures.append(f"mapping_failed:{';'.join(failed_mappings)}")

        write_csv(
            MAPPING_CSV,
            mapping_rows,
            [
                "detector_id",
                "detector_name",
                "road_axis_id",
                "lat",
                "lon",
                "am_peak_avg_3h_bidirectional",
                "status",
                "exclude_from_counts",
                "exclude_reason",
                "screenline_edge_ids",
                "direction_policy",
                "nearest_candidate_distance_m",
                "candidate_edge_count",
                "notes",
            ],
        )
        write_csv(
            SCREENLINE_COUNTS_CSV,
            count_rows,
            [
                "detector_id",
                "road_axis_id",
                "screenline_edge_id",
                "direction_index",
                "direction_policy",
                "am_peak_avg_3h_bidirectional",
                "count_600s_total",
                "count_600s_edge",
                "distance_m",
                "heading_deg",
            ],
        )
        edge_totals = write_edgedata(EDGEDATA_XML, count_rows, args.smoke_seconds)
        expected_600s_count = sum(float(row["count_600s_edge"]) for row in count_rows)
        candidate_count = max(5000, math.ceil(expected_600s_count * 5.0))
        lines.append(f"topis_row_count: {len(detectors)}")
        lines.append(f"valid_detector_count: {sum(1 for row in mapping_rows if not row.get('exclude_from_counts'))}")
        lines.append(f"excluded_detector_count: {sum(1 for row in mapping_rows if row.get('exclude_from_counts'))}")
        lines.append(f"screenline_edge_count: {len(edge_totals)}")
        lines.append(f"expected_600s_count: {expected_600s_count:.6f}")
        lines.append(f"candidate_route_target_count: {candidate_count}")

        if failures:
            raise Step09Error(";".join(failures))
        generate_candidate_routes(tools, candidate_count, args.smoke_seconds, args.seed, lines)
        run_route_sampler(tools, args.smoke_seconds, args.seed, lines)
        smoke = run_smoke(tools, lines)
        if int(smoke["exit_code"]) != 0 or int(smoke["departed_count"]) <= 0:
            failures.append(str(smoke["failure_reason"] or "smoke_failed"))
        if int(smoke["teleport_count"]) > 0:
            warnings.append("smoke_teleports_present")
        if int(smoke["route_error_count"]) > 0:
            warnings.append("smoke_route_errors_present")

        status = final_status(warnings, failures)
        summary = {
            "generated_at": generated_at,
            "final_status": status,
            "period": args.period,
            "smoke_seconds": args.smoke_seconds,
            "active_net": rel(ACTIVE_NET),
            "canonical_topis_csv": rel(CANONICAL_TOPIS_CSV),
            "canonical_input_action": "copy",
            "topis_row_count": len(detectors),
            "valid_detector_count": sum(1 for row in mapping_rows if not row.get("exclude_from_counts")),
            "excluded_detector_count": sum(1 for row in mapping_rows if row.get("exclude_from_counts")),
            "screenline_edge_count": len(edge_totals),
            "expected_600s_count": round(expected_600s_count, 6),
            "candidate_route_target_count": candidate_count,
            "route_sampler_vehicle_count": count_route_vehicles(BACKGROUND_ROUTES_XML),
            "warnings": warnings,
            "failures": failures,
            "tools": tools,
            "outputs": [
                rel(MAPPING_CSV),
                rel(SCREENLINE_COUNTS_CSV),
                rel(EDGEDATA_XML),
                rel(BACKGROUND_TRIPS_XML),
                rel(CANDIDATE_ROUTES_XML),
                rel(BACKGROUND_ROUTES_XML),
                rel(DEMAND_SUMMARY_JSON),
                rel(SMOKE_SUMMARY_CSV),
                rel(SMOKE_SUMMARY_JSON),
                rel(LOG_PATH),
                rel(STEP_DOC),
            ],
            "smoke": smoke,
        }
        write_json(DEMAND_SUMMARY_JSON, summary)
        smoke_row = {
            "period": args.period,
            "smoke_seconds": args.smoke_seconds,
            "final_status": status,
            "expected_vehicle_count": smoke["expected_vehicle_count"],
            "departed_count": smoke["departed_count"],
            "arrived_count": smoke["arrived_count"],
            "arrival_rate": round(float(smoke["arrival_rate"]), 6),
            "teleport_count": smoke["teleport_count"],
            "route_error_count": smoke["route_error_count"],
            "exit_code": smoke["exit_code"],
            "failure_reason": smoke["failure_reason"],
            "run_dir": smoke["run_dir"],
            "sumocfg": smoke["sumocfg"],
            "tripinfo": smoke["tripinfo"],
        }
        write_csv(
            SMOKE_SUMMARY_CSV,
            [smoke_row],
            [
                "period",
                "smoke_seconds",
                "final_status",
                "expected_vehicle_count",
                "departed_count",
                "arrived_count",
                "arrival_rate",
                "teleport_count",
                "route_error_count",
                "exit_code",
                "failure_reason",
                "run_dir",
                "sumocfg",
                "tripinfo",
            ],
        )
        write_json(SMOKE_SUMMARY_JSON, {"generated_at": generated_at, **smoke_row})
        write_step9_doc(summary)

        lines.append(f"final_status: {status}")
        lines.append(f"background_routes: {rel(BACKGROUND_ROUTES_XML)}")
        lines.append(f"smoke_summary_json: {rel(SMOKE_SUMMARY_JSON)}")
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if status in {"PASS", "WARNING"} else 1
    except (Step09Error, OSError, ET.ParseError, subprocess.TimeoutExpired, ValueError, RuntimeError, ImportError) as exc:
        lines.extend(["final_status: FAIL", f"failure_reason: {exc}"])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
